#!/usr/bin/env bash
# Deterministic regressions for canonical HF cache staging and human output.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-pull-weights.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT
export CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"

pass=0
fail=0

ok() {
  echo "OK   $1"
  pass=$((pass + 1))
}

not_ok() {
  echo "FAIL $1" >&2
  fail=$((fail + 1))
}

assert_true() {
  local label="$1"
  shift
  if "$@"; then ok "$label"; else not_ok "$label"; fi
}

assert_contains() {
  local body="$1" pattern="$2" label="$3"
  if printf '%s\n' "$body" | grep -Eq "$pattern"; then
    ok "$label"
  else
    not_ok "$label"
  fi
}

assert_not_contains() {
  local body="$1" pattern="$2" label="$3"
  if printf '%s\n' "$body" | grep -Eq "$pattern"; then
    not_ok "$label"
  else
    ok "$label"
  fi
}

mkdir -p "$STATE_DIR/bin" "$STATE_DIR/hf"
legacy="$STATE_DIR/hf/models--Qwen--Qwen3-1.7B"
snapshot="$legacy/snapshots/test"
mkdir -p "$snapshot" "$legacy/refs"
printf 'test\n' >"$legacy/refs/main"
printf '{}\n' >"$snapshot/config.json"
printf 'weight-data\n' >"$snapshot/model.safetensors"

cat >"$STATE_DIR/bin/hf" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${HF_SHIM_LOG:?}"
expected="${HF_CACHE:?}/hub"
case " $* " in
  *" --cache-dir $expected "*) ;;
  *) echo "wrong cache dir" >&2; exit 64 ;;
esac
case " $* " in
  *" --quiet "*) ;;
  *) echo "missing quiet mode" >&2; exit 65 ;;
esac
test -d "$expected/models--Qwen--Qwen3-1.7B"
printf '%s\n' "$expected/models--Qwen--Qwen3-1.7B/snapshots/test"
SHIM
chmod +x "$STATE_DIR/bin/hf"
: >"$STATE_DIR/hf.log"

set +e
output=$(COLUMNS=48 HF_CACHE="$STATE_DIR/hf" HF_SHIM_LOG="$STATE_DIR/hf.log" \
  PATH="$STATE_DIR/bin:$PATH" PULSAR_VERBOSE=0 \
  "$REPO_DIR/scripts/pull-weights.sh" qwen3-1.7b --yes 2>&1)
rc=$?
set -e

assert_true "legacy cache staging exits 0" test "$rc" -eq 0
assert_true "legacy cache moved to canonical hub" \
  test -d "$STATE_DIR/hf/hub/models--Qwen--Qwen3-1.7B"
assert_true "legacy top-level model cache is gone" \
  test ! -e "$STATE_DIR/hf/models--Qwen--Qwen3-1.7B"
assert_true "hf receives canonical cache directory" \
  grep -Fq -- "--cache-dir $STATE_DIR/hf/hub" "$STATE_DIR/hf.log"
assert_true "hf runs quietly in default mode" \
  grep -Fq -- "--quiet" "$STATE_DIR/hf.log"
assert_contains "$output" '^MODEL FILES$' \
  "staging starts with a semantic model section"
assert_contains "$output" '^STORAGE CHECK$' \
  "storage uses a semantic section"
assert_contains "$output" '^CACHE LOCATION$' \
  "legacy adoption is explained"
assert_contains "$output" '^DOWNLOAD COMPLETE$' \
  "download completion is explicit"
assert_contains "$output" '^MODEL FILES READY$' \
  "verification ends with a semantic ready section"
assert_not_contains "$output" 'disk rank|r0=|HF_HUB_OFFLINE|sha256:' \
  "default staging output hides implementation jargon"
assert_true "staging output honors a 48-column terminal" \
  env RENDERED_OUTPUT="$output" python3 -c \
    'import os; assert all(len(line) <= 48 for line in os.environ["RENDERED_OUTPUT"].splitlines())'

# If both layouts exist, fail closed before invoking Hugging Face or merging.
mkdir -p "$STATE_DIR/hf/models--Qwen--Qwen3-1.7B"
: >"$STATE_DIR/hf.log"
set +e
conflict_output=$(COLUMNS=48 HF_CACHE="$STATE_DIR/hf" \
  HF_SHIM_LOG="$STATE_DIR/hf.log" PATH="$STATE_DIR/bin:$PATH" \
  "$REPO_DIR/scripts/pull-weights.sh" qwen3-1.7b --yes 2>&1)
conflict_rc=$?
set -e
assert_true "cache-layout conflict exits nonzero" test "$conflict_rc" -ne 0
assert_contains "$conflict_output" '^MODEL FILE PREPARATION FAILED$' \
  "cache-layout conflict uses a semantic failure"
assert_contains "$conflict_output" 'standard cache and an older cache' \
  "cache-layout conflict explains the cause"
assert_true "cache-layout conflict does not invoke hf" \
  test ! -s "$STATE_DIR/hf.log"

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
