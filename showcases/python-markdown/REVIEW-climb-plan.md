# Review: CLIMB-PLAN.md (Python-Markdown showcase climb)

Reviewer: adversarial, read-only. Read: CLIMB-PLAN.md, hca.toml, BRIEF.md, bench/hca_bench.py, bench/corpus, heldout/, HCA's measure.py / stats.py / try_.py / confirm.py, ARCHITECTURE-v1 A2, skills/hca/SKILL.md. Nothing was run (no benchmarks, no tests). Items marked "estimate" are reasoning, not measurement.

## Verdict

**BLOCKER: 1** (S1). MUST-FIX: 4 (S1-S4). SHOULD-FIX: 9 (S5-S13). NITs: 7.

Do not launch until S1-S4 are fixed. The referee and the statistics look sound. The weakness is the benchmark: as written it can be won by caching that returns identical bytes, passes all 1,126 tests, and is not caught by the held-out confirm. For a public showcase that is the failure to avoid.

## Verified OK

- Parent/child split is real. The parent never imports markdown. `HCA_RESULT_FILE` is stripped from the child's env. Timing is the parent's `perf_counter` around one request/reply. The library cannot rewrite the result or fake timings. The import-path assertion in `child()` is correct.
- Hashes are recorded in the same order and sequence as they are measured (plain, then nonce 0..4, docs sorted), so process-global state differences between record and measure are not a false-positive source.
- IPC overhead vs effect (estimate): a 4-5 KB doc renders in a few ms. Pipe round trip plus JSON of ~5-40 KB is ~0.1-0.3 ms, about 2-4% of the smallest docs and under 1% of the large ones. It is identical in both arms, so it only dilutes a true gain slightly (a real 5% reads as ~4.9%). Fair and low-noise enough.
- The nonce itself is a one-word trailing paragraph, so it does not change what is measured. It does defeat whole-document output caching, as stated (but see S1 for what it does not defeat).
- Statistics: 10 blocks reach p = 2^-10 (alpha 0.01 needs >= 7), the keep floor is half the useful effect, and the extension to 20 is alpha-split. Per-doc median of 5, then median over docs, then per-block log ratio. Robust to a bad doc and to drift, because arms are interleaved and randomly ordered in each block. A2 snapshot arms, frozen hashing and the referee store close the earlier no-op-passes-confirm holes.
- The counterfactual data exists (S9): `_run_record` writes per-run `items` {value, status} per arm per block into each attempt's `runs.jsonl`.
- Repo state: branch `hca-showcase`, clean working tree, bench/spec commits on top of the 3.11.1 tag; `.hca` not yet created.

Not verified: that `python -m unittest` in an arm imports the arm's markdown rather than the editable finder's checkout. By Python's rules cwd is first on sys.path and the editable finder is a meta-path hook appended after PathFinder, so cwd should win. The plan only asserts this for the benchmark, not the guard. Check it once (S4c).

## MUST-FIX

### S1 (BLOCKER) Content-keyed memoisation across renders/instances wins and is undetected

Evidence:
- Each timed doc is rendered 6 times in one long-lived child, with the same text plus a one-word trailing paragraph (`text + "\n\nrender i\n"`). Everything except the last paragraph is byte-identical in all 6 renders. Docs are always rendered in the same sorted order.
- BRIEF.md bans only "caching whole-document output". It does not ban finer-grained caches, and its idea list says "caching regexes ... per instance" and "reducing per-instance setup cost", which invites cross-instance state.
- Any cache keyed on input content at block, line, or inline-span granularity hits ~100% on renders 2-6. Easy, legitimate-looking examples:
  - `@lru_cache` on pure string-to-string helpers (smarty substitution, `unescape`, `code_escape`, inline pattern `handleMatch` results, placeholder replacement);
  - a module-level dict keyed on a paragraph, line or stash string;
  - a prefix memo: only the tail ever changes, so the nonce design makes prefix and block memoisation perfectly safe.
- The output hashes match. The tests pass (they are small, one render each). The dev-corpus gain would be large. The held-out confirm would pass it too, because held-out docs are also rendered 6x each in one process. The held-out set catches corpus-specific precompute (reading `bench/corpus` at import) but not online memoisation.
- Sonnet is likely to try this innocently: the skill tells it to state a mechanism, and "avoid recomputing" is a mechanism. The post-run "read every kept patch" review would catch some, not subtle lru_caches, and the number would already be in the headline.

