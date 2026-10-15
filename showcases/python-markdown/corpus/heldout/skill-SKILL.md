---
name: hca
description: HillClimbAnything — hill-climb any measurable technical target (latency, frame time, build time, cost, accuracy, an eval score) with a noise-aware referee. Use when the user wants to make a number better through repeated experiments, asks whether a speedup or improvement is real or noise, or wants autoresearch-style iteration on their own project.
license: MIT
---

# HillClimbAnything

You propose changes. The `hca` CLI is the referee: it measures, runs the statistics, keeps or
reverts each change, and confirms gains on data you don't tune against. Never argue with the
referee or work around it. A win it can't confirm is not a win.

## 1. Before any change: is this number climbable?

1. Find or write the **one command that prints the number**. It either prints
   `METRIC <name>=<value>` lines, or prints item JSON as its last line (see
   [spec reference](references/spec.md)). If there is no such command, writing one is the first
   task.
2. Run `hca quick -- <command>`. It reports the noise and the smallest win it can detect in about
   2 minutes. If it says **too noisy**, fix the measurement first (more items, longer runs, fewer
   background jobs). Do not start climbing.
3. **Premise check** (ask the user or check yourself; write the answers into `BRIEF.md`):
   - What produced the current system, and what is the cheapest strong baseline? That means a
     zero-effort version, a known public best, or an obvious config change. Measure it before
     anything clever. The cheapest baseline often wins.
   - Can the intended kind of change move this metric at all, and how much headroom is there?
   - Is the evaluator itself right? Audit it on the cases that fail: a mislabelled target or a
     broken scorer wastes every experiment downstream.
   - What has already been tried? Mark each prior idea `negative` only if it actually tested the
     idea. Otherwise mark it `untested`.
4. Write `hca.toml` and `BRIEF.md` with the user. Declare:
   - `editable` paths (yours to change) and `frozen` paths (evaluator, data, benchmarks);
   - the useful effect: the smallest win worth keeping;
   - guards: tests, output equality, anything that must not regress.

   Then run `hca init`.

## 2. The climb (Tier 0: local, cheap, reversible)

Loop:

1. **Read the latest report** (`hca report`): what was kept and reverted, the mechanism metrics,
   and the slices.
2. **Propose one change with a stated mechanism.** Write it as "X should cut Y because Z". Keep
   changes small enough that a verdict says something about the idea.
3. **Edit only `editable` paths**, then run `hca try -m "<the change and its mechanism>"`.
4. **Accept the verdict.** `keep` means the change beat the noise and is committed. `revert`
   means it is gone; the attempt record keeps the patch. A revert whose interval still includes the
   useful effect is **no evidence, not "no effect"**. Note it, and retry it later in a stronger
   form if it was promising.
5. Run `hca confirm` after every ~3 keeps and at the end. **Only confirmed gains count.** If
   confirm fails, the referee rolls back to the last confirmed state. Ask what was overfit.

Rules:

- **Gaming the metric is failure.** That includes special-casing benchmark inputs, skipping work
  the benchmark doesn't see, caching across runs, weakening a guard, or touching frozen files. If
  the best path forward needs a frozen file changed, stop and ask the user.
- **Explore broadly.** Try genuinely different mechanisms, not ten variants of one. Don't drop an
  idea after one weak test.
- **Never look at held-out data** or `~/.hca/` and never ask for per-item confirm detail. The
  held-out set must stay held out.
- **Report honestly.** Quote confirmed deltas with their intervals and the number of tries. Never
  report a raw best score.

## 3. Expensive runs (Tier 2: paid GPU, remote jobs, scarce verifiers)

When one run costs real money or hours, switch to campaign discipline and read the references
before launching anything:

- [Campaign card](references/campaign-card.md): the question; arms, including a **matched "same
  compute, no idea" control**; equal steps and data order; a preregistered decision; power and the
  detectable effect per cohort; budget at the HIGH estimate plus one lost run; and an amendments
  log.
- [Launch checklist](references/launch-checklist.md):
  - a cheap rehearsal first, run **sequentially** and never alongside the paid run;
  - preflight (the import closure lists every missing module; sha checks across manifest, sidecar
    and baked commit; a clean tree);
  - watchers and a heartbeat that run **inside the remote job**;
  - auto-release, kill only your own processes, and a "human clicks" table.
- [Review](references/review.md): one adversarial review by a different model or agent before
  launch, with MUST / SHOULD-FIX / NIT findings. At most one re-review; NITs never block.
- [Deep evaluation](references/deep-eval.md): after every run, read slices, cohorts, the result
  vs the control, mechanism metrics and failure examples, then debate the result with a critic
  before writing the verdict.

## 4. Working with other agents

- Give each reviewer or labeller **at most ~7 heavy items** (images, long traces). Require a
  field the referee can check (for example, transcribe a visible value), because overloaded
  agents fabricate.
- Read their outputs critically. Spot-check one load-bearing claim against the raw data before
  relaying anything.
- Kill only processes you started (by PID or process group), never by name pattern.

## 5. What to tell the user

Lead with one current verdict: the confirmed delta ± interval vs the original baseline, the
number of tries, the validity rate, and what was learned. Then give the evidence paths
(`.hca/attempts/`, `hca report`). Separate what was confirmed from what was only kept, and kept
from merely promising. Never claim more than the referee measured.
