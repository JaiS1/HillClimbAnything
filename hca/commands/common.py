"""Pieces shared by init, try and confirm: opening a session, item-mode decisions, intervals, formatting."""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path

from .. import explore, gitops, ledger, paired, stats
from ..core import HcaError, Refusal, read_json, write_json
from ..spec import load_spec

ITEMS_ALPHA = 0.02          # two-sided, so the one-sided lower bound sits at 1%: the same per-try level as blocks mode
ITEMS_MIN_UNITS = 8
EXIT_KEEP, EXIT_REVERT, EXIT_INVALID, EXIT_USAGE = 0, 1, 2, 3
EXIT_SCREEN = 5             # a screen was recorded: not 0, so `hca try --explore && ...` can never read a screen as a keep


PENDING_KEEP = "pending-keep.json"


def pending_path(root) -> Path:
    return ledger.hca_dir(root) / PENDING_KEEP


def write_pending(root, task, entry) -> None:
    """Record that the branch is about to move: in the referee store first, then as a marker file in `.hca/`.

    The store entry is the one reconciliation trusts (the repo-side marker is agent-writable); `entry` carries the kind
    (`keep` or `restore`), the snapshot sha, the last keep before the move and, for a keep, the verdict record and its digest.
    """
    ledger.set_pending(task, entry)
    ledger.hca_dir(root).mkdir(parents=True, exist_ok=True)
    write_json(pending_path(root), entry)


def clear_pending(root, task) -> None:
    pending_path(root).unlink(missing_ok=True)
    ledger.set_pending(task, None)


def open_session(cwd=None, on_branch=True, out=print) -> tuple:
    """(root, spec, state) for the repo that holds `cwd`, checking we are on the task branch.

    Also: refuses repos hca v1 cannot handle, makes `state` agree with the referee store (the store wins, with a
    warning), and finishes or discards a keep that an earlier run was killed in the middle of.
    """
    root = gitops.repo_root(cwd)
    state = ledger.load_state(root)
    gitops.ignore_notebook(state.get("explore") is not None)     # IDEAS.md is the notebook once explore mode is in use
    spec = load_spec(root / "hca.toml")
    if spec.task.name not in (state["task"], state.get("spec_task")):       # `init --name` runs a climb under another name
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
    """Finish or discard a keep (or a confirm restore) whose process died after the branch was about to move.

    The referee store holds the pending entry (written before `update-ref`); `.hca/pending-keep.json` is a mirror.
    - Branch tip is the snapshot: the move happened. Adopt it (index reset, working tree for a restore, state, attempt
      record taken from the store entry; an existing attempt record must have the entry's verdict digest).
    - Branch tip is still the old last keep: it never happened. Drop the entry.
    - Anything else, a marker the store knows nothing about, or a record that does not match: REFUSE (exit 2) with a
      message. Nothing is adopted or dropped silently, because a forged marker would otherwise inject an unmeasured commit.

    CONTRACT NOTE: A2.5 describes the marker for `try`; confirm's reset-to-confirmed commit uses the same mechanism
    (`kind: restore`) because it has the same window between moving the branch and syncing the working tree.
    """
    task = state["task"]
    path = pending_path(root)
    marker = None
    if path.exists():
        try:
            marker = read_json(path)
            marker["snapshot_sha"], marker["last_keep"]
        except (ValueError, KeyError, TypeError):
            marker = {}
    entry = ledger.get_pending(task)
    tip = gitops.branch_tip(root, state["branch"])
    if entry is None:
        if marker is None:
            return
        if marker.get("last_keep") == tip or not marker:
            path.unlink(missing_ok=True)
            out("warning: discarded .hca/pending-keep.json: the referee store has no matching record and the branch never moved.")
            return
        raise Refusal(f"found .hca/pending-keep.json for {str(marker.get('snapshot_sha'))[:12]}, but the referee store has no pending keep, "
                      f"so it was not written by a running hca (the branch tip is {tip[:12]}). Not adopting it. Fix: ask the human; if the "
                      "marker is stale, delete it and check the branch history with `git log`.")
    sha, old, kind = entry["snapshot_sha"], entry["last_keep"], entry.get("kind", "keep")
    if marker and (marker.get("snapshot_sha"), marker.get("last_keep")) != (sha, old):
        raise Refusal("the pending-keep marker in .hca/ and the referee store disagree about the snapshot; not adopting either. Fix: ask the human.")
    adir = ledger.attempt_path(root, entry["n"]) if kind == "keep" else None
    if tip == sha and state["last_keep"] in (old, sha):
        existing = None
        if adir is not None and (adir / "verdict.json").exists():
            existing = read_json(adir / "verdict.json")
            if ledger.verdict_digest(existing) != entry.get("digest"):
                raise Refusal(f"attempt {entry['n']:04d}'s record does not match the verdict the referee store has for it; not adopting the keep. "
                              "Fix: ask the human.")
        gitops.finish_advance(root, sha)
        state["last_keep"] = sha
        if kind == "keep":
            if existing is None:
                record = dict(entry.get("record") or {})
                record.update(n=entry["n"], verdict="keep", valid=True, commit=sha, confirm="pending")
                record["reasons"] = list(record.get("reasons", [])) + ["recovered: hca was stopped after moving the branch and before writing this record"]
                adir.mkdir(parents=True, exist_ok=True)
                write_json(adir / "verdict.json", record)
        else:
            gitops.revert(root, spec, sha)                 # the working tree's editable paths may still hold the undone content
            for a in ledger.tests_only(ledger.read_attempts(root)):
                if a.get("confirm") == "pending":
                    ledger.update_attempt(root, a["n"], confirm="unconfirmed")
            # B2 after a crash: the store marks and the taint must not depend on the confirm process having lived to make them.
            explore.taint_after_fail(root, state, old, entry.get("target") or state.get("last_confirmed") or state["init_sha"])
        ledger.refresh_verdicts(root, state, spec)
        clear_pending(root, task)
        out(f"warning: finished an interrupted {'keep' if kind == 'keep' else 'confirm reset'}: the branch is at {sha[:12]}.")
    elif tip == old:
        if adir is not None and not (adir / "verdict.json").exists():
            adir.mkdir(parents=True, exist_ok=True)
            record = dict(entry.get("record") or {"n": entry["n"]})
            record.update(verdict="void", valid=False, reasons=["hca was stopped before the keep was applied; nothing was committed"])
            record.pop("commit", None)
            write_json(adir / "verdict.json", record)
            ledger.refresh_verdicts(root, state, spec)
        clear_pending(root, task)
        out("warning: discarded a pending keep that was never applied.")
    else:
        raise Refusal(f"the referee store records a pending {kind} (snapshot {sha[:12]}), but the branch tip is {tip[:12]}, which is neither "
                      f"that snapshot nor the last keep {old[:12]}. Not adopting or dropping anything. Fix: ask the human.")


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
