"""Shared fixtures: a tiny scripted objective whose output is dictated by files in the arm directory.

The script `bench.py` counts its own runs in `count` (arm dirs run once per block, so run n is block
n-1) and prints `out_<n>.txt` if it exists, else `out.txt`; likewise `err_<n>.txt` / `err.txt` for
stderr and `rc_<n>.txt` / `rc.txt` for the exit status. That makes every scenario deterministic and fast.
"""

import sys
import tempfile
import unittest
from pathlib import Path

BENCH = '''
import os, sys
n = int(open("count").read()) + 1 if os.path.exists("count") else 1
open("count", "w").write(str(n))
def pick(prefix):
    for name in (f"{prefix}_{n}.txt", f"{prefix}.txt"):
        if os.path.exists(name):
            return open(name).read()
    return ""
sys.stdout.write(pick("out"))
sys.stderr.write(pick("err"))
rc = pick("rc").strip()
sys.exit(int(rc) if rc else 0)
'''


class Arm:
    """A directory acting as one arm; `say(text)` / `say(text, block=3)` set what the objective prints."""

    def __init__(self, path: Path):
        self.path = path

    def say(self, text, block=None, rc=None, err=None):
        suffix = "" if block is None else f"_{block + 1}"
        (self.path / f"out{suffix}.txt").write_text(text)
        if rc is not None:
            (self.path / f"rc{suffix}.txt").write_text(str(rc))
        if err is not None:
            (self.path / f"err{suffix}.txt").write_text(err)

    def fail(self, block, err="boom\n", rc=1):
        self.say("", block=block, rc=rc, err=err)


class ArmTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bench = self.tmp / "bench.py"
        self.bench.write_text(BENCH)
        self.command = (sys.executable, str(self.bench))
        self.base, self.cand = Arm(self.tmp / "base"), Arm(self.tmp / "cand")
        for arm in (self.base, self.cand):
            arm.path.mkdir()
        self.out_dir = self.tmp / "out"
