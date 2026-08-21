#!/usr/bin/env bash
# Deterministic lifecycle ownership tests (docker/ssh shims — no live Docker).
#   scripts/selftest-lifecycle-ownership.sh
#
# Covers: managed named stop, managed --all, unowned exact-name refusal,
# legacy refusal, label/conf/rank mismatch, name-reuse/ID revalidation failure,
# safe stale managed removal, two-node partial/ambiguous ownership refusal,
# SSH/Docker error-vs-absent, --all placement gates, post-rm verification,
# docker run id validation.
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
  if "$@" >/dev/null 2>&1; then
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
  if "$@" >/dev/null 2>&1; then
    echo "FAIL $msg (expected false)" >&2
    fail=$((fail + 1))
  else
    echo "OK   $msg"
    pass=$((pass + 1))
  fi
}

assert_rc() {
  local want="$1" msg="$2"
  shift 2
  local rc=0
  "$@" >/dev/null 2>&1 || rc=$?
  assert_eq "$rc" "$want" "$msg"
}

# ---------------------------------------------------------------------------
# Shim state
# ---------------------------------------------------------------------------
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-lifecycle-own.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT

SHIM_DIR="$STATE_DIR/bin"
mkdir -p "$SHIM_DIR"
HEAD_STATE="$STATE_DIR/head.json"
WORKER_STATE="$STATE_DIR/worker.json"
printf '%s\n' '[]' >"$HEAD_STATE"
printf '%s\n' '[]' >"$WORKER_STATE"
: >"$STATE_DIR/rm.log"
echo ok >"$STATE_DIR/head.docker_status"
echo ok >"$STATE_DIR/worker.docker_status"
echo ok >"$STATE_DIR/ssh_status"

seed_state() {
  local file="$1" json="$2"
  printf '%s\n' "$json" >"$file"
}

hex64() {
  # Deterministic 64-hex id from a short tag (not cryptographic).
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

# Fake docker: HEAD_STATE or WORKER_STATE; supports info for error-vs-absent.
cat >"$SHIM_DIR/docker" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
STATE="${FAKE_DOCKER_STATE:?}"
NODE="${FAKE_DOCKER_NODE:-head}"
LOG="${FAKE_DOCKER_RM_LOG:-/dev/null}"
STATUS_FILE="${FAKE_DOCKER_STATUS_FILE:-}"

if [ -n "$STATUS_FILE" ] && [ -f "$STATUS_FILE" ]; then
  st=$(cat "$STATUS_FILE")
  if [ "$st" = "down" ]; then
    echo "Cannot connect to the Docker daemon" >&2
    exit 1
  fi
fi

case "${1:-}" in
  info)
    exit 0
    ;;
  inspect)
    shift
    format=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --format) format="$2"; shift 2 ;;
        --format=*) format="${1#--format=}"; shift ;;
        *) break ;;
      esac
    done
    ref="${1:-}"
    [ -n "$ref" ] || exit 1
    STATE="$STATE" REF="$ref" python3 - <<'PY'
import json, os, sys
state = json.load(open(os.environ["STATE"], encoding="utf-8"))
ref = os.environ["REF"]
c = None
for item in state:
    cid = item.get("id") or ""
    name = item.get("name") or ""
    if cid == ref or cid.startswith(ref) or name == ref or name == ref.lstrip("/"):
        c = item
        break
if c is None:
    sys.exit(1)
labels = c.get("labels") or {}
out = {
    "id": c["id"],
    "name": "/" + c["name"].lstrip("/"),
    "labels": labels,
}
print(json.dumps(out, separators=(",", ":")))
PY
    ;;
  ps)
    if [ "${FAKE_DOCKER_PS_FAIL:-0}" = "1" ]; then
      echo "Error response from daemon: fake ps failure" >&2
      exit 1
    fi
    shift
    quiet=0
    filter_managed=0
    format=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -a) shift ;;
        -q|--quiet) quiet=1; shift ;;
        -aq) quiet=1; shift ;;
        --filter)
          case "$2" in
            label=io.pulsar.gb10.managed=true)
              filter_managed=1
              ;;
          esac
          shift 2
          ;;
        --format) format="$2"; shift 2 ;;
        --format=*) format="${1#--format=}"; shift ;;
        *) shift ;;
      esac
    done
    STATE="$STATE" QUIET="$quiet" FILTER_MANAGED="$filter_managed" FORMAT="$format" python3 - <<'PY'
import json, os
state = json.load(open(os.environ["STATE"], encoding="utf-8"))
quiet = os.environ.get("QUIET") == "1"
filt = os.environ.get("FILTER_MANAGED") == "1"
fmt = os.environ.get("FORMAT") or ""
for c in state:
    labels = c.get("labels") or {}
    managed = labels.get("io.pulsar.gb10.managed") == "true"
    if filt and not managed:
        continue
    if quiet:
        print(c["id"])
    elif fmt == "{{.Names}}":
        print(c["name"])
    else:
        print(c["id"], c["name"])
PY
    ;;
  rm)
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        -f|--force) shift ;;
        *) break ;;
      esac
    done
    ref="${1:-}"
    [ -n "$ref" ] || exit 1
    if [ "${FAKE_RM_FAIL:-0}" = "1" ]; then
      echo "rm forced failure" >&2
      exit 1
    fi
    echo "rm $NODE $ref" >>"$LOG"
    STATE="$STATE" REF="$ref" python3 - <<'PY'
import json, os, sys
path = os.environ["STATE"]
ref = os.environ["REF"]
state = json.load(open(path, encoding="utf-8"))
new = []
found = False
for c in state:
    cid = c.get("id") or ""
    name = c.get("name") or ""
    if cid == ref or cid.startswith(ref) or name == ref:
        found = True
        continue
    new.append(c)
if not found:
    sys.exit(1)
json.dump(new, open(path, "w", encoding="utf-8"))
print(ref)
PY
    ;;
  run)
    shift
    name=""
    labels_managed=""
    labels_conf=""
    labels_rank=""
    while [ $# -gt 0 ]; do
      case "$1" in
        -d) shift ;;
        --name) name="$2"; shift 2 ;;
        --label)
          case "$2" in
            io.pulsar.gb10.managed=*) labels_managed="${2#io.pulsar.gb10.managed=}" ;;
            io.pulsar.gb10.conf=*) labels_conf="${2#io.pulsar.gb10.conf=}" ;;
            io.pulsar.gb10.rank=*) labels_rank="${2#io.pulsar.gb10.rank=}" ;;
          esac
          shift 2
          ;;
        *) shift ;;
      esac
    done
    if [ "${FAKE_DOCKER_RUN_OUTPUT:-}" != "" ]; then
      # Test hook: emit controlled stdout instead of a real id.
      printf '%s' "$FAKE_DOCKER_RUN_OUTPUT"
      exit 0
    fi
    STATE="$STATE" NAME="$name" M="$labels_managed" C="$labels_conf" R="$labels_rank" python3 - <<'PY'
