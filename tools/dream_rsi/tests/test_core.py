"""CPU integration checks use copied toy fixtures only, never project data."""
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from tools.dream_rsi import core
from tools.dream_rsi.cli import dream, checked_selection, cycle
from tools.dream_rsi import evolve
from tools.dream_rsi.replay import replay


class DriverTests(unittest.TestCase):
    def setUp(self):
        home = Path(__file__).resolve().parents[1]
        scratch = home / ".runs"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "project"
        shutil.copytree(home / "examples/knapsack", self.project)
        self.config = core.read_json(self.project / "project.json")
        self.config["limits"].update(attempts=3, wall_seconds=30)
        self.config["policy"]["width"] = 1
        self.run = self.base / "run"
        self.save_config()

    def save_config(self):
        core.write_json(self.project / "project.json", self.config)

    def initialize(self):
        return core.initialize(self.project / "project.json", self.run)

    def test_automatic_run_cap_and_replay_incumbent(self):
        self.initialize()
        results = list(core.run_automatic(self.run))
        self.assertEqual(len(results), 3)
        state = core.verify(self.run)
        self.assertIsNone(core.ask(self.run))
        self.assertFalse((self.run / "pending.json").exists())
        selection = dream([self.run])
        baseline = selection["results"][0]
        self.assertTrue(baseline["eligible"])
        self.assertEqual(baseline["episodes"][0]["observed_ids"], ["root"] + [n["id"] for n in state["nodes"]])

    def test_claim_exclusive_and_manual_invalid_retained(self):
        self.initialize()
        packet = core.ask(self.run)
        with self.assertRaisesRegex(core.HarnessError, "pending"):
            core.ask(self.run)
        core.write_json(Path(packet["workspace"]) / "candidate.json", {"selected": [999]})
        result = core.tell(self.run)
        self.assertEqual((result["status"], result["score"]), ("invalid", None))
        self.assertEqual(result["result"]["generation_cost_scope"], "external_unmeasured")
        self.assertTrue(result["artifact_hash"])
        packet2 = core.ask(self.run)
        self.assertEqual(packet2["artifact_parent_id"], "root")
        core.tell(self.run, abandoned="stop test")
        core.verify(self.run)

    def test_verified_hashes_detect_config_artifact_and_evaluator_changes(self):
        self.initialize()
        state = core.read_json(self.run / "world.json")
        state["config"]["limits"]["attempts"] += 1
        core.write_json(self.run / "world.json", state)
        with self.assertRaisesRegex(core.HarnessError, "config changed"):
            core.ask(self.run)
        state["config"]["limits"]["attempts"] -= 1
        core.write_json(self.run / "world.json", state)
        candidate = self.run / "root/workspace/candidate.json"
        original = candidate.read_bytes()
        candidate.write_text('{"selected":[1]}')
        with self.assertRaisesRegex(core.HarnessError, "artifact changed"):
            core.verify(self.run)
        candidate.write_bytes(original)
        with (self.project / "evaluate.py").open("a") as f:
            f.write("\n# changed evaluator\n")
        with self.assertRaisesRegex(core.HarnessError, "verifier changed"):
            core.verify(self.run)

    def test_declared_generator_source_is_frozen(self):
        self.initialize()
        with (self.project / "propose.py").open("a") as handle:
            handle.write("\n# changed generator\n")
        with self.assertRaisesRegex(core.HarnessError, "generator changed"):
            core.verify(self.run)

    def test_interrupted_evaluation_never_relaunches(self):
        self.initialize()
        packet = core.ask(self.run)
        marker = self.run / "attempts" / packet["attempt_id"] / "evaluation-started.json"
        core.write_json(marker, {"started_at": 1})
        with self.assertRaisesRegex(core.HarnessError, "already started"):
            core.tell(self.run)
        result = core.tell(self.run, abandoned="interrupted; no duplicate launch")
        self.assertEqual(result["status"], "blocked")

    def test_wall_deadline_does_not_launch_new_attempt(self):
        self.initialize()
        state = core.read_json(self.run / "world.json")
        state["deadline_at"] = 0
        core.write_json(self.run / "world.json", state)
        self.assertIsNone(core.ask(self.run))

    def test_subprocess_deadline_records_budget_stop(self):
        with self.assertRaises(core.BudgetExhausted):
            core.command([sys.executable, "-c", "import time; time.sleep(5)"], self.base, self.base, "slow", 0.03)
        record = core.read_json(self.base / "slow.command.json")
        self.assertTrue(record["timed_out"])
        self.assertNotEqual(record["returncode"], 0)

    def test_metric_mismatch_is_invalid_not_promoted(self):
        script = self.project / "evaluate.py"
        script.write_text('import json\nfrom pathlib import Path\nr=json.loads(Path("candidate.json").read_text())\nprint(json.dumps({"status":"valid","score":0,"metric":' + repr(self.config["metric"]) + ' if not r["selected"] else {"name":"other"}}))\n')
        self.initialize()
        packet = core.ask(self.run)
        core.write_json(Path(packet["workspace"]) / "candidate.json", {"selected": [1]})
        result = core.tell(self.run)
        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["score"])

    def test_dream_pool_cannot_mix_metrics_or_duplicate_worlds(self):
        self.initialize()
        list(core.run_automatic(self.run))
        with self.assertRaisesRegex(core.HarnessError, "duplicate world"):
            dream([self.run, self.run])
        # A second valid project with a different metric contract must not be pooled.
        self.config["metric"]["identity"] = "different"
        script = self.project / "evaluate-other.py"
        script.write_text('import json\nprint(json.dumps({"status":"valid","score":0,"metric":' + repr(self.config["metric"]) + '}))\n')
        self.config["evaluate"] = ["{python}", "{config_dir}/evaluate-other.py"]
        self.config["verifier_files"] = ["evaluate-other.py"]
        self.save_config()
        other = self.base / "other"
        core.initialize(self.project / "project.json", other)
        with self.assertRaisesRegex(core.HarnessError, "mixes"):
            dream([self.run, other])

    def test_symlink_and_oversized_snapshot_rejected(self):
        seed = self.project / "seed"
        (seed / "link").symlink_to(self.project / "evaluate.py")
        with self.assertRaisesRegex(core.HarnessError, "symlinks"):
            self.initialize()
        (seed / "link").unlink()
        with self.assertRaisesRegex(core.HarnessError, "max_artifact_bytes"):
            core.tree_hash(seed, 1)

    def test_completed_packet_corruption_stops_next_ask(self):
        self.initialize()
        packet = core.ask(self.run)
        core.tell(self.run)
        saved = self.run / "attempts" / packet["attempt_id"] / "packet.json"
        data = core.read_json(saved)
        data["branch"] = 999
        core.write_json(saved, data)
        with self.assertRaisesRegex(core.HarnessError, "packet record mismatch"):
            core.ask(self.run)
        self.assertFalse((self.run / "pending.json").exists())

    def test_corrupt_world_rejected_before_evaluation(self):
        self.initialize()
        core.ask(self.run)
        core.tell(self.run)
        packet = core.ask(self.run)
        state = core.read_json(self.run / "world.json")
        state["nodes"][0]["score"] = None
        core.write_json(self.run / "world.json", state)
        with self.assertRaises(ValueError):
            core.tell(self.run)
        self.assertFalse((self.run / "attempts" / packet["attempt_id"] / "evaluation-started.json").exists())

    def test_policy_adoption_reproduces_selection_and_rejects_tampering(self):
        self.initialize()
        list(core.run_automatic(self.run))
        selection = dream([self.run], 0.5)
        path = self.base / "selection.json"
        core.write_json(path, selection)
        config = core.load_config(self.project / "project.json")
        self.assertEqual(checked_selection(path, config), selection)
        selection["selected_policy"]["width"] = 99
        core.write_json(path, selection)
        with self.assertRaisesRegex(core.HarnessError, "does not reproduce"):
            checked_selection(path, config)

    def test_live_automatic_owner_blocks_manual_completion_or_abandonment(self):
        self.initialize()
        core.ask(self.run, automated=True)
        with self.assertRaisesRegex(core.HarnessError, "owns this attempt"):
            core.tell(self.run)
        with self.assertRaisesRegex(core.HarnessError, "still be running"):
            core.tell(self.run, abandoned="unsafe concurrent reclaim")
        self.assertTrue((self.run / "pending.json").exists())
        core.tell(self.run, abandoned="owner closes its own attempt", _automatic=True)

    def test_policy_code_evolution_retains_improved_executable_and_freezes_adoption(self):
        self.config["limits"]["attempts"] = 3
        self.config["policy"]["width"] = 2
        self.save_config()
        self.initialize()
        list(core.run_automatic(self.run))
        bad = self.base / "bad.py"
        bad.write_text("import json, sys\njson.load(sys.stdin)\nprint('{\\\"action\\\": \\\"root\\\"}')\n")
        good = evolve.baseline_source(core.load_config(self.project / "project.json")["policy"])
        developer = self.base / "developer.py"
        developer.write_text("import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'source': " + repr(good) + ", 'summary': 'restore supported traversal'}))\n")
        output = self.base / "evolution.json"
        selection = evolve.evolve([self.run], [sys.executable, str(developer)], revisions=2,
                                   timeout_seconds=2, wall_seconds=10, output=output,
                                   initial_source=bad)
        self.assertTrue(selection["selected_evaluation"]["eligible"])
        self.assertFalse(selection["initial_evaluation"]["eligible"])
        self.assertEqual(len(selection["revisions"]), 2)
        self.assertIn("request", selection["revisions"][0])
        self.assertIn("reply", selection["revisions"][0])
        self.assertIn("candidate_source", selection["revisions"][0])
        self.assertFalse(selection["revisions"][1]["accepted"])
        self.assertEqual(selection["revisions"][1]["request"]["history"][0]["candidate_source"], good)
        checked = checked_selection(output, core.load_config(self.project / "project.json"))
        self.assertEqual(checked["selected_policy"]["name"], "executable")
        adopted = self.base / "adopted"
        core.initialize(self.project / "project.json", adopted, checked["selected_policy"])
        self.assertEqual(len(list(core.run_automatic(adopted))), 3)
        frozen = adopted / "policy/selected.py"
        frozen.write_text("# tampered\n")
        with self.assertRaisesRegex(core.HarnessError, "frozen policy"):
            core.verify(adopted)

    def test_live_executable_policy_uses_same_trace_as_replay(self):
        source = self.base / "trace-policy.py"
        source.write_text("""import json, sys
r = json.load(sys.stdin)
if r['nodes']:
    assert r['trace'][-1]['revealed_id'] == r['nodes'][-1]['id']
print(json.dumps({'action': 'root' if not r['nodes'] else r['nodes'][-1]['id']}))
""")
        policy = {"name": "executable", "source_file": str(source), "source_hash": core.digest(source),
                  "timeout_seconds": 1, "max_attempts": 3, "width": 1, "patience": 1}
        core.initialize(self.project / "project.json", self.run, policy)
        nodes = list(core.run_automatic(self.run))
        state = core.verify(self.run)
        episode = replay(state, state["config"]["policy"])
        self.assertEqual([node["parent_id"] for node in nodes], [step["action"] for step in episode["trace"]])

    def test_cycle_closes_rollout_evolve_adopt_under_outer_budget(self):
        source = evolve.baseline_source(core.load_config(self.project / "project.json")["policy"])
        developer = self.base / "cycle-developer.py"
        developer.write_text("import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'source': " + repr(source) + "}))\n")
        output = self.base / "cycle"
        runs = cycle(self.project / "project.json", output, [sys.executable, str(developer)],
                     cycles=2, revisions=1, developer_seconds=2, policy_seconds=1, wall_seconds=30, penalty=0.0)
        self.assertEqual(len(runs), 2)
        self.assertTrue((output / "dream-001.json").is_file())
        self.assertEqual(core.verify(runs[1])["config"]["policy"]["name"], "executable")

    def test_timed_out_developer_is_recorded_and_incumbent_survives(self):
        self.initialize()
        list(core.run_automatic(self.run))
        developer = self.base / "slow-developer.py"
        developer.write_text("import time\ntime.sleep(.4)\n")
        output = self.base / "timeout-evolution.json"
        selection = evolve.evolve([self.run], [sys.executable, str(developer)], revisions=1,
                                   timeout_seconds=.15, wall_seconds=2, output=output)
        self.assertIn("timed out", selection["revisions"][0]["error"])
        self.assertEqual(selection["selected_source_hash"], selection["initial_evaluation"]["policy"]["source_hash"])


if __name__ == "__main__":
    unittest.main()
