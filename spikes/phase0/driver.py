#!/usr/bin/env python3
"""Phase 0 benchmark driver: collects raw timings only (no statistics are computed here).

Run the whole thing under `nice -n 19` so every child inherits it. Strictly one job at a
time. The web server is started as this process's own child (sequentially, never two at
once) and killed by PID. See README.md for the exact commands.

Targets
  web : two sequential server processes (A baseline / B less CPU work per request); 12 routes.
  cli : `python -m compileall -f -q <tree>` wall time; B tree has some files removed.
Conditions: aa (B identical to A), b3 (B is 1.03x faster), b5 (B is 1.05x faster).
"""
import argparse
import atexit
import json
import math
import os
import random
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
SERVER = HERE / "server.py"
PORT = 18765
SEED = 20261014
ROUTES = ["/catalog", "/listing", "/search", "/report", "/table", "/tree", "/feed",
          "/widget", "/profile", "/stats", "/text", "/export"]
CONDS = ["aa", "b3", "b5"]
SPEEDUP = {"aa": 1.0, "b3": 1.03, "b5": 1.05}
CLI_ROOT = Path("/tmp/phase0_cli")
KNOBS = RESULTS / "knobs.json"

_children = []


def _cleanup():
    for p in _children:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()


atexit.register(_cleanup)
for _sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_sig, lambda *_: sys.exit(130))


def loadavg():
    with open("/proc/loadavg") as fh:
        return [float(x) for x in fh.read().split()[:3]]


def load_knobs():
    return json.loads(KNOBS.read_text()) if KNOBS.exists() else {}


def save_knobs(update):
    k = load_knobs()
    k.update(update)
    KNOBS.write_text(json.dumps(k, indent=2))


# ---------------------------------------------------------------- web target

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(route):
    t = time.perf_counter()
    with OPENER.open("http://127.0.0.1:%d%s" % (PORT, route), timeout=30) as r:
        r.read()
    return (time.perf_counter() - t) * 1000.0


