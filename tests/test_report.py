import math
import unittest

import repokit
from hca import ledger
from hca.commands import report
from repokit import env


class ReportTests(repokit.RepoTestCase):
    toml_args = {"mechanism": '[mechanism]\nmetrics = ["size_kb"]\n'}

    def setUp(self):
        super().setUp()
        self.repo.write("src/size.txt", "100\n")
        self.repo.commit_all("size")

    def status(self):
        start = len(self.lines)
        code = report.run_status(None, out=self.out, cwd=self.root)
        return code, "\n".join(self.lines[start:])

    def test_report_before_any_attempt(self):
        self.init()
        code, text = self.report()
        self.assertEqual(code, 0)
        self.assertIn("Attempts\n  none yet", text)
        self.assertIn("No keeps yet.", text)
        self.assertIn("Validity rate: 0 of 0 tries valid", text)
        code, text = self.status()
        self.assertIn("tries 0 / 50", text)
        self.assertIn("confirms 0 / 5", text)

    def test_report_after_a_realistic_session(self):
        self.init()
        self.set_speed(21)
        self.repo.write("src/size.txt", "90\n")
        self.assertEqual(self.try_("shrink the loop")[0], 0)
        code, text = self.report()
        self.assertIn("No confirmed result yet. Unconfirmed cumulative -", text)        # a keep, but not yet confirmed
        self.assertEqual(self.confirm()[0], 0)
        self.set_speed("21.0000001")
        self.assertEqual(self.try_("reformat")[0], 1)
        self.repo.write("README.md", "oops\n")
        self.assertEqual(self.try_("docs")[0], 2)
        self.repo.git("checkout", "--", "README.md")
        with env(BENCH_JITTER="0.08"):
            self.set_speed(20.6)
            self.assertEqual(self.try_("micro-tune", seed=2)[0], 1)
        self.assertTrue(self.attempt(4)["includes_useful"], self.attempt(4))

        md_path = self.tmp / "report.md"
        code, text = self.report(md=str(md_path))
        self.assertEqual(code, 0)
        # headline and table
        self.assertIn("Confirmed: t_ms -", text)
        self.assertIn("cut of at least", text)
        for column in ("n", "verdict", "estimate", "p", "blocks", "wall", "message"):
            self.assertIn(column, text.splitlines()[text.splitlines().index("Attempts") + 1])
        self.assertRegex(text, r"0001\s+keep \(confirmed\)\s+-\d+\.\d%\s+0\.\d+\s+10\s+\d+\.\ds\s+shrink the loop")
        self.assertRegex(text, r"0002\s+revert\s")
        self.assertRegex(text, r"0003\s+invalid\s+-\s+-\s+-\s")
        self.assertIn("micro-tune", text)
        # validity: keep, no-op revert and micro-tune are valid; the scope violation is not
        self.assertIn("Validity rate: 3 of 4 tries valid (75%)", text)
        # mechanism metric for the kept attempt
        self.assertIn("0001: size_kb 100 -> 90 (-10.0%)", text)
        # the reminder, only for reverts that could not rule out the useful effect
        self.assertIn("No evidence is not no effect: 1 reverted attempt(s) (0004)", text)
        self.assertIn("Confirms used: 1 of 5.", text)
        self.assertIn("src/speed.txt", text)                              # diff summary
        self.assertIn("git checkout main && git merge hca/speedy", text)
        # markdown
        md = md_path.read_text()
        self.assertIn("# HCA report: speedy", md)
        self.assertIn("| n | verdict | estimate | p | blocks | wall | message |", md)
        self.assertIn("|---|---|---|---|---|---|---|", md)
        self.assertIn("| 0001 | keep (confirmed) |", md)
        self.assertIn("**Validity rate:** 3 of 4", md)
        self.assertIn("## No evidence is not no effect", md)
        self.assertIn("```\ngit checkout main\ngit merge hca/speedy\n```", md)
        # status
        code, status = self.status()
        self.assertIn("tries 4 / 50  (kept 1, valid 3)", status)
        self.assertIn("confirms 1 / 5", status)
        self.assertIn("last confirmed " + self.state()["last_confirmed"][:12], status)

    def test_verdicts_md_has_one_current_verdict_and_strikes_superseded_ones(self):
        self.init()
        self.set_speed(21)
        self.try_("first")
        self.set_speed(14)
        self.try_("second")
        verdicts = (self.root / ".hca" / "VERDICTS.md").read_text()
        self.assertEqual(verdicts.count("## Current verdict"), 1)
        current = verdicts.split("## Current verdict")[1].split("##")[0]
        self.assertIn("2 keep(s)", current)
        self.assertNotIn("~~", current)
        superseded = verdicts.split("## Superseded")[1].split("##")[0]
        self.assertIn("~~No keeps yet.~~", superseded)
        self.assertIn("~~No confirmed result yet. Unconfirmed cumulative", superseded)
        self.assertIn("1 keep(s)", superseded)
        self.confirm()
        verdicts = (self.root / ".hca" / "VERDICTS.md").read_text()
        current = verdicts.split("## Current verdict")[1].split("##")[0]
        self.assertIn("Confirmed: t_ms -", current)
        self.assertIn("~~No confirmed result yet. Unconfirmed cumulative", verdicts.split("## Superseded")[1])
        self.assertIn("- 0001 keep", verdicts)

    def test_cumulative_unconfirmed_delta_compounds_the_per_try_estimates(self):
        self.init()
        self.set_speed(21)
        self.try_("first")
        self.set_speed(14)
        self.try_("second")
        a1, a2 = self.attempt(1), self.attempt(2)
        head = ledger.headline(self.state(), ledger.read_attempts(self.root))
        expected = (1 + a1["estimate"] / 100) * (1 + a2["estimate"] / 100) - 1
        self.assertAlmostEqual(head["cumulative"] / 100, expected, places=6)
        self.assertLess(head["cumulative"], -50)                         # 30 -> 14 is a 53% cut


