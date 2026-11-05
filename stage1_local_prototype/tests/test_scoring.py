"""Stage 1 tests: does the scoring do what the guide says it does?"""
import pytest

from cacheaware.scoring import (PodHistory, Weights, cache_value, deletion_cost,
                                worth_patching)
from cacheaware.vllm_metrics import VllmSample


def sample(queries, hits, usage=0.5):
    return VllmSample(kv_usage=usage, prefix_queries=queries, prefix_hits=hits)


def test_cost_is_hit_rate_times_1000_when_only_hit_rate_counts():
    w = Weights(hit_rate=1.0, kv_usage=0.0)
    assert deletion_cost(cache_value(0.70, 0.9, weights=w)) == 700
    assert deletion_cost(cache_value(0.55, 0.9, weights=w)) == 550
    assert deletion_cost(cache_value(0.05, 0.9, weights=w)) == 50


def test_default_weights_blend_hit_rate_and_usage():
    # 0.7 * 0.5 + 0.3 * 1.0 = 0.65
    assert deletion_cost(cache_value(0.5, 1.0)) == 650


def test_weights_are_normalised():
    assert cache_value(0.5, 1.0, weights=Weights(7, 3, 0)) == pytest.approx(0.65)


def test_unknown_signals_count_as_zero():
    assert cache_value(None, None) == 0.0
    assert deletion_cost(cache_value(None, None)) == 0


def test_hotter_pod_always_gets_higher_cost():
    cold = deletion_cost(cache_value(0.05, 0.3))
    warm = deletion_cost(cache_value(0.55, 0.3))
    hot = deletion_cost(cache_value(0.70, 0.3))
    assert cold < warm < hot


def test_recent_hit_rate_uses_deltas_not_lifetime():
    h = PodHistory()
    # Lifetime: 9000 hits of 10000 -> 0.9. Pod was hot once.
    assert h.update(sample(10000, 9000)) == pytest.approx(0.9)
    # Next interval: 1000 lookups, 0 hits -> the pod is cold *now*.
    rate = h.update(sample(11000, 9000), alpha=1.0)   # alpha=1: no smoothing
    assert rate == pytest.approx(0.0)


def test_ewma_smooths():
    h = PodHistory()
    h.update(sample(1000, 1000))                   # starts at 1.0
    rate = h.update(sample(2000, 1000), alpha=0.3)  # interval rate 0.0
    assert rate == pytest.approx(0.7)              # 0.3*0 + 0.7*1.0


def test_idle_interval_keeps_last_value():
    h = PodHistory()
    h.update(sample(1000, 500))
    assert h.update(sample(1010, 505), min_tokens=256) == pytest.approx(0.5)


def test_counter_reset_starts_over():
    h = PodHistory()
    h.update(sample(5000, 4000))
    assert h.update(sample(100, 10)) == pytest.approx(0.1)   # pod restarted


def test_fresh_pod_has_no_hit_rate_yet():
    assert PodHistory().update(sample(0, 0)) is None


def test_v0_gauge_is_passed_through():
    s = VllmSample(kv_usage=0.1, hit_rate_gauge=0.33)
    assert PodHistory().update(s) == pytest.approx(0.33)


def test_dead_band():
    assert worth_patching(None, 500)
    assert worth_patching(500, 530, min_delta=25)
    assert not worth_patching(500, 510, min_delta=25)
