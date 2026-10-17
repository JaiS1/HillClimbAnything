# Explore mode: the engine reference

Explore mode is opt-in and experimental. It is `blocks` mode only (`try --explore` and `try --promote` refuse in `items`
mode). The design and its reasoning are in [`docs/DESIGN-climb-engine.md`](../../../docs/DESIGN-climb-engine.md).
Nothing here weakens the referee: only a normal try (plain, or a promotion) on fresh blocks can keep, and only a
confirm is a result.

## IDEAS.md

Your notebook, at the repo root. HCA reads it; it is never measured, snapshotted, reverted or committed.

```markdown
## I4 [avoid-work] Skip inline patterns whose required character is absent
where: markdown/treeprocessors.py
why: ~87% of (pattern, text) pairs are no-match; every match needs one literal char.

## I14 [dispatch] Dispatch table keyed on trigger char  (stepping stone)
parent: 0019
why: alone it may cost ~1%; it makes I9 and I12 one-line changes.
```

Kinds: `avoid-work`, `algorithm`, `data-structure`, `allocation`, `dispatch`, `io`, `concurrency`, `config`, `micro`,
`other`. An unknown kind or an id that is not in the file is recorded as `untagged`. Bad grammar never fails anything.
`hca try --idea I4` records the idea and its kind on the attempt, with a snapshot of the backlog.

## Outcome classes

| Class | Meaning |
|---|---|
| `KEPT` | a referee keep: real and above the floor (its estimate is selected, so biased upward) |
| `SMALL` | reverted, significant, but below the keep floor |
| `NO EVIDENCE` | reverted; the interval still allows the useful effect |
| `NULL` | reverted; the interval excludes the useful effect |
| `HARMFUL` | reverted; significantly worse |
| `SCREENED` | an explore child, ranked against its parent, **not tested** |
| `INVALID` | scope, guard, crash, refused replay: not a test of the idea |

## The loop

1. `hca next` (at most 60 lines): the budget (tries, promotions and screens counted from the referee store), the
   screen size and resolution, the incumbent, the parent the editable paths are at with its lineage, siblings, two
   inspirations from other modules, your ideas with their classes, the archive, dropped keeps, the promotion hint,
   an overlap warning when your working diff touches the same lines as an earlier revert or screen, warnings, and the
   backlog in a fixed order (ideas naming this parent; `NO EVIDENCE` ideas; ideas for a module with no member; the
   rest; kinds with two `NULL`s last).
2. Optionally `hca next --parent M` (an attempt id, or `incumbent`). Refused if the editable paths hold unscreened edits.
3. Edit from the parent, then `hca try --explore --idea I<n> -m "..."`.
   - The child is measured against the parent in `n_s = clamp(ceil((3 sigma / F)^2), 5, 8)` blocks (sigma from the A/A
     at init, F half the useful effect). The estimate is a Hodges-Lehmann shift; a one-sided 95% bound is shown from 5
     blocks. Below about 2 SE (`next` prints it) a screen cannot tell children apart.
   - Afterwards HCA admits the child to the archive (one elite per module), samples the next parent and sets the
     editable paths to it. Your edits are committed nowhere; the child lives at `refs/hca/attempts/NNNN`.
   - With no edits while you are at a screened member, `try --explore` re-screens that member against its own parent
     and pools the blocks.
4. `hca try --promote M -m "..."` sets the editable paths to M's tree and runs the normal try against the last keep. It
   needs `--again` if that tree had an earlier `try` or `promote` (screens do not count), and `--tainted` for a member a
   failed confirm tainted. A member forked before a later keep is tested as its whole tree; the keeps it lacks are
   printed as dropped keeps.
5. `hca confirm` every few keeps. On FAIL, every member built on the undone keeps is tainted: never sampled, never
   hinted, and promotable only with `--tainted`.
6. Between sessions, `hca report --dream` writes `.hca/DREAM.md` (at most 150 lines, no benchmark run): the archive map
   with empty and tainted modules, lineages and stepping stones with their trend, open evidence (`NO EVIDENCE` and
   `SMALL` with intervals, `NULL`s by kind), the files tried and how often, the screen resolution and the budget left.
   It ends with the instruction: write 5–10 ideas into `IDEAS.md`, each naming a parent member, at least one for an
   empty module if any is meaningful and at least one stepping stone you expect to read worse first, and say which
   evidence each answers.

## Archive, fitness and sampling

- Raw fitness telescopes along a lineage: the parent's fitness plus this screen's log estimate. Each step was selected,
  so the ranking fitness subtracts one screen standard error per screened step since the last keep.
- Parent sampling: the last keep with probability 0.40; other elites by `sigmoid((f - median) / s) / (1 + children)`;
  elites more than 2F behind the last keep are never drawn. The sampler has its own seed (from the referee store) and
  never touches the block-order randomness of any try. Every child logs the sampler, the candidates, their weights and
  the chosen parent's probability (`null` for an override).
- The promotion hint fires when a member's ranked lead over the last keep is at least F + SE_chain * sqrt(2 ln M). On
  noisy targets (SE > F/3) it asks for a re-screen first.
- `.hca/archive.json` is a derived view. Promotion re-derives everything from the referee store and the refs.

## Exit codes and files

| Command | 0 | 1 | 2 |
|---|---|---|---|
| `hca try --explore` | screen recorded | (never) | invalid, void, interrupted or refused |
| `hca try --promote M` | keep | revert | invalid, void, refused |
| `hca next` | printed | (never) | refused (unscreened edits, a bad parent) |

Records: `kind` (`try`, `promote`, `screen`) on every referee-store entry and every `verdict.json`. A screen's record
has `explore.parent`, `explore.sampling`, `explore.module`, `explore.divergence` and `screen` (blocks, estimate, SE,
bound). A promotion's has `explore.member`, `lineage`, `dropped_keeps`, `tainted`. `state.json` has
`explore.parent` (null at the last keep).

## Rules

- A screen is a ranking, not a result. Never report a screen estimate or a chained lead as a gain.
- The report's false-keep bound counts tries and promotions; screens are listed separately because they cannot keep.
- After 10 screens without a promotion attempt, or 20 without a confirm, `next` warns: screens tune against dev data.
- Overriding the sampler every time is allowed and logged; the report counts overrides and children that rewrote more
  than 25% of their parent.
