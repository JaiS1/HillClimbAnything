"""`hca compare -- <command A> -- <command B>`: a rigorous A/B of two commands, with no git, no hca.toml, no referee store.

The zero-setup first result. It uses the same instrument as `hca try`: randomised A/B blocks (each block runs both commands
once, in a random order), per-block log ratios, the exact signed-rank test with the Hodges-Lehmann estimate and interval,
and the two-look rule (10 blocks, extended to 20 once when the result looks promising). With `--blocks N` there is one look.

Both commands run without a shell (write `sh -c '...'` if you need one), in their own process group, exactly as in the
runner. The default metric is each run's wall time; `--metric NAME` reads `METRIC NAME=value` from stdout (or the file
named by HCA_RESULT_FILE) instead. A failing run or a missing metric is invalid, never a result.

Exit codes: 0 B is better than A by a significant amount (and passes the keep rule for --min-effect when given),
1 it is not, 2 invalid (a run failed or lacked the metric), 3 usage, 4 internal error (cli).
"""

from __future__ import annotations

import json
import math
import statistics
import sys
import tempfile
import time
from pathlib import Path

from .. import measure, stats
from ..core import HcaError
from ..spec import DEFAULT_DENY
from . import common

DEFAULT_METRIC = "wall_s"
FIXED_MIN_BLOCKS = 7        # the fewest blocks at which one look can reach p <= 0.01 (2**-7 < 0.01)
TWO_LOOK_MIN_BLOCKS = 8     # the fewest blocks at which a look at one-sided alpha 0.005 can reject (2**-8 < 0.005)
FIRST_LOOK, SECOND_LOOK = stats.MIN_BLOCKS, stats.EXTENDED_BLOCKS


def add_arguments(parser) -> None:
    parser.add_argument("--metric", help=f"which `METRIC NAME=value` line to compare (default: each run's wall time, {DEFAULT_METRIC})")
    parser.add_argument("--direction", choices=("min", "max"), default="min", help="min if lower is better (default)")
    parser.add_argument("--min-effect", metavar="X%", help='smallest difference worth caring about, e.g. "5%%"; adds a keep-rule line and '
                        "makes exit 0 require it (default: report only)")
    parser.add_argument("--blocks", type=int, help=f"blocks at the first look (default {FIRST_LOOK}). Alone, it is a fixed number of blocks with one "
                        f"look (at least {FIXED_MIN_BLOCKS}); with --extend-to it is the first of two looks (at least {TWO_LOOK_MIN_BLOCKS})")
    parser.add_argument("--extend-to", type=int, metavar="M", help=f"total blocks after the one extension, taken when B (or A) looks promising "
                        f"(default {SECOND_LOOK} when --blocks is not given); e.g. --blocks 20 --extend-to 40 for noisy targets")
    parser.add_argument("--warmup", type=int, default=1, help="discarded runs of each command first (default 1)")
    parser.add_argument("--cwd-a", metavar="DIR", help="directory to run command A in (default: the current directory)")
    parser.add_argument("--cwd-b", metavar="DIR", help="directory to run command B in (default: the current directory)")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds allowed per run (default 300)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of the report")
    parser.add_argument("--out", metavar="DIR", help="also write compare.json (the report) and runs.jsonl (every run: block, order, value, wall time) here")
    parser.add_argument("--sep", default="--", metavar="TOKEN", help="the token that separates command A from command B (default `--`; "
                        "use e.g. `::` when command A itself contains `--`)")
    parser.add_argument("--seed", type=int, help="seed for the block order (default: random)")
    parser.add_argument("command", nargs="*", help="`-- <command A ...> -- <command B ...>`")


def split_commands(args) -> tuple:
    """(A, B) from the argv after the first `--`: `<A...> -- <B...>`. Splits on the first `--` only, so B may contain more."""
    if getattr(args, "stray", None):
        raise HcaError(f"unexpected argument(s) before `--`: {' '.join(args.stray)}. "
                       "Fix: put both commands after `--`, e.g. `hca compare -- <command A> -- <command B>`.")
    tail = list(args.command)
    sep = getattr(args, "sep", None) or "--"
    hint = ("Command A may not itself contain `--`; if it must, pick another separator: `hca compare --sep :: -- <A> :: <B>`. "
            "Use `sh -c '...'` if a command needs a shell.")
    if sep not in tail:
        raise HcaError(f"hca compare needs two commands, each after its own `--`: `hca compare [options] -- <command A> {sep} <command B>`. "
                       f"Fix: add the separator `{sep}` between them. {hint}")
    cut = tail.index(sep)
    a, b = tail[:cut], tail[cut + 1:]
    if not a:
        raise HcaError(f"command A is empty (nothing before `{sep}`). Fix: `hca compare -- <command A> {sep} <command B>`. {hint}")
    if not b:
        raise HcaError(f"command B is empty (nothing after `{sep}`). Fix: `hca compare -- <command A> {sep} <command B>`. {hint}")
    return a, b


