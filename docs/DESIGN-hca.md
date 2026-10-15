# HillClimbAnything (HCA): design v0.2

Status: DESIGN AGREED 2026-10-14. Jai chose the recommended option on every big choice: rename in
place (to be confirmed before pushing), command `hca`, cut dream replay to a tag, first build = Phase 0 + Phase 1 + a
real recorded climb. Revised after an adversarial review (see the appendix).
Branch `hca/design`, 2026-10-14.

> **HillClimbAnything: the harness to get you to hill-climb any technical challenge.**
> Point your coding agent at a number you want to move, such as latency, frame time, cost,
> accuracy or an eval score. HCA first tells you how noisy that number is and what size of win you
> can actually detect. It then lets the agent climb, keeping only changes that beat the noise and
> confirming them on data the agent never saw.

HCA is a learning and research tool. It makes no claim of results until the showcases in §12 exist
and can be reproduced.

**Works best when** a command run on a local or ssh-reachable checkout prints a number, and the
command is repeatable enough to estimate its noise in a few minutes. Expensive remote runs (paid
GPUs, notebooks) are supported through campaign mode (§7). Campaign mode adds discipline and a guard
script that runs next to the job; it does not provide remote orchestration.

---

## 1. Inputs (summary; evidence is kept privately)

### 1.1 Dream Loop (this repo)

**Keep:**

- The statistics in `stats.py`: a paired bootstrap-t with cluster resampling, Bonferroni correction,
  and harm and subgroup guards.
- Registration and freeze.
- Runtime tags and same-session controls.
- Hashing, command supervision, and the honest-claims tone.

**Drop from the main path:**

- **Replay-based policy evolution.** It needs many replay-compatible runs, which real campaigns don't
  produce, and it was never tested with an LLM.
- **The serial one-candidate tree.** Real work runs batches of arms with controls.
- **The heavy onboarding:** two adapter contracts, five commands before anything is visible, and a
  400-line README.

### 1.2 A real model fine-tuning campaign on a public leaderboard

We audited 35 lessons against that project's repo: 32 confirmed, 3 partly. They are listed here
without project specifics. Where HCA can only offer a template or a checklist rather than code, the
right-hand column says so.

