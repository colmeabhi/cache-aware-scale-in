#!/usr/bin/env bash
# Stage 2 — run the controller from your shell against the cluster.
# It finds the pods with SELECTOR, scrapes them through the API-server proxy
# (no pod-network access needed) and patches pod-deletion-cost every 5 s.
#
#   stage2_mock_cluster/run_controller.sh            # patches for real
#   DRY_RUN=1 stage2_mock_cluster/run_controller.sh  # only prints
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
need kubectl "install kubectl"
"$PYTHON" -c "import kubernetes" 2>/dev/null || die "pip install -r $ROOT/requirements.txt"

exec "$PYTHON" -m cacheaware.controller --namespace "$NAMESPACE" --selector "$SELECTOR" \
    --metrics-port "$METRICS_PORT" --interval "${INTERVAL:-5}" $([ "${DRY_RUN:-0}" = 1 ] && echo --dry-run)
