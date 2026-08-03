#!/usr/bin/env bash
# Deterministic regressions for fail-closed Docker/SSH/artifact probes.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-preflight-probes.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT

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
exit 0
SHIM
chmod +x "$STATE_DIR/ssh"

# Every worker probe receives finite connection/liveness bounds.
: >"$STATE_DIR/ssh.log"
PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" \
  WORKER_IP=worker.test bash -c \
  '. "$1"; ssh_worker true' _ "$REPO_DIR/scripts/lib.sh"
grep -q -- '-o BatchMode=yes' "$STATE_DIR/ssh.log"
grep -q -- '-o ConnectTimeout=8' "$STATE_DIR/ssh.log"
grep -q -- '-o ConnectionAttempts=1' "$STATE_DIR/ssh.log"
grep -q -- '-o ServerAliveCountMax=2' "$STATE_DIR/ssh.log"
echo "OK   worker SSH is bounded"

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
  "$REPO_DIR/scripts/check-image.sh" qwen3-1.7b --json)
image_rc=$?
set -e
[ "$image_rc" -ne 0 ]
assert_json_state "$image_out" head-docker-error \
  "image check reports head Docker failure"

: >"$STATE_DIR/ssh.log"
set +e
image_out=$(DOCKER_MODE=ok PULSAR_DOCKER="$STATE_DIR/docker" \
  PULSAR_SSH="$STATE_DIR/ssh" SSH_LOG="$STATE_DIR/ssh.log" SSH_MODE=down \
  "$REPO_DIR/scripts/check-image.sh" deepseek-v4-flash --json)
image_rc=$?
set -e
[ "$image_rc" -ne 0 ]
assert_json_state "$image_out" worker-unreachable \
  "image check reports worker SSH failure"

# A config file alone is not a complete model cache. Require actual non-empty
# weights and reject interrupted-download markers.
snapshot="$STATE_DIR/hf/hub/models--Qwen--Qwen3-1.7B/snapshots/test"
mkdir -p "$snapshot"
printf '{}\n' >"$snapshot/config.json"
set +e
weights_out=$(HF_CACHE="$STATE_DIR/hf" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" partial \
  "config-only cache is partial"

printf 'weight-data\n' >"$STATE_DIR/weight-blob"
ln -s "$STATE_DIR/weight-blob" "$snapshot/model.safetensors"
HF_CACHE="$STATE_DIR/hf" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json >/dev/null
echo "OK   Hugging Face weight symlinks are accepted"

printf '{"weight_map":{"layer":"missing-shard.safetensors"}}\n' \
  >"$snapshot/model.safetensors.index.json"
set +e
weights_out=$(HF_CACHE="$STATE_DIR/hf" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" partial \
  "index referencing a missing shard is partial"
rm -f "$snapshot/model.safetensors.index.json"

: >"$snapshot/download.incomplete"
set +e
weights_out=$(HF_CACHE="$STATE_DIR/hf" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json)
weights_rc=$?
set -e
[ "$weights_rc" -ne 0 ]
assert_json_state "$weights_out" partial \
  "interrupted download marker is partial"

echo "fail-closed probe selftest OK"
