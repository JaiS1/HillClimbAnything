import json
import os
import subprocess
import sys
import time
import unittest

import repokit
from hca import ledger
from hca.commands import try_
from hca.core import HcaError
from hca.spec import Guard
from repokit import env

PY = sys.executable


def guard_toml(name, command=None, metric=None, regression="2%", extra=""):
    body = f"command = {json.dumps(command)}\n" if command else f'metric = "{metric}"\nmax_regression = "{regression}"\n{extra}'
    return f'[[guards]]\nname = "{name}"\n{body}\n'


class InitTests(repokit.RepoTestCase):
    def test_init_refuses_a_dirty_tree_and_changes_nothing(self):
        self.repo.write("README.md", "modified\n")                       # a tracked modification
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("not clean", str(caught.exception))
        self.assertIn("README.md", str(caught.exception))
        self.assertEqual(self.repo.git("branch", "--show-current"), "main")
        self.assertFalse((self.root / ".hca" / "state.json").exists())
        self.repo.git("checkout", "--", "README.md")
        self.repo.write("src/new.txt", "x\n")                            # untracked inside editable: would be absorbed into a keep
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("src/new.txt", str(caught.exception))
        (self.root / "src" / "new.txt").unlink()
        (self.root / "hca.toml").rename(self.tmp / "hca.toml.bak")        # an uncommitted spec is not a baseline either
        self.repo.git("rm", "-q", "--cached", "hca.toml")
        (self.tmp / "hca.toml.bak").rename(self.root / "hca.toml")
        with self.assertRaises(HcaError):
            self.init()

    def test_init_warns_about_untracked_files_outside_editable_but_continues(self):
        self.repo.write("scratch.log", "x\n")
        code, text = self.init()
        self.assertEqual(code, 0, text)
        self.assertIn("warning: 1 untracked file(s) outside editable were ignored: scratch.log; gitignore them to silence this.", text)

    def test_init_creates_the_branch_exclude_state_and_frozen_hashes(self):
        code, text = self.init()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.repo.git("branch", "--show-current"), "hca/speedy")
        self.assertIn(".hca/", (self.root / ".git" / "info" / "exclude").read_text().splitlines())
        self.assertFalse((self.root / ".gitignore").exists())
        self.assertEqual(self.status_lines(), [])                       # .hca/ is invisible to git
        state = self.state()
        self.assertEqual((state["init_sha"], state["last_keep"]), (self.repo.head(), self.repo.head()))
        self.assertIsNone(state["last_confirmed"])
        self.assertEqual(set(state["frozen"]), {"hca.toml", "bench/run.sh", "bench/items.py"})
        self.assertEqual(state["spec_hash"], state["frozen"]["hca.toml"])
        self.assertTrue((self.root / ".hca" / "noise.json").is_file())
        self.assertTrue((self.root / ".hca" / "VERDICTS.md").is_file())
        self.assertTrue((self.root / ".hca" / "arms" / state["init_sha"][:12]).is_dir())
        self.assertIn("Baseline A/A (8 blocks", text)
        self.assertIn("Useful effect 5%: OK", text)
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("already initialised", str(caught.exception))

    def test_a_too_noisy_baseline_is_refused_unless_forced(self):
        with env(BENCH_JITTER="0.6"):
            code, text = self.init()
            self.assertEqual(code, 2)
            self.assertIn("too noisy", text)
            self.assertIn("--force", text)
            self.assertFalse((self.root / ".hca" / "state.json").exists())
            self.assertEqual(self.repo.git("branch", "--show-current"), "main")
            code, _ = self.init(force=True)
            self.assertEqual(code, 0)
            self.assertTrue((self.root / ".hca" / "state.json").exists())

    def test_a_broken_objective_is_reported_not_initialised(self):
        os.remove(self.root / "bench" / "run.sh")
        self.repo.commit_all("remove the bench")
        code, text = self.init()
        self.assertEqual(code, 2)
        self.assertIn("could not be measured", text)
        self.assertFalse((self.root / ".hca" / "state.json").exists())

    def test_reset_archives_the_old_ledger_and_starts_from_the_branch_tip(self):
        self.init()
        self.set_speed(21)
        self.assertEqual(self.try_()[0], 0)
        keep = self.repo.head()
        old_sha12 = self.state()["init_sha"][:12]
        code, text = self.init(reset=True)
        self.assertEqual(code, 0, text)
        state = self.state()
        self.assertEqual((state["init_sha"], state["last_keep"]), (keep, keep))
        self.assertTrue(list((self.root / ".hca").glob("archive-*/attempts/0001")))
        self.assertFalse((self.root / ".hca" / "arms" / old_sha12).exists())
        self.assertEqual(ledger.read_attempts(self.root), [])

    def test_init_adopts_its_own_branch_when_the_harness_was_committed_there(self):
        self.repo.git("checkout", "-q", "-b", "hca/speedy")
        self.repo.write("src/boost.txt", "1\n")
        harness = self.repo.commit_all("harness work on the task branch")
        code, text = self.init()
        self.assertEqual(code, 0, text)
        self.assertEqual(self.repo.git("branch", "--show-current"), "hca/speedy")
        self.assertEqual(self.repo.head(), harness)                       # nothing was moved or recreated
        self.assertEqual(self.state()["init_sha"], harness)
        self.assertEqual(text.count("Adopting the existing branch hca/speedy"), 1)
        self.assertIn("Initialised task 'speedy' on branch hca/speedy", text)
        self.assertIsNone(self.state()["base_branch"])

    def test_init_refuses_an_existing_branch_that_is_not_checked_out(self):
        self.repo.git("branch", "hca/speedy")
        with self.assertRaises(HcaError) as caught:
            self.init()
        message = str(caught.exception)
        self.assertIn("not checked out", message)
        self.assertIn("git checkout hca/speedy", message)
        self.assertIn("git branch -D hca/speedy", message)
        self.assertFalse((self.root / ".hca" / "state.json").exists())
        self.assertEqual(self.repo.git("branch", "--show-current"), "main")

    def test_init_on_its_own_branch_still_refuses_when_state_exists_unless_reset(self):
        self.assertEqual(self.init()[0], 0)
        self.assertEqual(self.repo.git("branch", "--show-current"), "hca/speedy")
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("already initialised", str(caught.exception))
        code, text = self.init(reset=True)                                # unchanged semantics
        self.assertEqual(code, 0, text)
        self.assertNotIn("Adopting", text)

    def test_the_confirm_cap_guard_still_applies_to_an_adoptable_branch(self):
        self.assertEqual(self.init()[0], 0)
        self.set_speed(21)
        self.assertEqual(self.try_("win")[0], 0)
        self.assertEqual(self.confirm()[0], 0)
        import shutil
        shutil.rmtree(self.root / ".hca")                                 # no state, branch checked out: adoptable except for the store
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("referee store", str(caught.exception))
        self.assertEqual(self.lines.count("Adopting"), 0)
        self.assertFalse((self.root / ".hca" / "state.json").exists())

    def test_heldout_must_live_outside_the_repo_and_is_copied_to_the_store(self):
        inside = self.root / "src" / "held"
        inside.mkdir()
        with self.assertRaises(HcaError) as caught:
            self.init(heldout=str(inside))
        self.assertIn("inside the repo", str(caught.exception))
        outside = self.tmp / "held"
        outside.mkdir()
        (outside / "scale.txt").write_text("1.0\n")
        code, text = self.init(heldout=str(outside))
        self.assertEqual(code, 0, text)
        self.assertEqual((ledger.heldout_dir("speedy") / "scale.txt").read_text(), "1.0\n")
        self.assertTrue(str(ledger.heldout_dir("speedy")).startswith(str(self.tmp / "home")))


