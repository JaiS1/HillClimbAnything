"""`hca confirm`: re-measure the cumulative change on data the agent has not been tuning against.

Every keep is a one-sided test at a small alpha, but many tries still accumulate false keeps and
winner's-curse estimates. Confirm measures the original baseline (init sha) against the last keep
with fresh blocks (and held-out items when a held-out set exists), at a single look. Both arms are
git worktrees at those commits, never the working tree, which may hold uncommitted edits. The agent
sees only pass/fail, the estimate and the bound; everything else goes to the referee store. Confirms
are capped so the held-out set cannot quietly become a dev set.

Exit codes: 0 pass, 1 fail (the branch is reset to the last confirmed commit), 2 inconclusive, 3 usage.
"""

from __future__ import annotations

import shutil
import time

from .. import arms, gitops, ledger, measure, stats
from ..core import HcaError
from . import common

ALPHA = 0.01


def add_arguments(parser) -> None:
    pass


def run(args=None, out=print, cwd=None, seed=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "confirm", out):
        return _run(args, out, cwd, seed)


def _run(args=None, out=print, cwd=None, seed=None) -> int:
    root, spec, state = common.open_session(cwd)
    task, mode = state["task"], spec.objective.mode
    if gitops.head(root) != state["last_keep"]:
        raise HcaError(f"HEAD is not the last keep ({state['last_keep'][:12]}). Fix: do not commit on the hca branch; "
                       "`git checkout` the branch tip or run `hca status`.")
    if state["last_keep"] == state["init_sha"]:
        out("Nothing to confirm yet: no change has been kept. Run `hca try` first.")
        return 0
    if state.get("last_confirmed") == state["last_keep"]:
        out(f"Already confirmed at {state['last_keep'][:12]}; nothing new to measure.")
        return 0
    used = ledger.confirms_used(state, task)
    if used >= spec.limits.confirms:
        raise HcaError(f"the confirm cap is reached ({used} of {spec.limits.confirms} used). Fix: raise [limits] confirms in hca.toml "
                       "and re-init, or stop here: more confirms would turn the held-out set into a dev set.")
    number = used + 1
    started = time.monotonic()
    baseline = arms.ensure_arm(root, spec, state["init_sha"])
    candidate = arms.ensure_arm(root, spec, state["last_keep"])
    held = ledger.heldout_dir(task)
    env = {"HCA_ITEMS": "heldout", "HCA_HELDOUT_DIR": str(held)} if held else {"HCA_ITEMS": "dev"}
    store = ledger.store_dir(task) / "confirms" / f"{number:02d}"
    if store.exists():
        shutil.rmtree(store)                           # inside the referee store; a stale directory from a crashed confirm
    rng, used_seed = common.make_rng(seed)
    useful = spec.objective.useful

    record = {"n": number, "at": ledger.now(), "init_sha": state["init_sha"], "last_keep": state["last_keep"], "heldout": bool(held),
              "mode": mode, "alpha": ALPHA, "seed": used_seed}
    if mode == "items":
        pair = measure.run_item_pair(spec, baseline, candidate, rng, env, store)
        if pair.void or pair.candidate_failed:
            outcome, detail = "void", {"reasons": pair.reasons}
        else:
            d = common.items_decision(spec, pair, alpha=2 * ALPHA)
            ok = d["verdict"] == "keep" and not pair.candidate_bad
            outcome = "pass" if ok else "fail"
            detail = {"reasons": d["reasons"], "estimate": d["estimate"], "lower": d["lower"], "upper": d["upper"], "items": d["items"],
                      "candidate_invalid_items": pair.candidate_bad, "arm_medians": pair.arm_medians,
                      "per_item": {i: {"baseline": pair.baseline[i], "candidate": pair.candidate[i]} for i in pair.baseline},
                      "slices": common.pair_slices(pair, spec.mechanism.slices, spec.objective.direction)}
            record["blocks"] = 1
    else:
        n = spec.limits.extend_to if spec.limits.extend_to > 0 else spec.limits.blocks
        result = measure.run_blocks(spec, baseline, candidate, n, rng, env, store)
        if result.void or len(result.scores) < 2:
            outcome, detail = "void", {"reasons": result.warnings}
        else:
            decision = stats.keep_decision(result.scores, useful, alpha=ALPHA, look=1, looks=1, direction=spec.objective.direction)
            clean = not (result.candidate_failures or result.candidate_invalid_blocks)
            outcome = "pass" if decision["verdict"] == "keep" and clean else "fail"
            fields = common.blocks_fields(decision, result.scores, useful, ALPHA, spec.objective.direction)
            reasons = list(decision["reasons"]) + ([] if clean else ["the candidate failed or reported invalid items in some blocks"])
            detail = {**fields, "reasons": reasons, "p": decision["p_value"], "scores": result.scores,
                      "arm_medians": result.arm_medians,
                      "slices": common.block_slices(result, spec.mechanism.slices, spec.objective.direction)}
            record["blocks"] = len(result.scores)
    record.update(outcome=outcome, wall_seconds=round(time.monotonic() - started, 3), **detail)
    ledger.append_confirm(task, record)                # full detail lives here only
    state["confirms_used"] = used + 1

    if outcome == "void":
        ledger.refresh_verdicts(root, state, spec)
        out(f"CONFIRM {number}: INCONCLUSIVE - the measurement could not be completed (see the referee store). "
            f"It counts against the {spec.limits.confirms} confirms.")
        return common.EXIT_INVALID
    shown = {"estimate": detail.get("estimate"), "lower": detail.get("lower")}
    direction = spec.objective.direction
    text = (f"estimate {ledger.fmt_change(shown['estimate'], mode, direction, spec.objective.metric)} vs the original, "
            f"{ledger.fmt_bound(shown['lower'], mode, direction)}")
    attempts = ledger.read_attempts(root)
    if outcome == "pass":
        state["last_confirmed"] = state["last_keep"]
        state["last_confirmed_result"] = {"sha": state["last_keep"], "n": number, "estimate": detail["estimate"],
                                          "estimate_log": detail.get("estimate_log", detail["estimate"]), "lower": detail["lower"],
                                          "blocks": record.get("blocks", 1), "heldout": bool(held)}
        for a in attempts:
            if a.get("confirm") == "pending":
                ledger.update_attempt(root, a["n"], confirm="confirmed")
        arms.prune_unused(root, [state["init_sha"], state["last_confirmed"], state["last_keep"]])
        ledger.refresh_verdicts(root, state, spec)
        out(f"CONFIRM {number}: PASS - {text} ({used + 1} of {spec.limits.confirms} confirms used).")
        return 0

    target = state.get("last_confirmed") or state["init_sha"]
    dirty_patch, _ = gitops.capture_patch(root, spec, state["last_keep"])
    if dirty_patch.strip():
        saved = ledger.hca_dir(root) / f"discarded-by-confirm-{number:02d}.diff"
        saved.write_text(dirty_patch, encoding="utf-8")
    sha = gitops.restore_to(root, spec, target, "hca revert to confirmed")
    state["last_keep"] = sha
    for a in attempts:
        if a.get("confirm") == "pending":
            ledger.update_attempt(root, a["n"], confirm="unconfirmed")
    arms.prune_unused(root, [state["init_sha"], state.get("last_confirmed"), state["last_keep"]])
    ledger.refresh_verdicts(root, state, spec)
    out(f"CONFIRM {number}: FAIL - {text}. The editable paths were reset to "
        f"{'the last confirmed commit' if state.get('last_confirmed') else 'the original'} ({target[:12]}) as commit {sha[:12]}; "
        f"the keeps in between are marked unconfirmed ({used + 1} of {spec.limits.confirms} confirms used)."
        + (f" Your uncommitted edits were saved to .hca/discarded-by-confirm-{number:02d}.diff." if dirty_patch.strip() else ""))
    return common.EXIT_REVERT
