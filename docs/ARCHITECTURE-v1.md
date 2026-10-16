# HCA v1 architecture (Phase 1 core)

This is the implementation contract for the Phase 1 core. The design rationale is in
`DESIGN-hca.md` (§2–§4, amendment A1). Requirements: Python 3.11+, standard library only, POSIX
(Linux, WSL, macOS).

## Modules

| Module | Responsibility |
|---|---|
| `hca/core.py` | `HcaError`, atomic JSON I/O, hashing (exists) |
| `hca/stats.py` | block test, keep rule, noise report (exists; do not change its API) |
| `hca/paired.py` | `items` mode bootstrap-t from Dream Loop (exists) |
| `hca/spec.py` | load + validate `hca.toml` into a frozen `Spec` dataclass; defaults; clear errors |
| `hca/runner.py` | run one command: own process group, timeout, stall-free capture to files, env denylist, full (never truncated) error text |
| `hca/measure.py` | parse objective output; run randomised A/B blocks over two arm directories; return block scores |
| `hca/arms.py` | materialise an arm directory for a commit (git worktree + declared links), build it once per sha |
| `hca/gitops.py` | branch, scope check, keep (commit), revert (restore editable paths only), patch capture |
| `hca/ledger.py` | `.hca/` layout, attempt numbering, records, the referee store, `VERDICTS.md` |
| `hca/commands/*.py` | `quick`, `init`, `try_`, `confirm`, `report`, `status`, `doctor` |
| `hca/cli.py` | argparse entry point `hca` |

## Spec (`hca.toml`)

```toml
[task]
name     = "checkout-latency"            # required; [a-z0-9-]
brief    = "BRIEF.md"                    # default BRIEF.md
editable = ["src/**"]                    # required; git pathspec globs (relative to repo root)
frozen   = ["bench/**"]                  # default []; hca.toml is always frozen
link     = ["node_modules", ".venv"]     # default []; untracked paths symlinked into arm worktrees

[build]                                  # optional
command  = ["npm", "run", "build"]       # argv list, run once per arm sha, cwd = arm dir

[objective]
command       = ["./bench/run.sh"]       # required argv list; cwd = arm dir
metric        = "lcp_ms"                 # required; name in METRIC lines or item JSON
direction     = "min"                    # required: min | max
mode          = "blocks"                 # blocks (default) | items
useful_effect = "5%"                     # required
max_tries     = 50                       # default 50 (informational in v1; reported)

[[guards]]                               # optional, any number
name = "tests"
command = ["npm", "test"]                # pass = exit 0 (one retry on failure); run in candidate arm once per try
[[guards]]
name = "bundle"
metric = "bundle_kb"                     # read from objective output (METRIC line or item mechanism)
max_regression = "2%"                    # candidate median vs baseline median over blocks

[mechanism]
metrics = ["bytes_sent"]                 # extra METRIC names / item mechanism keys to report
slices  = ["route"]                      # item tag keys to slice by in reports

[limits]
command_seconds = 300                    # per command run (default 300)
try_seconds     = 1800                   # per try wall cap (default 1800); stop + revert on exceed
blocks          = 10                     # first look (default 10, minimum 10)
extend_to       = 20                     # second look (default 20; 0 disables extension)
confirms        = 5                      # max confirms per session (default 5)

[env]
deny = ["MPLBACKEND", "UV_*", "PYTHONSTARTUP"]   # default; glob on var names; removed from children
```

Validation errors name the field and the fix. Unknown keys are errors, which catches typos.

## Objective output contract

The objective command's **stdout** is parsed. Stderr is diagnostics only.

1. **Item JSON.** If the last non-empty stdout line parses as a JSON object with an `items` key:
   ```json
   {"metric":"lcp_ms","items":[{"id":"/cart","runs":[212.4,208.9],"tags":{"route":"cart"},
     "status":"valid","mechanism":{"bytes_sent":48211}}]}
   ```
   `runs` (a list) or `score` (a number) is required. `status` is `valid` (default),
   `candidate_invalid` or `infra_invalid`. The item value for a block is the median of `runs`.
2. **Otherwise METRIC lines.** Lines matching `^METRIC\s+([A-Za-z0-9_.-]+)=(\S+)$`. The line named
   `objective.metric` is the value. Others are mechanism or guard metrics.
