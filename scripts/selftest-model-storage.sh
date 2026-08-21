#!/usr/bin/env bash
# Interactive Models & storage scenarios with mocked health/refresh services.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-model-storage-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

python3 "$REPO_DIR/scripts/testlib/model_storage_fixture.py" "$STATE/reports"
VIEW="$REPO_DIR/scripts/model-storage.sh"
LOG="$STATE/health.log"
REFRESH_LOG="$STATE/refresh.log"
PROFILES_LOG="$STATE/profiles.log"
PREPARE_LOG="$STATE/prepare.log"

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

cat >"$STATE/bin/catalog-refresh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$REFRESH_LOG"
if [ "${REFRESH_RC:-0}" -ne 0 ]; then
  echo "fixture refresh observation failed" >&2
  exit "$REFRESH_RC"
fi
if [ -n "${REFRESH_RESULT:-}" ]; then
  cp "$REFRESH_RESULT" "$HEALTH_REPORT"
fi
SH
chmod +x "$STATE/bin/catalog-refresh"

cat >"$STATE/bin/profiles-json" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$PROFILES_LOG"
cat "$PROFILES_REPORT"
SH
chmod +x "$STATE/bin/profiles-json"

cat >"$STATE/bin/prepare-model" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$PREPARE_LOG"
if [ "${PREPARE_RC:-0}" -ne 0 ]; then
  echo "fixture preparation failed" >&2
  exit "$PREPARE_RC"
fi
if [ -n "${PREPARE_RESULT:-}" ]; then
  cp "$PREPARE_RESULT" "$HEALTH_REPORT"
fi
SH
chmod +x "$STATE/bin/prepare-model"

run_view() {
  local report="$1" rc="$2" input="$3" output="$4"
  local refresh_rc="${5:-0}" refresh_result="${6:-}"
  local prepare_rc="${7:-0}" prepare_result="${8:-}"
  local profiles_report="${9:-$STATE/reports/profiles.json}"
  local active_report="$STATE/current-health.json"
  cp "$STATE/reports/$report" "$active_report"
  : >"$LOG"
  : >"$REFRESH_LOG"
  : >"$PROFILES_LOG"
  : >"$PREPARE_LOG"
  set +e
  printf '%s' "$input" | env \
    GUM=0 \
    MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
    MODEL_STORAGE_REFRESH_CMD="$STATE/bin/catalog-refresh" \
    MODEL_STORAGE_PROFILES_CMD="$STATE/bin/profiles-json" \
    MODEL_STORAGE_PREPARE_CMD="$STATE/bin/prepare-model" \
    HEALTH_LOG="$LOG" \
    HEALTH_REPORT="$active_report" \
    HEALTH_RC="$rc" \
    REFRESH_LOG="$REFRESH_LOG" \
    REFRESH_RC="$refresh_rc" \
    REFRESH_RESULT="$refresh_result" \
    PROFILES_LOG="$PROFILES_LOG" \
    PROFILES_REPORT="$profiles_report" \
    PREPARE_LOG="$PREPARE_LOG" \
    PREPARE_RC="$prepare_rc" \
    PREPARE_RESULT="$prepare_result" \
    "$VIEW" >"$output" 2>&1
  VIEW_RC=$?
  set -e
}

"$VIEW" --help | grep -q 'The model library is the only weight-distribution mechanism'
! "$VIEW" --help | grep -qi experimental
grep -Fq 'Prepare $profile through the model library?' "$VIEW"
echo "OK   model-storage help is library-only and not experimental"

python3 -m unittest scripts.testlib.test_model_storage

