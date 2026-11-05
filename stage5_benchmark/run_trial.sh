#!/usr/bin/env bash
# Stage 5 — ONE benchmark trial: warm the caches, scale 3 -> 2, measure TTFT.
#
#   stage5_benchmark/run_trial.sh MODE TRIAL        MODE = baseline | cache-aware
#
# Timeline (the dashboard shows which phase you are in):
#
#   fresh pods ──► load starts ──► [warm WARM s] ──► scale 3->2 ──► [measure MEASURE s] ──► report
#                                      ▲                 ▲
#                            caches fill up    Kubernetes picks a pod:
#                                              baseline = its default rule
#                                              cache-aware = lowest cost
#
# Variables (env):
#   GATEWAY_URL   required  base URL requests go to (llm-d gateway or the vllm Service)
#   TOOL          [loadgen]  loadgen | inference-perf
#   RATE          [4]        requests per second
#   WARM          [120]      seconds before scale-in
#   MEASURE       [120]      seconds after scale-in (inference-perf: fixed 4 x 30 s)
#   GROUPS/ZIPF   [16/1.0]   loadgen prefix groups and popularity skew
#   FRESH_PODS    [1]        restart pods before the trial (same age, empty caches)
#
# Output: $RESULTS_DIR/bench/<MODE>/trial_<N>/{meta.json,pre_snapshot.json,post_snapshot.json,summary.json,<tool>/...}
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
MODE="${1:?MODE}"; TRIAL="${2:?TRIAL}"
: "${GATEWAY_URL:?set GATEWAY_URL, e.g. http://<gateway-ip>:80}"
TOOL="${TOOL:-loadgen}" RATE="${RATE:-4}" WARM="${WARM:-120}" MEASURE="${MEASURE:-120}"
GROUPS="${GROUPS:-16}" ZIPF="${ZIPF:-1.0}" FRESH_PODS="${FRESH_PODS:-1}" MODEL="${MODEL:-auto}"
trial_dir="$RESULTS_DIR/bench/$MODE/trial_$TRIAL"; mkdir -p "$trial_dir"
snap() { "$PYTHON" -m cacheaware.live --results "$RESULTS_DIR" snapshot --namespace "$NAMESPACE" --selector "$SELECTOR" --metrics-port "$METRICS_PORT" --out "$1" >/dev/null; }

traffic_running() {   # true once any pod reports running requests (inference-perf has no start flag)
    local p; for p in $(ready_pods); do
        scrape "$p" | grep -E '^vllm:num_requests_running' | awk '{s+=$NF} END {exit !(s>0)}' && return 0
    done; return 1
}

# ---- 1. pods + controller -------------------------------------------------------
echo; log "===== $MODE trial $TRIAL ($TOOL, rate $RATE/s, warm ${WARM}s, measure ${MEASURE}s) ====="
live_set phase=preparing mode="$MODE" trial="$TRIAL" tool="$TOOL" removed_pod= removed_rank= expected_pod= note=
if [ "$FRESH_PODS" = 1 ]; then fresh_pods 3; else scale 3; wait_ready 3; fi
if [ "$MODE" = cache-aware ]; then controller_on; live_set controller=on; else controller_off; live_set controller=off; fi

