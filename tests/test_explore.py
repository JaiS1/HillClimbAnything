"""Explore mode (docs/DESIGN-climb-engine.md v0.5): screens, the archive, the sampler, promotions, taint, `next`, the dream.

Each review class from §9 has its own tests (B1 replay, B2 taint, B3 base threading, S1 dropped keeps, S3/N2 store counts and
screen filtering, S4 the separate screen path, S5 arm pruning, S7 screen size), plus the statistical invariants: selection
then promotion at exact alpha, an RNG for the sampler that is independent of the block order, and line mode unchanged.
"""

import contextlib
import io
import json
import math
import random
from types import SimpleNamespace
from unittest import mock

import repokit
from hca import arms, cli, explore, gitops, ledger, measure, stats
from hca.commands import common, report, try_
from hca.core import HcaError, Refusal
from repokit import env

# A line-mode try recorded on the code before explore mode existed (init seed 3, speed 27, try seed 424242): block order,
# both arms' values, the estimate and the verdict must not move.
GOLDEN_ORDER = [[0, "candidate", 0, 26.64], [0, "baseline", 1, 30.5245], [1, "baseline", 0, 29.4524], [1, "candidate", 1, 27.3224],
                [2, "baseline", 0, 30.3234], [2, "candidate", 1, 27.4076], [3, "candidate", 0, 27.1901], [3, "baseline", 1, 29.4706],
                [4, "baseline", 0, 29.7515], [4, "candidate", 1, 26.5145], [5, "candidate", 0, 27.4999], [5, "baseline", 1, 29.5638],
                [6, "candidate", 0, 27.5049], [6, "baseline", 1, 30.3329], [7, "baseline", 0, 29.4864], [7, "candidate", 1, 26.8857],
                [8, "baseline", 0, 29.4663], [8, "candidate", 1, 27.3921], [9, "baseline", 0, 29.8541], [9, "candidate", 1, 27.0819]]
GOLDEN_HEADLINE = "attempt 0001  KEEP  t_ms -9.0% (better; cut of at least 7.2%, p = 0.000977, 10 blocks"


def runs(root, n):
    path = ledger.attempt_path(root, n) / "runs.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if json.loads(line).get("kind") == "run"]


