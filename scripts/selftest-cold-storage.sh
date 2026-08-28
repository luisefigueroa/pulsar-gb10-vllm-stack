#!/usr/bin/env bash
# Deterministic cold recovery storage configuration suite.
# No Docker, SSH, NFS, GPU, Hub, or physical archive worker.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pass=0
fail=0

assert_eq() {
  local got="$1" want="$2" msg="$3"
  if [ "$got" = "$want" ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (got='$got' want='$want')" >&2
    fail=$((fail + 1))
  fi
}

assert_true() {
  local msg="$1"
  shift
  if "$@"; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg" >&2
    fail=$((fail + 1))
  fi
}

assert_file_contains() {
  local f="$1" pat="$2" msg="$3"
  if grep -qE "$pat" "$f" 2>/dev/null; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (pattern /$pat/ not in $f)" >&2
    fail=$((fail + 1))
  fi
}

assert_file_not_contains() {
  local f="$1" pat="$2" msg="$3"
  if grep -qE "$pat" "$f" 2>/dev/null; then
    echo "FAIL $msg (unexpected /$pat/ in $f)" >&2
    fail=$((fail + 1))
  else
    echo "OK   $msg"
    pass=$((pass + 1))
  fi
}

cd "$REPO_DIR"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-cold-storage-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
mkdir -p "$STATE/library" "$STATE/hot" "$STATE/hf" "$STATE/cold" "$STATE/bin" "$STATE/logs"
chmod 700 "$STATE/library" "$STATE/hot" "$STATE/cold"
printf '' >"$STATE/env"
chmod 600 "$STATE/env"

export PULSAR_SELFTEST=1
export PULSAR_COLD_STORAGE_TEST_DOTENV="$STATE/env"
export MODEL_LIBRARY_DIR="$STATE/library"
export PULSAR_HOT_ROOT="$STATE/hot"
export HF_CACHE="$STATE/hf"
export GUM=0
export NO_COLOR=1
export TERM=dumb
unset PULSAR_COLD_ROOT MODELS_NFS || true
chmod +x "$REPO_DIR/scripts/configure-cold-storage.sh" "$REPO_DIR/pulsar"

CLI="$REPO_DIR/scripts/configure-cold-storage.sh"
PY="$REPO_DIR/scripts/model_library_cold_storage.py"

echo "=== direct CLI ==="
set +e
out=$("$CLI" show --json)
rc=$?
set -e
assert_eq "$rc" "0" "show not-configured → 0"
echo "$out" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["state"]=="not-configured"'
assert_eq "$?" "0" "show JSON state is not-configured"

set +e
"$CLI" set --path "$STATE/cold" >/dev/null 2>"$STATE/logs/set-noyes.err"
rc=$?
set -e
assert_eq "$rc" "2" "set without --yes → 2"
assert_file_contains "$STATE/logs/set-noyes.err" "requires --yes" "missing --yes message"

set +e
"$CLI" set --path "$STATE/cold" --yes --json >"$STATE/logs/set.json"
rc=$?
set -e
assert_eq "$rc" "0" "set existing path --yes → 0"
python3 - "$STATE/logs/set.json" <<'PY'
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["kind"] == "pulsar-model-library-cold-storage-mutation-result"
assert document["status"]["state"] == "configured-available"
assert document["status"]["persisted"]["value"].endswith("/cold")
assert document["plan"]["action"] == "set-new"
PY
assert_eq "$?" "0" "set JSON reports configured-available"
assert_file_contains "$STATE/env" "PULSAR_COLD_ROOT=" "persisted preferred key"

missing="$STATE/missing-cold"
set +e
"$CLI" plan --path "$missing" --json >"$STATE/logs/plan-missing.json"
rc=$?
set -e
assert_eq "$rc" "1" "plan missing path → 1"
assert_true "plan does not create missing path" test ! -e "$missing"

set +e
"$CLI" disable --yes --json >"$STATE/logs/disable.json"
rc=$?
set -e
assert_eq "$rc" "0" "disable --yes → 0"
python3 - "$STATE/logs/disable.json" "$STATE/env" <<'PY'
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["status"]["state"] == "disabled"
raw = open(sys.argv[2], "rb").read()
assert b"PULSAR_COLD_ROOT=''" in raw or b'PULSAR_COLD_ROOT=""' in raw
PY
assert_eq "$?" "0" "disable persists empty assignment"

echo "=== MODELS_NFS is not a live recovery root ==="
set +e
MODELS_NFS=/mnt/Models env -u PULSAR_COLD_ROOT \
  python3 "$PY" show --json >"$STATE/logs/nfs.json"
rc=$?
set -e
assert_eq "$rc" "0" "MODELS_NFS set still show-exit 0"
python3 - "$STATE/logs/nfs.json" <<'PY'
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["state"] in {"disabled", "not-configured"}
assert document["effective"].get("value") in (None, "")
assert "/mnt/Models" not in json.dumps(document.get("path"))
PY
assert_eq "$?" "0" "MODELS_NFS does not become effective cold root"
assert_true "model-library legacy fill requires an invocation-local root" \
  grep -q 'Legacy fill commands accept only an invocation-local --cold-root' \
  "$REPO_DIR/scripts/model-library.sh"

echo "=== first-use plain ==="
printf '' >"$STATE/env"
chmod 600 "$STATE/env"
set +e
printf '%s' $'3\n' | "$CLI" first-use >"$STATE/logs/first-notnow.out" 2>"$STATE/logs/first-notnow.err"
rc=$?
set -e
assert_eq "$rc" "0" "first-use not now → 0"
cat "$STATE/logs/first-notnow.out" "$STATE/logs/first-notnow.err" >"$STATE/logs/first-notnow.combined"
assert_file_contains "$STATE/logs/first-notnow.combined" "not now|no explicit persisted" \
  "first-use not-now messaging"
python3 - "$STATE/env" <<'PY'
import sys
raw = open(sys.argv[1], "rb").read()
assert b"PULSAR_COLD_ROOT" not in raw
PY
assert_eq "$?" "0" "not now writes nothing"

set +e
printf '%s' '' | "$CLI" first-use >"$STATE/logs/first-eof.out" 2>"$STATE/logs/first-eof.err"
rc=$?
set -e
assert_eq "$rc" "0" "first-use EOF → 0"
python3 - "$STATE/env" <<'PY'
import sys
raw = open(sys.argv[1], "rb").read()
assert b"PULSAR_COLD_ROOT" not in raw
PY
assert_eq "$?" "0" "EOF writes nothing"

set +e
printf '%s' $'2\ny\n' | "$CLI" first-use >"$STATE/logs/first-disable.out" 2>"$STATE/logs/first-disable.err"
rc=$?
set -e
assert_eq "$rc" "0" "first-use disable → 0"
assert_file_contains "$STATE/env" "PULSAR_COLD_ROOT=''" "first-use disable persists empty"

printf '' >"$STATE/env"
chmod 600 "$STATE/env"
set +e
printf '%s' $'1\n'"$STATE/cold"$'\ny\n' | "$CLI" first-use \
  >"$STATE/logs/first-set.out" 2>"$STATE/logs/first-set.err"
rc=$?
set -e
assert_eq "$rc" "0" "first-use configure existing path → 0"
assert_file_contains "$STATE/env" "PULSAR_COLD_ROOT=" "first-use set writes path"
assert_file_not_contains "$STATE/logs/first-set.err" \
  "plan document fields are invalid" \
  "first-use renders the exact serialized plan"

echo "=== first-use Gum ==="
cat >"$STATE/bin/fake-gum" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
case "$cmd" in
  choose)
    printf '%s\n' "${FAKE_GUM_CHOICE:-Not now}"
    exit 0
    ;;
  confirm)
    exit 0
    ;;
  input)
    if IFS= read -r line; then
      printf '%s\n' "$line"
    else
      printf '%s\n' "/tmp/unused"
    fi
    exit 0
    ;;
  --version|version)
    echo "gum version v0.0.0-fake"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
