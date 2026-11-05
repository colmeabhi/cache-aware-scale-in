"""Stage 3 — prefix-heavy traffic for vLLM, with client-side TTFT measurement.

Standard library only, so it runs on any box with Python 3.10+.

What a "prefix group" is
------------------------
Real traffic reuses long prefixes: the same system prompt for thousands of
chats, the same long document for many questions. We imitate that with
``--groups G`` deterministic long prefixes (seeded, so every run and every
pod sees the *same* text). Each request = one group's prefix + a unique
question. The second request with the same prefix hits vLLM's prefix cache
and skips most of the prefill, which is what shortens TTFT.

Which group a request uses is drawn from a Zipf distribution (``--zipf 1.0``;
0 = uniform), because real prompt popularity is skewed: a few prefixes are hot.

Sub-commands
------------
    warm     closed loop: send N requests at concurrency C, then stop.
             Used to make a pod's cache hot (stage 3 and 4).
    bench    open loop at ``--rate`` requests/s (Poisson arrivals):
             ``--warm-seconds`` of warm-up, then ``--measure-seconds`` of
             measurement. Writes requests.jsonl + summary.json into --out.
             This is the fallback for inference-perf in stage 5.

Examples
--------
    python -m cacheaware.loadgen warm  --url http://127.0.0.1:18000 --requests 60 --groups 4
    python -m cacheaware.loadgen bench --url http://GATEWAY/ --rate 4 --warm-seconds 120 \
                                       --measure-seconds 120 --out results/bench/x/loadgen
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

WORDS = ("the model must answer carefully and cite the policy document section when the "
         "customer asks about refunds shipping warranty billing privacy security account "
         "access data retention escalation tone format language region currency tax "
         "returns exchange coupon loyalty tier premium standard support hours holiday "
         "schedule contact phone email chat ticket priority severity resolution").split()


# ----------------------------------------------------------------------------
# Prompts
# ----------------------------------------------------------------------------

def make_prefix(group: int, words: int, seed: int) -> str:
    """A long, deterministic 'system prompt' for one prefix group."""
    rng = random.Random(seed * 1000 + group)
    body = " ".join(rng.choice(WORDS) for _ in range(words))
    return f"[system prompt for tenant {group}]\n{body}\n\n"


def make_question(request_id: int, rng: random.Random) -> str:
    """A short unique tail so no two requests are identical end to end."""
    noise = " ".join(rng.choice(WORDS) for _ in range(12))
    return f"Question {request_id}: {noise}. Answer in one paragraph.\nAnswer:"


def zipf_weights(groups: int, exponent: float) -> list[float]:
    if exponent <= 0:
        return [1.0] * groups
    return [1.0 / (k ** exponent) for k in range(1, groups + 1)]


# ----------------------------------------------------------------------------
# One request
# ----------------------------------------------------------------------------

@dataclass
class RequestResult:
    request_id: int
    group: int
    phase: str                # warm | measure
    sent_at: float            # seconds since the run started
    ttft: float | None        # seconds to the first streamed token
    latency: float | None     # seconds to the last byte
    status: int               # HTTP status, 0 = transport error
    error: str = ""
    chunks: int = 0


def send_one(base_url: str, model: str, prompt: str, max_tokens: int,
             timeout: float, request_id: int, group: int, phase: str, t_start: float) -> RequestResult:
    body = json.dumps({"model": model, "prompt": prompt, "max_tokens": max_tokens,
                       "stream": True, "temperature": 0, "ignore_eos": True}).encode()
    request = urllib.request.Request(base_url.rstrip("/") + "/v1/completions", data=body,
                                     headers={"Content-Type": "application/json"})
    result = RequestResult(request_id, group, phase, time.perf_counter() - t_start, None, None, 0)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result.status = response.status
            for line in response:                      # server-sent events, one per line
                if not line.startswith(b"data:") or b"[DONE]" in line:
                    continue
                if result.ttft is None:
                    result.ttft = time.perf_counter() - t0
                result.chunks += 1
        result.latency = time.perf_counter() - t0
    except urllib.error.HTTPError as exc:
        result.status, result.error = exc.code, exc.reason
    except Exception as exc:
        result.error = str(exc)[:120]
    return result


def discover_model(base_url: str) -> str:
    """Ask the server which model it serves (vLLM lists exactly one)."""
    with urllib.request.urlopen(base_url.rstrip("/") + "/v1/models", timeout=10) as response:
        data = json.load(response)
    return data["data"][0]["id"]


# ----------------------------------------------------------------------------
# The two traffic shapes
# ----------------------------------------------------------------------------

class Run:
    def __init__(self, args):
        self.args = args
        self.model = args.model if args.model != "auto" else discover_model(args.url)
        self.prefixes = [make_prefix(g, args.prefix_words, args.seed) for g in range(args.groups)]
        self.weights = zipf_weights(args.groups, args.zipf)
        self.rng = random.Random(args.seed + 7)
        self.results: list[RequestResult] = []
        self.lock = threading.Lock()
        self.t_start = time.perf_counter()
        self.out = getattr(args, "out", None)
        if self.out:
            os.makedirs(self.out, exist_ok=True)
            self.jsonl = open(os.path.join(self.out, "requests.jsonl"), "w")
        else:
            self.jsonl = None

    def prompt_for(self, request_id: int) -> tuple[int, str]:
        if self.rng.random() < self.args.unique_fraction:
            # A one-off prompt nobody will ever repeat: guaranteed cache miss.
            group = -1
            prefix = make_prefix(10_000 + request_id, self.args.prefix_words, self.args.seed)
        else:
            group = self.rng.choices(range(self.args.groups), weights=self.weights)[0]
            prefix = self.prefixes[group]
        return group, prefix + make_question(request_id, self.rng)

    def fire(self, request_id: int, phase: str) -> None:
        group, prompt = self.prompt_for(request_id)
        result = send_one(self.args.url, self.model, prompt, self.args.max_tokens,
                          self.args.timeout, request_id, group, phase, self.t_start)
        with self.lock:
            self.results.append(result)
            if self.jsonl:
                self.jsonl.write(json.dumps(asdict(result)) + "\n")
                self.jsonl.flush()
            if self.args.verbose or result.error:
                print(f"  #{request_id:<5} group={group:<3} {phase:<7} ttft={fmt(result.ttft)} "
                      f"lat={fmt(result.latency)} status={result.status} {result.error}", flush=True)

    def warm(self) -> None:
        """Closed loop: keep `concurrency` requests in flight until N are done."""
        n, done, next_id = self.args.requests, 0, 0
        semaphore = threading.Semaphore(self.args.concurrency)
        threads = []
        for next_id in range(n):
            semaphore.acquire()

            def worker(rid=next_id):
                try:
                    self.fire(rid, "warm")
                finally:
                    semaphore.release()
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def bench(self) -> None:
        """Open loop: Poisson arrivals at --rate for warm then measure phases."""
        phases = [("warm", self.args.warm_seconds), ("measure", self.args.measure_seconds)]
        threads, request_id = [], 0
        touch(self.out, "started.flag")
        for phase, seconds in phases:
            if seconds <= 0:
                continue
            touch(self.out, f"{phase}.flag")
            print(f"phase {phase}: {seconds}s at {self.args.rate} req/s", flush=True)
            phase_start = time.perf_counter()
            while time.perf_counter() - phase_start < seconds:
                t = threading.Thread(target=self.fire, args=(request_id, phase), daemon=True)
                t.start()
                threads.append(t)
                request_id += 1
                time.sleep(self.rng.expovariate(self.args.rate))
        print("draining in-flight requests", flush=True)
        for t in threads:
            t.join(timeout=self.args.timeout)
        touch(self.out, "finished.flag")

    def summary(self) -> dict:
        """Percentiles per phase and per 30 s window of the measure phase."""
        measure = [r for r in self.results if r.phase == "measure"]
        windows = {}
        width = self.args.window_seconds
        measure_start = min((r.sent_at for r in measure), default=0.0)
        for r in measure:
            idx = int((r.sent_at - measure_start) // width)
            windows.setdefault(idx, []).append(r)
        return {
            "model": self.model,
            "url": self.args.url,
            "rate": getattr(self.args, "rate", None),
            "groups": self.args.groups, "zipf": self.args.zipf,
            "prefix_words": self.args.prefix_words, "max_tokens": self.args.max_tokens,
            "phases": {
                "warm": stats([r for r in self.results if r.phase == "warm"]),
                "measure": stats(measure),
            },
            "measure_windows": {
                f"{idx * width:.0f}-{(idx + 1) * width:.0f}s": stats(rs)
                for idx, rs in sorted(windows.items())
            },
        }


def stats(results: list[RequestResult]) -> dict:
    ttfts = sorted(r.ttft for r in results if r.ttft is not None)
    errors = sum(1 for r in results if r.error or r.status >= 400)
    if not ttfts:
        return {"count": len(results), "errors": errors}
    return {
        "count": len(results), "errors": errors,
        "ttft_mean": statistics.fmean(ttfts),
        "ttft_p50": percentile(ttfts, 50), "ttft_p90": percentile(ttfts, 90),
        "ttft_p99": percentile(ttfts, 99),
        "latency_mean": statistics.fmean(r.latency for r in results if r.latency is not None),
    }


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return math.nan
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def touch(directory: str | None, name: str) -> None:
    if directory:
        open(os.path.join(directory, name), "w").close()


def fmt(x: float | None) -> str:
    return "  -  " if x is None else f"{x:6.3f}s"


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", required=True, help="base URL, e.g. http://127.0.0.1:18000")
    p.add_argument("--model", default="auto", help="model name; 'auto' asks /v1/models")
    p.add_argument("--groups", type=int, default=8, help="number of distinct prefixes")
    p.add_argument("--zipf", type=float, default=1.0, help="popularity skew, 0 = uniform")
    p.add_argument("--prefix-words", type=int, default=1200, help="~1.5 tokens per word")
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--unique-fraction", type=float, default=0.0, help="share of never-repeated prompts")
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    warm = sub.add_parser("warm", help="closed-loop warm-up of one endpoint")
    add_common(warm)
    warm.add_argument("--requests", type=int, default=50)
    warm.add_argument("--concurrency", type=int, default=8)
    warm.add_argument("--out", help="optional dir for requests.jsonl + summary.json")

    bench = sub.add_parser("bench", help="open-loop warm + measure, writes a report")
    add_common(bench)
    bench.add_argument("--rate", type=float, default=4.0, help="requests per second")
    bench.add_argument("--warm-seconds", type=float, default=120)
    bench.add_argument("--measure-seconds", type=float, default=120)
    bench.add_argument("--window-seconds", type=float, default=30)
    bench.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    if not hasattr(args, "window_seconds"):
        args.window_seconds = 30
    run = Run(args)
    print(f"model={run.model} groups={args.groups} zipf={args.zipf} prefix_words={args.prefix_words}", flush=True)

    if args.command == "warm":
        run.warm()
    else:
        run.bench()

    summary = run.summary()
    print(json.dumps(summary["phases"], indent=2))
    if run.out:
        with open(os.path.join(run.out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(f"wrote {run.out}/summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
