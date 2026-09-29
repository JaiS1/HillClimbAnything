"""Small fixed, real CPU objective. No generated or fabricated scores."""
import json
from pathlib import Path

ITEMS = [(12, 24), (7, 13), (11, 23), (8, 15), (9, 16), (5, 10), (14, 28), (6, 11), (4, 7), (10, 19), (3, 6), (13, 25)]
CAPACITY = 45
METRIC = {"name": "knapsack_value", "direction": "max", "identity": "knapsack-12-cap45-v1"}

if __name__ == "__main__":
    try:
        chosen = json.loads(Path("candidate.json").read_text())["selected"]
        assert isinstance(chosen, list) and all(type(i) is int and 0 <= i < len(ITEMS) for i in chosen)
        assert len(set(chosen)) == len(chosen), "duplicate items"
        weight = sum(ITEMS[i][0] for i in chosen)
        assert weight <= CAPACITY, "capacity exceeded"
        result = {"status": "valid", "score": sum(ITEMS[i][1] for i in chosen), "summary": f"weight={weight}/{CAPACITY}"}
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        result = {"status": "invalid", "score": None, "summary": str(exc)}
    print(json.dumps({**result, "metric": METRIC}))
