# HCA showcase 2: js-yaml `load()` (set up, NOT launched)

> Written before the climb launched, as the handoff for the agent doing it. It describes the harness
> as it was set up and hardened after an adversarial review; the climb itself is in
> [ledger/](ledger/) and the [README](README.md). Where it says "set up, NOT launched", `hca init`
> has since been run and the climb is done. Paths are from the machine it ran on.

Status: harness built and hardened after an adversarial review, committed on the local branch,
`hca doctor` clean (0 errors; warnings are only the skill-install notes). `hca init` has not been run
and no climb has happened. Nothing was pushed (the repo's push URL is disabled).
Final check: `sh hca/verify-clean.sh` (fresh clone, `npm ci`, build, all guards, 200-process A/B against
the pinned build; last run clean: candidate/original total-time ratio 1.003, 95% CI -1.3% to +1.7%,
0 output mismatches).

## Layout

| Path | What |
|---|---|
| `~/hca-showcase-yaml/js-yaml/` | clone of nodeca/js-yaml at tag **5.5.0** (`cb62b2d`), branch `hca/yaml-load`, harness commits on top (see `git log`) |
| `~/hca-showcase-yaml/heldout/` | held-out corpus (195 docs, 1.2 MB) + `expected.json`, OUTSIDE the repo; goes to `hca init --heldout` |
| `~/hca-showcase-yaml/pinned/js-yaml-5.5.0/` | the original release build (`js-yaml.mjs`, `.cjs.js`), read-only, sha256 recorded; outside the repo |
| `~/hca-showcase-yaml/sources/` | the sparse clones the corpus came from (not needed again) |
| `~/.local/node/` | Node v22.21.1, user-local (no sudo); `~/.profile` puts it on PATH |

Climber instructions should say: work only in `js-yaml/`; do not read `heldout/`, `pinned/` or
`sources/` (this file lists the held-out sources only in aggregate, on purpose).

Environment notes:
- js-yaml 5.5.0 is a TypeScript rewrite: sources are `src/**/*.ts`, built by vite into `dist/`
  (untracked, 2.6 s). Benchmark and tests run on `dist/`, so `hca.toml` has `[build] npm run build`
  and the editable path is `src/**` (not `lib/**`).
- `npm ci` installs fine, but the lockfile lacks the linux rolldown binding (npm optional-deps bug),
  so `npm run build` failed until I ran `npm install --no-save @rolldown/binding-linux-x64-gnu@1.2.14`.
  It is in `node_modules` only (linked into arms); `package*.json` are untouched. Rebuilding
  `node_modules` from scratch needs that one extra command.
- `hca` needs `~/.local/node/bin` on PATH (`hca.toml` uses bare `node`, `npm`, `sh`). A login shell has it.
- `node_modules` is made read-only (`chmod -R a-w node_modules`; build and tests still work) rather than
  hashed: hashing 134 MB on every try costs more than it buys and the climber is told not to touch it.
  It is a speed bump, not a lock (the same user could chmod it back); `source.mjs` and the frozen
  `support/**` and `package*.json` cover the build inputs.
- `test/spec/yaml-test-suite/` (from `npm run spec:get`, gitignored) is symlinked into each arm
  (`link`) and hashed because `test/**` is frozen.

## How each piece runs

All commands from `js-yaml/`, after `npm run build`.

- **Benchmark**: `node hca/bench.mjs` (writes `HCA_RESULT_FILE`, else prints item JSON), metric
  `load_ms`, min. The parent reads the 13 warm-up files (`hca/warmup/`, 35 KB, separate from the corpus)
  and the corpus and sends the texts to each child in its first stdin message. 15 fresh node processes per
  measurement; each imports the lib, warms up, then calls `load()` once per document in a shuffled order,
  timing each call with `process.hrtime.bigint()`. 491 docs are grouped into 89 items (files over 20 KB
  alone, smaller ones grouped by kind to about 10 KB); an item's score is its summed time. **The metric is
  a per-call, cold-ish time over mostly small documents (median 1.1 KB), scored by HCA as the median item
  ratio per block**, not total time. Mechanism metrics: `kb`, `total_ms` (sum of all calls, median over
  runs) and `ms_gha/k8s/helm/openapi/compose`; hca's slices give the per-kind median ratio. (A true
  "ratio" metric would need the original running alongside; `hca` already compares arms, so per-kind
  totals plus slices do the job.) A measurement takes about 10 s (~190 ms of pure load time per process).
  `HCA_ITEMS=heldout` + `HCA_HELDOUT_DIR` switches to the held-out corpus (checked: 195 docs, 37 items, valid).
  - Child isolation: it runs under `node --permission` allowed to read ONLY `bench_child.mjs`,
    `lib/canon.mjs` and the lib's `dist/` directory. No corpus or held-out path is in its argv, execArgv,
    env or cwd (its cwd is `dist/`; `HCA_*` and `NODE_OPTIONS` are stripped). No writes, no child
    processes, no workers: it cannot read the corpus or the original release, or keep a disk cache.
  - Moving work out of the timed region: `canon.mjs` is imported before the lib and captures the builtins
    it uses; `hrtime`, `JSON`, `Number` and `write` are captured in the child before the import; canon
    rejects Proxies. After the timed loop the child awaits a microtask and a `setImmediate`, and the
    parent fails the run if that drain took over 1 ms (honest: 0.15-0.21 ms). The child's summed call
    times must cover **at least 93%** of the parent's own wall clock around the timed phase (honest runs
    measure **98.0-99.4%** over ~70 runs; 93% leaves room for a scheduler stall) and may not exceed 100%.
    The lib's import time (median over runs) must be within 10 ms of the original's, stored in
    `expected.json` as `import_ms` (5.2 ms train, 5.5 ms held-out; candidate measures 5.1-5.5 ms).
  - Results: every value or thrown error is canonicalised and hashed against `expected.json` (recorded
    from the pinned original with `node hca/bench.mjs --record <dir>`); a result containing an object
    returned by an earlier `load()` is rejected (WeakSet). Any failed check scores every item
    `candidate_invalid`.
  - The corpus has no duplicate documents, so a content-keyed memo never hits within a run, and fresh
    processes plus no write access stop anything surviving across runs. `--max-semi-space-size=64` is
    passed to the child (fewer scavenges, less GC jitter).
