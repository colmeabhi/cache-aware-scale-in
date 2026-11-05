#!/usr/bin/env bash
# Stage 6 — go / no-go check before spending GPU time. Read-only.
#   GATEWAY_URL=http://... stage6_final/preflight.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
problems=0
check() {   # check <command string>, evaluated in THIS shell so lib.sh functions are available
    if eval "$*" >/dev/null 2>&1; then ok "$NAME"; else fail "$NAME"; problems=$((problems+1)); fi
}

NAME="kubectl installed";                 check command -v kubectl
NAME="cluster reachable";                 check kubectl cluster-info
NAME="python3 >= 3.10";                   check "$PYTHON -c 'import sys; assert sys.version_info >= (3, 10)'"
NAME="python kubernetes client";          check "$PYTHON -c 'import kubernetes'"
NAME="curl installed";                    check command -v curl
NAME="namespace $NAMESPACE exists";       check kubectl get namespace "$NAMESPACE"
NAME="a node advertises nvidia.com/gpu";  check "kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}' | grep -q '[1-9]'"
NAME="deployment $DEPLOYMENT exists";     check kubectl get deployment -n "$NAMESPACE" "$DEPLOYMENT"
NAME="pods matching '$SELECTOR' are ready"; check "[ \$(ready_pods | wc -l) -ge 1 ]"
first=$(ready_pods | head -1 || true)
if [ -n "$first" ]; then
    NAME="metrics scrape of $first through API proxy"; check scrape "$first"
    NAME="vLLM exposes a prefix-cache metric";   check "scrape $first | $PYTHON -m cacheaware.vllm_metrics"
fi
NAME="server version >= 1.22 (pod-deletion-cost on by default)"; check "kubectl version -o json | $PYTHON -c 'import json,sys; v=json.load(sys.stdin)[\"serverVersion\"]; assert int(v[\"minor\"].rstrip(\"+\")) >= 22'"
if [ -n "${GATEWAY_URL:-}" ]; then
    NAME="gateway $GATEWAY_URL answers /v1/models";  check curl -fs "$GATEWAY_URL/v1/models"
else
    fail "GATEWAY_URL not set (needed for stage 5)"; problems=$((problems+1))
fi
NAME="inference-perf installed (optional: TOOL=loadgen works without it)"; command -v inference-perf >/dev/null && ok "$NAME" || log "note: $NAME"
NAME="nvcc (for the N-body benchmarks)"; command -v nvcc >/dev/null && ok "$NAME" || log "note: $NAME not found"
NAME="ncu / Nsight Compute (optional, N-body profiling)"; command -v ncu >/dev/null && ok "$NAME" || log "note: $NAME not found"
NAME="monitoring stack installed (optional: monitoring/setup.sh)"; kubectl get svc -n monitoring grafana >/dev/null 2>&1 && ok "$NAME" || log "note: $NAME"
NAME="matplotlib (optional, charts)"; "$PYTHON" -c 'import matplotlib' 2>/dev/null && ok "$NAME" || log "note: $NAME not installed"
echo
df -h "$RESULTS_DIR" | sed 's/^/  /'
echo
[ "$problems" -eq 0 ] && ok "GO: $problems problems" || fail "NO-GO: $problems problem(s) above"
exit "$problems"