import hashlib, json, os
path = os.environ["STATE"]
state = json.load(open(path, encoding="utf-8"))
name = os.environ.get("NAME") or "anon"
cid = hashlib.sha256((name + str(len(state))).encode()).hexdigest()
labels = {}
if os.environ.get("M"):
    labels["io.pulsar.gb10.managed"] = os.environ["M"]
if os.environ.get("C"):
    labels["io.pulsar.gb10.conf"] = os.environ["C"]
if os.environ.get("R"):
    labels["io.pulsar.gb10.rank"] = os.environ["R"]
state = [c for c in state if c.get("name") != name]
state.append({"id": cid, "name": name, "labels": labels})
json.dump(state, open(path, "w", encoding="utf-8"))
print(cid)
PY
    ;;
  *)
    echo "fake-docker: unsupported: $*" >&2
    exit 64
    ;;
esac
SHIM
chmod +x "$SHIM_DIR/docker"

# Fake ssh: fail when FAKE_SSH_STATUS=down; else run remote docker against worker state.
cat >"$SHIM_DIR/ssh" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
if [ "${FAKE_SSH_STATUS:-ok}" = "down" ]; then
  echo "ssh: connect failed" >&2
  exit 255
fi
while [ $# -gt 0 ]; do
  case "$1" in
    -o|-F|-J|-i|-l|-p) shift 2 ;;
    --) shift; break ;;
    -*) shift ;;
    *) break ;;
  esac
done
shift || true
remote_cmd="${1:-}"
export FAKE_DOCKER_NODE=worker
export FAKE_DOCKER_STATE="${FAKE_WORKER_STATE:?}"
export FAKE_DOCKER_RM_LOG="${FAKE_DOCKER_RM_LOG:-/dev/null}"
export FAKE_DOCKER_STATUS_FILE="${FAKE_WORKER_DOCKER_STATUS:?}"
# shellcheck disable=SC2086
eval "$remote_cmd"
SHIM
chmod +x "$SHIM_DIR/ssh"

export FAKE_DOCKER_STATE="$HEAD_STATE"
export FAKE_WORKER_STATE="$WORKER_STATE"
export FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log"
export FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status"
export FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status"
export FAKE_SSH_STATUS=ok
export PULSAR_DOCKER="$SHIM_DIR/docker"
export PULSAR_SSH="$SHIM_DIR/ssh"
export PATH="$SHIM_DIR:$PATH"

# Deterministic topology: standalone by default; cluster sections switch to a
# confirmed two-node fixture manifest (legacy env vars no longer build one).
export CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"
TOPOLOGY_FIXTURE="$STATE_DIR/topology.json"
python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$TOPOLOGY_FIXTURE" worker-host

# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
SCRIPT_NAME=selftest-lifecycle

ID_A=$(hex64 managed-a)
ID_B=$(hex64 managed-b)
ID_LEGACY=$(hex64 legacy)
ID_FOREIGN=$(hex64 foreign)
ID_RANK=$(hex64 rank-mis)
ID_INCOMPLETE=$(hex64 incomplete)
ID_STALE=$(hex64 stale)
ID_HEAD=$(hex64 head-ok)
ID_WORKER_LEG=$(hex64 worker-leg)
ID_HEAD_ONLY=$(hex64 head-only)
ID_H=$(hex64 pair-h)
ID_W=$(hex64 pair-w)
ID_HEAD_BAD=$(hex64 head-bad)
ID_WORKER_OK=$(hex64 worker-ok)
ID_LAUNCH=$(hex64 launch)
ID_REUSED=$(hex64 reused)
ID_DOWN=$(hex64 down)
ID_ALL1=$(hex64 all1)
ID_ALL_LEG=$(hex64 all-leg)
ID_REFUSE=$(hex64 refuse)
ID_ORIG=$(hex64 original)
ID_REUSE_NAME=$(hex64 reused-name)
ID_UNKNOWN=$(hex64 unknown-conf)
ID_BAD_RANK=$(hex64 bad-rank)
ID_RANK1_HEAD=$(hex64 rank1-head)
ID_RANK0_WORKER=$(hex64 rank0-worker)
ID_SINGLE_WORKER=$(hex64 single-worker)
ID_VERIFY=$(hex64 verify-fail)

# ---------------------------------------------------------------------------
# Pure helper unit checks
# ---------------------------------------------------------------------------
echo "=== ownership proof helpers ==="

good_meta=$(python3 -c 'import json; print(json.dumps({
  "id":"'"$ID_A"'","name":"/vllm-qwen3-1.7b",
  "labels":{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}
}))')
assert_true "proven managed conf+rank" container_ownership_is_proven "$good_meta" "qwen3-1.7b" "single"
assert_false "reject wrong conf" container_ownership_is_proven "$good_meta" "other" "single"
assert_false "reject wrong rank" container_ownership_is_proven "$good_meta" "qwen3-1.7b" "0"

yes_meta=$(python3 -c 'import json; print(json.dumps({
  "id":"x","name":"/vllm-x",
  "labels":{"io.pulsar.gb10.managed":"yes","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}
}))')
assert_false "managed=yes is not accepted" container_ownership_is_proven "$yes_meta" "qwen3-1.7b" "single"
one_meta=$(python3 -c 'import json; print(json.dumps({
  "id":"x","name":"/vllm-x",
  "labels":{"io.pulsar.gb10.managed":"1","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}
}))')
assert_false "managed=1 is not accepted" container_ownership_is_proven "$one_meta" "qwen3-1.7b" "single"

legacy_meta=$(python3 -c 'import json; print(json.dumps({
  "id":"'"$ID_LEGACY"'","name":"/vllm-qwen3-1.7b","labels":{}
}))')
assert_false "legacy unlabeled not proven" container_ownership_is_proven "$legacy_meta" "qwen3-1.7b" "single"
reason=$(container_ownership_refuse_reason "$legacy_meta" "qwen3-1.7b" "single")
assert_true "legacy reason mentions unlabeled" bash -c "printf '%s' $(printf %q "$reason") | grep -qi unlabeled"

