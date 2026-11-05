"""Stage 1 tests: the control loop against an in-memory backend (no network)."""
from cacheaware.controller import COST_ANNOTATION, Controller, Settings
from cacheaware.kube import PodInfo
from cacheaware.scoring import Weights


def metrics(queries, hits, usage):
    return (f"vllm:kv_cache_usage_perc 0{usage}\n"
            f"vllm:prefix_cache_queries {queries}\n"
            f"vllm:prefix_cache_hits {hits}\n")


class MemoryBackend:
    """Three pods whose /metrics pages we script by hand."""

    def __init__(self):
        self.pages = {}
        self.annotations = {"a": {}, "b": {}, "c": {}}
        self.patches = []

    def list_pods(self):
        return [PodInfo(n, "ns", "10.0.0.1", "Running", True, None, None, dict(self.annotations[n]))
                for n in sorted(self.annotations)]

    def scrape(self, pod):
        if pod.name not in self.pages:
            raise RuntimeError("connection refused")
        return self.pages[pod.name]

    def annotate(self, pod, annotations):
        self.annotations[pod.name].update(annotations)
        self.patches.append(pod.name)

    def describe(self):
        return "memory"


def make_controller(backend):
    return Controller(Settings(weights=Weights(1.0, 0.0), min_delta=25), backend)


def test_lowest_hit_rate_gets_lowest_cost():
    b = MemoryBackend()
    b.pages = {"a": metrics(1000, 700, ".5"), "b": metrics(1000, 550, ".5"), "c": metrics(1000, 50, ".5")}
    c = make_controller(b)
    c.run_once()
    costs = {n: int(b.annotations[n][COST_ANNOTATION]) for n in "abc"}
    assert costs == {"a": 700, "b": 550, "c": 50}


def test_small_changes_are_not_patched():
    b = MemoryBackend()
    b.pages = {"a": metrics(1000, 700, ".5"), "b": metrics(1000, 550, ".5"), "c": metrics(1000, 50, ".5")}
    c = make_controller(b)
    c.run_once()
    assert len(b.patches) == 3
    # next interval: tiny movement for a (705 hits of 1000 new lookups), big drop for b
    b.pages["a"] = metrics(2000, 1405, ".5")
    b.pages["b"] = metrics(2000, 600, ".5")
    reports = {r.name: r for r in c.run_once()}
    assert reports["a"].action == "kept"
    assert reports["b"].action == "patched"


def test_scrape_error_keeps_old_cost():
    b = MemoryBackend()
    b.pages = {"a": metrics(1000, 700, ".5"), "b": metrics(1000, 550, ".5"), "c": metrics(1000, 50, ".5")}
    c = make_controller(b)
    c.run_once()
    del b.pages["a"]                 # pod a stops answering
    reports = {r.name: r for r in c.run_once()}
    assert reports["a"].action == "error"
    assert b.annotations["a"][COST_ANNOTATION] == "700"


def test_dry_run_never_patches():
    b = MemoryBackend()
    b.pages = {"a": metrics(10, 5, ".5"), "b": metrics(10, 5, ".5"), "c": metrics(10, 5, ".5")}
    c = Controller(Settings(dry_run=True), b)
    c.run_once()
    assert b.patches == []
