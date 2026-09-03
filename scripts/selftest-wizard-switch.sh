#!/usr/bin/env bash
# Deterministic wizard + dispatcher scenario suite (no Docker/SSH/GPU/network).
#   scripts/selftest-wizard-switch.sh
#
# Uses GUM=0 plain menus, inventory/memory fixtures, and command shims.
# Proves: ownership-safe options, no stop before final confirm, and dispatcher
# routing. Does not invoke live lifecycle against a real daemon.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0
fail=0

assert_eq() {
  local got="$1" want="$2" msg="$3"
  if [ "$got" = "$want" ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (got='$got' want='$want')" >&2
    fail=$((fail + 1))
  fi
}

assert_true() {
  local msg="$1"
  shift
  if "$@"; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg" >&2
    fail=$((fail + 1))
  fi
}

assert_false() {
  local msg="$1"
  shift
  if "$@"; then
    echo "FAIL $msg (expected false)" >&2
    fail=$((fail + 1))
  else
    echo "OK   $msg"
    pass=$((pass + 1))
  fi
}

assert_file_contains() {
  local f="$1" pat="$2" msg="$3"
  if grep -qE "$pat" "$f" 2>/dev/null; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (pattern /$pat/ not in $f)" >&2
    fail=$((fail + 1))
  fi
}

assert_file_not_contains() {
  local f="$1" pat="$2" msg="$3"
  if grep -qE "$pat" "$f" 2>/dev/null; then
    echo "FAIL $msg (unexpected /$pat/ in $f)" >&2
    fail=$((fail + 1))
  else
    echo "OK   $msg"
    pass=$((pass + 1))
  fi
}

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-wizard-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
SHIM="$STATE/bin"
mkdir -p "$SHIM" "$STATE/inv" "$STATE/mem" "$STATE/logs" "$STATE/mem_by_node"
python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$STATE/confirmed-topology.json"

MODELS_JSON='{
  "models": [
    {
      "id": "qwen3-1.7b-2node",
      "status": "tested+soaked",
      "nodes": 2,
      "source": "hf",
      "served_name": "qwen3-1.7b-2node",
      "spec": "recommended",
      "spec_default_enabled": true,
      "first_run_candidate": false,
      "release_spec": {"receipt": "missing", "identities": []}
    },
    {
      "id": "qwen3.6-27b-fp8",
      "status": "tested",
      "nodes": 1,
      "source": "hf",
      "served_name": "qwen3.6-27b-fp8",
      "spec": "none",
      "spec_default_enabled": false,
      "first_run_candidate": true,
      "release_spec": {"receipt": "missing", "identities": []}
    },
    {
      "id": "qwen3.8-27b-fp8",
      "status": "tested",
      "nodes": 1,
      "source": "hf",
      "served_name": "qwen3.8-27b-fp8",
      "spec": "none",
      "spec_default_enabled": false,
      "first_run_candidate": true,
      "family": "qwen3.8-27b-fp8",
      "family_recommended": true,
      "release_spec": {"receipt": "missing", "identities": []}
    }
  ]
}'
printf '%s\n' "$MODELS_JSON" >"$STATE/models.json"

# Minimal inventory templates via python
write_inv() {
  local path="$1"
  shift
  # remaining args as python assignment snippets applied to base
  python3 - "$path" "$@" <<'PY'
import json, sys
path = sys.argv[1]
base = {
  "schema_version": 1,
  "generated_at": "2026-08-03T00:00:00Z",
  "worker": {"ip": "", "status": "unset", "reason": "WORKER_IP unset"},
  "nodes": {
    "head": {"mem_available_gib": 100.0, "mem_status": "ok", "mem_source": "fixture"},
    "worker": {"mem_available_gib": None, "mem_status": "n/a", "mem_source": "unset"},
  },
  "services": [],
  "unmanaged_gpu_processes": [],
}
# Optional overlay JSON file as argv[2]
if len(sys.argv) > 2 and sys.argv[2].startswith("{"):
    overlay = json.loads(sys.argv[2])
    base.update({k: overlay[k] for k in overlay if k in base or k in ("services", "unmanaged_gpu_processes", "worker", "nodes")})
    for k, v in overlay.items():
        base[k] = v
with open(path, "w", encoding="utf-8") as f:
    json.dump(base, f, indent=2)
    f.write("\n")
PY
}

mem_pass() {
  local path="$1" model="${2:-qwen3.8-27b-fp8}"
  python3 -c "
import json
print(json.dumps({
  'model': '$model', 'result': 'pass', 'mode': 'cold-start',
  'already_loaded': False, 'already_how': '',
  'footprint_gib': 12.0, 'need_start_gib': 15.0,
  'weights_gib_total': 4.0, 'weights_gib_per_rank': 4.0,
  'kv_gib': 4.0, 'overhead_gib': 4.0, 'buffer_gib': 8.0,
  'spike_gib': 3.0, 'hard_floor_gib': 4.0,
  'head_available_gib': 100.0, 'worker_available_gib': None,
  'max_model_len': None, 'kv_fixed': False, 'note': '', 'reason': '',
}, indent=2))
" >"$path"
}

mem_warn() {
  local path="$1" model="${2:-qwen3.8-27b-fp8}"
  python3 -c "
import json
print(json.dumps({
  'model': '$model', 'result': 'warn', 'mode': 'cold-start',
  'already_loaded': False, 'already_how': '',
  'footprint_gib': 90.0, 'need_start_gib': 93.0,
  'weights_gib_total': 80.0, 'weights_gib_per_rank': 80.0,
  'kv_gib': 5.0, 'overhead_gib': 5.0, 'buffer_gib': 8.0,
  'spike_gib': 3.0, 'hard_floor_gib': 4.0,
  'head_available_gib': 92.0, 'worker_available_gib': None,
  'max_model_len': None, 'kv_fixed': False, 'note': '',
  'reason': 'head: available 92.00 GiB < ideal start 93.00 GiB',
}, indent=2))
" >"$path"
}

mem_fail() {
  local path="$1" model="${2:-qwen3.8-27b-fp8}"
  python3 -c "
import json
print(json.dumps({
  'model': '$model', 'result': 'fail', 'mode': 'cold-start',
  'already_loaded': False, 'already_how': '',
  'footprint_gib': 100.0, 'need_start_gib': 103.0,
  'weights_gib_total': 90.0, 'weights_gib_per_rank': 90.0,
  'kv_gib': 5.0, 'overhead_gib': 5.0, 'buffer_gib': 8.0,
  'spike_gib': 3.0, 'hard_floor_gib': 4.0,
  'head_available_gib': 20.0, 'worker_available_gib': None,
  'max_model_len': None, 'kv_fixed': False, 'note': '',
  'reason': 'head: available 20.00 GiB << footprint 100.00 GiB',
}, indent=2))
" >"$path"
}

QWEN_CONTRACT_ID=$(bash -c '. "$1/scripts/lib.sh"; load_conf qwen3.8-27b-fp8; loaded_launch_contract_id' _ "$REPO_DIR")
NEMOTRON_CONTRACT_ID=$(bash -c '. "$1/scripts/lib.sh"; load_conf nemotron-3-nano-30b-nvfp4; loaded_launch_contract_id' _ "$REPO_DIR")

