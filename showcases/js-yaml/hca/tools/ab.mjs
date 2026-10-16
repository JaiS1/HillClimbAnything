// Alternating A/B of the candidate build against the pinned original, in fresh processes.
//   node hca/tools/ab.mjs [pairs=100] [corpus dir]     (default corpus: hca/corpus/train)
// Each pair runs the pinned original and the candidate on the same shuffled order, in random order,
// one fresh process each (2 x pairs processes). Reports the median total-time ratio candidate /
// original with a bootstrap 95% interval, the per-kind ratios, and whether every output matched.
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomInt } from 'node:crypto'
import { PINNED_DIR } from '../lib/libs.mjs'
import { HERE, ROOT, corpusFiles, oneRun, readTexts } from '../lib/runner.mjs'

const pairs = Number(process.argv[2] ?? 100)
const corpusDir = resolve(process.argv[3] ?? join(HERE, 'corpus', 'train'))
const files = corpusFiles(corpusDir)
const texts = readTexts(corpusDir, files)
const expected = JSON.parse(readFileSync(join(corpusDir, 'expected.json'), 'utf8'))
const libs = { original: join(PINNED_DIR, 'js-yaml.mjs'), candidate: join(ROOT, 'dist', 'js-yaml.mjs') }
const kinds = [...new Set(files.map(f => f.split('/')[0]))]
const median = l => l.slice().sort((a, b) => a - b)[l.length >> 1]
const sumKind = (r, k) => files.filter(f => f.startsWith(k + '/')).reduce((a, f) => a + r.times[f], 0)

const ratios = []
const byKind = Object.fromEntries(kinds.map(k => [k, []]))
let mismatched = 0
let problems = 0
for (let i = 0; i < pairs; i++) {
  const seed = randomInt(2 ** 32)
  const order = randomInt(2) ? ['original', 'candidate'] : ['candidate', 'original']
  const res = {}
  for (const name of order) res[name] = await oneRun({ lib: libs[name], corpusDir, files, seed, texts })
  problems += res.candidate.problems.length
  mismatched += files.filter(f => res.candidate.digests[f] !== expected.digests[f]).length
  ratios.push(res.candidate.sum / res.original.sum)
  for (const k of kinds) byKind[k].push(sumKind(res.candidate, k) / sumKind(res.original, k))
}
const boot = list => {
  const meds = Array.from({ length: 2000 }, () => median(Array.from(list, () => list[randomInt(list.length)])))
  meds.sort((a, b) => a - b)
  return [meds[50], meds[1949]]
}
const pct = x => ((x - 1) * 100).toFixed(1) + '%'
const [lo, hi] = boot(ratios)
console.log(`A/B over ${pairs} pairs (${2 * pairs} fresh processes): candidate/original total time = ${median(ratios).toFixed(4)} (${pct(median(ratios))}, 95% CI ${pct(lo)} to ${pct(hi)})`)
for (const k of kinds) console.log(`  ${k.padEnd(8)} ${pct(median(byKind[k]))}`)
console.log(`output mismatches vs recorded original: ${mismatched}; timing cross-check problems: ${problems}`)
process.exit(mismatched || problems ? 1 : 0)