class ExploreCase(repokit.RepoTestCase):
    """The fixture repo with a fixed sampler seed (the real one comes from the random store id)."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(explore, "climb_seed", return_value=12345)
        patch.start()
        self.addCleanup(patch.stop)

    def _try(self, seed, **args):
        base = {"message": "child", "again": False, "explore": False, "promote": None, "idea": None, "tainted": False}
        base.update(args)
        return self.hca(try_, args=base, seed=seed)

    def screen(self, message="child", seed=1, idea=None):
        return self._try(seed, message=message, explore=True, idea=idea)

    def promote(self, m, message="promote", seed=2, again=False, tainted=False):
        return self._try(seed, message=message, promote=str(m), again=again, tainted=tainted)

    def next_(self, parent=None, why="test override"):
        from hca.commands import next_
        return self.hca(next_, args={"parent": None if parent is None else str(parent), "why": why})

    def entry(self, n):
        return explore.entries("speedy")[n]

    def show(self, sha, path):
        return self.repo.git("show", f"{sha}:{path}")


# --- line mode is unchanged ----------------------------------------------------------------------------

class LineModeRegressionTests(repokit.RepoTestCase):
    def test_a_seeded_line_try_has_the_same_block_order_scores_and_verdict_as_before_explore_mode(self):
        self.init(seed=3)
        self.set_speed(27)
        code, text = self.try_("golden", seed=424242)
        self.assertEqual(code, 0, text)
        self.assertEqual([[r["block"], r["arm"], r["order"], r["value"]] for r in runs(self.root, 1)], GOLDEN_ORDER)
        rec = self.attempt(1)
        self.assertEqual((rec["verdict"], rec["estimate_log"], rec["p"], rec["blocks"]), ("keep", 0.09377803467050569, 0.0009765625, 10))
        self.assertEqual(rec["lower"], -7.232008257391602)
        self.assertEqual(text.splitlines()[0].rsplit(",", 1)[0], GOLDEN_HEADLINE)
        self.assertNotIn("explore", self.state())                      # line mode writes no explore state
        self.assertEqual(ledger.store_counts("speedy"), {"try": 1, "promote": 0, "screen": 0})


# --- S4: screens have their own path and can never keep -------------------------------------------------

class ScreenPathTests(ExploreCase):
    def test_a_screen_never_reaches_apply_keep_or_write_pending_and_never_moves_the_branch(self):
        self.init()
        head = self.repo.head()
        boom = AssertionError("a screen reached the keep path")
        with mock.patch.object(try_, "_apply_keep", side_effect=boom), mock.patch.object(common, "write_pending", side_effect=boom), \
                mock.patch.object(gitops, "advance_branch", side_effect=boom):
            self.set_speed(15)                                            # a huge win: a keep would be certain on the line path
            code, text = self.screen("much faster")
            self.assertEqual(code, 5, text)
            self.assertIn("a screen, not evidence", text)
            self.next_(parent=1)
            self.repo.write("src/boost.txt", "3\n")
            self.assertEqual(self.screen("again")[0], 5)
        self.assertEqual(self.repo.head(), head)
        self.assertEqual(self.state()["last_keep"], head)
        self.assertEqual(self.attempt(1)["verdict"], "screened")
        self.assertIsNone(self.attempt(1).get("commit"))

    def test_the_screen_records_kind_class_estimate_and_ref(self):
        self.init()
        self.set_speed(27)
        self.screen("faster", idea="I1")
        rec, entry = self.attempt(1), self.entry(1)
        self.assertEqual((rec["kind"], rec["class"], entry["kind"]), ("screen", "SCREENED", "screen"))
        self.assertLess(rec["estimate"], -5)                              # t_ms about -10% vs the parent
        self.assertEqual(self.repo.git("rev-parse", gitops.attempt_ref(1)), entry["sha"])
        self.assertEqual(entry["parent"], None)
        self.assertGreater(rec["explore"]["divergence"], 0)
        self.assertEqual(rec["explore"]["module"], "src/speed.txt")

    def test_explore_is_refused_in_items_mode(self):
        class Items(repokit.RepoTestCase):
            mode = "items"

            def runTest(self):
                pass
        case = Items()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.init()
        case.repo.write("src/boost.txt", "0.2\n")
        with self.assertRaises(HcaError) as caught:
            case.hca(try_, args={"message": "x", "explore": True}, seed=1)
        self.assertIn("blocks mode only", str(caught.exception))


# --- S7: screen size and the bound -------------------------------------------------------------------

class ScreenSizeTests(ExploreCase):
    def spec(self, useful=0.05):
        return SimpleNamespace(objective=SimpleNamespace(useful=useful, direction="min"),
                               limits=SimpleNamespace(try_seconds=10 ** 6, blocks=10, extend_to=20))

    def test_n_s_is_clamped_to_5_through_8(self):
        self.assertEqual(explore.screen_blocks({"noise": {"noise_sd": 0.0001}}, self.spec()), 5)
        self.assertEqual(explore.screen_blocks({"noise": {"noise_sd": 0.02}}, self.spec()), 6)
        self.assertEqual(explore.screen_blocks({"noise": {"noise_sd": 0.5}}, self.spec()), 8)
        self.assertEqual(explore.screen_blocks({}, self.spec()), 8)

    def test_no_bound_is_reported_below_5_blocks(self):
        def fake(scores):
            return measure.BlockResult(scores, list(range(len(scores))), [], False, 5, {}, {}, 0, [], 0.0, 0.0)
        t = SimpleNamespace(state={"noise": {"noise_sd": 0.0001}}, refresh=lambda: ("b", "c"), rng=random.Random(1), adir=None,
                            deadline=float("inf"), prepare=None)
        with mock.patch.object(measure, "run_blocks", return_value=fake([0.01, 0.02, 0.015, 0.03])):    # one of 5 blocks voided
            outcome = try_._decide_screen(self.spec(), t, {})
        self.assertIsNone(outcome["fields"]["screen"]["bound95"])
        with mock.patch.object(measure, "run_blocks", return_value=fake([0.01, 0.02, 0.015, 0.03, 0.02])):
            outcome = try_._decide_screen(self.spec(), t, {})
        self.assertIsNotNone(outcome["fields"]["screen"]["bound95"])


# --- B3: base threading --------------------------------------------------------------------------------

class BaseThreadingTests(ExploreCase):
    def parent_p(self):
        self.init()
        self.set_speed(27)
        self.assertEqual(self.screen("P: faster")[0], 5)
        self.next_(parent=1)
        self.assertEqual(self.repo.read("src/speed.txt"), "27\n")

    def test_a_file_hand_restored_to_the_last_keep_is_measured_as_it_is_on_disk(self):
        self.parent_p()
        self.set_speed(30)                                                # the parent changed P; the agent restores it by hand
        self.repo.write("src/boost.txt", "1\n")
        code, text = self.screen("undo P, touch something else")
        self.assertEqual(code, 5, text)
        rec = self.attempt(2)
        self.assertEqual(self.show(rec["snapshot"], "src/speed.txt"), "30")
        self.assertGreater(rec["estimate"], 5)                            # slower than the parent P (27 -> 30), as on disk

    def test_the_baseline_is_the_parent_and_the_change_list_is_against_it(self):
        self.parent_p()
        self.repo.write("src/boost.txt", "1\n")
        self.assertEqual(self.screen("only boost")[0], 5)
        rec = self.attempt(2)
        self.assertEqual(rec["base"], self.entry(1)["sha"])
        self.assertLess(abs(rec["estimate"]), 3)                          # the same speed as the parent, not -10% vs the incumbent
        self.assertIn("src/boost.txt", (ledger.attempt_path(self.root, 2) / "patch.diff").read_text())
        self.assertNotIn("src/speed.txt", (ledger.attempt_path(self.root, 2) / "patch.diff").read_text())
        self.assertEqual(json.loads((ledger.attempt_path(self.root, 2) / "scope.json").read_text())["changed"], ["src/boost.txt"])

    def test_head_is_still_checked_against_the_last_keep_and_outside_or_frozen_edits_are_caught(self):
        self.parent_p()
        self.repo.write("README.md", "edited\n")
        code, text = self.screen("outside")
        self.assertEqual(code, 2)
        self.assertIn("outside the editable paths", text)
        self.repo.git("checkout", "--", "README.md")
        self.repo.write("bench/run.sh", self.repo.read("bench/run.sh") + "\n# tweak\n")
        code, text = self.screen("frozen")
        self.assertEqual(code, 2)
        self.assertIn("frozen file changed", text)
        self.repo.git("checkout", "--", "bench/run.sh")
        self.repo.write("src/boost.txt", "2\n")
        self.repo.commit_all("sneaky commit on the branch")
        code, text = self.screen("committed")
        self.assertEqual(code, 2)
        self.assertIn("(HEAD)", text)

    def test_the_empty_check_is_against_the_base(self):
        self.init()
        code, text = self.screen("nothing")
        self.assertEqual(code, 2)
        self.assertIn("nothing to measure", text)
        self.assertEqual(self.attempt(1)["class"], "INVALID")

    def test_the_base_tree_assert_fires_when_disk_and_snapshot_differ(self):
        self.parent_p()
        self.repo.write("src/boost.txt", "1\n")
        with mock.patch.object(gitops, "working_tree_hash", return_value="0" * 40):
            code, text = self.screen("mismatch")
        self.assertEqual(code, 2)
        self.assertIn("differs from the editable files on disk", text)
        self.assertEqual(self.repo.read("src/boost.txt"), "1\n")          # nothing touched


# --- re-screen, ideas, pointers -----------------------------------------------------------------------

class MemoryTests(ExploreCase):
    def test_a_screen_with_no_edits_at_a_member_re_screens_it_and_pools_its_blocks(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        n_s = self.attempt(1)["screen"]["blocks"]
        self.next_(parent=1)
        code, text = self.screen("re-screen", seed=9)
        self.assertEqual(code, 5, text)
        rec = self.attempt(2)
        self.assertEqual((rec["screen"]["blocks"], rec["explore"]["pooled_from"], rec["tree"]), (2 * n_s, [1], self.attempt(1)["tree"]))
        self.assertIn(f"{n_s} pooled", text)
        archive = explore.build(self.root, repokit_spec(self.root), self.state())
        self.assertEqual(archive["elites"], [2])                          # the pooled re-screen supersedes the first screen

    def test_ideas_are_parsed_leniently_and_a_try_records_the_idea_kind_and_backlog(self):
        self.repo.write("IDEAS.md", "# notes\n## I4 [avoid-work] Skip absent patterns\nwhere: src/speed.txt\nwhy: most never match\n\n"
                        "## I14 [dispatch] Table  (stepping stone)\nparent: 0003\n## I9b [nonsense] bad kind\n## not an idea\n")
        ideas = explore.read_ideas(self.root)
        self.assertEqual([(i["id"], i["kind"], i["parent"], i["where"]) for i in ideas],
                         [("I4", "avoid-work", None, "src/speed.txt"), ("I14", "dispatch", 3, None), ("I9b", "untagged", None, None)])
        self.init()                                                       # IDEAS.md is not an edit: init and tries ignore it
        self.set_speed(27)
        self.screen("faster", idea="I4")
        rec = self.attempt(1)
        self.assertEqual((rec["idea"], rec["idea_kind"], rec["backlog"]["ids"]), ("I4", "avoid-work", ["I4", "I14", "I9b"]))
        self.assertEqual(len(rec["backlog"]["sha256"]), 64)
        self.assertTrue((self.root / "IDEAS.md").exists())                # never reverted or snapshotted
        self.assertNotIn("IDEAS.md", (ledger.attempt_path(self.root, 1) / "patch.diff").read_text())
        self.assertEqual(explore.idea_kind(self.root, "I99"), "untagged")

    def test_review_pointers_are_recorded_and_unioned_over_the_lineage_at_promotion(self):
        self.init()
        self.repo.write("src/memo.py", "cache = {}\n")
        self.set_speed(28)
        self.screen("adds a cache")
        self.assertEqual(self.attempt(1)["review_pointers"], ["cache"])
        self.next_(parent=1)
        self.set_speed(27)
        self.screen("faster still")
        self.next_(parent=2)
        code, text = self.promote(2)
        self.assertIn("review pointers over the lineage: cache", text)
        self.assertIn("lineage 0001, 0002", text)

    def test_outcome_classes(self):
        useful = math.log(1 / 0.95)
        base = {"verdict": "revert", "valid": True, "p": 0.3, "level": 0.01, "estimate_log": 0.01, "upper": -6.0, "includes_useful": True}
        cls = lambda **kw: explore.outcome_class(dict(base, **kw), useful, "min")      # noqa: E731
        self.assertEqual(cls(), "NO EVIDENCE")
        self.assertEqual(cls(includes_useful=False), "NULL")
        self.assertEqual(cls(p=0.001), "SMALL")
        self.assertEqual(cls(estimate_log=-0.05, upper=+2.0, includes_useful=False), "HARMFUL")
        self.assertEqual(cls(verdict="keep"), "KEPT")
        self.assertEqual(cls(valid=False), "INVALID")
        self.assertEqual(cls(kind="screen", verdict="screened"), "SCREENED")
        self.assertEqual(cls(kind="screen", verdict="invalid"), "INVALID")


def repokit_spec(root):
    from hca.spec import load_spec
    return load_spec(root / "hca.toml")


# --- B1: store kinds and the replay rule ---------------------------------------------------------------

class ReplayTests(ExploreCase):
    def test_promoting_a_member_right_after_its_screen_is_not_a_replay(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        self.next_(parent="incumbent")
        code, text = self.promote(1)
        self.assertEqual(code, 0, text)
        self.assertIn("KEEP", text)
        self.assertIn("promoted member 0001", text)
        self.assertEqual(self.entry(2)["kind"], "promote")
        self.assertEqual(self.repo.read("src/speed.txt"), "27\n")
        self.assertEqual(self.state()["explore"]["parent"], None)

    def revert_r(self):
        self.init()
        self.set_speed("29.9")                                            # 0.3% faster: a line revert
        code, text = self.try_("tiny")
        self.assertEqual(code, 1, text)

    def test_a_tree_with_a_prior_try_needs_again_and_the_again_is_recorded(self):
        self.revert_r()
        self.set_speed("29.9")                                            # the same tree, now as a screen
        self.assertEqual(self.screen("R as a screen")[0], 5)
        self.next_(parent=2)
        code, text = self.promote(2)
        self.assertEqual(code, 2, text)
        self.assertIn("already tried as attempt 0001", text)
        self.assertEqual((self.attempt(3)["verdict"], self.entry(3)["kind"], self.entry(3)["refused"]), ("invalid", "promote", True))
        self.assertEqual(ledger.store_counts("speedy")["promote"], 1)     # a refused promotion counts
        code, text = self.promote(2, again=True)
        self.assertIn(code, (0, 1), text)
        self.assertTrue(self.entry(4)["again"])

    def test_the_null_laundering_sequence_is_refused_without_again(self):
        self.revert_r()
        self.next_(parent=1)                                              # name the line revert as a parent ...
        self.assertEqual(self.repo.read("src/speed.txt"), "29.9\n")
        code, text = self.screen("re-screen R")                           # ... a line try is not a screen: nothing to re-screen
        self.assertEqual(code, 2, text)
        code, text = self.promote(1)                                      # ... and promoting R itself is a replay of R
        self.assertEqual(code, 2, text)
        self.assertIn("refused", text)


# --- S3 / N2: counts from the store, consumers filter screens ------------------------------------------

class CountTests(ExploreCase):
    def test_max_tries_counts_line_tries_only_and_counts_survive_a_tampered_verdict(self):
        toml = self.repo.read("hca.toml").replace('useful_effect = "5%"\n', 'useful_effect = "5%"\nmax_tries = 1\n')
        self.repo.write("hca.toml", toml)
        self.repo.commit_all("max_tries 1")
        self.init()
        self.set_speed("29.9")
        self.assertNotIn("past max_tries", self.try_("one")[1])
        for i, speed in enumerate(("28", "27")):
            self.set_speed(speed)
            self.assertNotIn("past max_tries", self.screen(f"s{i}")[1])
            self.next_(parent="incumbent")
        record = ledger.attempt_path(self.root, 2) / "verdict.json"
        tampered = json.loads(record.read_text())
        tampered.update(kind="try", verdict="keep", confirm="pending")
        record.write_text(json.dumps(tampered))
        self.assertEqual(ledger.store_counts("speedy"), {"try": 1, "promote": 0, "screen": 2})
        self.assertEqual(ledger.tries_used(self.root, "speedy"), 1)
        _, text = self.next_()
        self.assertIn("tries 1 · promotions 0 · screens 2", text)
        self.set_speed("29.8")
        self.assertIn("past max_tries", self.try_("two")[1])

    def test_headline_verdicts_report_rows_and_the_confirm_sweep_ignore_screens(self):
        self.init()
        self.set_speed(27)
        self.screen("a screen")
        self.next_(parent="incumbent")
        verdicts = (self.root / ".hca" / "VERDICTS.md").read_text()
        self.assertIn("No keeps yet.", verdicts)
        self.assertNotIn("0001", verdicts)
        _, text = self.report()
        self.assertIn("Attempts\n  none yet", text)
        self.assertIn("Validity rate: 0 of 0", text)
        self.assertIn("Screens: 1 (1 valid, 0 invalid)", text)
        self.set_speed(21)
        self.assertEqual(self.try_("real keep")[0], 0)
        self.assertEqual(self.confirm()[0], 0)
        self.assertNotIn("confirm", self.attempt(1).get("confirm") or "")  # the sweep never touched the screen

    def test_the_bound_counts_tries_plus_promotions_and_screens_are_separate(self):
        self.init()
        self.set_speed(27)
        self.screen("s")
        self.next_(parent="incumbent")
        self.promote(1)
        self.set_speed("29.9")
        self.try_("line")
        _, text = self.report()
        self.assertIn("Expected false keeps so far <= 2 tries + promotions x 1% = 0.02", text)
        self.assertIn("Screens: 1", text)
        self.assertIn("Promotions: 1 (1 kept)", text)


# --- S1: dropped keeps --------------------------------------------------------------------------------

class DroppedKeepTests(ExploreCase):
    def test_promoting_a_member_forked_before_a_keep_records_and_prints_the_dropped_keep(self):
        self.init()
        self.repo.write("src/boost.txt", "1\n")
        self.set_speed(28)
        self.screen("member M from the original")
        self.next_(parent="incumbent")
        self.set_speed(21)
        self.assertEqual(self.try_("keep K")[0], 0)
        code, text = self.promote(1)
        self.assertIn("dropped keeps: keep 0002", text)
        self.assertEqual(self.attempt(3)["explore"]["dropped_keeps"], [2])
        self.assertEqual(self.entry(3)["dropped_keeps"], [2])
        self.assertIn("Promotion 0003 dropped keep 0002", self.report()[1])
        self.assertIn("keep 0002 dropped by 0003", self.next_()[1])


# --- B2: taint after a failed confirm ----------------------------------------------------------------

class TaintTests(ExploreCase):
    toml_args = {"limits": {"confirms": 3}}

    def setUp(self):
        super().setUp()
        held = self.tmp / "held"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        self.held = str(held)

    def failed_confirm(self):
        self.init(heldout=self.held)
        self.set_speed(28)
        self.screen("A from the original")                               # 1: not built on the bad keep
        self.next_(parent="incumbent")
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("dev-only win")[0], 0)                 # 2: a keep that will not confirm
        self.repo.write("src/boost.txt", "1\n")
        self.screen("B on the bad keep")                                  # 3: descends from keep 2
        self.next_(parent="incumbent")
        code, text = self.confirm()
        self.assertEqual(code, 1, text)
        return text

    def test_a_failed_confirm_taints_exactly_the_members_built_on_unconfirmed_keeps(self):
        text = self.failed_confirm()
        self.assertIn("now tainted", text)
        self.assertTrue(self.entry(3)["tainted"])
        self.assertTrue(self.entry(2)["unconfirmed"])
        self.assertFalse(self.entry(1).get("tainted"))
        self.assertEqual(self.state()["explore"], {"parent": None, "sampling": None})

    def test_tainted_members_are_never_sampled_or_hinted_and_promotion_needs_tainted(self):
        self.failed_confirm()
        archive = explore.build(self.root, repokit_spec(self.root), self.state(), 7)
        self.assertNotIn(3, archive["elites"])
        self.assertIn(3, archive["tainted"])
        self.assertTrue(all(explore.sample(archive, k, 7)["parent"] != 3 for k in range(50)))
        hint = explore.hint(archive, 5)
        self.assertTrue(hint is None or hint["n"] != 3)
        with self.assertRaises(Refusal) as caught:
            self.promote(3)
        self.assertIn("--tainted", str(caught.exception))
        with self.assertRaises(Refusal):
            self.next_(parent=3)
        code, text = self.promote(3, tainted=True)
        self.assertIn("TAINTED member", text)
        self.assertTrue(self.attempt(4)["explore"]["tainted"])
        self.assertIn("promoted a TAINTED member", self.report()[1])


# --- S5: arm pruning ----------------------------------------------------------------------------------

class PruneTests(ExploreCase):
    def arm_dirs(self):
        return {p.name for p in arms.arms_root(self.root).iterdir() if p.is_dir()}

    def test_pruning_keeps_the_parent_arm_and_caps_member_arms(self):
        self.init()
        self.set_speed(28)
        self.screen("P")
        self.next_(parent=1)
        with mock.patch.object(explore, "MEMBER_ARMS", 2):
            for i in range(3):
                self.repo.write("src/boost.txt", f"{i + 1}\n")
                self.assertEqual(self.screen(f"child {i}")[0], 5)
                self.next_(parent=1)
            self.assertIn(self.entry(1)["sha"][:12], self.arm_dirs())
            self.assertLessEqual(len(self.arm_dirs()), 3)                 # the original (= last keep), the parent, one recent member


# --- the sampler, the archive, the RNG ----------------------------------------------------------------

class SamplerTests(ExploreCase):
    def fake_archive(self):
        nodes = {n: {"f_rank": f} for n, f in ((1, 0.0), (2, -0.05), (3, 0.01))}
        return {"incumbent": 0, "f_inc": 0.0, "floor": 0.01, "elites": [1, 2, 3], "nodes": nodes, "children": {1: 3}}

    def test_incumbent_mass_and_the_deficit_cap(self):
        log = explore.sample(self.fake_archive(), 1, 5)
        self.assertEqual(log["candidates"], [0, 1, 3])                    # member 2 is more than 2F behind: never drawn
        self.assertEqual(log["weights"][0], 0.4)
        self.assertAlmostEqual(sum(log["weights"]), 1.0, places=5)
        self.assertLess(log["weights"][1], log["weights"][2])             # three children already drawn from 1
        self.assertEqual(sorted(log), ["candidates", "parent", "parent_p", "sampler", "weights", "weights_hash"])

    def test_the_same_seed_and_store_give_the_same_parents_and_a_byte_identical_archive(self):
        self.init()
        for speed in ("28", "27", "29"):
            self.set_speed(speed)
            self.repo.write("src/boost.txt", speed + "\n")
            self.screen(f"s{speed}")
            self.next_(parent="incumbent")
        spec = repokit_spec(self.root)
        archive = explore.build(self.root, spec, self.state(), 99)
        first = [explore.sample(archive, k, 99)["parent"] for k in range(40)]
        self.assertEqual(first, [explore.sample(explore.build(self.root, spec, self.state(), 99), k, 99)["parent"] for k in range(40)])
        self.assertGreater(len(set(first)), 1)
        cache = self.root / ".hca" / "archive.json"
        before = cache.read_bytes()
        cache.unlink()
        self.next_()
        self.assertEqual(cache.read_bytes(), before)

    def test_the_sampler_seed_never_changes_a_block_order(self):
        self.init()
        orders = []
        for i, sampler_seed in enumerate((1, 2, 3)):
            with mock.patch.object(explore, "climb_seed", return_value=sampler_seed):
                self.set_speed(str(27 + i))
                self.screen(f"s{i}", seed=777)
                self.next_(parent="incumbent")
            orders.append([(r["block"], r["arm"]) for r in runs(self.root, i + 1)])
        rng, expected = random.Random(777), []
        for block in range(len(orders[0]) // 2):
            pair = ["baseline", "candidate"]
            rng.shuffle(pair)
            expected += [(block, arm) for arm in pair]
        self.assertEqual(orders, [expected] * 3)
        state = random.getstate()
        explore.sample(self.fake_archive_from_store(), 1, 4)
        self.assertEqual(random.getstate(), state)                        # the sampler has its own stream, not the global one

    def fake_archive_from_store(self):
        return explore.build(self.root, repokit_spec(self.root), self.state(), 4)


class ExactAlphaTests(ExploreCase):
    """S2: selection then promotion. Fake arms with Gaussian noise and no true effect, floor 0 (alpha is the only barrier):
    2,000 rounds of 'screen 20 null children, promote the best'. The promotion's blocks are fresh, so the keep count must be
    consistent with alpha = 0.01 under a one-sided binomial test at 1e-4."""

    def test_selection_then_promotion_keeps_at_most_alpha(self):
        noise = random.Random(20261016)

        def fake_blocks(spec, base, cand, n, rng, env, out, **kw):
            for _ in range(n):                                            # the block order still consumes the try's own RNG
                rng.shuffle(["baseline", "candidate"])
            scores = [noise.gauss(0.0, 0.02) for _ in range(n)]
            return measure.BlockResult(scores, list(range(n)), [], False, n, {}, {}, 0, [], 0.0, 0.0)

        spec = SimpleNamespace(objective=SimpleNamespace(useful=0.0, direction="max"),
                               limits=SimpleNamespace(blocks=10, extend_to=20, try_seconds=10 ** 6))
        t = SimpleNamespace(state={"noise": {"noise_sd": 0.02}}, refresh=lambda: ("b", "c"), adir=None, deadline=float("inf"),
                            prepare=None, rng=random.Random(1))
        rounds, keeps = 2000, 0
        with mock.patch.object(measure, "run_blocks", side_effect=fake_blocks):
            for r in range(rounds):
                t.rng = random.Random(r)
                best = max(try_._decide_screen(spec, t, {})["fields"]["estimate_log"] for _ in range(20))
                self.assertGreater(best, -1)
                t.rng = random.Random(10 ** 6 + r)
                keeps += try_._decide_blocks(spec, t, {})["verdict"] == "keep"
        pmf = lambda k: math.exp(math.lgamma(rounds + 1) - math.lgamma(k + 1) - math.lgamma(rounds - k + 1)      # noqa: E731
                                 + k * math.log(0.01) + (rounds - k) * math.log(0.99))
        tail = 1 - sum(pmf(k) for k in range(keeps))                       # P(X >= keeps) under alpha = 0.01
        self.assertGreater(tail, 1e-4, f"{keeps} keeps in {rounds} null promotions")
        self.assertLessEqual(keeps, 40)


# --- next, dream, promotion hint, leakage --------------------------------------------------------------

class NextTests(ExploreCase):
    def test_next_shows_budget_parent_archive_and_a_promotion_hint(self):
        self.repo.write("IDEAS.md", "## I1 [micro] faster\nwhere: src/speed.txt\n## I2 [io] untested\nwhere: src/other/x.txt\n")
        self.init()
        self.set_speed(25)
        self.screen("much faster", idea="I1")
        code, text = self.next_(parent=1)
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertLessEqual(len(lines), 60)
        self.assertTrue(lines[0].startswith("climb     speedy · "))
        self.assertIn("tries 0 · promotions 0 · screens 1 · confirms 0 of 5", lines[0])
        self.assertIn("resolution ≈", text)
        self.assertIn("parent    0001  [src/speed.txt]  chosen by override", text)
        self.assertIn("lineage  0001 I1", text)
        self.assertIn("archive   2 members, 1 of 7 files", text)
        self.assertRegex(text, r"promote   0001 lead \+\d+\.\d pp")
        self.assertIn("open      I1 SCREENED", text)
        self.assertIn("backlog   I2 [io]", text)
        self.assertIn("working   editable paths are at 0001", text)

    def test_next_parent_refuses_unscreened_edits(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        self.next_(parent="incumbent")
        self.repo.write("src/boost.txt", "5\n")
        with self.assertRaises(Refusal):
            self.next_(parent=1)
        self.assertEqual(self.repo.read("src/boost.txt"), "5\n")
        with self.assertRaises(Refusal):
            self.promote(1)

    def test_a_line_try_after_a_screen_resets_the_parent_and_status_still_works(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        self.next_(parent=1)
        self.repo.write("src/boost.txt", "1\n")
        self.assertIn(self.try_("line try from the parent")[0], (0, 1))
        self.assertIsNone(self.state()["explore"]["parent"])
        lines = []
        report.run_status(None, out=lines.append, cwd=self.root)
        self.assertIn("hca next", lines[-1])
        with contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()) as shown:
            self.assertEqual(cli.main(["next"]), 0)
        self.assertIn("climb     speedy", shown.getvalue())

    def test_dream_writes_a_bounded_packet_with_all_sections(self):
        self.init()
        for speed in ("28", "27"):
            self.set_speed(speed)
            self.screen(f"s{speed}")
            self.next_(parent="incumbent")
        self.set_speed("29.9")
        self.try_("tiny")
        with mock.patch.object(report, "_context", return_value=(self.root, repokit_spec(self.root), self.state())):
            code, text = self.hca(report, args={"md": None, "dream": True})
        self.assertEqual(code, 0)
        self.assertIn("Wrote .hca/DREAM.md", text)
        dream = (self.root / ".hca" / "DREAM.md").read_text()
        self.assertLessEqual(len(dream.splitlines()), 150)
        for heading in ("## 1. Archive map", "## 2. Lineages", "## 3. Open evidence", "## 4. Overlap map", "## 5. Resolution", "## 6."):
            self.assertIn(heading, dream)
        self.assertIn("stepping stone you expect to read worse first", dream)

    def test_next_archive_and_dream_never_show_heldout_or_failed_confirm_numbers(self):
        held = self.tmp / "held"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        self.init(heldout=str(held))
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("dev-only")[0], 0)
        self.set_speed(28)
        self.screen("s")
        self.next_(parent="incumbent")
        self.assertEqual(self.confirm()[0], 1)
        failed = ledger.read_confirms("speedy")[-1]
        _, text = self.next_()
        from hca.commands import next_
        next_.dream(self.root, repokit_spec(self.root), self.state())
        blob = text + (self.root / ".hca" / "archive.json").read_text() + (self.root / ".hca" / "DREAM.md").read_text()
        self.assertNotIn(str(held), blob)
        self.assertNotIn("heldout", blob.lower())
        self.assertNotIn(ledger.fmt_effect(failed["estimate"], "blocks"), blob)
        self.assertIn("(no confirm yet)", text)


class EnvSanityTests(ExploreCase):
    def test_the_jitter_fixture_gives_five_block_screens(self):
        with env(BENCH_JITTER="0.02"):
            self.init()
        self.assertEqual(explore.screen_blocks(self.state(), repokit_spec(self.root)), 5)


# --- N3: promotion trusts the store and the refs only --------------------------------------------------

class PromotionChecksTests(ExploreCase):
    def test_promotion_refuses_unknown_invalid_and_ref_tampered_members(self):
        self.init()
        with self.assertRaises(Refusal):
            self.promote(7)                                               # no such attempt
        self.screen("nothing to screen")                                  # 1: invalid (empty)
        with self.assertRaises(Refusal) as caught:
            self.promote(1)
        self.assertIn("not a measured attempt", str(caught.exception))
        self.set_speed(27)
        self.screen("P")                                                  # 2
        self.next_(parent="incumbent")
        self.repo.git("update-ref", gitops.attempt_ref(2), self.repo.head())
        with self.assertRaises(Refusal) as caught:
            self.promote(2)
        self.assertIn("does not match the referee store", str(caught.exception))

    def test_a_forged_archive_json_or_verdict_changes_nothing_a_promotion_checks(self):
        self.init()
        self.set_speed("29.9")
        self.try_("R")                                                    # 1: a line revert
        self.set_speed("29.9")
        self.screen("R again as a screen")                                # 2: same tree
        self.next_(parent="incumbent")
        (self.root / ".hca" / "archive.json").write_text('{"members": [2], "tainted": []}')
        record = ledger.attempt_path(self.root, 1) / "verdict.json"
        record.write_text(json.dumps(dict(json.loads(record.read_text()), kind="screen", tree="forged")))
        code, text = self.promote(2)                                      # the store still knows attempt 1 was a try of this tree
        self.assertEqual(code, 2, text)
        self.assertIn("already tried as attempt 0001", text)


# --- outcomes of a screen other than a ranking -----------------------------------------------------------

class ScreenOutcomeTests(ExploreCase):
    def test_after_a_screen_the_editable_paths_are_at_the_sampled_parent(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        state = self.state()
        parent = state["explore"]["parent"]
        sha = self.state()["last_keep"] if parent is None else self.entry(parent)["sha"]
        self.assertEqual(gitops.working_tree_hash(self.root, repokit_spec(self.root), sha), self.repo.git("rev-parse", f"{sha}^{{tree}}"))
        self.assertEqual(state["explore"]["sampling"]["sampler"], "w1")
        self.assertEqual(self.attempt(1)["explore"]["sampling"], None)   # the first child was not drawn by the sampler

    def test_a_crashing_candidate_is_an_invalid_screen_with_edits_untouched(self):
        self.init()
        self.repo.write("src/crash.txt", "x\n")
        code, text = self.screen("crash")
        self.assertEqual(code, 2, text)
        self.assertIn("INVALID", text)
        self.assertTrue((self.root / "src" / "crash.txt").exists())
        self.assertFalse(self.entry(1)["valid"])
        self.assertEqual(explore.build(self.root, repokit_spec(self.root), self.state())["elites"], [])

    def test_an_interrupted_screen_is_recorded_and_leaves_the_edits(self):
        self.init()
        self.set_speed(27)
        with mock.patch.object(measure, "run_blocks", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.screen("interrupted")
        self.assertEqual((self.attempt(1)["verdict"], self.attempt(1)["kind"]), ("interrupted", "screen"))
        self.assertEqual(self.repo.read("src/speed.txt"), "27\n")

    def test_files_changed_during_a_screen_void_it_and_nothing_is_archived(self):
        self.init()
        self.set_speed(27)
        real = try_._decide_screen

        def edit_midway(*args, **kwargs):
            outcome = real(*args, **kwargs)
            self.repo.write("src/boost.txt", "9\n")
            return outcome
        with mock.patch.object(try_, "_decide_screen", side_effect=edit_midway):
            code, text = self.screen("racing edit")
        self.assertEqual(code, 2, text)
        self.assertIn("VOID", text)
        self.assertEqual(self.repo.read("src/boost.txt"), "9\n")
        self.assertEqual(explore.build(self.root, repokit_spec(self.root), self.state())["elites"], [])


# --- the pieces of `next`, on fake records ------------------------------------------------------------

class NextPiecesTests(ExploreCase):
    def ctx(self, entries, records=None, ideas=()):
        archive = {"cells": {"src/a.py": {}}, "elites": [], "nodes": {}}
        return {"task": "speedy", "entries": entries, "records": records or {}, "ideas": list(ideas), "archive": archive}

    def test_warnings_after_ten_screens_without_a_promotion_and_twenty_without_a_confirm(self):
        from hca.commands import next_
        with mock.patch.object(ledger, "read_confirms", return_value=[]):
            few = next_._warnings(self.ctx({n: {"kind": "screen", "at": "x", "valid": True} for n in range(1, 10)}))
            self.assertEqual(few, [])
            ten = next_._warnings(self.ctx({n: {"kind": "screen", "at": "x", "valid": True} for n in range(1, 11)}))
            self.assertEqual(len(ten), 1)
            self.assertIn("10 screens since the last promotion", ten[0])
            many = {n: {"kind": "screen", "at": "x", "valid": True} for n in range(1, 22)}
            many[5] = {"kind": "promote", "at": "x"}
            self.assertIn("20 screens since the last confirm", "\n".join(next_._warnings(self.ctx(many))))

    def test_the_backlog_has_a_fixed_mechanical_order(self):
        from hca.commands import next_
        ideas = [{"id": i, "kind": k, "parent": p, "where": w} for i, k, p, w in (
            ("I1", "micro", None, "src/a.py"), ("I2", "allocation", None, "src/new.py"), ("I3", "micro", 4, None),
            ("I4", "dispatch", None, None), ("I5", "io", None, None), ("I6", "config", None, None), ("I7", "algorithm", None, None))]
        records = {1: {"idea": "I4", "class": "NO EVIDENCE"}, 2: {"idea": "I6", "class": "KEPT"},
                   3: {"idea": "X1", "class": "NULL", "idea_kind": "io"}, 5: {"idea": "X2", "class": "NULL", "idea_kind": "io"}}
        order = next_._backlog(self.ctx({}, records, ideas), parent=4)
        self.assertEqual(order, ["I3 [micro]", "I4 [dispatch]", "I2 [allocation]", "I1 [micro]", "I7 [algorithm]", "I5 [io]"])

    def test_the_hint_promotes_a_clear_lead_however_noisy_and_re_screens_only_an_ambiguous_one(self):
        def node(f_raw, se, blocks=5, lineage=(1,), step=None):
            return {"f_raw": f_raw, "var": se * se, "se": se, "blocks": blocks, "lineage": list(lineage), "screened": True,
                    "retired": False, "step": f_raw if step is None else step}
        nodes = {1: node(0.040, 0.01)}
        archive = {"elites": [1], "nodes": nodes, "floor": 0.01, "f_inc": 0.0, "screens": 3}
        best = explore.hint(archive, 5)
        self.assertEqual((best["n"], best["action"]), (1, "promote"))
        self.assertAlmostEqual(best["allowance"], 0.01 * math.sqrt(2 * math.log(3)))  # M = the number of valid screens
        nodes[1] = node(0.36, 0.0063)                                     # PM: a +36 pp lead with SE > F/3 promotes, no re-screen
        self.assertEqual(explore.hint(archive, 5)["action"], "promote")
        nodes[1] = node(0.020, 0.01)                                      # below floor + allowance, but could be above the floor
        self.assertEqual(explore.hint(archive, 5)["action"], "rescreen")
        nodes[1] = node(0.020, 0.01, blocks=10)                           # ... already re-screened: nothing to hint
        self.assertIsNone(explore.hint(archive, 5))
        nodes[1] = node(-0.03, 0.01)                                      # behind: nothing to hint
        self.assertIsNone(explore.hint(archive, 5))
        archive["screens"] = 1                                            # one screen: no selection, no allowance
        nodes[1] = node(0.011, 0.01)
        self.assertEqual(explore.hint(archive, 5)["allowance"], 0.0)

    def test_the_hint_names_a_stacked_chain(self):
        nodes = {1: {"f_raw": 0.02, "var": 0.0, "se": 0.0, "blocks": 5, "lineage": [1], "screened": True, "retired": False, "step": 0.02},
                 2: {"f_raw": 0.05, "var": 0.0, "se": 0.0, "blocks": 5, "lineage": [1, 2], "screened": True, "retired": False, "step": 0.03}}
        best = explore.hint({"elites": [2], "nodes": nodes, "floor": 0.01, "f_inc": 0.0, "screens": 2}, 5)
        self.assertEqual((best["n"], best["stacked"]), (2, [1, 2]))
        nodes[1]["retired"] = True                                        # step 1 is already in the incumbent: no chain left
        self.assertEqual(explore.hint({"elites": [2], "nodes": nodes, "floor": 0.01, "f_inc": 0.0, "screens": 2}, 5)["stacked"], [])

    def test_next_never_prints_more_than_60_lines(self):
        from hca.commands import next_
        self.init()
        with mock.patch.object(next_, "render", return_value=[f"line {i}" for i in range(200)]):
            _, text = self.next_()
        self.assertEqual(len(text.splitlines()), 60)

    def test_next_warns_when_the_working_diff_overlaps_an_earlier_revert(self):
        self.init()
        self.set_speed("29.9")
        self.assertEqual(self.try_("tiny")[0], 1)
        self.set_speed("29.8")
        _, text = self.next_()
        self.assertRegex(text, r"overlap   your working diff touches the same lines as 0001 (NULL|NO EVIDENCE)")


# --- fixes from the adversarial review of Phase 1 ---------------------------------------------------------

class HeldoutCase(ExploreCase):
    toml_args = {"limits": {"confirms": 3}}

    def setUp(self):
        super().setUp()
        held = self.tmp / "held"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        self.held = str(held)


class ReviewTaintTests(HeldoutCase):
    def test_a_failed_promotion_taints_the_promoted_member_and_its_children(self):
        """adv2 test_A: the keep a promotion makes is a fresh commit, so its member and the member's children do not descend
        from it; they must be tainted by content and lineage."""
        self.init(heldout=self.held)
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.screen("A dev-only")                                         # 1
        self.next_(parent=1)
        self.repo.write("src/boost.txt", "1\n")
        self.screen("B child of A")                                       # 2
        self.next_(parent="incumbent")
        self.assertEqual(self.promote(1)[0], 0)                           # 3: keeps the dev-only hack
        self.assertEqual(self.confirm()[0], 1)
        self.assertTrue(self.entry(1)["tainted"])
        self.assertTrue(self.entry(2)["tainted"])
        self.assertTrue(self.entry(3)["unconfirmed"])
        self.assertFalse(self.entry(3).get("tainted"))                    # the undone keep is unconfirmed, not "tainted by itself"
        with self.assertRaises(Refusal):
            self.promote(2)
        with self.assertRaises(Refusal):
            self.promote(1, again=True)

    def test_a_crash_in_the_failed_confirm_is_finished_with_its_taint_by_the_next_command(self):
        """adv test_C: the taint hook raises; the next command reconciles the restore and taints."""
        self.init(heldout=self.held)
        self.set_speed(28)
        self.screen("A")                                                  # 1
        self.next_(parent="incumbent")
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("dev win")[0], 0)                      # 2
        self.repo.write("src/boost.txt", "1\n")
        self.screen("B on the bad keep")                                  # 3
        self.next_(parent="incumbent")
        with mock.patch.object(explore, "taint_after_fail", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.confirm()
        self.assertEqual(ledger.get_pending("speedy")["kind"], "restore")
        _, text = self.next_()
        self.assertIn("finished an interrupted confirm reset", text)
        self.assertIsNone(ledger.get_pending("speedy"))
        self.assertTrue(self.entry(2)["unconfirmed"])
        self.assertTrue(self.entry(3)["tainted"])
        self.assertFalse(self.entry(1).get("tainted"))
        with self.assertRaises(Refusal):
            self.promote(3)


class ReviewFixTests(ExploreCase):
    def test_re_screens_pool_each_block_once(self):
        """adv test_B: 5, 10, 15, 20 blocks, not 5, 10, 20, 40."""
        self.init()
        self.set_speed(28)
        self.screen("M")
        for i in range(3):
            self.next_(parent=1)
            self.assertEqual(self.screen("re-screen", seed=10 + i)[0], 5)
        self.assertEqual([self.entry(n)["blocks"] for n in (1, 2, 3, 4)], [5, 10, 15, 20])
        self.assertEqual([len(self.entry(n)["scores"]) for n in (1, 2, 3, 4)], [5, 10, 15, 20])

    def test_dream_keeps_its_instruction_however_many_records(self):
        """adv2 test_F: 200 open records once pushed the instruction past the 150-line cut."""
        from hca.commands import next_
        self.init()
        self.set_speed("29.9")
        self.try_("t")
        ctx = next_._context(self.root, repokit_spec(self.root), self.state())
        for n in range(10, 210):
            ctx["records"][n] = {"n": n, "class": "NO EVIDENCE", "kind": "try", "estimate": 1.0, "message": "m"}
        with mock.patch.object(next_, "_context", return_value=ctx):
            text = next_.dream(self.root, repokit_spec(self.root), self.state()).read_text()
        self.assertLessEqual(len(text.splitlines()), 150)
        self.assertIn("Write 5-10 ideas", text)
        self.assertIn("- (+175 more)", text)

    def test_relabelling_a_keep_as_a_screen_in_verdict_json_changes_nothing(self):
        """adv test_D: kind comes from the referee store."""
        self.init()
        self.set_speed(21)
        self.assertEqual(self.try_("keep")[0], 0)
        _, before = self.report()
        record = ledger.attempt_path(self.root, 1) / "verdict.json"
        record.write_text(json.dumps(dict(json.loads(record.read_text()), kind="screen")))
        _, after = self.report()
        self.assertEqual(after, before)
        self.assertRegex(after, r"0001\s+keep")
        self.assertIn("Unconfirmed cumulative", after)

    def test_a_member_that_hand_undoes_a_keep_reports_it_dropped(self):
        self.init()
        self.set_speed(21)
        self.assertEqual(self.try_("keep K")[0], 0)                       # 1
        self.set_speed(30)                                                # undo K's file by hand, on top of K
        self.repo.write("src/boost.txt", "1\n")
        self.screen("undoes K")                                           # 2: descends from K
        self.next_(parent="incumbent")
        self.assertEqual(explore.dropped_keeps(self.root, self.state(), self.entry(2)["sha"]), [1])
        self.assertIn("dropped keeps: keep 0001", self.promote(2)[1])

    def test_next_parent_with_a_bad_id_is_a_usage_error(self):
        self.init()
        with self.assertRaises(HcaError) as caught:
            self.next_(parent="foo")
        self.assertNotIsInstance(caught.exception, Refusal)
        self.assertIn("attempt id", str(caught.exception))
        with contextlib.chdir(self.root), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["next", "--parent", "foo"]), 3)

    def test_an_editable_ideas_md_is_an_edit_in_line_mode_and_the_notebook_in_explore_mode(self):
        self.repo.write("hca.toml", self.repo.read("hca.toml").replace('editable = ["src/**"]', 'editable = ["src/**", "IDEAS.md"]'))
        self.repo.commit_all("IDEAS.md is editable")
        self.init()
        self.repo.write("IDEAS.md", "## I1 [micro] x\n")
        self.set_speed(21)
        self.assertEqual(self.try_("line keep with notes")[0], 0)
        self.assertEqual(self.repo.git("show", "HEAD:IDEAS.md"), "## I1 [micro] x")
        self.repo.write("IDEAS.md", "## I1 [micro] x\n## I2 [io] y\n")
        self.set_speed(19)
        self.assertEqual(self.screen("explore")[0], 5)
        self.assertNotIn("IDEAS.md", (ledger.attempt_path(self.root, 2) / "patch.diff").read_text())
        self.assertIn("I2", self.repo.read("IDEAS.md"))                   # the restore to the next parent leaves the notebook alone

    def test_the_max_tries_warning_counts_promotions(self):
        self.repo.write("hca.toml", self.repo.read("hca.toml").replace('useful_effect = "5%"\n', 'useful_effect = "5%"\nmax_tries = 1\n'))
        self.repo.commit_all("max_tries 1")
        self.init()
        self.set_speed("29.9")
        self.assertNotIn("past max_tries", self.try_("one")[1])
        self.set_speed(27)
        self.assertNotIn("past max_tries", self.screen("s")[1])
        self.next_(parent="incumbent")
        self.assertIn("this is test 2 (tries + promotions), past max_tries (1)", self.promote(2)[1])

    def test_a_refused_promotion_puts_the_editable_paths_back(self):
        self.init()
        self.set_speed("29.9")
        self.assertEqual(self.try_("R")[0], 1)                            # 1
        self.set_speed("29.9")
        self.screen("R as a screen")                                      # 2: same tree
        self.next_(parent="incumbent")
        code, text = self.promote(2)
        self.assertEqual(code, 2, text)
        self.assertIn("put back where they were before --promote", text)
        self.assertNotIn("untouched", text)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertIsNone(self.state()["explore"]["parent"])

    def test_a_promotion_whose_baseline_cannot_be_built_is_discarded_and_undone(self):
        self.init()
        self.set_speed(27)
        self.screen("M")
        self.next_(parent="incumbent")
        with mock.patch.object(arms, "ensure_arm", side_effect=HcaError("baseline build broke")):
            with self.assertRaises(HcaError):
                self.promote(1)
        self.assertEqual(self.repo.read("src/speed.txt"), "30\n")
        self.assertIsNone(self.state()["explore"]["parent"])
        self.assertNotIn(2, explore.entries("speedy"))

    def test_report_dream_takes_the_lock(self):
        self.init()
        with ledger.lock(self.root, "try"):
            with self.assertRaises(HcaError) as caught:
                self.hca(report, args={"md": None, "dream": True})
        self.assertIn("another `hca try` is running", str(caught.exception))

    def test_a_pre_explore_ledger_still_works(self):
        self.init()
        self.set_speed("29.9")
        self.assertEqual(self.try_("old")[0], 1)
        store = ledger.load_store("speedy")
        store["attempts"] = [{k: e[k] for k in ("n", "session", "tree", "again", "refused", "at")} for e in store["attempts"]]
        ledger.save_store("speedy", store)
        record = ledger.attempt_path(self.root, 1) / "verdict.json"
        old = {k: v for k, v in json.loads(record.read_text()).items() if k not in ("kind", "review_pointers")}
        record.write_text(json.dumps(old))
        self.assertEqual(self.next_()[0], 0)
        code, text = self.report()
        self.assertEqual(code, 0)
        self.assertRegex(text, r"0001\s+revert")
        self.set_speed(27)
        self.assertEqual(self.try_("new")[0], 0)
        self.assertEqual(ledger.store_counts("speedy"), {"try": 2, "promote": 0, "screen": 0})


class ExactAlphaControlTests(ExploreCase):
    def test_a_leaky_promotion_that_reuses_the_screen_blocks_fails_the_bound(self):
        """Positive control for the exact-alpha test: the same selection, but the 'promotion' reuses the best screen's blocks."""
        noise = random.Random(7)
        rounds, keeps = 1000, 0
        for _ in range(rounds):
            screens = [[noise.gauss(0.0, 0.02) for _ in range(8)] for _ in range(20)]
            best = max(screens, key=stats.hodges_lehmann)
            keeps += stats.keep_decision(best, 0.0, look=1, looks=1)["verdict"] == "keep"
        pmf = lambda k: math.exp(math.lgamma(rounds + 1) - math.lgamma(k + 1) - math.lgamma(rounds - k + 1)      # noqa: E731
                                 + k * math.log(0.01) + (rounds - k) * math.log(0.99))
        tail = 1 - sum(pmf(k) for k in range(keeps))
        self.assertLess(tail, 1e-4, f"{keeps} keeps in {rounds}: the leak should be caught")

    def test_the_real_promote_path_measures_fresh_blocks(self):
        self.init()
        self.set_speed(27)
        self.screen("M")
        self.next_(parent="incumbent")
        screen_scores = self.entry(1)["scores"]
        drawn, noise = [], random.Random(11)

        def fresh(spec, base, cand, n, rng, env, out, **kw):
            scores = [noise.gauss(0.0, 0.02) for _ in range(n)]
            drawn.extend(scores)
            return measure.BlockResult(scores, list(range(n)), [], False, n, {}, {}, 0, [], 0.0, 0.0)
        with mock.patch.object(measure, "run_blocks", side_effect=fresh):
            self.promote(1)
        rec = self.attempt(2)
        self.assertEqual(rec["blocks"], len(drawn))
        self.assertAlmostEqual(rec["estimate_log"], stats.hodges_lehmann(drawn))
        self.assertFalse(set(screen_scores) & set(drawn))