svc_managed() {
  # conf state complete safe [port]
  # complete/safe: True|False (Python)
  # Nemotron is the production first-run local-files profile (receipt/occupancy).
  # Qwen stays a leftover pre-library fixture so that migration menu is covered.
  local conf="$1" state="$2" complete="$3" safe="$4" port="${5:-8000}"
  local contract_id weight_source identity_status
  case "$conf" in
    qwen3.8-27b-fp8)
      contract_id="$QWEN_CONTRACT_ID"
      weight_source=replicated
      identity_status=
      ;;
    nemotron-3-nano-30b-nvfp4)
      contract_id="$NEMOTRON_CONTRACT_ID"
      weight_source=local-files
      identity_status=receipt-occupancy
      ;;
    *) echo "svc_managed: missing contract for $conf" >&2; return 1 ;;
  esac
  local running="True" stale="False"
  [ "$state" = "stale" ] && running="False" && stale="True"
  [ "$state" = "stopped" ] && running="False"
  python3 -c "
import json
identity = '''$identity_status''' or None
print(json.dumps({
  'service_id': '$conf',
  'profile': '$conf',
  'conf': '$conf',
  'served_name': '$conf',
  'expected_nodes': 1,
  'expected_ranks': ['single'],
  'observed_ranks': ['single'],
  'container_name': 'vllm-$conf',
  'state': '$state',
  'ownership': 'managed',
  'safe_to_stop': $safe,
  'complete': $complete,
  'observability': 'complete' if $complete else 'partial',
  'api_port': $port,
  'weight_source': '$weight_source',
  'launch_contract_id': '$contract_id',
  'spec_decode': 'off',
  'model_revision': None,
  'model_seal_id': None,
  'validation_bundle_id': None,
  'model_identity_status': identity,
  'estimated_footprint_gib_per_rank': 12.0,
  'reasons': [],
  'ranks': [{
    'rank': 'single', 'node': 'head', 'expected_node': 'head',
    'container_name': 'vllm-$conf', 'container_id': 'a'*64,
    'container_id_short': 'aaaaaaaaaaaa', 'image': 'vllm/vllm-openai:v0.26.0',
    'running': $running, 'stale': $stale, 'status': '$state',
    'ownership': 'managed', 'safe_to_stop': $safe,
    'labels': {
      'io.pulsar.gb10.managed': 'true',
      'io.pulsar.gb10.conf': '$conf',
      'io.pulsar.gb10.rank': 'single',
      'io.pulsar.gb10.weight-source': '$weight_source',
      'io.pulsar.gb10.launch-contract': '$contract_id',
      'io.pulsar.gb10.spec-decode': 'off',
      **({'io.pulsar.gb10.model-identity-status': identity} if identity else {}),
    },
    'api_port': $port, 'mem_available_gib': 50.0, 'mem_status': 'ok', 'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 8000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': 12.0, 'reasons': [],
  }],
}))
"
}

svc_partial_2node() {
  local conf="$1" safe="${2:-True}"
  python3 -c "
import json
safe = '$safe' == 'True'
print(json.dumps({
  'service_id': '$conf',
  'profile': '$conf',
  'conf': '$conf',
  'served_name': '$conf',
  'expected_nodes': 2,
  'expected_ranks': ['0', '1'],
  'observed_ranks': ['0'],
  'container_name': 'vllm-cluster-$conf',
  'state': 'partial',
  'ownership': 'managed',
  'safe_to_stop': safe,
  'complete': False,
  'observability': 'partial',
  'api_port': 8000,
  'estimated_footprint_gib_per_rank': 100.0,
  'reasons': ['missing rank 1'],
  'ranks': [{
    'rank': '0', 'node': 'head', 'expected_node': 'head',
    'container_name': 'vllm-cluster-$conf', 'container_id': 'b'*64,
    'container_id_short': 'bbbbbbbbbbbb', 'image': 'img',
    'running': True, 'stale': False, 'status': 'running',
    'ownership': 'managed', 'safe_to_stop': safe,
    'labels': {'io.pulsar.gb10.managed': 'true', 'io.pulsar.gb10.conf': '$conf', 'io.pulsar.gb10.rank': '0'},
    'api_port': 8000, 'mem_available_gib': 40.0, 'mem_status': 'ok', 'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': None, 'status': 'n/a', 'source': 'none'},
    'estimated_footprint_gib_per_rank': 100.0, 'reasons': [],
  }],
}))
"
}

svc_unknown() {
  python3 -c "
import json
print(json.dumps({
  'service_id': 'vllm-mystery',
  'profile': None,
  'conf': None,
  'served_name': None,
  'expected_nodes': None,
  'expected_ranks': [],
  'observed_ranks': ['?'],
  'container_name': 'vllm-mystery',
  'state': 'running',
  'ownership': 'unknown',
  'safe_to_stop': False,
  'complete': False,
  'observability': 'unknown',
  'api_port': 8000,
  'estimated_footprint_gib_per_rank': None,
  'reasons': ['unlabeled unknown'],
  'ranks': [{
    'rank': '?', 'node': 'head', 'expected_node': None,
    'container_name': 'vllm-mystery', 'container_id': 'c'*64,
    'container_id_short': 'cccccccccccc', 'image': 'other',
    'running': True, 'stale': False, 'status': 'running',
    'ownership': 'unknown', 'safe_to_stop': False,
    'labels': {}, 'api_port': 8000, 'mem_available_gib': 30.0,
    'mem_status': 'ok', 'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 20000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': None, 'reasons': ['unlabeled'],
  }],
}))
"
}

# Stateful shims: inventory/memory/down/up
cat >"$SHIM/inv-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [ -f "${STATE_DIR}/inv_fail" ]; then
  echo "fixture inventory failure" >&2
  exit 42
fi
if [ -f "${STATE_DIR}/inv_invalid" ]; then
  echo "not-json"
  exit 0
fi
# STATE_DIR and INV_FILE set by harness
if [ -f "${STATE_DIR}/inv_override" ]; then
  cat "${STATE_DIR}/inv_override"
elif [ -f "${STATE_DIR}/inv_after_stop" ] && [ -f "${STATE_DIR}/stopped" ]; then
  cat "${STATE_DIR}/inv_after_stop"
else
  cat "${STATE_DIR}/inv_current"
fi
SH
chmod +x "$SHIM/inv-cmd"

cat >"$SHIM/mem-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
model="${1:-}"
shift || true
printf '%s' "$model" >>"${STATE_DIR}/logs/memory.log"
printf ' %s' "$@" >>"${STATE_DIR}/logs/memory.log"
printf '\n' >>"${STATE_DIR}/logs/memory.log"
node=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --node)
      node="${2:-}"
      shift
      ;;
  esac
  shift
done
if [ -n "$node" ] && [ -f "${STATE_DIR}/mem_by_node/${node}.json" ]; then
  cat "${STATE_DIR}/mem_by_node/${node}.json"
  exit "$(cat "${STATE_DIR}/mem_by_node/${node}.rc" 2>/dev/null || echo 0)"
fi
# Per-model fixtures: mem_by_model/<id>.json + optional .rc
if [ -n "$model" ] && [ -f "${STATE_DIR}/mem_by_model/${model}.json" ]; then
  cat "${STATE_DIR}/mem_by_model/${model}.json"
  if [ -f "${STATE_DIR}/mem_by_model/${model}.rc" ]; then
    exit "$(cat "${STATE_DIR}/mem_by_model/${model}.rc")"
  fi
  exit 0
