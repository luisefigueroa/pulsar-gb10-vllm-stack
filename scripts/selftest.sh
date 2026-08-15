#!/usr/bin/env bash
# Control-plane selftests (no Docker required for most).
#   scripts/selftest.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
fail=0

run() {
  local title="$1"; shift
  echo "=== $title ==="
  if "$@"; then
    echo "OK   $title"
  else
    echo "FAIL $title" >&2
    fail=$((fail + 1))
  fi
  echo
}

run "status advisory policy" "$REPO_DIR/scripts/selftest-status-gate.sh"
run "container names" "$REPO_DIR/scripts/selftest-container-names.sh"
run "N-node topology + exact profile subset" "$REPO_DIR/scripts/selftest-topology.sh"
run "topology-bound SSH identity" "$REPO_DIR/scripts/selftest-topology-ssh-trust.sh"
run "managed container ownership" "$REPO_DIR/scripts/selftest-managed-containers.sh"
run "spec-decode policy" "$REPO_DIR/scripts/selftest-spec-decode.sh"
run "memory profiles" "$REPO_DIR/scripts/selftest-memory-profiles.sh"
run "Docker Compose remains an unsupported historical sketch" \
  "$REPO_DIR/scripts/selftest-compose-unsupported.sh"
run "publishable documentation privacy" \
  "$REPO_DIR/scripts/selftest-docs-privacy.sh"
run "Grok sanitized review-tree contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_grok_review_tree.py"
run "Grok review skill isolation" \
  "$REPO_DIR/scripts/selftest-grok-review-tree.sh"
run "vendored Gum" "$REPO_DIR/scripts/selftest-vendored-gum.sh"
run "terminal formatting" "$REPO_DIR/scripts/selftest-terminal-format.sh"
run "CLI malformed input" "$REPO_DIR/scripts/selftest-cli-inputs.sh"
run "API auth and secret redaction" "$REPO_DIR/scripts/selftest-api-auth.sh"
run "validation verdicts" "$REPO_DIR/scripts/selftest-validation.sh"
run "fail-closed probes" "$REPO_DIR/scripts/selftest-preflight-probes.sh"
run "weight staging + cache layout" "$REPO_DIR/scripts/selftest-pull-weights.sh"
run "replicated model identity integration" \
  "$REPO_DIR/scripts/selftest-replicated-identity.sh"
run "weight fabric schema-2 contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_weight_fabric_schema2.py"
run "single-copy weight fabric" "$REPO_DIR/scripts/selftest-weight-fabric.sh"
run "model library remote-home contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_remote_home.py"
run "model library parallel-copy contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_parallel_copy.py"
run "model library transport contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_transport.py"
run "model library integrity contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_integrity.py"
run "model library serve-witness contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_witness.py"
run "replicated model exact-identity contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_replicated_model_identity.py"
run "model library expected-seal and validation-bundle contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_expected_seal.py"
run "model release identity and candidate contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_release.py"
run "Model Serving Release and Validation Contract schemas" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release.py"
run "Model Serving Release evidence and decision schemas" \
  python3 "$REPO_DIR/scripts/testlib/test_model_validation_evidence.py"
run "Model Serving Release evidence adversarial regressions" \
  python3 "$REPO_DIR/scripts/testlib/test_model_validation_evidence_adversarial.py"
run "Model Serving Release trusted-persistence registry" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_registry.py"
run "Model Serving Release evidence-capture candidate persistence" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_capture.py"
run "model library durable-home removal contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_home_removal.py"
run "model library durable-home acquisition contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_home_acquisition.py"
run "model library durable-home acquisition public CLI" \
  "$REPO_DIR/scripts/selftest-model-library-acquisition.sh"
run "model library persistent-primary contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_primary.py"
run "model library hot-budget contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_hot_budget.py"
run "model library health and legacy-repair contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_health.py"
run "model library health public CLI" \
  "$REPO_DIR/scripts/selftest-model-library-health.sh"
run "doctor Hugging Face cache remains read-only" \
  "$REPO_DIR/scripts/selftest-doctor-cache.sh"
run "interactive models and storage" \
  "$REPO_DIR/scripts/selftest-model-storage.sh"
run "model library startup-evidence contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_startup_metric.py"
run "federated model library catalog" "$REPO_DIR/scripts/selftest-model-library.sh"
run "inventory classifier" "$REPO_DIR/scripts/selftest-inventory.sh"
run "lifecycle ownership" "$REPO_DIR/scripts/selftest-lifecycle-ownership.sh"
run "serving replacement transaction contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_replacement_transaction.py"
run "wizard model-switch + dispatcher" "$REPO_DIR/scripts/selftest-wizard-switch.sh"
run "wizard experimental model-library serving" \
  "$REPO_DIR/scripts/selftest-wizard-model-library.sh"
run "library-hot stop retention policy" \
  python3 "$REPO_DIR/scripts/testlib/test_down_hot_policy.py"
run "operator home + quick-status" "$REPO_DIR/scripts/selftest-home.sh"