# --- friction from the proof climbs (PM -50.2%, yaml -8.8%) ----------------------------------------------------------

class ProofClimbFixTests(ExploreCase):
    def promoted_lineage(self):
        """PM's sequence: promote member A (keep 2), then screen A's child B and promote it."""
        self.init()
        self.set_speed(28)
        self.screen("A")                                                  # 1
        self.next_(parent="incumbent")
        self.assertEqual(self.promote(1)[0], 0)                           # 2: keep, promoting member 1
        self.next_(parent=1, why="build on the promoted member")
        self.repo.write("src/boost.txt", "1\n")
        self.set_speed(26)
        self.screen("B, child of A")                                      # 3
        self.next_(parent="incumbent")

    def test_promoting_the_child_of_a_promoted_member_drops_no_keep(self):
        self.promoted_lineage()
        self.assertEqual(explore.dropped_keeps(self.root, self.state(), self.entry(3)["sha"]), [])
        code, text = self.promote(3)                                      # 4
        self.assertIn("dropped keeps: none", text)
        self.assertEqual(explore.dropped_at(self.root, self.state(), 4), [])
        self.assertNotIn("dropped", self.report()[1].split("Stepping stones")[1].split("\n")[1])
        self.assertNotIn("dropped   ", self.next_()[1])

    def test_a_promoted_member_and_its_ancestors_are_retired_from_sampling(self):
        self.promoted_lineage()
        self.assertEqual(self.promote(3)[0], 0)                           # 4: promotes B, whose parent A was promoted before
        archive = explore.build(self.root, repokit_spec(self.root), self.state(), 9)
        self.assertEqual(archive["retired"], [1, 3])
        self.assertFalse(set(archive["elites"]) & {1, 3})
        self.assertTrue(all(explore.sample(archive, k, 9)["parent"] not in (1, 3) for k in range(50)))
        self.assertIn("2 retired (in the incumbent)", self.next_()[1])

    def test_a_stepping_stone_is_a_screen_worse_than_its_parent_and_stone_keeps_are_reported(self):
        self.init()
        self.set_speed(31)
        self.screen("stone: slower alone")                                # 1: worse than its parent
        self.next_(parent=1, why="build on the stone")
        self.set_speed(25)
        self.screen("pays off on the stone")                              # 2
        self.next_(parent="incumbent")
        archive = explore.build(self.root, repokit_spec(self.root), self.state())
        self.assertEqual(archive["stepping_stones"], [1])
        self.promote(2)                                                   # 3: keeps; its lineage holds the stone
        self.assertEqual(explore.stone_keeps("speedy"), [3])
        self.assertIn("Stepping stones: 1 screen(s) read worse than their own parent; kept promotions built on one: 1.", self.report()[1])
        self.assertIn("1 stepping stones (screened worse than their parent) · 0 behind the incumbent", self.next_()[1])

    def test_init_name_runs_a_new_task_without_editing_hca_toml_and_logs_it(self):
        from hca.commands import init
        ledger.init_store("speedy", "0" * 40, None, {}, {})               # an earlier climb under this name used a confirm
        ledger.append_confirm("speedy", {"n": 1, "outcome": "pass"})
        with self.assertRaises(HcaError) as caught:
            self.init()
        self.assertIn("hca init --name speedy-2", str(caught.exception))
        with self.assertRaises(HcaError):
            self.hca(init, args={"heldout": None, "force": False, "reset": False, "name": "Bad Name"}, aa_blocks=8, seed=1)
        with self.assertRaises(HcaError):                                 # the hca.toml name itself stays refused
            self.hca(init, args={"heldout": None, "force": False, "reset": False, "name": "speedy"}, aa_blocks=8, seed=1)
        code, text = self.hca(init, args={"heldout": None, "force": False, "reset": False, "name": "speedy-2"}, aa_blocks=8, seed=1)
        self.assertEqual(code, 0, text)
        state = self.state()
        self.assertEqual((state["task"], state["spec_task"], state["branch"]), ("speedy-2", "speedy", "hca/speedy-2"))
        self.assertEqual(self.repo.git("status", "--porcelain"), "")       # hca.toml untouched
        self.set_speed(25)
        self.assertEqual(self.try_("works under the new name")[0], 0)
        self.assertEqual(ledger.load_store("speedy")["renamed_to"][0]["task"], "speedy-2")
        self.assertEqual(ledger.load_store("speedy-2")["renamed_from"][0]["spec_name_confirms_used"], 1)
        self.assertIn("hca.toml says 'speedy'", self.report()[1])

    def test_a_refusal_is_on_stdout_and_says_how_to_recover_without_git_stash(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        self.next_(parent="incumbent")
        self.repo.write("src/boost.txt", "4\n")
        with contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()) as shown, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["next", "--parent", "1", "--why", "x"]), 2)
        self.assertTrue(shown.getvalue().startswith("REFUSED (exit 2): the editable paths hold edits"))
        self.assertIn("src/boost.txt", shown.getvalue())
        self.assertIn(f"git checkout {self.state()['last_keep'][:12]} -- src/boost.txt", shown.getvalue())
        self.assertIn("Do not `git stash` them", shown.getvalue())

    def test_a_crlf_rewrite_gets_a_churn_warning(self):
        self.repo.write("src/big.txt", "".join(f"line {i}\n" for i in range(120)))
        self.repo.commit_all("a big file")
        self.init()
        (self.root / "src" / "big.txt").write_bytes("".join(f"line {i}\r\n" for i in range(120)).replace("line 7\r", "LINE 7\r").encode())
        code, text = self.screen("one line, CRLF everywhere")
        self.assertIn("changes 120 lines, but only 1 once line endings are ignored", text)
        self.assertEqual(self.attempt(1)["churn"], {"lines": 120, "ignoring_line_endings": 1})

    def test_invalid_screens_are_counted_apart(self):
        self.init()
        self.screen("no edit")                                            # 1: invalid
        self.set_speed(27)
        self.screen("real")                                               # 2
        self.next_(parent="incumbent")
        self.assertIn("screens 1 (1 invalid)", self.next_()[1])
        self.assertIn("Screens: 2 (1 valid, 1 invalid)", self.report()[1])
        self.assertEqual(explore.build(self.root, repokit_spec(self.root), self.state())["screens"], 1)

    def test_parent_overrides_need_a_reason_that_is_recorded_and_the_rate_is_reported(self):
        self.init()
        self.set_speed(27)
        self.screen("P")                                                  # 1
        with self.assertRaises(HcaError) as caught:
            self.next_(parent=1, why=None)
        self.assertIn("--why", str(caught.exception))
        _, text = self.next_(parent=1, why="P's table makes the next idea one line")
        self.assertIn("chosen by override (P's table makes the next idea one line)", text)
        self.assertIn('then      edit from 0001, then:  hca try --explore --idea I<n> -m "..."', text)
        self.repo.write("src/boost.txt", "1\n")
        self.screen("child")                                              # 2
        self.assertEqual(self.attempt(2)["explore"]["sampling"]["why"], "P's table makes the next idea one line")
        self.assertIn("parent overrides 1 of 1 screen(s) with sampler data (100%); 1 screen(s) without it (the sampler was not used)", self.report()[1])

    def test_next_says_confirm_now_after_two_keeps(self):
        self.init()
        self.set_speed(27)
        self.assertEqual(self.try_("one")[0], 0)
        self.assertNotIn("confirm   ", self.next_()[1])
        self.set_speed(24)
        self.assertEqual(self.try_("two")[0], 0)
        self.assertIn("confirm   2 keeps since the last confirm  →  hca confirm (5 of 5 left)", self.next_()[1])


