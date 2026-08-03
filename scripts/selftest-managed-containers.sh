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
  deepseek-v4-flash-0422 deepseek-ai/DeepSeek-V4-Flash-0422 deepseek-v4-flash-0422 0; then
  echo "legacy ownership accepted the wrong profile" >&2
  exit 1
fi

labeled='{"running":true,"labels":{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"deepseek-v4-flash","io.pulsar.gb10.rank":"1"},"cmd":[]}'
container_metadata_matches_profile "$labeled" \
  deepseek-v4-flash deepseek-ai/DeepSeek-V4-Flash-0731 deepseek-v4-flash 1

if container_metadata_matches_profile "$labeled" \
  deepseek-v4-flash-0422 deepseek-ai/DeepSeek-V4-Flash-0422 deepseek-v4-flash-0422 1; then
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
grep -q 'profile_service_is_stack_owned' "$REPO_DIR/wizard.sh"
grep -q 'scripts/down.sh' "$REPO_DIR/wizard.sh"