fi
# Optional sequence: mem_rc_seq file with one rc per call
if [ -f "${STATE_DIR}/mem_rc_seq" ]; then
  rc=$(head -1 "${STATE_DIR}/mem_rc_seq")
  tail -n +2 "${STATE_DIR}/mem_rc_seq" >"${STATE_DIR}/mem_rc_seq.tmp" || true
  mv "${STATE_DIR}/mem_rc_seq.tmp" "${STATE_DIR}/mem_rc_seq"
  case "$rc" in
    0) cat "${STATE_DIR}/mem_pass" 2>/dev/null || cat "${STATE_DIR}/mem_current" ;;
    2) cat "${STATE_DIR}/mem_warn" 2>/dev/null || cat "${STATE_DIR}/mem_current" ;;
    1) cat "${STATE_DIR}/mem_fail" 2>/dev/null || cat "${STATE_DIR}/mem_current" ;;
    *) cat "${STATE_DIR}/mem_pass" 2>/dev/null || cat "${STATE_DIR}/mem_current"; rc=0 ;;
  esac
  exit "$rc"
fi
if [ -f "${STATE_DIR}/stopped" ] && [ -f "${STATE_DIR}/mem_after_stop" ]; then
  cat "${STATE_DIR}/mem_after_stop"
  exit "${MEM_AFTER_STOP_RC:-0}"
fi
cat "${STATE_DIR}/mem_current"
exit "${MEM_CURRENT_RC:-0}"
SH
chmod +x "$SHIM/mem-cmd"

cat >"$SHIM/weights-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cat "${STATE_DIR}/weights.json"
exit "$(cat "${STATE_DIR}/weights.rc")"
SH
chmod +x "$SHIM/weights-cmd"

cat >"$SHIM/pull-weights-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "pull-weights $*" >>"${STATE_DIR}/logs/pull-weights.log"
rc=$(cat "${STATE_DIR}/pull-weights.rc")
if [ "$rc" -ne 0 ]; then
  printf '%s\n' \
    "MODEL FILE PREPARATION FAILED" \
    "Problem   simulated weight staging failure" \
    "Result    Model was not started."
fi
exit "$rc"
SH
chmod +x "$SHIM/pull-weights-cmd"

cat >"$SHIM/down-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "down $*" >>"${STATE_DIR}/logs/down.log"
echo "$*" >>"${STATE_DIR}/stopped"
# Flip inventory to after-stop if present
if [ -f "${STATE_DIR}/inv_after_stop" ]; then
  cp "${STATE_DIR}/inv_after_stop" "${STATE_DIR}/inv_current"
fi
exit 0
SH
chmod +x "$SHIM/down-cmd"

cat >"$SHIM/up-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "up $*" >>"${STATE_DIR}/logs/up.log"
# Fail only the first call (for launch-failure → restart-previous scenarios).
if [ -f "${STATE_DIR}/up_fail_once" ]; then
  rm -f "${STATE_DIR}/up_fail_once"
  echo "simulated up failure (once)" >&2
  exit 1
fi
if [ -f "${STATE_DIR}/up_fail" ]; then
  echo "simulated up failure" >&2
  exit 1
fi
exit 0
SH
chmod +x "$SHIM/up-cmd"

cat >"$SHIM/status-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "status $*" >>"${STATE_DIR}/logs/status.log"
echo "[status] fixture ok $*"
exit 0
SH
chmod +x "$SHIM/status-cmd"

cat >"$SHIM/doctor-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "doctor ok" >>"${STATE_DIR}/logs/doctor.log"
exit 0
SH
chmod +x "$SHIM/doctor-cmd"

export STATE_DIR="$STATE"

# seed_inv <out-path> <head-mem> <service-json-or-empty> [worker_status] [extra-python]
seed_inv() {
  local out="$1" head_mem="$2" svc_json="${3:-}" worker_status="${4:-unset}"
  local unmanaged="${5:-[]}"
  HEAD_MEM="$head_mem" OUT="$out" SVC="$svc_json" WSTATUS="$worker_status" UNMAN="$unmanaged" python3 - <<'PY'
import json, os
svc_raw = os.environ.get("SVC") or ""
services = [json.loads(svc_raw)] if svc_raw.strip() else []
unmanaged = json.loads(os.environ.get("UNMAN") or "[]")
wstatus = os.environ.get("WSTATUS") or "unset"
inv = {
  "schema_version": 1,
  "generated_at": "t",
  "worker": {
    "ip": "10.0.0.2" if wstatus != "unset" else "",
    "status": wstatus,
    "reason": "ssh failed" if wstatus == "unreachable" else "",
  },
  "nodes": {
    "head": {
      "mem_available_gib": float(os.environ["HEAD_MEM"]),
      "mem_status": "ok",
      "mem_source": "fixture",
    },
    "worker": {
      "mem_available_gib": 100.0 if wstatus == "ok" else None,
      "mem_status": "ok" if wstatus == "ok" else "n/a",
      "mem_source": wstatus,
    },
  },
  "services": services,
  "unmanaged_gpu_processes": unmanaged,
}
with open(os.environ["OUT"], "w", encoding="utf-8") as f:
    json.dump(inv, f, indent=2)
    f.write("\n")
PY
}

# Base env for all wizard runs
wizard_env() {
  export GUM=0
  export WIZARD_SKIP_DOCTOR=1
  export WIZARD_SKIP_WEIGHTS="${TEST_SKIP_WEIGHTS:-1}"
  export WIZARD_SKIP_IMAGE=1
  export WIZARD_SKIP_FABRIC_PROMPT="${TEST_SKIP_FABRIC_PROMPT:-1}"
  export WIZARD_SKIP_LIBRARY_CHECK=1
  if [ "${TEST_USE_LOADED_TOPOLOGY_CAPACITY:-0}" = 1 ]; then
    unset WIZARD_TOPOLOGY_NODES
  else
    export WIZARD_TOPOLOGY_NODES="${TEST_TOPOLOGY_NODES:-2}"
  fi
  if [ -n "${TEST_CLUSTER_TOPOLOGY_FILE:-}" ]; then
    export CLUSTER_TOPOLOGY_FILE="$TEST_CLUSTER_TOPOLOGY_FILE"
  else
    export CLUSTER_TOPOLOGY_FILE="$STATE/confirmed-topology.json"
  fi
  export WIZARD_LIST_MODELS_JSON="$STATE/models.json"
  export WIZARD_INVENTORY_CMD="$SHIM/inv-cmd"
  export WIZARD_CHECK_MEMORY_CMD="$SHIM/mem-cmd"
  export WIZARD_REPLACEMENT_TRANSACTION_FILE="$STATE/replacement-transaction.json"
  export WIZARD_CHECK_WEIGHTS_CMD="$SHIM/weights-cmd"
  export WIZARD_DOWN_CMD="$SHIM/down-cmd"
  export WIZARD_UP_CMD="$SHIM/up-cmd"
  export WIZARD_STATUS_CMD="$SHIM/status-cmd"
  export WIZARD_DOCTOR_CMD="$SHIM/doctor-cmd"
  export STATE_DIR="$STATE"
}

