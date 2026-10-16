# HillClimbAnything

**The harness to get you to hill-climb any technical challenge.**

Point your coding agent at a number you want to move: latency, render time, build time, frame
time, an eval score. HCA tells you how noisy that number is, keeps only the changes that beat the
noise, and confirms the total gain on data the agent never tuned against.

![Python-Markdown render time across an HCA climb](https://raw.githubusercontent.com/JaiS1/HillClimbAnything/main/docs/assets/climb-light.svg)

```bash
uvx hillclimbanything quick -- python bench.py   # try it with no install: how noisy is this number?
uv tool install hillclimbanything                # or: pipx install hillclimbanything
hca setup                                        # add the agent skill to Claude Code, Codex and others
```

- **Showcase:** a coding agent refereed by HCA cut Python-Markdown's render time by **53.8%**
  (confirmed on held-out documents, output byte-identical).
  [Read the write-up](https://github.com/JaiS1/HillClimbAnything/blob/main/showcases/python-markdown/README.md).
  A second climb cut js-yaml's `load()` time by **15.3%** (held-out, results identical;
  [write-up](https://github.com/JaiS1/HillClimbAnything/blob/main/showcases/js-yaml/README.md)).
- **Why a referee:** on an ordinary desktop, keeping the raw best score kept 17–55% of changes that
  did nothing. HCA's exact signed-rank test on randomised blocks keeps at most 1%.
- **Python 3.11+, zero dependencies.** It never calls an LLM. Tested on Linux and WSL.

Docs, method and limits: <https://github.com/JaiS1/HillClimbAnything>
