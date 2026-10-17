"""`hca try -m "..."`: measure the agent's uncommitted edits against the last keep, then keep or revert.

The order is fixed by the contract: scope check, snapshot, builds, guard commands, a discarded warm-up run
per arm, randomised blocks, the keep rule (with at most one extension), metric guards, then keep or restore.
The candidate is a *snapshot commit* (the last keep's tree plus the working-tree contents of the changed
editable paths, built in a private index) run in its own worktree beside the baseline's, so both arms are
structurally identical; a keep moves the branch to that very commit, so the committed tree is exactly the
measured tree. Every outcome, valid or not, becomes an attempt record, because the validity rate and the
history must not be tidied. Only the editable paths are ever restored; nothing outside them is touched.

Explore mode (docs/DESIGN-climb-engine.md §3c) adds two kinds of attempt:
- `--explore` screens the edits against the current *parent* (an archive member, or the incumbent) in 5-8 blocks.
  It has its own path, `_decide_screen` and `_finish_screen`, which never reaches `_apply_keep` or `write_pending`:
  a screen ranks and archives a child, it can never move the branch. Afterwards the editable paths are set to the
  next sampled parent.
- `--promote M` restores the editable paths to member M's tree and runs the normal try against the incumbent, on fresh
  blocks. Validity, lineage, taint and replay status come from the referee store and the refs, never from archive.json.

Exit codes: 0 keep, 1 revert, 2 invalid (scope, no change, replay) or void (infra, time, files changed during the run)
or interrupted or refused, 3 usage errors, 4 internal error (cli), 5 a screen recorded (never 0: a screen is not a keep).
"""

from __future__ import annotations

import math
import time

from .. import arms, explore, gitops, ledger, measure, runner, stats
from ..core import HcaError, Refusal
from . import common

NO_EVIDENCE = ("No evidence, not no effect: the interval {interval} still allows the useful effect ({useful}). "
               "More blocks or a lower-noise measurement could tell; this is not proof that the idea fails.")
SCREEN_BOUND_LEVEL = 0.05        # the screen's one-sided display bound; printed only from 5 blocks (2^-4 > 0.05)
CHURN_LINES, CHURN_RATIO = 50, 5  # a snapshot this many changed lines, five times more than with line endings ignored, is churn


def add_arguments(parser) -> None:
    parser.add_argument("-m", "--message", help="what changed and why (required)")
    parser.add_argument("--again", action="store_true",
                        help="measure an edit whose tree is identical to an earlier attempt (recorded, and it counts as a try)")
    parser.add_argument("--idea", help="the IDEAS.md id this attempt tests (e.g. I4); its [kind] is recorded")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--explore", action="store_true",
                       help="screen the edits against the current parent in a few blocks: ranked and archived, never kept")
    group.add_argument("--promote", metavar="M", help="set the editable paths to archive member M and test it against the incumbent")
    parser.add_argument("--tainted", action="store_true", help="allow promoting a member tainted by a failed confirm (recorded)")


def run(args, out=print, cwd=None, seed=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "try", out):
        return _run(args, out, cwd, seed)


class _Try:
    """What the pieces of one try share. `base` is the baseline commit: the last keep, or a screen's parent."""

    def __init__(self, root, spec, state, adir, rec, out, started, deadline, rng, base=None):
        self.root, self.spec, self.state, self.adir, self.rec, self.out = root, spec, state, adir, rec, out
        self.rng = rng
        self.started, self.deadline = started, deadline
        self.base = base or state["last_keep"]
        self.snap = None
        self.cand_dir = None
        self.store_extra = {}
        # Before every objective run an arm is cleaned of untracked files its build did not make (NEW-3: a disk cache
        # written by the candidate must not survive from one run to the next, the warm-up included).
        self.prepare = {
            "baseline": lambda: arms.prepare_run(root, spec, self.base, "baseline", warn=out),
            "candidate": lambda: arms.prepare_run(root, spec, self.snap["sha"], "candidate", logs=adir, warn=out)}

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def refresh(self):
        """Both arm directories, verified right now (a tampered one is recreated with a warning)."""
        base = arms.ensure_arm(self.root, self.spec, self.base, "baseline", warn=self.out)
        cand = arms.ensure_arm(self.root, self.spec, self.snap["sha"], "candidate", logs=self.adir, warn=self.out)
        return base, cand


