#!/usr/bin/env bash
# Thin public-CLI scenarios for model-library health. Public hot-legacy repair
# is removed (SIM-13); leftover schema-1/2 is observed, not mutated.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-health-shell.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
python3 "$REPO_DIR/scripts/testlib/model_library_health_fixture.py" "$STATE"

BASE_ENV=(
  "PATH=$STATE/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/topology.json"
  "MODEL_LIBRARY_DIR=$STATE/library"
  "MODEL_LIBRARY_CATALOG=$STATE/library/catalog.json"
  "PULSAR_HOT_ROOT=$STATE/hot-owner"
  "PULSAR_DOCKER=$STATE/bin/docker"
  "PULSAR_SSH=$STATE/ssh"
  "MOCK_BIN=$STATE/bin"
  "MOCK_OWNER_HOT_ROOT=$STATE/hot-owner"
  "MOCK_CLIENT_HOT_ROOT=$STATE/hot-client"
)
LIBRARY="$REPO_DIR/scripts/model-library.sh"
PYTHON="$REPO_DIR/scripts/model_library.py"

health_rc=0
env "${BASE_ENV[@]}" "$LIBRARY" health --json >"$STATE/health.json" \
  || health_rc=$?
[ "$health_rc" -eq 1 ]
python3 - "$STATE/health.json" "$STATE" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["schema_version"] == 1
assert report["state"] == "attention"
legacy = [item for item in report["hot_instances"] if item["metadata_status"] == "legacy"]
assert {item["rank"] for item in legacy} == {0, 1}
assert all(item["repairable"] is False for item in legacy)
assert all(item["repair_id"] is None for item in legacy)
assert "legacy-hot-metadata" in {issue["code"] for issue in report["issues"]}
assert not any(
    (issue.get("remediation") or {}).get("command")
    for issue in report["issues"]
    if issue.get("code") == "legacy-hot-metadata"
)
encoded = json.dumps(report)
assert sys.argv[2] not in encoded
assert "fixture-owner-id" not in encoded
assert "fixture-client-id" not in encoded
PY

hot_rc=0
env "${BASE_ENV[@]}" "$LIBRARY" hot legacy check unused --json \
  >"$STATE/hot-check.out" 2>"$STATE/hot-check.err" || hot_rc=$?
[ "$hot_rc" -eq 2 ]
grep -q "SIM-13" "$STATE/hot-check.err"
[ -d "$STATE/hot-owner/fixture-health-topology/content" ]

hot_remove_rc=0
env "${BASE_ENV[@]}" "$LIBRARY" hot legacy remove unused --yes \
  >"$STATE/hot-remove.out" 2>"$STATE/hot-remove.err" || hot_remove_rc=$?
[ "$hot_remove_rc" -eq 2 ]
grep -q "SIM-13" "$STATE/hot-remove.err"
[ -d "$STATE/hot-owner/fixture-health-topology/content" ]
[ -d "$STATE/hot-client/fixture-health-topology/content" ]

COLUMNS=44 env "${BASE_ENV[@]}" "$LIBRARY" health \
  >"$STATE/narrow.out" 2>/dev/null || true
python3 - "$STATE/narrow.out" <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
assert lines
assert max(map(len, lines)) <= 44, max(lines, key=len)
PY

doctor_rows_rc=0
python3 "$PYTHON" render-health --report-file "$STATE/health.json" \
  --doctor-rows >"$STATE/doctor.rows" || doctor_rows_rc=$?
[ "$doctor_rows_rc" -eq 1 ]
grep -q $'^warn\tmodel_library_' "$STATE/doctor.rows"
# Doctor must consume valid attention rows even though their render exits 1.
grep -Fq '[ "$library_render_rc" -le 1 ] && [ -s "$library_rows" ]' \
  "$REPO_DIR/scripts/doctor.sh"

unavailable_rc=0
env "${BASE_ENV[@]}" PULSAR_SSH=/bin/false \
  "$LIBRARY" health --json >"$STATE/unavailable.json" || unavailable_rc=$?
[ "$unavailable_rc" -eq 1 ]
python3 -c '
import json,sys
doc=json.load(open(sys.argv[1]))
assert doc["state"] == "unavailable"
assert doc["issues"]
' "$STATE/unavailable.json"

route=$(PULSAR_DOCTOR_SCRIPT="$STATE/doctor" "$REPO_DIR/pulsar" doctor --json)
[ "$route" = "doctor-routed:--json" ]

echo "model-library health shell scenarios: PASS"
