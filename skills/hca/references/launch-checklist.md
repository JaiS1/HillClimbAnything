# Launch checklist (Tier 2)

Each item below is here because skipping it once cost a paid run.

## Before anyone clicks

- [ ] **The card is reviewed** (see review.md) and its amendments are applied.
- [ ] **The code is committed.** The runnable artifact (notebook, job script, container) has the
      commit hash **baked in** and refuses to run if the tree is dirty.
- [ ] **Three-way sha check:** the manifest, the per-file sidecars and the baked-in values agree.
      One function writes and verifies sidecars, so "what files exist" has a single definition.
- [ ] **Import closure:** a check that walks every import in your code and its vendored
      dependencies and reports **all** missing modules at once. It must pass in a clean env, not
      the one with everything preinstalled.
- [ ] **Hermetic child env:** child processes don't inherit poisonous parent variables (plotting
      backends, package-manager settings, interpreter paths). Build the child env explicitly.
- [ ] **Budget:** read the provider's real rate from its UI. The cap covers the HIGH estimate plus
      one lost run. The ledger counts every session, including aborted ones. If the budget file
      can't be read, refuse to start: **fail closed**.
- [ ] **Unit tests, the simulation matrix and real-library call-site tests pass.** The matrix
      covers VM loss, budget stop then resume, a corrupt upload, NaN loss, a scorer crash, a stale
      log, and a leaked env var.

## Rehearsal (cheap tier, sequential)

- [ ] Run the same entrypoint on the cheap tier (CPU, a tiny data slice) with `REHEARSAL=1`.
- [ ] It writes a **receipt keyed on the artifact sha**: PASS, FAIL, or SKIPPED with the reason,
      one line per step.
- [ ] **The paid job refuses to start without a PASS receipt for the same sha.**
- [ ] **Never rehearse in parallel with the paid run.** A parallel rehearsal finds the bug at the
      same moment the paid run dies.

## Inside the remote job (the laptop may sleep; these must run *there*)

- [ ] **Watchers** on the step log kill the job's own process group at the first bad signal: NaN,
      loss above a bound, a wrong step-0 eval. A missing field means no match, and a stale log has
      its own rule.
- [ ] **Heartbeat** JSON goes to durable storage every ~60 s with stage, progress, cost used and
      the commit. Under 3 min old means alive; over 5 min means gone. A manifest saying
      "running" is not liveness.
- [ ] **Idle self-release** of the paid runtime after any stop, through the provider's release
      call. It runs as a timer in the runtime.
- [ ] **Full error logging:** the complete reason goes to `SUMMARY.json`, never truncated. Logs
      are mirrored to durable storage, and partial outputs survive a kill.
- [ ] **Kill only processes you started** (process group, recorded PIDs). Never kill by name
      pattern.

## Human clicks (everything else is unattended)

| When | Click |
|---|---|
| before launch | grant tokens and storage access, accept provider prompts |
| launch | run all |

Launch while the human is awake. Overnight, browsers minimise, and OAuth or permission dialogs
can't be answered.

## After the run

- [ ] Re-score from the downloaded per-item outputs on a second machine. It must **agree** with
      the in-run numbers.
- [ ] Write a ledger entry with provenance: commit, manifest sha, run manifest.
- [ ] Do the deep evaluation (deep-eval.md) before any verdict.
