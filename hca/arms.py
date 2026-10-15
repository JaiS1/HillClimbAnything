"""Arm directories: where each side of a comparison actually runs.

The baseline arm for a commit is a detached git worktree under `.hca/arms/<sha12>`, created once and
reused, so the user's checkout is never disturbed while the baseline is measured. Untracked
dependencies (node_modules, a venv, model weights) are symlinked in from the repo root, because a
fresh worktree has none of them. A build runs once per sha; a marker file records its exit status so
a broken build is not mistaken for a built one. The candidate arm is the repo root itself, built once
per try. Nothing outside `.hca/` is ever deleted here.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import gitops, runner
from .core import HcaError, write_json

ARMS_DIR = "arms"


def sha12(sha: str) -> str:
    return sha[:12]


def arms_root(root) -> Path:
    return Path(root) / gitops.HCA_DIR / ARMS_DIR


def arm_dir(root, sha) -> Path:
    return arms_root(root) / sha12(sha)


def _marker(root, sha) -> Path:
    return arms_root(root) / f"{sha12(sha)}.built"


def ensure_worktree(root, spec, sha) -> Path:
    """The baseline worktree for `sha` (created if missing), with the spec's `link` paths symlinked in."""
    root = Path(root)
    target = arm_dir(root, sha)
    arms_root(root).mkdir(parents=True, exist_ok=True)
    if not (target / ".git").exists():
        gitops.git(root, "worktree", "prune")
        if target.exists():
            _remove_inside_hca(root, target)           # a half-made leftover from an interrupted run
        gitops.git(root, "worktree", "add", "-q", "--detach", str(target), sha)
    for link in spec.task.link:
        source, dest = root / link, target / link
        if source.exists() and not os.path.lexists(dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source.resolve(), dest)
    return target


def ensure_arm(root, spec, sha) -> Path:
    """The baseline arm for `sha`: worktree plus a successful build (run once, then remembered)."""
    target = ensure_worktree(root, spec, sha)
    if spec.build is None:
        return target
    marker = _marker(root, sha)
    if marker.exists():
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("status") == 0:
                return target
        except ValueError:
            pass                                       # an unreadable marker is no marker: rebuild
    logs = arms_root(root) / f"{sha12(sha)}.build"
    result = runner.run(spec.build.command, target, logs, "build", spec.limits.command_seconds,
                        env_extra={"HCA_ARM": "baseline"}, deny=spec.env.deny)
    write_json(marker, {"sha": sha, "status": result.returncode, "timed_out": result.timed_out,
                        "seconds": round(result.seconds, 3), "command": list(spec.build.command),
                        "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    if not result.ok:
        raise HcaError(f"the baseline build failed for {sha12(sha)} ({'timed out' if result.timed_out else f'exit {result.returncode}'}). "
                       f"Last stderr:\n{result.error_tail}\nFix: make `[build] command` pass on the original code; logs are in {logs}.")
    return target


def build_candidate(root, spec, out_dir):
    """Build the candidate (the repo root with the agent's edits). Returns the RunResult, or None if there is no build."""
    if spec.build is None:
        return None
    return runner.run(spec.build.command, root, out_dir, "build", spec.limits.command_seconds,
                      env_extra={"HCA_ARM": "candidate"}, deny=spec.env.deny)


def _inside_hca(root, path) -> bool:
    hca = (Path(root) / gitops.HCA_DIR).resolve()
    return hca in Path(path).resolve().parents


def _remove_inside_hca(root, path) -> None:
    """rmtree, but only for a directory that really lives under <root>/.hca (and never through symlinks)."""
    if not _inside_hca(root, path):
        raise HcaError(f"refusing to delete {path}: it is not under {Path(root) / gitops.HCA_DIR}")
    if os.path.islink(path):
        os.unlink(path)
    else:
        shutil.rmtree(path)


def prune_arms(root) -> list:
    """Remove every arm worktree and build marker (init --reset, doctor --clean). Returns what was removed."""
    root = Path(root)
    removed = []
    base = arms_root(root)
    if not base.exists():
        return removed
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and (entry / ".git").exists():
            gitops.git(root, "worktree", "remove", "--force", str(entry), check=False)
        if entry.exists() or os.path.islink(entry):
            if entry.is_dir() and not entry.is_symlink():
                _remove_inside_hca(root, entry)
            else:
                entry.unlink()
        removed.append(entry.name)
    gitops.git(root, "worktree", "prune", check=False)
    return removed


def prune_unused(root, keep_shas) -> list:
    """Remove baseline worktrees (and their build markers and logs) whose sha is not in .

    Every keep makes a new baseline for the next try, so without this the arms pile up one per keep.
    Only entries named like an arm (<sha12>, <sha12>.built, <sha12>.build) inside .hca/arms are touched.
    """
    root = Path(root)
    base = arms_root(root)
    if not base.exists():
        return []
    wanted = {sha12(s) for s in keep_shas if s}
    removed = []
    for entry in sorted(base.iterdir()):
        stem, _, suffix = entry.name.partition(".")
        if not (len(stem) == 12 and all(c in "0123456789abcdef" for c in stem)) or suffix not in ("", "built", "build"):
            continue
        if stem in wanted:
            continue
        if entry.is_dir() and not entry.is_symlink():
            if (entry / ".git").exists():
                gitops.git(root, "worktree", "remove", "--force", str(entry), check=False)
            if entry.exists():
                _remove_inside_hca(root, entry)
        elif entry.exists() or os.path.islink(entry):
            entry.unlink()
        removed.append(entry.name)
    if removed:
        gitops.git(root, "worktree", "prune", check=False)
    return removed