class TryTests(repokit.RepoTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.init()[0], 0)

    def adir(self, n=1):
        return ledger.attempt_path(self.root, n)

    def bench_count(self):
        return int((self.tmp / "state" / "count").read_text())

    def test_a_real_speedup_is_kept_and_committed_on_the_branch(self):
        before = self.repo.head()
        self.set_speed(21)                                              # a 30% speedup
        code, text = self.try_("cache the loop bound")
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"attempt 0001  KEEP  t_ms -\d+\.\d% \(better; cut of at least \d+\.\d%, p = 0\.\d+, 10 blocks, \d+\.\d s\)")
        after = self.repo.head()
        self.assertNotEqual(before, after)
        self.assertEqual(self.repo.git("log", "-1", "--format=%s"), "hca keep 0001: cache the loop bound")
        self.assertEqual(self.repo.git("log", "-1", "--format=%P"), before)
        self.assertEqual(self.state()["last_keep"], after)
        self.assertEqual(self.status_lines(), [])
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"], a["confirm"], a["commit"], a["blocks"]), ("keep", True, "pending", after, 10))
        self.assertAlmostEqual(a["estimate"], -30, delta=3)          # 30 -> 21 is a 30% cut: the metric's own change
        self.assertLess(a["lower"], -25)                                # the least-improvement end of the interval
        for name in ("message.txt", "patch.diff", "scope.json", "guards.json", "runs.jsonl", "verdict.json"):
            self.assertTrue((self.adir() / name).is_file(), name)
        self.assertIn("+21", (self.adir() / "patch.diff").read_text())
        self.assertEqual(len(ledger.read_attempts(self.root)), 1)
        self.assertIn("0001 keep", (self.root / ".hca" / "VERDICTS.md").read_text())

    def test_a_noop_change_is_reverted_with_a_reason(self):
        before = self.repo.head()
        self.set_speed("30.0000001")
        code, text = self.try_("reformat")
        self.assertEqual(code, 1, text)
        self.assertIn("REVERT", text)
        self.assertIn("p = ", text)
        self.assertIn("below the keep floor", text)
        self.assertEqual(self.repo.head(), before)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertEqual(self.status_lines(), [])
        self.assertEqual(self.state()["last_keep"], before)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", True))
        self.assertNotIn("commit", a)

    def test_an_edit_outside_the_editable_paths_is_invalid_and_not_measured(self):
        before_count = self.bench_count()
        self.repo.write("README.md", "agent was here\n")
        code, text = self.try_("docs")
        self.assertEqual(code, 2, text)
        self.assertIn("INVALID", text)
        self.assertIn("README.md", text)
        self.assertEqual(self.bench_count(), before_count)              # no measurement happened
        self.assertFalse((self.adir() / "runs.jsonl").exists())
        self.assertEqual(self.repo.read("README.md"), "agent was here\n")   # left in place for the agent to fix
        self.assertEqual(self.repo.head(), self.state()["last_keep"])
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("invalid", False))
        self.assertFalse(json.loads((self.adir() / "scope.json").read_text())["ok"])
        # once the agent fixes the scope the next try measures normally
        self.repo.git("checkout", "--", "README.md")
        self.set_speed(21)
        self.assertEqual(self.try_("real change")[0], 0)
        self.assertEqual(self.attempt(2)["verdict"], "keep")

    def test_editing_a_frozen_file_or_the_spec_is_invalid(self):
        before_count = self.bench_count()
        self.repo.write("bench/run.sh", "print('METRIC t_ms=1')\n")
        code, text = self.try_("cheat")
        self.assertEqual(code, 2)
        self.assertIn("bench/run.sh: frozen file changed", text)
        self.assertEqual(self.repo.read("bench/run.sh"), "print('METRIC t_ms=1')\n")
        self.repo.git("checkout", "--", "bench/run.sh")
        self.repo.write("hca.toml", self.repo.read("hca.toml") + "# loosened\n")
        code, text = self.try_("cheat again")
        self.assertEqual(code, 2)
        self.assertIn("hca.toml: frozen file changed", text)
        self.assertEqual(self.bench_count(), before_count)

    def test_new_untracked_files_outside_editable_and_moved_head_are_invalid(self):
        self.repo.write("bench/extra.py", "x\n")
        code, text = self.try_()
        self.assertEqual(code, 2)
        self.assertIn("new file in a frozen path", text)
        (self.root / "bench" / "extra.py").unlink()
        self.set_speed(21)
        self.repo.commit_all("agent committed by itself")
        code, text = self.try_()
        self.assertEqual(code, 2)
        self.assertIn("(HEAD)", text)

    def test_nothing_to_measure_is_invalid(self):
        code, text = self.try_()
        self.assertEqual(code, 2)
        self.assertIn("no changes", text)
        self.assertEqual(self.attempt(1)["verdict"], "invalid")

    def test_a_message_is_required_and_the_branch_is_checked(self):
        with self.assertRaises(HcaError) as caught:
            self.try_(message="  ")
        self.assertIn("needs a message", str(caught.exception))
        self.repo.git("checkout", "-q", "main")
        with self.assertRaises(HcaError) as caught:
            self.try_()
        self.assertIn("hca/speedy", str(caught.exception))

    def test_a_crashing_candidate_is_reverted_and_scored_as_worst(self):
        self.set_speed(21)
        self.repo.write("src/crash.txt", "1\n")
        code, text = self.try_("faster but broken")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", False))
        self.assertTrue(any("candidate failed" in r for r in a["reasons"]), a["reasons"])
        self.assertFalse((self.root / "src" / "crash.txt").exists())    # untracked editable file removed by the revert
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        stderr = sorted((self.adir() / "runs").glob("*candidate.stderr"))
        self.assertTrue(stderr and "candidate crashed on purpose" in stderr[0].read_text())
        records = [json.loads(line) for line in (self.adir() / "runs.jsonl").read_text().splitlines()]
        self.assertTrue(any(r.get("kind") == "run" and r.get("ok") is False and "crashed on purpose" in r.get("error_tail", "") for r in records))

    def arm_dirs(self):
        return {p.name for p in (self.root / ".hca" / "arms").iterdir() if p.is_dir()}

    def test_untracked_files_outside_editable_warn_but_do_not_invalidate_and_are_never_committed(self):
        self.repo.write("scratch.log", "agent scratch\n")
        self.repo.write("notes/a.txt", "x\n")
        self.set_speed(21)
        code, text = self.try_("faster")
        self.assertEqual(code, 0, text)
        self.assertIn("warning: 2 untracked file(s) outside editable were ignored: notes/a.txt, scratch.log; gitignore them", text)
        scope = json.loads((self.adir() / "scope.json").read_text())
        self.assertEqual(scope["untracked_outside_editable"], ["notes/a.txt", "scratch.log"])
        self.assertEqual(self.repo.git("show", "--name-only", "--format=", "HEAD"), "src/speed.txt")
        self.assertEqual(self.repo.read("scratch.log"), "agent scratch\n")
        self.set_speed("21.0000001")
        self.assertEqual(self.try_("noop")[0], 1)                       # a revert leaves them alone too
        self.assertEqual(self.repo.read("scratch.log"), "agent scratch\n")
        self.assertTrue((self.root / "notes" / "a.txt").exists())

    def test_baseline_worktrees_that_are_no_longer_needed_are_pruned(self):
        init12 = self.state()["init_sha"][:12]
        self.set_speed(21)
        self.assertEqual(self.try_("one")[0], 0)
        one12 = self.repo.head()[:12]
        self.assertEqual(self.arm_dirs(), {init12, one12})               # the keep's own arm (the candidate) is the next baseline
        self.set_speed(14)
        self.assertEqual(self.try_("two")[0], 0)
        two12 = self.repo.head()[:12]
        self.assertEqual(self.arm_dirs(), {init12, two12})               # keep one was the baseline for this try, now pruned
        self.assertEqual(self.confirm()[0], 0)
        self.assertEqual(self.arm_dirs(), {init12, two12})               # confirm: init and the last keep, which is also last_confirmed
        self.set_speed(10)
        self.assertEqual(self.try_("three")[0], 0)
        three12 = self.repo.head()[:12]
        self.assertEqual(self.arm_dirs(), {init12, two12, three12})
        self.set_speed("10.0000001")
        self.assertEqual(self.try_("noop")[0], 1)
        self.assertEqual(self.arm_dirs(), {init12, two12, three12})      # the reverted candidate's worktree is pruned after the record
        listed = self.repo.git("worktree", "list")
        self.assertNotIn(one12, listed)
        self.assertEqual(len(listed.splitlines()), 4)                    # the checkout plus three arms

    def test_a_keep_does_not_run_the_users_commit_hooks(self):
        # CONTRACT NOTE (A2): the keep is `commit-tree` + `update-ref`, so pre-commit and commit-msg hooks do not run on
        # hca's commits. The measured snapshot is committed byte for byte; a hook that rewrites or rejects it would break that.
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho ran > \"$HOOKOUT\"\nexit 1\n")
        hook.chmod(0o755)
        out = self.tmp / "hook.out"
        self.set_speed(21)
        with env(HOOKOUT=str(out)):
            code, text = self.try_("faster")
        self.assertEqual(code, 0, text)
        self.assertFalse(out.exists())
        self.assertEqual(self.state()["last_keep"], self.repo.head())

    def test_a_second_keep_is_measured_against_the_first(self):
        self.set_speed(21)
        self.assertEqual(self.try_("first")[0], 0)
        first = self.repo.head()
        self.set_speed(14)
        code, text = self.try_("second")
        self.assertEqual(code, 0, text)
        self.assertEqual(self.attempt(2)["base"], first)
        self.assertEqual(self.repo.git("log", "-1", "--format=%P"), first)
        self.assertEqual(self.arm_dirs(), {self.state()["init_sha"][:12], self.repo.head()[:12]})   # the first keep's arm was pruned after the second keep
        self.assertAlmostEqual(self.attempt(2)["estimate"], -33.3, delta=4)  # 21 -> 14 is a 33% cut, measured against the first keep (not -53% from the original)


