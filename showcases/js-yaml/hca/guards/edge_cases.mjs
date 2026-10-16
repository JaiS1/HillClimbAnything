// Hand-written edge cases plus the yaml-test-suite (valid and deliberately invalid documents).
// Used by equality.mjs: each text is loaded with every option set by the candidate and by the
// pinned original, and value / thrown error must match exactly.
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { ROOT, loadPinned } from '../lib/libs.mjs'

const deep = (n, open, close, leaf) => open.repeat(n) + leaf + close.repeat(n)
const bomb = levels => {
  const lines = ['a0: &a0 [x, x, x, x, x]']
  for (let i = 1; i < levels; i++) lines.push(`a${i}: &a${i} [${Array(5).fill('*a' + (i - 1)).join(', ')}]`)
  return lines.join('\n')
}

export const HAND_WRITTEN = [
  // numbers
  ...['0', '-0', '+0', '0.0', '-0.0', '.5', '5.', '1e3', '1E+3', '1e-3', '0x1F', '0X1f', '0o17', '017', '0b101', '1_000', '1,000',
    '+12', '-12', '--12', '0xdeadbeef', '9007199254740991', '9007199254740993', '123456789012345678901234567890', '-9007199254740993',
    '.inf', '.Inf', '.INF', '-.inf', '+.inf', 'inf', '.nan', '.NaN', '.NAN', 'nan', '1:30:00', '190:20:30.15', '0.1.2', '1e', '0x', '0xG',
    '1__0', '_1', '1_', '3.14159265358979323846', '1.7976931348623157e309', '5e-324', '0.30000000000000004', '00', '08', '0.', '-.5', '+.5'].flatMap(v => [v, `n: ${v}`, `[${v}, ${v}]`, `${v}: x`, `"${v}"`, `!!int ${v}`, `!!float ${v}`]),
  // custom tag that re-enters load() (option set `reentrant`)
  '!re 1', 'a: !re x', '- !re 1\n- !re 2', '!re [', '!re a: b', '!re "q"', '!re',
  // booleans / null / yaml 1.1 words
  ...['true', 'True', 'TRUE', 'false', 'yes', 'Yes', 'YES', 'no', 'No', 'on', 'On', 'off', 'OFF', 'y', 'n', 'Y', 'N', '~', 'null', 'Null', 'NULL', '', 'nULL']
    .flatMap(v => [v, `k: ${v}`, `${v}: v`, `[${v}]`, `!!bool ${v}`, `!!null ${v}`]),
  // timestamps and binary
  ...['2001-12-14t21:59:43.10-05:00', '2001-12-14 21:59:43.10 -5', '2001-12-15 2:59:43.10', '2002-12-14', '2002-13-45', '2002-02-30',
    '2001-12-14T21:59:43Z', '2001-12-14T21:59:43.123456789Z', '0000-01-01', '9999-12-31T23:59:59.999Z', '2001-1-1', '1e3-01-01', '2001-12-14T24:00:00Z']
    .flatMap(v => [v, `d: ${v}`, `!!timestamp ${v}`, `d: !!timestamp ${v}`]),
  '!!binary |\n  R0lGODlhDAAMAIQAAP//9/X17unp5WZmZgAAAOfn515eXvPz7Y6OjuDg4J+fn5\n  OTk6enp56enmlpaWNjY6Ojo4SEhP/++f/++f/++f/++f/++f/++f/++f/++f/++SH+Dk1hZGUgd2l0aCBHSU1QACwAAAAADAAMAAAFLC',
  '!!binary "SGVsbG8="', '!!binary "SGVsbG8"', '!!binary "%%%"', '!!binary ""', '!!binary |\n  SGVs\n  bG8=', '!!binary 123',
  // collections / tags
  '!!set {a, b, c}', '!!set {a: 1}', '!!omap [a: 1, b: 2]', '!!omap [a: 1, a: 2]', '!!omap [a, b]', '!!pairs [a: 1, a: 2]', '!!pairs [a]',
  '!!seq [1, 2]', '!!map {a: 1}', '!!str 12', '!!str', '!!int "3"', '!!float "x"', '!!map [1]', '!!seq {a: 1}', '!custom x', '!<tag:yaml.org,2002:str> 5',
  '!e!foo bar', '%TAG !e! tag:example.com,2000:\n---\n!e!foo bar', '%TAG ! tag:x,2000:\n---\n!a b', '%YAML 1.1\n---\na', '%YAML 1.2\n---\na',
  '%YAML 2.0\n---\na', '%YAML 1.1\n%YAML 1.2\n---\na', '%FOO bar\n---\na', '%TAG !e! x\n%TAG !e! y\n---\na',
  // merge keys, anchors, aliases
  'a: &x {k: 1}\nb:\n  <<: *x\n  j: 2', 'a: &x {k: 1}\nb: &y {j: 2}\nc:\n  <<: [*x, *y]\n  k: 9', '<<: {a: 1}', '<<: 1', '<<: [1]', 'a: &x 1\n<<: *x',
  'a: &x {k: 1}\nb: {<<: *x, <<: *x}', 'a: &x [1, 2]\nb: *x', 'a: *missing', 'a: &x 1\na2: &x 2\nb: *x', '&a [*a]', '&a {k: *a}', '&a a: *a',
  '- &a x\n- *a\n- &a y\n- *a', 'a: &a\n  b: *a', '*a', '&a', '&a !!str', bomb(4), bomb(5),
  // keys
  '__proto__: 1', '__proto__: {a: 1}', '{__proto__: {a: 1}}', 'constructor: 1', 'toString: x', 'hasOwnProperty: 1', 'a: 1\na: 2', '{a: 1, a: 2}',
  '? a\n: b', '? [a, b]\n: c', '? {a: 1}\n: c', '? a', '?\n:', ': x', '? |\n  multi\n: v', '[a]: 1', '{a: 1}: 2', '1: a\n"1": b\n1.0: c', 'null: a\n~: b',
  'true: a\n"true": b', '? - a\n  - b\n: c', '"a\\nb": 1', '2: a\n1: b\n10: c\nz: d\n0: e', '-1: a', '1e3: a', '0x10: a\n16: b',
  // scalars and escapes
  '"\\x41\\u0042\\U00000043"', '"\\xZZ"', '"\\u12"', '"\\U0001F600"', '"\\U00110000"', '"\\ud83d"', '"\\ud83d\\ude00"', '"\\q"', '"\\0\\a\\b\\t\\n\\v\\f\\r\\e\\ \\"\\/\\\\\\N\\_\\L\\P"',
  '"tab\there"', "'it''s'", "'multi\n  line'", '"multi\n  line\n\n  para"', '"trailing \\\n  continued"', 'plain\n  multi\n  line', 'a #b', 'a#b', 'a: b #c', 'a:b', 'a :b',
  '|\n literal\n  indented\n\n trailing', '|-\n strip\n', '|+\n keep\n\n\n', '>\n folded\n text\n\n more\n  indented\n', '>-\n a\n b', '>+\n a\n\n', '|2\n  two\n   three', '|1-\n x', '|0\n x',
  '|\n\n\n x', '>\n\n x', '|\n', '>\n', 'a: |\n  x\nb: >-\n  y\n  z\nc: |+\n  w\n\nd: 1', '- |\n  a\n- >\n  b', '--- |\n  doc', '--- >\n  doc', '|\n\ta',
  '\uFEFFa: 1', '\uFEFF', '\uFEFF---\na', 'a: 1\r\nb: 2\r\n', 'a: |\r\n  x\r\n  y\r\n', 'a: "x\r\n  y"', 'a: 1\rb: 2', 'a:\u0085b', 'a: \u2028 b', 'a: \u2029',
  'a:\n\tb', 'a:\t b', '\tb: 1', 'a: \tb', '- a\n\t- b', 'a: 1\u00a0', 'é: ü', '中文: 值', '🎉: 🎊', 'a: \u0000', 'a: \u0001', 'a: \u007f', 'a: \u0080', 'a: \uFFFE', 'a: \uFEFF',
  'a: \ud800', 'a: \udc00x', '"\ud800"', 'a: "\u0007"', '"\u0085"',
  // structure / errors
  '', ' ', '\n', '# comment only', '---', '--- ', '...', '---\n...', '---\n---', '--- #c\na', 'a\n---\nb', 'a\n---\nb\n---\nc', '---\na\n...\n---\nb', '---\n...\n---\na',
  '%YAML 1.2', '---\na\n...\n%YAML 1.2\n---\nb', 'a\n...\nb', '- a\n- b\n- c', '- - a\n  - b\n- - c', '-\n  - a', '- ', '-', '- - -', '-a', '- a\nb: c', 'a: b\n- c',
  'a:\n  - b\n  - c\nd: e', 'a:\n- b\n- c', 'a:\n  b: 1\n c: 2', 'a:\n b: 1\n  c: 2', 'a: b: c', 'a: [b: c]', 'a: {b: c: d}', 'a: b\n  c', 'a:\n  b\n  c', 'a:    \n  b',
  '[a, b, c]', '[a, b, c,]', '[a, b,, c]', '[,]', '[', ']', '[a', 'a]', '[a: b, c: d]', '[? a : b]', '[a, [b, [c, [d]]]]', '[[[[[]]]]]', '[ a , b ]', '[a\n, b]', '[a,\n\n b]', '[a #c\n, b]',
  '{a: 1, b: 2}', '{a: 1, b: 2,}', '{a, b}', '{a: }', '{: a}', '{a: 1', '{', '}', '{a: 1}}', '{? a : b}', '{"a": 1, \'b\': 2}', '{a: {b: {c: {}}}}', '{a:1}', '{a: 1,\n b: 2}', '{ }', '{\n}',
  '"unterminated', "'unterminated", '[unterminated', '{a: 1', 'a: "b', 'a: \'b', '"a" b', '"a": "b" c', 'a: "b" c', '[a] b', '{a: 1} b', '- a\n  - b', '- a\n - b', 'a: b\n c: d', 'a: b\n  c: d',
  'key: [\n  a,\n  b\n]', 'key: [\na\n]', 'key:\n[a]', 'key:\n{a: 1}', 'a: !!str\nb: 1', 'a: !!str\n  b', 'a: &x\n  - 1', 'a: !x &y z', 'a: &y !x z', '!x !y z', '&x &y z', '- !!str\n- b', '!!map\na: 1',
  '"a": 1\n"b"  : 2', '? a\n? b', 'a: 1\n? b', '- ? a\n  : b', '- a: 1\n  b: 2\n- c: 3', '-   a:  1\n    b:  2', 'a:\n  - b:\n      c: 1', 'a: - b', 'a: -b', 'a: - ', '- - - - a',
  '# c\na: 1 # d\n# e\nb: 2 # f\n', 'a: 1\n\n\n\nb: 2', 'a: 1\n  # c\nb: 2', 'a: # c\n  b', 'a: # c\n  - b', '#c\n---\n#c\na\n#c\n...\n#c', ' # c\na', 'a #\n', '!!str #c',
  deep(100, '[', ']', 'x'), deep(100, '{a: ', '}', 'x'), deep(300, '- ', '', 'x').replace(/(- )+/, m => m), Array.from({ length: 60 }, (_, i) => ' '.repeat(i * 2) + 'k' + i + ':').join('\n') + ' v',
  Array.from({ length: 200 }, (_, i) => `k${i}: v${i}`).join('\n'), 'x: ' + 'a'.repeat(100000), 'x: "' + 'b '.repeat(20000) + '"', '- ' + 'x\n- '.repeat(5000) + 'y',
  'a: !!str ' + 'x'.repeat(10), 'a: !<tag:yaml.org,2002:int> 5', 'a: !<bad', 'a: !!', 'a: !', 'a: !!unknown x', 'a: !unknown {b: c}', 'a: !unknown [b]', '%TAG !! tag:example.com,2000:\n---\n!!int 3',
  'a: 1 # c\nb:\n  c:\n    - d\n    - e: f\n      g: h\n  i: j', 'k: v\n...\n', 'k: v\n...\nextra', 'k: v\n--- extra', '--- extra', '--- [a]\n', '--- {a: 1}', '--- !!str\n', '--- &a\n- *a', '---a', '--a', '-- a',
  '...\n---\na', '... # c', '--- # c', 'a\n--- # c\nb', '--- a\n--- b', "a: 'b''c'", 'a: "b\\"c"', "a: 'b\\c'", 'a: ""', "a: ''", 'a:   ', 'a:\n', 'a:\n\n\n', ':', '::', ': :', '? ', '?', ',', ',a', '@a', '`a', '%a', '!', '&', '*', '|', '>',
  "'", '"', '[]', '{}', '- []', '- {}', 'a: []', 'a: {}', 'a: [ ]', 'a:\n  []', '[]:', '[]: []', '{}:', '"": ""', "'': ''", '? ""\n: ""'
]

// yaml-test-suite src/*.yaml: each file is a list of cases with `yaml` text and an optional `fail`.
function unescapeFixtureText (text) {
  return text
    .replaceAll('␣', ' ')
    .replace(/—*»/g, '\t')
    .replaceAll('←', '\r')
    .replaceAll('⇔', '\uFEFF')
    .replaceAll('↵', '')
    .replace(/∎\n$/u, '')
}

export async function specCases () {
  const dir = join(ROOT, 'test', 'spec', 'yaml-test-suite', 'src')
  const { load } = await loadPinned()
  const out = []
  for (const name of readdirSync(dir).filter(n => n.endsWith('.yaml')).sort()) {
    const cases = load(readFileSync(join(dir, name), 'utf8'))
    cases.forEach((c, i) => {
      if (typeof c.yaml === 'string') out.push({ label: `${name}#${i}${c.fail ? ' (invalid)' : ''}`, text: unescapeFixtureText(c.yaml) })
    })
  }
  return out
}
