"""`hca doctor`: find what would quietly corrupt a measurement before you spend tries on it.

Leaked environment (PYTHONPATH pointing at the candidate, a different virtualenv, LD_PRELOAD) and a
busy machine are the usual reasons a benchmark disagrees with itself; Windows-native has no process
groups, so timeouts could not be enforced. Errors make the exit status 1; warnings do not.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core import HcaError
from ..spec import DEFAULT_DENY, load_spec

LOAD_WARN_PER_CPU = 0.30        # 1-minute load per core above this adds visible noise
ALWAYS_SUSPECT = ("PYTHONPATH", "LD_PRELOAD")


def add_arguments(parser) -> None:
    pass


def run(args=None, out=print, cwd=None, env=None) -> int:
    results = checks(Path(cwd or Path.cwd()), os.environ if env is None else env)
    for level, name, message in results:
        out(f"[{level:<5}] {name}: {message}")
    errors = sum(level == "error" for level, _, _ in results)
    out(f"{errors} error(s), {sum(level == 'warn' for level, _, _ in results)} warning(s).")
    return 1 if errors else 0


def checks(cwd: Path, env, platform=None, loadavg=None, python=None) -> list:
    """[(level, name, message)] with level ok | warn | error. Arguments are injectable for tests."""
    platform = platform or sys.platform
    python = python or sys.version_info
    results = []

    def add(level, name, message):
        results.append((level, name, message))

    if tuple(python[:2]) < (3, 11):
        add("error", "python", f"{python[0]}.{python[1]} is too old. Fix: install Python 3.11 or newer.")
    else:
        add("ok", "python", f"{python[0]}.{python[1]}")
    if platform.startswith("win"):
        add("warn", "platform", "native Windows is not supported (no process groups, so timeouts cannot be enforced). "
            "Fix: run hca inside WSL.")
    else:
        add("ok", "platform", platform)

    git = shutil.which("git")
    if not git:
        add("error", "git", "git not found on PATH. Fix: install git (`hca init` needs it).")
    else:
        top = _git(git, cwd, "rev-parse", "--show-toplevel")
        if top is None:
            add("warn", "repo", "not inside a git repository. `hca quick` works anywhere; `hca init` needs a repo.")
        else:
            dirty = [line for line in (_git(git, cwd, "status", "--porcelain") or "").splitlines() if ".hca/" not in line]
            add("warn" if dirty else "ok", "repo", f"{top}" + (f"; {len(dirty)} uncommitted change(s). `hca init` needs a clean tree." if dirty else "; clean"))

    deny, spec_note = DEFAULT_DENY, "default deny list"
    if (cwd / "hca.toml").exists():
        try:
            deny, spec_note = load_spec(cwd / "hca.toml").env.deny, "deny list from hca.toml"
            add("ok", "hca.toml", "valid")
        except HcaError as exc:
            add("error", "hca.toml", str(exc))
    leaked = sorted(n for n in env if any(fnmatch.fnmatchcase(n, pat) for pat in deny))
    if leaked:
        add("warn", "env", f"{', '.join(leaked)} will be removed from every measured command ({spec_note}).")
    suspect = [n for n in ALWAYS_SUSPECT if env.get(n)]
    if suspect:
        add("warn", "env", f"{', '.join(suspect)} is set and is NOT removed: it can make the baseline import the candidate's code "
            "or a different library. Fix: unset it for the session, or add it to [env] deny in hca.toml.")
    venv = env.get("VIRTUAL_ENV")
    if venv and os.path.realpath(venv) != os.path.realpath(sys.prefix):
        add("warn", "env", f"VIRTUAL_ENV={venv} but hca runs from {sys.prefix}. Measured commands may use a different Python "
            "than the one you tested with. Fix: activate the right environment before running hca.")
    if not leaked and not suspect:
        add("ok", "env", "no leaked variables")

    load, cpus = loadavg or _load(), os.cpu_count() or 1
    if load is None:
        add("warn", "load", "cannot read the load average on this platform.")
    elif load[0] / cpus > LOAD_WARN_PER_CPU:
        add("warn", "load", f"1-minute load {load[0]:.2f} on {cpus} CPUs: something else is busy, so measurements will be noisier. "
            "Fix: close games, browsers and builds, then rerun.")
    else:
        add("ok", "load", f"1-minute load {load[0]:.2f} on {cpus} CPUs")
    return results


def _git(git, cwd, *argv):
    try:
        done = subprocess.run([git, *argv], cwd=cwd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _load():
    try:
        return tuple(float(x) for x in Path("/proc/loadavg").read_text().split()[:3])
    except (OSError, ValueError):
        try:
            return os.getloadavg()
        except (OSError, AttributeError):
            return None
