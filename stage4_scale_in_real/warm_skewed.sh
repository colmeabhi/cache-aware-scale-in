#!/usr/bin/env bash
# Stage 4 — make three real caches unequal with real traffic.
#
#   stage4_scale_in_real/warm_skewed.sh HOT_POD WARM_POD COLD_POD
#
#   HOT   120 requests over 3 shared prefixes      -> hit rate ~0.9
#   WARM   60 requests over 6 prefixes, 30 % unique -> hit rate ~0.5
#   COLD   40 requests, every prompt unique          -> hit rate ~0.0
#
# Traffic goes to each pod directly through its own kubectl port-forward, so
# the router cannot redistribute it. Runs the three warm-ups in parallel.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/lib.sh"
[ $# -eq 3 ] || die "usage: warm_skewed.sh HOT WARM COLD"
need curl "install curl"
trap stop_port_forwards EXIT

HOT_REQUESTS="${HOT_REQUESTS:-120}" WARM_REQUESTS="${WARM_REQUESTS:-60}" COLD_REQUESTS="${COLD_REQUESTS:-40}"
CONC="${CONC:-6}"
out="$RESULTS_DIR/stage4_warm/$(date -u +%H%M%S)"; mkdir -p "$out"

port_forward "$1" 18001
port_forward "$2" 18002
port_forward "$3" 18003
log "warming  hot=$1 ($HOT_REQUESTS req)  warm=$2 ($WARM_REQUESTS req)  cold=$3 ($COLD_REQUESTS unique req)"

"$PYTHON" -m cacheaware.loadgen warm --url http://127.0.0.1:18001 --requests "$HOT_REQUESTS"  --groups 3 --concurrency "$CONC" --out "$out/hot"  > "$out/hot.log"  2>&1 &
p1=$!
"$PYTHON" -m cacheaware.loadgen warm --url http://127.0.0.1:18002 --requests "$WARM_REQUESTS" --groups 6 --unique-fraction 0.3 --concurrency "$CONC" --out "$out/warm" > "$out/warm.log" 2>&1 &
p2=$!
"$PYTHON" -m cacheaware.loadgen warm --url http://127.0.0.1:18003 --requests "$COLD_REQUESTS" --unique-fraction 1.0 --concurrency "$CONC" --out "$out/cold" > "$out/cold.log" 2>&1 &
p3=$!
wait $p1 $p2 $p3

for p in "$1" "$2" "$3"; do
    printf '  %-44s lifetime hit rate %s\n' "$p" "$(scrape "$p" | "$PYTHON" -m cacheaware.vllm_metrics | sed -n 's/.*cumulative prefix-cache hit rate: //p')"
done
log "warm-up done (logs in $out)"
