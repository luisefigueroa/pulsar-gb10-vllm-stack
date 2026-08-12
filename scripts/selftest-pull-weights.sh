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
revision=70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
snapshot="$legacy/snapshots/$revision"
mkdir -p "$snapshot" "$legacy/refs"
printf '%s\n' "$revision" >"$legacy/refs/main"
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
case " $* " in
  *" --revision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e "*) ;;
  *) echo "missing exact revision" >&2; exit 66 ;;
esac
test -d "$expected/models--Qwen--Qwen3-1.7B"
printf '%s\n' "$expected/models--Qwen--Qwen3-1.7B/snapshots/70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
SHIM
chmod +x "$STATE_DIR/bin/hf"

cat >"$STATE_DIR/bin/model-library" <<'SHIM'
#!/usr/bin/env python3
import json
import sys

command = sys.argv[1] if len(sys.argv) > 1 else ""
if command == "verify-profile-bundle":
    print('{"state":"match"}')
elif command == "replicated-plan":
    plan = {
        "snapshot_revision": "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        "validation": {"expected_seal": {
            "seal_id": "ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94",
            "validation_bundle_id": "9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380",
        }},
        "manifest": {
            "manifest_id": "775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1",
        },
    }
    if "--transport-envelope" in sys.argv:
        print(json.dumps({"encoded_plan": "encoded-plan", "plan": plan}))
    elif "--encoded" in sys.argv:
        print("encoded-plan")
    else:
        print(json.dumps(plan))
elif command == "verify-replicated":
    print('{"state":"ok","identity_status":"match"}')
else:
    raise SystemExit(64)
SHIM
chmod +x "$STATE_DIR/bin/model-library"
: >"$STATE_DIR/hf.log"

set +e
output=$(COLUMNS=48 HF_CACHE="$STATE_DIR/hf" HF_SHIM_LOG="$STATE_DIR/hf.log" \
  PATH="$STATE_DIR/bin:$PATH" PULSAR_VERBOSE=0 \
  PULSAR_MODEL_LIBRARY_PY="$STATE_DIR/bin/model-library" \
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
assert_true "sealed download requests the reviewed commit" \
  grep -Fq -- "--revision $revision" "$STATE_DIR/hf.log"
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
  PULSAR_MODEL_LIBRARY_PY="$STATE_DIR/bin/model-library" \
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
