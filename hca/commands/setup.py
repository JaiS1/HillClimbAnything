"""`hca setup`: install the hca agent skill where coding agents look for it, and keep it current.

The skill (skills/hca/SKILL.md plus references/*.md) is what tells an agent how to drive the referee. Two user-level
locations cover the agents we know of: `~/.claude/skills/hca/` (Claude Code, also read by VS Code Copilot) and
`~/.agents/skills/hca/` (the cross-agent location read by Codex, Cursor, Gemini CLI and Copilot). `--project` installs
into the current repo instead, for a team that wants the skill checked in beside the code.

Safety rules, in the order they matter:
  * nothing is written before a plan is printed and confirmed (`--dry-run` stops after the plan, `--yes` skips the question);
  * a directory is only ever replaced or removed if it carries our `.hca-skill.json` marker, or the user said `--force`;
  * whatever is replaced or removed is moved to `~/.hca/backups/skills-<time>/<target>/`, never left inside a skills
    directory (an agent would load a backup there as a second, duplicate skill);
  * a new copy is built in a temp sibling directory and renamed into place, so a crash never leaves half a skill;
  * a symlinked target is refused (it is probably a development checkout) and, with `--force`, only the link is removed.
Standard library only.
"""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import hca as _hca
from .. import gitops
from ..core import HcaError, Refusal

MARKER = ".hca-skill.json"
SKILL_PACKAGE = "hca.skill"       # the wheel ships skills/hca as this package (pyproject.toml package-dir)
AGENTS = ("claude", "agents", "all")

NEXT_CLAUDE = ("Claude Code: new sessions pick it up. In a session that is already running, run /reload-skills "
               "if the skills folder was just created.")
NEXT_OTHERS = "Codex, Cursor, Gemini CLI, Copilot: restart the agent if the skill doesn't appear."
NEXT_THEN = "Then run `hca doctor` to check the setup, and `hca quick -- <your benchmark>` to measure how noisy it is."


def add_arguments(parser) -> None:
    parser.add_argument("--agent", choices=AGENTS, default="all",
                        help="claude = ~/.claude/skills/hca; agents = ~/.agents/skills/hca (Codex, Cursor, Gemini CLI, Copilot); default all")
    parser.add_argument("--project", action="store_true",
                        help="install into <repo root or current directory>/.claude and /.agents instead of your home directory")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and stop; write nothing")
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation (required when stdin is not a terminal)")
    parser.add_argument("--force", action="store_true",
                        help="replace a directory this command did not install (it is backed up first); remove a symlink at the target (the link only)")
    parser.add_argument("--uninstall", action="store_true", help="remove the skill from the chosen locations (only if hca setup installed it; backed up first)")


# --- the skill's source files -----------------------------------------------------------------------

def checkout_dir() -> Path:
    """skills/hca in a source checkout (hca/ sits next to skills/); it does not exist in an installed wheel."""
    return Path(_hca.__file__).resolve().parents[1] / "skills" / "hca"


def _wheel_root(package):
    """The installed package's resource directory if it holds SKILL.md, else None."""
    try:
        root = importlib.resources.files(package)
        return root if root.joinpath("SKILL.md").is_file() else None
    except (ImportError, TypeError, ValueError, OSError):      # no such package in a checkout; a broken namespace package
        return None


def _collect(root) -> dict:
    """{relative path: bytes} for SKILL.md and references/*.md only: never anything else that happens to sit in the folder."""
    files = {"SKILL.md": root.joinpath("SKILL.md").read_bytes()}
    refs = root.joinpath("references")
    if refs.is_dir():
        for entry in sorted(refs.iterdir(), key=lambda e: e.name):
            if entry.is_file() and entry.name.endswith(".md"):
                files[f"references/{entry.name}"] = entry.read_bytes()
    return files


def read_source(package=None, checkout=None):
    """(files, "wheel" | "checkout"). The wheel resource wins; the checkout folder is the fallback for development."""
    package = package or SKILL_PACKAGE
    root = _wheel_root(package)
    if root is not None:
        return _collect(root), "wheel"
    folder = Path(checkout) if checkout else checkout_dir()
    if (folder / "SKILL.md").is_file():
        return _collect(folder), "checkout"
    raise HcaError(f"cannot find the hca skill files: the {package} package resource is missing and {folder / 'SKILL.md'} does not exist. "
                   "Fix: reinstall hillclimbanything (pip install --force-reinstall hillclimbanything), or run from a full checkout.")


def digests(files) -> dict:
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}


# --- what is on disk -------------------------------------------------------------------------------

