# Stage 4 — three replicas, scale-in verified (3 GPUs)

**GPU-day goal 1.** A scale-down of the real Deployment removes the
lowest-scored replica.

**What this stage adds.** Only the warm-up is new; the experiment itself is
`scripts/scale_in_trials.sh` from stage 2 with `SKEW=real`.

| file | role |
|---|---|
| `setup.sh` | scale to 3, wait |
| `warm_skewed.sh HOT WARM COLD` | real traffic through three port-forwards: 120 repeated-prefix requests, 60 mixed, 40 unique |
| `eval.sh` | 3 cache-aware trials + 3 baseline trials, summary |

**Why port-forward and not the gateway:** llm-d's router is prefix-aware and
would send every repeated prefix to the same pod, so you could not *choose*
which pod gets cold. Port-forward talks to one pod directly.

## Run

```bash
stage4_scale_in_real/setup.sh
stage4_scale_in_real/eval.sh             # ~25 min with FRESH_PODS=1 (restarts between trials)
```

Per trial you will see the warm-up's lifetime hit rates, then the cost table:

```
POD                 READY COST   HIT    USAGE
vllm-6f7d-abc12     True  694    0.91   0.38
vllm-6f7d-def34     True  412    0.49   0.21
vllm-6f7d-ghi56     True   61    0.02   0.14   <- lowest cost -> should be removed
scaled vllm to 2 replica(s)
Kubernetes removed: vllm-6f7d-ghi56 (cost rank 1 of 3, 1 = lowest)
[PASS] removed the COLD pod
```

## Eval pass criterion

`cache-aware coldest pod removed in 3/3 trials`. The baseline line is
informative: expect about 1/3.

Quicker variant for a first look: `TRIALS=2 FRESH_PODS=0 stage4_scale_in_real/eval.sh`.

## If it fails

- Warm-up hit rates do not separate (hot ≈ cold): the pod is restarting
  between warm-ups, or requests error out (`results/stage4_warm/*/cold.log`).
- Costs separate but the wrong pod is removed: raise `SETTLE`, check all pods were Ready, check `kubectl version`.

**Next:** stage 5 measures what that buys in TTFT.
