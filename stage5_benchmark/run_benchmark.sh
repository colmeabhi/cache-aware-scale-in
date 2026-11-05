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
: "${GATEWAY_URL:?set GATEWAY_URL}"
TRIALS="${TRIALS:-3}"
export TRIALS

"$PYTHON" -m cacheaware.live --results "$RESULTS_DIR" sample --namespace "$NAMESPACE" --selector "$SELECTOR" \
    --metrics-port "$METRICS_PORT" --interval 2 > "$RESULTS_DIR/sampler.log" 2>&1 &
sampler_pid=$!
trap 'kill $sampler_pid 2>/dev/null || true; controller_off_quiet' EXIT
live_set trials="$TRIALS" phase=starting
log "sampler started (pid $sampler_pid); dashboard: stage5_benchmark/dashboard.sh"

for t in $(seq 1 "$TRIALS"); do
    "$ROOT/stage5_benchmark/run_trial.sh" baseline "$t"
    "$ROOT/stage5_benchmark/run_trial.sh" cache-aware "$t"
done
scale 3

echo; log "===== report ====="
"$PYTHON" -m cacheaware.report --results "$RESULTS_DIR"
