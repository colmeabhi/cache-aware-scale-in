#!/usr/bin/env bash
# Stage 5 — the live dashboard (read-only web page on the GPU box).
#
#   stage5_benchmark/dashboard.sh                  # http://127.0.0.1:8080
#   from your laptop:  ssh -L 8080:127.0.0.1:8080 user@gpu-box   then open http://localhost:8080
#
# It only reads files under results/live that the sampler and run_trial.sh
# write; it never touches vLLM or Kubernetes, so it cannot skew the benchmark.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
exec "$PYTHON" -m cacheaware.dashboard --results "$RESULTS_DIR" --port "${PORT:-8080}" --bind "${BIND:-127.0.0.1}"