- **Guard (a), js-yaml's tests**: `sh hca/guards/tests.sh` = `npm run lint`, `npm run type-check`,
  and `node --test` over `test/core` + `test/spec` (what `npm test` does minus the build, which `[build]`
  did, and the network `spec:get`). 1,646 tests (1,633 pass, 13 skipped as upstream), about 6 s.
- **Guard (b), output equality**: `node hca/guards/equality.mjs` (about 6.5 s), run twice: against the
  ES-module build and, as a second guard, the CommonJS build (`--cjs`). 25,384 comparisons of candidate vs
  pinned original each: the 491 corpus docs under 4 option sets; 904 hand-written edge cases (numbers in
  every notation, YAML 1.1 words, timestamps, binary, tags and directives, merge keys, anchors and alias
  bombs, `__proto__` keys, escapes, BOM/CRLF/NEL/tabs, unpaired surrogates, block-scalar indicators,
  flow errors, deep nesting, 100 KB scalars, a custom tag that re-enters `load()`) under 10 option sets
  (default, YAML 1.1, 1.1+merge+Map, core+extra tags, `json:true`, filename+maxAliases, `JSON_SCHEMA`,
  `maxDepth` 3 and 1e6, re-entrant tag) with `load` and `loadAll`; all 406 yaml-test-suite cases under 6
  option sets; and 468 API-surface cases: export names and types, 16 non-string inputs, 20 odd or partial
  option objects (null, unknown keys, undefined fields, wrong types), an options object mutated between
  calls and a frozen one, error-then-success sequences, anchor and directive leakage between calls,
  load-mutate-load again (results must be fresh and independent), and two ~5 MB synthetic documents. A
  value must canon-equal; a thrown error must match in class chain, name, message (with snippet) and own
  properties. A result that reuses an object from an earlier `load()` is a difference.
