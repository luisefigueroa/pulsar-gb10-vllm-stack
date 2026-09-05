#!/usr/bin/env bash
# Control-plane regression tests for speculative decode under released specs.
# A profile is a released spec (ADR 0017 Stage 4): its identity fixes the
# speculative-decode arguments, so there is no optional toggle to resolve.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-spec-decode.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
spec_fixture_env >/dev/null

assert_eq() {
  local expected="$1" actual="$2" label="$3"
  if [ "$actual" != "$expected" ]; then
    echo "$label: expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

# A spec without speculative-decode arguments serves without them.
load_conf "$ONE_NODE_ID"
resolve_spec_decode auto
assert_eq 0 "$SPEC_DECODE_ENABLED" "spec without spec-decode args stays off"
assert_eq 0 "${#SPEC_DECODE_ARGS[@]}" "spec exports no spec-decode args"

# Forcing speculative decode onto a spec that carries none must fail closed.
if (
  load_conf "$ONE_NODE_ID"
  resolve_spec_decode on
) >/dev/null 2>&1; then
  echo "forced speculative decode unexpectedly accepted without validated args" >&2
  exit 1
fi

# Contradictory overrides must fail closed.
if (
  mode=auto
  set_spec_decode_mode mode on
  set_spec_decode_mode mode off
) >/dev/null 2>&1; then
  echo "contradictory speculative-decode overrides unexpectedly accepted" >&2
  exit 1
fi

# A default-on marker without validated args must fail closed.
if (
  load_conf "$ONE_NODE_ID"
  RECOMMENDED_SPEC=1
  SPEC_DECODE_ARGS=()
  resolve_spec_decode auto
) >/dev/null 2>&1; then
  echo "default-on profile unexpectedly accepted without validated args" >&2
  exit 1
fi

# --spec-decode on a spec id is refused: identity is fixed by the spec.
if "$REPO_DIR/scripts/up.sh" "$ONE_NODE_ID" --dry-run --spec-decode \
    >"$STATE/spec-decode-flag.out" 2>&1; then
  echo "--spec-decode on a spec id unexpectedly accepted" >&2
  exit 1
fi
grep -q "identity is fixed" "$STATE/spec-decode-flag.out" \
  || { echo "--spec-decode refusal does not name the fixed identity" >&2; cat "$STATE/spec-decode-flag.out" >&2; exit 1; }

# The machine-readable catalog never marks a spec as default-on.
"$REPO_DIR/scripts/list-models.sh" --serving --json | python3 -c '
import json, sys
models = {m["id"]: m for m in json.load(sys.stdin)["models"]}
assert models
assert not any(m.get("spec_default_enabled") for m in models.values())
assert all("release_spec" in m for m in models.values())
'

grep -q -- '--no-spec-decode' "$REPO_DIR/scripts/up.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/serve.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/cluster/start-cluster.sh"

echo "spec-decode selftest passed"
