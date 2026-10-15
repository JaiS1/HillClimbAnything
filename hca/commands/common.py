"""Pieces shared by init, try and confirm: opening a session, item-mode decisions, intervals, formatting."""

from __future__ import annotations

import math
import random
import statistics
from pathlib import Path

from .. import gitops, ledger, paired, stats
from ..core import HcaError
from ..spec import load_spec

ITEMS_ALPHA = 0.02          # two-sided, so the one-sided lower bound sits at 1%: the same per-try level as blocks mode
ITEMS_MIN_UNITS = 8
EXIT_KEEP, EXIT_REVERT, EXIT_INVALID, EXIT_USAGE = 0, 1, 2, 3


def open_session(cwd=None, on_branch=True) -> tuple:
    """(root, spec, state) for the repo that holds `cwd`, checking we are on the task branch."""
    root = gitops.repo_root(cwd)
    state = ledger.load_state(root)
    spec = load_spec(root / "hca.toml")
    if spec.task.name != state["task"]:
        raise HcaError(f"hca.toml says task {spec.task.name!r} but this session is {state['task']!r}. "
                       "Fix: restore hca.toml (it is frozen), or run `hca init --reset`.")
    if on_branch and gitops.current_branch(root) != state["branch"]:
        raise HcaError(f"you are on {gitops.current_branch(root)!r} but this session lives on {state['branch']!r}. "
                       f"Fix: `git checkout {state['branch']}`.")
    return root, spec, state


def make_rng(seed=None) -> tuple:
    """(Random, seed). The seed is recorded so a try's block order can be replayed; it is not secret."""
    seed = random.SystemRandom().getrandbits(48) if seed is None else int(seed)
    return random.Random(seed), seed


def finite_or(value, fallback=None):
    return value if value is not None and math.isfinite(value) else fallback


def block_interval(scores, level) -> tuple:
    """(lower, upper) log-ratio bounds, each one-sided at `level` (the upper by symmetry). May be +-inf."""
    return stats.walsh_lower_bound(scores, level), -stats.walsh_lower_bound([-s for s in scores], level)


def blocks_fields(decision, scores, useful, level) -> dict:
    """Display fields of a stats.keep_decision, in %, plus whether the interval still allows a useful effect."""
    lower, upper = block_interval(scores, level)
    return {"estimate_log": decision["estimate"], "estimate": decision["estimate_pct"],
            "lower": stats.to_percent(lower) if math.isfinite(lower) else None,
            "upper": stats.to_percent(upper) if math.isfinite(upper) else None,
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
    """Per-tag-value median item log ratio (in %). `item_rows`: {id: (baseline, candidate, tags)}.

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
                   "median_pct": stats.to_percent(statistics.median(g["scores"])) if g["scores"] else None}
            for name, g in groups.items()}


def block_slices(result, tag_keys, direction) -> dict:
    return slices({i: (v["baseline"], v["candidate"], v["tags"]) for i, v in result.item_medians.items()}, tag_keys, direction)


def pair_slices(pair, tag_keys, direction) -> dict:
    return slices({i: (pair.baseline[i], pair.candidate[i], pair.tags.get(i, {})) for i in pair.baseline}, tag_keys, direction)


def tail_text(text, limit=600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def rel(path, root) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)
