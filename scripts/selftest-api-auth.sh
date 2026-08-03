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

serve_out=$(HF_TOKEN="$SECRET" VLLM_API_KEY="$SECRET" \
  "$REPO_DIR/serve.sh" qwen3-1.7b --dry-run)
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