def _run(args, out=print, cwd=None, seed=None) -> int:
    message = (getattr(args, "message", None) or "").strip()
    if not message:
        raise HcaError('hca try needs a message. Fix: hca try -m "what changed and why".')
    again = bool(getattr(args, "again", False))
    kind = "screen" if getattr(args, "explore", False) else "promote" if getattr(args, "promote", None) is not None else "try"
    root, spec, state = common.open_session(cwd, out=out)
    if kind != "try":
        gitops.ignore_notebook(True)
    task = state["task"]
    started = time.monotonic()
    deadline = started + spec.limits.try_seconds
    mode = spec.objective.mode
    if kind != "try" and mode != "blocks":
        raise HcaError("explore mode (--explore, --promote) is blocks mode only for now: items mode has no screen statistic yet. "
                       "Fix: use plain `hca try`.")
    promoted = _restore_member(root, spec, state, args) if kind == "promote" else None
    parent, base = explore.parent_base(root, state) if kind == "screen" else (None, state["last_keep"])
    sampling = (state.get("explore") or {}).get("sampling")
    scope = gitops.scope_check(root, spec, state, base)
    store = ledger.load_store(task) or {}
    for problem in gitops.verify_history(root, spec, state["init_sha"], state["last_keep"], store.get("frozen_blobs") or state.get("frozen_blobs") or {}):
        scope["violations"].append({"path": "(history)", "reason": problem})
        scope["ok"] = False
    n, adir = ledger.new_attempt(root, task)
    ledger.note_attempt(task, n, again=again, kind=kind)
    (adir / "message.txt").write_text(message + "\n", encoding="utf-8")
    ledger.write_record(adir, "scope.json", scope)
    # Each try and each promotion is a 1% test, so the warning counts both; screens cannot keep and do not count.
    promotions = ledger.store_counts(task)["promote"]
    tries = ledger.tries_used(root, task) + promotions
    if kind != "screen" and tries > spec.objective.max_tries:
        what = f"try {tries}" if not promotions else f"test {tries} (tries + promotions)"
        out(f"warning: this is {what}, past max_tries ({spec.objective.max_tries}). Every extra try adds to the chance of a false keep "
            "(about 1% each); `hca confirm` is the check, and the report says how many keeps to expect from chance alone.")
    staged = gitops.staged_outside_editable(root, spec)
    if staged:
        out(f"warning: your index has staged changes outside editable ({', '.join(staged[:3])}{' ...' if len(staged) > 3 else ''}); "
            "HCA ignores the index (a keep resets it).")
    if scope["untracked_outside_editable"]:
        out(common.untracked_warning(scope["untracked_outside_editable"]))
    rng, used_seed = common.make_rng(seed)
    rec = {"n": n, "message": message, "at": ledger.now(), "mode": mode, "unit": "%" if mode == "blocks" else "", "base": base,
           "verdict": None, "valid": False, "reasons": [], "seed": used_seed, "confirm": None, "again": again, "kind": kind}
    idea = getattr(args, "idea", None)
    if idea:
        rec.update(idea=idea, idea_kind=explore.idea_kind(root, idea))
    if explore.backlog(root):
        rec["backlog"] = explore.backlog(root)
    previous = promoted.pop("previous") if promoted else None
    if promoted:
        rec["explore"] = promoted
    t = _Try(root, spec, state, adir, rec, out, started, deadline, rng, base)
    t.previous = previous

    changes = gitops.editable_changes(scope)
    # A screen with no edits while the working tree is at an archive member re-screens that member against its own parent,
    # pooled with its earlier blocks (the promotion hint asks for this on noisy targets).
    rescreen = explore.member(root, state, parent, what="parent") if kind == "screen" and scope["ok"] and not changes and parent else None
    if rescreen and ledger.kind_of(rescreen) != "screen":
        rescreen = None
    if rescreen:
        parent, t.base = rescreen.get("parent"), rescreen["base"]
        rec["base"] = t.base
    if not scope["ok"] or not (changes or rescreen):
        reasons = [f"{v['path']}: {v['reason']}" for v in scope["violations"]] or \
            [f"no changes: there is nothing to measure (the working tree equals {'the parent' if kind == 'screen' else 'the last keep'})"]
        (adir / "patch.diff").write_text(gitops.raw_patch(root, t.base), encoding="utf-8")
        return _end(t, "invalid", reasons, note="scope" if not scope["ok"] else "empty")

    try:
        arms.ensure_arm(root, spec, t.base, "baseline", timeout=t.remaining(), warn=out)
    except arms.ArmBuildError as exc:
        if exc.result is not None and exc.result.timed_out and t.remaining() <= 0:
            return _end(t, "void", [f"try_seconds ({spec.limits.try_seconds}) ran out while building the baseline"])
        _undo_promotion(t)
        _discard(adir, task, n)                         # a broken baseline is an environment problem, not a try
        raise
    except HcaError:
        _undo_promotion(t)
        _discard(adir, task, n)
        raise
    first_line = message.splitlines()[0]
    if rescreen:
        t.snap = {"sha": rescreen["sha"], "tree": rescreen["tree"], "paths": []}
    else:
        t.snap = gitops.snapshot(root, spec, t.base, f"hca {'screen' if kind == 'screen' else 'keep'} {n:04d}: {first_line}", changes)
    rec.update(tree=t.snap["tree"], snapshot=t.snap["sha"])
    patch = gitops.patch_between(root, t.base, t.snap["sha"])
    (adir / "patch.diff").write_text(patch, encoding="utf-8")
    rec["review_pointers"] = t.store_extra["pointers"] = explore.pointers(patch)
    _churn_warning(t)
    if t.snap["tree"] == gitops.tree_of(root, t.base):
        return _end(t, "invalid", ["no changes: the editable files equal the last keep once build and cache files are ignored"], note="empty")
    if kind != "try":
        # B3: what is measured must be what is on disk. The change list is computed against the base, and this assert is the
        # second defence (a promotion must also measure exactly the member's tree).
        if gitops.working_tree_hash(root, spec, t.base) != t.snap["tree"] or (promoted and t.snap["tree"] != promoted["tree"]):
            return _end(t, "invalid", ["the snapshot to be measured differs from the editable files on disk; nothing was measured. "
                                       "Fix: report this as an hca bug (base threading)"], note="scope")
    if kind == "screen":
        pooled = _pooled(task, t.snap["tree"], t.base)
        ledger.note_attempt(task, n, tree=t.snap["tree"])
        # The referee store keeps whether the sampler chose this parent: True for an override (`next --parent --why`), False
        # for a sampled parent, None when the sampler was not used (the first screen, or right after a promotion).
        sampled = sampling is not None and sampling.get("override") != "promote"
        t.store_extra.update(parent=parent, override=(sampling.get("parent_p", 0) is None) if sampled else None,
                             why=(sampling or {}).get("why"))
        rec["explore"] = {"parent": parent, "sampling": sampling, "pooled_from": [p["n"] for p in pooled]}
        try:
            return _screen(t, [s for p in pooled for s in p.get("scores", [])])
        except BaseException as exc:
            _interrupted(t, exc)
            raise
    # CONTRACT NOTE: a refused replay is recorded as an `invalid` attempt (it counts toward tries and max_tries), so
    # hammering `hca try` on the same edit shows up in the validity rate instead of vanishing. Screens of the same tree
    # are not prior tests (B1): a member can be promoted right after its screen, but not after a try of its tree.
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
        _interrupted(t, exc)
        raise