def run(args, out=print, cwd=None, progress=None) -> int:
    """`progress(line)` gets one line per block (default: stderr, unless --json is given and stdout is not a terminal)."""
    a, b = split_commands(args)
    direction = args.direction
    wall = args.metric is None
    if wall and direction == "max":
        raise HcaError("wall time is lower-is-better, so --direction max would invert the answer. "
                       "Fix: pass --metric NAME for a metric where higher is better, or drop --direction.")
    if progress is None:
        quiet = args.json and not sys.stdout.isatty()
        progress = (lambda line: None) if quiet else (lambda line: print(line, file=sys.stderr, flush=True))
    metric = DEFAULT_METRIC if wall else args.metric
    useful = stats.parse_effect(args.min_effect, direction) if args.min_effect else 0.0
    extend_to = getattr(args, "extend_to", None)
    if extend_to is not None or args.blocks is None:
        first = args.blocks or FIRST_LOOK
        second = extend_to or SECOND_LOOK
        looks = 2
        if first < TWO_LOOK_MIN_BLOCKS:
            raise HcaError(f"--blocks must be at least {TWO_LOOK_MIN_BLOCKS} with two looks (fewer cannot reach p <= 0.005 even if B wins every block).")
        if second <= first:
            raise HcaError(f"--extend-to ({second}) must be larger than the first look ({first} blocks).")
    else:
        first, second, looks = args.blocks, args.blocks, 1
        if first < FIXED_MIN_BLOCKS:
            raise HcaError(f"--blocks must be at least {FIXED_MIN_BLOCKS} (fewer cannot reach p <= 0.01 even if B wins every block).")
    if args.warmup < 0:
        raise HcaError("--warmup must be 0 or more.")
    if not args.timeout > 0:
        raise HcaError("--timeout must be positive.")
    here = Path(cwd) if cwd is not None else Path.cwd()
    dirs = {}
    for arm, given in (("baseline", args.cwd_a), ("candidate", args.cwd_b)):
        dirs[arm] = Path(given) if given else here
        if not dirs[arm].is_dir():
            raise HcaError(f"--cwd-{'a' if arm == 'baseline' else 'b'} {dirs[arm]} is not a directory.")
    rng, seed = common.make_rng(args.seed)
    commands = {"baseline": a, "candidate": b}

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="hca-compare-") as tmp:
        tmp = Path(tmp)

        def measure_one(index):
            return measure.measure_blocks(commands, metric, direction, dirs["baseline"], dirs["candidate"], 1, rng, {}, tmp,
                                          args.timeout, DEFAULT_DENY, first_block=index, wall_only=wall)

        scores, a_values, b_values = [], [], []

        def blocks(count, start):
            """Run `count` more blocks one at a time, so a failure stops the compare at once. Returns the failure or None."""
            for index in range(start, start + count):
                progress(f"block {index + 1}/{first if index < first else second}, {time.monotonic() - started:.0f} s elapsed")
                result = measure_one(index)
                if result.void or result.voided_blocks or result.candidate_failures or result.candidate_invalid_blocks or not result.scores:
                    return _failure(tmp, index, result)
                scores.extend(result.scores)
                a_values.extend(result.arm_values["baseline"].get(metric, []))
                b_values.extend(result.arm_values["candidate"].get(metric, []))
            return None

        failure = None
        for k in range(args.warmup):                      # a warm-up is a discarded block: random order, same code path, never scored
            progress(f"warm-up {k + 1}/{args.warmup}, {time.monotonic() - started:.0f} s elapsed")
            failure = _failure_or_none(measure_one(-(k + 1)), tmp, -(k + 1))
            if failure:
                break
        if not failure:
            failure = blocks(first, 0)
        extended = False
        decision = None
        if not failure:
            decision = _decide(scores, useful, 1, looks, direction)
            if looks == 2 and _wants_extension(scores, useful, direction):
                failure = blocks(second - first, first)
                extended = True
                if not failure:
                    decision = _decide(scores, useful, 2, looks, direction)
        wall_seconds = _total_seconds(tmp)
        raw = (tmp / "runs.jsonl").read_text(encoding="utf-8") if getattr(args, "out", None) and (tmp / "runs.jsonl").exists() else ""
    if failure:
        return _invalid(failure, args, out, a, b, raw)
    report = _report(scores, a_values, b_values, decision, useful, direction, metric, wall, args, extended, looks, seed, wall_seconds, a, b, first)
    if getattr(args, "out", None):
        target = Path(args.out)
        target.mkdir(parents=True, exist_ok=True)
        (target / "compare.json").write_text(json.dumps(report["json"], indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (target / "runs.jsonl").write_text(raw, encoding="utf-8")
    if args.json:
        out(json.dumps(report["json"], indent=2, allow_nan=False))
    else:
        for line in report["lines"]:
            out(line)
    return common.EXIT_KEEP if report["json"]["keep"] else common.EXIT_REVERT


# --- decisions ---------------------------------------------------------------------------------------

def _decide(scores, useful, look, looks, direction) -> dict:
    """The try's keep rule on these scores (positive = B better), plus the two-sided p and the interval at the same level."""
    decision = stats.keep_decision(scores, useful, look=look, looks=looks, direction=direction)
    level = decision["alpha"]
    lower, upper = common.block_interval(scores, level)
    p_up, p_down = stats.signed_rank_p(scores), stats.signed_rank_p([-s for s in scores])
    decision.update(level=level, p_two_sided=min(1.0, 2 * min(p_up, p_down)),
                    worse=p_down <= level and decision["estimate"] < 0,
                    ci_change_pct=sorted(_pct(v, direction) for v in (lower, upper)))
    return decision


def _wants_extension(scores, useful, direction) -> bool:
    """Extend once if B looks promising (the try's rule) or, so that A-vs-B is not judged asymmetrically, if A does."""
    ahead = stats.keep_decision(scores, useful, look=1, looks=2, direction=direction)["verdict"] == "extend"
    behind = stats.keep_decision([-s for s in scores], useful, look=1, looks=2, direction=direction)["verdict"] == "extend"
    return ahead or behind


def _pct(log_value, direction):
    return stats.metric_change_pct(log_value, direction) if math.isfinite(log_value) else None


# --- failures ----------------------------------------------------------------------------------------

def _failure_or_none(result, tmp, index):
    if result.void or result.voided_blocks or result.candidate_failures or result.candidate_invalid_blocks:
        return _failure(tmp, index, result)
    return None


def _failure(tmp, index, result) -> dict:
    """The last failed run in runs.jsonl: which command, what went wrong, and the tail of its stderr."""
    found = None
    log = Path(tmp) / "runs.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") == "run" and not record.get("ok"):
                found = record
    if found is None:
        return {"command": None, "block": index, "problem": "; ".join(result.warnings) or "no usable run", "error_tail": ""}
    return {"command": "A" if found["arm"] == "baseline" else "B", "block": found["block"], "problem": found.get("problem") or "failed",
            "error_tail": (found.get("error_tail") or "").strip()}


def _invalid(failure, args, out, a, b, raw="") -> int:
    where = "a warm-up run" if failure["block"] < 0 else f"block {failure['block'] + 1}"
    who = f"command {failure['command']}" if failure["command"] else "a command"
    cmd = {"A": a, "B": b}.get(failure["command"])
    text = f"Invalid: {who} failed in {where}: {failure['problem']}."
    if failure["error_tail"]:
        text += f" Last stderr:\n{failure['error_tail']}"
    text += "\nA comparison is only made when every run succeeds and reports the metric. Fix the command and rerun."
    data = {"verdict": "invalid", "better": False, "worse": False, "keep": False, "exit": common.EXIT_INVALID,
            "command": failure["command"], "argv": cmd, "block": failure["block"], "problem": failure["problem"],
            "error_tail": failure["error_tail"]}
    if getattr(args, "out", None):                    # a failed compare is the one you most need the raw runs of
        target = Path(args.out)
        target.mkdir(parents=True, exist_ok=True)
        (target / "compare.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (target / "runs.jsonl").write_text(raw, encoding="utf-8")
    if args.json:
        out(json.dumps(data, indent=2, allow_nan=False))
    else:
        out(text)
    return common.EXIT_INVALID


def _total_seconds(tmp) -> float:
    total = 0.0
    log = Path(tmp) / "runs.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("kind") == "run":
                total += record.get("wall_seconds") or 0.0
    return total


# --- the report --------------------------------------------------------------------------------------

def _report(scores, a_values, b_values, decision, useful, direction, metric, wall, args, extended, looks, seed, wall_seconds, a, b, first) -> dict:
    n = len(scores)
    level = decision["level"]
    est = decision["estimate_change_pct"]                      # B vs A in the metric's own terms
    ci = decision["ci_change_pct"]
    sd = stats.robust_sd(scores)
    a_sd = stats.robust_sd([math.log(v) for v in a_values]) if len(a_values) >= 2 else None
    detect = abs(stats.metric_change_pct(stats.detectable_shift(sd, n, level), direction))
    better = decision["reject"] and decision["estimate"] > 0
    worse = decision["worse"]
    verdict = "b_better" if better else "a_better" if worse else "no_difference"
    keep = decision["verdict"] == "keep"
    p = decision["p_two_sided"]
    ci_level = 100 * (1 - 2 * level)

    unit = " s" if wall else ""
    lines = [f"Compared A and B on {'wall time (' + metric + ')' if wall else 'METRIC ' + metric}: {n} blocks"
             + (f" (extended from {first})" if extended else "") + f", order randomised, {args.warmup} warm-up run(s) of each discarded, "
             f"{wall_seconds:.0f} s of runs.",
             f"Medians: A {_med(a_values)}{unit}, B {_med(b_values)}{unit}."]
    detects = f"this N detects about {detect:.1f}%" if sd else "no variation seen, so any consistent difference shows"
    noise = f"Noise: ±{stats.to_percent(sd):.1f}% per block" if sd else "Noise: no variation seen (the values were identical across blocks)"
    if a_sd:
        noise += f" (A's own runs spread ±{stats.to_percent(a_sd):.1f}%)"
    lines.append(noise + (f"; {detects}." if sd else "."))
    ci_text = f"{ci_level:.0f}% CI " + _range(ci)
    if verdict == "no_difference":
        lines.append(f"No detectable difference (estimate {est:+.1f}%, {ci_text}, p={p:.2g}, which would need to be ≤ {2 * level:g}); {detects}.")
    else:
        speed = f"{1 / (1 + est / 100):.2f}× the speed, " if wall else ""
        lines.append(f"B is {abs(est):.1f}% {_word(est, wall, direction)} than A ({speed}{ci_text}, p={p:.2g}, {n} blocks).")
    min_line = None
    if args.min_effect:
        floor = decision["keep_floor_change_pct"]
        better_by = -est if direction == "min" else est                  # how much better B is, positive when better
        why = []
        if not decision["reject"]:
            why.append(f"not significant (p ≤ {level:g} one-sided is needed; two-sided p={p:.2g})")
        if decision["estimate"] < decision["keep_floor"]:
            why.append(f"the estimate, {better_by:.1f}% better, is below the keep floor of {abs(floor):.1f}% (half of {args.min_effect})")
        min_line = (f"Keep rule (--min-effect {args.min_effect}): B beats A by at least that much: " +
                    (f"yes (significant, and the estimate, {better_by:.1f}% better, clears the keep floor of {abs(floor):.1f}%, "
                     f"half the useful effect)." if keep else "no (" + "; ".join(why) + ")."))
        lines.append(min_line)
    lines.append("To go further, `hca init` runs a guarded climb on your code (frozen evaluator, keep or revert each change, held-out confirm); "
                 "see the README.")
    data = {"verdict": verdict, "better": better, "worse": worse, "keep": keep, "exit": common.EXIT_KEEP if keep else common.EXIT_REVERT,
            "metric": metric, "direction": direction, "wall_time": wall,
            "blocks": n, "looks": looks, "extended": extended, "seed": seed, "warmup": args.warmup,
            "estimate_pct": est, "ci_pct": ci, "ci_level": ci_level / 100, "p": p, "p_value": p, "alpha_one_sided": level,
            "noise": {"block_sd_pct": stats.to_percent(sd), "a_run_sd_pct": stats.to_percent(a_sd) if a_sd else None, "detectable_pct": detect},
            "min_effect": ({"requested": args.min_effect, "keep_floor_pct": abs(decision["keep_floor_change_pct"]), "passes": keep}
                           if args.min_effect else None),
            "a": {"argv": a, "median": _median(a_values), "values": a_values},
            "b": {"argv": b, "median": _median(b_values), "values": b_values},
            "scores": scores}
    return {"lines": lines, "json": data}


def _word(est, wall, direction) -> str:
    """'faster' / 'slower' for wall time, else 'lower (better)' etc. `est` is B's change in the metric's own terms."""
    if wall:
        return "faster" if est < 0 else "slower"
    if est < 0:
        return "lower (better)" if direction == "min" else "lower (worse)"
    return "higher (worse)" if direction == "min" else "higher (better)"


def _range(ci) -> str:
    """'10.1–14.6%' when the interval excludes zero, else '-1.2 to +2.0%'."""
    low, high = ci
    if low is not None and high is not None and (low > 0 or high < 0):
        return f"{min(abs(low), abs(high)):.1f}–{max(abs(low), abs(high)):.1f}%"
    return f"{'-inf' if low is None else format(low, '+.1f')} to {'+inf' if high is None else format(high, '+.1f')}%"


def _median(values):
    return statistics.median(values)


def _med(values) -> str:
    return f"{_median(values):.4g}"
