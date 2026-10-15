"""`hca try -m "..."`: measure the agent's uncommitted edits against the last keep, then keep or revert.

The order is fixed by the contract: scope check, snapshot, builds, guard commands, a discarded warm-up run
per arm, randomised blocks, the keep rule (with at most one extension), metric guards, then keep or restore.
The candidate is a *snapshot commit* (the last keep's tree plus the working-tree contents of the changed
editable paths, built in a private index) run in its own worktree beside the baseline's, so both arms are
structurally identical; a keep moves the branch to that very commit, so the committed tree is exactly the
measured tree. Every outcome, valid or not, becomes an attempt record, because the validity rate and the
history must not be tidied. Only the editable paths are ever restored; nothing outside them is touched.

Exit codes: 0 keep, 1 revert, 2 invalid (scope, no change, replay) or void (infra, time, files changed
during the run) or interrupted, 3 usage errors, 4 internal error (cli).
"""

from __future__ import annotations

import time

from .. import arms, gitops, ledger, measure, runner, stats
from ..core import HcaError
from . import common

NO_EVIDENCE = ("No evidence, not no effect: the interval {interval} still allows the useful effect ({useful}). "
               "More blocks or a lower-noise measurement could tell; this is not proof that the idea fails.")


def add_arguments(parser) -> None:
    parser.add_argument("-m", "--message", help="what changed and why (required)")
    parser.add_argument("--again", action="store_true",
                        help="measure an edit whose tree is identical to an earlier attempt (recorded, and it counts as a try)")


def run(args, out=print, cwd=None, seed=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "try", out):
        return _run(args, out, cwd, seed)


class _Try:
    """What the pieces of one try share."""

    def __init__(self, root, spec, state, adir, rec, out, started, deadline, rng):
        self.root, self.spec, self.state, self.adir, self.rec, self.out = root, spec, state, adir, rec, out
        self.rng = rng
        self.started, self.deadline = started, deadline
        self.snap = None
        self.cand_dir = None
        # Before every objective run an arm is cleaned of untracked files its build did not make (NEW-3: a disk cache
        # written by the candidate must not survive from one run to the next, the warm-up included).
        self.prepare = {
            "baseline": lambda: arms.prepare_run(root, spec, state["last_keep"], "baseline", warn=out),
            "candidate": lambda: arms.prepare_run(root, spec, self.snap["sha"], "candidate", logs=adir, warn=out)}

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def refresh(self):
        """Both arm directories, verified right now (a tampered one is recreated with a warning)."""
        base = arms.ensure_arm(self.root, self.spec, self.state["last_keep"], "baseline", warn=self.out)
        cand = arms.ensure_arm(self.root, self.spec, self.snap["sha"], "candidate", logs=self.adir, warn=self.out)
        return base, cand


