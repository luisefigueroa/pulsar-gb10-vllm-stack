#!/usr/bin/env bash
# Static contract: Compose must not masquerade as a Pulsar-managed launch path.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "compose unsupported contract: $*" >&2
  exit 1
}

contains_compose_recommendation() {
  local line lower
  local compose_ref='docker[[:space:]-]compose(\.ya?ml)?'
  local negative='unsupported|historical[^.;]{0,40}sketch|do[[:space:]]+not[[:space:]]+(use|run|start|recommend)|not[[:space:]]+(a[[:space:]]+|the[[:space:]]+)?(supported|canonical|equivalent|preferred|recommended)'
  local command="${compose_ref}[[:space:]]+(up|run|start)([[:space:]]|$)"
  local directive="(use|prefer|run|launch|start)[[:space:]]+(the[[:space:]]+)?${compose_ref}"
  local forward_claim="${compose_ref}[^.;]{0,80}(is|as)[[:space:]]+(a[[:space:]]+|the[[:space:]]+)?(supported|canonical|equivalent|preferred|recommended)([[:space:][:punct:]]|$)"
  local reverse_claim="(supported|canonical|equivalent|preferred|recommended)[^.;]{0,40}${compose_ref}"

  while IFS= read -r line; do
    lower=${line,,}
    [[ $lower =~ $compose_ref ]] || continue
    [[ $lower =~ $negative ]] && continue
    if [[ $lower =~ $command ]] || [[ $lower =~ $directive ]] \
        || [[ $lower =~ $forward_claim ]] || [[ $lower =~ $reverse_claim ]]; then
      return 0
    fi
  done
  return 1
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

if contains_compose_recommendation < <(sed -n 'p' README.md AGENTS.md); then
  fail "primary operator guidance must not advertise the historical Compose sketch"
fi

if contains_compose_recommendation \
    <<< 'Docker Compose is unsupported; use ./pulsar start <profile> instead.'; then
  fail "an explicit negative Compose warning must remain allowed"
fi
if ! contains_compose_recommendation \
    <<< 'Run docker compose up for the supported single-node service.'; then
  fail "a Docker Compose launch recommendation must be rejected"
fi
if ! contains_compose_recommendation \
    <<< 'Docker Compose is equivalent to ./pulsar start <profile>.'; then
  fail "a positive Docker Compose equivalence claim must be rejected"
fi

echo "compose unsupported contract selftest OK"
