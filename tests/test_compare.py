import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from hca import cli
from hca.commands import compare
from hca.core import HcaError

# A fake bench: prints `METRIC lat=<base * exp(amp * u)>` where u is seeded by the run number (a counter file in the
# cwd), so a scenario is deterministic and instant. `val.txt` in the cwd overrides the base; `order.txt` logs which
# command ran, in order; HCA_RESULT_FILE (when set and the third argument is "file") receives the metric instead of stdout.
BENCH = '''
import math, os, random, sys
label, base, amp = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
mode = sys.argv[4] if len(sys.argv) > 4 else "stdout"
n = int(open("count").read()) + 1 if os.path.exists("count") else 1
open("count", "w").write(str(n))
open("order.txt", "a").write(label)
if os.path.exists("val.txt"):
    base = float(open("val.txt").read())
line = "METRIC lat=%.9f" % (base * math.exp(amp * random.Random(n * 7919).uniform(-1, 1)))
if mode == "file":
    open(os.environ["HCA_RESULT_FILE"], "w").write(line + "\\n")
    print("METRIC lat=1")
else:
    print("noise before")
    print("METRIC other=7")
    print(line)
'''


class CompareTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hca-compare-test-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        (self.tmp / "bench.py").write_text(BENCH)

    def bench(self, label, base, amp=0.01, mode="stdout"):
        return [sys.executable, "-S", str(self.tmp / "bench.py"), label, str(base), str(amp), mode]

    def run_compare(self, a, b, cwd=None, progress=None, **opts):
        values = dict(metric="lat", direction="min", min_effect=None, blocks=None, extend_to=None, out=None, warmup=1, cwd_a=None, cwd_b=None,
                      timeout=60.0, json=False, seed=1, command=list(a) + ["--"] + list(b))
        values.update(opts)
        lines = []
        values.setdefault("sep", "--")
        code = compare.run(Namespace(**values), out=lines.append, cwd=cwd or self.tmp, progress=progress or (lambda line: None))
        return code, "\n".join(lines)

    def as_json(self, a, b, **opts):
        code, text = self.run_compare(a, b, json=True, **opts)
        return code, json.loads(text)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    # --- verdicts and exit codes ------------------------------------------------------------------

    def test_a_clear_win_for_b_exits_0_with_a_percentage_interval_and_p(self):
        code, text = self.run_compare(self.bench("A", 100), self.bench("B", 80))
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"B is 2\d\.\d% lower \(better\) than A \(99% CI \d+\.\d–\d+\.\d%, p=0\.002, 10 blocks\)")
        self.assertRegex(text, r"Noise: ±\d\.\d% per block \(A's own runs spread ±\d\.\d%\); this N detects about \d\.\d%\.")
        self.assertIn("`hca init`", text)
        self.assertNotIn("Keep rule", text)                           # no --min-effect: report only

    def test_wall_time_is_the_default_metric_and_reads_faster(self):
        sleeper = lambda s: [sys.executable, "-S", "-c", f"import time; time.sleep({s})"]
        code, text = self.run_compare(sleeper(0.04), sleeper(0.02), metric=None, blocks=8, warmup=1)
        self.assertEqual(code, 0, text)
        self.assertIn("wall time (wall_s)", text)
        self.assertRegex(text, r"B is \d+\.\d% faster than A \(\d\.\d\d× the speed, 98% CI")
        self.assertIn(" s, B ", text)

    def test_identical_commands_show_no_detectable_difference_and_exit_1(self):
        code, text = self.run_compare(self.bench("A", 100, 0.02), self.bench("B", 100, 0.02))
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r"No detectable difference \(estimate [+-]\d\.\d%, 99% CI [+-]\d\.\d to [+-]\d\.\d%, p=0\.\d+, which would need to be ≤ 0\.01\); this N detects about \d\.\d%\.")

    def test_a_faster_than_b_exits_1_and_says_so(self):
        code, text = self.run_compare(self.bench("A", 80), self.bench("B", 100))
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r"B is \d+\.\d% higher \(worse\) than A")
        code, data = self.as_json(self.bench("A", 80), self.bench("B", 100))
        self.assertEqual((code, data["verdict"], data["keep"]), (1, "a_better", False))

    def test_direction_max_means_higher_is_better(self):
        code, text = self.run_compare(self.bench("A", 100), self.bench("B", 120), direction="max")
        self.assertEqual(code, 0, text)
        self.assertRegex(text, r"B is \d\d\.\d% higher \(better\) than A")
        code, text = self.run_compare(self.bench("A", 120), self.bench("B", 100), direction="max")
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r"B is \d+\.\d% lower \(worse\) than A")

    def test_the_metric_line_is_parsed_from_noisy_stdout_and_the_result_file_wins(self):
        code, data = self.as_json(self.bench("A", 50, 0.001), self.bench("B", 40, 0.001))
        self.assertEqual(code, 0)
        self.assertAlmostEqual(data["a"]["median"], 50, delta=0.2)          # `METRIC other=7` and the chatter are ignored
        self.assertAlmostEqual(data["b"]["median"], 40, delta=0.2)
        code, data = self.as_json(self.bench("A", 50, 0.001, "file"), self.bench("B", 40, 0.001, "file"))
        self.assertEqual(code, 0)
        self.assertAlmostEqual(data["a"]["median"], 50, delta=0.2)          # stdout said lat=1 but HCA_RESULT_FILE is the evaluator's channel
        self.assertEqual(data["metric"], "lat")

    def test_each_command_runs_in_its_own_cwd(self):
        for name, value in (("a", 100), ("b", 70)):
            (self.tmp / name).mkdir()
            (self.tmp / name / "val.txt").write_text(str(value))
        same = self.bench("X", 1, 0.001)
        code, data = self.as_json(same, same, cwd_a=str(self.tmp / "a"), cwd_b=str(self.tmp / "b"))
        self.assertEqual(code, 0)
        self.assertAlmostEqual(data["a"]["median"], 100, delta=0.5)
        self.assertAlmostEqual(data["b"]["median"], 70, delta=0.5)
        self.assertTrue((self.tmp / "a" / "count").is_file() and (self.tmp / "b" / "count").is_file())
        with self.assertRaises(HcaError):
            self.run_compare(same, same, cwd_a=str(self.tmp / "nope"))

    # --- the design: warm-up, order, looks ---------------------------------------------------------

    def test_warmup_runs_are_discarded_and_the_order_is_randomised_within_blocks(self):
        code, data = self.as_json(self.bench("A", 100), self.bench("B", 90), blocks=8, warmup=2)
        self.assertEqual((data["blocks"], data["looks"], data["extended"], data["warmup"]), (8, 1, False, 2))
        self.assertEqual(int((self.tmp / "count").read_text()), 2 * (2 + 8))
        order = (self.tmp / "order.txt").read_text()
        pairs = [order[i:i + 2] for i in range(4, len(order), 2)]            # skip the two warm-up blocks
        self.assertTrue(all(sorted(p) == ["A", "B"] for p in pairs), order)  # every block runs A and B once
        self.assertEqual(set(pairs), {"AB", "BA"})                           # in both orders
        self.assertEqual(len(data["a"]["values"]), 8)

    def test_a_promising_but_unproven_result_extends_once_to_20_blocks(self):
        code, data = self.as_json(self.bench("A", 100, 0.05), self.bench("B", 98, 0.05), seed=3)
        self.assertEqual((data["blocks"], data["looks"], data["extended"]), (20, 2, True), data)
        self.assertEqual(len(data["scores"]), 20)

    def test_a_decisive_result_stops_at_the_first_look(self):
        code, data = self.as_json(self.bench("A", 100), self.bench("B", 70))
        self.assertEqual((data["blocks"], data["extended"]), (10, False))

    def test_fixed_blocks_is_one_look_at_the_one_sided_1_percent_level(self):
        code, data = self.as_json(self.bench("A", 100), self.bench("B", 60), blocks=7)
        self.assertEqual(code, 0)
        self.assertEqual((data["blocks"], data["looks"], data["alpha_one_sided"], data["ci_level"]), (7, 1, 0.01, 0.98))
        with self.assertRaises(HcaError) as caught:
            self.run_compare(self.bench("A", 100), self.bench("B", 60), blocks=6)
        self.assertIn("at least 7", str(caught.exception))

    def test_two_sided_json_says_better_worse_or_neither(self):
        _, up = self.as_json(self.bench("A", 100), self.bench("B", 80))
        _, down = self.as_json(self.bench("A", 80), self.bench("B", 100))
        _, same = self.as_json(self.bench("A", 100, 0.02), self.bench("B", 100, 0.02))
        self.assertEqual([(d["better"], d["worse"]) for d in (up, down, same)], [(True, False), (False, True), (False, False)])
        self.assertEqual([d["exit"] for d in (up, down, same)], [0, 1, 1])

    def test_extend_to_sets_the_second_look(self):
        _, data = self.as_json(self.bench("A", 100, 0.05), self.bench("B", 98, 0.05), seed=3, blocks=10, extend_to=14)
        self.assertEqual((data["blocks"], data["looks"], data["extended"]), (14, 2, True), data)
        _, data = self.as_json(self.bench("A", 100), self.bench("B", 70), blocks=8, extend_to=12)
        self.assertEqual((data["blocks"], data["looks"], data["extended"]), (8, 2, False))     # decisive at the first look
        for bad in (dict(blocks=10, extend_to=10), dict(blocks=7, extend_to=12)):
            with self.assertRaises(HcaError):
                self.run_compare(self.bench("A", 100), self.bench("B", 80), **bad)
        code, out, err = self.cli("compare", "--extend-to", "5", "--", "true", "--", "true")
        self.assertEqual(code, 3)

    def test_out_dir_keeps_the_report_and_every_raw_run(self):
        out = self.tmp / "keep"
        self.run_compare(self.bench("A", 100), self.bench("B", 80), blocks=8, out=str(out))
        report = json.loads((out / "compare.json").read_text())
        runs = [json.loads(line) for line in (out / "runs.jsonl").read_text().splitlines()]
        runs = [r for r in runs if r["kind"] == "run" and r["block"] >= 0]
        self.assertEqual(len(runs), 16)
        self.assertEqual(sorted(r["value"] for r in runs if r["arm"] == "baseline"), sorted(report["a"]["values"]))
        self.assertTrue(all("order" in r and "wall_seconds" in r for r in runs))

    def test_wall_time_with_direction_max_is_refused(self):
        with self.assertRaises(HcaError) as caught:
            self.run_compare(self.bench("A", 100), self.bench("B", 80), metric=None, direction="max")
        self.assertIn("lower-is-better", str(caught.exception))
        self.assertIn("--metric NAME", str(caught.exception))
        code, out, err = self.cli("compare", "--direction", "max", "--", "true", "--", "true")
        self.assertEqual(code, 3)

    def test_an_invalid_compare_still_writes_the_out_dir(self):
        out = self.tmp / "failed"
        crash = [sys.executable, "-S", "-c", "import sys; sys.stderr.write('kaboom\n'); sys.exit(4)"]
        code, _ = self.run_compare(self.bench("A", 100), crash, out=str(out), warmup=0)
        self.assertEqual(code, 2)
        report = json.loads((out / "compare.json").read_text())
        self.assertEqual((report["verdict"], report["command"], report["exit"]), ("invalid", "B", 2))
        self.assertIn("kaboom", report["error_tail"])
        self.assertIn('"ok": false', (out / "runs.jsonl").read_text())

    def test_wording_ratio_zero_noise_and_the_threshold(self):
        _, text = self.run_compare(self.bench("A", 100), self.bench("B", 80))
        self.assertRegex(text, r"lower \(better\) than A \(")
        sleeper = lambda s: [sys.executable, "-S", "-c", f"import time; time.sleep({s})"]
        _, text = self.run_compare(sleeper(0.04), sleeper(0.02), metric=None, blocks=8)
        self.assertRegex(text, r"faster than A \(\d\.\d\d× the speed, ")
        flat = [sys.executable, "-S", "-c", "print('METRIC lat=5')"]
        _, text = self.run_compare(flat, flat)
        self.assertIn("Noise: no variation seen", text)
        self.assertNotIn("detects about 0.0%", text)
        self.assertIn("No detectable difference", text)
        _, text = self.run_compare(self.bench("A", 100, 0.02), self.bench("B", 100, 0.02))
        self.assertIn("which would need to be ≤ 0.01", text)

    def test_progress_goes_to_the_callback_not_the_report(self):
        seen = []
        _, text = self.run_compare(self.bench("A", 100), self.bench("B", 70), blocks=8, progress=seen.append)
        self.assertEqual(seen[0].split(",")[0], "warm-up 1/1")
        self.assertEqual([s.split(",")[0] for s in seen[1:]], [f"block {i}/8" for i in range(1, 9)])
        self.assertNotIn("elapsed", text)

    def test_sep_lets_command_a_contain_double_dash(self):
        a = self.bench("A", 100) + ["--"]
        b = self.bench("B", 80)
        args = Namespace(command=a + ["::"] + b, sep="::")
        self.assertEqual(compare.split_commands(args), (a, b))
        with self.assertRaises(HcaError) as caught:
            compare.split_commands(Namespace(command=a, sep="::"))
        self.assertIn("`::`", str(caught.exception))
        with self.assertRaises(HcaError) as caught:
            compare.split_commands(Namespace(command=["x"], sep="--"))
        self.assertIn("may not itself contain `--`", str(caught.exception))

    # --- --min-effect: the keep floor is half the useful effect, plus significance --------------------

    def min_effect(self, text):
        # B is 4% lower than A with almost no noise: estimate about 4%
        return self.run_compare(self.bench("A", 100, 0.0005), self.bench("B", 96, 0.0005), min_effect=text)

    def test_min_effect_floor_boundary(self):
        code, text = self.min_effect("6%")                                  # floor 3%: a 4% win clears it, though it is under 6%
        self.assertEqual(code, 0, text)
        self.assertIn("Keep rule (--min-effect 6%): B beats A by at least that much: yes", text)
        self.assertRegex(text, r"keep floor of 3\.\d%")
        code, text = self.min_effect("10%")                                 # floor 5%: the same win is not enough
        self.assertEqual(code, 1, text)
        self.assertIn("B beats A by at least that much: no", text)
        self.assertRegex(text, r"below the keep floor of 5\.\d% \(half of 10%\)")
        code, data = self.as_json(self.bench("A", 100, 0.0005), self.bench("B", 96, 0.0005), min_effect="10%")
        self.assertEqual(data["verdict"], "b_better")                       # significantly better, yet below the floor
        self.assertEqual((data["keep"], data["min_effect"]["passes"]), (False, False))
        self.assertAlmostEqual(data["min_effect"]["keep_floor_pct"], 5.1, delta=0.05)         # half the useful effect, in log terms

    def test_min_effect_also_needs_significance(self):
        code, text = self.run_compare(self.bench("A", 100, 0.05), self.bench("B", 100, 0.05), min_effect="1%", seed=5)
        self.assertEqual(code, 1, text)
        self.assertIn("no (not significant", text)

    # --- invalid runs --------------------------------------------------------------------------------

    def test_a_missing_metric_is_invalid_exit_2(self):
        silent = [sys.executable, "-S", "-c", "print('METRIC other=1')"]
        code, text = self.run_compare(self.bench("A", 100), silent)
        self.assertEqual(code, 2, text)
        self.assertIn("Invalid", text)
        self.assertIn("command B", text)
        self.assertIn("lat", text)

    def test_a_failing_command_is_invalid_with_its_stderr_tail(self):
        crash = [sys.executable, "-S", "-c", "import sys; sys.stderr.write('boom: bad input\\n'); sys.exit(3)"]
        code, text = self.run_compare(crash, self.bench("B", 100))
        self.assertEqual(code, 2, text)
        self.assertIn("command A failed", text)
        self.assertIn("exit status 3", text)
        self.assertIn("boom: bad input", text)
        code, text = self.run_compare(self.bench("A", 100), crash, warmup=0)           # also without a warm-up to catch it
        self.assertEqual(code, 2, text)
        self.assertIn("command B failed in block 1", text)
        code, text = self.run_compare(self.bench("A", 100), ["no-such-binary-hca-test"])
        self.assertEqual(code, 2, text)
        self.assertIn("could not start", text)

    def test_a_non_positive_metric_is_invalid(self):
        zero = [sys.executable, "-S", "-c", "print('METRIC lat=0')"]
        code, text = self.run_compare(self.bench("A", 100), zero)
        self.assertEqual(code, 2, text)
        self.assertIn("not positive", text)

    # --- --json ---------------------------------------------------------------------------------------

    def test_json_shape(self):
        code, data = self.as_json(self.bench("A", 100), self.bench("B", 80), min_effect="5%")
        self.assertEqual(code, 0)
        self.assertEqual(set(data), {"verdict", "better", "worse", "p", "keep", "exit", "metric", "direction", "wall_time", "blocks", "looks", "extended", "seed",
                                     "warmup", "estimate_pct", "ci_pct", "ci_level", "p_value", "alpha_one_sided", "noise", "min_effect",
                                     "a", "b", "scores"})
        self.assertEqual((data["verdict"], data["keep"], data["exit"], data["metric"], data["direction"], data["wall_time"], data["seed"]),
                         ("b_better", True, 0, "lat", "min", False, 1))
        self.assertEqual((data["better"], data["worse"], data["p"]), (True, False, data["p_value"]))
        self.assertLess(data["estimate_pct"], -15)                         # B's lat is about 20% lower
        self.assertEqual(len(data["ci_pct"]), 2)
        self.assertLess(data["ci_pct"][0], data["ci_pct"][1])
        self.assertLessEqual(data["ci_pct"][1], 0)
        self.assertEqual(set(data["noise"]), {"block_sd_pct", "a_run_sd_pct", "detectable_pct"})
        self.assertEqual((data["min_effect"]["requested"], data["min_effect"]["passes"]), ("5%", True))
        self.assertAlmostEqual(data["min_effect"]["keep_floor_pct"], 2.5, delta=0.05)
        self.assertEqual(set(data["a"]), {"argv", "median", "values"})
        self.assertEqual(len(data["scores"]), data["blocks"])
        code, data = self.as_json(self.bench("A", 100), ["no-such-binary-hca-test"])
        self.assertEqual((code, data["verdict"], data["exit"], data["command"]), (2, "invalid", 2, "B"))

    # --- the command line ---------------------------------------------------------------------------------

    def test_cli_splits_a_and_b_at_the_first_double_dash(self):
        with contextlib.chdir(self.tmp):
            code, out, err = self.cli("compare", "--metric", "lat", "--blocks", "7", "--warmup", "0", "--seed", "2", "--json", "--",
                                      *self.bench("A", 100, 0.001), "--", *self.bench("B", 60, 0.001))
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["b"]["argv"][-4:], ["B", "60", "0.001", "stdout"])
        a, b = compare.split_commands(Namespace(command=["x", "-v", "--", "y", "--", "z"]))       # B may contain further `--`
        self.assertEqual((a, b), (["x", "-v"], ["y", "--", "z"]))

    def test_usage_errors_exit_3(self):
        cases = [("compare", "--", "true"),                                  # no second --
                 ("compare", "--", "true", "--"),                            # B empty
                 ("compare", "--", "--", "true"),                            # A empty
                 ("compare", "--", "--"),
                 ("compare",),
                 ("compare", "stray", "--", "true", "--", "true"),           # something before the first --
                 ("compare", "--blocks", "3", "--", "true", "--", "true"),
                 ("compare", "--blocks", "ten", "--", "true", "--", "true"),   # argparse's own error is 3 too, never 2 (= invalid)
                 ("compare", "--min-effect", "fast", "--", "true", "--", "true"),
                 ("compare", "--direction", "up", "--", "true", "--", "true"),
                 ("quick", "--blocks", "ten", "--", "true")]
        for argv in cases:
            code, out, err = self.cli(*argv)
            self.assertEqual(code, 3, (argv, out, err))
            self.assertEqual(out, "")
            self.assertTrue(err.strip(), argv)

    def test_help_lists_compare(self):
        code, out, _ = self.cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("compare", out)
        code, out, _ = self.cli("compare", "--help")
        self.assertIn("hca compare [options] -- <command A ...> -- <command B ...>", out)
        for option in ("--metric", "--direction", "--min-effect", "--blocks", "--warmup", "--cwd-a", "--cwd-b", "--timeout", "--json", "--seed"):
            self.assertIn(option, out)


if __name__ == "__main__":
    unittest.main()