def _interrupted(t: _Try, exc) -> None:
    if not (t.adir / "verdict.json").exists():
        if isinstance(exc, KeyboardInterrupt):
            _record(t, "interrupted", ["interrupted (Ctrl-C or a termination signal) while the try was running; your edits are untouched"])
            t.out(f"attempt {t.rec['n']:04d}  INTERRUPTED  (no verdict; your edits are untouched)")
        else:
            _record(t, "void", [f"crashed: {type(exc).__name__}: {exc}"])
        arms.prune_unused(t.root, explore.arm_shas(t.state))


def _churn_warning(t: _Try) -> None:
    """Warn when whole-file churn dwarfs the edit (a CRLF rewrite turns a 6-line edit into hundreds of changed lines)."""
    churn = sum(gitops.numstat(t.root, t.spec, t.base, t.snap["sha"]).values())
    if churn < CHURN_LINES:
        return
    real = sum(gitops.numstat(t.root, t.spec, t.base, t.snap["sha"], ignore_eol=True).values())
    if churn > CHURN_RATIO * max(real, 1):
        t.rec["churn"] = {"lines": churn, "ignoring_line_endings": real}
        t.out(f"warning: this snapshot changes {churn} lines, but only {real} once line endings are ignored: a whole-file rewrite "
              "(CRLF?) is churning the diff. The measurement is unaffected, but patches, divergence and the overlap check read it as "
              "a huge change. Fix: normalise line endings (your editor's setting, or `* text=auto eol=lf` in .gitattributes) and save again.")