3. A nonzero exit, a missing metric, or a non-positive or non-finite value in log-ratio use is an
   **invalid run**. It is recorded with the full stderr tail (the last 4 KB) and the run's
   stdout/stderr files.

## Measurement (`measure.run_blocks`)

`run_blocks(spec, baseline_dir, candidate_dir, n_blocks, rng, env_extra, out_dir) -> BlockResult`

- For each block, the arm order is `rng.shuffle(["baseline", "candidate"])`. Each arm runs the
  objective command once, with `cwd` = that arm's dir. Arms always run **sequentially**.
- **Block score:**
  - one number: `stats.log_ratio(base, cand, direction)`;
  - items: `stats.item_block_score` over the items valid in both arms.
- **Invalid handling:**
  - A `candidate_invalid` item, or a candidate run failure, scores as **the worst observed value**
    for that item in this try, or the run's worst: the max for `min` metrics, the min for `max`.
  - An `infra_invalid` item is dropped from **both** arms for that block. More than 10% of items
    infra-invalid in a block voids the block. More than 30% of blocks voided voids the try (verdict
    `void`).
  - A baseline run failure voids the block and is logged loudly: the baseline should not fail.
- Each run's raw parsed output is appended to `out_dir/runs.jsonl`: block, arm, order, wall
  seconds, `/proc/loadavg` if present, values, and status.
- `env_extra` is used for `HCA_ARM=baseline|candidate`, `HCA_ITEMS=dev|heldout` and
  `HCA_HELDOUT_DIR`.

## Arms (`arms.py`)

- The baseline arm for commit `sha` is `git worktree add --detach .hca/arms/<sha12> <sha>`, created
  once and reused. Each `link` path is symlinked from the repo root if it exists there. A build
  runs once per arm sha, with a marker file `.hca/arms/<sha12>.built` that records the exit status.
- The candidate arm is the user's checkout (the repo root) with the agent's uncommitted edits. Its
  build runs once per try.
- Arm worktrees are pruned on `hca init --reset` and by `hca doctor --clean`. Never `rm -rf`
  outside `.hca/`.

## Git operations (`gitops.py`)

- `init` requires a clean tree (`git status --porcelain` empty, ignoring `.hca/`). It creates and
  checks out the branch `hca/<task>` from HEAD and records `init_sha`. It adds `.hca/` to
  `.git/info/exclude`, not to the user's `.gitignore`.
- **Scope check before a try:** collect the changed paths (`git diff --name-only <last_keep>`
  plus `git ls-files --others --exclude-standard`). Every path must match an `editable` glob and
  must not match `frozen` or `hca.toml`. Frozen files' sha256 must equal the values recorded at
  init. Any violation makes the try **invalid (scope)**, with no measurement; the edits are left in
  place for the agent to fix.
- **Patch capture:** `git add -A -- <editable pathspecs>`, then `git diff --cached <last_keep>`
  goes to `patch.diff`.
- **Keep:** `git commit -m "hca keep NNNN: <message>"` on the branch, with only staged editable
  paths. `last_keep` becomes the new sha.
- **Revert:** `git reset -q -- <editable pathspecs>`, then
  `git checkout <last_keep> -- <editable pathspecs>` for tracked files. Untracked files matching
  editable globs are deleted (only those). Nothing else is touched, and remotes never are.

## Ledger (`.hca/` in repo root; referee store `~/.hca/<task>/`)

```
.hca/
  state.json            # task, init_sha, last_keep, last_confirmed, confirms_used, frozen hashes, spec hash
  attempts/0001/        # message.txt, patch.diff, scope.json, guards.json, runs.jsonl, verdict.json,
                        # build logs, per-run stdout/stderr for invalid runs
  arms/<sha12>/         # baseline worktrees
  VERDICTS.md           # one current verdict per question (task headline + per-attempt list)
~/.hca/<task>/
  heldout/              # copied at init from `--heldout PATH` (a directory outside the repo)
  confirms.jsonl        # every confirm: arms, blocks, test result (full detail lives here only)
```