Why it matters: the headline would measure "repeat-render memo hit rate", not Python-Markdown speed. This is the most likely path to a misleading public claim.

Fix (benchmark, before `hca init`; then `--record` both corpora again and re-commit; init has not run so this is cheap):
1. Never render the same document more than once per process. Restructure the worker: 5 rounds, each in a fresh child, each child rendering every doc once. Use a fixed throwaway warm-up doc (in neither corpus) that exercises all 7 extensions (table, footnote, admonition, [TOC], smarty quotes, meta, wikilink) so lazy imports and regex compiles are paid before timing. Randomise doc order per child (seeded from a counter) so first-doc-in-process cost is spread evenly. Keep the per-doc median over the 5 rounds. Process start is ~0.2-0.4 s x 5, trivial next to a render pass. Expected hashes become plain-only (the nonce is no longer needed).
2. If you prefer to keep the current structure, the minimum is a second gating check: render each doc once in a fresh process after only the throwaway warm-up, and compare with the same-process steady-state number. A candidate whose advantage mostly vanishes on first-seen docs is memoising. Weaker than (1).
3. Set `PYTHONHASHSEED=0` in the child's env (reduces between-process noise from dict ordering with the multi-process layout). Add a non-timed correctness pass with a second hash seed so order-dependent output cannot hide.
4. Run a positive control for gaming before launch (see S11): write a ~15-line `lru_cache`-on-smarty or paragraph-memo patch on a scratch branch, show it scores a keep on the current bench, and show it does not on the revised one. If it still wins, the fix is incomplete.

### S2 BRIEF/climber rules do not define legitimate vs gaming caching

Evidence: BRIEF: "Caching whole-document output across renders cannot win, and it would be gaming anyway." SKILL: "caching across runs" is gaming. Neither says whether caching compiled regexes or extension setup across `Markdown` instances counts. The plan's own question (is cross-instance caching legitimate?) is unanswered for the climber.

My ruling (put it in BRIEF.md and the climber prompt verbatim):
- **Legitimate (upstream-quality):** immutable, content-independent state computed once at import or first use and shared across instances: module- or class-level compiled patterns, constant tables, a precomputed registry skeleton copied per instance. Conditions: no behaviour change, no state shared between instances that is mutated after construction, results independent of earlier inputs, safe under threads.
- **Gaming (reject):** any cache whose key or content derives from the input text (documents, blocks, lines, spans, strings from the document, including `lru_cache` on pure functions of such strings); shared mutable extension or processor instances across `Markdown` objects (they hold `self.md`); anything reading `bench/`, the environment, `sys.argv`, files on disk, or writing files, pickles or temp dirs; threads, processes, subprocess, ctypes, C extensions, new dependencies. The library must remain pure Python stdlib, single-threaded, supporting the Python versions 3.11.1 declares.
- Intra-document dedup inside one `convert` is allowed if reset per convert and justified by a stated mechanism.
- **Mandatory static screen of every kept patch** (grep the diff, not just read it): `lru_cache|cache|memo|pickle|shelve|marshal|tempfile|open\(|os\.environ|sys\.argv|__main__|thread|multiprocessing|subprocess|ctypes|gc\.`. Any hit needs a written justification in the write-up.
- A state-sharing change must also be argued safe: a second `Markdown` instance with different extension configs must not see the first's state.

### S3 Run mechanics: try length vs tool limits and the 2.5 h budget

Evidence (estimates; `hca quick` will give the real figure, I could not time it): a pass over the 258 KB dev corpus with 7 extensions plausibly takes ~1-1.5 s, so one run (6 passes) is ~6-9 s. A 10-block try is 20 runs plus 2 warm-ups plus the test guard: ~3-4 min. A try that extends to 20 blocks is ~6-8 min. try_seconds is 900. Forty tries with ~30% extending is ~3.5-4.5 h, over the "about 2.5 h" cap. Separately, a foreground Bash call by the climber is capped at 10 min, shorter than try_seconds 900 and `hca confirm`'s 20 blocks. A killed `hca try` leaves an `interrupted` attempt that still counts.

