import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from hca import runner
from hca.core import HcaError


WINDOWS = os.name == "nt"
# A command killed by hca: negative signal number on POSIX, the exit code taskkill / TerminateJobObject set on Windows.
KILLED = 1 if WINDOWS else None


def detached_kwargs():
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}


from repokit import alive  # noqa: E402



class RunnerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-run-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def py(self, code, name="r", timeout=20, **kwargs):
        return runner.run([sys.executable, "-c", code], self.tmp, self.tmp / "out", name, timeout, **kwargs)

    def test_wall_time_is_not_quantised_by_polling(self):
        # Regression: Popen.wait(timeout=...) polls with sleeps up to 50 ms, so commands of 30 ms and
        # 50 ms both used to read as the same ~64 ms. A referee that cannot see a 20 ms gap is blind.
        short = min(self.py("import time; time.sleep(0.03)", name=f"s{i}").seconds for i in range(3))
        long = min(self.py("import time; time.sleep(0.05)", name=f"l{i}").seconds for i in range(3))
        # Windows sleeps in 15.6 ms ticks, so the 20 ms gap can read as 31 ms there; the polling bug it guards against was 50 ms.
        self.assertAlmostEqual(long - short, 0.02, delta=0.016 if WINDOWS else 0.008)

    def test_captures_streams_to_files_and_reports_status(self):
        result = self.py("import sys; print('hello'); sys.stderr.write('oops'); sys.exit(3)")
        self.assertEqual((result.returncode, result.timed_out, result.ok), (3, False, False))
        self.assertEqual(result.stdout_path.read_text(), "hello\n")
        self.assertEqual(result.stderr_path.read_text(), "oops")
        self.assertEqual(result.error_tail, "oops")
        self.assertGreater(result.seconds, 0)
        self.assertTrue(self.py("pass", name="ok").ok)

    def test_error_tail_is_the_last_4096_bytes_in_full(self):
        result = self.py("import sys; sys.stderr.write('A' * 6000 + 'B' * 4000); sys.exit(1)")
        self.assertEqual(len(result.error_tail), 4096)
        self.assertEqual(result.error_tail, "A" * 96 + "B" * 4000)
        self.assertEqual(result.stderr_path.stat().st_size, 10000)

    def test_large_output_neither_stalls_nor_is_held_in_memory(self):
        result = self.py("import sys\nfor _ in range(50):\n    sys.stdout.write('x' * 100000); sys.stderr.write('y' * 100000)")
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout_path.stat().st_size, 5_000_000)
        self.assertEqual(result.stderr_path.stat().st_size, 5_000_000)

    def test_env_denylist_removes_matching_vars_and_records_them(self):
        base = {"PATH": os.environ["PATH"], "UV_CACHE_DIR": "/x", "UV_PYTHON": "y", "MPLBACKEND": "agg", "KEEP_ME": "1"}
        result = self.py("import os; print(sorted(k for k in os.environ if k in ('UV_CACHE_DIR','UV_PYTHON','MPLBACKEND','KEEP_ME','HCA_ARM')), os.environ.get('HCA_ARM'))",
                         deny=("UV_*", "MPLBACKEND"), env_extra={"HCA_ARM": "baseline"}, base_env=base)
        self.assertEqual(result.stdout_path.read_text().strip(), "['HCA_ARM', 'KEEP_ME'] baseline")
        self.assertEqual(result.dropped_env, ("MPLBACKEND", "UV_CACHE_DIR", "UV_PYTHON"))
        env, dropped = runner.build_env(("PY*",), {"PYTHONPATH": "kept-because-extra"}, {"PYTHONHOME": "h", "A": "b"})
        self.assertEqual((env, dropped), ({"A": "b", "PYTHONPATH": "kept-because-extra"}, ("PYTHONHOME",)))

    def test_missing_binary_is_an_invalid_run_not_an_exception(self):
        result = runner.run(["definitely-not-a-binary-xyz"], self.tmp, self.tmp / "out", "m", 5)
        self.assertEqual(result.returncode, 127)
        self.assertIn("could not start", result.error_tail)
        with self.assertRaises(HcaError):
            runner.run([], self.tmp, self.tmp / "out", "m", 5)

    def test_timeout_kills_the_whole_group_including_a_grandchild(self):
        pid_file = self.tmp / "grandchild.pid"
        code = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(60)\n")
        bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **detached_kwargs())
        self.addCleanup(bystander.wait)
        self.addCleanup(bystander.kill)
        started = time.monotonic()
        result = self.py(code, timeout=1.5)
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 6)
        if WINDOWS:
            self.assertNotEqual(result.returncode, 0)
        else:
            self.assertLess(result.returncode, 0)
        grandchild = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(grandchild), "the grandchild survived the timeout")
        self.assertIsNone(bystander.poll(), "a process outside the group was killed")

    def test_sigterm_ignoring_command_is_killed_after_the_grace_period(self):
        ignored = "SIGBREAK" if WINDOWS else "SIGTERM"      # Windows asks with Ctrl-Break, which Python calls SIGBREAK
        code = f"import signal, time; signal.signal(signal.{ignored}, signal.SIG_IGN); time.sleep(60)"
        started = time.monotonic()
        result = self.py(code, timeout=1.0)
        elapsed = time.monotonic() - started
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, KILLED if WINDOWS else -signal.SIGKILL)
        self.assertGreaterEqual(elapsed, 1.0 + runner.GRACE_SECONDS - 0.2)
        self.assertLess(elapsed, 8)

    def test_leftover_background_process_is_cleaned_up_after_a_normal_exit(self):
        pid_file = self.tmp / "orphan.pid"
        code = ("import subprocess, sys\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n")
        result = self.py(code)
        self.assertTrue(result.ok)
        self.assertTrue(result.orphans_killed)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid))


if __name__ == "__main__":
    unittest.main()
