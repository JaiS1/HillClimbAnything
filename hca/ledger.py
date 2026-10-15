"""The ledger: `.hca/` in the repo (state, attempts, VERDICTS.md) and the referee store `~/.hca/<task>/`.

Everything the harness decides is written down here, whether or not the candidate was valid, so the
validity rate and the history of verdicts can never be quietly tidied. The referee store is outside
the repo and holds what the agent should not read: the held-out data and the full confirm detail. It is
also the source of truth for what the agent could otherwise rewrite in `.hca/state.json`: the original
sha, the spec hash, the frozen hashes, the confirm count, the reset log and the attempt tree hashes. On any
disagreement the store wins and a warning is printed (not a security boundary: tampering takes deliberate
effort and leaves a trace). `HCA_HOME` replaces `~/.hca` (tests use it, and so can anyone who wants the
store elsewhere). Standard library only.
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


def proc_start_time(pid):
    """The process start time in clock ticks since boot (field 22 of /proc/<pid>/stat), or None where /proc is unavailable."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return int(text.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid, start=None) -> bool:
    """Is `pid` the same process that took the lock? A recycled pid has a different start time, so it reads as dead."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if start is not None:
        current = proc_start_time(pid)
        if current is not None and current != start:
            return False
    return True


@contextlib.contextmanager
def lock(root, command, out=print):
    """`.hca/lock`, created with O_EXCL and holding pid, process start time and command, for init, try and confirm.

    Two of those running at once in one repo would race on the git index and the ledger. A lock whose process
    is alive refuses (HcaError, exit 3); one whose process is gone, or whose pid now belongs to a different
    process (pid reuse), is stale: warn, take it over. Always released.
    """
    directory = hca_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "lock"
    info = {"pid": os.getpid(), "start": proc_start_time(os.getpid()), "started": now(), "command": command}
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
                                   "then delete it (`rm .hca/lock`) if no hca command is running.") from None
            elif _pid_alive(held.get("pid"), held.get("start")):
                raise HcaError(f"another `hca {held.get('command')}` is running in this repo (pid {held.get('pid')}, started "
                               f"{held.get('started')}). Fix: wait for it to finish; if it is hung, stop that process; "
                               "if you are sure nothing is running, `rm .hca/lock`.") from None
            if attempt == 2:
                raise HcaError(f"could not take {path}: another hca command won the race. Fix: run again, or `rm .hca/lock` "
                               "if nothing is running.") from None
            out(f"warning: removing a stale lock left by `hca {held.get('command') if held else '?'}` "
                f"(pid {held.get('pid') if held else '?'} is not the process that took it).")
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
            held = json.loads(path.read_text(encoding="utf-8"))
            if held.get("pid") == os.getpid() and held.get("start") == info["start"]:
                path.unlink()
        except (OSError, ValueError):
            pass


def attempts_dir(root) -> Path:
    return hca_dir(root) / "attempts"


def new_attempt(root, task=None) -> tuple:
    """(n, directory) for the next attempt: `attempts/0001/`, numbered after the highest existing one.

    With `task` the referee store's attempt list counts too, so deleting `.hca/attempts` does not restart the numbering.
    """
    base = attempts_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    taken = [int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()]
    if task:
        taken += [a["n"] for a in session_attempts(task)]
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


STORE_VERSION = 1


def store_path(task) -> Path:
    return store_dir(task) / "store.json"


def load_store(task):
    """The referee record for `task`, or None if there is none yet."""
    path = store_path(task)
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except ValueError as exc:
        raise HcaError(f"the referee store file {path} is not valid JSON ({exc}). Fix: restore it from a backup; hca will not guess.") from None
    if not isinstance(data, dict):
        raise HcaError(f"the referee store file {path} is not a JSON object. Fix: restore it from a backup.")
    return data


def save_store(task, store) -> None:
    store_dir(task).mkdir(parents=True, exist_ok=True)
    store["updated"] = now()
    write_json(store_path(task), store)


def reset_blocked(task) -> bool:
    """True if re-initialising this task must be refused: the store records confirms and a human has not said otherwise."""
    store = load_store(task)
    if store is None:
        return False
    used = max(int(store.get("confirms_used", 0)), len(read_confirms(task)))
    return used > 0 and os.environ.get("HCA_ALLOW_RESET") != "1"


def init_store(task, init_sha, spec_hash, frozen, blobs) -> dict:
    """Write the referee record at init. A second init of the same task is logged as a reset; confirms_used is never reset."""
    store = load_store(task)
    if store is None:
        store = {"version": STORE_VERSION, "task": task, "confirms_used": len(read_confirms(task)), "resets": [],
                 "attempts": [], "created": now()}
    else:
        store.setdefault("resets", []).append({"at": now(), "previous_init_sha": store.get("init_sha"), "new_init_sha": init_sha,
                                               "confirms_used": max(int(store.get("confirms_used", 0)), len(read_confirms(task))),
                                               "allowed_by_env": os.environ.get("HCA_ALLOW_RESET") == "1"})
    store.update(init_sha=init_sha, spec_hash=spec_hash, frozen=dict(frozen), frozen_blobs=dict(blobs))
    save_store(task, store)
    return store


def session(store) -> int:
    """Which session of the task this is: 0 until the first reset, then 1, 2, ..."""
    return len(store.get("resets", []))


def session_attempts(task) -> list:
    store = load_store(task)
    if store is None:
        return []
    return [a for a in store.get("attempts", []) if a.get("session", 0) == session(store)]


def note_attempt(task, n, tree=None, again=False, refused=False) -> None:
    """Record attempt `n` (and, once known, its snapshot tree hash) in the referee store. Replays are detected from here."""
    store = load_store(task)
    if store is None:
        return
    current = session(store)
    for entry in store.setdefault("attempts", []):
        if entry.get("n") == n and entry.get("session", 0) == current:
            entry.update(tree=tree or entry.get("tree"), again=again or entry.get("again", False), refused=refused or entry.get("refused", False))
            break
    else:
        store["attempts"].append({"n": n, "session": current, "tree": tree, "again": again, "refused": refused, "at": now()})
    save_store(task, store)


def forget_attempt(task, n) -> None:
    """Drop attempt `n` of this session from the store (an environment failure before anything was measured is not a try)."""
    store = load_store(task)
    if store is None:
        return
    current = session(store)
    kept = [a for a in store.get("attempts", []) if not (a.get("n") == n and a.get("session", 0) == current)]
    if len(kept) != len(store.get("attempts", [])):
        store["attempts"] = kept
        save_store(task, store)


def seen_tree(task, tree):
    """The first earlier attempt (any session) whose snapshot tree equals `tree`, as a store entry, or None."""
    store = load_store(task)
    for entry in (store or {}).get("attempts", []):
        if tree and entry.get("tree") == tree:
            return entry
    return None


def tries_used(root, task) -> int:
    """Tries so far this session: the larger of the ledger's attempts and the store's (deleting attempt directories does not help)."""
    base = attempts_dir(root)
    on_disk = sum(1 for p in base.iterdir() if p.is_dir() and p.name.isdigit()) if base.exists() else 0
    return max(on_disk, len(session_attempts(task)))


def confirms_used(state, task) -> int:
    """Confirms charged so far: the store's count (and its log) is the truth; state.json's copy can only raise it."""
    store = load_store(task) or {}
    return max(int(store.get("confirms_used", 0)), len(read_confirms(task)), int(state.get("confirms_used", 0)))


def charge_confirm(task, used) -> None:
    store = load_store(task)
    if store is not None:
        store["confirms_used"] = max(int(store.get("confirms_used", 0)), used)
        save_store(task, store)


def reconcile_state(state, task, out=print) -> None:
    """Make `state` agree with the referee store, warning about every disagreement (the store wins). Creates the record if missing."""
    store = load_store(task)
    if store is None:
        out("warning: no referee record for this task in the store; recording it from .hca/state.json (is HCA_HOME the same as at init?).")
        init_store(task, state["init_sha"], state.get("spec_hash"), state["frozen"], state.get("frozen_blobs", {}))
        return
    for key in ("init_sha", "spec_hash", "frozen", "frozen_blobs"):
        if key in store and state.get(key) != store[key]:
            out(f"warning: .hca/state.json disagrees with the referee store about {key!r}; using the store's value.")
            state[key] = store[key]
    state["confirms_used"] = confirms_used(state, task)


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


def fmt_bound(value, mode, direction, kept=False) -> str:
    """The one-sided bound: 'cut of at least 27.1%' (min) / 'gain of at least 1.9%' (max); otherwise says no improvement is shown.

    `kept` is for a bound printed next to a KEEP or a confirm PASS. The test rejects there, so a bound that
    shows no improvement can only come from tied scores (the Walsh bound is approximate with ties): say so
    instead of contradicting the verdict.
    """
    if value is None:
        return "n/a"
    if not improves(value, mode, direction):
        return "bound not shown (tied scores)" if kept else f"{fmt_effect(value, mode)}, no improvement shown"
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
                f"({fmt_bound(confirmed['lower'], mode, direction, kept=True)}; confirm {confirmed['n']}, {confirmed['blocks']} "
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
