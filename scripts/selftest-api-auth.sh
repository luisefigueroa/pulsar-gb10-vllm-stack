#!/usr/bin/env bash
# Deterministic API-auth and secret-redaction checks (no live server required).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

SECRET="pulsar-selftest-secret-value"

VLLM_API_KEY="$SECRET"
API_KEY=""
auth_args=()
api_auth_curl_args auth_args
[ "${#auth_args[@]}" -eq 2 ]
[ "${auth_args[0]}" = "-H" ]
[ "${auth_args[1]}" = "Authorization: Bearer $SECRET" ]

rendered=$(shell_join_q_redacted docker run -e "HF_TOKEN=$SECRET" \
  -e "API_KEY=$SECRET" --api-key "$SECRET")
case "$rendered" in
  *"$SECRET"*)
    echo "secret redaction failed" >&2
    exit 1
    ;;
esac
printf '%s' "$rendered" | grep -q redacted

# Launch resolution requires confirmed topology + ready library views
# (ADR 0006); serve them from deterministic fixtures.
auth_state=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-api-auth.XXXXXX")
# Profiles are released specs: a fixture release set stands in for releases/.
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
STATE="$auth_state"
spec_fixture_env >/dev/null
trap 'rm -rf "$auth_state"' EXIT
python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$auth_state/topology.json"
auth_topology_id=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' \
  "$auth_state/topology.json")
python3 "$REPO_DIR/scripts/testlib/library_hot_fixture.py" \
  "$auth_state/hot-info.json" --profile "$ONE_NODE_QWEN_ID" \
  --model-id "$ONE_NODE_QWEN_MODEL" --topology-id "$auth_topology_id"
serve_out=$(HF_TOKEN="$SECRET" VLLM_API_KEY="$SECRET" \
  CLUSTER_TOPOLOGY_FILE="$auth_state/topology.json" \
  PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py" \
  FAKE_HOT_INFO_FILE="$auth_state/hot-info.json" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run)
case "$serve_out" in
  *"$SECRET"*)
    echo "serve dry-run disclosed a credential" >&2
    exit 1
    ;;
esac
printf '%s' "$serve_out" | grep -q -- '--api-key'
printf '%s' "$serve_out" | grep -q redacted

PYTHONPATH="$REPO_DIR/validate" VLLM_API_KEY="$SECRET" python3 - <<'PY'
from http_auth import api_headers, resolve_api_key

secret = "pulsar-selftest-secret-value"
assert resolve_api_key() == secret
assert api_headers()["Authorization"] == f"Bearer {secret}"
assert api_headers(content_type=True)["Content-Type"] == "application/json"
assert resolve_api_key("explicit") == "explicit"
assert api_headers("explicit")["Authorization"] == "Bearer explicit"
PY

for file in \
  validate/warmup.py \
  validate/bench_serve.py \
  validate/greedy_capture.py \
  validate/needle.py \
  validate/soak.py; do
  grep -q 'api_headers' "$file"
  grep -q -- '--api-key' "$file"
done

for file in \
  wizard.sh \
  scripts/quick-status.sh \
  scripts/doctor.sh \
  scripts/status.sh \
  scripts/up.sh \
  cluster/start-cluster.sh; do
  grep -q 'api_auth_curl_args' "$file"
done

grep -q 'print_shell_command_redacted' serve.sh
grep -q 'shell_join_q_redacted' cluster/start-cluster.sh

echo "api auth/redaction selftest OK"