Held-out data in v1 lives outside the repo, at a path the user gives at `init`
(`--heldout PATH`), and is copied into the referee store. The objective command receives
`HCA_ITEMS=heldout` and `HCA_HELDOUT_DIR` during `confirm`. This keeps held-out data out of the
agent's working tree; it is not a security boundary.

## Commands (v1)

- **`hca quick [--blocks N] [--budget-seconds 120] -- <cmd...>`**
  - Needs no spec. It runs `<cmd>` in the cwd as both arms (an A/A) for N blocks; the default N is
    chosen from the first run's time to fit the budget (between 5 and 20).
  - It prints `stats.noise_report` as plain sentences and ends with the next step.
  - Useful effect: `--useful 5%`.
  - It writes nothing to the repo unless `--save`.
- **`hca init [--heldout PATH]`**
  - Validates the spec, checks for a clean tree, then creates the branch, `.hca/state.json` and the
    frozen hashes.
  - **Adopts its own branch.** If `hca/<task>` already exists and is the checked-out branch, there is
    no `.hca/state.json`, and the referee store holds no confirms, init uses the branch as it is (the
    baseline is its tip) and prints one line saying so. Committing the harness on `hca/<task>` before
    the first init is the normal start. If the branch exists but is not checked out, init refuses and
    says to check it out or delete it. State without `--reset` and the confirm-cap guard still refuse.
  - It runs **one A/A on the baseline**: `extend_to` blocks of the init sha against itself. It
    saves a noise report and refuses to finish if the verdict is `too-noisy`, unless `--force`.
- **`hca try -m "what changed and why"`**
  1. Scope check.
  2. Build the candidate.
  3. Guard commands.
  4. Run `blocks` blocks, apply `stats.keep_decision` (look 1 of 2 when extension is on), extend
     if told to, and apply the metric guards.
  5. Keep or revert, and record everything.
  6. Print a short verdict: % estimate, bound, p, blocks, wall time, and reasons.
  - Exit code: 0 for keep, 1 for revert, 2 for invalid or void, 3 for usage errors.
- **`hca confirm`**
  - Arms: `init_sha` (baseline) vs `last_keep` (candidate).
  - Uses `HCA_ITEMS=heldout` when a held-out dir exists, else fresh blocks on dev items.
  - Runs `extend_to` blocks at one look with α = 0.01, using the same keep floor.
  - On pass, sets `last_confirmed = last_keep`.
  - On fail, resets the branch's editable paths to `last_confirmed` (or `init_sha`) as a new commit
    "hca revert to confirmed", and marks the attempts in between as `unconfirmed`.
  - It prints only pass/fail, the estimate and the bound. Per-item detail goes to the referee
    store. It counts against `confirms`.
- **`hca report [--md PATH]`**
  - Headline: the confirmed delta vs init (if any), then the unconfirmed cumulative delta.
  - An attempts table: n, message, verdict, estimate, p, blocks, wall time.
  - Validity rate (valid tries / all tries); mechanism metric deltas for kept attempts; slices
    (per-tag median item log ratio) for the last confirm, if items.
  - The line "no evidence is not no effect" for reverted attempts whose bound includes the useful
    effect.
- **`hca status`** prints the short state: branch, last keep, last confirmed, tries used / max,
  confirms used / max.
- **`hca doctor`** checks: Python ≥ 3.11, git present, inside a repo, leaked env vars (the deny
  list plus `PYTHONPATH`, `LD_PRELOAD`, `VIRTUAL_ENV` mismatch), CPU load (`/proc/loadavg`), the
  platform (warns on native Windows), and skill install status (later).

## Testing

Use `unittest` with a fixture git repo built in a temp dir. The objective is a tiny Python script
whose speed is controlled by a value in an editable file, with an injectable noise seed, so tests
are fast (< 0.05 s per run) and deterministic enough. Required scenarios:

- keep;
- revert on no-op;
- invalid scope (edit outside editable, edit to a frozen file);
- a candidate crash scored as worst;
- an infra-invalid drop;
- guard failure;
- extension path;
- confirm pass;
- confirm fail with reset;
- quick on a quiet command and on a noisy one;
- spec validation errors;
- an env denylist removal;
- a timeout killing the process group;
- revert leaves files outside editable untouched (including untracked user files).

---

