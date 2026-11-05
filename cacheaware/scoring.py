"""Stage 1 — turn vLLM cache signals into a ``pod-deletion-cost``.

The whole idea in three lines:

    recent_hit_rate = hits_in_last_window / lookups_in_last_window   (0..1)
    cache_value     = 0.7 * recent_hit_rate + 0.3 * kv_usage           (0..1)
    deletion_cost   = round(cache_value * 1000)                        (int)

Kubernetes deletes the pod with the *lowest* cost first, so the pod whose
cache is worth the least goes first.

Why "recent" and not "since start"?  vLLM only exposes cumulative counters.
A pod that served a lot of cache hits an hour ago but nothing since still has
a high lifetime hit rate, yet its cache is not helping anyone right now. We
remember the last counter values per pod, take the delta between scrapes and
smooth it with an exponential moving average (EWMA) so one noisy interval
does not flip the ranking.

Why include kv_usage at all?  Hit rate says how *useful* the cached prefixes
are; usage says how *much* is cached. A pod with a 70 % hit rate on a nearly
empty cache is cheap to replace; the same hit rate on a full cache is not.
"""
from __future__ import annotations

from dataclasses import dataclass

from .vllm_metrics import VllmSample


@dataclass
class Weights:
    """How much each signal counts. They are normalised, so 7/3/0 == 0.7/0.3/0."""

    hit_rate: float = 0.7
    kv_usage: float = 0.3
    inflight: float = 0.0   # requests in flight; off by default (see guide.md)

    def normalised(self) -> "Weights":
        total = self.hit_rate + self.kv_usage + self.inflight
        if total <= 0:
            raise ValueError("at least one weight must be positive")
        return Weights(self.hit_rate / total, self.kv_usage / total, self.inflight / total)


@dataclass
class PodHistory:
    """What we remember about one pod between two scrapes."""

    last_queries: float | None = None
    last_hits: float | None = None
    recent_hit_rate: float | None = None   # the EWMA; None until we know anything

    def update(self, sample: VllmSample, alpha: float = 0.3, min_tokens: float = 256) -> float | None:
        """Feed one new sample and return the recent hit rate (None = unknown).

        alpha      EWMA weight of the newest interval (0.3 = ~3 intervals memory)
        min_tokens below this many lookups in an interval we learn nothing new
        """
        queries, hits = sample.prefix_queries, sample.prefix_hits

        if queries is None or hits is None:
            # Old vLLM: no counters, only a since-start gauge. Best we can do.
            self.recent_hit_rate = sample.hit_rate_gauge
            return self.recent_hit_rate

        first_sample = self.last_queries is None
        counters_reset = not first_sample and queries < self.last_queries
        if first_sample or counters_reset:
            # Start from the lifetime rate (a fresh pod has 0 queries -> None).
            self.last_queries, self.last_hits = queries, hits
            self.recent_hit_rate = hits / queries if queries > 0 else None
            return self.recent_hit_rate

        delta_queries = queries - self.last_queries
        delta_hits = hits - self.last_hits
        self.last_queries, self.last_hits = queries, hits

        if delta_queries < min_tokens:
            return self.recent_hit_rate   # idle interval: keep what we knew

        interval_rate = min(1.0, max(0.0, delta_hits / delta_queries))
        if self.recent_hit_rate is None:
            self.recent_hit_rate = interval_rate
        else:
            self.recent_hit_rate = alpha * interval_rate + (1 - alpha) * self.recent_hit_rate
        return self.recent_hit_rate


def cache_value(hit_rate: float | None, kv_usage: float | None,
                inflight_fraction: float = 0.0, weights: Weights | None = None) -> float:
    """0.0 = the cache is worth nothing, 1.0 = every lookup hits and the cache is full."""
    w = (weights or Weights()).normalised()
    value = (w.hit_rate * clamp01(hit_rate)
             + w.kv_usage * clamp01(kv_usage)
             + w.inflight * clamp01(inflight_fraction))
    return clamp01(value)


def deletion_cost(value: float, scale: int = 1000) -> int:
    """The integer Kubernetes compares. 0.613 -> 613."""
    return int(round(clamp01(value) * scale))


def worth_patching(current_cost: int | None, new_cost: int, min_delta: int = 25) -> bool:
    """Only touch the pod when the cost moved enough to matter.

    Every patch is an API-server write; Kubernetes' own docs warn against
    updating pod-deletion-cost on every metric tick. A small dead band keeps
    the ranking fresh without hammering the API server.
    """
    if current_cost is None:
        return True
    return abs(new_cost - current_cost) >= min_delta


def clamp01(x: float | None) -> float:
    if x is None:
        return 0.0
    return max(0.0, min(1.0, float(x)))
