"""Stage 1 — read the cache signals out of a vLLM ``/metrics`` page.

vLLM serves Prometheus text that looks like this:

    # HELP vllm:kv_cache_usage_perc KV-cache usage. 1 means 100 percent usage.
    # TYPE vllm:kv_cache_usage_perc gauge
    vllm:kv_cache_usage_perc{model_name="meta-llama/Meta-Llama-3-8B-Instruct"} 0.42
    vllm:prefix_cache_queries{model_name="..."} 183296.0
    vllm:prefix_cache_hits{model_name="..."} 128000.0

Two things make this slightly annoying:

1. The metric names changed between vLLM versions (the V0 engine had a
   ``gpu_prefix_cache_hit_rate`` gauge; the V1 engine replaced it with two
   counters, first called ``gpu_prefix_cache_*`` and later ``prefix_cache_*``).
   This module knows every spelling and records which one it found, so the
   stage 3 "discover metrics" step can tell you what your vLLM speaks.

2. The counters are *cumulative since the pod started*. A pod that was hot
   yesterday and idle today still shows a high lifetime hit rate. The
   controller therefore works with the *difference* between two scrapes
   (see scoring.py); this module only reads the raw numbers.

Run it by hand to see what a page contains:

    curl -s http://POD_IP:8000/metrics | python -m cacheaware.vllm_metrics
    python -m cacheaware.vllm_metrics --url http://127.0.0.1:9001/metrics
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass, field

# Every name vLLM has used for each signal, newest spelling first.
# The first name that exists on the page is the one we use.
NAME_CANDIDATES: dict[str, list[str]] = {
    "kv_usage":       ["vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"],
    "prefix_queries": ["vllm:prefix_cache_queries", "vllm:gpu_prefix_cache_queries"],
    "prefix_hits":    ["vllm:prefix_cache_hits", "vllm:gpu_prefix_cache_hits"],
    "hit_rate_gauge": ["vllm:gpu_prefix_cache_hit_rate"],   # old (V0 engine) vLLM only
    "running":        ["vllm:num_requests_running"],
    "waiting":        ["vllm:num_requests_waiting"],
    "ttft_sum":       ["vllm:time_to_first_token_seconds_sum"],
    "ttft_count":     ["vllm:time_to_first_token_seconds_count"],
    "requests_ok":    ["vllm:request_success_total"],
}

# One sample line:  name{labels} value [timestamp]
_SAMPLE_LINE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"   # metric name
    r"(?P<labels>\{[^}]*\})?"                # optional {label="..."} block
    r"\s+(?P<value>[^\s]+)"                  # the number
    r"(?:\s+-?\d+)?$"                        # optional timestamp
)


def parse_prometheus_text(text: str) -> dict[str, float]:
    """Return ``{metric_name: value}``.

    Lines that share a name (different labels) are summed. For the metrics we
    read that is what we want: a pod serves one model, so there is one label
    set, and summing is a no-op. Histogram buckets also get summed, which is
    meaningless, but we never read bucket lines.
    """
    totals: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_LINE.match(line)
        if not match:
            continue
        try:
            value = float(match["value"])
        except ValueError:
            continue
        totals[match["name"]] = totals.get(match["name"], 0.0) + value
    return totals


@dataclass
class VllmSample:
    """The handful of numbers the controller needs from one scrape."""

    kv_usage: float | None = None        # 0.0 .. 1.0, how full the KV cache is
    prefix_queries: float | None = None  # cumulative prefix-cache lookups (tokens)
    prefix_hits: float | None = None     # cumulative prefix-cache hits (tokens)
    hit_rate_gauge: float | None = None  # old vLLM only: hit rate since start
    running: float = 0.0                 # requests being generated right now
    waiting: float = 0.0                 # requests queued
    ttft_sum: float | None = None        # cumulative TTFT seconds (histogram _sum)
    ttft_count: float | None = None      # cumulative TTFT samples (histogram _count)
    requests_ok: float | None = None     # cumulative successful requests
    found: dict[str, str] = field(default_factory=dict)  # signal -> metric name used

    @property
    def cumulative_hit_rate(self) -> float | None:
        """Hit rate since the pod started. Useful for display, not for scoring."""
        if self.prefix_queries and self.prefix_hits is not None and self.prefix_queries > 0:
            return self.prefix_hits / self.prefix_queries
        return self.hit_rate_gauge

    @property
    def has_cache_signal(self) -> bool:
        """True if this vLLM exposes *some* prefix-cache metric we understand."""
        return "prefix_hits" in self.found or "hit_rate_gauge" in self.found


def sample_from_text(text: str) -> VllmSample:
    """Parse a /metrics page and pick out the signals, trying each spelling."""
    metrics = parse_prometheus_text(text)
    sample = VllmSample()
    for signal, candidate_names in NAME_CANDIDATES.items():
        for name in candidate_names:
            if name in metrics:
                setattr(sample, signal, metrics[name])
                sample.found[signal] = name
                break
    return sample


def fetch_text(url: str, timeout: float = 2.0) -> str:
    """GET a URL and return the body as text (stdlib only, no requests dependency)."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_sample(url: str, timeout: float = 2.0) -> VllmSample:
    return sample_from_text(fetch_text(url, timeout))


def describe(sample: VllmSample) -> str:
    """Human-readable table of what was found, for the discover-metrics step."""
    rows = []
    for signal in NAME_CANDIDATES:
        name = sample.found.get(signal, "-- not found --")
        value = getattr(sample, signal)
        shown = "" if value is None else f"{value:.4g}"
        rows.append(f"  {signal:<15} {name:<42} {shown}")
    hit = sample.cumulative_hit_rate
    rows.append(f"  cumulative prefix-cache hit rate: "
                f"{'n/a' if hit is None else f'{hit:.3f}'}")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the cache signals on a vLLM /metrics page.")
    parser.add_argument("--url", help="metrics URL; without it the page is read from stdin")
    args = parser.parse_args(argv)
    text = fetch_text(args.url) if args.url else sys.stdin.read()
    sample = sample_from_text(text)
    print(describe(sample))
    if not sample.has_cache_signal:
        print("\nNo prefix-cache metric found. Is --enable-prefix-caching on? "
              "Is this really vLLM's /metrics page?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
