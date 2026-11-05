#!/usr/bin/env bash
# The other job for the GPU day: the N-body CUDA benchmarks.
#
# The repo colmeabhi/parallelized-nbody-simulation-cuda-cpp already has the
# full pipeline; the GPU rows in its README just say "not measured yet". This
# script runs that pipeline on the A100 and leaves a commit ready to push.
#
#   stage6_final/nbody_a100.sh                 # full: CPU + GPU + Nsight + report (~1 h)
#   QUICK=1 stage6_final/nbody_a100.sh         # smoke test (~5 min)
#   SKIP_CPU=1 stage6_final/nbody_a100.sh      # GPU only (CPU rows stay from the old machine)
#
# Why run the CPU part again: the speedup column is "GPU vs serial CPU on the
# same machine". The CPU numbers in the repo came from a 4-thread Xeon, so
# rerunning both on the A100 box keeps the comparison honest.
#
# Free the GPU first (vLLM holds ~90 % of its memory):  kubectl scale deploy vllm --replicas=0
set -euo pipefail
NBODY_DIR="${NBODY_DIR:-$HOME/parallelized-nbody-simulation-cuda-cpp}"
NBODY_REPO="${NBODY_REPO:-https://github.com/colmeabhi/parallelized-nbody-simulation-cuda-cpp.git}"
log() { printf '\033[1;34m[%s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

command -v nvidia-smi >/dev/null || die "nvidia-smi not found"
command -v nvcc >/dev/null || die "nvcc not found: install the CUDA toolkit (or add /usr/local/cuda/bin to PATH)"
command -v cmake >/dev/null || die "cmake not found (apt install cmake)"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
if nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -q .; then
    die "the GPU is busy (vLLM?). Scale it to 0 first so the benchmark has the whole card."
fi

if [ -d "$NBODY_DIR/.git" ]; then
    log "updating $NBODY_DIR"; git -C "$NBODY_DIR" pull --ff-only
else
    log "cloning into $NBODY_DIR"; git clone "$NBODY_REPO" "$NBODY_DIR"
fi
cd "$NBODY_DIR"

cap=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' .')
export CUDA_ARCH="${CUDA_ARCH:-$cap}"        # A100 -> 80
log "CUDA arch $CUDA_ARCH; running scripts/run_benchmarks.sh (QUICK=${QUICK:-0} SKIP_CPU=${SKIP_CPU:-0})"
python3 -c 'import numpy, matplotlib' 2>/dev/null || pip install -q numpy matplotlib
QUICK="${QUICK:-0}" SKIP_CPU="${SKIP_CPU:-0}" scripts/run_benchmarks.sh

if command -v ncu >/dev/null; then
    log "Nsight Compute: naive vs tiled kernels"
    scripts/profile_ncu.sh || log "ncu failed (needs root or NVreg_RestrictProfilingToAdminUsers=0); skipping"
else
    log "ncu not found; skipping the Nsight section (optional)"
fi

log "regenerating README results section and charts"
python3 scripts/make_report.py

branch="results/a100-$(date -u +%Y%m%d)"
git checkout -B "$branch" >/dev/null
git add results README.md
git -c user.name="${GIT_AUTHOR_NAME:-$(git config user.name || echo nbody-bench)}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-$(git config user.email || echo nbody@example.invalid)}" \
    commit -q -m "benchmark results on A100 ($(nvidia-smi --query-gpu=name --format=csv,noheader | head -1))" || log "nothing new to commit"
log "done. Review with: git -C $NBODY_DIR show --stat HEAD"
log "then push:        git -C $NBODY_DIR push -u origin $branch"
