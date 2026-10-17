"""`hca init`: validate the spec, take the branch, hash what is frozen, and measure the baseline against itself.

The A/A at init is the instrument check: if identical code differs by more than the useful effect
can survive, every later try would come back inconclusive, so init refuses (unless --force) and says
why. It compares the baseline worktree with a second worktree of the same tree (an empty snapshot commit), the
same kind of pair later tries use, so any asymmetry between arms shows up here and not as a phantom win.
The referee store records the original sha, the spec hash and the frozen hashes; a second init of the same
task is a reset, which the store refuses once it holds confirms unless a human sets HCA_ALLOW_RESET=1.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from .. import arms, gitops, ledger, measure, stats
from ..core import HcaError, Refusal, digest
from ..spec import load_spec
from . import common
from .quick import NOISY_FIXES

_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")


def add_arguments(parser) -> None:
    parser.add_argument("--heldout", help="directory of held-out data (outside the repo); copied to the referee store")
    parser.add_argument("--force", action="store_true", help="finish even if the baseline is too noisy")
    parser.add_argument("--reset", action="store_true", help="drop arm worktrees, archive the old ledger and start over")
    parser.add_argument("--name", metavar="TASK",
                        help="run this climb as a new task named TASK instead of hca.toml's [task] name (a fresh baseline, store and "
                             "confirm cap; hca.toml stays untouched and the rename is logged in both referee stores)")


def run(args, out=print, cwd=None, aa_blocks=None, seed=None) -> int:
    root = gitops.repo_root(cwd)
    with ledger.lock(root, "init", out):
        return _run(args, out, cwd, aa_blocks, seed)


def _run(args, out=print, cwd=None, aa_blocks=None, seed=None) -> int:
    """`aa_blocks` overrides the A/A length (tests); normally it is `extend_to`, or `blocks` without extension."""
    root = gitops.repo_root(cwd)
    spec = load_spec(root / "hca.toml")
    has_state = ledger.state_path(root).exists()
    old = ledger.read_json(ledger.state_path(root)) if has_state else {}
    name = getattr(args, "name", None)
    renamed = name.strip() if isinstance(name, str) else None
    if renamed == "":
        raise HcaError(f"--name needs a task name. Fix: use lowercase letters, digits and dashes, e.g. --name {spec.task.name}-2.")
    if renamed is not None and not _NAME.match(renamed):
        raise HcaError(f"--name {renamed!r} is not a valid task name. Fix: use lowercase letters, digits and dashes, e.g. {spec.task.name}-2.")
    if renamed is None and has_state and args.reset:              # a reset keeps the name the session was started under
        renamed = old.get("task") if old.get("spec_task") == spec.task.name else None
    if renamed == spec.task.name:                                 # --name equal to hca.toml's is no rename at all
        renamed = None
    task = renamed or spec.task.name
    previous = old.get("task") or spec.task.name                  # the task this one would follow on from
    inherited = _check_rename(root, previous, task, args.heldout) if task != previous else None
    branch = f"hca/{task}"
    gitops.refuse_unsupported(root)
    # CONTRACT NOTE: the confirm-cap guard applies to any init of a task whose store holds confirms, not only `--reset`:
    # otherwise `rm -rf .hca && hca init` would be the same loophole under another name.
    if ledger.reset_blocked(task):
        raise HcaError(f"the referee store already holds confirms for {task!r}, so starting over is refused: re-baselining would let "
                       "the confirm cap and the original baseline be rewritten by whoever runs this (the agent included). "
                       f"Fix: start a new task with its own fresh baseline and confirm cap, `hca init --name {task}-2` (hca.toml stays as "
                       "it is; the new name is logged in both referee stores). Or, if a human really wants to re-baseline this task, "
                       "`HCA_ALLOW_RESET=1 hca init --reset` (logged; the confirm count is never reset).")
    if has_state and not args.reset:
        raise HcaError(f"this repo is already initialised for {ledger.read_json(ledger.state_path(root)).get('task')!r}. "
                       "Fix: use `hca status`, or `hca init --reset` to start over.")
    dirty = gitops.dirty_state(root, spec)
    if dirty["blocking"]:
        shown = ", ".join(dirty["blocking"][:8]) + (f" and {len(dirty['blocking']) - 8} more" if len(dirty["blocking"]) > 8 else "")
        raise HcaError(f"the working tree is not clean ({shown}). Fix: commit or stash first (hca.toml must be committed too); "
                       "init needs a clean tree so the baseline is exactly a commit.")
    if dirty["ignored"]:
        out(common.untracked_warning(dirty["ignored"]))
    base_branch = gitops.current_branch(root)
    exists = gitops.branch_exists(root, branch)
    if exists and not args.reset:
        # CONTRACT NOTE: a harness committed on hca/<task> before the first init is the normal way to start (both real climbs did it).
        # Adopting is safe because every guard that protects the baseline has already passed: no state.json, no confirms in the store
        # (reset_blocked), a clean tree. Any other existing branch stays a refusal: its history may not be what init is about to freeze.
        if base_branch != branch:
            raise HcaError(f"branch {branch} already exists but is not checked out. Fix: `git checkout {branch}` to start the climb from it "
                           f"(hca init adopts its own branch), or `git branch -D {branch}` if it is stale.")
        out(f"Adopting the existing branch {branch} as it is; the baseline is its tip {gitops.head(root)[:12]}.")
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
    gitops.ensure_exclude(root)
    init_sha = gitops.head(root)
    mode = spec.objective.mode
    rng, _ = common.make_rng(seed)
    noise_dir = ledger.hca_dir(root) / "noise"
    if noise_dir.exists():
        shutil.rmtree(noise_dir)
    env = {"HCA_ITEMS": "dev"}

    # CONTRACT NOTE: the A/A needs two distinct directories like every later pair, so the candidate is an empty snapshot
    # commit (same tree, different commit) in its own worktree. It costs a second build at init.
    # Two worktrees of the same tree: the baseline at the original commit and a candidate at an empty snapshot commit
    # (same tree, different commit), built and linked identically, like every later pair of arms.
    aa_sha = gitops.commit_tree(root, gitops.tree_of(root, init_sha), init_sha, "hca A/A")
    try:
        baseline = arms.ensure_arm(root, spec, init_sha, "baseline", warn=out)
        candidate = arms.ensure_arm(root, spec, aa_sha, "candidate", logs=noise_dir, warn=out)
    except arms.ArmBuildError as exc:
        raise HcaError(f"the build fails on the original code (exit {exc.result.returncode}). Last stderr:\n{exc.result.error_tail}\n"
                       f"Fix: make `[build] command` pass at {init_sha[:12]} before starting.") from None

    prepare = {"baseline": lambda: arms.prepare_run(root, spec, init_sha, "baseline", warn=out),
               "candidate": lambda: arms.prepare_run(root, spec, aa_sha, "candidate", logs=noise_dir, warn=out)}
    noise, lines = None, []
    if mode == "blocks":
        n = aa_blocks or (spec.limits.extend_to if spec.limits.extend_to > 0 else spec.limits.blocks)
        result = measure.run_blocks(spec, baseline, candidate, n, rng, env, noise_dir, warmup=True, prepare=prepare)
        for line in result.warnings:
            out(f"warning: {line}")
        if result.void or len(result.scores) < stats.NOISE_MIN_BLOCKS:
            out(f"The baseline could not be measured: only {len(result.scores)} usable A/A blocks of {result.blocks_run} "
                f"(at least {stats.NOISE_MIN_BLOCKS} are needed). "
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
        pair = measure.run_item_pair(spec, baseline, candidate, rng, env, noise_dir, warmup=True, prepare=prepare)
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
    elif inherited is not None:                          # a renamed climb keeps the held-out set of the task it follows
        heldout_path = ledger.install_heldout(task, inherited, root)
        out(f"Held-out data inherited from {previous!r}.")
    elif args.reset:
        heldout_path = ledger.heldout_dir(task)          # a reset keeps the held-out set that is already in the store
    else:
        ledger.archive_heldout(task)                     # a stale set from an earlier session of the same name must not be used silently
        heldout_path = None
    frozen = gitops.frozen_hashes(root, spec)
    entries = gitops.tree_entries(root, init_sha)
    blobs = {p: entries[p][1] for p in frozen if p in entries}
    spec_hash = digest(root / "hca.toml")
    store = ledger.init_store(task, init_sha, spec_hash, frozen, blobs)  # the referee store is the source of truth; state.json is a cache
    if task != previous:
        ledger.log_rename(previous, task, init_sha)
    state = {"version": ledger.STATE_VERSION, "task": task, "spec_task": spec.task.name if renamed else None, "branch": branch, "init_sha": init_sha, "last_keep": init_sha,
             "last_confirmed": None, "last_confirmed_result": None, "confirms_used": 0, "frozen": frozen, "frozen_blobs": blobs, "store_id": store["store_id"],
             "spec_hash": spec_hash, "base_branch": None if base_branch in (branch, "HEAD") else base_branch, "mode": mode, "metric": spec.objective.metric,
             "direction": spec.objective.direction, "useful_effect": spec.objective.useful_effect,
             "heldout": heldout_path is not None, "noise": noise, "created": ledger.now(), "verdict_history": []}
    ledger.save_state(root, state)
    ledger.write_record(ledger.hca_dir(root), "noise.json", {"mode": mode, "noise": noise, "at": ledger.now()})
    ledger.refresh_verdicts(root, state, spec)
    arms.prune_unused(root, [init_sha])                  # drops the A/A candidate's worktree
    out(f"Initialised task {task!r} on branch {branch} at {init_sha[:12]}.")
    for line in lines:
        out(line)
    out(f"Frozen and hashed: {len(frozen)} file(s) (hca.toml included). Held-out data: "
        + (f"{sum(1 for p in heldout_path.rglob('*') if p.is_file())} file(s) in the referee store." if heldout_path else "none (confirm uses fresh blocks on dev items)."))
    out(f"Next: edit only {', '.join(spec.task.editable)}, then run `hca try -m \"what changed and why\"`.")
    return 0


def _check_rename(root, previous, task, heldout_src):
    """`init --name`: refuse a rename that would launder the previous task's record; return its held-out dir to inherit (or None).

    A new name is a fresh baseline, so it must start from something the previous task has shown: its original or its last
    confirmed commit (by tree; a FAIL's restore commit has the confirmed tree). After a failed confirm only a human may
    rename (HCA_ALLOW_RESET=1), as for a reset. A held-out set is never dropped: it is inherited, and a different
    `--heldout` is refused. The confirms of the chain keep counting against the cap (ledger.log_rename, confirm)."""
    store = ledger.load_store(previous)
    if store is None:
        return None
    confirms = ledger.read_confirms(previous)
    if confirms and confirms[-1].get("outcome") == "fail" and os.environ.get("HCA_ALLOW_RESET") != "1":
        raise Refusal(f"the last confirm of {previous!r} FAILED, so starting it over as {task!r} is refused: a new name would hide the "
                      "failure and start a fresh baseline from the same evidence. Fix: keep climbing under the current name (the "
                      f"failed keeps are already undone), or, if a human decides to start over, `HCA_ALLOW_RESET=1 hca init --reset --name {task}` "
                      "(logged in both referee stores).")
    passed = [c.get("last_keep") for c in confirms if c.get("outcome") == "pass"]
    allowed = {}
    for label, sha in (("its original", store.get("init_sha")), ("its last confirmed commit", passed[-1] if passed else None)):
        try:
            allowed[gitops.tree_of(root, sha)] = f"{label} {sha[:12]}"
        except (HcaError, TypeError):
            continue
    if allowed and gitops.tree_of(root, "HEAD") not in allowed:
        raise Refusal(f"--name starts a fresh baseline at HEAD, but HEAD is neither {' nor '.join(allowed.values())} of {previous!r}: it "
                      f"holds keeps that were never confirmed. Fix: `hca confirm` them under {previous!r} first, or check out one of "
                      "those commits.")
    held = ledger.heldout_dir(previous)
    if held is None:
        return None
    if heldout_src:
        if ledger.dir_digest(heldout_src) != ledger.dir_digest(held):
            raise Refusal(f"--heldout differs from the held-out set of {previous!r}; a renamed climb must keep it. Fix: drop --heldout "
                          "(it is inherited) or pass the same data.")
        return None
    return held


def _archive_ledger(root, out) -> None:
    """Move the old state, attempts and verdicts into `.hca/archive-<time>/` (inside .hca; nothing is deleted)."""
    hca = ledger.hca_dir(root)
    old = [hca / name for name in ("state.json", "attempts", "VERDICTS.md", "noise.json", "noise") if (hca / name).exists()]
    if not old:
        return
    stamp = time.strftime('%Y%m%dT%H%M%S')
    target, extra = hca / f"archive-{stamp}", 1
    while target.exists():                               # two resets in one second must not collide
        target, extra = hca / f"archive-{stamp}-{extra}", extra + 1
    target.mkdir()
    for path in old:
        path.rename(target / path.name)
    out(f"Archived the previous ledger to {common.rel(target, root)}.")


def _blocks_lines(report, result, spec) -> list:
    n10, n20 = stats.MIN_BLOCKS, stats.EXTENDED_BLOCKS
    d = {k: abs(stats.metric_change_pct(v, spec.objective.direction)) for k, v in report["detectable"].items()}   # the metric's own %
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
