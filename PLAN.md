# Plan

## Goal of the GPU day (Fri Nov 13)

1. Prove that a scale-down of the vLLM Deployment removes the lowest-scored
   replica (stage 4 eval).
2. Benchmark default scale-in vs cache-aware scale-in with inference-perf
   (or the built-in load generator) and compare TTFT and prefix-cache hit
   rate (stage 5 eval).
3. Send the numbers (`results/RESULTS.md`, section "Numbers for the resume bullet").
4. While the A100 is rented: run the N-body GPU benchmarks
   (`stage6_final/nbody_a100.sh`, about an hour).

## Before the day (laptop, no GPU)

| step | command | time | done when |
|---|---|---|---|
| install | `pip install -r requirements.txt` | 2 min | |
| stage 1 | `make test` | 1 min | 21 tests pass, scenario prints 700/550/50 |
| stage 2 | `make stage2` (needs docker + kind, or any cluster) | 10 min | cache-aware 3/3, baseline < 3/3 |
| monitoring | `make monitoring` on the same kind cluster | 5 min | `monitoring stack: PASS`, Grafana shows the fake pods' costs |
| inference-perf | `pip install inference-perf`, compare its example config with `stage5_benchmark/inference-perf.yaml.template` | 15 min | config keys match, or decision made to use `TOOL=loadgen` |
| llm-d facts | gateway URL, namespace and labels of the decode pods | 10 min | values for `GATEWAY_URL`, `NAMESPACE`, `SELECTOR`, `DEPLOYMENT` |
| read | guide.md sections 4 and 6 | 30 min | |

## Hands-free option

Fill `cloud/.env` (or the GitHub secrets) and run `cloud/run_remote.sh`, or
the *GPU benchmark* workflow. It provisions, bootstraps k3s + NVIDIA, runs
every phase below with `PAUSE=0`, fetches the results and destroys the
machine. First time: `KEEP_INSTANCE=1` so you can fix any environment
surprise in place. Details in `cloud/README.md`.

## The day (time budget ~4 h of GPU)

```mermaid
gantt
  title GPU day
  dateFormat HH:mm
  axisFormat %H:%M
  section setup
  preflight + stage 3 (1 replica, weights download) :a1, 00:00, 30m
  discover metrics + stage 3 eval                   :a2, after a1, 10m
  section goal 1
  stage 4: 3 replicas, 6 scale-in trials            :b1, after a2, 30m
  section goals 2+3
  stage 5: 3 baseline + 3 cache-aware trials        :c1, after b1, 60m
  report, read numbers, commit results              :c2, after c1, 15m
  section N-body
  scale vllm to 0, nbody_a100.sh                    :d1, after c2, 70m
  push N-body branch, hand GPU back                 :d2, after d1, 10m
```

Commands, in order (details in guide.md section 6):

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_... GATEWAY_URL=http://<gateway>
stage6_final/preflight.sh
stage6_final/run_day.sh            # pauses between phases; PAUSE=0 to run straight through
```

## Risks and what to do

| risk | mitigation already built | fallback |
|---|---|---|
| vLLM metric names differ from what the controller expects | `vllm_metrics.py` knows three generations; stage 3 `discover_metrics.sh` shows which one is live | add the new name to `NAME_CANDIDATES` (one line) |
| inference-perf config schema changed | template is marked "check against installed version" | `TOOL=loadgen` uses the built-in generator, same protocol, same report |
| llm-d manages the pods, not our Deployment | all scripts take `NAMESPACE/SELECTOR/DEPLOYMENT` | point them at llm-d's decode Deployment |
| pods not reachable from where scripts run | scraping goes through the API-server proxy; warm-up uses port-forward | run the scripts on the k3s node |
| baseline accidentally removes the cold pod too (1 in 3 chance per trial) | 3+ trials per mode, rotated cold pod, fresh pods each trial | add trials: `TRIALS=5` |
| effect too small to see | skewed (Zipf) prefix popularity, measurement in 30 s windows right after scale-in | raise `WARM`, lower `GROUPS`, report the first window |
| weights download eats the budget | hostPath HF cache; startupProbe allows 30 min | pre-pull the day before if the box is available |
| GPU busy when N-body starts | script refuses to run if any compute process is on the GPU | `kubectl scale deploy vllm --replicas=0` |

## After the day

- Commit `results/` here and push the N-body results branch.
- Send: the "Numbers for the resume bullet" block from `results/RESULTS.md`,
  the chart `results/ttft_after_scale_in.png`, and the stage 4 line
  "cache-aware: coldest pod removed in N/N trials".
