"""Stage 5 on Modal: the default-vs-cache-aware benchmark without Kubernetes.

Modal gives us GPU containers, not a k3s node, so one container with 3 GPUs
stands in for the 3-replica Deployment. Everything that decides the result
is the repo's own code; only the Kubernetes plumbing is replaced:

    k8s concept            here
    ---------------------  ------------------------------------------------
    vLLM pod i             `vllm serve` pinned to GPU i, port 8001+i
    llm-d / router.py      cacheaware.router Ring + handler, in-process :8080
    controller.py          cacheaware.scoring on a scrape loop (same defaults)
    pod-deletion-cost      the cost the scorer computed at scale-in time
    `kubectl scale 3->2`   drop the victim from the ring, SIGTERM its vLLM
    FRESH_PODS=1           every trial is a brand-new container

Usage (from the repo root):

    modal run cloud/modal_bench.py                                   # smoke: 1 trial/mode, 60 s + 60 s
    modal run cloud/modal_bench.py --trials 3 --warm 120 --measure 120   # the repo's default run

Results land in results/modal/<timestamp>/bench/<mode>/trial_<n>/ in the
same layout stage 5 writes, and RESULTS.md is built by cacheaware.report.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import modal

DEFAULT_MODEL = "Qwen/Qwen2.5-7B-Instruct"   # ungated; Llama 3 8B needs an HF token secret
VLLM_VERSION = "0.32.0"
GPU = "A100-40GB"
N_REPLICAS = 3
ROUTER_PORT = 8080

ROOT = pathlib.Path(__file__).resolve().parent.parent

app = modal.App("cache-aware-scale-in")
cache = modal.Volume.from_name("cache-aware-hf-cache", create_if_missing=True)
# A CUDA *devel* base, not debian_slim: vLLM 0.32 JIT-compiles kernels during
# engine warm-up and dies with "Could not find nvcc" on an image without the
# toolkit. 13.0 matches the cu130 torch wheel vLLM pulls in.
image = (
    modal.Image.from_registry("nvidia/cuda:13.0.3-devel-ubuntu24.04", add_python="3.12")
    .uv_pip_install(f"vllm=={VLLM_VERSION}", "ninja")
    .env({"HF_HOME": "/cache/hf", "VLLM_CACHE_ROOT": "/cache/vllm", "CUDA_HOME": "/usr/local/cuda"})
    .add_local_dir(ROOT / "cacheaware", remote_path="/root/cacheaware")
)


# ----------------------------------------------------------------------------
# The one decision the benchmark's validity rests on
# ----------------------------------------------------------------------------

def pick_baseline_victim(replicas: list[str], costs: dict[str, int], rng) -> str:
    """Which replica does *default* Kubernetes remove when scaling 3 -> 2?

    replicas  names in start order, e.g. ["replica-0", "replica-1", "replica-2"]
    costs     what the cache-aware scorer would have written (baseline must NOT use it
              to choose; it is passed only so you can log or assert on it)
    rng       random.Random seeded per trial, so a run is reproducible
    """
    # Fresh pods are equally ready, on one node and the same age, so the ReplicaSet
    # tie-break is effectively arbitrary: uniform odds, as stage 5's README assumes.
    return rng.choice(replicas)


# ----------------------------------------------------------------------------
# Remote side
# ----------------------------------------------------------------------------

@app.function(image=image, volumes={"/cache": cache}, timeout=60 * 60)
def download_model(model: str) -> None:
    """Pull the weights once into the volume on a CPU container (cheap), not on 3 GPUs."""
    from huggingface_hub import snapshot_download
    snapshot_download(model, allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "tokenizer*"])
    cache.commit()


@app.function(image=image, gpu=f"{GPU}:{N_REPLICAS}", volumes={"/cache": cache}, timeout=60 * 60)
def run_trial(mode: str, trial: int, cfg: dict) -> dict:
    import random
    import signal
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    sys.path.insert(0, "/root")
    from cacheaware import router, scoring, vllm_metrics

    t0 = time.time()

    def log(msg: str) -> None:
        print(f"[{mode} #{trial} +{time.time() - t0:6.1f}s] {msg}", flush=True)

    # ---- 1. three fresh replicas ------------------------------------------------
    urls = {f"replica-{i}": f"http://127.0.0.1:{8001 + i}" for i in range(N_REPLICAS)}
    procs: dict[str, subprocess.Popen] = {}
    for i, name in enumerate(urls):
        procs[name] = subprocess.Popen(
            ["vllm", "serve", cfg["model"], "--port", str(8001 + i),
             "--max-model-len", "8192", "--gpu-memory-utilization", "0.90",
             "--enable-prefix-caching"],
            env=dict(os.environ, CUDA_VISIBLE_DEVICES=str(i)),
            stdout=open(f"/tmp/{name}.log", "w"), stderr=subprocess.STDOUT)

    def tail(name: str, n: int = 25) -> str:
        """The root-cause lines (vLLM prints them long before the final traceback) + the end."""
        lines = open(f"/tmp/{name}.log").readlines()
        causes = [l for l in lines if " ERROR " in l or l.startswith(("RuntimeError", "ValueError"))]
        return "".join(causes[:15]) + "---- last lines ----\n" + "".join(lines[-n:])

    def healthy(url: str) -> bool:
        try:
            return urllib.request.urlopen(url + "/health", timeout=2).status == 200
        except OSError:
            return False

    pending = set(urls)
    while pending:
        for name in list(pending):
            if procs[name].poll() is not None:
                raise RuntimeError(f"{name} exited during startup:\n{tail(name)}")
            if healthy(urls[name]):
                pending.discard(name)
                log(f"{name} ready")
        if time.time() - t0 > 900:
            raise TimeoutError("replicas not ready in 15 min:\n" + tail(sorted(pending)[0]))
        time.sleep(2)

    # ---- 2. gateway: the repo's consistent-hash router ------------------------------
    ring, rstats = router.Ring(), router.Stats()
    ring.set_pods(dict(urls))
    server = ThreadingHTTPServer(("127.0.0.1", ROUTER_PORT), router.make_handler(ring, rstats, 300.0))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # ---- 3. controller: the repo's scoring, same defaults as controller.py -----------
    live = dict(urls)
    history = {n: scoring.PodHistory() for n in urls}
    latest_cost: dict[str, int] = {}
    timeline: list[dict] = []
    stop = threading.Event()

    def score_loop() -> None:
        while not stop.is_set():
            row = {"t": round(time.time() - t0, 1)}
            for name, url in list(live.items()):
                try:
                    s = vllm_metrics.fetch_sample(url + "/metrics")
                except OSError:
                    continue
                hit = history[name].update(s, alpha=0.3, min_tokens=256)
                cost = scoring.deletion_cost(scoring.cache_value(hit, s.kv_usage))
                latest_cost[name] = cost
                row[name] = {"hit_rate": hit, "kv_usage": s.kv_usage, "cost": cost}
            timeline.append(row)
            stop.wait(cfg["scrape_every"])

    threading.Thread(target=score_loop, daemon=True).start()

    def snapshot() -> dict:
        pods = {}
        for name, url in live.items():
            s = vllm_metrics.fetch_sample(url + "/metrics")
            pods[name] = {"queries": s.prefix_queries, "hits": s.prefix_hits,
                          "ttft_sum": s.ttft_sum, "ttft_count": s.ttft_count,
                          "kv_usage": s.kv_usage, "metric_names": s.found}
        return {"at": time.time(), "pods": pods}

    first = snapshot()["pods"]["replica-0"]["metric_names"]
    log(f"vLLM metric names: {first}")
    if "prefix_hits" not in first and "hit_rate_gauge" not in first:
        raise RuntimeError(f"vLLM {VLLM_VERSION} exposes no prefix-cache metric we know: {first}")

    # ---- 4. load: warm, then measure --------------------------------------------------
    out = "/tmp/loadgen"
    lg = subprocess.Popen(
        [sys.executable, "-m", "cacheaware.loadgen", "bench", "--url", f"http://127.0.0.1:{ROUTER_PORT}",
         "--rate", str(cfg["rate"]), "--warm-seconds", str(cfg["warm"]),
         "--measure-seconds", str(cfg["measure"]), "--groups", str(cfg["groups"]),
         "--zipf", str(cfg["zipf"]), "--out", out],
        cwd="/root", env=dict(os.environ, PYTHONPATH="/root"),
        stdout=open("/tmp/loadgen.log", "w"), stderr=subprocess.STDOUT)
    log(f"load started: warm {cfg['warm']}s at {cfg['rate']} req/s")
    while not os.path.exists(f"{out}/measure.flag"):
        if lg.poll() is not None:
            raise RuntimeError("loadgen died:\n" + open("/tmp/loadgen.log").read()[-3000:])
        time.sleep(0.2)

    # ---- 5. scale in 3 -> 2 -------------------------------------------------------------
    pre = snapshot()
    costs = {n: latest_cost.get(n, 0) for n in urls}
    ranked = sorted(urls, key=lambda n: (costs[n], n))       # rank 1 = lowest cost
    expected = ranked[0]
    if mode == "cache-aware":
        victim = expected
    else:
        victim = pick_baseline_victim(list(urls), dict(costs), random.Random(cfg["seed"] * 1000 + trial))
    rank = ranked.index(victim) + 1
    del live[victim]
    ring.set_pods(dict(live))                 # endpoint removed: no new requests go there
    procs[victim].send_signal(signal.SIGTERM)
    scale_at = time.time()
    log(f"scale-in: costs={costs} removed={victim} (rank {rank}; lowest was {expected})")

    # ---- 6. finish -----------------------------------------------------------------------
    lg.wait(timeout=cfg["measure"] + 600)
    post = snapshot()
    stop.set()
    server.shutdown()
    for p in procs.values():
        p.terminate()
    log("done")

    def read(path: str, default=""):
        try:
            return open(path).read()
        except FileNotFoundError:
            return default

    return {
        "meta": {"mode": mode, "trial": trial, "tool": "loadgen", "removed_pod": victim,
                 "removed_rank": rank, "expected_pod": expected, "costs_at_scale_in": costs,
                 "scale_in_at": int(scale_at), "warm_seconds": cfg["warm"],
                 "measure_seconds": cfg["measure"], "rate": cfg["rate"],
                 "gateway_url": "modal-in-process-router", "model": cfg["model"],
                 "vllm_version": VLLM_VERSION, "gpu": f"{GPU}:{N_REPLICAS}"},
        "pre_snapshot": pre, "post_snapshot": post,
        "loadgen_summary": json.loads(read(f"{out}/summary.json", "{}")),
        "loadgen_requests": read(f"{out}/requests.jsonl"),
        "loadgen_log": read("/tmp/loadgen.log"),
        "router_stats": {"requests_by_pod": rstats.by_pod, "failovers": rstats.failovers,
                         "errors": rstats.errors},
        "cost_timeline": timeline,
    }


# ----------------------------------------------------------------------------
# Local side
# ----------------------------------------------------------------------------

@app.local_entrypoint()
def main(trials: int = 1, warm: int = 60, measure: int = 60, rate: float = 4.0,
         groups: int = 16, zipf: float = 1.0, scrape_every: float = 5.0,
         seed: int = 0, model: str = DEFAULT_MODEL):
    run_dir = ROOT / "results" / "modal" / time.strftime("%Y%m%d-%H%M%S")
    cfg = {"model": model, "warm": warm, "measure": measure, "rate": rate, "groups": groups,
           "zipf": zipf, "scrape_every": scrape_every, "seed": seed}
    print(f"results -> {run_dir}\nconfig  {cfg}")

    print("downloading weights into the volume (once)...")
    download_model.remote(model)

    jobs = [(mode, t, cfg) for t in range(1, trials + 1) for mode in ("baseline", "cache-aware")]
    failures = 0
    for (mode, t, _), res in zip(jobs, run_trial.starmap(jobs, return_exceptions=True)):
        if isinstance(res, BaseException):
            failures += 1
            print(f"!! {mode} trial {t} failed: {res}")
            continue
        d = run_dir / "bench" / mode / f"trial_{t}"
        (d / "loadgen").mkdir(parents=True, exist_ok=True)
        for key, name in [("meta", "meta.json"), ("pre_snapshot", "pre_snapshot.json"),
                          ("post_snapshot", "post_snapshot.json"),
                          ("router_stats", "router_stats.json"), ("cost_timeline", "cost_timeline.json")]:
            (d / name).write_text(json.dumps(res[key], indent=2))
        (d / "loadgen" / "summary.json").write_text(json.dumps(res["loadgen_summary"], indent=2))
        (d / "loadgen" / "requests.jsonl").write_text(res["loadgen_requests"])
        (d / "loadgen.log").write_text(res["loadgen_log"])
        m = res["meta"]
        print(f"ok {mode} trial {t}: removed {m['removed_pod']} rank {m['removed_rank']} costs {m['costs_at_scale_in']}")

    py = ROOT / ".venv" / "bin" / "python"
    subprocess.run([str(py if py.exists() else sys.executable), "-m", "cacheaware.report",
                    "--results", str(run_dir)], cwd=ROOT, check=False)
    if failures:
        print(f"{failures} trial(s) failed; see the log above")