| Lesson | HCA response |
|---|---|
| A public checkpoint beat weeks of in-house training, and a zero-parameter rule matched a learned component. | `hca quick` / baseline report declared baselines. The question "can this change move the scored quantity at all?" is a **skill checklist** (human/agent judgment, not code). |
| Effects smaller than the instrument's CI were chased across dozens of arms. | **The noise floor and detectable effect come first** (`hca quick`). Runs below power are labelled exploratory. |
| Noise margins taken from runs with *different* treatments overstated noise. | **Calibrate within the session** (A/A) or with a same-recipe seed replicate. |
| A "no effect" headline hid a narrow slice effect. A null was read as "fails". | **Slices and mechanism metrics** appear in every report. Reports print "no evidence ≠ no effect" unless the interval excludes the useful effect. |
| Most of one gain was under-training, shown only by a "same steps, no new idea" control. | Campaign cards require a **matched no-treatment arm**. Verdicts are read against it, never against the incumbent alone. |
| A data-order change silently moved shared samples to other steps. | **Matched comparisons**: equal steps and matched order (a card checklist item), and for benchmarks, randomised block order. |
| "Failed" prior attempts mostly never tested the idea. | Hypothesis status `untested` is separate from `negative`, with a prior-attempts audit (skill step). |
| A labelling rule meant one important behaviour was never labelled. | **Audit the targets and the evaluator on the failing slice** before changing the system (skill step). |
| Reviews caught real errors, but review overhead grew until most findings were hygiene. | **One review per card, at most one re-review, NITs never block.** Keep/kill trigger: >2 review rounds or 24 h with no efficacy number. |
| An external critic with full context changed a conclusion. | **A debate step before any campaign verdict** (template). |
| Agents given dozens of images fabricated judgments. | **Per-agent load caps** (≤ ~7 heavy items) plus a machine-checkable transcription field (skill rule). |
| A rehearsal run in parallel with the paid run caught nothing in time. | **A rehearsal receipt keyed on the artifact sha.** The paid run's guard refuses to start without it, so rehearsal is sequential by construction. |
| Simulations that faked a real library call hid a launch-killing bug. | **Real-call tests** are a required card field. `hca card check` refuses a card with no real-call test log, a weak but mechanical check. |
| A bad run would have burned hours before anyone looked. | **Watchers inside the remote guard** kill at the first bad signal. |
| Hard stops must fail closed. The budget meter read 22% low. Idle runtimes kept billing. | **The remote guard** has a fail-closed budget meter (HIGH estimate, a manually entered provider rate, a one-lost-VM allowance, a ledger that includes aborted sessions) and an **idle self-release timer that runs in the runtime itself**. |
| A broad `pkill` can kill another worker's job. | **Process groups first**, with an env job marker as a secondary check. Kill only what HCA started. |
| A clean env lacked a module the reference env had. Leaked parent env vars broke children. | **An import-closure check** that lists every missing module, and **an env denylist** for known-poisonous prefixes, with a leaked-vars diff in `doctor`. |
| A missing checksum sidecar, and error text truncated to 100 chars, hid the cause. | **One function writes and verifies sidecars**, and **errors are never truncated** in durable logs. |
| Code, upload and notebook drifted apart. | **The commit is baked into the artifact**, and manifest = sidecar = baked sha is checked three ways. Paid runs refuse a dirty tree. |
| A stale "running" manifest was taken as liveness. 12 h of wall time held <2 min of compute. | **The heartbeat is written by the job.** <3 min old means alive, >5 min means gone. Waits are event-driven, plus the keep/kill trigger. |
| Superseded headlines outlived their retractions. Amendments need discipline. | **`VERDICTS.md` holds one current verdict per question.** Card **amendments** are dated, pre-result only, and keep the original struck through with a reason. |
| A gate compared against stored numbers from another runtime and aborted a good launch. | **The baseline is re-measured in the same session.** Gates stop on gross mismatch only. |
| Human-in-the-loop launch hazards: OAuth prompts, a browser minimised overnight, late token grants. | A **"human clicks" table** in the launch checklist template. |
| Rule files forked between two agents' instruction files. | The skill ships **one source** and `setup` links it into each agent rather than copying it. |

### 1.3 Repos that got traction (verified 2026-10-14)

- **REA (morluto/rea), ~250k stars.** It spiked after a Show HN of its website. What drove it:
  - an "X Anything" name and a desire-shaped hook;
  - one-command `npx … setup` into existing agents, with a `doctor` command;
  - layered packaging (an instructions-only skill plus a CLI that does the work);
  - showcases with checkable numbers.

  It was criticised for an opaque installer and a large context footprint.
- **karpathy/autoresearch, ~98k stars.** A problem is a markdown brief, one editable file and a
  fixed-time metric. Followers (pi-autoresearch, ~8k; an autoresearch Claude Code skill, ~6.6k)
  generalised it to any metric within a week. One real result (a template engine got 53% faster)
  outweighed any feature list.
- **rebase-energy/hillclimb** (PyPI `hillclimb`, 4 stars) has a similar concept: a verifier script,
  headless coding agents, budgets and a `--agent dummy` mode. A keyless demo alone is therefore not a
  differentiator. **HCA's differentiator is the referee that says "this win is noise".**
- **Names.** `hillclimbanything` is free on PyPI, npm and GitHub. `hillclimb` is taken. PyPI `hca`
  is the Human Cell Atlas DSS CLI (v8.0.0), which also installs an `hca` command.

### 1.4 "Harness Learning Enables Generalizable Test-Time Adaptation" (arXiv 2609.35738)

Mostly orthogonal. The paper RL-trains a 4B proposer to rewrite a frozen solver's harness against
cheap, nearly noiseless graders. It has no noise model and no budgets. Its own ablation found no
gain from training on later-round trajectory states, weak evidence against making replay central.

What we take from it:

- a standard **execution report** as the interface between rounds;
- **dev/held-out separation** with a confirmation step;
- **validity rate** tracking;
- **preregistered scoring of invalid candidates** (its headline flips on this rule);
- **oracle@k reported apart from deployable selection**;
- as our self-test, **sequential revisions vs best-of-K at matched compute**.

