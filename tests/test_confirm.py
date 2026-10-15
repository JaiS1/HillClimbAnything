import unittest
from pathlib import Path

import repokit
from hca import ledger
from hca.core import HcaError


class ConfirmTests(repokit.RepoTestCase):
    toml_args = {"limits": {"extend_to": 20, "confirms": 3}}

    def heldout(self, scale="1.0"):
        held = self.tmp / "held"
        held.mkdir(exist_ok=True)
        if scale is not None:
            (held / "scale.txt").write_text(scale + "\n")
        return str(held)

    def store(self):
        return self.tmp / "home" / "speedy"

    def records(self):
        return ledger.read_confirms("speedy")

    def test_nothing_to_confirm_before_a_keep_and_nothing_new_after_a_confirm(self):
        self.init()
        code, text = self.confirm()
        self.assertEqual(code, 0)
        self.assertIn("Nothing to confirm", text)
        self.assertEqual(self.records(), [])
        self.set_speed(21)
        self.try_("faster")
        self.assertEqual(self.confirm()[0], 0)
        code, text = self.confirm()
        self.assertIn("Already confirmed", text)
        self.assertEqual(len(self.records()), 1)                         # not charged again

    def test_confirm_pass_marks_the_keep_confirmed_and_keeps_detail_in_the_referee_store(self):
        self.init(heldout=self.heldout())
        self.set_speed(21)
        self.assertEqual(self.try_("faster")[0], 0)
        keep = self.repo.head()
        code, text = self.confirm()
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"CONFIRM 1: PASS - estimate t_ms -\d+\.\d% \(better\) vs the original, cut of at least \d+\.\d% \(1 of 3 confirms used\)\.")
        state = self.state()
        self.assertEqual((state["last_confirmed"], state["last_keep"], state["confirms_used"]), (keep, keep, 1))
        self.assertEqual(self.attempt(1)["confirm"], "confirmed")
        (record,) = self.records()
        self.assertEqual((record["outcome"], record["blocks"], record["heldout"], record["alpha"]), ("pass", 20, True, 0.01))
        self.assertEqual(len(record["scores"]), 20)                      # full detail lives in the store only
        self.assertNotIn("scores", text)
        self.assertTrue((self.store() / "confirms" / "01" / "runs.jsonl").is_file())
        self.assertIn("Confirmed: t_ms -", (self.root / ".hca" / "VERDICTS.md").read_text())
        self.assertEqual(self.state()["last_confirmed_result"]["n"], 1)

    def test_confirm_uses_commits_not_the_working_tree(self):
        self.init()
        self.set_speed(21)
        self.try_("faster")
        self.set_speed(5)                                                # an uncommitted edit: would read as +500% if measured
        code, text = self.confirm()
        self.assertEqual(code, 0, text)
        self.assertGreater(self.records()[0]["estimate"], -40)             # about -30%; the 5 in the working tree would read -83%
        self.assertEqual(self.repo.read("src/speed.txt"), "5\n")         # a passing confirm leaves the edit alone

    def test_heldout_dir_is_passed_to_the_objective_and_a_dev_only_win_fails_to_confirm(self):
        self.init(heldout=self.heldout())
        self.repo.write("src/dev_hack.txt", "0.7\n")                     # looks 30% faster on dev items only
        code, text = self.try_("game the dev items")
        self.assertEqual(code, 0, text)
        init_tree = self.repo.git("rev-parse", f"{self.state()['init_sha']}^{{tree}}")
        self.set_speed(25)                                               # uncommitted work in the editable paths
        code, text = self.confirm()
        self.assertEqual(code, 1, text)
        self.assertIn("CONFIRM 1: FAIL", text)
        self.assertRegex(text, r"estimate t_ms [+-]\d+\.\d% \((better|worse|unchanged)\) vs the original")
        self.assertNotIn("heldout", text.lower())
        state = self.state()
        self.assertIsNone(state["last_confirmed"])
        self.assertEqual(state["last_keep"], self.repo.head())
        self.assertEqual(self.repo.git("log", "-1", "--format=%s"), "hca revert to confirmed")
        self.assertEqual(self.repo.git("rev-parse", "HEAD^{tree}"), init_tree)   # editable paths are back to the original
        self.assertFalse((self.root / "src" / "dev_hack.txt").exists())
        self.assertEqual(self.status_lines(), [])
        self.assertEqual(self.attempt(1)["confirm"], "unconfirmed")
        self.assertEqual(self.records()[0]["outcome"], "fail")
        self.assertTrue(self.records()[0]["heldout"])
        discarded = self.root / ".hca" / "discarded-by-confirm-01.diff"
        self.assertIn("+25", discarded.read_text())                      # the uncommitted edit was saved before being reset
        verdicts = (self.root / ".hca" / "VERDICTS.md").read_text()
        self.assertIn("~~0001 keep", verdicts)
        self.assertIn("unconfirmed", verdicts)
        # the next try is measured against the reset commit
        self.set_speed(21)
        self.assertEqual(self.try_("an honest change")[0], 0)
        self.assertEqual(self.attempt(2)["base"], state["last_keep"])

    def test_without_a_heldout_set_confirm_uses_fresh_dev_blocks_so_the_dev_hack_survives(self):
        self.init()
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("game the dev items")[0], 0)
        code, text = self.confirm()
        self.assertEqual(code, 0, text)
        self.assertFalse(self.records()[0]["heldout"])

    def test_a_failed_confirm_resets_to_the_last_confirmed_commit_not_to_the_original(self):
        self.init(heldout=self.heldout())
        self.set_speed(21)
        self.assertEqual(self.try_("real win")[0], 0)
        win = self.repo.head()
        self.assertEqual(self.confirm()[0], 0)
        self.set_speed(40)                                               # really slower everywhere ...
        self.repo.write("src/dev_hack.txt", "0.4\n")                     # ... but it looks fast on dev items
        code, text = self.try_("too clever")
        self.assertEqual(code, 0, text)
        code, text = self.confirm()
        self.assertEqual(code, 1, text)
        self.assertIn("last confirmed commit", text)
        state = self.state()
        self.assertEqual(state["last_confirmed"], win)
        self.assertNotEqual(state["last_keep"], win)                     # the reset is a new commit on top
        self.assertEqual(self.repo.git("rev-parse", "HEAD^{tree}"), self.repo.git("rev-parse", f"{win}^{{tree}}"))
        self.assertEqual(self.repo.read("src/speed.txt"), "21\n")
        self.assertFalse((self.root / "src" / "dev_hack.txt").exists())
        self.assertEqual([self.attempt(1)["confirm"], self.attempt(2)["confirm"]], ["confirmed", "unconfirmed"])
        verdicts = (self.root / ".hca" / "VERDICTS.md").read_text()
        self.assertIn("Confirmed: t_ms -", verdicts)                          # the confirmed result is still the headline
        self.assertIn("~~0002 keep", verdicts)

    def test_confirm_cap_is_enforced_from_the_referee_store(self):
        self.repo.write("hca.toml", repokit.toml(limits={"extend_to": 20, "confirms": 1}))
        self.repo.commit_all("one confirm only")
        self.init()
        self.set_speed(21)
        self.try_("a")
        self.assertEqual(self.confirm()[0], 0)
        self.set_speed(14)
        self.try_("b")
        state = self.state()
        state["confirms_used"] = 0                                       # the agent rewinds its own counter ...
        ledger.save_state(self.root, state)
        with self.assertRaises(HcaError) as caught:                      # ... the store still knows
            self.confirm()
        self.assertIn("confirm cap is reached (1 of 1", str(caught.exception))
        self.assertEqual(len(self.records()), 1)

    def test_an_unmeasurable_confirm_is_inconclusive_and_changes_nothing(self):
        self.init(heldout=self.heldout(scale=None))                      # the objective needs scale.txt in heldout mode
        self.set_speed(21)
        self.try_("faster")
        keep = self.repo.head()
        code, text = self.confirm()
        self.assertEqual(code, 2, text)
        self.assertIn("INCONCLUSIVE", text)
        self.assertEqual(self.repo.head(), keep)
        self.assertIsNone(self.state()["last_confirmed"])
        self.assertEqual(self.attempt(1)["confirm"], "pending")
        self.assertEqual(self.records()[0]["outcome"], "void")

    def test_hca_home_isolates_the_referee_store(self):
        real = Path.home() / ".hca" / "speedy"
        existed = real.exists()
        self.init(heldout=self.heldout())
        self.set_speed(21)
        self.try_("faster")
        self.confirm()
        self.assertEqual(ledger.store_dir("speedy"), self.store())
        self.assertTrue((self.store() / "confirms.jsonl").is_file())
        self.assertTrue((self.store() / "heldout" / "scale.txt").is_file())
        self.assertEqual(real.exists(), existed)


