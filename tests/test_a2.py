"""Regression tests for Amendment A2 (snapshot arms, a referee that verifies itself): one per Phase 1 review finding.

Each test names the finding it pins (S1..S20, N1..N7 from `.private/2026-10-15-phase1-code-review.md`) and the
A2.6 item number. The fixture is tests/repokit.py: a frozen `bench/run.sh` printing `METRIC t_ms` from `src/speed.txt`
with deterministic jitter, so a verdict never depends on how busy the machine is.
"""

import contextlib
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import repokit
from hca import arms, cli, gitops, ledger, measure
from hca.commands import common, report, try_
from hca.core import HcaError, Refusal
from hca.spec import load_spec
from repokit import env

PY = sys.executable
REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


from repokit import alive  # noqa: E402



class Started(repokit.RepoTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init()[0], 0)

    def records(self):
        return ledger.read_confirms("speedy")


class IndexAndStateTests(Started):
    toml_args = {"limits": {"confirms": 3}}

    def test_an_index_only_change_to_a_frozen_file_is_never_committed(self):
        # A2.6 #1, S1. The index holds a tampered evaluator, the working tree the original: the old keep committed the index.
        original = self.repo.read("bench/run.sh")
        tampered = original.replace("speed * (1 + jit * r)", "speed * 0.5 * (1 + jit * r)")
        self.assertNotEqual(tampered, original)
        self.repo.write("bench/run.sh", tampered)
        self.repo.git("add", "bench/run.sh")
        self.repo.write("bench/run.sh", original)
        self.assertIn("M bench/run.sh", "\n".join(self.status_lines()))
        self.set_speed(28)
        code, text = self.try_("a real win")
        self.assertEqual(code, 0, text)
        self.assertIn("your index has staged changes outside editable (bench/run.sh)", text)
        self.assertEqual(self.repo.git("show", "--name-only", "--format=", "HEAD"), "src/speed.txt")
        self.assertEqual(self.repo.git("show", "HEAD:bench/run.sh") + "\n", original)
        self.assertEqual(self.repo.git("diff", "--cached", "--name-only"), "")          # the keep reset the poisoned index
        code, text = self.confirm()
        self.assertEqual(code, 0, text)
        self.assertGreater(self.records()[0]["estimate"], -20)                            # about -7%, not the -53% of the repro

    def test_a_tampered_state_json_is_overridden_by_the_store_and_confirm_refuses_an_out_of_scope_history(self):
        # A2.6 #3, S3 (repro r4): a halved evaluator committed on the branch, with state.json rewritten to bless it.
        self.set_speed(21)
        self.assertEqual(self.try_("real win")[0], 0)
        honest_init = self.state()["init_sha"]
        run_sh = self.repo.read("bench/run.sh")
        cheat = run_sh.replace("speed * (1 + jit * r)", "speed * 0.5 * (1 + jit * r)")
        self.repo.write("bench/run.sh", cheat)
        sneaky = self.repo.commit_all("sneaky")
        state = self.state()
        state.update(last_keep=sneaky, init_sha=sneaky)                                   # the agent re-baselines on its own commit ...
        state["frozen"]["bench/run.sh"] = sha256(cheat)                                   # ... and re-hashes the evaluator
        ledger.save_state(self.root, state)
        code, text = self.confirm()
        self.assertEqual(code, 2, text)
        self.assertIn("disagrees with the referee store about 'init_sha'", text)
        self.assertIn("disagrees with the referee store about 'frozen'", text)
        self.assertIn("CONFIRM refused", text)
        self.assertIn("outside the editable scope", text)
        self.assertIn("is not an hca keep", text)
        self.assertEqual(self.records(), [])                                              # nothing measured, nothing charged
        self.assertEqual(ledger.confirms_used({}, "speedy"), 0)
        code, text = self.try_("another")                                                 # try applies the same checks, cheaply
        self.assertEqual(code, 2, text)
        self.assertIn("(history)", text)
        self.assertEqual(ledger.load_store("speedy")["init_sha"], honest_init)

    def test_init_reset_with_confirms_is_refused_without_allow_reset_and_is_logged_with_it(self):
        # A2.6 #4, S3 (repro r1): `hca init --reset` used to zero confirms_used and re-baseline.
        self.set_speed(21)
        self.assertEqual(self.try_("win")[0], 0)
        self.assertEqual(self.confirm()[0], 0)
        self.assertEqual(ledger.confirms_used(self.state(), "speedy"), 1)
        before = self.state()["init_sha"]
        with self.assertRaises(HcaError) as caught:
            self.init(reset=True)
        self.assertIn("HCA_ALLOW_RESET=1", str(caught.exception))
        self.assertEqual(self.state()["init_sha"], before)
        self.assertEqual(ledger.load_store("speedy")["resets"], [])
        import shutil                                                                      # deleting .hca and starting afresh is no way round it
        shutil.rmtree(self.root / ".hca", ignore_errors=True)
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("referee store", str(caught.exception))
        with env(HCA_ALLOW_RESET="1"):
            code, text = self.init(reset=True)
        self.assertEqual(code, 0, text)
        (reset,) = ledger.load_store("speedy")["resets"]
        self.assertEqual((reset["previous_init_sha"], reset["allowed_by_env"], reset["confirms_used"]), (before, True, 1))
        self.assertEqual(ledger.confirms_used(self.state(), "speedy"), 1)                  # never reset
        self.assertEqual(len(self.records()), 1)
        code, text = self.report()
        self.assertIn("re-initialised 1 time(s)", text)

    def test_a_tampered_confirm_counter_loses_to_the_store_and_a_legacy_state_without_a_store_id_is_adopted(self):
        state = self.state()
        state["confirms_used"] = 0
        ledger.save_state(self.root, state)
        self.set_speed(21)
        self.assertEqual(self.try_("win")[0], 0)
        self.assertEqual(ledger.confirms_used(self.state(), "speedy"), 0)
        # a state.json from before store ids (no store_id) gets the old behaviour: record a fresh store, with a warning
        state = self.state()
        state.pop("store_id")
        ledger.save_state(self.root, state)
        ledger.store_path("speedy").unlink()
        self.set_speed(20)
        code, text = self.try_("another win")
        self.assertEqual(code, 0, text)
        self.assertIn("no referee record for this task in the store", text)
        self.assertEqual(ledger.load_store("speedy")["store_id"], self.state()["store_id"])


