"""cacheaware — a cache-aware scale-in controller for vLLM on Kubernetes.

The package is built up stage by stage (see the stage*/ folders):

    stage 1   vllm_metrics.py   read cache signals from a vLLM /metrics page
              scoring.py        turn those signals into a pod-deletion-cost
              fake_vllm.py      a fake vLLM pod so you can test without a GPU
    stage 2   kube.py           the few Kubernetes calls we need
              controller.py     the control loop (scrape -> score -> annotate)
    stage 3   loadgen.py        prefix-heavy traffic generator + TTFT measurement
    stage 5   live.py           status + metric sampler that feeds the dashboard
              dashboard.py      tiny web server for the live dashboard
              report.py         baseline vs cache-aware comparison report
    add-on    metrics_exporter.py  the controller's own Prometheus /metrics (monitoring/)
"""
