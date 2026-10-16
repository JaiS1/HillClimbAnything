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
    """Item JSON on the last non-empty line wins; otherwise METRIC lines (see the objective output contract).

    The objective metric must appear exactly once. A second `METRIC <objective>` line (a candidate that
    prints its own number after the evaluator's, S8) is an invalid run, and so is item JSON that comes with
    a METRIC line for the objective: the stream is a shared channel, so ambiguity is never resolved in the
    writer's favour. Other (mechanism) metrics: the last line wins. `HCA_RESULT_FILE` avoids the problem.
    """
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    json_problem = None
    counts = {}
    for line in lines:
        match = METRIC_LINE.match(line)
        if match:
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    if lines and lines[-1].startswith("{"):
        try:
            obj = json.loads(lines[-1])
        except ValueError as exc:
            json_problem = f"the last stdout line looks like JSON but does not parse ({exc})"
        else:
            if isinstance(obj, dict) and "items" in obj:
                names = {n for n in (spec_metric, obj.get("metric")) if isinstance(n, str)}
                clash = sorted(n for n in names if counts.get(n))
                if clash:
                    return Parsed("none", None, {}, (), f"item JSON came with a METRIC line for {clash[0]!r}; the objective metric "
                                  "must be reported once (write the result to HCA_RESULT_FILE to keep it out of the shared stdout)")
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
    if spec_metric is not None and counts.get(spec_metric, 0) > 1:
        problem = (f"METRIC {spec_metric} was printed {counts[spec_metric]} times; the objective must report its metric exactly "
                   "once (write the result to HCA_RESULT_FILE to keep it out of the shared stdout)")
    elif spec_metric is not None and spec_metric not in metrics:
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
    arm_values: dict = field(default_factory=dict)      # arm -> metric name -> per-block values (what the medians summarise)
    item_values: dict = field(default_factory=dict)     # item id -> {"baseline": [...], "candidate": [...], "tags": {...}}


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


def run_blocks(spec, baseline_dir, candidate_dir, n_blocks, rng, env_extra, out_dir, first_block=0, deadline=None,
               warmup=False, prepare=None) -> BlockResult:
    """Measure `spec.objective` in `n_blocks` randomised blocks of baseline vs candidate.

    `prepare` is {"baseline": fn, "candidate": fn}: called right before every objective run of that arm (the warm-up
    too), so the arm can be cleaned of files an earlier run left behind (arms.prepare_run).
    """
    return measure_blocks(spec.objective.command, spec.objective.metric, spec.objective.direction, baseline_dir,
                          candidate_dir, n_blocks, rng, env_extra, out_dir, spec.limits.command_seconds,
                          spec.env.deny, first_block=first_block, deadline=deadline, warmup=warmup, prepare=prepare)


# CONTRACT NOTE: A2.3 says try_seconds covers builds, guards and blocks; every command run (including each objective run)
# is therefore given at most the time left in the try, never less than one second.
def _capped(timeout, deadline):
    """A command's timeout, shortened so it cannot run past the try's deadline (never below one second)."""
    return timeout if deadline is None else max(1.0, min(timeout, deadline - time.monotonic()))


def _command_for(command, arm):
    """`command` is one argv for both arms, or {"baseline": argv, "candidate": argv} when the arms run different commands (compare)."""
    return command[arm] if isinstance(command, dict) else command


