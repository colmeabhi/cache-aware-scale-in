## Limitations, caveats and what comes next

A project is only as credible as its stated limits. These are the ones that matter.

### What has and has not been executed

- Stages 1 and the local rehearsal of the stage 5 pipeline (load generator, sampler, report, dashboard) were executed on fake pods and pass; 39 unit tests pass.
- Stages 2 to 6, the monitoring stack and the cloud layer were written and checked (syntax, manifest parsing, image tags) but not executed, because the writing environment had no cluster and no GPU. Stage 2 and the monitoring eval will be executed by CI on the first push; stages 3 to 6 execute on the GPU day.
- The vLLM metric names and the inference-perf config schema were written from knowledge, not verified against the versions you will install. Both have cheap fallbacks: a one-line addition to the name list, and the built-in load generator. The first fallback has now been used: vLLM 0.32.0 spells the counters `prefix_cache_*_total`, found on the first Modal run and added with a test (Part VI).
- The stage 5 benchmark has run on real GPUs through the Modal path (`cloud/modal_bench.py`: three vLLM replicas on three A100s, the repo's router, scoring and load generator, scale-in emulated without Kubernetes). The smoke test ran cleanly and showed no difference between the arms, for reasons described in Part VI and below.

### Limits of the idea

- **Staleness.** A cost is at most five seconds old. A sudden traffic shift between the last scrape and the scale-in can beat it.
- **Best effort.** `pod-deletion-cost` is documented as best effort. The experiments verify it empirically, but Kubernetes does not promise it under every race.
- **Node spreading.** Cost ordering comes *before* the rule that spreads pods across nodes. On a multi-node cluster the controller could therefore concentrate replicas on one node. A cap on the cost gap when it would break spreading is a natural guard.
- **One ReplicaSet.** The annotation only orders pods within one ReplicaSet. llm-d's disaggregated layouts use other controllers, where the mechanism differs.
- **Weights are a judgment.** 0.7 and 0.3 were chosen, not fitted. A sweep is the obvious next experiment and the harness supports it with environment variables.

### Limits of the measurement

- Three trials per arm is a small sample; the per-trial table matters more than the mean.
- The effect size depends on how skewed the traffic is and on how prefix-aware the router is. The benchmark reports the conditions; a reader should not generalise beyond them.
- **Stage 5's default traffic does not make caches unequal.** Measured on Modal: with 16 Zipf-weighted prefix groups behind the prefix-affinity router, all three replicas converged to a ~95 % token hit rate within 45 s (costs 663 / 666 / 677), and KV usage read ~0 because idle cached blocks count as free. The router skewed *volume* (235 vs 69 requests per replica), which the score does not see. A benchmark that wants an effect must create a genuinely cold replica (stage 4's skewed warm-up) and make lost caches expensive (longer prefixes, a smaller KV budget).
- One scale-in event, three to two pods. Larger fleets lose less per event.

### What comes next, in order of value

1. **Sensitivity sweep.** Run the benchmark at several `ZIPF` values and at hit-rate-only weights. A chart of "benefit versus traffic skew" is more credible than one number and preempts the obvious question.
2. **Cached tokens per second as the score.** `Δhits ÷ Δtime` measures prefill actually avoided per second, which is closer to what should be protected than a hit *rate*: a pod at 70 % on one request per second protects less than a pod at 55 % on ten. One line in `scoring.py`; the stage 2 and 4 harness can compare the two rules head to head. The first Modal run is direct evidence for this: hit rates saturated at ~95 % on every replica while request volume differed by more than 3×.
3. **Pre-drain hint to the router.** Tell the router to stop assigning new prefixes to the lowest-cost pod a minute before a planned scale-in, so even less is lost. Turns a controller into a system.
4. **Node-spread guard** as described above.
5. **A monitoring-off control pair** in the benchmark, so the "it does not disturb the measurement" argument becomes a measured row.
6. **Production shape.** Liveness and readiness probes on the controller pod, two alert rules (scrape errors rising, a pod without a cost for over a minute), a watch-based pod list instead of polling, an image published from CI.
