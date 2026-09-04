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
run "platform reference schema" \
  python3 "$REPO_DIR/scripts/testlib/test_platform_reference.py"
run "platform reference probe selection" \
  "$REPO_DIR/scripts/selftest-platform-reference.sh"
run "release spec schema (ADR 0017 Stage 1)" \
  python3 -m unittest discover -s "$REPO_DIR/release_spec/tests" -p 'test_*.py'
run "baseline-v1 policy and evaluator" \
  python3 "$REPO_DIR/scripts/testlib/test_baseline_v1.py"
run "release spec from-draft generator (ADR 0017 WP1.3, Stage 4 drafts)" \
  python3 "$REPO_DIR/scripts/testlib/test_release_spec_generate.py"
run "release spec stack consumer (ADR 0017 WP1.4a)" \
  python3 "$REPO_DIR/scripts/testlib/test_release_consumer.py"
run "release spec stack consumer CLI (ADR 0017 WP1.4a)" \
  "$REPO_DIR/scripts/selftest-release.sh"
run "start a released spec (ADR 0017 WP1.4c)" \
  "$REPO_DIR/scripts/selftest-release-spec-start.sh"
run "model library accepts a released spec (ADR 0017 WP1.4d)" \
  "$REPO_DIR/scripts/selftest-release-spec-library.sh"
run "model library spec prepare planner (ADR 0017 WP1.4d)" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_spec_prepare.py"
run "launch-plan and serving-probe contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_launch_plan.py"
run "topology-bound SSH identity" "$REPO_DIR/scripts/selftest-topology-ssh-trust.sh"
run "managed container ownership" "$REPO_DIR/scripts/selftest-managed-containers.sh"
run "spec-decode policy" "$REPO_DIR/scripts/selftest-spec-decode.sh"
run "memory profiles" "$REPO_DIR/scripts/selftest-memory-profiles.sh"
run "publishable privacy contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_publishable_privacy.py"
run "publishable content privacy" \
  "$REPO_DIR/scripts/selftest-docs-privacy.sh"
run "active current-state documentation drift (AUD-03)" \
  python3 "$REPO_DIR/scripts/testlib/test_docs_current_state.py"
run "canonical public skill layout" \
  "$REPO_DIR/scripts/selftest-skill-layout.sh"
run "Grok sanitized review-tree contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_grok_review_tree.py"
run "vendored Gum" "$REPO_DIR/scripts/selftest-vendored-gum.sh"
run "terminal formatting" "$REPO_DIR/scripts/selftest-terminal-format.sh"
run "CLI malformed input" "$REPO_DIR/scripts/selftest-cli-inputs.sh"
run "API auth and secret redaction" "$REPO_DIR/scripts/selftest-api-auth.sh"
run "validation verdicts" "$REPO_DIR/scripts/selftest-validation.sh"
run "fail-closed probes" "$REPO_DIR/scripts/selftest-preflight-probes.sh"
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
run "Model Serving Release and Validation Contract schemas" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release.py"
run "Model Serving Release draft planning" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_plan.py"
run "Model Serving Release evidence and decision schemas" \
  python3 "$REPO_DIR/scripts/testlib/test_model_validation_evidence.py"
run "Model Serving Release evidence adversarial regressions" \
  python3 "$REPO_DIR/scripts/testlib/test_model_validation_evidence_adversarial.py"
run "Model Serving Release trusted-persistence registry" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_registry.py"
run "Model Serving Release evidence-capture candidate persistence" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_capture.py"
run "Model Serving Release issuance staging" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_issue.py"
run "Model Serving Release issuance public CLI" \
  "$REPO_DIR/scripts/selftest-model-serving-release-issue.sh"
run "validator measurement contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_validator_measurement.py"
run "model-serving experiment resource monitor" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_experiment_monitor.py"
run "GSM8K closed measurement producer" \
  python3 "$REPO_DIR/scripts/testlib/test_gsm8k_eval.py"
run "snapshot manifest closed measurement producer" \
  python3 "$REPO_DIR/scripts/testlib/test_verify_snapshot_manifest.py"
run "serving smoke closed measurement producer" \
  python3 "$REPO_DIR/scripts/testlib/test_serve_smoke.py"
run "baseline-v1 run record and policy run arguments" \
  python3 "$REPO_DIR/scripts/testlib/test_baseline_run.py"
run "release spec promotion" \
  python3 "$REPO_DIR/scripts/testlib/test_release_spec_promote.py"
run "baseline-v1 runner dry run" \
  "$REPO_DIR/scripts/selftest-baseline-runner.sh"
run "Model Serving Release attempt composition" \
  python3 "$REPO_DIR/scripts/testlib/test_model_serving_release_attempt.py"
run "Model Serving Release onboarding skill" \
  "$REPO_DIR/scripts/selftest-model-onboarding-skill.sh"
run "Model Serving Release issuance skill" \
  "$REPO_DIR/scripts/selftest-model-serving-release-issuance-skill.sh"
run "safe commit skill" \
  "$REPO_DIR/scripts/selftest-pulsar-safe-commit-skill.sh"
run "Model Serving Release onboarding journal" \
  python3 "$REPO_DIR/scripts/testlib/test_onboarding_journal.py"
run "model library durable-home removal contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_home_removal.py"
run "model library durable-home acquisition contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_home_acquisition.py"
run "model library durable-home acquisition public CLI" \
  "$REPO_DIR/scripts/selftest-model-library-acquisition.sh"
run "model library relocation geometry contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_relocation.py"
run "model library download-receipt acquisition contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_receipt.py"
run "model library cold receipt and model-archive contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_cold_archive.py"
run "model library cold recovery storage contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_cold_storage.py"
run "model library cold recovery storage public CLI" \
  "$REPO_DIR/scripts/selftest-cold-storage.sh"
