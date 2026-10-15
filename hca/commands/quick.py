"""`hca quick -- <cmd>`: how noisy is this command, and what win could a try detect? Needs no spec.

It runs the command as both arms (an A/A) in randomised blocks and reports stats.noise_report in a
few plain sentences, so "too noisy" shows up in two minutes instead of after fifty wasted tries.
"""

from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

from .. import measure, runner, stats
from ..core import HcaError, write_json
from ..spec import DEFAULT_DENY

MIN_BLOCKS, MAX_BLOCKS = 5, 20
NOISY_FIXES = ("report more items per run (the median over items cancels noise)",
               "make each run longer than about a second",
               "close other load (browser, game, indexers, other builds) and check `hca doctor`")


def add_arguments(parser) -> None:
    parser.add_argument("--blocks", type=int, help=f"A/A blocks to run (default: fit the budget, {MIN_BLOCKS} to {MAX_BLOCKS})")
    parser.add_argument("--budget-seconds", type=float, default=120.0, help="time to spend (default 120)")
    parser.add_argument("--useful", default="5%", help='smallest effect worth finding, e.g. "5%%" (default 5%%)')
    parser.add_argument("--direction", choices=("min", "max"), default="min", help="min if lower is better (default)")
    parser.add_argument("--metric", help="which METRIC line to use when the command prints several")
    parser.add_argument("--save", action="store_true", help="write the report to .hca/quick.json")
    parser.add_argument("command", nargs="*", help="the command to measure, after `--`")


def run(args, out=print) -> int:
    command = list(args.command)
    if not command:
        raise HcaError("hca quick needs a command after `--`, e.g. `hca quick -- python3 bench.py`.")
    useful = stats.parse_effect(args.useful, args.direction)
    if args.blocks is not None and args.blocks < MIN_BLOCKS:
        raise HcaError(f"--blocks must be at least {MIN_BLOCKS} (noise needs a few A/A blocks to estimate).")
    budget = args.budget_seconds
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="hca-quick-") as tmp:
        tmp = Path(tmp)
        started = time.monotonic()
        probe = runner.run(command, cwd, tmp / "probe", "probe", budget, deny=DEFAULT_DENY)   # also a warm-up
        if not probe.ok:
            why = "timed out" if probe.timed_out else f"exited with status {probe.returncode}"
            out(f"The command {why} on its first run, so there is nothing to measure. Last stderr:\n{probe.error_tail}")
            return 2
        metric, source = _choose_metric(probe, args.metric)
        per_run = max(probe.seconds, 1e-3)
        if args.blocks:
            blocks, deadline = args.blocks, None
        else:
            blocks = min(MAX_BLOCKS, int((budget - probe.seconds) / (2 * per_run)))
            if blocks < MIN_BLOCKS:
                out(f"One run takes about {per_run:.1f} s, so {MIN_BLOCKS} A/A blocks need about {2 * MIN_BLOCKS * per_run:.0f} s, "
                    f"over the {budget:.0f} s budget. Raise --budget-seconds or measure a shorter command.")
                return 2
            deadline = started + budget
        result = measure.measure_blocks(command, metric, args.direction, cwd, cwd, blocks, random.Random(), {}, tmp / "aa",
                                        budget, DEFAULT_DENY, deadline=deadline, wall_fallback=source == "wall")
    for line in result.warnings:
        out(f"warning: {line}")
    if result.void or len(result.scores) < MIN_BLOCKS:
        out(f"Only {len(result.scores)} usable blocks (need {MIN_BLOCKS}): the command failed or ran out of budget. "
            "See the warnings above; fix the command and rerun.")
        return 2
    report = stats.noise_report(result.scores, useful)
    for line in _lines(report, result, metric, source, args.useful, args.direction):
        out(line)
    if args.save:
        target = cwd / ".hca"
        target.mkdir(exist_ok=True)
        write_json(target / "quick.json", {"command": command, "metric": metric, "source": source, "useful": args.useful,
                                           "blocks": result.scored_blocks, "scores": result.scores, "report": _jsonable(report)})
        out(f"Saved {target / 'quick.json'}.")
    return 0


def _choose_metric(probe, wanted):
    """(metric name, 'metric'|'items'|'wall') from the first run's stdout."""
    parsed = measure.parse_output(measure._read_stdout(probe.stdout_path), wanted)
    if parsed.kind == "items":
        return wanted or parsed.metric or "metric", "items"
    if parsed.kind == "none" and parsed.problem and parsed.problem.startswith("item JSON"):
        raise HcaError(f"the command's last line is item JSON but is unusable: {parsed.problem}. Fix: see the objective output contract.")
    if parsed.kind == "metrics":
        if wanted:
            if parsed.problem:
                raise HcaError(f"{parsed.problem}. Fix: pass a --metric that the command prints.")
            return wanted, "metric"
        if len(parsed.metrics) == 1:
            return next(iter(parsed.metrics)), "metric"
        raise HcaError(f"the command prints several METRIC lines ({', '.join(sorted(parsed.metrics))}). Fix: pass --metric NAME.")
    if wanted:
        raise HcaError(f"--metric {wanted!r} was given but the command printed no METRIC line or item JSON.")
    return measure.WALL_METRIC, "wall"


def _lines(report, result, metric, source, useful_text, direction="min"):
    n10, n20 = stats.MIN_BLOCKS, stats.EXTENDED_BLOCKS
    d = {k: abs(stats.metric_change_pct(v, direction)) for k, v in report["detectable"].items()}   # in the metric's own %
    t = 2 * result.median_run_seconds
    what = {"wall": "the command's own wall time (it printed no METRIC line or item JSON)",
            "metric": f"METRIC {metric}", "items": f"item JSON ({metric})"}[source]
    lines = [f"Measured: {what}; {result.blocks_run} A/A blocks in {result.wall_seconds:.0f} s.",
             f"Noise: ±{report['noise_sd_pct']:.1f}% (robust SD of A/A block log ratios, {report['blocks']} blocks)."
             + (" No noise seen at all: if the value is deterministic, `mode = \"items\"` fits better." if report["noise_sd"] == 0 else ""),
             f"Detectable at 80% power: {d[n10]:.1f}% at {n10} blocks (~{n10 * t:.0f} s per try), "
             f"{d[n20]:.1f}% at {n20} blocks (~{n20 * t:.0f} s)."]
    verdict = report["verdict"]
    if verdict == "ok":
        lines.append(f"Your useful effect {useful_text}: OK ({n10} blocks are enough).")
    elif verdict == "needs-extension":
        lines.append(f"Your useful effect {useful_text}: needs {n20} blocks (a try extends from {n10} to {n20} when it looks promising).")
    else:
        lines.append(f"Your useful effect {useful_text}: too noisy here; even {n20} blocks only detect {d[n20]:.1f}%. Try: "
                     + "; ".join(NOISY_FIXES) + ".")
    if report["aa_offset_flag"]:
        lines.append(f"Note: identical arms differed by {report['aa_centre_pct']:+.1f}% on average. Look for warm-up, caching "
                     "or thermal drift; random order keeps the test valid but the noise estimate is inflated.")
    if source == "wall":
        lines.append("To track it later, have your script print `METRIC wall_s=<seconds>`.")
    lines.append("Next: write hca.toml (see docs/ARCHITECTURE-v1.md) and run `hca init`.")
    return lines


def _jsonable(report):
    return {k: ({str(a): b for a, b in v.items()} if isinstance(v, dict) else v) for k, v in report.items()}
