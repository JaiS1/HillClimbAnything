// Benchmark worker. One fresh process = one measurement run. It runs the code under test, so the
// parent (bench.mjs, which never imports the library) cross-checks its clock and its outputs.
// It runs under `node --permission` and may read only this file, lib/canon.mjs and the library's
// directory; it is never given a corpus path (the parent sends the texts in the first message).
//   argv[2] : absolute path of the library module
//   stdin   : first line {warmup:[text], docs:[{file,text}]}; then "warmup"; then "timed"
//   stdout  : {ready,importMs} {warmup_done} {timed_done,times,drainMs} {digests}
import { createInterface } from 'node:readline'
import { pathToFileURL } from 'node:url'
import { canon, canonError, sha256 } from './lib/canon.mjs'

// Everything below is captured BEFORE the library is imported.
const now = process.hrtime.bigint.bind(process.hrtime)
const stringify = JSON.stringify
const parse = JSON.parse
const write = process.stdout.write.bind(process.stdout)
const NumberFn = Number
const setImmediateFn = setImmediate
const PromiseFn = Promise
const send = obj => write(stringify(obj) + '\n')

const rl = createInterface({ input: process.stdin })
const lines = rl[Symbol.asyncIterator]()
const next = async () => (await lines.next()).value

const { warmup, docs } = parse(await next())

const i0 = now()
const mod = await import(pathToFileURL(process.argv[2]).href)
const importMs = NumberFn(now() - i0) / 1e6
const load = mod.load
if (typeof load !== 'function') throw new Error('no load() export')
send({ ready: true, importMs })

if (await next() !== '"warmup"') throw new Error('expected warmup')
for (let i = 0; i < warmup.length; i++) {
  try { load(warmup[i]) } catch { /* warm-up results are discarded */ }
}
send({ warmup_done: true })

if (await next() !== '"timed"') throw new Error('expected timed')
const n = docs.length
const times = new Array(n)
const results = new Array(n)
const failed = new Array(n)
for (let i = 0; i < n; i++) {
  const text = docs[i].text
  let r
  let bad = false
  const t0 = now()
  try {
    r = load(text)
  } catch (error) {
    r = error
    bad = true
  }
  const t1 = now()
  times[i] = NumberFn(t1 - t0) / 1e6
  results[i] = r
  failed[i] = bad
}

// Deferred work (microtasks, immediates) queued by load() must not escape the timed region.
const d0 = now()
await PromiseFn.resolve()
await new PromiseFn(resolve => setImmediateFn(resolve))
const drainMs = NumberFn(now() - d0) / 1e6
send({ timed_done: true, times, drainMs })

// Outside the timed region: fingerprint every output (value or error) against the recorded original.
// The registry rejects a result containing an object an earlier load() already returned.
const registry = new WeakSet()
const digests = {}
for (let i = 0; i < n; i++) {
  digests[docs[i].file] = sha256(failed[i] ? canonError(results[i]) : 'OK:' + canon(results[i], registry))
}
send({ digests })
process.exit(0)