SH
chmod +x "$STATE/bin/fake-gum"
printf '' >"$STATE/env"
chmod 600 "$STATE/env"
set +e
env \
  GUM=1 NO_COLOR= TERM=xterm-256color PULSAR_FORCE_GUM=1 \
  GUM_BIN="$STATE/bin/fake-gum" FAKE_GUM_CHOICE="Not now" \
  "$CLI" first-use \
  >"$STATE/logs/gum-notnow.out" 2>"$STATE/logs/gum-notnow.err"
rc=$?
set -e
assert_eq "$rc" "0" "Gum first-use not now → 0"
python3 - "$STATE/env" <<'PY'
import sys
raw = open(sys.argv[1], "rb").read()
assert b"PULSAR_COLD_ROOT" not in raw
PY
assert_eq "$?" "0" "Gum not now writes nothing"

echo "=== archive-jobs and one-job retry intercept ==="
python3 - "$STATE/library" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from scripts import model_library_cold_archive as cold_archive
library = Path(sys.argv[1])
cold_archive.write_cold_archive_job(
    library,
    {
        "schema_version": 1,
        "kind": cold_archive.COLD_ARCHIVE_JOB_KIND,
        "receipt_id": "f" * 64,
        "model_id": "Org/Model",
        "snapshot_revision": "c" * 40,
        "state": "running",
        "detail": "running fixture",
    },
)
PY
assert_eq "$?" "0" "wrote running job fixture"
set +e
"$CLI" archive-jobs --json >"$STATE/logs/jobs.json"
rc=$?
set -e
assert_eq "$rc" "0" "archive-jobs read-only → 0"
python3 - "$STATE/logs/jobs.json" <<'PY'
import json, sys
document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["count"] == 1
assert document["jobs"][0]["receipt_id"] == "f" * 64
assert document["jobs"][0]["retry_eligible"] is False
PY
assert_eq "$?" "0" "running job is listed and not retry-eligible"