---

## 2. The product: a noise-aware autoresearch with a referee

The agent drives through the skill. The CLI is the **referee**: it owns measurement, statistics, the
held-out set, the ledger and the verdict, which the agent must not decide on its honour.

**Five verbs cover the core** (Tier 0, local):

| Verb | What it does |
|---|---|
| `hca quick -- <cmd>` | Needs no spec file and runs in about 2 minutes. It runs an A/A in randomised blocks and reports one honest sentence, e.g. "noise ±4.1%; a 6% win is detectable at 10 blocks (~20 s per try), 3% is not". If the noise is too high it suggests the cheapest fix (more items, longer runs, pin clocks, close the game). |
| `hca init` | Writes `hca.toml` + `BRIEF.md` (the skill fills them by asking), creates the `hca/<task>` branch and moves held-out items into the referee store. |
| `hca try` | Diff-scope check → build → measure the candidate vs the last keep in randomised blocks with an in-session A/A → verdict (§3) → keep (commit on the branch) or revert (restore the editable paths to the last keep). It writes an attempt record whether the candidate was valid or not. |
| `hca confirm` | Measures the cumulative change vs the *original* baseline on held-out items with fresh blocks. The agent sees pass/fail and the interval only. The number of confirms is capped and logged. |
| `hca report` / `hca status` | The execution report and the hypothesis board / `VERDICTS.md`. |

Campaign mode (§7) adds `card`, `rehearse` and a remote guard file. `doctor` and `setup` handle
installation.

**Working tree.** HCA works on a dedicated branch `hca/<task>` in the user's own checkout, the way
autoresearch does, so untracked dependencies such as `node_modules`, venvs and weights stay in place.
It requires a clean tree at `init`. Revert restores only the declared editable paths; it never runs
`git clean` outside them, and it never touches remotes. The result is handed back as the branch
itself (`hca report` prints the diff summary and the merge command).

---

## 3. Statistics: what a "win" means

> **Amendment A1 (2026-10-15, before any build, after the Phase 0 spike, `spikes/phase0/RESULT.md`).**
> Was: per-item bootstrap-t on `items`, tested at alpha/50 per try, starting at 3 repeats. Reason:
> on real timings, routes measured in the same block share noise, so treating them as independent
> units rejected at 6-12% when the nominal rate was 2.5%. A sign-flip test on randomised blocks was
> exactly calibrated and caught 96% of 5% wins on cli at 15 blocks, where the old rule caught 65%.

### 3.1 Measurement modes (declared in the spec, never guessed)

