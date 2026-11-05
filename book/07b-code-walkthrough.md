## Part III · Chapter 2: A guided reading of the code

This chapter walks through the pieces of code that carry the idea, excerpt by excerpt. Each excerpt is real code from the repository, trimmed of comments; read the files for the full version. The order follows the data: a scrape becomes a hit rate, a hit rate becomes a cost, a cost is written, a scale-in reads it, an experiment checks it.

### 1. From two scrapes to a recent hit rate (`cacheaware/scoring.py`)

```python
@dataclass
class PodHistory:
    last_queries: float | None = None
    last_hits: float | None = None
    recent_hit_rate: float | None = None   # the EWMA

    def update(self, sample, alpha=0.3, min_tokens=256):
        queries, hits = sample.prefix_queries, sample.prefix_hits
        if queries is None or hits is None:          # old vLLM: only a gauge
            self.recent_hit_rate = sample.hit_rate_gauge
            return self.recent_hit_rate

        first_sample = self.last_queries is None
        counters_reset = not first_sample and queries < self.last_queries
        if first_sample or counters_reset:
            self.last_queries, self.last_hits = queries, hits
            self.recent_hit_rate = hits / queries if queries > 0 else None
            return self.recent_hit_rate

        delta_queries = queries - self.last_queries
        delta_hits = hits - self.last_hits
        self.last_queries, self.last_hits = queries, hits
        if delta_queries < min_tokens:
            return self.recent_hit_rate                # idle: keep what we knew

        interval_rate = min(1.0, max(0.0, delta_hits / delta_queries))
        if self.recent_hit_rate is None:
            self.recent_hit_rate = interval_rate
        else:
            self.recent_hit_rate = alpha * interval_rate + (1 - alpha) * self.recent_hit_rate
        return self.recent_hit_rate
```

Read it top to bottom as a list of cases. No counters: use the gauge. First time, or counters went backwards: start from the lifetime ratio. Too little traffic: say nothing new. Otherwise: the interval's ratio, folded into the running estimate with weight `alpha`. The function returns `None` whenever it honestly does not know, and the caller treats `None` as zero value, which is right for a fresh pod with an empty cache.

### 2. From a hit rate to a cost

```python
def cache_value(hit_rate, kv_usage, inflight_fraction=0.0, weights=None):
    w = (weights or Weights()).normalised()
    value = (w.hit_rate * clamp01(hit_rate)
             + w.kv_usage * clamp01(kv_usage)
             + w.inflight * clamp01(inflight_fraction))
    return clamp01(value)

def deletion_cost(value, scale=1000):
    return int(round(clamp01(value) * scale))

def worth_patching(current_cost, new_cost, min_delta=25):
    if current_cost is None:
        return True
    return abs(new_cost - current_cost) >= min_delta
```

Three one-screen functions, each testable alone. `clamp01` turns `None` into 0 and keeps everything in range, so a bad reading can never produce a cost above 1000 or below 0. `worth_patching` is the dead band: a missing annotation is always written; an existing one only when it moved enough.

### 3. One pod, one cycle (`cacheaware/controller.py`)

```python
def handle_pod(self, pod):
    report = PodReport(name=pod.name, previous_cost=read_cost(pod))

    if pod.phase != "Running" or pod.deleting:
        report.action = "skipped"
        return report

    try:
        text = self.backend.scrape(pod)
    except Exception as exc:
        report.action, report.note = "error", f"scrape failed: {exc}"[:80]
        return report                      # keep whatever cost it had

    sample = sample_from_text(text)
    history = self.histories.setdefault(pod.name, PodHistory())
    report.hit_rate = history.update(sample, self.s.ewma_alpha, self.s.min_tokens)
    report.kv_usage = sample.kv_usage
    inflight = (sample.running + sample.waiting) / max(1, self.s.max_inflight)
    report.value = cache_value(report.hit_rate, report.kv_usage, inflight, self.s.weights)
    report.cost = deletion_cost(report.value, self.s.cost_scale)

    if not worth_patching(report.previous_cost, report.cost, self.s.min_delta):
        report.action = "kept"
        return report

    annotations = {COST_ANNOTATION: str(report.cost), ...}
    if self.s.dry_run:
        report.action = "dry-run"
    else:
        self.backend.annotate(pod, annotations)
        report.action = "patched"
    return report
```

Every early `return` is a deliberate "do nothing" case: a pod that is starting or stopping, a pod that did not answer. The only write happens at the very end, after every check. `self.backend` is either the Kubernetes client or the static list of URLs from stage 1; the function cannot tell the difference, which is why the stage 1 tests exercise the same code the cluster runs.

### 4. Which pod *should* go (`scripts/lib.sh`)