class CellTests(ExploreCase):
    def test_weighty_skips_metadata_small_reexports_test_helpers_and_tiny_files(self):
        self.assertFalse(explore.weighty("markdown/__meta__.py", 5000))
        self.assertFalse(explore.weighty("markdown/__init__.py", 900))
        self.assertTrue(explore.weighty("markdown/__init__.py", 9000))
        self.assertFalse(explore.weighty("markdown/test_tools.py", 9000))
        self.assertFalse(explore.weighty("lib/tests/helpers.js", 9000))
        self.assertFalse(explore.weighty("src/parser/x.spec.ts", 9000))
        self.assertFalse(explore.weighty("src/tiny.ts", 120))
        self.assertTrue(explore.weighty("markdown/treeprocessors.py", 17000))

    def test_cells_are_files_for_a_small_editable_set_and_dream_lists_only_weighty_empty_cells(self):
        from hca.commands import next_
        self.repo.write("src/parser/a.ts", "x" * 900)
        self.repo.write("src/parser/b.ts", "y" * 900)
        self.repo.write("src/__init__.py", "from .a import *\n")
        self.repo.write("src/test_tools.py", "z" * 900)
        self.repo.commit_all("more editable files")
        self.init()
        self.repo.write("src/parser/a.ts", "x" * 899 + "!")
        self.screen("a")
        self.next_(parent="incumbent")
        archive = explore.build(self.root, repokit_spec(self.root), self.state())
        self.assertTrue(archive["per_file"])
        self.assertEqual(list(archive["cells"]), ["src/parser/a.ts"])     # not "src/parser"
        dream = next_.dream(self.root, repokit_spec(self.root), self.state()).read_text()
        self.assertIn("- empty cells (files with code weight): src/parser/b.ts\n", dream)
        self.assertIn("only if that file is plausibly hot", dream)
        self.repo.write("IDEAS.md", "hot: src/speed.txt\n")
        dream = next_.dream(self.root, repokit_spec(self.root), self.state()).read_text()
        self.assertIn("- empty cells (the hot: list in IDEAS.md): src/speed.txt\n", dream)

    def test_hot_lines_accept_bullets_dot_slash_and_backslashes(self):
        self.repo.write("IDEAS.md", "- hot: ./src/a.py, src\\parser\\b.ts\n* hot: `src/c.py`\n## I1 [micro] x\nnot hot: src/d.py\n")
        self.assertEqual(explore.read_hot(self.root), ["src/a.py", "src/parser/b.ts", "src/c.py"])

    def test_a_ledger_without_file_cells_does_not_list_files_under_a_filled_module_as_empty(self):
        from hca.commands import next_
        self.repo.write("src/parser/a.ts", "x" * 900)
        self.repo.write("src/parser/b.ts", "y" * 900)
        self.repo.commit_all("parser files")
        self.init()
        self.repo.write("src/parser/a.ts", "x" * 899 + "!")
        self.screen("a")
        self.next_(parent="incumbent")
        store = ledger.load_store("speedy")
        for e in store["attempts"]:
            e.pop("file", None)                                           # as an older hca wrote it
        ledger.save_store("speedy", store)
        archive = explore.build(self.root, repokit_spec(self.root), self.state())
        self.assertEqual(list(archive["cells"]), ["src/parser"])
        self.assertNotIn("src/parser/b.ts", next_._empty_cells(self.root, repokit_spec(self.root), self.state(), archive)[0])

    def test_numstat_keeps_non_ascii_paths_as_they_are(self):
        self.repo.write("src/ünï.txt", "a\n")
        first = self.repo.commit_all("a non-ASCII name")
        self.repo.write("src/ünï.txt", "b\n")
        second = self.repo.commit_all("changed")
        self.assertEqual(gitops.numstat(self.root, repokit_spec(self.root), first, second), {"src/ünï.txt": 1})