def _run(args, out=print, cwd=None, seed=None) -> int:
    message = (getattr(args, "message", None) or "").strip()
    if not message:
        raise HcaError('hca try needs a message. Fix: hca try -m "what changed and why".')
    again = bool(getattr(args, "again", False))
    root, spec, state = common.open_session(cwd, out=out)
    task = state["task"]
    started = time.monotonic()
    deadline = started + spec.limits.try_seconds
    mode = spec.objective.mode
    scope = gitops.scope_check(root, spec, state)
    store = ledger.load_store(task) or {}
    for problem in gitops.verify_history(root, spec, state["init_sha"], state["last_keep"], store.get("frozen_blobs") or state.get("frozen_blobs") or {}):
        scope["violations"].append({"path": "(history)", "reason": problem})
        scope["ok"] = False
    n, adir = ledger.new_attempt(root, task)
    ledger.note_attempt(task, n, again=again)
    (adir / "message.txt").write_text(message + "\n", encoding="utf-8")
    ledger.write_record(adir, "scope.json", scope)
    tries = ledger.tries_used(root, task)
    if tries > spec.objective.max_tries:
        out(f"warning: this is try {tries}, past max_tries ({spec.objective.max_tries}). Every extra try adds to the chance of a false keep "
            "(about 1% each); `hca confirm` is the check, and the report says how many keeps to expect from chance alone.")
    staged = gitops.staged_outside_editable(root, spec)
    if staged:
        out(f"warning: your index has staged changes outside editable ({', '.join(staged[:3])}{' ...' if len(staged) > 3 else ''}); "
            "HCA ignores the index (a keep resets it).")
    if scope["untracked_outside_editable"]:
        out(common.untracked_warning(scope["untracked_outside_editable"]))
    rng, used_seed = common.make_rng(seed)
    rec = {"n": n, "message": message, "at": ledger.now(), "mode": mode, "unit": "%" if mode == "blocks" else "", "base": state["last_keep"],
           "verdict": None, "valid": False, "reasons": [], "seed": used_seed, "confirm": None, "again": again}
    t = _Try(root, spec, state, adir, rec, out, started, deadline, rng)

    changes = gitops.editable_changes(scope)
    if not scope["ok"] or not changes:
        reasons = [f"{v['path']}: {v['reason']}" for v in scope["violations"]] or \
            ["no changes: there is nothing to measure (the working tree equals the last keep)"]
        (adir / "patch.diff").write_text(gitops.raw_patch(root, state["last_keep"]), encoding="utf-8")
        return _finish(t, "invalid", reasons, note="scope" if not scope["ok"] else "empty")

    try:
        arms.ensure_arm(root, spec, state["last_keep"], "baseline", timeout=t.remaining(), warn=out)
    except arms.ArmBuildError as exc:
        if exc.result is not None and exc.result.timed_out and t.remaining() <= 0:
            return _finish(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out while building the baseline"])
        _discard(adir, task, n)                         # a broken baseline is an environment problem, not a try
        raise
    except HcaError:
        _discard(adir, task, n)
        raise
    first_line = message.splitlines()[0]
    t.snap = gitops.snapshot(root, spec, state["last_keep"], f"hca keep {n:04d}: {first_line}", changes)
    rec.update(tree=t.snap["tree"], snapshot=t.snap["sha"])
    (adir / "patch.diff").write_text(gitops.patch_between(root, state["last_keep"], t.snap["sha"]), encoding="utf-8")
    if t.snap["tree"] == gitops.tree_of(root, state["last_keep"]):
        return _finish(t, "invalid", ["no changes: the editable files equal the last keep once build and cache files are ignored"], note="empty")
    # CONTRACT NOTE: a refused replay is recorded as an `invalid` attempt (it counts toward tries and max_tries), so
    # hammering `hca try` on the same edit shows up in the validity rate instead of vanishing.
    prior = ledger.seen_tree(task, t.snap["tree"], root)
    ledger.note_attempt(task, n, tree=t.snap["tree"], again=again, refused=bool(prior) and not again)
    if prior:
        rec["replay_of"] = prior.get("n")
        if not again:
            return _finish(t, "invalid", [f"refused: this exact tree was already tried as attempt {prior.get('n'):04d}. A replay is a fresh roll "
                                          "of the dice (each try is a 1% test), so it needs --again, and it counts as a try."], note="replay")
    try:
        return _measure_and_decide(t)
    except BaseException as exc:                        # leave the edits alone, but never leave an attempt without a verdict
        pending = ledger.get_pending(task)
        if pending and pending.get("n") == n:           # the branch may have moved: reconciliation owns this record, not us
            raise
        if not (adir / "verdict.json").exists():
            if isinstance(exc, KeyboardInterrupt):
                _record(t, "interrupted", ["interrupted (Ctrl-C or a termination signal) while the try was running; your edits are untouched"])
                out(f"attempt {n:04d}  INTERRUPTED  (no verdict; your edits are untouched)")
            else:
                _record(t, "void", [f"crashed: {type(exc).__name__}: {exc}"])
            arms.prune_unused(root, [state["init_sha"], state.get("last_confirmed"), state["last_keep"]])
        raise


def _measure_and_decide(t: _Try) -> int:
    root, spec, state, adir, rec, out = t.root, t.spec, t.state, t.adir, t.rec, t.out
    mode = spec.objective.mode
    try:
        t.cand_dir = arms.ensure_arm(root, spec, t.snap["sha"], "candidate", logs=adir, timeout=t.remaining(), warn=out)
    except arms.ArmBuildError as exc:
        if t.remaining() <= 0:
            return _finish(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out while building the candidate"])
        why = "timed out" if exc.result.timed_out else f"exit status {exc.result.returncode}"
        return _finish(t, "revert", [f"candidate build failed ({why}); last stderr:\n{exc.result.error_tail}"], valid=False)
    if t.remaining() <= 0:
        return _finish(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the build"])

    guards = _run_command_guards(spec, t.cand_dir, adir, t.deadline)
    ledger.write_record(adir, "guards.json", guards)
    failed = [g for g in guards if not g["passed"]]
    if failed:
        if t.remaining() <= 0:
            return _finish(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the guard commands"], guards=guards)
        return _finish(t, "revert",
                       [f"guard {g['name']!r} failed (after one retry): {g['attempts'][-1]['error_tail'] or 'exit ' + str(g['attempts'][-1]['returncode'])}" for g in failed],
                       valid=False, guards=guards)
    if t.remaining() <= 0:
        return _finish(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the guard commands"], guards=guards)

    env = {"HCA_ITEMS": "dev"}
    if mode == "items":
        outcome = _decide_items(spec, t, env)
    else:
        outcome = _decide_blocks(spec, t, env)
    rec.update(outcome["fields"])
    reasons = list(outcome["reasons"])
    verdict, valid = outcome["verdict"], outcome["valid"]

    # Both arms must still be exactly their commits after the run: a benchmark or candidate that wrote into a
    # worktree (the baseline's included) changed what was compared.
    for sha, label in ((state["last_keep"], "baseline"), (t.snap["sha"], "candidate")):
        problem = arms.verify_arm(arms.arm_dir(root, sha), sha)
        if problem:
            verdict, valid = "void", False
            reasons.append(f"the {label} arm was modified while it was measured ({problem}); the comparison cannot be trusted. "
                           "Fix: the benchmark and the guards must not write to tracked files.")
            break

    guard_rows = list(guards)
    medians = outcome.get("arm_medians")
    if medians is not None and verdict != "void":
        for g in spec.guards:
            if g.metric is None:
                continue
            row = _metric_guard(g, medians)
            guard_rows.append(row)
            if not row["passed"]:
                valid = False
                reasons.append(row["message"])
                if verdict == "keep":
                    verdict = "revert"
        ledger.write_record(adir, "guards.json", guard_rows)
        rec["arm_medians"] = medians
    if verdict == "keep" and not valid:
        verdict = "revert"
    return _finish(t, verdict, reasons, valid=valid, guards=guard_rows)


# --- the two measurement modes ---------------------------------------------------------------------

def _decide_blocks(spec, t: _Try, env) -> dict:
    limits, useful, direction = spec.limits, spec.objective.useful, spec.objective.direction
    looks = 2 if limits.extend_to > limits.blocks else 1
    fields = {"extended": False, "blocks": 0, "blocks_run": 0}
    timeout = "try_seconds ({}) ran out before the measurement finished".format(limits.try_seconds)

    def void(reasons):
        return {"verdict": "void", "valid": False, "reasons": reasons, "fields": fields, "arm_medians": None}

    def too_few(result, level_note):
        return void(list(result.warnings) + [f"only {len(result.scores)} usable blocks, too few to decide {level_note}; edits kept, not a valid try"])

    base_dir, cand_dir = t.refresh()
    result = measure.run_blocks(spec, base_dir, cand_dir, limits.blocks, t.rng, env, t.adir, deadline=t.deadline, warmup=True, prepare=t.prepare)
    fields.update(blocks_run=result.blocks_run)
    if result.blocks_run < limits.blocks:             # the deadline stopped the blocks: say so, whatever else is also true
        return void([timeout])
    if result.void or len(result.scores) < 2:
        return void(result.warnings or ["too few usable blocks"])
    decision = stats.keep_decision(result.scores, useful, look=1, looks=looks, direction=direction)
    if decision["verdict"] == "insufficient":
        return void(decision["reasons"] + ["blocks were voided, so this try cannot reject; edits kept, not a valid try"])
    if decision["verdict"] == "extend" and (result.candidate_failures or result.candidate_invalid_blocks):
        decision["verdict"] = "revert"                  # a flaky candidate is never kept, so do not spend blocks extending it
        decision["reasons"].append("not extended: the candidate already failed in some blocks")
    if decision["verdict"] == "extend":
        if time.monotonic() > t.deadline:
            return void([timeout + " before the extension"])
        base_dir, cand_dir = t.refresh()
        more = measure.run_blocks(spec, base_dir, cand_dir, limits.extend_to - limits.blocks, t.rng, env, t.adir,
                                  first_block=limits.blocks, deadline=t.deadline, prepare=t.prepare)
        result = measure.combine(result, more)
        fields.update(extended=True, blocks_run=result.blocks_run)
        if result.blocks_run < limits.extend_to:
            return void([timeout + " during the extension"])
        if result.void or len(result.scores) < 2:
            return void(result.warnings or ["too few usable blocks"])
        decision = stats.keep_decision(result.scores, useful, look=2, looks=2, direction=direction)
        if decision["verdict"] == "insufficient":
            return void(decision["reasons"] + ["blocks were voided, so this try cannot reject; edits kept, not a valid try"])
    reasons = list(decision["reasons"])
    valid = True
    if result.candidate_failures or result.candidate_invalid_blocks:
        valid = False
        reasons.append(f"the candidate failed or reported invalid items in {len(set(result.candidate_invalid_blocks))} of "
                       f"{result.blocks_run} blocks (scored as the worst value; a candidate that is flaky is never kept)")
    keep = decision["verdict"] == "keep" and valid
    fields.update(common.blocks_fields(decision, result.scores, useful, decision["alpha"], direction))
    fields.update(blocks=len(result.scores), look=decision["look"], looks=looks, useful=stats.metric_change_pct(useful, direction))
    return {"verdict": "keep" if keep else "revert", "valid": valid, "reasons": reasons, "fields": fields,
            "arm_medians": result.arm_medians, "result": result}


def _decide_items(spec, t: _Try, env) -> dict:
    base_dir, cand_dir = t.refresh()
    pair = measure.run_item_pair(spec, base_dir, cand_dir, t.rng, env, t.adir, deadline=t.deadline, warmup=True, prepare=t.prepare)
    fields = {"extended": False, "blocks": 1, "blocks_run": 1}
    if pair.void:
        return {"verdict": "void", "valid": False, "reasons": pair.reasons, "fields": fields, "arm_medians": None}
    if pair.candidate_failed:
        return {"verdict": "revert", "valid": False, "reasons": pair.reasons, "fields": fields, "arm_medians": None}
    decision = common.items_decision(spec, pair)
    reasons = list(decision["reasons"]) + list(decision["warnings"])
    valid = not pair.candidate_bad
    if pair.candidate_bad:
        reasons.append(f"{len(pair.candidate_bad)} item(s) were invalid in the candidate (scored as the run's worst): "
                       f"{', '.join(pair.candidate_bad[:5])}")
    fields.update({k: decision[k] for k in ("estimate_log", "estimate", "lower", "upper", "p", "level", "includes_useful", "items", "units")})
    fields.update(useful=spec.objective.useful)
    return {"verdict": "keep" if decision["verdict"] == "keep" and valid else "revert", "valid": valid, "reasons": reasons,
            "fields": fields, "arm_medians": pair.arm_medians}


# --- guards ----------------------------------------------------------------------------------------

def _run_command_guards(spec, cand_dir, adir, deadline) -> list:
    """Run each command guard in the candidate arm once (one retry if it fails, for flaky tests); capped by the try's deadline."""
    rows = []
    for g in spec.guards:
        if g.command is None:
            continue
        attempts = []
        for attempt in (1, 2):
            limit = max(1.0, min(spec.limits.command_seconds, deadline - time.monotonic()))
            result = runner.run(g.command, cand_dir, adir / "guards", f"{g.name}-{attempt}", limit,
                                env_extra={"HCA_ITEMS": "dev"}, deny=spec.env.deny)
            attempts.append({"returncode": result.returncode, "timed_out": result.timed_out, "seconds": round(result.seconds, 3),
                             "error_tail": result.error_tail if not result.ok else ""})
            if result.ok or time.monotonic() > deadline:
                break
        rows.append({"name": g.name, "kind": "command", "passed": attempts[-1]["returncode"] == 0 and not attempts[-1]["timed_out"],
                     "attempts": attempts})
    return rows


def _metric_guard(g, medians) -> dict:
    """Candidate median vs baseline median for a guard metric; a metric that is not reported fails closed."""
    base, cand = medians["baseline"].get(g.metric), medians["candidate"].get(g.metric)
    row = {"name": g.name, "kind": "metric", "metric": g.metric, "direction": g.direction,
           "max_regression": g.max_regression, "baseline": base, "candidate": cand, "passed": True, "message": ""}
    if base is None or cand is None:
        row.update(passed=False, message=f"guard {g.name!r}: metric {g.metric!r} was not reported by both arms")
        return row
    bad = cand > base * (1 + g.max_regression) if g.direction == "min" else cand < base * (1 - g.max_regression)
    if bad:
        change = (cand - base) / base * 100 if base else float("inf")
        row.update(passed=False, message=f"guard {g.name!r}: {g.metric} went {base:g} -> {cand:g} ({change:+.1f}%), "
                   f"beyond the allowed {g.max_regression_text} regression")
    return row


# --- recording and output --------------------------------------------------------------------------

def _record(t: _Try, verdict, reasons, valid=False) -> None:
    if t.snap is not None:                              # the snapshot ref appears only now, after the measurement (S19: an arm must not see it)
        try:
            gitops.set_ref(t.root, gitops.attempt_ref(t.rec["n"]), t.snap["sha"])
        except HcaError:
            pass
    t.rec.update(verdict=verdict, valid=valid, reasons=reasons, wall_seconds=round(time.monotonic() - t.started, 3))
    ledger.write_record(t.adir, "verdict.json", t.rec)
    ledger.refresh_verdicts(t.root, t.state, t.spec)


def _apply_keep(t: _Try, reasons):
    """The keep itself. Returns the (possibly changed) verdict: 'keep', or 'void' if the files moved under us or git refused."""
    root, spec, state, rec = t.root, t.spec, t.state, t.rec
    # The measured tree must still be the tree on disk. If anything editable changed during the run (a formatter,
    # the agent's parallel edits, the candidate itself), what was measured is not what would be committed: void,
    # nothing committed, edits left as they are (S5).
    if gitops.working_tree_hash(root, spec, state["last_keep"]) != t.snap["tree"]:
        reasons.append("the editable files changed while the try was being measured, so the measured snapshot is not what is on disk; "
                       "nothing was committed and your edits are untouched")
        return "void"
    sha, old = t.snap["sha"], state["last_keep"]
    final = dict(rec, verdict="keep", valid=True, reasons=list(reasons), commit=sha, confirm="pending",
                 wall_seconds=round(time.monotonic() - t.started, 3))
    common.write_pending(root, state["task"], {"kind": "keep", "n": rec["n"], "snapshot_sha": sha, "last_keep": old, "record": final,
                                               "digest": ledger.verdict_digest(final)})
    try:
        gitops.advance_branch(root, state["branch"], sha, old)
    except HcaError as exc:
        if gitops.branch_tip(root, state["branch"]) == old:
            common.clear_pending(root, state["task"])
        else:
            raise                                       # the branch moved but the index reset failed: reconciliation finishes it
        reasons.append(f"the branch could not be moved to the measured snapshot: {exc}; nothing was kept and your edits are untouched")
        return "void"
    state["last_keep"] = sha
    rec.update(commit=sha, confirm="pending")
    return "keep"


def _finish(t: _Try, verdict, reasons, valid=False, guards=None, note=None) -> int:
    """Keep or restore, write the attempt record and state, print the short verdict, return the exit code.

    CONTRACT NOTE: every `void` (and `interrupted`) leaves the working tree alone and commits nothing, not only the
    two A2.3 names (`insufficient`, `try_seconds`). The old code reverted voids; with replays now refused and counted,
    keeping the edits no longer gives a free re-roll. CONTRACT NOTE: arm directories are verified again after the
    measurement; an arm that changed during the run (a benchmark or guard writing to tracked files, or into the
    sibling arm) voids the try rather than trusting a comparison whose baseline moved.
    """
    root, spec, state, rec, out = t.root, t.spec, t.state, t.rec, t.out
    mode = spec.objective.mode
    reasons = list(reasons)
    action = []
    if verdict == "keep":
        verdict = _apply_keep(t, reasons)
        valid = verdict == "keep"
        if verdict == "keep":
            action.append(f"kept as commit {state['last_keep'][:12]} on {state['branch']}")
    if verdict == "revert":
        result = gitops.revert(root, spec, state["last_keep"])
        action.append(f"reverted the editable paths to {state['last_keep'][:12]}"
                      + (f" (removed {len(result['deleted'])} new file(s))" if result["deleted"] else ""))
    elif verdict in ("void", "interrupted"):
        action.append("your edits are untouched")
    _record(t, verdict, reasons, valid=valid)
    common.clear_pending(root, state["task"])
    arms.prune_unused(root, [state["init_sha"], state.get("last_confirmed"), state["last_keep"]])     # after the records, best effort
    wall = rec["wall_seconds"]
    useful_text = spec.objective.useful_effect
    out(_headline(rec, verdict, mode, wall, spec))
    for reason in reasons:
        out("  - " + reason)
    if verdict == "revert" and rec.get("valid") and rec.get("includes_useful") and rec.get("estimate") is not None:
        out("  " + NO_EVIDENCE.format(interval=_interval_text(rec, mode), useful=useful_text))
    if verdict == "invalid":
        out("  Nothing was measured and your edits are untouched. Fix the above and run `hca try` again."
            if note in ("scope", "replay") else "  Make an edit inside the editable paths, then run `hca try` again.")
    for line in action:
        out("  " + line + ".")
    return {"keep": common.EXIT_KEEP, "revert": common.EXIT_REVERT}.get(verdict, common.EXIT_INVALID)


def _interval_text(rec, mode) -> str:
    """The estimate's two-sided interval in display units, smaller end first ('unbounded' if the data cannot bound an end)."""
    ends = [rec.get("lower"), rec.get("upper")]
    if any(e is None for e in ends):
        return "(unbounded on at least one side)"
    lo, hi = sorted(ends)
    return f"[{ledger.fmt_effect(lo, mode)}, {ledger.fmt_effect(hi, mode)}]"


def _headline(rec, verdict, mode, wall, spec) -> str:
    label = verdict.upper()
    if verdict == "invalid":
        return f"attempt {rec['n']:04d}  INVALID  (no measurement)"
    if verdict == "void":
        return f"attempt {rec['n']:04d}  VOID  (no verdict; {wall:.1f} s)"
    if rec.get("estimate") is None:
        return f"attempt {rec['n']:04d}  {label}  (not measured to a verdict; {wall:.1f} s)"
    p = f", p = {rec['p']:.3g}" if rec.get("p") is not None else ""
    kept = verdict == "keep"
    if mode == "blocks":
        direction, blocks = spec.objective.direction, f"{rec['blocks']} blocks" + (" after extending" if rec.get("extended") else "")
        word = ledger.fmt_change(rec["estimate"], mode, direction, spec.objective.metric)
        head, tail = word[:word.rindex("(")], word[word.rindex("(") + 1:-1]
        bound = ledger.fmt_bound(rec.get("lower"), mode, direction, kept=kept)
        return f"attempt {rec['n']:04d}  {label}  {head}({tail}; {bound}{p}, {blocks}, {wall:.1f} s)"
    return (f"attempt {rec['n']:04d}  {label}  {ledger.fmt_effect(rec['estimate'], mode)} "
            f"(lower bound {ledger.fmt_effect(rec.get('lower'), mode)}{p}, {rec.get('items')} items, {wall:.1f} s)")


def _discard(adir, task, n) -> None:
    """Remove an attempt directory that never got a verdict (a broken baseline arm is not a try). Inside .hca only."""
    import shutil
    ledger.forget_attempt(task, n)
    if adir.parent.name == "attempts" and adir.parent.parent.name == gitops.HCA_DIR:
        shutil.rmtree(adir, ignore_errors=True)
