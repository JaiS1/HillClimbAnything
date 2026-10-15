"""A2.6 #15: end-to-end calibration of the false-keep rate through the real `hca try`.

The unit tests in test_stats check the arithmetic; this one checks the wiring (look, alpha, floor, snapshots,
arms, warm-ups, randomised order) by running 100 do-nothing tries on the fixture, with the shipped two-look rule
(10 blocks, extend to 20, 0.5% per look, plus the keep floor), and requiring at most 3 keeps. The fixture's jitter is
a deterministic function of the run counter and the block order comes from a seeded generator, so the result is the
same on every machine and never flaky. With a true false-keep rate of about 0.2% the expected number of keeps is
0.2; a rate of 1% would give 1, and a wiring bug that keeps noise (a one-sided arm, a missing floor) gives dozens.
"""

import unittest

import repokit
from repokit import env

TRIES = 100
MAX_KEEPS = 3


class CalibrationTests(repokit.RepoTestCase):
    toml_args = {"limits": {"blocks": 10, "extend_to": 20}}

    def test_100_noop_tries_keep_at_most_three(self):
        with env(BENCH_JITTER="0.02"):
            self.assertEqual(self.init()[0], 0)
            keeps, extended, invalid = 0, 0, 0
            for i in range(1, TRIES + 1):
                self.set_speed(f"30.{i:07d}")                       # a different tree every time: the replay rule must not interfere
                code, text = self.try_(f"no-op {i}", seed=1000 + i)
                a = self.attempt(i)
                keeps += code == 0
                extended += bool(a.get("extended"))
                invalid += not a["valid"]
        self.assertLessEqual(keeps, MAX_KEEPS, f"{keeps} of {TRIES} do-nothing tries were kept")
        self.assertEqual(invalid, 0)                                # every try was a real, valid measurement
        self.assertGreater(extended, 5)                             # the second look really ran for the promising ones
        self.assertEqual(self.state()["last_keep"], self.repo.head())


if __name__ == "__main__":
    unittest.main()
