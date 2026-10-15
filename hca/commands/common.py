"""Pieces shared by init, try and confirm: opening a session, item-mode decisions, intervals, formatting."""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path

from .. import gitops, ledger, paired, stats
from ..core import HcaError, read_json, write_json
from ..spec import load_spec

ITEMS_ALPHA = 0.02          # two-sided, so the one-sided lower bound sits at 1%: the same per-try level as blocks mode
ITEMS_MIN_UNITS = 8
EXIT_KEEP, EXIT_REVERT, EXIT_INVALID, EXIT_USAGE = 0, 1, 2, 3


PENDING_KEEP = "pending-keep.json"


def pending_path(root) -> Path:
    return ledger.hca_dir(root) / PENDING_KEEP


def write_pending(root, data) -> None:
    ledger.hca_dir(root).mkdir(parents=True, exist_ok=True)
    write_json(pending_path(root), data)


def clear_pending(root) -> None:
    pending_path(root).unlink(missing_ok=True)


def open_session(cwd=None, on_branch=True, out=print) -> tuple:
    """(root, spec, state) for the repo that holds `cwd`, checking we are on the task branch.

    Also: refuses repos hca v1 cannot handle, makes `state` agree with the referee store (the store wins, with a
    warning), and finishes or discards a keep that an earlier run was killed in the middle of.
    """
    root = gitops.repo_root(cwd)
    state = ledger.load_state(root)
    spec = load_spec(root / "hca.toml")
    if spec.task.name != state["task"]:
        raise HcaError(f"hca.toml says task {spec.task.name!r} but this session is {state['task']!r}. "
                       "Fix: restore hca.toml (it is frozen), or run `hca init --reset`.")
    gitops.refuse_unsupported(root)
    ledger.reconcile_state(state, state["task"], out)
    reconcile_pending(root, spec, state, out)
    if on_branch and gitops.current_branch(root) != state["branch"]:
        raise HcaError(f"you are on {gitops.current_branch(root)!r} but this session lives on {state['branch']!r}. "
                       f"Fix: `git checkout {state['branch']}`.")
    return root, spec, state


def reconcile_pending(root, spec, state, out=print) -> None:
    """Finish or discard a keep (or a confirm restore) whose process died after writing `.hca/pending-keep.json`.

    CONTRACT NOTE: A2.5 describes the marker for `try`; confirm's reset-to-confirmed commit uses the same marker
    (`kind: restore`) because it has the same window between moving the branch and saving the state.

    The marker is written before the branch moves. If the branch tip is now the snapshot, the move happened: adopt it
    (index reset, state, attempt record). If the tip is still the old keep, it never happened: drop the marker. Anything
    else is left alone for a human, and the HEAD-mismatch message points back here.
    """
    path = pending_path(root)
    if not path.exists():
        return
    try:
        pending = read_json(path)
        sha, old, kind = pending["snapshot_sha"], pending["last_keep"], pending.get("kind", "keep")
    except (ValueError, KeyError, TypeError):
        path.unlink(missing_ok=True)
        out("warning: removed an unreadable .hca/pending-keep.json.")
        return
    tip = gitops.branch_tip(root, state["branch"])
    adir = ledger.attempt_path(root, pending["n"]) if kind == "keep" and "n" in pending else None
    if tip == sha and state["last_keep"] == old:
        gitops.finish_advance(root, sha)
        state["last_keep"] = sha
        if kind == "keep":
            record = dict(pending.get("record") or {})
            record.update(n=pending["n"], verdict="keep", valid=True, commit=sha, confirm="pending")
            record["reasons"] = list(record.get("reasons", [])) + ["recovered: hca was stopped after moving the branch and before writing this record"]
            adir.mkdir(parents=True, exist_ok=True)
            write_json(adir / "verdict.json", record)
        else:
            for a in ledger.read_attempts(root):
                if a.get("confirm") == "pending":
                    ledger.update_attempt(root, a["n"], confirm="unconfirmed")
        ledger.refresh_verdicts(root, state, spec)
        path.unlink(missing_ok=True)
        out(f"warning: finished an interrupted {'keep' if kind == 'keep' else 'confirm reset'}: the branch is at {sha[:12]}.")
    elif tip == old:
        if adir is not None and not (adir / "verdict.json").exists():
            adir.mkdir(parents=True, exist_ok=True)
            record = dict(pending.get("record") or {"n": pending["n"]})
            record.update(verdict="void", valid=False, reasons=["hca was stopped before the keep was applied; nothing was committed"])
            record.pop("commit", None)
            write_json(adir / "verdict.json", record)
            ledger.refresh_verdicts(root, state, spec)
        path.unlink(missing_ok=True)
        out("warning: discarded a pending keep that was never applied.")


