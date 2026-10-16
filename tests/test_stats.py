import itertools
import math
import random
import unittest

from hca import stats
from hca.core import HcaError


def brute_force_p(scores):
    """P(W+ >= observed) by enumerating every sign flip, with Pratt zeros and midranks."""
    ranks = stats._midranks([abs(s) for s in scores])
    observed = sum(r for r, s in zip(ranks, scores) if s > 0)
    weights = [r for r, s in zip(ranks, scores) if s != 0]
    hits = sum(sum(w for w, sign in zip(weights, signs) if sign) >= observed - 1e-9
               for signs in itertools.product((0, 1), repeat=len(weights)))
    return hits / 2 ** len(weights)


def symmetric_heavy_noise(rng, scale):
    """Cauchy-like (ratio of normals) noise clipped to stay finite: symmetric, very heavy-tailed."""
    return max(-50 * scale, min(50 * scale, scale * rng.gauss(0, 1) / max(abs(rng.gauss(0, 1)), 1e-3)))


class ExactTestTests(unittest.TestCase):
    def test_p_value_matches_enumeration_with_ties_and_zeros(self):
        rng = random.Random(1)
        cases = [[0.3, 0.1, -0.2], [1, 1, -1, 2, 0, 0.5], [0, 0, 0], [-1, -2, -3, 4]]
        for _ in range(40):
            cases.append([rng.choice([-2, -1, 0, 1, 2, 3]) * 0.01 for _ in range(rng.randint(2, 9))])
        for scores in cases:
            self.assertAlmostEqual(stats.signed_rank_p(scores), brute_force_p(scores), places=12, msg=str(scores))

    def test_all_positive_gives_the_smallest_p(self):
        self.assertEqual(stats.signed_rank_p([0.1] * 3 + [0.2, 0.3]), brute_force_p([0.1] * 3 + [0.2, 0.3]))
        self.assertEqual(stats.signed_rank_p([0.1, 0.2, 0.3, 0.4]), 1 / 16)
        self.assertEqual(stats.smallest_p(10), 1 / 1024)

    def test_bound_agrees_with_the_test_for_untied_scores(self):
        rng = random.Random(2)
        for _ in range(300):
            scores = [rng.gauss(0.02, 0.03) for _ in range(rng.randint(10, 20))]
            for alpha in (0.005, 0.01, 0.025):
                p = stats.signed_rank_p(scores)
                low = stats.walsh_lower_bound(scores, alpha)
                if p <= alpha:
                    self.assertGreater(low, 0, (scores, alpha, p))
                elif low > 0:
                    self.fail(f"bound {low} > 0 but p {p} > {alpha}")

    def test_hodges_lehmann_is_robust_to_one_wild_block(self):
        clean = [0.05, 0.04, 0.06, 0.05, 0.045, 0.055, 0.05, 0.048, 0.052, 0.05]
        wild = clean[:-1] + [-3.0]
        self.assertAlmostEqual(stats.hodges_lehmann(clean), 0.05, places=2)
        self.assertGreater(stats.hodges_lehmann(wild), 0.04)

    def test_null_rejection_rate_is_at_most_alpha_under_heavy_tails(self):
        rng = random.Random(3)
        sims, alpha = 4000, 0.01
        rejects = 0
        for _ in range(sims):
            scores = [symmetric_heavy_noise(rng, 0.02) for _ in range(10)]
            rejects += stats.signed_rank_p(scores) <= alpha
        # exact test: the true rate is <= alpha; allow sampling error (3 SE above alpha)
        self.assertLessEqual(rejects / sims, alpha + 3 * math.sqrt(alpha * (1 - alpha) / sims))

    def test_power_for_a_clear_shift(self):
        rng = random.Random(4)
        kept = sum(stats.keep_decision([math.log(1.06) + rng.gauss(0, 0.02) for _ in range(10)],
                                       stats.parse_effect("5%"))["verdict"] == "keep" for _ in range(300))
        self.assertGreater(kept / 300, 0.9)