class LockTests(repokit.RepoTestCase):
    def lock_path(self):
        return self.root / ".hca" / "lock"

    def test_the_lock_records_pid_start_and_command_and_is_released(self):
        self.init()
        self.assertFalse(self.lock_path().exists())                      # released after init
        with ledger.lock(self.root, "try"):
            held = json.loads(self.lock_path().read_text())
            self.assertEqual((held["pid"], held["command"]), (os.getpid(), "try"))
            self.assertIn("started", held)
            with self.assertRaises(HcaError) as caught:
                with ledger.lock(self.root, "confirm"):
                    pass
            self.assertIn("another `hca try` is running", str(caught.exception))
            self.assertIn(str(os.getpid()), str(caught.exception))
        self.assertFalse(self.lock_path().exists())

    def test_a_live_lock_refuses_init_try_and_confirm_and_is_left_alone(self):
        self.init()
        self.set_speed(21)
        self.lock_path().write_text(json.dumps({"pid": os.getpid(), "started": "now", "command": "try"}))
        for call in (lambda: self.try_("faster"), self.confirm, lambda: self.init(reset=True)):
            with self.assertRaises(HcaError) as caught:
                call()
            self.assertIn("is running in this repo", str(caught.exception))
        self.assertTrue(self.lock_path().exists())
        self.assertEqual(ledger.read_attempts(self.root), [])             # nothing ran
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")

    def test_a_stale_lock_is_taken_over_with_a_warning(self):
        self.init()
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.lock_path().write_text(json.dumps({"pid": dead.pid, "started": "long ago", "command": "try"}))
        self.set_speed(21)
        code, text = self.try_("faster")
        self.assertEqual(code, 0, text)
        self.assertIn("warning: removing a stale lock left by `hca try`", text)
        self.assertFalse(self.lock_path().exists())

    def test_the_lock_is_released_when_the_command_fails(self):
        self.init()
        with self.assertRaises(HcaError):
            self.try_(message=" ")
        self.assertFalse(self.lock_path().exists())

    def test_a_just_created_unreadable_lock_is_not_stolen_but_an_old_one_is(self):
        self.init()
        self.lock_path().write_text("")
        with self.assertRaises(HcaError) as caught:
            with ledger.lock(self.root, "try"):
                pass
        self.assertIn("cannot be read", str(caught.exception))
        old = time.time() - 3600
        os.utime(self.lock_path(), (old, old))
        with ledger.lock(self.root, "try", out=self.out):
            pass
        self.assertFalse(self.lock_path().exists())


