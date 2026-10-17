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
2. `hca next` alone is the normal path: it samples the parent, sets the editable paths to it and prints the
   `hca try --explore --idea I<n>` line to run. To choose another parent, `hca next --parent M --why "..."` (an attempt id,
   or `incumbent`; the reason is recorded and the report gives the override rate). Refused if the editable paths hold
   unscreened edits; the message gives the exact `git checkout` that drops them. Do not `git stash` them: that puts the
   files back to the last keep, not to the parent hca recorded.
3. Edit from the parent, then `hca try --explore --idea I<n> -m "..."`.
   - The child is measured against the parent in `n_s = clamp(ceil((3 sigma / F)^2), 5, 8)` blocks (sigma from the A/A
     at init, F half the useful effect). The estimate is a Hodges-Lehmann shift; a one-sided 95% bound is shown from 5
     blocks. Below about 2 SE (`next` prints it) a screen cannot tell children apart.
   - Afterwards HCA admits the child to the archive (one elite per module), samples the next parent and sets the
     editable paths to it. Your edits are committed nowhere; the child lives at `refs/hca/attempts/NNNN`.
   - With no edits while you are at a screened member, `try --explore` re-screens that member against its own parent
     and pools the blocks (each block counted once).
   - A recorded screen exits **5**, never 0: a screen is not a keep, so `hca try --explore && ...` must not treat it as one.
4. `hca try --promote M -m "..."` sets the editable paths to M's tree and runs the normal try against the last keep. It
   needs `--again` if that tree had an earlier `try` or `promote` (screens do not count), and `--tainted` for a member a
   failed confirm tainted. A member forked before a later keep, or one that put a keep's file back by hand, is tested as
   its whole tree; the keeps it lacks are printed as dropped keeps. If the promotion measures nothing (a refused replay,
   a scope problem), the editable paths go back to where they were.
5. `hca confirm` every few keeps. On FAIL, every member built on the undone keeps is tainted: never sampled, never
   hinted, and promotable only with `--tainted`. "Built on" includes the member an undone promotion promoted and every
   child of it, not only git descendants.
6. Between sessions, `hca report --dream` writes `.hca/DREAM.md` (under 150 lines, each list capped at 25, no benchmark
   run): the archive map
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
- The promotion hint says **promote** when lead - SE_chain * sqrt(2 ln M) >= F (M = valid screens so far), however noisy
  the screen: the promotion is itself a fresh test. It says **re-screen** only when the result is ambiguous (that fails,
  but lead + 1.645 SE still reaches F) and the member was not re-screened yet. It names a **stacked chain** when two or
  more open screened steps each improved on their parent: promote the tip.
- A promotion's member and its ancestors are **retired** once the promotion is in the incumbent: kept in the history,
  never sampled, inspired from or hinted again.
- A **stepping stone** is a screen that read worse than its own parent; "behind the incumbent" is a separate count. The
  report gives the stone-keep metric (kept promotions whose lineage holds a stone).
- Cells are files when the editable set has at most 25 files, else modules. Put a `hot: a.py, b.py` line in IDEAS.md
  (from a profile) to say which files carry the time; the dream then suggests only those for empty cells.
- `.hca/archive.json` is a derived view. Promotion re-derives everything from the referee store and the refs.

## Exit codes and files

| Command | 0 | 1 | 2 | 3 | 5 |
|---|---|---|---|---|---|
| `hca try --explore` | (never) | (never) | invalid, void, interrupted or refused | usage | screen recorded |
| `hca try --promote M` | keep | revert | invalid, void, refused | usage | (never) |
| `hca next` | printed | (never) | refused (unscreened edits, a tainted or invalid parent) | not an attempt id | (never) |

Records: `kind` (`try`, `promote`, `screen`) on every referee-store entry and every `verdict.json` (the store's wins).
`IDEAS.md` is your notebook once explore mode is in use, even if the editable globs cover it; in a line-only climb an
editable `IDEAS.md` is an ordinary edit. A screen's record
has `explore.parent`, `explore.sampling`, `explore.module`, `explore.divergence` and `screen` (blocks, estimate, SE,
bound). A promotion's has `explore.member`, `lineage`, `dropped_keeps`, `tainted`. `state.json` has
`explore.parent` (null at the last keep).

## Starting a renamed climb

If `hca init` refuses because the referee store already holds confirms for the task's name, start a new task without
editing the frozen `hca.toml`: `hca init --name <task>-2`. It is a fresh baseline with its own confirm cap; both stores log
the rename. Running the old name again is still refused.

## Rules

- A screen is a ranking, not a result. Never report a screen estimate or a chained lead as a gain.
- The report's false-keep bound counts tries and promotions; screens are listed separately because they cannot keep.
- After 10 valid screens without a promotion attempt, or 20 without a confirm, `next` warns: screens tune against dev
  data. After two unconfirmed keeps it says "confirm now".
- A refusal prints `REFUSED (exit 2): ...` on stdout too. A churn warning (a CRLF rewrite) means: normalise line endings.
- Overriding the sampler every time is allowed and logged; the report counts overrides and children that rewrote more
  than 25% of their parent.
