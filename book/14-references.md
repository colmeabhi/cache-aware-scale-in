## References and further reading

URLs are given as known at the time of writing; the pages were not re-fetched while writing this book, so check a link before quoting it in a paper.

### Papers

1. Vaswani et al., *Attention Is All You Need* (2017). The transformer and the attention mechanism whose keys and values the KV cache stores. https://arxiv.org/abs/1706.03762
2. Kwon et al., *Efficient Memory Management for Large Language Model Serving with PagedAttention* (SOSP 2023). The vLLM paper: block-based KV cache, sharing, continuous batching. https://arxiv.org/abs/2309.06180
3. Zheng et al., *SGLang: Efficient Execution of Structured Language Model Programs* (2023). RadixAttention, the other well-known prefix-caching design. https://arxiv.org/abs/2312.07104
4. Grattafiori et al., *The Llama 3 Herd of Models* (2024). Architecture details (layers, heads) used for the KV-cache arithmetic in Part I. https://arxiv.org/abs/2407.21783
5. Karger et al., *Consistent Hashing and Random Trees* (STOC 1997). The ring used by the built-in router. https://dl.acm.org/doi/10.1145/258533.258660
6. Breslau et al., *Web Caching and Zipf-like Distributions: Evidence and Implications* (INFOCOM 1999). Why request popularity is modelled as Zipf. https://ieeexplore.ieee.org/document/749260
7. Nyland, Harris, Prins, *Fast N-Body Simulation with CUDA*, GPU Gems 3, chapter 31 (2007). The tiled kernel in Part VII. https://developer.nvidia.com/gpugems/gpugems3/part-v-physics-simulation/chapter-31-fast-n-body-simulation-cuda
8. Barnes and Hut, *A hierarchical O(N log N) force-calculation algorithm*, Nature 324 (1986). https://www.nature.com/articles/324446a0

### Kubernetes

9. ReplicaSet: pod deletion cost. The annotation, its semantics, and the warning against frequent updates. https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/#pod-deletion-cost
10. KEP-2255, Pod Cost. The design proposal behind the annotation. https://github.com/kubernetes/enhancements/tree/master/keps/sig-apps/2255-pod-cost
11. Deployments. https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
12. Using RBAC authorization. https://kubernetes.io/docs/reference/access-authn-authz/rbac/
13. k3s documentation, including NVIDIA container runtime support. https://docs.k3s.io/ and https://docs.k3s.io/advanced#nvidia-container-runtime-support
14. kind (Kubernetes in Docker). https://kind.sigs.k8s.io/
15. NVIDIA device plugin for Kubernetes. https://github.com/NVIDIA/k8s-device-plugin
16. NVIDIA container toolkit installation guide. https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

### Serving and routing

17. vLLM documentation. https://docs.vllm.ai/
18. vLLM: automatic prefix caching. https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html
19. vLLM: metrics design. https://docs.vllm.ai/en/latest/design/metrics.html
20. vLLM source. https://github.com/vllm-project/vllm
21. llm-d. https://llm-d.ai/ and https://github.com/llm-d/llm-d
22. Kubernetes Gateway API Inference Extension (the endpoint-picker model llm-d builds on). https://gateway-api-inference-extension.sigs.k8s.io/
23. inference-perf. https://github.com/kubernetes-sigs/inference-perf
24. OpenAI completions API reference (the request and streaming format vLLM mirrors). https://platform.openai.com/docs/api-reference/completions
25. Server-sent events, WHATWG HTML standard. https://html.spec.whatwg.org/multipage/server-sent-events.html
26. Meta Llama 3 8B Instruct on Hugging Face (gated; needs a token). https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct

### Observability

27. Prometheus overview and PromQL basics. https://prometheus.io/docs/introduction/overview/ and https://prometheus.io/docs/prometheus/latest/querying/basics/
28. Prometheus exposition format (the `/metrics` text format). https://prometheus.io/docs/instrumenting/exposition_formats/
29. Grafana Loki. https://grafana.com/docs/loki/latest/
30. Grafana Alloy. https://grafana.com/docs/alloy/latest/
31. Grafana provisioning (data sources and dashboards from files). https://grafana.com/docs/grafana/latest/administration/provisioning/
32. NVIDIA Nsight Compute. https://docs.nvidia.com/nsight-compute/

### Background mathematics

33. Exponential smoothing (EWMA). https://en.wikipedia.org/wiki/Exponential_smoothing
34. Zipf's law. https://en.wikipedia.org/wiki/Zipf%27s_law

### This project

35. The repository: https://github.com/colmeabhi/cache-aware-scale-in — `guide.md` (design decisions, runbook, troubleshooting), `PLAN.md`, per-stage READMEs, `monitoring/README.md`, `cloud/README.md`.
36. The N-body repository: https://github.com/colmeabhi/parallelized-nbody-simulation-cuda-cpp
