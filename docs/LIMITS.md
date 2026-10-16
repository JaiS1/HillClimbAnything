# What HillClimbAnything does not do

HCA is a referee, not a guarantee. This page lists what it cannot see, and what to do about each
gap. Read it before you trust a number.

## Statistics

- **The test controls false keeps per try, not per session.** Each try keeps a do-nothing change
  at most about 1% of the time; simulated, the shipped rule keeps 0.0–0.2%. Over many tries, chance
  adds up. `hca report` prints the bound ("expected false keeps so far ≤ tries × 1%"), and that is
  why `hca confirm` exists: only confirmed gains are results.
- **"No evidence" is not "no effect".** A reverted change whose interval still includes the useful
  effect may be a real but small win. The report marks these. Bundling several small changes is a
  legitimate way to measure them together.
- **Held-out data is out of the repo, not secret.** Its path is given to the benchmark during
  `confirm`, so code you run could read it.

## Gaming

HCA makes cheating inconvenient. It is not a security boundary against an agent that is determined
to cheat, so read the patches.

What it already does:

- **Measurement before tampering:** a tracked edit outside `editable`, or any change to a frozen
  file (ignored and linked files included), invalidates the try before anything is measured.
- **Scoring:** a crashed candidate scores as the worst value seen, and a second `METRIC` line for
  the objective makes the run invalid.
- **Replays:** an identical tree tried twice needs `--again`, and it counts as a try.
- **Arms:** both arms are clean worktrees of exact commits. Untracked files are removed before
  every run, and an arm modified during a run is rebuilt.
- **Ledger:** the referee store (`~/.hca/<task>/`) keeps the original commit, the frozen hashes, the
  confirm count and the pending keeps, so rewriting `.hca/state.json` does not move them.

Known residuals, and what to do about each:

- **Same-process evaluators.** If the benchmark runs the code under test in its own process, that
  code can rewrite `HCA_RESULT_FILE` or print a number before it exits. Fix: run the code under
  test in a child process, time it from the parent, and remove `HCA_RESULT_FILE` from the child's
  environment. The [showcase benchmark](../showcases/python-markdown/bench/hca_bench.py) does this.
- **Arms are not blind.** A benchmark can tell which arm it is in from the git state around it
  (`HEAD`, the worktree path). Nothing stops code that goes looking for it.
- **`link` is shared.** A linked path is one symlink target that both arms see and can write to. If
  it holds data that must not change, also list it under `frozen`, so it is hashed on every try.
- **Writes outside the arms** (`/tmp`, `$HOME`, linked directories, the network) are not seen.
- **Editable installs.** If your project is `pip install -e`'d into the Python that runs the
  benchmark, `import pkg` can resolve to the live checkout in both arms, so every try measures the
  same code twice. `hca doctor` warns about this. The benchmark should put its own checkout first
  on `sys.path` and assert where the package was imported from.
- **Benchmarks that repeat identical inputs in one process** reward caches keyed on content. Use a
  fresh process per run, with each input once, and rule content caches out in your brief.

## Scope

- **Not built for concurrent use.** Don't edit, commit or switch branches in the checkout while a
  loop is running. Two `hca` commands at once are refused by a lock. v1 refuses repos with
  submodules or a half-finished merge or rebase.
- **Platforms.** Linux and WSL are the reference. Native Windows (Python 3.11+) is supported and runs in CI, but its process control is weaker than POSIX process groups: the command is put in a Job Object, and a timeout sends Ctrl-Break, then terminates the job and runs `taskkill /T /F`. A process spawned in the first milliseconds before the job is assigned can escape, and so can a grandchild when Windows refuses the job (the fallback walks parent pids, which a dead parent breaks). A returncode there is the raw exit code, never a negative signal. Commands run without a shell, so write a batch file as `["cmd", "/c", "x.bat"]`. macOS is covered by CI only.
- **Not a benchmark harness.** You bring the command that measures; HCA decides what the
  measurement means.
- **Not orchestration.** HCA doesn't call model APIs. Your agent drives; HCA referees.
- **Expensive remote runs** (paid GPUs, notebooks) are covered by the skill's campaign references
  (card, launch checklist, review, deep evaluation). There is no remote tooling yet.
- **Not proven broadly.** There is one showcase, on one machine.