plain_index=$(printf '2\n' | env GUM=0 REPO_DIR="$REPO_DIR" bash -c '
  . "$REPO_DIR/scripts/ui.sh"
  choose_index "Duplicate choices" "same label" "same label"
')
[ "$plain_index" = 1 ]
echo "OK   plain chooser returns the selected duplicate-label index"

cat >"$STATE/bin/gum-index" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = choose ]
sed -n '2p'
SH
chmod +x "$STATE/bin/gum-index"
gum_index=$(env -u NO_COLOR \
  GUM=1 \
  GUM_BIN="$STATE/bin/gum-index" \
  PULSAR_COLOR=always \
  PULSAR_FORCE_GUM=1 \
  TERM=xterm \
  REPO_DIR="$REPO_DIR" \
  bash -c '
    . "$REPO_DIR/scripts/ui.sh"
    choose_index "Duplicate choices" "same label" "same label"
  ')
[ "$gum_index" = 1 ]
echo "OK   Gum chooser returns the selected duplicate-label index"

run_view healthy.json 0 $'1\n2\n6\n' "$STATE/healthy.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/healthy.out" 'only weight mechanism'   "library-only mechanism remains visible"
assert_contains "$STATE/healthy.out" 'read-only inventory'   "catalog view is visibly read-only"
assert_contains "$STATE/healthy.out" 'MODEL STORAGE DETAIL'   "exact-model detail is reachable"
assert_contains "$STATE/healthy.out" 'durable home|durable-home'   "durable-home dependency is visible"
[ "$(wc -l <"$LOG")" -eq 1 ]
[ "$(cat "$LOG")" = "--json" ]
[ "$(cat "$PROFILES_LOG")" = "--serving --json" ]
[ ! -s "$REFRESH_LOG" ]
[ ! -s "$PREPARE_LOG" ]
echo "OK   browsing never refreshes the catalog automatically"
echo "OK   ordinary browsing never prepares model files"

run_view unprepared.json 0 $'1\n1\nn\n6\n' "$STATE/prepare-decline.out"
[ "$VIEW_RC" -eq 0 ]
[ ! -s "$PREPARE_LOG" ]
assert_contains "$STATE/prepare-decline.out" 'PREPARE FOR TWO-RANK SERVING' \
  "preparation shows the bounded two-rank GA scope before confirmation"
assert_contains "$STATE/prepare-decline.out" 'SSH over confirmed RoCE.*8 streams' \
  "preparation preview exposes the fixed transfer policy"
assert_contains "$STATE/prepare-decline.out" 'fallback[[:space:]]+none' \
  "preparation preview promises no silent transfer fallback"
assert_contains "$STATE/prepare-decline.out" '167 GiB on each non-home' \
  "preparation preview estimates non-home storage"
assert_contains "$STATE/prepare-decline.out" 'does not start or qualify a model' \
  "preparation preview preserves the launch boundary"
assert_not_contains "$STATE/prepare-decline.out" 'experimental' \
  "preparation preview does not call the library experimental"
echo "OK   declined confirmation leaves model files unchanged"

run_view unprepared.json 0 $'1\n1\ny\n6\n' "$STATE/prepare-success.out" 0 "" 0 \
  "$STATE/reports/healthy.json"
[ "$VIEW_RC" -eq 0 ]
[ "$(cat "$PREPARE_LOG")" = \
  "prepare deepseek-v4-flash --backend copy --transport ssh-roce --copy-streams 8 --yes" ]
[ "$(wc -l <"$LOG")" -eq 2 ]
assert_contains "$STATE/prepare-success.out" 'model files prepared and verified' \
  "successful preparation reports verified model files"
assert_contains "$STATE/prepare-success.out" 'serving was not started' \
  "successful preparation never claims launch"
assert_contains "$STATE/prepare-success.out" 'views[[:space:]]+2' \
  "successful preparation re-renders current runtime-view count"
assert_not_contains "$PREPARE_LOG" 'allow-unvalidated' \
  "interactive preparation does not use a validation-status override"

run_view unprepared.json 0 $'1\n1\ny\n6\n' "$STATE/prepare-failure.out" 0 "" 1
[ "$VIEW_RC" -eq 0 ]
[ "$(wc -l <"$LOG")" -eq 2 ]
assert_contains "$STATE/prepare-failure.out" 'model preparation did not complete' \
  "failed preparation never claims success"
assert_contains "$STATE/prepare-failure.out" 'fixture preparation failed' \
  "failed preparation preserves service diagnostics"
assert_contains "$STATE/prepare-failure.out" 'serving was not started' \
  "failed preparation preserves the launch boundary"

run_view attention.json 1 $'2\n1\n6\n' "$STATE/attention.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/attention.out" 'CATALOG FINDINGS'   "attention report remains browsable"
assert_contains "$STATE/attention.out" 'catalog topology stale'   "structured remediation finding is rendered"
[ "$(cat "$LOG")" = "--json" ]

run_view attention.json 1 $'1\n1\n6\n' "$STATE/stale-detail.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/stale-detail.out" 'placement stale' \
  "stale topology marks model placement in the menu"
assert_contains "$STATE/stale-detail.out" 'cached topology is stale' \
  "stale topology suppresses cached home placement"
assert_contains "$STATE/stale-detail.out" 'primary.*unavailable.*refresh catalog' \
  "stale topology suppresses cached primary placement"
assert_not_contains "$STATE/stale-detail.out" '^home[[:space:]]+node 2' \
  "stale topology never labels a cached home as a current node"
[ ! -s "$PREPARE_LOG" ]
assert_contains "$STATE/stale-detail.out" 'Preparation blocked' \
  "stale topology disables distributed catalog preparation"

run_view collision.json 0 $'2\n1\n7\n' "$STATE/collision.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/collision.out" '^model[[:space:]]+example/second-model' \
  "second truncated duplicate label opens the second model"
assert_not_contains "$STATE/collision.out" '^model[[:space:]]+example/first-model' \
  "duplicate display text does not fall back to the first model"

run_view not-configured.json 0 $'5\n' "$STATE/absent.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/absent.out" 'Serving requires one'   "catalog absence stays bounded to new serving"

run_view unavailable.json 1 $'1\n1\n5\n' "$STATE/unavailable.out"
[ "$VIEW_RC" -eq 0 ]
assert_contains "$STATE/unavailable.out" 'Running services are unaffected'   "unavailable catalog stays bounded to library maintenance"
assert_contains "$STATE/unavailable.out" 'observation unavailable'   "unavailable observation is explained"

run_view healthy.json 0 $'5\n6\n' "$STATE/recheck.out"
[ "$VIEW_RC" -eq 0 ]
[ "$(wc -l <"$LOG")" -eq 2 ]
if grep -vFx -- '--json' "$LOG" | grep -q .; then
  echo "FAIL recheck invoked a command other than health --json" >&2
  exit 1
fi
echo "OK   recheck remains a read-only health observation"
[ ! -s "$REFRESH_LOG" ]

run_view healthy.json 0 $'4\nn\n6\n' "$STATE/refresh-decline.out"
[ "$VIEW_RC" -eq 0 ]
[ ! -s "$REFRESH_LOG" ]
[ "$(wc -l <"$LOG")" -eq 1 ]
assert_contains "$STATE/refresh-decline.out" 'REFRESH DISTRIBUTED CATALOG' \
  "catalog refresh presents its scope before confirmation"
echo "OK   declined confirmation never invokes catalog refresh"
assert_contains "$STATE/refresh-decline.out" 'cached catalog and model files were not changed' \
  "declined refresh reports its no-change outcome"

run_view healthy.json 0 $'4\ny\n6\n' "$STATE/refresh-success.out" 0 \
  "$STATE/reports/refreshed.json"
[ "$VIEW_RC" -eq 0 ]
[ "$(cat "$REFRESH_LOG")" = "catalog refresh" ]
[ "$(wc -l <"$LOG")" -eq 2 ]
assert_contains "$STATE/refresh-success.out" 'rescans durable Hugging Face model homes' \
  "refresh preview explains inventory scope"
assert_contains "$STATE/refresh-success.out" 'preserves explicit exact-revision primary selections' \
  "refresh preview explains primary-selection preservation"
assert_contains "$STATE/refresh-success.out" 'catalog cache updated' \
  "successful refresh is reported"
assert_contains "$STATE/refresh-success.out" '2026-08-12T13:00:00.000Z' \
  "successful refresh re-renders the new cached timestamp"

run_view healthy.json 0 $'4\ny\n6\n' "$STATE/refresh-failure.out" 1
[ "$VIEW_RC" -eq 0 ]
[ "$(cat "$REFRESH_LOG")" = "catalog refresh" ]
[ "$(wc -l <"$LOG")" -eq 1 ]
assert_contains "$STATE/refresh-failure.out" 'refresh did not complete' \
  "failed all-rank refresh never claims an updated cache"
assert_contains "$STATE/refresh-failure.out" 'fixture refresh observation failed' \
  "refresh failure exposes actionable service detail"
assert_contains "$STATE/refresh-failure.out" 'serving default were not changed' \
  "refresh failure preserves the serving-policy boundary"

run_view invalid.json 1 '' "$STATE/invalid.out"
[ "$VIEW_RC" -eq 1 ]
assert_contains "$STATE/invalid.out" 'invalid data.*no catalog action was taken'   "invalid health data fails closed"

run_view healthy.json 2 '' "$STATE/unexpected-rc.out"
[ "$VIEW_RC" -eq 1 ]
assert_contains "$STATE/unexpected-rc.out" 'failed with status 2.*no catalog action was taken' \
  "unexpected health-service failures fail closed"

run_view unprepared.json 0 $'1\n1\n6\n' "$STATE/invalid-profiles.out" 0 "" 0 "" \
  "$STATE/reports/invalid-profiles.json"
[ "$VIEW_RC" -eq 0 ]
[ ! -s "$PREPARE_LOG" ]
assert_contains "$STATE/invalid-profiles.out" \
  'serving-profile catalog is invalid.*preparation is disabled' \
  "invalid serving-profile metadata disables preparation"
assert_contains "$STATE/invalid-profiles.out" 'MODEL STORAGE DETAIL' \
  "invalid serving-profile metadata does not block browsing"

run_view healthy.json 0 $'6\n' "$STATE/narrow.out"
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
printf '6\n' | env \
  GUM=0 \
  MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
  MODEL_STORAGE_REFRESH_CMD="$STATE/bin/catalog-refresh" \
  MODEL_STORAGE_PROFILES_CMD="$STATE/bin/profiles-json" \
  MODEL_STORAGE_PREPARE_CMD="$STATE/bin/prepare-model" \
  HEALTH_LOG="$LOG" \
  HEALTH_REPORT="$STATE/reports/healthy.json" \
  HEALTH_RC=0 \
  PROFILES_LOG="$PROFILES_LOG" \
  PROFILES_REPORT="$STATE/reports/profiles.json" \
  PREPARE_LOG="$PREPARE_LOG" \
  "$REPO_DIR/pulsar" models >"$STATE/dispatcher.out" 2>&1
assert_contains "$STATE/dispatcher.out" 'MODELS & STORAGE'   "./pulsar models routes to model storage"
"$REPO_DIR/pulsar" help >"$STATE/help.out"
assert_contains "$STATE/help.out" 'Catalog refresh and' \
  "dispatcher help introduces catalog refresh"
assert_contains "$STATE/help.out" 'separate confirmed actions' \
  "dispatcher help describes refresh and preparation as explicit"
assert_not_contains "$STATE/help.out" 'models reads cached health only' \
  "dispatcher help does not claim model storage is read-only"

cat >"$STATE/bin/models-cmd" <<SH
#!/usr/bin/env bash
exec env GUM=0 \
  MODEL_STORAGE_HEALTH_CMD="$STATE/bin/health-json" \
  MODEL_STORAGE_PROFILES_CMD="$STATE/bin/profiles-json" \
  MODEL_STORAGE_PREPARE_CMD="$STATE/bin/prepare-model" \
  HEALTH_LOG="$LOG" \
  HEALTH_REPORT="$STATE/reports/healthy.json" \
  HEALTH_RC=0 \
  PROFILES_LOG="$PROFILES_LOG" \
  PROFILES_REPORT="$STATE/reports/profiles.json" \
  PREPARE_LOG="$PREPARE_LOG" \
  "$VIEW"
SH
chmod +x "$STATE/bin/models-cmd"
: >"$LOG"
: >"$PREPARE_LOG"
printf '4\n6\n7\n' | env \
  GUM=0 \
  HOME_MODELS_CMD="$STATE/bin/models-cmd" \
  HOME_DOCTOR_CMD=/bin/false \
  HOME_INVENTORY_CMD=/bin/false \
  HOME_QUICK_STATUS_CMD=/bin/false \
  HOME_WIZARD_CMD=/bin/false \
  "$REPO_DIR/scripts/home.sh" >"$STATE/home.out" 2>&1
assert_contains "$STATE/home.out" 'Models & storage'   "operator home exposes the model-storage subsystem"
assert_contains "$STATE/home.out" 'only weight mechanism'   "home route preserves serving-policy context"
assert_not_contains "$LOG" 'refresh|prepare|start|pin|purge|remove'   "ordinary browsing invokes no lifecycle mutation"
[ ! -s "$PREPARE_LOG" ]

echo "model-storage selftest PASS"