class ArmTests(Started):
    def test_a_tampered_baseline_worktree_is_detected_and_recreated(self):
        # A2.6 #2, S2: the agent (or a stray tool) edits the reused baseline worktree.
        arm = arms.arm_dir(self.root, self.state()["init_sha"])
        (arm / "src" / "speed.txt").write_text("60\n")
        self.repo.write("src/speed.txt", "30.0\n")                                         # byte-different, value-identical
        code, text = self.try_("a no-op")
        self.assertEqual(code, 1, text)                                                    # not the old KEEP of -49.7%
        self.assertIn("was modified or damaged (tracked files were modified (src/speed.txt)); recreating it", text)
        self.assertEqual((arm / "src" / "speed.txt").read_text(), "30\n")
        self.assertTrue(abs(self.attempt(1)["estimate"]) < 4, self.attempt(1)["estimate"])

    def test_an_arm_modified_while_it_was_measured_voids_the_try(self):
        # The comparison is untrustworthy if anything wrote into the baseline during the run (S2, post-run verification).
        arm = arms.arm_dir(self.root, self.state()["init_sha"])
        self.set_speed(21)
        with env(BENCH_TAMPER=str(arm / "src" / "speed.txt")):
            code, text = self.try_("the benchmark writes to its sibling")
        self.assertEqual(code, 2, text)
        a = self.attempt(1)
        self.assertEqual(a["verdict"], "void")
        self.assertTrue(any("baseline arm was modified while it was measured" in r for r in a["reasons"]), a["reasons"])
        self.assertEqual(self.repo.head(), self.state()["last_keep"])
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")

    def test_a_wrong_head_in_an_arm_is_recreated(self):
        sha = self.state()["init_sha"]
        arm = arms.arm_dir(self.root, sha)
        self.repo.write("src/speed.txt", "29\n")
        other = self.repo.commit_all("elsewhere")
        subprocess.run(["git", "-C", str(arm), "checkout", "-q", "--detach", other], check=True)
        self.assertIn("its HEAD is", arms.verify_arm(arm, sha))
        self.repo.git("reset", "-q", "--hard", sha)
        arms.ensure_arm(self.root, load_spec(self.root / "hca.toml"), sha, warn=self.out)
        self.assertIsNone(arms.verify_arm(arm, sha))
        self.assertIn("recreating", self.text)

    def test_the_candidate_arm_is_a_worktree_not_the_checkout(self):
        # S15/S4: the benchmark's cwd is never the repo root, so `.hca/` (and anything ignored there) is invisible to both arms.
        self.set_speed(21)
        self.assertEqual(self.try_("faster")[0], 0)
        self.assertEqual(self.attempt(1)["snapshot"], self.repo.head())                    # a keep adopts the measured commit itself
        self.assertEqual(self.repo.git("rev-parse", "refs/hca/attempts/0001"), self.repo.head())   # the snapshot stays reachable
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")


class IgnoredFileTests(repokit.RepoTestCase):
    def test_an_ignored_file_in_the_checkout_does_not_advantage_the_candidate(self):
        # A2.6 #5, S4 (repro r1): the benchmark halves its time when cache/warm exists; cache/ is gitignored.
        self.repo.write(".gitignore", "cache/\n")
        self.repo.commit_all("ignore the cache")
        self.assertEqual(self.init()[0], 0)
        self.repo.write("cache/warm", "x\n")
        self.set_speed("30.0")
        code, text = self.try_("value-identical edit")
        self.assertEqual(code, 1, text)                                                    # was KEEP -50.4%, p=0.00098
        self.assertLess(abs(self.attempt(1)["estimate"]), 5)
        self.set_speed("30.00")
        self.assertEqual(self.try_("again, different bytes")[0], 1)
        arm_dirs = [p for p in (self.root / ".hca" / "arms").iterdir() if p.is_dir()]
        self.assertTrue(arm_dirs and not any((p / "cache").exists() for p in arm_dirs))


class LinkedFileTests(repokit.RepoTestCase):
    toml_args = {"extra_task": 'link = ["cache"]', "frozen": '["bench/**", "cache/**"]'}

    def test_a_linked_ignored_path_is_shared_by_both_arms_and_protected_by_frozen(self):
        self.repo.write(".gitignore", "cache/\n")
        self.repo.commit_all("ignore the cache")
        self.repo.write("cache/warm", "x\n")
        self.assertEqual(self.init()[0], 0)
        self.assertIn("cache/warm", self.state()["frozen"])                                # hashed although ignored and linked
        self.set_speed("30.0")
        code, text = self.try_("no-op")
        self.assertEqual(code, 1, text)
        self.assertLess(abs(self.attempt(1)["estimate"]), 5)                              # both arms see the same linked cache
        for arm in (self.root / ".hca" / "arms").iterdir():
            if arm.is_dir():
                self.assertTrue((arm / "cache").is_symlink())
        self.repo.write("cache/warm", "tampered\n")
        self.set_speed("30.00")
        code, text = self.try_("after tampering with the frozen dataset")
        self.assertEqual(code, 2, text)
        self.assertIn("cache/warm: frozen file differs from the sha256 recorded at init", text)


