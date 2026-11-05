# Stage 6 — final setup

**Goal.** The controller as a real in-cluster component (image + RBAC +
Deployment), a go/no-go preflight, a one-command runner for the GPU day,
and the N-body benchmark runbook for the same rented A100.

| file | role |
|---|---|
| `Dockerfile` | python:3.12-slim + kubernetes client + `cacheaware/`, non-root |
| `k8s/rbac.yaml` | ServiceAccount, Role (pods get/list/watch/patch, pods/proxy get), RoleBinding |
| `k8s/controller.yaml` | Deployment, `Recreate` strategy, settings as env vars |
| `build_image.sh` | build, import into k3s (or kind), apply RBAC + Deployment |
| `preflight.sh` | read-only checks: cluster, GPUs, pods, metrics, gateway, tools |
| `run_day.sh` | preflight → stage 3 → 4 → 5 → report → N-body, pausing between phases |
| `nbody_a100.sh` | clone/update the N-body repo, run its benchmark + Nsight + report scripts, commit on a results branch |
| `checklist.md` | before / during / after the day |

## Run

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml HF_TOKEN=hf_... GATEWAY_URL=http://<gateway>
stage6_final/preflight.sh                   # GO / NO-GO
stage6_final/build_image.sh                 # optional: controller in-cluster
export CONTROLLER_MODE=cluster              # ...and tell the stage 4/5 scripts to use it
stage6_final/run_day.sh                     # PAUSE=0 to run without stopping
```

With `CONTROLLER_MODE=cluster`, `controller_on`/`controller_off` scale the
in-cluster Deployment to 1/0 instead of starting a local process. The
baseline still strips the annotations.

## N-body

```bash
kubectl scale deploy -n vllm vllm --replicas=0     # give the GPU back first
stage6_final/nbody_a100.sh                         # ~1 h; QUICK=1 for a 5-min smoke test
git -C ~/parallelized-nbody-simulation-cuda-cpp push -u origin results/a100-<date>
```

The N-body repository's own `scripts/run_benchmarks.sh`,
`scripts/profile_ncu.sh` and `scripts/make_report.py` do the work; this
runbook only sets `CUDA_ARCH` from `nvidia-smi`, refuses to start while the
GPU is busy, installs numpy/matplotlib if missing, and commits the results.
It reruns the CPU benchmarks too, so the speedup column compares against
the same machine.

## Eval

`preflight.sh` exit code 0 is the eval of this stage. The day's real evals
are stages 3-5, which `run_day.sh` calls in order.
