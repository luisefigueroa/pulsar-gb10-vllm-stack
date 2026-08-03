#!/usr/bin/env bash
# Deterministic operator-home + quick-status suite (no Docker/SSH/GPU/network).
#   scripts/selftest-home.sh
#
# Uses GUM=0 plain menus, inventory/API fixtures, and command shims.
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

STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
SHIM="$STATE/bin"
mkdir -p "$SHIM" "$STATE/logs" "$STATE/inv" "$STATE/api"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
svc_managed() {
  local conf="$1" state="$2" complete="$3" safe="$4" port="${5:-8000}"
  local running="True" stale="False"
  [ "$state" = "stale" ] && running="False" && stale="True"
  [ "$state" = "stopped" ] && running="False"
  python3 -c "
import json
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
  'estimated_footprint_gib_per_rank': 12.0,
  'reasons': [],
  'ranks': [{
    'rank': 'single', 'node': 'head', 'expected_node': 'head',
    'container_name': 'vllm-$conf', 'container_id': 'a'*64,
    'container_id_short': 'aaaaaaaaaaaa', 'image': 'vllm/vllm-openai:v0.26.0',
    'running': $running, 'stale': $stale, 'status': '$state',
    'ownership': 'managed', 'safe_to_stop': $safe,
    'labels': {'io.pulsar.gb10.managed': 'true', 'io.pulsar.gb10.conf': '$conf', 'io.pulsar.gb10.rank': 'single'},
    'api_port': $port, 'mem_available_gib': 50.0, 'mem_status': 'ok', 'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 8000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': 12.0, 'reasons': [],
  }],
}))
"
}

svc_unknown() {
  python3 -c "
import json
print(json.dumps({
  'service_id': 'vllm-mystery',
  'profile': None, 'conf': None, 'served_name': None,
  'expected_nodes': None, 'expected_ranks': [], 'observed_ranks': ['?'],
  'container_name': 'vllm-mystery', 'state': 'running', 'ownership': 'unknown',
  'safe_to_stop': False, 'complete': False, 'observability': 'unknown',
  'api_port': 8000, 'estimated_footprint_gib_per_rank': None,
  'reasons': ['unlabeled'],
  'ranks': [{
    'rank': '?', 'node': 'head', 'expected_node': None,
    'container_name': 'vllm-mystery', 'container_id': 'c'*64,
    'container_id_short': 'cccccccccccc', 'image': 'other',
    'running': True, 'stale': False, 'status': 'running',
    'ownership': 'unknown', 'safe_to_stop': False, 'labels': {},
    'api_port': 8000, 'mem_available_gib': 30.0, 'mem_status': 'ok',
    'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 20000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': None, 'reasons': ['unlabeled'],
  }],
}))
"
}

svc_legacy() {
  python3 -c "
import json
print(json.dumps({
  'service_id': 'vllm-legacy',
  'profile': 'qwen3-1.7b', 'conf': 'qwen3-1.7b', 'served_name': 'qwen3-1.7b',
  'expected_nodes': 1, 'expected_ranks': ['single'], 'observed_ranks': ['single'],
  'container_name': 'vllm-legacy', 'state': 'running', 'ownership': 'legacy',
  'safe_to_stop': False, 'complete': False, 'observability': 'partial',
  'api_port': 8000, 'estimated_footprint_gib_per_rank': 12.0, 'reasons': ['legacy'],
  'ranks': [{
    'rank': 'single', 'node': 'head', 'expected_node': 'head',
    'container_name': 'vllm-legacy', 'container_id': 'd'*64,
    'container_id_short': 'dddddddddddd', 'image': 'img',
    'running': True, 'stale': False, 'status': 'running',
    'ownership': 'legacy', 'safe_to_stop': False, 'labels': {},
    'api_port': 8000, 'mem_available_gib': 40.0, 'mem_status': 'ok',
    'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 9000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': 12.0, 'reasons': ['legacy'],
  }],
}))
"
}

