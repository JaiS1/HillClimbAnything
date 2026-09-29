from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tools.dream_rsi import core, evolve, stats
from tools.dream_rsi.cli import main


def arm(diffs, base=10.0, clusters=None, subgroups=None):
    """Baseline and candidate item lists whose paired differences equal diffs."""
    baseline, candidate = [], []
    for index, diff in enumerate(diffs):
        extra = {}
        if clusters:
            extra["cluster"] = clusters[index]
        if subgroups:
            extra["subgroup"] = subgroups[index]
        baseline.append(dict(id="i%03d" % index, score=base, **extra))
        candidate.append(dict(id="i%03d" % index, score=base + diff, **extra))
    return candidate, baseline


def alternating(n, mean, spread=1.0):
    return [mean + (spread if i % 2 else -spread) for i in range(n)]


METRIC = {"name": "accuracy", "direction": "max", "identity": "toy-items-v1"}


def score_file(name, items, runtime="cpu-fp32", created=None):
    data = {"schema_version": 1, "kind": "dream_loop_item_scores", "arm": name, "runtime": runtime,
            "metric": METRIC, "items": items}
    if created:
        data["created_utc"] = created
    return data


class DecisionTests(unittest.TestCase):
    def test_clear_gain_promotes_and_noise_holds(self):
        gain = stats.decide(*arm(alternating(40, 1.0)), "max")
        self.assertEqual(gain["verdict"], "promote")
        self.assertGreater(gain["interval"][0], 0)
        noise = stats.decide(*arm(alternating(40, 0.0)), "max")
        self.assertEqual(noise["verdict"], "hold")
        self.assertLessEqual(noise["interval"][0], 0)

    def test_bonferroni_holds_a_winner_picked_from_many(self):
        candidate, baseline = arm(alternating(50, 0.35))
        single = stats.decide(candidate, baseline, "max", {"comparisons": 1})
        many = stats.decide(candidate, baseline, "max", {"comparisons": 20})
        self.assertEqual(single["verdict"], "promote")
        self.assertEqual(many["verdict"], "hold")
        self.assertLess(many["interval"][0], single["interval"][0])

    def test_clustered_items_are_resampled_by_cluster(self):
        diffs = [0.35 + (1.0 if (i // 10) % 2 else -1.0) + 0.01 * (i % 3) for i in range(100)]
        clusters = ["c%d" % (i // 10) for i in range(100)]
        flat = stats.decide(*arm(diffs), "max")
        grouped = stats.decide(*arm(diffs, clusters=clusters), "max")
        self.assertEqual(grouped["units"], 10)
        self.assertGreater(grouped["half_width"], 2 * flat["half_width"])
        self.assertEqual(flat["verdict"], "promote")
        self.assertEqual(grouped["verdict"], "hold")

    def test_guardrails_block_a_positive_mean(self):
        diffs = [3.0] * 30 + [-1.0] * 10
        subgroups = ["day"] * 30 + ["night"] * 10
        candidate, baseline = arm(diffs, subgroups=subgroups)
        self.assertEqual(stats.decide(candidate, baseline, "max")["verdict"], "promote")
        harmed = stats.decide(candidate, baseline, "max", {"max_harm_rate": 0.2})
        self.assertEqual(harmed["verdict"], "hold")
        self.assertAlmostEqual(harmed["harmed_rate"], 0.25)
        worst = stats.decide(candidate, baseline, "max", {"max_subgroup_loss": 0.5})
        self.assertEqual(worst["verdict"], "hold")
        self.assertEqual(worst["worst_subgroup"]["name"], "night")

    def test_small_or_mismatched_samples_and_direction(self):
        self.assertEqual(stats.decide(*arm([5.0, 5.0, 5.0]), "max")["verdict"], "insufficient")
        candidate, baseline = arm(alternating(40, 1.0))
        self.assertEqual(stats.decide(candidate, baseline, "min")["verdict"], "hold")
        with self.assertRaisesRegex(core.HarnessError, "identical item ids"):
            stats.decide(candidate[1:], baseline, "max")
        with self.assertRaisesRegex(core.HarnessError, "unknown decision-rule"):
            stats.decide(candidate, baseline, "max", {"alpah": 0.1})

    def test_power_warning_when_interval_is_wider_than_useful_effect(self):
        result = stats.decide(*arm(alternating(40, 1.0)), "max", {"useful_effect": 0.05})
        self.assertTrue(any("underpowered" in w for w in result["warnings"]))


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.candidate, self.baseline = arm(alternating(40, 1.0))

    def test_cross_runtime_comparison_is_refused_without_a_same_session_control(self):
        with self.assertRaisesRegex(core.HarnessError, "re-run the incumbent"):
            stats.compare([score_file("cand", self.candidate, "gpu-bf16")], score_file("inc", self.baseline, "gpu-fp16"))
        result = stats.compare([score_file("cand", self.candidate, "gpu-bf16")], score_file("inc", self.baseline, "gpu-fp16"),
                               control=score_file("inc-rerun", self.baseline, "gpu-bf16"))
        self.assertEqual(result["baseline"], "inc-rerun")
        self.assertIsNone(result["same_session_noise"])
        self.assertEqual(result["current_verdict"]["promote"], "cand")

    def test_same_session_control_exposes_an_unreproducible_evaluator(self):
        drifted = [dict(item, score=item["score"] + 2.0) for item in self.baseline]
        result = stats.compare([score_file("cand", self.candidate)], score_file("inc", self.baseline),
                               control=score_file("inc-rerun", drifted))
        self.assertEqual(result["same_session_noise"]["verdict"], "promote")
        self.assertTrue(any("not reproducible" in note for note in result["notes"]))
        self.assertIsNone(result["current_verdict"]["promote"])

    def test_one_current_verdict_and_bonferroni_over_all_candidates(self):
        weak, _ = arm(alternating(50, 0.35))
        _, base50 = arm(alternating(50, 0.35))
        strong, _ = arm(alternating(50, 1.0))
        result = stats.compare([score_file("weak", weak), score_file("strong", strong)], score_file("inc", base50),
                               rule={"comparisons": 20})
        self.assertEqual(result["rule"]["comparisons"], 20)
        self.assertEqual(result["current_verdict"]["promote"], "strong")
        self.assertIn("promote strong", stats.render(result).splitlines()[0])


class RegistrationTests(unittest.TestCase):
    def draft(self, **changes):
        base = {"kind": "dream_loop_registration", "question": "Does the candidate beat the incumbent?",
                "metric": METRIC, "incumbent": "inc", "arms": ["cand"],
                "decision": {"alpha": 0.05, "comparisons": 1, "useful_effect": 0.5, "max_harm_rate": 0.5},
                "expected_half_width": 0.4,
                "premise": {"produced_by": "hand-written baseline, no training", "data_new_to_it": "yes",
                            "zero_effort_baseline": 10.0, "can_move_scored_quantity": "yes: it changes every item",
                            "step0_metric": 10.0}}
        base.update(changes)
        return base

    def test_register_requires_premise_and_useful_effect_and_warns_on_power(self):
        with self.assertRaisesRegex(core.HarnessError, "premise check incomplete"):
            stats.register(self.draft(premise={"produced_by": "x"}))
        with self.assertRaisesRegex(core.HarnessError, "useful_effect"):
            stats.register(self.draft(decision={"comparisons": 1}))
        frozen = stats.register(self.draft(expected_half_width=0.9))
        self.assertTrue(any("underpowered" in w for w in frozen["warnings"]))

    def test_frozen_rule_labels_late_arms_exploratory_and_detects_edits(self):
        registered = datetime(2026, 1, 1, tzinfo=timezone.utc)
        frozen = stats.register(self.draft(), now=registered)
        candidate, baseline = arm(alternating(40, 1.0))
        after = (registered + timedelta(hours=1)).isoformat()
        before = (registered - timedelta(hours=1)).isoformat()
        good = stats.compare([score_file("cand", candidate, created=after)], score_file("inc", baseline), frozen=frozen)
        self.assertEqual(good["arms"][0]["status"], "confirmatory")
        self.assertEqual(good["current_verdict"]["promote"], "cand")
        late = stats.compare([score_file("cand", candidate, created=before), score_file("extra", candidate, created=after)],
                             score_file("inc", baseline), frozen=frozen)
        self.assertEqual([a["status"].split(":")[0] for a in late["arms"]], ["exploratory", "exploratory"])
        self.assertIsNone(late["current_verdict"]["promote"])
        with self.assertRaisesRegex(core.HarnessError, "fixes the decision rule"):
            stats.compare([score_file("cand", candidate)], score_file("inc", baseline), frozen=frozen, rule={"alpha": 0.5})
        frozen["registration"]["decision"]["alpha"] = 0.5
        with self.assertRaisesRegex(core.HarnessError, "changed after it was frozen"):
            stats.compare([score_file("cand", candidate)], score_file("inc", baseline), frozen=frozen)

    def test_cli_register_and_compare_write_records(self):
        home = Path(__file__).resolve().parents[1] / ".runs"
        home.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=home) as temp:
            temp = Path(temp)
            core.write_json(temp / "draft.json", self.draft())
            candidate, baseline = arm(alternating(40, 1.0))
            now = datetime.now(timezone.utc) + timedelta(minutes=5)
            core.write_json(temp / "cand.json", score_file("cand", candidate, created=now.isoformat()))
            core.write_json(temp / "inc.json", score_file("inc", baseline))
            main(["register", str(temp / "draft.json"), "--output", str(temp / "frozen.json")])
            main(["compare", str(temp / "cand.json"), "--incumbent", str(temp / "inc.json"),
                  "--registration", str(temp / "frozen.json"), "--output", str(temp / "verdict.json")])
            verdict = core.read_json(temp / "verdict.json")
            self.assertEqual(verdict["current_verdict"]["promote"], "cand")
            with self.assertRaises(SystemExit):
                main(["compare", str(temp / "cand.json"), "--incumbent", str(temp / "inc.json"),
                      "--output", str(temp / "verdict.json")])


class EvolutionPromotionTests(unittest.TestCase):
    def setUp(self):
        home = Path(__file__).resolve().parents[1]
        scratch = home / ".runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        shutil.copytree(home / "examples/knapsack", self.project)
        config = core.read_json(self.project / "project.json")
        config["limits"].update(attempts=3, wall_seconds=30)
        config["policy"]["width"] = 2
        core.write_json(self.project / "project.json", config)
        self.runs = []
        for name in ("select", "confirm"):
            run = self.base / name
            core.initialize(self.project / "project.json", run)
            list(core.run_automatic(run))
            self.runs.append(run)
        self.bad = self.base / "bad.py"
        self.bad.write_text("import json, sys\njson.load(sys.stdin)\nprint('{\\\"action\\\": \\\"root\\\"}')\n")
        good = evolve.baseline_source(core.load_config(self.project / "project.json")["policy"])
        self.developer = [sys.executable, str(self.base / "developer.py")]
        (self.base / "developer.py").write_text(
            "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'source': " + repr(good) + "}))\n")
        self.good = good

    def evolve(self, name, **options):
        return evolve.evolve([self.runs[0]], self.developer, revisions=1, timeout_seconds=5, wall_seconds=30,
                             output=self.base / name, **options)

    def test_paired_diagnostic_is_recorded_and_one_world_is_insufficient(self):
        good_path = self.base / "good.py"
        good_path.write_text(self.good)
        result = self.evolve("same.json", initial_source=good_path, promotion="paired")
        self.assertEqual(result["revisions"][0]["paired"]["verdict"], "insufficient")
        self.assertFalse(result["revisions"][0]["accepted"])
        self.assertEqual(result["revisions"][0]["request"]["history"], [])

    def test_held_out_confirmation_reverts_only_under_paired_promotion(self):
        with self.assertRaisesRegex(core.HarnessError, "shared between select and confirm"):
            self.evolve("dup.json", confirm_runs=[self.runs[0]])
        point = self.evolve("point.json", initial_source=self.bad, confirm_runs=[self.runs[1]])
        self.assertTrue(point["revisions"][0]["accepted"])
        self.assertEqual(point["confirmation"]["verdict"], "unsupported")
        self.assertFalse(point["confirmation"]["reverted_to_initial"])
        paired = self.evolve("paired.json", initial_source=self.bad, confirm_runs=[self.runs[1]], promotion="paired")
        self.assertTrue(paired["revisions"][0]["accepted"])
        self.assertTrue(paired["confirmation"]["reverted_to_initial"])
        self.assertEqual(paired["selected_source_hash"], paired["initial_evaluation"]["policy"]["source_hash"])
        self.assertEqual(paired["confirmation"]["worlds"][0]["id"], core.verify(self.runs[1])["world_id"])


if __name__ == "__main__":
    unittest.main()
