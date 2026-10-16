#!/bin/sh
# Final check before (or after) a climb: a fresh clone of this repo's branch, installed, built and
# tested from scratch, then every guard, then a 200-process alternating A/B against the pinned build.
#   sh hca/verify-clean.sh [branch-or-commit]      (run from anywhere inside the repo)
# Uses nice and one job at a time. Leaves nothing running; the clone is removed at the end unless KEEP=1.
set -e
export PATH="$HOME/.local/node/bin:$PATH"
SRC="$(cd "$(dirname "$0")/.." && pwd)"
REF="${1:-HEAD}"
DIR="$(mktemp -d "${TMPDIR:-/tmp}/yaml-verify.XXXXXX")"
trap '[ -n "$KEEP" ] || rm -rf "$DIR"' EXIT
echo "== fresh clone of $SRC at $REF into $DIR"
git clone -q "$SRC" "$DIR/repo"
cd "$DIR/repo"
git checkout -q "$REF" 2>/dev/null || git checkout -q "$(git -C "$SRC" rev-parse "$REF")"
echo "== npm ci (+ the linux rolldown binding the lockfile lacks)"
nice -n 19 npm ci --no-audit --no-fund >/dev/null 2>&1
RD=$(node -p "require('./node_modules/rolldown/package.json').version")
nice -n 19 npm install --no-save --no-audit --no-fund --force "@rolldown/binding-linux-x64-gnu@$RD" >/dev/null 2>&1 || true
echo "== yaml-test-suite (spec:get) and build"
nice -n 19 npm run --silent spec:get >/dev/null 2>&1
nice -n 19 npm run --silent build >/dev/null 2>&1
echo "== guards"
nice -n 19 sh hca/guards/tests.sh >/tmp/verify-tests.log 2>&1 && echo "tests: pass ($(grep -E '^# pass' /tmp/verify-tests.log))" || { echo "tests: FAIL"; tail -30 /tmp/verify-tests.log; exit 1; }
nice -n 19 node hca/guards/source.mjs
nice -n 19 node hca/guards/equality.mjs | tail -2
nice -n 19 node hca/guards/equality.mjs --cjs | tail -2
nice -n 19 node hca/guards/fuzz.mjs | tail -1
echo "== benchmark (train corpus), then 200-process alternating A/B against the pinned original"
nice -n 19 node hca/bench.mjs 2>&1 >/dev/null | tail -2
nice -n 19 node hca/tools/ab.mjs 100
echo "== verify-clean: OK"