Fix:
- Read the per-run seconds from `hca quick`, compute tries/hour, and state the budget as "tries or hours, whichever first", saying which is expected to bind.
- Tell the climber to run `hca try` and `hca confirm` in the background and poll, never foreground with a short timeout.
- Check that try_seconds 900 covers 20 blocks x 2 arms x run time plus guard with margin (a 20 s run on a busy box gives 800+ s and a `void` on the extension). Raise try_seconds or shrink per-run work rather than let extensions void.
- Make the climber's long session resumable: a progress file updated after every try (number, verdict, one-line mechanism).

### S4 The output guard is HTML-only; import and warm-up costs are invisible

(a) `md.toc`, `md.toc_tokens`, `md.Meta`, `md.references` and other instance state after `convert` are not checked. Plausible "skip work the benchmark doesn't see": make toc string/token building lazy or conditional on a [TOC] marker, skip `meta` dict construction, skip `references`. All leave the HTML identical. These are public API outputs of the selected extensions. Fix: in the child, after the untimed plain render of each doc, return a second untimed reply with a canonical dump of `md.toc`, `md.toc_tokens`, `md.Meta`, `sorted(md.references.items())`; the parent hashes it against recorded values. No timing cost.

(b) The metric excludes import time, extension module imports and every first-call lazy initialisation (the warm-up). A change that moves work to import time (eager compiles, eager imports, templates built at import) looks like a win, but a one-shot `markdown.markdown()` call pays the same total. Acceptable if disclosed: label the metric "steady-state per-render time after warm-up, import excluded". Add a non-gating cold metric to the independent check (S7): `import markdown` plus first render of a small doc in a fresh process, baseline vs final, ABBA, 30+ repeats. If cold time regresses, say so.

(c) Confirm once, from inside an arm worktree, that `python -c "import markdown; print(markdown.__file__)"` and the guard's `python -m unittest discover` both resolve to the arm. A guard run against the wrong checkout passes every candidate.

## SHOULD-FIX

### S5 The held-out directory is exported to the child

Evidence: A2.3 says `HCA_ITEMS` and `HCA_HELDOUT_DIR` are still exported to the objective; `Worker` copies the whole env except `HCA_RESULT_FILE`. Library code in the child can read `os.environ["HCA_HELDOUT_DIR"]` and precompute. The child needs neither (text goes over the pipe). Fix: build the child env explicitly (PATH, LANG, PYTHONHASHSEED only), dropping every `HCA_*`, and run the child with cwd in a fresh temp dir so relative reads of `bench/corpus` fail loudly. One-line change; also removes a trust question from the write-up and blunts an on-disk cache.

### S6 Held-out set: useful, but not what the plan says it is

Evidence:
- 11 docs, 80 KB (dev: 21 docs, 258 KB). Median held-out doc is ~3.4 KB, so per-`Markdown()` setup dominates; dev is parse-dominated (changelog, api, contributing are ~46% of dev bytes). A throughput-only improvement will confirm smaller than a setup improvement, and the reverse on dev. Confirm measures a different mixture, not just "unseen".
- Feature mix differs (counted from the files): held-out has 94 table rows, 0 admonitions, 0 footnotes, 12 links; dev has 57 admonition markers, 12 footnote refs, 150 links, 60 table rows. A legitimate optimisation of the admonition or footnote path cannot confirm, and the climber sees only FAIL.
- Leaky: `skill-*.md` are copies of the skill files the climber is told to read, and `hca-readme.md` / `hca-architecture.md` are public docs under `~/dream-loop`. "Held out" means "not tuned on", not "unseen".
- One author, 11 docs: small for a headline.
- FAIL rolls back every keep since the last confirm and gives no numbers. With a different workload mix, a FAIL may mean "irrelevant to this mix", not "overfit".

Fix: (1) expand to >= 20 docs and >= 150 KB from unrelated public docs (READMEs and docs trees of other projects; only hashes ship in replay data, but check licences if you redistribute text); keep HCA docs out or a minority. (2) Match the feature mix to dev or state the difference. (3) Say in the write-up and the climber prompt that a held-out FAIL is not diagnostic. (4) Preregister that the held-out confirm is the headline, and label dev gains as selection-biased.

