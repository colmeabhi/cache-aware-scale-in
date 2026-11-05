# Stage 5 — benchmark: default vs cache-aware scale-in (3 GPUs + gateway)

**GPU-day goals 2 and 3.** TTFT and prefix-cache hit rate, default scale-in
vs cache-aware, with inference-perf (or the built-in generator), plus a
live dashboard and a report with the numbers to send.

**What this stage adds.**

| file | role |
|---|---|
| `run_trial.sh MODE N` | one trial: fresh pods → load → warm → scale 3→2 → measure → snapshots → summary |
| `run_benchmark.sh` | N trials per mode, interleaved, sampler on, report at the end |
| `eval.sh` | run_benchmark + sanity checks + prints the numbers |
| `dashboard.sh` | the read-only live dashboard |
| `inference-perf.yaml.template` | 1 warm stage + 4 × 30 s measure stages; **check against your installed version** |
| `cacheaware/live.py` | status/events files and the single metrics sampler |
| `cacheaware/dashboard.py/.html` | web page over those files |
| `cacheaware/report.py` | trial folders → `results/RESULTS.md` + chart |

**Prerequisites.** Stage 4 passing; `GATEWAY_URL` that answers
`/v1/models` (the llm-d gateway, or `http://<node-ip>:<nodeport>` of the
`vllm` Service); optional `pip install inference-perf matplotlib`.

## Run

```bash
export GATEWAY_URL=http://<gateway>
stage5_benchmark/dashboard.sh &              # or in a second tmux pane
stage5_benchmark/eval.sh                      # TRIALS=3 by default, ~50-60 min
```

Variables: `TOOL=inference-perf|loadgen` (default loadgen), `TRIALS`,
`RATE`, `WARM`, `MEASURE`, `GROUPS`, `ZIPF`, `FRESH_PODS`. Details at the
top of `run_trial.sh`.

A single trial by hand: `stage5_benchmark/run_trial.sh cache-aware 1`.

## What a trial writes

```
results/bench/cache-aware/trial_1/
  meta.json            removed pod, its cost rank, costs at scale-in, timings
  pre_snapshot.json    cumulative counters per pod just before the scale-in
  post_snapshot.json   same at the end  -> survivor hit rate = Δhits/Δlookups
  loadgen/summary.json TTFT per 30 s window  (or inference-perf/ report files)
  summary.json         the trial digested (what the dashboard table shows)
```

## Eval pass criterion

`PASS: benchmark data is usable` means every trial has TTFT numbers, error
rate < 5 %, and every cache-aware trial removed the rank-1 pod. The numbers
themselves are in `results/RESULTS.md`; the headline table compares the
first 30 s after scale-in and the whole window, plus survivor hit rate and
the server-side TTFT cross-check.

## If the effect is small

Both modes identical in a trial usually means the baseline happened to pick
the cold pod (1 in 3). Look at the per-trial table. If *every* trial is
identical: caches not warm enough (`WARM=240`), traffic not skewed enough
(`GROUPS=8`), or the router is not sending repeated prefixes to the same
pods (check hit rates on the dashboard during warm-up).

**Next:** stage 6 packages the controller and runs the whole day from one script.
