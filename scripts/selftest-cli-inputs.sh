#!/usr/bin/env bash
# Deterministic malformed-input and non-interactive CLI regressions.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-cli-inputs.XXXXXX")
STATE="$tmpdir"
# Profiles are released specs: malformed-input cases run against fixture specs.
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
spec_fixture_env >/dev/null
trap 'rm -rf "$tmpdir"' EXIT
export CLUSTER_TOPOLOGY_FILE="$tmpdir/no-topology.json"

expect_failure() {
  local expected_rc="$1" needle="$2" label="$3"
  shift 3
  local output rc=0
  set +e
  output=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$rc" != "$expected_rc" ]; then
    echo "FAIL $label: rc=$rc expected=$expected_rc output=$output" >&2
    exit 1
  fi
  if ! printf '%s' "$output" | grep -q -- "$needle"; then
    echo "FAIL $label: missing '$needle' in output=$output" >&2
    exit 1
  fi
  echo "OK   $label"
}

expect_failure 2 "--port requires a value" "serve missing port value" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run --port
expect_failure 2 "invalid --port" "serve rejects nonnumeric port" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run --port nope
expect_failure 2 "invalid --port" "serve rejects out-of-range port" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run --port 70000
expect_failure 1 "invalid model id" "config loader rejects path traversal" \
  "$REPO_DIR/serve.sh" ../outside --dry-run
expect_failure 2 "ADR 0006" "serve rejects the removed weight-mode axis" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run --weight-source library-hot
expect_failure 2 "SIM-12" "pulsar refuses removed weight-fabric helper" \
  "$REPO_DIR/pulsar" weight-fabric show "$TWO_NODE_ID"
expect_failure 2 "configure cold-storage" "pulsar configure without a topic is usage" \
  "$REPO_DIR/pulsar" configure
expect_failure 2 "configure cold-storage" "pulsar configure rejects an unsupported topic" \
  "$REPO_DIR/pulsar" configure networking
expect_failure 2 "SIM-13" "model-library refuses removed hot-legacy repair" \
  "$REPO_DIR/scripts/model-library.sh" hot legacy check unused --json
expect_failure 2 "SIM-13" "model-library refuses removed hot-legacy remove" \
  "$REPO_DIR/scripts/model-library.sh" hot legacy remove unused --yes
expect_failure 2 "ADR 0012" "model-library refuses retired cold stage-only" \
  "$REPO_DIR/scripts/model-library.sh" cold stage-only "$ONE_NODE_QWEN_ID" --yes

expect_failure 2 "ADR 0008" "serve refuses removed --force" \
  "$REPO_DIR/serve.sh" "$ONE_NODE_QWEN_ID" --dry-run --force
expect_failure 2 "Drop the flag" "up refuses removed --force" \
  "$REPO_DIR/scripts/up.sh" "$ONE_NODE_QWEN_ID" --dry-run --force
expect_failure 2 "Drop the flag" "start-cluster refuses removed --force" \
  "$REPO_DIR/cluster/start-cluster.sh" "$ONE_NODE_QWEN_ID" --dry-run --force
expect_failure 2 "released specs" "list-models refuses removed --validated" \
  "$REPO_DIR/scripts/list-models.sh" --validated
expect_failure 2 "retired (ADR 0012)" "catalog list refuses removed --validated" \
  "$REPO_DIR/scripts/model-library.sh" catalog list --validated
expect_failure 2 "use prepare" "model-library refuses removed activate" \
  "$REPO_DIR/scripts/model-library.sh" activate "$ONE_NODE_QWEN_ID"
expect_failure 2 "Drop the flag" "python plan-prepare refuses --allow-unvalidated" \
  python3 "$REPO_DIR/scripts/model_library.py" plan-prepare --allow-unvalidated
expect_failure 2 "retired (ADR 0012)" "python list refuses removed --validated" \
  python3 "$REPO_DIR/scripts/model_library.py" list --catalog /dev/null --validated
expect_failure 2 "ADR 0012" "python refuses retired cold stage-only planner" \
  python3 "$REPO_DIR/scripts/model_library.py" plan-cold-stage --execute

grep -q '\[ "$DRY" != 1 \].*PULL_IMG' "$REPO_DIR/scripts/up.sh"
echo "OK   up dry-run cannot enter image pull/sync branches"

expect_failure 2 "--tag requires a value" "gate runner missing tag value" \
  "$REPO_DIR/validate/run-gates.sh" model --tag
expect_failure 2 "invalid --needle-tokens" "gate runner rejects nonnumeric context" \
  "$REPO_DIR/validate/run-gates.sh" model --needle-tokens nope
expect_failure 2 "invalid served name" "gate runner rejects artifact path traversal" \
  "$REPO_DIR/validate/run-gates.sh" ../outside
expect_failure 2 "baseline is not readable" "gate runner rejects missing baseline" \
  "$REPO_DIR/validate/run-gates.sh" model --baseline /no/such/capture.json
expect_failure 2 "--measurement-dir requires a value" "gate runner missing measurement dir" \
  "$REPO_DIR/validate/run-gates.sh" model --measurement-dir
expect_failure 2 "--invocation-plan requires a value" "gate runner missing invocation plan" \
  "$REPO_DIR/validate/run-gates.sh" model --invocation-plan
expect_failure 2 "invocation plan is not readable" "gate runner rejects missing invocation plan" \
  "$REPO_DIR/validate/run-gates.sh" model --invocation-plan /no/such/invocation-plan.json
expect_failure 2 "measurement directory is not a safe" "gate runner rejects protected measurement dir" \
  "$REPO_DIR/validate/run-gates.sh" model --tag measdir --measurement-dir models/unsafe

# Legacy HEAD_IP/WORKER_IP environment variables never admit multi-node
# operations — including hostile values that could become SSH options.
expect_failure 1 "do not confirm membership" "legacy env vars cannot admit multi-node topology" \
  bash -c 'export HEAD_IP=10.0.0.1 WORKER_IP=-oProxyCommand=false; . "$1"; require_profile_topology 2 roce-full-mesh 2' \
  _ "$REPO_DIR/cluster/cluster-env.sh"

# With no controlling terminal, Gum is disabled before a menu is started. The
# plain path then handles EOF as cancellation instead of waiting invisibly.
set +e
ui_output=$(REPO_DIR="$REPO_DIR" GUM_BIN=/bin/true GUM=1 TERM=xterm-256color \
  bash -c '. "$1"; . "$2"; [ "$have_gum" = 0 ]; choose "Menu" "Exit"' \
  _ "$REPO_DIR/scripts/lib.sh" "$REPO_DIR/scripts/ui.sh" </dev/null 2>&1)
ui_rc=$?
set -e
if [ "$ui_rc" != 1 ]; then
  echo "FAIL non-interactive UI: rc=$ui_rc output=$ui_output" >&2
  exit 1
fi
printf '%s' "$ui_output" | grep -q Menu
echo "OK   non-interactive UI falls back and cancels on EOF"

echo "CLI malformed-input selftest OK"