### S7 Third-corpus independent check: weak as specified

Evidence: 100 `tests/**/*.txt` fixtures, 179 KB, median 174 bytes, one 48 KB file. It measures `Markdown()` construction plus trivial parse, not throughput. The files are read by the test guard on every try (exercised, though not timed). Many fixtures target extension syntax outside the 7 and may throw or emit odd output. "Plain timeit" is also easy to do noisily.

Fix: run the check with the same statistics as HCA:
- Fresh process per sample, ABBA alternation of 3.11.1 tag vs final commit in two worktrees, >= 30 alternations, ratio with bootstrap interval, plus total-time ratio.
- Two corpora: (a) the fixtures (expect setup-dominated) and (b) a parse-heavy set (>= 100 KB of unrelated docs from another project). Configs: the 7-extension config and default `markdown.markdown(text)`.
- Output equality baseline vs final on every file and config, including toc/meta side outputs (S4a); exceptions must match too.
- Preregister agreement: interval excludes zero in the same direction and the point estimate is >= half the headline.
- Add the cold-start metric from S4b.

### S8 Useful effect, metric weighting, and boredom risk

- 5% useful effect (2.5% keep floor) is set before the noise is known. Python-Markdown 3.11.1 is reasonably tuned already, so many real micro-optimisations are 1-4% each and will be reverted or "below useful effect". An honest outcome could be "40 tries, 0-2 keeps", a weak showcase. Choose `useful_effect` from `hca quick`: the detectable effect at 80% power for 10 blocks (quick/init print it), then freeze before init. If that is ~2%, use 3% (floor 1.5%).
- The metric is the median over docs of per-doc log ratios. It weights docs equally and ignores the worst 10 of 21. A change that speeds up 11 mid-size docs and slows the big ones by 50% still keeps. Check every keep from `runs.jsonl` for total-time ratio and worst-quartile ratio, and report total-time alongside the headline. Say "median per-document speedup" in the write-up, not "x% faster rendering". If possible add `total_ms` as a mechanism metric with a guard.
- No headroom check was done (skill section 1 premise check). Before launch the orchestrator (not the climber) should run one cProfile pass on the dev corpus to see where time goes: setup (`Markdown.__init__`, registries, `extendMarkdown`), inline patterns, serialiser, postprocessors. If all is flat below ~2%, expect a boring result and decide whether to proceed.

### S9 Raw best-score counterfactual: computable, but define it properly

Computable: each attempt's `runs.jsonl` has per-block, per-arm, per-item values (median of 5) and status, and the record has `arm_medians`. Provide a small script that reads these.

