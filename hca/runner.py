"""Run one command the way a benchmark needs it: no shell, its own process group, files not pipes.

Why each choice: a shell would let quoting change what is measured; a process group lets a timeout
kill the command's grandchildren (a leaked `node` would skew every later block); streaming to files
means a chatty command can neither fill a pipe and stall nor eat memory; and the stderr tail is kept
at 4 KB in full because the last lines are where the real error is. POSIX only.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .core import HcaError, read_tail

TAIL_BYTES = 4096
GRACE_SECONDS = 2.0     # SIGTERM first, SIGKILL after this


@dataclass(frozen=True)
class RunResult:
    argv: tuple
    returncode: int | None          # negative = killed by that signal; 126/127 = could not start
    timed_out: bool
    seconds: float                  # wall time, including start-up
    stdout_path: Path
    stderr_path: Path
    error_tail: str                 # last TAIL_BYTES of stderr, never shortened further
    dropped_env: tuple = ()         # env var names removed by the denylist
    orphans_killed: bool = False    # something was still alive in the group after the command exited

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


def build_env(deny=(), env_extra=None, base=None):
    """(env, dropped): `base` minus names matching any `deny` glob, plus `env_extra` (which always wins)."""
    base = os.environ if base is None else base
    dropped = tuple(sorted(name for name in base if any(fnmatch.fnmatchcase(name, pat) for pat in deny)))
    env = {name: value for name, value in base.items() if name not in dropped}
    for name, value in (env_extra or {}).items():
        env[str(name)] = str(value)
    return env, dropped


def run(argv, cwd, out_dir, name, timeout, env_extra=None, deny=(), base_env=None) -> RunResult:
    """Run `argv` in `cwd`; stdout/stderr go to `out_dir/<name>.stdout|stderr`. Never raises for a bad command."""
    argv = tuple(str(a) for a in argv)
    if not argv:
        raise HcaError("run needs a non-empty argv list")
    if not name or "/" in name or name.startswith("."):
        raise HcaError(f"run name {name!r} must be a plain file stem")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stdout_path, stderr_path = out_dir / f"{name}.stdout", out_dir / f"{name}.stderr"
    env, dropped = build_env(deny, env_extra, base_env)
    start = time.monotonic()
    timed_out = orphans = False
    returncode = None
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    start_new_session=True)
        except OSError as exc:                  # missing binary, bad cwd, no exec permission: an invalid run, not a crash
            err.write(f"hca: could not start {argv[0]!r} in {str(cwd)!r}: {exc}\n".encode())
            returncode = 127 if isinstance(exc, FileNotFoundError) else 126
            proc = None
        if proc is not None:
            # A blocking wait, with the deadline enforced by a timer thread. Popen.wait(timeout=...)
            # polls with sleeps that grow to 50 ms, which quantised every measured wall time to the
            # poll schedule (a 75 ms command read as 114.7 ms +- 0.2 ms) and hid the real noise.
            expired, done = threading.Event(), threading.Event()
            timer = threading.Timer(timeout, expired.set)
            timer.daemon = True
            timer.start()
            watcher = threading.Thread(target=_enforce_deadline, args=(proc.pid, expired, done), daemon=True)
            watcher.start()
            returncode = proc.wait()
            seconds = time.monotonic() - start
            done.set()
            timer.cancel()
            timed_out = expired.is_set()
            expired.set()                        # release the watcher if the command finished in time
            watcher.join()
            if timed_out:
                _terminate_group(proc)           # grandchildren may outlive the leader
            else:
                orphans = _group_alive(proc.pid) and _kill_group(proc.pid, signal.SIGKILL)
    if proc is None:
        seconds = time.monotonic() - start
    return RunResult(argv, returncode, timed_out, seconds, stdout_path, stderr_path,
                     read_tail(stderr_path, TAIL_BYTES), dropped, bool(orphans))


def _enforce_deadline(pgid, expired, done) -> None:
    """On the deadline: SIGTERM the command's group, then SIGKILL it after the grace period."""
    expired.wait()
    if done.is_set():
        return
    _kill_group(pgid, signal.SIGTERM)
    if not done.wait(GRACE_SECONDS):
        _kill_group(pgid, signal.SIGKILL)


def _kill_group(pgid, sig) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _group_alive(pgid) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_group(proc) -> int | None:
    """SIGTERM the command's own group, wait up to GRACE_SECONDS, then SIGKILL whatever is left."""
    pgid = proc.pid                      # start_new_session made the command its own group leader
    _kill_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        proc.poll()                      # reaps the leader so only real members keep the group alive
        if not _group_alive(pgid):
            break
        time.sleep(0.02)
    if _group_alive(pgid):
        _kill_group(pgid, signal.SIGKILL)
    try:
        return proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        return None
