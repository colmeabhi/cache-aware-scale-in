## Questions people ask

**Why not just share the KV cache between pods so nothing is lost?**
Sharing GPU memory across machines needs a fast interconnect and a transfer layer (llm-d is working on exactly this with KV-cache offload and transfer). It is heavier, newer, and solves a different problem: moving cache. This project solves the cheaper one: not throwing away the best cache when you did not need to. The two compose.

**Why not let the router handle it?**
The router decides where new requests go; it is not consulted when Kubernetes deletes a pod. Until the deletion happens, the router has no reason to move anything, and after it happens, the cache is gone. A pre-drain hint from the controller to the router is the natural extension (Limitations chapter).

**Why not use an autoscaler like the HPA or KEDA?**
Autoscalers decide *how many* replicas to run, not *which* ones to remove. `pod-deletion-cost` is the knob Kubernetes provides for the second question, and it works underneath any autoscaler.

**Why not delete the pod from the controller directly?**
It would race the ReplicaSet controller, which is also deleting pods to reach the new replica count, and both might delete one each. Ranking and letting Kubernetes delete is race-free and needs no delete permission.

**Why hit rate and usage, and not the number of requests in flight?**
In-flight requests are protected by the termination grace period: a deleted pod gets 30 seconds to finish them. The cache is the thing nothing protects.

**Why a dead band of 25 and not write every cycle?**
The ranking only has to be ordered correctly. Kubernetes documentation asks for this annotation not to be updated on every metric tick, because each update is an API-server write. The dead band keeps writes rare without changing the order.

**Why five seconds?**
Fast enough that a cost is never more than one cache warm-up old; slow enough that three pods cost nothing to poll. On a fleet of hundreds you would use a watch and Prometheus; the parsing and scoring would not change.

**Why not read the annotations in Grafana instead of adding metrics to the controller?**
kube-state-metrics does not expose arbitrary annotations, and reading them would need another exporter anyway. The controller already has the numbers; publishing them is 80 lines.

**Does the controller work with llm-d?**
Yes, provided llm-d's decode pods are an ordinary Deployment. Point `NAMESPACE`, `SELECTOR` and `DEPLOYMENT` at them. The controller never talks to the gateway. llm-d's disaggregated layouts use other controllers, where the annotation does not apply.

**What if the three pods are on different nodes?**
Cost ordering comes before the node-spreading rule, so the controller could leave two pods on one node and none on another. A guard that caps the cost gap when it would break spreading is listed as future work.

**Why restart the pods before every benchmark trial?**
So they have the same age. Kubernetes' default tie-break prefers the most recently ready pod, which after a scale-out is also the coldest; without the restart the baseline would look smart by accident.

**Why is the baseline sometimes as good as the controller?**
Because with three pods, a blind choice is right one time in three. Over several trials the averages separate; the per-trial table shows which trial it was.

**Why fake pods at all?**
Because the only part that needs a GPU is the GPU. The parsing, scoring, annotation writing, the ReplicaSet's behaviour, the load generator, the report and the dashboard all run against a 180-line fake in seconds, so bugs are found on a laptop and the rented hours are spent measuring.

**Does monitoring slow the benchmark down?**
No. Prometheus does one small HTTP GET per pod every 15 seconds, the sampler one every 2 seconds; rendering the page is text formatting in the server's front end. Everything runs in both arms and cancels. The only mechanism that could matter is host CPU on a small machine; `kubectl top pods` answers that.

**Why both inference-perf and your own load generator?**
inference-perf is the standard tool and belongs on the resume; its config schema has moved between releases and could not be verified in advance. The built-in generator implements the same protocol so a mismatch costs minutes, not the day.

**What does the resume bullet say at the end?**
Built a Kubernetes controller that scores each vLLM replica's KV-cache value from its prefix-cache metrics and sets `pod-deletion-cost` so scale-in removes the coldest replica; on Llama 3 8B across 3 A100 replicas, cut post-scale-in TTFT p90 from X ms to Y ms and raised the surviving replicas' prefix-cache hit rate from A % to B % over N trials; verified the coldest replica was removed in N/N scale-downs. The letters are filled from `results/RESULTS.md`.
