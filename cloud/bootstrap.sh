#!/usr/bin/env bash
# Runs ON the GPU machine (as root / with sudo). Idempotent: safe to rerun.
# Turns a fresh Ubuntu box with NVIDIA drivers into a single-node k3s cluster
# that can schedule GPU pods, with this repo and its Python deps installed.
#
#   sudo cloud/bootstrap.sh
#
# Steps: apt basics -> NVIDIA container toolkit -> k3s -> RuntimeClass +
#        device plugin -> wait for nvidia.com/gpu -> pip deps -> HF cache dir
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
log() { printf '\033[1;34m[bootstrap %s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
export DEBIAN_FRONTEND=noninteractive

log "1/6 base packages"
command -v nvidia-smi >/dev/null || { echo "nvidia-smi missing: pick an image with the NVIDIA driver preinstalled"; exit 1; }
apt-get update -qq
apt-get install -y -qq curl git jq python3-pip python3-venv cmake build-essential >/dev/null
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

log "2/6 NVIDIA container toolkit (must exist BEFORE k3s starts so k3s registers the nvidia runtime)"
if ! command -v nvidia-container-runtime >/dev/null; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq && apt-get install -y -qq nvidia-container-toolkit >/dev/null
fi

log "3/6 k3s"
if ! command -v k3s >/dev/null; then
    curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
for _ in $(seq 1 60); do kubectl get nodes >/dev/null 2>&1 && break; sleep 2; done
grep -q nvidia /var/lib/rancher/k3s/agent/etc/containerd/config.toml || { log "k3s did not register the nvidia runtime; restarting k3s"; systemctl restart k3s; sleep 10; }

log "4/6 RuntimeClass + NVIDIA device plugin"
kubectl apply -f "$ROOT/cloud/k8s/nvidia.yaml" >/dev/null
for _ in $(seq 1 60); do
    n=$(kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' 2>/dev/null || true)
    [ -n "$n" ] && [ "$n" != "0" ] && break; sleep 5
done
[ -n "${n:-}" ] && [ "$n" != "0" ] || { echo "node never advertised nvidia.com/gpu; check: kubectl logs -n kube-system ds/nvidia-device-plugin"; exit 1; }
log "node advertises $n GPU(s)"

log "5/6 Python deps"
pip3 install -q --break-system-packages -r "$ROOT/requirements.txt" 2>/dev/null || pip3 install -q -r "$ROOT/requirements.txt"
[ "${TOOL:-loadgen}" = inference-perf ] && { pip3 install -q --break-system-packages inference-perf 2>/dev/null || pip3 install -q inference-perf; }

log "6/6 Hugging Face cache dir"
mkdir -p /var/lib/hf-cache && chmod 777 /var/lib/hf-cache
echo 'export KUBECONFIG=/etc/rancher/k3s/k3s.yaml' > /etc/profile.d/k3s.sh
log "bootstrap complete"
