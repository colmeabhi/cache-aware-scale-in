"""Stage 2 — the cache-aware scale-in controller.

The loop, forever, every ``interval`` seconds:

    1. list the vLLM pods                       (kube.py)
    2. scrape each pod's /metrics               (kube.py + vllm_metrics.py)
    3. score each pod's cache                   (scoring.py)
    4. write   controller.kubernetes.io/pod-deletion-cost = score * 1000

When the Deployment is scaled down, Kubernetes removes the pod with the
lowest cost first, i.e. the one whose cache is worth the least.

Run against a real cluster (stage 2 onwards):

    python -m cacheaware.controller --namespace vllm --selector app=vllm

Run against fake pods on localhost with no Kubernetes at all (stage 1):

    python -m cacheaware.controller --static-targets a=http://127.0.0.1:9001,b=http://127.0.0.1:9002

Every setting can also come from an environment variable (see Settings), which
is how the in-cluster Deployment in stage 6 configures it.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .kube import Kube, PodInfo
from .scoring import PodHistory, Weights, cache_value, deletion_cost, worth_patching
from .vllm_metrics import fetch_text, sample_from_text

COST_ANNOTATION = "controller.kubernetes.io/pod-deletion-cost"

# Extra annotations so `kubectl describe pod` explains the number.
INFO_HIT_RATE = "cacheaware.dev/recent-hit-rate"
INFO_KV_USAGE = "cacheaware.dev/kv-usage"
INFO_VALUE = "cacheaware.dev/cache-value"
INFO_UPDATED = "cacheaware.dev/updated-at"


@dataclass
class Settings:
    namespace: str = "vllm"
    selector: str = "app=vllm"
    interval: float = 5.0          # seconds between cycles
    metrics_port: int = 8000
    metrics_path: str = "/metrics"
    scrape_mode: str = "auto"      # auto | direct | proxy
    weights: Weights = field(default_factory=Weights)
    ewma_alpha: float = 0.3
    min_tokens: float = 256        # ignore intervals with fewer lookups than this
    min_delta: int = 25            # dead band before re-patching (out of cost_scale)
    cost_scale: int = 1000
    max_inflight: int = 64         # what counts as "fully busy" for the inflight weight
    dry_run: bool = False          # compute and log, never patch
    kubeconfig: str | None = None

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> tuple["Settings", argparse.Namespace]:
        env = os.environ.get
        p = argparse.ArgumentParser(description="cache-aware scale-in controller for vLLM")
        p.add_argument("--namespace", default=env("NAMESPACE", "vllm"))
        p.add_argument("--selector", default=env("SELECTOR", "app=vllm"))
        p.add_argument("--interval", type=float, default=float(env("INTERVAL", "5")))
        p.add_argument("--metrics-port", type=int, default=int(env("METRICS_PORT", "8000")))
        p.add_argument("--metrics-path", default=env("METRICS_PATH", "/metrics"))
        p.add_argument("--scrape-mode", choices=["auto", "direct", "proxy"], default=env("SCRAPE_MODE", "auto"))
        p.add_argument("--w-hit", type=float, default=float(env("W_HIT", "0.7")))
        p.add_argument("--w-usage", type=float, default=float(env("W_USAGE", "0.3")))
        p.add_argument("--w-inflight", type=float, default=float(env("W_INFLIGHT", "0")))
        p.add_argument("--ewma-alpha", type=float, default=float(env("EWMA_ALPHA", "0.3")))
        p.add_argument("--min-tokens", type=float, default=float(env("MIN_TOKENS", "256")))
        p.add_argument("--min-delta", type=int, default=int(env("MIN_DELTA", "25")))
        p.add_argument("--cost-scale", type=int, default=int(env("COST_SCALE", "1000")))
        p.add_argument("--max-inflight", type=int, default=int(env("MAX_INFLIGHT", "64")))
        p.add_argument("--dry-run", action="store_true", default=env("DRY_RUN", "0") == "1")
        p.add_argument("--kubeconfig", default=env("KUBECONFIG_PATH"))
        p.add_argument("--once", action="store_true", help="run one cycle and exit")
        p.add_argument("--static-targets", default=env("STATIC_TARGETS"),
                       help="stage 1: name=url,name=url ... instead of Kubernetes")
        args = p.parse_args(argv)
        settings = cls(
            namespace=args.namespace, selector=args.selector, interval=args.interval,
            metrics_port=args.metrics_port, metrics_path=args.metrics_path,
            scrape_mode=args.scrape_mode,
            weights=Weights(args.w_hit, args.w_usage, args.w_inflight),
            ewma_alpha=args.ewma_alpha, min_tokens=args.min_tokens, min_delta=args.min_delta,
            cost_scale=args.cost_scale, max_inflight=args.max_inflight,
            dry_run=args.dry_run, kubeconfig=args.kubeconfig,
        )
        return settings, args


@dataclass
class PodReport:
    """What happened to one pod in one cycle (for the log and for tests)."""
    name: str
    hit_rate: float | None = None
    kv_usage: float | None = None
    running: float = 0.0
    value: float | None = None
    cost: int | None = None
    previous_cost: int | None = None
    action: str = ""          # patched | kept | skipped | error
    note: str = ""


# ----------------------------------------------------------------------------
# Backends: where pods come from and how we talk to them.
# ----------------------------------------------------------------------------

class KubeBackend:
    """Real pods from the Kubernetes API."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.kube = Kube(kubeconfig=settings.kubeconfig)

    def list_pods(self) -> list[PodInfo]:
        return self.kube.list_pods(self.s.namespace, self.s.selector)

    def scrape(self, pod: PodInfo) -> str:
        return self.kube.scrape(pod, self.s.metrics_port, self.s.metrics_path, self.s.scrape_mode)

    def annotate(self, pod: PodInfo, annotations: dict[str, str]) -> None:
        self.kube.annotate(pod.namespace, pod.name, annotations)

    def describe(self) -> str:
        where = "in-cluster" if self.kube.in_cluster else "kubeconfig"
        return f"kubernetes ({where}) namespace={self.s.namespace} selector={self.s.selector!r}"


