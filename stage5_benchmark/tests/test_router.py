"""Cloud add-on: the prefix-affinity router."""
import json
import subprocess
import sys
import time
import urllib.request

import pytest

from cacheaware.router import Ring, routing_key


def test_same_prefix_same_pod():
    ring = Ring()
    ring.set_pods({"a": "http://a", "b": "http://b", "c": "http://c"})
    key = routing_key("/v1/completions", {"prompt": "x" * 600})
    key2 = routing_key("/v1/completions", {"prompt": "x" * 600 + "different tail"})
    assert key == key2 and ring.candidates(key)[0] == ring.candidates(key2)[0]


def test_candidates_cover_every_pod_once_for_failover():
    ring = Ring()
    ring.set_pods({"a": "http://a", "b": "http://b", "c": "http://c"})
    assert sorted(ring.candidates("anything")) == ["a", "b", "c"]


def test_removing_a_pod_only_moves_its_keys():
    ring = Ring()
    ring.set_pods({"a": "http://a", "b": "http://b", "c": "http://c"})
    keys = [f"prefix-{i}" for i in range(500)]
    before = {k: ring.candidates(k)[0] for k in keys}
    ring.set_pods({"a": "http://a", "b": "http://b"})
    after = {k: ring.candidates(k)[0] for k in keys}
    moved = [k for k in keys if before[k] != after[k]]
    assert all(before[k] == "c" for k in moved)          # only c's keys moved
    assert len(moved) == sum(1 for k in keys if before[k] == "c")


def test_routing_key_uses_prompt_or_system_message():
    assert routing_key("/v1/completions", {"prompt": "SYS " * 300})[:4] == "SYS "
    assert len(routing_key("/v1/completions", {"prompt": "p" * 2000})) == 512
    assert routing_key("/v1/chat/completions", {"messages": [{"role": "system", "content": "hello"}]}) == "hello"


@pytest.fixture
def fake_pods_and_router():
    procs = [subprocess.Popen([sys.executable, "-m", "cacheaware.fake_vllm", "--port", str(port), "--bind", "127.0.0.1"],
                              stdout=subprocess.DEVNULL) for port in (9301, 9302)]
    procs.append(subprocess.Popen([sys.executable, "-m", "cacheaware.router", "--listen", "127.0.0.1:9381",
                                   "--targets", "a=http://127.0.0.1:9301,b=http://127.0.0.1:9302"],
                                  stdout=subprocess.DEVNULL))
    time.sleep(1.0)
    yield "http://127.0.0.1:9381"
    for p in procs:
        p.terminate()


def test_proxies_streaming_completions_with_affinity(fake_pods_and_router):
    url = fake_pods_and_router
    models = json.load(urllib.request.urlopen(url + "/v1/models", timeout=5))
    assert models["data"][0]["id"]

    def ask(prefix):
        body = json.dumps({"model": "m", "prompt": prefix * 200 + " q", "max_tokens": 3, "stream": True}).encode()
        req = urllib.request.Request(url + "/v1/completions", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.headers["X-Routed-To"], r.read()

    pods_a = {ask("alpha ")[0] for _ in range(5)}
    pods_b = {ask("bravo ")[0] for _ in range(5)}
    assert len(pods_a) == 1 and len(pods_b) == 1      # every request of a prefix hits one pod
    assert b"data: [DONE]" in ask("alpha ")[1]       # the stream was passed through
    stats = json.load(urllib.request.urlopen(url + "/router/stats", timeout=5))
    assert sum(stats["requests_by_pod"].values()) == 12    # 11 completions + the /v1/models call
