"""`hca setup` and the doctor's skill check. Every test runs in a temp HOME and temp HCA_HOME; the real ones are never read or written."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hca
from hca import cli
from hca.commands import doctor, setup

CHECKOUT_SKILL = Path(hca.__file__).resolve().parents[1] / "skills" / "hca"


class SetupCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-setup-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.home = self.tmp / "home"
        self.hca_home = self.tmp / "hca-home"
        self.cwd = self.tmp / "work"
        for directory in (self.home, self.cwd):
            directory.mkdir()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                                         "HCA_HOME": str(self.hca_home)}))
        stack.enter_context(mock.patch("pathlib.Path.home", return_value=self.home))
        stack.enter_context(contextlib.chdir(self.cwd))
        self.tty = False
        stack.enter_context(mock.patch.object(setup, "_is_tty", lambda: self.tty))
        self.claude = self.home / ".claude" / "skills" / "hca"
        self.agents = self.home / ".agents" / "skills" / "hca"

    def hca(self, *argv):
        """(exit code, stdout, stderr) of `hca setup ...` through the real CLI."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["setup", *argv])
        return code, out.getvalue(), err.getvalue()

    def source_names(self):
        return {"SKILL.md", *(f"references/{p.name}" for p in (CHECKOUT_SKILL / "references").glob("*.md"))}

    def installed_names(self, directory):
        return {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} - {setup.MARKER}

    def snapshot(self, directory):
        return {p.relative_to(directory).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in directory.rglob("*") if p.is_file()}

    def backups(self):
        root = self.hca_home / "backups"
        return sorted(root.glob("skills-*/*")) if root.exists() else []

    def make_foreign(self, directory, text="someone else's skill\n"):
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(text)