# ---- 2. start the load ------------------------------------------------------------
live_set phase=warming; live_event "trial $TRIAL ($MODE): load starting"
case "$TOOL" in
    loadgen)
        "$PYTHON" -m cacheaware.loadgen bench --url "$GATEWAY_URL" --model "$MODEL" --rate "$RATE" \
            --warm-seconds "$WARM" --measure-seconds "$MEASURE" --groups "$GROUPS" --zipf "$ZIPF" \
            --out "$trial_dir/loadgen" > "$trial_dir/loadgen.log" 2>&1 &
        load_pid=$!
        until [ -f "$trial_dir/loadgen/started.flag" ]; do sleep 0.5; kill -0 $load_pid 2>/dev/null || die "loadgen died, see $trial_dir/loadgen.log"; done
        ;;
    inference-perf)
        need inference-perf "pip install inference-perf"
        mkdir -p "$trial_dir/inference-perf"
        model="$MODEL"; [ "$model" = auto ] && model=$(curl -fs "$GATEWAY_URL/v1/models" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])')
        sed "s|__GATEWAY_URL__|$GATEWAY_URL|g; s|__MODEL__|$model|g; s|__RATE__|$RATE|g; s|__WARM__|$WARM|g" \
            "$ROOT/stage5_benchmark/inference-perf.yaml.template" > "$trial_dir/inference-perf/config.yaml"
        (cd "$trial_dir/inference-perf" && inference-perf --config_file config.yaml > ../inference-perf.log 2>&1) &
        load_pid=$!
        log "waiting for the first requests to reach the pods"
        until traffic_running; do sleep 1; kill -0 $load_pid 2>/dev/null || die "inference-perf died, see $trial_dir/inference-perf.log"; done
        ;;
    *) die "TOOL must be loadgen or inference-perf" ;;
esac
log "load started; warming for ${WARM}s"
sleep "$WARM"

# ---- 3. scale in --------------------------------------------------------------------
snap "$trial_dir/pre_snapshot.json"
costs=$(costs_string); expected=$(lowest_cost_pod); before=$(pods)
echo; pod_table; echo
live_event "scaling 3 -> 2 (costs: $costs)"
scale 2
scale_at=$(date +%s)
# the removed pod gets a deletionTimestamp immediately; find it without waiting for it to vanish
removed=""
for _ in $(seq 1 120); do
    removed=$(kubectl get pods -n "$NAMESPACE" -l "$SELECTOR" -o json 2>/dev/null | "$PYTHON" -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    if p["metadata"].get("deletionTimestamp"):
        print(p["metadata"]["name"]); break' || true)
    [ -n "$removed" ] && break
    removed=$(removed_pod "$before" || true)
    [ -n "$removed" ] && break
    sleep 0.5
done
[ -n "$removed" ] || die "no pod was removed within 60 s of scaling to 2"
rank=$(cost_rank_of "$removed" "$costs")
log "Kubernetes removed $removed (cost rank $rank of 3; lowest-cost pod was $expected)"
live_set phase=measuring removed_pod="$removed" removed_rank="$rank" expected_pod="$expected"
live_event "removed $removed (rank $rank)"

# ---- 4. let the measurement finish ---------------------------------------------------
wait $load_pid || log "load tool exited non-zero (check the log); continuing"
snap "$trial_dir/post_snapshot.json"
wait_ready 2 300

"$PYTHON" - "$trial_dir/meta.json" "$MODE" "$TRIAL" "$TOOL" "$removed" "$rank" "$expected" "$costs" "$scale_at" "$WARM" "$MEASURE" "$RATE" "$GATEWAY_URL" <<'PY'
import json, sys
(path, mode, trial, tool, removed, rank, expected, costs, scale_at, warm, measure, rate, url) = sys.argv[1:]
json.dump({"mode": mode, "trial": int(trial), "tool": tool, "removed_pod": removed,
           "removed_rank": int(rank) if rank.isdigit() else None, "expected_pod": expected,
           "costs_at_scale_in": {p.split("=")[0]: int(p.split("=")[1]) for p in costs.split()},
           "scale_in_at": int(scale_at), "warm_seconds": float(warm), "measure_seconds": float(measure),
           "rate": float(rate), "gateway_url": url}, open(path, "w"), indent=2)
PY
"$PYTHON" -m cacheaware.report --trial "$trial_dir" > "$trial_dir/summary.txt"
live_set phase=idle; live_event "trial $TRIAL ($MODE) done"
log "trial written to $trial_dir"
tail -n 12 "$trial_dir/summary.txt"