class MeasurementIntegrityTests(Started):
    def test_a_file_changed_during_measurement_gives_void_with_nothing_committed(self):
        # A2.6 #6, S5 (repro r3): the old keep committed whatever was on disk afterwards (500, a 16x regression).
        counter = self.tmp / "state" / "count"
        start = int(counter.read_text())                                                   # init already ran 18 benchmark runs
        stop = threading.Event()

        def writer():
            while not stop.is_set():
                try:
                    if int(counter.read_text() or 0) >= start + 15:
                        (self.root / "src" / "speed.txt").write_text("500\n")
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(0.002)

        self.set_speed(21)
        thread = threading.Thread(target=writer)
        thread.start()
        try:
            with env(BENCH_SLEEP_MS="30"):
                code, text = self.try_("measured 21, then someone writes 500")
        finally:
            stop.set()
            thread.join()
        self.assertEqual(code, 2, text)
        a = self.attempt(1)
        self.assertEqual(a["verdict"], "void")
        self.assertTrue(any("changed while the try was being measured" in r for r in a["reasons"]), a["reasons"])
        self.assertEqual(self.repo.head(), self.state()["last_keep"])
        self.assertEqual(self.repo.git("show", "HEAD:src/speed.txt"), "30")
        self.assertEqual(self.repo.read("src/speed.txt"), "500\n")                         # the edits are left as they are
        self.assertFalse((self.root / ".hca" / "pending-keep.json").exists())

    def test_a_candidate_printing_the_objective_metric_twice_is_candidate_invalid(self):
        # A2.6 #9, S8 (repro r9): an extra `METRIC t_ms=0.5` made the old referee KEEP at -100%.
        self.repo.write("src/echo_metric.txt", "1\n")
        code, text = self.try_("print the metric a second time")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", False))
        self.assertTrue(any("candidate failed or reported invalid" in r for r in a["reasons"]), a["reasons"])
        records = [json.loads(line) for line in (ledger.attempt_path(self.root, 1) / "runs.jsonl").read_text().splitlines()]
        bad = [r for r in records if r.get("kind") == "run" and not r["ok"]]
        self.assertTrue(bad and all(r["arm"] == "candidate" and "printed 2 times" in r["problem"] for r in bad), bad)

    def test_hca_result_file_takes_precedence_over_stdout(self):
        # A2.6 #9: with the evaluator writing to HCA_RESULT_FILE, stdout noise (even a fake METRIC line) is ignored.
        self.repo.write("src/echo_metric.txt", "1\n")
        self.set_speed(21)
        with env(BENCH_RESULT_FILE="1"):
            code, text = self.try_("real win with a noisy stdout")
        self.assertEqual(code, 0, text)
        self.assertAlmostEqual(self.attempt(1)["estimate"], -30, delta=4)                  # -30%, not -98%

    def test_hca_arm_is_not_exported_and_the_result_file_is_fresh_per_run(self):
        command = [PY, "-c", "import os; print('METRIC lat=%d' % (100 + ('HCA_ARM' in os.environ)))"]
        base, cand = self.tmp / "b", self.tmp / "c"
        base.mkdir()
        cand.mkdir()
        import random
        with env(HCA_ARM="baseline"):
            result = measure.measure_blocks(command, "lat", "min", base, cand, 3, random.Random(1), {"HCA_ITEMS": "dev"}, self.tmp / "out", 20)
        self.assertEqual(result.arm_medians, {"baseline": {"lat": 100.0}, "candidate": {"lat": 100.0}})     # not even the caller's HCA_ARM leaks
        writer = [PY, "-c", "import os; open(os.environ['HCA_RESULT_FILE'], 'a').write('METRIC lat=7\\n'); print('METRIC lat=99')"]
        result = measure.measure_blocks(writer, "lat", "min", base, cand, 3, random.Random(1), {}, self.tmp / "out2", 20)
        self.assertEqual(result.arm_medians["baseline"]["lat"], 7.0)                       # one line, not two: the file is new for every run

    def test_every_try_runs_one_discarded_warm_up_per_arm(self):
        # S14: a cold first run must not land in a measured block.
        count = self.tmp / "state" / "count"
        before = int(count.read_text())
        self.set_speed("30.0000001")
        self.assertEqual(self.try_("noop")[0], 1)
        self.assertEqual(int(count.read_text()) - before, 2 + 2 * 10)                      # two warm-ups, then 10 blocks of two runs
        records = [json.loads(line) for line in (ledger.attempt_path(self.root, 1) / "runs.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(r["arm"] for r in records if r["kind"] == "warmup"), ["baseline", "candidate"])
        self.assertEqual(len([r for r in records if r["kind"] == "run"]), 20)

    def test_try_seconds_covers_the_build(self):
        # S17: only the blocks were under try_seconds; a slow candidate build could run for command_seconds.
        self.repo.write("hca.toml", repokit.toml(limits={"try_seconds": 1}, build="[build]\ncommand = " + json.dumps(
            [PY, "-c", "import time; time.sleep(0 if float(open('src/speed.txt').read()) >= 29 else 5)"]) + "\n"))
        self.repo.commit_all("slow build for fast candidates")
        with env(HCA_ALLOW_RESET="1"):
            self.assertEqual(self.init(reset=True)[0], 0)
        self.set_speed(21)
        started = time.monotonic()
        code, text = self.try_("slow build")
        self.assertEqual(code, 2, text)
        self.assertLess(time.monotonic() - started, 4)
        a = self.attempt(1)
        self.assertEqual(a["verdict"], "void")
        self.assertTrue(any("try_seconds" in r and "build" in r for r in a["reasons"]), a["reasons"])
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")


class InsufficientTests(Started):
    toml_args = {"limits": {"extend_to": 20}}        # the shipped two-look rule: alpha/2 per look needs 8 scored blocks

    def test_insufficient_blocks_give_void_not_a_valid_revert(self):
        # A2.6 #13, S12: 7 scored blocks cannot reach p <= 0.005, so the try cannot reject; the real edit must not be thrown away.
        self.set_speed(21)
        result = measure.BlockResult([0.35] * 7, list(range(7)), [7, 8, 9], False, 10, {"baseline": {}, "candidate": {}}, {}, 0, [], 0.01, 0.2)
        with mock.patch.object(measure, "run_blocks", return_value=result):
            code, text = self.try_("a real win with three voided blocks")
        self.assertEqual(code, 2, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("void", False))
        self.assertTrue(any("cannot reach p" in r for r in a["reasons"]), a["reasons"])
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")                          # edits kept
        self.assertEqual(self.repo.head(), self.state()["last_keep"])


class ReplayTests(Started):
    toml_args = {"limits": {"extend_to": 20}}

    def test_a_replayed_tree_is_refused_without_again_and_counted_with_it(self):
        # A2.6 #10, S9: each try is a fresh 1% test; k replays of a no-op keep it with probability 1-0.99^k.
        count = self.tmp / "state" / "count"
        self.set_speed("30.0000001")
        self.assertEqual(self.try_("first")[0], 1)
        self.repo.write("src/speed.txt", "30.0000001\n")                                   # the same tree again
        before = count.read_text()
        code, text = self.try_("same again")
        self.assertEqual(code, 2, text)
        self.assertIn("this exact tree was already tried as attempt 0001", text)
        self.assertIn("--again", text)
        self.assertEqual(count.read_text(), before)                                        # nothing measured
        a = self.attempt(2)
        self.assertEqual((a["verdict"], a["valid"], a["replay_of"]), ("invalid", False, 1))
        self.assertEqual(self.repo.read("src/speed.txt"), "30.0000001\n")                  # edits left in place
        code, text = self.try_("deliberate", again=True)
        self.assertEqual(code, 1, text)
        a = self.attempt(3)
        self.assertEqual((a["again"], a["replay_of"]), (True, 1))
        self.assertEqual(ledger.tries_used(self.root, "speedy"), 3)
        store = ledger.load_store("speedy")
        self.assertEqual([(e["n"], e["again"]) for e in store["attempts"]], [(1, False), (2, False), (3, True)])
        self.assertEqual(len({e["tree"] for e in store["attempts"]}), 1)

    def test_void_and_invalid_attempts_count_toward_max_tries_and_the_report_says_what_chance_allows(self):
        # A2.4: past max_tries try still runs but warns on every try; report prints the family-wise line.
        self.repo.write("hca.toml", self.repo.read("hca.toml").replace('useful_effect = "5%"', 'useful_effect = "5%"\nmax_tries = 2'))
        self.repo.commit_all("max_tries 2")
        with env(HCA_ALLOW_RESET="1"):
            self.assertEqual(self.init(reset=True)[0], 0)
        self.repo.write("README.md", "scope violation\n")
        self.assertEqual(self.try_("invalid one")[0], 2)
        self.repo.git("checkout", "--", "README.md")
        result = measure.BlockResult([0.35] * 7, list(range(7)), [7, 8, 9], False, 10, {"baseline": {}, "candidate": {}}, {}, 0, [], 0.01, 0.2)
        self.set_speed(21)
        with mock.patch.object(measure, "run_blocks", return_value=result):
            code, text = self.try_("void two")
        self.assertEqual(code, 2, text)
        self.assertNotIn("past max_tries", text)
        self.set_speed(20)
        code, text = self.try_("third")
        self.assertIn("warning: this is try 3, past max_tries (2)", text)
        code, text = self.report()
        self.assertIn("Expected false keeps so far <= 3 tries x 1% = 0.03", text)
        code, text = self.hca(report, args={"referee": True})
        self.assertIn("Referee store for speedy", text)


class PendingKeepTests(Started):
    def test_a_crash_between_update_ref_and_the_state_write_is_reconciled(self):
        # A2.6 #8, S7: the old referee wedged ("HEAD is X but the last keep is Y") and only `init --reset` got out.
        before = self.repo.head()
        self.set_speed(21)
        with mock.patch.object(try_, "_record", side_effect=SystemExit(9)):             # stands in for kill -9 after the branch moved
            with self.assertRaises(SystemExit):
                self.try_("win")
        moved = self.repo.head()
        self.assertNotEqual(moved, before)
        self.assertEqual(self.state()["last_keep"], before)                                # state.json never saw the keep
        self.assertTrue((self.root / ".hca" / "pending-keep.json").exists())
        code, text = self.confirm()                                                        # any command reconciles first
        self.assertIn("finished an interrupted keep", text)
        self.assertEqual(code, 0, text)
        self.assertEqual(self.state()["last_keep"], moved)
        self.assertFalse((self.root / ".hca" / "pending-keep.json").exists())
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["commit"]), ("keep", moved))
        self.assertTrue(any("recovered" in r for r in a["reasons"]))

    def test_a_pending_keep_whose_branch_never_moved_is_discarded(self):
        before = self.repo.head()
        self.set_speed(21)
        snap = gitops.snapshot(self.root, load_spec(self.root / "hca.toml"), before, "hca keep 0001: x", ["src/speed.txt"])
        n, adir = ledger.new_attempt(self.root)
        record = {"n": n, "verdict": "keep", "valid": True, "commit": snap["sha"]}
        common.write_pending(self.root, "speedy", {"kind": "keep", "n": n, "snapshot_sha": snap["sha"], "last_keep": before,
                                                   "record": record, "digest": ledger.verdict_digest(record)})
        code, text = self.try_("a fresh try after the crash")
        self.assertIn("discarded a pending keep that was never applied", text)
        self.assertEqual(json.loads((adir / "verdict.json").read_text())["verdict"], "void")
        self.assertEqual(code, 0, text)


