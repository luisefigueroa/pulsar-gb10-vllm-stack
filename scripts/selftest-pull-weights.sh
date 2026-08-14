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
if [ "${HF_EXPECT_QUIET:-1}" = 1 ]; then
  case " $* " in
    *" --quiet "*) ;;
    *) echo "missing quiet mode" >&2; exit 65 ;;
  esac
else
  case " $* " in
    *" --quiet "*) echo "unexpected quiet mode" >&2; exit 65 ;;
    *) ;;
  esac
fi
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

# A trusted schema-2 topology must also bind the bulk rsync subprocess to the
# same generated SSH config used by the surrounding mkdir and verification
# calls. Reuse the topology trust fixture instead of encoding node roles in the
# command shims.
remote_hf="$STATE_DIR/remote-hf"
remote_hub="$remote_hf/hub/models--Qwen--Qwen3-1.7B"
remote_snapshot="$remote_hub/snapshots/$revision"
topology_file="$STATE_DIR/topology.json"
ssh_config_file="$STATE_DIR/ssh-config"
mkdir -p "$remote_snapshot" "$remote_hub/refs"
printf '%s\n' "$revision" >"$remote_hub/refs/main"
printf '{}\n' >"$remote_snapshot/config.json"
printf 'weight-data\n' >"$remote_snapshot/model.safetensors"

python3 - "$REPO_DIR" "$topology_file" "$ssh_config_file" <<'PY'
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(repo / "scripts"))
sys.path.insert(0, str(repo / "scripts" / "testlib"))

import topology_manifest as manifest
from test_topology_ssh_trust import enroll

topology, _ = enroll()
manifest.write_trust_bundle(topology, sys.argv[2], sys.argv[3])
PY

cat >"$STATE_DIR/bin/ssh-fixture" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'BEGIN\n'
  printf 'ARG=%s\n' "$@"
  printf 'END\n'
} >>"${SSH_FIXTURE_LOG:?}"
command="${*: -1}"
if [[ "$command" == *"df -BG"* ]]; then
  printf '1024\n'
elif [[ "$command" == *"verify-replicated"* ]]; then
  printf '%s\n' '{"state":"ok","identity_status":"match"}'
elif [[ "$command" == *'hub/snapshots/$ref'* ]]; then
  printf '%s\n' "${REMOTE_SNAPSHOT:?}"
elif [[ "$command" == *"echo ok"* ]]; then
  printf 'ok\n'
fi
SHIM
chmod +x "$STATE_DIR/bin/ssh-fixture"

cat >"$STATE_DIR/bin/rsync" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
{
  printf 'BEGIN\n'
  printf 'ARG=%s\n' "$@"
  printf 'END\n'
} >>"${RSYNC_SHIM_LOG:?}"
SHIM
chmod +x "$STATE_DIR/bin/rsync"

: >"$STATE_DIR/ssh.log"
: >"$STATE_DIR/rsync.log"

run_remote_staging() {
  local verbose="${1:?}" expect_quiet=1 output rc
  [ "$verbose" = 0 ] || expect_quiet=0
  set +e
  output=$(COLUMNS=72 HF_CACHE="$remote_hf" \
    HF_EXPECT_QUIET="$expect_quiet" HF_SHIM_LOG="$STATE_DIR/hf.log" \
    PATH="$STATE_DIR/bin:$PATH" PULSAR_VERBOSE="$verbose" \
    PULSAR_MODEL_LIBRARY_PY="$STATE_DIR/bin/model-library" \
    PULSAR_SSH="$STATE_DIR/bin/ssh-fixture" \
    SSH_FIXTURE_LOG="$STATE_DIR/ssh.log" \
    RSYNC_SHIM_LOG="$STATE_DIR/rsync.log" \
    REMOTE_SNAPSHOT="$remote_snapshot" \
    CLUSTER_TOPOLOGY_FILE="$topology_file" \
    CLUSTER_SSH_CONFIG_FILE="$ssh_config_file" \
    "$REPO_DIR/scripts/pull-weights.sh" qwen3-1.7b \
      --node node-one-identity --yes 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' "$output" >&2
  fi
  return "$rc"
}

assert_true "quiet remote staging exits 0" run_remote_staging 0
assert_true "verbose remote staging exits 0" run_remote_staging 1

check_rsync_contract() {
  python3 - "$STATE_DIR/rsync.log" "$STATE_DIR/bin/ssh-fixture" \
    "$ssh_config_file" "$remote_hub" <<'PY'
import pathlib
import sys

lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
calls = []
current = None
for line in lines:
    if line == "BEGIN":
        current = []
    elif line == "END":
        calls.append(current)
        current = None
    elif current is not None:
        assert line.startswith("ARG=")
        current.append(line[4:])

assert len(calls) == 2, calls
for args in calls:
    remote_shell = args[args.index("-e") + 1]
    assert sys.argv[2] in remote_shell
    assert "-F" in remote_shell and sys.argv[3] in remote_shell
    assert "StrictHostKeyChecking=yes" in remote_shell
    assert "AddressFamily=inet" in remote_shell
    assert f"fixture-one.local:{sys.argv[4]}/" in args
assert any("--quiet" in args for args in calls)
assert any("--info=progress2" in args for args in calls)
PY
}

assert_true "rsync receives topology-pinned SSH in both output modes" \
  check_rsync_contract

legacy_rshell=$(PULSAR_SSH="$STATE_DIR/bin/ssh-fixture" \
  CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
  bash -c '. "$1"; load_cluster_topology >/dev/null; pulsar_rsync_remote_shell' \
    _ "$REPO_DIR/scripts/lib.sh")
assert_true "legacy rsync shell retains the configured SSH binary" \
  grep -Fq "$STATE_DIR/bin/ssh-fixture" <<<"$legacy_rshell"
assert_not_contains "$legacy_rshell" '(^| )-F( |$)' \
  "legacy rsync shell does not invent a topology config"

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