assert_true "placement head single ok" placement_rank_allowed "qwen3-1.7b" "single" "head"
assert_false "placement head rank0 for single-node conf refused" placement_rank_allowed "qwen3-1.7b" "0" "head"
assert_true "placement head rank0 for 2-node ok" placement_rank_allowed "qwen3-1.7b-2node" "0" "head"
assert_true "placement worker rank1 for 2-node ok" placement_rank_allowed "qwen3-1.7b-2node" "1" "worker"
assert_false "placement worker rank0 refused" placement_rank_allowed "qwen3-1.7b-2node" "0" "worker"
assert_true "placement worker single allowed; node-id proof gates removal" placement_rank_allowed "qwen3-1.7b" "single" "worker"
assert_true "placement rank-2 single allowed; node-id proof gates removal" placement_rank_allowed "qwen3-1.7b" "single" "rank-2"
assert_eq "$(placement_index_for_role rank-2)" "2" \
  "rank-2 inventory key resolves to physical topology index 2"
assert_false "unknown conf placement refused" placement_rank_allowed "no-such-conf" "single" "head"

rank2_meta=$(python3 -c 'import json; print(json.dumps({
  "id":"'"$ID_A"'","name":"/vllm-qwen3-1.7b",
  "labels":{
    "io.pulsar.gb10.managed":"true",
    "io.pulsar.gb10.conf":"qwen3-1.7b",
    "io.pulsar.gb10.rank":"single",
    "io.pulsar.gb10.topology":"fixture-topology",
    "io.pulsar.gb10.node-id":"fixture-node-3"
  }
}))')
rank2_identity_is_proven() (
  CLUSTER_TOPOLOGY_LOADED=1
  CLUSTER_TOPOLOGY_COUNT=3
  CLUSTER_TOPOLOGY_ID=fixture-topology
  CLUSTER_NODE_IDS=(fixture-node-1 fixture-node-2 fixture-node-3)
  container_single_node_identity_is_proven "$rank2_meta" rank-2
)
assert_true "rank-2 single cleanup proves topology and physical node identity" \
  rank2_identity_is_proven

echo "=== strict loaded-state proof ==="
load_conf qwen3-1.7b
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_A")"
assert_true "managed running single rank earns loaded exemption" \
  profile_service_is_proven_running qwen3-1.7b
warm_rc=0
warm_json=$("$REPO_DIR/scripts/check-memory.sh" qwen3-1.7b --json) || warm_rc=$?
warm_mode=$(printf '%s' "$warm_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["mode"])')
warm_loaded=$(printf '%s' "$warm_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["already_loaded"])')
assert_eq "$warm_mode" "already-loaded" \
  "check-memory uses loaded-state policy by default for a proven service"
assert_eq "$warm_loaded" "True" \
  "check-memory reports the proven loaded-state exemption"

cold_rc=0
cold_json=$("$REPO_DIR/scripts/check-memory.sh" qwen3-1.7b --cold-start --json) \
  || cold_rc=$?
cold_mode=$(printf '%s' "$cold_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["mode"])')
cold_loaded=$(printf '%s' "$cold_json" | python3 -c \
  'import json,sys; print(json.load(sys.stdin)["already_loaded"])')
assert_eq "$cold_mode" "cold-start" \
  "--cold-start bypasses the loaded-state exemption"
assert_eq "$cold_loaded" "False" \
  "--cold-start evaluates placement as a fresh launch"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{}}
]))' "$ID_LEGACY")"
assert_false "legacy exact-name container cannot earn loaded exemption" \
  profile_service_is_proven_running qwen3-1.7b

CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE"
reload_cluster_topology
load_conf qwen3-1.7b-2node
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_H")"
seed_state "$WORKER_STATE" '[]'
assert_false "incomplete cluster cannot earn loaded exemption" \
  profile_service_is_proven_running qwen3-1.7b-2node

seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_W")"
assert_true "complete managed cluster earns loaded exemption" \
  profile_service_is_proven_running qwen3-1.7b-2node

# Restore the common single-node profile and standalone topology for
# subsequent cases.
load_conf qwen3-1.7b
CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"
reload_cluster_topology

echo "=== parse_docker_run_container_id ==="
valid_id=$(hex64 run-valid)
got=$(parse_docker_run_container_id "$valid_id")
assert_eq "$got" "$valid_id" "accept plain 64-hex id"
got=$(parse_docker_run_container_id "${valid_id}"$'\n')
assert_eq "$got" "$valid_id" "accept 64-hex with trailing newline"
assert_rc 1 "reject short id" parse_docker_run_container_id "abc123"
assert_rc 1 "reject extra stdout line" parse_docker_run_container_id "${valid_id}"$'\n'"extra"
assert_rc 1 "reject prefixed garbage" parse_docker_run_container_id "id=${valid_id}"
assert_rc 1 "reject empty" parse_docker_run_container_id ""

# ---------------------------------------------------------------------------
# 1) Managed named stop
# ---------------------------------------------------------------------------
echo "=== managed named stop ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_A")"
: >"$STATE_DIR/rm.log"
assert_rc 0 "remove managed single-node by name" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_false "managed container gone after named stop" \
  container_ownership_inspect_local "vllm-qwen3-1.7b"
grep -q "rm head $ID_A" "$STATE_DIR/rm.log"
assert_eq "$?" "0" "rm log records id-based remove"

# ---------------------------------------------------------------------------
# 2) Managed --all (only known conf + placement-valid)
# ---------------------------------------------------------------------------
echo "=== managed --all ==="
# Multi-node ranks are only removable when their profile geometry is fully
# confirmed, so this section runs under the two-node fixture manifest.
CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE"
reload_cluster_topology
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}},
  {"id":sys.argv[2],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}},
  {"id":sys.argv[3],"name":"vllm-someone-else","labels":{}}
]))' "$ID_A" "$ID_B" "$ID_LEGACY")"
: >"$STATE_DIR/rm.log"
assert_rc 0 "remove_all stack-managed only" remove_all_stack_managed_local
assert_false "managed a gone" container_ownership_inspect_local "$ID_A"
assert_false "managed b gone" container_ownership_inspect_local "$ID_B"
assert_true "legacy unlabeled still present after --all" \
  container_ownership_inspect_local "$ID_LEGACY"
rm_count=$(grep -c '^rm head ' "$STATE_DIR/rm.log" || true)
assert_eq "$rm_count" "2" "--all removed exactly two managed containers"