| Mode | Unit of the test | Use when | What it can claim |
|---|---|---|---|
| `blocks` (default for anything timed or noisy) | randomised A/B block: both arms measured back to back, order random | latency, frame time, build time, binary size from a noisy build, cost from a noisy run | "the candidate is X% better under these conditions"; per-item slices are descriptive |
| `items` | item id | deterministic per-item scores with no shared measurement noise (eval-set accuracy, solver objective per instance) | "the typical item improved by X" (Dream Loop's paired bootstrap-t), with slice reads |
| `seeds` | seed pair | training or stochastic pipelines | "the recipe improved by X across seeds"; >= 5 seed pairs, otherwise exploratory |

`METRIC name=value` output and item JSON with repeated `runs` both default to `blocks`. Item JSON
with one deterministic score per item defaults to `items`.

### 3.2 Estimand and test

- **`blocks` mode.** Each block gets a score: `log(baseline/candidate)` for one number, or the
  **median over items** of the per-item log ratios, sign-adjusted for direction. The test is a
  **Wilcoxon signed-rank test with an exact sign-flip null**. The randomised order makes it valid by
  construction, with no assumption about the shape of the noise. The estimate is Hodges-Lehmann,
  reported as a % change, with a Walsh-average one-sided bound. `useful_effect = "5%"` means
  `log(1.05)`.
- **`items` mode.** The paired bootstrap-t from Dream Loop, with `mean_diff` or `mean_log_ratio` as
  the estimand.
- **Tail metrics** (p95 latency, p99 frame time) are computed per item, within a block, from that
  item's runs. A pooled tail across items is a mechanism metric.

### 3.3 Noise control

- Arms run **sequentially** inside each block, **in random order**. Concurrent arms are opt-in only,
  because they contend for cores.
- **`quick` measures an A/A** (the same build in both arms) and reports the noise floor and the
  smallest detectable effect at 10 and 20 blocks. If even 20 blocks cannot detect the useful
  effect, it says "too noisy here" and names the cheapest fix, rather than letting tries come back
  inconclusive.
- **Load** is recorded per block where the platform allows it, and outlier blocks are flagged in
  the report. The rank test already limits their influence.

### 3.4 Keep rule, and why many keeps don't inflate wins

- **Per-try test:** one-sided, alpha = 0.01, after 10 blocks. An inconclusive try may extend once to
  20 blocks; the two looks are tested at 0.005 each. At most 1% of do-nothing changes are kept per
  try (measured: 0.1-0.9%).
- **Keep only if** the bound clears 0 *and* the estimate is at least half the useful effect.
  Microscopic wins are not kept. Guards must also hold, each with an A/A-derived tolerance, and a
  test command gets one retry for flakiness.
- **Confirm** runs every N keeps (default 3) and at the end. It is the defence against the false
  keeps that accumulate over many tries. If confirm fails, HCA reverts to the last confirmed commit
  and marks the intervening keeps `unconfirmed`. They stay in the ledger and their ideas stay on
  the board.
- **Only confirmed deltas are reported as results.**

### 3.5 Invalid candidates (preregistered)

Item JSON separates `candidate_invalid` (crash, wrong output, a guard failure) from `infra_invalid`
(a timeout of the harness, a network flake):

- A candidate-invalid item scores as the **worst observed** for that item, or fails the try under
  `invalid = "fail"`.
- An infra-invalid item is **excluded from both arms**, and >10% infra-invalid items voids the try.
- The **validity rate** (share of tries that produce a valid candidate) is tracked per session.

### 3.6 Held-out data

Held-out items live in the **referee store** (`~/.hca/<task>/heldout`, outside the repo and the
brief), copied there at `init`. The agent never gets per-item held-out detail, and confirms are
capped (default 5 per session) so the held-out set can't become a dev set. For a single-number metric, where
there are no items to hold out, confirm uses fresh repeats on a fresh process/seed and a
cooled-down machine.

---

## 4. Task spec: `hca.toml` + `BRIEF.md`

```toml
[task]
name     = "checkout-latency"
brief    = "BRIEF.md"                    # what, why, constraints, ideas, already tried
editable = ["src/**"]                    # diff-checked; anything else invalidates the try
frozen   = ["bench/**", "hca.toml"]      # hashed

[build]                                  # optional: separate from scoring
command  = ["npm", "run", "build"]

[objective]
command       = ["./bench/run.sh"]       # prints METRIC lines or item JSON
metric        = "lcp_ms"
direction     = "min"
mode          = "blocks"                 # blocks | items | seeds  (§3.1)
useful_effect = "5%"
max_tries     = 50

[[guards]]
name = "tests";       command = ["npm", "test"]               # one retry on failure
[[guards]]
name = "same-pixels"; metric = "screenshot_diff"; max = "auto" # A/A tolerance

[mechanism]
metrics = ["bytes_sent", "js_exec_ms", "requests"]
slices  = ["route", "cache"]             # item tags

[env]
mode = "inherit-minus-denylist"          # default; "allowlist" is opt-in (campaign mode)
deny = ["UV_*", "PIP_*", "CONDA_*", "PYTHON*", "MPL*", "VIRTUAL_ENV"]   # applied when the
                                         # child uses a different interpreter; dropped vars are
                                         # printed on the first failure
```

The item JSON contract (`items` is just an id → score map; the label in the spec is cosmetic):

```json
{"metric":"lcp_ms","items":[{"id":"/cart","runs":[212.4,208.9,215.0],"tags":{"route":"cart"},
  "status":"valid","mechanism":{"bytes_sent":48211}}]}
```

---

## 5. Rigour tiers

There are two, chosen by cost; `--tier` can only raise the tier.

| Tier | When | Before | During | After |
|---|---|---|---|---|
| 0 | local, reversible, minutes | `quick` / `init` | wall + stall caps, process group | auto verdict, `confirm` |
| 2 ("campaign") | paid, remote, or scarce verifier | card (arms + matched control + MDE per cohort + real-call test) → one review → rehearsal receipt | remote guard: watchers, budget, heartbeat, self-release | deep report → debate → `VERDICTS.md` |

The old "Tier 1" is dropped. It added a review to cheap reversible runs, which is exactly where the
review overhead piled up last time.

---

## 6. Worked examples

### 6.1 Web-page latency (Tier 0, `blocks`)

- **Metric:** LCP for 12 pages × k adaptive runs, measured with Playwright against a local build and
  fixed CPU throttling. Each block scores the median over pages of the per-page log ratio. The headline is the
  Hodges-Lehmann % change; per-page reads and pooled p75 are mechanism metrics.
- **Guards:** e2e tests, screenshot diff and visible-text equality. These catch the hack of
  deferring the LCP element.
- **Mechanism metrics:** bytes transferred, JS execution time, request count.
- **Held-out:** 4 further pages for confirm. These catch per-page tricks, which is the point. If a
  site has fewer than 12 + 4 meaningful pages, confirm uses fresh blocks on the same pages instead.

### 6.2 Game/UI frame-time jank (Tier 0, `blocks`)

- **Metric:** a deterministic input replay over 8 scene segments. Each block plays every segment once per arm; a
  segment's score is its p99 frame time, and the block score is the median of the segment log
  ratios. The share of
  frames over 16.7 ms is a mechanism metric, alongside GC pauses, draw calls and shader-compile
  hitches.
- **Guards:** visual parity at fixed frames and a game-state checksum at the end of the replay.
- **Noise:** an in-session A/A plus cooldowns. On a shared home PC, `quick` will often say "too
  noisy here", with concrete fixes. That is the referee working.

### 6.3 Model capability by fine-tuning (campaign, `seeds`)

This does not fit `hca.toml` alone. It is a **campaign card** (`hca card new`) whose arms are:

- the idea;
- the idea with one component ablated;
- a matched "same steps, no new idea" control.

Every arm has equal steps and matched data order. Noise comes from a same-recipe seed replicate, and
the card carries an MDE per cohort, flagging cohorts that are undetectable. Mechanism metrics target
what the change should move, because dilution hides effects in the headline. The job runs remotely
with `hca_guard.py` beside it (§7). Afterwards, `hca report` ingests the per-item dumps (slices,
cohorts, vs the control, failure examples), then a debate, then `VERDICTS.md`.

Where it breaks, and is out of scope for v1: metrics that need a deploy to shared infrastructure,
true multi-objective Pareto search (guards are constraints only), LLM-graded metrics with
undisclosed grader drift, and state that persists between tries without a reset command.

---

## 7. Campaign mode (phase 2)

- **`hca card new|check`** creates and checks a template with:
  - the question;
  - a premise checklist;
  - arms plus the matched control;
  - the preregistered decision, power and MDE table;
  - a gates table;
  - budget (HIGH);
  - a real-call test log;
  - risks;
  - an amendments log (dated, pre-result only, original struck through with a reason).

  `check` validates the structure and the required fields, not the science.
- **`hca_guard.py`** is one stdlib file, uploaded with the job and run *inside* the remote runtime.
  It:
  - checks for a rehearsal receipt matching the baked sha;
  - meters the budget against a ledger (fail closed);
  - runs watcher rules on the job's JSON log (a fixed grammar; a missing field means no match; a
    separate stale-log rule);
  - writes a heartbeat to durable storage;
  - runs an idle self-release timer through a user-supplied release command;
  - writes a full-error `SUMMARY.json`;
  - kills only its own process group.

  The local CLI reads the heartbeat and verifies receipts. It never pretends to control a job it
  cannot reach.
