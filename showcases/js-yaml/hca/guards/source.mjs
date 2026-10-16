// Guard: source review. Compares every file under src/ with js-yaml 5.5.0 (commit cb62b2d) and fails on
// lines ADDED that reach outside the library: the process, the module system, timers and microtasks,
// proxies, the benchmark / corpus / hca directories, and imports that resolve outside src/. It also caps
// the size of the built module at 1.2x the original (a precomputed table of the corpus would be big).
// A failure here is a pointer for the human reviewer, not a proof of cheating; legitimate code rarely trips it.
import { execFileSync } from 'node:child_process'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { PINNED_DIR, ROOT } from '../lib/libs.mjs'

const BASE = 'cb62b2dbbaa0b3e7379d83a5d69d8d52d21b6efb'
const FORBIDDEN = /node:|process\.|globalThis|require\(|import\(|eval\b|new Function|Proxy|queueMicrotask|setTimeout|setImmediate|nextTick|\bv8\b|\.\.\/hca|corpus|\.hca|import\.meta|\?raw/
const SIZE_FACTOR = 1.2
const SRC = join(ROOT, 'src')

function walk (dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

function baseText (rel) {
  try {
    return execFileSync('git', ['show', BASE + ':' + rel], { cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 1 << 26 })
  } catch {
    return null // file did not exist in 5.5.0
  }
}

const problems = []
let added = 0
let files = 0
for (const file of walk(SRC).sort()) {
  const rel = relative(ROOT, file).replaceAll(String.fromCharCode(92), '/')
  const text = readFileSync(file, 'utf8')
  const before = baseText(rel)
  files++
  if (before === null) problems.push(`${rel}: new file (not in 5.5.0)`)
  const old = new Map()
  for (const l of (before ?? '').split('\n')) old.set(l, (old.get(l) ?? 0) + 1)
  text.split('\n').forEach((line, i) => {
    const n = old.get(line) ?? 0
    if (n > 0) { old.set(line, n - 1); return }
    added++
    if (FORBIDDEN.test(line)) problems.push(`${rel}:${i + 1}: added line matches ${FORBIDDEN.exec(line)[0]}: ${line.trim().slice(0, 100)}`)
  })
  // imports: every specifier in the file must stay inside src/; new bare specifiers are not allowed
  const specs = [...text.matchAll(/(?:from|import)\s*['"]([^'"]+)['"]/g)].map(m => m[1])
  const oldSpecs = new Set([...(before ?? '').matchAll(/(?:from|import)\s*['"]([^'"]+)['"]/g)].map(m => m[1]))
  for (const spec of specs) {
    if (spec.startsWith('.')) {
      const target = resolve(dirname(file), spec)
      if (target !== SRC && !target.startsWith(SRC + '/')) problems.push(`${rel}: import "${spec}" resolves outside src/`)
    } else if (!oldSpecs.has(spec)) {
      problems.push(`${rel}: new non-relative import "${spec}"`)
    }
  }
}
// files deleted from src are fine (they would break the build / tests)

const dist = join(ROOT, 'dist', 'js-yaml.mjs')
const size = statSync(dist).size
const limit = SIZE_FACTOR * statSync(join(PINNED_DIR, 'js-yaml.mjs')).size
if (size > limit) problems.push(`dist/js-yaml.mjs is ${size} bytes, over ${SIZE_FACTOR}x the original (${Math.round(limit)})`)

console.log(`source: ${files} files under src/, ${added} lines differ from 5.5.0, dist/js-yaml.mjs ${size} bytes; ${problems.length} problems`)
for (const p of problems.slice(0, 20)) console.log('  ' + p)
process.exit(problems.length ? 1 : 0)
