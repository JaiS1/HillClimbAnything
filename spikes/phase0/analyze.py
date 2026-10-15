#!/usr/bin/env python3
"""Turn raw timings (driver.py) into verdicts using hca/paired.py (tools/dream_rsi/stats.py at run time), then SUMMARY.md.

Item scores are log(time) with direction "min", so the mean paired difference reported by
stats.decide is the mean log ratio log(A/B): positive means B (candidate) is faster.
Rule S: HCA spec (alpha .05, 50 comparisons, 20000 bootstrap, min_units 5, useful_effect log 1.05),
        KEEP iff verdict == promote AND gain >= 0.5*log(1.05).
Rule N: naive (alpha .05, 1 comparison, 4000 bootstrap), KEEP iff promote.
Rule R: raw, KEEP iff B's mean log score is lower than A's (mean gain > 0).
"""
import argparse
import json
import math
import statistics
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from hca import paired as stats  # noqa: E402 (was tools/dream_rsi/stats.py when the spike ran)

KS = (5, 10, 15)
ROUTES = ["/catalog", "/listing", "/search", "/report", "/table", "/tree", "/feed",
          "/widget", "/profile", "/stats", "/text", "/export"]
CONDS = ("aa", "b3", "b5")
COND_NAME = {"aa": "A/A", "b3": "A/B 3%", "b5": "A/B 5%"}
MIN_GAIN = 0.5 * math.log(1.05)
RULE_S = dict(alpha=0.05, comparisons=50, bootstrap=20000, min_units=5, useful_effect=math.log(1.05))
RULE_N = dict(alpha=0.05, comparisons=1, bootstrap=4000, min_units=5)


def scores(row, k):
    """Return (baseline_items, candidate_items) of log-time scores from the first k blocks."""
    blocks = row["blocks"][:k]
    if row["target"] == "web":
        base, cand = [], []
        for r in ROUTES:
            for arm, dst in (("A", base), ("B", cand)):
                med = statistics.median(statistics.median(b[arm][r]) for b in blocks)
                dst.append({"id": r, "score": math.log(med)})
    else:
        base = [{"id": "b%02d" % i, "score": math.log(b["A"])} for i, b in enumerate(blocks)]
        cand = [{"id": "b%02d" % i, "score": math.log(b["B"])} for i, b in enumerate(blocks)]
    return base, cand


def verdicts(row):
    out = []
    for k in KS:
        base, cand = scores(row, k)
        seed = zlib.crc32(("%s/%s/%d/%d" % (row["target"], row["cond"], row["try"], k)).encode())
        s = stats.decide(cand, base, "min", dict(RULE_S, seed=seed))
        n = stats.decide(cand, base, "min", dict(RULE_N, seed=seed))
        gain = s["mean_difference"]
        out.append({
            "target": row["target"], "cond": row["cond"], "try": row["try"], "k": k,
            "units": s["units"], "gain_log": gain, "gain_pct": (math.exp(gain) - 1) * 100,
            "verdict_S": s["verdict"], "verdict_N": n["verdict"],
            "keep_S": s["verdict"] == "promote" and gain >= MIN_GAIN,
            "keep_N": n["verdict"] == "promote",
            "keep_R": gain > 0,
            "interval_S": s["interval"], "interval_N": n["interval"],
            "half_width_S_pct": None if s["half_width"] is None else s["half_width"] * 100,
            "half_width_N_pct": None if n["half_width"] is None else n["half_width"] * 100,
            "wall_s": sum(b["wall_s"] for b in row["blocks"][:k]),
            "try_wall_s_total": row["wall_s"],
            "load_start": row["load_start"], "load_end": row["load_end"], "started": row["started"],
        })
    return out


def per_block_logratio(row):
    """log(A/B) per block (web: mean over routes of log of per-block route medians)."""
    vals = []
    for b in row["blocks"]:
        if row["target"] == "web":
            vals.append(statistics.mean(math.log(statistics.median(b["A"][r]) / statistics.median(b["B"][r]))
                                        for r in ROUTES))
        else:
            vals.append(math.log(b["A"] / b["B"]))
    return vals


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else float("nan")