def _end(t: _Try, verdict, reasons, note=None) -> int:
    """An early invalid or void ends the attempt on its own kind's path (a screen never touches _finish)."""
    return (_finish_screen if t.rec["kind"] == "screen" else _finish)(t, verdict, reasons, note=note)


def _undo_promotion(t: _Try) -> str | None:
    """A promotion that measured nothing (refused, invalid, or a broken baseline) puts the editable paths and the explore parent
    back where `--promote` found them, so no message ever claims "untouched" over files hca overwrote. None if not a promotion."""
    if t.previous is None:
        return None
    gitops.revert(t.root, t.spec, t.previous["base"])
    t.state["explore"] = t.previous["explore"]
    ledger.save_state(t.root, t.state)
    return "the editable paths were put back where they were before --promote"


def _restore_member(root, spec, state, args) -> dict:
    """`--promote M`: check M from the store and the refs (N3), refuse over unscreened edits, then set the editable paths to M."""
    entry = explore.member(root, state, args.promote, allow_tainted=bool(getattr(args, "tainted", False)))
    m = int(args.promote)
    _, here = explore.parent_base(root, state)
    if gitops.working_tree_hash(root, spec, here) not in (gitops.tree_of(root, here), entry["tree"]):
        raise Refusal(f"the editable paths hold edits that were never screened or tried; --promote would overwrite them with member "
                      f"{m:04d}. Fix: screen them (`hca try --explore`), or undo them.")
    previous = {"base": here, "explore": state.get("explore")}       # where a promotion that measures nothing puts things back
    gitops.revert(root, spec, entry["sha"])
    state["explore"] = {"parent": m, "sampling": {"override": "promote", "parent": m, "parent_p": None}}
    ledger.save_state(root, state)
    task = state["task"]
    chain = explore.lineage(task, m)
    by_n = explore.entries(task)
    return {"previous": previous,
            "member": m, "tree": entry["tree"], "lineage": chain, "dropped_keeps": explore.dropped_keeps(root, state, entry["sha"]),
            "tainted": bool(entry.get("tainted")), "lineage_pointers": sorted({w for i in chain for w in by_n.get(i, {}).get("pointers", [])}),
            "screens": sum(1 for i in chain if ledger.kind_of(by_n.get(i, {})) == "screen")}


def _pooled(task, tree, base) -> list:
    """The latest valid screen of this very tree against this very parent (or nothing): a re-screen pools its blocks. Its scores
    already include every earlier screen it pooled, so taking more than the latest would count blocks twice."""
    same = [e for n, e in sorted(explore.entries(task).items())
            if ledger.kind_of(e) == "screen" and e.get("valid") and e.get("tree") == tree and e.get("base") == base and e.get("scores")]
    return same[-1:]


def _build_and_guard(t: _Try):
    """Build the candidate arm and run the command guards. Returns (guards, None), or (None, (verdict, reasons, kwargs)) to stop."""
    root, spec, adir, out = t.root, t.spec, t.adir, t.out
    try:
        t.cand_dir = arms.ensure_arm(root, spec, t.snap["sha"], "candidate", logs=adir, timeout=t.remaining(), warn=out)
    except arms.ArmBuildError as exc:
        if t.remaining() <= 0:
            return None, ("void", [f"try_seconds ({spec.limits.try_seconds}) ran out while building the candidate"], {})
        why = "timed out" if exc.result.timed_out else f"exit status {exc.result.returncode}"
        return None, ("revert", [f"candidate build failed ({why}); last stderr:\n{exc.result.error_tail}"], {"valid": False})
    if t.remaining() <= 0:
        return None, ("void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the build"], {})

    guards = _run_command_guards(spec, t.cand_dir, adir, t.deadline)
    ledger.write_record(adir, "guards.json", guards)
    failed = [g for g in guards if not g["passed"]]
    if failed:
        if t.remaining() <= 0:
            return None, ("void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the guard commands"], {"guards": guards})
        return None, ("revert",
                      [f"guard {g['name']!r} failed (after one retry): {g['attempts'][-1]['error_tail'] or 'exit ' + str(g['attempts'][-1]['returncode'])}" for g in failed],
                      {"valid": False, "guards": guards})
    if t.remaining() <= 0:
        return None, ("void", [f"try_seconds ({spec.limits.try_seconds}) ran out during the guard commands"], {"guards": guards})
    return guards, None


