// API-surface cases for the equality guard: things a parser rewrite could break that plain
// "text in, value out" comparisons miss. Each case runs the same function against the candidate and
// the pinned original (`f(module, preparedOptions)`) and compares value-or-error with canon().
import { outcome } from '../lib/canon.mjs'

const GOOD = 'a: 1\nb: [x, y]\nc: {d: 2}\n'
const BAD = 'a: [1, 2\nb: }'

function synthetic5MB () {
  const rows = []
  let size = 0
  for (let i = 0; size < 5 * 1024 * 1024; i++) {
    const row = `- id: ${i}\n  name: "item ${i} \u00e9"\n  tags: [alpha, beta, 'g${i % 7}']\n  nested:\n    x: ${(i * 1.25).toFixed(2)}\n    y: ${i % 2 === 0}\n    z: ~\n    text: |\n      line one ${i}\n      line two\n`
    rows.push(row)
    size += row.length
  }
  return rows.join('')
}

export function runApiCases (ctx, extraTexts) {
  const failures = []
  let total = 0
  let errors = 0

  function both (label, f, text = label) {
    total++
    const a = outcome(() => f(ctx.cand, ctx.candOpts), ctx.registry)
    const b = outcome(() => f(ctx.pin, ctx.pinOpts))
    if (a === b) { if (!b.startsWith('OK:')) errors++; return }
    failures.push({ label, text, optName: 'api', fn: 'api', candidate: a.slice(0, 400), original: b.slice(0, 400) })
  }

  // export surface
  both('export names and types', m => Object.keys(m).sort().map(k => k + ':' + typeof m[k]))

  // non-string input
  const weird = [null, undefined, 0, 12, true, {}, [], ['a: 1'], { toString: () => 'a: 1' }, new String('a: 1'), 10n, Buffer.from('a: 1'), Symbol('s'), () => 1, new Date(0), NaN]
  weird.forEach((v, i) => {
    both(`non-string input #${i}`, (m, o) => m.load(v, o.default))
    both(`non-string input loadAll #${i}`, (m, o) => m.loadAll(v, o.default))
  })

  // options oddities
  const optionOddities = {
    null: () => null,
    undefined: () => undefined,
    empty: () => ({}),
    unknownKey: () => ({ unknownKey: 1, schemaa: 'x' }),
    schemaUndefined: () => ({ schema: undefined }),
    filenameUndefined: () => ({ filename: undefined }),
    depthUndefined: () => ({ maxDepth: undefined }),
    jsonUndefined: () => ({ json: undefined }),
    filenameNumber: () => ({ filename: 123 }),
    stringOptions: () => 'oops',
    arrayOptions: () => [],
    depthNegative: () => ({ maxDepth: -1 }),
    depthNaN: () => ({ maxDepth: NaN }),
    depthString: () => ({ maxDepth: '3' }),
    depth0: () => ({ maxDepth: 0 }),
    jsonString: () => ({ json: 'yes' }),
    schemaNull: () => ({ schema: null }),
    schemaPlainObject: () => ({ schema: {} }),
    maxAliasesZero: () => ({ maxAliases: 0 }),
    maxAliasesNeg: () => ({ maxAliases: -5 })
  }
  const oddTexts = [GOOD, BAD, 'a: &x [1]\nb: *x', '[[[[[1]]]]]', '', '---\na\n---\nb', 'x: !!str 1', '& a: 1']
  for (const [name, mk] of Object.entries(optionOddities)) {
    oddTexts.forEach((t, i) => {
      both(`options ${name} / text ${i}`, m => m.load(t, mk()), t)
      both(`options ${name} / loadAll / text ${i}`, m => m.loadAll(t, mk()), t)
    })
  }

  // the caller's options object: mutated between calls, frozen, reused across calls
  both('options mutated between calls', (m, o) => {
    const opts = { filename: 'a.yml' }
    const seq = []
    const run = fn => seq.push(outcome(fn))
    run(() => m.load(BAD, opts))
    opts.filename = 'b.yml'
    run(() => m.load(BAD, opts))
    opts.schema = o.jsonSchema.schema
    run(() => m.load('x: 2001-01-01\ny: yes', opts))
    opts.maxDepth = 1
    run(() => m.load(GOOD, opts))
    delete opts.maxDepth
    run(() => m.load(GOOD, opts))
    return seq
  })
  both('frozen options object', (m, o) => {
    const opts = Object.freeze({ filename: 'f.yml', schema: o.yaml11.schema })
    return [outcome(() => m.load(GOOD, opts)), outcome(() => m.load(BAD, opts)), outcome(() => m.load(GOOD, opts)), Object.isFrozen(opts)]
  })
  both('options object is not modified', (m, o) => {
    const opts = { filename: 'k.yml', json: true }
    const before = JSON.stringify(opts)
    m.load(GOOD, opts)
    try { m.load(BAD, opts) } catch { /* expected */ }
    return [before, JSON.stringify(opts), Object.keys(opts)]
  })

  // an error followed by success, and the reverse: no state may leak between calls
  both('error then success', m => {
    const seq = []
    for (const t of [BAD, GOOD, '&a [*a]', GOOD, 'a: *nope', 'a: 1', '\tx', GOOD, '', GOOD]) seq.push(outcome(() => m.load(t)))
    return seq
  })
  both('anchors do not leak between documents', m => [outcome(() => m.load('a: &x 1\nb: *x')), outcome(() => m.load('b: *x')), outcome(() => m.load('c: &x 2\nd: *x'))])
  both('directives do not leak', m => [outcome(() => m.load('%TAG !e! tag:x,2000:\n---\n!e!a b')), outcome(() => m.load('!e!a b')), outcome(() => m.load('%YAML 1.2\n---\nz'))])

  // results must be fresh and mutable: load, mutate, load again
  const mutate = r => {
    if (Array.isArray(r)) r.push('MUT')
    else if (r instanceof Map) r.set('MUT', 1)
    else if (r && typeof r === 'object') r.MUT = 1
  }
  for (const t of [GOOD, '[1, 2, [3]]', 'a: &x [1]\nb: *x', '- {a: 1}\n- {a: 1}', '{}', '[]', 'x', '!!set {a, b}']) {
    both(`load, mutate, load again: ${JSON.stringify(t).slice(0, 30)}`, (m, o) => {
      const r1 = m.load(t, o.yaml11)
      mutate(r1)
      if (r1 && typeof r1 === 'object') for (const k of Object.keys(r1)) if (r1[k] && typeof r1[k] === 'object') mutate(r1[k])
      const r2 = m.load(t, o.yaml11)
      return [r1, r2]
    }, t)
  }
  for (const t of extraTexts) {
    both('load, mutate, load again (corpus doc)', (m, o) => {
      const r1 = m.load(t, o.default)
      mutate(r1)
      return [r1, m.load(t, o.default)]
    }, t)
  }

  // a large (~5 MB) synthetic document
  const big = synthetic5MB()
  both('5 MB synthetic document', (m, o) => m.load(big, o.default), 'synthetic 5 MB')
  both('5 MB synthetic document, YAML 1.1', (m, o) => m.load(big, o.yaml11), 'synthetic 5 MB')

  return { total, failures, errors }
}
