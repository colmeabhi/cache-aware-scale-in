"""Stage 5 — live status for the dashboard, written as plain files.

Design rule: the dashboard must not disturb the benchmark. So there is exactly
ONE process that talks to the vLLM pods for monitoring purposes, the sampler
below, and it writes small JSON files. The dashboard web server only reads
those files. The sampler scrapes each pod's /metrics every ``--interval``
seconds; that is one small HTTP GET per pod, the same thing Prometheus would
do, and is negligible next to the inference traffic.

Files, all under ``<results>/live/``:

    status.json     key=value facts set by the orchestrating script
                    (phase, mode, trial, removed pod, ...)
    events.jsonl    one line per noteworthy thing ("scaled to 2", ...)
    samples.jsonl   one line per pod per sample: hit rate, usage, cost, TTFT

Sub-commands (all meant to be called from shell scripts):

    python -m cacheaware.live set phase=warming trial=2 mode=cache-aware
    python -m cacheaware.live event "scaled vllm to 2 replicas"
    python -m cacheaware.live sample  --namespace vllm --selector app=vllm &   # background
    python -m cacheaware.live snapshot --namespace vllm --selector app=vllm --out pre.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

from .controller import COST_ANNOTATION
from .scoring import PodHistory
from .vllm_metrics import fetch_text, sample_from_text


def live_dir(results: str) -> str:
    path = os.path.join(results, "live")
    os.makedirs(path, exist_ok=True)
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------
# status / event: trivial file writers
# ----------------------------------------------------------------------------

def set_status(results: str, pairs: list[str]) -> None:
    path = os.path.join(live_dir(results), "status.json")
    status = read_json(path, default={})
    for pair in pairs:
        key, _, value = pair.partition("=")
        status[key] = value
    status["updated_at"] = now_iso()
    write_json(path, status)


def add_event(results: str, text: str) -> None:
    with open(os.path.join(live_dir(results), "events.jsonl"), "a") as f:
        f.write(json.dumps({"t": time.time(), "at": now_iso(), "text": text}) + "\n")
    print(f"[event] {text}", flush=True)


# ----------------------------------------------------------------------------
# targets: either static name=url pairs or pods found through Kubernetes
# ----------------------------------------------------------------------------

class Targets:
    """Knows how to find the pods and fetch their /metrics pages."""

    def __init__(self, args):
        self.args = args
        self.kube = None
        self.static: dict[str, str] = {}
        if args.targets:
            for item in args.targets.split(","):
                name, url = item.split("=", 1)
                self.static[name.strip()] = url.strip().rstrip("/") + "/metrics"
        else:
            from .kube import Kube
            self.kube = Kube(kubeconfig=args.kubeconfig)

    def scrape_all(self) -> dict[str, tuple[str, int | None]]:
        """-> {pod name: (metrics text, deletion cost annotation or None)}"""
        pages = {}
        if self.static:
            for name, url in self.static.items():
                try:
                    pages[name] = (fetch_text(url), None)
                except Exception:
                    pass
            return pages
        for pod in self.kube.list_pods(self.args.namespace, self.args.selector):
            if pod.phase != "Running" or pod.deleting:
                continue
            try:
                text = self.kube.scrape(pod, self.args.metrics_port, "/metrics", self.args.scrape_mode)
            except Exception:
                continue
            raw_cost = pod.annotations.get(COST_ANNOTATION)
            pages[pod.name] = (text, int(raw_cost) if raw_cost and raw_cost.lstrip("-").isdigit() else None)
        return pages


# ----------------------------------------------------------------------------
# sample: the one monitoring loop
# ----------------------------------------------------------------------------

def run_sampler(args) -> None:
    targets = Targets(args)
    path = os.path.join(live_dir(args.results), "samples.jsonl")
    histories: dict[str, PodHistory] = {}
    last_ttft: dict[str, tuple[float, float]] = {}   # pod -> (sum, count) at last sample
    print(f"sampler: writing {path} every {args.interval}s", flush=True)
    with open(path, "a") as out:
        while True:
            t = time.time()
            for name, (text, cost) in targets.scrape_all().items():
                sample = sample_from_text(text)
                hit_recent = histories.setdefault(name, PodHistory()).update(sample, alpha=0.5, min_tokens=64)
                ttft_recent = None
                if sample.ttft_sum is not None and sample.ttft_count is not None:
                    prev_sum, prev_count = last_ttft.get(name, (sample.ttft_sum, sample.ttft_count))
                    if sample.ttft_count - prev_count > 0:
                        ttft_recent = (sample.ttft_sum - prev_sum) / (sample.ttft_count - prev_count)
                    last_ttft[name] = (sample.ttft_sum, sample.ttft_count)
                out.write(json.dumps({
                    "t": t, "pod": name, "cost": cost,
                    "kv_usage": sample.kv_usage,
                    "hit_rate_recent": hit_recent,
                    "hit_rate_cumulative": sample.cumulative_hit_rate,
                    "queries": sample.prefix_queries, "hits": sample.prefix_hits,
                    "running": sample.running, "waiting": sample.waiting,
                    "ttft_recent_mean": ttft_recent,
                }) + "\n")
            out.flush()
            time.sleep(max(0.0, args.interval - (time.time() - t)))


# ----------------------------------------------------------------------------
# snapshot: cumulative counters per pod, for before/after comparisons
# ----------------------------------------------------------------------------

def take_snapshot(args) -> dict:
    snapshot = {"at": now_iso(), "t": time.time(), "pods": {}}
    for name, (text, cost) in Targets(args).scrape_all().items():
        s = sample_from_text(text)
        snapshot["pods"][name] = {
            "cost": cost, "kv_usage": s.kv_usage,
            "queries": s.prefix_queries, "hits": s.prefix_hits,
            "hit_rate_gauge": s.hit_rate_gauge,
            "ttft_sum": s.ttft_sum, "ttft_count": s.ttft_count,
            "requests_ok": s.requests_ok,
            "metric_names": s.found,
        }
    return snapshot


# ----------------------------------------------------------------------------
# helpers + CLI
# ----------------------------------------------------------------------------

def read_json(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)      # atomic: the dashboard never sees a half-written file


def add_target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--targets", default=os.environ.get("STATIC_TARGETS"), help="name=url,... (no Kubernetes)")
    p.add_argument("--namespace", default=os.environ.get("NAMESPACE", "vllm"))
    p.add_argument("--selector", default=os.environ.get("SELECTOR", "app=vllm"))
    p.add_argument("--metrics-port", type=int, default=int(os.environ.get("METRICS_PORT", "8000")))
    p.add_argument("--scrape-mode", default=os.environ.get("SCRAPE_MODE", "auto"))
    p.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG_PATH"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", default=os.environ.get("RESULTS_DIR", "results"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_set = sub.add_parser("set");        p_set.add_argument("pairs", nargs="+")
    p_event = sub.add_parser("event");    p_event.add_argument("text")
    p_sample = sub.add_parser("sample");  add_target_args(p_sample)
    p_sample.add_argument("--interval", type=float, default=2.0)
    p_snap = sub.add_parser("snapshot");  add_target_args(p_snap)
    p_snap.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if args.command == "set":
        set_status(args.results, args.pairs)
    elif args.command == "event":
        add_event(args.results, args.text)
    elif args.command == "sample":
        try:
            run_sampler(args)
        except KeyboardInterrupt:
            pass
    elif args.command == "snapshot":
        snap = take_snapshot(args)
        write_json(args.out, snap)
        print(f"snapshot of {len(snap['pods'])} pods -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