reset_logs() {
  rm -f "$STATE/logs/"* "$STATE/stopped" "$STATE/up_fail" "$STATE/up_fail_once" \
    "$STATE/inv_override" "$STATE/mem_rc_seq" "$STATE/inv_after_stop" \
    "$STATE/mem_after_stop" "$STATE/inv_fail" "$STATE/inv_invalid" \
    "$STATE/weights.json" "$STATE/weights.rc" "$STATE/pull-weights.rc" \
    "$STATE/no-topology.json" "$STATE/invalid-topology.json" \
    "$STATE/replacement-transaction.json" \
    2>/dev/null || true
  rm -rf "$STATE/mem_by_model"
  mkdir -p "$STATE/logs" "$STATE/mem_by_model"
  : >"$STATE/logs/down.log"
  : >"$STATE/logs/up.log"
  : >"$STATE/logs/status.log"
  : >"$STATE/logs/doctor.log"
  : >"$STATE/logs/pull-weights.log"
  : >"$STATE/logs/wizard.combined"
  : >"$STATE/logs/wizard.out"
  : >"$STATE/logs/wizard.err"
  unset MEM_CURRENT_RC MEM_AFTER_STOP_RC WIZARD_API_HEALTHY \
    TEST_TOPOLOGY_NODES TEST_SKIP_WEIGHTS TEST_SKIP_FABRIC_PROMPT \
    TEST_USE_LOADED_TOPOLOGY_CAPACITY TEST_CLUSTER_TOPOLOGY_FILE \
    CLUSTER_TOPOLOGY_FILE 2>/dev/null || true
  export MEM_CURRENT_RC=0
  export MEM_AFTER_STOP_RC=0
}

# Install per-model memory fixture used by mem-cmd (overrides global current/after).
set_mem_model() {
  local model="$1" kind="$2" # pass|warn|fail
  case "$kind" in
    pass) mem_pass "$STATE/mem_by_model/${model}.json" "$model"; echo 0 >"$STATE/mem_by_model/${model}.rc" ;;
    warn) mem_warn "$STATE/mem_by_model/${model}.json" "$model"; echo 2 >"$STATE/mem_by_model/${model}.rc" ;;
    fail) mem_fail "$STATE/mem_by_model/${model}.json" "$model"; echo 1 >"$STATE/mem_by_model/${model}.rc" ;;
    *) echo "set_mem_model: bad kind $kind" >&2; return 1 ;;
  esac
}

run_wizard() {
  # stdin choices as arguments newline-joined
  local input="$1"
  local out="$STATE/logs/wizard.out"
  local err="$STATE/logs/wizard.err"
  wizard_env
  set +e
  printf '%s' "$input" | "$REPO_DIR/wizard.sh" >"$out" 2>"$err"
  local rc=$?
  set -e
  LAST_RC=$rc
  cat "$out" >>"$STATE/logs/wizard.combined"
  cat "$err" >>"$STATE/logs/wizard.combined"
  return 0
}

pick_model_qwen() {
  # models list: qwen first → select 1
  echo "1"
}

# ---------------------------------------------------------------------------
# 1) Dispatcher routing (read-only)
# ---------------------------------------------------------------------------
echo "=== dispatcher routing ==="
chmod +x "$REPO_DIR/pulsar"
assert_true "pulsar help exits 0" "$REPO_DIR/pulsar" help
assert_true "pulsar --help exits 0" "$REPO_DIR/pulsar" --help
out=$("$REPO_DIR/pulsar" help 2>&1)
assert_true "help mentions wizard" bash -c "printf '%s' \"\$0\" | grep -q wizard" "$out"
assert_true "help mentions invalid ./ wizard.sh habit" bash -c "printf '%s' \"\$0\" | grep -q 'wizard.sh'" "$out"

# inventory --help via dispatcher (no docker mutation; may run live read-only)
# Prefer --help path that exits 0 without probing hardware heavily
out=$("$REPO_DIR/pulsar" inventory --help 2>&1) || true
assert_true "inventory help via dispatcher" bash -c "printf '%s' \"\$0\" | grep -qi inventory" "$out"

# start/stop without args should fail usage (no mutation)
set +e
"$REPO_DIR/pulsar" start >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "2" "pulsar start without model → usage exit 2"

set +e
"$REPO_DIR/pulsar" stop >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "2" "pulsar stop without target → usage exit 2"

# unknown command
set +e
"$REPO_DIR/pulsar" nosuch >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "2" "pulsar unknown command → exit 2"

# ---------------------------------------------------------------------------
# 2) Clean clone: declining discovery keeps standalone local serving available
# ---------------------------------------------------------------------------
echo "=== clean-clone standalone fallback ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
export TEST_SKIP_FABRIC_PROMPT=0
export TEST_USE_LOADED_TOPOLOGY_CAPACITY=1
export TEST_CLUSTER_TOPOLOGY_FILE="$STATE/no-topology.json"
# Serving requires a confirmed topology manifest (ADR 0006); declining the
# discovery prompt fails closed with the exact remediation command.
run_wizard $'n\n'
assert_eq "$LAST_RC" "1" "declined discovery fails closed without a manifest"
assert_file_contains "$STATE/logs/wizard.combined" \
  "serving requires a confirmed topology manifest" \
  "missing manifest names the serving prerequisite"
assert_file_contains "$STATE/logs/wizard.combined" \
  "detect-fabric.sh --write-topology" \
  "missing manifest names the exact remediation"
assert_file_not_contains "$STATE/logs/wizard.combined" \
  "invalid confirmed topology capacity '0'" \
  "missing topology no longer becomes invalid zero capacity"
assert_false "declined discovery writes no topology" \
  test -e "$STATE/no-topology.json"
assert_false "standalone decline starts nothing" \
  bash -c "test -s '$STATE/logs/up.log'"
assert_false "standalone decline stops nothing" \
  bash -c "test -s '$STATE/logs/down.log'"

# A present-but-invalid manifest must not receive the missing-file fallback.
reset_logs
printf '%s\n' '{"schema_version":1,"nodes":[]}' \
  >"$STATE/invalid-topology.json"
export TEST_SKIP_FABRIC_PROMPT=1
export TEST_USE_LOADED_TOPOLOGY_CAPACITY=1
export TEST_CLUSTER_TOPOLOGY_FILE="$STATE/invalid-topology.json"
run_wizard ""
assert_eq "$LAST_RC" "1" "invalid topology remains fail-closed"
assert_file_contains "$STATE/logs/wizard.combined" \
  "confirmed topology is invalid" \
  "invalid topology is not treated as standalone"
assert_false "invalid topology starts nothing" \
  bash -c "test -s '$STATE/logs/up.log'"
assert_false "invalid topology stops nothing" \
  bash -c "test -s '$STATE/logs/down.log'"

# ---------------------------------------------------------------------------
# 3) Missing-weight staging failure is explicit and non-mutating
# ---------------------------------------------------------------------------
echo "=== weight readiness failure feedback ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
cat >"$STATE/weights.json" <<'JSON'
{
  "model": "qwen3.8-27b-fp8",
  "state": "missing",
  "source": "local-files",
  "ok": false
}
JSON
echo 1 >"$STATE/weights.rc"
export TEST_SKIP_WEIGHTS=0
run_wizard $'3\n'
assert_eq "$LAST_RC" "1" "weight readiness failure exits nonzero"
assert_file_contains "$STATE/logs/wizard.combined" \
  "library runtime views are not ready" \
  "weight readiness failure names the library contract"