## Amendment A2 (2026-10-15): snapshot arms and a referee that verifies itself

**Why.** The Phase 1 adversarial code review (4 blockers, all reproduced) found three ways
`confirm` could pass a no-op at about -53%:

1. a staged (index-only) change to a frozen file;
2. editing the reused baseline worktree;
3. editing `.hca/state.json`.

It also found that the candidate ran in the live root while the baseline ran in a clean worktree,
so any ignored or untracked file the benchmark reads was a one-sided win. Other findings:

- files changed during measurement were committed unmeasured;
- the candidate could print the metric itself;
- replays went uncounted;
- interrupts left benchmarks running.

Where this amendment and the sections above disagree, this one wins.

### A2.1 Snapshot arms (replaces "Arms" and the keep/revert mechanics in "Git operations")

- **The candidate is a commit, built privately.** Take the last keep's tree, overlay the
  working-tree contents of the changed editable paths, and commit it. Use a temporary index
  (`GIT_INDEX_FILE`: `read-tree last_keep`, then `update-index --add/--remove` per editable path,
  `write-tree`, `commit-tree -p last_keep`). The user's real index is never read or written.
  Scope is computed from this snapshot vs `last_keep`, so it covers editable paths only by
  construction. Staged changes in the user's index outside editable produce a warning ("your
  index has staged changes outside editable; HCA ignores the index").
- **Both arms are detached worktrees under `.hca/arms/<sha12>/`**: the baseline at `last_keep`
  (or `init_sha` for confirm), the candidate at the snapshot sha.
  - The structure is identical: each gets the same `link` symlinks and the same build. Neither is
    the repo root.
  - A benchmark walking its cwd sees only that worktree.
  - **Before every measurement, each arm is verified:** `HEAD == sha` and no modified tracked files
    (`git -C arm status --porcelain --untracked-files=no` empty). Otherwise the arm is removed and
    recreated, with a warning. Untracked build outputs are allowed.
  - **Warm-up:** one discarded objective run per arm per try, before block 0.
- **Keep** first re-checks that the working tree's editable files still equal the snapshot. Any
  difference means the files changed during measurement: the try is `void` and the edits are left
  as they are. Then it moves the branch to the snapshot commit with `git update-ref`, followed by
  `git reset -q <snapshot>`. The reset is mixed: it resets the index only, and the working tree
  already equals the snapshot on editable paths. Commit message: `hca keep NNNN: <msg>`. The
  committed tree is exactly the measured tree.
- **Revert:** unchanged (restore editable paths to `last_keep`, delete untracked editable files).
  The snapshot ref is kept as `refs/hca/attempts/NNNN` so the patch stays reproducible.
- **Restore on a failed confirm:** build a snapshot commit whose editable paths equal
  `last_confirmed` (or `init_sha`), on top of HEAD, by the same private-index method. Move the
  branch to it, then sync the working tree's editable paths.
- **Paths HCA always ignores** in scope and snapshots: `__pycache__/`, `*.pyc`, `.DS_Store`, and
  anything git ignores. Linked paths are excluded too.
- **Pruning:** worktrees outside {`init_sha`, `last_confirmed`, `last_keep`} are pruned after the
  attempt record and state are written. Pruning is best-effort and never fails a try.
- **v1 refuses** to start on repos with submodules (`.gitmodules`) or an in-progress
  merge/rebase/cherry-pick, with a clear message.

### A2.2 Frozen files and the referee store

- **Frozen hashing** walks the filesystem: tracked, untracked and ignored files under `frozen`
  globs, plus `hca.toml`. It streams in 1 MiB chunks. Linked paths matched by `frozen` are hashed
  too, so a gitignored dataset can be both linked and protected.
- **The referee store `$HCA_HOME/<task>/` is the source of truth** for `init_sha`, the spec hash,
  the frozen hashes, `confirms_used`, the reset log and the attempt tree hashes. `.hca/state.json`
  is a cache. On any disagreement the store wins and a warning is printed. It is not a security
  boundary; the aim is that tampering takes deliberate effort and leaves a trace.
- **`confirm` verifies before measuring:**
  - `git diff --name-only init_sha last_keep` touches only editable paths;
  - `last_keep` descends from `init_sha`;
  - every commit in between is an `hca keep` / `hca revert to confirmed` commit;
  - frozen files at both shas match the stored hashes.

  Otherwise it refuses (exit 2) and says what is wrong.
