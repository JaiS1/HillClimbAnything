// Canonical, type-aware serialisation of a load() result or of a thrown error.
// Two results are "the same" iff their canonical strings are identical. It distinguishes:
// -0 / 0, NaN, Infinity, bigint, Date (and invalid Date), binary (typed arrays), Map / Set,
// own-property order, property flags, prototypes, sparse arrays, and shared references
// (an alias in the YAML must stay the same object, a copy is a difference).
//
// Hardening: this module is imported BEFORE the library under test, and captures every builtin it
// uses at load time, so a library that patches Object / Reflect / JSON / Map / Date prototypes later
// cannot change how its own output is read. Proxies are rejected. With a `registry` (a WeakSet),
// a result that contains an object returned by an EARLIER load() is rejected (no result reuse).
import { createHash } from 'node:crypto'
import { types } from 'node:util'

const uncurry = fn => Function.prototype.call.bind(fn)
const getProto = Object.getPrototypeOf
const getDesc = Object.getOwnPropertyDescriptor
const hasOwn = Object.hasOwn
const ownKeys = Reflect.ownKeys
const isArray = Array.isArray
const is = Object.is
const stringify = JSON.stringify
const toStr = String
const ObjectProto = Object.prototype
const ArrayProto = Array.prototype
const join = uncurry(Array.prototype.join)
const mapForEach = uncurry(Map.prototype.forEach)
const setForEach = uncurry(Set.prototype.forEach)
const mapGet = uncurry(Map.prototype.get)
const mapSet = uncurry(Map.prototype.set)
const mapHas = uncurry(Map.prototype.has)
const dateTime = uncurry(Date.prototype.getTime)
const dateISO = uncurry(Date.prototype.toISOString)
const symbolToString = uncurry(Symbol.prototype.toString)
const getName = (fn) => { const d = getDesc(fn, 'name'); return d && hasOwn(d, 'value') ? d.value : '' }
const Uint8 = Uint8Array
const wsHas = uncurry(WeakSet.prototype.has)
const wsAdd = uncurry(WeakSet.prototype.add)
const isDate = types.isDate
const isMap = types.isMap
const isSet = types.isSet
const isProxy = types.isProxy
const HEX = Array.from({ length: 256 }, (_, i) => (i < 16 ? '0' : '') + i.toString(16))

function protoTag (v) {
  const p = getProto(v)
  if (p === null) return 'null'
  if (p === ObjectProto) return 'Object'
  if (p === ArrayProto) return 'Array'
  let name = ''
  for (let q = p; q && q !== ObjectProto; q = getProto(q)) {
    const d = getDesc(q, 'constructor')
    name += (d && hasOwn(d, 'value') && typeof d.value === 'function' ? getName(d.value) : '?') + '>'
  }
  return name
}

export function canon (root, registry) {
  const seen = new Map()
  const fresh = []
  let reused = false

  function walk (v) {
    switch (typeof v) {
      case 'undefined': return 'U'
      case 'boolean': return v ? 'T' : 'F'
      case 'number': return is(v, -0) ? 'N-0' : 'N' + toStr(v)
      case 'bigint': return 'B' + toStr(v)
      case 'string': return 'S' + stringify(v)
      case 'symbol': return 'Y' + symbolToString(v)
      case 'function': return 'Fn:' + getName(v)
    }
    if (v === null) return 'Z'
    if (isProxy(v)) return 'PROXY!'
    if (mapHas(seen, v)) return '@' + mapGet(seen, v)
    if (registry && wsHas(registry, v)) reused = true
    mapSet(seen, v, seen.size)
    fresh[fresh.length] = v

    if (isDate(v)) {
      const t = dateTime(v)
      return 'D' + protoTag(v) + ':' + (t !== t ? 'invalid' : dateISO(v)) + ':' + t
    }
    if (ArrayBuffer.isView(v)) {
      const bytes = new Uint8(v.buffer, v.byteOffset, v.byteLength)
      let hex = ''
      for (let i = 0; i < bytes.length; i++) hex += HEX[bytes[i]]
      return 'V' + protoTag(v) + ':' + v.length + ':' + hex
    }
    if (isMap(v)) {
      const parts = []
      mapForEach(v, (val, k) => { parts[parts.length] = walk(k) + '=>' + walk(val) })
      return 'M' + protoTag(v) + '{' + join(parts, ',') + '}'
    }
    if (isSet(v)) {
      const parts = []
      setForEach(v, k => { parts[parts.length] = walk(k) })
      return 'Set' + protoTag(v) + '{' + join(parts, ',') + '}'
    }
    const arr = isArray(v)
    const parts = []
    for (const key of ownKeys(v)) {
      if (arr && key === 'length') continue
      const d = getDesc(v, key)
      const flags = (d.enumerable ? 'e' : '-') + (d.writable ? 'w' : '-') + (d.configurable ? 'c' : '-')
      const k = typeof key === 'symbol' ? symbolToString(key) : stringify(key)
      parts[parts.length] = k + flags + ':' + (hasOwn(d, 'value') ? walk(d.value) : 'accessor')
    }
    return (arr ? 'A' + v.length : 'O') + protoTag(v) + '{' + join(parts, ',') + '}'
  }

  let out
  try {
    out = walk(root)
  } catch (e) {
    if (e instanceof RangeError) return 'TOO-DEEP'
    throw e
  }
  if (reused) return 'REUSED-OBJECT!'
  if (registry) for (let i = 0; i < fresh.length; i++) wsAdd(registry, fresh[i])
  return out
}

// An error is compared by class chain, name, message (includes the snippet), and its own data.
export function canonError (e) {
  if (e === null || typeof e !== 'object') return 'THROWN-NON-OBJECT:' + canon(e)
  if (isProxy(e)) return 'PROXY!'
  const own = {}
  for (const key of ownKeys(e)) {
    if (key === 'stack') continue
    own[typeof key === 'symbol' ? symbolToString(key) : key] = e[key]
  }
  return 'ERR:' + protoTag(e) + '|' + e.name + '|' + stringify(e.message) + '|' + canon(own)
}

// Run fn and canonicalise its outcome: value or thrown error.
export function outcome (fn, registry) {
  try {
    return 'OK:' + canon(fn(), registry)
  } catch (e) {
    return canonError(e)
  }
}

const createHashFn = createHash
export function sha256 (s) {
  return createHashFn('sha256').update(s).digest('hex')
}
