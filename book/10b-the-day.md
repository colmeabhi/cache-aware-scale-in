## Part VI · Chapter 2: The GPU day, as a story

This chapter tells the day once through, with what you see at each step and what you decide. Terminal excerpts are illustrative: the shape is exact, the numbers are what a typical successful run looks like.

### 09:00 · The machine

You have an Ubuntu box with three A100s and SSH. Either `cloud/run_remote.sh` did everything below for you and you are reading its log, or you are doing it by hand. By hand, you open `tmux` with four panes: one to run things, one for `kubectl get pods -n vllm -w`, one for the dashboard, one for the Grafana port-forwards. On your laptop:

```bash
ssh -L 8080:127.0.0.1:8080 -L 3000:127.0.0.1:3000 user@gpu-box
```

Then, on the box:

```bash
sudo cloud/bootstrap.sh          # only on a fresh machine; 3-6 minutes
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_...
stage6_final/preflight.sh
```

```
[PASS] kubectl installed
[PASS] cluster reachable
[PASS] python3 >= 3.10
[PASS] python kubernetes client
[PASS] a node advertises nvidia.com/gpu
[FAIL] deployment vllm exists
[FAIL] pods matching 'app=vllm' are ready
[PASS] stage 5 will use the built-in prefix-affinity router (no llm-d gateway given)
[FAIL] NO-GO: 2 problem(s) above
```

The two failures are expected before stage 3 has run; they are the next step, not a problem. Decision: proceed.

### 09:10 · One real vLLM (stage 3)

```bash
stage3_vllm_single/setup.sh
```

The first start downloads about 16 GB of weights into `/var/lib/hf-cache`; the `kubectl get pods -w` pane shows the pod in `ContainerCreating`, then `Running` but not Ready, for five to twenty minutes depending on the network. The script waits for it. Then:

```bash
stage3_vllm_single/discover_metrics.sh
```

```
== vllm-6f7d9c8b4-x2k9p
-- vLLM version:
{"version": "0.11.0"}
-- what the controller reads:
  kv_usage        vllm:kv_cache_usage_perc                   0.0012
  prefix_queries  vllm:prefix_cache_queries                  0
  prefix_hits     vllm:prefix_cache_hits                     0
  ...
```

This is the moment the metric-name question is settled. If a line said `-- not found --` for both `prefix_hits` and `hit_rate_gauge`, you would stop here and add the new name to `NAME_CANDIDATES`; it has not happened in any version so far. Then:

```bash
stage3_vllm_single/eval.sh
```

```
model=meta-llama/Meta-Llama-3-8B-Instruct groups=2 zipf=1.0 prefix_words=1200
  prefix-cache lookups during the run: 65,920 tokens, hits: 59,904 tokens, hit rate 0.909
  PASS: prefix caching works
```

Forty requests over two prefixes; nine tokens in ten came from cache. The load generator also prints its TTFT summary: the first request of each prefix around 170 ms, the rest around 20 ms. Decision: the foundation holds; proceed.

### 09:40 · Three replicas and the scale-in test (stage 4)

```bash
stage4_scale_in_real/setup.sh
stage4_scale_in_real/eval.sh
```

Each trial begins with a restart of all three pods (a minute or two, weights already on disk), then the three warm-ups run in parallel through port-forwards. The pod table appears:

```
  POD                                 READY  COST   HIT    USAGE
  vllm-6f7d9c8b4-abc12                True   694    0.91   0.38
  vllm-6f7d9c8b4-def34                True   412    0.49   0.21
  vllm-6f7d9c8b4-ghi56                True    61    0.02   0.14
[10:02:40] lowest cost -> Kubernetes should remove: vllm-6f7d9c8b4-ghi56
[10:02:40] scaled vllm to 2 replica(s)
[10:03:12] Kubernetes removed: vllm-6f7d9c8b4-ghi56 (cost rank 1 of 3, 1 = lowest)
[PASS] removed the COLD pod
```

Three trials, the cold pod rotating, three passes. Then three baseline trials with the controller off; two of the three remove a warm or hot pod, one happens to remove the cold one. The summary:

