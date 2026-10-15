# `hca.toml` and the metric contract

The full schema is in the repo's `docs/ARCHITECTURE-v1.md`. This page covers the parts you write.

```toml
[task]
name     = "api-latency"
editable = ["src/**"]              # the only paths you may change
frozen   = ["bench/**"]            # evaluator, data, benchmark: hashed, never edited
link     = ["node_modules"]        # untracked or ignored data both arms need, symlinked into each

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

## What the objective command reports

**Recommended: write the result to the file named by `HCA_RESULT_FILE`.** The variable is set for every
run, to a path that is fresh each time. If the file is non-empty, HCA reads the result from it and ignores
stdout, so the candidate's own output cannot add to or change the number. The content is the same as the
stdout forms below. Without it, HCA reads stdout, and the objective metric must appear **exactly once**: a
second `METRIC <metric>` line (or item JSON next to a `METRIC` line for the objective) makes the run invalid,
and in the candidate arm that is scored as `candidate_invalid`.

**One number.** Print one line per metric. Other `METRIC` names are mechanism or guard metrics (the last
line wins for those).

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
- `"status":"infra_invalid"`: the harness flaked. The item is dropped from both arms, but only when the
  baseline's run of it is the invalid one. If the baseline scored the item fine and the candidate's run
  reports `infra_invalid`, it is scored as `candidate_invalid`.

## Environment the command sees

| Variable | Meaning |
|---|---|
| `HCA_RESULT_FILE` | file to write the result to (see above); fresh for every run |
| `HCA_ITEMS` | `dev` normally, `heldout` during `hca confirm` |
| `HCA_HELDOUT_DIR` | where held-out items live during confirm |

`HCA_ARM` is no longer set: a benchmark must not be able to tell which arm it is in, and the arm is
recorded in `runs.jsonl` only. Do not try to detect it from the working directory either.

Write the benchmark so it reads held-out items only when told to.

## Where the benchmark runs

Both arms run in detached git worktrees under `.hca/arms/<sha>/`: the baseline at the last keep and the
candidate at a snapshot commit (the last keep plus your editable files). Neither is your checkout, so:

- only **tracked** files exist in an arm. Ignored or untracked data the benchmark reads (a dataset, a
  model, `node_modules`, a venv) must be listed in `link`, which symlinks it into **both** arms. A `frozen`
  glob that matches a linked path protects it too (it is hashed, ignored or not);
- the `[build]` command runs once in each new arm and must write only untracked outputs. If it modifies a
  tracked file, or the benchmark does, HCA reports it (an arm is checked before and after every run, and
  recreated from its commit if it changed);
- each try starts with one discarded warm-up run per arm, so a cold cache is not measured;
- `__pycache__/`, `*.pyc` and `.DS_Store` are never treated as edits.

## Making a metric climbable

- **Measure inside one process** where possible, and **warm up** before timing.
- **Use more items** (pages, scenes, cases) rather than more repeats of one. Items also give you
  slices.
- **Reset state between runs**: caches, databases, temp files. Otherwise the second arm wins for
  free.
- **Fix the sources of variance** you can (CPU throttling, fixed seeds, fixed input traces) and
  record the ones you can't.
