# One target per stage. Each stage's README explains what it needs.
.PHONY: help test stage1 stage2 stage3 stage4 stage5 stage6 monitoring dashboard report clean

help:
	@echo "make test     every unit test + the stage 1 scenario (laptop, no cluster)"
	@echo "make stage1   same as test"
	@echo "make stage2   fake pods in a real cluster, scale-in verified      (kind/k3d/k3s)"
	@echo "make stage3   one real vLLM replica, metrics discovered           (GPU box)"
	@echo "make stage4   three replicas, scale-in removes the coldest        (GPU box)"
	@echo "make stage5   benchmark baseline vs cache-aware + report          (GPU box, GATEWAY_URL)"
	@echo "make stage6   controller image in-cluster + preflight             (GPU box)"
	@echo "make monitoring Prometheus + Loki + Alloy + Grafana add-on, then its eval  (any cluster)"
	@echo "make dashboard  live dashboard on :8080 (read-only)"
	@echo "make report     rebuild results/RESULTS.md from results/bench"

stage1:
	stage1_local_prototype/eval.sh

test: stage1
	PYTHONPATH=. python3 -m pytest -q stage3_vllm_single/tests stage5_benchmark/tests

stage2:
	stage2_mock_cluster/setup.sh
	stage2_mock_cluster/eval.sh

stage3:
	stage3_vllm_single/setup.sh
	stage3_vllm_single/discover_metrics.sh
	stage3_vllm_single/eval.sh

stage4:
	stage4_scale_in_real/setup.sh
	stage4_scale_in_real/eval.sh

stage5:
	stage5_benchmark/eval.sh

stage6:
	stage6_final/build_image.sh
	stage6_final/preflight.sh

monitoring:
	monitoring/setup.sh
	monitoring/eval.sh

dashboard:
	stage5_benchmark/dashboard.sh

report:
	PYTHONPATH=. python3 -m cacheaware.report --results results

clean:
	rm -rf results/live results/bench results/stage3_warm results/stage4_warm results/*.log results/.controller.pid
	find . -name __pycache__ -type d -exec rm -rf {} +
