// Locates the candidate (this checkout's built dist) and the pinned original release.
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')

// The original js-yaml 5.5.0 release build: outside the repo, read-only, and hash-checked here.
export const PINNED_DIR = '/home/jai/hca-showcase-yaml/pinned/js-yaml-5.5.0'
const PINNED_SHA256 = '213470960d9c2ff1d01f9164e5017749bfc383d8a6ebb8683f35c76e8c6d052b'

// `cjs` loads the CommonJS build instead of the ES module build (equality.mjs --cjs).
export async function loadCandidate (cjs = false) {
  if (cjs) return createRequire(import.meta.url)(join(ROOT, 'dist', 'js-yaml.cjs.js'))
  return import(pathToFileURL(join(ROOT, 'dist', 'js-yaml.mjs')).href)
}

export async function loadPinned () {
  const file = join(PINNED_DIR, 'js-yaml.mjs')
  const digest = createHash('sha256').update(readFileSync(file)).digest('hex')
  if (digest !== PINNED_SHA256) throw new Error('pinned original js-yaml was modified: ' + digest)
  return import(pathToFileURL(file).href)
}
