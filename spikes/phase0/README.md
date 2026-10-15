# Phase 0: does a noise-aware keep rule work on this machine?

Question: on this WSL box (shared with a gaming PC), does the HCA keep rule (a) rarely keep a
change that does nothing (A/A false-keep rate) and (b) detect real 3% and 5% speedups, and at what
wall time per try?

Files

- `server.py` - target 1: stdlib `ThreadingHTTPServer`, 12 routes, `--scale` knob (data size multiplier).
- `driver.py` - collects raw timings only. Everything runs strictly one job at a time under `nice -n 19`.
- `analyze.py` - applies `hca/paired.py (ported unchanged from tools/dream_rsi/stats.py, which the spike used at commit 7291206)` (`decide`) with Rules S / N / R, writes `results/raw.jsonl` and `results/SUMMARY.md`.
- `results/` - `timings_web.jsonl`, `timings_cli.jsonl` (raw timings), `raw.jsonl` (verdict rows),
  `SUMMARY.md`, `calibration.jsonl` (knob calibration long runs), `knobs.json`.

Seed: `20261014` (arm order and route order per block are derived from it; `random.Random("<seed>/<target>/<cond>/<try>")`).
Bootstrap seed in `analyze.py` is a crc32 of target/cond/try/k. Python 3.11.9 stdlib only.

"3%" means B is 1.03x faster (A/B time ratio 1.03), "5%" means 1.05x, matching `useful_effect = log(1.05)`.

## Reproduce

```bash
cd ~/dream-loop/spikes/phase0
PY=/home/jai/.pyenv/versions/3.11.9/bin/python3      # use the real interpreter, not the pyenv shim

# knobs (web: B data-size scale; cli: removed files). These values were calibrated by measurement.
nice -n 19 $PY driver.py set-knob web_scale_b3 0.9669
nice -n 19 $PY driver.py set-knob web_scale_b5 0.9459
nice -n 19 $PY driver.py cli-prep --target-ms 330          # builds /tmp/phase0_cli/{A,A2,B3,B5}, writes knobs.json

# optional: calibration long runs (30 blocks)
for s in 1.0 0.9669 0.9459; do nice -n 19 $PY driver.py calib --target web --scale $s --blocks 30; done
for t in A2 B3 B5;          do nice -n 19 $PY driver.py calib --target cli --tree $t --blocks 30; done

# full run (about 35 min): web 12 tries x 3 conditions x 15 blocks, then cli 20 tries x 3 x 15 blocks
nice -n 19 $PY driver.py run --target web --tries 12 --blocks 15 --out results/timings_web.jsonl --deadline-s 1150
nice -n 19 $PY driver.py run --target cli --tries 20 --blocks 15 --out results/timings_cli.jsonl --deadline-s 930

# statistics (run after the benchmarks finish, CPU heavy: 20000-draw bootstrap)
nice -n 19 $PY analyze.py --timings results/timings_web.jsonl results/timings_cli.jsonl

# smoke run (about 1 min)
nice -n 19 $PY driver.py run --target web --tries 1 --blocks 5 --out /tmp/smoke_web.jsonl
nice -n 19 $PY driver.py run --target cli --tries 1 --blocks 5 --out /tmp/smoke_cli.jsonl
nice -n 19 $PY analyze.py --timings /tmp/smoke_web.jsonl /tmp/smoke_cli.jsonl --out-dir /tmp
```

Protocol per try: 15 blocks. Web: per block start server for arm X (random order), 3 warm-up rounds over the
12 routes, 5 timed rounds (route order shuffled each round), record per-route median, kill the server by PID,
then the other arm. Item score = median over the first k blocks of per-block route medians, log-transformed.
Cli: per block one `python -m compileall -f -q <tree>` run per arm (random order); unit = block. Verdicts are
computed at k = 5, 10, 15 blocks. Conditions are interleaved within each try index (random order) so drift hits
all three equally.
