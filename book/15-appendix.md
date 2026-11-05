## Appendix: cheat sheet

### Commands by stage

```bash
# stage 1 — laptop
make test                              # all unit tests + the 3-fake-pod scenario
stage1_local_prototype/run.sh          # watch the controller think (dry-run)

# stage 2 — any cluster (kind is created if none is reachable)
stage2_mock_cluster/setup.sh
stage2_mock_cluster/run_controller.sh  # terminal 1
stage2_mock_cluster/eval.sh            # terminal 2: 3 cache-aware + 3 baseline trials

# stage 3 — one GPU
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_...
stage3_vllm_single/setup.sh
stage3_vllm_single/discover_metrics.sh
stage3_vllm_single/eval.sh

# stage 4 — three GPUs
stage4_scale_in_real/setup.sh
stage4_scale_in_real/eval.sh

# stage 5 — benchmark (GATEWAY_URL, or leave unset for the built-in router)
stage5_benchmark/dashboard.sh &        # http://127.0.0.1:8080, tunnel it with ssh -L
stage5_benchmark/eval.sh
python -m cacheaware.report --results results

# stage 6 — packaging and the whole day
stage6_final/build_image.sh && export CONTROLLER_MODE=cluster
stage6_final/preflight.sh
stage6_final/run_day.sh                # PAUSE=0 to run straight through
stage6_final/nbody_a100.sh             # after: kubectl scale deploy vllm --replicas=0

# add-ons
make monitoring                        # Prometheus + Loki + Alloy + Grafana, then eval
monitoring/open.sh                     # port-forward 3000 / 9090 / 3100
cp cloud/.env.example cloud/.env && cloud/run_remote.sh   # hands-free day
```

### Environment variables

| variable | default | used by |
|---|---|---|
| `NAMESPACE` | `vllm` | every cluster script |
| `SELECTOR` | `app=vllm` | which pods are "the vLLM pods" (controller, scripts, Prometheus) |
| `DEPLOYMENT` | `vllm` | what gets scaled 3 → 2 |
| `METRICS_PORT` | `8000` | vLLM's HTTP port |
| `RESULTS_DIR` | `results/` | where everything lands |
| `CONTROLLER_MODE` | `local` | `cluster` to switch the in-cluster Deployment on/off instead |
| `GATEWAY_URL` | unset | the llm-d gateway; unset = built-in router on `127.0.0.1:8081` |
| `TRIALS` | `3` | benchmark trials per mode |
| `TOOL` | `loadgen` | `inference-perf` to use it |
| `RATE`, `WARM`, `MEASURE` | `4`, `120`, `120` | requests/s, warm seconds, measure seconds |
| `GROUPS`, `ZIPF` | `16`, `1.0` | prefix groups and popularity skew (loadgen) |
| `FRESH_PODS` | `1` | restart pods before each trial |
| `SETTLE` | `30` | seconds for the controller to re-score after a warm-up |
| `HF_TOKEN` | required | Llama 3 weights |
| `MODEL`, `VLLM_IMAGE` | Llama 3 8B Instruct, `vllm/vllm-openai:latest` | stage 3 manifest |
| `W_HIT`, `W_USAGE`, `W_INFLIGHT` | `0.7`, `0.3`, `0` | controller weights |
| `INTERVAL`, `MIN_DELTA`, `EWMA_ALPHA` | `5`, `25`, `0.3` | controller loop |
| `METRICS_LISTEN` | unset | `0.0.0.0:9108` to expose the controller's own metrics |
| `MONITORING`, `NBODY`, `PAUSE` | `1`, `1`, `1` | `run_day.sh` phases |
| `GPU_PROVIDER`, `GPU_SSH_HOST`, `GPU_SSH_USER`, `GPU_SSH_KEY`, `KEEP_INSTANCE` | `manual`, –, `ubuntu`, `~/.ssh/id_ed25519`, `0` | cloud runner |

### Where results land

| file | from | contents |
|---|---|---|
| `results/scale_in_trials.csv` | stages 2 and 4 | one row per trial: cold pod, costs, removed pod, rank, verdict |
| `results/metrics_discovery_*.txt` | stage 3 | the vLLM version and the metric names it exposes |
| `results/bench/<mode>/trial_<n>/` | stage 5 | `meta.json`, pre/post snapshots, load-tool output, `summary.json` |
| `results/RESULTS.md`, `results/ttft_after_scale_in.png` | report | the comparison tables and chart |
| `results/live/` | sampler and scripts | what the file-based dashboard reads |
| `results/controller.log`, `router.log`, `sampler.log` | scripts | process logs |
| `results/remote-<time>/` | cloud runner | a copy of all of the above from the GPU machine |

### Reading the code, in order

`cacheaware/scoring.py` → `cacheaware/controller.py` → `scripts/lib.sh` → `scripts/scale_in_trials.sh` → `stage5_benchmark/run_trial.sh` → `cacheaware/report.py` → `cacheaware/router.py`. About 1,200 lines in total.
