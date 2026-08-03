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

run "status gate" "$REPO_DIR/scripts/selftest-status-gate.sh"
run "container names" "$REPO_DIR/scripts/selftest-container-names.sh"
run "managed container ownership" "$REPO_DIR/scripts/selftest-managed-containers.sh"
run "spec-decode policy" "$REPO_DIR/scripts/selftest-spec-decode.sh"
run "memory profiles" "$REPO_DIR/scripts/selftest-memory-profiles.sh"
run "vendored Gum" "$REPO_DIR/scripts/selftest-vendored-gum.sh"
run "inventory classifier" "$REPO_DIR/scripts/selftest-inventory.sh"
run "lifecycle ownership" "$REPO_DIR/scripts/selftest-lifecycle-ownership.sh"
run "wizard model-switch + dispatcher" "$REPO_DIR/scripts/selftest-wizard-switch.sh"

run "list-models --json" bash -c '
  j=$("'"$REPO_DIR"'/scripts/list-models.sh" --validated --json)
  echo "$j" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get(\"models\"); assert any(m[\"id\"]==\"deepseek-v4-flash\" and m[\"spec_default_enabled\"] for m in d[\"models\"])"
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

run "digest-pinned image sync repair" bash -c '
  grep -q "docker load omitted the digest reference" "'"$REPO_DIR"'/scripts/sync-image.sh"
  grep -q "ssh_worker.*docker pull" "'"$REPO_DIR"'/scripts/sync-image.sh"
'

run "wizard uses list-models --json" bash -c '
  grep -qE "list-models\.sh\" --validated --json|WIZARD_LIST_MODELS_JSON|cmd_list_models_json" "'"$REPO_DIR"'/wizard.sh"
'

run "dispatcher routes wizard" bash -c '
  grep -q "wizard.sh" "'"$REPO_DIR"'/pulsar"
  test -x "'"$REPO_DIR"'/pulsar"
'

echo "=============================="
if [ "$fail" -eq 0 ]; then
  echo "selftest PASS"
  exit 0
fi
echo "selftest FAIL ($fail)" >&2
exit 1