# --- the second adversarial review: renames, retired children, --why, reverted promotions -------------------------------

class RenameChainTests(HeldoutCase):
    def rn(self, name, reset=True, heldout=None):
        from hca.commands import init
        return self.hca(init, args={"heldout": heldout, "force": False, "reset": reset, "name": name}, aa_blocks=8, seed=1)

    def failed(self):
        self.init(heldout=self.held)
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("dev-only win")[0], 0)
        self.assertEqual(self.confirm()[0], 1)

    def test_t1_init_name_cannot_launder_a_failed_confirm(self):
        self.failed()
        with self.assertRaises(Refusal) as caught:
            self.rn("speedy-2")
        self.assertIn("FAILED", str(caught.exception))
        self.assertIsNone(ledger.load_store("speedy-2"))
        self.assertEqual(self.state()["task"], "speedy")

    def test_a_human_rename_after_a_fail_keeps_the_heldout_set_and_the_chain(self):
        self.failed()
        with env(HCA_ALLOW_RESET="1"):
            code, text = self.rn("speedy-2")
        self.assertEqual(code, 0, text)
        self.assertIn("Held-out data inherited from 'speedy'", text)
        self.assertTrue(self.state()["heldout"])
        self.assertIsNotNone(ledger.heldout_dir("speedy-2"))
        self.assertEqual(ledger.chain_of("speedy-2"), {"tasks": [{"task": "speedy", "confirms": 1, "fails": 1}], "confirms": 1, "fails": 1})
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.assertEqual(self.try_("the same hack again", again=True)[0], 0)
        self.assertEqual(self.confirm()[0], 1)                            # the inherited held-out set still catches it
        report = self.report()[1]
        self.assertIn("Re-baselined from 'speedy' after 1 confirm(s) (1 FAIL) along the chain speedy", report)
        self.assertIn("Confirms used: 2 of 3 (1 under earlier names of this climb).", report)
        with env(HCA_ALLOW_RESET="1"):
            self.assertEqual(self.rn("speedy-3")[0], 0)
        self.assertEqual(ledger.load_store("speedy-2")["renamed_to"][0]["task"], "speedy-3")
        self.assertEqual(ledger.load_store("speedy-3")["renamed_from"][0]["from"], "speedy-2")
        self.assertEqual(ledger.chain_of("speedy-3")["confirms"], 2)
        self.assertEqual(ledger.chain_of("speedy-3")["fails"], 2)

    def test_a_rename_starts_only_from_a_confirmed_or_original_tree(self):
        self.init(heldout=self.held)
        self.set_speed(27)
        self.assertEqual(self.try_("an unconfirmed keep")[0], 0)
        with self.assertRaises(Refusal) as caught:
            self.rn("speedy-2")
        self.assertIn("never confirmed", str(caught.exception))
        self.assertEqual(self.confirm()[0], 0)
        self.assertEqual(self.rn("speedy-2")[0], 0)                       # HEAD is now the last confirmed commit

    def test_a_rename_with_a_different_heldout_set_is_refused(self):
        self.init(heldout=self.held)
        other = self.tmp / "other-held"
        other.mkdir()
        (other / "scale.txt").write_text("2.0\n")
        with self.assertRaises(Refusal) as caught:
            self.rn("speedy-2", heldout=str(other))
        self.assertIn("--heldout differs", str(caught.exception))
        self.assertEqual(self.rn("speedy-2", heldout=self.held)[0], 0)     # the same data is fine

    def test_the_chain_counts_against_the_confirm_cap(self):
        self.init(heldout=self.held)
        for i, (speed, name) in enumerate(((27, "speedy-2"), (24, "speedy-3"), (21, None))):
            self.set_speed(speed)
            self.assertEqual(self.try_(f"keep {i}")[0], 0)
            self.assertEqual(self.confirm()[0], 0)
            if name:
                self.assertEqual(self.rn(name)[0], 0)
        self.set_speed(19)
        self.assertEqual(self.try_("one more")[0], 0)
        with self.assertRaises(HcaError) as caught:
            self.confirm()
        self.assertIn("the confirm cap is reached (3 of 3 used, 2 of them under earlier names of this climb)", str(caught.exception))

    def test_an_empty_name_is_a_usage_error_and_the_hca_toml_name_is_no_rename(self):
        with self.assertRaises(HcaError) as caught:
            self.rn("", reset=False)
        self.assertIn("--name needs a task name", str(caught.exception))
        self.assertEqual(self.rn("speedy", reset=False)[0], 0)
        self.assertIsNone(self.state()["spec_task"])
        self.assertNotIn("renamed_from", ledger.load_store("speedy"))
        self.assertNotIn("renamed_to", ledger.load_store("speedy"))


