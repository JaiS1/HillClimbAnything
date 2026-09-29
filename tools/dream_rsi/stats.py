"""Noise-aware promotion: paired comparisons, same-session controls, frozen rules.

A candidate is promoted over its baseline only when a paired bootstrap interval
on the mean per-item difference excludes zero after a Bonferroni correction for
every comparison made, and when declared harm guardrails hold. Clustered items
are resampled by cluster. Standard library only; deterministic for a fixed seed.

These checks reduce the chance of promoting noise. They do not replace a fresh
confirmation, and they cannot repair a biased or contaminated evaluator.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import random

from . import core


DEFAULT_RULE = {
    "alpha": 0.05,              # family-wise error rate before Bonferroni division
    "comparisons": 1,           # candidates or revisions compared against a baseline
    "bootstrap": 2000,
    "seed": 0,
    "min_units": 5,             # independent units (clusters, else items) required
    "harm_tolerance": 0.0,      # an item is harmed when its paired difference < -tolerance
    "max_harm_rate": None,      # e.g. 0.2: at most 20% of items harmed
    "max_subgroup_loss": None,  # e.g. 0.5: no subgroup mean difference below -0.5
    "useful_effect": None,      # smallest improvement worth acting on (power check)
}
PREMISE_FIELDS = ("produced_by", "data_new_to_it", "zero_effort_baseline",
                  "can_move_scored_quantity", "step0_metric")


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_rule(rule=None):
    """Merge a partial rule with defaults and reject malformed thresholds."""
    rule = dict(DEFAULT_RULE, **(rule or {}))
    unknown = set(rule) - set(DEFAULT_RULE)
    if unknown:
        raise core.HarnessError(f"unknown decision-rule fields: {sorted(unknown)}")
    if not _finite(rule["alpha"]) or not 0 < rule["alpha"] < 1:
        raise core.HarnessError("alpha must be between 0 and 1")
    for key, low in (("comparisons", 1), ("bootstrap", 100), ("min_units", 2)):
        if isinstance(rule[key], bool) or not isinstance(rule[key], int) or rule[key] < low:
            raise core.HarnessError(f"{key} must be an integer >= {low}")
    if isinstance(rule["seed"], bool) or not isinstance(rule["seed"], int):
        raise core.HarnessError("seed must be an integer")
    if not _finite(rule["harm_tolerance"]) or rule["harm_tolerance"] < 0:
        raise core.HarnessError("harm_tolerance must be finite and nonnegative")
    for key in ("max_harm_rate", "max_subgroup_loss", "useful_effect"):
        value = rule[key]
        if value is not None and (not _finite(value) or value < 0 or (key == "max_harm_rate" and value > 1)):
            raise core.HarnessError(f"{key} must be null or a nonnegative number")
    return rule


def _items(items):
    """Map id -> item after validating the per-item score list."""
    if not isinstance(items, list) or not items:
        raise core.HarnessError("item scores need a nonempty list")
    mapped = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise core.HarnessError("each item needs a nonempty string id")
        if not _finite(item.get("score")):
            raise core.HarnessError(f"item {item['id']} needs a finite score")
        for key in ("cluster", "subgroup"):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise core.HarnessError(f"item {item['id']} {key} must be a string")
        if item["id"] in mapped:
            raise core.HarnessError(f"duplicate item id: {item['id']}")
        mapped[item["id"]] = item
    return mapped


def paired_differences(candidate, baseline, direction):
    """Direction-adjusted candidate-minus-baseline differences on identical items."""
    if direction not in ("max", "min"):
        raise core.HarnessError("direction must be max or min")
    left, right = _items(candidate), _items(baseline)
    if set(left) != set(right):
        raise core.HarnessError("paired comparison needs identical item ids in both arms")
    sign = 1.0 if direction == "max" else -1.0
    rows = []
    for key in sorted(right):
        for field in ("cluster", "subgroup"):
            if left[key].get(field) != right[key].get(field):
                raise core.HarnessError(f"item {key} has a different {field} in each arm")
        rows.append({"id": key, "diff": sign * (left[key]["score"] - right[key]["score"]),
                     "cluster": right[key].get("cluster") or key, "subgroup": right[key].get("subgroup")})
    return rows


def _quantile(values, q):
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_interval(rows, level, draws, seed):
    """Percentile interval for the mean difference, resampling whole clusters."""
    clusters = {}
    for row in rows:
        clusters.setdefault(row["cluster"], []).append(row["diff"])
    groups = [clusters[key] for key in sorted(clusters)]
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        total = count = 0
        for _ in groups:
            group = groups[rng.randrange(len(groups))]
            total += sum(group)
            count += len(group)
        means.append(total / count)
    tail = (1 - level) / 2
    return _quantile(means, tail), _quantile(means, 1 - tail)


def decide(candidate, baseline, direction, rule=None):
    """Paired promotion verdict: promote, hold or insufficient, with reasons."""
    rule = check_rule(rule)
    rows = paired_differences(candidate, baseline, direction)
    diffs = [row["diff"] for row in rows]
    units = len({row["cluster"] for row in rows})
    level = 1 - rule["alpha"] / rule["comparisons"]
    mean = sum(diffs) / len(diffs)
    result = {"items": len(rows), "units": units, "mean_difference": mean,
              "interval_level": level, "interval": None, "half_width": None,
              "harmed_rate": sum(d < -rule["harm_tolerance"] for d in diffs) / len(diffs),
              "subgroups": {}, "worst_subgroup": None, "warnings": [], "reasons": []}
    subgroups = {}
    for row in rows:
        if row["subgroup"] is not None:
            subgroups.setdefault(row["subgroup"], []).append(row["diff"])
    result["subgroups"] = {key: sum(value) / len(value) for key, value in sorted(subgroups.items())}
    if result["subgroups"]:
        worst = min(result["subgroups"], key=result["subgroups"].get)
        result["worst_subgroup"] = {"name": worst, "mean_difference": result["subgroups"][worst]}
    if units < rule["min_units"]:
        result["verdict"] = "insufficient"
        result["reasons"].append(f"{units} independent units < min_units {rule['min_units']}")
        return result
    low, high = bootstrap_interval(rows, level, rule["bootstrap"], rule["seed"])
    result["interval"] = [low, high]
    result["half_width"] = (high - low) / 2
    if rule["useful_effect"] is not None and result["half_width"] > rule["useful_effect"]:
        result["warnings"].append(
            f"underpowered: interval half-width {result['half_width']:.4g} exceeds useful effect "
            f"{rule['useful_effect']:.4g}; add items, gate to the affected subset, or register a larger effect")
    if low <= 0:
        result["reasons"].append(f"lower bound {low:.4g} is not above zero at level {level:.4g}")
    if rule["max_harm_rate"] is not None and result["harmed_rate"] > rule["max_harm_rate"]:
        result["reasons"].append(f"harmed-item rate {result['harmed_rate']:.3g} > {rule['max_harm_rate']:.3g}")
    if (rule["max_subgroup_loss"] is not None and result["worst_subgroup"] is not None and
            result["worst_subgroup"]["mean_difference"] < -rule["max_subgroup_loss"]):
        result["reasons"].append(f"subgroup {result['worst_subgroup']['name']} mean difference "
                                 f"{result['worst_subgroup']['mean_difference']:.4g} < -{rule['max_subgroup_loss']:.4g}")
    result["verdict"] = "hold" if result["reasons"] else "promote"
    return result


def load_scores(path):
    """Read one arm's per-item scores, tagged with the runtime that produced them."""
    data = core.read_json(path)
    if data.get("kind") != "dream_loop_item_scores":
        raise core.HarnessError(f"{path}: kind must be dream_loop_item_scores")
    for key in ("arm", "runtime"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise core.HarnessError(f"{path}: {key} must be a nonempty string")
    metric = data.get("metric")
    if (not isinstance(metric, dict) or metric.get("direction") not in ("max", "min") or
            not all(isinstance(metric.get(k), str) and metric[k] for k in ("name", "identity"))):
        raise core.HarnessError(f"{path}: metric needs name, identity and direction")
    _items(data.get("items"))
    if data.get("created_utc") is not None:
        _utc(data["created_utc"], path)
    return data


def _utc(value, where):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise core.HarnessError(f"{where}: timestamps must be ISO 8601") from exc
    if stamp.tzinfo is None:
        raise core.HarnessError(f"{where}: timestamps need a UTC offset")
    return stamp


def register(draft, now=None):
    """Freeze arms, decision rule, premise check and power target before results."""
    if not isinstance(draft, dict) or draft.get("kind") != "dream_loop_registration":
        raise core.HarnessError("registration kind must be dream_loop_registration")
    for key in ("question", "incumbent"):
        if not isinstance(draft.get(key), str) or not draft[key]:
            raise core.HarnessError(f"registration needs {key}")
    arms = draft.get("arms")
    if not isinstance(arms, list) or not arms or not all(isinstance(a, str) and a for a in arms) or len(set(arms)) != len(arms):
        raise core.HarnessError("registration needs a list of unique arm names")
    metric = draft.get("metric")
    if not isinstance(metric, dict) or metric.get("direction") not in ("max", "min"):
        raise core.HarnessError("registration metric needs name, identity and direction")
    rule = check_rule(draft.get("decision"))
    if rule["useful_effect"] is None or rule["useful_effect"] <= 0:
        raise core.HarnessError("registration must state a positive decision.useful_effect")
    if rule["comparisons"] < len(arms):
        raise core.HarnessError("decision.comparisons must cover every registered arm")
    premise = draft.get("premise")
    missing = [k for k in PREMISE_FIELDS if not isinstance(premise, dict) or premise.get(k) in (None, "")]
    if missing:
        raise core.HarnessError(f"premise check incomplete: {missing}")
    warnings = []
    expected = draft.get("expected_half_width")
    if expected is not None:
        if not _finite(expected) or expected <= 0:
            raise core.HarnessError("expected_half_width must be positive")
        if expected > rule["useful_effect"]:
            warnings.append(f"underpowered by design: expected half-width {expected:g} exceeds useful effect "
                            f"{rule['useful_effect']:g}; gate to a subset, add items or use a paired design")
    else:
        warnings.append("no expected_half_width stated; power cannot be checked before results")
    draft = dict(draft, decision=rule)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return {"schema_version": 1, "kind": "dream_loop_frozen_registration", "registration": draft,
            "sha256": core.object_hash(draft), "registered_utc": stamp, "warnings": warnings}


def check_registration(frozen):
    if not isinstance(frozen, dict) or frozen.get("kind") != "dream_loop_frozen_registration":
        raise core.HarnessError("not a frozen registration")
    if core.object_hash(frozen.get("registration")) != frozen.get("sha256"):
        raise core.HarnessError("registration changed after it was frozen")
    _utc(frozen.get("registered_utc"), "registration")
    return frozen["registration"]


def compare(candidates, incumbent, control=None, frozen=None, rule=None):
    """Compare candidate arms with the incumbent and return one current verdict.

    candidates, incumbent and control are loaded score files. A same-session
    control (the incumbent re-run in the candidates' runtime) becomes the paired
    baseline; its difference from the stored incumbent measures evaluator noise.
    Cross-runtime comparisons are refused. With a frozen registration, the
    registered rule is used and unregistered or pre-dated arms are exploratory.
    """
    if not candidates:
        raise core.HarnessError("compare needs at least one candidate")
    everyone = list(candidates) + [incumbent] + ([control] if control else [])
    metric = incumbent["metric"]
    if any(arm["metric"] != metric for arm in everyone):
        raise core.HarnessError("compare needs one metric identity across all arms")
    names = [arm["arm"] for arm in everyone]
    if len(set(names)) != len(names):
        raise core.HarnessError("arm names must be unique")
    runtime = candidates[0]["runtime"]
    if any(arm["runtime"] != runtime for arm in candidates) or (control and control["runtime"] != runtime):
        raise core.HarnessError("candidates and control must share one runtime; never compare across runtimes")
    registration = None
    if frozen is not None:
        if rule:
            raise core.HarnessError("a frozen registration fixes the decision rule; remove rule overrides")
        registration = check_registration(frozen)
        rule = registration["decision"]
        if registration["metric"] != metric:
            raise core.HarnessError("score metric differs from the registered metric")
        if incumbent["arm"] != registration["incumbent"]:
            raise core.HarnessError("incumbent differs from the registered incumbent")
    rule = check_rule(rule)
    rule["comparisons"] = max(rule["comparisons"], len(candidates))
    notes, noise = [], None
    if control is not None:
        baseline = control
        if incumbent["runtime"] == runtime:
            noise = decide(control["items"], incumbent["items"], metric["direction"], dict(rule, comparisons=1))
            if noise["interval"] is not None and (noise["interval"][0] > 0 or noise["interval"][1] < 0):
                notes.append("same-session rerun differs from the stored incumbent beyond noise: the "
                             "evaluator or runtime is not reproducible; investigate before trusting deltas")
        else:
            notes.append(f"stored incumbent runtime {incumbent['runtime']!r} differs; it is not compared, "
                         "and deltas are read against the same-session control")
    elif incumbent["runtime"] != runtime:
        raise core.HarnessError(f"incumbent runtime {incumbent['runtime']!r} differs from candidates' {runtime!r}; "
                                "re-run the incumbent in this runtime and pass it as the control")
    else:
        baseline = incumbent
        notes.append("no same-session control: evaluator noise is not measured")
    registered_at = _utc(frozen["registered_utc"], "registration") if frozen else None
    arms = []
    for arm in candidates:
        verdict = decide(arm["items"], baseline["items"], metric["direction"], rule)
        status = "confirmatory" if registration else "unregistered"
        if registration:
            if arm["arm"] not in registration["arms"]:
                status = "exploratory: arm added after registration"
            elif arm.get("created_utc") is None:
                status = "confirmatory (result timestamp not recorded)"
            elif _utc(arm["created_utc"], arm["arm"]) < registered_at:
                status = "exploratory: result predates registration"
        eligible = verdict["verdict"] == "promote" and not status.startswith("exploratory")
        arms.append({"arm": arm["arm"], "status": status, "promotable": eligible, **verdict})
    promotable = [a for a in arms if a["promotable"]]
    chosen = max(promotable, key=lambda a: a["interval"][0]) if promotable else None
    current = ({"promote": chosen["arm"], "over": baseline["arm"],
                "summary": f"promote {chosen['arm']}: mean difference {chosen['mean_difference']:.4g}, "
                           f"interval [{chosen['interval'][0]:.4g}, {chosen['interval'][1]:.4g}]"}
               if chosen else {"promote": None, "over": baseline["arm"],
                               "summary": f"keep {incumbent['arm']}: no candidate passed the rule"})
    return {"schema_version": 1, "kind": "dream_loop_comparison", "current_verdict": current,
            "metric": metric, "runtime": runtime, "baseline": baseline["arm"], "rule": rule,
            "registration": ({"sha256": frozen["sha256"], "registered_utc": frozen["registered_utc"]}
                             if frozen else None),
            "same_session_noise": noise, "notes": notes, "arms": arms,
            "claim": "Paired, multiplicity-corrected comparison on the supplied items. It measures sampling "
                     "noise, not evaluator bias, contamination or fresh-item generalization."}


def render(result):
    """Plain-text summary that leads with the single current verdict."""
    lines = [f"CURRENT VERDICT: {result['current_verdict']['summary']}",
             f"Baseline: {result['baseline']} · runtime {result['runtime']} · "
             f"Bonferroni over {result['rule']['comparisons']} comparison(s)"]
    if result["same_session_noise"] and result["same_session_noise"]["interval"]:
        low, high = result["same_session_noise"]["interval"]
        lines.append(f"Same-session noise (control - stored incumbent): [{low:.4g}, {high:.4g}]")
    for arm in result["arms"]:
        interval = ("n/a" if arm["interval"] is None else f"[{arm['interval'][0]:.4g}, {arm['interval'][1]:.4g}]")
        lines.append(f"  {arm['arm']}: {arm['verdict']} ({arm['status']}); mean {arm['mean_difference']:.4g}, "
                     f"interval {interval}, harmed {arm['harmed_rate']:.2f}"
                     + "".join(f"; {r}" for r in arm["reasons"]))
        lines += [f"    warning: {w}" for w in arm["warnings"]]
    lines += [f"Note: {n}" for n in result["notes"]]
    return "\n".join(lines)


def load_frozen(path):
    frozen = core.read_json(Path(path))
    check_registration(frozen)
    return frozen