# ---------------------------------------------------------------------------
# 2b) --all refuses unknown conf / invalid rank / wrong placement
# ---------------------------------------------------------------------------
echo "=== --all placement and conf gates ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-unknown","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"not-a-real-conf","io.pulsar.gb10.rank":"single"}},
  {"id":sys.argv[2],"name":"vllm-bad-rank","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"0"}},
  {"id":sys.argv[3],"name":"vllm-rank1-on-head","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_UNKNOWN" "$ID_BAD_RANK" "$ID_RANK1_HEAD")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "--all refuses bad ranks on head" remove_all_stack_managed_local
assert_false "retired-conf single with proven identity is removed (ADR 0006)" \
  container_ownership_inspect_local "$ID_UNKNOWN"
assert_true "invalid rank left intact" container_ownership_inspect_local "$ID_BAD_RANK"
assert_true "rank 1 on head left intact" container_ownership_inspect_local "$ID_RANK1_HEAD"
assert_eq "$(grep -c '^rm head ' "$STATE_DIR/rm.log" || true)" "1" \
  "--all removed only the proven retired-conf container"

# ---------------------------------------------------------------------------
# 2c) Retired profiles stay stoppable through labels (ADR 0006)
# ---------------------------------------------------------------------------
echo "=== retired profile cleanup ==="
ID_RETIRED_R0=$(hex64 retired-rank0)
ID_RETIRED_NOWS=$(hex64 retired-no-world-size)
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-retired-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-2node",
    "io.pulsar.gb10.rank":"0","io.pulsar.gb10.world-size":"2"}},
  {"id":sys.argv[2],"name":"vllm-cluster-retired-unsized","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-unsized",
    "io.pulsar.gb10.rank":"0"}}
]))' "$ID_RETIRED_R0" "$ID_RETIRED_NOWS")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "--all removes sized retired rank, refuses unsized" \
  remove_all_stack_managed_local
assert_false "retired 2-rank with world-size label is removed" \
  container_ownership_inspect_local "$ID_RETIRED_R0"
assert_true "retired rank without world-size label left intact" \
  container_ownership_inspect_local "$ID_RETIRED_NOWS"

# down.sh stops a retired single-node profile by proven labels alone.
ID_RETIRED_SINGLE=$(hex64 retired-single)
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-retired-profile","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-profile",
    "io.pulsar.gb10.rank":"single"}}
]))' "$ID_RETIRED_SINGLE")"
: >"$STATE_DIR/rm.log"
retired_rc=0
env FAKE_DOCKER_STATE="$HEAD_STATE" FAKE_DOCKER_NODE=head \
  FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
  FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
  PULSAR_DOCKER="$SHIM_DIR/docker" PULSAR_SSH="$SHIM_DIR/ssh" \
  CLUSTER_TOPOLOGY_FILE="$CLUSTER_TOPOLOGY_FILE" \
  "$REPO_DIR/scripts/down.sh" retired-profile >/dev/null 2>&1 || retired_rc=$?
assert_eq "$retired_rc" "0" "down.sh stops a retired single-node profile"
assert_false "retired single-node container is removed" \
  container_ownership_inspect_local "$ID_RETIRED_SINGLE"

# A retired cluster with only remote ranks left is still found and removed:
# the named stop probes every confirmed node, never just the local one.
ID_RETIRED_REMOTE=$(hex64 retired-remote-rank1)
seed_state "$HEAD_STATE" '[]'
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-retired-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-2node",
    "io.pulsar.gb10.rank":"1","io.pulsar.gb10.world-size":"2"}}
]))' "$ID_RETIRED_REMOTE")"
: >"$STATE_DIR/rm.log"
retired_rc=0
env FAKE_DOCKER_STATE="$HEAD_STATE" FAKE_DOCKER_NODE=head \
  FAKE_WORKER_STATE="$WORKER_STATE" \
  FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
  FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
  FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
  PULSAR_DOCKER="$SHIM_DIR/docker" PULSAR_SSH="$SHIM_DIR/ssh" \
  CLUSTER_TOPOLOGY_FILE="$CLUSTER_TOPOLOGY_FILE" \
  "$REPO_DIR/scripts/down.sh" retired-2node >/dev/null 2>&1 || retired_rc=$?
assert_eq "$retired_rc" "0" "retired stop reaches remote-only cluster ranks"
assert_false "remote retired rank is removed" \
  container_ownership_inspect_remote "worker-host" "$ID_RETIRED_REMOTE"
seed_state "$WORKER_STATE" '[]'

# A surviving rank that moved to a different topology index after a
# membership reconfirm is removed where it is OBSERVED, not by index math.
ID_RETIRED_MOVED=$(hex64 retired-moved-rank1)
seed_state "$WORKER_STATE" '[]'
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-retired-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-2node",
    "io.pulsar.gb10.rank":"1","io.pulsar.gb10.world-size":"2"}}
]))' "$ID_RETIRED_MOVED")"
: >"$STATE_DIR/rm.log"
retired_rc=0
env FAKE_DOCKER_STATE="$HEAD_STATE" FAKE_DOCKER_NODE=head \
  FAKE_WORKER_STATE="$WORKER_STATE" \
  FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
  FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
  FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
  PULSAR_DOCKER="$SHIM_DIR/docker" PULSAR_SSH="$SHIM_DIR/ssh" \
  CLUSTER_TOPOLOGY_FILE="$CLUSTER_TOPOLOGY_FILE" \
  "$REPO_DIR/scripts/down.sh" retired-2node >/dev/null 2>&1 || retired_rc=$?
assert_eq "$retired_rc" "0" "retired stop removes a rank at its observed node"
assert_false "moved retired rank is removed where observed" \
  container_ownership_inspect_local "$ID_RETIRED_MOVED"

# An unobservable confirmed node blocks retired removal: an unobserved live
# rank could be stranded.
ID_RETIRED_BLOCKED=$(hex64 retired-blocked-rank0)
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-retired-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"retired-2node",
    "io.pulsar.gb10.rank":"0","io.pulsar.gb10.world-size":"2"}}
]))' "$ID_RETIRED_BLOCKED")"
echo down >"$STATE_DIR/worker.docker_status"
retired_rc=0
retired_out=$(env FAKE_DOCKER_STATE="$HEAD_STATE" FAKE_DOCKER_NODE=head \
  FAKE_WORKER_STATE="$WORKER_STATE" \
  FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
  FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
  FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
  PULSAR_DOCKER="$SHIM_DIR/docker" PULSAR_SSH="$SHIM_DIR/ssh" \
  CLUSTER_TOPOLOGY_FILE="$CLUSTER_TOPOLOGY_FILE" \
  "$REPO_DIR/scripts/down.sh" retired-2node 2>&1) || retired_rc=$?