def warm_up(command, dirs, out_dir, timeout, deny, env_extra, deadline=None, prepare=None) -> None:
    """One discarded objective run per arm, before block 0: cold caches and fresh worktrees must not land in a measured block.

    Both arms get exactly one, in a fixed order; the results are logged as `warmup` and never scored.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for arm in ARMS:
        result_file = out_dir / "runs" / f"warmup-{arm}.result"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.unlink(missing_ok=True)
        if prepare and prepare.get(arm):
            prepare[arm]()
        env = dict(env_extra or {}, HCA_RESULT_FILE=str(result_file))
        result = runner.run(_command_for(command, arm), dirs[arm], out_dir / "runs", f"warmup-{arm}", _capped(timeout, deadline), env_extra=env, deny=deny)
        _append(out_dir / "runs.jsonl", {"kind": "warmup", "arm": arm, "wall_seconds": round(result.seconds, 6),
                                         "returncode": result.returncode, "timed_out": result.timed_out})
        for path in (result.stdout_path, result.stderr_path, result_file):
            path.unlink(missing_ok=True)


def measure_blocks(command, metric, direction, baseline_dir, candidate_dir, n_blocks, rng, env_extra, out_dir,
                   timeout, deny=(), first_block=0, deadline=None, wall_fallback=False, warmup=False, prepare=None,
                   wall_only=False) -> BlockResult:
    """The engine under run_blocks, usable without a spec (quick, compare). `wall_fallback` scores the command's
    own wall time when it prints neither item JSON nor a METRIC line (the zero-config case); `wall_only` always scores
    it and ignores the output (compare's default metric). `command` may be a {"baseline": argv, "candidate": argv}
    dict (compare runs a different command in each arm). `warmup` runs
    one discarded objective run per arm first. `HCA_ARM` is deliberately not exported: the benchmark must not
    be able to tell the arms apart (the arm stays in runs.jsonl)."""
    if direction not in ("min", "max") or not metric or n_blocks < 1:
        raise HcaError("measure_blocks needs direction min|max, a metric name and at least one block")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "runs.jsonl"
    dirs = {"baseline": Path(baseline_dir), "candidate": Path(candidate_dir)}
    if warmup:
        warm_up(command, dirs, out_dir, timeout, deny, env_extra, deadline, prepare)
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
            if prepare and prepare.get(arm):
                prepare[arm]()
            run = _execute(_command_for(command, arm), metric, dirs[arm], out_dir, index, arm, position, dict(env_extra or {}),
                           _capped(timeout, deadline), deny, wall_fallback, wall_only)
            dropped.update(run.result.dropped_env)
            pair[arm] = run
            _append(log, _run_record(run))
        blocks.append((index, pair))
    return _score(blocks, direction, metric, log, start, tuple(sorted(dropped)))


def read_output(stdout_path, result_file) -> str:
    """What the objective reported: the result file if it is non-empty (the evaluator's own channel), else stdout."""
    try:
        if result_file is not None and Path(result_file).stat().st_size > 0:
            with open(result_file, "rb") as handle:
                return handle.read(MAX_STDOUT_BYTES).decode("utf-8", errors="replace")
    except OSError:
        pass
    return _read_stdout(stdout_path)


def _execute(command, metric, cwd, out_dir, block, arm, order, env, timeout, deny, wall_fallback, wall_only=False) -> _Run:
    stem = f"b{block:04d}-{arm}"
    result_file = out_dir / "runs" / f"{stem}.result"          # fresh per run: removed before the command starts
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.unlink(missing_ok=True)
    result = runner.run(command, cwd, out_dir / "runs", stem, timeout, env_extra=dict(env, HCA_RESULT_FILE=str(result_file)), deny=deny)
    run = _Run(block, arm, order, result, loadavg=_loadavg())
    parsed = None
    if result.timed_out:
        run.problem = f"timed out after {timeout:g} s"
    elif result.returncode != 0:
        run.problem = f"exit status {result.returncode}"
    elif wall_only:
        parsed = Parsed("metrics", result.seconds, {metric: result.seconds}, (), None)
    else:
        parsed = parse_output(read_output(result.stdout_path, result_file), metric)
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
        for path in (result.stdout_path, result.stderr_path, result_file):
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
    arm_values, item_values = _collect(blocks, set(voided), infra_by_block, metric)
    arm_medians, item_medians = _summarise(arm_values, item_values)
    seconds = [run.result.seconds for _, pair in blocks for run in pair.values()]
    return BlockResult(scores, scored, voided, void, len(blocks), arm_medians, item_medians, cand_failures,
                       cand_bad_blocks, statistics.median(seconds) if seconds else 0.0,
                       time.monotonic() - start, warnings, dropped_env, arm_values, item_values)


def _item_score(base, cand, direction, worst_items):
    """(score, void_reason, infra_ids, candidate_scored_worst) for one block of item runs."""
    ids = set(base.items) | (set(cand.items) if cand.ok else set())
    infra, cand_bad, base_values, cand_values = set(), False, {}, {}
    for ident in sorted(ids):
        b = base.items.get(ident)
        c = cand.items.get(ident) if cand.ok else None
        if b is None or b.status != "valid" or not _positive(b.value):
            infra.add(ident)
            continue
        base_values[ident] = b.value
        # A candidate-side infra_invalid on an item the baseline scored fine is not the harness's fault as far as we can
        # tell (the candidate can influence what the evaluator relays, S13): it scores as candidate_invalid, never dropped.
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


def _collect(blocks, voided, infra_by_block, metric):
    """Per-block values by arm and metric, and per-item values (non-void blocks only)."""
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
    return values, per_item


def _summarise(values, per_item):
    arm_medians = {arm: {name: statistics.median(v) for name, v in sorted(by_name.items())} for arm, by_name in values.items()}
    item_medians = {ident: {"baseline": statistics.median(s["baseline"]) if s["baseline"] else None,
                            "candidate": statistics.median(s["candidate"]) if s["candidate"] else None,
                            "tags": s["tags"]} for ident, s in sorted(per_item.items())}
    return arm_medians, item_medians


def combine(first: BlockResult, second: BlockResult) -> BlockResult:
    """One BlockResult for a try that ran `first` and then an extension `second` (block numbers continue).

    The medians are recomputed from the pooled per-block values, because the median of two medians is
    not the median of all the blocks. The void rule is re-applied to the pooled blocks.
    """
    values = {arm: {name: list(v) for name, v in first.arm_values.get(arm, {}).items()} for arm in ARMS}
    for arm in ARMS:
        for name, v in second.arm_values.get(arm, {}).items():
            values[arm].setdefault(name, []).extend(v)
    per_item = {i: {"baseline": list(s["baseline"]), "candidate": list(s["candidate"]), "tags": dict(s["tags"])}
                for i, s in first.item_values.items()}
    for ident, s in second.item_values.items():
        slot = per_item.setdefault(ident, {"baseline": [], "candidate": [], "tags": {}})
        slot["baseline"].extend(s["baseline"])
        slot["candidate"].extend(s["candidate"])
        slot["tags"] = slot["tags"] or dict(s["tags"])
    arm_medians, item_medians = _summarise(values, per_item)
    blocks_run = first.blocks_run + second.blocks_run
    voided = first.voided_blocks + second.voided_blocks
    void = first.void or second.void or not blocks_run or len(voided) / blocks_run > TRY_VOID_FRACTION
    runs = first.blocks_run * first.median_run_seconds + second.blocks_run * second.median_run_seconds
    return BlockResult(first.scores + second.scores, first.scored_blocks + second.scored_blocks, voided, void, blocks_run,
                       arm_medians, item_medians, first.candidate_failures + second.candidate_failures,
                       first.candidate_invalid_blocks + second.candidate_invalid_blocks,
                       runs / blocks_run if blocks_run else 0.0, first.wall_seconds + second.wall_seconds,
                       first.warnings + second.warnings, tuple(sorted(set(first.dropped_env) | set(second.dropped_env))),
                       values, per_item)


@dataclass
class ItemPair:
    """One deterministic measurement of both arms in `items` mode (see run_item_pair)."""
    baseline: dict                  # item id -> value, for the items kept in the comparison
    candidate: dict                 # item id -> value; candidate-invalid items carry the run's worst value
    tags: dict                      # item id -> tags
    infra: list                     # ids dropped from both arms
    candidate_bad: list             # ids the candidate got wrong (scored as the run's worst)
    candidate_failed: bool          # the candidate run itself failed (crash, timeout, unusable output)
    void: bool
    reasons: list                   # why it is void / failed
    arm_medians: dict               # arm -> metric name -> median over the kept items (objective + mechanism)
    wall_seconds: float


def run_item_pair(spec, baseline_dir, candidate_dir, rng, env_extra, out_dir, block=0, deadline=None, warmup=False,
                  prepare=None) -> ItemPair:
    """Items mode: run each arm once in random order and return the per-item values.

    This does not go through block scoring on purpose: deterministic scores such as accuracy can be
    zero or negative, and block scoring takes log ratios, which would drop those items as invalid.
    The preregistered invalid rules still apply. A candidate that fails outright is flagged for the
    caller to revert. A candidate-invalid item scores as the worst value in the run (with a single
    measurement per item, the item's own "worst observed" would just be the baseline, which would
    score breaking an item as a tie). Infra-invalid items are dropped from both arms; more than 10%
    of them voids the measurement.
    """
    obj = spec.objective
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "runs.jsonl"
    dirs = {"baseline": Path(baseline_dir), "candidate": Path(candidate_dir)}
    if warmup:
        warm_up(obj.command, dirs, out_dir, spec.limits.command_seconds, spec.env.deny, env_extra, deadline, prepare)
    start = time.monotonic()
    order = ["baseline", "candidate"]
    rng.shuffle(order)
    runs = {}
    for position, arm in enumerate(order):
        if prepare and prepare.get(arm):
            prepare[arm]()
        runs[arm] = _execute(obj.command, obj.metric, dirs[arm], out_dir, block, arm, position, dict(env_extra or {}),
                             _capped(spec.limits.command_seconds, deadline), spec.env.deny, False)
        _append(log, _run_record(runs[arm]))
    base, cand = runs["baseline"], runs["candidate"]

    def result(**kw):
        defaults = dict(baseline={}, candidate={}, tags={}, infra=[], candidate_bad=[], candidate_failed=False, void=False,
                        reasons=[], arm_medians={"baseline": {}, "candidate": {}}, wall_seconds=time.monotonic() - start)
        defaults.update(kw)
        pair = ItemPair(**defaults)
        _append(log, {"kind": "block", "block": block, "void": pair.void, "reason": "; ".join(pair.reasons) or None,
                      "score": None, "candidate_scored_worst": bool(pair.candidate_bad or pair.candidate_failed),
                      "infra_items": sorted(pair.infra)})
        return pair

    if not base.ok:
        return result(void=True, reasons=[f"baseline run failed ({base.problem}); the baseline should not fail"])
    if base.kind != "items":
        return result(void=True, reasons=["items mode needs the objective to print item JSON, but the baseline printed METRIC lines"])
    if not cand.ok or cand.kind != "items":
        why = cand.problem or "output kind differs from the baseline's"
        return result(candidate_failed=True, reasons=[f"candidate run failed ({why})"])
    valid = [i.value for run in (base, cand) for i in run.items.values() if i.status == "valid" and _finite(i.value)]
    worst = (max if obj.direction == "min" else min)(valid) if valid else None
    ids = sorted(set(base.items) | set(cand.items))
    infra, bad, base_values, cand_values, tags = [], [], {}, {}, {}
    for ident in ids:
        b, c = base.items.get(ident), cand.items.get(ident)
        if b is None or b.status != "valid" or not _finite(b.value):
            infra.append(ident)
            continue
        base_values[ident], tags[ident] = b.value, dict(b.tags)
        if c is None or c.status != "valid" or not _finite(c.value):
            cand_values[ident] = worst
            bad.append(ident)
        else:
            cand_values[ident] = c.value
    if len(infra) / len(ids) > INFRA_VOID_FRACTION or not base_values:
        return result(infra=infra, void=True,
                      reasons=[f"{len(infra)} of {len(ids)} items infra-invalid (more than {INFRA_VOID_FRACTION:.0%}) or none usable"])
    medians = {"baseline": {}, "candidate": {}}
    for arm, run in (("baseline", base), ("candidate", cand)):
        kept = [i for i in run.items.values() if i.id in base_values and i.status == "valid" and _finite(i.value)]
        if kept:
            medians[arm][obj.metric] = statistics.median(i.value for i in kept)
        for name in sorted({k for i in kept for k in i.mechanism}):
            medians[arm][name] = statistics.median(i.mechanism[name] for i in kept if name in i.mechanism)
    return result(baseline=base_values, candidate=cand_values, tags=tags, infra=infra, candidate_bad=bad, arm_medians=medians)


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


def _finite(value) -> bool:
    return _number(value) and math.isfinite(value)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value) -> bool:
    return _number(value) and math.isfinite(value) and value > 0