class StaticBackend:
    """Stage 1: a fixed list of name=url targets, annotations kept in memory."""

    def __init__(self, targets: str):
        self.urls: dict[str, str] = {}
        for item in targets.split(","):
            name, url = item.split("=", 1)
            self.urls[name.strip()] = url.strip().rstrip("/")
        self.annotations: dict[str, dict[str, str]] = {name: {} for name in self.urls}

    def list_pods(self) -> list[PodInfo]:
        return [PodInfo(name=n, namespace="local", ip="127.0.0.1", phase="Running", ready=True,
                        node=None, created=None, annotations=dict(self.annotations[n]))
                for n in sorted(self.urls)]

    def scrape(self, pod: PodInfo) -> str:
        return fetch_text(self.urls[pod.name] + "/metrics")

    def annotate(self, pod: PodInfo, annotations: dict[str, str]) -> None:
        self.annotations[pod.name].update(annotations)

    def describe(self) -> str:
        return "static targets " + ", ".join(f"{n}={u}" for n, u in self.urls.items())


# ----------------------------------------------------------------------------
# The controller itself.
# ----------------------------------------------------------------------------

class Controller:
    def __init__(self, settings: Settings, backend):
        self.s = settings
        self.backend = backend
        self.histories: dict[str, PodHistory] = {}   # pod name -> counters we remember

    def run_once(self) -> list[PodReport]:
        pods = self.backend.list_pods()
        reports = [self.handle_pod(pod) for pod in pods]
        self.forget_missing(pods)
        return reports

    def handle_pod(self, pod: PodInfo) -> PodReport:
        report = PodReport(name=pod.name, previous_cost=read_cost(pod))

        if pod.phase != "Running" or pod.deleting:
            report.action, report.note = "skipped", pod.phase if not pod.deleting else "terminating"
            return report

        try:
            text = self.backend.scrape(pod)
        except Exception as exc:  # network blip, pod restarting, ...
            # Leave whatever cost it had. Wrongly writing 0 on a transient
            # error could get a hot pod deleted.
            report.action, report.note = "error", f"scrape failed: {exc}"[:80]
            return report

        sample = sample_from_text(text)
        history = self.histories.setdefault(pod.name, PodHistory())
        report.hit_rate = history.update(sample, self.s.ewma_alpha, self.s.min_tokens)
        report.kv_usage = sample.kv_usage
        report.running = sample.running
        inflight = (sample.running + sample.waiting) / max(1, self.s.max_inflight)
        report.value = cache_value(report.hit_rate, report.kv_usage, inflight, self.s.weights)
        report.cost = deletion_cost(report.value, self.s.cost_scale)

        if not worth_patching(report.previous_cost, report.cost, self.s.min_delta):
            report.action = "kept"
            return report

        annotations = {
            COST_ANNOTATION: str(report.cost),
            INFO_HIT_RATE: fmt(report.hit_rate),
            INFO_KV_USAGE: fmt(report.kv_usage),
            INFO_VALUE: fmt(report.value),
            INFO_UPDATED: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if self.s.dry_run:
            report.action = "dry-run"
        else:
            self.backend.annotate(pod, annotations)
            report.action = "patched"
        return report

    def forget_missing(self, pods: list[PodInfo]) -> None:
        alive = {p.name for p in pods}
        for name in list(self.histories):
            if name not in alive:
                del self.histories[name]

    def run_forever(self) -> None:
        stop = {"now": False}

        def ask_to_stop(signum, frame):
            stop["now"] = True

        signal.signal(signal.SIGTERM, ask_to_stop)
        signal.signal(signal.SIGINT, ask_to_stop)
        print(f"controller started: {self.backend.describe()} interval={self.s.interval}s "
              f"weights={self.s.weights} dry_run={self.s.dry_run}", flush=True)
        while not stop["now"]:
            started = time.monotonic()
            try:
                print(format_table(self.run_once()), flush=True)
            except Exception as exc:   # e.g. API server unreachable: log and retry
                print(f"cycle failed: {exc}", flush=True)
            time.sleep(max(0.0, self.s.interval - (time.monotonic() - started)))
        print("controller stopped", flush=True)


def read_cost(pod: PodInfo) -> int | None:
    raw = pod.annotations.get(COST_ANNOTATION)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def fmt(x: float | None) -> str:
    return "" if x is None else f"{x:.3f}"


def format_table(reports: list[PodReport]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    lines = [f"{stamp}  {'pod':<40} {'hit':>5} {'usage':>5} {'value':>5} {'cost':>5} {'was':>5}  action"]
    for r in reports:
        lines.append(f"          {r.name:<40} {pct(r.hit_rate):>5} {pct(r.kv_usage):>5} "
                     f"{pct(r.value):>5} {num(r.cost):>5} {num(r.previous_cost):>5}  {r.action} {r.note}")
    return "\n".join(lines)


def pct(x: float | None) -> str:
    return "  -  " if x is None else f"{x:.2f}"


def num(x: int | None) -> str:
    return "  -  " if x is None else str(x)


def main(argv: list[str] | None = None) -> int:
    settings, args = Settings.from_args(argv)
    backend = StaticBackend(args.static_targets) if args.static_targets else KubeBackend(settings)
    controller = Controller(settings, backend)
    if args.once:
        print(format_table(controller.run_once()))
        return 0
    controller.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