- **`hca preflight`** checks the import closure (every missing module at once), the manifest,
  sidecar and baked-sha triple, and a clean tree.
- **Templates in the skill:** the launch checklist (with a "human clicks" table), the review format
  (MUST / SHOULD-FIX / NIT, one re-review cap), the debate prompt, and the deep-eval checklist.

---

## 8. Kept and cut from Dream Loop

**Kept:**

- `stats.py`, kept for `items` mode, plus a new signed-rank test on randomised blocks (A1);
- registration, extended with amendments;
- runtime tags and same-session control;
- hashing and verification;
- command supervision.

**Cut from the main path:**

- `dream` / `evolve` / `cycle` and the replay policy (choice B);
- the knapsack demo;
- the two-adapter contract;
- the long README.

The design-notes backlog mostly becomes §3, §5 and §7.

---

## 9. Big choices (recommendation first)

**A. What HCA is**
1. *(Recommended)* **Skill-led, CLI as referee** (§2).
2. **CLI orchestrator.** HCA drives headless agents itself. It can run unattended and hide held-out
   data structurally, but it needs per-provider adapters. Defer it as `hca auto`.
3. **Skill only.** Methodology without code means an honour system, which is exactly what failed
   before.

**B. Dream-style replay**
1. *(Recommended)* **Cut it from main and keep it under a `dream-loop-v0.1` tag and branch.** The
   idea is preserved and can return if fast-loop sessions ever accumulate replayable attempts.
