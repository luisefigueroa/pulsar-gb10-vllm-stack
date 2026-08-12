#!/usr/bin/env bash
# Read-only interactive Models & storage scenarios (no Docker/SSH/GPU/network).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-model-storage-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

python3 "$REPO_DIR/scripts/testlib/model_storage_fixture.py" "$STATE/reports"
VIEW="$REPO_DIR/scripts/model-storage.sh"
LOG="$STATE/health.log"

assert_contains() {
  local file="$1" pattern="$2" message="$3"
  if ! grep -qE "$pattern" "$file"; then
    echo "FAIL $message (missing /$pattern/)" >&2
    sed -n '1,180p' "$file" >&2
    exit 1
  fi
  echo "OK   $message"
}

assert_not_contains() {
  local file="$1" pattern="$2" message="$3"
  if grep -qE "$pattern" "$file"; then
    echo "FAIL $message (unexpected /$pattern/)" >&2
    sed -n '1,180p' "$file" >&2
    exit 1
  fi
  echo "OK   $message"
}

mkdir -p "$STATE/bin"
cat >"$STATE/bin/health-json" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$HEALTH_LOG"
cat "$HEALTH_REPORT"
exit "${HEALTH_RC:-0}"
SH
chmod +x "$STATE/bin/health-json"

run_view() {
  local report="$1" rc="$2" input="$3" output="$4"
  : >"$LOG"
  set +e
  printf '%s' "$input" | env \
    GUM=0 \
    MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
    HEALTH_LOG="$LOG" \
    HEALTH_REPORT="$STATE/reports/$report" \
    HEALTH_RC="$rc" \
    "$VIEW" >"$output" 2>&1
  VIEW_RC=$?
  set -e
}

python3 -m unittest scripts.testlib.test_model_storage

run_view healthy.json 0 $'1\n1\n5\n' "$STATE/healthy.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/healthy.out" 'guided default'   "guided replicated default remains visible"
assert_contains "$STATE/healthy.out" 'experimental read-only'   "catalog view is visibly experimental"
assert_contains "$STATE/healthy.out" 'MODEL STORAGE DETAIL'   "exact-model detail is reachable"
assert_contains "$STATE/healthy.out" 'durable home|durable-home'   "durable-home dependency is visible"
[ "$(wc -l <"$LOG")" -eq 1 ]
[ "$(cat "$LOG")" = "--json" ]

run_view attention.json 1 $'2\n1\n5\n' "$STATE/attention.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/attention.out" 'CATALOG FINDINGS'   "attention report remains browsable"
assert_contains "$STATE/attention.out" 'catalog topology stale'   "structured remediation finding is rendered"
[ "$(cat "$LOG")" = "--json" ]

run_view not-configured.json 0 $'4\n' "$STATE/absent.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/absent.out" 'Replicated serving remains'   "catalog absence does not block replicated serving"

run_view unavailable.json 1 $'1\n1\n4\n' "$STATE/unavailable.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/unavailable.out" 'Keep using the replicated path'   "unavailable catalog fails toward the guided path"
assert_contains "$STATE/unavailable.out" 'observation unavailable'   "unavailable observation is explained"

run_view healthy.json 0 $'4\n5\n' "$STATE/recheck.out"
[ "$VIEW_RC" -eq 0 ]
[ "$(wc -l <"$LOG")" -eq 2 ]
if grep -vFx -- '--json' "$LOG" | grep -q .; then
  echo "FAIL recheck invoked a command other than health --json" >&2
  exit 1
fi
echo "OK   recheck remains a read-only health observation"

run_view invalid.json 1 '' "$STATE/invalid.out"
[ "$VIEW_RC" -eq 1 ]
assert_contains "$STATE/invalid.out" 'invalid data.*no catalog action was taken'   "invalid health data fails closed"

run_view healthy.json 2 '' "$STATE/unexpected-rc.out"
[ "$VIEW_RC" -eq 1 ]
assert_contains "$STATE/unexpected-rc.out" 'failed with status 2.*no catalog action was taken' \
  "unexpected health-service failures fail closed"

run_view healthy.json 0 $'5\n' "$STATE/narrow.out"
[ "$VIEW_RC" -eq 0 ]
COLUMNS=48 python3 "$REPO_DIR/scripts/model_storage.py" \
  --report-file "$STATE/reports/healthy.json" summary >"$STATE/render-narrow.out"
python3 - "$STATE/render-narrow.out" <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
assert lines
assert max(map(len, lines)) <= 48
PY
echo "OK   human summary fits a 48-column terminal"

: >"$LOG"
printf '5\n' | env \
  GUM=0 \
  MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
  HEALTH_LOG="$LOG" \
  HEALTH_REPORT="$STATE/reports/healthy.json" \
  HEALTH_RC=0 \
  "$REPO_DIR/pulsar" models >"$STATE/dispatcher.out" 2>&1
assert_contains "$STATE/dispatcher.out" 'MODELS & STORAGE'   "./pulsar models routes to the read-only view"

cat >"$STATE/bin/models-cmd" <<SH
#!/usr/bin/env bash
exec env GUM=0 \
  MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
  HEALTH_LOG="$LOG" \
  HEALTH_REPORT="$STATE/reports/healthy.json" \
  HEALTH_RC=0 \
  "$VIEW"
SH
chmod +x "$STATE/bin/models-cmd"
: >"$LOG"
printf '4\n5\n7\n' | env \
  GUM=0 \
  HOME_MODELS_CMD="$STATE/bin/models-cmd" \
  HOME_DOCTOR_CMD=/bin/false \
  HOME_INVENTORY_CMD=/bin/false \
  HOME_QUICK_STATUS_CMD=/bin/false \
  HOME_WIZARD_CMD=/bin/false \
  "$REPO_DIR/scripts/home.sh" >"$STATE/home.out" 2>&1
assert_contains "$STATE/home.out" 'Models & storage'   "operator home exposes the read-only subsystem"
assert_contains "$STATE/home.out" 'guided default'   "home route preserves serving-policy context"
assert_not_contains "$LOG" 'refresh|prepare|start|pin|purge|remove'   "interactive view invokes no lifecycle mutation"

echo "model-storage selftest PASS"
