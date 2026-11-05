#!/usr/bin/env bash
# Stage 3 — which metric names does THIS vLLM speak?
#
# Prints the vLLM version, the cache-related metric lines of every pod, and
# the controller's view of them (which spelling it picked). Saves everything
# to results/metrics_discovery_<time>.txt so you can quote it later.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
out="$RESULTS_DIR/metrics_discovery_$(date -u +%Y%m%dT%H%M%SZ).txt"

{
    for p in $(ready_pods); do
        echo "== $p"
        echo "-- vLLM version:"; scrape "$p" version 2>/dev/null || echo "(no /version endpoint)"; echo
        echo "-- raw cache / request metric lines:"
        scrape "$p" | grep -E '^vllm:(kv_cache|gpu_cache|prefix_cache|gpu_prefix_cache|num_requests|request_success|time_to_first_token_seconds_(sum|count))' || echo "(none!)"
        echo
        echo "-- what the controller reads:"
        scrape "$p" | "$PYTHON" -m cacheaware.vllm_metrics || true
        echo
    done
} | tee "$out"
log "saved to $out"