def _post_checks(t: _Try, outcome, guards) -> tuple:
    """Arm verification and metric guards after the blocks. Returns (verdict, valid, reasons, guard rows)."""
    root, spec, adir, rec = t.root, t.spec, t.adir, t.rec
    rec.update(outcome["fields"])
    reasons = list(outcome["reasons"])
    verdict, valid = outcome["verdict"], outcome["valid"]

    # Both arms must still be exactly their commits after the run: a benchmark or candidate that wrote into a
    # worktree (the baseline's included) changed what was compared.
    for sha, label in ((t.base, "baseline"), (t.snap["sha"], "candidate")):
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
    return verdict, valid, reasons, guard_rows


def _measure_and_decide(t: _Try) -> int:
    guards, early = _build_and_guard(t)
    if early:
        return _finish(t, early[0], early[1], **early[2])
    env = {"HCA_ITEMS": "dev"}
    if t.spec.objective.mode == "items":
        outcome = _decide_items(t.spec, t, env)
    else:
        outcome = _decide_blocks(t.spec, t, env)
    verdict, valid, reasons, guard_rows = _post_checks(t, outcome, guards)
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


# --- screens: ranked and archived, never kept ---------------------------------------------------------

def _screen(t: _Try, pooled) -> int:
    guards, early = _build_and_guard(t)
    if early:
        return _finish_screen(t, early[0], early[1], **early[2])
    outcome = _decide_screen(t.spec, t, {"HCA_ITEMS": "dev"}, pooled)
    verdict, valid, reasons, guard_rows = _post_checks(t, outcome, guards)
    return _finish_screen(t, verdict, reasons, valid=valid, guards=guard_rows)


def _decide_screen(spec, t: _Try, env, pooled=()) -> dict:
    """n_s blocks of the child against its parent: a Hodges-Lehmann estimate and its standard error, no test, no floor.

    The statistic only ranks. A one-sided 95% bound is reported from 5 blocks on (below that 2^-n > 0.05, S7). Earlier
    blocks of the same tree against the same parent are pooled (a re-screen).
    """
    n_s, direction = explore.screen_blocks(t.state, spec), spec.objective.direction
    fields = {"extended": False, "blocks": 0, "blocks_run": 0}

    def void(reasons):
        return {"verdict": "void", "valid": False, "reasons": reasons, "fields": fields, "arm_medians": None}

    base_dir, cand_dir = t.refresh()
    result = measure.run_blocks(spec, base_dir, cand_dir, n_s, t.rng, env, t.adir, deadline=t.deadline, warmup=True, prepare=t.prepare)
    fields.update(blocks_run=result.blocks_run)
    if result.blocks_run < n_s:
        return void([f"try_seconds ({spec.limits.try_seconds}) ran out before the screen finished"])
    if result.void or len(result.scores) < 2:
        return void(result.warnings or ["too few usable blocks"])
    scores = list(pooled) + list(result.scores)
    estimate = stats.hodges_lehmann(scores)
    se = stats.robust_sd(scores) / math.sqrt(len(scores))
    bound = stats.walsh_lower_bound(scores, SCREEN_BOUND_LEVEL) if len(scores) >= 5 else None
    bound = bound if bound is not None and math.isfinite(bound) else None
    valid = not (result.candidate_failures or result.candidate_invalid_blocks)
    reasons = [] if valid else ["the candidate failed or reported invalid items in some blocks: not a test of the idea"]
    fields.update(blocks=len(scores), estimate_log=estimate, estimate=stats.metric_change_pct(estimate, direction),
                  lower=stats.metric_change_pct(bound, direction) if bound is not None else None, p=None,
                  screen={"blocks": len(scores), "new_blocks": len(result.scores), "estimate_log": estimate, "se": se,
                          "bound95": bound, "scores": scores})
    return {"verdict": "screened" if valid else "invalid", "valid": valid, "reasons": reasons, "fields": fields,
            "arm_medians": result.arm_medians}


