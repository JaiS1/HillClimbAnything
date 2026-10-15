"""The ledger: `.hca/` in the repo (state, attempts, VERDICTS.md) and the referee store `~/.hca/<task>/`.

Everything the harness decides is written down here, whether or not the candidate was valid, so the
validity rate and the history of verdicts can never be quietly tidied. The referee store is outside
the repo and holds what the agent should not read: the held-out data and the full confirm detail.
`HCA_HOME` replaces `~/.hca` (tests use it, and so can anyone who wants the store elsewhere).
A reader of the repo's own files can edit them, so the confirm cap trusts the store's count over
`state.json`'s. Standard library only.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import time
from pathlib import Path

from . import gitops, stats
from .core import HcaError, read_json, write_json

STATE_VERSION = 1


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# --- repo side -------------------------------------------------------------------------------------

def hca_dir(root) -> Path:
    return Path(root) / gitops.HCA_DIR


def state_path(root) -> Path:
    return hca_dir(root) / "state.json"


def load_state(root) -> dict:
    path = state_path(root)
    if not path.exists():
        raise HcaError(f"no {gitops.HCA_DIR}/state.json here. Fix: run `hca init` first (it needs an hca.toml).")
    try:
        state = read_json(path)
    except ValueError as exc:
        raise HcaError(f"{path} is not valid JSON ({exc}). Fix: restore it, or run `hca init --reset`.") from None
    for key in ("task", "branch", "init_sha", "last_keep", "frozen"):
        if key not in state:
            raise HcaError(f"{path} is missing {key!r}. Fix: run `hca init --reset`.")
    return state


def save_state(root, state) -> None:
    state["updated"] = now()
    hca_dir(root).mkdir(parents=True, exist_ok=True)
    write_json(state_path(root), state)


STALE_UNREADABLE_SECONDS = 10      # a lock file that cannot be parsed is only taken over once it is this old


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def lock(root, command, out=print):
    """`.hca/lock`, created with O_EXCL and holding pid, start time and command, for init, try and confirm.

    Two of those running at once in one repo would race on the git index and the ledger. A lock whose pid
    is alive refuses (HcaError, exit 3); one whose pid is dead is stale: warn, take it over. Always released.
    """
    directory = hca_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "lock"
    info = {"pid": os.getpid(), "started": now(), "command": command}
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            held = None
            try:
                held = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            if held is None:
                try:
                    old_enough = time.time() - path.stat().st_mtime > STALE_UNREADABLE_SECONDS
                except OSError:
                    old_enough = True               # it vanished: try again
                if not old_enough:
                    raise HcaError(f"{path} exists and cannot be read (another hca may be starting). Fix: wait a few seconds, "
                                   "then delete it if no hca command is running.") from None
            elif _pid_alive(held.get("pid")):
                raise HcaError(f"another `hca {held.get('command')}` is running in this repo (pid {held.get('pid')}, started "
                               f"{held.get('started')}). Fix: wait for it to finish; if it is hung, stop that process.") from None
            if attempt == 2:
                raise HcaError(f"could not take {path}: another hca command won the race. Fix: run again.") from None
            out(f"warning: removing a stale lock left by `hca {held.get('command') if held else '?'}` "
                f"(pid {held.get('pid') if held else '?'} is not running).")
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(info))
    try:
        yield
    finally:
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
                path.unlink()
        except (OSError, ValueError):
            pass


def attempts_dir(root) -> Path:
    return hca_dir(root) / "attempts"


def new_attempt(root) -> tuple:
    """(n, directory) for the next attempt: `attempts/0001/`, numbered after the highest existing one."""
    base = attempts_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    taken = [int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()]
    n = max(taken, default=0) + 1
    path = base / f"{n:04d}"
    path.mkdir()
    return n, path


def attempt_path(root, n) -> Path:
    return attempts_dir(root) / f"{n:04d}"


def write_record(directory, name, data) -> None:
    write_json(Path(directory) / name, data)


def read_attempts(root) -> list:
    """verdict.json of every attempt, oldest first (an attempt with no verdict yet is skipped)."""
    out = []
    base = attempts_dir(root)
    if base.exists():
        for path in sorted(base.iterdir()):
            record = path / "verdict.json"
            if path.name.isdigit() and record.is_file():
                try:
                    out.append(read_json(record))
                except ValueError:
                    continue
    return out


def update_attempt(root, n, **fields) -> None:
    path = attempt_path(root, n) / "verdict.json"
    record = read_json(path)
    record.update(fields)
    write_json(path, record)


# --- referee store ---------------------------------------------------------------------------------

def home() -> Path:
    return Path(os.environ.get("HCA_HOME") or (Path.home() / ".hca"))


def store_dir(task) -> Path:
    return home() / task


def heldout_dir(task):
    """The held-out directory if one was installed at init, else None."""
    path = store_dir(task) / "heldout"
    return path if path.is_dir() else None


def install_heldout(task, source, repo_root) -> Path:
    source = Path(source).resolve()
    if not source.is_dir():
        raise HcaError(f"--heldout {source} is not a directory. Fix: pass the directory that holds the held-out data.")
    repo = Path(repo_root).resolve()
    if source == repo or repo in source.parents:
        raise HcaError(f"--heldout {source} is inside the repo, where the agent can read it. "
                       "Fix: keep the held-out data in a directory outside the checkout; hca copies it to the referee store.")
    target = store_dir(task) / "heldout"
    store_dir(task).mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target


def archive_heldout(task) -> None:
    path = heldout_dir(task)
    if path is not None:
        path.rename(path.with_name(f"heldout-old-{time.strftime('%Y%m%dT%H%M%S')}"))


def archive_old_confirms(task) -> None:
    """A fresh init starts a fresh confirm count; keep the old log rather than deleting evidence."""
    path = store_dir(task) / "confirms.jsonl"
    if path.exists():
        path.rename(path.with_name(f"confirms-{time.strftime('%Y%m%dT%H%M%S')}.jsonl"))


def read_confirms(task) -> list:
    path = store_dir(task) / "confirms.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def append_confirm(task, record) -> None:
    store_dir(task).mkdir(parents=True, exist_ok=True)
    with open(store_dir(task) / "confirms.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, allow_nan=False) + "\n")


def confirms_used(state, task) -> int:
    return max(int(state.get("confirms_used", 0)), len(read_confirms(task)))


# --- formatting and the headline -------------------------------------------------------------------

def fmt_effect(value, mode) -> str:
    """An effect in display units: a signed % in blocks mode, a signed score difference in items mode."""
    if value is None:
        return "n/a"
    if isinstance(value, float) and math.isinf(value):
        return "-inf" if value < 0 else "+inf"
    return f"{value:+.1f}%" if mode == "blocks" else f"{value:+.4g}"


def improves(value, mode, direction) -> bool:
    """Is a displayed effect an improvement? Blocks mode shows the metric's own change, so for `min` better is negative."""
    return value < 0 if (mode == "blocks" and direction == "min") else value > 0