svc_mismatch() {
  python3 -c "
import json
print(json.dumps({
  'service_id': 'vllm-mm',
  'profile': 'qwen3-1.7b', 'conf': 'qwen3-1.7b', 'served_name': 'qwen3-1.7b',
  'expected_nodes': 1, 'expected_ranks': ['single'], 'observed_ranks': ['single'],
  'container_name': 'vllm-mm', 'state': 'running', 'ownership': 'mismatch',
  'safe_to_stop': False, 'complete': False, 'observability': 'partial',
  'api_port': 8000, 'estimated_footprint_gib_per_rank': 12.0, 'reasons': ['mismatch'],
  'ranks': [{
    'rank': 'single', 'node': 'head', 'expected_node': 'head',
    'container_name': 'vllm-mm', 'container_id': 'e'*64,
    'container_id_short': 'eeeeeeeeeeee', 'image': 'img',
    'running': True, 'stale': False, 'status': 'running',
    'ownership': 'mismatch', 'safe_to_stop': False,
    'labels': {'io.pulsar.gb10.managed': 'true', 'io.pulsar.gb10.conf': 'other'},
    'api_port': 8000, 'mem_available_gib': 40.0, 'mem_status': 'ok',
    'mem_source': 'fixture',
    'gpu_memory': {'measured_mib': 9000, 'status': 'ok', 'source': 'fixture'},
    'estimated_footprint_gib_per_rank': 12.0, 'reasons': ['mismatch'],
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
  'profile': '$conf', 'conf': '$conf', 'served_name': '$conf',
  'expected_nodes': 2, 'expected_ranks': ['0', '1'], 'observed_ranks': ['0'],
  'container_name': 'vllm-cluster-$conf', 'state': 'partial',
  'ownership': 'managed', 'safe_to_stop': safe, 'complete': False,
  'observability': 'partial', 'api_port': 8000,
  'estimated_footprint_gib_per_rank': 100.0, 'reasons': ['missing rank 1'],
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

svc_complete_2node() {
  local conf="$1" safe="${2:-True}"
  python3 -c "
import json
safe = '$safe' == 'True'
print(json.dumps({
  'service_id': '$conf',
  'profile': '$conf', 'conf': '$conf', 'served_name': '$conf',
  'expected_nodes': 2, 'expected_ranks': ['0', '1'], 'observed_ranks': ['0', '1'],
  'container_name': 'vllm-cluster-$conf', 'state': 'running',
  'ownership': 'managed', 'safe_to_stop': safe, 'complete': True,
  'observability': 'complete', 'api_port': 8000,
  'estimated_footprint_gib_per_rank': 100.0, 'reasons': [],
  'ranks': [
    {
      'rank': '0', 'node': 'head', 'expected_node': 'head',
      'container_name': 'vllm-cluster-$conf', 'container_id': 'b'*64,
      'container_id_short': 'bbbbbbbbbbbb', 'image': 'img',
      'running': True, 'stale': False, 'status': 'running',
      'ownership': 'managed', 'safe_to_stop': safe,
      'labels': {'io.pulsar.gb10.managed': 'true', 'io.pulsar.gb10.conf': '$conf', 'io.pulsar.gb10.rank': '0'},
      'api_port': 8000, 'mem_available_gib': 40.0, 'mem_status': 'ok', 'mem_source': 'fixture',
      'gpu_memory': {'measured_mib': 50000, 'status': 'ok', 'source': 'fixture'},
      'estimated_footprint_gib_per_rank': 100.0, 'reasons': [],
    },
    {
      'rank': '1', 'node': 'worker', 'expected_node': 'worker',
      'container_name': 'vllm-cluster-$conf', 'container_id': 'f'*64,
      'container_id_short': 'ffffffffffff', 'image': 'img',
      'running': True, 'stale': False, 'status': 'running',
      'ownership': 'managed', 'safe_to_stop': safe,
      'labels': {'io.pulsar.gb10.managed': 'true', 'io.pulsar.gb10.conf': '$conf', 'io.pulsar.gb10.rank': '1'},
      'api_port': 8000, 'mem_available_gib': 40.0, 'mem_status': 'ok', 'mem_source': 'fixture',
      'gpu_memory': {'measured_mib': 51000, 'status': 'ok', 'source': 'fixture'},
      'estimated_footprint_gib_per_rank': 100.0, 'reasons': [],
    },
  ],
}))
"
}

seed_inv() {
  local out="$1" head_mem="$2"
  shift 2
  # Remaining optional: services as JSON strings, then worker_status, unmanaged JSON
  local services_py="[]"
  local worker_status="unset"
  local unmanaged="[]"
  local args=("$@")
  # Parse: any number of service JSON blobs ending before status keyword or unmanaged array
  HEAD_MEM="$head_mem" OUT="$out" python3 - "$out" "$head_mem" "$@" <<'PY'
import json, os, sys
out = sys.argv[1]
head_mem = float(sys.argv[2])
args = sys.argv[3:]
services = []
worker_status = "unset"
unmanaged = []
i = 0
while i < len(args):
    a = args[i]
    if a in ("ok", "unset", "unreachable"):
        worker_status = a
        i += 1
        if i < len(args):
            try:
                unmanaged = json.loads(args[i])
            except Exception:
                pass
        break
    try:
        services.append(json.loads(a))
    except Exception:
        pass
    i += 1

inv = {
  "schema_version": 1,
  "generated_at": "t",
  "worker": {
    "ip": "10.0.0.2" if worker_status != "unset" else "",
    "status": worker_status,
    "reason": "ssh failed" if worker_status == "unreachable" else "",
  },
  "nodes": {
    "head": {
      "mem_available_gib": head_mem,
      "mem_total_gib": 128.0,
      "mem_status": "ok",
      "mem_source": "fixture",
    },
    "worker": {
      "mem_available_gib": 90.0 if worker_status == "ok" else None,
      "mem_total_gib": 128.0 if worker_status == "ok" else None,
      "mem_status": "ok" if worker_status == "ok" else ("unreachable" if worker_status == "unreachable" else "n/a"),
      "mem_source": worker_status,
    },
  },
  "services": services,
  "unmanaged_gpu_processes": unmanaged,
}
with open(out, "w", encoding="utf-8") as f:
    json.dump(inv, f, indent=2)
    f.write("\n")
PY
}

# Shims
cat >"$SHIM/down-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "down $*" >>"${STATE_DIR}/logs/down.log"
exit 0
SH
chmod +x "$SHIM/down-cmd"

cat >"$SHIM/doctor-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "doctor" >>"${STATE_DIR}/logs/doctor.log"
echo "[doctor] fixture ok"
exit 0
SH
chmod +x "$SHIM/doctor-cmd"

cat >"$SHIM/status-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "status $*" >>"${STATE_DIR}/logs/status.log"
echo "POST /v1/completions" >>"${STATE_DIR}/logs/status.log"
echo "[status] fixture smoke ok"
exit 0
SH
chmod +x "$SHIM/status-cmd"

cat >"$SHIM/wizard-cmd" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "wizard" >>"${STATE_DIR}/logs/wizard.log"
echo "[wizard] fixture entered"
exit 0
SH
chmod +x "$SHIM/wizard-cmd"

cat >"$SHIM/inv-track" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "inventory" >>"${STATE_DIR}/logs/inventory.log"
if [ -f "${STATE_DIR}/inv_current" ]; then
  cat "${STATE_DIR}/inv_current"
else
  echo '{}'
fi
SH
chmod +x "$SHIM/inv-track"

cat >"$SHIM/api-ok" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "api_probe" >>"${STATE_DIR}/logs/api.log"
cat <<'JSON'
{"data":[{"id":"qwen3-1.7b"}]}
JSON
SH
chmod +x "$SHIM/api-ok"

cat >"$SHIM/api-fail" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
echo "api_probe" >>"${STATE_DIR}/logs/api.log"
exit 1
SH
chmod +x "$SHIM/api-fail"

cat >"$SHIM/api-forbid-completion" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
# Any URL/path containing completions must not be used by quick-status
echo "api_cmd $*" >>"${STATE_DIR}/logs/api.log"
if printf '%s' "$*" | grep -qi completion; then
  echo "FORBIDDEN completion call" >>"${STATE_DIR}/logs/api.log"
  exit 99
fi
# quick-status API_CMD receives port only
cat <<'JSON'
{"data":[{"id":"nemotron-3-nano"}]}
JSON
SH
chmod +x "$SHIM/api-forbid-completion"

export STATE_DIR="$STATE"

reset_logs() {
  rm -f "$STATE/logs/"* 2>/dev/null || true
  mkdir -p "$STATE/logs"
  : >"$STATE/logs/down.log"
  : >"$STATE/logs/doctor.log"
  : >"$STATE/logs/status.log"
  : >"$STATE/logs/wizard.log"
  : >"$STATE/logs/inventory.log"
  : >"$STATE/logs/api.log"
  : >"$STATE/logs/home.out"
  : >"$STATE/logs/home.err"
  : >"$STATE/logs/home.combined"
}

home_env() {
  export GUM=0
  export HOME_DOWN_CMD="$SHIM/down-cmd"
  export HOME_DOCTOR_CMD="$SHIM/doctor-cmd"
  export HOME_STATUS_CMD="$SHIM/status-cmd"
  export HOME_WIZARD_CMD="$SHIM/wizard-cmd"
  export HOME_INVENTORY_JSON="$STATE/inv_current"
  export QUICK_STATUS_INVENTORY_JSON="$STATE/inv_current"
  export QUICK_STATUS_API_CMD="$SHIM/api-ok"
  export STATE_DIR="$STATE"
  # Ensure doctor/inventory not auto-run: leave real paths unused via hooks
  unset HOME_QUICK_STATUS_CMD HOME_INVENTORY_CMD HOME_INVENTORY_JSON_CMD 2>/dev/null || true
}

run_home() {
  local input="$1"
  home_env
  set +e
  printf '%s' "$input" | "$REPO_DIR/scripts/home.sh" >"$STATE/logs/home.out" 2>"$STATE/logs/home.err"
  LAST_RC=$?
  set -e
  cat "$STATE/logs/home.out" >>"$STATE/logs/home.combined"
  cat "$STATE/logs/home.err" >>"$STATE/logs/home.combined"
}

# ---------------------------------------------------------------------------
# 1) Dispatcher: no-arg home vs wizard; direct command compatibility
# ---------------------------------------------------------------------------
echo "=== dispatcher routing ==="
chmod +x "$REPO_DIR/pulsar" "$REPO_DIR/scripts/home.sh" "$REPO_DIR/scripts/quick-status.sh"

# Help
out=$("$REPO_DIR/pulsar" help 2>&1)
assert_true "help mentions operator home" bash -c "printf '%s' \"\$0\" | grep -qiE 'home|workflow menu|operator'" "$out"
assert_true "help mentions wizard shortcut" bash -c "printf '%s' \"\$0\" | grep -q wizard" "$out"

# No-arg must invoke home.sh, not wizard immediately — prove via PATH-less
# static + shim: HOME_WIZARD not set on dispatcher; grep pulsar routes.
assert_true "pulsar no-arg routes to home.sh" grep -q 'scripts/home.sh' "$REPO_DIR/pulsar"
assert_true "pulsar wizard still routes to wizard.sh" bash -c \
  "grep -A2 'wizard)' '$REPO_DIR/pulsar' | grep -q wizard.sh"
# Empty command must not share the wizard case arm.
assert_false "empty cmd is not bundled with wizard" grep -qE '""\|wizard\)|"\|wizard\)' "$REPO_DIR/pulsar"

# Argument forwarding: start/stop usage
set +e
"$REPO_DIR/pulsar" start >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "2" "pulsar start without model → exit 2"

set +e
"$REPO_DIR/pulsar" stop >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "2" "pulsar stop without target → exit 2"

set +e
"$REPO_DIR/pulsar" inventory --help >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "pulsar inventory --help → 0"

# ---------------------------------------------------------------------------
# 2) Home starts with no doctor/inventory before first choice
# ---------------------------------------------------------------------------
echo "=== home no preflight before first choice ==="
reset_logs
seed_inv "$STATE/inv_current" 100 ""
# Exit immediately (menu item 6)
run_home $'6\n'
assert_eq "$LAST_RC" "0" "home exit 0"
assert_false "no doctor before/on exit" bash -c "test -s '$STATE/logs/doctor.log'"
assert_false "no inventory before exit-only" bash -c "test -s '$STATE/logs/inventory.log'"
assert_false "no down on exit" bash -c "test -s '$STATE/logs/down.log'"
assert_false "no wizard on exit" bash -c "test -s '$STATE/logs/wizard.log'"
assert_file_contains "$STATE/logs/home.combined" "operator home|Pulsar operator|plain menus|goodbye" "home greeting or exit"

# ---------------------------------------------------------------------------
# 3) Every home workflow + return/exit/cancel (GUM=0)
# ---------------------------------------------------------------------------
echo "=== home workflows plain mode ==="

# Status → Back → Exit
reset_logs
seed_inv "$STATE/inv_current" 80 "$(svc_managed qwen3-1.7b running True True)"
run_home $'1\n4\n6\n'
assert_eq "$LAST_RC" "0" "status then exit"
assert_file_contains "$STATE/logs/home.combined" "quick-status|managed|read-only" "status showed overview"
assert_false "status path: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_false "status path: no doctor" bash -c "test -s '$STATE/logs/doctor.log'"
assert_false "status path: no full smoke status" bash -c "test -s '$STATE/logs/status.log'"

# Serve/switch → wizard shim → Exit
reset_logs
seed_inv "$STATE/inv_current" 100 ""
run_home $'2\n6\n'
assert_file_contains "$STATE/logs/wizard.log" "wizard" "serve entered wizard shim"
assert_false "serve path: no down" bash -c "test -s '$STATE/logs/down.log'"

# Diagnostics doctor → Exit
reset_logs
seed_inv "$STATE/inv_current" 100 ""
# 5 Diagnostics, 1 Run doctor, 6 Exit (after return)
run_home $'5\n1\n6\n'
assert_file_contains "$STATE/logs/doctor.log" "doctor" "diagnostics ran doctor"
assert_false "diagnostics: no down" bash -c "test -s '$STATE/logs/down.log'"

# Cancel/EOF on home menu
reset_logs
seed_inv "$STATE/inv_current" 100 ""
run_home ''
assert_eq "$LAST_RC" "0" "EOF cancel exits cleanly"
assert_false "EOF: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/home.combined" "cancelled|goodbye|exiting" "EOF messaging"

# ---------------------------------------------------------------------------
# 4) Quick status fields + no completion
# ---------------------------------------------------------------------------
echo "=== quick-status fields ==="
reset_logs
seed_inv "$STATE/inv_current" 64.0 "$(svc_managed qwen3-1.7b running True True)" ok \
  '[{"node":"head","pid":111,"process_name":"foreign","used_memory_mib":4096,"note":"x"}]'
# Add stale via multi-service inventory
python3 - <<PY
import json
with open("$STATE/inv_current") as f:
    inv = json.load(f)
stale = json.loads('''$(svc_managed other-model stale True True)'''.replace("other-model", "stale-qwen"))
# fix conf name in embedded
stale = json.loads(r'''$(svc_managed stale-qwen stale True True)''')
inv["services"].append(stale)
with open("$STATE/inv_current", "w") as f:
    json.dump(inv, f, indent=2)
    f.write("\n")
PY

export QUICK_STATUS_INVENTORY_JSON="$STATE/inv_current"
export QUICK_STATUS_API_CMD="$SHIM/api-forbid-completion"
out=$("$REPO_DIR/scripts/quick-status.sh" 2>&1)
assert_true "quick-status mentions conf" bash -c "printf '%s' \"\$0\" | grep -q qwen3-1.7b" "$out"
assert_true "quick-status memory head" bash -c "printf '%s' \"\$0\" | grep -qi memory" "$out"
assert_true "quick-status API advertised" bash -c "printf '%s' \"\$0\" | grep -qiE 'advertised|nemotron'" "$out"
assert_true "quick-status unmanaged" bash -c "printf '%s' \"\$0\" | grep -qi unmanaged" "$out"
assert_true "quick-status stale nonblocking" bash -c "printf '%s' \"\$0\" | grep -qiE 'stale|nonblocking'" "$out"
assert_true "quick-status not inference smoke" bash -c "printf '%s' \"\$0\" | grep -qiE 'not an inference|no inference'" "$out"
assert_false "quick-status never logs completion" bash -c "grep -qi completion '$STATE/logs/api.log' && grep -q FORBIDDEN '$STATE/logs/api.log'"

narrow=$(COLUMNS=48 "$REPO_DIR/scripts/quick-status.sh" 2>&1)
assert_true "quick-status honors narrow terminal width" bash -c \
  "printf '%s\n' \"\$0\" | python3 -c 'import sys; lines=sys.stdin.read().splitlines(); assert max(map(len, lines)) <= 48'" "$narrow"
assert_true "quick-status uses labeled human fields" bash -c \
  "printf '%s' \"\$0\" | grep -q '^QUICK STATUS' && ! printf '%s' \"\$0\" | grep -q '\[quick-status\]'" "$narrow"

j=$("$REPO_DIR/scripts/quick-status.sh" --json)
assert_true "quick-status json kind" bash -c "printf '%s' \"\$0\" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"kind\"]==\"quick_status\"; assert d[\"inference_smoke\"] is False'" "$j"
assert_true "quick-status json mem total" bash -c "printf '%s' \"\$0\" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"memory\"][\"head\"][\"mem_total_gib\"]==128.0'" "$j"
assert_true "quick-status json unmanaged agg" bash -c "printf '%s' \"\$0\" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"unmanaged_gpu\"][\"count\"]==1; assert d[\"unmanaged_gpu\"][\"measured_mib_aggregate\"]==4096'" "$j"
assert_true "quick-status json stale count" bash -c "printf '%s' \"\$0\" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d[\"stale_managed\"][\"count\"]>=1'" "$j"

# API unavailable
export QUICK_STATUS_API_CMD="$SHIM/api-fail"
out=$("$REPO_DIR/scripts/quick-status.sh" 2>&1)
assert_true "API unavailable messaging" bash -c "printf '%s' \"\$0\" | grep -qi unavailable" "$out"

# Worker unreachable
seed_inv "$STATE/inv_current" 50 "$(svc_managed qwen3-1.7b running True True)" unreachable
export QUICK_STATUS_INVENTORY_JSON="$STATE/inv_current"
export QUICK_STATUS_API_CMD="$SHIM/api-ok"
out=$("$REPO_DIR/scripts/quick-status.sh" 2>&1)
assert_true "worker unreachable in overview" bash -c "printf '%s' \"\$0\" | grep -qi unreachable" "$out"

# ---------------------------------------------------------------------------
# 5) Stop list eligibility
# ---------------------------------------------------------------------------
echo "=== stop eligibility ==="
reset_logs
# Mix: managed safe + unknown + legacy + mismatch + incomplete partial
python3 - <<PY
import json
services = [
  json.loads(r'''$(svc_managed good-model running True True)'''),
  json.loads(r'''$(svc_unknown)'''),
  json.loads(r'''$(svc_legacy)'''),
  json.loads(r'''$(svc_mismatch)'''),
  json.loads(r'''$(svc_partial_2node deepseek-v4-flash True)'''),
  json.loads(r'''$(svc_managed unsafe-model running True False)'''),
]
inv = {
  "schema_version": 1, "generated_at": "t",
  "worker": {"ip": "10.0.0.2", "status": "ok", "reason": ""},
  "nodes": {
    "head": {"mem_available_gib": 50, "mem_total_gib": 128, "mem_status": "ok", "mem_source": "f"},
    "worker": {"mem_available_gib": 50, "mem_total_gib": 128, "mem_status": "ok", "mem_source": "f"},
  },
  "services": services,
  "unmanaged_gpu_processes": [],
}
with open("$STATE/inv_current", "w") as f:
    json.dump(inv, f, indent=2)
PY

# Stop: 3, select first (only good-model), decline confirm n, exit 6
run_home $'3\n1\nn\n6\n'
assert_file_contains "$STATE/logs/home.combined" "good-model" "stop lists managed safe"
assert_file_contains "$STATE/logs/home.combined" "good-model · RUNNING · ranks 1/1" \
  "stop choice is compact and human-readable"
assert_file_not_contains "$STATE/logs/home.combined" "vllm-mystery|mystery" "stop excludes unknown"
# Legacy/mismatch conf names might appear in inventory messages? should not in choices - check down not called
assert_false "decline: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/home.combined" "declined|no containers changed" "stop decline message"

# Confirm stop routes only through down shim
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_managed good-model running True True)"
run_home $'3\n1\ny\n6\n'
assert_file_contains "$STATE/logs/down.log" "good-model" "confirmed stop → down shim"
assert_file_not_contains "$STATE/logs/down.log" "docker" "down log has no docker"
assert_eq "$(grep -c . "$STATE/logs/down.log" || true)" "1" "single down call"

# Empty stop list
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_unknown)"
run_home $'3\n6\n'
assert_file_contains "$STATE/logs/home.combined" "no eligible" "empty stop list message"
assert_false "empty stop: no down" bash -c "test -s '$STATE/logs/down.log'"

# Incomplete partial excluded
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_partial_2node deepseek-v4-flash True)" ok
run_home $'3\n6\n'
assert_file_contains "$STATE/logs/home.combined" "no eligible" "partial incomplete excluded"
assert_false "partial: no down" bash -c "test -s '$STATE/logs/down.log'"

# Worker unreachable 2-node complete excluded
reset_logs
seed_inv "$STATE/inv_current" 50 "$(svc_complete_2node deepseek-v4-flash True)" unreachable
run_home $'3\n6\n'
assert_file_contains "$STATE/logs/home.combined" "no eligible" "worker-unreach 2-node excluded"
assert_false "unreach: no down" bash -c "test -s '$STATE/logs/down.log'"

# ---------------------------------------------------------------------------
# 6) Stale maintenance
# ---------------------------------------------------------------------------
echo "=== stale maintenance ==="
reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_managed stale-qwen stale True True)"
# 4 Maintenance, 1 Clean stale, 1 select conf, n decline, 6 exit
run_home $'4\n1\n1\nn\n6\n'
assert_false "stale decline: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/home.combined" "declined|no containers changed" "stale decline"
assert_file_contains "$STATE/logs/home.combined" "stale-qwen · STALE · safe_to_stop" \
  "stale choice is compact and explicit"

reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_managed stale-qwen stale True True)"
run_home $'4\n1\n1\ny\n6\n'
assert_file_contains "$STATE/logs/down.log" "stale-qwen" "stale confirm → down"
assert_file_contains "$STATE/logs/home.combined" "no model memory|nonblocking" "stale messaging"

# Unsafe stale excluded
reset_logs
seed_inv "$STATE/inv_current" 100 "$(svc_managed bad-stale stale True False)"
run_home $'4\n1\n6\n'
assert_file_contains "$STATE/logs/home.combined" "no eligible stale" "unsafe stale excluded"
assert_false "unsafe stale: no down" bash -c "test -s '$STATE/logs/down.log'"

# ---------------------------------------------------------------------------
# 7) Inventory/status observability failures never offer mutation
# ---------------------------------------------------------------------------
echo "=== inventory observability failures ==="
reset_logs
printf '%s\n' 'not-json' >"$STATE/inv_current"
run_home $'3\n6\n'
assert_eq "$LAST_RC" "0" "home recovers to menu after malformed inventory"
assert_false "malformed home inventory: no down" bash -c "test -s '$STATE/logs/down.log'"
assert_file_contains "$STATE/logs/home.combined" "inventory returned invalid data.*no action was taken" \
  "home explains malformed inventory safely"

reset_logs
export QUICK_STATUS_INVENTORY_CMD=/bin/false
unset QUICK_STATUS_INVENTORY_JSON
set +e
"$REPO_DIR/scripts/quick-status.sh" >"$STATE/logs/quick-fail.out" 2>&1
quick_rc=$?
set -e
assert_eq "$quick_rc" "1" "quick-status inventory failure exits nonzero"
assert_file_contains "$STATE/logs/quick-fail.out" "inventory collection failed.*status is unavailable" \
  "quick-status explains collection failure"

printf '%s\n' 'not-json' >"$STATE/inv_current"
export QUICK_STATUS_INVENTORY_JSON="$STATE/inv_current"
unset QUICK_STATUS_INVENTORY_CMD
set +e
"$REPO_DIR/scripts/quick-status.sh" >"$STATE/logs/quick-invalid.out" 2>&1
quick_rc=$?
set -e
assert_eq "$quick_rc" "1" "quick-status malformed inventory exits nonzero"
assert_file_contains "$STATE/logs/quick-invalid.out" "inventory returned invalid data.*status is unavailable" \
  "quick-status explains malformed inventory"

# ---------------------------------------------------------------------------
# 8) Gum style configuration — runtime fake-Gum argv capture
# ---------------------------------------------------------------------------
echo "=== gum style / color policy (runtime argv) ==="

# Fake gum records argv (one arg per line, records separated by ---) and
# implements minimal choose/confirm/version for non-interactive tests.
# UI markers go to stderr (real Gum draws the TUI there); selection stays stdout.
FAKE_GUM_CHOOSE_UI_MARKER="FAKE_GUM_CHOOSE_UI_VISIBLE"
FAKE_GUM_CONFIRM_UI_MARKER="FAKE_GUM_CONFIRM_UI_VISIBLE"
cat >"$SHIM/fake-gum" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
log="${GUM_ARGV_LOG:?GUM_ARGV_LOG required}"
{
  printf '%s\n' "---"
  printf '%s\n' "$@"
} >>"$log"
cmd="${1:-}"
case "$cmd" in
  choose)
    # Simulate TUI on stderr (must remain visible through choose() capture).
    printf '%s\n' "FAKE_GUM_CHOOSE_UI_VISIBLE" >&2
    # Selection on stdout only.
    if IFS= read -r line; then
      printf '%s\n' "$line"
    else
      printf '%s\n' "option-a"
    fi
    exit 0
    ;;
  confirm)
    printf '%s\n' "FAKE_GUM_CONFIRM_UI_VISIBLE" >&2
    exit 0
    ;;
  --version|version)
    echo "gum version v0.0.0-fake"
    exit 0
    ;;
  spin)
    # drop title flags until -- then run rest
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --) shift; break ;;
        *) shift ;;
      esac
    done
    if [ $# -gt 0 ]; then
      "$@"
    fi
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
SH
chmod +x "$SHIM/fake-gum"

argv_has() {
  local log="$1" needle="$2"
  # -- ends options so needles like --cursor.foreground=12 are not flags
  grep -qxF -- "$needle" "$log"
}

# Color-enabled Gum path: fake Gum must be invoked with full blue overrides.
# Captures helper stderr so TUI markers must remain visible (not silenced).
run_ui_color_gum() {
  local argv_log="$1"
  local err_log="${2:-}"
  : >"$argv_log"
  [ -n "$err_log" ] && : >"$err_log"
  local err_target="/dev/stderr"
  [ -n "$err_log" ] && err_target="$err_log"
  (
    set -euo pipefail
    export REPO_DIR="$REPO_DIR"
    export GUM_ARGV_LOG="$argv_log"
    export GUM_BIN="$SHIM/fake-gum"
    export GUM=1
    export PULSAR_FORCE_GUM=1
    unset NO_COLOR PULSAR_COLOR
    export TERM=xterm-256color
    export PULSAR_ACCENT=12
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/lib.sh"
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/ui.sh"
    [ "$have_gum" = 1 ]
    [ "$GUM_CMD" = "$SHIM/fake-gum" ]
    # stdout is selection only; stderr must carry UI markers to outer capture
    sel=$(choose "Test header" "option-a" "option-b")
    [ "$sel" = "option-a" ]
    confirm "Proceed?" || true
  ) 2>"$err_target"
}

# Forced no-color: plain menus only — fake Gum must never be invoked.
run_ui_plain_no_gum() {
  local argv_log="$1"
  local env_snippet="$2"
  : >"$argv_log"
  (
    set -euo pipefail
    export REPO_DIR="$REPO_DIR"
    export GUM_ARGV_LOG="$argv_log"
    export GUM_BIN="$SHIM/fake-gum"
    export GUM=1
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/lib.sh"
    eval "$env_snippet"
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/ui.sh"
    [ "$have_gum" = 0 ]
    [ -z "${GUM_CMD:-}" ]
    # plain choose + confirm via stdin (uncolored)
    out=$(printf '1\n' | choose "Plain header" "only-option")
    [ "$out" = "only-option" ]
    printf 'n\n' | confirm "Proceed?" && exit 1
    true
  )
}

# --- Color enabled: blue overrides on choose + confirm; TUI stderr visible ---
ARGV_COLOR="$STATE/logs/gum-argv-color.txt"
ERR_COLOR="$STATE/logs/gum-ui-stderr.txt"
run_ui_color_gum "$ARGV_COLOR" "$ERR_COLOR"
assert_eq "$?" "0" "color-enabled Gum path runs with fake-gum"
assert_true "choose passes cursor.foreground blue" argv_has "$ARGV_COLOR" "--cursor.foreground=12"
assert_true "choose passes header.foreground blue" argv_has "$ARGV_COLOR" "--header.foreground=12"
assert_true "choose passes selected.foreground blue (not pink)" argv_has "$ARGV_COLOR" "--selected.foreground=12"
assert_true "confirm passes prompt.foreground blue" argv_has "$ARGV_COLOR" "--prompt.foreground=12"
assert_true "confirm passes selected.foreground 15" argv_has "$ARGV_COLOR" "--selected.foreground=15"
assert_true "confirm passes selected.background 4" argv_has "$ARGV_COLOR" "--selected.background=4"
assert_false "choose never sets pink selected.foreground" argv_has "$ARGV_COLOR" "--selected.foreground=212"
assert_false "choose never sets pink cursor.foreground" argv_has "$ARGV_COLOR" "--cursor.foreground=212"
assert_true "color path invoked fake-gum choose" grep -qxF -- "choose" "$ARGV_COLOR"
assert_true "color path invoked fake-gum confirm" grep -qxF -- "confirm" "$ARGV_COLOR"
# Regression: Gum TUI is drawn on stderr; choose/confirm must not silence it.
assert_file_contains "$ERR_COLOR" "$FAKE_GUM_CHOOSE_UI_MARKER" \
  "choose TUI marker visible on outer stderr (not silenced)"
assert_file_contains "$ERR_COLOR" "$FAKE_GUM_CONFIRM_UI_MARKER" \
  "confirm TUI marker visible on outer stderr (not silenced)"
assert_true "ui choose does not silence gum stderr" bash -c \
  "! grep -n 'GUM_CMD.*choose\|gum.*choose' '$REPO_DIR/scripts/ui.sh' | grep -q '2>/dev/null'"
assert_true "ui confirm does not silence gum stderr" bash -c \
  "! awk '/confirm\\(\\)/,/^}/' '$REPO_DIR/scripts/ui.sh' | grep -q '2>/dev/null'"

# --- NO_COLOR / PULSAR_COLOR=never / TERM=dumb / GUM=0 → plain; Gum never called ---
ARGV_NC="$STATE/logs/gum-argv-nocolor.txt"
run_ui_plain_no_gum "$ARGV_NC" 'export NO_COLOR=1; unset PULSAR_COLOR; export TERM=xterm-256color'
assert_eq "$?" "0" "NO_COLOR forces plain menus"
assert_false "NO_COLOR does not invoke Gum" bash -c "test -s '$ARGV_NC'"

ARGV_PC="$STATE/logs/gum-argv-pulsar-never.txt"
run_ui_plain_no_gum "$ARGV_PC" 'unset NO_COLOR; export PULSAR_COLOR=never; export TERM=xterm-256color'
assert_eq "$?" "0" "PULSAR_COLOR=never forces plain menus"
assert_false "PULSAR_COLOR=never does not invoke Gum" bash -c "test -s '$ARGV_PC'"

ARGV_DUMB="$STATE/logs/gum-argv-term-dumb.txt"
run_ui_plain_no_gum "$ARGV_DUMB" 'unset NO_COLOR PULSAR_COLOR; export TERM=dumb'
assert_eq "$?" "0" "TERM=dumb forces plain menus"
assert_false "TERM=dumb does not invoke Gum" bash -c "test -s '$ARGV_DUMB'"

ARGV_PLAIN="$STATE/logs/gum-argv-gum0.txt"
run_ui_plain_no_gum "$ARGV_PLAIN" 'export GUM=0; unset NO_COLOR PULSAR_COLOR; export TERM=xterm-256color'
assert_eq "$?" "0" "GUM=0 forces plain menus"
assert_false "GUM=0 does not invoke Gum" bash -c "test -s '$ARGV_PLAIN'"

# Resolve policy is explicit in source
assert_true "ui forces plain when color disabled" \
  grep -q 'if ! pulsar_color_enabled' "$REPO_DIR/scripts/ui.sh"
assert_true "ui sources PULSAR_ACCENT default 12" grep -q 'PULSAR_ACCENT:-12' "$REPO_DIR/scripts/ui.sh"
assert_true "ui choose always sets selected.foreground to accent" \
  grep -q 'selected.foreground="$PULSAR_ACCENT"' "$REPO_DIR/scripts/ui.sh"
assert_true "ui honors NO_COLOR" grep -q 'NO_COLOR' "$REPO_DIR/scripts/ui.sh"
assert_true "ui honors PULSAR_COLOR" grep -q 'PULSAR_COLOR' "$REPO_DIR/scripts/ui.sh"

# ---------------------------------------------------------------------------
# 9) Static safety
# ---------------------------------------------------------------------------
echo "=== static safety ==="
assert_false "home has no docker rm" grep -qE 'docker[[:space:]]+rm' "$REPO_DIR/scripts/home.sh"
assert_false "home has no kill -9" grep -qE 'kill[[:space:]]+-9' "$REPO_DIR/scripts/home.sh"
assert_true "home stops only via cmd_down/down.sh" grep -q 'cmd_down' "$REPO_DIR/scripts/home.sh"
assert_true "quick-status never curls completions" bash -c \
  "! grep -E 'curl.*completions|/v1/completions[\"'\'']' '$REPO_DIR/scripts/quick-status.sh' | grep -v never | grep -q ."
assert_true "quick-status only probes /v1/models" grep -q '/v1/models' "$REPO_DIR/scripts/quick-status.sh"

# ---------------------------------------------------------------------------
echo "=============================="
echo "home selftest: pass=$pass fail=$fail"
if [ "$fail" -ne 0 ]; then
  exit 1
fi
exit 0