assert_false "weight failure: no launch" bash -c "test -s '$STATE/logs/up.log'"
assert_false "weight failure: no stop" bash -c "test -s '$STATE/logs/down.log'"

# ---------------------------------------------------------------------------
# 4) Same healthy keep — no mutation
# ---------------------------------------------------------------------------
echo "=== same healthy keep ==="
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_managed qwen3.8-27b-fp8 running True True)"
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
export WIZARD_API_HEALTHY=1
# model=3; the running service is a pre-library launch, so the migration
# menu interposes: keep=2
run_wizard $'3\n2\n'
assert_eq "$LAST_RC" "0" "same-healthy keep exit 0"
assert_false "keep: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "keep: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "keeping qwen3.8-27b-fp8 running" "keep message"
assert_file_contains "$STATE/logs/wizard.combined" "pre-library launch" \
  "legacy running service is named a pre-library launch"
assert_file_contains "$STATE/logs/wizard.out" "^MODEL SELECTED$" \
  "wizard model selection uses a semantic section"
assert_file_contains "$STATE/logs/wizard.out" "Spec review" \
  "wizard model selection shows display-only spec review"
assert_file_contains "$STATE/logs/wizard.err" "spec=-" \
  "wizard picker shows spec review from catalog JSON"
assert_file_not_contains "$STATE/logs/wizard.out" "sha256:" "default model selection hides image digest"
assert_file_contains "$STATE/logs/wizard.out" "^TARGET$" "wizard target uses a semantic section"
assert_file_contains "$STATE/logs/wizard.out" "^PREFLIGHT$" "wizard preflight uses a semantic section"
assert_file_contains "$STATE/logs/wizard.out" "^RELEVANT SERVICES" \
  "wizard diagnostics use stacked service sections"
assert_file_contains "$STATE/logs/wizard.err" "qwen3.8-27b-fp8[[:space:]]+1 node · suggested · first run" "wizard model choice is compact and human-readable"
assert_file_not_contains "$STATE/logs/wizard.err" "spec none" "wizard model choice omits profile-detail clutter"
assert_true "wizard model list is sorted by model name" python3 -c '
import sys
text = open(sys.argv[1], encoding="utf-8").read()
names = sys.argv[2:]
positions = [text.find(name) for name in names]
raise SystemExit(0 if min(positions) >= 0 and positions == sorted(positions) else 1)
' "$STATE/logs/wizard.err" qwen3-1.7b-2node qwen3.6-27b-fp8 qwen3.8-27b-fp8

# ---------------------------------------------------------------------------
# 3) Same restart — stop only after final confirm
# ---------------------------------------------------------------------------
echo "=== same restart after final confirm ==="
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_managed qwen3.8-27b-fp8 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export WIZARD_API_HEALTHY=1
# Abort path: model, restart, final n → no mutation
run_wizard $'3\n1\nn\n'
assert_eq "$LAST_RC" "0" "restart decline final confirm exit 0"
assert_false "restart aborted: no down before/without confirm" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/wizard.combined" "aborted; no containers changed" "restart aborted message"

reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_managed qwen3.8-27b-fp8 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export WIZARD_API_HEALTHY=1
run_wizard $'3\n1\ny\n'
assert_eq "$LAST_RC" "0" "restart confirmed exit 0"
assert_file_contains "$STATE/logs/down.log" "qwen3.8-27b-fp8" "restart: down called"
assert_file_contains "$STATE/logs/up.log" "qwen3.8-27b-fp8" "restart: up called"

# ---------------------------------------------------------------------------
# 4) Different managed blocker — replace
# ---------------------------------------------------------------------------
echo "=== different managed replace ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
mem_fail "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export MEM_CURRENT_RC=1
export MEM_AFTER_STOP_RC=0
export WIZARD_API_HEALTHY=0
# model qwen=3, stop listed=1, final y
run_wizard $'3\n1\ny\n'
assert_eq "$LAST_RC" "0" "replace managed exit 0"
assert_file_contains "$STATE/logs/down.log" "nemotron-3-nano-30b-nvfp4" "replace: stopped blocker"
assert_file_contains "$STATE/logs/up.log" "qwen3.8-27b-fp8" "replace: started target"
assert_file_contains "$STATE/logs/wizard.combined" \
  "without an exact restore contract" \
  "receipt/occupancy local-files replace names missing exact rollback"
assert_file_not_contains "$STATE/logs/down.log" "--pin-weights" \
  "receipt/occupancy local-files replace does not pin for rollback"
assert_false "unsealed replace leaves no rollback transaction" \
  bash -c "test -f '$STATE/replacement-transaction.json'"

# ---------------------------------------------------------------------------
# 5) Decline replace leaves service unchanged
# ---------------------------------------------------------------------------
echo "=== decline replace ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
mem_fail "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=1
export WIZARD_API_HEALTHY=0
# model=3, keep current=2
run_wizard $'3\n2\n'
assert_eq "$LAST_RC" "0" "decline replace exit 0"
assert_false "decline: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "decline: no up" bash -c "test -s '$STATE/logs/up.log'"

# ---------------------------------------------------------------------------
# 6) Hard fail + unknown consumer — no stop / no continue
# ---------------------------------------------------------------------------
echo "=== hard fail unknown consumer ==="
reset_logs
unmanaged='[{"node":"head","pid":9999,"process_name":"mystery-cuda","used_memory_mib":40000,"note":"read-only"}]'
seed_inv "$STATE/inv_current" 20 "$(svc_unknown)" unset "$unmanaged"
mem_fail "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=1
export WIZARD_API_HEALTHY=0
# model=3, exit=1
run_wizard $'3\n1\n'
assert_eq "$LAST_RC" "0" "unknown consumer exit path"
assert_false "unknown: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/wizard.combined" "will not stop" "unknown: will not stop messaging"
assert_file_not_contains "$STATE/logs/wizard.combined" "Continue with start anyway" "unknown+fail: no continue-anyway"

# ---------------------------------------------------------------------------
# 7) WARN explicit continuation
# ---------------------------------------------------------------------------
echo "=== memory WARN continue ==="
reset_logs
seed_inv "$STATE/inv_current" 92 ""
mem_warn "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=2
export WIZARD_API_HEALTHY=0
# model=3, continue warn y, final y
run_wizard $'3\ny\ny\n'
assert_eq "$LAST_RC" "0" "warn continue exit 0"
assert_file_contains "$STATE/logs/up.log" "accept-memory-warn" "warn: up got --accept-memory-warn"
assert_false "warn clean: no down" bash -c "test -s '$STATE/logs/down.log'"