echo ok >"$STATE_DIR/worker.docker_status"
assert_eq "$retired_rc" "1" "unobservable node blocks retired removal"
printf '%s' "$retired_out" | grep -q 'unobservable' \
  && echo "OK   unobservable refusal names the blocked observation" \
  || { echo "FAIL unobservable refusal names the blocked observation" >&2; fail=$((fail + 1)); }
assert_true "retired rank left intact behind unobservable node" \
  container_ownership_inspect_local "$ID_RETIRED_BLOCKED"
seed_state "$HEAD_STATE" '[]'

# A retired named stop never accepts hot retention flags.
retired_rc=0
env FAKE_DOCKER_STATE="$HEAD_STATE" FAKE_DOCKER_NODE=head \
  FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
  FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
  PULSAR_DOCKER="$SHIM_DIR/docker" PULSAR_SSH="$SHIM_DIR/ssh" \
  CLUSTER_TOPOLOGY_FILE="$CLUSTER_TOPOLOGY_FILE" \
  "$REPO_DIR/scripts/down.sh" retired-profile --pin-weights >/dev/null 2>&1 || retired_rc=$?
assert_eq "$retired_rc" "2" "retired stop refuses hot retention flags"

# Restore standalone topology for subsequent single-node sections.
CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"
reload_cluster_topology

seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}},
  {"id":sys.argv[2],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_RANK0_WORKER" "$ID_SINGLE_WORKER")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "--all refuses rank0/single on worker" \
  remove_all_stack_managed_remote "worker-host"
assert_true "rank 0 on worker left intact" \
  container_ownership_inspect_remote "worker-host" "$ID_RANK0_WORKER"
assert_true "single on worker left intact" \
  container_ownership_inspect_remote "worker-host" "$ID_SINGLE_WORKER"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "worker placement refuse: no rm"

# ---------------------------------------------------------------------------
# 3) Unowned exact-name refusal
# ---------------------------------------------------------------------------
echo "=== unowned exact-name refusal ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"other-conf","io.pulsar.gb10.rank":"single"}}
]))' "$ID_FOREIGN")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "refuse wrong-conf exact name" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_true "unowned container still present" \
  container_ownership_inspect_local "vllm-qwen3-1.7b"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "no rm on unowned refusal"

# ---------------------------------------------------------------------------
# 4) Legacy refusal
# ---------------------------------------------------------------------------
echo "=== legacy refusal ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{}}
]))' "$ID_LEGACY")"
assert_rc 2 "refuse legacy unlabeled exact name" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_true "legacy still present" container_ownership_inspect_local "$ID_LEGACY"

# ---------------------------------------------------------------------------
# 5) Label/conf/rank mismatch
# ---------------------------------------------------------------------------
echo "=== label conf/rank mismatch ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_RANK")"
assert_rc 2 "refuse rank mismatch (want 0 have 1)" \
  remove_stack_owned_container_local "vllm-cluster-qwen3-1.7b-2node" "qwen3-1.7b-2node" "0"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-x","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b"}}
]))' "$ID_INCOMPLETE")"
assert_rc 2 "refuse incomplete rank label" \
  remove_stack_owned_container_local "vllm-x" "qwen3-1.7b" "single"

# ---------------------------------------------------------------------------
# 6) Name-reuse / ID revalidation failure
# ---------------------------------------------------------------------------
echo "=== name-reuse ID revalidation failure ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_ORIG")"

cat >"$SHIM_DIR/docker-mutate" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
COUNTER_FILE="${FAKE_MUTATE_COUNTER:?}"
MODE="${FAKE_MUTATE_MODE:-reuse}"
if [ "${1:-}" = "inspect" ]; then
  n=$(cat "$COUNTER_FILE")
  set +e
  out=$("$PULSAR_DOCKER_INNER" "$@" 2>/dev/null)
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    n=$((n + 1))
    echo "$n" >"$COUNTER_FILE"
    if [ "$n" -eq 1 ] && [ "$MODE" = "reuse" ]; then
      python3 - <<PY
import json, os
path = os.environ["FAKE_DOCKER_STATE"]
json.dump([{
  "id": os.environ["ID_REUSE_NAME"],
  "name": "vllm-qwen3-1.7b",
  "labels": {
    "io.pulsar.gb10.managed": "true",
    "io.pulsar.gb10.conf": "other-conf",
    "io.pulsar.gb10.rank": "single",
  },
}], open(path, "w", encoding="utf-8"))
PY
    elif [ "$n" -eq 1 ] && [ "$MODE" = "relabel" ]; then
      python3 - <<PY
import json, os
path = os.environ["FAKE_DOCKER_STATE"]
json.dump([{
  "id": os.environ["ID_ORIG"],
  "name": "vllm-qwen3-1.7b",
  "labels": {
    "io.pulsar.gb10.managed": "true",
    "io.pulsar.gb10.conf": "other-conf",
    "io.pulsar.gb10.rank": "single",
  },
}], open(path, "w", encoding="utf-8"))
PY
    elif [ "$n" -ge 2 ] && [ "$MODE" = "verify_fail" ]; then
      # After rm, re-insert id so post-rm verification fails.
      :
    fi
    printf '%s' "$out"
  fi
  exit "$rc"
fi
if [ "${1:-}" = "rm" ] && [ "${FAKE_MUTATE_MODE:-}" = "verify_fail" ]; then
  # Perform rm then re-create same id so verify sees it present.
  "$PULSAR_DOCKER_INNER" "$@"
  python3 - <<PY
import json, os
path = os.environ["FAKE_DOCKER_STATE"]
json.dump([{
  "id": os.environ["ID_VERIFY"],
  "name": "vllm-cluster-qwen3-1.7b-2node",
  "labels": {
    "io.pulsar.gb10.managed": "true",
    "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
    "io.pulsar.gb10.rank": "0",
  },
}], open(path, "w", encoding="utf-8"))
PY
  exit 0
fi
exec "$PULSAR_DOCKER_INNER" "$@"
SHIM
chmod +x "$SHIM_DIR/docker-mutate"
export PULSAR_DOCKER_INNER="$SHIM_DIR/docker"
export PULSAR_DOCKER="$SHIM_DIR/docker-mutate"
export ID_ORIG ID_REUSE_NAME ID_VERIFY

