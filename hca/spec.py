"""Load and validate `hca.toml` into frozen dataclasses.

The spec is the user's promise about what the agent may touch and how success is scored, so a typo
must fail loudly (an ignored `editabel` would silently widen the scope). Every error names the field
and says how to fix it. Defaults match docs/ARCHITECTURE-v1.md. Standard library only.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import stats
from .core import HcaError

DEFAULT_DENY = ("MPLBACKEND", "UV_*", "PYTHONSTARTUP")
_NAME = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
_GUARD_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_PERCENT = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%\s*\Z")
_MISSING = object()


@dataclass(frozen=True)
class Task:
    name: str
    brief: str
    editable: tuple
    frozen: tuple
    link: tuple


@dataclass(frozen=True)
class Build:
    command: tuple


@dataclass(frozen=True)
class Objective:
    command: tuple
    metric: str
    direction: str
    mode: str
    useful_effect: str
    max_tries: int

    @property
    def useful(self) -> float:
        """The useful effect as a log ratio (what stats.keep_decision wants)."""
        return stats.parse_effect(self.useful_effect)


@dataclass(frozen=True)
class Guard:
    """Either a command guard (pass = exit 0) or a metric guard (candidate median vs baseline median)."""
    name: str
    command: tuple | None
    metric: str | None
    max_regression: float | None        # fraction, e.g. 0.02 for "2%"
    max_regression_text: str | None


@dataclass(frozen=True)
class Mechanism:
    metrics: tuple
    slices: tuple


@dataclass(frozen=True)
class Limits:
    command_seconds: int
    try_seconds: int
    blocks: int
    extend_to: int
    confirms: int


@dataclass(frozen=True)
class EnvCfg:
    deny: tuple


@dataclass(frozen=True)
class Spec:
    task: Task
    build: Build | None
    objective: Objective
    guards: tuple
    mechanism: Mechanism
    limits: Limits
    env: EnvCfg


def load_spec(path) -> Spec:
    """Read and validate a hca.toml file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HcaError(f"{path} not found. Fix: create it in the repo root (see docs/ARCHITECTURE-v1.md), "
                       "or run `hca quick -- <cmd>` first to see whether your metric is measurable.") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise HcaError(f"{path.name} is not valid TOML: {exc}. Fix: correct the syntax at that line.") from None
    return parse_spec(data, source=path.name)


def parse_spec(data: dict, source: str = "hca.toml") -> Spec:
    """Validate an already-parsed spec dict (unknown keys and bad values raise HcaError)."""
    p = _Parser(source)
    p.keys(data, "", allowed=("task", "build", "objective", "guards", "mechanism", "limits", "env"),
           required=("task", "objective"))
    task = _task(p, p.table(data, "task"))
    build = None
    if "build" in data:
        t = p.table(data, "build")
        p.keys(t, "build", allowed=("command",), required=("command",))
        build = Build(p.argv(t, "build.command"))
    objective = _objective(p, p.table(data, "objective"))
    guards = _guards(p, data.get("guards", []))
    mech = p.table(data, "mechanism") if "mechanism" in data else {}
    p.keys(mech, "mechanism", allowed=("metrics", "slices"))
    mechanism = Mechanism(p.strlist(mech, "mechanism.metrics", default=()), p.strlist(mech, "mechanism.slices", default=()))
    limits = _limits(p, p.table(data, "limits") if "limits" in data else {})
    env = p.table(data, "env") if "env" in data else {}
    p.keys(env, "env", allowed=("deny",))
    return Spec(task, build, objective, guards, mechanism, limits, EnvCfg(p.strlist(env, "env.deny", default=DEFAULT_DENY)))


def _task(p, t) -> Task:
    p.keys(t, "task", allowed=("name", "brief", "editable", "frozen", "link"), required=("name", "editable"))
    name = p.string(t, "task.name")
    if not _NAME.match(name):
        p.fail("task.name", f"{name!r} is not a valid name", 'use lowercase letters, digits and dashes, e.g. "checkout-latency"')
    editable = p.paths(t, "task.editable", default=())
    if not editable:
        p.fail("task.editable", "is empty", 'list the paths the agent may change, e.g. editable = ["src/**"]')
    frozen = p.paths(t, "task.frozen", default=())
    link = p.paths(t, "task.link", default=())
    if "hca.toml" in editable:
        p.fail("task.editable", "contains hca.toml", "the spec is always frozen; remove it from editable")
    return Task(name, p.string(t, "task.brief", default="BRIEF.md"), editable, frozen, link)


def _objective(p, t) -> Objective:
    p.keys(t, "objective", allowed=("command", "metric", "direction", "mode", "useful_effect", "max_tries"),
           required=("command", "metric", "direction", "useful_effect"))
    mode = p.choice(t, "objective.mode", ("blocks", "items"), default="blocks",
                    hint={"seeds": "seeds mode is planned for a later version; use mode = \"blocks\" or \"items\""})
    useful = t["useful_effect"]
    if not (isinstance(useful, str) and _PERCENT.match(useful)):
        p.fail("objective.useful_effect", f"{useful!r} is not a percentage string",
               'write it as a quoted percent such as "5%" (a bare number would mean a log ratio)')
    try:
        stats.parse_effect(useful)
    except ValueError as exc:
        p.fail("objective.useful_effect", str(exc), 'use a positive percentage such as "5%"')
    return Objective(p.argv(t, "objective.command"), p.string(t, "objective.metric"),
                     p.choice(t, "objective.direction", ("min", "max")), mode, useful.strip(),
                     p.integer(t, "objective.max_tries", default=50, minimum=1))


