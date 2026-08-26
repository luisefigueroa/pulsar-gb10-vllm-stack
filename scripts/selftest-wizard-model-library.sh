#!/usr/bin/env bash
# Explicit distributed-catalog serving-wizard scenarios.
# Uses only sanitized fixtures and command shims; no Docker, SSH, GPU, model
# bytes, or live catalog state is touched.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIZARD_FIXTURE_TOOL="$REPO_DIR/scripts/testlib/wizard_replacement_fixture.py"
export WIZARD_FIXTURE_TOOL
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-wizard-library-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
mkdir -p "$STATE/bin" "$STATE/reports" "$STATE/logs"

python3 "$REPO_DIR/scripts/testlib/model_storage_fixture.py" "$STATE/reports"

python3 - "$STATE/inventory.json" "$STATE/memory.json" "$REPO_DIR" <<'PY'
import json
import pathlib
import sys

inventory_path, memory_path, repo_dir = sys.argv[1:]
inventory = {
    "schema_version": 1,
    "generated_at": "2026-08-13T00:00:00Z",
    "worker": {"status": "ok", "reason": None},
    "nodes": {
        "head": {
            "hostname": "fixture-zero",
            "node_id": "node-zero-identity",
            "ssh_host": "local",
            "control_ip": "192.0.2.10",
            "topology_index": 0,
            "local": True,
            "remote": False,
            "confirmed": True,
            "mem_available_gib": 120.0,
            "mem_status": "ok",
            "mem_source": "fixture",
            "probe_status": "ok",
        },
        "worker": {
            "hostname": "fixture-one",
            "node_id": "node-one-identity",
            "ssh_host": "fixture-one.local",
            "control_ip": "192.0.2.11",
            "topology_index": 1,
            "local": False,
            "remote": True,
            "confirmed": True,
            "mem_available_gib": 120.0,
            "mem_status": "ok",
            "mem_source": "fixture",
            "probe_status": "ok",
        },
    },
    "services": [],
    "unmanaged_gpu_processes": [],
}
memory = {
    "model": "qwen3.8-27b-fp8-2node",
    "result": "pass",
    "mode": "cold-start",
    "already_loaded": False,
    "already_how": "",
    "footprint_gib": 110.0,
    "need_start_gib": 114.0,
    "weights_gib_total": 167.0,
    "weights_gib_per_rank": 83.5,
    "kv_gib": 10.0,
    "overhead_gib": 16.5,
    "buffer_gib": 8.0,
    "spike_gib": 4.0,
    "hard_floor_gib": 4.0,
    "head_available_gib": 120.0,
    "worker_available_gib": 120.0,
    "max_model_len": None,
    "kv_fixed": False,
    "note": "",
    "reason": "",
}
running = json.loads(json.dumps(inventory))
running["services"] = [{
    "service_id": "qwen3.8-27b-fp8-2node",
    "profile": "qwen3.8-27b-fp8-2node",
    "conf": "qwen3.8-27b-fp8-2node",
    "served_name": "qwen3.8-27b-fp8-2node",
    "expected_nodes": 2,
    "expected_ranks": ["0", "1"],
    "observed_ranks": ["0", "1"],
    "container_name": "vllm-cluster-qwen3.8-27b-fp8-2node",
    "state": "running",
    "ownership": "managed",
    "safe_to_stop": True,
    "complete": True,
    "observability": "complete",
    "weight_source": "local-files",
    "required_remote_probes": [{
        "rank": "1", "node": "worker", "status": "ok", "reason": None,
    }],
    "api_port": 8000,
    "estimated_footprint_gib_per_rank": 110.0,
    "reasons": [],
    "ranks": [
        {
            "rank": str(rank),
            "node": "head" if rank == 0 else "worker",
            "expected_node": "head" if rank == 0 else "worker",
            "container_name": "vllm-cluster-qwen3.8-27b-fp8-2node",
            "container_id": str(rank + 1) * 64,
            "container_id_short": str(rank + 1) * 12,
            "running": True,
            "stale": False,
            "status": "running",
            "ownership": "managed",
            "safe_to_stop": True,
            "labels": {},
            "api_port": 8000,
            "gpu_memory": {"measured_mib": 100000, "status": "ok"},
            "reasons": [],
        }
        for rank in range(2)
    ],
}]
for path, value in (
    (inventory_path, inventory),
    (memory_path, memory),
    (inventory_path + ".running", running),
):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")