class DecisionTests(unittest.TestCase):
    useful = math.log(1.05)

    def test_keep_needs_significance_and_half_the_useful_effect(self):
        tiny = [0.004 + 0.0001 * i for i in range(12)]          # significant but microscopic
        decision = stats.keep_decision(tiny, self.useful)
        self.assertEqual(decision["verdict"], "revert")
        self.assertTrue(any("keep floor" in reason for reason in decision["reasons"]))
        big = [0.06 + 0.001 * i for i in range(12)]
        self.assertEqual(stats.keep_decision(big, self.useful)["verdict"], "keep")

    def test_two_looks_split_alpha_and_extend_only_promising_tries(self):
        rng = random.Random(5)
        promising = [0.03 + rng.gauss(0, 0.05) for _ in range(10)]
        first = stats.keep_decision(promising, self.useful, look=1, looks=2)
        self.assertEqual(first["alpha"], 0.005)
        if first["verdict"] != "keep":
            self.assertEqual(first["verdict"], "extend")
        worse = [-0.03 + rng.gauss(0, 0.01) for _ in range(10)]
        self.assertEqual(stats.keep_decision(worse, self.useful, look=1, looks=2)["verdict"], "revert")
        self.assertEqual(stats.keep_decision(worse, self.useful, look=2, looks=2)["verdict"], "revert")

    def test_too_few_blocks_is_insufficient_not_revert(self):
        decision = stats.keep_decision([0.1] * 5, self.useful)
        self.assertEqual(decision["verdict"], "insufficient")
        self.assertIn("cannot reach", decision["reasons"][0])

    def test_two_look_false_keep_rate_stays_below_alpha(self):
        rng = random.Random(6)
        sims, keeps = 3000, 0
        for _ in range(sims):
            scores = [rng.gauss(0, 0.03) for _ in range(20)]
            first = stats.keep_decision(scores[:10], self.useful, look=1, looks=2)
            verdict = first["verdict"]
            if verdict == "extend":
                verdict = stats.keep_decision(scores, self.useful, look=2, looks=2)["verdict"]
            keeps += verdict == "keep"
        self.assertLessEqual(keeps / sims, 0.01 + 3 * math.sqrt(0.01 * 0.99 / sims))


