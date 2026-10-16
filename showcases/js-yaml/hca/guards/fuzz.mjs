// Guard (c): seeded differential fuzz of load() against the pinned original.
//   node hca/guards/fuzz.mjs [seed] [docs]     defaults: seed 20261016, 4000 docs (+1000 on a fresh seed)
// Three generators, ~5k documents in all:
//   values  - random JS values (Dates, binary, -0, NaN, Infinity, '__proto__' keys, hostile strings),
//             serialised by the PINNED original's dump() with random layout options
//   grammar - random YAML text covering block / flow collections, every scalar style, anchors, tags
//   mutants - grammar or values documents with random punctuation inserted / deleted / truncated,
//             which mostly produces malformed YAML, so error class and message are compared too
// The last 1000 documents use a fresh random seed (printed) so the suite is not memorisable; any
// difference prints the seed, so a failure can be replayed with `node hca/guards/fuzz.mjs <seed>`.
import { randomInt } from 'node:crypto'
import { outcome } from '../lib/canon.mjs'
import { compareOne, loadBoth, report } from './compare.mjs'

const seed = Number(process.argv[2] ?? 20261016)
const N = Number(process.argv[3] ?? 4000)
const FRESH = 1000

function rng (s) {
  let a = s >>> 0
  return () => {
    a = (a + 0x6D2B79F5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const ctx = await loadBoth()
const { dump } = ctx.pin

function makeGen (rand) {
  let last = null
  const int = n => Math.floor(rand() * n)
  const pick = list => list[int(list.length)]
  const chance = p => rand() < p

  const WORDS = ['a', 'key', 'value', 'foo bar', 'x-y', 'with: colon', '- dash', '#hash', ' lead', 'trail ', 'multi\nline', 'tab\there', 'quote"d', "it's", 'back\\slash',
    'yes', 'No', 'null', '~', 'true', 'FALSE', '0x1F', '0o17', '017', '1e3', '.inf', '-.INF', '.nan', '1_000', '+1', '1:30', '2001-12-14', '2001-12-14t21:59:43.1Z', '0.5', '-0',
    '', ' ', '@at', '`tick', '%pct', '!bang', '&amp', '*star', '|pipe', '>gt', '[br', 'br]', '{cu', 'cu}', ',comma', '?q', '? q', '---', '...', '<<', '= eq',
    'é', '中文', '🎉', ' nbsp', ' ls', '\u0085nel', '\u0007bell', 'a'.repeat(120), 'word '.repeat(30), '__proto__', 'constructor', 'toString', '1', '2', '10', '007']
  const BINS = [new Uint8Array(0), new Uint8Array([1, 2, 3]), new Uint8Array([0, 255, 128, 64, 10, 13]), Uint8Array.from({ length: 70 }, (_, i) => i * 3 % 256)]

  function value (depth) {
    const r = rand()
    if (depth > 3 || r < 0.45) {
      const k = int(12)
      if (k === 0) return pick(WORDS)
      if (k === 1) return pick([0, -0, 1, -1, 1.5, 1e21, 1e-7, 123456789012, 2 ** 53, -(2 ** 53), NaN, Infinity, -Infinity, 0.1 + 0.2, 5e-324])
      if (k === 2) return chance(0.5)
      if (k === 3) return null
      if (k === 4) return new Date(int(4e12) - 1e12)
      if (k === 5) return pick(BINS)
      if (k === 6) return int(1e6)
      if (k === 7) return rand() * 1000
      if (k === 8) return pick(WORDS) + pick(WORDS)
      if (k === 9) return String(int(1e5))
      if (k === 10) return pick(['\n', '\n\n', 'a\n', '\na', '  a\n b\n', 'x\ty', ' ', '  ', '\t'])
      return pick(WORDS)
    }
    if (r < 0.72) return Array.from({ length: int(5) }, () => value(depth + 1))
    const o = {}
    for (let i = int(5); i > 0; i--) {
      const key = pick(WORDS)
      Object.defineProperty(o, key, { value: value(depth + 1), enumerable: true, writable: true, configurable: true })
    }
    return o
  }

  function valueDoc () {
    const root = chance(0.85) ? (chance(0.5) ? Array.from({ length: 1 + int(6) }, () => value(0)) : value(-1)) : value(0)
    const opts = {
      indent: pick([1, 2, 2, 4, 8]),
      lineWidth: pick([-1, 20, 40, 80, 80, 200]),
      flowLevel: pick([-1, -1, -1, 0, 1, 2]),
      seqNoIndent: chance(0.3),
      seqInlineFirst: chance(0.7),
      forceQuotes: chance(0.15),
      quoteStyle: pick(['single', 'double']),
      flowBracketPadding: chance(0.2),
      flowSkipCommaSpace: chance(0.1),
      flowSkipColonSpace: chance(0.1),
      noRefs: chance(0.5),
      sortKeys: chance(0.2),
      skipInvalid: true
    }
    last = { root, opts }
    try {
      return dump(root, opts)
    } catch {
      return null
    }
  }

  // --- grammar generator ---
  const scalar = () => {
    const k = int(14)
    const w = pick(WORDS)
    const plain = w.replace(/[\n\r\t:#\[\]{},&*!|>'"%@`?-]/g, 'q') || 'p'
    switch (k) {
      case 0: return plain
      case 1: return JSON.stringify(w)
      case 2: return `'${w.replaceAll("'", "''").replaceAll('\n', ' ')}'`
      case 3: return pick(['1', '-1', '0', '0x1f', '0o17', '1_000', '1.5', '.5', '1e3', '.inf', '-.inf', '.nan', '-0', '+12', '0b11', '017', '190:20:30', '+1.5', '+.5', '-.5e3', '+1e3', '+.inf'])
      case 4: return pick(['true', 'false', 'yes', 'no', 'on', 'off', 'y', 'n', 'True', 'NULL', 'null', '~', ''])
      case 5: return pick(['2001-12-14', '2001-12-14t21:59:43.10-05:00', '2002-12-14 21:59:43.10 -5', '2002-13-45', '2001-12-14T21:59:43Z'])
      case 6: return `!!str ${plain}`
      case 7: return pick(['!!int 42', '!!float 1', '!!bool true', '!!null ~', '!!binary SGVsbG8=', '!!timestamp 2001-01-01', '!!int abc', '!!str', '!custom x', '!!binary "R0lGODlh"'])
      case 8: return pick(['|\n  ' + plain + '\n  more', '>\n  folded ' + plain + '\n  text\n', '|-\n  ' + plain, '|+\n  ' + plain + '\n\n', '>-\n  a\n\n  b', '|2\n   x'])
      case 9: return plain + ' ' + plain
      case 10: return pick(['*a', '*b', '*missing'])
      case 11: return `"${plain}\\n\\t\\u00e9\\x41"`
      case 12: return `"multi\n  line ${plain}"`
      default: return plain
    }
  }
  const key = () => pick(['a', 'b', 'c', 'name', 'on', 'yes', '1', 'k k', '__proto__', 'x', '"q"', "'s'", '? complex', '[1, 2]', '{a: 1}', 'null', '~', '2001-01-01', '<<', '0x10'])
  const ind = n => ' '.repeat(n)

  function flow (depth) {
    if (depth > 3 || chance(0.4)) return scalarFlow()
    if (chance(0.5)) return '[' + Array.from({ length: int(4) }, () => flow(depth + 1)).join(pick([', ', ',', ' , '])) + (chance(0.1) ? ',' : '') + ']'
    return '{' + Array.from({ length: int(4) }, () => `${pick(['a', 'b', '"c"', "'d'", 'e f', '1'])}${pick([': ', ':', ' : '])}${flow(depth + 1)}`).join(', ') + '}'
  }
  const scalarFlow = () => pick(['x', '1', 'true', 'null', '"s"', "'t'", '-0', '.nan', '2001-01-01', 'a b', '!!str 1', '&q v', '*q', '~'])

  function block (level, depth) {
    const kind = int(depth > 4 ? 2 : 5)
    const lines = []
    const n = 1 + int(4)
    if (kind === 0 || kind === 3) {
      for (let i = 0; i < n; i++) {
        const anchor = chance(0.15) ? `&${pick(['a', 'b'])} ` : ''
        const k = kind === 3 && i === 0 && chance(0.5) ? '<<' : key()
        if (chance(0.4) && depth < 4) lines.push(`${ind(level)}${k}:${chance(0.2) ? ' ' + anchor.trim() : ''}\n${block(level + pick([1, 2, 2, 4]), depth + 1)}`)
        else lines.push(`${ind(level)}${k}:${pick([' ', ' ', '  '])}${anchor}${chance(0.2) ? flow(0) : scalar()}${chance(0.1) ? ' # c' : ''}`)
      }
    } else if (kind === 1) {
      for (let i = 0; i < n; i++) {
        if (chance(0.3) && depth < 4) lines.push(`${ind(level)}-\n${block(level + 2, depth + 1)}`)
        else if (chance(0.2)) lines.push(`${ind(level)}- ${key()}: ${scalar()}\n${ind(level + 2)}${key()}: ${scalar()}`)
        else lines.push(`${ind(level)}- ${chance(0.15) ? '&a ' : ''}${chance(0.2) ? flow(0) : scalar()}`)
      }
    } else if (kind === 2) {
      return `${ind(level)}${key()}: ${flow(0)}`
    } else {
      lines.push(`${ind(level)}- ${key()}: ${scalar()}`, `${ind(level)}- - ${scalar()}`)
    }
    return lines.join('\n')
  }

  function grammarDoc () {
    let out = ''
    if (chance(0.1)) out += pick(['%YAML 1.2\n---\n', '%YAML 1.1\n---\n', '%TAG !e! tag:x,2000:\n---\n', '--- ', '---\n', '# top comment\n'])
    out += chance(0.1) ? flow(0) : block(0, 0)
    if (chance(0.1)) out += '\n' + pick(['...', '---', '--- b: 1', '# end'])
    if (chance(0.1)) out = out.replaceAll('\n', '\r\n')
    return out
  }

  const NOISE = ['', ':', '-', '?', '[', ']', '{', '}', ',', '#', '&', '*', '!', '|', '>', "'", '"', '%', '@', '`', '\t', '\n', ' ', '  ', ': ', '- ', '\\', '\u0000', '﻿', '---', '...', '!!', '&a ', '*a']
  function mutate (text) {
    let s = text
    for (let i = 1 + int(3); i > 0 && s.length; i--) {
      const at = int(s.length + 1)
      const m = int(5)
      if (m === 0) s = s.slice(0, at) + pick(NOISE) + s.slice(at)
      else if (m === 1) s = s.slice(0, at) + s.slice(at + 1 + int(3))
      else if (m === 2) s = s.slice(0, at)
      else if (m === 3) { const b = int(s.length); s = s.slice(0, at) + s[b] + s.slice(at) } else s = s.slice(0, at) + pick(NOISE) + s.slice(at + 1)
    }
    return s
  }

  return { valueDoc, grammarDoc, mutate, lastValue: () => last }
}

const failures = []
let total = 0
let errored = 0
let docs = 0
let dumps = 0
const byKind = {}
const optNames = ['default', 'yaml11', 'full', 'coreMerge', 'json']

function run (label, text, i) {
  if (text === null) return
  docs++
  // each document runs under two option sets, rotating, so every set sees thousands of documents
  for (const optName of [optNames[i % optNames.length], optNames[(i + 2) % optNames.length]]) {
    total++
    const r = compareOne(ctx, text, optName)
    const slot = (byKind[label] ??= { ok: 0, err: 0 })
    if (!r.same) failures.push({ ...r, label, text })
    else if (!r.ok) { errored++; slot.err++ } else slot.ok++
  }
}

// dump() equality: the same JS value and layout options must give the same text (or error).
function compareDump ({ root, opts }) {
  total++
  const a = outcome(() => ctx.cand.dump(root, opts))
  const b = outcome(() => ctx.pin.dump(root, opts))
  if (a !== b) failures.push({ optName: 'dump', fn: 'dump', label: 'dump', text: JSON.stringify(opts), candidate: a.slice(0, 400), original: b.slice(0, 400) })
  else dumps++
}

function batch (s, count) {
  const g = makeGen(rng(s))
  for (let i = 0; i < count; i++) {
    const kind = i % 4
    if (kind === 0) {
      run('values', g.valueDoc(), i)
      compareDump(g.lastValue())
    }
    else if (kind === 1) run('grammar', g.grammarDoc(), i)
    else if (kind === 2) run('mutant-grammar', g.mutate(g.grammarDoc()), i)
    else run('mutant-values', g.mutate(g.valueDoc() ?? 'a: 1'), i)
  }
}

const freshSeed = randomInt(2 ** 31)
batch(seed, N)
batch(freshSeed, FRESH)
console.log(`fuzz: seed ${seed} (${N} docs) + fresh seed ${freshSeed} (${FRESH} docs); ${docs} documents (+ ${dumps} dump() comparisons), ${errored} comparisons were errors in both`)
console.log('  loads that succeeded / threw (same in both), by generator:', JSON.stringify(byKind))
report('fuzz', total, failures)
if (docs < 4000) {
  console.log('SELF-CHECK FAILED: too few documents generated')
  process.exit(1)
}
process.exit(failures.length ? 1 : 0)
