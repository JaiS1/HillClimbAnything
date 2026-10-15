# Showcase: Python-Markdown renders 2× faster, byte-for-byte identical

**Result.** On [Python-Markdown](https://github.com/Python-Markdown/markdown) 3.11.1, HCA
confirmed a **53.8% cut in render time** (lower bound 53.0%). The measurement used held-out
documents the climber never tuned against. The output is unchanged: the project's 1,126 tests
pass, and 23,000 randomly generated documents render identically.

| Check | Corpus | Change in render time | Output |
|---|---|---|---|
| `hca confirm` (final, orchestrator-run) | 11 held-out docs (HCA's own Markdown, different style) | **−53.8%** (at least −53.0%), 20 blocks | identical (hash guard) |
| Independent, outside HCA | 4 large docs merged from the project's test fixtures (never measured during the climb) | **−63.5%** (958 → 350 ms median), 20 alternating rounds, p ≈ 1e-6 | identical |
| Differential fuzz vs 3.11.1 | 23,000 random documents built from syntax fragments | n/a | 0 differences |

A Sonnet coding agent did the climb, using the [hca skill](../../skills/hca/SKILL.md), on
2026-10-15:

- **18 tries in about 1 h 20 min:** 14 kept and 4 reverted;
- **validity:** 100%, with 0 void;
- **confirms:** 4 of 5 used, all passed.

## What changed

[`optimisation.patch`](optimisation.patch) touches 14 files, +310/−99 lines. The kept changes,
largest first:

1. **A required-character prefilter for inline patterns (−28.6%).** For each inline regex, the
   change derives a small set of characters, at least one of which every match must contain, by
   walking the compiled pattern. Text that contains none of them skips that pattern without
   calling the regex engine. It bails out (no prefilter) on ignore-case, negated sets, ranges,
   categories and anything it doesn't fully understand.
2. **A leaner inline pattern loop (−9.3%, −8.9%, −2.7%).** It snapshots the sorted patterns once
   per run, binds `finditer` once, and handles the common "no match" case inline.
3. **Cheap necessary-condition checks before block-level regex scans (−5.9%, −9.7%).** These cover
   tables, headers (`#`), blockquotes (`>`), rules (`-`, `_` or `*`), definition lists,
   admonitions, footnotes, abbreviations and references (`]:`).
4. **Smaller wins of 1–5% each:**
   - skip placeholder expansion when nothing was stashed;
   - `is_block_level` uses a set that is re-validated against the live list on every call;
   - cache the registry's iteration order, invalidated on every change;
   - jump straight to the next line-start fence;
   - regex scans instead of per-character loops for brackets and backticks;
   - small TOC fixes.

There are no caches keyed on document content and no special-casing. The
[brief](BRIEF.md) ruled both out, and every kept patch was read for them.

## Why the referee mattered

A naive loop runs the benchmark once per change and keeps whatever got faster. Computed from the
first block of every try, such a loop would have kept **3 of the 4 changes HCA reverted**:

- **Try 0009** read as **−17.9%** on its first run. Over 20 randomised blocks it was −1.7%, below
  the keep floor.
- **Try 0012** read as **0.0%** on its first run, yet over 20 blocks it was a real −1.1%, which
  HCA kept and later confirmed.

HCA measured every try in randomised blocks against the last keep, and committed only changes that
passed an exact sign-flip test and cleared half the useful effect. It then re-measured the
cumulative change against the original on held-out documents (4 confirms, all passed: −18.9%,
−45.9%, −49.8%, −53.8%).

## How it was set up (so you can judge it)

- **Benchmark** ([`bench/hca_bench.py`](bench/hca_bench.py), frozen during the climb):
  - 21 of the project's own docs, with 7 extensions (`extra`, `toc`, `admonition`, `sane_lists`,
    `smarty`, `meta`, `wikilinks`).
  - Each measurement uses 5 fresh worker processes. Each worker renders a separate warm-up
    document, then every doc once, timed from a parent process that never imports the library.
  - The output (HTML, plus toc, toc_tokens, Meta and references) is hashed against the baseline.
  - Not measured: interpreter start-up, `import markdown`, and the warm-up.
- **Guards:** the full test suite runs in the candidate arm on every try. Useful effect 2%, set
  from `hca quick` (noise ±0.6%); keep floor 1%.
- **Adversarial review before launch** ([REVIEW-climb-plan.md](REVIEW-climb-plan.md)). It caught a
  real flaw in the first benchmark: rendering each document several times in one process would
  have rewarded content caches. The fix was one fresh process per run, each document rendered once.
- **The plan and its amendments** were written before launch: [CLIMB-PLAN.md](CLIMB-PLAN.md).
- **Every try** — hypothesis, profile evidence and verdict — is in [CLIMB-LOG.md](CLIMB-LOG.md).
  The raw ledger (patches, per-run timings, verdicts) is in [`ledger/`](ledger/).

## Limits, honestly

- **One machine:** a 16-core desktop under WSL2, partly loaded during the climb (noise rose from
  ±0.6% to ±1.8% late on). One run, one agent, and a single evening.
- **What the benchmark measures:** render time with these 7 extensions on documentation-style
  Markdown. Other extension sets, `codehilite`, or tiny documents (where `Markdown()` setup
  dominates) may see less.
- **Small held-out set:** the held-out documents are smaller than the dev corpus, and the climber
  could read them (they ship with HCA). Reading Markdown text doesn't help tune a parser, and the
  independent check uses a different corpus again.
- **Not upstreamed.** The patch has not been reviewed by Python-Markdown's maintainers. It uses
  the private `re._parser` module (the project requires Python 3.11 or later), and it leaves one
  unused helper (`util.required_char`). Treat it as a demonstration, not a release.

## Reproduce

```bash
git clone https://github.com/Python-Markdown/markdown && cd markdown && git checkout 3.11.1
git apply /path/to/HillClimbAnything/showcases/python-markdown/optimisation.patch
python -m unittest discover tests        # needs `pip install -e .` for the extension entry points
python /path/to/showcases/python-markdown/diff_fuzz.py . HEAD 3000      # after committing the patch
python /path/to/showcases/python-markdown/independent_check.py . HEAD 20
```

Python-Markdown is © The Python Markdown Project, BSD 3-Clause
([LICENSE](LICENSE-python-markdown.md)). The patch is a derivative of it under the same license.