2. **Keep it in `experimental/`.** That is optionality, but it also means ~1,000 lines of dead code
   in the tree.
3. **Keep it central.** Not supported by any evidence.

**C. Repo and names** *(outward-facing; Jai decides)*
1. *(Recommended)* **Rename JaiS1/dream-loop → HillClimbAnything in place.** It has 0 stars and
   public history; GitHub redirects old links. Nothing private has been pushed (the design branch is
   local and scrubbed).
2. **Start a new repo**, and archive dream-loop with a pointer. This gives a clean first commit and
   history.

Command name:
1. `hca`. It collides only with the old Human Cell Atlas CLI, whose audience barely overlaps.
2. `climb`. Short, but generic.
3. `hillclimbanything` only. Unambiguous, but long.

**D. First build scope**
1. *(Recommended)* **Phase 0, then Phase 1, then a recorded real climb as the demo.** Campaign mode
   follows once a real paid run needs it.
2. **Phases 0–2 together.**
3. **Phase 0 only**, then decide.

---

## 10. Build plan (granular commits; grunt work delegated)

**Phase 0: calibration spike.** This tests the riskiest assumption first. Use two real targets: one
web-page benchmark and one pure-CLI benchmark. Run the existing `stats.py` (adapted for log-ratio)
on 20 A/A and 20 A/B pairs with hand-made 3–5% changes, and report:

- the false-keep rate on A/A;
- the detection rate;
- wall time per try.

It runs nice'd, one job at a time, in under 3 GB of RAM. If the noise on this PC makes Tier 0
useless, we learn that before building anything.

**Phase 1: core.**
1. Create the `hca/` package, port and extend `stats.py` (modes, estimands, alpha spending) with
   synthetic-data tests, and move dream to a tag.
2. Parse and validate `hca.toml`, and add `quick`.
3. Add `init` (branch, referee store), then `try` (scope check, build, randomised blocks, A/A,
   guards, keep/revert), then `confirm`.
4. Add `report` / `status` / `VERDICTS.md`.
5. Add the skill (≤ 150 lines plus references) and `setup` / `doctor`, with skill install via
   `npx skills add` first.
6. **First real climb:** pick a small open-source project with a real slow path, run Claude with
   HCA, and record the transcript and ledger. Ship it as a keyless `hca replay demo`: the real
   attempts go through the real referee in about 90 seconds. A second scenario is a scripted reward
   hack that the guard catches.
7. Rewrite the README: hook, install, `quick`, the replay demo, honest scope.

**Phase 2: campaign mode** (§7), with sim-matrix tests covering a VM loss, a budget stop then
resume, a corrupt upload, a NaN, a stale log, a leaked env var, and a missing sidecar.

**Phase 3:** a review board UI (a generalised human-labelling UI with blind A/B and confidence),
`hca auto`, and more playbooks.

Each phase ends with a Sonnet adversarial review of the diff and a fixes commit.

