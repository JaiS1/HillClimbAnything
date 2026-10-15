import math
import tempfile
import unittest
from pathlib import Path

from hca import spec
from hca.core import HcaError

MINIMAL = {
    "task": {"name": "checkout-latency", "editable": ["src/**"]},
    "objective": {"command": ["./bench/run.sh"], "metric": "lcp_ms", "direction": "min", "useful_effect": "5%"},
}

FULL = """
[task]
name = "checkout-latency"
brief = "BRIEF.md"
editable = ["src/**"]
frozen = ["bench/**"]
link = ["node_modules", ".venv"]

[build]
command = ["npm", "run", "build"]

[objective]
command = ["./bench/run.sh"]
metric = "lcp_ms"
direction = "min"
mode = "blocks"
useful_effect = "5%"
max_tries = 40

[[guards]]
name = "tests"
command = ["npm", "test"]

[[guards]]
name = "bundle"
metric = "bundle_kb"
max_regression = "2%"

[mechanism]
metrics = ["bytes_sent"]
slices = ["route"]

[limits]
command_seconds = 60
try_seconds = 900
blocks = 12
extend_to = 24
confirms = 3

[env]
deny = ["UV_*"]
"""


def modified(**changes):
    """MINIMAL with section updates, e.g. modified(objective={"direction": "up"})."""
    data = {k: dict(v) for k, v in MINIMAL.items()}
    for section, values in changes.items():
        if values is None:
            data.pop(section, None)
        elif isinstance(values, dict):
            data.setdefault(section, {}).update(values)
        else:
            data[section] = values
    return data