class InstallTests(SetupCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self.hca("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("install", out)
        self.assertIn(str(self.claude), out)
        self.assertIn(str(self.agents), out)
        self.assertIn("references/spec.md", out)
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse(self.hca_home.exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_install_both_targets_from_the_checkout(self):
        code, out, _ = self.hca("--yes")
        self.assertEqual(code, 0)
        want = self.source_names()
        self.assertIn("references/spec.md", want)
        for directory in (self.claude, self.agents):
            self.assertEqual(self.installed_names(directory), want)            # SKILL.md and references/*.md, nothing else
            marker = json.loads((directory / setup.MARKER).read_text())
            self.assertEqual((marker["name"], marker["version"], marker["source"]), ("hca", hca.__version__, "checkout"))
            self.assertRegex(marker["installed_utc"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertEqual(set(marker["files"]), want)
            for rel, digest in marker["files"].items():
                self.assertEqual(setup.digests({rel: (directory / rel).read_bytes()})[rel], digest)
                self.assertEqual((directory / rel).read_bytes(), (CHECKOUT_SKILL / rel).read_bytes())
        self.assertIn(f"installed: {self.claude}", out)
        self.assertIn("Next", out)
        self.assertIn("/reload-skills", out)
        self.assertIn("hca quick -- <your benchmark>", out)
        self.assertEqual([p.name for p in (self.home / ".claude" / "skills").iterdir()], ["hca"])       # no temp dirs left behind

    def test_single_agent_target(self):
        self.assertEqual(self.hca("--agent", "agents", "--yes")[0], 0)
        self.assertTrue(self.agents.is_dir())
        self.assertFalse((self.home / ".claude").exists())

    def test_rerun_is_up_to_date_and_writes_nothing(self):
        self.hca("--yes")
        before = {d: self.snapshot(d) for d in (self.claude, self.agents)}
        code, out, _ = self.hca()                                               # no --yes needed: there is nothing to confirm
        self.assertEqual(code, 0)
        self.assertEqual(out.count("already up to date"), 2)
        self.assertIn("Nothing to do", out)
        self.assertEqual({d: self.snapshot(d) for d in (self.claude, self.agents)}, before)
        self.assertEqual(self.backups(), [])

    def test_update_when_the_marker_version_is_old(self):
        self.hca("--yes")
        marker_path = self.claude / setup.MARKER
        marker = json.loads(marker_path.read_text())
        marker["version"] = "0.0.1"
        marker_path.write_text(json.dumps(marker))
        code, out, _ = self.hca("--yes")
        self.assertEqual(code, 0)
        self.assertIn(f"update from v0.0.1 to v{hca.__version__}", out)
        self.assertEqual(json.loads(marker_path.read_text())["version"], hca.__version__)
        self.assertEqual(self.backups(), [])                                    # untouched files need no backup

    def test_update_when_a_source_file_changed_overwrites_and_backs_up_local_edits(self):
        self.hca("--yes")
        (self.claude / "SKILL.md").write_text("edited by hand\n")
        (self.claude / "extra.txt").write_text("mine\n")
        code, out, _ = self.hca("--yes")
        self.assertEqual(code, 0)
        self.assertIn("update from", out)
        self.assertEqual((self.claude / "SKILL.md").read_bytes(), (CHECKOUT_SKILL / "SKILL.md").read_bytes())
        self.assertFalse((self.claude / "extra.txt").exists())
        saved = self.backups()
        self.assertEqual([p.name for p in saved], ["claude"])
        self.assertEqual((saved[0] / "extra.txt").read_text(), "mine\n")

    def test_marker_that_disagrees_with_the_source_is_updated(self):
        self.hca("--yes")
        marker_path = self.agents / setup.MARKER
        marker = json.loads(marker_path.read_text())
        marker["files"]["SKILL.md"] = "0" * 64                                  # as if the shipped skill changed since
        marker_path.write_text(json.dumps(marker))
        code, out, _ = self.hca("--yes")
        self.assertEqual(code, 0)
        self.assertEqual(out.count("already up to date"), 1)
        self.assertEqual(json.loads(marker_path.read_text())["files"]["SKILL.md"], setup.digests({"SKILL.md": (CHECKOUT_SKILL / "SKILL.md").read_bytes()})["SKILL.md"])

    def test_non_tty_without_yes_exits_3_and_writes_nothing(self):
        code, out, err = self.hca()
        self.assertEqual(code, 3)
        self.assertIn("--yes", err)
        self.assertIn("install", out)                                           # the plan was still shown
        self.assertFalse((self.home / ".claude").exists())

    def test_tty_prompt(self):
        args = cli.build_parser().parse_args(["setup", "--agent", "claude"])
        self.tty = True
        lines = []
        self.assertEqual(setup.run(args, out=lines.append, ask=lambda prompt: "n"), 1)
        self.assertFalse(self.claude.exists())
        self.assertEqual(setup.run(args, out=lines.append, ask=lambda prompt: "y"), 0)
        self.assertTrue((self.claude / "SKILL.md").is_file())

    def test_project_targets_use_the_repo_root(self):
        repo = self.tmp / "repo"
        (repo / "sub" / "dir").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
        with contextlib.chdir(repo / "sub" / "dir"):
            code, out, _ = self.hca("--project", "--yes")
        self.assertEqual(code, 0)
        self.assertTrue((repo / ".claude" / "skills" / "hca" / "SKILL.md").is_file())
        self.assertTrue((repo / ".agents" / "skills" / "hca" / setup.MARKER).is_file())
        self.assertEqual(list(self.home.iterdir()), [])                         # user-level folders untouched

    def test_project_targets_fall_back_to_cwd_outside_a_repo(self):
        self.assertEqual(self.hca("--project", "--agent", "claude", "--yes")[0], 0)
        self.assertTrue((self.cwd / ".claude" / "skills" / "hca" / "SKILL.md").is_file())


class SafetyTests(SetupCase):
    def test_foreign_dir_is_refused_without_force_and_nothing_else_is_written(self):
        self.make_foreign(self.claude)
        code, out, err = self.hca("--yes")
        self.assertEqual(code, 2)
        self.assertIn("not installed by hca setup", err)
        self.assertEqual((self.claude / "SKILL.md").read_text(), "someone else's skill\n")
        self.assertFalse((self.home / ".agents").exists())                      # the whole run was refused, not half of it
        self.assertEqual(self.backups(), [])

    def test_force_backs_up_outside_the_skills_dirs_then_replaces(self):
        self.make_foreign(self.claude)
        code, out, _ = self.hca("--yes", "--force")
        self.assertEqual(code, 0)
        self.assertIn("replace (backed up)", out)
        saved = self.backups()
        self.assertEqual([p.name for p in saved], ["claude"])
        self.assertEqual((saved[0] / "SKILL.md").read_text(), "someone else's skill\n")
        for skills in (self.home / ".claude", self.home / ".agents"):
            self.assertFalse(any(self.hca_home in p.parents for p in skills.rglob("*")))
            self.assertEqual([p.name for p in (skills / "skills").iterdir()], ["hca"])
        self.assertTrue((self.claude / setup.MARKER).is_file())

    def test_symlinked_target_is_refused_then_only_the_link_is_removed_with_force(self):
        elsewhere = self.tmp / "dev-checkout"
        elsewhere.mkdir()
        (elsewhere / "SKILL.md").write_text("my development copy\n")
        self.claude.parent.mkdir(parents=True)
        self.claude.symlink_to(elsewhere, target_is_directory=True)
        code, _, err = self.hca("--yes")
        self.assertEqual(code, 2)
        self.assertIn("symlink", err)
        self.assertTrue(self.claude.is_symlink())
        code, out, _ = self.hca("--yes", "--force")
        self.assertEqual(code, 0)
        self.assertFalse(self.claude.is_symlink())
        self.assertTrue((self.claude / setup.MARKER).is_file())
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), ["SKILL.md"])         # the target was never touched
        self.assertEqual((elsewhere / "SKILL.md").read_text(), "my development copy\n")

    def test_uninstall_removes_only_marker_dirs_and_backs_up(self):
        self.hca("--yes", "--agent", "claude")
        self.make_foreign(self.agents)
        code, out, _ = self.hca("--uninstall", "--yes")
        self.assertEqual(code, 0)
        self.assertFalse(self.claude.exists())
        self.assertIn("not installed by hca setup; left alone", out)
        self.assertEqual((self.agents / "SKILL.md").read_text(), "someone else's skill\n")
        saved = self.backups()
        self.assertEqual([p.name for p in saved], ["claude"])
        self.assertTrue((saved[0] / setup.MARKER).is_file())
        self.assertTrue((saved[0] / "references" / "spec.md").is_file())

    def test_uninstall_dry_run_and_missing_targets(self):
        self.assertEqual(self.hca("--uninstall")[0], 0)                         # nothing installed: nothing to confirm
        self.hca("--yes")
        code, out, _ = self.hca("--uninstall", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("remove", out)
        self.assertTrue(self.claude.is_dir() and self.agents.is_dir())
        self.assertEqual(self.hca("--uninstall")[0], 3)                         # would remove, but cannot ask

    def test_uninstall_leaves_a_symlink_alone_unless_forced(self):
        elsewhere = self.tmp / "keep-me"
        elsewhere.mkdir()
        (elsewhere / "SKILL.md").write_text("x")
        self.claude.parent.mkdir(parents=True)
        self.claude.symlink_to(elsewhere, target_is_directory=True)
        code, out, _ = self.hca("--uninstall", "--agent", "claude", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("is a symlink; left alone", out)
        self.assertTrue(self.claude.is_symlink())
        self.assertEqual(self.hca("--uninstall", "--agent", "claude", "--yes", "--force")[0], 0)
        self.assertFalse(self.claude.is_symlink())
        self.assertEqual((elsewhere / "SKILL.md").read_text(), "x")


class SourceTests(SetupCase):
    def test_wheel_resource_is_preferred(self):
        package = self.tmp / "pkgs" / "fake_hca_skill"
        (package / "references").mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "SKILL.md").write_text("---\nname: hca\n---\nfrom the wheel\n")
        (package / "references" / "one.md").write_text("one\n")
        (package / "references" / "notes.txt").write_text("not shipped\n")
        (package / "stray.md").write_text("not shipped either\n")
        sys.path.insert(0, str(package.parent))
        self.addCleanup(sys.path.remove, str(package.parent))
        self.addCleanup(sys.modules.pop, "fake_hca_skill", None)
        files, source = setup.read_source(package="fake_hca_skill")
        self.assertEqual(source, "wheel")
        self.assertEqual(sorted(files), ["SKILL.md", "references/one.md"])
        with mock.patch.object(setup, "SKILL_PACKAGE", "fake_hca_skill"), \
                mock.patch.object(setup, "read_source", lambda: setup.read_source.__wrapped__()):
            pass                                                                 # (run() uses the default package, covered below)
        with mock.patch("hca.commands.setup._wheel_root", lambda package: setup.importlib.resources.files("fake_hca_skill")):
            code, _, _ = self.hca("--agent", "claude", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual((self.claude / "SKILL.md").read_text(), "---\nname: hca\n---\nfrom the wheel\n")
        self.assertEqual(json.loads((self.claude / setup.MARKER).read_text())["source"], "wheel")
        self.assertEqual(self.installed_names(self.claude), {"SKILL.md", "references/one.md"})

    def test_checkout_fallback_when_there_is_no_package(self):
        files, source = setup.read_source(package="no_such_hca_skill_package")
        self.assertEqual(source, "checkout")
        self.assertEqual(set(files), self.source_names())

    def test_missing_source_is_a_clear_error(self):
        with self.assertRaises(hca.core.HcaError) as ctx:
            setup.read_source(package="no_such_hca_skill_package", checkout=self.tmp / "nothing")
        self.assertIn("cannot find the hca skill files", str(ctx.exception))
        with mock.patch.object(setup, "read_source", side_effect=hca.core.HcaError("cannot find the hca skill files")):
            code, _, err = self.hca("--yes")
        self.assertEqual(code, 3)
        self.assertIn("cannot find", err)


class DoctorSkillTests(SetupCase):
    def states(self, **kwargs):
        return [(lvl, msg) for lvl, name, msg in doctor.checks(self.cwd, {}, loadavg=(0.1, 0.1, 0.1), home=self.home, **kwargs) if name == "skill"]

    def test_not_installed(self):
        rows = self.states()
        self.assertEqual([lvl for lvl, _ in rows], ["warn", "warn"])
        self.assertTrue(all("not installed: run hca setup" in msg for _, msg in rows))

    def test_ok_outdated_and_no_marker(self):
        self.hca("--yes")
        self.assertEqual([lvl for lvl, _ in self.states()], ["ok", "ok"])
        self.assertIn(f"ok v{hca.__version__}", self.states()[0][1])
        marker_path = self.claude / setup.MARKER
        marker = json.loads(marker_path.read_text())
        marker["version"] = "0.0.1"
        marker_path.write_text(json.dumps(marker))
        (self.agents / setup.MARKER).unlink()
        claude, agents = self.states()
        self.assertEqual(claude[0], "warn")
        self.assertIn(f"outdated (v0.0.1 installed, v{hca.__version__} available): run hca setup", claude[1])
        self.assertEqual(agents[0], "warn")
        self.assertIn("present but not installed by hca (no marker)", agents[1])

    def test_edited_file_counts_as_outdated(self):
        self.hca("--yes")
        (self.claude / "SKILL.md").write_text("changed")
        self.assertIn("outdated", self.states()[0][1])

    def test_project_level_is_reported_only_when_present(self):
        self.assertEqual(len(self.states()), 2)
        self.hca("--project", "--agent", "claude", "--yes")
        rows = self.states()
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[2][1].startswith(str(self.cwd / ".claude")))
        self.assertEqual(rows[2][0], "ok")

    def test_skill_warnings_never_fail_doctor(self):
        lines = []
        self.assertEqual(doctor.run(out=lines.append, cwd=self.cwd, env={}), 0)
        self.assertTrue(any("skill:" in line and "not installed" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
