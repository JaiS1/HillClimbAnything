// Builds the benchmark corpus from sparse clones of permissively licensed repos.
//   node hca/tools/build_corpus.mjs <sources dir> <train out dir> <heldout out dir>
// Not part of the benchmark: run once, output committed (train) / kept outside the repo (heldout).
// Every kept document loads cleanly with the pinned original js-yaml; multi-document files are split
// on `---` lines into separate single documents (load() takes exactly one document); exact duplicates
// are dropped so a content-keyed cache could never hit.
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { loadPinned } from '../lib/libs.mjs'

const [sourcesDir, trainDir, heldDir] = process.argv.slice(2)
const { load } = await loadPinned()

const MAX_FILE = 1600 * 1024
const MIN_FILE = 200
const MAX_PARTS_PER_FILE = 30

// role: train | heldout | split (by hash of key: half each)
const SOURCES = [
  { dir: 'starter-workflows', kind: 'gha', role: 'split', keep: p => /^(ci|deployments|automation|code-scanning|pages)\/[^/]+\.ya?ml$/.test(p) },
  { dir: 'node', kind: 'gha', role: 'train', keep: p => /^\.github\/workflows\//.test(p) },
  { dir: 'react', kind: 'gha', role: 'train', keep: p => /^\.github\/workflows\//.test(p) },
  { dir: 'pytorch', kind: 'gha', role: 'heldout', keep: p => /^\.github\//.test(p) },
  { dir: 'vscode', kind: 'gha', role: 'heldout', keep: p => /^(\.github|build\/azure-pipelines)\//.test(p) },
  { dir: 'awesome-compose', kind: 'compose', role: 'split', keyDepth: 1, keep: p => /(compose|docker-compose)[^/]*\.ya?ml$/.test(p) },
  { dir: 'k8s-examples', kind: 'k8s', role: 'train', keep: () => true },
  { dir: 'argo-cd', kind: 'k8s', role: 'train', keep: () => true },
  { dir: 'argo-workflows', kind: 'k8s', role: 'train', keep: () => true },
  { dir: 'kube-prometheus', kind: 'k8s', role: 'heldout', keep: () => true },
  { dir: 'istio', kind: 'k8s', role: 'heldout', keep: () => true },
  { dir: 'bitnami', kind: 'helm', role: 'split', keyDepth: 2, keep: p => /\/(values|Chart)\.ya?ml$/.test(p) },
  { dir: 'prom-helm', kind: 'helm', role: 'train', keep: p => /\/(values|Chart)\.ya?ml$/.test(p) },
  { dir: 'grafana-helm', kind: 'helm', role: 'heldout', keep: p => /\/values\.ya?ml$/.test(p) },
  { dir: 'traefik-helm', kind: 'helm', role: 'heldout', keep: () => true },
  { dir: 'oas', kind: 'openapi', role: 'split', keep: () => true },
  { dir: 'petstore', kind: 'openapi', role: 'train', keep: () => true },
  { dir: 'redoc', kind: 'openapi', role: 'heldout', keep: () => true },
  { dir: 'openapi-directory', kind: 'openapi', role: 'split', keep: () => true }
]

// bytes per (role, kind)
const BUDGET = {
  train: { gha: 600e3, compose: 120e3, k8s: 850e3, helm: 900e3, openapi: 1200e3 },
  heldout: { gha: 200e3, compose: 50e3, k8s: 300e3, helm: 300e3, openapi: 450e3 }
}

const h = s => createHash('sha1').update(s).digest('hex')
const sha = s => createHash('sha256').update(s).digest('hex')

function walk (dir, out = []) {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    if (name.name === '.git') continue
    const p = join(dir, name.name)
    if (name.isDirectory()) walk(p, out)
    else if (/\.ya?ml$/.test(name.name)) out.push(p)
  }
  return out
}

function parses (text) {
  try {
    load(text)
    return true
  } catch {
    return false
  }
}

function pieces (text) {
  if (parses(text)) return [text]
  const parts = text.split(/^---[ \t]*\r?\n/m).filter(t => t.trim().length >= MIN_FILE)
  return parts.filter(parses).slice(0, MAX_PARTS_PER_FILE)
}

const seen = new Set()
const candidates = { train: {}, heldout: {} }
const stats = {}

for (const src of SOURCES) {
  const root = join(sourcesDir, src.dir)
  for (const abs of walk(root).sort()) {
    const rel = relative(root, abs).replaceAll('\\', '/')
    if (!src.keep(rel) || /(^|\/)templates\//.test(rel)) continue
    const size = statSync(abs).size
    if (size > MAX_FILE || size < MIN_FILE) continue
    const buf = readFileSync(abs)
    if (buf.includes(0)) continue
    const text = buf.toString('utf8')
    const key = src.keyDepth ? rel.split('/').slice(0, src.keyDepth).join('/') : rel
    const role = src.role === 'split' ? (parseInt(h(src.dir + '/' + key).slice(0, 2), 16) % 2 ? 'train' : 'heldout') : src.role
    const parts = pieces(text)
    parts.forEach((part, i) => {
      if (part.trim().length < MIN_FILE) return
      const d = sha(part)
      if (seen.has(d)) return
      seen.add(d)
      const flat = (src.dir + '__' + rel).replace(/[^A-Za-z0-9._-]+/g, '_').replace(/\.ya?ml$/, '')
      const name = parts.length > 1 ? `${flat}.part${String(i).padStart(2, '0')}.yaml` : `${flat}.yaml`
      ;(candidates[role][src.kind] ??= []).push({ name, text: part, src: src.dir, digest: d })
    })
  }
}

rmSync(trainDir, { recursive: true, force: true })
rmSync(heldDir, { recursive: true, force: true })
const manifest = []
for (const role of ['train', 'heldout']) {
  const outRoot = role === 'train' ? trainDir : heldDir
  for (const [kind, files] of Object.entries(candidates[role])) {
    files.sort((a, b) => h(a.name).localeCompare(h(b.name)))
    let total = 0
    mkdirSync(join(outRoot, kind), { recursive: true })
    for (const f of files) {
      const bytes = Buffer.byteLength(f.text)
      if (total + bytes > BUDGET[role][kind]) continue
      total += bytes
      writeFileSync(join(outRoot, kind, f.name), f.text)
      manifest.push({ role, kind, src: f.src, name: f.name, bytes })
    }
  }
}
writeFileSync(join(sourcesDir, 'corpus-manifest.json'), JSON.stringify(manifest, null, 1))
for (const m of manifest) {
  const k = `${m.role} ${m.kind} ${m.src}`
  stats[k] ??= { files: 0, bytes: 0 }
  stats[k].files++
  stats[k].bytes += m.bytes
}
console.table(stats)
const tot = r => manifest.filter(m => m.role === r).reduce((a, m) => a + m.bytes, 0)
console.log('train bytes', tot('train'), 'heldout bytes', tot('heldout'), 'files', manifest.length)
