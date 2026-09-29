---
name: dream-loop
description: Configure and run Dream Loop, a Dream-RSI-inspired research harness for exploring mathematical constructions, scientific algorithms and engineering optimizations against a fixed evaluator. Use for budgeted autoresearch, measurable project hill climbing, and replay-based exploration-policy evolution.
license: MIT
---

# Dream Loop

Turn the user's project, evaluator and budget into an inspectable discovery loop.
Support research aimed at new results in mathematics, science and engineering
when candidates have a reliable evaluator. Numerical optimization does not
establish a mathematical proof or a scientific breakthrough by itself.
The discovery agent changes project candidates; a separate policy-development
agent revises how exploration is allocated. Keep those roles distinct.

Read [usage and contracts](references/usage.md) for commands and protocol details.
Use an existing installation or a user-provided checkout. This skill does not
bundle the harness or install a provider integration. Do not guess a package or
repository URL when the harness is unavailable.

## Choose the smallest useful run

- For a demonstration, run the bundled CPU evolution demo. It needs no model
  credentials and uses a scripted developer; describe it as a mechanism test.
- For a project, identify the editable seed, fixed evaluator, metric identity,
  direction, and a finite attempt/time budget from the user's request. Resolve
  missing essentials before launching dependent work. Do not invent a score.
- Preserve the user's existing authorization and compute limits. A local test
  does not authorize provider spending, remote jobs, publishing or submissions.

Use a deliberately small seed workspace. Declare evaluator and generator source
dependencies for hashing. Keep data/split, evaluator, metric and agent/model
configuration fixed across policy comparisons. Do not put credentials or datasets
in copied candidate workspaces.

## Execute the right loop

The `cycle` command requires two trusted adapters: the project config's generator
and a policy-development command. Confirm both contracts with a bounded smoke
before unattended execution. A skill installation alone does not connect Claude,
Codex or Devin to the harness.

If no automated generator exists, use `init`, `ask` and `evaluate`. Give each
candidate agent only the emitted packet and its workspace. An existing chat with
sibling outcomes is not clean branch-local generation context. Only one attempt
can be pending in each run.

Use `evolve` for executable policy-code revision and `cycle` for automatic
rollout → evolve → adopt. `dream` searches a fixed policy grid and is only a
baseline; do not present it as code self-improvement. Policies stay frozen within
a live rollout, then may change for the next rollout.

## Preserve evidence and stopping conditions

- Never edit the evaluator, historical results or hashes to make a candidate pass.
- Invalid/blocked outcomes have null scores. An unsupported replay continuation
  has no measured outcome; do not invent one.
- Keep source versions, development feedback, raw results and timing records.
- Promote a candidate only through a paired, corrected comparison (`compare`,
  or `evolve --promotion paired`); never from the best raw mean of many tries.
- On interruption, inspect the recorded command/owner before recovery. Do not
  remove a lock, abandon an active attempt, or silently rerun an evaluation.
- Stop at the declared budget. A new run is a new budget decision, not a way to
  bypass the existing limit.

Report the best measured artifact, attempts and costs, selected policy, failures,
and the paths to evidence. Separate replay fit from fresh-run performance and
scientific validation. Do not claim a leaderboard result or autonomous research
gain from the toy demonstration. No external posting or starring is part of this
skill's workflow.
