import json
import math
import random
import statistics
import unittest

from hca import measure, spec, stats
from testkit import ArmTestCase


def metric(name, value):
    return f"METRIC {name}={value}\n"


def items_json(**values):
    """One output line of item JSON; each kwarg is id=value or id=(value, status)."""
    rows = []
    for ident, value in values.items():
        value, status = value if isinstance(value, tuple) else (value, "valid")
        rows.append({"id": ident, "runs": [value, value], "status": status, "tags": {"kind": ident[0]},
                     "mechanism": {"bytes": value * 10}})
    return json.dumps({"metric": "lat", "items": rows}) + "\n"


class ParseTests(unittest.TestCase):
    def test_metric_lines(self):
        text = "loading...\nMETRIC lat=212.5\nMETRIC bytes_sent=4.8e4\n  METRIC odd-name.x=1  \nnot METRIC a=1\n"
        parsed = measure.parse_output(text, "lat")
        self.assertEqual((parsed.kind, parsed.value, parsed.problem), ("metrics", 212.5, None))
        self.assertEqual(parsed.metrics, {"lat": 212.5, "bytes_sent": 48000.0, "odd-name.x": 1.0})

    def test_metric_problems(self):
        self.assertIn("no METRIC line named 'lat'", measure.parse_output("METRIC other=1\n", "lat").problem)
        self.assertIn("non-numeric", measure.parse_output("METRIC lat=fast\n", "lat").problem)
        self.assertEqual(measure.parse_output("just logs\n", "lat").kind, "none")
        self.assertIsNone(measure.parse_output("", "lat").problem)
        self.assertEqual(measure.parse_output("METRIC lat=1\nMETRIC lat=2\n", "lat").value, 2.0)   # last wins
        self.assertTrue(math.isnan(measure.parse_output("METRIC lat=nan\n", "lat").value))

    def test_item_json_on_the_last_line(self):
        text = "warming up\n" + items_json(a=10, b=(20, "infra_invalid"), c=(5, "candidate_invalid"))
        parsed = measure.parse_output(text, "lat")
        self.assertEqual((parsed.kind, parsed.problem, parsed.metric), ("items", None, "lat"))
        a, b, c = parsed.items
        self.assertEqual((a.value, a.status, a.tags, a.mechanism), (10, "valid", {"kind": "a"}, {"bytes": 100}))
        self.assertEqual((b.status, c.status), ("infra_invalid", "candidate_invalid"))

    def test_item_json_wins_over_metric_lines_and_score_form_works(self):
        line = json.dumps({"items": [{"id": "x", "score": 3.5}, {"id": 7, "runs": [1, 5, 2]}]})
        parsed = measure.parse_output("METRIC lat=1\n" + line, "lat")
        self.assertEqual([(i.id, i.value) for i in parsed.items], [("x", 3.5), ("7", 2)])
        self.assertEqual(measure.parse_output(line + "\nMETRIC lat=1\n", "lat").kind, "metrics")   # JSON is not last

    def test_malformed_item_json_is_a_problem_not_a_crash(self):
        cases = {
            '{"items": [{"id": "a", "runs": [1, 2}': "does not parse",
            '{"items": []}': "non-empty",
            '{"items": [{"runs": [1]}]}': "string `id`",
            '{"items": [{"id": "a"}]}': "needs `runs`",
            '{"items": [{"id": "a", "runs": []}]}': "non-empty list of numbers",
            '{"items": [{"id": "a", "runs": ["1"]}]}': "list of numbers",
            '{"items": [{"id": "a", "score": 1, "status": "bad"}]}': "status",
            '{"items": [{"id": "a", "score": 1}, {"id": "a", "score": 2}]}': "duplicate",
            '{"metric": "other", "items": [{"id": "a", "score": 1}]}': "objective.metric",
        }
        for text, needle in cases.items():
            parsed = measure.parse_output(text, "lat")
            self.assertIsNotNone(parsed.problem, text)
            self.assertIn(needle, parsed.problem, text)
            self.assertEqual(parsed.kind, "none")
        self.assertIsNone(measure.parse_output('{"items": [{"id": "a", "status": "infra_invalid"}]}', "lat").problem)