class SecondReviewTests(ProofClimbFixTests):
    def test_t2_a_child_of_a_retired_member_leads_by_its_own_step(self):
        self.promoted_lineage()                                           # A promoted (keep 2, A retired), B screened on A
        def lead():
            a = explore.build(self.root, repokit_spec(self.root), self.state(), 9)
            return a["nodes"][3]["f_raw"] - a["f_inc"], a["nodes"][3]["step"], a["nodes"][3]["var"], a["nodes"][3]["se"]
        honest, step, var, se = lead()
        self.assertAlmostEqual(honest, step)
        self.assertAlmostEqual(var, se * se)                              # one open step: B's own
        ledger.update_entry("speedy", 2, estimate_log=self.entry(2)["estimate_log"] - 0.08)    # the referee read A weaker
        self.assertAlmostEqual(lead()[0], step)                           # B's lead does not inherit the screens' view of A

    def test_t3_why_is_one_short_line_stored_in_the_referee_store(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        why = "line1\nline2 \"quoted\" \\ back  \x1b[31mred\n" + "x" * 300000
        _, text = self.next_(parent=1, why=why)
        self.assertLess(len(text), 5000)
        self.repo.write("src/boost.txt", "1\n")
        self.screen("child")
        stored = self.entry(2)
        self.assertTrue(stored["override"])
        self.assertTrue(stored["why"].startswith('line1 line2 "quoted" \\ back [31mred xxx'))
        self.assertLessEqual(len(stored["why"]), 203)
        self.assertLess((ledger.attempt_path(self.root, 2) / "verdict.json").stat().st_size, 20000)
        before = [line for line in self.report()[1].splitlines() if line.startswith("Promotions")]
        record = ledger.attempt_path(self.root, 2) / "verdict.json"
        tampered = json.loads(record.read_text())
        tampered["explore"]["sampling"]["parent_p"] = 0.5
        record.write_text(json.dumps(tampered))
        self.assertEqual([line for line in self.report()[1].splitlines() if line.startswith("Promotions")], before)
        self.assertIn("parent overrides 1 of 1 screen(s) with sampler data", before[0])

    def test_the_first_screen_after_a_promotion_is_not_an_override(self):
        self.init()
        self.set_speed(27)
        self.screen("P")
        self.next_(parent="incumbent")
        self.assertEqual(self.promote(1)[0], 0)
        self.repo.write("src/boost.txt", "1\n")
        self.screen("right after the promotion")                          # 3
        self.assertIsNone(self.entry(3)["override"])

    def test_the_hint_never_suggests_a_member_whose_promotion_reverted(self):
        nodes = {1: {"f_raw": 0.05, "var": 0.0, "se": 0.0, "blocks": 5, "lineage": [1], "screened": True, "retired": False, "step": 0.05}}
        archive = {"elites": [1], "nodes": nodes, "floor": 0.01, "f_inc": 0.0, "screens": 2}
        self.assertEqual(explore.hint(archive, 5)["n"], 1)
        archive["reverted"] = [1]
        self.assertIsNone(explore.hint(archive, 5))

    def test_a_reverted_promotion_is_recorded_in_the_archive(self):
        self.init()
        self.set_speed("29.9")
        self.screen("tiny")                                               # 1
        self.next_(parent="incumbent")
        self.assertEqual(self.promote(1)[0], 1)                           # 2: reverts
        self.assertEqual(explore.build(self.root, repokit_spec(self.root), self.state())["reverted"], [1])

    def test_t4_a_tainted_promotion_never_brings_its_lineage_back_into_the_hint(self):
        held = self.tmp / "held-t4"
        held.mkdir()
        (held / "scale.txt").write_text("1.0\n")
        self.init(heldout=str(held))
        self.repo.write("src/dev_hack.txt", "0.7\n")
        self.screen("A (dev hack)")                                       # 1
        self.next_(parent="incumbent")
        self.assertEqual(self.promote(1)[0], 0)                           # 2
        self.next_(parent=1, why="build on A")
        self.repo.write("src/boost.txt", "1\n")
        self.set_speed(26)
        self.screen("B child of A")                                       # 3
        self.next_(parent="incumbent")
        self.assertEqual(self.confirm()[0], 1)
        archive = explore.build(self.root, repokit_spec(self.root), self.state(), 7)
        self.assertEqual((archive["tainted"], archive["elites"], explore.hint(archive, 8)), ([1, 3], [], None))
        self.assertEqual(self.promote(3, tainted=True)[0], 0)              # 4: a human-approved retest
        archive = explore.build(self.root, repokit_spec(self.root), self.state(), 7)
        self.assertEqual((archive["retired"], archive["elites"], explore.hint(archive, 8)), ([1, 3], [], None))

    def test_a_refusal_prints_once_on_a_shared_terminal(self):
        self.init()
        with contextlib.chdir(self.root), mock.patch.object(cli, "_same_terminal", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()) as shown, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["try", "--promote", "7", "-m", "x"]), 2)
        self.assertIn("REFUSED (exit 2)", shown.getvalue())
        self.assertEqual(err.getvalue(), "")
