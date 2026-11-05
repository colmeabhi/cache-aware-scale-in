#!/usr/bin/env bash
# Shared shell helpers. Every stage script starts with:
#
#     ROOT="$(cd "$(dirname "$0")/.." && pwd)"
#     source "$ROOT/scripts/lib.sh"
#
# Settings come from environment variables (defaults in brackets):
#
#   NAMESPACE      [vllm]         namespace of the vLLM pods
#   SELECTOR       [app=vllm]     label selector that matches ONLY the vLLM pods
#   DEPLOYMENT     [vllm]         the Deployment we scale 3 -> 2
#   METRICS_PORT   [8000]         vLLM's HTTP port (metrics + OpenAI API)
#   RESULTS_DIR    [$ROOT/results]
#   CONTROLLER_MODE [local]       local  = run `python -m cacheaware.controller` here
#                                 cluster = scale the in-cluster Deployment (stage 6)
#   CONTROLLER_DEPLOYMENT [cache-aware-controller]   used when CONTROLLER_MODE=cluster
#   CONTROLLER_NAMESPACE  [$NAMESPACE]
#
# If llm-d manages your vLLM pods, point NAMESPACE / SELECTOR / DEPLOYMENT at
# its decode Deployment instead of the one from stage 3.

: "${NAMESPACE:=vllm}"
: "${SELECTOR:=app=vllm}"
: "${DEPLOYMENT:=vllm}"
: "${METRICS_PORT:=8000}"
: "${RESULTS_DIR:=$ROOT/results}"
: "${CONTROLLER_MODE:=local}"
: "${CONTROLLER_DEPLOYMENT:=cache-aware-controller}"
: "${CONTROLLER_NAMESPACE:=$NAMESPACE}"
: "${PYTHON:=python3}"
export NAMESPACE SELECTOR DEPLOYMENT METRICS_PORT RESULTS_DIR PYTHON
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$RESULTS_DIR"

