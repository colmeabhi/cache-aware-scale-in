#!/usr/bin/env bash
# Stage 3 — one real vLLM replica on the GPU box.
#
#   export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
#   export HF_TOKEN=hf_xxx                      # Llama 3 is gated on Hugging Face
#   stage3_vllm_single/setup.sh
#
# Variables: MODEL [meta-llama/Meta-Llama-3-8B-Instruct]  VLLM_IMAGE [vllm/vllm-openai:latest]
#
# If llm-d already runs vLLM for you, skip this and point NAMESPACE/SELECTOR/
# DEPLOYMENT at llm-d's decode Deployment instead (see guide.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
MODEL="${MODEL:-meta-llama/Meta-Llama-3-8B-Instruct}"
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
need kubectl "install kubectl"

gpus=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.allocatable.nvidia\.com/gpu}{" "}{end}' | tr -s ' ')
[ -n "${gpus// /}" ] || die "no node advertises nvidia.com/gpu. Install the NVIDIA device plugin / GPU operator first."
log "allocatable GPUs per node: $gpus"

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
if ! kubectl get secret hf-token -n "$NAMESPACE" >/dev/null 2>&1; then
    [ -n "${HF_TOKEN:-}" ] || die "export HF_TOKEN=hf_... (needed once to create the hf-token secret)"
    kubectl create secret generic hf-token -n "$NAMESPACE" --from-literal=token="$HF_TOKEN" >/dev/null
    log "created secret hf-token"
fi

sed "s|__MODEL__|$MODEL|g; s|__IMAGE__|$VLLM_IMAGE|g" "$ROOT/stage3_vllm_single/k8s/vllm.yaml" \
    | kubectl apply -n "$NAMESPACE" -f - >/dev/null
log "applied vllm Deployment (model=$MODEL image=$VLLM_IMAGE). First start downloads ~16 GB of weights."
log "watch with:  kubectl logs -n $NAMESPACE deploy/$DEPLOYMENT -f"
wait_ready 1 2400

echo; pod_table; echo
log "next: stage3_vllm_single/discover_metrics.sh"
