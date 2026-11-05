#!/usr/bin/env bash
# The scale-in verification harness. Stage 2 runs it against fake pods, stage 4
# against real vLLM pods; only the "make the caches unequal" step differs.
#
#   MODE=cache-aware|baseline  TRIALS=3  SKEW=mock|real  scripts/scale_in_trials.sh
#
# One trial:
#   1. (FRESH_PODS=1) restart the Deployment so all 3 pods have the same age
#      and empty caches. This matters for the baseline: with equal ages,
#      Kubernetes' default tie-break is effectively arbitrary, which is the
#      honest thing to compare against.
#   2. pick which pod will be COLD this trial (rotates: trial 1 -> 1st pod,
#      trial 2 -> 2nd, ...) so a pass cannot be a coincidence of pod order.
#   3. make the caches unequal:  mock -> set the fake pods' knobs
#                                real -> stage4 warm_skewed.sh (real traffic)
#   4. cache-aware: wait for the controller to settle, show the costs
#      baseline:    make sure no cost annotations exist
#   5. scale 3 -> 2 and see which pod Kubernetes removed
#   6. PASS if the removed pod is the cold one
#
# Results: $RESULTS_DIR/scale_in_trials.csv (appended) + a printed summary.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"

MODE="${MODE:-cache-aware}"
TRIALS="${TRIALS:-3}"
SKEW="${SKEW:-mock}"
FRESH_PODS="${FRESH_PODS:-1}"
SETTLE="${SETTLE:-30}"           # seconds for the controller's EWMA to catch up
CSV="$RESULTS_DIR/scale_in_trials.csv"
[ -f "$CSV" ] || echo "mode,trial,cold_pod,hot_pod,warm_pod,costs_at_scale_in,removed_pod,removed_cost_rank,coldest_removed" > "$CSV"

# ---- the two ways to make caches unequal ------------------------------------

skew_mock() {   # skew_mock HOT WARM COLD : set the fake pods' knobs through the API-server proxy
    kubectl get --raw "/api/v1/namespaces/$NAMESPACE/pods/$1:$METRICS_PORT/proxy/set?hit_rate=0.70&usage=0.60" >/dev/null
    kubectl get --raw "/api/v1/namespaces/$NAMESPACE/pods/$2:$METRICS_PORT/proxy/set?hit_rate=0.55&usage=0.40" >/dev/null
    kubectl get --raw "/api/v1/namespaces/$NAMESPACE/pods/$3:$METRICS_PORT/proxy/set?hit_rate=0.05&usage=0.10" >/dev/null
    log "fake knobs set: hot=$1 warm=$2 cold=$3"
}

skew_real() {   # skew_real HOT WARM COLD : real prefix traffic, see stage4_scale_in_real/warm_skewed.sh
    "$ROOT/stage4_scale_in_real/warm_skewed.sh" "$1" "$2" "$3"
}

# ---- one trial ------------------------------------------------------------------

run_trial() {
    local trial="$1"
    echo; log "===== $MODE trial $trial/$TRIALS ($SKEW skew) ====="
    if [ "$FRESH_PODS" = 1 ]; then fresh_pods 3; else scale 3; wait_ready 3; fi

    local all; mapfile -t all < <(ready_pods)
    local cold="${all[$(( (trial - 1) % 3 ))]}"
    local hot="${all[$(( trial % 3 ))]}"
    local warm="${all[$(( (trial + 1) % 3 ))]}"
    log "this trial: hot=$hot  warm=$warm  cold=$cold"

    if [ "$MODE" = baseline ]; then strip_costs; fi
    "skew_$SKEW" "$hot" "$warm" "$cold"

    if [ "$MODE" = cache-aware ]; then
        log "waiting ${SETTLE}s for the controller to re-score"
        sleep "$SETTLE"
    else
        strip_costs
    fi
    echo; pod_table; echo

    local costs expected before removed rank verdict
    costs=$(costs_string)
    expected=$(lowest_cost_pod)
    [ "$MODE" = cache-aware ] && log "lowest cost -> Kubernetes should remove: $expected"
    before=$(pods)

    scale 2
    wait_ready 2 300
    removed=$(removed_pod "$before")
    rank=$(cost_rank_of "$removed" "$costs")
    log "Kubernetes removed: $removed (cost rank $rank of 3, 1 = lowest)"

    if [ "$removed" = "$cold" ]; then ok "removed the COLD pod"; verdict=1
    else fail "removed $removed, the cold pod was $cold"; verdict=0; fi
    if [ "$MODE" = cache-aware ] && [ "$removed" != "$expected" ]; then
        fail "removed pod differs from the lowest-cost pod ($expected): check that pod-deletion-cost is honoured (k8s >= 1.22)"
    fi
    echo "$MODE,$trial,$cold,$hot,$warm,\"$costs\",$removed,$rank,$verdict" >> "$CSV"
}

# ---- main ------------------------------------------------------------------------

need kubectl "install kubectl"
if [ "$MODE" = cache-aware ]; then controller_on; else controller_off; fi
trap 'controller_off_quiet; stop_port_forwards' EXIT

for t in $(seq 1 "$TRIALS"); do run_trial "$t"; done
scale 3

echo
passes=$(awk -F, -v m="$MODE" '$1==m && $NF==1' "$CSV" | wc -l)
total=$(awk -F, -v m="$MODE" '$1==m' "$CSV" | wc -l)
log "$MODE: removed the coldest pod in $passes of $total trial(s) (all runs so far, see $CSV)"
