#!/usr/bin/env bash
# Monitoring add-on — Prometheus + Loki + Alloy + Grafana, plain manifests, no Helm.
#
#   monitoring/setup.sh            # uses NAMESPACE / SELECTOR / METRICS_PORT from scripts/lib.sh
#   monitoring/open.sh             # port-forwards; then ssh -L from the laptop
#   monitoring/eval.sh             # proves every component ingests data
#
# Prometheus finds the vLLM pods with the same label selector the controller
# uses, so it works for the stage 3 Deployment and for llm-d's decode pods.
# Usable from stage 2 onwards (the fake pods expose the same metrics).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
need kubectl "install kubectl"
MON="$ROOT/monitoring"

# SELECTOR "app=vllm" -> label key "app", value "vllm". Prometheus writes label
# keys with every non-alphanumeric turned into "_" (llm-d.ai/role -> llm_d_ai_role).
key="${SELECTOR%%=*}"; value="${SELECTOR#*=}"
prom_key=$(printf '%s' "$key" | sed 's/[^A-Za-z0-9_]/_/g')
[ "$key" != "$SELECTOR" ] || die "SELECTOR must be key=value (got '$SELECTOR')"

log "applying monitoring stack (vLLM pods: label $key=$value, port $METRICS_PORT)"
kubectl apply -f "$MON/k8s/namespace.yaml" >/dev/null
sed "s|__VLLM_LABEL_KEY__|$prom_key|g; s|__VLLM_LABEL_VALUE__|$value|g; s|__METRICS_PORT__|$METRICS_PORT|g" \
    "$MON/k8s/prometheus.yaml" | kubectl apply -f - >/dev/null
kubectl apply -f "$MON/k8s/loki.yaml" >/dev/null
kubectl apply -f "$MON/k8s/alloy.yaml" >/dev/null
kubectl create configmap grafana-dashboards -n monitoring --from-file="$MON/grafana/" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f "$MON/k8s/grafana.yaml" >/dev/null

# a config change needs a restart to be picked up (ConfigMaps are not watched)
for d in prometheus loki alloy grafana; do
    kubectl rollout restart deployment -n monitoring "$d" >/dev/null 2>&1 || true
done
for d in prometheus loki alloy grafana; do
    kubectl rollout status deployment -n monitoring "$d" --timeout=300s >/dev/null
    log "$d ready"
done

echo
log "done. Reach it with:  monitoring/open.sh   (Grafana :3000, Prometheus :9090, Loki :3100)"
log "from a laptop:        ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 user@gpu-box"
log "Grafana dashboard:    http://localhost:3000/d/cache-aware-scale-in"