- **`init --reset`** refuses when the store records any confirm, unless `HCA_ALLOW_RESET=1` is set
  in the environment, which marks it as a human decision. Every reset is logged in the store with
  a timestamp, and `confirms_used` is never reset.

### A2.3 Measurement integrity

- **Result channel:** the objective may write its result (METRIC lines or item JSON) to the file
  named by `HCA_RESULT_FILE`, which is fresh per run. If that file is non-empty it is used and
  stdout is ignored. Otherwise stdout is parsed as before, but **the objective metric appearing on
  more than one METRIC line is an invalid run**. In the candidate arm that means `candidate_invalid`.
  Item JSON must be the last line with no METRIC line for the objective metric. The docs recommend
  `HCA_RESULT_FILE`.
- **`HCA_ARM` is no longer exported** to children; it stays in `runs.jsonl` only.
  `HCA_ITEMS` and `HCA_HELDOUT_DIR` are still exported.
- **A candidate-side `infra_invalid`** on an item that is valid in the baseline is scored as
  `candidate_invalid`.
- **`insufficient`** (too few scored blocks to reject) is `void`: edits kept, not a valid try.
- **`try_seconds`** covers builds, guards and blocks. Exceeding it gives `void`, with the edits kept.

### A2.4 Tries, replays and confirm output

- Each attempt records the snapshot **tree hash** (in `.hca/` and in the store). A try whose
  tree hash equals any earlier attempt's is refused (exit 2, nothing measured) unless `--again` is
  given; `--again` is recorded and counted.
- `void` and `invalid` attempts count toward `max_tries`. Past `max_tries`, `try` still runs but
  prints a warning on every try. `report` prints "expected false keeps so far <= tries x 1%".
- **`confirm` shows the agent only PASS or FAIL.** On PASS it also prints the confirmed estimate
  and bound, which become the headline anyway. On FAIL it prints no numbers; the detail goes to
  the referee store, and `hca report --referee` shows it to a human.

### A2.5 Robustness

- **Signals:** `cli.main` turns SIGTERM and SIGHUP into `KeyboardInterrupt`. `runner.run` kills its
  process group in a `finally`, on any exit path. An interrupted try records verdict
  `interrupted`, keeps the edits and releases the lock.
- **Crash between keep and state write:** before `update-ref`, write `.hca/pending-keep.json`
  (`{n, snapshot_sha, last_keep}`). `open_session` reconciles it:
  - if the branch tip equals the snapshot, the keep is adopted (state and record finished);
  - if it still equals `last_keep`, the marker is discarded.

  The HEAD-mismatch message names this.
- **Exit codes:** 0 keep or pass, 1 revert or fail, 2 invalid/void/refused, 3 usage, **4 internal
  error**. Any unexpected exception prints `hca: internal error: ...` with the traceback path
  (written to `.hca/crash-<ts>.log`).
- **Git calls** get a 120 s timeout. The lock records pid + `/proc/<pid>/stat` start time, so PID
  reuse can't make a stale lock look live; the error message mentions `rm .hca/lock`.
- **`noise_report`** needs at least 8 A/A blocks.

### A2.6 Tests required (each finding gets a regression test)

1. An index-only change to a frozen file is never committed.
2. A tampered baseline worktree is detected and recreated.
3. A tampered `state.json` is overridden by the store, and confirm refuses an out-of-scope history.
4. `init --reset` with confirms refuses without `HCA_ALLOW_RESET`.
5. An ignored file in the repo root no longer advantages the candidate (both arms are worktrees).
6. A file changed during measurement gives `void`, with nothing committed.
7. SIGINT and SIGTERM during a run kill the benchmark group and record `interrupted`.
8. A crash between `update-ref` and the state write is reconciled.
9. A candidate printing the objective METRIC a second time is `candidate_invalid`, and
   `HCA_RESULT_FILE` takes precedence.
