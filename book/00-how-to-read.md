## How to read this book

This book explains one project from nothing: a small program that makes a cluster of language-model servers smarter about which server to switch off when traffic drops. It assumes you have never used Kubernetes, never run a language model, and have not seen the code. If you can read Python and have used a terminal, you have everything you need.

The book has seven parts. Read them in order the first time; each part uses words defined in the part before it.

| part | what it gives you | pages |
|---|---|---|
| The problem in one page | the whole idea, before any background | 1 |
| I. Background | how language models generate text, how they are served, what Kubernetes is, how requests are routed | 14 |
| II. The idea | the controller: what it reads, how it scores, what it writes | 6 |
| III. Building it in six stages | the code, from a laptop prototype to a packaged cluster component | 10 |
| IV. Measuring honestly | how the benchmark is designed so its numbers mean something | 8 |
| V. Observability | metrics, logs and dashboards, and why they do not disturb the benchmark | 5 |
| VI. Running it anywhere | the hands-free cloud runner, CI, the GPU-day runbook | 5 |
| VII. The N-body side project, limitations, glossary, references, appendix | | 8 |

Conventions used throughout:

- A **term in bold** is defined where it first appears and again in the glossary.
- Code and commands look `like this`. Every command in the book exists in the repository and can be run as written.
- Diagrams are drawn with boxes and arrows; read them left to right or top to bottom. The sentence under each diagram says what to notice.
- Numbers given as examples are marked "illustrative" when they are typical values rather than measurements. The real measurements are produced by the benchmark in Part IV and land in `results/RESULTS.md`.
- Each chapter ends with a short "What to remember" list. If you only read those lists, you will still be able to follow the code.

The repository this book describes is `colmeabhi/cache-aware-scale-in`. File names in the text are paths inside it.
