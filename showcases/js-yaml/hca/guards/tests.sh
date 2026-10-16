#!/bin/sh
# Guard (a): js-yaml's own checks, i.e. `npm test` minus the steps hca's [build] already did
# (build) or that need the network (spec:get: the yaml-test-suite is linked into each arm).
set -e
cd "$(dirname "$0")/../.."
npm run --silent lint
npm run --silent type-check
node --test 'test/core/**/*.test.mjs' 'test/spec/*.test.mjs'