echo 0 >"$STATE_DIR/mutate.counter"
export FAKE_MUTATE_COUNTER="$STATE_DIR/mutate.counter"
export FAKE_MUTATE_MODE=reuse
: >"$STATE_DIR/rm.log"
assert_rc 0 "name-reuse: original id gone is non-destructive success" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_true "name-reuse: replacement container left intact" \
  container_ownership_inspect_local "$ID_REUSE_NAME"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" \
  "name-reuse: no docker rm against replacement"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_ORIG")"
echo 0 >"$STATE_DIR/mutate.counter"
export FAKE_MUTATE_MODE=relabel
: >"$STATE_DIR/rm.log"
assert_rc 2 "refuse when labels change on same id before remove" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_true "relabel race: container still present" \
  container_ownership_inspect_local "$ID_ORIG"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "relabel race: no rm"

export PULSAR_DOCKER="$SHIM_DIR/docker"
unset FAKE_MUTATE_MODE

# ---------------------------------------------------------------------------
# 7) Safe stale managed removal
# ---------------------------------------------------------------------------
echo "=== safe stale managed removal ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_STALE")"
assert_rc 0 "stale managed removable before relaunch" \
  remove_stack_owned_container_local "vllm-qwen3-1.7b" "qwen3-1.7b" "single"
assert_false "stale gone" container_ownership_inspect_local "$ID_STALE"

# ---------------------------------------------------------------------------
# 8) Two-node partial/ambiguous ownership refusal
# ---------------------------------------------------------------------------
echo "=== two-node partial/ambiguous ownership ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_HEAD")"
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{}}
]))' "$ID_WORKER_LEG")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "refuse ambiguous cluster pair (worker unowned)" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_true "head still present after ambiguous refuse" \
  container_ownership_inspect_local "$ID_HEAD"
assert_true "worker still present after ambiguous refuse" \
  container_ownership_inspect_remote "worker-host" "$ID_WORKER_LEG"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "no partial rm on ambiguous pair"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_HEAD_ONLY")"
seed_state "$WORKER_STATE" '[]'
assert_rc 0 "pair with only managed head present is removable" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_false "head-only removed" container_ownership_inspect_local "$ID_HEAD_ONLY"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_H")"
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_W")"
assert_rc 0 "managed pair both ranks removed" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_false "pair head gone" container_ownership_inspect_local "$ID_H"
assert_false "pair worker gone" container_ownership_inspect_remote "worker-host" "$ID_W"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{}}
]))' "$ID_HEAD_BAD")"
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_WORKER_OK")"
: >"$STATE_DIR/rm.log"
assert_rc 2 "refuse when head unowned even if worker managed" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_true "worker preserved on head-unowned refuse" \
  container_ownership_inspect_remote "worker-host" "$ID_WORKER_OK"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "no partial rm when head unowned"

# ---------------------------------------------------------------------------
# 8b) SSH unreachable / remote Docker failure — never treat as absence
# ---------------------------------------------------------------------------
echo "=== remote error vs absence ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_HEAD")"
seed_state "$WORKER_STATE" '[]'
: >"$STATE_DIR/rm.log"
export FAKE_SSH_STATUS=down
assert_rc 1 "SSH unreachable is operational error for pair" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_true "SSH down: head not removed" container_ownership_inspect_local "$ID_HEAD"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "SSH down: no rm"
export FAKE_SSH_STATUS=ok

echo down >"$STATE_DIR/worker.docker_status"
: >"$STATE_DIR/rm.log"
assert_rc 1 "remote docker down is operational error for pair" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_true "docker down: head not removed" container_ownership_inspect_local "$ID_HEAD"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "docker down: no rm"
echo ok >"$STATE_DIR/worker.docker_status"

# --all must not remove head when worker unreachable
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_A")"
export FAKE_SSH_STATUS=down
: >"$STATE_DIR/rm.log"
# Mimic down.sh --all worker preflight
if list_managed_container_ids_remote "worker-host" >/dev/null 2>&1; then
  echo "FAIL list_managed should fail when SSH down" >&2
  fail=$((fail + 1))
else
  echo "OK   list_managed remote fails on SSH down"
  pass=$((pass + 1))
fi
assert_true "SSH down preflight: head still present (no --all mutation)" \
  container_ownership_inspect_local "$ID_A"
export FAKE_SSH_STATUS=ok

echo down >"$STATE_DIR/worker.docker_status"
assert_rc 1 "list_managed remote fails on docker down" \
  list_managed_container_ids_remote "worker-host"
echo ok >"$STATE_DIR/worker.docker_status"

# True absence (healthy remote, no container) is not an error
seed_state "$HEAD_STATE" '[]'
seed_state "$WORKER_STATE" '[]'
assert_rc 0 "pair both absent is success" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
assert_rc 3 "inspect remote absent returns 3" \
  container_ownership_inspect_remote "worker-host" "no-such-container"

# ---------------------------------------------------------------------------
# 8c) Post-remove verification failure
# ---------------------------------------------------------------------------
echo "=== post-rm verification failure ==="
export PULSAR_DOCKER="$SHIM_DIR/docker-mutate"
export FAKE_MUTATE_MODE=verify_fail
echo 0 >"$STATE_DIR/mutate.counter"
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_VERIFY")"
seed_state "$WORKER_STATE" '[]'
assert_rc 1 "verify failure after head rm is operational error" \
  remove_stack_owned_cluster_pair "qwen3-1.7b-2node" "vllm-cluster-qwen3-1.7b-2node" "worker-host"
export PULSAR_DOCKER="$SHIM_DIR/docker"
unset FAKE_MUTATE_MODE

# ---------------------------------------------------------------------------
# 9) Launch-tracked ID cleanup does not touch reused name; rejects garbage ids
# ---------------------------------------------------------------------------
echo "=== launch ID cleanup ignores reused name ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-x","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"x","io.pulsar.gb10.rank":"0"}},
  {"id":sys.argv[2],"name":"vllm-cluster-x-other","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"y","io.pulsar.gb10.rank":"0"}}
]))' "$ID_LAUNCH" "$ID_REUSED")"
remove_container_id_local "$ID_LAUNCH"
assert_false "tracked launch id removed" container_ownership_inspect_local "$ID_LAUNCH"
assert_true "unrelated container untouched by id cleanup" \
  container_ownership_inspect_local "$ID_REUSED"