@unittest.skipIf(os.name == "nt", "POSIX signals and process groups: SIGINT/SIGTERM cannot be sent to a child on Windows, and killpg does not exist")
class SignalTests(Started):
    def run_and_signal(self, sig):
        pidfile = self.tmp / "pids"
        environ = dict(os.environ, BENCH_SLEEP_MS="6000", BENCH_PIDFILE=str(pidfile), PYTHONPATH=str(REPO_ROOT))
        proc = subprocess.Popen([PY, "-m", "hca", "try", "-m", "interrupted"], cwd=self.root, env=environ,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (pidfile.exists() and pidfile.read_text().strip()):
            time.sleep(0.05)
        self.assertTrue(pidfile.exists(), "the benchmark never started")
        pids = [int(p) for p in pidfile.read_text().split()]
        self.assertTrue(all(alive(p) for p in pids))
        proc.send_signal(sig)
        out, err = proc.communicate(timeout=30)
        return proc.returncode, out, err, pids

    def test_sigint_and_sigterm_kill_the_benchmark_group_and_record_interrupted(self):
        # A2.6 #7, S6: the child is its own session, so Ctrl-C never reached it and it ran on for up to command_seconds.
        for n, (sig, code) in enumerate(((signal.SIGINT, 130), (signal.SIGTERM, 128 + signal.SIGTERM)), start=1):
            with self.subTest(signal=sig.name):
                self.set_speed(21 - n)
                returncode, out, err, pids = self.run_and_signal(sig)
                self.assertEqual(returncode, code, err)
                time.sleep(0.2)
                for pid in pids:
                    self.assertFalse(alive(pid), f"benchmark process {pid} survived")
                    with self.assertRaises(ProcessLookupError):
                        os.killpg(pid, 0)                                                   # nor anything in its group (the sleep)
                a = self.attempt(n)
                self.assertEqual((a["verdict"], a["valid"]), ("interrupted", False))
                self.assertIn("INTERRUPTED", out)
                self.assertFalse((self.root / ".hca" / "lock").exists())
                self.assertEqual(self.repo.head(), self.state()["last_keep"])
                self.assertEqual(self.repo.read("src/speed.txt"), f"{21 - n}\n")           # the edits are kept
                pidfile = self.tmp / "pids"
                pidfile.unlink()


class CrashAndCliTests(Started):
    def test_an_internal_error_exits_4_and_writes_a_crash_log(self):
        # A2.6 #11, S10: any non-HcaError used to exit 1, the same as a normal revert.
        err = io.StringIO()
        with contextlib.chdir(self.root), mock.patch.object(report, "run_status", side_effect=RuntimeError("boom")), contextlib.redirect_stderr(err):
            code = cli.main(["status"])
        self.assertEqual(code, 4)
        self.assertIn("hca: internal error: RuntimeError: boom", err.getvalue())
        logs = list((self.root / ".hca").glob("crash-*.log"))
        self.assertEqual(len(logs), 1)
        self.assertIn("RuntimeError: boom", logs[0].read_text())

    def test_signal_handlers_are_installed_for_the_run_and_restored(self):
        names = [n for n in ("SIGTERM", "SIGHUP", "SIGBREAK") if hasattr(signal, n)]      # SIGHUP is POSIX, SIGBREAK Windows
        before = tuple(signal.getsignal(getattr(signal, n)) for n in names)
        seen = {}

        def fake_status(args, out=print, cwd=None):
            seen.update({n: signal.getsignal(getattr(signal, n)) for n in names})
            return 0

        with contextlib.chdir(self.root), mock.patch.object(report, "run_status", fake_status):
            self.assertEqual(cli.main(["status"]), 0)
        for n, old in zip(names, before):
            self.assertNotEqual(seen[n], old, n)
        self.assertEqual(tuple(signal.getsignal(getattr(signal, n)) for n in names), before)
        with self.assertRaises(KeyboardInterrupt):
            seen["SIGTERM"](signal.SIGTERM, None)


class ScopeAndNoiseTests(Started):
    def test_pycache_under_editable_is_ignored(self):
        # A2.6 #12, S11: python writes src/__pycache__ as a side effect and an empty try used to be "measured".
        self.repo.write("src/__pycache__/speed.cpython-311.pyc", "bytecode")
        self.repo.write("src/mod.pyc", "bytecode")
        self.repo.write("src/.DS_Store", "finder")
        code, text = self.try_("nothing but caches")
        self.assertEqual(code, 2, text)
        self.assertIn("no changes", text)
        self.set_speed(21)
        self.assertEqual(self.try_("a real change next to the caches")[0], 0)
        self.assertEqual(self.repo.git("show", "--name-only", "--format=", "HEAD"), "src/speed.txt")
        self.assertTrue((self.root / "src" / "__pycache__" / "speed.cpython-311.pyc").exists())   # and never deleted

    def test_init_refuses_submodules_and_unfinished_merges(self):
        # A2.1, S18/N4
        fresh = repokit.make_repo(self.tmp / "fresh")
        fresh.write(".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ./x\n')
        fresh.commit_all("a submodule file")
        from hca.commands import init
        with self.assertRaises(HcaError) as caught:
            init.run(mock.Mock(heldout=None, force=False, reset=False), out=self.out, cwd=fresh.path)
        self.assertIn("submodules", str(caught.exception))
        (fresh.path / ".gitmodules").unlink()
        fresh.commit_all("remove it")
        (fresh.path / ".git" / "MERGE_HEAD").write_text(fresh.head() + "\n")
        with self.assertRaises(HcaError) as caught:
            init.run(mock.Mock(heldout=None, force=False, reset=False), out=self.out, cwd=fresh.path)
        self.assertIn("merge is in progress", str(caught.exception))


class ItemsInfraTests(repokit.RepoTestCase):
    mode = "items"

    def test_a_candidate_side_infra_invalid_item_is_scored_as_candidate_invalid(self):
        # A2.6 #14, S13: it used to drop the item from both arms (up to 10% of items, the candidate's worst ones) uncounted.
        self.assertEqual(self.init()[0], 0)
        self.repo.write("src/boost.txt", "0.2\n")
        self.repo.write("src/infra.txt", "1\n")
        code, text = self.try_("gains, and the evaluator relays infra_invalid for one item")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", False))
        self.assertTrue(any("invalid in the candidate" in r for r in a["reasons"]), a["reasons"])


class LockTests(repokit.RepoTestCase):
    def test_a_recycled_pid_does_not_make_a_stale_lock_look_live_and_the_message_names_rm(self):
        # A2.5, N2: pid + /proc start time.
        self.init()
        path = self.root / ".hca" / "lock"
        mine = ledger.proc_start_time(os.getpid())
        self.assertIsNotNone(mine)
        path.write_text(json.dumps({"pid": os.getpid(), "start": mine, "started": "now", "command": "try"}))
        with self.assertRaises(HcaError) as caught:
            with ledger.lock(self.root, "confirm"):
                pass
        self.assertIn("rm .hca/lock", str(caught.exception))
        path.write_text(json.dumps({"pid": os.getpid(), "start": mine + 12345, "started": "long ago", "command": "try"}))   # same pid, different process
        with ledger.lock(self.root, "confirm", out=self.out):
            pass
        self.assertIn("removing a stale lock", self.text)
        self.assertFalse(path.exists())


class UntrackedStateTests(Started):
    def test_a_disk_cache_the_candidate_writes_does_not_survive_between_runs(self):
        # NEW-3 (re-review repro): the candidate writes `.memo` on its first call and every later call is fast. The arm
        # used to keep the file, so the warm-up primed it and the try KEPT at about -50% (and confirm passed).
        self.repo.write("src/memo_writer.txt", "1\n")
        code, text = self.try_("cache across runs")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", True))
        self.assertLess(abs(a["estimate"]), 6, a["estimate"])
        for arm in (self.root / ".hca" / "arms").iterdir():
            self.assertFalse((arm / ".memo").exists(), arm)

    def test_a_cache_is_just_as_dead_when_the_candidate_really_is_faster(self):
        self.repo.write("src/memo_writer.txt", "1\n")
        self.set_speed(21)
        code, text = self.try_("a real win plus a cache")
        self.assertEqual(code, 0, text)
        self.assertAlmostEqual(self.attempt(1)["estimate"], -30, delta=4)                  # -30%, not -65%


class ArmCleaningTests(repokit.RepoTestCase):
    toml_args = {"extra_task": 'link = ["deps"]',
                 "build": '[build]\ncommand = ' + json.dumps([PY, "-c", "open('out.bin', 'w').write('built')"]) + "\n"}

    def setUp(self):
        super().setUp()
        self.repo.write(".gitignore", "deps/\n*.cache\n")
        self.repo.commit_all("ignore")
        self.repo.write("deps/lib.txt", "dependency\n")
        self.assertEqual(self.init()[0], 0)
        self.sha = self.state()["init_sha"]
        self.arm = arms.arm_dir(self.root, self.sha)
        self.spec = load_spec(self.root / "hca.toml")

    def test_untracked_files_are_deleted_links_and_build_outputs_are_not(self):
        manifest = json.loads((arms.arms_root(self.root) / f"{self.sha[:12]}.files").read_text())["files"]
        self.assertEqual(set(manifest), {"out.bin"})                                       # the build output only; the link symlink is not listed
        (self.arm / "junk.txt").write_text("x")
        (self.arm / "nested" / "deeper").mkdir(parents=True)
        (self.arm / "nested" / "deeper" / "x.cache").write_text("ignored by git, still gone")
        os.symlink(self.arm / "junk.txt", self.arm / "stray-link")
        arms.prepare_run(self.root, self.spec, self.sha, warn=self.out)
        self.assertEqual(sorted(p.name for p in self.arm.iterdir() if p.name != ".git"),
                         sorted(["README.md", "bench", "deps", "hca.toml", "out.bin", "src", ".gitignore"]))
        self.assertTrue((self.arm / "deps").is_symlink())                                  # never deleted ...
        self.assertEqual((self.root / "deps" / "lib.txt").read_text(), "dependency\n")     # ... nor was its target
        self.assertNotIn("warning", self.text)

    def test_a_changed_or_missing_build_output_recreates_and_rebuilds_the_arm(self):
        (self.arm / "out.bin").write_text("the benchmark rewrote its own build output")
        arms.prepare_run(self.root, self.spec, self.sha, warn=self.out)
        self.assertIn("build outputs were changed or removed by a run (out.bin); recreating and rebuilding", self.text)
        self.assertEqual((self.arm / "out.bin").read_text(), "built")
        self.assertTrue((self.arm / "deps").is_symlink())
        (self.arm / "out.bin").unlink()
        arms.prepare_run(self.root, self.spec, self.sha, warn=self.out)
        self.assertEqual((self.arm / "out.bin").read_text(), "built")
        self.assertIsNone(arms.verify_arm(self.arm, self.sha))

    def test_every_objective_run_including_the_warm_up_is_preceded_by_a_clean(self):
        calls = []
        import random
        base, cand = self.tmp / "b", self.tmp / "c"
        base.mkdir()
        cand.mkdir()
        command = [PY, "-c", "print('METRIC lat=10')"]
        prepare = {"baseline": lambda: calls.append("b"), "candidate": lambda: calls.append("c")}
        measure.measure_blocks(command, "lat", "min", base, cand, 3, random.Random(1), {}, self.tmp / "out", 20, warmup=True, prepare=prepare)
        self.assertEqual(len(calls), 2 + 6)
        self.assertEqual(sorted(calls[:2]), ["b", "c"])


class StoreIdentityTests(Started):
    def test_a_different_hca_home_is_refused_not_silently_rebuilt(self):
        # NEW-1 (re-review repro): the same edit twice, the second under another HCA_HOME, used to be measured with no
        # replay memory, no confirm history and no held-out set.
        self.set_speed("30.0000001")
        self.assertEqual(self.try_("first")[0], 1)
        other = self.tmp / "other-home"
        with env(HCA_HOME=str(other)):
            with self.assertRaises(Refusal) as caught:
                self.try_("same edit, other store")
            self.assertIn("HCA_HOME", str(caught.exception))
            self.assertIn(str(other), str(caught.exception))
            self.assertFalse(other.exists())                                               # nothing was created there
            err = io.StringIO()
            with contextlib.chdir(self.root), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["try", "-m", "x"]), 2)                           # refused, not a usage error
            self.assertIn("HCA_HOME", err.getvalue())
            with self.assertRaises(Refusal):
                self.confirm()
        self.assertEqual(len(ledger.read_attempts(self.root)), 1)

    def test_a_deleted_store_or_one_with_another_id_is_refused(self):
        ledger.store_path("speedy").unlink()
        with self.assertRaises(Refusal) as caught:
            self.try_("x")
        self.assertIn("has no record for this task", str(caught.exception))
        store = ledger.init_store("speedy", self.state()["init_sha"], "h", {}, {})             # a store that was started separately
        self.assertNotEqual(store["store_id"], self.state()["store_id"])
        with self.assertRaises(Refusal) as caught:
            self.try_("x")
        self.assertIn("holds a different record", str(caught.exception))
        self.assertTrue(self.state()["store_id"])

    def test_a_deleted_store_with_allow_reset_can_start_over(self):
        ledger.store_path("speedy").unlink()
        with env(HCA_ALLOW_RESET="1"):
            code, text = self.init(reset=True)
        self.assertEqual(code, 0, text)
        self.assertEqual(ledger.load_store("speedy")["store_id"], self.state()["store_id"])

    def test_replay_memory_also_reads_the_attempt_records(self):
        self.set_speed("30.0000001")
        self.assertEqual(self.try_("first")[0], 1)
        store = ledger.load_store("speedy")
        store["attempts"] = []
        ledger.save_store("speedy", store)
        self.set_speed("30.0000001")                                                       # the revert undid it; make the same edit again
        code, text = self.try_("same edit")
        self.assertEqual(code, 2, text)
        self.assertIn("already tried as attempt 0001", text)

    def test_confirm_refuses_when_the_heldout_set_is_gone(self):
        held = self.tmp / "held"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        with env(HCA_ALLOW_RESET="1"):
            self.assertEqual(self.init(heldout=str(held), reset=True)[0], 0)
        self.set_speed(21)
        self.assertEqual(self.try_("win")[0], 0)
        import shutil
        shutil.rmtree(ledger.store_dir("speedy") / "heldout")
        with self.assertRaises(Refusal) as caught:
            self.confirm()
        self.assertIn("held-out", str(caught.exception))
        self.assertEqual(self.records_count(), 0)

    def records_count(self):
        return len(ledger.read_confirms("speedy"))


