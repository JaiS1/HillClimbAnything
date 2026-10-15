# Brief: make Python-Markdown render faster

**Goal.** Cut the time Python-Markdown 3.11.1 takes to render real documents, with output that is
byte-for-byte identical.

**Measured by** `bench/hca_bench.py`, which is frozen:
- it renders 21 of the project's own docs with `extra`, `toc`, `admonition`, `sane_lists`,
  `smarty`, `meta` and `wikilinks`;
- each measurement uses 5 fresh worker processes; each worker renders a separate warm-up document,
  then every corpus document exactly once, timed by the parent;
- any document whose output differs from the recorded output is scored as a failure. Output means
  the HTML plus `toc`, `toc_tokens`, `Meta` and `references`;
- interpreter start-up, `import markdown` and the warm-up are not measured.

**What counts as a real optimisation.**
- **Allowed:** making the algorithms, regexes and data structures cheaper, and caching anything
  derived from *configuration* (compiled patterns, extension registries, per-instance setup) that
  would help any user rendering any documents.
- **Gaming, and a failure even if the referee keeps it:**
  - caching keyed on document content or substrings of it;
  - special-casing the corpus;
  - skipping work whose effect the tests or the output check happen not to cover.

Every kept patch will be read for this.

**You may** profile freely, for example
`python -m cProfile -s cumtime bench/hca_bench.py --child` with a document piped in, or by
writing your own throwaway scripts outside the repo. Only `hca try` measurements count.

**Guards.**
- The project's full unittest suite (1,126 tests) must pass.
- The output must be identical, which the benchmark checks itself.

**You may change** anything under `markdown/` (the library). **You may not change** `bench/`,
`tests/` or `hca.toml`.

**Useful effect:** 2%, the smallest win worth keeping. It was set from `hca quick` (noise ±0.6%; 0.7% detectable at 10 blocks).

**Ideas worth testing** (none tested yet; all `untested`):
- precompiling or caching regexes created per call or per instance;
- cheaper inline-pattern matching: the inline processor tries many patterns at every position;
- avoiding repeated tree walks or serialisation passes in treeprocessors/postprocessors;
- avoiding string concatenation in loops in the serializer;
- faster placeholder/stash handling (`htmlStash`, `util.INLINE_PLACEHOLDER`);
- reducing per-instance setup cost: each render builds a new `Markdown(...)`, so extension
  registration counts.

**Premise notes.**
- The benchmark builds a fresh `Markdown` instance per render, so setup cost is part of the
  metric. That is realistic for one-off `markdown.markdown()` calls.
- Mechanism metric: output size (`html_kb`). It should not change.
- Slices by document kind: `extensions`, `change_log`, and top-level docs.
