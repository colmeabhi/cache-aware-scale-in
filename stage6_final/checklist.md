# GPU-day checklist (Fri Nov 13)

## The week before (laptop, no GPU)

- [ ] `make stage1` passes (unit tests + scenario).
- [ ] `make stage2` passes on kind/k3d: cache-aware 3/3, baseline not 3/3.
- [ ] `pip show inference-perf` and compare its example config with
      `stage5_benchmark/inference-perf.yaml.template`; fix keys if needed.
      (If it fights you, decide now to use `TOOL=loadgen`.)
- [ ] Hugging Face token with Llama 3 access ready (`HF_TOKEN`).
- [ ] Know your llm-d gateway URL and the labels of its decode pods
      (`kubectl get pods -A --show-labels | grep -i decode`).
- [ ] Read guide.md sections 4 (decisions) and 6 (runbook) once.

## On the box, before starting the clock

- [ ] `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=... GATEWAY_URL=...`
- [ ] `git clone` this repo; `pip install -r requirements.txt`
- [ ] `stage6_final/preflight.sh` says GO.
- [ ] `tmux` with 3 panes: runner, `stage5_benchmark/dashboard.sh`, `kubectl get pods -w`.
- [ ] Laptop: `ssh -L 8080:127.0.0.1:8080 user@box`, open http://localhost:8080.

## The run (about 3 h of GPU time + 1 h N-body)

| when | what | pass looks like |
|---|---|---|
| 0:00 | stage 3 setup (skip if llm-d already serves) | 1 pod Ready |
| 0:20 | discover_metrics + stage 3 eval | hit rate > 0.5 |
| 0:30 | stage 4 eval | cache-aware 3/3 coldest removed |
| 1:00 | stage 5 eval, dashboard open | 6 trials, TTFT first-window lower for cache-aware |
| 2:00 | read results/RESULTS.md, copy "numbers for the resume bullet" | |
| 2:10 | `kubectl scale deploy vllm --replicas=0`, then nbody_a100.sh | GPU rows filled in README |
| 3:15 | push N-body branch; commit results/ here | |

## Before giving the GPU back

- [ ] `results/RESULTS.md`, `results/ttft_after_scale_in.png`, `results/scale_in_trials.csv`, `results/metrics_discovery_*.txt` committed.
- [ ] N-body branch pushed.
- [ ] Copy the "Numbers for the resume bullet" section into the message you send.