- **Guard (d), source review**: `node hca/guards/source.mjs`. Compares every file under `src/` with 5.5.0
  (commit `cb62b2d`) and fails on added lines matching
  `node:|process\.|globalThis|require\(|import\(|eval\b|new Function|Proxy|queueMicrotask|setTimeout|setImmediate|nextTick|\bv8\b|\.\./hca|corpus|\.hca|import\.meta|\?raw`,
  on imports resolving outside `src/` or new bare imports, on new files, and on `dist/js-yaml.mjs` over
  1.2x the original's size. It is a pointer for the human reviewer; ordinary optimisations should not trip it.
- **Guard (e), metric guard** `total-time`: `total_ms` may not regress more than 5% against the baseline
  (so the median item cannot improve while the total gets worse).
- **Guard (c), seeded fuzz**: `node hca/guards/fuzz.mjs [seed] [docs]`, 1.6 s. 5,000 documents, each
  under 2 of 5 option sets (10,000 comparisons): random JS values (Dates, binary, -0, NaN, Infinity,
  `__proto__` keys, hostile strings) serialised by the PINNED `dump()` with random layout options, with `dump()` itself compared candidate vs original on the same values and options (1,250 comparisons);
  random YAML text from a grammar; and mutants of both (random punctuation inserted, deleted, swapped,
  truncated) which mostly give malformed YAML, compared by error class and message. 4,000 documents use
  the fixed seed 20261016; the last 1,000 use a fresh seed each run, printed (replay with
  `node hca/guards/fuzz.mjs <seed>`), so the suite cannot be memorised. Valid/error split of the
  comparisons: values 87/13%, grammar 41/59%, mutants about 30-40/60-70%.

## Noise (`hca quick`, A/A, 20 blocks unless noted)

15 runs per measurement, 89 items. Before the hardening (child read the files itself): ±1.7% (16 blocks,
quiet PC), ±2.3%, ±3.1% (lightly loaded). **After the hardening: ±4.8%, ±3.3%, ±4.5%** (loadavg 1.2-1.8
from other activity on the PC; nothing of mine was running). So it is noisier now, roughly 1.5x; I did not
isolate whether that is the PC being busier or the new data path (about 3.4 MB of corpus text held in the
child's heap). In a 200-process alternating A/B of the candidate against the original (same code), the
median total-time ratio was 1.003 with a 95% interval of -1.3% to +1.7%, so the total is much steadier than
the median-item block score.

Consequence: **`useful_effect` is now 5%** (keep floor 2.5%). Detectable at 10 blocks: 3.5-5.1%; at 20
blocks 2.5-3.6%. A try takes about 117 s at 10 blocks and about 233 s extended to 20. With the PC idle
expect closer to the earlier ±2%. Run `hca quick` again right before `hca init`, and do not game while it climbs.

Earlier tuning (12-20 blocks each): 5 runs per measurement gave ±6.2%; 9 runs ±2.7-3.6%; 15 runs
±1.7-3.1%. More items (46 -> 89) did not help; `--single-threaded` and CPU pinning made it worse;
`--max-semi-space-size=64` helped slightly (kept).

## Corpus composition

Train, in the repo (`hca/corpus/train`, sources and licences in `hca/corpus/SOURCES.md`): 491 docs,
3.2 MB, median doc 1.1 KB, 21 docs over 20 KB, largest 1.07 MB.

| Kind | Docs | KB | Median KB | Largest KB |
|---|---:|---:|---:|---:|
| gha (GitHub Actions workflows) | 157 | 439 | 2.0 | 24 |
| k8s (manifests, CRDs, Argo workflows) | 257 | 830 | 0.6 | 407 |
| helm (values.yaml, Chart.yaml) | 46 | 879 | 1.2 | 195 |
| compose (docker-compose) | 19 | 13 | 0.7 | 2 |
| openapi (OpenAPI 3 specs) | 12 | 1,129 | 3.5 | 1,075 |

Held-out, outside the repo: 195 docs, 1.2 MB. **Same genres, mostly different files**: mostly different repos (GitHub Actions: pytorch and
vscode; k8s: istio and kube-prometheus; Helm: grafana and traefik charts; OpenAPI: redoc and a
different APIs-guru spec) plus the other half of the repos split by hash (starter-workflows,
awesome-compose, bitnami, learn.openapis.org). Per kind: gha 38 docs / 195 KB, k8s 101 / 293, helm 14 / 293,
compose 13 / 10, openapi 29 / 439. Licences are all MIT, BSD-3, Apache-2.0, CC0 or CC-BY-4.0
(attribution kept in SOURCES). Sources are listed in `heldout/SOURCES.txt`.

Caveats on the mix: compose is tiny (13 KB) so its slice is thin; OpenAPI in train is one 1.07 MB
APIs-guru spec plus small ones, so that single document is a third of the bytes (but only one of 89
items, so it barely moves the median-of-items block score); the multi-document manifests were split
into single documents because `load()` rejects multi-document input, so the corpus has no `---` streams.

## What the guards catch (self-test)

Scratch copies of the committed tree, one deliberate bug each, built, then every check run. "Own tests"
is js-yaml's own suite. The ESM and CommonJS equality runs gave identical counts.

| Deliberate bug | Own tests | Source | Equality (ESM / CJS) | Fuzz | Bench check |
|---|---|---|---|---|---|
| `0o[0-7]+` -> `0o[0-6]+` (int regex) | **missed** | clean | caught (56 / 56) | caught (116) | none |
| `-0.0` loses its sign | caught (2) | clean | caught (70 / 70) | caught (28) | none |
| float first-char prefilter without `+` | caught (1) | clean | caught (112 / 112) | caught (14) | none |
| timestamp fraction cut to 2 digits | caught (1) | clean | caught (48 / 48) | caught (583) | none |
| error message says line+2 | **missed** | clean | caught (6,363 / 6,363) | caught (4,844) | none |
| float rounded to 2 places | caught (6) | clean | caught (317 / 317) | caught (466) | **caught (1 of 89 items)** |
| `load()` caches its result by input text | caught (7) | clean | caught (6,109 / 6,109) | caught (1,676) | none (each doc loaded once) |
| added `process.env.HOME` read in `src/` | missed | **caught** | clean | clean | none |

Takeaways: the output-equality guard and the fuzz each caught all seven behavioural bugs, including the
two that js-yaml's own suite misses; the source guard catches the non-behavioural cheat the others cannot
see; the benchmark's own output check only sees what the corpus exercises, so it is a backstop. (The
cache bug is also what the freshness check targets: a result reused from an earlier call is a difference.)

