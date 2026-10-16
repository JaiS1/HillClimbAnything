# Brief: make js-yaml load() faster

**Goal.** Cut the time js-yaml 5.5.0 spends in `load()` on real-world YAML, with identical results:
the same values, types and key order, and the same errors on bad input.

**Measured by** `hca/bench.mjs`, which is frozen:
- it loads 491 documents (about 3.4 MB: GitHub Actions workflows, Kubernetes manifests,
  docker-compose files, Helm values and OpenAPI specs) from `hca/corpus/train/`, grouped into 89 items;
- each measurement uses 15 fresh node processes. Each process runs a fixed warm-up pass on a
  separate small set (`hca/warmup/`), then calls `load()` exactly ONCE per document, in a shuffled
  order, timed with `process.hrtime.bigint()`;
- the metric is a per-call, cold-ish time over mostly small documents (median document 1.1 KB): an
  item's score is the summed load time of its documents, and HCA scores a block by the median item
  ratio. A metric guard (`total_ms`, 5%) stops the total time from regressing while the median improves;
- every result (value, or the error thrown) is fingerprinted and compared with the recorded output of
  the original release. A document that differs is scored as a failure;
- node start-up, importing the library, reading files and the warm-up are not measured;
- the worker runs under node's permission model: it can read only its own script and `dist/`, never
  the corpus or the original release, and cannot write, spawn or start workers. The documents arrive over
  stdin, so a cache cannot persist to disk or be precomputed from files. The harness also checks that
  the worker's summed timings cover 93% or more of the parent's own clock, that nothing was left queued
  after the loop, that importing the library took no longer than the original's (+10 ms), and that no
  result is a proxy or an object returned by an earlier call;

**What counts as a real optimisation.**
- **Allowed:** making the scanner, parser, scalar resolution and construction cheaper (fewer
  allocations, cheaper character tests, avoiding repeated work, better data structures), and caching
  anything derived from *configuration* (schemas, tag tables, compiled regexes) that would help any
  user loading any documents.
- **Gaming, and a failure even if the referee keeps it:**
  - caching keyed on document content or on substrings of it (the corpus has no duplicate documents,
    but a real user's input could repeat, so such a cache would only look like a speed-up);
  - special-casing the corpus, its file names, sizes or the order it is processed in;
  - skipping work whose effect the tests or the output check happen not to cover;
  - detecting the benchmark, the arm, or the pinned original.

Every kept patch will be read for this.

**You may** profile freely, for example `node --cpu-prof`, or write throwaway scripts outside the
repo. Only `hca try` measurements count. After editing `src/`, run `npm run build`; the benchmark
and the tests use `dist/`, which HCA rebuilds in each arm before measuring.

**Guards** (all run in the candidate arm on every try):
- `hca/guards/tests.sh`: lint, type-check and js-yaml's own test suite (1,646 tests, including the
  yaml-test-suite);
- `hca/guards/equality.mjs` (also run against the CommonJS build): `load()` / `loadAll()` on every corpus
  document, 900 hand-written edge cases and the 406 yaml-test-suite cases (valid and invalid), under ten
  option sets, plus odd inputs and options, state between calls, result freshness, and a 5 MB document. The value must
  deep-equal the original release's (types, `-0`, `NaN`, Dates, binary, key order, aliasing) and a
  thrown error must have the same class and message;
- `hca/guards/fuzz.mjs`: about 5,000 random documents, part of them generated on a fresh seed each
  run, compared the same way, plus `dump()` equality on the random values;
- `hca/guards/source.mjs`: a review aid that fails on added lines in `src/` that touch the process,
  timers, proxies, `eval`, the benchmark or corpus, imports resolving outside `src/`, or a built module
  more than 1.2x the original's size.

**You may change** anything under `src/`. **You may not change** `hca/`, `test/`, `support/`,
`package.json`, `package-lock.json`, `tsconfig.json`, `eslint.config.mjs` or `hca.toml`.

The held-out set used by `hca confirm` is the same genres, mostly different files.

**Useful effect:** 5%, the smallest win worth keeping; the keep floor is half of it. Set from `hca quick` (A/A noise ±3.3-4.8% on a busy PC, about ±2% on a quiet one; 3.5-5% detectable at 10 blocks, so a try often extends to 20).

**Ideas worth testing** (none tested yet; all `untested`):
- the scanner: `charCodeAt` dispatch tables instead of chains of comparisons, fewer `slice` and
  string concatenations per scalar, a faster plain-scalar fast path;
- scalar resolution: skipping the tag-resolver loop when the first character rules out int, float,
  bool and null (core schema resolvers run on every plain scalar);
- the event pipeline: parser to events array to constructor builds an intermediate array of event
  objects; fewer allocations per event;
- construction of mappings: key handling, duplicate-key checks and `__proto__` safety on every pair;
- snippet / mark bookkeeping that is only needed on errors (compute it lazily);
- `loadDocuments` rebuilds option objects and key lists on every call.

**Premise notes.**
- The first call after process start is slow (cold JIT). The benchmark warms up on a separate small set
  and measures each corpus document once, so JIT behaviour is part of the metric, as it is for a CLI
  or a build tool that loads a few files.
- Mechanism metric: item size in KB (a check on the corpus, it should not change). Slices by kind:
  `gha`, `compose`, `k8s`, `helm`, `openapi`.
- `load()` uses the default `CORE_SCHEMA`. The guards also exercise YAML 1.1 types, merge keys and
  `json: true`, so changes in those paths are checked even though the benchmark does not time them.
