# Stage 1 — local prototype (laptop, no Kubernetes, no GPU)

**Goal.** Prove the brain of the controller: read a vLLM `/metrics` page,
compute a recent prefix-cache hit rate, turn it into a `pod-deletion-cost`.

**What this stage adds** (in `cacheaware/`):

| file | role |
|---|---|
| `vllm_metrics.py` | parse Prometheus text; know all three generations of vLLM cache metric names |
| `scoring.py` | `PodHistory` (deltas + EWMA), `cache_value`, `deletion_cost`, the dead band |
| `fake_vllm.py` | a fake vLLM pod: `/metrics` with moving counters, knobs via `/set`, a fake streaming `/v1/completions` |
| `controller.py` | the loop, with a `StaticBackend` so it can run against URLs instead of Kubernetes |

**Prerequisites.** Python 3.10+, `pip install pytest`.

## Run

```bash
stage1_local_prototype/run.sh
```

Starts three fake pods (hit rates 0.70 / 0.55 / 0.05) on ports 9001-9003 and
runs the controller in dry-run mode against them. Every 2 s it prints:

```
10:02:11  pod        hit  usage  value  cost   was  action
          pod-a     0.70   0.60   0.67   670   670  dry-run
          pod-b     0.55   0.40   0.51   505   505  dry-run
          pod-c     0.05   0.10   0.07    65    65  dry-run
```

Then, from another terminal, make pod C hot and watch the cost climb over a
few cycles (that is the EWMA):

```bash
curl 'http://127.0.0.1:9003/set?hit_rate=0.9&usage=0.8'
```

Try the old metric names: `NAMES=v0 stage1_local_prototype/run.sh`.

## Eval

```bash
stage1_local_prototype/eval.sh        # or: make test
```

- `tests/test_vllm_metrics.py` — all three metric spellings parse; missing metrics are detected.
- `tests/test_scoring.py` — cost ordering, EWMA, idle intervals, counter resets, dead band.
- `tests/test_controller.py` — the loop against an in-memory backend: ordering, dead band, scrape errors keep the old cost, dry-run never patches.
- `eval_scenario.py` — three real fake pods, real controller, expects 700 / 550 / 50 with pod C lowest.

Pass looks like `21 passed` (stage 1 tests alone) and `PASS: coldest cache gets the lowest pod-deletion-cost`.

## If it fails

Only Python is involved. Run `python -m pytest -q stage1_local_prototype/tests -x`
and read the first failure.

**Next:** stage 2 puts the same fake pods into a real cluster.