## Open questions

1. **Noise went up** with the hardening (see above): `useful_effect` is 5% and a try will often extend to
   20 blocks. If that is too slow: more runs per measurement (costs seconds per block), or accept 6%.
2. **Held-out set is small and a bit lopsided** (195 docs; 394 KB of it is one OpenAPI doc, 247 KB Istio).
3. **The guards run candidate code in a process that can read the pinned original** (read-only,
   sha256-checked). A hostile patch could call it to pass the guards; it would then be no faster, fail the
   benchmark's output check, or trip `source.mjs` (`node:`, `process.`, `import(`...). The benchmark child
   cannot read it at all.
4. **`source.mjs` is a regex over added lines**: a determined climber can avoid the listed words. Every
   kept patch is still read by a human. `node_modules` is read-only, not hashed.
5. **JIT/cold start is in the metric on purpose** (one `load()` per doc after a 35 KB warm-up). A
   `load_hot_ms` mechanism metric was not added.
6. **The block score is a median of item ratios** (small-document weighted); the `total_ms` metric guard
   (5%) is the only protection for big-document performance.
7. Not timed and only lightly guarded: `dump()` (equality in the fuzz), `present()`, `parseEvents()`, the
   AST APIs (js-yaml's own tests only).
8. Tag 5.5.0 ("5.5.0 released", 2026-10-16) is a TypeScript rewrite whose API differs from 4.x.
9. The 0.93 cross-check floor and the 1 ms drain limit were set from ~70 honest runs on this PC; a much
   slower or busier machine could trip them (a false `candidate_invalid`), which `hca` shows as an invalid
   try, not a wrong keep.

## Next steps (not done)

`cd ~/hca-showcase-yaml/js-yaml && hca init --heldout ~/hca-showcase-yaml/heldout` (its A/A needs a
quiet PC), then hand the climber `BRIEF.md` and the hca skill.