# ---------------------------------------------------------------------------
# 8) Partial managed cleanup
# ---------------------------------------------------------------------------
echo "=== partial managed cleanup ==="
reset_logs
seed_inv "$STATE/inv_current" 40 "$(svc_partial_2node qwen3-1.7b-2node True)" ok
seed_inv "$STATE/inv_after_stop" 100 "" ok
mem_pass "$STATE/mem_current" qwen3-1.7b-2node
mem_pass "$STATE/mem_after_stop" qwen3-1.7b-2node
export MEM_CURRENT_RC=0
export MEM_AFTER_STOP_RC=0
export WIZARD_API_HEALTHY=0
# model=1, stop partial=1, spec default yes (confirm y), final y
run_wizard $'1\n1\ny\ny\n'
assert_eq "$LAST_RC" "1" "partial replacement fails closed without exact rollback state"
assert_false "partial: running incomplete service is not stopped" bash -c "test -s '$STATE/logs/down.log'"
assert_false "partial: target is not started" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "cannot be captured exactly|incomplete or unobservable" "partial: remediation explains unavailable automatic rollback"

# ---------------------------------------------------------------------------
# 9) Worker unreachable refusal
# ---------------------------------------------------------------------------
echo "=== worker unreachable refusal ==="
reset_logs
seed_inv "$STATE/inv_current" 40 "$(svc_partial_2node qwen3-1.7b-2node True)" unreachable
mem_pass "$STATE/mem_current" qwen3-1.7b-2node
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=0
# model=1 (deepseek 2-node), exit=1
run_wizard $'1\n1\n'
assert_eq "$LAST_RC" "0" "worker unreachable exit"
assert_false "unreachable: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/wizard.combined" "unreachable" "unreachable messaging"

# ---------------------------------------------------------------------------
# 9b) Idle extra rank does not block an exact configured subset
# ---------------------------------------------------------------------------
echo "=== idle extra rank does not block exact subset ==="
reset_logs
write_inv "$STATE/inv_current" '{
  "worker": {"ip": "10.0.0.2", "status": "unreachable", "reason": "rank 2 is unreachable"},
  "nodes": {
    "head": {"mem_available_gib": 100.0, "mem_status": "ok", "mem_source": "fixture"},
    "worker": {
      "mem_available_gib": 100.0,
      "mem_status": "ok",
      "mem_source": "fixture",
      "probe_status": "ok",
      "probe_reason": ""
    },
    "rank-2": {
      "mem_available_gib": null,
      "mem_status": "unreachable",
      "mem_source": "fixture",
      "probe_status": "unreachable",
      "probe_reason": "ssh failed"
    }
  }
}'
mem_pass "$STATE/mem_current" qwen3-1.7b-2node
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=0
export TEST_TOPOLOGY_NODES=3
# model=1 (validated 2-node profile), spec default yes, final yes
run_wizard $'1\ny\ny\n'
assert_eq "$LAST_RC" "0" "idle rank 2 exact-subset launch exit 0"
assert_file_contains "$STATE/logs/up.log" "qwen3-1.7b-2node" \
  "idle rank 2: exact 2-node profile launches"
assert_file_not_contains "$STATE/logs/wizard.combined" "Required cluster node unreachable" \
  "idle rank 2: no false required-node refusal"
assert_file_contains "$STATE/logs/wizard.out" "cluster node 2 · 100.00 GiB free.*ok" \
  "idle rank 2: target summary reports required cluster node"
assert_file_not_contains "$STATE/logs/wizard.out" "cluster node 3" \
  "idle rank 2: target summary excludes unused capacity"

# ---------------------------------------------------------------------------
# 10) Stale managed cleanup
# ---------------------------------------------------------------------------
echo "=== stale managed cleanup ==="
reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_managed qwen3.8-27b-fp8 stale True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=0
# model=3, remove stale=1, final y
run_wizard $'3\n1\ny\n'
assert_eq "$LAST_RC" "0" "stale cleanup exit 0"
assert_file_contains "$STATE/logs/down.log" "qwen3.8-27b-fp8" "stale: down"
assert_file_contains "$STATE/logs/wizard.combined" "does not hold model memory" "stale: memory note"

# ---------------------------------------------------------------------------
# 11) Stop then still-fail → Exit stopped (no up)
# ---------------------------------------------------------------------------
echo "=== stop then still memory fail (exit stopped) ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
seed_inv "$STATE/inv_after_stop" 30 ""
set_mem_model qwen3.8-27b-fp8 fail
set_mem_model nemotron-3-nano-30b-nvfp4 pass
export WIZARD_API_HEALTHY=0
# model=3, stop=1, final y, then Exit stopped=3
run_wizard $'3\n1\ny\n3\n'
assert_false "still-fail exit: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/down.log" "nemotron" "still-fail exit: did stop"
assert_file_contains "$STATE/logs/wizard.combined" "never offers continue-anyway|hard memory failure never offers" "still-fail exit: no continue"
assert_file_not_contains "$STATE/logs/down.log" "docker" "still-fail exit: no docker in down log"
assert_file_not_contains "$STATE/logs/wizard.combined" "docker rm|docker kill|kill -9" "still-fail exit: no foreign kill"

# ---------------------------------------------------------------------------
# 11b) Stop then still-fail → explicit Restart previous profile
# ---------------------------------------------------------------------------
echo "=== stop then still memory fail (no rollback for receipt/occupancy local-files) ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
set_mem_model qwen3.8-27b-fp8 fail
set_mem_model nemotron-3-nano-30b-nvfp4 pass
export WIZARD_API_HEALTHY=0
# model=3 (qwen), stop=1, final y; still-fail exits nonzero (no restore menu)
run_wizard $'3\n1\ny\n'
assert_eq "$LAST_RC" "1" "still-fail without exact rollback exits nonzero"
assert_file_contains "$STATE/logs/down.log" "nemotron-3-nano-30b-nvfp4" "still-fail: stopped blocker once"
down_lines=$(grep -c . "$STATE/logs/down.log" || true)
assert_eq "$down_lines" "1" "still-fail: single down (no extra stops)"
assert_false "still-fail: never up failed target" \
  bash -c "grep -q qwen3.8-27b-fp8 '$STATE/logs/up.log' 2>/dev/null"
assert_file_contains "$STATE/logs/wizard.combined" \
  "without an exact restore contract" \
  "receipt/occupancy local-files stop warns that exact rollback is unavailable"
assert_file_not_contains "$STATE/logs/wizard.combined" \
  "Restore previous exact service" \
  "no exact-restore offer exists for a receipt/occupancy local-files service"
assert_false "still-fail: no docker rm in wizard" grep -qE 'docker[[:space:]]+rm|docker[[:space:]]+kill' "$STATE/logs/wizard.combined"

# --------------------------------------------------------------------------
echo "=== launch failure after replace (exit stopped) ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
set_mem_model qwen3.8-27b-fp8 pass
set_mem_model nemotron-3-nano-30b-nvfp4 pass
# Force cold-start pressure narrative via after-stop still ok; use current fail until stop
# Actually for replace path: others_safe with qwen mem can be pass or fail.
# Use mem_current fail via global for qwen before stop is not needed if others_safe triggers.
mem_fail "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export MEM_CURRENT_RC=1
export MEM_AFTER_STOP_RC=0
export WIZARD_API_HEALTHY=0
# Clear per-model so global after-stop applies to qwen launch path
rm -rf "$STATE/mem_by_model"
mkdir -p "$STATE/mem_by_model"
touch "$STATE/up_fail"
# model=3, stop=1, final y, then Exit stopped=3
run_wizard $'3\n1\ny\n3\n'
assert_file_contains "$STATE/logs/down.log" "nemotron" "launch-fail exit: stopped previous"
assert_file_contains "$STATE/logs/up.log" "qwen3.8-27b-fp8" "launch-fail exit: attempted up target"
assert_file_contains "$STATE/logs/wizard.combined" "launch failed" "launch-fail exit message"
assert_file_not_contains "$STATE/logs/up.log" "nemotron" "launch-fail exit: no restart up without choice"

