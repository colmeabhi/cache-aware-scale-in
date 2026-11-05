#!/usr/bin/env bash
# Stage 5 — the whole benchmark: N trials of each mode, interleaved, then the report.
#
#   GATEWAY_URL=http://... TRIALS=3 stage5_benchmark/run_benchmark.sh
#
# Interleaving (baseline, cache-aware, baseline, ...) means slow drifts in the
# machine (thermal, other tenants, model warm-up) hit both modes equally.
# Starts the metrics sampler for the dashboard; run stage5_benchmark/dashboard.sh
# in another terminal to watch.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
TRIALS="${TRIALS:-3}"
export TRIALS
router_pid=""
if [ "${ROUTER:-}" = builtin ] || [ -z "${GATEWAY_URL:-}" ]; then
    # No llm-d: run the built-in prefix-affinity router (same prefix -> same pod).
    # It talks to pod IPs, so this script must run on the k3s node or in-cluster.
    export GATEWAY_URL="${GATEWAY_URL:-http://127.0.0.1:8081}"
    listen="${GATEWAY_URL#http://}"
    "$PYTHON" -m cacheaware.router --listen "$listen" --namespace "$NAMESPACE" --selector "$SELECTOR" \
        --pod-port "$METRICS_PORT" > "$RESULTS_DIR/router.log" 2>&1 &
    router_pid=$!
    for _ in $(seq 1 30); do curl -fs "$GATEWAY_URL/health" >/dev/null 2>&1 && break; sleep 1; done
    curl -fs "$GATEWAY_URL/health" >/dev/null || die "built-in router did not come up (see $RESULTS_DIR/router.log)"
    log "built-in prefix-affinity router on $GATEWAY_URL (pid $router_pid)"
fi

"$PYTHON" -m cacheaware.live --results "$RESULTS_DIR" sample --namespace "$NAMESPACE" --selector "$SELECTOR" \
    --metrics-port "$METRICS_PORT" --interval 2 > "$RESULTS_DIR/sampler.log" 2>&1 &
sampler_pid=$!
trap 'kill $sampler_pid $router_pid 2>/dev/null || true; controller_off_quiet' EXIT
live_set trials="$TRIALS" phase=starting
log "sampler started (pid $sampler_pid); dashboard: stage5_benchmark/dashboard.sh"

for t in $(seq 1 "$TRIALS"); do
    "$ROOT/stage5_benchmark/run_trial.sh" baseline "$t"
    "$ROOT/stage5_benchmark/run_trial.sh" cache-aware "$t"
done
scale 3

echo; log "===== report ====="
"$PYTHON" -m cacheaware.report --results "$RESULTS_DIR"
