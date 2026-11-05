"""Stage 5 tests: the report must read a trial folder correctly."""
import json

from cacheaware.report import (aggregate, first_window, server_ttft_mean, summarise_trial,
                               survivor_hit_rate, ttft_from_inference_perf)


def snapshot(**pods):
    return {"pods": {name: dict(zip(("queries", "hits", "ttft_sum", "ttft_count"), v))
                     for name, v in pods.items()}}


def test_survivor_hit_rate_ignores_the_removed_pod():
    pre = snapshot(a=(1000, 500, 1.0, 10), b=(1000, 100, 1.0, 10), c=(1000, 900, 1.0, 10))
    post = snapshot(a=(3000, 2100, 3.0, 30), b=(3000, 300, 2.0, 30))   # c is gone
    # survivors a and b: Δhits 1600+200 over Δqueries 2000+2000
    assert survivor_hit_rate(pre, post, "c") == 1800 / 4000
    assert server_ttft_mean(pre, post, "c") == (2.0 + 1.0) / (20 + 20)


def test_summarise_trial_with_loadgen_output(tmp_path):
    trial = tmp_path / "trial_1"
    (trial / "loadgen").mkdir(parents=True)
    (trial / "meta.json").write_text(json.dumps({"mode": "cache-aware", "trial": 1, "tool": "loadgen",
                                                 "removed_pod": "c", "removed_rank": 1,
                                                 "costs_at_scale_in": {"a": 700, "b": 550, "c": 50}}))
    (trial / "pre_snapshot.json").write_text(json.dumps(snapshot(a=(0, 0, 0, 0), b=(0, 0, 0, 0), c=(0, 0, 0, 0))))
    (trial / "post_snapshot.json").write_text(json.dumps(snapshot(a=(100, 80, 1, 10), b=(100, 40, 1, 10))))
    (trial / "loadgen" / "summary.json").write_text(json.dumps({
        "phases": {"measure": {"count": 40, "errors": 0, "ttft_p50": 0.2, "ttft_p90": 0.3}},
        "measure_windows": {"0-30s": {"count": 10, "errors": 0, "ttft_p50": 0.25, "ttft_p90": 0.35},
                            "30-60s": {"count": 10, "errors": 0, "ttft_p50": 0.15, "ttft_p90": 0.2}}}))
    s = summarise_trial(str(trial))
    assert s["removed_pod"] == "c" and s["removed_rank"] == 1
    assert s["survivor_hit_rate"] == 120 / 200
    assert first_window(s["ttft"])["ttft_p90"] == 0.35
    assert s["ttft"]["all"]["ttft_p50"] == 0.2


def test_inference_perf_reports_are_found_by_key_name(tmp_path):
    trial = tmp_path / "trial_1"
    (trial / "inference-perf").mkdir(parents=True)
    (trial / "inference-perf" / "stage_1_lifecycle_metrics.json").write_text(json.dumps({
        "latency": {"time_to_first_token": {"mean": 0.2, "p50": 0.18, "p90": 0.3, "p99": 0.4}},
        "successes": {"count": 50}, "failures": {"count": 1}}))
    w = ttft_from_inference_perf(str(trial))
    assert w["stage_1_lifecycle_metrics"]["ttft_p90"] == 0.3
    assert w["stage_1_lifecycle_metrics"]["errors"] == 1


def test_aggregate_counts_coldest_removed():
    trials = [
        {"mode": "cache-aware", "removed_rank": 1, "survivor_hit_rate": 0.6, "server_ttft_mean": 0.1,
         "ttft": {"0-30s": {"ttft_p50": 0.1, "ttft_p90": 0.2, "errors": 0}, "all": {"ttft_p50": 0.1, "ttft_p90": 0.2}}},
        {"mode": "baseline", "removed_rank": 3, "survivor_hit_rate": 0.3, "server_ttft_mean": 0.3,
         "ttft": {"0-30s": {"ttft_p50": 0.3, "ttft_p90": 0.4, "errors": 1}, "all": {"ttft_p50": 0.3, "ttft_p90": 0.4, "errors": 2}}},
    ]
    agg = aggregate(trials)
    assert agg["cache-aware"]["coldest_removed"] == 1
    assert agg["baseline"]["coldest_removed"] == 0
    assert agg["baseline"]["first_p90"][0] == 0.4
    assert agg["baseline"]["errors"] == 2