class ItemsConfirmTests(repokit.RepoTestCase):
    mode = "items"
    toml_args = {"mechanism": '[mechanism]\nslices = ["group"]\n'}

    def test_items_confirm_passes_and_records_slices_in_the_store(self):
        self.assertEqual(self.init()[0], 0)
        self.repo.write("src/boost.txt", "0.1\n")
        self.assertEqual(self.try_("better")[0], 0)
        code, text = self.confirm()
        self.assertEqual(code, 0, text)
        self.assertIn("estimate +0.0875 vs the original", text)
        (record,) = ledger.read_confirms("speedy")
        self.assertEqual((record["outcome"], record["blocks"]), ("pass", 1))
        self.assertEqual(set(record["slices"]), {"group=even", "group=odd"})
        self.assertEqual(record["slices"]["group=even"]["skipped"], 1)    # item i0 scores exactly 0: no log ratio
        self.assertEqual(len(record["per_item"]), 12)
        self.assertNotIn("per_item", text)

    def test_confirm_refuses_when_head_has_moved_off_the_last_keep(self):
        self.assertEqual(self.init()[0], 0)
        self.repo.write("src/boost.txt", "0.1\n")
        self.assertEqual(self.try_("better")[0], 0)
        # the user commits by hand on the branch: HEAD is no longer the last keep, which confirm refuses
        self.repo.write("src/boost.txt", "0\n")
        self.repo.commit_all("manual change")
        with self.assertRaises(HcaError) as caught:
            self.confirm()
        self.assertIn("HEAD is not the last keep", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
