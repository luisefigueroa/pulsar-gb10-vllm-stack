#!/usr/bin/env bash
# Static contract: Compose must not masquerade as a Pulsar-managed launch path.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "compose unsupported contract: $*" >&2
  exit 1
}

first_nonempty=$(awk 'NF { print; exit }' docker-compose.yml)
[ "$first_nonempty" = "# UNSUPPORTED. Not an operator path." ] \
  || fail "docker-compose.yml must lead with the unsupported classification"

# These are literal Markdown command spans, not shell substitutions.
# shellcheck disable=SC2016
for phrase in \
  'Canonical launch is `./pulsar start <profile>`' \
  'does not load a profile or enforce STATUS/placement' \
  'exact revision/seal identity' \
  'do not make it equivalent to a Pulsar-managed launch' \
  'use `./pulsar start`'; do
  grep -Fq "$phrase" docker-compose.yml \
    || fail "docker-compose.yml is missing warning: $phrase"
done

if grep -Fq 'io.pulsar.gb10.' docker-compose.yml; then
  fail "an unmanaged Compose service must not claim Pulsar ownership labels"
fi

# These are literal Markdown command spans, not shell substitutions.
# shellcheck disable=SC2016
for phrase in \
  '`docker-compose.yml` is an unsupported historical sketch' \
  'not an equivalent' \
  'home, wizard, and `down.sh` will not manage'; do
  grep -Fq "$phrase" docs/OPERATIONS.md \
    || fail "OPERATIONS.md is missing warning: $phrase"
done

grep -Fq '# docker-compose.yml is an unsupported historical sketch.' .env.example \
  || fail ".env.example must keep Compose knobs visibly unsupported"
grep -Fq '# managed profile, identity, placement, preflight, and lifecycle contracts;' \
  .env.example \
  || fail ".env.example must describe the managed contracts Compose bypasses"

if grep -Eqi 'docker[ -]compose|docker-compose' README.md AGENTS.md; then
  fail "primary operator guidance must not advertise the historical Compose sketch"
fi

echo "compose unsupported contract selftest OK"
