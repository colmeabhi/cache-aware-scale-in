#!/usr/bin/env bash
# Monitoring add-on evaluation: is each component up AND receiving data?
# Everything goes through the API-server service proxy, so no port-forward is needed.
#
#   1. Prometheus ready; the vllm job has >= 1 target up; a vLLM cache metric is queryable
#   2. Loki ready; a log line from the vLLM namespace is queryable (Alloy -> Loki works)
#   3. Grafana healthy; both datasources provisioned; the dashboard exists
#   4. (if the controller runs with METRICS_LISTEN) cacheaware_pod_deletion_cost is queryable
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
problems=0
svc() { kubectl get --raw "/api/v1/namespaces/monitoring/services/$1/proxy/$2"; }   # svc NAME:PORT path
urlenc() { "$PYTHON" -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
prom_query() { svc prometheus:9090 "api/v1/query?query=$(urlenc "$1")" | "$PYTHON" -c 'import json,sys; r=json.load(sys.stdin)["data"]["result"]; print(len(r))'; }
verdict() { if [ "$2" = 1 ]; then ok "$1"; else fail "$1"; problems=$((problems+1)); fi; }

log "1. Prometheus"
svc prometheus:9090 "-/ready" >/dev/null 2>&1 && verdict "prometheus ready" 1 || verdict "prometheus ready" 0
n=$(prom_query 'up{job="vllm"} == 1' 2>/dev/null || echo 0)
verdict "vllm job: $n target(s) up (expected $(ready_pods | wc -l))" "$([ "$n" -ge 1 ] && echo 1 || echo 0)"
n=$(prom_query 'vllm:prefix_cache_queries or vllm:gpu_prefix_cache_queries or vllm:gpu_prefix_cache_hit_rate' 2>/dev/null || echo 0)
verdict "a vLLM prefix-cache metric is in Prometheus ($n series)" "$([ "$n" -ge 1 ] && echo 1 || echo 0)"
n=$(prom_query 'cacheaware_pod_deletion_cost' 2>/dev/null || echo 0)
if [ "$n" -ge 1 ]; then ok "controller metrics in Prometheus ($n pods)"; else log "note: no cacheaware_* metrics (controller not running with METRICS_LISTEN, or not in-cluster)"; fi

log "2. Loki (waiting up to 60 s for the first log lines)"
svc loki:3100 "ready" >/dev/null 2>&1 && verdict "loki ready" 1 || verdict "loki ready" 0
streams=0
for _ in $(seq 1 12); do
    streams=$(svc loki:3100 "loki/api/v1/query_range?query=$(urlenc "{namespace=\"$NAMESPACE\"}")&limit=5" 2>/dev/null \
        | "$PYTHON" -c 'import json,sys; print(len(json.load(sys.stdin)["data"]["result"]))' 2>/dev/null || echo 0)
    [ "$streams" -ge 1 ] && break; sleep 5
done
verdict "logs from namespace $NAMESPACE reach Loki ($streams stream(s))" "$([ "$streams" -ge 1 ] && echo 1 || echo 0)"

log "3. Grafana"
svc grafana:3000 "api/health" 2>/dev/null | grep -q '"database": *"ok"' && verdict "grafana healthy" 1 || verdict "grafana healthy" 0
ds=$(svc grafana:3000 "api/datasources" 2>/dev/null | "$PYTHON" -c 'import json,sys; print(",".join(sorted(d["type"] for d in json.load(sys.stdin))))' 2>/dev/null || echo "")
verdict "datasources provisioned: $ds" "$([ "$ds" = "loki,prometheus" ] && echo 1 || echo 0)"
svc grafana:3000 "api/dashboards/uid/cache-aware-scale-in" >/dev/null 2>&1 && verdict "dashboard cache-aware-scale-in provisioned" 1 || verdict "dashboard provisioned" 0

echo
[ "$problems" -eq 0 ] && ok "monitoring stack: PASS" || fail "monitoring stack: $problems problem(s)"
exit "$problems"
