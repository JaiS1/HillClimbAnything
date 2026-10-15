# HillClimbAnything

### The harness to get you to hill-climb any technical challenge.

Point your coding agent at a number you want to move: latency, frame time, build time, cost, an
eval score. **HCA tells you how noisy that number is, keeps only the changes that beat the noise,
and confirms gains with a check whose numbers the agent is not shown.**

Python 3.11+ · no dependencies · Linux / WSL / macOS · works with any coding agent

```bash
pip install git+https://github.com/JaiS1/HillClimbAnything   # not on PyPI yet
hca quick -- python3 bench.py
```

```
Measured: the command's own wall time; 20 A/A blocks in 3 s.
Noise: ±4.0% (robust SD of A/A block log ratios, 20 blocks).
Detectable at 80% power: 4.5% at 10 blocks (~1 s per try), 3.1% at 20 blocks (~3 s).
Your useful effect 5%: OK (10 blocks are enough).
```

## Why a referee?

Autoresearch-style loops edit, measure, and keep whatever scored best. On a noisy metric, "best"
is often luck. We measured this on an ordinary desktop
([spikes/phase0](spikes/phase0/RESULT.md)): two identical builds, compared the way most loops
compare them.

| Keep rule | Do-nothing changes kept (CLI benchmark) | Real 5% speedups kept (15 blocks) |
|---|---|---|
| Raw best score | **~50%** | 100% |
| HCA (exact signed-rank test on randomised blocks) | **0.9%** | 95% |

The raw row comes from 20 live tries per condition. The HCA row is **one look at α = 1% on 15 blocks**,
estimated from 2,000 tries resampled from the same recorded blocks. That is not the rule HCA ships: the
shipped rule is **two looks** (10 blocks, extended to 20 when promising, 0.5% per look) plus a keep floor of
half your useful effect, which is more conservative (the Phase 1 review simulated it and saw 0.0% to 0.2% of do-nothing tries kept
under three noise models, against a nominal 1%; and `tests/test_calibration.py` runs 100 do-nothing tries
through the real `hca try` and requires at most 3 keeps). The effects in the table were made by hand, and this
is one machine on one evening.

A loop that keeps half of its no-op changes is climbing noise. Gains that look stacked are
partly fake, and they evaporate the moment you re-measure.

## How it works

```
hca quick -- <cmd>      # 2 minutes: how noisy is this, what win could we detect?
hca init                # branch hca/<task>, freeze the evaluator, measure the baseline noise
hca try -m "why"        # agent edited files → measure vs last keep → keep (commit) or revert
hca confirm             # cumulative gain vs the original, on held-out data; only this counts
hca report              # every attempt, verdicts, validity rate, mechanism metrics, slices
```

- **Randomised blocks.** Every try runs baseline and candidate back to back, in random order,
  10–20 times. Random order makes an exact sign-flip test valid whatever the noise looks like:
  heavy tails, thermal blips, a game running in the background.
- **A keep needs evidence and size.** The one-sided test must pass at 1% per try, and the estimate
  must be at least half your declared useful effect. Microscopic "wins" are not kept.
- **Confirm or it didn't happen.** After a few keeps, `hca confirm` re-measures the total change
  against the original baseline, on held-out items when you have them. It verifies the committed
  history first (only editable paths changed, only HCA's own commits, frozen files unchanged). The
  agent is shown PASS or FAIL, and the numbers only on PASS. If confirm fails, HCA rolls back to the
  last confirmed state.
- **Both arms are the same kind of tree.** The baseline and the candidate each run in a detached git
  worktree under `.hca/arms/`, never in your checkout. The candidate is a commit built from the last
  keep plus your editable files, so a keep commits exactly what was measured, and an ignored cache or
  a staged file in your checkout cannot help one side. Ignored data the benchmark needs goes in `link`,
  which gives both arms the same symlink.
- **It makes cheating inconvenient, not impossible.** A tracked edit outside the declared `editable`
  paths, or any change to a frozen file (ignored and linked files included), invalidates the try before
  anything is measured. Untracked files outside `editable` are left out of the candidate with a warning.
  A crashed candidate scores as the worst value seen, a second `METRIC` line for the objective makes the
  run invalid, and an identical edit tried twice needs `--again` and counts as a try. The referee store in
  `~/.hca/<task>/` holds the original commit and the frozen hashes, so rewriting `.hca/state.json` does not
  move them. None of this is a security boundary against an agent that is determined to cheat. Read the
  patches.
- **Everything is on disk.** Each attempt's patch, raw runs, verdict and reasons live under
  `.hca/`.

A task is three things: a brief, the paths the agent may edit, and one command that prints the
number.

```toml
# hca.toml
[task]
name = "api-latency"
editable = ["src/**"]
frozen = ["bench/**"]

[objective]
command = ["python3", "bench/run.py"]   # prints: METRIC p95_ms=212.4
metric = "p95_ms"
direction = "min"
useful_effect = "5%"
```

## Use it from your agent

The [skill](skills/hca/SKILL.md) teaches a coding agent the loop: the premise checks before
climbing, one mechanism per try, never gaming the metric, and honest reporting. Copy `skills/hca`
into your agent's skills directory, or use a skills installer:

```bash
npx skills add JaiS1/HillClimbAnything --skill hca
```

For expensive runs (paid GPUs, remote jobs), the skill's references cover the discipline that
matters there: a preregistered campaign card with a matched "same compute, no idea" control, a
rehearsal before the paid run, adversarial review, watchers and budget meters that run *inside*
the remote job, and a deep evaluation before any verdict. These come from a real months-long
model fine-tuning campaign and the mistakes it paid for.

## What it is not

- **It is not a guarantee.** The test controls false keeps per try. `confirm` exists because many
  tries still add up (`hca report` prints the false keeps that chance alone allows so far). Reward hacking is
  reduced, not eliminated: read the patches. Held-out data lives outside the repo, but its path is given to the
  benchmark during `confirm`, so it is not hidden from code you run.
- **It is not built for concurrent use.** Do not edit, commit or switch branches in the checkout while a
  loop is running, and do not run two `hca` commands at once (a lock refuses the second). v1 refuses repos
  with submodules or a half-finished merge or rebase.
- **It is not a benchmark harness.** You bring the command that measures. HCA decides what the
  measurement means.
- **It is not orchestration.** HCA doesn't call model APIs. Your agent drives; HCA referees.
- **It has no results of its own yet.** The numbers above are a calibration of the referee, not
  a claim that HCA has made anything faster. Showcases with reproducible numbers come next.

## Status

Early research release (0.2.0.dev). Phase 0 calibration and the core loop are under active
development on the `hca/design` branch. See the [design](docs/DESIGN-hca.md) and the
[architecture contract](docs/ARCHITECTURE-v1.md).

HCA grew out of [Dream Loop](https://github.com/JaiS1/dream-loop/tree/dream-loop-v0.1), whose
paired statistics it keeps. It also borrows ideas from
[autoresearch](https://github.com/karpathy/autoresearch)-style loops and the
[Harness Learning](https://arxiv.org/abs/2609.35738) paper's evaluation hygiene.

## Development

```bash
python3 -m unittest discover -s tests
```

MIT License.
