import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from hca import cli
from hca.commands import doctor, quick

JITTER = '''
import math, os, random, sys
amp = float(sys.argv[1])
n = int(open("count").read()) + 1 if os.path.exists("count") else 1
open("count", "w").write(str(n))
print("METRIC lat=%f" % (100 * math.exp(amp * random.Random(n * 7919).uniform(-1, 1))))
'''


class QuickTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-quick-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "jitter.py").write_text(JITTER)
        cwd = contextlib.chdir(self.tmp)
        cwd.__enter__()
        self.addCleanup(cwd.__exit__, None, None, None)

    def quick(self, *command, blocks=10, useful="5%", **extra):
        lines = []
        args = Namespace(blocks=blocks, budget_seconds=60.0, useful=useful, direction="min", metric=None, save=False,
                         command=list(command), **extra)
        return quick.run(args, out=lines.append), "\n".join(lines)

    def jitter(self, amp):
        return (sys.executable, str(self.tmp / "jitter.py"), str(amp))

    def test_quiet_command_is_ok(self):
        code, text = self.quick(*self.jitter(0.002))
        self.assertEqual(code, 0)
        self.assertIn("Measured: METRIC lat", text)
        self.assertRegex(text, r"Noise: ±\d+\.\d%")
        self.assertIn("Detectable at 80% power:", text)
        self.assertIn("Your useful effect 5%: OK", text)
        self.assertIn("Next: write hca.toml", text)
        self.assertIn("`hca init`", text)

    def test_noisy_command_is_too_noisy_with_fixes(self):
        code, text = self.quick(*self.jitter(0.5))
        self.assertEqual(code, 0)
        self.assertIn("too noisy here", text)
        self.assertIn("more items per run", text)
        self.assertIn("hca doctor", text)

    def test_moderate_noise_needs_extension(self):
        code, text = self.quick(*self.jitter(0.04), blocks=20)
        self.assertEqual(code, 0)
        self.assertRegex(text, r"Your useful effect 5%: (needs 20 blocks|OK|too noisy)")

    def test_command_without_metric_is_measured_by_wall_time(self):
        code, text = self.quick(sys.executable, "-c", "pass", blocks=8)
        self.assertEqual(code, 0)
        self.assertIn("wall time", text)
        self.assertIn("8 A/A blocks", text)

    def test_item_json_is_measured(self):
        line = '{"metric":"lat","items":[{"id":"a","runs":[10,11]},{"id":"b","runs":[20,19]}]}'
        code, text = self.quick(sys.executable, "-c", f"print({line!r})", blocks=8)
        self.assertEqual(code, 0)
        self.assertIn("item JSON (lat)", text)
        self.assertIn("No noise seen at all", text)

    def test_failing_command_and_ambiguous_metrics_explain_themselves(self):
        code, text = self.quick(sys.executable, "-c", "import sys; sys.stderr.write('bad thing'); sys.exit(2)")
        self.assertEqual(code, 2)
        self.assertIn("bad thing", text)
        with self.assertRaises(Exception) as caught:
            self.quick(sys.executable, "-c", "print('METRIC a=1'); print('METRIC b=2')")
        self.assertIn("--metric NAME", str(caught.exception))

    def test_too_slow_for_the_budget_is_refused_not_overspent(self):
        args = Namespace(blocks=None, budget_seconds=1.0, useful="5%", direction="min", metric=None, save=False,
                         command=[sys.executable, "-c", "import time; time.sleep(0.5)"])
        lines = []
        self.assertEqual(quick.run(args, out=lines.append), 2)
        self.assertIn("Raise --budget-seconds", "\n".join(lines))

    def test_save_is_opt_in(self):
        self.quick(*self.jitter(0.002), blocks=8)
        self.assertFalse((self.tmp / ".hca").exists())
        args = Namespace(blocks=8, budget_seconds=60.0, useful="5%", direction="min", metric=None, save=True,
                         command=list(self.jitter(0.002)))
        quick.run(args, out=lambda _: None)
        self.assertTrue((self.tmp / ".hca" / "quick.json").is_file())

    def test_cli_splits_the_command_at_double_dash(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(["quick", "--blocks", "8", "--useful", "10%", "--", sys.executable, "-c", "print('METRIC x=1')", "--blocks"])
        self.assertEqual(code, 0)
        self.assertIn("METRIC x", buffer.getvalue())
        self.assertIn("Your useful effect 10%", buffer.getvalue())

    def test_cli_errors_exit_3(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["quick", "--"]), 3)
            self.assertEqual(cli.main(["quick", "--blocks", "2", "--", "true"]), 3)
            self.assertEqual(cli.main(["quick", "--blocks", "7", "--", "true"]), 3)             # A2: noise needs at least 8 A/A blocks
            for name in ("init", "try", "confirm", "report", "status"):
                self.assertEqual(cli.main([name]), 3)
        self.assertIn("not inside a git repository", err.getvalue())    # the part-B commands need a repo and a spec

    def test_python_dash_m_hca_help_works(self):
        root = Path(__file__).resolve().parent.parent
        done = subprocess.run([sys.executable, "-m", "hca", "--help"], cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0)
        for word in ("quick", "doctor", "init", "try", "confirm", "report", "status"):
            self.assertIn(word, done.stdout)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-doctor-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def levels(self, env, **kwargs):
        kwargs.setdefault("loadavg", (0.1, 0.1, 0.1))
        return [(lvl, name, msg) for lvl, name, msg in doctor.checks(self.tmp, env, **kwargs)]

    def test_leaked_env_is_reported(self):
        results = self.levels({"UV_CACHE_DIR": "x", "PYTHONPATH": "/somewhere", "LD_PRELOAD": "lib.so",
                               "VIRTUAL_ENV": "/definitely/elsewhere"})
        text = "\n".join(m for _, n, m in results if n == "env")
        self.assertIn("UV_CACHE_DIR will be removed", text)
        self.assertIn("PYTHONPATH, LD_PRELOAD is set and is NOT removed", text)
        self.assertIn("VIRTUAL_ENV=/definitely/elsewhere", text)
        self.assertEqual({lvl for lvl, n, _ in results if n == "env"}, {"warn"})

    def test_clean_env_and_matching_venv_are_ok(self):
        results = self.levels({"VIRTUAL_ENV": sys.prefix})
        self.assertIn(("ok", "env", "no leaked variables"), results)

    def test_load_platform_and_python_checks(self):
        busy = self.levels({}, loadavg=(40.0, 1, 1))
        self.assertEqual([l for l, n, _ in busy if n == "load"], ["warn"])
        windows = self.levels({}, platform="win32")
        self.assertEqual([l for l, n, _ in windows if n == "platform"], ["ok"])
        old = self.levels({}, python=(3, 9, 0))
        self.assertEqual([l for l, n, _ in old if n == "python"], ["error"])

    def test_exit_status_is_zero_with_warnings_only(self):
        lines = []
        self.assertEqual(doctor.run(out=lines.append, cwd=self.tmp, env={"PYTHONPATH": "/x"}), 0)
        self.assertIn("warning(s)", lines[-1])
        (self.tmp / "hca.toml").write_text("[task]\nname = 'x'\n")
        lines.clear()
        self.assertEqual(doctor.run(out=lines.append, cwd=self.tmp, env={}), 1)       # invalid spec is an error
        self.assertTrue(any("hca.toml" in line and "error" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