def fmt_change(value, mode, direction, metric=None) -> str:
    """'t_ms -30.0% (better)' in blocks mode (the metric's own % change); the bare score difference in items mode."""
    text = fmt_effect(value, mode)
    if mode != "blocks" or value is None:
        return text
    word = "unchanged" if value == 0 else "better" if improves(value, mode, direction) else "worse"
    return f"{metric + ' ' if metric else ''}{text} ({word})"


def fmt_bound(value, mode, direction) -> str:
    """The one-sided bound: 'cut of at least 27.1%' (min) / 'gain of at least 1.9%' (max); otherwise says no improvement is shown."""
    if value is None:
        return "n/a"
    if not improves(value, mode, direction):
        return f"{fmt_effect(value, mode)}, no improvement shown"
    if mode != "blocks":
        return f"at least {fmt_effect(value, mode)}"
    return f"{'cut' if direction == 'min' else 'gain'} of at least {abs(value):.1f}%"


def cumulative(mode, logs, direction="max") -> float:
    """Sum of per-try estimates as a display value (log ratios compound; item differences add)."""
    total = sum(logs)
    return stats.metric_change_pct(total, direction) if mode == "blocks" else total


def headline(state, attempts) -> dict:
    """{text, confirmed, pending, cumulative}: the one current verdict for the task.

    Confirmed delta vs the original baseline if a confirm has passed; keeps made after it are
    "unconfirmed" and reported separately; with no confirm at all there is only the unconfirmed
    cumulative (a sum of per-try estimates, which selection inflates).
    """
    mode, direction, metric = state.get("mode", "blocks"), state.get("direction", "max"), state.get("metric")
    confirmed = state.get("last_confirmed_result")
    pending = [a for a in attempts if a.get("verdict") == "keep" and a.get("confirm") == "pending"]
    base = confirmed["estimate_log"] if confirmed else 0.0
    cum = cumulative(mode, [base] + [a.get("estimate_log", 0.0) for a in pending], direction)
    if confirmed and not pending:
        text = (f"Confirmed: {fmt_change(confirmed['estimate'], mode, direction, metric)} vs the original "
                f"({fmt_bound(confirmed['lower'], mode, direction)}; confirm {confirmed['n']}, {confirmed['blocks']} "
                f"{'blocks' if mode == 'blocks' else 'measurement'}).")
    elif confirmed:
        text = (f"Confirmed: {fmt_change(confirmed['estimate'], mode, direction, metric)} vs the original (confirm {confirmed['n']}). "
                f"Since then {len(pending)} unconfirmed keep(s); cumulative {fmt_effect(cum, mode)}, not yet confirmed.")
    elif pending:
        text = (f"No confirmed result yet. Unconfirmed cumulative {fmt_effect(cum, mode)} over {len(pending)} keep(s): "
                "a sum of per-try estimates that selection inflates; run `hca confirm`.")
    else:
        text = "No keeps yet."
    return {"text": text, "confirmed": confirmed, "pending": pending, "cumulative": cum}