def _finish_screen(t: _Try, verdict, reasons, valid=False, guards=None, note=None) -> int:
    """Record a screen, archive it, set the editable paths to the next sampled parent, print. Never keeps, never writes a
    pending keep, never moves the branch: there is no call to `_apply_keep` or `common.write_pending` on this path (S4)."""
    root, spec, state, rec, out = t.root, t.spec, t.state, t.rec, t.out
    reasons = list(reasons)
    if verdict != "screened" or not valid:
        verdict = verdict if verdict in ("void", "interrupted") else "invalid"
    elif gitops.working_tree_hash(root, spec, t.base) != t.snap["tree"]:
        verdict = "void"
        reasons.append("the editable files changed while the screen was measured; nothing was archived and your edits are untouched")
    lines = []
    if verdict == "screened":
        screen = rec["screen"]
        changed = gitops.numstat(root, spec, t.base, t.snap["sha"])
        parent_lines = sum(gitops.numstat(root, spec, gitops.EMPTY_TREE, t.base).values()) or 1
        vs_incumbent = sum(gitops.numstat(root, spec, state["last_keep"], t.snap["sha"]).values())
        module = explore.module_of(changed)
        rec["explore"].update(module=module, divergence=round(sum(changed.values()) / parent_lines, 4), lines_vs_incumbent=vs_incumbent)
        t.store_extra.update(module=module, file=explore.file_of(changed), scores=screen["scores"], se=screen["se"], blocks=screen["blocks"])
        rec["class"] = "SCREENED"
        lines.append(f"  divergence {100 * rec['explore']['divergence']:.0f}% of the parent's editable lines ({vs_incumbent} lines vs the incumbent)"
                     f" · review pointers: {', '.join(rec['review_pointers']) or 'none'}")
    else:
        rec["class"] = "INVALID"
    _record(t, verdict, reasons, valid=verdict == "screened")
    if verdict == "screened":
        lines += _archive_and_move(t)
    arms.prune_unused(root, explore.arm_shas(state))
    out(_screen_headline(t, verdict))
    for reason in reasons:
        out("  - " + reason)
    if verdict == "invalid":
        out("  Nothing was archived and your edits are untouched." if note != "empty" else
            "  Make an edit inside the editable paths (from the parent), then run `hca try --explore` again.")
    elif verdict == "void":
        out("  Your edits are untouched.")
    for line in lines:
        out(line)
    return common.EXIT_SCREEN if verdict == "screened" else common.EXIT_INVALID


def _archive_and_move(t: _Try) -> list:
    """Admit the screened child to the archive, draw the next parent (its own RNG stream) and restore the editable paths to it."""
    root, spec, state, n = t.root, t.spec, t.state, t.rec["n"]
    archive = explore.build(root, spec, state, explore.climb_seed(state))
    node = archive["nodes"][n]
    cell = node.get("cell", node["module"])
    elite = archive["cells"].get(cell, {}).get("elite") == n
    lines = [f"  archive    {'new elite of' if elite else 'not the elite of'} [{cell}] · lead vs incumbent: "
             f"raw {100 * (node['f_raw'] - archive['f_inc']):+.1f} pp, ranked {100 * (node['f_rank'] - archive['f_inc']):+.1f} pp"]
    sampling = explore.sample(archive, ledger.store_counts(state["task"])["screen"], archive["seed"])
    parent = None if sampling["parent"] == archive["incumbent"] else sampling["parent"]
    target = state["last_keep"] if parent is None else archive["nodes"][parent]["sha"]
    gitops.revert(root, spec, target)
    state["explore"] = {"parent": parent, "sampling": sampling}
    ledger.save_state(root, state)
    explore.write_cache(root, archive)
    who = "the incumbent" if parent is None else f"{parent:04d}"
    lines.append(f"  next       parent {who} (p = {sampling['parent_p']:.2f}); editable paths set to it")
    return lines