sys.path.insert(0, str(pathlib.Path(repo_dir) / "scripts"))
import topology_manifest  # noqa: E402

nodes = [
    {
        "rank": 0,
        "node_id": "node-zero-identity",
        "hostname": "fixture-zero",
        "ssh_host": "local",
        "control": {"interface": "lan0", "ip": "192.0.2.10"},
        "gpu": "NVIDIA GB10",
        "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.10/24"]}],
    },
    {
        "rank": 1,
        "node_id": "node-one-identity",
        "hostname": "fixture-one",
        "ssh_host": "fixture-one.local",
        "control": {"interface": "lan0", "ip": "192.0.2.11"},
        "gpu": "NVIDIA GB10",
        "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.11/24"]}],
    },
]
topology = {
    "schema_version": 1,
    "generated_at": "2026-08-13T00:00:00+00:00",
    "nodes": nodes,
    "links": [{
        "ranks": [0, 1],
        "rails": [{
            "network": "198.51.100.0/24",
            "a": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.10"},
            "b": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.11"},
        }],
    }],
    "validation": {
        "class": "roce-full-mesh",
        "full_mesh": True,
        "connectivity_verified": True,
        "min_rails_per_pair": 1,
    },
}
topology["topology_id"] = topology_manifest.topology_digest(topology)
with open(str(pathlib.Path(inventory_path).with_name("topology.json")), "w", encoding="utf-8") as handle:
    json.dump(topology, handle, indent=2)
    handle.write("\n")