def start_server(scale):
    with socket.socket() as s:  # make sure the port is free: never talk to someone else's server
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", PORT))
    p = subprocess.Popen([sys.executable, str(SERVER), "--port", str(PORT), "--scale", repr(scale)],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    _children.append(p)
    line = p.stdout.readline().strip()
    if line != "READY":
        raise RuntimeError("server did not start: %r" % line)
    return p


def stop_server(p):
    p.terminate()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    p.stdout.close()
    _children.remove(p)


def web_arm(scale, rng, warm=3, reps=5):
    """Start a server at `scale`, warm up, collect `reps` timings per route (interleaved rounds)."""
    p = start_server(scale)
    try:
        for _ in range(warm):
            order = ROUTES[:]
            rng.shuffle(order)
            for r in order:
                get(r)
        samples = {r: [] for r in ROUTES}
        for _ in range(reps):
            order = ROUTES[:]
            rng.shuffle(order)
            for r in order:
                samples[r].append(round(get(r), 4))
    finally:
        stop_server(p)
    return samples


def web_block(scale_a, scale_b, rng):
    t0 = time.perf_counter()
    order = "AB" if rng.random() < 0.5 else "BA"
    out = {"order": order}
    for arm in order:
        out[arm] = web_arm(scale_a if arm == "A" else scale_b, rng)
    out["wall_s"] = round(time.perf_counter() - t0, 3)
    return out


# ---------------------------------------------------------------- cli target

def cli_cmd(tree):
    return [sys.executable, "-m", "compileall", "-f", "-q", str(tree)]


def cli_time(tree):
    t = time.perf_counter()
    r = subprocess.run(cli_cmd(tree), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    dt = time.perf_counter() - t
    if r.returncode != 0:
        raise RuntimeError("compileall failed on %s" % tree)
    return dt * 1000.0


def cli_block(tree_a, tree_b, rng):
    t0 = time.perf_counter()
    order = "AB" if rng.random() < 0.5 else "BA"
    out = {"order": order}
    for arm in order:
        out[arm] = round(cli_time(tree_a if arm == "A" else tree_b), 3)
    out["wall_s"] = round(time.perf_counter() - t0, 3)
    return out


def cli_prep(args):
    """Build /tmp/phase0_cli/{A,A2,B3,B5}. A = real stdlib files; B_x = A minus files worth x% of time."""
    import sysconfig
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    rng = random.Random(SEED)
    cands = sorted(p for p in stdlib.rglob("*.py")
                   if "test" not in p.parts and "site-packages" not in p.parts
                   and "lib2to3" not in p.parts and "idlelib" not in p.parts
                   and 2000 < p.stat().st_size < 90000)
    rng.shuffle(cands)
    cost = {}
    for p in cands:
        src = p.read_bytes()
        try:
            best = min(_compile_ms(src, str(p)) for _ in range(2))
        except (SyntaxError, ValueError):
            continue
        cost[p] = best
        if sum(cost.values()) >= args.target_ms:
            break
    files = list(cost)
    est_total = sum(cost.values())
    if CLI_ROOT.exists():
        shutil.rmtree(CLI_ROOT)

    def build(name, keep):
        for p in keep:
            dst = CLI_ROOT / name / p.relative_to(stdlib)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)

    build("A", files)
    build("A2", files)
    (CLI_ROOT / "empty").mkdir()
    # measure A's command time and fixed overhead to convert estimated compile ms into real ms
    for _ in range(3):
        cli_time(CLI_ROOT / "A")
    ta = statistics.median(cli_time(CLI_ROOT / "A") for _ in range(7))
    f0 = statistics.median(cli_time(CLI_ROOT / "empty") for _ in range(7))
    scale = (ta - f0) / est_total
    knobs = {"cli_files_A": len(files), "cli_T_A_ms": round(ta, 2), "cli_fixed_ms": round(f0, 2),
             "cli_est_to_real": round(scale, 4)}
    for cond, mult in (("b3", args.mult3), ("b5", args.mult5)):
        target = (1 - 1 / SPEEDUP[cond]) * ta * mult / scale  # estimated compile-ms to remove
        removed, acc = [], 0.0
        for p in sorted(files, key=lambda q: -cost[q]):  # greedy, biggest first, never overshoot
            if acc + cost[p] <= target + 0.15 / scale:
                removed.append(p)
                acc += cost[p]
        keep = [p for p in files if p not in set(removed)]
        build("B" + cond[1], keep)
        knobs["cli_removed_" + cond] = len(removed)
        knobs["cli_removed_ms_est_" + cond] = round(acc * scale, 2)
    save_knobs(knobs)
    print(json.dumps(knobs, indent=2))


def _compile_ms(src, name):
    t = time.perf_counter()
    compile(src, name, "exec")
    return (time.perf_counter() - t) * 1000.0


# ---------------------------------------------------------------- orchestration

def done_keys(path):
    keys = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                keys.add((row["target"], row["cond"], row["try"]))
    return keys


def run_tries(args):
    target = args.target
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    knobs = load_knobs()
    done = done_keys(out)
    start = time.perf_counter()
    sched = random.Random("%s/sched" % SEED)
    for t in range(args.tries):
        conds = CONDS[:]
        sched.shuffle(conds)
        if time.perf_counter() - start > args.deadline_s:
            print("deadline reached before try %d: stopping" % t, flush=True)
            break
        for cond in conds:
            if (target, cond, t) in done:
                continue
            rng = random.Random("%s/%s/%s/%d" % (SEED, target, cond, t))
            if target == "web":
                sb = 1.0 if cond == "aa" else knobs["web_scale_" + cond]
                run = lambda: web_block(1.0, sb, rng)
            else:
                tb = CLI_ROOT / ("A2" if cond == "aa" else "B" + cond[1])
                run = lambda: cli_block(CLI_ROOT / "A", tb, rng)
            la = loadavg()
            t0 = time.perf_counter()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
            blocks = [run() for _ in range(args.blocks)]
            wall = time.perf_counter() - t0
            row = {"target": target, "cond": cond, "try": t, "seed": SEED, "started": stamp,
                   "wall_s": round(wall, 3), "load_start": la, "load_end": loadavg(),
                   "blocks": blocks}
            with open(out, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            print("%s %s try %d: %.1fs (elapsed %.0fs) load %s" % (
                target, cond, t, wall, time.perf_counter() - start, row["load_end"]), flush=True)


def calib(args):
    """Long run of A (scale 1) vs a candidate B knob; prints the achieved effect."""
    rng = random.Random("%s/calib/%s/%s" % (SEED, args.target, args.scale))
    per_block = []
    allA, allB = [], []
    if args.target == "web":
        for _ in range(args.blocks):
            b = web_block(1.0, args.scale, rng)
            m = {arm: {r: statistics.median(b[arm][r]) for r in ROUTES} for arm in "AB"}
            per_block.append(statistics.mean(math.log(m["A"][r] / m["B"][r]) for r in ROUTES))
            allA.append(sum(m["A"].values()))
            allB.append(sum(m["B"].values()))
        # headline estimate: log ratio of route medians across blocks, averaged over routes
    else:
        tb = CLI_ROOT / args.tree
        for _ in range(args.blocks):
            b = cli_block(CLI_ROOT / "A", tb, rng)
            per_block.append(math.log(b["A"] / b["B"]))
            allA.append(b["A"])
            allB.append(b["B"])
    mean = statistics.mean(per_block)
    se = statistics.stdev(per_block) / math.sqrt(len(per_block))
    res = {"target": args.target, "knob": args.scale if args.target == "web" else args.tree,
           "blocks": args.blocks, "mean_log_ratio": round(mean, 5), "speedup_pct": round((math.exp(mean) - 1) * 100, 3),
           "se_log": round(se, 5), "block_sd_log": round(statistics.stdev(per_block), 5),
           "median_A_ms": round(statistics.median(allA), 2), "median_B_ms": round(statistics.median(allB), 2)}
    print(json.dumps(res), flush=True)
    with open(RESULTS / "calibration.jsonl", "a") as fh:
        fh.write(json.dumps(dict(res, when=time.strftime("%Y-%m-%dT%H:%M:%S"))) + "\n")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cli-prep")
    p.add_argument("--target-ms", type=float, default=380.0)
    p.add_argument("--mult3", type=float, default=1.0)
    p.add_argument("--mult5", type=float, default=1.0)
    p = sub.add_parser("calib")
    p.add_argument("--target", choices=["web", "cli"], required=True)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--tree", default="B3")
    p.add_argument("--blocks", type=int, default=30)
    p = sub.add_parser("run")
    p.add_argument("--target", choices=["web", "cli"], required=True)
    p.add_argument("--tries", type=int, default=20)
    p.add_argument("--blocks", type=int, default=15)
    p.add_argument("--out", required=True)
    p.add_argument("--deadline-s", type=float, default=1e9)
    p = sub.add_parser("set-knob")
    p.add_argument("key")
    p.add_argument("value", type=float)
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    if args.cmd == "cli-prep":
        cli_prep(args)
    elif args.cmd == "calib":
        calib(args)
    elif args.cmd == "set-knob":
        save_knobs({args.key: args.value})
    else:
        run_tries(args)


if __name__ == "__main__":
    main()
