"""Stage 3 tests for the load generator (no server needed)."""
import random

from cacheaware.loadgen import (RequestResult, make_prefix, make_question, percentile, stats,
                                zipf_weights)


def test_prefix_is_deterministic_and_long():
    a, b = make_prefix(3, 1200, seed=42), make_prefix(3, 1200, seed=42)
    assert a == b                                    # same group + seed -> same text (cache hit)
    assert make_prefix(4, 1200, seed=42) != a        # other group -> other prefix
    assert len(a.split()) >= 1200


def test_questions_are_unique():
    rng = random.Random(1)
    assert make_question(1, rng) != make_question(2, rng)


def test_zipf_is_skewed_and_uniform_is_flat():
    w = zipf_weights(4, 1.0)
    assert w[0] > w[1] > w[2] > w[3]
    assert zipf_weights(4, 0.0) == [1.0] * 4


def test_percentiles():
    xs = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert percentile(xs, 50) == 0.3
    assert percentile(xs, 100) == 0.5
    assert percentile(xs, 0) == 0.1


def test_stats_counts_errors_and_ignores_missing_ttft():
    rs = [RequestResult(0, 0, "measure", 0.0, 0.10, 0.5, 200),
          RequestResult(1, 0, "measure", 0.1, 0.30, 0.6, 200),
          RequestResult(2, 0, "measure", 0.2, None, None, 0, error="timeout")]
    s = stats(rs)
    assert s["count"] == 3 and s["errors"] == 1
    assert s["ttft_p50"] == 0.2