class FormatTests(unittest.TestCase):
    def test_changes_read_in_the_metrics_own_terms_with_a_better_or_worse_word(self):
        f = ledger.fmt_change
        self.assertEqual(f(-30.0, "blocks", "min", "t_ms"), "t_ms -30.0% (better)")
        self.assertEqual(f(4.2, "blocks", "min", "t_ms"), "t_ms +4.2% (worse)")
        self.assertEqual(f(3.2, "blocks", "max", "accuracy"), "accuracy +3.2% (better)")
        self.assertEqual(f(-1.0, "blocks", "max", "accuracy"), "accuracy -1.0% (worse)")
        self.assertEqual(f(0.0, "blocks", "max", "accuracy"), "accuracy +0.0% (unchanged)")
        self.assertEqual(f(0.0875, "items", "max", "acc"), "+0.0875")           # items mode: a plain score difference

    def test_bounds_say_how_much_improvement_is_established(self):
        b = ledger.fmt_bound
        self.assertEqual(b(-27.1, "blocks", "min"), "cut of at least 27.1%")
        self.assertEqual(b(1.9, "blocks", "max"), "gain of at least 1.9%")
        self.assertEqual(b(1.4, "blocks", "min"), "+1.4%, no improvement shown")   # the bound for a time that might be worse
        self.assertEqual(b(-0.5, "blocks", "max"), "-0.5%, no improvement shown")
        self.assertEqual(b(0.0642, "items", "max"), "at least +0.0642")
        self.assertEqual(b(None, "blocks", "min"), "n/a")

    def test_a_cumulative_compounds_in_the_direction_of_the_metric(self):
        cut = math.log(1 / 0.7)
        self.assertAlmostEqual(ledger.cumulative("blocks", [cut, cut], "min"), -51.0)      # 0.7 * 0.7 = 0.49
        self.assertAlmostEqual(ledger.cumulative("blocks", [math.log(1.1)] * 2, "max"), 21.0)
        self.assertAlmostEqual(ledger.cumulative("items", [0.1, 0.2], "max"), 0.3)


class ItemsReportTests(repokit.RepoTestCase):
    mode = "items"
    toml_args = {"mechanism": '[mechanism]\nslices = ["group"]\n'}

    def test_report_in_items_mode_shows_score_units_and_slices_from_the_last_confirm(self):
        self.init()
        self.repo.write("src/boost.txt", "0.1\n")
        self.try_("better")
        self.repo.write("src/boost.txt", "0.1001\n")
        self.try_("noise")                                                # reverted: below the floor
        self.confirm()
        code, text = self.report()
        self.assertEqual(code, 0)
        self.assertIn("items mode", text)
        self.assertIn("Confirmed: +0.0875 vs the original", text)
        self.assertRegex(text, r"0001\s+keep \(confirmed\)\s+\+0\.0875\s+-\s+1\s")
        self.assertIn("Slices from confirm 1", text)
        self.assertIn("group=even:", text)
        self.assertIn("group=odd:", text)
        self.assertIn("(1 skipped)", text)                                # the zero-scoring item has no log ratio


if __name__ == "__main__":
    unittest.main()
