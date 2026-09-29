# Design notes: lessons for a noisy, budgeted search loop

These notes come from using a propose/evaluate/select loop on a noisy empirical
problem with a scarce external verifier and a small compute budget. They are
written as generic harness requirements. Three are implemented in this release;
the rest are documented directions, not promises. See the maintenance scope in
the [README](README.md).

## Implemented in this release

1. **Noise-aware selection.** Paired comparisons on identical items, studentized
   cluster-bootstrap intervals, Bonferroni correction over every candidate or
   revision compared, and promotion only when the lower bound is above zero and
   the harmed-item and worst-subgroup guardrails hold. In the dream phase, each
   revision records a corrected paired check and `--promotion paired` gates on it.
   That limits promotion of noise, but choosing the best of N revisions developed
   against the same replay feedback is still a winner's curse: only
   `--confirm-runs`, which re-checks the final choice on held-out recorded worlds,
   addresses adaptive fitting.
2. **Same-session control and runtime tags.** Every score names its runtime.
   Numbers from different runtimes are never paired. An incumbent re-run in the
   candidates' session becomes the baseline, and its gap from the stored incumbent
   measures evaluator and numerical noise.
3. **Registration and freeze.** Arms, decision rule, useful effect, expected
   interval half-width and a premise check are hashed before results. Unregistered
   or pre-dated arms are labelled exploratory and cannot be promoted. Registration
   warns when the expected half-width exceeds the useful effect (a partial power check).

## Documented, not implemented

**Evaluator card and verifier budget.** The assumption of a reliable evaluator can
fail in three common ways: a cheap proxy evaluator with a directional bias, a
pretrained component that has already seen the evaluation data, and a
trustworthy verifier that can only be consulted rarely. A project config could carry an optional card with the
proxy's rank correlation against the true verifier, known bias directions and a
contamination statement, and flag candidates whose change moves in a known-bias
direction. The scarce verifier would be a budget with a ledger, plus a written
forecast recorded before each use.

**Headroom check before searching.** Before spending on a selector or a new policy,
compute the oracle ceiling: best-of-N over existing candidates versus the incumbent,
plus a diversity measure. If sampled candidates are near-duplicates, no selector
can win. If the headroom is below the useful effect, stop and report that.

**Full power check.** Registration already compares the expected half-width with
the useful effect. A fuller version would estimate the half-width from a pilot and
suggest gating to an affected subset or a paired design when the effect is too small.

**Negative-result memory.** Loops repeatedly retry mechanisms that already failed.
Before generating, search past rollouts and notes for the same mechanism and pass
the prior warnings to both the discovery agent and the policy developer.

**Latency and liveness.** A short remote job can take far more wall time than
compute time if completion events never wake the orchestrator. Completion should be event-driven
rather than heartbeat-polled. Wall time and compute time should be recorded
separately for each attempt. A manifest saying "running" is not proof of liveness:
check a heartbeat or a timestamp. After N hours with no efficacy number, force an
explicit keep-or-kill decision.

**Proportional review gates.** Review effort should scale with cost. Tier 0 (cheap
and local) gets a post-hoc audit only. Tier 1 (shared quota) gets one review before
launch. Tier 2 (paid compute or a scarce-verifier use) gets the full gate. Allow at
most one re-review per decision.

**Resource policy.** A per-project allowlist of the devices and hosts a job may
use, caps on transfer sizes and memory guards. The current harness checks
snapshot sizes after commands; it does not enforce OS-level quotas.

**Remote-job adapter pattern.** Test packaging against local fixtures before the
first push. Let the worker own the push, poll, diagnose, fix loop under a hard retry
cap. Verify inputs by file hash. Retrieve artifacts before the remote copy expires;
a result whose artifact was not retained cannot be re-checked, so it should be
marked as such and should not serve as an incumbent.

**One current verdict everywhere.** `compare` leads with a single current verdict.
Run and dream reports should do the same and visibly mark superseded or retracted
results, rather than leaving several headlines side by side.

**Instrument validation.** A tool used inside a candidate (for example, a detector
feeding a rule) should be benchmarked on a small labelled sample before anything
relies on it. A detector's false positives can create wins that are not real.