class BuildTests(repokit.RepoTestCase):
    def with_build(self, command):
        self.repo.write("hca.toml", repokit.toml(build="[build]\ncommand = " + json.dumps([PY, "-c", command]) + "\n"))
        self.repo.commit_all("add a build")
        self.assertEqual(self.init()[0], 0)

    def test_a_candidate_build_failure_is_reverted(self):
        # the build rejects speeds below 25, so the original (30) builds and the "too clever" candidate does not
        self.with_build("import sys; sys.exit(0 if float(open('src/speed.txt').read()) >= 25 else 5)")
        self.set_speed(21)
        code, text = self.try_("too clever")
        self.assertEqual(code, 1, text)
        self.assertIn("candidate build failed", text)
        self.assertFalse(self.attempt(1)["valid"])
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertTrue((ledger.attempt_path(self.root, 1) / "build.stderr").is_file())

    def test_builds_run_once_per_baseline_sha_and_once_per_try(self):
        log = self.tmp / "state" / "builds.log"
        self.with_build("import os; d = os.environ['BENCH_STATE']; os.makedirs(d, exist_ok=True); "
                        "open(os.path.join(d, 'builds.log'), 'a').write(os.path.basename(os.getcwd()) + chr(10))")
        arm_builds = lambda: log.read_text().split()
        init12 = self.state()["init_sha"][:12]
        self.assertEqual(len(arm_builds()), 2)                          # init: the baseline arm and the A/A candidate arm, one build each
        self.assertIn(init12, arm_builds())
        self.set_speed(21)
        self.assertEqual(self.try_("one")[0], 0)
        one12 = self.repo.head()[:12]
        self.assertEqual(arm_builds()[2:], [one12])                     # the candidate is built once, in its own worktree
        self.set_speed(14)
        self.assertEqual(self.try_("two")[0], 0)                        # keep one's arm is the baseline now: already built, not rebuilt
        two12 = self.repo.head()[:12]
        self.assertEqual(arm_builds()[3:], [two12])
        markers = sorted(p.name for p in (self.root / ".hca" / "arms").glob("*.built"))
        self.assertEqual(markers, sorted([init12 + ".built", two12 + ".built"]))   # the markers of pruned arms went with them


