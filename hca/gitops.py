"""Git operations for the referee: snapshots, scope check, keep and revert.

The rule behind every function here: HCA may only ever change the files the spec declares editable,
and what it measures is exactly what it commits. So the candidate is never "whatever is in the
checkout": it is a commit built in a *private* index (`GIT_INDEX_FILE`) from the last keep's tree plus
the working-tree contents of the changed editable paths. The user's real index is never read to decide
what the candidate is (S1: staged content used to slip into commits). Paths are always handed to git
as explicit `:(literal)` lists, computed by our own glob matcher, never as globs or "everything".
Remotes are never touched. Standard library only; every git call has a timeout.
"""

from __future__ import annotations

import contextlib
import functools
import itertools
import os
import re
import subprocess
from pathlib import Path

from .core import HcaError, digest

CHUNK = 200                      # paths per git invocation, to stay clear of argv limits
SPEC_FILE = "hca.toml"
HCA_DIR = ".hca"
GIT_TIMEOUT = 120                # seconds for any one git call: a hung hook must not hold the lock forever
KEEP_SUBJECT = re.compile(r"hca keep \d{4}: ")
RESTORE_SUBJECT = "hca revert to confirmed"
_counter = itertools.count()


# --- running git -----------------------------------------------------------------------------------

def _run(argv, cwd, input=None, env=None, timeout=GIT_TIMEOUT):
    """subprocess.run for git that can never wait for a human: no terminal prompt, no editor, stdin closed.

    Hooks and commit signing are the user's own settings and stay enabled for the commands that use them.
    A call that outlives `timeout` raises HcaError (the child is killed) instead of holding the lock forever.
    """
    full_env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", **(env or {}))
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=full_env, timeout=timeout,
                              **({"input": input} if input is not None else {"stdin": subprocess.DEVNULL}))
    except subprocess.TimeoutExpired:
        raise HcaError(f"`{' '.join(argv[:4])}` did not finish within {timeout} s (a hook or credential helper may be "
                       "waiting for input). Fix: run it by hand to see what it waits for.") from None


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


def branch_tip(root, name) -> str:
    return git(root, "rev-parse", f"refs/heads/{name}").strip()


def tree_of(root, sha) -> str:
    return git(root, "rev-parse", f"{sha}^{{tree}}").strip()


def refuse_unsupported(root) -> None:
    """v1 does not handle submodules (a worktree arm would lack them) or a half-finished merge/rebase/cherry-pick."""
    root = Path(root)
    if (root / ".gitmodules").exists():
        raise HcaError("this repo has submodules (.gitmodules), which hca v1 does not support: a baseline worktree would "
                       "not have them. Fix: run hca in a repo without submodules, or vendor them.")
    gitdir = Path(git(root, "rev-parse", "--absolute-git-dir").strip())
    for name, what in (("MERGE_HEAD", "merge"), ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
                       ("rebase-merge", "rebase"), ("rebase-apply", "rebase (or git am)")):
        if (gitdir / name).exists():
            raise HcaError(f"a {what} is in progress in this repo. Fix: finish it (or abort it) first, then run hca again.")


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


NOTEBOOK = "IDEAS.md"           # explore mode's notebook at the repo root
_notebook = {"ignored": False}


def ignore_notebook(on) -> None:
    """Explore mode in use: treat a root IDEAS.md as noise even if the editable globs cover it. In line mode an editable
    IDEAS.md is an ordinary edit (a non-editable one is never an edit anyway, and is not warned about)."""
    _notebook["ignored"] = bool(on)


def is_noise(path: str) -> bool:
    """Files hca never treats as edits: `__pycache__/` anywhere, `*.pyc`, `.DS_Store` (tools write them as a side effect of running),
    and, in explore mode, the agent's notebook `IDEAS.md` at the repo root (never measured, snapshotted or reverted)."""
    parts = path.split("/")
    return "__pycache__" in parts[:-1] or parts[-1].endswith(".pyc") or parts[-1] == ".DS_Store" or (path == NOTEBOOK and _notebook["ignored"])


def _in_hca(path: str) -> bool:
    return path == HCA_DIR or path.startswith(HCA_DIR + "/")


# --- working tree state ----------------------------------------------------------------------------