class ForgedMarkerTests(Started):
    def test_a_forged_pending_keep_marker_is_refused_not_adopted(self):
        # NEW-2 (re-review repro): a hand-made commit with an hca-looking subject plus a marker with made-up numbers
        # used to become a valid keep with estimate -40% and p=1e-6 in the attempts table.
        before = self.repo.head()
        self.set_speed(21)
        forged = self.repo.commit_all("hca keep 0001: forged")
        marker = {"kind": "keep", "n": 1, "snapshot_sha": forged, "last_keep": before,
                  "record": {"n": 1, "verdict": "keep", "valid": True, "estimate": -40.0, "p": 1e-06, "commit": forged}}
        (self.root / ".hca").mkdir(exist_ok=True)
        (self.root / ".hca" / "pending-keep.json").write_text(json.dumps(marker))
        with self.assertRaises(Refusal) as caught:
            self.try_("next")
        self.assertIn("no pending keep", str(caught.exception))
        self.assertEqual(self.state()["last_keep"], before)
        self.assertEqual(ledger.read_attempts(self.root), [])
        code, text = self.report()
        self.assertNotIn("-40.0%", text)
        self.assertTrue((self.root / ".hca" / "pending-keep.json").exists())                # left for the human, not dropped

    def test_a_stale_marker_for_a_branch_that_never_moved_is_discarded_with_a_warning(self):
        before = self.repo.head()
        marker = {"kind": "keep", "n": 1, "snapshot_sha": "0" * 40, "last_keep": before}
        (self.root / ".hca" / "pending-keep.json").write_text(json.dumps(marker))
        self.set_speed(21)
        code, text = self.try_("fine")
        self.assertIn("discarded .hca/pending-keep.json", text)
        self.assertEqual(code, 0, text)

    def test_a_real_crash_whose_attempt_record_was_rewritten_is_refused(self):
        before = self.repo.head()
        self.set_speed(21)
        with mock.patch.object(try_, "_record", side_effect=SystemExit(9)):
            with self.assertRaises(SystemExit):
                self.try_("win")
        self.assertIsNotNone(ledger.get_pending("speedy"))                                  # the store knew before the branch moved
        adir = ledger.attempt_path(self.root, 1)
        adir.mkdir(exist_ok=True)
        (adir / "verdict.json").write_text(json.dumps({"n": 1, "verdict": "keep", "valid": True, "estimate": -99.0, "commit": self.repo.head()}))
        with self.assertRaises(Refusal) as caught:
            self.confirm()
        self.assertIn("does not match the verdict the referee store has", str(caught.exception))
        self.assertEqual(self.state()["last_keep"], before)


