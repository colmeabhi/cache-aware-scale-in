"""Monitoring add-on — the controller's own Prometheus ``/metrics`` endpoint.

The pod-deletion-cost lives in an annotation, which Prometheus cannot see.
So the controller publishes what it decided, per pod, as ordinary gauges:

    cacheaware_pod_deletion_cost{pod="vllm-abc"}   613
    cacheaware_recent_hit_rate{pod="vllm-abc"}     0.70
    cacheaware_kv_usage{pod="vllm-abc"}            0.41
    cacheaware_cache_value{pod="vllm-abc"}         0.613
    cacheaware_patches_total                        42
    cacheaware_scrape_errors_total                  1
    cacheaware_cycle_duration_seconds               0.08
    cacheaware_last_cycle_timestamp_seconds         1.7e9

Standard library only (no prometheus_client), ~80 lines, one thread.
Enable with  --metrics-listen 0.0.0.0:9108  (or METRICS_LISTEN=...).
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

Labels = tuple[tuple[str, str], ...]


class Exporter:
    def __init__(self):
        self._lock = threading.Lock()
        self._gauges: dict[str, dict[Labels, float]] = {}
        self._counters: dict[str, dict[Labels, float]] = {}
        self._help: dict[str, str] = {}

    # ---- writing -------------------------------------------------------------

    def set(self, name: str, value: float, help_text: str = "", **labels: str) -> None:
        with self._lock:
            self._help.setdefault(name, help_text)
            self._gauges.setdefault(name, {})[_key(labels)] = float(value)

    def inc(self, name: str, by: float = 1.0, help_text: str = "", **labels: str) -> None:
        with self._lock:
            self._help.setdefault(name, help_text)
            series = self._counters.setdefault(name, {})
            series[_key(labels)] = series.get(_key(labels), 0.0) + by

    def drop_label_value(self, label: str, value: str) -> None:
        """Forget every gauge series with label=value (a pod that is gone)."""
        with self._lock:
            for series in self._gauges.values():
                for key in [k for k in series if (label, value) in k]:
                    del series[key]

    # ---- reading -------------------------------------------------------------

    def render(self) -> str:
        with self._lock:
            lines = []
            for kind, table in (("gauge", self._gauges), ("counter", self._counters)):
                for name in sorted(table):
                    lines.append(f"# HELP {name} {self._help.get(name, '')}")
                    lines.append(f"# TYPE {name} {kind}")
                    for key, value in sorted(table[name].items()):
                        lines.append(f"{name}{_fmt_labels(key)} {value}")
            return "\n".join(lines) + "\n"

    def serve(self, listen: str) -> None:
        """Start serving /metrics on host:port in a daemon thread."""
        host, _, port = listen.rpartition(":")
        exporter = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/metrics"):
                    body = exporter.render().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                else:
                    body = b"ok\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt, *args):
                pass

        server = ThreadingHTTPServer((host or "0.0.0.0", int(port)), Handler)
        threading.Thread(target=server.serve_forever, daemon=True, name="metrics-exporter").start()


def _key(labels: dict[str, str]) -> Labels:
    return tuple(sorted(labels.items()))


def _fmt_labels(key: Labels) -> str:
    if not key:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in key) + "}"