run "model library cold-adopt publication safety" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_cold_adopt.py"
run "model library Hugging Face source-inventory contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_hf_source_inventory.py"
run "model library download-receipt acquisition public CLI" \
  "$REPO_DIR/scripts/selftest-model-library-receipt.sh"
run "model library runtime-view materialization" \
  "$REPO_DIR/scripts/selftest-model-library-materialize.sh"
run "model library persistent-primary contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_primary.py"
run "model library atomic catalog transaction contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_catalog_transaction.py"
run "model library unbound-tree public admission contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_model_library_unbound_admission.py"
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
run "wizard explicit model-library serving" \
  "$REPO_DIR/scripts/selftest-wizard-model-library.sh"
run "local-files stop retention policy" \
  python3 "$REPO_DIR/scripts/testlib/test_down_hot_policy.py"
run "workflow menu + quick-status" "$REPO_DIR/scripts/selftest-home.sh"

run "model catalog scopes" bash -c '
  set -e
  STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-catalog-scopes.XXXXXX")
  trap "rm -rf \"$STATE\"" EXIT
  REPO_DIR="'"$REPO_DIR"'"
  . "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
  spec_fixture_env >/dev/null
  all=$("$REPO_DIR/scripts/list-models.sh" --json)
  echo "$all" | ONE="$ONE_NODE_ID" TWO="$TWO_NODE_ID" python3 -c "import json,os,sys; d=json.load(sys.stdin); m={x[\"id\"]:x for x in d[\"models\"]}; assert os.environ[\"ONE\"] in m and os.environ[\"TWO\"] in m; assert m[os.environ[\"TWO\"]][\"nodes\"]==2; assert all(x[\"status\"]==\"-\" and x[\"review_status\"]==\"-\" for x in d[\"models\"]), d[\"models\"]; assert d[\"unloadable\"]==[]; assert all(x[\"served_name\"]==\"fixture-served\" for x in d[\"models\"])"
  serving=$("$REPO_DIR/scripts/list-models.sh" --serving --json)
  echo "$serving" | python3 -c "import json,sys; m=json.load(sys.stdin)[\"models\"]; assert m; assert all(x[\"purpose\"]==\"serving\" for x in m); assert all(\"weights_gib\" in x and \"reviewed_identity\" in x and \"release_spec\" in x and \"image_digest\" in x for x in m); assert not any(x.get(\"spec_default_enabled\") for x in m); assert all(x[\"reviewed_identity\"] is False for x in m)"
  diagnostic=$("$REPO_DIR/scripts/list-models.sh" --diagnostic --json)
  echo "$diagnostic" | python3 -c "import json,sys; assert json.load(sys.stdin)[\"models\"]==[]"
  if "$REPO_DIR/scripts/list-models.sh" --legacy-tested >/dev/null 2>&1; then echo "--legacy-tested must be retired" >&2; exit 1; fi
  # A missing overlay file (not the repo default, which may exist on a serving node).
  missing=$(PULSAR_OVERLAY_PATH="$STATE/missing-overlay.json" "$REPO_DIR/scripts/list-models.sh" --json)
  echo "$missing" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"models\"]==[] and len(d[\"unloadable\"])==4 and all(\"overlay\" in u[\"reason\"] for u in d[\"unloadable\"]), d"
'

run "WEIGHTS_GIB disk formula" bash -c '
  set -e
  STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-weights-formula.XXXXXX")
  trap "rm -rf \"$STATE\"" EXIT
  REPO_DIR="'"$REPO_DIR"'"
  . "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
  spec_fixture_env >/dev/null
  # shellcheck disable=SC1091
  . "$REPO_DIR/scripts/lib.sh"
  load_conf "$ONE_NODE_ID"
  w=$(estimate_weights_gib)
  need=$(awk -v w="$w" -v h=10 "BEGIN{printf \"%.0f\", w*1.1+h+0.999}")
  # A spec view carries its manifest bytes; the formula must exceed them plus headroom.
  awk -v n="$need" -v w="$w" "BEGIN{exit !(n+0 > w+0 && n+0 >= 11)}"
  echo "one-node fixture weights=$w need~$need"
'

run "soak exit policy (syntax + help)" bash -c '
  python3 -m py_compile "'"$REPO_DIR"'/validate/soak.py"
  python3 "'"$REPO_DIR"'/validate/soak.py" --help | grep -q max-errors
'
run "soak worker startup contracts" \
  python3 "$REPO_DIR/scripts/testlib/test_soak.py"

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
run "retired recipe page is gone; MODELS.md is generated from releases/" bash -c '
  ! test -e "'"$REPO_DIR"'/docs/RECIPES.md"
  grep -Fq "BEGIN generated: scripts/release.sh list --markdown" "'"$REPO_DIR"'/docs/MODELS.md"
'
run "guided CLI uses plain node language" bash -c '
  grep -Fq "doctor_ready_line \"no blocking issues found\"" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "tput setaf 2" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "NO_COLOR" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "detect-fabric.sh\" --json" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "PULSAR_PLATFORM_DISPLAY_NAME" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "systems discovered, but cluster membership is not confirmed." "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "Next: run ./pulsar wizard and confirm cluster discovery" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "no confirmed topology manifest" "'"$REPO_DIR"'/scripts/doctor.sh"
  grep -Fq "detect-fabric.sh --write-topology" "'"$REPO_DIR"'/scripts/doctor.sh"
  ! grep -Fq "single-node models remain available" "'"$REPO_DIR"'/scripts/doctor.sh"
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
