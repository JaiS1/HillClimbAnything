"""Fixture git repos for the part-B tests: a tiny objective whose speed is a number in an editable file.

`bench/run.py` (frozen) prints `METRIC t_ms=<speed * (1 +- jitter)>`. The jitter is seeded by a run counter
kept OUTSIDE the repo (`BENCH_STATE`), so a scenario is exactly reproducible and nothing dirties the
tree, and the printed value is computed rather than timed, so the verdicts do not depend on how busy the
machine is. Each run still sleeps in proportion to the value so wall times look real. A "dev hack"
file makes the metric look faster only when `HCA_ITEMS=dev`, which is how a change can win on dev items
and fail on held-out ones. `bench/items.py` is the items-mode objective: deterministic per-item scores.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

BLOCKS_BENCH = '''
import os, random, sys, time
speed = float(open("src/speed.txt").read())
state = os.environ["BENCH_STATE"]
os.makedirs(state, exist_ok=True)
counter = os.path.join(state, "count")
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
if os.path.exists("src/crash.txt"):
    sys.stderr.write("candidate crashed on purpose\\n")
    sys.exit(3)
value = speed * (1 + float(os.environ.get("BENCH_JITTER", "0.02")) * random.Random(n).uniform(-1, 1))
mode = os.environ.get("HCA_ITEMS", "dev")
if mode == "dev" and os.path.exists("src/dev_hack.txt"):
    value *= float(open("src/dev_hack.txt").read())
if mode == "heldout":
    value *= float(open(os.path.join(os.environ["HCA_HELDOUT_DIR"], "scale.txt")).read())
time.sleep(value / 4000 + float(os.environ.get("BENCH_SLEEP_MS", "0")) / 1000)
print("METRIC t_ms=%.4f" % value)
if os.path.exists("src/size.txt"):
    print("METRIC size_kb=%s" % open("src/size.txt").read().strip())
'''

ITEMS_BENCH = '''
import json, os, sys
if os.path.exists("src/crash.txt"):
    sys.exit(3)
boost = float(open("src/boost.txt").read())
items = []
for i in range(12):
    base = 0.0 if i == 0 else 0.4 + 0.04 * i                  # item 0 scores exactly zero
    items.append({"id": "i%d" % i, "score": base + boost * (0.5 + (i % 4) * 0.25), "tags": {"group": "even" if i % 2 == 0 else "odd"}})
if os.path.exists("src/break.txt"):
    for entry in items[:3]:
        entry["status"] = "candidate_invalid"
print(json.dumps({"metric": "acc", "items": items}))
'''


def toml(name="speedy", mode="blocks", useful=None, limits=None, guards="", build="", extra_task="", mechanism="", frozen='["bench/**"]'):
    if mode == "items":
        objective = ('command = ["%s", "-S", "bench/items.py"]\nmetric = "acc"\ndirection = "max"\nmode = "items"\nuseful_effect = %s\n'
                     % (sys.executable, useful or "0.04"))
    else:
        objective = ('command = ["%s", "-S", "bench/run.py"]\nmetric = "t_ms"\ndirection = "min"\nuseful_effect = "%s"\n'
                     % (sys.executable, useful or "5%"))
    lim = dict({"blocks": 10, "extend_to": 0}, **(limits or {}))
    limits_text = "".join(f"{k} = {v}\n" for k, v in lim.items())
    return (f'[task]\nname = "{name}"\neditable = ["src/**"]\nfrozen = {frozen}\n{extra_task}\n{build}\n[objective]\n{objective}\n'
            f'{guards}\n{mechanism}\n[limits]\n{limits_text}')


class Repo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args, check=True):
        done = subprocess.run(["git", *args], cwd=self.path, capture_output=True, text=True)
        if check and done.returncode != 0:
            raise AssertionError(f"git {args} failed: {done.stderr}")
        return done.stdout.rstrip("\n")

    def write(self, rel, text):
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def read(self, rel):
        return (self.path / rel).read_text()

    def head(self):
        return self.git("rev-parse", "HEAD").strip()

    def commit_all(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.head()


def make_repo(path: Path, mode="blocks", **toml_args) -> Repo:
    repo = Repo(path)
    path.mkdir(parents=True, exist_ok=True)
    repo.git("init", "-q")
    repo.git("symbolic-ref", "HEAD", "refs/heads/main")      # `init -b` needs git 2.28
    repo.git("config", "user.email", "test@example.com")
    repo.git("config", "user.name", "Test")
    repo.write("src/speed.txt", "30\n")
    repo.write("src/boost.txt", "0\n")
    repo.write("bench/run.py", BLOCKS_BENCH)
    repo.write("bench/items.py", ITEMS_BENCH)
    repo.write("README.md", "fixture\n")
    repo.write("hca.toml", toml(mode=mode, **toml_args))
    repo.commit_all("initial")
    return repo


class RepoTestCase(unittest.TestCase):
    """A fresh fixture repo plus an isolated referee store (HCA_HOME) and bench state, per test."""

    mode = "blocks"
    toml_args: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-repo-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"HCA_HOME": str(self.tmp / "home"), "BENCH_STATE": str(self.tmp / "state")})
        env.start()
        self.addCleanup(env.stop)
        self.repo = make_repo(self.tmp / "repo", self.mode, **self.toml_args)
        self.root = self.repo.path
        self.lines = []

    def out(self, line=""):
        self.lines.append(line)

    @property
    def text(self):
        return "\n".join(self.lines)

    def hca(self, module, *, cwd=None, **kwargs):
        """Run a command module's `run` with this repo as the cwd; returns (exit code, output text of just this call)."""
        start = len(self.lines)
        code = module.run(Namespace(**kwargs.pop("args", {})), out=self.out, cwd=cwd or self.root, **kwargs)
        return code, "\n".join(self.lines[start:])

    def init(self, aa_blocks=6, seed=1, heldout=None, force=False, reset=False):
        from hca.commands import init
        return self.hca(init, args={"heldout": heldout, "force": force, "reset": reset}, aa_blocks=aa_blocks, seed=seed)

    def try_(self, message="change", seed=1):
        from hca.commands import try_
        return self.hca(try_, args={"message": message}, seed=seed)

    def confirm(self, seed=1):
        from hca.commands import confirm
        return self.hca(confirm, seed=seed)

    def report(self, md=None):
        from hca.commands import report
        return self.hca(report, args={"md": md})

    def state(self):
        from hca import ledger
        return ledger.load_state(self.root)

    def attempt(self, n):
        from hca import ledger
        return ledger.read_json(ledger.attempt_path(self.root, n) / "verdict.json")

    def set_speed(self, value):
        self.repo.write("src/speed.txt", f"{value}\n")

    def status_lines(self):
        return self.repo.git("status", "--porcelain", "--untracked-files=all").splitlines()


@contextlib.contextmanager
def env(**values):
    with mock.patch.dict(os.environ, {k: str(v) for k, v in values.items()}):
        yield
