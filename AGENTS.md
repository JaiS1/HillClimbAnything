# AGENTS.md: working on HillClimbAnything

This file is for coding agents working on this repository. If you want to *use* HCA on another
project, read [`skills/hca/SKILL.md`](skills/hca/SKILL.md) instead.

## Layout

| Path | What it is |
|---|---|
| `hca/` | the referee CLI: standard library only, Python 3.11+ |
| `hca/stats.py` | exact signed-rank test on randomised blocks, keep rule, noise report (the statistical core) |
| `hca/paired.py` | `items` mode: paired bootstrap-t (from Dream Loop) |
| `hca/measure.py`, `hca/runner.py` | running commands and parsing their results; never use `Popen.wait(timeout=...)` for timing |
| `hca/gitops.py`, `hca/arms.py`, `hca/ledger.py` | snapshot commits, arm worktrees, the `.hca/` ledger and the referee store |
| `hca/core.py`, `hca/spec.py` | errors, JSON/hash helpers; `hca.toml` parsing and validation |
| `hca/commands/` | `quick`, `compare`, `init`, `try_`, `confirm`, `report` (also `status`), `doctor` |
| `tests/` | unittest suite (~2 min); `test_calibration.py` runs 100 no-op tries end to end |
| `skills/hca/` | the agent skill and its references |
| `docs/` | design, architecture contract (with amendments), limits, README assets and translations |
| `showcases/` | recorded climbs with their ledgers and reproduction scripts |
| `spikes/phase0/` | the calibration experiment behind the statistics |

## Setup

```bash
uv tool install hillclimbanything   # or: pipx install hillclimbanything
hca setup                                                        # copies the skill to ~/.claude/skills/hca and ~/.agents/skills/hca
```

Try it first without installing: `uvx --from hillclimbanything hca quick -- <cmd>`, or A/B two commands with `uvx --from hillclimbanything hca compare -- <A> -- <B>`.
`hca setup --uninstall` removes the skill. Claude Code plugin alternative: `/plugin marketplace add JaiS1/HillClimbAnything`
then `/plugin install hca@hillclimbanything`. To work on this repo itself, use the checkout and `python3 -m hca`.

## Commands

```bash
python3 -m unittest discover -s tests             # all tests (run under `nice -n 19` on shared machines)
python3 -m hca --help                             # the CLI from a checkout
python3 docs/assets/make_climb_chart.py           # regenerate the README climb charts from the showcase ledgers
python3 docs/assets/make_terminal_svg.py          # regenerate the animated terminal
python3 docs/readme/check.py                      # translations: links, numbers, structure vs README.md
```

## Rules

- **Standard library only.** No runtime dependencies.
- **Exit codes are a contract** (README table, `docs/ARCHITECTURE-v1.md`). Don't change them
  casually.
- **Every behaviour change gets a test.** Statistical changes also need a calibration check:
  false keeps at or below the nominal rate under heavy-tailed noise.
- **Respect `docs/ARCHITECTURE-v1.md`.** Where you deviate, mark it `# CONTRACT NOTE:` and update
  the doc.
- **Never weaken the referee to make a test pass.** The referee covers scope checks, frozen hashes,
  snapshot arms and the referee store.
- **Process hygiene:** kill only processes you started (by process group or PID), never by name
  pattern. Leave nothing running. Clean up temp dirs and worktrees.
- **README claims must match measured evidence.** Numbers come from `showcases/` and `spikes/`,
  never from estimates.
- **Translations** in `docs/readme/` follow the English README. When the English changes, update
  them or mark them as lagging.