: >"$STATE_DIR/rm.log"
remove_container_id_local "not-a-valid-id"
remove_container_id_local $'deadbeef\nextra'
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" \
  "invalid launch ids never passed to docker rm"

# ---------------------------------------------------------------------------
# 10) down.sh script paths
# ---------------------------------------------------------------------------
echo "=== down.sh script paths ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_DOWN")"
(
  export PULSAR_DOCKER="$SHIM_DIR/docker"
  export PULSAR_SSH="$SHIM_DIR/ssh"
  export FAKE_DOCKER_STATE="$HEAD_STATE"
  export FAKE_WORKER_STATE="$WORKER_STATE"
  export FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log"
  export FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status"
  export FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status"
  export FAKE_SSH_STATUS=ok
  # Isolate from repo .env and any real confirmed manifest: use an env -i
  # minimal path with an explicit standalone topology file.
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    REPO_DIR="$REPO_DIR" \
    CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/scripts/down.sh" qwen3-1.7b
)
assert_false "down.sh removed managed named" container_ownership_inspect_local "$ID_DOWN"

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}},
  {"id":sys.argv[2],"name":"vllm-foreign","labels":{}}
]))' "$ID_ALL1" "$ID_ALL_LEG")"
seed_state "$WORKER_STATE" '[]'
(
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/scripts/down.sh" --all
)
assert_false "down --all removed managed" container_ownership_inspect_local "$ID_ALL1"
assert_true "down --all left legacy" container_ownership_inspect_local "$ID_ALL_LEG"

# A multi-node rank without a confirmed manifest (e.g. a cluster launched
# before topology confirmation) must be refused by --all: removing rank 0
# locally would strand live remote ranks nobody can probe.
ID_ORPHAN=$(hex64 orphan-rank0)
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_ORPHAN")"
if (
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/scripts/down.sh" --all
) 2>/dev/null; then
  echo "FAIL down --all should refuse a multi-node rank with no confirmed topology" >&2
  fail=$((fail + 1))
else
  echo "OK   down --all refuses a multi-node rank with no confirmed topology"
  pass=$((pass + 1))
fi
assert_true "unconfirmed cluster rank survives down --all" \
  container_ownership_inspect_local "$ID_ORPHAN"
seed_state "$HEAD_STATE" '[]'

seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{}}
]))' "$ID_REFUSE")"
if (
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/scripts/down.sh" qwen3-1.7b
) 2>/dev/null; then
  echo "FAIL down.sh should refuse legacy named stop" >&2
  fail=$((fail + 1))
else
  echo "OK   down.sh refuses legacy named stop"
  pass=$((pass + 1))
fi
assert_true "legacy survives refused down.sh" container_ownership_inspect_local "$ID_REFUSE"

# down --all with a confirmed two-node manifest but SSH down must fail
# without head rm
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_A")"
if (
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=down \
    bash "$REPO_DIR/scripts/down.sh" --all
) 2>/dev/null; then
  echo "FAIL down --all should fail when worker SSH down" >&2
  fail=$((fail + 1))
else
  echo "OK   down --all fails when worker SSH down"
  pass=$((pass + 1))
fi
assert_true "down --all SSH down left head intact" container_ownership_inspect_local "$ID_A"

# ---------------------------------------------------------------------------
# 11) Static: dry-run exits before cleanup (no live launcher)
# ---------------------------------------------------------------------------
echo "=== dry-run static guards ==="
assert_true "serve dry-run exits before remove" \
  bash -c 'n=$(grep -n "DRY_RUN.*=.*1" "'"$REPO_DIR"'/serve.sh" | head -1 | cut -d: -f1); \
    m=$(grep -n "remove_stack_owned_single_at_resolved_node" "'"$REPO_DIR"'/serve.sh" | head -1 | cut -d: -f1); \
    [ -n "$n" ] && [ -n "$m" ] && [ "$n" -lt "$m" ]'
assert_true "start-cluster dry-run exits before stale remove" \
  bash -c 'n=$(grep -n "DRY_RUN.*=.*1" "'"$REPO_DIR"'/cluster/start-cluster.sh" | head -1 | cut -d: -f1); \
    m=$(grep -n "remove_stack_owned_cluster " "'"$REPO_DIR"'/cluster/start-cluster.sh" | head -1 | cut -d: -f1); \
    [ -n "$n" ] && [ -n "$m" ] && [ "$n" -lt "$m" ]'
assert_true "start-cluster validates run ids via parse_docker_run_container_id" \
  grep -q parse_docker_run_container_id "$REPO_DIR/cluster/start-cluster.sh"
assert_true "start-cluster reports untracked launch via helper" \
  grep -q report_untracked_launch_container "$REPO_DIR/cluster/start-cluster.sh"
assert_true "start-cluster remediation mentions inventory.sh" \
  grep -q 'scripts/inventory.sh' "$REPO_DIR/scripts/lib.sh"
assert_true "start-cluster remediation mentions down.sh" \
  grep -q 'scripts/down.sh' "$REPO_DIR/scripts/lib.sh"
assert_true "stop-cluster loads conf before named remove" \
  bash -c 'n=$(grep -n "load_conf" "'"$REPO_DIR"'/cluster/stop-cluster.sh" | head -1 | cut -d: -f1); \
    m=$(grep -n "remove_stack_owned_cluster " "'"$REPO_DIR"'/cluster/stop-cluster.sh" | head -1 | cut -d: -f1); \
    [ -n "$n" ] && [ -n "$m" ] && [ "$n" -lt "$m" ]'
assert_true "stop-cluster requires a multi-node exact profile" \
  grep -q 'NODES.*-le.*1' "$REPO_DIR/cluster/stop-cluster.sh"

# ---------------------------------------------------------------------------
# 12) lifecycle_merge_rc severity + mixed error/refusal
# ---------------------------------------------------------------------------
echo "=== lifecycle_merge_rc severity ==="
assert_eq "$(lifecycle_merge_rc 0 0)" "0" "merge 0+0=0"
assert_eq "$(lifecycle_merge_rc 0 2)" "2" "merge 0+2=2"
assert_eq "$(lifecycle_merge_rc 2 0)" "2" "merge 2+0=2"
assert_eq "$(lifecycle_merge_rc 1 2)" "1" "merge 1+2=1 (error wins)"
assert_eq "$(lifecycle_merge_rc 2 1)" "1" "merge 2+1=1 (error wins)"
assert_eq "$(lifecycle_merge_rc 1 0)" "1" "merge 1+0=1"
assert_eq "$(lifecycle_merge_rc 0 1)" "1" "merge 0+1=1"