one_profiles = {"models": [{
    "id": "qwen3.8-27b-fp8",
    "status": "tested",
    "nodes": 1,
    "source": "hf",
    "purpose": "serving",
    "served_name": "qwen3.8-27b-fp8",
    "spec": "none",
    "spec_default_enabled": False,
    "reviewed_identity": False,
    "reviewed_model_id": None,
    "reviewed_revision": None,
    "reviewed_manifest": None,
}]}
one_health = {
    "schema_version": 1,
    "kind": "pulsar-model-library-health",
    "state": "healthy",
    "catalog": {
        "status": "cached",
        "topology_compatible": True,
        "refreshed_at": "2026-08-13T00:00:00.000Z",
    },
    "models": [{
        "model_id": "Qwen/Qwen3-1.7B",
        "revision": "7" * 40,
        "profiles": ["qwen3.8-27b-fp8"],
        "expected_manifest": None,
        "validation": "unvalidated",
        "home_ranks": [1],
        "primary": {"mode": "automatic-single-home", "status": "match", "rank": 1},
        "duplicate_home": "none",
    }],
    "hot_instances": [{
        "rank": 1,
        "profile": "qwen3.8-27b-fp8",
        "model_id": "Qwen/Qwen3-1.7B",
        "revision": "7" * 40,
        "metadata_schema": 3,
        "metadata_status": "current",
        "runtime_source": "durable-home",
        "retention": "ephemeral",
        "identity_status": "receipt-occupancy",
        "witness_status": "match",
        "active_reference": False,
        "repairable": False,
        "repair_id": None,
    }],
    "issues": [],
}
for name, value in (("one-profiles.json", one_profiles), ("one-health.json", one_health)):
    target = pathlib.Path(inventory_path).parent / "reports" / name
    with open(str(target), "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
PY

printf '%s\n' '{"home":{"rank":1,"node_id":"node-one-identity"}}' \
  >"$STATE/reports/resolve.json"
python3 - "$STATE/reports" <<'PY'
import json
import pathlib
import sys

reports = pathlib.Path(sys.argv[1])
unsealed_one = {"models": [{
    "id": "qwen3.8-27b-fp8", "status": "tested", "nodes": 1,
    "source": "hf", "purpose": "serving", "served_name": "qwen3.8-27b-fp8",
    "spec": "none", "spec_default_enabled": False,
    "reviewed_identity": False, "reviewed_model_id": None,
    "reviewed_revision": None, "reviewed_manifest": None,
}]}
unsealed_two = {"models": [{
    "id": "qwen3.8-27b-fp8-2node", "status": "tested", "nodes": 2,
    "source": "hf", "purpose": "serving", "served_name": "qwen3.8-27b-fp8-2node",
    "spec": "none", "spec_default_enabled": False,
    "reviewed_identity": False, "reviewed_model_id": None,
    "reviewed_revision": None, "reviewed_manifest": None,
}]}
for name, value in (
    ("unsealed-one-profiles.json", unsealed_one),
    ("unsealed-two-profiles.json", unsealed_two),
):
    with open(reports / name, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
        handle.write("\n")
PY

CONTRACT_ID=$(bash -c '. "$1/scripts/lib.sh"; load_conf qwen3.8-27b-fp8-2node; loaded_launch_contract_id' _ "$REPO_DIR")
python3 "$WIZARD_FIXTURE_TOOL" seed-running \
  --inventory "$STATE/inventory.json.running" \
  --empty-inventory "$STATE/inventory.json" \
  --topology "$STATE/topology.json" \
  --health "$STATE/reports/healthy.json" \
  --active-health "$STATE/reports/healthy-active.json" \
  --contract-id "$CONTRACT_ID"
cp "$STATE/inventory.json.running" "$STATE/inventory.json.running-template"

cat >"$STATE/bin/health" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$HEALTH_LOG"
cat "$HEALTH_REPORT"
exit "${HEALTH_RC:-0}"
SH

cat >"$STATE/bin/prepare" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$PREPARE_LOG"
if [ "${PREPARE_RC:-0}" -ne 0 ]; then
  echo "fixture preparation failed" >&2
  exit "$PREPARE_RC"
fi
case "${1:-}" in
  resolve)
    cat "${RESOLVE_RESULT:?}"
    ;;
  prepare)
    cp "$PREPARE_RESULT" "$HEALTH_REPORT"
    ;;
  pin|unpin)
    retention=$([ "$1" = pin ] && echo pinned || echo ephemeral)
    python3 "$WIZARD_FIXTURE_TOOL" mutate-health \
      --path "$HEALTH_REPORT" --retention "$retention"
    ;;
  purge-hot)
    python3 "$WIZARD_FIXTURE_TOOL" mutate-health \
      --path "$HEALTH_REPORT" --purge
    ;;
esac
SH

cat >"$STATE/bin/weights" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$WEIGHTS_LOG"
rc=0
if [ -n "${WEIGHTS_RC_SEQ:-}" ] && [ -s "$WEIGHTS_RC_SEQ" ]; then
  rc=$(head -1 "$WEIGHTS_RC_SEQ")
  tail -n +2 "$WEIGHTS_RC_SEQ" >"$WEIGHTS_RC_SEQ.tmp" || true
  mv "$WEIGHTS_RC_SEQ.tmp" "$WEIGHTS_RC_SEQ"
fi
if [ "$rc" -eq 0 ]; then
  printf '{"schema_version":1,"state":"ready","ok":true}\n'
else
  printf '{"schema_version":1,"state":"missing","ok":false}\n'
fi
exit "$rc"
SH

cat >"$STATE/bin/up" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$UP_LOG"
if [ -f "${UP_FAIL_ONCE:-}" ]; then
  rm -f "$UP_FAIL_ONCE"
  exit 1
fi
python3 "$WIZARD_FIXTURE_TOOL" mutate-health \
  --path "$HEALTH_REPORT" --active true
SH

cat >"$STATE/bin/status" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$STATUS_LOG"
SH

cat >"$STATE/bin/down" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$DOWN_LOG"
cp "$EMPTY_INVENTORY" "$CURRENT_INVENTORY"
python3 "$WIZARD_FIXTURE_TOOL" mutate-health \
  --path "$HEALTH_REPORT" --active false
SH

chmod +x "$STATE/bin/health" "$STATE/bin/prepare" "$STATE/bin/weights" \
  "$STATE/bin/up" "$STATE/bin/status" "$STATE/bin/down"