class SpecTests(unittest.TestCase):
    def assertRejects(self, data, *needles):
        with self.assertRaises(HcaError) as caught:
            spec.parse_spec(data)
        message = str(caught.exception)
        for needle in needles:
            self.assertIn(needle, message)
        self.assertIn("Fix:", message)

    def test_minimal_spec_gets_the_contract_defaults(self):
        s = spec.parse_spec(MINIMAL)
        self.assertEqual((s.task.brief, s.task.frozen, s.task.link), ("BRIEF.md", (), ()))
        self.assertIsNone(s.build)
        self.assertEqual((s.objective.mode, s.objective.max_tries), ("blocks", 50))
        self.assertEqual(s.limits, spec.Limits(300, 1800, 10, 20, 5))
        self.assertEqual(s.env.deny, ("MPLBACKEND", "UV_*", "PYTHONSTARTUP"))
        self.assertEqual((s.guards, s.mechanism), ((), spec.Mechanism((), ())))
        self.assertAlmostEqual(s.objective.useful, -math.log(0.95))      # "5%" for a metric to minimise: 0.95x the baseline

    def test_full_spec_round_trips_from_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hca.toml"
            path.write_text(FULL)
            s = spec.load_spec(path)
        self.assertEqual(s.build.command, ("npm", "run", "build"))
        self.assertEqual([g.name for g in s.guards], ["tests", "bundle"])
        self.assertEqual(s.guards[0].command, ("npm", "test"))
        self.assertAlmostEqual(s.guards[1].max_regression, 0.02)
        self.assertEqual((s.limits.blocks, s.limits.extend_to, s.limits.confirms), (12, 24, 3))
        self.assertEqual(s.mechanism.slices, ("route",))
        self.assertEqual(s.env.deny, ("UV_*",))
        with self.assertRaises(Exception):
            s.objective.metric = "other"          # frozen

    def test_unknown_key_typo_names_the_key_and_suggests_the_fix(self):
        data = modified(task={"editabel": ["src/**"]})
        self.assertRejects(data, "task.editabel", "unknown key", "editable")
        self.assertRejects(modified(objective={"directon": "min"}), "objective.directon", "direction")
        self.assertRejects(modified(extra={"x": 1}), "extra", "unknown key")

    def test_missing_required_fields_are_named(self):
        data = modified()
        del data["objective"]["metric"]
        self.assertRejects(data, "objective.metric", "required")
        self.assertRejects(modified(task=None), "task", "required")
        data = modified()
        del data["task"]["editable"]
        self.assertRejects(data, "task.editable", "required")

    def test_bad_values_are_named_with_a_fix(self):
        self.assertRejects(modified(objective={"direction": "down"}), "objective.direction", "min, max")
        self.assertRejects(modified(objective={"command": "python3 bench.py"}), "objective.command", "list of arguments")
        self.assertRejects(modified(objective={"command": []}), "objective.command")
        self.assertRejects(modified(objective={"useful_effect": 5}), "objective.useful_effect", '"5%"')
        self.assertRejects(modified(objective={"useful_effect": "0%"}), "objective.useful_effect")
        self.assertRejects(modified(objective={"mode": "seeds"}), "objective.mode", "later version")
        self.assertRejects(modified(objective={"max_tries": 0}), "objective.max_tries")
        self.assertRejects(modified(task={"name": "Bad Name"}), "task.name")
        self.assertRejects(modified(task={"editable": []}), "task.editable", "empty")
        self.assertRejects(modified(task={"editable": ["../outside/**"]}), "task.editable", "climbs")
        self.assertRejects(modified(task={"editable": ["hca.toml"]}), "hca.toml", "frozen")
        self.assertRejects(modified(env={"deny": "UV_*"}), "env.deny")

    def test_limits_are_checked(self):
        self.assertRejects(modified(limits={"blocks": 9}), "limits.blocks", "minimum 10")
        self.assertRejects(modified(limits={"blocks": True}), "limits.blocks", "integer")
        self.assertRejects(modified(limits={"blocks": 12, "extend_to": 11}), "limits.extend_to")
        self.assertEqual(spec.parse_spec(modified(limits={"extend_to": 0})).limits.extend_to, 0)
        self.assertEqual(spec.parse_spec(modified(limits={"blocks": 30})).limits.extend_to, 0)   # default cannot undercut blocks

    def test_guards_are_checked(self):
        both = modified(guards=[{"name": "g", "command": ["x"], "metric": "m", "max_regression": "2%"}])
        self.assertRejects(both, "guards[0]", "exactly one")
        self.assertRejects(modified(guards=[{"name": "g", "metric": "m"}]), "max_regression", "required")
        self.assertRejects(modified(guards=[{"name": "g", "metric": "m", "max_regression": 2}]), "max_regression", "percentage")
        self.assertRejects(modified(guards=[{"name": "g", "command": ["x"], "max_regression": "2%"}]), "max_regression")
        self.assertRejects(modified(guards=[{"name": "g", "command": ["x"]}, {"name": "g", "command": ["y"]}]), "unique")
        self.assertRejects(modified(guards=[{"name": "g", "comand": ["x"]}]), "guards[0].comand", "command")

    def test_items_mode_takes_a_bare_absolute_useful_effect(self):
        items = lambda useful: modified(objective={"mode": "items", "useful_effect": useful})
        s = spec.parse_spec(items(0.02))
        self.assertEqual((s.objective.useful_effect, s.objective.useful), (0.02, 0.02))     # score units, not a log ratio of a %
        self.assertEqual(spec.parse_spec(items(1)).objective.useful, 1.0)
        for bad in ("5%", 0, -1, True, "0.02"):
            self.assertRejects(items(bad), "objective.useful_effect", "bare number")
        self.assertRejects(modified(objective={"useful_effect": 0.05}), "objective.useful_effect", '"5%"')   # blocks mode still wants a percent

    def test_blocks_useful_effect_is_a_percent_better_in_the_metrics_own_terms(self):
        up = spec.parse_spec(modified(objective={"direction": "max", "useful_effect": "5%"}))
        self.assertAlmostEqual(up.objective.useful, math.log(1.05))                       # candidate = 1.05 x baseline
        down = spec.parse_spec(modified(objective={"direction": "min", "useful_effect": "5%"}))
        self.assertAlmostEqual(down.objective.useful, -math.log(0.95))                    # candidate = 0.95 x baseline
        self.assertRejects(modified(objective={"direction": "min", "useful_effect": "100%"}), "objective.useful_effect", "below 100%")
        self.assertEqual(spec.parse_spec(modified(objective={"direction": "max", "useful_effect": "100%"})).objective.useful_effect, "100%")

    def test_metric_guard_direction_defaults_to_lower_is_better(self):
        guard = lambda **extra: modified(guards=[{"name": "g", "metric": "m", "max_regression": "2%", **extra}])
        self.assertEqual(spec.parse_spec(guard()).guards[0].direction, "min")
        self.assertEqual(spec.parse_spec(guard(direction="max")).guards[0].direction, "max")
        self.assertRejects(guard(direction="up"), "guards[0].direction", "min, max")
        self.assertRejects(modified(guards=[{"name": "g", "command": ["x"], "direction": "max"}]), "guards[0].direction", "metric guard")

    def test_toml_and_missing_file_errors_are_friendly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hca.toml"
            with self.assertRaises(HcaError) as caught:
                spec.load_spec(path)
            self.assertIn("not found", str(caught.exception))
            path.write_text("[task\nname = ")
            with self.assertRaises(HcaError) as caught:
                spec.load_spec(path)
            self.assertIn("not valid TOML", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