# Mixed: first candidate triggers operational error on rm, second is refusal.
# Use a docker wrapper that fails rm for one id only, then a bad-placement candidate.
echo "=== mixed operational error + refusal sticky rc ==="
ID_OP=$(hex64 op-err)
ID_REF=$(hex64 refuse-sticky)
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}},
  {"id":sys.argv[2],"name":"vllm-unknown","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"not-a-real-conf","io.pulsar.gb10.rank":"0"}}
]))' "$ID_OP" "$ID_REF")"
cat >"$SHIM_DIR/docker-rmfail-once" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = "rm" ]; then
  # Fail only the first managed-safe id; still invoke nothing.
  ref=""
  for a in "$@"; do ref=$a; done
  if [ "$ref" = "${ID_OP_FAIL:?}" ]; then
    echo "fake rm failure" >&2
    exit 1
  fi
fi
exec "$PULSAR_DOCKER_INNER" "$@"
SHIM
chmod +x "$SHIM_DIR/docker-rmfail-once"
export PULSAR_DOCKER_INNER="$SHIM_DIR/docker"
export ID_OP_FAIL="$ID_OP"
export PULSAR_DOCKER="$SHIM_DIR/docker-rmfail-once"
assert_rc 1 "mixed: operational error sticky over later refusal" remove_all_stack_managed_local
assert_true "mixed: refused retired rank without world size left intact" container_ownership_inspect_local "$ID_REF"
export PULSAR_DOCKER="$SHIM_DIR/docker"
unset ID_OP_FAIL

# ---------------------------------------------------------------------------
# 13) remote docker ps failure is operational error (not empty list)
# ---------------------------------------------------------------------------
echo "=== remote docker ps failure ==="
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-cluster-qwen3-1.7b-2node","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_W")"
export FAKE_DOCKER_PS_FAIL=1
assert_rc 1 "remote list fails when docker ps fails after info ok" \
  list_managed_container_ids_remote "worker-host"
assert_rc 1 "remote --all fails on docker ps error" \
  remove_all_stack_managed_remote "worker-host"
# Container must remain (no successful list → no remove path)
export FAKE_DOCKER_PS_FAIL=0
assert_true "ps-fail: worker candidate left intact" \
  container_ownership_inspect_remote "worker-host" "$ID_W"

# ---------------------------------------------------------------------------
# 14) stop-cluster named: unknown conf / single-node fail before inspect
# ---------------------------------------------------------------------------
echo "=== stop-cluster named profile gates ==="
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":"vllm-qwen3-1.7b","labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b","io.pulsar.gb10.rank":"single"}}
]))' "$ID_A")"
: >"$STATE_DIR/rm.log"
if (
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/cluster/stop-cluster.sh" no-such-conf-xyz
) 2>/dev/null; then
  echo "FAIL stop-cluster unknown conf should fail" >&2
  fail=$((fail + 1))
else
  echo "OK   stop-cluster unknown conf fails before inspect/remove"
  pass=$((pass + 1))
fi
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "unknown conf: no rm"

: >"$STATE_DIR/rm.log"
if (
  env -i \
    PATH="$SHIM_DIR:/usr/bin:/bin" \
    HOME="$HOME" \
    CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
    PULSAR_DOCKER="$SHIM_DIR/docker" \
    PULSAR_SSH="$SHIM_DIR/ssh" \
    FAKE_DOCKER_STATE="$HEAD_STATE" \
    FAKE_WORKER_STATE="$WORKER_STATE" \
    FAKE_DOCKER_RM_LOG="$STATE_DIR/rm.log" \
    FAKE_DOCKER_STATUS_FILE="$STATE_DIR/head.docker_status" \
    FAKE_WORKER_DOCKER_STATUS="$STATE_DIR/worker.docker_status" \
    FAKE_SSH_STATUS=ok \
    bash "$REPO_DIR/cluster/stop-cluster.sh" qwen3-1.7b
) 2>/dev/null; then
  echo "FAIL stop-cluster single-node conf should fail" >&2
  fail=$((fail + 1))
else
  echo "OK   stop-cluster single-node conf fails before inspect/remove"
  pass=$((pass + 1))
fi
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "single-node conf: no rm"
assert_true "single-node conf: head container left intact" \
  container_ownership_inspect_local "$ID_A"

# ---------------------------------------------------------------------------
# 15) report_untracked_launch_container message + optional short id
# ---------------------------------------------------------------------------
echo "=== untracked launch report (read-only) ==="
cname="vllm-cluster-qwen3-1.7b-2node"
seed_state "$HEAD_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":sys.argv[2],"labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"0"}}
]))' "$ID_H" "$cname")"
: >"$STATE_DIR/rm.log"
msg=$(report_untracked_launch_container head "qwen3-1.7b-2node" 0 "$cname" 2>&1) || true
assert_true "untracked report mentions inventory.sh" \
  bash -c "printf '%s' $(printf %q "$msg") | grep -q 'scripts/inventory.sh'"
assert_true "untracked report mentions down.sh <model>" \
  bash -c "printf '%s' $(printf %q "$msg") | grep -q 'scripts/down.sh qwen3-1.7b-2node'"
assert_true "untracked report says left untouched" \
  bash -c "printf '%s' $(printf %q "$msg") | grep -qi 'left untouched'"
assert_true "untracked report includes short id when proven" \
  bash -c "printf '%s' $(printf %q "$msg") | grep -q 'id=${ID_H:0:12}'"
assert_eq "$(wc -l <"$STATE_DIR/rm.log" | tr -d ' ')" "0" "untracked report never removes"
assert_true "untracked report left container intact" \
  container_ownership_inspect_local "$ID_H"

# Worker path with proven labels
seed_state "$WORKER_STATE" "$(python3 -c 'import json,sys; print(json.dumps([
  {"id":sys.argv[1],"name":sys.argv[2],"labels":{
    "io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"qwen3-1.7b-2node","io.pulsar.gb10.rank":"1"}}
]))' "$ID_W" "$cname")"
msg=$(report_untracked_launch_container worker "qwen3-1.7b-2node" 1 "$cname" "worker-host" 2>&1) || true
assert_true "worker untracked report includes short id" \
  bash -c "printf '%s' $(printf %q "$msg") | grep -q 'id=${ID_W:0:12}'"
assert_true "worker untracked container left intact" \
  container_ownership_inspect_remote "worker-host" "$ID_W"

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
