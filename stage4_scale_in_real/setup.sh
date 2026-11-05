#!/usr/bin/env bash
# Stage 4 — three real vLLM replicas (one GPU each).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
gpus=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}' | awk '{s+=$1} END {print s+0}')
[ "$gpus" -ge 3 ] || die "need 3 allocatable GPUs for 3 replicas, found $gpus"
scale 3
wait_ready 3 2400
echo; pod_table; echo
log "next: stage4_scale_in_real/eval.sh"
