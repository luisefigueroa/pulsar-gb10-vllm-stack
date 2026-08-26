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

# No live catalog profile enables spec decode by default after ADR 0012.
# Optional profiles stay off unless explicitly enabled.
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
"$REPO_DIR/scripts/list-models.sh" --legacy-tested --json | python3 -c '
import json, sys
models = {m["id"]: m for m in json.load(sys.stdin)["models"]}
assert "deepseek-v4-flash" not in models
assert not any(m.get("spec_default_enabled") for m in models.values())
assert models["nemotron-3-super-120b-nvfp4"]["spec_default_enabled"] is False
'

grep -q -- '--no-spec-decode' "$REPO_DIR/scripts/up.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/serve.sh"
grep -q -- '--no-spec-decode' "$REPO_DIR/cluster/start-cluster.sh"
