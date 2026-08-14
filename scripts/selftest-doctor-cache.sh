#!/usr/bin/env bash
# Doctor must inspect Hugging Face cache readiness without creating or changing it.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-doctor-cache.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

python3 "$REPO_DIR/scripts/testlib/doctor_cache_fixture.py" \
  "$STATE/fixture" "$REPO_DIR/scripts/doctor.sh"
DOCTOR="$STATE/fixture/scripts/doctor.sh"
BASE_ENV=(
  "PATH=$STATE/fixture/bin:/usr/bin:/bin"
  "MODELS_NFS=$STATE/fixture/models-nfs"
  "GUM=0"
  "NO_COLOR=1"
  "TERM=dumb"
)

run_json() {
  local cache="$1" output="$2" expected_rc="$3" rc=0
  env "${BASE_ENV[@]}" HF_CACHE="$cache" "$DOCTOR" --json >"$output" \
    || rc=$?
  [ "$rc" -eq "$expected_rc" ] || {
    echo "doctor returned $rc, expected $expected_rc" >&2
    return 1
  }
}

assert_cache_row() {
  local report="$1" level="$2" text="$3"
  python3 - "$report" "$level" "$text" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
rows = [row for row in report["checks"] if row["id"] == "hf_cache"]
assert len(rows) == 1, rows
assert rows[0]["level"] == sys.argv[2], rows[0]
assert sys.argv[3] in rows[0]["message"], rows[0]
PY
}

missing="$STATE/missing-parent/cache"
run_json "$missing" "$STATE/missing.json" 0
[ ! -e "$STATE/missing-parent" ]
assert_cache_row "$STATE/missing.json" warn "HF_CACHE missing"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["result"] == "pass_with_warnings"' \
  "$STATE/missing.json"
env "${BASE_ENV[@]}" HF_CACHE="$missing" "$DOCTOR" \
  >"$STATE/missing.human"
grep -Fq "HF_CACHE missing" "$STATE/missing.human"
[ ! -e "$STATE/missing-parent" ]

ancestor_file="$STATE/ancestor-file"
printf 'preserve\n' >"$ancestor_file"
run_json "$ancestor_file/cache" "$STATE/ancestor-file.json" 1
assert_cache_row "$STATE/ancestor-file.json" fail \
  "nearest existing path is not a writable, searchable directory"
[ "$(cat "$ancestor_file")" = preserve ]

unwritable_ancestor="$STATE/unwritable-ancestor"
mkdir "$unwritable_ancestor"
chmod 500 "$unwritable_ancestor"
if [ ! -w "$unwritable_ancestor" ]; then
  run_json "$unwritable_ancestor/cache" "$STATE/unwritable-ancestor.json" 1
  assert_cache_row "$STATE/unwritable-ancestor.json" fail \
    "nearest existing path is not a writable, searchable directory"
  [ ! -e "$unwritable_ancestor/cache" ]
fi
chmod 700 "$unwritable_ancestor"

unsearchable_ancestor="$STATE/unsearchable-ancestor"
mkdir "$unsearchable_ancestor"
chmod 600 "$unsearchable_ancestor"
if [ ! -x "$unsearchable_ancestor" ]; then
  run_json "$unsearchable_ancestor/cache" "$STATE/unsearchable-ancestor.json" 1
  assert_cache_row "$STATE/unsearchable-ancestor.json" fail \
    "nearest existing path is not a writable, searchable directory"
  [ ! -e "$unsearchable_ancestor/cache" ]
fi
chmod 700 "$unsearchable_ancestor"

ready="$STATE/ready"
mkdir "$ready"
run_json "$ready" "$STATE/ready.json" 0
assert_cache_row "$STATE/ready.json" ok "GiB free"
[ -d "$ready" ]

wrong_type="$STATE/cache-file"
printf 'preserve\n' >"$wrong_type"
run_json "$wrong_type" "$STATE/wrong-type.json" 1
assert_cache_row "$STATE/wrong-type.json" fail "not a directory"
[ "$(cat "$wrong_type")" = preserve ]

unwritable="$STATE/unwritable"
mkdir "$unwritable"
chmod 500 "$unwritable"
if [ ! -w "$unwritable" ]; then
  run_json "$unwritable" "$STATE/unwritable.json" 1
  assert_cache_row "$STATE/unwritable.json" fail "read, write, and directory access"
fi

link="$STATE/cache-link"
ln -s "$ready" "$link"
run_json "$link" "$STATE/link.json" 0
assert_cache_row "$STATE/link.json" ok "GiB free"
[ -L "$link" ]

echo "doctor Hugging Face cache scenarios: PASS"