def make_rng(seed=None) -> tuple:
    """(Random, seed). The seed is recorded so a try's block order can be replayed; it is not secret."""
    seed = random.SystemRandom().getrandbits(48) if seed is None else int(seed)
    return random.Random(seed), seed


def finite_or(value, fallback=None):
    return value if value is not None and math.isfinite(value) else fallback


def block_interval(scores, level) -> tuple:
    """(lower, upper) log-ratio bounds, each one-sided at `level` (the upper by symmetry). May be +-inf."""
    return stats.walsh_lower_bound(scores, level), -stats.walsh_lower_bound([-s for s in scores], level)


def blocks_fields(decision, scores, useful, level, direction) -> dict:
    """Display fields of a stats.keep_decision as the metric's own % change, plus whether the interval still allows a useful effect.

    `lower` is the change at the lower bound of the direction-adjusted log ratio (the "least improvement"
    end: for a metric to minimise it is the larger, less negative, change); `upper` is the other end.
    """
    lower, upper = block_interval(scores, level)
    return {"estimate_log": decision["estimate"], "estimate": stats.metric_change_pct(decision["estimate"], direction),
            "lower": stats.metric_change_pct(lower, direction) if math.isfinite(lower) else None,
            "upper": stats.metric_change_pct(upper, direction) if math.isfinite(upper) else None,
            "p": decision["p_value"], "level": level,
            "includes_useful": bool(lower < useful <= upper)}


def items_decision(spec, pair, alpha=ITEMS_ALPHA) -> dict:
    """The items-mode keep rule on one ItemPair: paired.decide must promote AND the mean difference >= half the useful effect."""
    useful = spec.objective.useful
    rule = {"alpha": alpha, "comparisons": 1, "min_units": ITEMS_MIN_UNITS, "useful_effect": useful}
    decision = paired.decide([{"id": i, "score": v} for i, v in sorted(pair.candidate.items())],
                             [{"id": i, "score": v} for i, v in sorted(pair.baseline.items())], spec.objective.direction, rule)
    mean = decision["mean_difference"]
    reasons = list(decision["reasons"])
    if mean < 0.5 * useful:
        reasons.append(f"mean difference {mean:+.4g} is below the keep floor {0.5 * useful:.4g} (half the useful effect)")
    low, high = decision["interval"] or (None, None)
    return {"verdict": "keep" if decision["verdict"] == "promote" and mean >= 0.5 * useful else "revert",
            "reasons": reasons, "estimate_log": mean, "estimate": mean, "lower": low, "upper": high, "p": None,
            "level": decision["interval_level"], "items": decision["items"], "units": decision["units"],
            "includes_useful": bool(low is not None and low < useful <= high),
            "warnings": decision["warnings"], "worst_subgroup": decision["worst_subgroup"]}


def slices(item_rows, tag_keys, direction) -> dict:
    """Per-tag-value median item change (the metric's own %). `item_rows`: {id: (baseline, candidate, tags)}.

    Items with a non-positive value in either arm have no log ratio and are skipped (counted).
    """
    groups = {}
    for ident, (base, cand, tags) in sorted(item_rows.items()):
        for key in tag_keys:
            if key not in tags:
                continue
            slot = groups.setdefault(f"{key}={tags[key]}", {"scores": [], "skipped": 0})
            if base is not None and cand is not None and base > 0 and cand > 0:
                slot["scores"].append(stats.log_ratio(base, cand, direction))
            else:
                slot["skipped"] += 1
    return {name: {"items": len(g["scores"]), "skipped": g["skipped"],
                   "median_pct": stats.metric_change_pct(statistics.median(g["scores"]), direction) if g["scores"] else None}
            for name, g in groups.items()}


def block_slices(result, tag_keys, direction) -> dict:
    return slices({i: (v["baseline"], v["candidate"], v["tags"]) for i, v in result.item_medians.items()}, tag_keys, direction)


def pair_slices(pair, tag_keys, direction) -> dict:
    return slices({i: (pair.baseline[i], pair.candidate[i], pair.tags.get(i, {})) for i in pair.baseline}, tag_keys, direction)


def untracked_warning(paths) -> str:
    """One line about untracked files outside the editable paths that were ignored."""
    shown = ", ".join(paths[:3]) + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")
    return f"warning: {len(paths)} untracked file(s) outside editable were ignored: {shown}; gitignore them to silence this."


def tail_text(text, limit=600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def rel(path, root) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)
