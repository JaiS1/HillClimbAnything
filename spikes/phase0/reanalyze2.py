"""Second offline re-analysis: a randomisation test on block scores.

Arm order is randomised inside every block, so under "no difference" the A/B labels within a
block are exchangeable and a sign-flip test on per-block scores is exact, whatever the noise
distribution.  This script compares, on recorded Phase 0 blocks:

  route_bt  - route as unit, bootstrap-t (the v0.2 spec; anti-conservative here)
  wsr       - Wilcoxon signed-rank on block scores, exact null over all sign flips,
              with a Hodges-Lehmann estimate and a Walsh-average lower bound

Block score: web = median over routes of log(medA/medB) in that block; cli = log(A/B).
Positive = B (candidate) faster.  Keep (as HCA would) = one-sided test passes at alpha.
"""
import itertools
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


def block_score(target, block):
    if target == "cli":
        return math.log(block["A"] / block["B"])
    return statistics.median(math.log(statistics.median(block["A"][r]) / statistics.median(block["B"][r]))
                             for r in block["A"])


_NULL_CACHE = {}


def wsr_null(n):
    """Exact null distribution of the signed-rank sum W+ for n untied ranks (counts per value)."""
    if n not in _NULL_CACHE:
        counts = [1]
        for rank in range(1, n + 1):
            nxt = [0] * (len(counts) + rank)
            for value, count in enumerate(counts):
                nxt[value] += count
                nxt[value + rank] += count
            counts = nxt
        _NULL_CACHE[n] = counts
    return _NULL_CACHE[n]


def wsr_lower_bound(scores, alpha):
    """Hodges-Lehmann estimate and the one-sided (1 - alpha) lower confidence bound from Walsh averages."""
    n = len(scores)
    walsh = sorted((scores[i] + scores[j]) / 2 for i in range(n) for j in range(i, n))
    counts = wsr_null(n)
    total = 2 ** n
    # smallest c with P(W+ <= c - 1) <= alpha ... the c-th smallest Walsh average is the bound
    cumulative, c = 0, 0
    for value, count in enumerate(counts):
        if (cumulative + count) / total > alpha:
            break
        cumulative += count
        c = value + 1
    if c == 0:
        return statistics.median(walsh), float("-inf")   # too few blocks for this alpha
    return statistics.median(walsh), walsh[c - 1]


def route_rows(blocks):
    rows = []
    for route in sorted(blocks[0]["A"]):
        a = statistics.median(statistics.median(b["A"][route]) for b in blocks)
        b = statistics.median(statistics.median(b["B"][route]) for b in blocks)
        rows.append({"id": route, "diff": math.log(a / b), "cluster": "item:" + route})
    return rows


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "web"
    sims = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    pool = load_blocks(target)
    rng = random.Random(20261015)
    alphas = (0.025, 0.01, 0.005)
    out = []
    for cond in ("aa", "b3", "b5"):
        blocks = pool[cond]
        for k in (5, 10, 15, 20):
            hits, hl = {}, []
            for sim in range(sims):
                chosen = rng.sample(blocks, k)
                scores = [block_score(target, b) for b in chosen]
                for alpha in alphas:
                    est, low = wsr_lower_bound(scores, alpha)
                    hits[f"wsr@{alpha}"] = hits.get(f"wsr@{alpha}", 0) + (low > 0)
                    # HCA keep: bound clears 0 AND estimate >= half the useful effect (5%)
                    hits[f"wsr_keep@{alpha}"] = hits.get(f"wsr_keep@{alpha}", 0) + (low > 0 and est >= 0.5 * math.log(1.05))
                hl.append(est)
                if target == "web":
                    low, _ = stats.bootstrap_interval(route_rows(chosen), 0.95, 499, sim)
                    hits["route_bt@0.025"] = hits.get("route_bt@0.025", 0) + (low > 0)
            row = {"target": target, "cond": cond, "k": k, "sims": sims, "pool_blocks": len(blocks),
                   "median_HL_pct": round(100 * (math.exp(statistics.median(hl)) - 1), 2)}
            row.update({key: round(value / sims, 3) for key, value in sorted(hits.items())})
            out.append(row)
            print(json.dumps(row), flush=True)
    with open(os.path.join(HERE, "results", f"reanalysis2_{target}.jsonl"), "w") as handle:
        for row in out:
            handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    main()
