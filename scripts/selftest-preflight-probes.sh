#!/usr/bin/env bash
# Deterministic regressions for fail-closed Docker/SSH/artifact probes.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-preflight-probes.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT
export CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"

# Confirmed two-node fixture manifest for multi-node probe scenarios.
TOPOLOGY_FIXTURE="$STATE_DIR/topology.json"
python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$TOPOLOGY_FIXTURE" worker.test

assert_json_state() {
  local body="$1" expected="$2" label="$3"
  BODY="$body" EXPECTED="$expected" python3 - <<'PY'
import json
import os

body = json.loads(os.environ["BODY"])
assert body.get("state") == os.environ["EXPECTED"], body
PY
  echo "OK   $label"
}

cat >"$STATE_DIR/docker" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
if [ "${DOCKER_MODE:-ok}" = psfail ] && [ "${1:-}" = ps ]; then
  echo "Docker ps failed" >&2
  exit 1
fi
if [ "${DOCKER_MODE:-ok}" = down ]; then
  echo "Cannot connect to the Docker daemon" >&2
  exit 1
fi
case "${1:-}" in
  info) exit 0 ;;
  image)
    [ "${2:-}" = inspect ] || exit 64
    exit 0
    ;;
  ps) exit 0 ;;
  *) exit 64 ;;
esac
SHIM
chmod +x "$STATE_DIR/docker"

cat >"$STATE_DIR/ssh" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${SSH_LOG:?}"
[ "${SSH_MODE:-ok}" != down ] || exit 255
if [ "${SSH_MODE:-ok}" = weights-missing ]; then
  remote_command="${!#}"
  case "$remote_command" in
    *refs/main*) exit 3 ;;
    *'echo partial || echo missing'*) echo missing; exit 0 ;;
  esac
fi
exit 0
SHIM
chmod +x "$STATE_DIR/ssh"

# Every remote-rank probe receives finite connection/liveness bounds.
: >"$STATE_DIR/ssh.log"
PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" bash -c \
  '. "$1"; ssh_node 1 true' _ "$REPO_DIR/scripts/lib.sh"
grep -q -- '-o BatchMode=yes' "$STATE_DIR/ssh.log"
grep -q -- '-o ConnectTimeout=8' "$STATE_DIR/ssh.log"
grep -q -- '-o ConnectionAttempts=1' "$STATE_DIR/ssh.log"
grep -q -- '-o ServerAliveCountMax=2' "$STATE_DIR/ssh.log"
grep -Eq -- ' -- [^ ]+ true$' "$STATE_DIR/ssh.log"
echo "OK   remote-rank SSH is bounded"

# Legacy HEAD_IP/WORKER_IP env vars never substitute for a confirmed manifest:
# a multi-node probe with no manifest refuses before any SSH work.
: >"$STATE_DIR/ssh.log"
set +e
image_out=$(DOCKER_MODE=ok PULSAR_DOCKER="$STATE_DIR/docker" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  HEAD_IP=head.test WORKER_IP=worker.test \
  "$REPO_DIR/scripts/check-image.sh" qwen3.8-27b-fp8-2node --json)
image_rc=$?
set -e
[ "$image_rc" -ne 0 ]
assert_json_state "$image_out" need-topology \
  "legacy env vars cannot admit a multi-node image probe"
if grep -Eq -- ' -- (head|worker)\.test ' "$STATE_DIR/ssh.log"; then
  echo "FAIL legacy env endpoints must never receive SSH probes" >&2
  exit 1
fi
echo "OK   legacy env endpoints receive no SSH probes"

# Head Docker failure cannot become an empty, apparently safe inventory.
set +e
inventory_out=$(DOCKER_MODE=down INVENTORY_DOCKER="$STATE_DIR/docker" \
  "$REPO_DIR/scripts/inventory.sh" --json 2>&1)
inventory_rc=$?
set -e
[ "$inventory_rc" -ne 0 ]
printf '%s' "$inventory_out" | grep -q 'no lifecycle action is safe'
echo "OK   inventory fails closed when head Docker is unavailable"

set +e
inventory_out=$(DOCKER_MODE=psfail INVENTORY_DOCKER="$STATE_DIR/docker" \
  "$REPO_DIR/scripts/inventory.sh" --json 2>&1)
inventory_rc=$?
set -e
[ "$inventory_rc" -ne 0 ]
printf '%s' "$inventory_out" | grep -q 'container inventory failed'
echo "OK   inventory fails closed when Docker enumeration fails"

# Image checks distinguish operational failure from a missing image.
set +e
image_out=$(DOCKER_MODE=down PULSAR_DOCKER="$STATE_DIR/docker" \
  "$REPO_DIR/scripts/check-image.sh" qwen3.8-27b-fp8 --json)
image_rc=$?
set -e
[ "$image_rc" -ne 0 ]
assert_json_state "$image_out" head-docker-error \
  "image check reports head Docker failure"

