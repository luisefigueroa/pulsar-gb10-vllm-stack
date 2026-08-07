#!/usr/bin/env bash
# Ownership checks used before the wizard offers to stop a running service.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

legacy='{"running":true,"labels":{},"cmd":["--model","deepseek-ai/DeepSeek-V4-Flash-0731","--served-model-name","deepseek-v4-flash","--node-rank","0"]}'
container_metadata_matches_profile "$legacy" \
  deepseek-v4-flash deepseek-ai/DeepSeek-V4-Flash-0731 deepseek-v4-flash 0

if container_metadata_matches_profile "$legacy" \
  deepseek-v4-flash-legacy deepseek-ai/DeepSeek-V4-Flash-Legacy deepseek-v4-flash-legacy 0; then
  echo "legacy ownership accepted the wrong profile" >&2
  exit 1
fi

labeled='{"running":true,"labels":{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"deepseek-v4-flash","io.pulsar.gb10.rank":"1"},"cmd":[]}'
container_metadata_matches_profile "$labeled" \
  deepseek-v4-flash deepseek-ai/DeepSeek-V4-Flash-0731 deepseek-v4-flash 1

if container_metadata_matches_profile "$labeled" \
  deepseek-v4-flash-legacy deepseek-ai/DeepSeek-V4-Flash-Legacy deepseek-v4-flash-legacy 1; then
  echo "managed label accepted the wrong profile" >&2
  exit 1
fi

stopped='{"running":false,"labels":{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"deepseek-v4-flash","io.pulsar.gb10.rank":"0"},"cmd":[]}'
if container_metadata_matches_profile "$stopped" \
  deepseek-v4-flash deepseek-ai/DeepSeek-V4-Flash-0731 deepseek-v4-flash 0; then
  echo "ownership check accepted a stopped container" >&2
  exit 1
fi

grep -q -- '--label "${PULSAR_MANAGED_LABEL}=true"' "$REPO_DIR/serve.sh"
grep -q -- '--label "${PULSAR_MANAGED_LABEL}=true"' "$REPO_DIR/cluster/start-cluster.sh"

# Wizard safety boundary (inventory contract — not a local ownership re-classifier):
# consume inventory JSON / safe_to_stop; mutate only via down.sh (or test hook).
grep -qE 'inventory\.sh|WIZARD_INVENTORY|cmd_inventory_json' "$REPO_DIR/wizard.sh"
grep -q 'safe_to_stop' "$REPO_DIR/wizard.sh"
grep -qE 'scripts/down\.sh|WIZARD_DOWN_CMD|cmd_down' "$REPO_DIR/wizard.sh"
# Must not stop containers with raw Docker from the wizard.
if grep -qE 'docker[[:space:]]+rm|docker[[:space:]]+kill' "$REPO_DIR/wizard.sh"; then
  echo "wizard must not call docker rm/kill directly" >&2
  exit 1
fi
# Lifecycle ownership helpers remain in lib (used by down.sh / cluster stop).
grep -q 'profile_service_is_stack_owned' "$REPO_DIR/scripts/lib.sh"
grep -q 'remove_stack_owned_container_local' "$REPO_DIR/scripts/lib.sh"
