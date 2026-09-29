# Dream Loop

### A research harness for your next breakthrough.

Explore ideas, evaluate experiments, and evolve your search strategy with
Dream-RSI-inspired policy improvement.

For researchers and builders pursuing discoveries in mathematics, scientific
algorithms and engineering optimization—where candidates can be checked by a
reliable evaluator. Bring a project, an objective and a finite budget.

Dream Loop records the search tree and uses replay feedback to develop executable
exploration policies for subsequent live runs. The coding agent and evaluator
stay fixed; the policy deciding where to search evolves.

**Propose → evaluate → retain → dream → repeat.**

Research prototype · Python 3.10+ · No runtime dependencies · Linux / WSL tested

The **harness** manages execution, budgets and evidence. The **loop** alternates
live discovery with replay-based exploration-policy improvement.

## What could you explore?

- **Mathematical constructions:** search for better feasible configurations or
  numerical bounds under explicit constraints. Numerical scores alone are not proofs.
- **Scientific algorithms:** compare solver variants against fixed correctness
  checks and performance measurements.
- **Engineering optimization:** improve code under a test suite and a reproducible
  runtime or resource metric.

These are intended applications, not demonstrated discoveries from this release.
The included CPU example verifies the loop's mechanics; research gains require
independent checks on the actual task.

## Try it in one command

From this repository's root:

```bash
python3 -m tools.dream_rsi.demo_evolution --output tools/dream_rsi/.runs/first-cycle
```

No GPU, model key, dataset download or provider account required. The demo mutates
real knapsack candidates and checks their capacity and value with a fixed CPU
evaluator. A scripted policy developer proposes actual Python-source revisions;
replay selects a policy and a second live rollout executes it. It is a
deterministic mechanism demonstration, not an LLM benchmark.

The command writes `report.md`, rollout directories and a versioned dream
selection. Open the report to inspect outcomes, policy source and feedback.
Output directories are never reused implicitly; choose a new name for another
demo. The smaller `python3 -m tools.dream_rsi demo --output NEWDIR` command
still runs the original finite-grid baseline.

Optional local package installation:

```bash
python3 -m pip install ./tools/dream_rsi
dream-loop --help
python3 -m dream_rsi.demo_evolution --output /tmp/my-dream-loop-demo
```

The package is not published to PyPI. `dream-loop` is the working name, not a
claim to a registered package name.

## Use with an agent skill

The release includes [the Dream Loop skill](../../skills/dream-loop/SKILL.md), which
teaches a coding agent to configure the evaluator, respect budgets, run the
loop and interpret evidence. It does not bundle an LLM provider adapter.

From the standalone release checkout, a compatible skills installer can install
it with `npx skills add . --skill dream-loop`. Alternatively, copy the complete
`skills/dream-loop` directory into your agent's supported skills directory.
Install the harness separately using the commands above. The skill can also
work from a verified source checkout.

## What makes it Dream-RSI-inspired?

The expensive operation is generating and evaluating a new candidate. The cheap
operation is traversing saved outcomes with a different exploration rule.

Dream Loop separates those phases:

1. **Live search:** a frozen policy decides which branch to open or continue.
   An external agent modifies a copy of the selected parent's valid artifact;
   an invalid parent falls back to its best valid ancestor. A fixed evaluator
   returns a typed result.
2. **Recorded worlds:** each attempt saves its decision, branch context, artifact
   parent, search parent, hashes, command logs, result and cost.
3. **Dreaming:** a policy-development command receives policy source and replay
   feedback, proposes revised Python source, and gets another replay evaluation.
   The incumbent remains eligible; unsuccessful revisions are retained.
4. **Next cycle:** deploy the selected source in a fresh live run, then add the
   resulting tree to the pool for another dream phase. The policy stays fixed
   during each live run.

The finite policy-grid `dream` command remains a simple baseline. `evolve` changes
executable policy code; `cycle` connects live rollouts and code evolution.
Supply your own trusted agent wrapper for model-driven policy development.
The included deterministic fixtures exercise the mechanism without model calls;
they do not demonstrate an LLM learning a better policy.