10. A replayed tree is refused without `--again`.
11. An internal error exits with 4.
12. `__pycache__` under editable is ignored.
13. `insufficient` gives `void`.
14. Candidate-side `infra_invalid` is scored as `candidate_invalid`.
15. **End-to-end calibration:** 100 no-op tries through the real `hca try` on the fixture keep at
    most 3 times (as a seeded, deterministic fixture).

### A2 implementation notes (where the code settled a question the amendment left open)

- **Clean tree.** `init` refuses tracked modifications and untracked files that are editable, frozen or
  `hca.toml`. Other untracked files only produce a warning, and the same rule applies to a try: they are left
  out of the candidate.
- **Scope.** A tracked edit outside `editable` (or to a frozen file) is still an invalid try with nothing
  measured, even though the snapshot could not contain it. Staged-only changes are not edits at all.
- **Void keeps the edits.** Every `void` verdict (blocks that cannot reject, `try_seconds`, an arm or an
  editable file changed during the run) leaves the working tree alone and nothing committed. This replaces
  "stop + revert on exceed". A `revert` is the only verdict that restores files.
- **Exit codes.** 0 keep or pass, 1 revert or fail, 2 invalid / void / interrupted / refused, 3 usage, 4 internal
  error. SIGINT exits 130, SIGTERM 143, SIGHUP 129 after cleaning up.
- **Keep commits skip hooks.** The keep is `commit-tree` plus `update-ref`, so the user's pre-commit and
  commit-msg hooks (and commit signing) do not run on hca's commits: the measured tree is committed byte for byte.
- **`init --reset`** re-baselines at the current branch tip (the last keep), not at the original commit. The old
  baseline stays in the store's reset log, and a re-init is refused outright once the store holds confirms
  (unless `HCA_ALLOW_RESET=1`), whether or not `--reset` is given.
- **A/A at init** compares the baseline worktree with a second worktree of the same tree (an empty snapshot
  commit), so it measures the same pair of directory kinds as every later try. Warm-up runs also precede the
  blocks at init and confirm.
- **Untracked editable files** are created by the agent and deleted by a revert, anywhere under `editable`: do not
  work in the same checkout while the loop runs.
- **Held-out data** is outside the repo but its path (`HCA_HELDOUT_DIR`) reaches the benchmark during confirm,
  so candidate code could read it. This is not a security boundary.

### A2 follow-ups from the re-review (NEW-1 to NEW-6)

- **Clean arms (NEW-3).** After an arm's build, `.hca/arms/<sha12>.files` records the arm's untracked files, ignored ones
  included (path, size, mtime_ns; `.git` and `link` paths excluded). Before every objective run, warm-up included,
  untracked files not in the manifest are deleted (symlinks are removed as links, never followed), and a manifest file that
  changed or vanished makes hca recreate and rebuild the arm with a warning. A candidate's disk cache cannot span runs.
- **Store identity (NEW-1).** `init` writes a random `store_id` into the referee store and into `.hca/state.json`. If
  state.json has one and the store is missing or has another, every command that opens the session refuses (exit 2),
  naming `HCA_HOME`, and never rebuilds the store. A state.json from before store ids is adopted as before. Replay memory
  also reads the attempt records under `.hca/` (archived sessions included), and `confirm` refuses if the session had a
  held-out set and the store no longer does.
- **Pending operations (NEW-2, NEW-4).** Before `update-ref`, the store gets a `pending` entry (kind `keep` or `restore`,
  snapshot sha, previous last keep and, for a keep, the full verdict record with its digest). `.hca/pending-keep.json` is only
  a mirror. Reconciliation adopts a move only when the store's entry matches the branch tip, takes the attempt record from
  the store, and refuses (exit 2) if an existing attempt record has a different digest or if a marker has no store entry
  and the branch tip is not the old keep. For `restore` it also finishes the working-tree sync, so a kill between the branch
  move and the sync no longer leaves the undone content on disk. The `refs/hca/attempts/NNNN` ref is created when the attempt
  is recorded, after the measurement, so no arm sees it while it runs.
- **Residuals.** Documented in the README and `skills/hca/references/spec.md`: an evaluator that shares a process with the
  code under test (S8), arms that can read the git state (S19), shared `link` symlinks (NEW-5), editable installs (NEW-6,
  with a `hca doctor` warning). `state.last_keep` is still agent-writable; `verify_history` bounds what it can point at.