def dirty_paths(root, ignore=()) -> list:
    """Paths that make the tree unclean, ignoring `.hca/` (our own directory), noise files and any `ignore` globs (the spec's links)."""
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
        if _in_hca(path) or is_noise(path) or matches_any(path, ignore):
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
            if path != NOTEBOOK:
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


def staged_outside_editable(root, spec) -> list:
    """Paths staged in the user's real index outside the editable scope (read only; hca ignores the index but says so)."""
    out = git(root, "diff", "--cached", "--name-only", "--no-renames", "-z", env={"GIT_OPTIONAL_LOCKS": "0"})
    return sorted(p for p in _lines(out) if not _in_hca(p) and not is_noise(p) and not is_editable(p, spec))


# --- the private index -----------------------------------------------------------------------------

@contextlib.contextmanager
def private_index(root):
    """A throwaway index file for `GIT_INDEX_FILE`, so the user's real index is never read or written to build a candidate."""
    directory = Path(root) / HCA_DIR / "tmp"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"index.{os.getpid()}.{next(_counter)}"
    try:
        yield {"GIT_INDEX_FILE": str(path)}
    finally:
        for leftover in (path, path.with_name(path.name + ".lock")):
            with contextlib.suppress(OSError):
                leftover.unlink()


def _overlay_lists(root, base, env) -> tuple:
    """(tracked paths whose working-tree content differs from `base`, untracked non-ignored paths), `.hca/` and noise removed.

    `read-tree base` fills the private index, so "tracked" means "in the base tree" and "untracked"
    means "not in it": whatever the user's real index says is irrelevant. `git diff` (index vs working
    tree) compares content when the stat data is absent, which a fresh index always is.
    """
    git(root, "read-tree", base, env=env)
    tracked = _lines(git(root, "diff", "--name-only", "--no-renames", "-z", env=env))
    untracked = _lines(git(root, "ls-files", "--others", "--exclude-standard", "-z", env=env))
    keep = lambda p: not _in_hca(p) and not is_noise(p)       # noqa: E731
    return sorted({p for p in tracked if keep(p)}), sorted({p for p in untracked if keep(p)})


# --- frozen files and the scope check --------------------------------------------------------------

def _glob_prefix(pattern: str) -> str:
    """The leading literal directory part of a glob ('bench/**' -> 'bench', '**/x' -> ''), where a walk can start."""
    pattern = pattern.strip()
    while pattern.startswith("./"):
        pattern = pattern[2:]
    parts = []
    for seg in pattern.rstrip("/").split("/"):
        if any(c in seg for c in "*?["):
            break
        parts.append(seg)
    return "/".join(parts)


