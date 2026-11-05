#!/usr/bin/env bash
# The one command: get a GPU machine, set it up, run the whole GPU day, bring
# the results back, destroy the machine. Works from a laptop or from CI.
#
#   cp cloud/.env.example cloud/.env   # fill it in
#   cloud/run_remote.sh
#
# Steps:
#   1. provider_create / provider_wait_ip  (cloud/providers/$GPU_PROVIDER.sh)
#   2. wait for SSH
#   3. copy this repo to the box (git archive over ssh, no rsync needed)
#   4. sudo cloud/bootstrap.sh on the box
#   5. PAUSE=0 stage6_final/run_day.sh on the box, detached; stream its log
#   6. tar the results back into results/remote-<timestamp>/
#   7. provider_destroy (always, unless KEEP_INSTANCE=1)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$ROOT/cloud/.env" ] && { set -a; source "$ROOT/cloud/.env"; set +a; }
: "${GPU_PROVIDER:=manual}" "${GPU_SSH_USER:=ubuntu}" "${GPU_SSH_KEY:=$HOME/.ssh/id_ed25519}"
: "${TRIALS:=3}" "${TOOL:=loadgen}" "${NBODY:=1}" "${MONITORING:=1}" "${KEEP_INSTANCE:=0}"
: "${HF_TOKEN:?set HF_TOKEN in cloud/.env}"
GPU_SSH_KEY="${GPU_SSH_KEY/#\~/$HOME}"
REMOTE_DIR="cache-aware-scale-in"
log() { printf '\033[1;35m[remote %s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }

source "$ROOT/cloud/providers/$GPU_PROVIDER.sh"
instance=$(provider_create); log "instance: $instance"
cleanup() {
    if [ "$KEEP_INSTANCE" = 1 ]; then log "KEEP_INSTANCE=1: leaving $instance running"; else provider_destroy "$instance" || true; fi
}
trap cleanup EXIT
host=$(provider_wait_ip "$instance"); log "host: $host"

SSH=(ssh -i "$GPU_SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ServerAliveInterval=30 -o ConnectTimeout=10 "$GPU_SSH_USER@$host")
log "waiting for ssh"
for _ in $(seq 1 90); do "${SSH[@]}" true 2>/dev/null && break; sleep 10; done
"${SSH[@]}" true || { echo "ssh never came up"; exit 1; }

log "copying repo"
"${SSH[@]}" "rm -rf $REMOTE_DIR && mkdir -p $REMOTE_DIR"
git -C "$ROOT" archive --format=tar HEAD | "${SSH[@]}" "tar -x -C $REMOTE_DIR"

log "bootstrap (k3s + NVIDIA stack); this takes 3-6 minutes"
"${SSH[@]}" "sudo TOOL=$TOOL bash $REMOTE_DIR/cloud/bootstrap.sh"

log "starting the run detached (log: ~/$REMOTE_DIR/results/run_day.log)"
env_line="HF_TOKEN='$HF_TOKEN' TRIALS=$TRIALS TOOL=$TOOL NBODY=$NBODY MONITORING=$MONITORING PAUSE=0"
[ -n "${GATEWAY_URL:-}" ] && env_line="$env_line GATEWAY_URL='$GATEWAY_URL'"
[ -n "${MODEL:-}" ] && env_line="$env_line MODEL='$MODEL'"
[ -n "${VLLM_IMAGE:-}" ] && env_line="$env_line VLLM_IMAGE='$VLLM_IMAGE'"
"${SSH[@]}" "cd $REMOTE_DIR && mkdir -p results && rm -f results/.run_day.exit && \
    export KUBECONFIG=/etc/rancher/k3s/k3s.yaml && \
    nohup env $env_line setsid bash stage6_final/run_day.sh > results/run_day.log 2>&1 < /dev/null &"

log "following the log (reconnects if ssh drops)"
while true; do
    "${SSH[@]}" "tail -n +1 -f $REMOTE_DIR/results/run_day.log & tp=\$!; while [ ! -f $REMOTE_DIR/results/.run_day.exit ]; do sleep 15; done; sleep 2; kill \$tp" 2>/dev/null && break
    sleep 15
done
code=$("${SSH[@]}" "cat $REMOTE_DIR/results/.run_day.exit")
log "run_day.sh exited with $code"

out="$ROOT/results/remote-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$out"
"${SSH[@]}" "cd $REMOTE_DIR && tar czf - results" | tar xzf - -C "$out" --strip-components=1
log "results in $out"
[ -f "$out/RESULTS.md" ] && sed -n '/## Numbers for the resume bullet/,$p' "$out/RESULTS.md"
exit "$code"