```bash
lowest_cost_pod() {   # the pod Kubernetes should delete first (missing cost counts as 0)
    local p c best="" best_c=""
    for p in $(ready_pods); do
        c=$(pod_cost "$p"); [ "$c" = "-" ] && c=0
        if [ -z "$best" ] || [ "$c" -lt "$best_c" ]; then best="$p"; best_c="$c"; fi
    done
    printf '%s' "$best"
}
```

The experiments need an independent opinion of what Kubernetes ought to do, computed from the annotations alone, not from the controller's memory. This is it: read every Ready pod's annotation, treat a missing one as 0 (exactly as Kubernetes does), return the minimum. If the pod that disappears is not this one, something is wrong with the mechanism, not with the scoring.

### 5. One trial of the scale-in experiment (`scripts/scale_in_trials.sh`)

```bash
run_trial() {
    local trial="$1"
    if [ "$FRESH_PODS" = 1 ]; then fresh_pods 3; else scale 3; wait_ready 3; fi

    local all; mapfile -t all < <(ready_pods)
    local cold="${all[$(( (trial - 1) % 3 ))]}"      # rotates: 1st, 2nd, 3rd pod
    local hot="${all[$(( trial % 3 ))]}"
    local warm="${all[$(( (trial + 1) % 3 ))]}"

    [ "$MODE" = baseline ] && strip_costs
    "skew_$SKEW" "$hot" "$warm" "$cold"             # skew_mock or skew_real
    [ "$MODE" = cache-aware ] && sleep "$SETTLE" || strip_costs

    local costs expected before removed rank
    costs=$(costs_string); expected=$(lowest_cost_pod); before=$(pods)
    scale 2
    wait_ready 2 300
    removed=$(removed_pod "$before")
    rank=$(cost_rank_of "$removed" "$costs")

    if [ "$removed" = "$cold" ]; then ok "removed the COLD pod"; verdict=1
    else fail "removed $removed, the cold pod was $cold"; verdict=0; fi
    echo "$MODE,$trial,$cold,$hot,$warm,\"$costs\",$removed,$rank,$verdict" >> "$CSV"
}
```

The whole experiment is twenty lines once the helpers exist. Two details carry the honesty: the cold pod rotates with the trial number, and `before` is captured right before the scale so `removed_pod` can find the one that vanished by set difference. The CSV row records everything needed to re-check the verdict by hand. The only line that differs between stage 2 and stage 4 is `"skew_$SKEW"`: a function that sets fake knobs, or one that sends real traffic.

### 6. The scale-in moment in the benchmark (`stage5_benchmark/run_trial.sh`)

```bash
log "load started; warming for ${WARM}s"
sleep "$WARM"

snap "$trial_dir/pre_snapshot.json"
costs=$(costs_string); expected=$(lowest_cost_pod); before=$(pods)
live_event "scaling 3 -> 2 (costs: $costs)"
scale 2
scale_at=$(date +%s)
# the removed pod gets a deletionTimestamp immediately; find it without waiting for it to vanish
removed=""
for _ in $(seq 1 120); do
    removed=$(kubectl get pods -n "$NAMESPACE" -l "$SELECTOR" -o json | "$PYTHON" -c '
import json, sys
for p in json.load(sys.stdin)["items"]:
    if p["metadata"].get("deletionTimestamp"):
        print(p["metadata"]["name"]); break' || true)
    [ -n "$removed" ] && break
    sleep 0.5
done
rank=$(cost_rank_of "$removed" "$costs")
live_set phase=measuring removed_pod="$removed" removed_rank="$rank" expected_pod="$expected"
```

The snapshot before the scale is what the survivor hit rate is later computed against. The removed pod is identified by its deletion timestamp within half a second, rather than waiting the thirty seconds it takes to actually disappear, so the dashboard shows the verdict immediately and the measurement window is not confused with the drain.

### 7. Same prefix, same pod (`cacheaware/router.py`)

```python
def candidates(self, key):
    """Pods for this key, best first, each pod once (for failover)."""
    with self._lock:
        if not self._points:
            return []
        start = bisect.bisect_left(self._points, _h(key)) % len(self._points)
        seen = []
        for i in range(len(self._points)):
            name = self._owner[self._points[(start + i) % len(self._points)]]
            if name not in seen:
                seen.append(name)
            if len(seen) == len(self.urls):
                break
        return seen
```

`_points` is the sorted list of every pod's 64 markers on the ring; `_owner` maps a marker back to its pod. `bisect_left` finds where the key's hash lands, and the loop walks clockwise collecting pods in the order they are met. The first is the pod that serves the request; the rest are the failover order if it is gone. Removing a pod removes its markers and nothing else, which is the consistent-hashing property the test checks.

### What to remember

- The scoring is three short pure functions; everything uncertain returns `None` and scores as zero.
- `handle_pod` does nothing in every doubtful case and writes only at the end.
- The experiments compute "what should happen" from the annotations alone, independently of the controller.
- The stage 2 and stage 4 experiments are the same twenty lines with one function swapped.
- The router's ring is a sorted list and a binary search.