This is our serial, budget-constrained interpretation, not the authors' official
code or an exact reproduction of their objective. See
[research and attribution](REFERENCES.md).

## Run the self-updating loop

Provide a project config and a JSON file containing your policy-development
command as an argv array, for example `["python3", "/absolute/path/my_agent.py"]`.
That command reads a JSON development request on stdin and returns one object:
`{"source": "<complete Python policy source>", "summary": "<reason for change>"}`.
Requests contain incumbent source, replay feedback and revision history. See
[policy-development instructions](POLICY_DEVELOPER.md) for your agent wrapper.

```bash
python3 -m tools.dream_rsi cycle path/to/project.json \
  --developer-json path/to/developer-command.json \
  --cycles 3 --revisions 4 --developer-seconds 60 --wall-seconds 900 \
  --attempt-penalty 0.5 --output path/to/new-cycle
```

The discovery command in the project config generates project candidates. The
developer command generates exploration-policy code. Both can wrap a coding
agent, but their tasks and output contracts differ. Keep the model configuration
fixed during the comparison. Provider time and spending limits belong in your
wrapper; a wall timeout alone does not cancel a remote provider job.

To dream on existing compatible rollouts without another live run:

```bash
python3 -m tools.dream_rsi evolve path/to/run-01 path/to/run-02 \
  --developer-json path/to/developer-command.json --revisions 4 \
  --developer-seconds 60 --wall-seconds 300 \
  --attempt-penalty 0.5 --output path/to/new-selection.json
python3 -m tools.dream_rsi init path/to/project.json \
  --policy-from path/to/new-selection.json --output path/to/run-03
python3 -m tools.dream_rsi run path/to/run-03
```

Each policy is a self-contained Python program. It reads a JSON observation
containing the revealed root, nodes, metric and trace, then emits exactly
`{"action": "root"}`, `{"action": "<observed-leaf-id>"}` or `{"action": null}`.
The same interface is used in live execution and replay. Policies cannot request
an internal node or an unseen node. Source hashes freeze the selected code for
the entire live rollout; policy revision happens only between rollouts.

## Use your own project

Copy `examples/knapsack/project.json` and supply:

- A small seed workspace containing the files the agent may change.
- One question with an explicit objective.
- A fixed evaluator command and its declared source/data dependencies.
- Declared `generator_files` for local discovery-agent source dependencies;
  hashes detect changes between runs. Provider model settings must also stay fixed.
- A metric identity that includes dataset/split, exposure, aggregation and version.
- Attempt, command-time, wall-time and snapshot-size limits.
- Optionally, a generator command that invokes your coding-agent adapter.

Command fields are argument arrays, not shell strings. `{python}` expands to
the current interpreter and `{config_dir}` to the config directory. Run from a
clean, deliberately small seed; do not copy datasets, credentials or an entire
training repository into every attempt.

```bash
python3 -m tools.dream_rsi init path/to/project.json --output path/to/run-01
python3 -m tools.dream_rsi run path/to/run-01
python3 -m tools.dream_rsi dream path/to/run-01 \
  --attempt-penalty 0.5 --output path/to/selection-01.json
python3 -m tools.dream_rsi init path/to/project.json --output path/to/run-02 \
  --policy-from path/to/selection-01.json
python3 -m tools.dream_rsi run path/to/run-02
```

Choose the attempt penalty before comparison, in units of your objective per
attempt. The demo's 0.5 is illustrative, not a generally appropriate penalty.
Repeated selection on the same archive is development fit. Pool only compatible
worlds by passing several run directories to `dream`.
Policy adoption checks the source-world hashes and recomputes the selection;
keep those source runs reachable and unchanged. Moving archives or upgrading the
harness requires a deliberate new environment/run, not silent history rewriting.

### Evaluator contract

The evaluator runs with the candidate workspace as its working directory. Print
one JSON object to stdout; send diagnostics to stderr:

