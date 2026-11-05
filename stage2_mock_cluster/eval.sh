#!/usr/bin/env bash
# Stage 2 evaluation: does a scale-down really remove the lowest-cost pod?
#
# Runs the trial harness twice on the fake pods:
#   cache-aware : controller on  -> expect the cold pod removed every time
#   baseline    : controller off -> Kubernetes' default pick; expect ~1 in 3
#
#   stage2_mock_cluster/eval.sh            # 3 trials each
#   TRIALS=5 stage2_mock_cluster/eval.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
export TRIALS="${TRIALS:-3}" SKEW=mock SETTLE="${SETTLE:-30}"

MODE=cache-aware "$ROOT/scripts/scale_in_trials.sh"
MODE=baseline    "$ROOT/scripts/scale_in_trials.sh"

echo; log "summary ($RESULTS_DIR/scale_in_trials.csv):"
"$PYTHON" - "$RESULTS_DIR/scale_in_trials.csv" <<'PY'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
for mode in ("cache-aware", "baseline"):
    r = [x for x in rows if x["mode"] == mode]
    hits = sum(int(x["coldest_removed"]) for x in r)
    print(f"  {mode:<12} coldest pod removed in {hits}/{len(r)} trials")
ca = [x for x in rows if x["mode"] == "cache-aware"]
ok = ca and all(x["coldest_removed"] == "1" for x in ca)
print("\n  PASS: cache-aware scale-in always removed the coldest pod" if ok else "\n  FAIL: see rows above")
sys.exit(0 if ok else 1)
PY
