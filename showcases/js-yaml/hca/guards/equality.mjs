// Guard (b): output equality against the pinned original release.
// For every document in the corpus, the hand-written edge cases and the yaml-test-suite
// (valid and invalid), load() / loadAll() must return a deep-equal value (types, key order,
// -0, NaN, Dates, binary, aliasing) or throw an error with the same class chain and message.
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ROOT } from '../lib/libs.mjs'
import { HAND_WRITTEN, specCases } from './edge_cases.mjs'
import { runApiCases } from './api_cases.mjs'
import { compareOne, loadBoth, report } from './compare.mjs'

function walk (dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.name.endsWith('.yaml')) out.push(p)
  }
  return out
}

const cjs = process.argv.includes('--cjs')
const ctx = await loadBoth({ cjs })
const failures = []
let total = 0
let errors = 0

function check (label, text, optNames, fns = ['load']) {
  for (const optName of optNames) {
    for (const fn of fns) {
      total++
      const r = compareOne(ctx, text, optName, fn)
      if (!r.same) failures.push({ ...r, label, text })
      else if (!r.ok) errors++
    }
  }
}

// 1. the corpus the climber sees: documents under three schemas
const corpus = walk(join(ROOT, 'hca', 'corpus', 'train')).sort()
for (const file of corpus) check(file.slice(ROOT.length + 1), readFileSync(file, 'utf8'), ['default', 'yaml11', 'full', 'jsonSchema'])
const corpusTotal = total

// 2. hand-written edge cases, every option set, load and loadAll
HAND_WRITTEN.forEach((text, i) => check(`hand#${i}`, text, Object.keys(ctx.candOpts), ['load', 'loadAll']))
const handTotal = total - corpusTotal

// 3. the yaml-test-suite
const spec = await specCases()
for (const c of spec) check(c.label, c.text, ['default', 'yaml11', 'full', 'json', 'jsonSchema', 'depth3'], ['load', 'loadAll'])

// 4. API surface: odd inputs and options, state between calls, result freshness, a 5 MB document
const sample = corpus.filter((_, i) => i % 5 === 0).map(f => readFileSync(f, 'utf8'))
const api = runApiCases(ctx, sample)
total += api.total
errors += api.errors
failures.push(...api.failures)

console.log(`candidate build: ${cjs ? 'CommonJS (dist/js-yaml.cjs.js)' : 'ES module (dist/js-yaml.mjs)'}`)
console.log(`corpus docs: ${corpus.length}; hand-written edge cases: ${HAND_WRITTEN.length}; yaml-test-suite cases: ${spec.length}; API cases: ${api.total}; ` +
  `comparisons that were errors in both: ${errors}`)
report('equality', total, failures)
if (corpusTotal === 0 || handTotal === 0 || spec.length < 300) {
  console.log('SELF-CHECK FAILED: inputs missing (corpus / edge cases / yaml-test-suite)')
  process.exit(1)
}
process.exit(failures.length ? 1 : 0)
