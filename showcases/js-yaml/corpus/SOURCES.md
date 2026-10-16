# Training corpus: sources and licences

491 YAML documents, 3.2 MB, in `train/<kind>/`. Each came from a shallow sparse clone of the repo
below (commit shown). Files are used unmodified, except that multi-document files were split at `---`
lines into single documents (js-yaml `load()` takes exactly one document) and exact duplicates were
dropped. Only documents that load without error with js-yaml 5.5.0 are kept. Helm `templates/` files
(Go-template syntax, not YAML) were excluded. The held-out corpus comes from different repos (and a
different half of the repos that are split), and lives outside this repo.

| Source repo | Licence | Commit | Kind | Docs | KB |
|---|---|---|---|---:|---:|
| docker/awesome-compose | CC0-1.0 | `30f4b7f6a6` | compose | 19 | 13 |
| nodejs/node | MIT | `bc5af1c67c` | gha | 45 | 161 |
| facebook/react | MIT | `098d2e87a1` | gha | 25 | 85 |
| actions/starter-workflows | MIT | `e3c451d60f` | gha | 87 | 194 |
| bitnami/charts | Apache-2.0 | `bb5e98a6e3` | helm | 27 | 835 |
| prometheus-community/helm-charts | Apache-2.0 | `59c89a6eb5` | helm | 19 | 44 |
| argoproj/argo-cd | Apache-2.0 | `059ac069d8` | k8s | 76 | 638 |
| argoproj/argo-workflows | Apache-2.0 | `f40f8dc6fd` | k8s | 90 | 139 |
| kubernetes/examples | Apache-2.0 | `d6b8cd27ea` | k8s | 91 | 53 |
| OAI/learn.openapis.org | CC-BY-4.0 (attribution) | `82b82f372a` | openapi | 10 | 31 |
| APIs-guru/openapi-directory | CC0-1.0 (individual API specs carry their own terms) | `f04b8d0bcd` | openapi | 1 | 1075 |
| swagger-api/swagger-petstore | Apache-2.0 | `d57941e8fe` | openapi | 1 | 23 |

File names are `<repo>__<path in repo>[.partNN].yaml`, so each file traces back to its origin. The
warm-up set in `../warmup/` is js-yaml's own `benchmark/samples` (MIT, except the 7 MB `document_huge.yaml`,
which is not used).

`expected.json` holds a digest of the original js-yaml 5.5.0's output (value or error) for each
document, recorded by `node hca/bench.mjs --record hca/corpus/train`.
`hca/tools/build_corpus.mjs` rebuilds the corpus from the sources.
