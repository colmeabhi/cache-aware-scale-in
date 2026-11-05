# Stage 2 — fake pods in a real cluster (kind / k3d / k3s, no GPU)

**Goal.** Prove the Kubernetes half: the controller writes
`pod-deletion-cost`, and `kubectl scale 3 -> 2` really removes the pod with
the lowest cost. This is GPU-day goal 1 rehearsed with fake pods, so the day
itself holds no surprises about the mechanism.

**What this stage adds.**

| file | role |
|---|---|
| `cacheaware/kube.py` | list pods, scrape (direct or API-server proxy), patch annotations |
| `cacheaware/controller.py` `KubeBackend` | the same loop, real pods |
| `scripts/lib.sh` | shell helpers: `pods`, `ready_pods`, `pod_table`, `scrape`, `scale`, `wait_ready`, `fresh_pods`, `controller_on/off` |
| `scripts/scale_in_trials.sh` | the scale-in experiment, reused unchanged by stage 4 |
| `k8s/mock-vllm.yaml` | 3 fake pods from a ConfigMap + stock Python image |

**Prerequisites.** `kubectl`, a cluster (if none: `kind` + docker, and
`setup.sh` creates one), `pip install -r requirements.txt`.

## Run

```bash
stage2_mock_cluster/setup.sh             # namespace, ConfigMap, 3 fake pods, waits for Ready
stage2_mock_cluster/run_controller.sh    # terminal 1: controller, patching for real
```

After ~10 s:

```bash
kubectl get pods -n vllm -o custom-columns=NAME:.metadata.name,COST:.metadata.annotations.controller\\.kubernetes\\.io/pod-deletion-cost
```

## Eval

```bash
stage2_mock_cluster/eval.sh              # terminal 2 (starts/stops its own controller)
```

For each of 3 trials it restarts the pods, makes one of them cold (rotating),
waits 30 s, prints the cost table, scales to 2 and checks which pod vanished.
Then it repeats with the controller off and annotations removed.

Pass looks like:

```
cache-aware  coldest pod removed in 3/3 trials
baseline     coldest pod removed in 1/3 trials
PASS: cache-aware scale-in always removed the coldest pod
```

The baseline number is whatever Kubernetes' age/UID tie-break happened to
pick; anything other than 3/3 is normal. Rows accumulate in
`results/scale_in_trials.csv`.

## If it fails

- Costs stay `-`: controller not running, or `SELECTOR` does not match. Look at `results/controller.log`.
- Removed pod ≠ lowest cost: `kubectl version` server ≥ 1.22? Were all 3 pods Ready (a not-Ready pod is removed first regardless of cost)?
- `port-forward` is not used here; everything goes through the API-server proxy.

**Next:** stage 3 replaces the fake pods with one real vLLM.
