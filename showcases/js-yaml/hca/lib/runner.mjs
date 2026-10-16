// Shared by bench.mjs and tools/ab.mjs: corpus walking, item grouping, and the fresh-process worker.
import { spawn } from 'node:child_process'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'

export const HERE = resolve(dirname(fileURLToPath(import.meta.url)), '..') // .../hca
export const ROOT = resolve(HERE, '..')
export const NODE_FLAGS = ['--max-semi-space-size=64'] // fewer scavenges: less GC-timing jitter
// Honest runs account for 98-99.4% of the parent wall clock (measured over 60+ runs); the floor leaves
// room for a scheduler stall on a busy PC.
export const MIN_ACCOUNTED = 0.93
export const MAX_DRAIN_MS = 1
export const IMPORT_SLACK_MS = 10
const SMALL = 20 * 1024 // files at least this big are their own item; smaller ones are grouped
const GROUP = 10 * 1024 // target bytes per group of small files

function walk (dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else if (e.name.endsWith('.yaml')) out.push(p)
  }
  return out
}

export function corpusFiles (dir) {
  return walk(dir).map(p => relative(dir, p).replaceAll('\\', '/')).sort()
}

// Deterministic items: {id, kind, files:[...]}.
export function makeItems (dir, files) {
  const byKind = {}
  for (const f of files) (byKind[f.split('/')[0]] ??= []).push(f)
  const items = []
  for (const [kind, list] of Object.entries(byKind)) {
    let group = []
    let bytes = 0
    let n = 0
    const flush = () => {
      if (group.length) items.push({ id: `${kind}-g${String(n++).padStart(2, '0')}`, kind, files: group })
      group = []
      bytes = 0
    }
    for (const f of list) {
      const size = statSync(join(dir, f)).size
      if (size >= SMALL) {
        items.push({ id: `${kind}-${f.split('/').pop().replace(/\.yaml$/, '').slice(0, 40)}`, kind, files: [f] })
      } else {
        group.push(f)
        bytes += size
        if (bytes >= GROUP) flush()
      }
    }
    flush()
  }
  return items
}

export function shuffle (list, seed) {
  let a = seed >>> 0 // mulberry32
  const rnd = () => {
    a = (a + 0x6D2B79F5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const out = list.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rnd() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

class Worker {
  // The child may read ONLY its own script, canon.mjs and the library's directory. It is never told a
  // corpus path: the parent reads every text and sends it in the first message, before the import.
  constructor (lib, first) {
    const env = {}
    for (const [k, v] of Object.entries(process.env)) if (!k.startsWith('HCA_') && k !== 'NODE_OPTIONS') env[k] = v
    const childFile = join(HERE, 'bench_child.mjs')
    const args = [...NODE_FLAGS, '--permission',
      `--allow-fs-read=${childFile}`, `--allow-fs-read=${join(HERE, 'lib', 'canon.mjs')}`, `--allow-fs-read=${dirname(lib)}`,
      childFile, lib]
    this.proc = spawn(process.execPath, args, { stdio: ['pipe', 'pipe', 'inherit'], env, cwd: dirname(lib) })
    this.lines = createInterface({ input: this.proc.stdout })[Symbol.asyncIterator]()
    this.proc.stdin.write(JSON.stringify(first) + '\n')
  }

  async read () {
    const { value, done } = await this.lines.next()
    if (done) throw new Error('worker died')
    return JSON.parse(value)
  }

  send (msg) {
    this.proc.stdin.write(JSON.stringify(msg) + '\n')
  }

  close () {
    this.proc.stdin.end()
    this.proc.kill('SIGKILL')
  }
}

// One fresh process = one measurement run. Returns {times, digests, sum, wallMs, importMs, drainMs, problems}.
export async function oneRun ({ lib, corpusDir, files, seed, texts }) {
  const warmupDir = join(HERE, 'warmup')
  const warmup = readdirSync(warmupDir).sort().map(f => readFileSync(join(warmupDir, f), 'utf8'))
  const order = shuffle(files, seed)
  const w = new Worker(lib, { warmup, docs: order.map(f => ({ file: f, text: texts[f] })) })
  try {
    const { importMs } = await w.read() // ready
    w.send('warmup')
    await w.read()
    const t0 = process.hrtime.bigint()
    w.send('timed')
    const timed = await w.read()
    const wallMs = Number(process.hrtime.bigint() - t0) / 1e6
    const { digests } = await w.read()
    const times = {}
    order.forEach((f, i) => { times[f] = timed.times[i] })
    const sum = timed.times.reduce((a, b) => a + b, 0)
    const problems = []
    if (sum > wallMs) problems.push(`child timings (${sum.toFixed(1)} ms) exceed the parent clock (${wallMs.toFixed(1)} ms)`)
    if (sum < MIN_ACCOUNTED * wallMs) problems.push(`child timings (${sum.toFixed(1)} ms) account for under ${MIN_ACCOUNTED * 100}% of the parent clock (${wallMs.toFixed(1)} ms)`)
    if (timed.drainMs > MAX_DRAIN_MS) problems.push(`${timed.drainMs.toFixed(2)} ms of deferred work ran after the timed loop`)
    return { times, digests, sum, wallMs, importMs, drainMs: timed.drainMs, problems }
  } finally {
    w.close()
  }
}

export function readTexts (corpusDir, files) {
  return Object.fromEntries(files.map(f => [f, readFileSync(join(corpusDir, f), 'utf8')]))
}