class RestoreCrashTests(repokit.RepoTestCase):
    toml_args = {"limits": {"extend_to": 20, "confirms": 3}}

    def test_a_kill_between_the_branch_move_and_the_work_tree_sync_is_finished(self):
        # NEW-4: a failed confirm moves the branch, then syncs the editable files; a kill in between left them undone.
        held = self.tmp / "held"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        self.assertEqual(self.init(heldout=str(held))[0], 0)
        self.repo.write("src/dev_hack.txt", "0.7\n")                                       # wins on dev items only
        self.assertEqual(self.try_("game the dev items")[0], 0)
        with mock.patch.object(gitops, "revert", side_effect=SystemExit(9)):
            with self.assertRaises(SystemExit):
                self.confirm()
        self.assertTrue((self.root / "src" / "dev_hack.txt").exists())                      # the undone content is still on disk
        self.assertNotEqual(self.state()["last_keep"], self.repo.head())
        code, text = self.try_("a fresh look")
        self.assertIn("finished an interrupted confirm reset", text)
        self.assertFalse((self.root / "src" / "dev_hack.txt").exists())
        self.assertEqual(self.state()["last_keep"], self.repo.head())
        self.assertEqual(self.attempt(1)["confirm"], "unconfirmed")
        self.assertIsNone(ledger.get_pending("speedy"))


