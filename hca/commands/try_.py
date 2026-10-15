"""`hca try -m "..."`: measure the agent's uncommitted edits against the last keep, then keep or revert.

The order is fixed by the contract: scope check, builds, guard commands, randomised blocks, the keep
rule (with at most one extension), metric guards, then commit or restore. Every outcome, valid or
not, becomes an attempt record, because the validity rate and the history must not be tidied. Only
the editable paths are ever committed or restored; nothing outside them is touched.

Exit codes: 0 keep, 1 revert, 2 invalid (scope, no change) or void (infra, time), 3 usage errors.
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


def run(args, out=print, cwd=None, seed=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "try", out):
        return _run(args, out, cwd, seed)


def _run(args, out=print, cwd=None, seed=None) -> int:
    message = (getattr(args, "message", None) or "").strip()
    if not message:
        raise HcaError('hca try needs a message. Fix: hca try -m "what changed and why".')
    root, spec, state = common.open_session(cwd)
    started = time.monotonic()
    deadline = started + spec.limits.try_seconds
    mode = spec.objective.mode
    scope = gitops.scope_check(root, spec, state)
    n, adir = ledger.new_attempt(root)
    (adir / "message.txt").write_text(message + "\n", encoding="utf-8")
    ledger.write_record(adir, "scope.json", scope)
    if scope["untracked_outside_editable"]:
        out(common.untracked_warning(scope["untracked_outside_editable"]))
    rng, used_seed = common.make_rng(seed)
    rec = {"n": n, "message": message, "at": ledger.now(), "mode": mode, "unit": "%" if mode == "blocks" else "", "base": state["last_keep"],
           "verdict": None, "valid": False, "reasons": [], "seed": used_seed, "confirm": None}

    if not scope["ok"] or not gitops.editable_changes(scope):
        reasons = [f"{v['path']}: {v['reason']}" for v in scope["violations"]] or \
            ["no changes: there is nothing to measure (the working tree equals the last keep)"]
        (adir / "patch.diff").write_text(gitops.raw_patch(root, state["last_keep"]), encoding="utf-8")
        return _finish(root, spec, state, adir, rec, out, "invalid", reasons, started, revert=False,
                       note="scope" if not scope["ok"] else "empty")

    try:
        baseline = arms.ensure_arm(root, spec, state["last_keep"])
    except HcaError:
        _discard(adir)                                  # a broken baseline is an environment problem, not a try
        raise
    patch, _ = gitops.capture_patch(root, spec, state["last_keep"])
    (adir / "patch.diff").write_text(patch, encoding="utf-8")
    try:
        return _measure_and_decide(root, spec, state, adir, rec, out, baseline, rng, started, deadline, scope, message)
    except BaseException as exc:                        # leave the edits alone, but never leave an attempt without a verdict
        if not (adir / "verdict.json").exists():
            _record(root, spec, state, adir, rec, "void", [f"interrupted or crashed: {type(exc).__name__}: {exc}"], started)
        raise


def _measure_and_decide(root, spec, state, adir, rec, out, baseline, rng, started, deadline, scope, message) -> int:
    mode = spec.objective.mode
    reasons = []
    build = arms.build_candidate(root, spec, adir)
    if build is not None and not build.ok:
        why = "timed out" if build.timed_out else f"exit status {build.returncode}"
        return _finish(root, spec, state, adir, rec, out, "revert", [f"candidate build failed ({why}); last stderr:\n{build.error_tail}"],
                       started, valid=False, revert=True)

    guards = _run_command_guards(spec, root, adir)
    ledger.write_record(adir, "guards.json", guards)
    failed = [g for g in guards if not g["passed"]]
    if failed:
        return _finish(root, spec, state, adir, rec, out, "revert",
                       [f"guard {g['name']!r} failed (after one retry): {g['attempts'][-1]['error_tail'] or 'exit ' + str(g['attempts'][-1]['returncode'])}" for g in failed],
                       started, valid=False, revert=True, guards=guards)

    env = {"HCA_ITEMS": "dev"}
    if mode == "items":
        outcome = _decide_items(spec, baseline, root, rng, env, adir)
    else:
        outcome = _decide_blocks(spec, baseline, root, rng, env, adir, deadline)
    rec.update(outcome["fields"])
    reasons = list(outcome["reasons"])
    verdict, valid = outcome["verdict"], outcome["valid"]

    guard_rows = list(guards)
    medians = outcome.get("arm_medians")
    if medians is not None:
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
    return _finish(root, spec, state, adir, rec, out, verdict, reasons, started, valid=valid, revert=verdict != "keep",
                   guards=guard_rows, scope=scope, message=message)


# --- the two measurement modes ---------------------------------------------------------------------

def _decide_blocks(spec, baseline, root, rng, env, adir, deadline) -> dict:
    limits, useful, direction = spec.limits, spec.objective.useful, spec.objective.direction
    looks = 2 if limits.extend_to > limits.blocks else 1
    fields = {"extended": False, "blocks": 0, "blocks_run": 0}
    timeout = "try_seconds ({}) ran out before the measurement finished".format(limits.try_seconds)

    def void(reasons, _result=None):
        return {"verdict": "void", "valid": False, "reasons": reasons, "fields": fields, "arm_medians": None}

    result = measure.run_blocks(spec, baseline, root, limits.blocks, rng, env, adir, deadline=deadline)
    fields.update(blocks_run=result.blocks_run)
    if result.void or len(result.scores) < 2:
        return void(result.warnings or ["too few usable blocks"], result)
    if result.blocks_run < limits.blocks:
        return void([timeout], result)
    decision = stats.keep_decision(result.scores, useful, look=1, looks=looks, direction=direction)
    if decision["verdict"] == "extend" and (result.candidate_failures or result.candidate_invalid_blocks):
        decision["verdict"] = "revert"                  # a flaky candidate is never kept, so do not spend blocks extending it
        decision["reasons"].append("not extended: the candidate already failed in some blocks")
    if decision["verdict"] == "extend":
        if time.monotonic() > deadline:
            return void([timeout + " before the extension"], result)
        more = measure.run_blocks(spec, baseline, root, limits.extend_to - limits.blocks, rng, env, adir,
                                  first_block=limits.blocks, deadline=deadline)
        result = measure.combine(result, more)
        fields.update(extended=True, blocks_run=result.blocks_run)
        if result.void or len(result.scores) < 2:
            return void(result.warnings or ["too few usable blocks"], result)
        if result.blocks_run < limits.extend_to:
            return void([timeout + " during the extension"], result)
        decision = stats.keep_decision(result.scores, useful, look=2, looks=2, direction=direction)
    reasons = list(decision["reasons"])
    valid = True
    if result.candidate_failures or result.candidate_invalid_blocks:
        valid = False
        reasons.append(f"the candidate failed or reported invalid items in {len(set(result.candidate_invalid_blocks))} of "
                       f"{result.blocks_run} blocks (scored as the worst value; a candidate that is flaky is never kept)")
    keep = decision["verdict"] == "keep" and valid
    fields.update(common.blocks_fields(decision, result.scores, useful, decision["alpha"], direction))
    fields.update(blocks=len(result.scores), look=decision["look"], looks=looks, useful=stats.metric_change_pct(useful, direction))
    if decision["verdict"] == "insufficient":
        reasons.append("the measurement is too small to decide")
    return {"verdict": "keep" if keep else "revert", "valid": valid, "reasons": reasons, "fields": fields,
            "arm_medians": result.arm_medians, "result": result}


def _decide_items(spec, baseline, root, rng, env, adir) -> dict:
    pair = measure.run_item_pair(spec, baseline, root, rng, env, adir)
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

def _run_command_guards(spec, root, adir) -> list:
    """Run each command guard in the candidate arm once (one retry if it fails, for flaky tests)."""
    rows = []
    for g in spec.guards:
        if g.command is None:
            continue
        attempts = []
        for attempt in (1, 2):
            result = runner.run(g.command, root, adir / "guards", f"{g.name}-{attempt}", spec.limits.command_seconds,
                                env_extra={"HCA_ARM": "candidate", "HCA_ITEMS": "dev"}, deny=spec.env.deny)
            attempts.append({"returncode": result.returncode, "timed_out": result.timed_out, "seconds": round(result.seconds, 3),
                             "error_tail": result.error_tail if not result.ok else ""})
            if result.ok:
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

def _record(root, spec, state, adir, rec, verdict, reasons, started, valid=False) -> None:
    rec.update(verdict=verdict, valid=valid, reasons=reasons, wall_seconds=round(time.monotonic() - started, 3))
    ledger.write_record(adir, "verdict.json", rec)
    ledger.refresh_verdicts(root, state, spec)


def _finish(root, spec, state, adir, rec, out, verdict, reasons, started, valid=False, revert=False, guards=None,
            scope=None, message=None, note=None) -> int:
    """Commit or restore, write the attempt record and state, print the short verdict, return the exit code."""
    mode = spec.objective.mode
    action = []
    if verdict == "keep":
        try:
            sha = gitops.keep(root, spec, scope, f"hca keep {rec['n']:04d}: {message.splitlines()[0]}")
        except HcaError as exc:                         # e.g. a pre-commit hook refused: the measurement said keep, the repo said no
            reasons = reasons + [f"commit failed: {exc}"]
            verdict, revert = "revert", True
        else:
            state["last_keep"] = sha
            rec.update(commit=sha, confirm="pending")
            arms.prune_unused(root, [state["init_sha"], state.get("last_confirmed"), sha])
            action.append(f"kept as commit {sha[:12]} on {state['branch']}")
    if revert:
        result = gitops.revert(root, spec, state["last_keep"])
        action.append(f"reverted the editable paths to {state['last_keep'][:12]}"
                      + (f" (removed {len(result['deleted'])} new file(s))" if result["deleted"] else ""))
    _record(root, spec, state, adir, rec, verdict, reasons, started, valid=valid)
    wall = rec["wall_seconds"]
    useful_text = spec.objective.useful_effect
    out(_headline(rec, verdict, mode, wall, spec))
    for reason in reasons:
        out("  - " + reason)
    if verdict == "revert" and rec.get("valid") and rec.get("includes_useful") and rec.get("estimate") is not None:
        out("  " + NO_EVIDENCE.format(interval=_interval_text(rec, mode), useful=useful_text))
    if verdict == "invalid":
        out("  Nothing was measured and your edits are untouched. Fix the above and run `hca try` again."
            if note == "scope" else "  Make an edit inside the editable paths, then run `hca try` again.")
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
    if mode == "blocks":
        direction, blocks = spec.objective.direction, f"{rec['blocks']} blocks" + (" after extending" if rec.get("extended") else "")
        word = ledger.fmt_change(rec["estimate"], mode, direction, spec.objective.metric)
        head, tail = word[:word.rindex("(")], word[word.rindex("(") + 1:-1]
        bound = ledger.fmt_bound(rec.get("lower"), mode, direction)
        return f"attempt {rec['n']:04d}  {label}  {head}({tail}; {bound}{p}, {blocks}, {wall:.1f} s)"
    return (f"attempt {rec['n']:04d}  {label}  {ledger.fmt_effect(rec['estimate'], mode)} "
            f"(lower bound {ledger.fmt_effect(rec.get('lower'), mode)}{p}, {rec.get('items')} items, {wall:.1f} s)")


def _discard(adir) -> None:
    """Remove an attempt directory that never got a verdict (a broken baseline arm is not a try). Inside .hca only."""
    import shutil
    if adir.parent.name == "attempts" and adir.parent.parent.name == gitops.HCA_DIR:
        shutil.rmtree(adir, ignore_errors=True)
