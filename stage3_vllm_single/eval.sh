#!/usr/bin/env bash
# Stage 3 evaluation: does prefix caching actually work on this vLLM?
#
# Sends 40 requests that share 2 long prefixes straight to the single pod
# (through a port-forward) and checks the pod's prefix-cache hit counters
# moved: hit rate over the run must be > 0.5. Also prints the TTFT the load
# generator saw, so you get a first feel for the numbers.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
need curl "install curl"
trap stop_port_forwards EXIT

pod=$(ready_pods | head -1); [ -n "$pod" ] || die "no ready vLLM pod"
port_forward "$pod" 18000
log "warming $pod with 40 requests over 2 shared prefixes"

scrape "$pod" > "$RESULTS_DIR/stage3_before.prom"
"$PYTHON" -m cacheaware.loadgen warm --url http://127.0.0.1:18000 --requests 40 --groups 2 --concurrency 4 \
    --out "$RESULTS_DIR/stage3_warm"
scrape "$pod" > "$RESULTS_DIR/stage3_after.prom"

"$PYTHON" - "$RESULTS_DIR/stage3_before.prom" "$RESULTS_DIR/stage3_after.prom" <<'PY'
import sys
from cacheaware.vllm_metrics import sample_from_text
b, a = sample_from_text(open(sys.argv[1]).read()), sample_from_text(open(sys.argv[2]).read())
if b.prefix_queries is None:
    rate = a.hit_rate_gauge
    print(f"  old vLLM: lifetime hit-rate gauge now {rate:.3f}")
else:
    dq, dh = a.prefix_queries - b.prefix_queries, a.prefix_hits - b.prefix_hits
    rate = dh / dq if dq else 0.0
    print(f"  prefix-cache lookups during the run: {dq:.0f} tokens, hits: {dh:.0f} tokens, hit rate {rate:.3f}")
print(f"  metric names in use: {a.found.get('prefix_hits') or a.found.get('hit_rate_gauge')}, {a.found.get('kv_usage')}")
ok = rate is not None and rate > 0.5
print("\n  PASS: prefix caching works" if ok else "\n  FAIL: hit rate too low; is --enable-prefix-caching on? are prompts identical up to the question?")
sys.exit(0 if ok else 1)
PY