def frozen_hashes(root, spec) -> dict:
    """sha256 of every present file the spec freezes (hca.toml included), keyed by repo-relative path.

    This walks the filesystem, not git's listings: a gitignored dataset or a linked path that `frozen`
    matches is hashed and protected too. Files stream in 1 MiB chunks. Symlinked directories are followed
    (a linked dataset is the point), once each.
    """
    root = Path(root)
    starts = sorted({_glob_prefix(p) for p in spec.task.frozen} | {SPEC_FILE})
    if "" in starts:
        starts = [""]
    else:
        starts = [s for s in starts if not any(s != o and s.startswith(o + "/") for o in starts)]
    out, seen = {}, set()
    for start in starts:
        full = root / start if start else root
        if full.is_file():
            if is_frozen(start, spec):
                out[start] = digest(full)
            continue
        if not full.is_dir():
            continue
        for current, dirs, files in os.walk(full, followlinks=True):
            real = os.path.realpath(current)
            if real in seen:
                dirs[:] = []
                continue
            seen.add(real)
            rel_dir = os.path.relpath(current, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            dirs[:] = sorted(d for d in dirs if d != ".git" and not (rel_dir == "" and d == HCA_DIR))
            for name in sorted(files):
                rel = f"{rel_dir}/{name}" if rel_dir else name
                path = os.path.join(current, name)
                if is_frozen(rel, spec) and os.path.isfile(path):
                    out[rel] = digest(path)
    return out


def tree_entries(root, sha) -> dict:
    """{path: (mode, blob id)} of every file in commit `sha`."""
    out = {}
    for entry in _lines(git(root, "ls-tree", "-r", "-z", sha)):
        meta, _, path = entry.partition("\t")
        mode, _kind, oid = meta.split(" ")
        out[path] = (mode, oid)
    return out


def frozen_blobs(root, spec, sha) -> dict:
    """{path: blob id} of the frozen files that exist in commit `sha` (hca.toml included)."""
    return {p: oid for p, (_, oid) in tree_entries(root, sha).items() if is_frozen(p, spec)}


def scope_check(root, spec, state, base=None) -> dict:
    """Is every change inside the editable scope? Returns {ok, base, changed, untracked, untracked_outside_editable, violations}.

    Violations name the path and the reason. The check covers: HEAD still being the last keep (the agent
    must not commit), every changed or new path in the working tree being editable and not frozen, and
    every frozen file (tracked, untracked or ignored) still having the sha256 recorded at init. The change
    list is computed against the last keep's tree in a private index, so staged-only changes in the user's
    index cannot hide anything: they are not part of the candidate at all.

    `base` (explore mode: the sampled parent's snapshot) replaces the last keep as what the change list is computed
    against; HEAD is still checked against the last keep, because the branch only ever moves on a keep (B3).
    """
    keep = state["last_keep"]
    base = base or keep
    violations = []
    if head(root) != keep:
        violations.append({"path": "(HEAD)", "reason": f"HEAD is {head(root)[:12]} but the last keep is {keep[:12]}: "
                           "do not commit or switch commits; hca owns this branch. If a previous `hca try` was killed in the "
                           "middle of a keep, run any hca command again: it finishes or discards the pending keep"})
    with private_index(root) as env:
        tracked, untracked = _overlay_lists(root, base, env)
    # CONTRACT NOTE: untracked paths under a declared `link` (node_modules, a venv) are infrastructure shared by
    # every arm, not agent edits, so they are exempt; otherwise a dependency directory that is not gitignored
    # would invalidate every try.
    untracked = [p for p in untracked if not matches_any(p, spec.task.link)]
    # CONTRACT NOTE: an untracked file outside the editable paths (a stray log, a scratch file) is recorded and
    # warned about, not a violation: it cannot be committed or measured as part of the change. Frozen paths and
    # hca.toml stay strict, and so do tracked edits outside editable (they cannot enter the snapshot, but an agent
    # touching the evaluator or the readme is told so before anything is measured).
    outside = [p for p in untracked if not is_frozen(p, spec) and not matches_any(p, spec.task.editable)]
    untracked = [p for p in untracked if p not in outside]
    outside = [p for p in outside if p != NOTEBOOK]
    for path in tracked + untracked:
        if is_frozen(path, spec):
            violations.append({"path": path, "reason": "frozen file changed" if path in tracked else "new file in a frozen path"})
        elif not is_editable(path, spec):
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


def verify_history(root, spec, init_sha, tip, blobs) -> list:
    """Problems with the committed history init_sha..tip (empty list = fine). Used by confirm and, cheaply, by try.

    The recorded state is agent-writable, so the committed history is checked on its own terms: the tip
    descends from the original, the diff touches only editable paths, every commit in between is one of
    hca's own (an `hca keep` or `hca revert to confirmed`, never a merge), and the frozen files in both
    commits are the blobs recorded at init.
    """
    problems = []
    if init_sha == tip:
        return problems
    if _run(["git", "merge-base", "--is-ancestor", init_sha, tip], root).returncode != 0:
        return [f"the last keep {tip[:12]} does not descend from the original {init_sha[:12]}"]
    outside = [p for p in _lines(git(root, "diff", "--name-only", "--no-renames", "-z", init_sha, tip)) if not is_editable(p, spec)]
    if outside:
        problems.append("the committed change touches paths outside the editable scope: " + ", ".join(outside[:5])
                        + (f" and {len(outside) - 5} more" if len(outside) > 5 else ""))
    for line in git(root, "log", "--reverse", "--format=%H%x1f%P%x1f%s", f"{init_sha}..{tip}").splitlines():
        sha, parents, subject = (line.split("\x1f") + ["", ""])[:3]
        if len(parents.split()) != 1:
            problems.append(f"commit {sha[:12]} is a merge or a root commit; only hca's own linear commits are allowed")
        elif not (KEEP_SUBJECT.match(subject) or subject == RESTORE_SUBJECT):
            problems.append(f"commit {sha[:12]} ({subject[:50]!r}) is not an hca keep or an hca restore commit")
    for sha, label in ((init_sha, "the original"), (tip, "the last keep")):
        entries = tree_entries(root, sha)
        for path, oid in sorted(blobs.items()):
            if path not in entries:
                problems.append(f"frozen file {path} is missing in {label} {sha[:12]}")
            elif entries[path][1] != oid:
                problems.append(f"frozen file {path} differs from the recorded original in {label} {sha[:12]}")
    return problems


# --- snapshots, patch, keep, revert ----------------------------------------------------------------

def _identity_args(root) -> list:
    """Fallback committer identity, only if the user has none configured (commit would otherwise fail)."""
    have = _run(["git", "config", "user.name"], root).stdout.strip() and \
        _run(["git", "config", "user.email"], root).stdout.strip()
    return [] if have else ["-c", "user.name=hca", "-c", "user.email=hca@localhost"]


def commit_tree(root, tree, parent, message) -> str:
    """A commit object for `tree` on top of `parent` (no branch moves, no hooks).

    CONTRACT NOTE: because a keep is `commit-tree` + `update-ref` (A2.1), the user's pre-commit / commit-msg hooks
    and commit signing do not run on hca's commits. The measured tree is committed byte for byte; a hook that
    rewrote or rejected it would break "committed tree == measured tree". The old `git commit` path ran hooks.
    """
    return git(root, *_identity_args(root), "commit-tree", tree, "-p", parent, "-m", message).strip()


def _update_index(root, env, paths) -> None:
    for chunk in _chunks(paths):
        git(root, "update-index", "--add", "--remove", "-z", "--stdin", env=env, input="\0".join(chunk) + "\0")


def snapshot(root, spec, base, message, paths) -> dict:
    """The candidate commit: `base`'s tree with the working-tree contents of `paths` (editable) overlaid.

    Built in a private index: `read-tree base`, then `update-index --add --remove` per path (a missing file is
    removed), `write-tree`, `commit-tree -p base`. Returns {sha, tree, paths}. Nothing in the user's repo moves.
    """
    paths = [p for p in paths if is_editable(p, spec) and not is_noise(p)]
    with private_index(root) as env:
        git(root, "read-tree", base, env=env)
        _update_index(root, env, paths)
        tree = git(root, "write-tree", env=env).strip()
    return {"sha": commit_tree(root, tree, base, message), "tree": tree, "paths": paths}


def working_tree_hash(root, spec, base) -> str:
    """The tree `snapshot` would produce right now (no commit). Keep compares this with the measured snapshot's tree."""
    with private_index(root) as env:
        tracked, untracked = _overlay_lists(root, base, env)
        _update_index(root, env, [p for p in tracked + untracked if is_editable(p, spec)])
        return git(root, "write-tree", env=env).strip()


def uncommitted_patch(root, spec, base) -> str:
    """The working tree's editable changes against `base` as a patch (built from a private-index tree, nothing staged)."""
    with private_index(root) as env:
        tracked, untracked = _overlay_lists(root, base, env)
        _update_index(root, env, [p for p in tracked + untracked if is_editable(p, spec)])
        tree = git(root, "write-tree", env=env).strip()
    return git(root, "diff", "--no-renames", base, tree)


def attempt_ref(n: int) -> str:
    return f"refs/hca/attempts/{n:04d}"


def set_ref(root, ref, sha) -> None:
    git(root, "update-ref", ref, sha)


def patch_between(root, base, sha) -> str:
    return git(root, "diff", "--no-renames", base, sha)


def raw_patch(root, base) -> str:
    """Tracked differences between the working tree and `base`, for attempts that failed the scope check (private index)."""
    with private_index(root) as env:
        git(root, "read-tree", base, env=env)
        return git(root, "diff", "--no-renames", env=env)


def advance_branch(root, branch, new_sha, old_sha) -> None:
    """Move the branch to the measured snapshot, then reset the index to it (mixed: the working tree already matches on editable paths).

    CONTRACT NOTE: the mixed reset (as A2.1 specifies) resets the whole index, so anything the user had staged
    outside the editable paths becomes an unstaged working-tree change. `hca try` warns about such staging first.
    """
    git(root, "update-ref", "-m", "hca keep", f"refs/heads/{branch}", new_sha, old_sha)
    git(root, "reset", "-q", new_sha)


def finish_advance(root, sha) -> None:
    """Idempotent second half of advance_branch, used when a crash left the branch moved but the index not."""
    git(root, "reset", "-q", sha)


def revert(root, spec, last_keep) -> dict:
    """Put the editable paths back to `last_keep`; touch nothing else.

    Editable paths that differ from `last_keep` are restored from it through a private index
    (`checkout-index`), and untracked files that match an editable glob are deleted (only files the
    agent created). The user's real index is then reset for exactly those paths so nothing stays staged.
    Anything outside the editable globs, including untracked files of the user's, is left as it was.
    """
    with private_index(root) as env:
        tracked, untracked = _overlay_lists(root, last_keep, env)
        restore = [p for p in tracked if is_editable(p, spec)]
        remove = [p for p in untracked if is_editable(p, spec)]
        for chunk in _chunks(restore):
            git(root, "checkout-index", "-f", "-q", "-z", "--stdin", env=env, input="\0".join(chunk) + "\0")
    for chunk in _chunks(sorted(restore + remove)):
        git(root, "reset", "-q", "--", *_literal(chunk))
    deleted = []
    for path in remove:
        full = Path(root) / path
        if full.is_file() or full.is_symlink():
            full.unlink()
            deleted.append(path)
            _remove_empty_parents(Path(root), full.parent)
    return {"restored": restore, "deleted": deleted}


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


def restore_commit(root, spec, target, message) -> dict:
    """A commit on top of HEAD whose editable paths equal those at `target`, built in a private index. Returns {sha, here}."""
    here = head(root)
    want, have = tree_entries(root, target), tree_entries(root, here)
    with private_index(root) as env:
        git(root, "read-tree", here, env=env)
        for path in sorted(set(want) | set(have)):
            if not is_editable(path, spec) or want.get(path) == have.get(path):
                continue
            if path in want:
                mode, oid = want[path]
                git(root, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}", env=env)
            else:
                git(root, "update-index", "--force-remove", "--", path, env=env)
        tree = git(root, "write-tree", env=env).strip()
    return {"sha": commit_tree(root, tree, here, message), "here": here}


def restore_to(root, spec, target, message) -> str:
    """Make the editable paths equal to those at `target` as a new commit on the branch; return its sha.

    Used by a failed confirm: build the restore commit privately, move the branch to it, then sync the
    working tree's editable paths (which also discards uncommitted editable edits).
    """
    made = restore_commit(root, spec, target, message)
    advance_branch(root, current_branch(root), made["sha"], made["here"])
    revert(root, spec, made["sha"])
    return made["sha"]


def diff_stat(root, base, tip) -> str:
    return git(root, "diff", "--stat", "--no-renames", base, tip).rstrip()


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def file_sizes(root, sha) -> dict:
    """{path: size in bytes} of every file in commit `sha` (ls-tree -l; submodule entries skipped)."""
    out = {}
    for entry in _lines(git(root, "ls-tree", "-r", "-l", "-z", sha)):
        meta, _, path = entry.partition("\t")
        size = meta.split()[-1]
        if size.isdigit():
            out[path] = int(size)
    return out


def numstat(root, spec, base, tip, ignore_eol=False) -> dict:
    """{editable path: lines changed} between two commits (or trees): max(added, deleted), so a modified line counts once;
    a binary file counts as 1. `ignore_eol` ignores CR at the end of lines (a CRLF rewrite then counts as no change)."""
    out = {}
    for line in git(root, "diff", "--numstat", "--no-renames", *(["--ignore-cr-at-eol"] if ignore_eol else []), base, tip).splitlines():
        added, deleted, path = line.split("\t", 2)
        if is_editable(path, spec):
            out[path] = max(int(added), int(deleted)) if added.isdigit() and deleted.isdigit() else 1
    return out


def is_ancestor(root, older, newer) -> bool:
    return _run(["git", "merge-base", "--is-ancestor", older, newer], root).returncode == 0


def keep_commits(root, init_sha, tip) -> list:
    """[(sha, attempt n)] of the `hca keep NNNN` commits in init_sha..tip, oldest first; restore commits as (sha, None)."""
    out = []
    for line in git(root, "log", "--reverse", "--format=%H%x1f%s", f"{init_sha}..{tip}").splitlines():
        sha, subject = (line.split("\x1f") + [""])[:2]
        if KEEP_SUBJECT.match(subject):
            out.append((sha, int(subject[9:13])))
        elif subject == RESTORE_SUBJECT:
            out.append((sha, None))
    return out
