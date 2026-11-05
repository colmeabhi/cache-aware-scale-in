"""Monitoring add-on: the controller's own /metrics endpoint."""
import urllib.request

from cacheaware.metrics_exporter import Exporter
from cacheaware.vllm_metrics import parse_prometheus_text


def test_render_gauges_and_counters():
    ex = Exporter()
    ex.set("cacheaware_pod_deletion_cost", 613, "cost", pod="a")
    ex.set("cacheaware_pod_deletion_cost", 50, "cost", pod="c")
    ex.inc("cacheaware_patches_total")
    ex.inc("cacheaware_patches_total")
    text = ex.render()
    assert 'cacheaware_pod_deletion_cost{pod="a"} 613.0' in text
    assert "# TYPE cacheaware_patches_total counter" in text
    assert parse_prometheus_text(text)["cacheaware_patches_total"] == 2
    assert parse_prometheus_text(text)["cacheaware_pod_deletion_cost"] == 663   # summed over pods


def test_drop_pod_series():
    ex = Exporter()
    ex.set("cacheaware_kv_usage", 0.5, pod="gone")
    ex.set("cacheaware_kv_usage", 0.2, pod="stays")
    ex.drop_label_value("pod", "gone")
    assert 'pod="gone"' not in ex.render()
    assert 'pod="stays"' in ex.render()


def test_serves_over_http():
    ex = Exporter()
    ex.set("cacheaware_pods_seen", 3)
    ex.serve("127.0.0.1:9177")
    body = urllib.request.urlopen("http://127.0.0.1:9177/metrics", timeout=3).read().decode()
    assert "cacheaware_pods_seen 3.0" in body
