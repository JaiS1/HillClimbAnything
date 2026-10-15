"""Arm directories: where each side of a comparison actually runs.

Both arms are detached git worktrees under `.hca/arms/<sha12>`: the baseline at the last keep (or the
original, for confirm) and the candidate at the snapshot commit. Neither is the user's checkout, so a
benchmark that walks its cwd sees the same kind of tree on both sides, and a file that only exists in the
checkout (an ignored cache, a stray log) cannot advantage the candidate (S4). Untracked dependencies
(node_modules, a venv, model weights) are symlinked in from the repo root through `link`, identically for
both. A build runs once per sha; a marker file records its exit status so a broken build is not mistaken
for a built one.

An arm is trusted only after it is verified: HEAD must be the sha and no tracked file may be modified
(untracked build outputs are fine). A tampered arm is removed and recreated with a warning (S2). Nothing
outside `.hca/` is ever deleted here.
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


class ArmBuildError(HcaError):
    """The build command failed in an arm; `.result` is the RunResult, `.logs` where its output went."""

    def __init__(self, message, result=None, logs=None, sha=None):
        super().__init__(message)
        self.result, self.logs, self.sha = result, logs, sha


def verify_arm(arm, sha) -> str | None:
    """None if the arm directory is exactly `sha` with no modified tracked file; else a short description of what is wrong."""
    arm = Path(arm)
    if not (arm / ".git").exists():
        return "it is not a git worktree"
    try:
        have = gitops.git(arm, "rev-parse", "HEAD").strip()
        if have != sha:
            return f"its HEAD is {have[:12]}, not {sha[:12]}"
        dirty = gitops.git(arm, "status", "--porcelain", "--untracked-files=no", env={"GIT_OPTIONAL_LOCKS": "0"})
    except HcaError as exc:
        return f"git could not read it ({exc})"
    if dirty.strip():
        shown = ", ".join(line[3:] for line in dirty.splitlines()[:3])
        return f"tracked files were modified ({shown})"
    return None


def _remove_arm(root, target) -> None:
    root = Path(root)
    if (target / ".git").exists():
        gitops.git(root, "worktree", "remove", "--force", str(target), check=False)
    if target.exists() or os.path.islink(target):
        _remove_inside_hca(root, target)
    gitops.git(root, "worktree", "prune", check=False)


def ensure_worktree(root, spec, sha, warn=None) -> Path:
    """The verified worktree for `sha` (created, or recreated if damaged), with the spec's `link` paths symlinked in."""
    root = Path(root)
    target = arm_dir(root, sha)
    arms_root(root).mkdir(parents=True, exist_ok=True)
    if target.exists():
        problem = verify_arm(target, sha)
        if problem:
            if warn:
                warn(f"warning: arm {sha12(sha)} was modified or damaged ({problem}); recreating it from the commit.")
            _remove_arm(root, target)
            _marker(root, sha).unlink(missing_ok=True)
    if not target.exists():
        gitops.git(root, "worktree", "prune")
        gitops.git(root, "worktree", "add", "-q", "--detach", str(target), sha)
        _marker(root, sha).unlink(missing_ok=True)
    for link in spec.task.link:
        source, dest = root / link, target / link
        if source.exists() and not os.path.lexists(dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source.resolve(), dest)
    return target


def ensure_arm(root, spec, sha, role="baseline", logs=None, timeout=None, warn=None) -> Path:
    """The arm for `sha`: a verified worktree plus a successful build (run once per sha, then remembered).

    `logs` is where the build's output goes (default: next to the arm). Raises ArmBuildError if the build fails.
    """
    root = Path(root)
    target = ensure_worktree(root, spec, sha, warn)
    if spec.build is None:
        return target
    marker = _marker(root, sha)
    if marker.exists():
        try:
            if json.loads(marker.read_text(encoding="utf-8")).get("status") == 0:
                return target
        except ValueError:
            pass                                       # an unreadable marker is no marker: rebuild
    logs = Path(logs) if logs else arms_root(root) / f"{sha12(sha)}.build"
    limit = spec.limits.command_seconds if timeout is None else max(1.0, min(spec.limits.command_seconds, timeout))
    result = runner.run(spec.build.command, target, logs, "build", limit, deny=spec.env.deny)
    write_json(marker, {"sha": sha, "status": result.returncode, "timed_out": result.timed_out,
                        "seconds": round(result.seconds, 3), "command": list(spec.build.command),
                        "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    if not result.ok:
        raise ArmBuildError(f"the {role} build failed for {sha12(sha)} ({'timed out' if result.timed_out else f'exit {result.returncode}'}). "
                            f"Last stderr:\n{result.error_tail}\nFix: make `[build] command` pass at that commit; logs are in {logs}.",
                            result, logs, sha)
    problem = verify_arm(target, sha)
    if problem:
        marker.unlink(missing_ok=True)
        raise HcaError(f"the build changed tracked files in the {role} arm {sha12(sha)} ({problem}). "
                       "Fix: make the build write only untracked outputs; arms must stay exactly the commit.")
    return target


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
    """Remove arm worktrees (and their build markers and logs) whose sha is not in `keep_shas`; best effort, never raises.

    Every keep and every candidate makes a new arm, so without this they pile up. Only entries named like an
    arm (<sha12>, <sha12>.built, <sha12>.build) inside .hca/arms are touched. Failures are swallowed on purpose:
    pruning runs after the attempt record is written and must not turn a finished try into an error.
    """
    root = Path(root)
    removed = []
    try:
        base = arms_root(root)
        if not base.exists():
            return []
        wanted = {sha12(s) for s in keep_shas if s}
        for entry in sorted(base.iterdir()):
            stem, _, suffix = entry.name.partition(".")
            if not (len(stem) == 12 and all(c in "0123456789abcdef" for c in stem)) or suffix not in ("", "built", "build"):
                continue
            if stem in wanted:
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    if (entry / ".git").exists():
                        gitops.git(root, "worktree", "remove", "--force", str(entry), check=False)
                    if entry.exists():
                        _remove_inside_hca(root, entry)
                elif entry.exists() or os.path.islink(entry):
                    entry.unlink()
                removed.append(entry.name)
            except (OSError, HcaError):
                continue
        if removed:
            gitops.git(root, "worktree", "prune", check=False)
    except (OSError, HcaError):
        pass
    return removed
