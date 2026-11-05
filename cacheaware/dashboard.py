"""Stage 5 — the live dashboard web server.

It is a *reader only*: it serves dashboard.html and one JSON endpoint that
merges the files the sampler and the orchestrator write (see live.py). It
never talks to vLLM, Kubernetes or the GPU, so it cannot skew the benchmark.

    python -m cacheaware.dashboard --results results --port 8080

The scripts run on the GPU box, so run the server there and look at it from
your laptop through an SSH tunnel (the server binds to localhost only):

    ssh -L 8080:127.0.0.1:8080 user@gpu-box      # then open http://localhost:8080

Endpoints:
    /                  the page
    /api/state         {"status": {...}, "events": [...], "samples": {pod: [...]}, "trials": [...]}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def tail_jsonl(path: str, max_lines: int) -> list[dict]:
    """Last max_lines parsed lines of a JSON-lines file (missing file -> [])."""
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        chunk = min(size, max_lines * 400)      # ~400 bytes per line is plenty
        f.seek(size - chunk)
        lines = f.read().decode("utf-8", errors="replace").splitlines()[-max_lines:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass                                 # a line still being written
    return out


def build_state(results: str, sample_lines: int = 3000) -> dict:
    live = os.path.join(results, "live")
    status = {}
    try:
        with open(os.path.join(live, "status.json")) as f:
            status = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    samples_by_pod: dict[str, list[dict]] = {}
    for s in tail_jsonl(os.path.join(live, "samples.jsonl"), sample_lines):
        samples_by_pod.setdefault(s["pod"], []).append(s)

    trials = []
    for path in sorted(glob.glob(os.path.join(results, "bench", "*", "trial_*", "summary.json"))):
        try:
            with open(path) as f:
                trials.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "status": status,
        "events": tail_jsonl(os.path.join(live, "events.jsonl"), 60),
        "samples": samples_by_pod,
        "trials": trials,
    }


def make_handler(results: str):
    with open(os.path.join(HERE, "dashboard.html"), "rb") as f:
        page = f.read()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/state"):
                body = json.dumps(build_state(results)).encode()
                self._reply(200, body, "application/json")
            elif self.path in ("/", "/index.html"):
                self._reply(200, page, "text/html; charset=utf-8")
            else:
                self._reply(404, b"not found", "text/plain")

        def _reply(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="live benchmark dashboard (read-only)")
    parser.add_argument("--results", default=os.environ.get("RESULTS_DIR", "results"))
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1", help="use 0.0.0.0 only on a trusted network")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(args.results))
    print(f"dashboard: http://{args.bind}:{args.port}  (reading {args.results}/live)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