# ---------------------------------------------------------------------------
# 12b) Launch failure → explicit Restart previous profile
# ---------------------------------------------------------------------------
echo "=== launch failure after replace (no rollback for receipt/occupancy local-files) ==="
reset_logs
seed_inv "$STATE/inv_current" 30 "$(svc_managed nemotron-3-nano-30b-nvfp4 running True True)"
seed_inv "$STATE/inv_after_stop" 100 ""
mem_fail "$STATE/mem_current" qwen3.8-27b-fp8
mem_pass "$STATE/mem_after_stop" qwen3.8-27b-fp8
export MEM_CURRENT_RC=1
export MEM_AFTER_STOP_RC=0
export WIZARD_API_HEALTHY=0
rm -rf "$STATE/mem_by_model"
mkdir -p "$STATE/mem_by_model"
touch "$STATE/up_fail_once"
# model=3, stop=1, final y; launch fails and no restore menu exists
run_wizard $'3\n1\ny\n'
assert_eq "$LAST_RC" "1" "launch failure without exact rollback exits nonzero"
assert_file_contains "$STATE/logs/down.log" "nemotron-3-nano-30b-nvfp4" "launch-fail: stopped previous once"
down_lines=$(grep -c . "$STATE/logs/down.log" || true)
assert_eq "$down_lines" "1" "launch-fail: single down only"
assert_file_contains "$STATE/logs/up.log" "qwen3.8-27b-fp8" "launch-fail: attempted up target"
assert_file_not_contains "$STATE/logs/up.log" "nemotron-3-nano-30b-nvfp4" \
  "launch-fail: no automatic restart of the receipt/occupancy local-files service"
assert_file_contains "$STATE/logs/wizard.combined" "launch failed" "launch-fail: reported failure"
assert_file_contains "$STATE/logs/wizard.combined" \
  "without an exact restore contract" \
  "launch-fail: receipt/occupancy local-files stop warned about rollback"
assert_false "launch-fail: no docker mutation language" grep -qE 'docker[[:space:]]+rm|docker[[:space:]]+kill|kill -9' "$STATE/logs/wizard.combined"

# --------------------------------------------------------------------------
echo "=== choose another model loop ==="
reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_managed qwen3.8-27b-fp8 running True True)"
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=1
# model=3, choose another=4, model=2 (nano), keep current=2
run_wizard $'3\n4\n2\n2\n'
assert_file_contains "$STATE/logs/wizard.combined" "returning to selection" "choose-another loop"
assert_file_contains "$STATE/logs/wizard.combined" "doctor not re-run" "no re-doctor"
assert_false "choose-another path: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "doctor not invoked in loop" bash -c "test -s '$STATE/logs/doctor.log'"

# ---------------------------------------------------------------------------
# 14) Unknown port owner
# ---------------------------------------------------------------------------
echo "=== unknown port owner ==="
reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_unknown)"
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=0
# model=3, exit=1
run_wizard $'3\n1\n'
assert_false "unknown port: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/wizard.combined" "will not stop" "unknown port: will not stop"

# ---------------------------------------------------------------------------
# 15) No mutation before final confirm (clean start decline)
# ---------------------------------------------------------------------------
echo "=== no mutation before final confirm ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
export MEM_CURRENT_RC=0
export WIZARD_API_HEALTHY=0
# model=3, final n
run_wizard $'3\nn\n'
assert_eq "$LAST_RC" "0" "decline start exit 0"
assert_false "decline start: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "decline start: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "aborted; no containers changed" "decline start message"

# ---------------------------------------------------------------------------
# 16) Three-node placement: keep a two-node service, launch on idle node 3
# ---------------------------------------------------------------------------
echo "=== three-node idle placement ==="
reset_logs
python3 - "$STATE/inv_current" <<'PY'
import json
import sys