```
  cache-aware  coldest pod removed in 3/3 trials
  baseline     coldest pod removed in 1/3 trials
  PASS: cache-aware scale-in removed the coldest replica every time
```

Goal 1 is done, and `results/scale_in_trials.csv` has the six rows. Decision: proceed to the benchmark. If a cache-aware trial had failed with the right pod table (the lowest-cost pod clearly marked, but another removed), you would check `kubectl version`, raise `SETTLE`, and rerun that trial before anything else.

### 10:15 · The benchmark (stage 5)

In the dashboard pane:

```bash
stage5_benchmark/dashboard.sh
```

and in the main pane:

```bash
stage5_benchmark/eval.sh
```

The script starts the metrics sampler and, because no `GATEWAY_URL` is set, the built-in router on port 8081. Trial 1, baseline: pods restart, the load generator starts at four requests per second, and on the dashboard at `http://localhost:8080` the three hit-rate lines climb from zero over the two-minute warm-up: one towards 0.9, one towards 0.5, one staying low, because the router keeps each prefix on one pod and the popular prefixes are few. The phase chip reads *warming*, then *measuring*; the "last scale-in" card shows which pod went and its rank. In Grafana at `http://localhost:3000/d/cache-aware-scale-in`, a red line marks the scale-in on every panel.

What you watch for: in a baseline trial where the hot pod was removed, the survivors' hit-rate lines dip right after the red line and recover over the next minute, and the TTFT panel spikes. In a cache-aware trial, nothing visible happens at the red line. That picture *is* the result, before any table.

Six trials take about fifty minutes. The trials table at the bottom of the dashboard fills in one row at a time. At the end:

```
| metric                                  | default scale-in | cache-aware    | change  |
| TTFT p90, first 30 s after scale-in     | 312 ms ± 95 ms   | 118 ms ± 11 ms | -62.2%  |
| TTFT p90, whole measure window          | 184 ms ± 40 ms   | 109 ms ± 8 ms  | -40.8%  |
| prefix-cache hit rate on survivors      | 46.3%            | 66.1%          | +42.8%  |
| coldest pod removed                     | 1/3 trials       | 3/3 trials     |         |

  6 trials checked
  PASS: benchmark data is usable
```

The large standard deviation on the baseline is the one trial that happened to remove the cold pod; the per-trial table shows it. Decision: the numbers are quotable. Commit `results/`.

### 11:20 · If something had gone wrong instead

- The load generator printing `status=0` errors: the router or gateway is not reachable from where the script runs; `curl $GATEWAY_URL/v1/models` tells you in one line.
- Both arms identical in every trial: look at the warm-up hit rates on the dashboard. If all three lines are similar, the traffic is not skewed enough or the router is not sticky; lower `GROUPS` to 8 and rerun one pair of trials.
- A cache-aware trial with rank 2 or 3: the annotation was not on the pod when the scale happened. `SETTLE=60`.

### 11:30 · The N-body benchmarks (Part VII)

```bash
kubectl scale deploy -n vllm vllm --replicas=0
stage6_final/nbody_a100.sh
```

The script refuses to start while vLLM still holds the GPU, which is why the first line exists. Then about an hour of output: CPU scaling, Barnes–Hut crossover, GPU scaling for naive and tiled kernels, the block-size sweep, energy conservation, Nsight Compute counters, and finally the regenerated README with the GPU rows filled in. It ends with the commit on a results branch and the push command.

### 12:45 · Handing the GPU back

Commit this repository's `results/`, push the N-body branch, copy the "Numbers for the resume bullet" block from `results/RESULTS.md`, and stop the machine. Everything else can be done from a laptop.

### What to remember

- Three decision points: metric names found (stage 3), coldest pod removed 3/3 (stage 4), benchmark data usable (stage 5). Each has a one-line pass condition.
- The dashboard hit-rate lines at the red scale-in marker show the result before any table does.
- A baseline trial that removes the cold pod is expected and shows up as a wide standard deviation.
- Scale vLLM to zero before the N-body run.