def _guards(p, raw) -> tuple:
    if not isinstance(raw, list) or not all(isinstance(g, dict) for g in raw):
        p.fail("guards", "must be an array of tables", "write each guard under a `[[guards]]` header")
    guards, seen = [], set()
    for i, g in enumerate(raw):
        where = f"guards[{i}]"
        p.keys(g, where, allowed=("name", "command", "metric", "max_regression"), required=("name",))
        name = p.string(g, f"{where}.name")
        if not _GUARD_NAME.match(name) or name in seen:
            p.fail(f"{where}.name", f"{name!r} is not a unique simple name", "use lowercase letters, digits, - or _, once each")
        seen.add(name)
        if ("command" in g) == ("metric" in g):
            p.fail(where, "needs exactly one of `command` or `metric`",
                   "a command guard passes on exit 0; a metric guard needs metric + max_regression")
        if "command" in g:
            if "max_regression" in g:
                p.fail(f"{where}.max_regression", "only applies to a metric guard", "remove it or use `metric` instead of `command`")
            guards.append(Guard(name, p.argv(g, f"{where}.command"), None, None, None))
            continue
        if "max_regression" not in g:
            p.fail(f"{where}.max_regression", "is required for a metric guard", 'add e.g. max_regression = "2%"')
        text = g["max_regression"]
        match = _PERCENT.match(text) if isinstance(text, str) else None
        if not match:
            p.fail(f"{where}.max_regression", f"{text!r} is not a percentage string", 'write it as a quoted percent such as "2%"')
        guards.append(Guard(name, None, p.string(g, f"{where}.metric"), float(match.group(1)) / 100.0, text.strip()))
    return tuple(guards)


def _limits(p, t) -> Limits:
    p.keys(t, "limits", allowed=("command_seconds", "try_seconds", "blocks", "extend_to", "confirms"))
    blocks = p.integer(t, "limits.blocks", default=stats.MIN_BLOCKS, minimum=stats.MIN_BLOCKS)
    # CONTRACT NOTE: the contract's default extend_to = 20 would contradict blocks > 20, so when
    # extend_to is not given it defaults to 20 for blocks < 20 and to 0 (disabled) otherwise.
    extend = p.integer(t, "limits.extend_to", default=stats.EXTENDED_BLOCKS if blocks < stats.EXTENDED_BLOCKS else 0, minimum=0)
    if extend and extend < blocks:
        p.fail("limits.extend_to", f"{extend} is below blocks ({blocks})", "set extend_to to 0 (no extension) or at least blocks")
    return Limits(p.integer(t, "limits.command_seconds", default=300, minimum=1),
                  p.integer(t, "limits.try_seconds", default=1800, minimum=1), blocks, extend,
                  p.integer(t, "limits.confirms", default=5, minimum=1))


class _Parser:
    """Typed getters that all fail the same way: `<file>: <field>: <problem>. Fix: <what to do>.`"""

    def __init__(self, source):
        self.source = source

    def fail(self, field, problem, fix):
        raise HcaError(f"{self.source}: {field}: {problem}. Fix: {fix}.")

    def table(self, data, key):
        value = data.get(key)
        if not isinstance(value, dict):
            self.fail(key, "must be a table", f"add a `[{key}]` section")
        return value

    def keys(self, table, where, allowed, required=()):
        for key in table:
            if key not in allowed:
                near = difflib.get_close_matches(key, allowed, n=1)
                hint = f"did you mean {near[0]!r}?" if near else f"allowed keys: {', '.join(allowed)}"
                self.fail(f"{where + '.' if where else ''}{key}", "unknown key", hint)
        for key in required:
            if key not in table:
                self.fail(f"{where + '.' if where else ''}{key}", "is required", f"add `{key} = ...`" + (f" under [{where}]" if where else ""))

    def string(self, table, path, default=_MISSING):
        key = path.rsplit(".", 1)[-1]
        if key not in table:
            if default is _MISSING:
                self.fail(path, "is required", f"add `{key} = \"...\"`")
            return default
        value = table[key]
        if not isinstance(value, str) or not value.strip():
            self.fail(path, f"{value!r} is not a non-empty string", "use a quoted, non-empty string")
        return value.strip()

    def integer(self, table, path, default, minimum):
        value = table.get(path.rsplit(".", 1)[-1], default)
        if isinstance(value, bool) or not isinstance(value, int):
            self.fail(path, f"{value!r} is not an integer", f"use a whole number >= {minimum}")
        if value < minimum:
            self.fail(path, f"{value} is below the minimum {minimum}", f"use a whole number >= {minimum}")
        return value

    def choice(self, table, path, options, default=_MISSING, hint=None):
        key = path.rsplit(".", 1)[-1]
        if key not in table and default is not _MISSING:
            return default
        value = table.get(key)
        if value not in options:
            msg = (hint or {}).get(value) if isinstance(value, str) else None
            self.fail(path, f"{value!r} is not allowed", msg or f"use one of: {', '.join(options)}")
        return value

    def strlist(self, table, path, default):
        value = table.get(path.rsplit(".", 1)[-1], default)
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
            self.fail(path, f"{value!r} is not a list of non-empty strings", 'write it like ["a", "b"]')
        return tuple(value)

    def argv(self, table, path):
        key = path.rsplit(".", 1)[-1]
        value = table.get(key)
        if isinstance(value, str):
            self.fail(path, f"{value!r} is a string", f'write the command as a list of arguments, e.g. {key} = ["python3", "bench.py"] (no shell is used)')
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
            self.fail(path, f"{value!r} is not a non-empty list of strings", f'write it like {key} = ["./bench/run.sh"]')
        return tuple(value)

    def paths(self, table, path, default):
        items = self.strlist(table, path, default)
        for item in items:
            if item.startswith("/") or ".." in Path(item).parts:
                self.fail(path, f"{item!r} is absolute or climbs out of the repo", "use paths relative to the repo root")
        return items
