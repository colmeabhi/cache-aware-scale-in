#!/usr/bin/env bash
# Port-forward Grafana, Prometheus and Loki to localhost. Ctrl-C stops all three.
# From a laptop, tunnel the same ports:  ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 user@gpu-box
set -euo pipefail
pids=()
trap 'kill "${pids[@]}" 2>/dev/null || true' EXIT
kubectl port-forward -n monitoring svc/grafana    "${GRAFANA_PORT:-3000}:3000" >/dev/null & pids+=($!)
kubectl port-forward -n monitoring svc/prometheus "${PROM_PORT:-9090}:9090"    >/dev/null & pids+=($!)
kubectl port-forward -n monitoring svc/loki       "${LOKI_PORT:-3100}:3100"    >/dev/null & pids+=($!)
echo "Grafana     http://localhost:${GRAFANA_PORT:-3000}/d/cache-aware-scale-in"
echo "Prometheus  http://localhost:${PROM_PORT:-9090}"
echo "Loki        http://localhost:${LOKI_PORT:-3100}/ready"
echo "Ctrl-C to stop"
wait