def fmt_rate(rows, key):
    return "%d/%d" % (sum(r[key] for r in rows), len(rows))


def anomalies(rows_by_target):
    lines = []
    for target, rows in rows_by_target.items():
        rows = sorted(rows, key=lambda r: r["started"])
        # drift: per-try mean of log(A time) over the sequence of tries (A is the same code in every condition)
        xs, ys = [], []
        spikes = tot = 0
        for i, r in enumerate(rows):
            if target == "web":
                a = [math.log(statistics.median(b["A"][rt])) for b in r["blocks"] for rt in ROUTES]
                for rt in ROUTES:
                    allv = [v for b in r["blocks"] for arm in "AB" for v in b[arm][rt]]
                    m = statistics.median(allv)
                    spikes += sum(v > 1.5 * m for v in allv)
                    tot += len(allv)
            else:
                a = [math.log(b["A"]) for b in r["blocks"]]
            xs.append(i)
            ys.append(statistics.mean(a))
        if len(xs) > 2:
            mx, my = statistics.mean(xs), statistics.mean(ys)
            slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
            lines.append("- %s drift of baseline-arm log time across the run: %+.2f%% per 10 tries "
                         "(first third mean %.3f ms, last third mean %.3f ms)." % (
                             target, slope * 10 * 100,
                             math.exp(statistics.mean(ys[:len(ys) // 3])), math.exp(statistics.mean(ys[-(len(ys) // 3):]))))
        if target == "web":
            lines.append("- web: %d of %d individual requests (%.2f%%) were > 1.5x their route's median "
                         "(latency spikes)." % (spikes, tot, 100 * spikes / tot))
        # order effect in A/A: is the arm run second systematically slower/faster?
        first_minus_second = []
        for r in rows:
            if r["cond"] != "aa":
                continue
            for b in r["blocks"]:
                if target == "web":
                    d = statistics.mean(math.log(statistics.median(b["A"][rt]) / statistics.median(b["B"][rt]))
                                        for rt in ROUTES)
                else:
                    d = math.log(b["A"] / b["B"])
                first_minus_second.append(d if b["order"] == "AB" else -d)  # log(first/second)
        if first_minus_second:
            m = statistics.mean(first_minus_second)
            se = statistics.stdev(first_minus_second) / math.sqrt(len(first_minus_second))
            lines.append("- %s A/A order effect: first-run arm is %+.2f%% (+-%.2f%% s.e., %d blocks) vs second-run arm "
                         "(positive = second arm slower). Order is randomised so this does not bias the "
                         "estimate, but it adds variance." % (target, m * 100, se * 100, len(first_minus_second)))
        # outlier blocks
        allr = [v for r in rows for v in per_block_logratio(r)]
        mu, sd = statistics.mean(allr), statistics.stdev(allr)
        mad = statistics.median(abs(v - statistics.median(allr)) for v in allr) * 1.4826
        out = [v for v in allr if abs(v - statistics.median(allr)) > 4 * mad]
        lines.append("- %s block-level log(A/B): sd %.2f%% (robust sd %.2f%%), %d of %d blocks beyond 4 robust sd "
                     "(max |dev| %.1f%%)." % (target, sd * 100, mad * 100, len(out), len(allr),
                                              100 * max(abs(v - statistics.median(allr)) for v in allr)))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timings", nargs="+", required=True)
    ap.add_argument("--out-dir", default=str(HERE / "results"))
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    rows = []
    for f in args.timings:
        rows += [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
    by_target = {}
    for r in rows:
        by_target.setdefault(r["target"], []).append(r)

    verdict_rows = []
    for r in rows:
        verdict_rows += verdicts(r)
        print("analysed", r["target"], r["cond"], r["try"], file=sys.stderr, flush=True)
    with open(out_dir / "raw.jsonl", "w") as fh:
        for v in verdict_rows:
            fh.write(json.dumps(v) + "\n")

    md = ["# Phase 0 calibration: noise-aware keep rule on this machine", "",
          "Generated by `analyze.py` from `results/timings_*.jsonl` (raw timings) and `results/raw.jsonl` "
          "(one row per try x condition x k). Seed %s. Score = log(time); direction min; gain = mean log(A/B) "
          "(positive = B faster)." % "20261014", "",
          "Rule S = HCA spec (alpha .05 over 50 comparisons, i.e. 99.9%% interval, 20000 bootstrap, min_units 5, "
          "KEEP iff promote and gain >= 0.5*log(1.05) = %.2f%%). Rule N = naive (95%% interval, 4000 bootstrap, "
          "KEEP iff promote). Rule R = raw (KEEP iff B's mean is better). Half-width = interval half-width on the "
          "log-ratio scale, shown as %% (S: 99.9%%, N: 95%%). Wall = cumulative seconds for the first k blocks "
          "of a try (median over tries)." % (MIN_GAIN * 100), ""]
    for target in ("web", "cli"):
        if target not in by_target:
            continue
        unit = "12 routes (items)" if target == "web" else "blocks (repeats)"
        md += ["## Target: %s (%s)" % (target, unit), "",
               "| condition | k blocks | tries | Rule S keep | Rule N keep | Rule R keep | median gain % | gain % IQR | "
               "median hw S % | median hw N % | median wall s/try |",
               "|---|---|---|---|---|---|---|---|---|---|---|"]
        for cond in CONDS:
            for k in KS:
                vs = [v for v in verdict_rows if v["target"] == target and v["cond"] == cond and v["k"] == k]
                if not vs:
                    continue
                g = sorted(v["gain_pct"] for v in vs)
                q1, q3 = g[len(g) // 4], g[(3 * len(g)) // 4]
                md.append("| %s | %d | %d | %s | %s | %s | %.2f | %.2f..%.2f | %.2f | %.2f | %.1f |" % (
                    COND_NAME[cond], k, len(vs), fmt_rate(vs, "keep_S"), fmt_rate(vs, "keep_N"), fmt_rate(vs, "keep_R"),
                    statistics.median(g), q1, q3, med([v["half_width_S_pct"] for v in vs]),
                    med([v["half_width_N_pct"] for v in vs]), med([v["wall_s"] for v in vs])))
        md.append("")
        # achieved effect from the full 15-block data of every try
        md += ["### Achieved effect (pooled over all tries x blocks, %s)" % target, "",
               "| condition | tries | blocks | mean speedup % (B vs A) | s.e. % (across tries) | minus A/A % |", "|---|---|---|---|---|---|"]
        eff = {}
        for cond in CONDS:
            rs = [r for r in by_target[target] if r["cond"] == cond]
            if not rs:
                continue
            per_try = [statistics.mean(per_block_logratio(r)) for r in rs]
            eff[cond] = (statistics.mean(per_try), (statistics.stdev(per_try) if len(per_try) > 1 else float("nan")) / math.sqrt(len(per_try)),
                         len(rs), sum(len(r["blocks"]) for r in rs))
        for cond, (m, se, nt, nb) in eff.items():
            aa = eff.get("aa", (0,))[0]
            md.append("| %s | %d | %d | %.2f | %.2f | %.2f |" % (
                COND_NAME[cond], nt, nb, (math.exp(m) - 1) * 100, se * 100, (math.exp(m - aa) - 1) * 100))
        md += ["", "### Load average (WSL /proc/loadavg, 1-min; includes this benchmark's own ~1 runnable thread)", "",
               "| condition | load at try start (median) | load at try end (median) | max load at end |", "|---|---|---|---|"]
        for cond in CONDS:
            rs = [r for r in by_target[target] if r["cond"] == cond]
            if rs:
                md.append("| %s | %.2f | %.2f | %.2f |" % (
                    COND_NAME[cond], med([r["load_start"][0] for r in rs]), med([r["load_end"][0] for r in rs]),
                    max(r["load_end"][0] for r in rs)))
        md.append("")
    md += ["## Anomalies and diagnostics (computed)", ""] + anomalies(by_target) + [""]
    notes = out_dir / "notes_manual.md"
    if notes.exists():
        md += [notes.read_text(), ""]
    (out_dir / "SUMMARY.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main()
