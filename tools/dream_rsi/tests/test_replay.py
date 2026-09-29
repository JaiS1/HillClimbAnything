import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from tools.dream_rsi.replay import replay, select_action, validate_world


def world(direction="max"):
    return {
        "schema_version": 1,
        "world_id": "synthetic",
        "metric": {"name": "score", "direction": direction, "identity": "score-v1"},
        "root": {"id": "root", "score": 10.0 if direction == "max" else 5.0},
        "nodes": [
            {"id": "a", "parent_id": "root", "context_ids": [], "sequence": 1, "status": "valid", "score": 11.0 if direction == "max" else 4.0, "cost_seconds": 2.0, "artifact_hash": "a"},
            {"id": "b", "parent_id": "root", "context_ids": [], "sequence": 2, "status": "valid", "score": 9.0 if direction == "max" else 6.0, "cost_seconds": 3.0, "artifact_hash": "b"},
        ],
    }


class ReplayTests(unittest.TestCase):
    def test_hidden_scores_cannot_change_first_action(self):
        first = select_action({"root": world()["root"], "nodes": [], "metric": world()["metric"]}, {"name": "greedy", "max_attempts": 3, "width": 2, "patience": 2})
        poisoned = world()
        poisoned["nodes"][0]["score"] = -999999.0
        poisoned["nodes"][1]["score"] = 999999.0
        second = select_action({"root": poisoned["root"], "nodes": [], "metric": poisoned["metric"]}, {"name": "greedy", "max_attempts": 3, "width": 2, "patience": 2})
        self.assertEqual((first, second), ("root", "root"))

    def test_missing_context_is_unsupported(self):
        data = world()
        data["nodes"].append({"id": "a2", "parent_id": "a", "context_ids": ["b"], "sequence": 3,
                              "status": "valid", "score": 12.0, "cost_seconds": 1.0, "artifact_hash": "a2"})
        result = replay(data, {"name": "round_robin", "max_attempts": 3, "width": 1, "patience": 2})
        self.assertEqual(result["stop_reason"], "unsupported")
        self.assertEqual(result["unsupported_reason"], "missing_context:b")
        self.assertEqual(result["attempts"], 1)

    def test_empty_tail_is_unsupported_not_zero_gain(self):
        result = replay(world(), {"name": "round_robin", "max_attempts": 3, "width": 1, "patience": 2})
        self.assertEqual(result["stop_reason"], "unsupported")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["best_score"], 11.0)
        self.assertEqual(result["improvement"], 1.0)

    def test_invalid_fabricated_score_is_rejected_and_null_retains_root(self):
        data = world()
        data["nodes"][0].update(status="invalid", score=999.0, artifact_hash=None)
        with self.assertRaises(ValueError):
            validate_world(data)
        data["nodes"][0]["score"] = None
        result = replay(data, {"name": "explore", "max_attempts": 1, "width": 1, "patience": 2})
        self.assertEqual(result["best_score"], 10.0)
        self.assertEqual(result["cost_seconds"], 2.0)

    def test_round_robin_live_prefix_matches_replay_and_ignores_future_poison(self):
        data = world()
        data["nodes"].extend([
            {"id": "a2", "parent_id": "a", "context_ids": [], "sequence": 3, "status": "valid", "score": 12.0, "cost_seconds": 1.0, "artifact_hash": "a2"},
            {"id": "b2", "parent_id": "b", "context_ids": [], "sequence": 4, "status": "valid", "score": 8.0, "cost_seconds": 1.0, "artifact_hash": "b2"},
        ])
        policy = {"name": "round_robin", "max_attempts": 4, "width": 2, "patience": 2}
        recorded = replay(data, policy)
        observed, live_actions = [], []
        for node in data["nodes"]:
            live_actions.append(select_action({"root": data["root"], "nodes": observed, "metric": data["metric"]}, policy))
            observed.append(node)
        self.assertEqual(live_actions, [item["action"] for item in recorded["trace"]])
        prefix = {"root": data["root"], "nodes": data["nodes"][:3], "metric": data["metric"]}
        poisoned = copy.deepcopy(data)
        poisoned["nodes"][3].update(score=999999.0, cost_seconds=999999.0)
        poisoned_prefix = {"root": poisoned["root"], "nodes": poisoned["nodes"][:3], "metric": poisoned["metric"]}
        self.assertEqual(select_action(prefix, policy), "b")
        self.assertEqual(select_action(poisoned_prefix, policy), "b")

    def test_min_metric_and_schema_checks(self):
        result = replay(world("min"), {"name": "explore", "max_attempts": 1, "width": 1, "patience": 2})
        self.assertEqual((result["best_score"], result["improvement"]), (4.0, 1.0))
        malformed = copy.deepcopy(world())
        malformed["nodes"][0]["score"] = float("nan")
        with self.assertRaises(ValueError):
            validate_world(malformed)
        mismatch = world()
        mismatch["nodes"][0]["metric_identity"] = "other"
        with self.assertRaises(ValueError):
            validate_world(mismatch)
        bad_order = world()
        bad_order["nodes"].append({"id": "a2", "parent_id": "a", "context_ids": [], "sequence": 3,
                                    "status": "valid", "score": 12.0, "cost_seconds": 1.0, "artifact_hash": "a2"})
        bad_order["nodes"][0]["sequence"] = 4
        with self.assertRaises(ValueError):
            validate_world(bad_order)

    def test_executable_policy_sees_only_observed_prefix_and_rejects_bad_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "policy.py"
            source.write_text("""import json, sys
r = json.load(sys.stdin)
assert 'result' not in r['root']
assert all('artifact_hash' not in n and 'result' not in n for n in r['nodes'])
print(json.dumps({'action': 'root' if not r['nodes'] else 'missing'}))
""")
            policy = {"name": "executable", "source_file": str(source),
                      "source_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "timeout_seconds": 1, "max_attempts": 3, "width": 1, "patience": 1}
            self.assertEqual(select_action({"root": world()["root"], "nodes": [], "metric": world()["metric"]}, policy), "root")
            with self.assertRaisesRegex(ValueError, "unavailable action"):
                select_action({"root": world()["root"], "nodes": [world()["nodes"][0]], "metric": world()["metric"]}, policy)

    def test_replay_deadline_clamps_each_executable_policy_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "slow.py"
            source.write_text("import json, sys, time\njson.load(sys.stdin)\ntime.sleep(.2)\nprint('{\\\"action\\\": null}')\n")
            policy = {"name": "executable", "source_file": str(source),
                      "source_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                      "timeout_seconds": 1, "max_attempts": 3, "width": 1, "patience": 1}
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "timed out"):
                replay(world(), policy, deadline=started + .03)
            self.assertLess(time.monotonic() - started, .15)


if __name__ == "__main__":
    unittest.main()
