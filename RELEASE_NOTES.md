# Release notes

## 0.1.0 (first public release, research prototype)

Dream Loop is a Dream-RSI-inspired research harness. It runs a serial loop:
live search with a frozen exploration policy, recorded worlds, a dream phase that
revises executable policy code against prefix-only replays, and a next live cycle
that deploys the selected code.

Included:

- `init`, `run`, `ask`, `evaluate`, `abandon`, `status`, `verify`, `report`:
  a serial, budgeted live search with typed results, hashes and command logs.
- `evolve` and `cycle`: executable policy-code revision with replay feedback,
  incumbent retention and adoption in the next live run. `dream` keeps the
  original finite policy grid as a baseline.
- `compare` and `register` (new): paired, studentized cluster-bootstrap,
  Bonferroni-corrected promotion with harm and subgroup guardrails; runtime tags
  and a same-session control; a frozen registration with a premise check and a
  power warning.
- `evolve --promotion paired` and `--confirm-runs` (new): noise-aware revision
  promotion and a held-out confirmation of the final selection.
- A CPU demo that needs no model key, a knapsack example, a scripted policy
  developer, an agent skill and 43 unit tests.

Limitations: one serial worker; trusted local subprocesses, not a security
sandbox; replay fit is in-sample and does not prove fresh-run gains; the demo's
developer is a deterministic script, not an LLM; no provider wrapper has been
tested. The statistical checks measure sampling noise, not evaluator bias or
training-set contamination. See [design notes](tools/dream_rsi/DESIGN_NOTES.md).

Maintenance is best-effort; see [contributing](CONTRIBUTING.md).