The plan's definition ("candidate raw mean beat the baseline's") is the wrong naive loop: it is a 10-20 block paired comparison, much better than a naive loop, and will show few false wins, understating the referee's value. Report two counterfactuals:
1. **Naive best-score loop:** one measurement per candidate (block 0 only) compared with the best single value seen so far for the incumbent across earlier tries (winner's curse). Simulate over recorded blocks, using the incumbent's own earlier baseline-arm values.
2. **Paired mean without a test:** keep if the candidate total is lower in more than half the blocks or the mean ratio > 1 (the plan's version).
Report the number of tries each would have kept, and for reverted tries the raw mean difference next to the referee's interval. Caveat that this is a simulation on the same data, not an independent run.

### S10 Confirm cadence vs the cap of 5

"After every 3 keeps and at the end" with a cap of 5 can exhaust confirms before the final one (>= 12 keeps; unlikely, but the failure mode is "no final confirm"). Reserve the last confirm for the end, and preregister the headline if the final confirm fails: the last passing confirm's estimate on the commit it covered, with later keeps reported as "kept, not confirmed". Record each confirm's seed and the try number it followed; five looks at the same held-out set make it a mild dev set, and the write-up should say so.

### S11 Controls before launch (cheap, high value)

On scratch branches outside the climber's repo, once the CPU is free:
1. **Power control:** a known real change of about the useful effect (for example a deliberate redundant pass adding ~3% in the serialiser). Confirm `hca try` reverts/keeps correctly at this benchmark's noise. This is the missing end-to-end power check.
2. **Gaming control:** the S1 patch. It must be a keep on the old bench and a revert on the fixed one.
3. **Guard control:** a patch silently dropping `md.toc_tokens` (must now be `candidate_invalid`), and one changing an unused extension's behaviour (shows what the test suite does and does not cover).
4. `hca init` A/A on both corpora with the new bench; read the detectable-effect figures.

### S12 Climber instructions: add profiling, add a stop rule, tighten the forbidden list

- Let the climber run `cProfile` on the dev corpus. It is read-only, touches no frozen files, fits the skill's "state a mechanism" rule, and a profile-less Sonnet will mostly guess from the idea list. Never run it while a `hca try` is in flight (it corrupts the noise). Do not hand it a pre-made profile or fix list beyond what BRIEF has.
- 40 tries is enough for a first real look. Add a stop rule (e.g. stop after 12 consecutive reverts or 3 consecutive `void`s and report), and have it record every idea, including untested ones, so negatives and untested ideas are reported as the skill requires ("no evidence, not no effect").
- Forbid reading or listing `~/hca-showcase/heldout`, `~/.hca`, `.hca/arms/`, and `hca report --referee`, and also `~/dream-loop/docs` and `~/dream-loop/tests`, which overlap held-out text. The plan's list omits the held-out dir and `~/dream-loop`.
- Add: do not edit `markdown/` while a try or confirm is in flight (a changed file voids the try).
- The climber prompt should include S2's rules verbatim and S3's background-run advice.

### S13 Machine and noise

- Only one gate (`hca doctor`, `hca quick`, init A/A) at the start. Noise on a gaming PC moves during a multi-hour run. Each run records `loadavg`; add (a) a `hca quick` re-check every ~10 tries, logged, (b) a post-hoc check of loadavg and that reverts' score distributions centre near zero, (c) a pause rule if noise worsens by > 50% or the detectable effect exceeds the useful effect.
- WSL2 is a VM: Windows-side load, Defender scans, updates and thermal throttling are invisible to `loadavg` inside WSL. Close the game and check Task Manager before each launch. Consider `taskset` in the objective command if `quick` is noisy.
- Another job is using this CPU now. Do not run `quick`/`init` until it finishes, or the noise estimate and detectable effect will be wrong.
- Record in the write-up: CPU model, WSL kernel, Python version, venv package versions, and the quick/init noise figures.
- "nice'd" does not help against Windows-host contention; harmless but not a mitigation.

## NITs

1. The plan says docs are rendered "in a random order", but the code iterates `sorted(folder.glob("*.md"))`. Fix the plan or the code (the S1 restructure randomises order anyway).
2. BRIEF says slices are "extensions, change_log, and top-level docs". The actual `kind` tag is `stem.split("-")[0]`, giving 8 kinds: `extensions`, `change_log`, `changelog`, `contributing`, `cli`, `index`, `library`, `test_tools`. Use a coarser tag.
3. The child uses stdout as its reply channel, so a stray `print` in library code (a candidate's debug line) kills the worker with a confusing error. Use a dedicated fd or length-prefixed protocol.
4. "RAM well under 3 GB" is not the real constraint on a 15.4 GB box; the one-heavy-job rule is.
5. `expected.json` records 6 hashes per doc; after S1 only one is needed. Remove the dead nonce code.
6. Write the `markdown.__file__` of each arm into the replay data once per run as isolation evidence.
7. State in the showcase what the claim is not: not upstream-acceptability, not valid for other Python versions or CPUs, no upstream PR opened; dev corpus is Python-Markdown's own docs.

## What would make the public showcase misleading (summary of question 7)

1. A memoisation win reported as a speedup (S1/S2).
2. A median-per-doc number described as "faster rendering" when total time or large docs regressed (S8).
3. Import and warm-up costs excluded without saying so (S4b).
4. "Held-out" presented as unseen when it is partly the climber's own reading material and a different workload mix (S6).
5. A "referee beat the naive loop" claim based on a counterfactual that is itself a paired test (S9).
6. A headline from a quiet window on one WSL box with no environment details (S13).
7. Dev-corpus gains quoted next to the confirmed number without labelling them selection-biased (S6/S10).