run "model catalog scopes" bash -c '
  set -e
  all=$("'"$REPO_DIR"'/scripts/list-models.sh" --legacy-tested --json)
  echo "$all" | python3 -c "import json,sys; m={x[\"id\"]:x for x in json.load(sys.stdin)[\"models\"]}; assert m[\"qwen3-1.7b\"][\"purpose\"]==\"diagnostic\"; assert m[\"qwen3-1.7b-2node\"][\"purpose\"]==\"diagnostic\""

  serving=$("'"$REPO_DIR"'/scripts/list-models.sh" --serving --json)
  echo "$serving" | python3 -c "import json,sys; m=json.load(sys.stdin)[\"models\"]; assert m; assert all(x[\"purpose\"]==\"serving\" for x in m); assert all(\"weights_gib\" in x and \"reviewed_identity\" in x for x in m); assert not any(x[\"id\"].startswith(\"qwen3-1.7b\") for x in m); assert any(x[\"status\"]==\"do-not-use\" for x in m); assert any(x[\"id\"]==\"deepseek-v4-flash\" and x[\"spec_default_enabled\"] and x[\"reviewed_identity\"] and x[\"weights_gib\"] > 160 and x[\"reviewed_model_id\"]==\"deepseek-ai/DeepSeek-V4-Flash-0731\" and len(x[\"reviewed_revision\"])==40 and len(x[\"reviewed_manifest\"])==64 for x in m); assert all((x[\"reviewed_model_id\"] is not None)==x[\"reviewed_identity\"] for x in m)"

  diagnostic=$("'"$REPO_DIR"'/scripts/list-models.sh" --legacy-tested --diagnostic --json)
  echo "$diagnostic" | python3 -c "import json,sys; m=json.load(sys.stdin)[\"models\"]; assert {x[\"id\"] for x in m}=={\"qwen3-1.7b\",\"qwen3-1.7b-2node\"}"
'

run "WEIGHTS_GIB disk formula" bash -c '
  # shellcheck disable=SC1091
  . "'"$REPO_DIR"'/scripts/lib.sh"
  load_conf deepseek-v4-flash
  w=$(estimate_weights_gib)
  need=$(awk -v w="$w" -v h=10 "BEGIN{printf \"%.0f\", w*1.1+h+0.999}")
  # flagship must need far more than the old flat 20 GiB gate
  awk -v n="$need" "BEGIN{exit !(n+0 > 100)}"
  echo "deepseek weights=$w need~$need"
'

run "soak exit policy (syntax + help)" bash -c '
  python3 -m py_compile "'"$REPO_DIR"'/validate/soak.py"
  python3 "'"$REPO_DIR"'/validate/soak.py" --help | grep -q max-errors
'

run "bench_serve asyncio API" bash -c '
  grep -q get_running_loop "'"$REPO_DIR"'/validate/bench_serve.py"
  ! grep -q get_event_loop "'"$REPO_DIR"'/validate/bench_serve.py"
'

run "digest-pinned N-rank image sync repair" bash -c '
  grep -q "docker load omitted the digest reference" "'"$REPO_DIR"'/scripts/sync-image.sh"
  grep -Fq "for ((rank = 1; rank < NODES; rank++))" "'"$REPO_DIR"'/scripts/sync-image.sh"
  grep -q "docker pull" "'"$REPO_DIR"'/scripts/sync-image.sh"
'

run "API base covers one- and multi-node launches" bash -c '
  grep -Fq "SERVICE_API_BASE=\"http://127.0.0.1:\$PORT\"" "'"$REPO_DIR"'/scripts/up.sh"
  grep -Fq "SERVICE_API_BASE=\$(single_node_api_base_url \"\$PORT\")" "'"$REPO_DIR"'/scripts/up.sh"
  ! grep -Fq SINGLE_API_BASE "'"$REPO_DIR"'/scripts/up.sh"
'

run "wizard uses serving-only model catalog" bash -c '
  grep -qE "list-models\.sh\" --serving --json|WIZARD_LIST_MODELS_JSON|cmd_list_models_json" "'"$REPO_DIR"'/wizard.sh"
'
run "guided CLI uses plain node language" bash -c '
  grep -Fq "doctor_ready_line \"no blocking issues found\"" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "tput setaf 2" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "NO_COLOR" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "detect-fabric.sh\" --json" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "GB10 systems discovered, but cluster membership is not confirmed." "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "Next: run ./pulsar wizard and confirm cluster discovery" "'"$REPO_DIR"'/scripts/doctor.sh"
  ! grep -Fq "Path A essentials" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "cluster node" "'"$REPO_DIR"'/wizard.sh"
  ! grep -Fq "Remote rank unreachable" "'"$REPO_DIR"'/wizard.sh"
  grep -Fq "CLUSTER DISCOVERY" "'"$REPO_DIR"'/scripts/topology_manifest.py"
  grep -Fq "SAVE CLUSTER MEMBERSHIP" "'"$REPO_DIR"'/scripts/topology_manifest.py"
  grep -Fq "same system already listed" "'"$REPO_DIR"'/scripts/topology_manifest.py"
'


run "dispatcher routes home + wizard" bash -c '
  grep -q "scripts/home.sh" "'"$REPO_DIR"'/pulsar"
  grep -q "wizard.sh" "'"$REPO_DIR"'/pulsar"
  test -x "'"$REPO_DIR"'/pulsar"
  test -x "'"$REPO_DIR"'/scripts/home.sh"
  test -x "'"$REPO_DIR"'/scripts/quick-status.sh"
  test -x "'"$REPO_DIR"'/scripts/model-storage.sh"
'

echo "=============================="
if [ "$fail" -eq 0 ]; then
  echo "selftest PASS"
  exit 0
fi
echo "selftest FAIL ($fail)" >&2
exit 1
