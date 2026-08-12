#!/usr/bin/env bash
# Thin public-CLI scenarios for model-library health and legacy-hot repair.
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
assert all(item["repairable"] for item in legacy)
encoded = json.dumps(report)
assert sys.argv[2] not in encoded
assert "fixture-owner-id" not in encoded
assert "fixture-client-id" not in encoded
PY

repair_id=$(python3 -c '
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
print(next(item["repair_id"] for item in doc["hot_instances"] if item["rank"] == 0))
' "$STATE/health.json")
remote_repair_id=$(python3 -c '
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
print(next(item["repair_id"] for item in doc["hot_instances"] if item["rank"] == 1))
' "$STATE/health.json")
env "${BASE_ENV[@]}" "$LIBRARY" hot legacy check "$repair_id" --json \
  >"$STATE/check.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["eligible"]' \
  "$STATE/check.json"

if env "${BASE_ENV[@]}" "$LIBRARY" hot legacy remove "$repair_id" \
    >"$STATE/no-confirm.out" 2>"$STATE/no-confirm.err"; then
  echo "legacy removal unexpectedly succeeded without --yes" >&2
  exit 1
fi
[ -d "$STATE/hot-owner/fixture-health-topology/content" ]
grep -q -- '--yes' "$STATE/no-confirm.err"

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

env "${BASE_ENV[@]}" "$LIBRARY" hot legacy remove "$repair_id" --yes \
  --json >"$STATE/removed.json"
python3 -c '
import json,sys
doc=json.load(open(sys.argv[1]))
assert doc["state"] == "removed"
assert doc["rank"] == 0
' "$STATE/removed.json"
[ ! -e "$STATE/hot-owner/fixture-health-topology/content" ]
[ -d "$STATE/hot-owner/fixture-health-topology/sibling" ]
[ "$(cat "$STATE/external/sentinel")" = preserve ]

env "${BASE_ENV[@]}" "$LIBRARY" hot legacy remove \
  "$remote_repair_id" --yes --json >"$STATE/removed-remote.json"
python3 -c '
import json,sys
doc=json.load(open(sys.argv[1]))
assert doc["state"] == "removed"
assert doc["rank"] == 1
' "$STATE/removed-remote.json"
[ ! -e "$STATE/hot-client/fixture-health-topology/content" ]
[ -d "$STATE/hot-client/fixture-health-topology/sibling" ]

route=$(PULSAR_DOCTOR_SCRIPT="$STATE/doctor" "$REPO_DIR/pulsar" doctor --json)
[ "$route" = "doctor-routed:--json" ]

echo "model-library health shell scenarios: PASS"
