# Stage 3 — one real vLLM replica (1 GPU)

**Goal.** Get a real vLLM serving Llama 3 8B, learn which metric names this
version exposes, and confirm prefix caching does what the whole project
assumes.

**What this stage adds.**

| file | role |
|---|---|
| `k8s/vllm.yaml` | Deployment (1 GPU, prefix caching on, HF weight cache on the host) + Service |
| `cacheaware/loadgen.py` | prefix-heavy traffic generator with client-side TTFT (`warm` and `bench`) |
| `discover_metrics.sh` | prints and saves the exact metric names; shows what the controller reads |

**Prerequisites.** k3s with the NVIDIA device plugin (a node must show
`nvidia.com/gpu` allocatable), `HF_TOKEN` with access to Llama 3, `curl`.

If llm-d already serves vLLM for you, skip `setup.sh` and set
`NAMESPACE`, `SELECTOR`, `DEPLOYMENT` to its decode pods; the other two
scripts work the same.

## Run

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_...
stage3_vllm_single/setup.sh              # first start downloads ~16 GB; wait_ready allows 40 min
stage3_vllm_single/discover_metrics.sh   # which spelling does this vLLM use?
```

`discover_metrics.sh` output to look for:

```
prefix_queries  vllm:prefix_cache_queries   12345
prefix_hits     vllm:prefix_cache_hits      9876
```

If it prints `-- not found --` for both `prefix_hits` and `hit_rate_gauge`,
stop: nothing downstream can work until that is fixed (see guide.md 8).

## Eval

```bash
stage3_vllm_single/eval.sh
```

Port-forwards to the pod, sends 40 requests over 2 shared ~1 600-token
prefixes, and compares the hit counters before and after. Pass looks like:

```
prefix-cache lookups during the run: 64000 tokens, hits: 58000 tokens, hit rate 0.906
PASS: prefix caching works
```

It also prints the load generator's TTFT summary, your first real numbers.

## If it fails

- Pod never Ready: `kubectl logs -n vllm deploy/vllm -f` (gated model → 401 means the token; OOM → lower `--max-model-len`).
- Hit rate ~0: `--enable-prefix-caching` missing, or the vLLM version ignores it; check the args in the pod spec.

**Next:** stage 4 scales to 3 replicas and runs the real scale-in experiment.
