# Research lineage

Dream Loop is an independent, Dream-RSI-inspired prototype, not an official
implementation or an affiliation with the paper's authors, Anthropic, OpenAI,
or any agent provider. No upstream implementation code was copied.

- [Dream-RSI, arXiv:2609.14858v1](https://arxiv.org/html/2609.14858v1): motivates
  replaying recorded discovery histories to improve exploration-policy code.
  Our implementation targets the serial case with an explicit per-attempt
  penalty. The finite policy family remains a comparison baseline. We do not
  reproduce the paper's parallel objective, appendix attainment-curve objective,
  or reported benchmark results. Our branch-local discovery context and strict
  unsupported-replay handling are deliberate differences.
- [Authors' repository](https://github.com/zhengkid/Dream-RSI): at our September 21
  inspection, the full codebase and reproduction scripts were marked as being
  prepared. This project should not be presented as a port of unreleased code.
- [Anthropic's Riemann-zeta work](https://www.anthropic.com/research/riemann-zeta):
  describes proposing many approaches, numerical checks, adversarial review,
  literature checks and subsequent formal/human verification. It improved a
  related bound, not a solution of the Riemann hypothesis. The transferable
  principle is search coupled to independent verification; its compute scale is
  not our laptop budget.
- [OpenAI's ten mathematical advances](https://openai.com/index/ten-advances-in-mathematics/):
  describes model-generated arguments, human manuscript preparation and Lean
  certificates. This source does not establish that a generic public harness
  alone reproduces those discoveries.

Neither numerical experiments nor a scalar model score replace a mathematical
proof. For ML, a correct evaluator still does not remove sample uncertainty,
training exposure, adaptive selection or the need for independent validation.

Replay-compatible runs need explicit parent artifacts and decision-time context;
an informal historical experiment log is not sufficient replay evidence.
