# Showcase climb plan: Python-Markdown render time (PREPARED, NOT LAUNCHED)

## Question

Can a coding agent refereed by HCA make Python-Markdown 3.11.1 render real documents measurably
faster, with byte-identical output and the full test suite passing? Can HCA show which "wins"
were noise?

This becomes HCA's first README showcase. Only confirmed numbers are quoted.

## Setup (done)

- **Repo:** `~/hca-showcase/python-markdown`, a clone pinned at tag 3.11.1, on the local branch
  `hca-showcase`. Nothing is pushed upstream.
- **Venv:** `~/hca-showcase/venv`, with an editable install, needed only so the test suite finds
  its extension entry points. The benchmark and the tests both import the arm's own checkout, and
  the benchmark asserts this.
- **Objective:** `bench/hca_bench.py` (frozen).
  - It renders 21 project docs with 7 extensions in a child process, timed from the parent, in a
    random order.
  - Each doc gets a warm-up plus 5 renders, each render with a unique trailing paragraph.
  - Output is compared by sha256 against the recorded hashes; any mismatch makes that item
    `candidate_invalid`.
  - Metric: `render_ms` per doc, `blocks` mode, median over docs of the per-doc log ratios.
- **Guards:** the full unittest suite (1,126 tests, ~1 s), run in the candidate arm.
- **Held-out:** 11 Markdown docs of a different style (HCA's own public docs), outside the repo, at
  `~/hca-showcase/heldout`, with recorded hashes. Used only by `hca confirm`.
- **Spec:** useful effect 5%, max_tries 40, 10 → 20 blocks, try_seconds 900, command_seconds 120.

## Agent

- **Who:** one Sonnet subagent, the "climber", given:
  - the hca skill;
  - `BRIEF.md`;
  - the instruction to work only in the showcase repo, editing only `markdown/**` and driving the
    loop with `hca try` and `hca confirm`.
- **Budget:** up to 40 tries or about 2.5 h of wall time, whichever comes first. Run `hca confirm`
  after every 3 keeps and at the end. At most 5 confirms (the spec default).
- **The climber may not:**
  - touch `bench/`, `tests/`, `hca.toml`, `.hca/` or `~/.hca`;
  - use `--again` without stating why;
  - run `init --reset`;
  - set `HCA_HOME` or `HCA_ALLOW_RESET`.
- **Machine rules:**
  - nice'd, one job at a time;
  - the benchmark's own child processes only;
  - RAM well under 3 GB (Python-Markdown is tiny).

## Before launch

1. `hca doctor`, then `hca quick` on the objective, to read the noise on this machine now. If the
   result is "too noisy" (for example, Jai is gaming), postpone.
2. `hca init --heldout ~/hca-showcase/heldout`. Its A/A must report "OK" or "needs-extension".

## Decision rules (preregistered)

- **The headline** is the final `hca confirm` delta vs 3.11.1 on the held-out corpus, with its
  bound.
- **Also reported:**
  - the number of tries and keeps;
  - the validity rate;
  - how many raw "best-score" wins there would have been: for each try, did the candidate's raw
    mean beat the baseline's? That is the counterfactual of a naive loop.
- **If no confirm passes,** the showcase says so honestly, and the run still demonstrates the
  referee.

## After the run

- **Deep evaluation:**
  - read every kept patch for gaming: caching keyed on input, skipping work, special-casing
    corpus docs;
  - check that the patches are plausible upstream-quality optimisations;
  - read the slices by doc kind;
  - read the mechanism metric (`html_kb` must not change).
- **Independent check:** re-measure the final confirmed commit against 3.11.1 outside HCA with a
  plain `timeit` on a third corpus (the Python-Markdown test fixtures `tests/**/*.txt`, which are
  frozen and never measured during the climb).
- **Write-up:** the showcase write-up and the replay data. Upstreaming any patch is a separate
  decision for Jai.

## Risks

- **Noise from Jai's gaming on the PC.** Mitigation: quick/init check it; the climb pauses or
  postpones if the noise goes up.
- **The climber overfits the 21 dev docs.** Mitigation: held-out confirm, plus the third-corpus
  timeit.
- **The editable venv install points at the main checkout.** Mitigation: the benchmark asserts the
  import path; tests run in the arm with `-m unittest` from the arm's cwd.
- **A real optimisation is under 5%.** It won't be kept by design. That is acceptable, and it
  should be reported as "below useful effect".

## Amendment A1 (pre-launch, after REVIEW-climb-plan.md)

| # | Finding | Resolution |
|---|---|---|
| S1 | Content caching could win: 6 renders per doc in one process | **Adopted.** Each measurement is 5 fresh worker processes. Each renders a warm-up doc that is not in the corpus, then each doc exactly once, with PYTHONHASHSEED=0. Hashes were re-recorded (bench commit d52223c). |
| S2 | No ruling on caches | **Adopted.** BRIEF now rules: configuration-derived caches are allowed; content-keyed caches and special-casing are gaming. Every kept patch is read, and grep'd for `lru_cache`, `cache` and dict memos keyed on text. |
| S3 | Try duration vs tool caps | **Adopted.** A run is ~3.7 s, so a try is ~80–160 s, well under a 10-min call. The budget is up to 40 tries or 3 h. `hca quick` sets the expectation before launch. |
| S4 | The guard covered HTML only | **Adopted.** The hash now covers HTML, toc, toc_tokens, Meta and references. The write-up will say that start-up, import and warm-up are excluded. Test-guard import path verified: an arm-local `python -m unittest` imports the arm's `markdown`. |
| S5 | HCA_HELDOUT_DIR leaked to the child | **Adopted.** Workers get no HCA_* vars. |
| S6 | Held-out docs are small and differ from dev; the climber may read copies | **Partly.** Kept as is, because a different style is the point. The write-up discloses the size difference and that the climber could read the docs (reading Markdown text doesn't help tune a parser). The third-corpus check (S7) adds large documents. |
| S7 | Third corpus too small / no protocol | **Adopted.** The third corpus is built by concatenating each `tests/` subdirectory's `.txt` fixtures into one larger document. It is measured outside HCA with an alternating A/B protocol over 20 rounds of fresh processes, final commit vs 3.11.1. |
| S8 | useful_effect | **Adopted.** It is set from `hca quick` before init. 5% stays unless quick says it is undetectable. The report gives total time and median per doc. |
| S9 | Counterfactual definition | **Adopted.** Naive loop = for each try, the first block's raw candidate-vs-baseline total (from runs.jsonl). We count how many tries a single-run "best score" loop would have kept. |
| S10 | Reserve the final confirm | **Adopted.** The climber may use at most 3 confirms. The final confirm is run by the orchestrator, not the climber. |
| S11 | Pre-launch controls | **Adopted.** The output-change mutation was verified to give candidate_invalid. Fresh-worker design makes content memos unable to hit (by construction). |
| S12 | Let the climber profile | **Adopted** in BRIEF. |
| S13 | Noise drift during the run | **Adopted.** The climber runs `hca quick` again if 3 tries in a row are void or need extension with estimates near 0, and reports noise. |

## Amendment A2 (pre-init)

useful_effect 5% -> 2%, from hca quick on 2026-10-15 (noise ±0.6%, detectable 0.7% at 10 blocks, ~65 s per try). Keep floor = 1%.
HCA version: dream-loop commit da79f6e (branch hca/design).