class NoiseAndUnitsTests(unittest.TestCase):
    def test_noise_report_verdicts(self):
        rng = random.Random(7)
        quiet = [rng.gauss(0, 0.005) for _ in range(20)]
        loud = [rng.gauss(0, 0.2) for _ in range(20)]
        useful = stats.parse_effect("5%")
        self.assertEqual(stats.noise_report(quiet, useful)["verdict"], "ok")
        self.assertEqual(stats.noise_report(loud, useful)["verdict"], "too-noisy")
        report = stats.noise_report(quiet, useful)
        self.assertLess(report["detectable"][20], report["detectable"][10])

    def test_robust_sd_and_detectable_shift_back_noise_report(self):
        rng = random.Random(11)
        scores = [rng.gauss(0, 0.03) for _ in range(30)]
        sd = stats.robust_sd(scores)
        self.assertAlmostEqual(sd, 0.03, delta=0.012)
        self.assertAlmostEqual(stats.robust_sd([0.1] * 5 + [0.3] * 4), 0.1, delta=0.11)         # zero MAD falls back to the population SD
        self.assertGreater(stats.robust_sd([1.0, 1.0, 1.0, 2.0]), 0)
        report = stats.noise_report(scores, 0.05)
        self.assertEqual(report["noise_sd"], sd)
        self.assertEqual(report["detectable"][10], stats.detectable_shift(sd, 10))
        self.assertLess(stats.detectable_shift(sd, 20), stats.detectable_shift(sd, 10))
        self.assertGreater(stats.detectable_shift(sd, 10, alpha=0.005), stats.detectable_shift(sd, 10, alpha=0.01))
        with self.assertRaises(HcaError):
            stats.robust_sd([])

    def test_log_ratio_direction_and_effect_parsing(self):
        self.assertGreater(stats.log_ratio(100, 90, "min"), 0)      # faster is better
        self.assertLess(stats.log_ratio(0.80, 0.70, "max"), 0)     # lower accuracy is worse
        self.assertAlmostEqual(stats.to_percent(stats.parse_effect("5%")), 5.0)
        self.assertAlmostEqual(stats.parse_effect(0.1), 0.1)
        for bad in ("5", "-5%", 0, True, "fast"):
            with self.assertRaises(HcaError):
                stats.parse_effect(bad)
        with self.assertRaises(HcaError):
            stats.log_ratio(0, 1, "min")

    def test_item_block_score_is_the_median_item_log_ratio(self):
        base = {"a": 100.0, "b": 100.0, "c": 100.0}
        cand = {"a": 90.0, "b": 95.0, "c": 500.0}             # one item regressed wildly
        self.assertAlmostEqual(stats.item_block_score(base, cand, "min"), math.log(100 / 95))
        with self.assertRaises(HcaError):
            stats.item_block_score(base, {"a": 1.0}, "min")

    def test_metric_change_pct_is_the_metrics_own_change(self):
        cut = stats.log_ratio(100, 70, "min")                   # a 30% time cut
        self.assertAlmostEqual(stats.metric_change_pct(cut, "min"), -30.0)
        gain = stats.log_ratio(0.800, 0.8256, "max")            # accuracy 0.800 -> 0.8256
        self.assertAlmostEqual(stats.metric_change_pct(gain, "max"), 3.2, places=6)
        self.assertAlmostEqual(stats.metric_change_pct(0.0, "min"), 0.0)
        # monotone: a lower bound on the log ratio maps to the least-improvement end, whichever the direction
        self.assertGreater(stats.metric_change_pct(0.02, "min"), stats.metric_change_pct(0.08, "min"))
        self.assertLess(stats.metric_change_pct(0.02, "max"), stats.metric_change_pct(0.08, "max"))
        with self.assertRaises(HcaError):
            stats.metric_change_pct(0.1, "up")

    def test_parse_effect_percent_means_that_much_better_in_the_metrics_own_terms(self):
        self.assertAlmostEqual(stats.parse_effect("5%"), math.log(1.05))                  # default direction is max
        self.assertAlmostEqual(stats.parse_effect("5%", "max"), math.log(1.05))
        self.assertAlmostEqual(stats.parse_effect("5%", "min"), -math.log(0.95))          # candidate = 0.95 x baseline
        self.assertAlmostEqual(stats.metric_change_pct(stats.parse_effect("5%", "min"), "min"), -5.0)
        self.assertAlmostEqual(stats.metric_change_pct(stats.parse_effect("5%", "max"), "max"), 5.0)
        self.assertAlmostEqual(stats.parse_effect(0.1, "min"), 0.1)                       # a bare number is a log ratio either way
        with self.assertRaises(HcaError):
            stats.parse_effect("100%", "min")                                              # a 100% cut would be zero
        self.assertAlmostEqual(stats.parse_effect("100%", "max"), math.log(2))

    def test_keep_decision_reasons_speak_in_improvement_terms_for_either_direction(self):
        scores = [0.003, -0.002, 0.004, 0.001, 0.002, -0.001, 0.003, 0.002, 0.001, 0.002]   # tiny, consistent
        useful = stats.parse_effect("5%", "min")
        for direction, sign in (("min", -1), ("max", 1)):
            decision = stats.keep_decision(scores, useful, direction=direction)
            self.assertEqual(decision["verdict"], "revert")
            self.assertTrue(any("better, below the keep floor of +" in r for r in decision["reasons"]), decision["reasons"])
            self.assertEqual(math.copysign(1, decision["estimate_change_pct"]), sign)
            self.assertEqual(math.copysign(1, decision["keep_floor_change_pct"]), sign)


if __name__ == "__main__":
    unittest.main()