assert_contains() {
  local file="$1" pattern="$2" message="$3"
  if grep -qE -- "$pattern" "$file"; then
    echo "OK   $message"
  else
    echo "FAIL $message (missing /$pattern/)" >&2
    sed -n '1,220p' "$file" >&2
    exit 1
  fi
}

assert_empty() {
  local file="$1" message="$2"
  if [ ! -s "$file" ]; then
    echo "OK   $message"
  else
    echo "FAIL $message (unexpected content)" >&2
    cat "$file" >&2
    exit 1
  fi
}

run_wizard() {
  local initial_report="$1" health_rc="$2" prepare_rc="$3" input="$4"
  local inventory_report="${5:-$STATE/inventory.json}"
  local profiles_report="${6:-$STATE/reports/profiles.json}"
  local fail_up_once="${7:-0}"
  cp "$STATE/reports/$initial_report" "$STATE/current-health.json"
  [ "${KEEP_REPLACEMENT_TRANSACTION:-0}" = 1 ] \
    || rm -f "$STATE/replacement-transaction.json"
  rm -f "$STATE/up-fail-once"
  [ "$fail_up_once" != 1 ] || touch "$STATE/up-fail-once"
  : >"$STATE/logs/health.log"
  : >"$STATE/logs/prepare.log"
  : >"$STATE/logs/weights.log"
  : >"$STATE/logs/up.log"
  : >"$STATE/logs/status.log"
  : >"$STATE/logs/down.log"
  set +e
  printf '%s' "$input" | env \
    GUM=0 \
    WIZARD_API_HEALTHY=1 \
    WIZARD_SKIP_DOCTOR=1 \
    WIZARD_SKIP_FABRIC_PROMPT=1 \
    WIZARD_SKIP_IMAGE=1 \
    WIZARD_TOPOLOGY_NODES=2 \
    CLUSTER_TOPOLOGY_FILE="$STATE/topology.json" \
    WIZARD_LIST_MODELS_JSON="$profiles_report" \
    WIZARD_INVENTORY_JSON="$inventory_report" \
    WIZARD_MEMORY_JSON="$STATE/memory.json" \
    WIZARD_MEMORY_RC=0 \
    WIZARD_MODEL_LIBRARY_HEALTH_CMD="$STATE/bin/health" \
    WIZARD_MODEL_LIBRARY_PREPARE_CMD="$STATE/bin/prepare" \
    WIZARD_REPLACEMENT_TRANSACTION_FILE="$STATE/replacement-transaction.json" \
    WIZARD_CHECK_WEIGHTS_CMD="$STATE/bin/weights" \
    WIZARD_UP_CMD="$STATE/bin/up" \
    WIZARD_DOWN_CMD="$STATE/bin/down" \
    WIZARD_STATUS_CMD="$STATE/bin/status" \
    HEALTH_REPORT="$STATE/current-health.json" \
    HEALTH_RC="$health_rc" \
    HEALTH_LOG="$STATE/logs/health.log" \
    PREPARE_RESULT="$STATE/reports/healthy.json" \
    RESOLVE_RESULT="${RESOLVE_RESULT_FILE:-$STATE/reports/resolve.json}" \
    WEIGHTS_RC_SEQ="${WEIGHTS_RC_SEQ_FILE:-}" \
    PREPARE_RC="$prepare_rc" \
    PREPARE_LOG="$STATE/logs/prepare.log" \
    WEIGHTS_LOG="$STATE/logs/weights.log" \
    UP_LOG="$STATE/logs/up.log" \
    UP_FAIL_ONCE="$STATE/up-fail-once" \
    DOWN_LOG="$STATE/logs/down.log" \
    EMPTY_INVENTORY="$STATE/inventory.json" \
    CURRENT_INVENTORY="$inventory_report" \
    STATUS_LOG="$STATE/logs/status.log" \
    "$REPO_DIR/wizard.sh" >"$STATE/logs/output.log" 2>&1
  LAST_RC=$?
  set -e
}

echo "=== ready library views launch without preparation ==="
run_wizard healthy.json 0 0 $'2\ny\n'
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" 'legacy=untested' \
  "wizard exposes a non-recommended serving profile with its display-only status"
