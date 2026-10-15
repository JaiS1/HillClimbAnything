"""Parse objective output and run randomised A/B blocks over two arm directories.

Each block runs both arms once, sequentially, in a random order (the randomisation is what makes
stats.block_test valid, so the caller passes the `random.Random`). Failures are handled by the
preregistered rules in docs/ARCHITECTURE-v1.md: a candidate that crashes or reports an item
candidate_invalid scores as the worst value observed in the try (so breaking things can never look
like a win); infra problems are dropped from both arms; too many of them void the block, and too
many void blocks void the try. Scores are computed after all blocks ran, because "worst observed"
needs the whole try. Standard library only.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import runner, stats
from .core import HcaError

METRIC_LINE = re.compile(r"^METRIC\s+([A-Za-z0-9_.-]+)=(\S+)$")
STATUSES = ("valid", "candidate_invalid", "infra_invalid")
ARMS = ("baseline", "candidate")
INFRA_VOID_FRACTION = 0.10      # more than this share of items infra-invalid voids the block
TRY_VOID_FRACTION = 0.30        # more than this share of voided blocks voids the try
MAX_STDOUT_BYTES = 32 * 1024 * 1024
WALL_METRIC = "wall_seconds"


@dataclass(frozen=True)
class Item:
    id: str
    value: float | None             # median of runs (or the score); None when the item reported no number
    status: str
    tags: dict
    mechanism: dict


@dataclass(frozen=True)
class Parsed:
    kind: str                       # "items" | "metrics" | "none"
    value: float | None             # the objective metric from METRIC lines
    metrics: dict                   # every METRIC line (last one wins)
    items: tuple
    problem: str | None             # why the output is unusable (malformed JSON, bad item, missing metric)
    metric: str | None = None       # the `metric` name declared in item JSON, if any


def parse_output(stdout_text: str, spec_metric: str | None) -> Parsed:
    """Item JSON on the last non-empty line wins; otherwise METRIC lines (see the objective output contract)."""
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    json_problem = None
    if lines and lines[-1].startswith("{"):
        try:
            obj = json.loads(lines[-1])
        except ValueError as exc:
            json_problem = f"the last stdout line looks like JSON but does not parse ({exc})"
        else:
            if isinstance(obj, dict) and "items" in obj:
                return _parse_items(obj, spec_metric)
    metrics, bad = {}, []
    for line in lines:
        match = METRIC_LINE.match(line)
        if not match:
            continue
        try:
            metrics[match.group(1)] = float(match.group(2))
        except ValueError:
            bad.append(match.group(1))
            metrics.pop(match.group(1), None)
    if not metrics and not bad:
        return Parsed("none", None, {}, (), json_problem)
    problem = None
    if spec_metric is not None and spec_metric not in metrics:
        problem = (f"METRIC {spec_metric} has a non-numeric value" if spec_metric in bad else
                   f"no METRIC line named {spec_metric!r} (found: {', '.join(sorted(metrics)) or 'none'})")
    return Parsed("metrics", metrics.get(spec_metric) if spec_metric else None, metrics, (), problem)


def _parse_items(obj: dict, spec_metric) -> Parsed:
    def bad(msg):
        return Parsed("none", None, {}, (), "item JSON: " + msg)

    if spec_metric is not None and obj.get("metric") not in (None, spec_metric):
        return bad(f"metric is {obj.get('metric')!r} but objective.metric is {spec_metric!r}")
    raw = obj["items"]
    if not isinstance(raw, list) or not raw:
        return bad("`items` must be a non-empty list")
    items, seen = [], set()
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), (str, int)) or isinstance(entry.get("id"), bool):
            return bad(f"item #{position} needs an object with a string `id`")
        ident = str(entry["id"])
        if ident in seen:
            return bad(f"duplicate item id {ident!r}")
        seen.add(ident)
        status = entry.get("status", "valid")
        if status not in STATUSES:
            return bad(f"item {ident!r}: status {status!r} must be one of {', '.join(STATUSES)}")
        value = None
        if "runs" in entry:
            runs = entry["runs"]
            if not isinstance(runs, list) or not runs or not all(_number(r) for r in runs):
                return bad(f"item {ident!r}: `runs` must be a non-empty list of numbers")
            value = statistics.median(runs)
        elif "score" in entry:
            if not _number(entry["score"]):
                return bad(f"item {ident!r}: `score` must be a number")
            value = float(entry["score"])
        elif status == "valid":
            return bad(f"item {ident!r}: needs `runs` (a list) or `score` (a number)")
        tags = entry.get("tags", {})
        mechanism = entry.get("mechanism", {})
        if not isinstance(tags, dict) or not isinstance(mechanism, dict):
            return bad(f"item {ident!r}: `tags` and `mechanism` must be objects")
        items.append(Item(ident, value, status, tags, {k: float(v) for k, v in mechanism.items() if _number(v)}))
    return Parsed("items", None, {}, tuple(items), None, obj.get("metric") if isinstance(obj.get("metric"), str) else None)


@dataclass
class BlockResult:
    scores: list                    # block scores of the non-void blocks, positive = candidate better
    scored_blocks: list             # block numbers matching `scores`
    voided_blocks: list             # block numbers voided (with the reason in `warnings` and runs.jsonl)
    void: bool                      # too many voided blocks: the try is void
    blocks_run: int
    arm_medians: dict               # arm -> metric name -> median over non-void blocks (objective + mechanism)
    item_medians: dict              # item id -> {"baseline": m, "candidate": m, "tags": {...}} (for slices)
    candidate_failures: int         # candidate runs that failed outright
    candidate_invalid_blocks: list  # blocks where anything in the candidate was scored as worst
    median_run_seconds: float
    wall_seconds: float
    warnings: list = field(default_factory=list)
    dropped_env: tuple = ()


@dataclass
class _Run:
    block: int
    arm: str
    order: int
    result: runner.RunResult
    ok: bool = False
    problem: str | None = None
    kind: str | None = None         # "metrics" | "items" when ok
    value: float | None = None
    metrics: dict = field(default_factory=dict)
    items: dict = field(default_factory=dict)
    loadavg: tuple | None = None


def run_blocks(spec, baseline_dir, candidate_dir, n_blocks, rng, env_extra, out_dir, first_block=0, deadline=None) -> BlockResult:
    """Measure `spec.objective` in `n_blocks` randomised blocks of baseline vs candidate."""
    return measure_blocks(spec.objective.command, spec.objective.metric, spec.objective.direction, baseline_dir,
                          candidate_dir, n_blocks, rng, env_extra, out_dir, spec.limits.command_seconds,
                          spec.env.deny, first_block=first_block, deadline=deadline)


def measure_blocks(command, metric, direction, baseline_dir, candidate_dir, n_blocks, rng, env_extra, out_dir,
                   timeout, deny=(), first_block=0, deadline=None, wall_fallback=False) -> BlockResult:
    """The engine under run_blocks, usable without a spec (quick). `wall_fallback` scores the command's
    own wall time when it prints neither item JSON nor a METRIC line (the zero-config case)."""
    if direction not in ("min", "max") or not metric or n_blocks < 1:
        raise HcaError("measure_blocks needs direction min|max, a metric name and at least one block")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "runs.jsonl"
    dirs = {"baseline": Path(baseline_dir), "candidate": Path(candidate_dir)}
    start = time.monotonic()
    blocks = []                     # each: {"baseline": _Run, "candidate": _Run}
    dropped = set()
    for index in range(first_block, first_block + n_blocks):
        if deadline is not None and blocks and time.monotonic() > deadline:
            break
        order = ["baseline", "candidate"]
        rng.shuffle(order)
        pair = {}
        for position, arm in enumerate(order):
            env = dict(env_extra or {}, HCA_ARM=arm)
            run = _execute(command, metric, dirs[arm], out_dir, index, arm, position, env, timeout, deny, wall_fallback)
            dropped.update(run.result.dropped_env)
            pair[arm] = run
            _append(log, _run_record(run))
        blocks.append((index, pair))
    return _score(blocks, direction, metric, log, start, tuple(sorted(dropped)))


def _execute(command, metric, cwd, out_dir, block, arm, order, env, timeout, deny, wall_fallback) -> _Run:
    result = runner.run(command, cwd, out_dir / "runs", f"b{block:04d}-{arm}", timeout, env_extra=env, deny=deny)
    run = _Run(block, arm, order, result, loadavg=_loadavg())
    parsed = None
    if result.timed_out:
        run.problem = f"timed out after {timeout} s"
    elif result.returncode != 0:
        run.problem = f"exit status {result.returncode}"
    else:
        parsed = parse_output(_read_stdout(result.stdout_path), metric)
        if parsed.kind == "none" and wall_fallback:      # even a malformed-looking last line: it is just program output
            parsed = Parsed("metrics", result.seconds, {metric: result.seconds}, (), None)
        elif parsed.problem:
            run.problem = parsed.problem
        elif parsed.kind == "none":
            run.problem = f"stdout has neither item JSON nor a METRIC line named {metric!r}"
    if run.problem is None:
        run.kind = parsed.kind
        if parsed.kind == "metrics":
            run.value, run.metrics = parsed.value, parsed.metrics
            if not _positive(run.value):
                run.problem = f"{metric} = {run.value!r} is not positive and finite (log ratios need that)"
        else:
            run.items = {item.id: item for item in parsed.items}
    run.ok = run.problem is None
    if run.ok:                       # keep the stdout/stderr files only for runs someone may need to read
        for path in (result.stdout_path, result.stderr_path):
            path.unlink(missing_ok=True)
    return run


def _score(blocks, direction, metric, log, start, dropped_env) -> BlockResult:
    worst = max if direction == "min" else min
    seen_scalar, seen_items = [], {}
    for _, pair in blocks:
        for run in pair.values():
            if run.ok and run.kind == "metrics":
                seen_scalar.append(run.value)
            elif run.ok:
                for item in run.items.values():
                    if item.status == "valid" and _positive(item.value):
                        seen_items.setdefault(item.id, []).append(item.value)
    worst_scalar = worst(seen_scalar) if seen_scalar else None
    worst_items = {key: worst(vals) for key, vals in seen_items.items()}

    scores, scored, voided, cand_bad_blocks, warnings = [], [], [], [], []
    infra_by_block = {}
    cand_failures = 0
    for index, pair in blocks:
        base, cand = pair["baseline"], pair["candidate"]
        cand_failures += not cand.ok
        score, reason, infra, cand_bad = None, None, set(), False
        if not base.ok:
            reason = f"baseline run failed ({base.problem}); the baseline should not fail"
        elif base.kind == "metrics":
            if cand.ok and cand.kind != "metrics":
                cand.ok, cand.problem = False, "output kind differs from the baseline's"
                cand_failures += 1
            cand_bad = not cand.ok
            cand_value = cand.value if cand.ok else worst_scalar
            score = stats.log_ratio(base.value, cand_value, direction)
        else:
            if cand.ok and cand.kind != "items":
                cand.ok, cand.problem = False, "output kind differs from the baseline's"
                cand_failures += 1
            score, reason, infra, cand_bad = _item_score(base, cand, direction, worst_items)
        if reason:
            voided.append(index)
            warnings.append(f"block {index} voided: {reason}")
        else:
            scores.append(score)
            scored.append(index)
        if cand_bad:
            cand_bad_blocks.append(index)
        infra_by_block[index] = infra
        _append(log, {"kind": "block", "block": index, "void": bool(reason), "reason": reason, "score": score,
                      "candidate_scored_worst": cand_bad, "infra_items": sorted(infra)})
    void = not blocks or len(voided) / len(blocks) > TRY_VOID_FRACTION
    if void:
        warnings.append(f"try void: {len(voided)} of {len(blocks)} blocks voided (more than {TRY_VOID_FRACTION:.0%})")
    for line in warnings:
        _append(log, {"kind": "warning", "message": line})
    arm_medians, item_medians = _medians(blocks, set(voided), infra_by_block, metric)
    seconds = [run.result.seconds for _, pair in blocks for run in pair.values()]
    return BlockResult(scores, scored, voided, void, len(blocks), arm_medians, item_medians, cand_failures,
                       cand_bad_blocks, statistics.median(seconds) if seconds else 0.0,
                       time.monotonic() - start, warnings, dropped_env)


def _item_score(base, cand, direction, worst_items):
    """(score, void_reason, infra_ids, candidate_scored_worst) for one block of item runs."""
    ids = set(base.items) | (set(cand.items) if cand.ok else set())
    infra, cand_bad, base_values, cand_values = set(), False, {}, {}
    for ident in sorted(ids):
        b = base.items.get(ident)
        c = cand.items.get(ident) if cand.ok else None
        if b is None or b.status != "valid" or not _positive(b.value) or (c is not None and c.status == "infra_invalid"):
            infra.add(ident)
            continue
        base_values[ident] = b.value
        if c is None or c.status != "valid" or not _positive(c.value):
            cand_values[ident] = worst_items[ident]
            cand_bad = True
        else:
            cand_values[ident] = c.value
    if ids and len(infra) / len(ids) > INFRA_VOID_FRACTION:
        return None, f"{len(infra)} of {len(ids)} items infra-invalid (more than {INFRA_VOID_FRACTION:.0%})", infra, cand_bad
    if not base_values:
        return None, "no item is valid in both arms", infra, cand_bad
    return stats.item_block_score(base_values, cand_values, direction), None, infra, cand_bad


def _medians(blocks, voided, infra_by_block, metric):
    values = {arm: {} for arm in ARMS}
    per_item = {}
    for index, pair in blocks:
        if index in voided:
            continue
        for arm, run in pair.items():
            if not run.ok:
                continue
            if run.kind == "metrics":
                for name, value in run.metrics.items():
                    if math.isfinite(value):
                        values[arm].setdefault(name, []).append(value)
                continue
            keep = [i for i in run.items.values() if i.status == "valid" and i.id not in infra_by_block[index]]
            good = [i.value for i in keep if _positive(i.value)]
            if good:
                values[arm].setdefault(metric, []).append(statistics.median(good))
            for name in sorted({k for i in keep for k in i.mechanism}):
                values[arm].setdefault(name, []).append(statistics.median(i.mechanism[name] for i in keep if name in i.mechanism))
            for item in keep:
                slot = per_item.setdefault(item.id, {"baseline": [], "candidate": [], "tags": {}})
                if _positive(item.value):
                    slot[arm].append(item.value)
                slot["tags"] = slot["tags"] or dict(item.tags)
    arm_medians = {arm: {name: statistics.median(v) for name, v in sorted(by_name.items())} for arm, by_name in values.items()}
    item_medians = {ident: {"baseline": statistics.median(s["baseline"]) if s["baseline"] else None,
                            "candidate": statistics.median(s["candidate"]) if s["candidate"] else None,
                            "tags": s["tags"]} for ident, s in sorted(per_item.items())}
    return arm_medians, item_medians


def _run_record(run: _Run) -> dict:
    result = run.result
    record = {"kind": "run", "block": run.block, "arm": run.arm, "order": run.order,
              "wall_seconds": round(result.seconds, 6), "loadavg": run.loadavg, "returncode": result.returncode,
              "timed_out": result.timed_out, "ok": run.ok, "problem": run.problem, "value": run.value,
              "metrics": run.metrics, "items": {i.id: {"value": i.value, "status": i.status} for i in run.items.values()},
              "orphans_killed": result.orphans_killed}
    if not run.ok:
        record.update(stdout=str(result.stdout_path), stderr=str(result.stderr_path), error_tail=result.error_tail)
    if result.dropped_env:
        record["dropped_env"] = list(result.dropped_env)
    return record


def _append(path: Path, record: dict) -> None:
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_clean(record), allow_nan=False) + "\n")


def _clean(obj):
    """JSON has no NaN/Infinity: write them as null so runs.jsonl stays strictly parseable."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_clean(v) for v in obj]
    return obj


def _read_stdout(path) -> str:
    """The stdout text; if it is huge, only its last MAX_STDOUT_BYTES (the item JSON line comes last)."""
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        handle.seek(max(0, handle.tell() - MAX_STDOUT_BYTES))
        return handle.read().decode("utf-8", errors="replace")


def _loadavg():
    try:
        return tuple(float(x) for x in Path("/proc/loadavg").read_text().split()[:3])
    except (OSError, ValueError):
        return None


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value) -> bool:
    return _number(value) and math.isfinite(value) and value > 0
