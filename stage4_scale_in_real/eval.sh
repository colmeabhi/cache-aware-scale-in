#!/usr/bin/env bash
# Stage 4 evaluation — GPU-day goal 1: a scale-down removes the lowest-scored pod.
#
# Same harness as stage 2, but the caches are made unequal with real traffic.
# Each trial restarts the 3 pods (same age, empty caches), warms them
# hot/warm/cold with the cold pod rotating, scales 3 -> 2 and checks.
#
#   stage4_scale_in_real/eval.sh                 # 3 cache-aware + 3 baseline trials (~25 min)
#   TRIALS=2 FRESH_PODS=0 stage4_scale_in_real/eval.sh   # quicker, no restarts
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
export TRIALS="${TRIALS:-3}" SKEW=real SETTLE="${SETTLE:-30}" FRESH_PODS="${FRESH_PODS:-1}"

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
print("\n  PASS: cache-aware scale-in removed the coldest replica every time" if ok else "\n  FAIL: see rows above")
sys.exit(0 if ok else 1)
PY