assert_empty "$STATE/logs/prepare.log" "ready views need no preparation"
assert_contains "$STATE/logs/weights.log" '^qwen3.8-27b-fp8-2node --json$' \
  "weight preflight carries no mode flag"
assert_contains "$STATE/logs/up.log" '^qwen3.8-27b-fp8-2node --yes$' \
  "launch carries no mode flag"

echo "=== two-rank preparation and launch ==="
run_wizard unprepared.json 0 0 $'2\ny\ny\n'
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" 'DISTRIBUTED CATALOG.*TWO-RANK SERVING' \
  "wizard labels the two-rank scope"
assert_contains "$STATE/logs/output.log" 'durable home remains required' \
  "wizard discloses the durable-home dependency"
assert_contains "$STATE/logs/output.log" '^durable home[[:space:]]+node 2' \
  "long catalog labels remain separated from their values"
assert_contains "$STATE/logs/prepare.log" \
  '^prepare qwen3.8-27b-fp8-2node --backend copy --transport ssh-roce --copy-streams 8 --yes$' \
  "wizard delegates the accepted eight-stream RoCE preparation policy"
assert_contains "$STATE/logs/weights.log" '^qwen3.8-27b-fp8-2node --json$' \
  "weight preflight carries no mode flag after preparation"
assert_contains "$STATE/logs/up.log" '^qwen3.8-27b-fp8-2node --yes$' \
  "launch happens only after separate confirmation"

echo "=== blocked catalog is leave-only and never launches ==="
run_wizard attention.json 1 0 $'2\n2\n'
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_empty "$STATE/logs/prepare.log" "blocked catalog does not attempt preparation"
assert_empty "$STATE/logs/up.log" "blocked catalog never launches"

echo "=== failed preparation never launches ==="
run_wizard unprepared.json 0 7 $'2\ny\n2\n'
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" 'preparation failed' \
  "preparation failure is visible"
assert_empty "$STATE/logs/up.log" "preparation failure cannot launch"

echo "=== confirmed two-rank GA restart retains prepared views ==="
cp "$STATE/inventory.json.running-template" "$STATE/inventory.json.running"
run_wizard healthy-active.json 0 0 $'2\n2\ny\n' \
  "$STATE/inventory.json.running"
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" \
  'without an exact restore contract|exact rollback is unavailable' \
  "same-source restart stops without a restore promise (ADR 0012)"
assert_contains "$STATE/logs/down.log" \
  '^qwen3.8-27b-fp8-2node$' \
  "same-source restart stops the previous local-files service"
assert_contains "$STATE/logs/up.log" '^qwen3.8-27b-fp8-2node --yes$' \
  "restart launches through the library with no mode flag"

echo "=== failed replacement does not restore a retired match contract ==="
cp "$STATE/inventory.json.running-template" "$STATE/inventory.json.rollback"
run_wizard healthy-active.json 0 0 $'2\n2\ny\n' \
  "$STATE/inventory.json.rollback" "$STATE/reports/profiles.json" 1
# Launch is expected to fail; the point is no match-identity restore.
assert_contains "$STATE/logs/output.log" \
  'without an exact restore contract' \
  "launch failure does not offer match-identity restore (ADR 0012)"
grep -q 'Restore previous exact service' "$STATE/logs/output.log" \
  && { echo "FAIL retired match restore was offered" >&2; exit 1; } \
  || echo "OK   launch failure does not offer exact restore"
[ ! -e "$STATE/replacement-transaction.json" ] \
  || { echo "FAIL unsealed switch left transaction state" >&2; exit 1; }

echo "=== one-node catalog serving explicitly moves to durable home ==="
run_wizard one-health.json 0 0 $'1\n1\n1\ny\n' \
  "$STATE/inventory.json" "$STATE/reports/one-profiles.json"
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" \
  'selected the durable-home node for one-rank library serving: fixture-one' \
  "wizard makes the home-node placement change visible"
assert_empty "$STATE/logs/prepare.log" \
  "ready one-node durable-home view needs no materialization"
