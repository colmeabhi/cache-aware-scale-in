#!/usr/bin/env bash
# Stage 2 — put the fake vLLM pods into a real Kubernetes cluster.
#
# Needs a cluster that kubectl can reach. If there is none and `kind` is
# installed, one is created for you (docker required). On the GPU box, k3s
# is already there: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml first.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
need kubectl "https://kubernetes.io/docs/tasks/tools/"

if ! kubectl cluster-info >/dev/null 2>&1; then
    if command -v kind >/dev/null; then
        log "no cluster reachable: creating a kind cluster named 'cacheaware'"
        kind create cluster --name cacheaware --wait 120s
    elif command -v k3d >/dev/null; then
        log "no cluster reachable: creating a k3d cluster named 'cacheaware'"
        k3d cluster create cacheaware --wait
    else
        die "no cluster reachable and neither kind nor k3d installed. On k3s: export KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
    fi
fi
log "cluster: $(kubectl config current-context)  server $(kubectl version -o json 2>/dev/null | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])')"

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl create configmap fake-vllm -n "$NAMESPACE" --from-file=fake_vllm.py="$ROOT/cacheaware/fake_vllm.py" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -n "$NAMESPACE" -f "$ROOT/stage2_mock_cluster/k8s/mock-vllm.yaml" >/dev/null
wait_ready 3 300

echo; pod_table; echo
first=$(ready_pods | head -1)
log "sample of $first metrics through the API-server proxy:"
scrape "$first" | grep -E '^vllm:(kv_cache|prefix_cache)' | sed 's/^/    /'
echo
log "next: stage2_mock_cluster/run_controller.sh in one terminal, stage2_mock_cluster/eval.sh in another"
