# Showcase: js-yaml `load()` parses 15% faster, identical results

**Result.** On [js-yaml](https://github.com/nodeca/js-yaml) 5.5.0, HCA confirmed a **15.3% cut in
`load()` time** (median per item, lower bound 13.3%) on held-out YAML documents the climber never
tuned against. The results are unchanged: js-yaml's own test suite passes (1,633 tests; 13 skipped,
as upstream), and a differential guard found 0 differences from the original release in 25,384
comparisons on each of the ES-module and CommonJS builds.

| Check | Corpus | Change in `load()` time | Output |
|---|---|---|---|
| `hca confirm` (final, orchestrator-run) | 195 held-out docs (37 items): same genres, mostly different files | **−15.3%** median per item (at least −13.3%), 20 blocks | identical (hash guard) |
| Clean-clone A/B against the pinned original, outside the climb loop | the 491 train docs, 200 fresh processes in alternating pairs | **−15.3%** total time (95% CI −16.6% to −14.4%) | 0 mismatches |
| Equality guard vs the original, ESM and CJS builds (each) | 25,384 comparisons: corpus, 904 edge cases, 406 yaml-test-suite cases, 468 API cases | n/a | 0 differences |
| Seeded fuzz vs the original | 11,250 comparisons of random values and random or mutated YAML | n/a | 0 differences |