head_id = "11111111111111111111"
worker_id = "22222222222222222222"
idle_id = "33333333333333333333"
inv = {
    "schema_version": 1,
    "generated_at": "2026-08-06T00:00:00Z",
    "topology_id": "fixture-three-node",
    "worker": {"ip": "node-2", "status": "ok", "reason": None},
    "nodes": {
        "head": {
            "hostname": "dgx-spark-1",
            "node_id": head_id,
            "ssh_host": "local",
            "control_ip": "192.0.2.1",
            "topology_index": 0,
            "local": True,
            "remote": False,
            "confirmed": True,
            "mem_available_gib": 10.0,
            "mem_total_gib": 128.0,
            "mem_status": "ok",
            "probe_status": "ok",
        },
        "worker": {
            "hostname": "dgx-spark-2",
            "node_id": worker_id,
            "ssh_host": "node-2",
            "control_ip": "192.0.2.2",
            "topology_index": 1,
            "local": False,
            "remote": True,
            "confirmed": True,
            "mem_available_gib": 9.0,
            "mem_total_gib": 128.0,
            "mem_status": "ok",
            "probe_status": "ok",
        },
        "rank-2": {
            "hostname": "dgx-spark-3",
            "node_id": idle_id,
            "ssh_host": "node-3",
            "control_ip": "192.0.2.3",
            "topology_index": 2,
            "local": False,
            "remote": True,
            "confirmed": True,
            "mem_available_gib": 118.0,
            "mem_total_gib": 128.0,
            "mem_status": "ok",
            "probe_status": "ok",
        },
    },
    "services": [{
        "service_id": "qwen3-1.7b-2node",
        "profile": "qwen3-1.7b-2node",
        "conf": "qwen3-1.7b-2node",
        "served_name": "qwen3-1.7b-2node",
        "expected_nodes": 2,
        "expected_ranks": ["0", "1"],
        "observed_ranks": ["0", "1"],
        "container_name": "vllm-cluster-qwen3-1.7b-2node",
        "state": "running",
        "ownership": "managed",
        "safe_to_stop": True,
        "complete": True,
        "observability": "complete",
        "required_remote_probes": [{
            "rank": "1", "node": "worker", "status": "ok", "reason": None
        }],
        "api_port": 8000,
        "estimated_footprint_gib_per_rank": 106.0,
        "reasons": [],
        "ranks": [
            {
                "rank": "0", "node": "head", "expected_node": "head",
                "container_name": "vllm-cluster-qwen3-1.7b-2node",
                "container_id": "a" * 64, "container_id_short": "a" * 12,
                "running": True, "stale": False, "status": "running",
                "ownership": "managed", "safe_to_stop": True,
                "labels": {}, "api_port": 8000,
                "gpu_memory": {"measured_mib": 105000, "status": "ok"},
                "reasons": [],
            },
            {
                "rank": "1", "node": "worker", "expected_node": "worker",
                "container_name": "vllm-cluster-qwen3-1.7b-2node",
                "container_id": "b" * 64, "container_id_short": "b" * 12,
                "running": True, "stale": False, "status": "running",
                "ownership": "managed", "safe_to_stop": True,
                "labels": {}, "api_port": 8000,
                "gpu_memory": {"measured_mib": 105000, "status": "ok"},
                "reasons": [],
            },
        ],
    }],
    "unmanaged_gpu_processes": [],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(inv, handle, indent=2)
    handle.write("\n")
PY
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
mem_fail "$STATE/mem_by_node/11111111111111111111.json" qwen3.8-27b-fp8
printf '1\n' >"$STATE/mem_by_node/11111111111111111111.rc"
mem_pass "$STATE/mem_by_node/22222222222222222222.json" qwen3.8-27b-fp8
printf '0\n' >"$STATE/mem_by_node/22222222222222222222.rc"
mem_pass "$STATE/mem_by_node/33333333333333333333.json" qwen3.8-27b-fp8
printf '0\n' >"$STATE/mem_by_node/33333333333333333333.rc"
export TEST_TOPOLOGY_NODES=3
export WIZARD_API_HEALTHY=0
export COLUMNS=48
# model qwen=3, recommended idle placement=1, final confirm=y
run_wizard $'3\n1\ny\n'
unset COLUMNS
assert_eq "$LAST_RC" "0" "three-node placement launch exits 0"
assert_file_contains "$STATE/logs/wizard.out" "^ELIGIBLE PHYSICAL NODES$" \
  "placement lists eligible confirmed Docker nodes"
assert_true "hard memory failure is excluded from eligible placement" python3 -c '
import sys
text = open(sys.argv[1], encoding="utf-8").read()
start = text.index("ELIGIBLE PHYSICAL NODES")
end = text.index("MODEL SELECTED", start)
raise SystemExit(0 if "dgx-spark-1" not in text[start:end] else 1)
' "$STATE/logs/wizard.out"
assert_file_contains "$STATE/logs/wizard.out" "dgx-spark-2" \
  "placement lists node 2 hostname and memory"
assert_file_contains "$STATE/logs/wizard.out" "dgx-spark-3" \
  "placement lists node 3 hostname and memory"
assert_file_contains "$STATE/logs/wizard.out" "Pulsar:" \
  "placement shows existing Pulsar occupancy"
assert_file_contains "$STATE/logs/wizard.out" "idle" \
  "placement shows idle capacity"
assert_file_contains "$STATE/logs/wizard.out" "333333333333.*recommended" \
  "idle node 3 is recommended"
assert_file_contains "$STATE/logs/memory.log" \
  "qwen3.8-27b-fp8 --node 33333333333333333333 --cold-start --json" \
  "placement eligibility explicitly uses the cold-start memory policy"
assert_file_contains "$STATE/logs/up.log" \
  "qwen3.8-27b-fp8 --node 33333333333333333333 --yes" \
  "single-node launch targets node 3 by stable ID"
assert_file_contains "$STATE/logs/status.log" \
  "qwen3.8-27b-fp8 --node 33333333333333333333" \
  "status follows the remote placement"
assert_false "non-overlapping DeepSeek service is not stopped" \
  bash -c "grep -q deepseek '$STATE/logs/down.log'"
assert_file_not_contains "$STATE/logs/wizard.combined" \
  "Managed service blocks target" \
  "non-overlapping service is not treated as a blocker"
assert_file_contains "$STATE/logs/wizard.combined" \
  "re-running inventory and memory immediately before launch" \
  "inventory and memory are rerun immediately before launch"
assert_true "placement section honors a 48-column terminal" python3 -c '
import sys
text = open(sys.argv[1], encoding="utf-8").read()
start = text.index("ELIGIBLE PHYSICAL NODES")
end = text.index("MODEL SELECTED", start)
raise SystemExit(0 if all(len(line) <= 48 for line in text[start:end].splitlines()) else 1)
' "$STATE/logs/wizard.out"

# 16) Observability failures fail closed
# ---------------------------------------------------------------------------
echo "=== inventory command failure fails closed ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
touch "$STATE/inv_fail"
run_wizard $'3\n'
assert_eq "$LAST_RC" "1" "inventory command failure exits nonzero"
assert_false "inventory failure: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "inventory failure: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "inventory collection failed.*no lifecycle action" \
  "inventory failure gives safe operator feedback"

echo "=== malformed inventory fails closed ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
mem_pass "$STATE/mem_current" qwen3.8-27b-fp8
touch "$STATE/inv_invalid"
run_wizard $'3\n'
assert_eq "$LAST_RC" "1" "malformed inventory exits nonzero"
assert_false "malformed inventory: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "inventory returned invalid data.*no lifecycle action" \
  "malformed inventory gives safe operator feedback"

echo "=== unexpected memory exit fails closed ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
set_mem_model qwen3.8-27b-fp8 pass
echo 42 >"$STATE/mem_by_model/qwen3.8-27b-fp8.rc"
run_wizard $'3\n'
assert_eq "$LAST_RC" "1" "unexpected memory exit is not treated as pass"
assert_false "unexpected memory exit: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "unexpected memory exit: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "memory preflight failed internally \(exit=42\)" \
  "unexpected memory exit is reported clearly"

echo "=== inconsistent memory result fails closed ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
set_mem_model qwen3.8-27b-fp8 pass
echo 2 >"$STATE/mem_by_model/qwen3.8-27b-fp8.rc"
run_wizard $'3\n'
assert_eq "$LAST_RC" "1" "memory JSON/exit disagreement exits nonzero"
assert_false "inconsistent memory result: no up" bash -c "test -s '$STATE/logs/up.log'"
assert_file_contains "$STATE/logs/wizard.combined" "invalid or inconsistent data \(exit=2\)" \
  "memory JSON/exit disagreement is reported clearly"

# ---------------------------------------------------------------------------
# Static: wizard does not call down before confirm patterns (source review)
# ---------------------------------------------------------------------------
echo "=== static safety checks ==="
# down only via execute_pending_stops / cmd_down after final_confirm_start
assert_true "wizard has execute_pending_stops" grep -q "execute_pending_stops" "$REPO_DIR/wizard.sh"
assert_true "wizard defers stop until after final confirm helper" grep -q "final_confirm_start" "$REPO_DIR/wizard.sh"
assert_true "receipt/occupancy local-files stop warns that exact rollback is unavailable" \
  grep -q "without an exact restore contract" "$REPO_DIR/wizard.sh"
# ensure cmd_down is only in execute_pending_stops
downs=$(grep -n "cmd_down" "$REPO_DIR/wizard.sh" | grep -v '^#' || true)
# Only definition and execute_pending_stops should call it
count=$(printf '%s\n' "$downs" | grep -c "cmd_down" || true)
assert_true "cmd_down references limited" bash -c "[ \"$count\" -le 4 ]"

# No kill/rm docker in wizard
assert_false "wizard has no docker rm" grep -qE "docker[[:space:]]+rm" "$REPO_DIR/wizard.sh"
assert_false "wizard has no kill -9" grep -qE "kill[[:space:]]+-9" "$REPO_DIR/wizard.sh"

# ---------------------------------------------------------------------------
echo "=============================="
echo "wizard-switch selftest: pass=$pass fail=$fail"
if [ "$fail" -ne 0 ]; then
  exit 1
fi
exit 0