: >"$STATE_DIR/ssh.log"
set +e
image_out=$(DOCKER_MODE=ok PULSAR_DOCKER="$STATE_DIR/docker" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" SSH_MODE=down \
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  "$REPO_DIR/scripts/check-image.sh" qwen3.8-27b-fp8-2node --json)
image_rc=$?
set -e
[ "$image_rc" -ne 0 ]
assert_json_state "$image_out" rank-unreachable \
  "image check reports rank SSH failure"

# The weight-mode axis is removed (ADR 0006): any use fails closed with an
# actionable retirement message before other work.
set +e
flag_out=$("$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8 \
  --weight-source library-hot 2>&1)
flag_rc=$?
set -e
[ "$flag_rc" -eq 2 ]
printf '%s' "$flag_out" | grep -q 'ADR 0006'
set +e
flag_out=$("$REPO_DIR/scripts/up.sh" qwen3.8-27b-fp8 --dry-run \
  --weight-source replicated 2>&1)
flag_rc=$?
set -e
[ "$flag_rc" -eq 2 ]
printf '%s' "$flag_out" | grep -q 'ADR 0006'
echo "OK   removed weight-mode flags fail closed with remediation"

# Serving requires a confirmed topology manifest, even on one machine, and
# refuses before any SSH probe.
: >"$STATE_DIR/ssh.log"
set +e
weights_out=$(CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8 --json 2>&1)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
printf '%s' "$weights_out" | grep -q 'confirmed topology manifest'
[ ! -s "$STATE_DIR/ssh.log" ]
echo "OK   weights check requires a confirmed topology manifest"

# Unprepared library views are missing, not healthy.
set +e
weights_out=$(CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  PULSAR_HOT_ROOT="$STATE_DIR/empty-hot" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8-2node --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" missing \
  "unprepared library views are missing"

# Ready library views report a single self-describing JSON contract.
fixture_topology_id=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' \
  "$TOPOLOGY_FIXTURE")
python3 "$REPO_DIR/scripts/testlib/library_hot_fixture.py" \
  "$STATE_DIR/hot-info.json" --profile qwen3.8-27b-fp8-2node \
  --topology-id "$fixture_topology_id"
: >"$STATE_DIR/ssh.log"
weights_out=$(CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py" \
  FAKE_HOT_INFO_FILE="$STATE_DIR/hot-info.json" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8-2node --json)
assert_json_state "$weights_out" ok "ready library views report ok"
grep -q 'verify-hot' "$STATE_DIR/ssh.log" \
  || { echo "FAIL readiness must verify every remote rank" >&2; exit 1; }
echo "OK   readiness verifies remote ranks before reporting ok"

WEIGHTS_JSON="$weights_out" python3 - <<'PY2'
import json
import os

data = json.loads(os.environ["WEIGHTS_JSON"])
assert data["source"] == "library-hot", data
assert data["identity_status"] == "legacy-unsealed", data
assert data["model"] == "qwen3.8-27b-fp8-2node", data
assert data["nodes"] == 2, data
PY2
# A remote rank whose view cannot be verified is missing, not healthy.
set +e
weights_out=$(CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py" \
  FAKE_HOT_INFO_FILE="$STATE_DIR/hot-info.json" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" SSH_MODE=down \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8-2node --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" missing \
  "unverifiable remote rank view is missing"
WEIGHTS_JSON="$weights_out" python3 - <<'PY2'
import json
import os

data = json.loads(os.environ["WEIGHTS_JSON"])
assert data["failed_rank"] == 1, data
assert data.get("reason") == "rank-unreachable", data
PY2
weights_human=$(QUIET=1 CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py" \
  FAKE_HOT_INFO_FILE="$STATE_DIR/hot-info.json" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8-2node)
printf '%s\n' "$weights_human" | grep -q 'identity=receipt/occupancy'
echo "OK   human and JSON weight projections agree"

# One-node --node on an unreachable confirmed rank is not a prepare gap.
: >"$STATE_DIR/ssh.log"
set +e
weights_out=$(CLUSTER_TOPOLOGY_FILE="$TOPOLOGY_FIXTURE" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" SSH_MODE=down \
  "$REPO_DIR/scripts/check-weights.sh" qwen3.8-27b-fp8 --node fixture-node-1 --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" missing \
  "one-node unreachable placement is missing"
WEIGHTS_JSON="$weights_out" python3 - <<'PY2'
import json
import os

data = json.loads(os.environ["WEIGHTS_JSON"])
assert data.get("reason") == "rank-unreachable", data
assert data.get("failed_rank") == 1, data
assert "prepare" not in (data.get("remediation") or ""), data
assert "inventory" in (data.get("remediation") or ""), data
PY2
echo "OK   one-node --node unreachable rank does not suggest prepare"

echo "fail-closed probe selftest OK"
