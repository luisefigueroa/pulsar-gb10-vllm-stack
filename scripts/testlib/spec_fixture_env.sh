# Source from a selftest after REPO_DIR and STATE are set. Writes the released
# fixture set under $STATE/spec-fixture and exports the environment that makes
# those specs loadable profiles:
#   PULSAR_RELEASES_ROOT, PULSAR_OVERLAY_PATH, VLLM_IMAGE_MAINLINE
# and the ids ONE_NODE_ID, TWO_NODE_ID, ONE_NODE_MODEL, TWO_NODE_MODEL,
# FIXTURE_SERVED_NAME. No conf is involved; profiles are released specs.
spec_fixture_env() {
  local root="${1:-$STATE/spec-fixture}" ids
  ids=$(python3 "$REPO_DIR/scripts/testlib/release_spec_fixture_set.py" "$root") \
    || { echo "spec_fixture_env: fixture set failed" >&2; return 1; }
  ONE_NODE_ID=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["one_node"]["spec_id"])')
  TWO_NODE_ID=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["two_node"]["spec_id"])')
  ONE_NODE_MODEL=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["one_node"]["model_id"])')
  TWO_NODE_MODEL=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["two_node"]["model_id"])')
  ONE_NODE_QWEN_ID=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["one_node_qwen"]["spec_id"])')
  ONE_NODE_QWEN_MODEL=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["one_node_qwen"]["model_id"])')
  DIAG_TWO_NODE_ID=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["diagnostic_two_node"]["spec_id"])')
  DIAG_TWO_NODE_MODEL=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["diagnostic_two_node"]["model_id"])')
  FIXTURE_SERVED_NAME=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["one_node"]["served_name"])')
  export PULSAR_RELEASES_ROOT="$root/releases"
  export PULSAR_OVERLAY_PATH="$root/overlay.json"
  VLLM_IMAGE_MAINLINE=$(printf '%s' "$ids" | python3 -c 'import json,sys; print(json.load(sys.stdin)["image"])')
  export VLLM_IMAGE_MAINLINE
  export ONE_NODE_ID TWO_NODE_ID ONE_NODE_MODEL TWO_NODE_MODEL ONE_NODE_QWEN_ID ONE_NODE_QWEN_MODEL
  export DIAG_TWO_NODE_ID DIAG_TWO_NODE_MODEL FIXTURE_SERVED_NAME
}
