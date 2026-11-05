#!/usr/bin/env bash
# Stage 1 — see the controller think, with no Kubernetes and no GPU.
#
# Starts three fake vLLM pods on localhost with different hit rates and runs
# the controller against them in the foreground. Ctrl-C stops everything.
#
#   stage1_local_prototype/run.sh
#   NAMES=v0 stage1_local_prototype/run.sh     # old vLLM metric names
#
# While it runs, change a pod's hit rate from another terminal and watch the
# cost follow it a few cycles later (that is the EWMA at work):
#
#   curl 'http://127.0.0.1:9003/set?hit_rate=0.9'
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT"
NAMES="${NAMES:-new}"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT

python3 -m cacheaware.fake_vllm --port 9001 --hit-rate 0.70 --usage 0.6 --names "$NAMES" & pids+=($!)
python3 -m cacheaware.fake_vllm --port 9002 --hit-rate 0.55 --usage 0.4 --names "$NAMES" & pids+=($!)
python3 -m cacheaware.fake_vllm --port 9003 --hit-rate 0.05 --usage 0.1 --names "$NAMES" & pids+=($!)
sleep 1

echo
echo "controller running (dry-run against fake pods). Ctrl-C to stop."
echo
python3 -m cacheaware.controller --interval 2 --dry-run \
    --static-targets "pod-a=http://127.0.0.1:9001,pod-b=http://127.0.0.1:9002,pod-c=http://127.0.0.1:9003"
