## Part IV · Measuring honestly

A controller that "feels faster" is worth nothing on a resume or in a paper. The benchmark in stage 5 exists to produce two numbers that would survive a sceptical reader: time to first token after a scale-in, and the prefix-cache hit rate of the surviving pods, each for default scale-in and for cache-aware scale-in. This part explains how the experiment is built so those numbers mean what they claim.

### What "fair" means

An experiment is fair when the only thing that differs between the two arms is the thing being tested. Here the thing being tested is *which pod Kubernetes deletes*. So everything else must be the same in both arms: the pods, their age, the traffic, the moment of the scale-in, the measurement window, the machine, the time of day as far as possible. Each design choice below removes one way the two arms could differ by accident.

### One trial

```mermaid
sequenceDiagram
  participant R as run_trial.sh
  participant K as Kubernetes
  participant L as load tool
  participant P as 3 vLLM pods
  R->>K: rollout restart; wait for 3 Ready
  Note over R: controller ON (cache-aware) or OFF + annotations stripped (baseline)
  R->>L: start: WARM s of traffic, then MEASURE s, same prefixes throughout
  L->>P: requests through the gateway or built-in router
  Note over P: caches fill; controller scores every 5 s
  R->>R: after WARM s: snapshot counters, record each pod's cost
  R->>K: scale 3 → 2
  K-->>P: deletes one pod
  R->>R: record which pod went and its cost rank (1 = lowest)
  L->>P: measurement continues on the 2 survivors
  R->>R: snapshot counters again; write meta.json; summarise
```

Default timings: 120 seconds of warm-up, 120 seconds of measurement, 4 requests per second, 16 prefix groups. One trial is about eight minutes including the pod restart.

### The design choices, and the accident each one prevents

**Fresh pods for every trial.** Before each trial, all three pods are restarted so they have the same age and empty caches. Without this, the baseline's age rule (Part I, Chapter 3) would always delete the pod that was most recently started, which after a scale-out is also the coldest. The baseline would look clever by accident. With equal ages, Kubernetes' default choice is effectively arbitrary, which is the honest thing to compare against.

**Baseline means controller stopped and annotations removed.** Stopping the controller alone would leave the last costs on the pods, and Kubernetes would still honour them.

**Interleaved trials.** The order is baseline, cache-aware, baseline, cache-aware, and so on. If the machine slowly warms up, another tenant appears, or the model's first minutes are slower, both arms are affected equally.

**The same prefixes in warm-up and measurement.** The load tool runs once per trial and switches from the warm phase to the measure phase without changing its prompts. If the measurement used different prompts, no cache could help either arm, and the experiment would measure nothing.

**Skewed prefix popularity.** The load generator draws each request's prefix from 16 groups with Zipf-distributed popularity (exponent 1.0):

| group | share of requests | | group | share of requests |
|--:|--:|---|--:|--:|
| 1 | 29.6 % | | 5 | 5.9 % |
| 2 | 14.8 % | | 8 | 3.7 % |
| 3 | 9.9 % | | 12 | 2.5 % |
| 4 | 7.4 % | | 16 | 1.8 % |

Real traffic looks like this: a few system prompts dominate. With uniform popularity and an even router, every pod's cache would be worth the same and there would be nothing for the controller to choose. Skew is not a trick in the controller's favour; it is what makes the question answerable, and the exponent is a knob (`ZIPF`) so you can show the effect shrinking as traffic becomes uniform.

**Measurement in 30-second windows.** The cost of losing a hot cache is paid by the requests that arrive right after the scale-in; the survivors re-learn the lost prefixes within a minute or two. A 10-minute average would bury the effect under recovery. The report shows the first 30 seconds after scale-in as the headline and every later window as the recovery curve, plus the whole-window average so nobody can accuse it of cherry-picking.

**Three numbers from two independent sources.**

| number | where it comes from | what it answers |
|---|---|---|
| TTFT p50 / p90 / p99 per window | the load tool, client side | what users would feel |
| mean TTFT on the survivors | vLLM's own histogram, server side, from the two snapshots | a cross-check: if client and server disagree wildly, the measurement is broken |
| survivor prefix-cache hit rate | `Δhits ÷ Δqueries` on the surviving pods between the two snapshots | how much cache the fleet kept |

**The rank of the removed pod** is recorded in every trial, so the benchmark also re-verifies goal 1 every time it runs.

### The two load tools

**inference-perf** is the Kubernetes community's benchmarking tool for inference servers (`kubernetes-sigs/inference-perf`). It takes a YAML config with a sequence of load stages (rate and duration), a dataset type (`shared_prefix`: groups of prompts sharing a long system prompt, which is exactly the shape needed), and writes per-stage reports with TTFT percentiles. The project's config template has one warm stage and four 30-second measurement stages, and the orchestrator scales the Deployment at the boundary between them.

**loadgen** is the project's own generator, used as a fallback (`TOOL=loadgen`). It exists because inference-perf's config schema has changed between releases and could not be verified against the version you will install; a schema mismatch should cost five minutes, not the GPU day. It implements the same protocol: deterministic prefixes, Zipf popularity, Poisson arrivals at a fixed rate, warm then measure, TTFT per request, per-window summaries. The report module reads either tool's output.

### Reading the report

`python -m cacheaware.report` turns the trial folders into `results/RESULTS.md`. Its headline table, with illustrative numbers of the kind a successful run produces:

| metric | default scale-in | cache-aware | change |
|---|--:|--:|--:|
| TTFT p90, first 30 s after scale-in | ~300 ms | ~120 ms | −60 % |
| TTFT p90, whole measure window | ~180 ms | ~110 ms | −39 % |
| prefix-cache hit rate on survivors | ~45 % | ~65 % | +44 % |
| coldest pod removed | 1 of 3 trials | 3 of 3 trials | |

Below it, a per-trial table (which pod was removed, its cost rank, the costs at the moment of scale-in, errors) and the recovery curve (TTFT p50 per window). A chart of the recovery curve is saved next to it.

**What a confusing result looks like, and why.** In a baseline trial, Kubernetes sometimes happens to delete the cold pod. That trial is then identical to a cache-aware trial and shows no difference. With three trials per arm this happens in roughly one baseline trial; the averages still separate, and the per-trial table shows exactly which trial it was. If *every* trial looks identical, the caches were not warm enough (`WARM` too short), the traffic was not skewed enough (`GROUPS` too many or `ZIPF` too low), or the router was not keeping prefixes on their pods (check hit rates on the dashboard during the warm-up).

### What the numbers do not say

- Three trials per arm is a small sample. The report prints mean and standard deviation; a reader should look at the per-trial table, not only the mean. More trials (`TRIALS=5`) tighten the averages at eight minutes per pair.
- The effect size depends on the workload. A sensitivity sweep over `ZIPF` and over the scoring weights is the natural next experiment, and the harness runs it with environment variables alone.
- The benchmark measures one scale-in from three to two pods. Larger fleets lose a smaller share of their cache per deletion, so the per-event effect shrinks, though scale-ins also happen more often.

### What to remember

- Fair means one difference: which pod is deleted. Fresh pods, interleaving, identical traffic and identical timing remove the others.
- Skewed prefix popularity is what makes caches unequal; it is a knob, not a thumb on the scale.
- The headline is the first 30 seconds after scale-in; the whole window and the recovery curve are reported too.
- Three numbers from two sources: client TTFT, server TTFT, survivor hit rate.
- A baseline trial that happens to delete the cold pod looks like a cache-aware trial; the per-trial table shows it.