cat >"$STATE/bin/archive-run" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${STATE_DIR}/logs/archive-run.log"
exit 0
SH
chmod +x "$STATE/bin/archive-run"
: >"$STATE/logs/archive-run.log"
set +e
printf '%s' $'1\n' | env STATE_DIR="$STATE" \
  PULSAR_COLD_STORAGE_ARCHIVE_RUN_CMD="$STATE/bin/archive-run" "$CLI" inspect \
  >"$STATE/logs/inspect.out" 2>"$STATE/logs/inspect.err"
rc=$?
set -e
assert_eq "$rc" "0" "inspect ineligible jobs → 0"
assert_true "inspect did not start archive run" \
  test ! -s "$STATE/logs/archive-run.log"

python3 - "$STATE/library" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from scripts import model_library_cold_archive as cold_archive
library = Path(sys.argv[1])
cold_archive.write_cold_archive_job(
    library,
    {
        "schema_version": 1,
        "kind": cold_archive.COLD_ARCHIVE_JOB_KIND,
        "receipt_id": "e" * 64,
        "model_id": "Org/Model",
        "snapshot_revision": "c" * 40,
        "state": "pending",
        "detail": "pending fixture",
    },
)
PY
# pending without occupancy is not eligible; confirm intercept stays unused
: >"$STATE/logs/archive-run.log"
set +e
printf '%s' $'1\n' | env STATE_DIR="$STATE" \
  PULSAR_COLD_STORAGE_ARCHIVE_RUN_CMD="$STATE/bin/archive-run" \
  "$CLI" inspect >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "inspect pending without occupancy → 0"
assert_true "pending without occupancy does not run archive" \
  test ! -s "$STATE/logs/archive-run.log"

cat >"$STATE/bin/fake-cold-storage-py" <<'PY'
#!/usr/bin/env python3
import json, sys
receipt = "d" * 64
command = sys.argv[1]
json_mode = "--json" in sys.argv
if command == "archive-jobs":
    document = {
        "schema_version": 1,
        "kind": "pulsar-model-library-cold-storage-archive-jobs",
        "count": 1,
        "jobs": [{
            "receipt_id": receipt,
            "receipt_id_prefix": receipt[:12],
            "model_id": "Org/Model",
            "snapshot_revision": "c" * 40,
            "state": "failed",
            "detail": "fixture",
            "retry_eligible": True,
            "reason": "fixture can be retried",
        }],
    }
    if json_mode:
        print(json.dumps(document))
    else:
        print("Archive jobs\nReceipt      " + receipt[:12] + "\nState        failed")
elif command == "retry-plan":
    document = {
        "schema_version": 1,
        "kind": "pulsar-model-library-cold-storage-retry-eligibility",
        "receipt_id": receipt,
        "model_id": "Org/Model",
        "snapshot_revision": "c" * 40,
        "state": "failed",
        "eligible": True,
        "reason": "fixture can be retried",
        "command": ["scripts/model-library.sh", "home", "archive", "run", "--receipt", receipt, "--yes"],
    }
    if json_mode:
        print(json.dumps(document))
    else:
        print("Retry one archive job\nEligible     yes")
else:
    raise SystemExit(2)
PY
chmod +x "$STATE/bin/fake-cold-storage-py"
: >"$STATE/logs/archive-run.log"
set +e
printf '%s' $'2\ny\n' | env STATE_DIR="$STATE" \
  PULSAR_COLD_STORAGE_PY="$STATE/bin/fake-cold-storage-py" \
  PULSAR_COLD_STORAGE_ARCHIVE_RUN_CMD="$STATE/bin/archive-run" \
  "$CLI" inspect >"$STATE/logs/retry.out" 2>"$STATE/logs/retry.err"
