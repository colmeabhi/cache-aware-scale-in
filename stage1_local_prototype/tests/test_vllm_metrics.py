"""Stage 1 tests: can we read every generation of vLLM metric names?"""
from cacheaware.vllm_metrics import parse_prometheus_text, sample_from_text

NEW_STYLE = """
# HELP vllm:kv_cache_usage_perc KV-cache usage.
# TYPE vllm:kv_cache_usage_perc gauge
vllm:kv_cache_usage_perc{model_name="m"} 0.42
vllm:prefix_cache_queries{model_name="m"} 1000.0
vllm:prefix_cache_hits{model_name="m"} 700.0
vllm:num_requests_running{model_name="m"} 3.0
vllm:time_to_first_token_seconds_bucket{le="0.1",model_name="m"} 5.0
vllm:time_to_first_token_seconds_sum{model_name="m"} 12.5
vllm:time_to_first_token_seconds_count{model_name="m"} 50.0
"""

GPU_STYLE = """
vllm:gpu_cache_usage_perc{model_name="m"} 0.2
vllm:gpu_prefix_cache_queries{model_name="m"} 400
vllm:gpu_prefix_cache_hits{model_name="m"} 100
"""

V0_STYLE = """
vllm:gpu_cache_usage_perc{model_name="m"} 0.9
vllm:gpu_prefix_cache_hit_rate{model_name="m"} 0.55
"""


def test_parse_skips_comments_and_sums_labels():
    text = 'a{x="1"} 1\na{x="2"} 2\n# comment\nb 5 1700000000\n'
    assert parse_prometheus_text(text) == {"a": 3.0, "b": 5.0}


def test_new_style_names():
    s = sample_from_text(NEW_STYLE)
    assert s.kv_usage == 0.42
    assert s.prefix_queries == 1000 and s.prefix_hits == 700
    assert s.cumulative_hit_rate == 0.7
    assert s.running == 3
    assert s.ttft_sum == 12.5 and s.ttft_count == 50
    assert s.found["prefix_hits"] == "vllm:prefix_cache_hits"
    assert s.has_cache_signal


def test_gpu_prefixed_names():
    s = sample_from_text(GPU_STYLE)
    assert s.kv_usage == 0.2
    assert s.cumulative_hit_rate == 0.25
    assert s.found["prefix_queries"] == "vllm:gpu_prefix_cache_queries"


def test_v0_gauge_only():
    s = sample_from_text(V0_STYLE)
    assert s.prefix_queries is None
    assert s.hit_rate_gauge == 0.55
    assert s.cumulative_hit_rate == 0.55
    assert s.has_cache_signal


def test_no_cache_metrics_at_all():
    s = sample_from_text("vllm:num_requests_running 1\n")
    assert not s.has_cache_signal
    assert s.cumulative_hit_rate is None