class SnapshotRefTests(Started):
    def test_the_attempt_ref_does_not_exist_while_the_benchmark_runs(self):
        # S19 (partly): an arm could tell it is the candidate from the highest refs/hca/attempts/N. The ref is created at record time.
        count = self.tmp / "state" / "count"
        seen = []
        real = gitops.set_ref

        def spy(root, ref, sha):
            seen.append((ref, int(count.read_text())))
            return real(root, ref, sha)

        start = int(count.read_text())
        self.set_speed(21)
        with mock.patch.object(gitops, "set_ref", spy):
            self.assertEqual(self.try_("win")[0], 0)
        (ref, at_count), = seen
        self.assertEqual(ref, "refs/hca/attempts/0001")
        self.assertEqual(at_count - start, 2 + 2 * 10)                                      # both warm-ups and all 20 block runs came first
        self.assertEqual(self.repo.git("rev-parse", ref), self.repo.head())


class DoctorEditableTests(unittest.TestCase):
    def test_an_editable_install_pointing_at_the_repo_is_warned_about(self):
        import tempfile
        from hca.commands import doctor
        with tempfile.TemporaryDirectory(prefix="hca-doctor-edit-") as tmp:
            tmp = Path(tmp).resolve()
            repo = tmp / "repo"
            repo.mkdir()
            site = tmp / "site"
            info = site / "fakepkg-1.0.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text("Metadata-Version: 2.1\nName: fakepkg\nVersion: 1.0\n")
            (info / "direct_url.json").write_text(json.dumps({"url": f"file://{repo}", "dir_info": {"editable": True}}))
            (repo / "hca.toml").write_text('[task]\nname = "x"\neditable = ["src/**"]\n[objective]\ncommand = [%s, "bench.py"]\n'
                                           'metric = "m"\ndirection = "min"\nuseful_effect = "5%%"\n' % json.dumps(PY))
            env_ = dict(os.environ, PYTHONPATH=str(site))
            results = doctor.checks(repo, env_, loadavg=(0.1, 0.1, 0.1))
            hits = [m for lvl, n, m in results if n == "editable-install"]
            self.assertEqual(len(hits), 1, results)
            self.assertIn("fakepkg", hits[0])
            self.assertIn("sys.path", hits[0])
            other = tmp / "elsewhere"
            other.mkdir()
            (info / "direct_url.json").write_text(json.dumps({"url": f"file://{other}", "dir_info": {"editable": True}}))
            results = doctor.checks(repo, env_, loadavg=(0.1, 0.1, 0.1))
            self.assertEqual([m for lvl, n, m in results if n == "editable-install"], [])


