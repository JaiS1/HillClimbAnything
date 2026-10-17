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
   [spec reference](references/spec.md)). Have it write the result to the file named by
   `HCA_RESULT_FILE` where it can, and print the metric exactly once otherwise. If there is no such
   command, writing one is the first task.
2. Before that, if the question is only "is B really faster/better than A?" (two commands, two
   builds, two settings), run `hca compare -- <A> -- <B>`. It needs no git and no `hca.toml`, and it
   reports the difference with an interval and p-value, the noise it saw, and the smallest difference it
   could have detected. Exit 0 means B is better than A by a significant amount, 1 means not, 2 means a run
   failed or lacked the metric, 3 usage. Commands run without a shell (`sh -c '...'` if needed); the default
   metric is wall time, or pass `--metric NAME` for a `METRIC NAME=value` line. `--min-effect 5%` adds the
   keep rule `hca try` uses; `--json` gives `better`/`worse`/`estimate_pct`/`ci_pct`/`p`, `--seed` and `--out DIR` keep it reproducible.
   It usually runs 20 blocks (10 only if the first look is decisive), so budget for that. On native Windows,
   built-ins and `.cmd` shims (`npm`, `dir`) need `cmd /c ...`; command A may not contain `--` (use `--sep ::`). "No detectable difference" is only as strong as the detectable size it prints.
   Then run `hca quick -- <command>`. It reports the noise and the smallest win it can detect in about
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

   Then run `hca init`. If you already committed the harness (`hca.toml`, the benchmark) on a
   `hca/<task>` branch, stay on it: init adopts that branch as it is and prints one line saying so.
   An existing `hca/<task>` branch that is not checked out is refused; check it out, or delete it if stale.
   If the referee store already holds confirms for the task name, start a new task with `hca init --name <task>-2`.

## 2. The climb (Tier 0: local, cheap, reversible)

Loop:

1. **Read the latest report** (`hca report`): what was kept and reverted, the mechanism metrics,
   and the slices.
2. **Propose one change with a stated mechanism.** Write it as "X should cut Y because Z". Keep
   changes small enough that a verdict says something about the idea.
3. **Edit only `editable` paths**, then run `hca try -m "<the change and its mechanism>"`.
4. **Accept the verdict.** `keep` means the change beat the noise and is committed. `revert`
   means it is gone; the attempt record keeps the patch. `void` and `interrupted` mean nothing was
   decided and your edits are still in place: fix what the message says and try again. An edit identical
   to an earlier attempt is refused unless you pass `--again`, and every try counts, so do not re-roll a
   verdict. A revert whose interval still includes the
   useful effect is **no evidence, not "no effect"**. Note it, and retry it later in a stronger
   form if it was promising.
5. Run `hca confirm` after every ~3 keeps and at the end. **Only confirmed gains count.** It prints
   PASS (with the confirmed estimate) or FAIL (with no numbers). If confirm fails, the referee rolls
   back to the last confirmed state. Ask what was overfit.

Rules:

- **Gaming the metric is failure.** That includes special-casing benchmark inputs, skipping work
  the benchmark doesn't see, caching across runs, weakening a guard, or touching frozen files. If
  the best path forward needs a frozen file changed, stop and ask the user.
- **Explore broadly.** Try genuinely different mechanisms, not ten variants of one. Don't drop an
  idea after one weak test.
- **Never look at held-out data** or `~/.hca/`, never run `hca report --referee` (it is for the human),
  and never ask for per-item confirm detail. The held-out set must stay held out.
- **Keep one shell and one `HCA_HOME`.** A different `HCA_HOME` is refused (the replay memory and held-out set live there).
- **Never run `hca init --reset`, edit `.hca/`, or touch the arm directories under `.hca/arms/`.** The
  referee keeps its own record, so they only get you refused. Do not commit on the `hca/` branch.
- **Report honestly.** Quote confirmed deltas with their intervals and the number of tries. Never
  report a raw best score.

### Explore mode (opt-in, experimental, `blocks` mode only)

Use it only when the user asks for it; line mode stays the default. Details: [engine reference](references/engine.md).

- Keep your hypotheses in `IDEAS.md` at the repo root: `## I<n> [<kind>] <title>`, then optional `parent: NNNN`,
  `where:` and `why:` lines. HCA reads it and never edits, measures or reverts it.
- Start every child with `hca next`: it picks the parent, sets the editable paths to it and prints the command to run,
  with the lineage, the archive, the promotion hint and the backlog. Override only with a reason:
  `hca next --parent M --why "..."`. Never `git stash` editable files in explore mode; screen them or drop them as told.
- Edit, then `hca try --explore --idea I<n> -m "..."`. A screen measures the child against its parent in 5–8 blocks,
  archives it, and moves the editable paths to the next sampled parent; it exits 5. **A screen is not evidence and never keeps.**
- To test a member, `hca try --promote M -m "..."`: a normal referee try of M's whole tree against the last keep, on
  fresh blocks. It needs `--again` if that tree was already tried. Use `--tainted` only if the user agrees to retest a
  member that a failed confirm tainted.
- Confirm as usual. Report dropped keeps and how many screens were run; never quote a screen estimate as a result.
- Between sessions, `hca report --dream` writes `.hca/DREAM.md` (no benchmark runs). Read it and write 5–10 new ideas.

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