```json
{
  "status": "valid",
  "score": 83,
  "metric": {
    "name": "knapsack_value",
    "direction": "max",
    "identity": "knapsack-12-cap45-v1"
  },
  "summary": "weight=43/45"
}
```

`valid` requires a finite number. `invalid` and `blocked` require a null score.
Metric fields must exactly match the config. Evaluator mutation of the candidate
invalidates the comparison. The driver fingerprints declared verifier files;
list every relevant dependency. These checks do not make a malicious command safe.

“Valid” means the evaluation contract passed. Scientific support, independent
review and deployability remain task-specific judgments. For noisy objectives,
use the paired promotion tools below rather than comparing two single scores.

## Promote only what beats noise

The most common way a search loop fools itself is by promoting a "winner" that is
noise: the best of many candidates on the same items looks better than it is.
Dream Loop has three small safeguards.

**Paired comparison with a multiplicity correction.** `compare` reads per-item
scores for the incumbent and each candidate on identical items and reports a
bootstrap interval for the mean paired difference. Clustered items (for example,
several items drawn from one source) are resampled by cluster. The interval is
Bonferroni-corrected for the number of candidates compared. A candidate is
promoted only if the lower bound is above zero and the optional guardrails hold:
harmed-item rate at most `--max-harm-rate`, and no subgroup mean below
`--max-subgroup-loss`. The output leads with one current verdict.

**Same-session control.** Every score file names the runtime that produced it
(hardware, precision, library versions: any string you choose). `compare` refuses
to pair numbers from different runtimes. Re-run the incumbent in the candidates'
session and pass it as `--control`: deltas are then read against that rerun, and
its difference from the stored incumbent measures evaluator and numerical noise.

**Registration before results.** `register` freezes the arms, decision rule,
useful effect, expected interval half-width and a premise check (what produced the
thing being changed and whether the data are new to it, the zero-effort baseline,
whether the change can move the scored quantity at all, and the step-0 metric).
It warns when the expected half-width exceeds the useful effect. `compare
--registration` then applies the frozen rule and labels any arm that was not
registered, or whose result predates the registration, as exploratory: exploratory
arms are never promoted.

```bash
python3 -m tools.dream_rsi register draft.json --output registration.json
python3 -m tools.dream_rsi compare candidate-a.json candidate-b.json \
  --incumbent incumbent.json --control incumbent-rerun.json \
  --registration registration.json --output verdict.json
```

A score file looks like this; `cluster`, `subgroup` and `created_utc` are optional:

```json
{
  "kind": "dream_loop_item_scores",
  "arm": "candidate-a",
  "runtime": "cpu-fp32-py3.11",
  "created_utc": "2026-01-01T12:00:00+00:00",
  "metric": {"name": "accuracy", "direction": "max", "identity": "suite-v3"},
  "items": [{"id": "case-001", "score": 0.82, "cluster": "file-a", "subgroup": "hard"}]
}
```

The same statistics apply inside the dream phase. Each policy revision records a
paired check against the incumbent across recorded worlds. `evolve --promotion
paired` promotes a revision only if that check passes, with the correction taken
over all requested revisions. `evolve --confirm-runs` holds out recorded worlds
that the developer never sees: the final selection is re-checked against the
starting policy there, and under paired promotion a failed confirmation keeps
the starting policy. Paired promotion needs several recorded worlds (`--min-units`,
default 5); with fewer, it holds the incumbent and says so.

These checks measure sampling noise. They cannot detect a biased evaluator, an
evaluation set the model was trained on, or poor generalization to fresh items.
[Design notes](DESIGN_NOTES.md) list those and other lessons that are documented
but not yet implemented.

### Bring Claude, Codex, Devin—or a script

The tested integration boundary is a process or an explicit handoff. There is no
mandatory model SDK, provider routing or automatic access to existing app chats.

For an automatic adapter, read `DREAM_PACKET`, modify files under
`DREAM_WORKSPACE`, write `proposal.md`, and exit. The harness then invokes the
evaluator. The local demo's `propose.py` is a complete minimal adapter. Replace
the config's `generate` array with your own trusted wrapper for a supported agent
CLI. No live provider adapter has been tested in this release.

