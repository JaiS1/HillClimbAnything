import sys
import tomllib
import unittest

import repokit
from hca import arms, gitops, spec as specmod
from hca.core import HcaError


class GlobTests(unittest.TestCase):
    def test_matcher_semantics(self):
        cases = [
            ("src/**", "src/a.txt", True), ("src/**", "src/a/b/c.py", True), ("src/**", "srcx/a.txt", False),
            ("src/**", "other/src/a.txt", False), ("src", "src/a/b.txt", True), ("src/", "src/a.txt", True),
            ("src/*.py", "src/a.py", True), ("src/*.py", "src/sub/a.py", False), ("src/*.py", "src/a.txt", False),
            ("**/*.py", "a.py", True), ("**/*.py", "x/y/a.py", True), ("**/*.py", "x/y/a.txt", False),
            ("a/**/b.txt", "a/b.txt", True), ("a/**/b.txt", "a/x/y/b.txt", True), ("a/**/b.txt", "a/x/c.txt", False),
            ("*.md", "README.md", True), ("*.md", "docs/README.md", False),
            ("hca.toml", "hca.toml", True), ("hca.toml", "hca.toml.bak", False), ("hca.toml", "sub/hca.toml", False),
            ("src/?.txt", "src/a.txt", True), ("src/?.txt", "src/ab.txt", False),
            ("src/[ab].txt", "src/a.txt", True), ("src/[ab].txt", "src/c.txt", False), ("src/[!ab].txt", "src/a.txt", False),
            ("./src/**", "src/a.txt", True), ("src/a.b", "src/aXb", False), ("**", "anything/at/all", True),
        ]
        for pattern, path, expected in cases:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(gitops.matches(path, pattern), expected)

    def test_matches_any_and_frozen_wins(self):
        s = specmod.parse_spec(tomllib.loads(repokit.toml(frozen='["src/locked/**"]')))
        self.assertTrue(gitops.is_editable("src/speed.txt", s))
        self.assertFalse(gitops.is_editable("src/locked/x.txt", s))      # frozen takes precedence over editable
        self.assertFalse(gitops.is_editable("README.md", s))
        self.assertTrue(gitops.is_frozen("hca.toml", s))                  # always frozen


