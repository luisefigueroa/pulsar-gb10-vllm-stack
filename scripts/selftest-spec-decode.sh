#!/usr/bin/env bash
# Control-plane regression tests for profile-driven speculative decode.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

assert_eq() {
  local expected="$1" actual="$2" label="$3"
  if [ "$actual" != "$expected" ]; then
    echo "$label: expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

# The flagship's validated fast path is automatic.
load_conf deepseek-v4-flash
resolve_spec_decode auto
assert_eq 1 "$SPEC_DECODE_ENABLED" "flagship auto mode"
assert_eq profile-default "$SPEC_DECODE_SOURCE" "flagship auto source"
assert_eq --speculative-config "${SPEC_DECODE_ARGS[0]}" "flagship spec flag"
python3 - "${SPEC_DECODE_ARGS[1]}" <<'PY'
import json
import sys

config = json.loads(sys.argv[1])
assert config == {"method": "dspark", "num_speculative_tokens": 5}, config
PY

# Rollback wins, but does not mutate the validated profile configuration.
resolve_spec_decode off
assert_eq 0 "$SPEC_DECODE_ENABLED" "flagship rollback"
assert_eq forced-off "$SPEC_DECODE_SOURCE" "flagship rollback source"

# Profiles with optional speculative decode stay off unless explicitly enabled.
load_conf nemotron-3-super-120b-nvfp4
resolve_spec_decode auto
assert_eq 0 "$SPEC_DECODE_ENABLED" "optional auto mode"
resolve_spec_decode on
assert_eq 1 "$SPEC_DECODE_ENABLED" "optional forced mode"

# Invalid and contradictory policies must fail closed.
if (
  load_conf qwen3.6-27b-fp8
  resolve_spec_decode on
) >/dev/null 2>&1; then
  echo "forced speculative decode unexpectedly accepted without validated args" >&2
  exit 1
fi

if (
  mode=auto
  set_spec_decode_mode mode on
  set_spec_decode_mode mode off
) >/dev/null 2>&1; then
  echo "contradictory speculative-decode overrides unexpectedly accepted" >&2
  exit 1
fi

if (
  load_conf nemotron-3-super-120b-nvfp4
  RECOMMENDED_SPEC=1
  SPEC_DECODE_ARGS=()
  resolve_spec_decode auto
) >/dev/null 2>&1; then
  echo "default-on profile unexpectedly accepted without validated args" >&2
  exit 1
fi

# Keep the machine-readable model catalog explicit about the effective default.
"$REPO_DIR/scripts/list-models.sh" --validated --json | python3 -c '
import json, sys
models = {m["id"]: m for m in json.load(sys.stdin)["models"]}
assert models["deepseek-v4-flash"]["spec"] == "recommended"
assert models["deepseek-v4-flash"]["spec_default_enabled"] is True
assert models["nemotron-3-super-120b-nvfp4"]["spec_default_enabled"] is False
'

grep -q -- '--no-spec-decode' "$REPO_DIR/scripts/up.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/serve.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/cluster/start-cluster.sh"
