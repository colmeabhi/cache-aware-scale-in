"""Stage 1 — a fake vLLM pod. Serves /metrics like the real thing, needs no GPU.

This file is deliberately self-contained (standard library only, no imports
from the package) because stage 2 mounts it into a Kubernetes pod from a
ConfigMap and runs it with a plain python:3.12-slim image.

    python -m cacheaware.fake_vllm --port 9001 --hit-rate 0.70 --usage 0.4

Endpoints:
    GET /metrics      Prometheus text, same metric names as vLLM
    GET /health       200 OK (readiness probe)
    GET /state        current knobs as JSON
    GET /set?hit_rate=0.05&usage=0.1&tokens_per_sec=800   change the knobs live
    GET /v1/models, POST /v1/completions (streaming)        just enough of the
                     OpenAI API for loadgen.py to run against it. TTFT is faked
                     from hit_rate so the stage 5 pipeline can be rehearsed.

The counters move forward on every scrape as if the pod were serving
``tokens_per_sec`` prefix-cache lookups per second, of which ``hit_rate``
hit. That makes the delta-based scoring in scoring.py behave exactly as it
does against a real pod.

--names picks the metric spelling, so you can test the controller against
every vLLM generation:  new (vllm:prefix_cache_*), gpu (vllm:gpu_prefix_cache_*)
or v0 (the old hit-rate gauge).
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODEL = "fake/llama-3-8b"

SPELLINGS = {
    "new": {"usage": "vllm:kv_cache_usage_perc",
            "queries": "vllm:prefix_cache_queries",
            "hits": "vllm:prefix_cache_hits"},
    "gpu": {"usage": "vllm:gpu_cache_usage_perc",
            "queries": "vllm:gpu_prefix_cache_queries",
            "hits": "vllm:gpu_prefix_cache_hits"},
    "v0":  {"usage": "vllm:gpu_cache_usage_perc",
            "hit_rate_gauge": "vllm:gpu_prefix_cache_hit_rate"},
}


class FakePod:
    """The simulated state of one vLLM replica."""

    def __init__(self, hit_rate: float, usage: float, tokens_per_sec: float,
                 requests_per_sec: float, names: str):
        self.hit_rate = hit_rate
        self.usage = usage
        self.tokens_per_sec = tokens_per_sec
        self.requests_per_sec = requests_per_sec
        self.names = names
        self.queries = 0.0      # cumulative prefix-cache lookups (tokens)
        self.hits = 0.0         # cumulative prefix-cache hits (tokens)
        self.ttft_sum = 0.0     # cumulative TTFT seconds
        self.ttft_count = 0.0   # cumulative requests
        self.last_tick = time.monotonic()

    def tick(self) -> None:
        """Advance the counters by the time that passed since the last scrape."""
        now = time.monotonic()
        elapsed = now - self.last_tick
        self.last_tick = now
        lookups = self.tokens_per_sec * elapsed
        self.queries += lookups
        self.hits += lookups * self.hit_rate
        # A cache hit skips most of the prefill, so TTFT shrinks with the hit rate.
        requests = self.requests_per_sec * elapsed
        ttft_each = 0.40 * (1.0 - 0.8 * self.hit_rate)
        self.ttft_count += requests
        self.ttft_sum += requests * ttft_each

    def set_knobs(self, query: dict[str, list[str]]) -> None:
        for key in ("hit_rate", "usage", "tokens_per_sec", "requests_per_sec"):
            if key in query:
                setattr(self, key, float(query[key][0]))

    def state(self) -> dict:
        return {"hit_rate": self.hit_rate, "usage": self.usage,
                "tokens_per_sec": self.tokens_per_sec, "requests_per_sec": self.requests_per_sec,
                "queries": self.queries, "hits": self.hits, "names": self.names}

    def metrics_text(self) -> str:
        self.tick()
        label = f'{{model_name="{MODEL}"}}'
        names = SPELLINGS[self.names]
        lines = [
            f"# HELP {names['usage']} KV-cache usage. 1 means 100 percent usage.",
            f"# TYPE {names['usage']} gauge",
            f"{names['usage']}{label} {self.usage}",
            f"vllm:num_requests_running{label} 2.0",
            f"vllm:num_requests_waiting{label} 0.0",
            f"vllm:time_to_first_token_seconds_sum{label} {self.ttft_sum}",
            f"vllm:time_to_first_token_seconds_count{label} {self.ttft_count}",
            f"vllm:request_success_total{label} {self.ttft_count}",
        ]
        if "hit_rate_gauge" in names:
            lines.append(f"{names['hit_rate_gauge']}{label} {self.hit_rate}")
        else:
            lines += [f"# TYPE {names['queries']} counter",
                      f"{names['queries']}{label} {self.queries}",
                      f"# TYPE {names['hits']} counter",
                      f"{names['hits']}{label} {self.hits}"]
        return "\n".join(lines) + "\n"


def make_handler(pod: FakePod):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/metrics":
                self._reply(200, pod.metrics_text(), "text/plain; version=0.0.4")
            elif url.path == "/health":
                self._reply(200, "OK\n")
            elif url.path == "/set":
                pod.set_knobs(parse_qs(url.query))
                self._reply(200, json.dumps(pod.state()) + "\n", "application/json")
            elif url.path == "/state":
                self._reply(200, json.dumps(pod.state()) + "\n", "application/json")
            elif url.path == "/v1/models":
                self._reply(200, json.dumps({"data": [{"id": MODEL}]}) + "\n", "application/json")
            else:
                self._reply(404, "not found\n")

        def do_POST(self):
            """Fake streaming completion: a short pause (the 'prefill'), then tokens."""
            if self.path != "/v1/completions":
                self._reply(404, "not found\n")
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            max_tokens = int(body.get("max_tokens", 16))
            pod.tick()
            pod.ttft_count += 1
            ttft = 0.40 * (1.0 - 0.8 * pod.hit_rate)
            pod.ttft_sum += ttft
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            time.sleep(ttft)
            for i in range(max_tokens):
                chunk = {"choices": [{"text": f"tok{i} ", "index": 0}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.01)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def _reply(self, status: int, body: str, content_type: str = "text/plain"):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):   # keep the terminal quiet
            pass

    return Handler


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--hit-rate", type=float, default=0.5)
    parser.add_argument("--usage", type=float, default=0.3)
    parser.add_argument("--tokens-per-sec", type=float, default=1000.0)
    parser.add_argument("--requests-per-sec", type=float, default=2.0)
    parser.add_argument("--names", choices=sorted(SPELLINGS), default="new")
    args = parser.parse_args(argv)

    pod = FakePod(args.hit_rate, args.usage, args.tokens_per_sec, args.requests_per_sec, args.names)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(pod))
    print(f"fake vLLM on http://{args.bind}:{args.port}  hit_rate={pod.hit_rate} usage={pod.usage} names={args.names}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