---

## 11. Risks

- **Tier 0 may be mostly "too noisy" on ordinary machines.** Phase 0 measures this. Mitigations are
  more blocks, longer runs, closing other load. Phase 0: 5% wins are detectable here at 10-20 blocks.
- **Reward hacking.** The diff scope, frozen hashes, guards and the referee-held held-out set reduce
  it; they do not eliminate it. The skill treats gaming the metric as failure.
- **Branch-in-place revert.** It touches the user's tree. It is limited to the editable paths, needs
  a clean start, and never touches remotes.
- **Platforms.** HCA is built for Linux, WSL and macOS. Native Windows process control is unverified,
  and the README says so on the first screen.
- **Python 3.11+** (`tomllib`) raises Dream Loop's 3.10 floor.

---

## 12. How we will know HCA works (before claiming anything)

1. **Three showcases on real targets:** web LCP, frame time, and a model eval. Each reports the
   baseline, the confirmed delta with its interval, the number of tries, and the reproduce command.
2. **Matched-compute self-test:** sequential revisions vs best-of-K independent proposals, with K
   declared in advance.
3. **Noise-gating ablation:** how many raw "best score" wins survive `confirm`, compared with HCA
   keeps.
4. **Validity rate and cost per confirmed gain.**

---

## Appendix: adversarial review resolution (Sonnet, medium effort, 2026-10-14)

Result: 2 blockers, 9 MUST, 9 SHOULD-FIX. The original review is kept privately because it quotes
project specifics.

| # | Finding | Resolution |
|---|---|---|
| S1 | The stats tested a mean, while the examples used tails; relative vs absolute effect was unreconciled | **Adopted.** Explicit estimand (§3.2), per-item tails, log-ratio, ≥ 8 items |
| S2 | Tier 2 guards assumed a local process | **Adopted.** `hca_guard.py` runs inside the remote runtime (§7) |
| S3 | Held-out data was readable by the agent; confirm failure was undefined | **Adopted.** Referee store, capped confirms, revert to the last confirmed commit (§3.4, §3.6) |
| S4 | A bare METRIC line had no stats path | **Adopted.** Three modes (§3.1) |
| S5 | `exclude` was a biased default | **Adopted.** Candidate-invalid scores as worst; infra-invalid is excluded from both arms (§3.5) |
| S6 | ABBA was overstated | **Adopted.** Randomised blocks, in-session A/A, sequential arms by default (§3.3) |
| S7 | First aha came too slowly | **Adopted.** `hca quick`, adaptive repeats, actionable "too noisy" (§2, §3.3) |
| S8 | The env allowlist would break tools | **Adopted.** Inherit minus a denylist by default (§4) |
| S9 | Privacy | **Adopted.** Scrubbed; specifics moved to a gitignored folder; the v0.1 commit was rewritten before any push |
| S10 | Sequential peeking | **Adopted.** Alpha spending + a half-useful-effect floor (§3.4) |
| S11 | Guards ignored noise | **Adopted.** A/A tolerance + one retry (§3.4) |
| S12 | Generality was overreached | **Adopted.** "Works best when" box, `[build]`, 6.3 as a campaign card, out-of-scope list; the overreaching sentence is removed |
| S13 | The worktree had gaps | **Adopted.** Branch in place, as autoresearch does (§2) |
| S14 | The watcher grammar was undefined | **Adopted.** Fixed grammar, missing field = no match, a stale-log rule (§7) |
| S15 | Process marking was weak; Windows | **Adopted.** Process groups first; platform scope stated (§11) |
| S16 | Missing lessons | **Adopted.** Amendments, matched control, "no evidence ≠ no effect", human clicks, rule-file forking added to §1.2; the honour-system mappings are now labelled as such |
| S17 | Competitor and name facts | **Partly rebutted.** The 4-star count and the PyPI `hca` = Human Cell Atlas CLI were both re-verified directly. The "no noise model" claim is softened to "a keyless demo is not a differentiator". The name is left to Jai (§9C) |
| S18 | The scripted demo was a toy | **Adopted.** A recorded real climb replayed through the real referee (§10, step 6) |
