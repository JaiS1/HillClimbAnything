# Commands and adapter contracts

## Find the harness

For an installed package, use `python3 -m dream_rsi --help`. For a checkout with
`tools/dream_rsi/`, run `python3 -m tools.dream_rsi --help` from its root. Use one
prefix consistently below. The examples use the installed-package prefix.

Install from a verified checkout, preferably in a local virtual environment:
`python3 -m pip install /path/to/checkout/tools/dream_rsi`.
The package is not claimed to exist on PyPI; do not use a bare package-name install.
The full project README and `POLICY_DEVELOPER.md` are in `tools/dream_rsi/`.

## No-key demonstration

From an installed package:

```bash
python3 -m dream_rsi.demo_evolution --output .runs/demo-01
```

From an uninstalled source checkout, run at its repository root:

```bash
python3 -m tools.dream_rsi.demo_evolution --output .runs/demo-01
```

Use a fresh output directory. This exercises actual source revision, replay
selection and redeployment using deterministic scripts, not an LLM.

## Project config

Start from the bundled `examples/knapsack/project.json`, replacing its task,
seed, generator and evaluator. For an installed package, find its directory with
`python3 -c 'import dream_rsi; print(dream_rsi.__file__)'`.

Required fields: `name`, `question`, `metric` (name/direction/identity), `seed_dir`,
`evaluate` (argv array), `verifier_files`, `limits`, and `policy`.
Optional `generate` is a command argv array; `generator_files` declares its source
dependencies. Paths in seed/dependency fields are relative to the config file.
Command arguments support `{python}` and `{config_dir}` substitution.

Limits include `attempts`, `wall_seconds`, `command_seconds`, and
`max_artifact_bytes`. A starting policy can be
`{"name":"round_robin","width":2,"patience":3}`. Metric identity must identify
the evaluator version and relevant dataset/split. The seed must evaluate validly.

The evaluator runs in the candidate workspace and writes exactly one JSON object
to stdout (diagnostics to stderr), for example:

```json
{"status":"valid","score":12,"metric":{"name":"objective","direction":"max","identity":"task-v1"}}
```

`invalid` and `blocked` require `score:null`. Metric fields must match the config.
The evaluator must not modify the candidate.

The generator reads the file named by `DREAM_PACKET`, edits only `DREAM_WORKSPACE`,
writes `proposal.md`, then exits. It does not produce the evaluator score itself.

## Live and manual runs

```bash
python3 -m dream_rsi init project.json --output .runs/run-01
python3 -m dream_rsi run .runs/run-01
```

Without a generator, replace `run` with `ask`, edit the emitted workspace using
the supplied packet, then `evaluate`. Repeat within budget. `status`, `verify`
and `report` accept the run directory. An interrupted evaluation is not silently
repeated; after confirming its owner has stopped, `abandon RUN --reason TEXT`
records the blocked attempt. The original wall deadline continues during pauses.

## Policy-code development

The developer command receives one JSON request on stdin containing incumbent
Python source and replay feedback/history. It returns exactly
`{"source":"complete Python program","summary":"reason for revision"}`.
Put its argv in a JSON file, such as `["python3","/path/to/developer_wrapper.py"]`.
It should follow the bundled policy-development instructions.

The generated policy itself reads prefix observations on stdin and returns
`{"action":"root"}`, `{"action":"observed-leaf-id"}` or `{"action":null}`.
It must derive decisions only from that input and remain deterministic. Policies
may not consult archive files or hidden outcomes. Same interface online/replay.

```bash
python3 -m dream_rsi evolve .runs/run-01 \
  --developer-json developer-command.json --revisions 3 \
  --developer-seconds 60 --policy-seconds 2 --wall-seconds 300 \
  --attempt-penalty 0.5 --output .runs/selection-01.json
python3 -m dream_rsi init project.json --policy-from .runs/selection-01.json \
  --output .runs/run-02
python3 -m dream_rsi run .runs/run-02
```

Set the attempt penalty from the user's objective before comparing policies.
The number above is illustrative. Keep source worlds unchanged and reachable.
Only wholly supported replays are eligible; ties retain the incumbent.

For an automatic outer loop, use the same config and developer:

```bash
python3 -m dream_rsi cycle project.json --developer-json developer-command.json \
  --cycles 2 --revisions 3 --developer-seconds 60 --policy-seconds 2 \
  --wall-seconds 600 --attempt-penalty 0.5 --output .runs/cycle-01
```

## Noise-aware promotion

Before calling a candidate better, compare per-item scores on identical items.
Write a draft registration (arms, incumbent, metric, `decision` rule with a positive
`useful_effect`, `expected_half_width`, and a `premise` block with `produced_by`,
`data_new_to_it`, `zero_effort_baseline`, `can_move_scored_quantity`,
`step0_metric`) before generating results, then:

```bash
python3 -m dream_rsi register draft.json --output registration.json
python3 -m dream_rsi compare cand-a.json cand-b.json --incumbent inc.json \
  --control inc-rerun.json --registration registration.json --output verdict.json
```

Report the printed current verdict, not the best raw mean. Every score file names
its runtime; never compare numbers across runtimes. Re-run the incumbent in the
candidates' session as `--control`. Exploratory arms are never promoted. For the
dream phase, prefer `evolve --promotion paired` with several recorded worlds and
`--confirm-runs` for held-out worlds. These checks do not detect evaluator bias
or training-set contamination; state those separately.

These are trusted local commands, not a security sandbox. Remote-provider
spending/cancellation needs the wrapper's own controls. Serial execution is the
supported scope; do not assume machine-wide GPU reservation or parallel workers.