class GitopsTests(repokit.RepoTestCase):
    def setUp(self):
        super().setUp()
        self.spec = specmod.load_spec(self.root / "hca.toml")
        self.base = self.repo.head()
        self.state = {"last_keep": self.base, "frozen": gitops.frozen_hashes(self.root, self.spec)}

    def scope(self):
        return gitops.scope_check(self.root, self.spec, self.state)

    def reasons(self, scope):
        return {v["path"]: v["reason"] for v in scope["violations"]}

    # --- tree state ---------------------------------------------------------------------------
    def test_clean_tree_check_ignores_the_hca_directory(self):
        self.assertEqual(gitops.dirty_paths(self.root), [])
        self.repo.write(".hca/state.json", "{}")
        self.assertEqual(gitops.dirty_paths(self.root), [])
        self.repo.write("notes.txt", "x")
        self.set_speed(25)
        self.assertEqual(sorted(gitops.dirty_paths(self.root)), ["notes.txt", "src/speed.txt"])

    def test_exclude_is_added_once_to_git_info_exclude_not_gitignore(self):
        gitops.ensure_exclude(self.root)
        gitops.ensure_exclude(self.root)
        exclude = (self.root / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.splitlines().count(".hca/"), 1)
        self.assertFalse((self.root / ".gitignore").exists())
        self.repo.write(".hca/anything", "x")
        self.assertEqual(self.status_lines(), [])

    def test_branch_create_and_checkout(self):
        gitops.create_branch(self.root, "hca/speedy")
        self.assertEqual(gitops.current_branch(self.root), "hca/speedy")
        self.assertTrue(gitops.branch_exists(self.root, "hca/speedy"))
        gitops.checkout_branch(self.root, "main")
        self.assertEqual(gitops.current_branch(self.root), "main")
        self.assertFalse(gitops.branch_exists(self.root, "hca/nope"))

    # --- scope check --------------------------------------------------------------------------
    def test_scope_ok_for_editable_changes_and_new_files(self):
        self.set_speed(20)
        self.repo.write("src/new/helper.txt", "x")
        scope = self.scope()
        self.assertTrue(scope["ok"], scope)
        self.assertEqual(gitops.editable_changes(scope), ["src/new/helper.txt", "src/speed.txt"])

    def test_scope_violations_are_named(self):
        self.repo.write("README.md", "edited\n")
        self.repo.write("bench/run.sh", "# tampered\n")
        self.repo.write("hca.toml", "# tampered\n")
        self.repo.write("bench/new.py", "x")
        self.repo.write("stray.txt", "x")
        reasons = self.reasons(self.scope())
        self.assertIn("outside the editable", reasons["README.md"])
        self.assertEqual(reasons["bench/run.sh"], "frozen file changed")
        self.assertEqual(reasons["hca.toml"], "frozen file changed")
        self.assertEqual(reasons["bench/new.py"], "new file in a frozen path")
        self.assertNotIn("stray.txt", reasons)                           # an untracked stray file is a warning, not a violation
        self.assertEqual(self.scope()["untracked_outside_editable"], ["stray.txt"])

    def test_init_cleanliness_blocks_on_tracked_edits_and_untracked_editable_or_frozen_files_only(self):
        self.repo.write("stray.txt", "x\n")
        self.repo.write("notes/todo.md", "x\n")
        state = gitops.dirty_state(self.root, self.spec)
        self.assertEqual((state["blocking"], sorted(state["ignored"])), ([], ["notes/todo.md", "stray.txt"]))
        self.repo.write("src/new.txt", "x\n")                            # untracked but editable: would be absorbed into the first keep
        self.repo.write("bench/new.py", "x\n")                           # untracked and frozen
        self.repo.write("README.md", "edited\n")                         # tracked modification
        self.assertEqual(sorted(gitops.dirty_state(self.root, self.spec)["blocking"]), ["README.md", "bench/new.py", "src/new.txt"])

    def test_git_runs_without_a_terminal_editor_or_stdin_but_hooks_still_run(self):
        out = self.tmp / "hook.out"
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text('#!/bin/sh\n{ echo "$GIT_TERMINAL_PROMPT"; echo "$GIT_EDITOR"; readlink /proc/self/fd/0; } > "$HOOKOUT"\n')
        hook.chmod(0o755)
        with repokit.env(HOOKOUT=str(out)):
            gitops.commit(self.root, "hook probe", allow_empty=True)
        prompt, editor, stdin = out.read_text().split()
        self.assertEqual((prompt, stdin), ("0", "/dev/null"))
        self.assertIn(editor, ("true", ":"))                              # git itself sets : for hooks of a commit with -m

    def test_a_failing_hook_surfaces_git_message(self):
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.write_text('#!/bin/sh\necho "lint says no" >&2\nexit 1\n')
        hook.chmod(0o755)
        with self.assertRaises(HcaError) as caught:
            gitops.commit(self.root, "will fail", allow_empty=True)
        self.assertIn("lint says no", str(caught.exception))

    def test_ignored_files_are_not_scope_violations(self):
        # an untracked file that the user's own .gitignore covers never shows up in `ls-files --others --exclude-standard`
        self.repo.write(".gitignore", "*.log\n")
        self.repo.commit_all("ignore logs")
        self.state["last_keep"] = self.repo.head()
        self.state["frozen"] = gitops.frozen_hashes(self.root, self.spec)
        self.repo.write("build.log", "x")
        self.set_speed(20)
        self.assertTrue(self.scope()["ok"])

    def test_untracked_linked_dependency_paths_are_infrastructure_not_edits(self):
        self.repo.write("hca.toml", repokit.toml(extra_task='link = ["deps"]'))
        self.repo.commit_all("link deps")
        s = specmod.load_spec(self.root / "hca.toml")
        state = {"last_keep": self.repo.head(), "frozen": gitops.frozen_hashes(self.root, s)}
        self.repo.write("deps/lib.txt", "dependency, not gitignored\n")
        self.assertTrue(gitops.scope_check(self.root, s, state)["ok"])
        self.assertEqual(gitops.dirty_paths(self.root, s.task.link), [])
        self.assertEqual(gitops.dirty_paths(self.root), ["deps/lib.txt"])
        self.repo.write("src/new.txt", "x\n")
        gitops.revert(self.root, s, state["last_keep"])
        self.assertTrue((self.root / "deps" / "lib.txt").exists())        # never deleted by a revert
        self.assertFalse((self.root / "src" / "new.txt").exists())

    def test_frozen_hash_catches_a_committed_edit_and_head_moves(self):
        self.repo.write("bench/run.sh", "# tampered and committed\n")
        self.repo.commit_all("sneaky")                                  # HEAD is no longer last_keep
        reasons = self.reasons(self.scope())
        self.assertIn("(HEAD)", reasons)
        self.assertIn("frozen file changed", reasons["bench/run.sh"])   # still differs from last_keep, so reported as a change
        # even if the agent re-bases the notion of last_keep onto the sneaky commit, the recorded hash still fails
        self.state["last_keep"] = self.repo.head()
        self.assertEqual(self.reasons(self.scope())["bench/run.sh"], "frozen file differs from the sha256 recorded at init")
        self.repo.git("rm", "-q", "bench/items.py")
        self.repo.commit_all("delete a frozen file")
        self.state["last_keep"] = self.repo.head()
        self.assertEqual(self.reasons(self.scope())["bench/items.py"], "frozen file deleted")

    # --- patch, keep, revert ------------------------------------------------------------------
    def test_patch_capture_covers_edits_and_new_files_but_not_other_paths(self):
        self.set_speed(20)
        self.repo.write("src/extra.txt", "new\n")
        self.repo.write("README.md", "not editable\n")
        patch, paths = gitops.capture_patch(self.root, self.spec, self.base)
        self.assertEqual(paths, ["src/extra.txt", "src/speed.txt"])
        self.assertIn("+20", patch)
        self.assertIn("src/extra.txt", patch)
        self.assertNotIn("README.md", patch)

    def test_keep_commits_only_the_changed_editable_paths(self):
        self.set_speed(20)
        self.repo.write("src/extra.txt", "new\n")
        scope = self.scope()
        sha = gitops.keep(self.root, self.spec, scope, "hca keep 0001: faster")
        self.assertEqual(self.repo.head(), sha)
        self.assertEqual(self.repo.git("log", "-1", "--format=%s"), "hca keep 0001: faster")
        self.assertEqual(sorted(self.repo.git("show", "--name-only", "--format=", "HEAD").splitlines()), ["src/extra.txt", "src/speed.txt"])
        self.assertEqual(self.status_lines(), [])

    def test_revert_restores_editable_paths_and_touches_nothing_else(self):
        self.set_speed(20)                                              # modified
        (self.root / "src" / "boost.txt").unlink()                      # deleted
        self.repo.write("src/created.txt", "new\n")                     # untracked, editable
        self.repo.write("src/deep/er/created.txt", "new\n")             # untracked, editable, in a new directory
        self.repo.write("src/staged.txt", "staged\n")
        self.repo.git("add", "src/staged.txt")                          # added to the index, editable
        self.repo.write("notes.txt", "my own untracked file\n")         # untracked, NOT editable
        self.repo.write("README.md", "my own edit\n")                   # modified, NOT editable
        result = gitops.revert(self.root, self.spec, self.base)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertEqual(self.repo.read("src/boost.txt"), "0\n")
        for gone in ("src/created.txt", "src/deep/er/created.txt", "src/staged.txt"):
            self.assertFalse((self.root / gone).exists(), gone)
        self.assertFalse((self.root / "src" / "deep").exists())         # the emptied directories went too
        self.assertEqual(self.repo.read("notes.txt"), "my own untracked file\n")
        self.assertEqual(self.repo.read("README.md"), "my own edit\n")
        self.assertEqual(sorted(self.status_lines()), [" M README.md", "?? notes.txt"])
        self.assertEqual(sorted(result["deleted"]), ["src/created.txt", "src/deep/er/created.txt", "src/staged.txt"])

    def test_revert_never_deletes_ignored_or_frozen_untracked_files(self):
        self.repo.write(".gitignore", "src/cache/\n")
        self.repo.commit_all("ignore cache")
        base = self.repo.head()
        self.repo.write("src/cache/blob.bin", "keep me")                # ignored (even though under an editable glob)
        self.repo.write("bench/scratch.txt", "frozen path, untracked")
        gitops.revert(self.root, self.spec, base)
        self.assertTrue((self.root / "src" / "cache" / "blob.bin").exists())
        self.assertTrue((self.root / "bench" / "scratch.txt").exists())

    def test_restore_to_makes_editable_paths_match_an_earlier_commit_as_a_new_commit(self):
        first = self.repo.head()
        self.set_speed(20)
        self.repo.write("src/extra.txt", "x\n")
        (self.root / "src" / "boost.txt").unlink()
        self.repo.commit_all("hca keep")
        self.set_speed(10)                                              # an uncommitted edit on top is discarded too
        sha = gitops.restore_to(self.root, self.spec, first, "hca revert to confirmed")
        self.assertEqual(self.repo.head(), sha)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertEqual(self.repo.read("src/boost.txt"), "0\n")
        self.assertFalse((self.root / "src" / "extra.txt").exists())
        self.assertEqual(self.repo.git("log", "-1", "--format=%s"), "hca revert to confirmed")
        self.assertEqual(self.status_lines(), [])
        self.assertEqual(self.repo.git("rev-parse", "HEAD^{tree}"), self.repo.git("rev-parse", f"{first}^{{tree}}"))

    # --- arms ---------------------------------------------------------------------------------
    def test_baseline_arm_is_a_reused_worktree_with_links_and_one_build(self):
        build = {"command": f'["{sys.executable}", "-c", "open(\'built.log\', \'a\').write(\'x\')"]'}
        text = repokit.toml(build=f"[build]\ncommand = {build['command']}\n", extra_task='link = ["deps", "missing"]')
        self.repo.write("hca.toml", text)
        self.repo.commit_all("add build")
        s = specmod.load_spec(self.root / "hca.toml")
        self.repo.write("deps/lib.txt", "dependency\n")                  # untracked: a worktree would not have it
        sha = self.repo.head()
        gitops.ensure_exclude(self.root)
        arm = arms.ensure_arm(self.root, s, sha)
        self.assertEqual(arm, arms.arm_dir(self.root, sha))
        self.assertTrue((arm / "src" / "speed.txt").exists())
        self.assertTrue((arm / "deps").is_symlink())
        self.assertEqual((arm / "deps" / "lib.txt").read_text(), "dependency\n")
        self.assertFalse((arm / "missing").exists())                    # a link that does not exist at the root is skipped
        again = arms.ensure_arm(self.root, s, sha)
        self.assertEqual(again, arm)
        self.assertEqual((arm / "built.log").read_text(), "x")          # built exactly once
        marker = arms.arms_root(self.root) / f"{sha[:12]}.built"
        self.assertEqual(self.repo.git("rev-parse", "HEAD"), sha)
        self.assertIn('"status": 0', marker.read_text())
        removed = arms.prune_arms(self.root)
        self.assertIn(sha[:12], removed)
        self.assertFalse(arm.exists())
        self.assertTrue((self.root / "deps" / "lib.txt").exists())      # pruning followed no symlink into the real dependency
        self.assertNotIn(str(arm), self.repo.git("worktree", "list"))

    def test_failed_baseline_build_is_recorded_and_retried(self):
        text = repokit.toml(build=f'[build]\ncommand = ["{sys.executable}", "-c", "import sys; sys.stderr.write(\'no compiler\'); sys.exit(4)"]\n')
        self.repo.write("hca.toml", text)
        self.repo.commit_all("bad build")
        s = specmod.load_spec(self.root / "hca.toml")
        gitops.ensure_exclude(self.root)
        sha = self.repo.head()
        for _ in range(2):                                              # a failed marker does not make the next call skip the build
            with self.assertRaises(HcaError) as caught:
                arms.ensure_arm(self.root, s, sha)
            self.assertIn("no compiler", str(caught.exception))
        self.assertIn('"status": 4', (arms.arms_root(self.root) / f"{sha[:12]}.built").read_text())


if __name__ == "__main__":
    unittest.main()