COST_ANNOTATION="controller.kubernetes.io/pod-deletion-cost"

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date -u +%H:%M:%S)" "$*"; }
ok()   { printf '\033[1;32m[PASS]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1 ($2)"; }

# ---- pods ------------------------------------------------------------------

pods() {   # names of all pods matching SELECTOR, sorted
    kubectl get pods -n "$NAMESPACE" -l "$SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' | sort
}

ready_pods() {   # names of pods that are Running + Ready and not terminating
    kubectl get pods -n "$NAMESPACE" -l "$SELECTOR" -o json | "$PYTHON" -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    if p["metadata"].get("deletionTimestamp"): continue
    if p["status"].get("phase") != "Running": continue
    if any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", [])):
        print(p["metadata"]["name"])' | sort
}

pod_annotation() {   # pod_annotation NAME KEY  -> value or "-"
    local v
    v=$(kubectl get pod -n "$NAMESPACE" "$1" -o jsonpath="{.metadata.annotations.$(printf '%s' "$2" | sed 's/\./\\./g')}" 2>/dev/null || true)
    printf '%s' "${v:--}"
}

pod_cost() { pod_annotation "$1" "$COST_ANNOTATION"; }

pod_table() {   # pretty table of every pod with its cost and the info annotations
    printf '  %-44s %-6s %-6s %-6s %-6s %s\n' POD READY COST HIT USAGE NODE
    local p
    for p in $(pods); do
        printf '  %-44s %-6s %-6s %-6s %-6s %s\n' "$p" \
            "$(kubectl get pod -n "$NAMESPACE" "$p" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" \
            "$(pod_cost "$p")" \
            "$(pod_annotation "$p" cacheaware.dev/recent-hit-rate)" \
            "$(pod_annotation "$p" cacheaware.dev/kv-usage)" \
            "$(kubectl get pod -n "$NAMESPACE" "$p" -o jsonpath='{.spec.nodeName}')"
    done
}

lowest_cost_pod() {   # the pod Kubernetes *should* delete first (missing cost counts as 0)
    local p c best="" best_c=""
    for p in $(ready_pods); do
        c=$(pod_cost "$p"); [ "$c" = "-" ] && c=0
        if [ -z "$best" ] || [ "$c" -lt "$best_c" ]; then best="$p"; best_c="$c"; fi
    done
    printf '%s' "$best"
}

cost_rank_of() {   # cost_rank_of POD "name=cost name=cost ..."  -> 1 if lowest cost, 2 if second, ...
    "$PYTHON" - "$1" "$2" <<'PY'
import sys
pod, pairs = sys.argv[1], sys.argv[2].split()
costs = {p.split("=")[0]: int(p.split("=")[1]) for p in pairs}
ranked = sorted(costs, key=lambda n: (costs[n], n))
print(ranked.index(pod) + 1 if pod in ranked else "-")
PY
}

costs_string() {   # "pod=cost pod=cost ..." for the ready pods (missing cost -> 0)
    local p c out=""
    for p in $(ready_pods); do c=$(pod_cost "$p"); [ "$c" = "-" ] && c=0; out="$out $p=$c"; done
    printf '%s' "${out# }"
}

strip_costs() {   # remove our annotations from every pod (baseline mode)
    local p
    for p in $(pods); do
        kubectl annotate pod -n "$NAMESPACE" "$p" "$COST_ANNOTATION-" \
            cacheaware.dev/recent-hit-rate- cacheaware.dev/kv-usage- \
            cacheaware.dev/cache-value- cacheaware.dev/updated-at- >/dev/null 2>&1 || true
    done
}

# ---- scraping through the API server (works from anywhere kubectl works) ---

scrape() {   # scrape POD [path]  -> the pod's /metrics text
    kubectl get --raw "/api/v1/namespaces/$NAMESPACE/pods/$1:$METRICS_PORT/proxy/${2:-metrics}"
}

# ---- scaling & waiting -------------------------------------------------------

scale() {   # scale N
    kubectl scale deployment -n "$NAMESPACE" "$DEPLOYMENT" --replicas="$1" >/dev/null
    log "scaled $DEPLOYMENT to $1 replica(s)"
}

wait_ready() {   # wait_ready N [timeout_seconds]  until exactly N pods are Ready (and none terminating)
    local want="$1" timeout="${2:-900}" start=$SECONDS n total
    while true; do
        n=$(ready_pods | wc -l); total=$(pods | wc -l)
        if [ "$n" -eq "$want" ] && [ "$total" -eq "$want" ]; then
            log "$want pod(s) ready"; return 0
        fi
        (( SECONDS - start > timeout )) && die "timed out waiting for $want ready pods (have $n ready, $total total)"
        sleep 3
    done
}

fresh_pods() {   # restart every pod so they all have the same age and an empty cache
    local n="$1"
    log "restarting $DEPLOYMENT so all $n pods are the same age with empty caches"
    scale "$n"
    kubectl rollout restart deployment -n "$NAMESPACE" "$DEPLOYMENT" >/dev/null
    kubectl rollout status deployment -n "$NAMESPACE" "$DEPLOYMENT" --timeout=900s >/dev/null
    wait_ready "$n"
}

removed_pod() {   # removed_pod "before list" -> the name that is no longer present
    local before="$1" after p
    after=$(pods)
    for p in $before; do
        grep -qx "$p" <<<"$after" || printf '%s' "$p"
    done
}

# ---- controller on / off --------------------------------------------------

CONTROLLER_PID_FILE="$RESULTS_DIR/.controller.pid"

controller_on() {
    if [ "$CONTROLLER_MODE" = cluster ]; then
        kubectl scale deployment -n "$CONTROLLER_NAMESPACE" "$CONTROLLER_DEPLOYMENT" --replicas=1 >/dev/null
        kubectl rollout status deployment -n "$CONTROLLER_NAMESPACE" "$CONTROLLER_DEPLOYMENT" --timeout=120s >/dev/null
    else
        controller_off_quiet
        "$PYTHON" -m cacheaware.controller --namespace "$NAMESPACE" --selector "$SELECTOR" \
            --metrics-port "$METRICS_PORT" ${CONTROLLER_ARGS:-} > "$RESULTS_DIR/controller.log" 2>&1 &
        echo $! > "$CONTROLLER_PID_FILE"
    fi
    log "controller ON ($CONTROLLER_MODE)"
}

controller_off_quiet() {
    if [ "$CONTROLLER_MODE" = cluster ]; then
        kubectl scale deployment -n "$CONTROLLER_NAMESPACE" "$CONTROLLER_DEPLOYMENT" --replicas=0 >/dev/null 2>&1 || true
    elif [ -f "$CONTROLLER_PID_FILE" ]; then
        kill "$(cat "$CONTROLLER_PID_FILE")" 2>/dev/null || true
        rm -f "$CONTROLLER_PID_FILE"
    fi
}

controller_off() { controller_off_quiet; strip_costs; log "controller OFF, cost annotations removed"; }

# ---- port-forward (to send traffic to ONE specific pod) --------------------

PF_PIDS=()
port_forward() {   # port_forward POD LOCALPORT -> starts kubectl port-forward in the background
    kubectl port-forward -n "$NAMESPACE" "pod/$1" "$2:$METRICS_PORT" >/dev/null 2>&1 &
    PF_PIDS+=($!)
    local i
    for i in $(seq 1 30); do
        curl -fs "http://127.0.0.1:$2/health" >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    die "port-forward to $1 on port $2 did not come up"
}
stop_port_forwards() { local pid; for pid in "${PF_PIDS[@]:-}"; do [ -n "$pid" ] && kill "$pid" 2>/dev/null || true; done; PF_PIDS=(); }

# ---- live dashboard helpers -------------------------------------------------

live_set()   { "$PYTHON" -m cacheaware.live --results "$RESULTS_DIR" set "$@" >/dev/null; }
live_event() { "$PYTHON" -m cacheaware.live --results "$RESULTS_DIR" event "$*"; }
