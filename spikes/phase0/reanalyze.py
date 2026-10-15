"""Offline re-analysis of Phase 0 timings: is route-as-unit anti-conservative, and does a
block-as-unit estimator fix it?  No new benchmark runs; resamples recorded blocks.

For each condition, pool every recorded block (each block holds per-route request times for
arm A and arm B, measured back to back in random order).  Simulate tries by drawing k blocks
without replacement, then compute two paired estimators of the mean log ratio (A over B,
so a faster B is positive):

  route  - unit = route, item score = median over the try's blocks of the per-block median
           (what Phase 0 and the v0.2 spec do);
  block  - unit = block, block score = mean over routes of log(medA/medB) in that block.

Reports the share of simulated tries whose lower bound clears zero at one-sided alpha.
"""
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from hca import paired as stats  # noqa: E402

HERE = os.path.dirname(__file__)


def load_blocks(target):
    pool = {}
    with open(os.path.join(HERE, "results", f"timings_{target}.jsonl")) as handle:
        for line in handle:
            row = json.loads(line)
            pool.setdefault(row["cond"], []).extend(row["blocks"])
    return pool


def route_rows(blocks):
    routes = sorted(blocks[0]["A"])
    rows = []
    for route in routes:
        a = statistics.median(statistics.median(b["A"][route]) for b in blocks)
        b = statistics.median(statistics.median(b["B"][route]) for b in blocks)
        rows.append({"id": route, "diff": math.log(a / b), "cluster": "item:" + route})
    return rows


def block_rows(blocks):
    rows = []
    for index, block in enumerate(blocks):
        logs = [math.log(statistics.median(block["A"][r]) / statistics.median(block["B"][r])) for r in block["A"]]
        rows.append({"id": str(index), "diff": sum(logs) / len(logs), "cluster": f"block:{index}"})
    return rows


def lower_bound(rows, alpha, draws, seed):
    level = 1 - 2 * alpha          # two-sided interval whose lower bound is a one-sided test
    low, _ = stats.bootstrap_interval(rows, level, draws, seed)
    return low


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "web"
    sims = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    draws = int(sys.argv[3]) if len(sys.argv) > 3 else 999
    pool = load_blocks(target)
    rng = random.Random(20261015)
    out = []
    for cond in ("aa", "b3", "b5"):
        blocks = pool[cond]
        for k in (10, 15):
            hits = {("route", 0.025): 0, ("route", 0.005): 0, ("block", 0.025): 0, ("block", 0.005): 0}
            for sim in range(sims):
                chosen = rng.sample(blocks, k)
                for name, rows in (("route", route_rows(chosen)), ("block", block_rows(chosen))):
                    for alpha in (0.025, 0.005):
                        if lower_bound(rows, alpha, draws, sim) > 0:
                            hits[(name, alpha)] += 1
            row = {"target": target, "cond": cond, "k": k, "sims": sims, "pool_blocks": len(blocks)}
            row.update({f"{n}@{a}": round(v / sims, 3) for (n, a), v in hits.items()})
            out.append(row)
            print(json.dumps(row), flush=True)
    with open(os.path.join(HERE, "results", f"reanalysis_{target}.jsonl"), "w") as handle:
        for row in out:
            handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
