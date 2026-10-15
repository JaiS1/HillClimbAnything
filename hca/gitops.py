"""Git operations for the referee: branch, scope check, patch capture, keep and revert.

The rule behind every function here: HCA may only ever change the files the spec declares editable.
So paths are always handed to git as explicit `:(literal)` lists, computed by our own glob matcher,
never as globs or "everything" (git errors on a pathspec that matches nothing, and a bare `git
checkout .` or `git clean` would eat the user's work). Remotes are never touched. Standard library
only; `git` runs through subprocess.run (these calls are not timed measurements).
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

from .core import HcaError, digest

CHUNK = 200                      # paths per git invocation, to stay clear of argv limits
SPEC_FILE = "hca.toml"
HCA_DIR = ".hca"


# --- running git -----------------------------------------------------------------------------------

def _run(argv, cwd, input=None, env=None):
    """subprocess.run for git that can never wait for a human: no terminal prompt, no editor, stdin closed.

    Hooks and commit signing are deliberately left enabled (they are the user's own settings); if one of
    them fails, the commit fails with git's message and the caller reverts with that message as the reason.
    """
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", **(env or {}))
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, env=full_env,
                          **({"input": input} if input is not None else {"stdin": subprocess.DEVNULL}))


def git(root, *args, check=True, input=None, env=None) -> str:
    """Run `git <args>` in `root`; return stdout. A failure raises HcaError with git's own message."""
    done = _run(["git", *args], root, input=input, env=env)
    if check and done.returncode != 0:
        raise HcaError(f"git {' '.join(args[:3])} failed (exit {done.returncode}): {(done.stderr or done.stdout).strip()}")
    return done.stdout


def _lines(text: str) -> list:
    return [line for line in text.split("\0") if line]


def _literal(paths) -> list:
    return [f":(literal){p}" for p in paths]


def _chunks(paths):
    paths = list(paths)
    for i in range(0, len(paths), CHUNK):
        yield paths[i:i + CHUNK]


def repo_root(cwd=None) -> Path:
    cwd = Path(cwd or Path.cwd())
    try:
        done = _run(["git", "rev-parse", "--show-toplevel"], cwd)
    except FileNotFoundError:
        raise HcaError("git was not found on PATH. Fix: install git.") from None
    if done.returncode != 0:
        raise HcaError(f"{cwd} is not inside a git repository. Fix: run hca from your project's checkout.")
    return Path(done.stdout.strip())


def head(root) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def current_branch(root) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()


def branch_exists(root, name) -> bool:
    return _run(["git", "rev-parse", "--verify", "-q", f"refs/heads/{name}"], root).returncode == 0


# --- glob matching ---------------------------------------------------------------------------------

def _segment_regex(segment: str) -> str:
    out, i = [], 0
    while i < len(segment):
        c = segment[i]
        if c == "*":
            while i + 1 < len(segment) and segment[i + 1] == "*":
                i += 1
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            close = segment.find("]", i + 2)
            if close == -1:
                out.append(re.escape(c))
            else:
                body = segment[i + 1:close]
                body = "^" + body[1:] if body[:1] in "!^" else body
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = close
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@functools.lru_cache(maxsize=None)
def compile_glob(pattern: str):
    """A regex for a repo-relative glob: `*` stays inside a path segment, `**` crosses directories.

    `src/**` is every file under src/, `**/x.py` is x.py at any depth (including the top), `a/**/b`
    allows zero or more directories between. A plain name matches that file or everything under that
    directory, like a git pathspec.
    """
    pattern = pattern.strip()
    while pattern.startswith("./"):
        pattern = pattern[2:]
    pattern = pattern.rstrip("/")
    segments = pattern.split("/")
    parts = []
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "**":
            parts.append(".*" if last else "(?:[^/]+/)*")
        else:
            parts.append(_segment_regex(seg) + ("" if last else "/"))
    return re.compile("".join(parts) + r"\Z")


def matches(path: str, pattern: str) -> bool:
    """True if `path` (a file, repo-relative with `/`) is selected by `pattern` or by a parent directory of it."""
    regex = compile_glob(pattern)
    parts = path.split("/")
    return any(regex.match("/".join(parts[:i])) for i in range(1, len(parts) + 1))


def matches_any(path: str, patterns) -> bool:
    return any(matches(path, p) for p in patterns)


def is_editable(path: str, spec) -> bool:
    """Editable, and neither frozen (hca.toml always is) nor a linked dependency path, which hca never writes."""
    return matches_any(path, spec.task.editable) and not is_frozen(path, spec) and not matches_any(path, spec.task.link)


def is_frozen(path: str, spec) -> bool:
    return path == SPEC_FILE or matches_any(path, spec.task.frozen)


# --- working tree state ----------------------------------------------------------------------------

def dirty_paths(root, ignore=()) -> list:
    """Paths that make the tree unclean, ignoring `.hca/` (our own directory) and any `ignore` globs (the spec's links)."""
    out = git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    entries = _lines(out)
    paths, skip = [], False
    for entry in entries:
        if skip:                              # the source half of a rename/copy record
            skip = False
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            skip = True
        if path == HCA_DIR or path.startswith(HCA_DIR + "/") or matches_any(path, ignore):
            continue
        paths.append(path)
    return paths


def dirty_state(root, spec) -> dict:
    """{blocking, ignored}: what makes the tree unclean for `init`, and what is only worth a warning.

    Blocking: any tracked modification, and any untracked file that is editable, frozen or hca.toml (the
    baseline must be exactly a commit). Ignored with a warning: other untracked files (links and `.hca/`
    are not even listed).
    """
    blocking, ignored = [], []
    untracked_now = _untracked_set(root)
    for path in dirty_paths(root, spec.task.link):
        untracked = path in untracked_now
        if untracked and not is_frozen(path, spec) and not matches_any(path, spec.task.editable):
            ignored.append(path)
        else:
            blocking.append(path)
    return {"blocking": blocking, "ignored": ignored}


def _untracked_set(root) -> set:
    return set(_lines(git(root, "ls-files", "--others", "--exclude-standard", "-z")))


def ensure_exclude(root) -> None:
    """Add `.hca/` to .git/info/exclude (not to the user's .gitignore)."""
    target = Path(root) / git(root, "rev-parse", "--git-path", "info/exclude").strip()
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    if not any(line.strip() in (".hca", ".hca/", "/.hca/") for line in existing.splitlines()):
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(("" if existing.endswith("\n") or not existing else "\n") + ".hca/\n")


def create_branch(root, name) -> None:
    git(root, "checkout", "-q", "-b", name)


def checkout_branch(root, name) -> None:
    git(root, "checkout", "-q", name)


def changed_paths(root, base) -> tuple:
    """(tracked paths that differ from `base` in the index or tree, untracked non-ignored paths)."""
    tracked = [p for p in _lines(git(root, "diff", "--name-only", "--no-renames", "-z", base)) if not _in_hca(p)]
    untracked = [p for p in _lines(git(root, "ls-files", "--others", "--exclude-standard", "-z")) if not _in_hca(p)]
    return sorted(set(tracked)), sorted(set(untracked))


def _in_hca(path: str) -> bool:
    return path == HCA_DIR or path.startswith(HCA_DIR + "/")


def _tracked_and_untracked(root) -> list:
    tracked = _lines(git(root, "ls-files", "-z"))
    return sorted(set(tracked) | set(_lines(git(root, "ls-files", "--others", "--exclude-standard", "-z"))))


# --- frozen files and the scope check --------------------------------------------------------------

def frozen_hashes(root, spec) -> dict:
    """sha256 of every present file the spec freezes (hca.toml included), keyed by repo-relative path."""
    out = {}
    for path in _tracked_and_untracked(root):
        if is_frozen(path, spec) and (Path(root) / path).is_file():
            out[path] = digest(Path(root) / path)
    return out


def scope_check(root, spec, state) -> dict:
    """Is every change inside the editable scope? Returns {ok, base, changed, untracked, untracked_outside_editable, violations}.

    Violations name the path and the reason. The check covers: HEAD still being the last keep (the
    agent must not commit), every changed or new path being editable and not frozen, and every frozen
    file still having the sha256 recorded at init (so a frozen file cannot be edited and committed,
    or deleted, or added, without being noticed).
    """
    base = state["last_keep"]
    violations = []
    if head(root) != base:
        violations.append({"path": "(HEAD)", "reason": f"HEAD is {head(root)[:12]} but the last keep is {base[:12]}: "
                           "do not commit or switch commits; hca owns this branch"})
    tracked, untracked = changed_paths(root, base)
    # CONTRACT NOTE: untracked paths under a declared `link` (node_modules, a venv) are infrastructure shared by
    # every arm, not agent edits, so they are exempt; otherwise a dependency directory that is not gitignored
    # would invalidate every try.
    untracked = [p for p in untracked if not matches_any(p, spec.task.link)]
    # CONTRACT NOTE: an untracked file outside the editable paths (a stray log, a scratch file) is recorded and
    # warned about, not a violation: it cannot be committed or measured as part of the change. Frozen paths and
    # hca.toml stay strict, and so do tracked edits outside editable.
    outside = [p for p in untracked if not is_frozen(p, spec) and not matches_any(p, spec.task.editable)]
    untracked = [p for p in untracked if p not in outside]
    for path in tracked + untracked:
        if is_frozen(path, spec):
            violations.append({"path": path, "reason": "frozen file changed" if path in tracked else "new file in a frozen path"})
        elif not matches_any(path, spec.task.editable):
            violations.append({"path": path, "reason": "outside the editable paths " + str(list(spec.task.editable))})
    recorded, now = state["frozen"], frozen_hashes(root, spec)
    flagged = {v["path"] for v in violations}
    for path in sorted(set(recorded) | set(now)):
        if path in flagged:
            continue
        if path not in now:
            violations.append({"path": path, "reason": "frozen file deleted"})
        elif path not in recorded:
            violations.append({"path": path, "reason": "frozen file added since init"})
        elif now[path] != recorded[path]:
            violations.append({"path": path, "reason": "frozen file differs from the sha256 recorded at init"})
    return {"ok": not violations, "base": base, "changed": tracked, "untracked": untracked,
            "untracked_outside_editable": outside, "violations": violations}


def editable_changes(scope) -> list:
    """The paths a try changed (all editable once the scope check passed)."""
    return sorted(set(scope["changed"]) | set(scope["untracked"]))


# --- patch, keep, revert ---------------------------------------------------------------------------

def stage(root, paths) -> None:
    for chunk in _chunks(paths):
        git(root, "add", "-A", "--", *_literal(chunk))


def capture_patch(root, spec, base) -> tuple:
    """Stage the changed editable paths and return (patch text, those paths). `git diff --cached <base>`."""
    tracked, untracked = changed_paths(root, base)
    paths = [p for p in sorted(set(tracked) | set(untracked)) if is_editable(p, spec)]
    stage(root, paths)
    return git(root, "diff", "--cached", base), paths


def raw_patch(root, base) -> str:
    """Tracked differences from `base`, unstaged and unfiltered (for attempts that failed the scope check)."""
    return git(root, "diff", base)


def _identity_args(root) -> list:
    """Fallback committer identity, only if the user has none configured (commit would otherwise fail)."""
    have = _run(["git", "config", "user.name"], root).stdout.strip() and \
        _run(["git", "config", "user.email"], root).stdout.strip()
    return [] if have else ["-c", "user.name=hca", "-c", "user.email=hca@localhost"]


def commit(root, message, allow_empty=False) -> str:
    args = [*_identity_args(root), "commit", "-q", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    git(root, *args)
    return head(root)


def keep(root, spec, scope, message) -> str:
    """Commit exactly the changed editable paths on the branch and return the new sha."""
    paths = [p for p in editable_changes(scope) if is_editable(p, spec)]
    stage(root, paths)
    return commit(root, message)


def revert(root, spec, last_keep) -> dict:
    """Put the editable paths back to `last_keep`; touch nothing else.

    Unstage the changed editable paths, check them out from `last_keep` (tracked ones), and delete
    untracked files that match an editable glob (these are only files the agent created). Anything
    outside the editable globs, including untracked files of the user's, is left exactly as it was.
    """
    tracked, untracked = changed_paths(root, last_keep)
    touched = [p for p in sorted(set(tracked) | set(untracked)) if is_editable(p, spec)]
    for chunk in _chunks(touched):
        git(root, "reset", "-q", "--", *_literal(chunk))
    in_keep = set(_lines(git(root, "ls-tree", "-r", "--name-only", "-z", last_keep)))
    for chunk in _chunks([p for p in touched if p in in_keep]):
        git(root, "checkout", last_keep, "--", *_literal(chunk))
    deleted = []
    for path in _lines(git(root, "ls-files", "--others", "--exclude-standard", "-z")):   # after the reset: also files the agent had `git add`ed
        if _in_hca(path) or not is_editable(path, spec):
            continue                                # also skips frozen and linked paths
        full = Path(root) / path
        if full.is_file() or full.is_symlink():
            full.unlink()
            deleted.append(path)
            _remove_empty_parents(Path(root), full.parent)
    return {"restored": [p for p in touched if p in in_keep], "deleted": deleted}


def _remove_empty_parents(root: Path, directory: Path) -> None:
    """rmdir upward while the directory is empty and still inside the repo (rmdir cannot remove a non-empty one)."""
    root = root.resolve()
    directory = directory.resolve()
    while directory != root and root in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def restore_to(root, spec, target, message) -> str:
    """Make the editable paths equal to those at `target` as a new commit on the branch; return its sha.

    Used by a failed confirm. It first reverts uncommitted editable edits to HEAD, then restores or
    removes only the editable files that differ between HEAD and `target`.
    """
    here = head(root)
    revert(root, spec, here)
    differing = [p for p in _lines(git(root, "diff", "--name-only", "--no-renames", "-z", target, here)) if is_editable(p, spec)]
    in_target = set(_lines(git(root, "ls-tree", "-r", "--name-only", "-z", target)))
    for chunk in _chunks([p for p in differing if p in in_target]):
        git(root, "checkout", target, "--", *_literal(chunk))
    for chunk in _chunks([p for p in differing if p not in in_target]):
        git(root, "rm", "-q", "-f", "--", *_literal(chunk))
    return commit(root, message, allow_empty=True)


def diff_stat(root, base, tip) -> str:
    return git(root, "diff", "--stat", "--no-renames", base, tip).rstrip()