For your existing agent conversations, omit `generate` and use the manual path:

```bash
python3 -m tools.dream_rsi ask path/to/run-01
# Give the printed packet and workspace to one of your coding agents.
# After it finishes editing:
python3 -m tools.dream_rsi evaluate path/to/run-01
python3 -m tools.dream_rsi status path/to/run-01
```

The packet includes only the task and that branch's recorded lineage. Keep the
agent's generation context within that contract. An existing chat with additional
outcome knowledge is not a clean replay-compatible generation session. Manual
generation cost is marked unmeasured, not treated as free compute. Only one
attempt can be pending per run; this release does not orchestrate simultaneous
writers or reserve a machine-wide GPU.

## Evidence you can inspect

```text
run-01/
  world.json                 # frozen config, identities, results and provenance
  harness/                   # exact driver/policy source snapshot
  root/workspace/            # evaluated seed
  attempts/n0001/
    decision.json            # policy, observed IDs and search parent
    packet.json              # branch-local agent context
    workspace/               # retained candidate, including proposal.md
    generate.command.json    # actual argv, exit, elapsed time (automatic mode)
    evaluate.command.json
    evaluate.stdout          # raw evaluator response
    evaluate.stderr
    result.json              # typed outcome, score or null, artifact hash
  dream.json                 # policy grid, traces, support and selection
  report.md                  # readable summary
```

`verify` detects changed retained artifacts, packets, result records, frozen
config, harness source, policy source and declared evaluator/generator files. A rejected oversized/linked
candidate may have no hash and is explicitly an incomplete invalid snapshot.
Hash checks detect drift; they are not signatures or adversarial tamper protection.

Dream selections retain executable policy versions, development requests/replies
and replay feedback. Keep their source worlds reachable for adoption checks.
Snapshots created under an older harness version deliberately fail verification
under changed code: use their recorded environment to inspect them, or start a
new run. Do not edit historical hashes to make a stale run appear current.

Interrupted attempts are not automatically rerun. Inspect their logs, then use
`abandon RUN --reason "..."` to retain a blocked record. A stale `.lock` also needs
owner inspection before removal. Do not abandon a command that is still running.
Resume with `run` only after reconciling the pending attempt. Budget deadlines do
not reset when the CLI restarts; exhausted runs require a new, explicit run.

## Honest replay

- The policy sees only revealed observations, never the hidden tree's size or ceiling.
- A requested unrecorded child stops as **unsupported**. It earns no invented score.
- A child requiring unrevealed context is unsupported even if its checkpoint parent exists.
- Invalid attempts cost work and cannot become an incumbent.
- Only fully supported policy episodes enter the selection objective. A supported
  subset is still conditional on the historical policy's chosen archive.
- The objective is direction-adjusted gain minus a declared per-attempt penalty;
  no cross-metric averaging, parallelism bonus or assumed GPU-hour equivalence.
- Offline selection is in-sample. It is not proof of prospective improvement,
  unbiased off-policy evaluation, or counterfactual equivalence of stochastic agents.

The generator runs in a copied workspace, **not a security sandbox**. Commands have
the invoking user's permissions and may access the network or spend provider
credits. Only use trusted commands and configure provider limits separately. The
driver enforces serial attempts and supervised command deadlines; snapshot/log
size checks happen after commands and are not OS disk/RAM/VRAM quotas. POSIX command
timeouts kill the process group; remote API jobs need their own cancellation.

## Development

```bash
python3 -m unittest discover -s tools/dream_rsi/tests -v
```

## Release and maintenance scope

This is a focused research release, provided as-is with best-effort maintenance.
Fork and adapt it for your experiments. There is no support SLA, hosted service,
or promised provider-integration roadmap. Current support is Linux/WSL; native
Windows process-tree cancellation and external provider integrations are unverified.
See [contributing](../../CONTRIBUTING.md) for what kinds of changes are likely to be accepted.

If this research is useful to you, a star helps others find it.
