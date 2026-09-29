import contextlib
from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tools.dream_rsi import core, evolve, stats
from tools.dream_rsi.cli import checked_selection, main


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

    def test_null_promotion_rate_stays_near_nominal_with_few_units(self):
        import random
        rng = random.Random(7)
        promoted = 0
        for trial in range(200):
            candidate, baseline = arm([rng.gauss(0, 1) for _ in range(5)])
            promoted += stats.decide(candidate, baseline, "max", {"bootstrap": 500, "seed": trial})["verdict"] == "promote"
        # Nominal one-sided rate is 0.025; a plain percentile bootstrap gives about 0.10 here.
        self.assertLessEqual(promoted / 200, 0.06)

    def test_item_ids_do_not_merge_with_cluster_names(self):
        candidate, baseline = arm([1.0] * 6, clusters=["A", "A", "B", "B", None, None])
        for items in (candidate, baseline):
            items[4]["id"] = "A"
            items[4].pop("cluster")
            items[5].pop("cluster")
        self.assertEqual(stats.decide(candidate, baseline, "max", {"min_units": 2})["units"], 4)

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
        self.assertEqual(result["same_session_noise"]["verdict"], "differs")
        self.assertTrue(any("not reproducible" in note for note in result["notes"]))
        self.assertIsNone(result["current_verdict"]["promote"])
        steady = stats.compare([score_file("cand", self.candidate)], score_file("inc", self.baseline),
                               control=score_file("inc-rerun", self.baseline))
        self.assertEqual(steady["same_session_noise"]["verdict"], "consistent")
        self.assertEqual(steady["current_verdict"]["promote"], "cand")

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
        undated = stats.compare([score_file("cand", candidate)], score_file("inc", baseline), frozen=frozen)
        self.assertTrue(undated["arms"][0]["status"].startswith("exploratory"))
        self.assertIsNone(undated["current_verdict"]["promote"])
        with self.assertRaisesRegex(core.HarnessError, "fixes the decision rule"):
            stats.compare([score_file("cand", candidate)], score_file("inc", baseline), frozen=frozen, rule={"alpha": 0.5})
        backdated = dict(frozen, registered_utc=(registered - timedelta(days=1)).isoformat())
        with self.assertRaisesRegex(core.HarnessError, "changed after it was frozen"):
            stats.compare([score_file("cand", candidate, created=before)], score_file("inc", baseline), frozen=backdated)
        frozen["registration"]["decision"]["alpha"] = 0.5
        with self.assertRaisesRegex(core.HarnessError, "changed after it was frozen"):
            stats.compare([score_file("cand", candidate, created=after)], score_file("inc", baseline), frozen=frozen)

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
            printed, errors = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(errors):
                main(["register", str(temp / "draft.json"), "--output", str(temp / "frozen.json")])
                main(["compare", str(temp / "cand.json"), "--incumbent", str(temp / "inc.json"),
                      "--registration", str(temp / "frozen.json"), "--output", str(temp / "verdict.json")])
                with self.assertRaises(SystemExit):
                    main(["compare", str(temp / "cand.json"), "--incumbent", str(temp / "inc.json"),
                          "--output", str(temp / "verdict.json")])
            verdict = core.read_json(temp / "verdict.json")
            self.assertEqual(verdict["current_verdict"]["promote"], "cand")
            self.assertTrue(printed.getvalue().splitlines()[1].startswith("CURRENT VERDICT: promote cand"))
            self.assertIn("output already exists", errors.getvalue())


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
        self.runs = {}
        for name in ("select-1", "select-2", "confirm-1", "confirm-2"):
            run = self.base / name
            core.initialize(self.project / "project.json", run)
            list(core.run_automatic(run))
            self.runs[name] = run
        self.bad = self.base / "bad.py"
        self.bad.write_text("import json, sys\njson.load(sys.stdin)\nprint('{\\\"action\\\": \\\"root\\\"}')\n")
        self.good = self.base / "good.py"
        self.good.write_text(evolve.baseline_source(core.load_config(self.project / "project.json")["policy"]))
        # Proposes a policy that stops at once; with a large attempt penalty it wins on every world.
        stop = "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'action': None}))\n"
        self.stopper = [sys.executable, str(self.base / "stopper.py")]
        (self.base / "stopper.py").write_text(
            "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'source': " + repr(stop) + "}))\n")
        self.restorer = [sys.executable, str(self.base / "restorer.py")]
        (self.base / "restorer.py").write_text(
            "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'source': " + repr(self.good.read_text()) + "}))\n")

    def evolve(self, name, developer, select, **options):
        return evolve.evolve([self.runs[s] for s in select], developer, revisions=1, timeout_seconds=5,
                             wall_seconds=30, output=self.base / name, **options)

    def test_paired_gate_rejects_what_the_point_rule_accepts(self):
        common = dict(initial_source=self.good, penalty=1000.0, rule={"min_units": 3})
        point = self.evolve("point.json", self.stopper, ["select-1", "select-2"], **common)
        paired = self.evolve("paired.json", self.stopper, ["select-1", "select-2"], promotion="paired", **common)
        self.assertTrue(point["revisions"][0]["accepted"])
        self.assertFalse(paired["revisions"][0]["accepted"])
        self.assertEqual(paired["revisions"][0]["paired"]["verdict"], "insufficient")
        self.assertEqual(paired["selected_source_hash"], paired["initial_evaluation"]["policy"]["source_hash"])

    def test_multi_world_promotion_confirms_on_held_out_worlds(self):
        with self.assertRaisesRegex(core.HarnessError, "shared between select and confirm"):
            self.evolve("dup.json", self.stopper, ["select-1"], confirm_runs=[self.runs["select-1"]])
        result = self.evolve("confirmed.json", self.stopper, ["select-1", "select-2"], initial_source=self.good,
                             penalty=1000.0, promotion="paired", rule={"min_units": 2},
                             confirm_runs=[self.runs["confirm-1"], self.runs["confirm-2"]])
        self.assertEqual(result["revisions"][0]["paired"]["verdict"], "promote")
        self.assertTrue(result["revisions"][0]["accepted"])
        self.assertEqual(result["confirmation"]["verdict"], "promote")
        self.assertFalse(result["confirmation"]["unconfirmed"])
        self.assertNotEqual(result["selected_source_hash"], result["initial_evaluation"]["policy"]["source_hash"])
        self.assertEqual([w["id"] for w in result["confirmation"]["worlds"]],
                         [core.verify(self.runs[n])["world_id"] for n in ("confirm-1", "confirm-2")])

    def test_failed_confirmation_reverts_only_to_a_supported_start(self):
        options = dict(penalty=1000.0, promotion="paired", rule={"min_units": 2})
        # Supported start, too few confirm worlds: the confirmation is insufficient, so keep the start.
        reverted = self.evolve("reverted.json", self.stopper, ["select-1", "select-2"], initial_source=self.good,
                               confirm_runs=[self.runs["confirm-1"]], **options)
        self.assertTrue(reverted["revisions"][0]["accepted"])
        self.assertEqual(reverted["confirmation"]["verdict"], "insufficient")
        self.assertTrue(reverted["confirmation"]["reverted_to_initial"])
        self.assertEqual(reverted["selected_source_hash"], reverted["initial_evaluation"]["policy"]["source_hash"])
        # Unsupported start: never revert to it; keep the selection, marked unconfirmed, and still adoptable.
        kept = self.evolve("kept.json", self.restorer, ["select-1"], initial_source=self.bad,
                           confirm_runs=[self.runs["confirm-1"]], **options)
        self.assertEqual(kept["confirmation"]["verdict"], "unsupported")
        self.assertTrue(kept["confirmation"]["unconfirmed"])
        self.assertFalse(kept["confirmation"]["reverted_to_initial"])
        checked = checked_selection(self.base / "kept.json", core.load_config(self.project / "project.json"))
        self.assertEqual(checked["selected_policy"]["name"], "executable")


if __name__ == "__main__":
    unittest.main()