rc=$?
set -e
assert_eq "$rc" "0" "one eligible archive retry → 0"
assert_file_contains "$STATE/logs/archive-run.log" \
  "^home archive run --receipt d{64} --yes$" \
  "eligible retry delegates exactly once to owning command"
assert_eq "$(wc -l <"$STATE/logs/archive-run.log" | tr -d ' ')" "1" \
  "eligible retry invokes exactly one archive job"

echo "=== legacy fill paths require explicit root ==="
set +e
"$REPO_DIR/scripts/model-library.sh" cold scan --json \
  >"$STATE/logs/cold-scan.out" 2>"$STATE/logs/cold-scan.err"
rc=$?
set -e
assert_eq "$rc" "1" "cold scan without --cold-root fails"
assert_file_contains "$STATE/logs/cold-scan.err" "explicit --cold-root" \
  "cold scan names explicit root requirement"
assert_true "legacy --root alias is not accepted" \
  bash -c '! grep -Fq -- "--root|--cold-root" "$1"' _ \
  "$REPO_DIR/scripts/model-library.sh"

echo "=== home preserves process-vs-dotenv provenance ==="
cat >"$STATE/bin/cold-home-fixture" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [ -n "${PULSAR_COLD_ROOT+x}" ]; then
  observed="set:${PULSAR_COLD_ROOT}"
else
  observed="unset"
fi
printf '%s\t%s\n' "${1:-}" "$observed" >>"$HOME_COLD_LOG"
exit 0
SH
chmod +x "$STATE/bin/cold-home-fixture"
printf "PULSAR_COLD_ROOT='%s'\n" "$STATE/cold" >"$STATE/env"
chmod 600 "$STATE/env"
: >"$STATE/logs/home-cold.log"
set +e
printf '%s' $'8\n' | env -u PULSAR_COLD_ROOT \
  PULSAR_SELFTEST=1 PULSAR_COLD_STORAGE_TEST_DOTENV="$STATE/env" \
  HOME_COLD_STORAGE_CMD="$STATE/bin/cold-home-fixture" \
  HOME_COLD_LOG="$STATE/logs/home-cold.log" GUM=0 NO_COLOR=1 TERM=dumb \
  "$REPO_DIR/scripts/home.sh" >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "home with persisted cold root exits cleanly"
assert_file_contains "$STATE/logs/home-cold.log" $'^first-use\tunset$' \
  "home does not mislabel dotenv value as process override"

: >"$STATE/logs/home-cold.log"
set +e
printf '%s' $'8\n' | env PULSAR_COLD_ROOT="$STATE/hot-override" \
  PULSAR_SELFTEST=1 PULSAR_COLD_STORAGE_TEST_DOTENV="$STATE/env" \
  HOME_COLD_STORAGE_CMD="$STATE/bin/cold-home-fixture" \
  HOME_COLD_LOG="$STATE/logs/home-cold.log" GUM=0 NO_COLOR=1 TERM=dumb \
  "$REPO_DIR/scripts/home.sh" >/dev/null 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "home with process override exits cleanly"
assert_file_contains "$STATE/logs/home-cold.log" \
  $'^first-use\tset:.*hot-override$' \
  "home preserves genuine process override"

echo "=== 40-column human output ==="
set +e
COLUMNS=40 NO_COLOR=1 TERM=dumb "$CLI" show >"$STATE/logs/show-40.out"
rc=$?
set -e
assert_eq "$rc" "0" "40-column show → 0"
python3 - "$STATE/logs/show-40.out" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
assert "\x1b[" not in text
for line in text.splitlines():
    assert len(line) <= 40, repr(line)
assert "Pulsar can verify path safety" in text
PY
assert_eq "$?" "0" "40-column/no-color human output"

echo "=== dispatcher ==="
set +e
"$REPO_DIR/pulsar" configure >/dev/null 2>"$STATE/logs/configure-bare.err"
rc=$?
set -e
assert_eq "$rc" "2" "./pulsar configure without topic → 2"
assert_file_contains "$STATE/logs/configure-bare.err" "configure cold-storage" \
  "unsupported configure topic names the surface"

echo "=============================="
echo "cold-storage selftest: pass=$pass fail=$fail"
if [ "$fail" -ne 0 ]; then
  exit 1
fi
exit 0
