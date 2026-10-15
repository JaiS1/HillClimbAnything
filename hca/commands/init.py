"""`hca init`: validate the spec, take the branch, hash what is frozen, and measure the baseline against itself.

The A/A at init is the instrument check: if identical code differs by more than the useful effect
can survive, every later try would come back inconclusive, so init refuses (unless --force) and says
why. It compares the baseline worktree with the repo root, the same pair of directories later tries
use, so any asymmetry between arms shows up here and not as a phantom win.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import arms, gitops, ledger, measure, stats
from ..core import HcaError, digest
from ..spec import load_spec
from . import common
from .quick import NOISY_FIXES


def add_arguments(parser) -> None:
    parser.add_argument("--heldout", help="directory of held-out data (outside the repo); copied to the referee store")
    parser.add_argument("--force", action="store_true", help="finish even if the baseline is too noisy")
    parser.add_argument("--reset", action="store_true", help="drop arm worktrees, archive the old ledger and start over")


def run(args, out=print, cwd=None, aa_blocks=None, seed=None) -> int:
    """`aa_blocks` overrides the A/A length (tests); normally it is `extend_to`, or `blocks` without extension."""
    root = gitops.repo_root(cwd)
    spec = load_spec(root / "hca.toml")
    task = spec.task.name
    branch = f"hca/{task}"
    has_state = ledger.state_path(root).exists()
    if has_state and not args.reset:
        raise HcaError(f"this repo is already initialised for {ledger.read_json(ledger.state_path(root)).get('task')!r}. "
                       "Fix: use `hca status`, or `hca init --reset` to start over.")
    dirty = gitops.dirty_paths(root, spec.task.link)
    if dirty:
        shown = ", ".join(dirty[:8]) + (f" and {len(dirty) - 8} more" if len(dirty) > 8 else "")
        raise HcaError(f"the working tree is not clean ({shown}). Fix: commit or stash first (hca.toml must be committed too); "
                       "init needs a clean tree so the baseline is exactly a commit.")
    base_branch = gitops.current_branch(root)
    exists = gitops.branch_exists(root, branch)
    if exists and not args.reset:
        raise HcaError(f"branch {branch} already exists. Fix: `git branch -D {branch}` if it is stale, or `hca init --reset` to adopt it.")
    heldout_src = args.heldout
    if heldout_src:                                      # validate before anything is changed
        src = Path(heldout_src).resolve()
        if not src.is_dir():
            raise HcaError(f"--heldout {src} is not a directory. Fix: pass the directory that holds the held-out data.")
        if src == root.resolve() or root.resolve() in src.parents:
            raise HcaError(f"--heldout {src} is inside the repo, where the agent can read it. Fix: keep it outside the checkout.")

    if args.reset:
        arms.prune_arms(root)
        _archive_ledger(root, out)
        if exists and gitops.current_branch(root) != branch:
            gitops.checkout_branch(root, branch)
    ledger.archive_old_confirms(task)                    # a new session starts a new confirm count
    gitops.ensure_exclude(root)
    init_sha = gitops.head(root)
    mode = spec.objective.mode
    rng, _ = common.make_rng(seed)
    noise_dir = ledger.hca_dir(root) / "noise"
    if noise_dir.exists():
        shutil.rmtree(noise_dir)
    env = {"HCA_ITEMS": "dev"}

    built = arms.build_candidate(root, spec, noise_dir)
    if built is not None and not built.ok:
        raise HcaError(f"the build fails on the original code (exit {built.returncode}). Last stderr:\n{built.error_tail}\n"
                       f"Fix: make `[build] command` pass at {init_sha[:12]} before starting.")
    baseline = arms.ensure_arm(root, spec, init_sha)

    noise, lines = None, []
    if mode == "blocks":
        n = aa_blocks or (spec.limits.extend_to if spec.limits.extend_to > 0 else spec.limits.blocks)
        result = measure.run_blocks(spec, baseline, root, n, rng, env, noise_dir)
        for line in result.warnings:
            out(f"warning: {line}")
        if result.void or len(result.scores) < 5:
            out(f"The baseline could not be measured: only {len(result.scores)} usable A/A blocks of {result.blocks_run}. "
                f"Fix the objective command so it runs cleanly on the original code (see {common.rel(noise_dir / 'runs.jsonl', root)}).")
            return common.EXIT_INVALID
        report = stats.noise_report(result.scores, spec.objective.useful)
        noise = {k: ({str(a): b for a, b in v.items()} if isinstance(v, dict) else v) for k, v in report.items()}
        lines = _blocks_lines(report, result, spec)
        if report["verdict"] == "too-noisy" and not args.force:
            for line in lines:
                out(line)
            out("Refusing to initialise: the baseline is too noisy for this useful effect. Try: " + "; ".join(NOISY_FIXES)
                + ". Or rerun with --force to accept inconclusive tries.")
            return common.EXIT_INVALID
    else:
        pair = measure.run_item_pair(spec, baseline, root, rng, env, noise_dir)
        if pair.void or pair.candidate_failed:
            out("The baseline could not be measured: " + "; ".join(pair.reasons) + ". Fix the objective command so it runs cleanly on the original code.")
            return common.EXIT_INVALID
        drift = max((abs(pair.candidate[i] - pair.baseline[i]) / max(1.0, abs(pair.baseline[i])) for i in pair.baseline), default=0.0)
        problems = []
        if len(pair.baseline) < common.ITEMS_MIN_UNITS:
            problems.append(f"only {len(pair.baseline)} items, but items mode needs at least {common.ITEMS_MIN_UNITS}")
        if drift > 1e-9:
            problems.append("the same code scored differently across two runs (items mode needs deterministic scores; use mode = \"blocks\")")
        noise = {"items": len(pair.baseline), "aa_max_relative_difference": drift}
        lines = [f"Baseline: {len(pair.baseline)} items measured twice; largest difference between the two runs {drift:.3g}."]
        if problems and not args.force:
            for line in lines + problems:
                out(line)
            out("Refusing to initialise (rerun with --force to accept).")
            return common.EXIT_INVALID

    if not exists:
        gitops.create_branch(root, branch)
    if heldout_src:
        heldout_path = ledger.install_heldout(task, heldout_src, root)
    elif args.reset:
        heldout_path = ledger.heldout_dir(task)          # a reset keeps the held-out set that is already in the store
    else:
        ledger.archive_heldout(task)                     # a stale set from an earlier session of the same name must not be used silently
        heldout_path = None
    frozen = gitops.frozen_hashes(root, spec)
    state = {"version": ledger.STATE_VERSION, "task": task, "branch": branch, "init_sha": init_sha, "last_keep": init_sha,
             "last_confirmed": None, "last_confirmed_result": None, "confirms_used": 0, "frozen": frozen,
             "spec_hash": digest(root / "hca.toml"), "base_branch": None if base_branch in (branch, "HEAD") else base_branch, "mode": mode, "metric": spec.objective.metric,
             "direction": spec.objective.direction, "useful_effect": spec.objective.useful_effect,
             "heldout": heldout_path is not None, "noise": noise, "created": ledger.now(), "verdict_history": []}
    ledger.save_state(root, state)
    ledger.write_record(ledger.hca_dir(root), "noise.json", {"mode": mode, "noise": noise, "at": ledger.now()})
    ledger.refresh_verdicts(root, state, spec)
    out(f"Initialised task {task!r} on branch {branch} at {init_sha[:12]}.")
    for line in lines:
        out(line)
    out(f"Frozen and hashed: {len(frozen)} file(s) (hca.toml included). Held-out data: "
        + (f"{sum(1 for p in heldout_path.rglob('*') if p.is_file())} file(s) in the referee store." if heldout_path else "none (confirm uses fresh blocks on dev items)."))
    out(f"Next: edit only {', '.join(spec.task.editable)}, then run `hca try -m \"what changed and why\"`.")
    return 0


def _archive_ledger(root, out) -> None:
    """Move the old state, attempts and verdicts into `.hca/archive-<time>/` (inside .hca; nothing is deleted)."""
    hca = ledger.hca_dir(root)
    old = [hca / name for name in ("state.json", "attempts", "VERDICTS.md", "noise.json", "noise") if (hca / name).exists()]
    if not old:
        return
    target = hca / f"archive-{time.strftime('%Y%m%dT%H%M%S')}"
    target.mkdir()
    for path in old:
        path.rename(target / path.name)
    out(f"Archived the previous ledger to {common.rel(target, root)}.")


def _blocks_lines(report, result, spec) -> list:
    n10, n20 = stats.MIN_BLOCKS, stats.EXTENDED_BLOCKS
    d = report["detectable_pct"]
    useful = spec.objective.useful_effect
    lines = [f"Baseline A/A ({result.blocks_run} blocks, {result.wall_seconds:.0f} s): noise ±{report['noise_sd_pct']:.1f}%; "
             f"detectable at 80% power {d[n10]:.1f}% at {n10} blocks, {d[n20]:.1f}% at {n20}."]
    verdict = report["verdict"]
    if verdict == "ok":
        lines.append(f"Useful effect {useful}: OK ({n10} blocks are enough).")
    elif verdict == "needs-extension":
        lines.append(f"Useful effect {useful}: needs {n20} blocks (a promising try extends from {n10} to {n20}).")
    else:
        lines.append(f"Useful effect {useful}: too noisy here (accepted with --force); even {n20} blocks only detect {d[n20]:.1f}%.")
    if report["aa_offset_flag"]:
        lines.append(f"Note: identical arms differed by {report['aa_centre_pct']:+.1f}% on average; look for warm-up, caching or thermal drift.")
    return lines
