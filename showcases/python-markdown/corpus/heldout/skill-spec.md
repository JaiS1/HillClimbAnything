# `hca.toml` and the metric contract

The full schema is in the repo's `docs/ARCHITECTURE-v1.md`. This page covers the parts you write.

```toml
[task]
name     = "api-latency"
editable = ["src/**"]              # the only paths you may change
frozen   = ["bench/**"]            # evaluator, data, benchmark: hashed, never edited
link     = ["node_modules"]        # untracked dependencies the baseline arm needs

[build]
command = ["npm", "run", "build"]  # optional; once per arm

[objective]
command       = ["python3", "bench/run.py"]
metric        = "p95_ms"
direction     = "min"
useful_effect = "5%"               # the smallest win worth keeping

[[guards]]
name = "tests"
command = ["npm", "test"]
```

## What the objective command prints (stdout)

**One number.** Print one line per metric. Other `METRIC` names are mechanism or guard metrics.

```
METRIC p95_ms=212.4
METRIC bytes_sent=48211
```

**Many items** (pages, scenes, eval cases). Print the item JSON as the last line. Give `runs`
(repeated measurements) for timings, or `score` for deterministic per-item values.

```json
{"metric":"p95_ms","items":[{"id":"/cart","runs":[212.4,208.9,215.0],"tags":{"route":"cart"}}]}
```

Mark failures honestly:

- `"status":"candidate_invalid"`: the change broke this item. It is scored as the worst observed.
- `"status":"infra_invalid"`: the harness flaked. The item is dropped from both arms.

## Environment the command sees

| Variable | Meaning |
|---|---|
| `HCA_ARM` | `baseline` or `candidate` |
| `HCA_ITEMS` | `dev` normally, `heldout` during `hca confirm` |
| `HCA_HELDOUT_DIR` | where held-out items live during confirm |

Write the benchmark so it reads held-out items only when told to.

## Making a metric climbable

- **Measure inside one process** where possible, and **warm up** before timing.
- **Use more items** (pages, scenes, cases) rather than more repeats of one. Items also give you
  slices.
- **Reset state between runs**: caches, databases, temp files. Otherwise the second arm wins for
  free.
- **Fix the sources of variance** you can (CPU throttling, fixed seeds, fixed input traces) and
  record the ones you can't.