class GuardTests(repokit.RepoTestCase):
    def setup_repo(self, guards, size=None, **kwargs):
        self.repo.write("hca.toml", repokit.toml(guards=guards, **kwargs))
        if size is not None:
            self.repo.write("src/size.txt", f"{size}\n")
        self.repo.commit_all("guards")
        self.assertEqual(self.init()[0], 0)

    def test_a_failing_command_guard_reverts_before_any_measurement(self):
        self.setup_repo(guard_toml("tests", [PY, "-c", "import sys; sys.stderr.write('3 tests failed'); sys.exit(1)"]))
        count_before = (self.tmp / "state" / "count").read_text()
        self.set_speed(21)
        code, text = self.try_("faster")
        self.assertEqual(code, 1, text)
        self.assertIn("guard 'tests' failed", text)
        self.assertIn("3 tests failed", text)
        self.assertEqual((self.tmp / "state" / "count").read_text(), count_before)
        guards = json.loads((self.adir(1) / "guards.json").read_text())
        self.assertEqual(len(guards[0]["attempts"]), 2)                 # one retry
        self.assertEqual((self.attempt(1)["verdict"], self.attempt(1)["valid"]), ("revert", False))
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")

    def adir(self, n):
        return ledger.attempt_path(self.root, n)

    def test_a_flaky_command_guard_passes_on_its_retry(self):
        flaky = "import os, sys; p = os.path.join(os.environ['BENCH_STATE'], 'flaky'); first = not os.path.exists(p); open(p, 'w').write('x'); sys.exit(1 if first else 0)"
        self.setup_repo(guard_toml("tests", [PY, "-c", flaky]))
        self.set_speed(21)
        code, text = self.try_("faster")
        self.assertEqual(code, 0, text)
        guards = json.loads((self.adir(1) / "guards.json").read_text())
        self.assertEqual([a["returncode"] for a in guards[0]["attempts"]], [1, 0])

    def test_a_metric_guard_blocks_a_faster_but_bigger_candidate(self):
        self.setup_repo(guard_toml("size", metric="size_kb", regression="2%") + "[mechanism]\nmetrics = [\"size_kb\"]\n", size=100)
        self.set_speed(21)
        self.repo.write("src/size.txt", "110\n")
        code, text = self.try_("faster, bigger")
        self.assertEqual(code, 1, text)
        self.assertIn("guard 'size': size_kb went 100 -> 110", text)
        a = self.attempt(1)
        self.assertFalse(a["valid"])
        self.assertEqual(a["arm_medians"]["candidate"]["size_kb"], 110)
        self.assertEqual(self.repo.read("src/size.txt"), "100\n")
        self.set_speed(21)
        self.repo.write("src/size.txt", "101\n")                        # +1% is inside the allowance
        code, text = self.try_("faster, slightly bigger")
        self.assertEqual(code, 0, text)

    def test_a_metric_guard_whose_metric_is_missing_fails_closed(self):
        self.setup_repo(guard_toml("size", metric="size_kb"))           # the objective never prints size_kb
        self.set_speed(21)
        code, text = self.try_("faster")
        self.assertEqual(code, 1)
        self.assertIn("was not reported by both arms", text)

    def test_metric_guard_directions(self):
        down = Guard("acc", None, "acc", 0.02, "2%", "max")
        up = Guard("size", None, "size", 0.02, "2%", "min")
        medians = lambda b, c, name: {"baseline": {name: b}, "candidate": {name: c}}
        self.assertTrue(try_._metric_guard(up, medians(100, 102, "size"))["passed"])
        self.assertFalse(try_._metric_guard(up, medians(100, 102.5, "size"))["passed"])
        self.assertTrue(try_._metric_guard(up, medians(100, 50, "size"))["passed"])          # smaller is fine for "min"
        self.assertTrue(try_._metric_guard(down, medians(0.80, 0.79, "acc"))["passed"])
        self.assertFalse(try_._metric_guard(down, medians(0.80, 0.70, "acc"))["passed"])
        self.assertTrue(try_._metric_guard(down, medians(0.80, 0.95, "acc"))["passed"])      # higher is fine for "max"


