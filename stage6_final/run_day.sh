#!/usr/bin/env bash
# Stage 6 — the GPU day, start to finish. Each phase pauses for Enter unless
# PAUSE=0, so you can look at the dashboard/logs between phases.
#
#   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_... GATEWAY_URL=http://...
#   stage6_final/run_day.sh
#
# Phases:   preflight -> (MONITORING=1) Prometheus/Loki/Grafana
#        -> (stage 3 setup if no vLLM yet) -> discover metrics
#        -> stage 3 eval -> stage 4 setup + eval -> stage 5 eval -> report
#        -> (NBODY=1) N-body benchmarks on the same GPU
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
PAUSE="${PAUSE:-1}"
pause() { echo; log "next: $*"; if [ "$PAUSE" = 1 ]; then read -r -p "        press Enter to continue (Ctrl-C to stop) "; fi; }

log "GPU day runner. Results go to $RESULTS_DIR"
if [ "${MONITORING:-1}" = 1 ]; then
    pause "monitoring add-on: Prometheus + Loki + Alloy + Grafana (MONITORING=0 to skip)"
    "$ROOT/monitoring/setup.sh" && "$ROOT/monitoring/eval.sh" || log "monitoring eval reported problems; the benchmark does not depend on it"
fi
"$ROOT/stage6_final/preflight.sh" || { pause "preflight reported problems. Continue anyway?"; }

if ! kubectl get deployment -n "$NAMESPACE" "$DEPLOYMENT" >/dev/null 2>&1; then
    pause "stage 3: deploy one vLLM replica (downloads weights on first start)"
    "$ROOT/stage3_vllm_single/setup.sh"
fi
pause "stage 3: discover which metric names this vLLM exposes"
"$ROOT/stage3_vllm_single/discover_metrics.sh"
pause "stage 3 eval: prefix caching works on one pod"
"$ROOT/stage3_vllm_single/eval.sh"

pause "stage 4: scale to 3 replicas and verify scale-in removes the coldest pod (~25 min)"
"$ROOT/stage4_scale_in_real/setup.sh"
"$ROOT/stage4_scale_in_real/eval.sh" || log "stage 4 eval reported a failure; read the table above before going on"

pause "stage 5: benchmark baseline vs cache-aware (~${TRIALS:-3} x 2 trials, ~8 min each). Start stage5_benchmark/dashboard.sh in another terminal now."
"$ROOT/stage5_benchmark/eval.sh"

echo; log "===== DONE: the numbers to send ====="
sed -n '/## Headline/,/## Per trial/p' "$RESULTS_DIR/RESULTS.md"
log "full report: $RESULTS_DIR/RESULTS.md  chart: $RESULTS_DIR/ttft_after_scale_in.png"

if [ "${NBODY:-1}" = 1 ]; then
    pause "N-body GPU benchmarks on this A100 (~1 h, GPU must be otherwise idle: scale vllm to 0 first?)"
    "$ROOT/stage6_final/nbody_a100.sh"
fi
