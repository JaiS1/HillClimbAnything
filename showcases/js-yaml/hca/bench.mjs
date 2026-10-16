// HCA objective for js-yaml: time of load() over a corpus of real-world YAML documents.
//
// Each measurement run is a FRESH node process (bench_child.mjs). The parent reads the warm-up set
// and the corpus and sends the texts in the child's first message; the child may read nothing from
// disk (node --permission: only its own script, canon.mjs and dist/), cannot write, spawn or start
// workers, and gets no HCA_* variables or corpus paths. It runs a fixed warm-up pass on a separate
// small set (hca/warmup), then calls load() exactly ONCE per document, in a shuffled order, timing
// each call with process.hrtime.bigint(). The corpus has no duplicate documents, so a cache keyed on
// document text can never hit. An item (a big file, or a group of small files of one kind) is scored
// by its summed load time; 15 fresh processes give 15 runs per item, and HCA scores a block by the
// median item ratio (so this is a per-call, cold-ish time over mostly small documents).
//
// The child runs the code under test, so this parent (which never imports the library) checks:
//  - the child's summed per-call times account for >= 93% (and <= 100%) of the parent's own clock
//    around the timed phase (work moved out of the timed region would show up as missing time);
//  - nothing was left queued after the loop (a microtask / setImmediate drain of > 1 ms is a failure);
//  - importing the library took no more than the pinned original's import time + 10 ms;
//  - every result (value or thrown error) matches the digest recorded from the pinned original
//    release (type-aware canon(); proxies and results reused from an earlier load() are rejected).
// Any failure scores every item candidate_invalid.
//
// Mechanism metrics: kb (item size), total_ms (sum of all per-call times, median over runs; a metric
// guard keeps the TOTAL from regressing even if the median item improves) and ms_<kind> per kind.
//
//   node hca/bench.mjs                # measure (train corpus, or held-out when HCA_ITEMS=heldout)
//   node hca/bench.mjs --record <dir> # write <dir>/expected.json using the PINNED original
//
// Not measured: node start-up, importing the library, reading files, the warm-up, fingerprinting.
import { randomInt } from 'node:crypto'
import { readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { PINNED_DIR } from './lib/libs.mjs'
import { HERE, IMPORT_SLACK_MS, ROOT, corpusFiles, makeItems, oneRun, readTexts } from './lib/runner.mjs'

const RUNS = 15 // fresh processes per measurement; more runs is what brought A/A noise from ±6% (5 runs) to ~±2%

const median = list => {
  const s = list.slice().sort((a, b) => a - b)
  return s[s.length >> 1]
}

async function main () {
  const recordIdx = process.argv.indexOf('--record')
  const record = recordIdx !== -1
  const heldout = process.env.HCA_ITEMS === 'heldout'
  const corpusDir = record
    ? resolve(process.argv[recordIdx + 1])
    : heldout ? resolve(process.env.HCA_HELDOUT_DIR) : join(HERE, 'corpus', 'train')
  const lib = record ? join(PINNED_DIR, 'js-yaml.mjs') : join(ROOT, 'dist', 'js-yaml.mjs')
  const files = corpusFiles(corpusDir)
  const texts = readTexts(corpusDir, files)
  const seed = randomInt(2 ** 32)

  if (record) {
    const runs = []
    for (let i = 0; i < 9; i++) runs.push(await oneRun({ lib, corpusDir, files, seed: seed + i, texts }))
    const digests = Object.fromEntries(Object.entries(runs[0].digests).sort(([a], [b]) => a < b ? -1 : 1))
    const importMs = median(runs.map(r => r.importMs))
    writeFileSync(join(corpusDir, 'expected.json'), JSON.stringify({ import_ms: Math.round(importMs * 100) / 100, digests }, null, 1) + '\n')
    console.error(`recorded ${files.length} digests and import_ms=${importMs.toFixed(2)} to ${join(corpusDir, 'expected.json')}`)
    for (const r of runs) console.error(`  accounted ${(100 * r.sum / r.wallMs).toFixed(1)}% of wall, drain ${r.drainMs.toFixed(2)} ms, ${r.problems.join('; ')}`)
    return
  }

  const expected = JSON.parse(readFileSync(join(corpusDir, 'expected.json'), 'utf8'))
  const items = makeItems(corpusDir, files)
  const runs = []
  for (let i = 0; i < RUNS; i++) runs.push(await oneRun({ lib, corpusDir, files, seed: seed + i, texts }))

  const problems = runs.flatMap(r => r.problems)
  const importMs = median(runs.map(r => r.importMs))
  if (importMs > expected.import_ms + IMPORT_SLACK_MS) {
    problems.push(`importing the library took ${importMs.toFixed(1)} ms, original ${expected.import_ms} ms (+${IMPORT_SLACK_MS} allowed)`)
  }
  if (problems.length) console.error('INVALID RUN: ' + [...new Set(problems)].slice(0, 3).join(' | '))

  const kinds = [...new Set(items.map(i => i.kind))]
  const sumOver = (r, fs) => fs.reduce((a, f) => a + r.times[f], 0)
  const totalMs = median(runs.map(r => r.sum))
  const kindMs = Object.fromEntries(kinds.map(k => [k, median(runs.map(r => sumOver(r, files.filter(f => f.startsWith(k + '/')))))]))

  const out = items.map(item => {
    const perRun = runs.map(r => sumOver(r, item.files))
    const bad = problems.length > 0 || item.files.some(f => runs.some(r => r.digests[f] !== expected.digests[f]))
    return {
      id: item.id,
      runs: perRun,
      status: bad ? 'candidate_invalid' : 'valid',
      tags: { kind: item.kind },
      mechanism: {
        kb: item.files.reduce((a, f) => a + statSync(join(corpusDir, f)).size, 0) / 1024,
        total_ms: totalMs,
        ['ms_' + item.kind]: kindMs[item.kind]
      }
    }
  })
  const accounted = runs.map(r => r.sum / r.wallMs)
  console.error(`${files.length} docs in ${items.length} items x ${RUNS} fresh processes; median total load ${totalMs.toFixed(1)} ms ` +
    `(${Object.entries(kindMs).map(([k, v]) => `${k} ${v.toFixed(0)}`).join(', ')}); child accounted ${(100 * Math.min(...accounted)).toFixed(1)}-${(100 * Math.max(...accounted)).toFixed(1)}% of wall; ` +
    `import ${importMs.toFixed(1)} ms (original ${expected.import_ms}); invalid items: ${out.filter(i => i.status !== 'valid').length}`)
  const result = JSON.stringify({ metric: 'load_ms', items: out })
  if (process.env.HCA_RESULT_FILE) writeFileSync(process.env.HCA_RESULT_FILE, result + '\n')
  else console.log(result)
}

main().catch(e => {
  console.error(e)
  process.exit(1)
})
