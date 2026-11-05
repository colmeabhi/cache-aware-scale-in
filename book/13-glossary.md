## Glossary

| term | plain meaning |
|---|---|
| A100 | an NVIDIA data-centre GPU with 40 or 80 GB of memory; the project uses three |
| Alloy | Grafana's log collector; reads pod logs through the Kubernetes API and pushes them to Loki |
| annotation | a key-value note on a Kubernetes object, not used for selection; `pod-deletion-cost` is one |
| API server | the front door of Kubernetes; stores desired state and answers `kubectl` |
| baseline | the comparison arm of the benchmark: controller stopped and annotations removed, so Kubernetes chooses alone |
| cache value | the controller's 0..1 score for a pod: 0.7 × recent hit rate + 0.3 × KV usage |
| consistent hashing | placing servers and keys on a ring so that removing a server moves only its own keys |
| container | a program packaged with everything it needs, running from an image |
| continuous batching | mixing prefill and decode steps of many requests in each GPU step so the GPU never idles |
| controller | a loop that observes state and writes a change back; here, the program that scores pods and writes costs |
| CUDA | NVIDIA's programming model for running code on GPUs |
| dead band | the minimum change (25) in cost before the controller rewrites the annotation |
| decode | the phase that produces an answer one token at a time |
| deletion cost | `controller.kubernetes.io/pod-deletion-cost`; lower is deleted first on scale-in |
| Deployment | a Kubernetes object that keeps N identical pods running through a ReplicaSet |
| device plugin | the NVIDIA component that lets a node advertise GPUs to Kubernetes |
| EWMA | exponentially weighted moving average; smoothing where each new reading gets weight α (0.3) and the old estimate 1 − α |
| gateway | the proxy in front of the vLLM pods that chooses which pod serves each request |
| hit rate | prefix-cache hits ÷ queries, in tokens, over some window |
| inference-perf | the Kubernetes community's load-testing tool for inference servers |
| k3s | a small single-binary Kubernetes distribution |
| kind | "Kubernetes in Docker": a laptop cluster for testing |
| kubectl | the Kubernetes command-line tool |
| KV cache | the stored attention keys and values of every token, kept on the GPU during generation |
| label / selector | a key-value tag used to pick sets of pods, and the expression that picks them (`app=vllm`) |
| llm-d | an open-source platform for serving vLLM at scale on Kubernetes, with a prefix-aware gateway |
| Loki | Grafana's log store, queried with LogQL |
| namespace | a named partition of Kubernetes objects (`vllm`, `monitoring`) |
| node | one machine in a Kubernetes cluster |
| PagedAttention | vLLM's technique of storing the KV cache in fixed-size shareable blocks |
| pod | the smallest unit Kubernetes runs; here one vLLM server with one GPU |
| port-forward | a `kubectl` tunnel from your machine straight to one pod, bypassing routers |
| prefill | the phase that reads the whole prompt and computes its keys and values; dominates TTFT |
| prefix caching | reusing the KV entries of a shared prompt beginning instead of recomputing them |
| prefix group | in the load generator, one long deterministic system prompt that many requests share |
| Prometheus | the metrics database that scrapes `/metrics` pages on a schedule; queried with PromQL |
| readiness probe | a URL Kubernetes polls to decide whether a pod may receive traffic |
| ReplicaSet | the Kubernetes controller that keeps exactly N pods and chooses which to delete on scale-in |
| RuntimeClass | a Kubernetes setting that selects a container runtime, here the NVIDIA one |
| sampler | the one process in stage 5 that scrapes pod metrics for the file-based dashboard |
| scale-in / scale-out | lowering / raising a Deployment's replica count |
| scrape | fetching a `/metrics` page |
| server-sent events | the streaming format (`data: ...` lines) vLLM uses to send tokens as they are produced |
| system prompt | the fixed instructions at the start of every prompt of an application; the typical shared prefix |
| token | the unit a model reads and writes; about three quarters of an English word |
| TTFT | time to first token: how long a user waits for the first word of the answer |
| vLLM | the open-source inference server used in the project |
| Zipf distribution | a popularity curve where the k-th most popular item has share proportional to 1/k; models skewed real traffic |