assert_contains "$STATE/logs/weights.log" \
  '^qwen3.8-27b-fp8 --node node-one-identity --json$' \
  "one-node weight preflight targets its durable home"
assert_contains "$STATE/logs/up.log" \
  '^qwen3.8-27b-fp8 --node node-one-identity --yes$' \
  "one-node catalog launch targets its durable home"

echo "=== unsealed one-node serving routes to its durable home ==="
run_wizard one-health.json 0 0 $'1\n1\n1\ny\n' \
  "$STATE/inventory.json" "$STATE/reports/unsealed-one-profiles.json"
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" \
  'selected the durable-home node for one-rank library serving: fixture-one' \
  "unsealed one-node placement moves to the catalog home rank"
assert_contains "$STATE/logs/weights.log" \
  '^qwen3.8-27b-fp8 --node node-one-identity --json$' \
  "unsealed readiness check targets the durable home"
assert_contains "$STATE/logs/up.log" \
  '^qwen3.8-27b-fp8 --node node-one-identity --yes$' \
  "unsealed one-node launch targets the durable home"
grep -qv '^resolve ' "$STATE/logs/prepare.log" \
  && { echo "FAIL ready unsealed views must not prepare or mutate" >&2; exit 1; } \
  || echo "OK   ready unsealed views need no preparation"

echo "=== unsealed multi-rank preparation keeps ssh-roce ==="
run_wizard unprepared.json 0 0 $'1\ny\ny\n' \
  "$STATE/inventory.json" "$STATE/reports/unsealed-two-profiles.json"
[ "$LAST_RC" -eq 0 ] || { cat "$STATE/logs/output.log" >&2; exit 1; }
assert_contains "$STATE/logs/prepare.log" \
  '^prepare qwen3.8-27b-fp8-2node --backend copy --transport ssh-roce --copy-streams 8 --yes$' \
  "unsealed multi-rank preparation uses eight-stream ssh-roce"
assert_contains "$STATE/logs/up.log" '^qwen3.8-27b-fp8-2node --yes$' \
  "unsealed multi-rank launch carries no mode flag"

echo "=== leftover main-era replicated transaction can be archived ==="
MAIN_TX="$REPO_DIR/scripts/testlib/replacement-transaction-main-replicated-stopped.json"
cp "$MAIN_TX" "$STATE/replacement-transaction.json"
KEEP_REPLACEMENT_TRANSACTION=1 run_wizard healthy.json 0 0 $'n\n'
[ "$LAST_RC" -ne 0 ] || { echo "FAIL decline must leave leftover transaction" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" \
  'exact rollback is impossible|predates the library-only decision' \
  "wizard names the pre-library leftover as unrestorable"
assert_contains "$STATE/logs/output.log" \
  "archive --path $STATE/replacement-transaction.json --yes" \
  "noninteractive remediation names the archive command"
[ -f "$STATE/replacement-transaction.json" ] \
  || { echo "FAIL declined archive removed the live transaction" >&2; exit 1; }

cp "$MAIN_TX" "$STATE/replacement-transaction.json"
KEEP_REPLACEMENT_TRANSACTION=1 run_wizard healthy.json 0 0 $'y\n2\n2\n'
[ ! -e "$STATE/replacement-transaction.json" ] \
  || { echo "FAIL confirmed archive left the live transaction" >&2; exit 1; }
archived=$(find "$STATE/recovered" -name replacement-transaction.json -type f | head -n 1)
[ -n "$archived" ] || { echo "FAIL archived copy is missing" >&2; exit 1; }
cmp -s "$MAIN_TX" "$archived" \
  || { echo "FAIL archived copy does not match the main-era fixture" >&2; exit 1; }
assert_contains "$STATE/logs/output.log" \
  'leftover transaction archived' \
  "wizard continues after archiving the leftover transaction"

grep -q 'confirm_library_serving()' "$REPO_DIR/wizard.sh"
! grep -q 'confirm_library_serving_sealed\|confirm_library_serving_unsealed' \
  "$REPO_DIR/wizard.sh"
echo "OK   wizard has one library-serving flow"

echo "wizard model-library selftest PASS"