class ExtensionAndDeadlineTests(repokit.RepoTestCase):
    toml_args = {"limits": {"extend_to": 20}}

    def test_a_moderate_effect_extends_to_twenty_blocks_and_can_be_kept(self):
        with env(BENCH_JITTER="0.10"):
            self.assertEqual(self.init(force=True)[0], 0)
            self.set_speed(28.5)
            code, text = self.try_("small win", seed=1)
        a = self.attempt(1)
        self.assertTrue(a["extended"], a)
        self.assertEqual((a["blocks"], a["look"], a["looks"]), (20, 2, 2))
        self.assertEqual((code, a["verdict"]), (0, "keep"), text)
        self.assertIn("20 blocks after extending", text)
        runs = [json.loads(line) for line in (ledger.attempt_path(self.root, 1) / "runs.jsonl").read_text().splitlines()]
        self.assertEqual(sorted({r["block"] for r in runs if r["kind"] == "run"}), list(range(20)))   # numbering continued
        self.assertLessEqual(a["p"], 0.005)                              # decided at the second look's level (alpha / 2)

    def test_a_moderate_effect_can_still_be_reverted_after_extending(self):
        with env(BENCH_JITTER="0.10"):
            self.assertEqual(self.init(force=True)[0], 0)
            self.set_speed(28.8)
            code, text = self.try_("tiny win", seed=4)
        a = self.attempt(1)
        self.assertTrue(a["extended"])
        self.assertEqual((code, a["verdict"], a["blocks"]), (1, "revert", 20), text)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")

    def test_no_evidence_is_not_no_effect_when_the_interval_allows_the_useful_effect(self):
        with env(BENCH_JITTER="0.08"):
            self.assertEqual(self.init(force=True)[0], 0)
            self.set_speed(29.4)
            code, text = self.try_("maybe", seed=5)
        a = self.attempt(1)
        self.assertEqual((code, a["verdict"], a["extended"]), (1, "revert", False), text)
        self.assertTrue(a["includes_useful"])
        self.assertIn("No evidence, not no effect", text)
        self.assertIn("5%", text)

    def test_a_clear_failure_does_not_claim_the_useful_effect_is_possible(self):
        with env(BENCH_JITTER="0.02"):
            self.assertEqual(self.init()[0], 0)
            self.set_speed(33)                                           # 10% slower
            code, text = self.try_("slower", seed=1)
        a = self.attempt(1)
        self.assertEqual((code, a["verdict"], a["extended"]), (1, "revert", False))
        self.assertFalse(a["includes_useful"])
        self.assertNotIn("No evidence", text)

    def test_try_seconds_stops_the_try_and_reverts(self):
        self.repo.write("hca.toml", repokit.toml(limits={"try_seconds": 1}))
        self.repo.commit_all("tight limit")
        self.assertEqual(self.init()[0], 0)
        self.set_speed(21)
        with env(BENCH_SLEEP_MS="250"):
            code, text = self.try_("slow machine")
        a = self.attempt(1)
        self.assertEqual((code, a["verdict"]), (2, "void"), text)
        self.assertTrue(any("try_seconds" in r for r in a["reasons"]), a["reasons"])
        self.assertLess(a["wall_seconds"], 10)                           # it stopped; it did not run all 20 runs (>= 5 s)
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")        # A2: void keeps the edits (nothing was decided)
        self.assertEqual(self.repo.head(), self.state()["last_keep"])