class BlockTests(ArmTestCase):
    def blocks(self, n=10, direction="min", seed=1, metric_name="lat", **kwargs):
        return measure.measure_blocks(self.command, metric_name, direction, self.base.path, self.cand.path, n,
                                      random.Random(seed), {}, self.out_dir, 20, **kwargs)

    def records(self, kind=None):
        rows = [json.loads(line) for line in (self.out_dir / "runs.jsonl").read_text().splitlines()]
        return [r for r in rows if kind is None or r["kind"] == kind]

    def test_scalar_blocks_score_the_log_ratio_in_the_right_direction(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 80))
        result = self.blocks()
        self.assertEqual(len(result.scores), 10)
        for score in result.scores:
            self.assertAlmostEqual(score, math.log(100 / 80))
        self.assertFalse(result.void)
        self.assertEqual(result.arm_medians, {"baseline": {"lat": 100.0}, "candidate": {"lat": 80.0}})
        self.assertEqual((result.voided_blocks, result.candidate_failures), ([], 0))
        self.assertAlmostEqual(self.blocks(direction="max", seed=2).scores[0], -math.log(100 / 80))

    def test_run_blocks_takes_a_spec_and_continues_block_numbers(self):
        s = spec.parse_spec({"task": {"name": "t", "editable": ["src/**"]},
                             "objective": {"command": list(self.command), "metric": "lat", "direction": "min",
                                           "useful_effect": "5%"}, "limits": {"command_seconds": 20}})
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 90) + metric("bytes", 7))
        first = measure.run_blocks(s, self.base.path, self.cand.path, 10, random.Random(3), {"HCA_ITEMS": "dev"}, self.out_dir)
        more = measure.run_blocks(s, self.base.path, self.cand.path, 3, random.Random(4), {}, self.out_dir, first_block=10)
        self.assertEqual(first.scored_blocks, list(range(10)))
        self.assertEqual(more.scored_blocks, [10, 11, 12])
        self.assertEqual(first.arm_medians["candidate"]["bytes"], 7.0)
        stats.block_test(first.scores + more.scores)          # feeds the referee unchanged

    def test_arm_order_is_randomised_and_logged(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 100))
        result = self.blocks(n=24, seed=7)
        runs = self.records("run")
        self.assertEqual(len(runs), 48)
        first_arms = [next(r["arm"] for r in runs if r["block"] == b and r["order"] == 0) for b in range(24)]
        self.assertEqual(set(first_arms), {"baseline", "candidate"})
        self.assertTrue(6 <= first_arms.count("baseline") <= 18)
        self.assertEqual(first_arms, [next(r["arm"] for r in self.records("run") if r["block"] == b and r["order"] == 0) for b in range(24)])
        self.assertTrue(all(set(r) >= {"block", "arm", "order", "wall_seconds", "loadavg", "value", "ok", "returncode"} for r in runs))
        self.assertEqual(len(result.scores), 24)

    def test_same_seed_gives_the_same_order(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 100))

        def orders(seed):
            self.out_dir = self.tmp / f"out{seed}-{random.random()}"
            self.blocks(n=6, seed=seed)
            return [r["arm"] for r in self.records("run") if r["order"] == 0]

        self.assertEqual(orders(5), orders(5))

    def test_candidate_crash_scores_as_the_worst_observed_value(self):
        self.base.say(metric("lat", 100))
        self.base.say(metric("lat", 120), block=6)            # the worst value seen in the try
        self.cand.say(metric("lat", 80))
        self.cand.fail(2, err="Traceback ...\nValueError: boom\n")
        result = self.blocks()
        self.assertEqual(result.candidate_failures, 1)
        self.assertEqual(result.candidate_invalid_blocks, [2])
        self.assertAlmostEqual(result.scores[2], math.log(100 / 120))        # worse than baseline: a loss, never a win
        self.assertLess(result.scores[2], 0)
        self.assertAlmostEqual(result.scores[0], math.log(100 / 80))
        self.assertAlmostEqual(result.scores[6], math.log(120 / 80))
        self.assertFalse(result.void)
        bad = [r for r in self.records("run") if not r["ok"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("ValueError: boom", bad[0]["error_tail"])
        self.assertEqual(bad[0]["returncode"], 1)

    def test_candidate_crash_for_a_max_metric_scores_as_the_minimum(self):
        self.base.say(metric("lat", 100))
        self.base.say(metric("lat", 60), block=1)
        self.cand.say(metric("lat", 130))
        self.cand.fail(0)
        result = self.blocks(direction="max")
        self.assertAlmostEqual(result.scores[0], math.log(60 / 100))          # candidate := 60 (worst for max)
        self.assertLess(result.scores[0], 0)

    def test_bad_values_are_invalid_runs(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 90))
        self.cand.say(metric("lat", 0), block=1)
        self.cand.say("no metric here\n", block=3)
        self.cand.say(metric("lat", "nan"), block=5)
        result = self.blocks()
        self.assertEqual(result.candidate_failures, 3)
        self.assertEqual(result.candidate_invalid_blocks, [1, 3, 5])
        self.assertEqual(len(result.scores), 10)
        for index in (1, 3, 5):
            self.assertAlmostEqual(result.scores[index], 0.0)       # worst seen was the baseline's 100

    def test_baseline_failure_voids_the_block_loudly(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 90))
        self.base.fail(4)
        result = self.blocks()
        self.assertEqual(result.voided_blocks, [4])
        self.assertEqual(len(result.scores), 9)
        self.assertNotIn(4, result.scored_blocks)
        self.assertFalse(result.void)
        self.assertTrue(any("block 4 voided" in w and "baseline" in w for w in result.warnings))

    def test_more_than_thirty_percent_voided_blocks_voids_the_try(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 90))
        for b in (0, 1, 2):
            self.base.fail(b)
        three = self.blocks()
        self.assertEqual((len(three.voided_blocks), three.void), (3, False))      # exactly 30% is allowed
        self.base.fail(3)
        self.out_dir = self.tmp / "out2"
        (self.base.path / "count").unlink()
        (self.cand.path / "count").unlink()
        four = self.blocks()
        self.assertEqual((len(four.voided_blocks), four.void), (4, True))
        self.assertTrue(any("try void" in w for w in four.warnings))

    def test_item_blocks_use_the_median_item_log_ratio_and_collect_slices(self):
        self.base.say(items_json(a1=10, a2=20, b1=40))
        self.cand.say(items_json(a1=5, a2=10, b1=40))
        result = self.blocks()
        expected = statistics.median([math.log(2), math.log(2), 0.0])
        self.assertTrue(all(abs(s - expected) < 1e-12 for s in result.scores))
        self.assertEqual(result.item_medians["a1"], {"baseline": 10.0, "candidate": 5.0, "tags": {"kind": "a"}})
        self.assertEqual(result.item_medians["b1"]["tags"], {"kind": "b"})
        self.assertEqual(result.arm_medians["baseline"]["bytes"], 200.0)          # median over items of the mechanism key
        self.assertEqual(result.arm_medians["baseline"]["lat"], 20.0)

    def test_candidate_invalid_item_scores_as_that_items_worst_observed(self):
        self.base.say(items_json(a=40, b=20))
        self.cand.say(items_json(a=10, b=10))                              # a: 4x better, b: 2x better
        self.cand.say(items_json(a=(10, "candidate_invalid"), b=10), block=0)
        result = self.blocks()
        self.assertAlmostEqual(result.scores[1], (math.log(4) + math.log(2)) / 2)
        self.assertAlmostEqual(result.scores[0], (0.0 + math.log(2)) / 2)         # a := 40, the worst a seen
        self.assertEqual(result.candidate_invalid_blocks, [0])

    def test_infra_invalid_items_are_dropped_from_both_arms(self):
        ids = [f"i{k}" for k in range(10)]
        self.base.say(items_json(**{k: 10 for k in ids}))
        self.cand.say(items_json(**{k: 5 for k in ids}))
        one = {k: 5 for k in ids}
        one["i0"] = (999, "infra_invalid")                                  # junk value must not count anywhere
        self.cand.say(items_json(**one), block=2)
        result = self.blocks()
        self.assertEqual(result.voided_blocks, [])                           # 1 of 10 = 10%, not more than 10%
        self.assertAlmostEqual(result.scores[2], math.log(2))
        self.assertEqual(result.item_medians["i0"]["candidate"], 5.0)

    def test_more_than_ten_percent_infra_invalid_voids_the_block(self):
        ids = [f"i{k}" for k in range(10)]
        self.base.say(items_json(**{k: 10 for k in ids}))
        self.cand.say(items_json(**{k: 5 for k in ids}))
        two = {k: 5 for k in ids}
        two["i0"] = (5, "infra_invalid")
        two["i1"] = (5, "infra_invalid")
        self.cand.say(items_json(**two), block=1)
        base_bad = {k: 10 for k in ids}
        base_bad["i3"] = (10, "infra_invalid")
        base_bad["i4"] = (10, "infra_invalid")
        self.base.say(items_json(**base_bad), block=5)
        result = self.blocks()
        self.assertEqual(result.voided_blocks, [1, 5])
        self.assertEqual((len(result.scores), result.void), (8, False))
        self.assertTrue(any("infra-invalid" in w for w in result.warnings))

    def test_wall_time_fallback_and_stray_output(self):
        self.base.say("progress... {'not': json}\n")
        self.cand.say("")
        result = self.blocks(n=5, metric_name=measure.WALL_METRIC, wall_fallback=True)
        self.assertEqual(len(result.scores), 5)
        self.assertGreater(result.arm_medians["baseline"][measure.WALL_METRIC], 0)
        strict = self.blocks(n=5, metric_name="lat")
        self.assertTrue(strict.void)                                         # no metric, no fallback: every block is a baseline failure

    def test_runs_jsonl_is_strict_json_even_with_nan(self):
        self.base.say(metric("lat", "nan"))
        self.cand.say(metric("lat", 1))
        self.blocks(n=2)
        for line in (self.out_dir / "runs.jsonl").read_text().splitlines():
            json.loads(line, parse_constant=lambda c: self.fail(f"{c} in runs.jsonl"))

    def test_deadline_stops_starting_new_blocks(self):
        self.base.say(metric("lat", 100))
        self.cand.say(metric("lat", 100))
        result = self.blocks(n=50, deadline=0)                               # already past: exactly one block runs
        self.assertEqual(result.blocks_run, 1)


if __name__ == "__main__":
    unittest.main()
