# Climb log: Python-Markdown 3.11.1 render time

Profile (scratch/prof.py, fresh Markdown per corpus doc, 7 extensions): ~453 ms total in-process, ~70% in the inline treeprocessor.
Top: InlineProcessor.__applyPattern 150k calls (0.255s tottime of 1.13 profiled), Registry.__getitem__ 151k calls (0.127s + overhead), __handleInline 0.127s, finditer 0.059s.
So every text node is tried against all ~30 patterns, each through a Registry lookup plus per-call setup.

## Try 1 — pattern snapshot per run()
Hypothesis: Registry.__getitem__ called 151k times (isinstance key checks, _sort) from __handleInline; snapshot patterns list once per run().
Verdict: attempt 0001  KEEP  render_ms -9.3% (better; cut of at least 8.7%, p = 0.000977, 10 blocks, 72.5 s)
Takeaway: per-pattern overhead in the inline loop is the lever.

## Try 2 — inline no-match fast path in __handleInline
Hypothesis: __applyPattern called 150k times, ~87% return no-match; run finditer/handleMatch loop inline in __handleInline for new-style patterns with no ANCESTOR_EXCLUDES (flag precomputed per run), factoring the replacement into __replaceMatch/__processNode (single finditer call preserved; test requires it). Profile: __applyPattern 0.25s tottime of 0.89s.
Verdict: attempt 0002  KEEP  render_ms -8.9% (better; cut of at least 4.5%, p = 0.000977, 10 blocks, 68.5 s)
Takeaway: call overhead per (pattern,text) pair matters; next remove regex work itself.

## Try 3 — bound finditer per run
Hypothesis: getCompiledRegExp()+attribute lookup per pattern per text (150k calls) -> resolve bound finditer once per run().
Verdict: attempt 0003  KEEP  render_ms -2.7% (better; cut of at least 1.1%, p = 0.000977, 10 blocks, 65.0 s)
Takeaway: small but real.

## Confirm 1 (after tries 1-3)
CONFIRM 1: PASS - estimate render_ms -18.9% (better) vs the original, cut of at least 18.0% (1 of 5 confirms used).

