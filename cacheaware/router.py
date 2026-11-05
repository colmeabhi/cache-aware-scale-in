"""Cloud add-on — a minimal prefix-affinity router, a stand-in for llm-d.

Why it exists. The whole benchmark relies on *different* pods holding
*different* prefixes. llm-d's gateway does that (prefix-aware routing). With
a plain Kubernetes Service (round-robin) every pod soon caches every prefix,
all caches are worth the same, and there is nothing for the controller to
choose. When llm-d is not installed (the hands-free cloud path), this router
gives the same property in ~150 lines:

    key   = first PREFIX_CHARS of the prompt            (the shared system prompt)
    pod   = consistent_hash_ring.lookup(key)             (same prefix -> same pod)
    proxy the request there, streaming the response back

Consistent hashing matters on scale-in: when a pod disappears, only *its*
keys move to other pods; everyone else keeps hitting their warm cache.

    python -m cacheaware.router --namespace vllm --selector app=vllm --listen 0.0.0.0:8081
    python -m cacheaware.router --targets a=http://127.0.0.1:9001,b=http://127.0.0.1:9002

Needs to reach pod IPs, so run it on the k3s node (or in-cluster).
Endpoints: POST /v1/completions, POST /v1/chat/completions, GET /v1/models,
GET /health, GET /router/stats.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import http.client
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PREFIX_CHARS = 512
VNODES = 64            # virtual nodes per pod: smoother key distribution


# ----------------------------------------------------------------------------
# The ring
# ----------------------------------------------------------------------------

def _h(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:16], 16)


class Ring:
    """Consistent hash ring over {pod name: base url}."""

    def __init__(self):
        self._lock = threading.Lock()
        self._points: list[int] = []
        self._owner: dict[int, str] = {}
        self.urls: dict[str, str] = {}

    def set_pods(self, urls: dict[str, str]) -> None:
        points, owner = [], {}
        for name in urls:
            for i in range(VNODES):
                p = _h(f"{name}#{i}")
                points.append(p)
                owner[p] = name
        points.sort()
        with self._lock:
            self._points, self._owner, self.urls = points, owner, dict(urls)

    def candidates(self, key: str) -> list[str]:
        """Pods for this key, best first, each pod once (for failover)."""
        with self._lock:
            if not self._points:
                return []
            start = bisect.bisect_left(self._points, _h(key)) % len(self._points)
            seen: list[str] = []
            for i in range(len(self._points)):
                name = self._owner[self._points[(start + i) % len(self._points)]]
                if name not in seen:
                    seen.append(name)
                if len(seen) == len(self.urls):
                    break
            return seen


# ----------------------------------------------------------------------------
# Pod discovery (Kubernetes or static)
# ----------------------------------------------------------------------------

def discover_loop(ring: Ring, args) -> None:
    from .kube import Kube
    kube = Kube(kubeconfig=args.kubeconfig)
    while True:
        try:
            pods = kube.list_pods(args.namespace, args.selector)
            live = {p.name: f"http://{p.ip}:{args.pod_port}" for p in pods
                    if p.phase == "Running" and p.ready and not p.deleting and p.ip}
            if live != ring.urls:
                print(f"router: pods -> {sorted(live)}", flush=True)
                ring.set_pods(live)
        except Exception as exc:
            print(f"router: discovery failed: {exc}", flush=True)
        time.sleep(args.refresh)


# ----------------------------------------------------------------------------
# The proxy
# ----------------------------------------------------------------------------

def routing_key(path: str, body: dict) -> str:
    if path.endswith("/chat/completions"):
        messages = body.get("messages") or []
        text = "".join(str(m.get("content", "")) for m in messages[:1])   # the system message
    else:
        prompt = body.get("prompt", "")
        text = prompt if isinstance(prompt, str) else json.dumps(prompt)
    return text[:PREFIX_CHARS]


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_pod: dict[str, int] = {}
        self.failovers = 0
        self.errors = 0


def make_handler(ring: Ring, stats: Stats, timeout: float):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"   # response ends when we close: no chunking needed

        def do_GET(self):
            if self.path == "/health":
                self._text(200 if ring.urls else 503, "ok\n" if ring.urls else "no pods\n")
            elif self.path == "/router/stats":
                with stats.lock:
                    self._text(200, json.dumps({"pods": ring.urls, "requests_by_pod": stats.by_pod,
                                                "failovers": stats.failovers, "errors": stats.errors}) + "\n")
            elif self.path.startswith("/v1/models"):
                self._forward("GET", self.path, b"", ring.candidates("models"))
            else:
                self._text(404, "not found\n")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._text(400, "bad json\n")
                return
            self._forward("POST", self.path, raw, ring.candidates(routing_key(self.path, body)))

        def _forward(self, method: str, path: str, raw: bytes, candidates: list[str]) -> None:
            """Try the best pod; on a connection failure before any byte, try the next."""
            if not candidates:
                self._text(503, "no pods\n")
                return
            for i, name in enumerate(candidates):
                url = urlparse(ring.urls.get(name, ""))
                if not url.hostname:
                    continue
                conn = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=timeout)
                try:
                    conn.request(method, path, raw, {"Content-Type": "application/json"})
                    resp = conn.getresponse()
                except OSError:
                    conn.close()
                    with stats.lock:
                        stats.failovers += 1
                    continue
                with stats.lock:
                    stats.by_pod[name] = stats.by_pod.get(name, 0) + 1
                    if i > 0:
                        stats.failovers += 1
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.getheader("Content-Type", "application/json"))
                self.send_header("X-Routed-To", name)
                self.end_headers()
                try:
                    while True:
                        line = resp.readline()     # SSE arrives line by line; pass each on at once
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                except OSError:
                    with stats.lock:
                        stats.errors += 1
                finally:
                    conn.close()
                return
            with stats.lock:
                stats.errors += 1
            self._text(502, "all pods failed\n")

        def _text(self, status: int, body: str):
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="prefix-affinity router (stand-in for llm-d)")
    p.add_argument("--listen", default=os.environ.get("ROUTER_LISTEN", "127.0.0.1:8081"))
    p.add_argument("--targets", default=os.environ.get("STATIC_TARGETS"), help="name=url,... instead of Kubernetes")
    p.add_argument("--namespace", default=os.environ.get("NAMESPACE", "vllm"))
    p.add_argument("--selector", default=os.environ.get("SELECTOR", "app=vllm"))
    p.add_argument("--pod-port", type=int, default=int(os.environ.get("METRICS_PORT", "8000")))
    p.add_argument("--refresh", type=float, default=2.0, help="seconds between pod list refreshes")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG_PATH"))
    args = p.parse_args(argv)

    ring, stats = Ring(), Stats()
    if args.targets:
        ring.set_pods({t.split("=", 1)[0].strip(): t.split("=", 1)[1].strip().rstrip("/") for t in args.targets.split(",")})
    else:
        threading.Thread(target=discover_loop, args=(ring, args), daemon=True).start()

    host, _, port = args.listen.rpartition(":")
    server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), make_handler(ring, stats, args.timeout))
    print(f"router listening on http://{args.listen}  prefix key = first {PREFIX_CHARS} chars", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
