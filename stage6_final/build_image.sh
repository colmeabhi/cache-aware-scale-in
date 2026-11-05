#!/usr/bin/env bash
# Stage 6 — build the controller image and load it into the cluster.
#   k3s:  docker build -> docker save -> k3s ctr images import   (no registry needed)
#   kind: docker build -> kind load docker-image
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
IMAGE="${IMAGE:-cache-aware-controller:local}"
need docker "install docker"

log "building $IMAGE"
docker build -q -f "$ROOT/stage6_final/Dockerfile" -t "$IMAGE" "$ROOT" >/dev/null
if command -v k3s >/dev/null; then
    log "importing into k3s containerd"
    docker save "$IMAGE" | sudo k3s ctr images import - >/dev/null
elif command -v kind >/dev/null && kind get clusters 2>/dev/null | grep -q .; then
    log "loading into kind"
    kind load docker-image "$IMAGE" --name "$(kind get clusters | head -1)" >/dev/null
else
    log "no k3s/kind found: push $IMAGE to a registry your cluster can pull from and set the image in k8s/controller.yaml"
fi

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -n "$NAMESPACE" -f "$ROOT/stage6_final/k8s/rbac.yaml" >/dev/null
sed "s|value: \"app=vllm\"|value: \"$SELECTOR\"|; s|cache-aware-controller:local|$IMAGE|" "$ROOT/stage6_final/k8s/controller.yaml" \
    | kubectl apply -n "$NAMESPACE" -f - >/dev/null
kubectl rollout status deployment -n "$NAMESPACE" cache-aware-controller --timeout=120s
log "controller running in-cluster. Logs: kubectl logs -n $NAMESPACE deploy/cache-aware-controller -f"
log "use CONTROLLER_MODE=cluster with the stage 4/5 scripts so they switch THIS controller on and off"