class OutputTests(unittest.TestCase):
    def test_a_keep_whose_bound_is_not_positive_because_of_ties_says_so(self):
        # N1: signed_rank_p can reject while the (approximate, with ties) Walsh bound is <= 0.
        self.assertEqual(ledger.fmt_bound(0.0, "blocks", "min", kept=True), "bound not shown (tied scores)")
        self.assertEqual(ledger.fmt_bound(1.4, "blocks", "min", kept=True), "bound not shown (tied scores)")
        self.assertEqual(ledger.fmt_bound(1.4, "blocks", "min"), "+1.4%, no improvement shown")
        self.assertEqual(ledger.fmt_bound(-3.0, "blocks", "min", kept=True), "cut of at least 3.0%")
        rec = {"n": 1, "estimate": -6.0, "lower": 0.0, "p": 0.001, "blocks": 10, "extended": False}
        spec = mock.Mock()
        spec.objective.direction, spec.objective.metric = "min", "t_ms"
        text = try_._headline(rec, "keep", "blocks", 1.0, spec)
        self.assertIn("bound not shown (tied scores)", text)
        self.assertNotIn("no improvement shown", text)

    def test_noise_report_needs_eight_blocks(self):
        from hca import stats
        useful = stats.parse_effect("5%")
        with self.assertRaises(HcaError) as caught:
            stats.noise_report([0.01, -0.01, 0.0, 0.02, -0.02, 0.01, 0.0], useful)
        self.assertIn("at least 8", str(caught.exception))
        self.assertEqual(stats.noise_report([0.01, -0.01, 0.0, 0.02, -0.02, 0.01, 0.0, 0.005], useful)["blocks"], 8)


if __name__ == "__main__":
    unittest.main()