## Try 4 — required-character prefilter for inline patterns
Hypothesis: after tries 1-3 the remaining loop cost is ~150k (pattern, text) iterations, ~87% no-match. Derive per compiled regex (via re._parser tree, lru_cached on the compiled pattern; configuration-derived, not content-keyed) a single char every match must contain (e.g. [ ! < & backslash, quote chars); skip the pattern when absent (str containment). Sound by construction (IGNORECASE/lookarounds/negated sets/ranges => no info). Differential fuzz (2400 random markdown docs, 7 exts) identical to 3.11.1 output; 1126 tests pass.
Verdict: attempt 0004  KEEP  render_ms -28.6% (better; cut of at least 27.1%, p = 0.000977, 10 blocks, 80.5 s)
Takeaway: biggest single win; reviewers should read util.required_char carefully (uses private re._parser; requires-python>=3.11).

## Try 5 — Registry.__iter__ cached list
Hypothesis: parseBlocks iterates the blockprocessors Registry per block (3101 iterations), each building a fresh list; cache the ordered list until register/deregister.
Verdict: attempt 0005  KEEP  render_ms -2.5% (better; cut of at least 1.4%, p = 9.54e-06, 20 blocks after extending, 102.6 s)
Takeaway: small general win.

## Try 6 — TableProcessor.test early rejection
Hypothesis: TableProcessor.test was the most expensive block-processor test (~13us/call, 2029 calls): it split all rows and ran the code-aware tokenizer on the header for every block. A valid table needs a second line made only of chars in [|:\ -] (proved from _split_row: cells hold only |:- plus removed pipe/backslash border chars). Reject earlier with one fullmatch on line 2. Differential fuzz (table alphabet, 6x5000 docs + mixed fuzz) identical to 3.11.1.
Verdict: attempt 0006  KEEP  render_ms -5.9% (better; cut of at least 4.6%, p = 0.000977, 10 blocks, 51.3 s)
Takeaway: whole-block regex/tokenizer work in block-processor test() methods is the next lever.

## Try 7 — substring prechecks in block processor test()s
Hypothesis: failed whole-block multiline regex scans in HashHeader/BlockQuote/HR/DefList/Admonition tests (4.7-10 ms each in a bench run) can be skipped by a C-level containment check for the literal their regex requires (# > one-of(-_*) : !!!). Exact short-circuits; differential fuzz (misc alphabet 4x4000 + mixed 4x300) identical.
Verdict: attempt 0007  KEEP  render_ms -9.7% (better; cut of at least 6.6%, p = 0.000977, 10 blocks, 51.1 s)
Takeaway: block-test regex scans were bigger than expected.

## Confirm 2 (after tries 4-7)
CONFIRM 2: PASS - estimate render_ms -45.9% (better) vs the original, cut of at least 45.1% (2 of 5 confirms used).

## Try 8 — skip __processPlaceholders when nothing was stashed
Hypothesis: InlineProcessor.run calls __processPlaceholders (and for tails builds a dummy Element + list(currElement).index(child)) for all 10090 texts/tails but only 2199 placeholders exist; when the placeholder prefix is absent put the text/tail straight back (exact same result).
Verdict: attempt 0008  KEEP  render_ms -4.6% (better; cut of at least 3.1%, p = 0.000977, 10 blocks, 54.4 s)
Takeaway: more run()-level overhead exists. (Note: machine was loaded, load avg ~6, while this ran; HCA blocks interleave A/B so it coped.)

## Try 9 — candidate dispatch by required-character sets (inline)
Hypothesis: instead of testing ~17 pattern entries per text node, scan once with a char-class regex over all trigger chars and only try patterns whose required chars occur (30% of texts have none). In-process timing showed ~3% gain.
Verdict: attempt 0009  REVERT  render_ms -1.7% (better; +5.9%, no improvement shown, p = 0.00859, 20 blocks after extending, 112.7 s)
Takeaway: NO EVIDENCE, not no effect (interval [-3.9%, +5.9%] includes the useful effect). Complexity not worth chasing; reverted.

## Try 10 — literal test() for Footnote/Abbr/Reference block processors
Hypothesis: these three processors had test() == True and run() popped the block, ran a regex search, pushed the block back and returned False for nearly every block; make test() the literal pre-check ([^, *[, ]:).
Verdict: attempt 0010  KEEP  render_ms -2.9% (better; cut of at least 0.1%, p = 0.00488, 10 blocks, 47.5 s)
Takeaway: small, kept.

## Try 11 — TOC replace_marker / used-id micro-fixes
Hypothesis: replace_marker joined c.itertext() for every element before testing the marker; for len(c)==0 it is just c.text.strip(), test cheap condition first; used_ids loop used el.attrib (dict alloc per element) -> el.get.
Verdict: attempt 0011  KEEP  render_ms -2.1% (better; cut of at least 0.7%, p = 0.000977, 10 blocks, 46.3 s)
Takeaway: ok.

## Try 12 — regex scans for getText / find_code_spans
Hypothesis: Python per-character loops in LinkInlineProcessor.getText (1487 calls) and BacktickInlineProcessor.find_code_spans (1421 calls) -> finditer over [ ] and backtick runs. Differential fuzz (links/backtick alphabets) identical.
Verdict: attempt 0012  KEEP  render_ms -1.1% (better; cut of at least 0.1%, p = 0.00279, 20 blocks after extending, 85.7 s)
Takeaway: marginal but kept.

## Try 13 — whole-loop skip for text with no trigger char
Hypothesis: simpler form of reverted try 9. If every pattern has a known required-char set (disjoint from placeholder chars), one char-class search per text returns early for the ~30% of strings with no trigger char. Fuzz identical.
Verdict: attempt 0013  KEEP  render_ms -2.2% (better; cut of at least 1.0%, p = 0.000131, 20 blocks after extending, 86.4 s)
Takeaway: the simple form worked where the dispatch version showed no evidence.

## Try 14 — is_block_level set lookup + STX guards
Hypothesis: Markdown.is_block_level (13k calls, ~44% of AttrList.run and most of Prettify) scanned a 41-item list; use a frozenset rebuilt whenever the public list != snapshot (list compare of identical strings is cheap, and extension mutation of md.block_level_elements stays correct). Plus: UnescapeTreeprocessor.unescape and RawHtmlPostprocessor stash recursion skip regex sub when no STX is present. In-process A/B -2.3 to -4%.
Verdict: attempt 0014  KEEP  render_ms -3.2% (better; cut of at least 1.9%, p = 0.000977, 10 blocks, 45.4 s)
Takeaway: reviewer should read is_block_level carefully (cache validated against the live list each call).

## Try 15 — TOC replace_marker precheck
Hypothesis: replace_marker walks the whole tree with the recursive iterparent generator for each doc; a flat root.iter() check that any element text contains the marker lets us return early (A/B in-process about -1%).
Verdict: attempt 0015  REVERT  render_ms -0.5% (better; +1.1%, no improvement shown, p = 0.165, 20 blocks after extending, 84.8 s)
Takeaway: below the keep floor (1.01%); no evidence either way.

## Try 16 — fenced-code find-jump + NormalizeWhitespace guard
Hypothesis: FENCED_BLOCK_RE.search tries every position (~3 ms for the corpus) and the NormalizeWhitespace lookbehind regex (~3.4 ms); jump with str.find to the next line starting with a fence, and only run the blank-line regex when " \n" is present. Exact short-circuits; fenced fuzz identical. In-process A/B -1.0 to -1.2%.
Verdict: attempt 0016  REVERT  render_ms -1.4% (better; +4.4%, no improvement shown, p = 0.0884, 20 blocks after extending, 108.0 s)
Takeaway: no evidence, not no effect (interval [-7.4%, +4.4%], noisier than earlier blocks).

## Try 17 — bundle of exact short-circuits (reattempt of 15+16 plus init micro)
Hypothesis: individually below the noise floor (quick showed noise +-1.8% on a loaded machine), so combine: fenced find-jump, NormalizeWhitespace guard, TOC marker precheck, DOTALL|UNICODE constant (35 enum unions per instance), Registry.register dict check. In-process A/B -3.6%; init -14%. Fuzz identical (fenced/whitespace alphabet + mixed).
Verdict: attempt 0017  KEEP  render_ms -4.1% (better; cut of at least 0.7%, p = 0.00293, 10 blocks, 44.5 s)
Takeaway: bundling sub-floor micro-optimisations is what got them over the keep floor; individually they were no-evidence.

## Try 18 - second micro bundle (serializer lower-once, HR count>=3, lazy logger args)
Hypothesis: serializer calls tag.lower() up to 3x per element; the HR regex is only needed with 3+ of the same rule char; eager %-formatting in two logger.debug calls per extension. In-process A/B -1.0 to -2.4%.
Verdict: attempt 0018  REVERT  render_ms -0.6% (better; +1.1%, no improvement shown, p = 0.165, 20 blocks after extending, 83.7 s)
Takeaway: below the keep floor (1.01%); machine was loaded (hca quick: noise +-1.8% vs +-0.6% at the start). Stopped climbing here.

# SUMMARY
Tries: 18 of 40 (14 kept; 4 reverted: 0009, 0015, 0016, 0018, all "no evidence, not no effect"; the 0016 idea was later kept inside bundle 0017). Voids: 0. Wall time about 1h20.
Confirms used by the climber: 3 of 5 (the 4th/5th are left for the orchestrator).
 - CONFIRM 1: PASS - estimate render_ms -18.9% (better) vs the original, cut of at least 18.0% (1 of 5 confirms used).
 - CONFIRM 2: PASS - estimate render_ms -45.9% (better) vs the original, cut of at least 45.1% (2 of 5 confirms used).
 - CONFIRM 3: PASS - estimate render_ms -49.8% (better) vs the original, cut of at least 48.6% (3 of 5 confirms used).
hca status after try 18: "Confirmed: render_ms -49.8% (better) vs the original (confirm 3). Since then 2 unconfirmed keep(s); cumulative -53.3%, not yet confirmed." (unconfirmed keeps: 0014, 0017).

What worked (by size): required-character prefilter for inline patterns (try 4, -28.6%); removing inline pattern-loop overhead (tries 1-3: -9.3%, -8.9%, -2.7%); cheap necessary-condition guards in block-processor test() methods (6, 7: -5.9%, -9.7%); skipping placeholder processing when nothing was stashed (8: -4.6%); bundle of exact short-circuits (17: -4.1%); is_block_level set lookup + STX guards (14: -3.2%); trigger-char early exit in the inline loop (13: -2.2%); Registry iteration cache, Footnote/Abbr/Reference test(), TOC tweaks, regex scans for getText/find_code_spans (1-3% each).
What did not (or could not be shown): candidate dispatch by trigger char (9: in-process about -3%, HCA -1.7% no evidence), TOC precheck alone (15), fenced find-jump + whitespace guard alone (16), serializer/HR/logger micro bundle (18).
Ideas left untested: per-run inline-entry setup cost (about 2%), build_extension entry-point dict, parseBlocks blocks[0] hoist, multi-char required-substring prefilters for the smarty patterns, merging the two inline passes (smarty runs a second full-tree pass, about 11% of runtime; riskiest, needs output-equality care).

Legitimacy notes for reviewers: util.required_chars parses regexes with the private re._parser (project requires python>=3.11) and is sound by construction (no information for IGNORECASE, lookarounds, ranges, negated sets or categories; characters overlapping placeholder text disable the optimisation); util.required_char is now an unused leftover; is_block_level's cache is validated against the live public list on every call; all guards are exact necessary conditions (differential fuzz against the 3.11.1 tree with mixed, table, link/backtick, fence/whitespace alphabets, all identical); no content-keyed caches, no corpus special-casing.
