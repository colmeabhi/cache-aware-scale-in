#!/usr/bin/env bash
# Stage 1 evaluation: unit tests + the end-to-end scenario on fake pods.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$ROOT"
cd "$ROOT"

echo "== unit tests =="
python3 -m pytest -q stage1_local_prototype/tests

echo
echo "== scenario: three fake pods, real controller loop =="
python3 stage1_local_prototype/eval_scenario.py
