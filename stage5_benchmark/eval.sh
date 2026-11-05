#!/usr/bin/env bash
# Stage 5 evaluation — GPU-day goals 2 and 3: the benchmark plus sanity checks.
#
#   GATEWAY_URL=http://... stage5_benchmark/eval.sh
#
# Runs run_benchmark.sh, then checks the trials are usable:
#   - every trial has TTFT numbers
#   - error rate under 5 % in every trial
#   - cache-aware trials removed the lowest-cost pod
# and prints the "numbers to send" block from results/RESULTS.md.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
"$ROOT/stage5_benchmark/run_benchmark.sh"

echo; log "sanity checks"
"$PYTHON" - "$RESULTS_DIR" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
problems, n = [], 0
for path in sorted(glob.glob(os.path.join(root, "bench", "*", "trial_*", "summary.json"))):
    s = json.load(open(path)); n += 1
    name = f"{s['mode']} trial {s['trial']}"
    windows = s.get("ttft") or {}
    if not any("ttft_p50" in w for w in windows.values()):
        problems.append(f"{name}: no TTFT numbers found (check the load tool log)")
    for label, w in windows.items():
        if w.get("count") and w.get("errors", 0) / w["count"] > 0.05:
            problems.append(f"{name}: {w['errors']}/{w['count']} errors in window {label}")
    if s["mode"] == "cache-aware" and s.get("removed_rank") != 1:
        problems.append(f"{name}: removed pod had cost rank {s.get('removed_rank')}, expected 1")
print(f"  {n} trials checked")
for p in problems: print("  WARN", p)
print("\n  PASS: benchmark data is usable" if not problems else "\n  CHECK the warnings above before quoting numbers")
PY
echo; sed -n '/## Numbers for the resume bullet/,$p' "$RESULTS_DIR/RESULTS.md"
