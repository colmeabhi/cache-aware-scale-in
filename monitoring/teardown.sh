#!/usr/bin/env bash
# Remove the monitoring stack (namespace + cluster-wide RBAC). Nothing else is touched.
set -euo pipefail
kubectl delete namespace monitoring --ignore-not-found
kubectl delete clusterrole,clusterrolebinding prometheus alloy --ignore-not-found
echo "monitoring stack removed"
