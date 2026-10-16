"""`hca doctor`: find what would quietly corrupt a measurement before you spend tries on it.

An editable install of the project (pip install -e) in the objective's interpreter is the other classic: `import pkg`
then resolves to the live checkout in both arms, and every try measures the same code twice.

Leaked environment (PYTHONPATH pointing at the candidate, a different virtualenv, LD_PRELOAD) and a
busy machine are the usual reasons a benchmark disagrees with itself. A `skill` line per agent folder says whether `hca setup` has installed
the agent skill and whether it is current (warnings only). Errors make the exit status 1; warnings do not.
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

from ..core import HcaError
from ..spec import DEFAULT_DENY, load_spec
from . import setup as skill_setup

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


EDITABLE_PROBE = (
    "import importlib.metadata as m, json\n"
    "out = []\n"
    "for d in m.distributions():\n"
    "    t = d.read_text('direct_url.json')\n"
    "    if not t:\n"
    "        continue\n"
    "    j = json.loads(t)\n"
    "    if (j.get('dir_info') or {}).get('editable'):\n"
    "        out.append([d.metadata['Name'], j.get('url')])\n"
    "print(json.dumps(out))\n")


def editable_installs(interpreter, env) -> list:
    """[(name, directory)] of the editable installs (direct_url.json with "editable": true) the interpreter can see."""
    try:
        done = subprocess.run([interpreter, "-c", EDITABLE_PROBE], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=dict(env), stdin=subprocess.DEVNULL)
        rows = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 and done.stdout.strip() else []
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return []
    out = []
    for name, url in rows:
        if isinstance(url, str) and url.startswith("file://"):
            out.append((name, Path(urllib.parse.unquote(url[len("file://"):]))))
    return out


def checks(cwd: Path, env, platform=None, loadavg=None, python=None, home=None) -> list:
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
        add("ok", "platform", f"{platform} (native Windows: timeouts kill the process tree with taskkill; WSL gives the strictest isolation)")
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
            spec = load_spec(cwd / "hca.toml")
            deny, spec_note = spec.env.deny, "deny list from hca.toml"
            add("ok", "hca.toml", "valid")
            _check_editable(add, spec, cwd, env)
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
    for level, message in skill_setup.skill_checks(cwd, home):         # warnings only: a missing skill never breaks measuring
        add(level, "skill", message)
    return results


def _check_editable(add, spec, cwd, env) -> None:
    """Warn if the objective's Python has an editable install pointing at this repo: both arms would import the live checkout."""
    interpreter = spec.objective.command[0]
    if not Path(interpreter).name.startswith("python"):
        return
    found = shutil.which(interpreter, path=env.get("PATH")) or interpreter
    root = Path(cwd).resolve()
    hits = []
    for name, directory in editable_installs(found, env):
        try:
            here = directory.resolve()
        except OSError:
            continue
        if here == root or root in here.parents or here in root.parents:
            hits.append(f"{name} ({here})")
    if hits:
        add("warn", "editable-install", f"{', '.join(hits)} is installed in editable mode in the objective's Python ({found}). "
            "`import` may then resolve to the live checkout in both arms, so every try would measure the same code twice. Fix: uninstall it "
            "from that environment, or have the benchmark put its own checkout first on sys.path and assert where the package was imported from.")


def _git(git, cwd, *argv):
    try:
        done = subprocess.run([git, *argv], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
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