def load_marker(path):
    """The parsed marker if `path` is a directory holding a valid one of ours, else None (a corrupt marker counts as foreign)."""
    try:
        data = json.loads((Path(path) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (isinstance(data, dict) and data.get("name") == "hca" and isinstance(data.get("version"), str)
            and isinstance(data.get("files"), dict)):
        return data
    return None


def disk_digests(path) -> dict:
    """{relative path: sha256} of every file under `path` except the marker. Symlinks are listed, never followed."""
    found = {}

    def walk(directory, prefix):
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                rel = prefix + entry.name
                if entry.is_symlink():
                    found[rel] = "symlink"
                elif entry.is_dir(follow_symlinks=False):
                    walk(entry.path, rel + "/")
                elif rel != MARKER:
                    found[rel] = hashlib.sha256(Path(entry.path).read_bytes()).hexdigest()

    walk(path, "")
    return found


def installed_state(path, source_hashes) -> tuple:
    """(state, marker, drifted) for a directory that has our marker: state is "current" or "outdated".

    `drifted` means the files on disk are not what the marker says we installed (edited, added or deleted by hand)."""
    marker = load_marker(path)
    if marker is None:
        return "foreign", None, False
    try:
        drifted = disk_digests(path) != marker["files"]
        current = (not drifted and marker["version"] == _hca.__version__ and marker["files"] == source_hashes)
    except OSError:
        drifted, current = True, False
    return ("current" if current else "outdated"), marker, drifted


# --- the plan --------------------------------------------------------------------------------------

@dataclass
class Item:
    name: str            # claude | agents | project-claude | project-agents (also the backup subfolder)
    path: Path
    action: str          # the words shown in the plan
    kind: str            # install | update | current | replace | remove | skip | refuse
    backup: bool = False
    unlink: bool = False        # the path is a symlink: remove the link itself
    old_version: str = ""

    @property
    def writes(self) -> bool:
        return self.kind in ("install", "update", "replace")

    @property
    def changes(self) -> bool:
        return self.kind in ("install", "update", "replace", "remove")


def project_root(cwd) -> Path:
    cwd = Path(cwd).resolve()
    try:
        return gitops.repo_root(cwd).resolve()
    except HcaError:
        return cwd


def targets(agent, project, cwd, home) -> list:
    """[(name, directory)] the skill is installed to."""
    base = project_root(cwd) if project else Path(home)
    prefix = "project-" if project else ""
    rows = []
    if agent in ("claude", "all"):
        rows.append((prefix + "claude", base / ".claude" / "skills" / "hca"))
    if agent in ("agents", "all"):
        rows.append((prefix + "agents", base / ".agents" / "skills" / "hca"))
    return rows


def plan_target(name, path, source_hashes, uninstall=False, force=False) -> Item:
    path = Path(path)
    if path.is_symlink():                    # checked first: exists() would look through the link
        if uninstall:
            if force:
                return Item(name, path, "remove (the symlink only; what it points to is untouched)", "remove", unlink=True)
            return Item(name, path, "is a symlink; left alone (--force removes the link itself)", "skip")
        if force:
            return Item(name, path, "replace (symlink removed; what it points to is untouched)", "replace", unlink=True)
        return Item(name, path, "refused: the path is a symlink (--force removes the link itself, never its target)", "refuse")
    if not path.exists():
        if uninstall:
            return Item(name, path, "not installed", "skip")
        return Item(name, path, "install", "install")
    marker = load_marker(path) if path.is_dir() else None
    if marker is None:
        if uninstall:
            return Item(name, path, "not installed by hca setup; left alone", "skip")
        if force:
            return Item(name, path, "replace (backed up)", "replace", backup=True)
        return Item(name, path, "refused: exists but was not installed by hca setup (--force backs it up and replaces it)", "refuse")
    if uninstall:
        return Item(name, path, "remove", "remove", backup=True, old_version=marker["version"])
    state, _, drifted = installed_state(path, source_hashes)
    if state == "current":
        return Item(name, path, "already up to date", "current", old_version=marker["version"])
    note = " (local changes backed up)" if drifted else ""
    return Item(name, path, f"update from v{marker['version']} to v{_hca.__version__}{note}", "update", backup=drifted,
                old_version=marker["version"])


def _backup_root() -> Path:
    from .. import ledger
    return ledger.home() / "backups" / f"skills-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"


# --- applying it -----------------------------------------------------------------------------------

def _remove(path: Path) -> None:
    """Delete a leftover file, link or directory without ever following a symlink."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _unlink_link(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        os.rmdir(path)                         # a Windows directory junction


def _unique(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists() or candidate.is_symlink():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


def _move_aside(path: Path, destination: Path) -> Path:
    destination = _unique(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(destination))     # a rename on one filesystem, copy-then-delete across filesystems
    return destination


def _marker_bytes(hashes, source) -> bytes:
    marker = {"name": "hca", "version": _hca.__version__, "installed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "source": source, "files": hashes}
    return (json.dumps(marker, indent=2, sort_keys=True) + "\n").encode()


def apply_item(item: Item, files, hashes, source, backup_root: Path):
    """Do one planned action. Returns the backup path if something was moved there, else None."""
    path = item.path
    if item.kind == "remove":
        if item.unlink:
            _unlink_link(path)
            return None
        return _move_aside(path, backup_root / item.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    _remove(temp)
    backed_up = aside = None
    try:
        for rel, data in files.items():
            target = temp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temp / MARKER).write_bytes(_marker_bytes(hashes, source))
        if item.unlink:
            _unlink_link(path)
        elif path.exists():
            if item.backup:
                backed_up = _move_aside(path, backup_root / item.name)
            else:
                aside = path.with_name(f"{path.name}.old-{os.getpid()}")
                _remove(aside)
                os.replace(path, aside)
        try:
            os.replace(temp, path)
        except OSError:
            if backed_up is not None:            # put the old copy back rather than leave no skill at all
                shutil.move(str(backed_up), str(path))
                backed_up = None
            elif aside is not None:
                os.replace(aside, path)
                aside = None
            raise
        if aside is not None:
            _remove(aside)                        # only now, after the new copy is in place
    finally:
        _remove(temp)
    return backed_up


# --- the command -----------------------------------------------------------------------------------

def _is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def run(args, out=print, ask=input, cwd=None, home=None) -> int:
    uninstall = bool(args.uninstall)
    cwd = Path(cwd or Path.cwd())
    home = Path(home) if home else Path.home()
    files, source = read_source()
    hashes = digests(files)
    rows = targets(args.agent, args.project, cwd, home)
    items = [plan_target(name, path, hashes, uninstall=uninstall, force=args.force) for name, path in rows]
    backup_root = _backup_root()

    verb = "remove" if uninstall else "install"
    out(f"hca setup: {verb} the hca agent skill (hca v{_hca.__version__}, files from the {source})")
    for item in items:
        out(f"  {item.name:<15} {item.path}")
        out(f"  {'':<15} -> {item.action}")
        if item.writes:
            out(f"  {'':<15}    files: {', '.join(sorted(files))}, {MARKER}")
    if any(item.backup for item in items):
        out(f"  backups go to {backup_root}/<target>/ (outside the skills folders, so agents never load them)")

    refused = [item for item in items if item.kind == "refuse"]
    if refused:
        raise Refusal("; ".join(f"{item.path}: {item.action.removeprefix('refused: ')}" for item in refused) + ". Nothing was changed.")
    if not any(item.changes for item in items):
        out("Nothing to do.")
        return 0
    if args.dry_run:
        out("Dry run: nothing was changed.")
        return 0
    if not args.yes:
        if not _is_tty():
            raise HcaError("hca setup would change the files above but stdin is not a terminal, so it cannot ask. "
                           "Fix: re-run with --yes (or --dry-run to only see the plan).")
        try:
            answer = ask("Proceed? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            out("Not confirmed; nothing was changed.")
            return 1

    done = []
    for item in items:
        if item.kind in ("current", "skip"):
            continue
        backup = apply_item(item, files, hashes, source, backup_root)
        done.append((item, backup))
    for item, backup in done:
        label = "removed" if item.kind == "remove" else ("updated" if item.kind == "update" else "installed")
        out(f"{label}: {item.path}" + (f"  (old copy backed up to {backup})" if backup else ""))
    if not uninstall and done:
        names = {item.name.removeprefix("project-") for item, _ in done}
        out("")
        out("Next")
        if "claude" in names:
            out(f"  {NEXT_CLAUDE}")
        if "agents" in names:
            out(f"  {NEXT_OTHERS}")
        out(f"  {NEXT_THEN}")
    return 0


# --- for `hca doctor` ------------------------------------------------------------------------------

def skill_checks(cwd, home=None) -> list:
    """[(level, message)] for the two user-level skill folders, plus any project-level ones in `cwd`. Warnings only; never raises."""
    home, cwd = Path(home) if home else Path.home(), Path(cwd)
    paths = [home / ".claude" / "skills" / "hca", home / ".agents" / "skills" / "hca"]
    paths += [p for p in (cwd / ".claude" / "skills" / "hca", cwd / ".agents" / "skills" / "hca") if p.exists() or p.is_symlink()]
    try:
        source_hashes = digests(read_source()[0])
    except HcaError:
        source_hashes = None
    results, seen = [], set()
    for path in paths:
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            results.append(_skill_check(path, source_hashes))
        except OSError as exc:
            results.append(("warn", f"{path}: cannot inspect ({exc})"))
    return results


def _skill_check(path, source_hashes):
    if not (path.exists() or path.is_symlink()):
        return "warn", f"{path}: not installed: run hca setup"
    marker = load_marker(path)
    if marker is None:
        return "warn", f"{path}: present but not installed by hca (no marker)"
    available, installed = _hca.__version__, marker["version"]
    if source_hashes is None:
        stale = installed != available
    else:
        stale = installed != available or marker["files"] != source_hashes or disk_digests(path) != marker["files"]
    if not stale:
        return "ok", f"{path}: ok v{installed}"
    detail = f"v{installed} installed, v{available} available" + ("" if installed != available else ", files differ")
    return "warn", f"{path}: outdated ({detail}): run hca setup"
