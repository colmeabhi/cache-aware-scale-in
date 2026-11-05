# Cache-Aware Scale-In: The Book

A complete, from-zero explanation of the project: what problem it solves, the background (language-model serving, vLLM, Kubernetes, routing), the controller, the six-stage build, the benchmark design, the observability stack, the hands-free cloud runner, the N-body side project, limitations, a glossary and references.

It assumes no prior knowledge of Kubernetes, llm-d or vLLM. Diagrams are Mermaid blocks and render on GitHub.

Read it as one file: [BOOK.md](BOOK.md). Or chapter by chapter:

| # | chapter | file |
|---|---|---|
| 0 | How to read this book | [00-how-to-read.md](00-how-to-read.md) |
| 1 | The problem in one page | [01-problem.md](01-problem.md) |
| 2 | Part I · Ch. 1: How a language model produces text | [02-background-llm.md](02-background-llm.md) |
| 3 | Part I · Ch. 2: Serving a model with vLLM | [03-background-vllm.md](03-background-vllm.md) |
| 4 | Part I · Ch. 3: Containers and Kubernetes from zero | [04-background-kubernetes.md](04-background-kubernetes.md) |
| 5 | Part I · Ch. 4: Routing requests to replicas | [05-background-routing.md](05-background-routing.md) |
| 6 | Part II · The idea | [06-the-idea.md](06-the-idea.md) |
| 7 | Part III · Building it in six stages | [07-six-stages.md](07-six-stages.md) |
| 7b | Part III · Ch. 2: A guided reading of the code | [07b-code-walkthrough.md](07b-code-walkthrough.md) |
| 8 | Part IV · Measuring honestly | [08-measuring.md](08-measuring.md) |
| 9 | Part V · Observability | [09-observability.md](09-observability.md) |
| 10 | Part VI · Running it anywhere | [10-running-anywhere.md](10-running-anywhere.md) |
| 10b | Part VI · Ch. 2: The GPU day, as a story | [10b-the-day.md](10b-the-day.md) |
| 11 | Part VII · The N-body side project | [11-nbody.md](11-nbody.md) |
| 12 | Limitations, caveats and what comes next | [12-limitations.md](12-limitations.md) |
| 12b | Questions people ask | [12b-faq.md](12b-faq.md) |
| 13 | Glossary | [13-glossary.md](13-glossary.md) |
| 14 | References and further reading | [14-references.md](14-references.md) |
| 15 | Appendix: cheat sheet | [15-appendix.md](15-appendix.md) |

The same text is also published as a Claude document for reading and commenting.