class ItemsModeTests(repokit.RepoTestCase):
    mode = "items"

    def setUp(self):
        super().setUp()
        code, text = self.init()
        self.assertEqual(code, 0, text)

    def test_init_in_items_mode_checks_determinism_and_item_count(self):
        self.assertIn("12 items", self.text)
        self.assertEqual(self.state()["mode"], "items")

    def test_a_clear_improvement_is_kept(self):
        self.repo.write("src/boost.txt", "0.1\n")
        code, text = self.try_("better prompt")
        self.assertEqual(code, 0, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"], a["mode"], a["blocks"]), ("keep", True, "items", 1))
        self.assertAlmostEqual(a["estimate"], 0.0875, places=4)         # mean of boost * (0.5 + (i % 4) * 0.25)
        self.assertGreater(a["lower"], 0)
        self.assertIsNone(a["p"])
        self.assertIn("KEEP  +0.0875", text)
        self.assertEqual(self.state()["last_keep"], self.repo.head())

    def test_a_tiny_improvement_below_the_keep_floor_is_reverted(self):
        self.repo.write("src/boost.txt", "0.01\n")
        code, text = self.try_("barely")
        self.assertEqual(code, 1, text)
        self.assertIn("below the keep floor", text)
        self.assertEqual(self.repo.read("src/boost.txt"), "0\n")
        self.assertTrue(self.attempt(1)["valid"])

    def test_a_change_with_no_effect_is_reverted(self):
        self.repo.write("src/boost.txt", "0.0\n")
        code, text = self.try_("cosmetic")
        self.assertEqual(code, 1, text)
        self.assertEqual(self.attempt(1)["estimate"], 0)

    def test_a_worse_candidate_is_reverted(self):
        self.repo.write("src/boost.txt", "-0.1\n")
        code, _ = self.try_("worse")
        self.assertEqual(code, 1)
        self.assertLess(self.attempt(1)["estimate"], 0)

    def test_candidate_invalid_items_score_worst_and_block_a_keep(self):
        self.repo.write("src/boost.txt", "0.2\n")
        self.repo.write("src/break.txt", "1\n")
        code, text = self.try_("gains elsewhere, breaks three items")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertFalse(a["valid"])
        self.assertTrue(any("invalid in the candidate" in r for r in a["reasons"]), a["reasons"])
        self.assertFalse((self.root / "src" / "break.txt").exists())

    def test_a_crashing_candidate_in_items_mode_is_reverted(self):
        self.repo.write("src/boost.txt", "0.2\n")
        self.repo.write("src/crash.txt", "1\n")
        code, text = self.try_("gains, then crashes")
        self.assertEqual(code, 1, text)
        a = self.attempt(1)
        self.assertEqual((a["verdict"], a["valid"]), ("revert", False))
        self.assertTrue(any("candidate run failed" in r for r in a["reasons"]), a["reasons"])
        self.assertEqual(self.repo.read("src/boost.txt"), "0\n")

    def test_percent_useful_effect_is_rejected_in_items_mode(self):
        from hca import spec
        import tomllib
        with self.assertRaises(HcaError) as caught:
            spec.parse_spec(tomllib.loads(repokit.toml(mode="items", useful='"5%"')))
        self.assertIn("bare number", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