def _screen_headline(t: _Try, verdict) -> str:
    rec, spec = t.rec, t.spec
    if verdict != "screened":
        return f"screen {rec['n']:04d}  {verdict.upper()}  (not archived)"
    direction, screen = spec.objective.direction, rec["screen"]
    parent = rec["explore"]["parent"]
    bound = "" if screen["bound95"] is None else \
        f"; {ledger.fmt_bound(stats.metric_change_pct(screen['bound95'], direction), 'blocks', direction)} at 95%"
    pooled = f", {screen['blocks'] - screen['new_blocks']} pooled" if screen["blocks"] > screen["new_blocks"] else ""
    return (f"screen {rec['n']:04d}  {spec.objective.metric} {ledger.fmt_effect(rec['estimate'], 'blocks')} vs parent "
            f"{'the incumbent' if parent is None else format(parent, '04d')} ({screen['blocks']} blocks{pooled}, {rec['wall_seconds']:.1f} s{bound}) "
            "· a screen, not evidence")


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
    # The store entry gets the outcome too: counts, members, lineage and fitness are read from here, never from verdict.json.
    ledger.update_entry(t.state["task"], t.rec["n"], verdict=verdict, valid=valid, base=t.base, sha=t.snap["sha"] if t.snap else None,
                        estimate_log=t.rec.get("estimate_log"), wall_seconds=t.rec["wall_seconds"], **t.store_extra)
    ledger.write_record(t.adir, "verdict.json", t.rec)
    ledger.refresh_verdicts(t.root, t.state, t.spec)


def _apply_keep(t: _Try, reasons):
    """The keep itself. Returns the (possibly changed) verdict: 'keep', or 'void' if the files moved under us or git refused."""
    root, spec, state, rec = t.root, t.spec, t.state, t.rec
    if rec.get("kind") == "screen":                     # unreachable by construction (S4); a screen must never get this far
        raise HcaError("internal: a screen reached the keep")
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
    promoted = rec.get("kind") == "promote"
    if verdict in ("keep", "revert") and state.get("explore") is not None:
        state["explore"] = {"parent": None, "sampling": None}      # the working tree is at the incumbent again
    elif verdict == "invalid" and promoted:
        action.append(_undo_promotion(t))
    elif verdict in ("void", "interrupted"):
        action.append(f"the editable paths are at member {rec['explore']['member']:04d}" if promoted else "your edits are untouched")
    if rec.get("kind") == "promote":
        t.store_extra.update(member=rec["explore"]["member"], dropped_keeps=rec["explore"]["dropped_keeps"],
                             tainted_member=rec["explore"]["tainted"])
    _record(t, verdict, reasons, valid=valid)
    common.clear_pending(root, state["task"])
    arms.prune_unused(root, explore.arm_shas(state))     # after the records, best effort
    wall = rec["wall_seconds"]
    useful_text = spec.objective.useful_effect
    out(_headline(rec, verdict, mode, wall, spec))
    for reason in reasons:
        out("  - " + reason)
    if verdict == "revert" and rec.get("valid") and rec.get("includes_useful") and rec.get("estimate") is not None:
        out("  " + NO_EVIDENCE.format(interval=_interval_text(rec, mode), useful=useful_text))
    if verdict == "invalid" and promoted:
        out("  Nothing was measured. Fix the above and promote again.")
    elif verdict == "invalid":
        out("  Nothing was measured and your edits are untouched. Fix the above and run `hca try` again."
            if note in ("scope", "replay") else "  Make an edit inside the editable paths, then run `hca try` again.")
    if rec.get("kind") == "promote":
        out("  " + _promotion_line(rec["explore"]))
    for line in action:
        out("  " + line + ".")
    return {"keep": common.EXIT_KEEP, "revert": common.EXIT_REVERT}.get(verdict, common.EXIT_INVALID)


def _promotion_line(info) -> str:
    dropped = ", ".join(f"keep {k:04d}" for k in info["dropped_keeps"]) or "none"
    return (f"promoted member {info['member']:04d} (lineage {', '.join(format(i, '04d') for i in info['lineage'])}; {info['screens']} screen(s); "
            f"review pointers over the lineage: {', '.join(info['lineage_pointers']) or 'none'})"
            + (" · TAINTED member, promoted with --tainted" if info["tainted"] else "")
            + f"\n  dropped keeps: {dropped} · fresh blocks; the screens' estimates were selected and are not reused")


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