Held-out, per kind (the metric's own median item change): compose −8.1% (1 item), gha −16.7% (15),
helm −16.9% (6), k8s −16.2% (10), openapi −12.0% (5). Compose is only 13 KB of documents, so treat
its slice as thin.

A coding agent proposed the changes, with HCA as the referee, on 2026-10-16:

- **12 tries in about an hour of wall clock** (37 minutes of it measuring): 3 kept and 9 not kept.
  Three of the nine were invalid: try 2 failed the equality guard, try 3 made no change, and try 5
  had a block with a failed item;
- **validity:** 9 of 12 tries valid (75%);
- **confirms:** 1 of 5 used, and it passed.

## What changed

[`optimisation.patch`](optimisation.patch) touches 3 files in `src/`, +132/−31 lines. The kept
changes, in the order they landed:

1. **Try 6, a bundle of three avoid-work changes (−2.7%).** `checkPrintable` (a slice plus a regex)
   is skipped for plain scalars that only contain printable ASCII; the `nodeTags` map is filled only
   when the schema has a merge tag; and the parser stops allocating a state snapshot for every node
   and every block-mapping key (it keeps the fields in locals and builds the object only when it
   has to restore).
2. **Try 7, no per-node properties object (−4.9%).** `parseNode` shares one never-written empty
   properties object and allocates a real one only when a `!` tag or `&` anchor is about to be
   read.
3. **Try 12, a bundle of three more (−3.2%).** `load()` with no options skips merging and copying
   option objects; `parseNode` dispatches on the first character before calling the sequence, flow,
   block-scalar, quoted-scalar and alias readers, and pushes the plain-scalar event directly;
   `readPlainScalar` runs over ordinary ASCII with a table lookup and a local cursor.

The per-try estimates add up to about −10% on the train corpus; the held-out confirm of the final
state read −15.3%. We have not isolated why the confirm is larger than the sum.

There are no caches keyed on document content and no special-casing. The [brief](BRIEF.md) ruled
both out, the benchmark and guards were built to catch them, and every kept patch was read for them.

## What the referee did

Several early changes were real but small, and HCA would not keep any of them alone:

- Try 1 (skip `checkPrintable`): −2.0%, p = 0.005, below the 2.5% keep floor.
- Try 9 (first-character dispatch plus an inline scalar event): −1.8%, p = 0.003, also below it.
- Try 11 (the no-options fast path): −2.1%, p = 0.001, also below it.
- Tries 4, 5, 8 and 10: within noise or worse (+0.5%, +0.8%, −0.7%, 0.0%).
- Try 2 (record `nodeTags` only when the schema has a merge tag) was invalid, and not for being
  slow: it failed the output-equality guard on both builds, so the guard caught a real behaviour
  difference. Try 4 redid it by resolving the schema lazily, and it was then within noise.

The agent then bundled sub-floor changes into tries 6 and 12 (ledger numbers; the messages call
them T4 and T10), each of which cleared the floor as a whole (−2.7%, p = 0.0002; −3.2%,
p = 0.0003). That is allowed, and it is also the weak spot of the run: HCA judged each bundle, not
its parts, so the ledger cannot say which part earned what. This is disclosed rather than hidden,
and the held-out confirm covers the final state. Try 7 was measured on its own and kept at −4.9%.

## How it was set up (so you can judge it)

The harness is in [`hca/`](hca/), frozen during the climb, with the design notes and every check
written down in [HARNESS-NOTES.md](HARNESS-NOTES.md). The notes were written before launch.

- **Benchmark** ([`hca/bench.mjs`](hca/bench.mjs)):
  - 491 real-world documents (3.4 MB; median 1.1 KB): GitHub Actions workflows, Kubernetes
    manifests, docker-compose files, Helm values and OpenAPI specs, grouped into 89 items.
  - Each measurement uses 15 fresh node processes. Each process runs a warm-up on a separate small
    set, then calls `load()` exactly once per document in a shuffled order, timed with
    `process.hrtime.bigint()`. HCA scores a block by the median item ratio.
  - Every result (value or thrown error) is hashed against the recorded output of the original
    release. The worker runs under node's permission model, so it cannot read the corpus or the
    original, write to disk or spawn anything; a content-keyed cache can never hit and cannot
    persist. The parent also cross-checks the worker's timings against its own clock.
  - Not measured: node start-up, importing the library, reading files, the warm-up.
- **Guards**, run in the candidate arm on every try:
  - js-yaml's own lint, type-check and tests ([`tests.sh`](hca/guards/tests.sh));
  - the output-equality guard against the pinned original, on the ESM and CJS builds
    ([`equality.mjs`](hca/guards/equality.mjs));
  - seeded fuzz, with part of each run on a fresh seed ([`fuzz.mjs`](hca/guards/fuzz.mjs));
  - a source review aid that flags added lines touching the process, timers, `eval`, the benchmark
    or the corpus ([`source.mjs`](hca/guards/source.mjs));
  - a metric guard: total time may not regress by more than 5% while the median item improves.
- **Guards were tested against deliberate bugs** before the climb (table in HARNESS-NOTES.md): the
  equality guard and the fuzz each caught all seven behavioural bugs, including two that js-yaml's
  own suite misses.
- **Adversarial review before launch.** An adversarial review of the harness led to the hardening
  described in the notes (for example, the worker no longer reads the corpus itself).
- **Useful effect 5%** (keep floor 2.5%), set from `hca quick` after the hardening made the
  benchmark noisier (A/A noise ±3.3–4.8% on a busy machine, so a try often ran to 20 blocks).
- **The ledger.** [`ledger/VERDICTS.md`](ledger/VERDICTS.md) lists every try's hypothesis and
  verdict. [`ledger/attempts/`](ledger/attempts/) holds each try's message, patch, scope check,
  verdict and the small guard outputs; [`ledger/report.txt`](ledger/report.txt) is the `hca report`
  output, with [`confirm.log`](ledger/confirm.log) and [`verify-clean.log`](ledger/verify-clean.log)
  alongside. The per-run timing logs (about 230 KB per try) and js-yaml's 320 KB test-runner output
  are not included.

## Limits, honestly

- **What the metric measures.** A per-call, cold-ish time: one `load()` per document in a fresh
  process after a short warm-up, over mostly small real-world config documents, scored as the median
  item. It is **not** hot-loop throughput, and the speed-up on a long-running service that parses
  the same shapes millions of times, or on very large documents, may differ. Only `load()` was
  timed; `dump()` and the other APIs are covered by the fuzz and equality guards, not benchmarked.
- **The held-out set is "same genres, mostly different files".** It has different repos for most
  kinds (pytorch and vscode workflows, istio and kube-prometheus manifests, grafana and traefik
  charts, redoc and another APIs-guru spec), and also the other half of the repos that were split
  by file hash (starter-workflows, awesome-compose, bitnami, learn.openapis.org). It is a check on
  overfitting to particular files, not a different distribution. It is small (195 docs, 1.2 MB),
  and a few documents dominate its bytes. It lives outside the repository the climber worked in.
- **Bundled tries.** Tries 6 and 12 each combine several changes that were individually below the
  floor (see above). The confirm measures the final state, not each part.
- **One machine:** a laptop under WSL2, partly loaded during the climb. One run, one agent, one
  confirm. Node v22.21.1.
- **Guards cannot prove equivalence.** They make about 36,600 output comparisons, plus js-yaml's
  own tests; the `source.mjs` guard is a regex over added lines. A patch could still differ on an
  input none of them generate.
- **Not upstreamed.** The patch has not been submitted to, or reviewed by, js-yaml's maintainers.
  Treat it as a demonstration, not a release.

## Reproduce

The corpus is not copied here: it is made of other projects' files under their own licences
([`corpus/SOURCES.md`](corpus/SOURCES.md), [`corpus/HELDOUT-SOURCES.txt`](corpus/HELDOUT-SOURCES.txt)).
[`hca/tools/build_corpus.mjs`](hca/tools/build_corpus.mjs) rebuilds both sets from sparse clones at
the listed commits.

```bash
git clone https://github.com/nodeca/js-yaml && cd js-yaml && git checkout 5.5.0     # cb62b2d
git apply /path/to/HillClimbAnything/showcases/js-yaml/optimisation.patch
npm ci && npm run build && npm test          # on Linux the lockfile may lack the rolldown binding:
                                             # npm install --no-save @rolldown/binding-linux-x64-gnu
```

To run the harness, copy `hca/` and `hca.toml` into the repo, rebuild the corpus into
`hca/corpus/train`, and point `PINNED_DIR` in `hca/lib/libs.mjs` at a read-only copy of the
original 5.5.0 `dist/`; then `sh hca/verify-clean.sh` repeats the final check.

js-yaml is © 2011-2015 by Vitaly Puzrin, MIT ([LICENSE](LICENSE-js-yaml.md)). The patch is a
derivative of it under the same licence. The warm-up documents in `hca/warmup/` are js-yaml's own
`benchmark/samples`.
