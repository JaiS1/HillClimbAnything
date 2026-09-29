"""Deterministic local mutation adapter, not an LLM or an efficacy benchmark."""
import json
import os
from pathlib import Path
import random

packet = json.loads(Path(os.environ["DREAM_PACKET"]).read_text())
candidate = json.loads(Path("candidate.json").read_text())
rng = random.Random(211 + 101 * packet["branch"] + packet["depth"])
selected = set(candidate["selected"])
# A simple mutation sometimes fails feasibility; the harness must retain that evidence.
if selected and rng.random() < 0.3:
    selected.remove(rng.choice(sorted(selected)))
available = sorted(set(range(12)) - selected)
if available:
    selected.add(rng.choice(available))
Path("candidate.json").write_text(json.dumps({"selected": sorted(selected)}) + "\n")
Path("proposal.md").write_text("Add one item, occasionally replacing an existing item. Test value subject to the fixed capacity.\n")