def attempt_line(a, mode) -> str:
    base = f"{a['n']:04d} {a['verdict']}"
    if a.get("estimate") is not None:
        base += f" {fmt_effect(a['estimate'], mode)}"
    if a.get("p") is not None:
        base += f" p={a['p']:.3g}"
    return f"{base} - {a.get('message') or ''}".rstrip(" -")


def render_verdicts(state, attempts, spec=None) -> str:
    mode = state.get("mode", "blocks")
    head = headline(state, attempts)
    history = state.get("verdict_history", [])
    lines = [f"# Verdicts: {state['task']}", "",
             f"Updated {state.get('updated') or now()}. One current verdict; superseded ones are struck through.", "",
             "## Current verdict", "", head["text"], ""]
    if spec is not None:
        o = spec.objective
        lines += [f"Question: do edits to {', '.join(spec.task.editable)} move `{o.metric}` ({o.direction}) by at least "
                  f"{o.useful_effect}?", ""]
    old = [h for h in history[:-1]]
    if old:
        lines += ["## Superseded", ""]
        lines += [f"- ~~{h['text']}~~ (as of {h['at']})" for h in old]
        lines.append("")
    lines += ["## Attempts", ""]
    if not attempts:
        lines.append("None yet.")
    for a in attempts:
        text = attempt_line(a, mode)
        if a.get("confirm") == "unconfirmed":
            lines.append(f"- ~~{text}~~ (unconfirmed: reverted by a failed confirm)")
        elif a.get("verdict") == "keep" and a.get("confirm") == "confirmed":
            lines.append(f"- {text} (confirmed)")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines) + "\n"


def refresh_verdicts(root, state, spec=None) -> None:
    """Recompute the headline, append it to the history if it changed, save state, rewrite VERDICTS.md."""
    attempts = read_attempts(root)
    text = headline(state, attempts)["text"]
    history = state.setdefault("verdict_history", [])
    if not history or history[-1]["text"] != text:
        history.append({"text": text, "at": now()})
    save_state(root, state)
    (hca_dir(root) / "VERDICTS.md").write_text(render_verdicts(state, attempts, spec), encoding="utf-8")
