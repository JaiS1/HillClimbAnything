// Shared by the equality and fuzz guards: run the candidate and the pinned original on the same
// text and option set, and compare value-or-error with the type-aware canonical form.
// The candidate's results go through a WeakSet registry: a result containing an object that an
// earlier load() already returned (a reused / cached result) is a difference.
import { outcome } from '../lib/canon.mjs'
import { loadCandidate, loadPinned } from '../lib/libs.mjs'

// Option sets are built per module (a schema object belongs to the module that made it).
export const OPTION_SETS = {
  default: () => ({}),
  yaml11: m => ({ schema: m.YAML11_SCHEMA }),
  full: m => ({ schema: m.YAML11_SCHEMA.withTags(m.mergeTag, m.realMapTag) }),
  coreMerge: m => ({ schema: m.CORE_SCHEMA.withTags(m.mergeTag, m.omapTag, m.setTag, m.binaryTag, m.timestampTag) }),
  json: () => ({ json: true }),
  named: () => ({ filename: 'doc.yml', maxAliases: 50 }),
  jsonSchema: m => ({ schema: m.JSON_SCHEMA }),
  depth3: () => ({ maxDepth: 3 }),
  depth1e6: () => ({ maxDepth: 1e6 }),
  // a custom tag whose resolver calls load() again (re-entrancy)
  reentrant: m => ({
    schema: m.CORE_SCHEMA.withTags(m.defineScalarTag('!re', { resolve: s => m.load('[' + s + ', ' + s + ']'), identify: () => false }))
  })
}

export async function loadBoth ({ cjs = false } = {}) {
  const [cand, pin] = await Promise.all([loadCandidate(cjs), loadPinned()])
  const prep = mod => Object.fromEntries(Object.entries(OPTION_SETS).map(([k, f]) => [k, f(mod)]))
  return { cand, pin, candOpts: prep(cand), pinOpts: prep(pin), registry: new WeakSet() }
}

// Returns {same:true, ok} when identical, else {same:false, optName, fn, candidate, original}.
export function compareOne (ctx, text, optName, fn = 'load') {
  const a = outcome(() => ctx.cand[fn](text, ctx.candOpts[optName]), ctx.registry)
  const b = outcome(() => ctx.pin[fn](text, ctx.pinOpts[optName]))
  if (a === b) return { same: true, ok: b.startsWith('OK:') }
  return { same: false, optName, fn, candidate: a.slice(0, 400), original: b.slice(0, 400) }
}

export function report (title, total, failures) {
  console.log(`${title}: ${total} comparisons, ${failures.length} differences`)
  for (const f of failures.slice(0, 5)) {
    console.log(`  DIFF [${f.optName}/${f.fn}] ${f.label ?? ''}\n    input:     ${JSON.stringify(String(f.text).slice(0, 160))}\n    original:  ${f.original}\n    candidate: ${f.candidate}`)
  }
  if (failures.length > 5) console.log(`  ... and ${failures.length - 5} more`)
}
