#!/usr/bin/env bash
# Thin public-CLI scenarios for download-receipt home add and verify.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-download-receipt-cli.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
python3 "$REPO_DIR/scripts/testlib/model_library_receipt_fixture.py" "$STATE"
# Profiles are released specs: a first acquisition names a conf-format draft
# (home add --draft); prepare and relocate name the released fixture spec id.
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/testlib/spec_fixture_env.sh"
spec_fixture_env >/dev/null
NANO_DRAFT="$REPO_DIR/scripts/testdata/drafts/nemotron-3-nano-30b-nvfp4.conf"

LIBRARY="$REPO_DIR/scripts/model-library.sh"
BASE_ENV=(
  "PATH=$STATE/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/topology.json"
  "HF_CACHE=$STATE/cache"
  "MODEL_LIBRARY_DIR=$STATE/library"
  "MODEL_LIBRARY_CATALOG=$STATE/library/catalog.json"
  "MOCK_HF_LOG=$STATE/hf.log"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/bin/hf-source-inventory.py"
  "PULSAR_COLD_ROOT="
  "PULSAR_COLD_ARCHIVE_AUTOSTART=0"
)

if env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
    --json --yes \
    >"$STATE/no-revision.out" 2>"$STATE/no-revision.err"; then
  echo "unsealed home add unexpectedly succeeded without --revision" >&2
  exit 1
fi
grep -q -- '--revision' "$STATE/no-revision.err"
grep -q -- 'modern hf' "$STATE/no-revision.err"
[ ! -s "$STATE/hf.log" ]

# The deprecated huggingface-cli command is never an acquisition candidate.
# A managed modern-hf installation remains usable even when the legacy command
# is present on PATH.
LEGACY_STATE="$STATE/legacy-cli"
LEGACY_USER_ROOT="$LEGACY_STATE/operator-root"
python3 "$REPO_DIR/scripts/testlib/model_library_receipt_fixture.py" "$LEGACY_STATE"
mv "$LEGACY_STATE/bin/hf" "$LEGACY_STATE/managed-hf"
cat >"$LEGACY_STATE/bin/huggingface-cli" <<'SH'
#!/usr/bin/env bash
printf '%s\n' invoked >>"$MOCK_LEGACY_HF_LOG"
exit 99
SH
chmod +x "$LEGACY_STATE/bin/huggingface-cli"
touch "$LEGACY_STATE/legacy-hf.log"
LEGACY_ENV=(
  "PATH=$LEGACY_STATE/bin:/usr/bin:/bin"
  "HOME=$LEGACY_USER_ROOT"
  "CLUSTER_TOPOLOGY_FILE=$LEGACY_STATE/topology.json"
  "HF_CACHE=$LEGACY_STATE/cache"
  "MODEL_LIBRARY_DIR=$LEGACY_STATE/library"
  "MODEL_LIBRARY_CATALOG=$LEGACY_STATE/library/catalog.json"
  "MOCK_HF_LOG=$LEGACY_STATE/hf.log"
  "MOCK_LEGACY_HF_LOG=$LEGACY_STATE/legacy-hf.log"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$LEGACY_STATE/bin/hf-source-inventory.py"
  "PULSAR_COLD_ROOT="
  "PULSAR_COLD_ARCHIVE_AUTOSTART=0"
)
if env "${LEGACY_ENV[@]}" "$LIBRARY" home add \
    --draft "$NANO_DRAFT" --revision main --plan --json \
    >"$LEGACY_STATE/legacy-only.out" 2>"$LEGACY_STATE/legacy-only.err"; then
  echo "home add accepted deprecated huggingface-cli" >&2
  exit 1
fi
grep -q 'no eligible serving rank has modern hf' "$LEGACY_STATE/legacy-only.err"
[ ! -s "$LEGACY_STATE/legacy-hf.log" ]

mkdir -p "$LEGACY_USER_ROOT/.hf-cli/venv/bin"
mv "$LEGACY_STATE/managed-hf" "$LEGACY_USER_ROOT/.hf-cli/venv/bin/hf"
env "${LEGACY_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
  --revision main --plan --json >"$LEGACY_STATE/managed-modern-plan.json"
python3 - "$LEGACY_STATE/managed-modern-plan.json" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["kind"] == "pulsar-model-library-download-plan"
assert plan["approval"]["selected_rank"] == 0
PY
[ ! -s "$LEGACY_STATE/legacy-hf.log" ]

# An explicit --node resolves source metadata only on that reviewed rank
# before the read-only plan is shown. This exercises the streamed metadata
# helper without downloading model bytes or asking another rank.
python3 - "$REPO_DIR" "$STATE/remote" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from scripts.testlib import model_library_receipt_fixture as fixture

fixture.write_cli_fixture(pathlib.Path(sys.argv[2]), ranks=2)
PY
mkdir -p "$STATE/remote/home"
REMOTE_ENV=(
  "PATH=$STATE/remote/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/remote/topology.json"
  "HF_CACHE=$STATE/remote/cache"
  "MODEL_LIBRARY_DIR=$STATE/remote/library"
  "MODEL_LIBRARY_CATALOG=$STATE/remote/library/catalog.json"
  "MOCK_HF_LOG=$STATE/remote/hf.log"
  "MOCK_SSH_LOG=$STATE/remote/ssh.log"
  "MOCK_REMOTE_HF_CACHE=$STATE/remote/cache-1"
  "MOCK_REMOTE_HOME=$STATE/remote/home"
  "PULSAR_SSH=$STATE/remote/bin/ssh"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/remote/bin/hf-source-inventory.py"
  "PULSAR_COLD_ROOT="
  "PULSAR_COLD_ARCHIVE_AUTOSTART=0"
)

# No-attachment flows must not perform a remote live-directory probe just
# to discover that download-receipt occupancy authority is absent. A failing
# SSH path therefore still returns the local no-authority result.
no_attachment_probe=$(env \
  "${REMOTE_ENV[@]}" \
  "MODEL_LIBRARY_DIR=$STATE/no-attachment-library" \
  PULSAR_SSH=/bin/false \
  bash -c '
    library_script="$1"
    probe_workdir="$2"
    set -- --help
    source "$library_script" >/dev/null
    load_cluster_topology >/dev/null
    resolve_attached_source_attested_receipt \
      "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4" \
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
      1 node-1 /does/not/exist "$probe_workdir" "no-attachment probe"
  ' _ "$LIBRARY" "$STATE/no-attachment-work")
[ "$no_attachment_probe" = null ]

if ! env "${REMOTE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
    --revision main --node 1 --plan --json \
    >"$STATE/remote/plan.json" 2>"$STATE/remote/plan.err"; then
  echo "remote selected-rank source plan failed" >&2
  sed -n '1,120p' "$STATE/remote/plan.err" >&2
  exit 1
fi
python3 - "$STATE/remote/plan.json" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["approval"]["selected_rank"] == 1
assert plan["source"]["snapshot_revision"] == "a" * 40
PY
[ "$(grep -c 'source-inventory --model-id' "$STATE/remote/hf.log")" -eq 1 ]
grep -q -- '--model-id nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4' \
  "$STATE/remote/ssh.log"
if grep -q 'download nvidia/' "$STATE/remote/hf.log"; then
  echo "remote plan mode downloaded model bytes" >&2
  exit 1
fi

# Automatic placement must treat a metadata failure as making only that
# candidate ineligible. Rank 0 cannot resolve; rank 1 can, so rank 1 is
# selected and no model bytes move.
python3 - "$REPO_DIR" "$STATE/auto-meta" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from scripts.testlib import model_library_receipt_fixture as fixture

fixture.write_cli_fixture(pathlib.Path(sys.argv[2]), ranks=2)
PY
mkdir -p "$STATE/auto-meta/home"
AUTO_ENV=(
  "PATH=$STATE/auto-meta/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/auto-meta/topology.json"
  "HF_CACHE=$STATE/auto-meta/cache"
  "MODEL_LIBRARY_DIR=$STATE/auto-meta/library"
  "MODEL_LIBRARY_CATALOG=$STATE/auto-meta/library/catalog.json"
  "MOCK_HF_LOG=$STATE/auto-meta/hf.log"
  "MOCK_SSH_LOG=$STATE/auto-meta/ssh.log"
  "MOCK_REMOTE_HF_CACHE=$STATE/auto-meta/cache-1"
  "MOCK_REMOTE_HOME=$STATE/auto-meta/home"
  "MOCK_HF_INVENTORY_FAIL_IF_CACHE=$STATE/auto-meta/cache"
  "PULSAR_COLD_ROOT="
  "PULSAR_COLD_ARCHIVE_AUTOSTART=0"
  "PULSAR_SSH=$STATE/auto-meta/bin/ssh"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/auto-meta/bin/hf-source-inventory.py"
)
if ! env "${AUTO_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
    --revision main --plan --json \
    >"$STATE/auto-meta/plan.json" 2>"$STATE/auto-meta/plan.err"; then
  echo "automatic metadata-eligibility plan failed" >&2
  sed -n '1,120p' "$STATE/auto-meta/plan.err" >&2
  exit 1
fi
python3 - "$STATE/auto-meta/plan.json" <<'PY'
import json
import sys

plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["approval"]["selected_rank"] == 1
assert plan["source"]["snapshot_revision"] == "a" * 40
PY
[ "$(grep -c 'source-inventory --model-id' "$STATE/auto-meta/hf.log")" -eq 1 ]
if grep -q 'download nvidia/' "$STATE/auto-meta/hf.log"; then
  echo "automatic metadata-eligibility plan downloaded model bytes" >&2
  exit 1
fi

env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
  --revision main --plan --json \
  >"$STATE/plan.json" 2>"$STATE/plan.err"
python3 - "$STATE/plan.json" "$STATE/plan.err" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["kind"] == "pulsar-model-library-download-plan"
assert plan["source"]["selector"] == "main"
assert plan["source"]["snapshot_revision"] == "a" * 40
assert plan["identity"]["identity_class"] == "download-receipt"
assert plan["approval"]["selected_rank"] == 0
blob = open(sys.argv[1], encoding="utf-8").read()
err = open(sys.argv[2], encoding="utf-8").read()
for banned in ("node-0", "192.0.2.", "topology_id", "fixture-0"):
    assert banned not in blob, banned
    assert banned not in err, banned
PY
if grep -q 'download nvidia/' "$STATE/hf.log"; then
  echo "plan mode downloaded model bytes" >&2
  exit 1
fi
grep -q 'source-inventory --model-id' "$STATE/hf.log"
if grep -Eiq -- '--token|hf_token|HUGGING_FACE_HUB_TOKEN' "$STATE/hf.log"; then
  echo "hf argv leaked a token" >&2
  exit 1
fi

: >"$STATE/hf.log"
if printf 'n\n' | COLUMNS=52 env "${BASE_ENV[@]}" "$LIBRARY" \
    home add --draft "$NANO_DRAFT" --revision main \
    >"$STATE/declined.out" 2>"$STATE/declined.err"; then
  echo "declined download-receipt home add unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'Hugging Face download  PLAN' "$STATE/declined.out"
python3 - "$STATE/declined.out" <<'PY'
import sys
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
assert lines
assert max(map(len, lines)) <= 52, max(lines, key=len)
PY
if grep -q 'download nvidia/' "$STATE/hf.log"; then
  echo "declined plan downloaded model bytes" >&2
  exit 1
fi

: >"$STATE/hf.log"
env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
  --revision main --yes --json \
  >"$STATE/result.json" 2>"$STATE/result.err"
python3 - "$STATE/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["kind"] == "pulsar-model-library-download-result"
assert result["state"] == "published"
assert result["identity_class"] == "download-receipt"
assert result["snapshot_revision"] == "a" * 40
assert len(result["source_digest"]) == 64
assert len(result["approval_id"]) == 64
assert result["catalog_refreshed"] is False
assert "node_id" not in result
assert "cache_root" not in result
blob = open(sys.argv[1], encoding="utf-8").read()
for banned in ("device", "inode", "ctime_ns", "durable_home_path",
               "home-occupancy"):
    assert banned not in blob, banned
PY
grep -q -- 'download nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4' "$STATE/hf.log"
grep -q -- '--revision aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "$STATE/hf.log"
if grep -q -- '--revision main' "$STATE/hf.log"; then
  echo "download used the mutable selector" >&2
  exit 1
fi
if grep -Eiq -- '--token|hf_token' "$STATE/hf.log"; then
  echo "download argv leaked a token" >&2
  exit 1
fi
test -d "$STATE/cache/hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4/snapshots/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
receipts=$(find "$STATE/library/download-receipts" -name '*.json' | wc -l)
[ "$receipts" -eq 1 ]
attachments=$(find "$STATE/library/home-occupancy" -name '*.json' | wc -l)
[ "$attachments" -eq 1 ]

env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
# Receipt-backed preparation must carry tracked zero-byte files through the
# witness/full-verification barrier, not only through source acquisition.
env "${BASE_ENV[@]}" \
  PULSAR_HOT_ROOT="$STATE/hot" \
  PULSAR_HOT_RESERVE_BYTES=0 \
  PULSAR_HOT_BUDGET_BYTES=1000000 \
  "$LIBRARY" prepare "$ONE_NODE_ID" \
  --transport ssh-control --yes >/dev/null
# Prepare fails without occupancy even when a download receipt exists.
occ_backup="$STATE/occ-backup"
mkdir -p "$occ_backup"
mv "$STATE/library/home-occupancy"/*.json "$occ_backup/"
set +e
env "${BASE_ENV[@]}" \
  PULSAR_HOT_ROOT="$STATE/hot" \
  PULSAR_HOT_RESERVE_BYTES=0 \
  PULSAR_HOT_BUDGET_BYTES=1000000 \
  "$LIBRARY" prepare "$ONE_NODE_ID" \
  --transport ssh-control --yes >/dev/null 2>"$STATE/prepare-no-occ.err"
prep_no_occ_rc=$?
set -e
mv "$occ_backup"/*.json "$STATE/library/home-occupancy/"
rmdir "$occ_backup"
if [ "$prep_no_occ_rc" -eq 0 ]; then
  echo "prepare succeeded without occupancy" >&2
  exit 1
fi
if ! grep -q "occupancy is missing for a download receipt" "$STATE/prepare-no-occ.err"; then
  echo "prepare missing-occupancy error was unclear" >&2
  cat "$STATE/prepare-no-occ.err" >&2
  exit 1
fi
# An interrupted writer temp next to the final receipt must not block verify.
python3 - "$STATE/library/download-receipts" <<'PY'
import pathlib
import sys

store = pathlib.Path(sys.argv[1])
receipt = next(store.glob("[0-9a-f]*.json"))
temp = store / f".{receipt.name}.9.0123456789abcdef.tmp"
temp.write_text("{not-a-receipt", encoding="utf-8")
PY
env "${BASE_ENV[@]}" "$LIBRARY" home verify \
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --json >"$STATE/verify.json"
python3 - "$STATE/verify.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["kind"] == "pulsar-model-library-home-verify-result"
assert result["state"] == "verified"
blob = open(sys.argv[1], encoding="utf-8").read()
for banned in ("node_id", "device", "inode", "ctime_ns", "durable_home_path"):
    assert banned not in blob, banned
PY

# Plan/check paths must not detach; detach happens only after --yes and before
# the removal mutation.
python3 - "$REPO_DIR/scripts/model-library.sh" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
check_body = text[text.index("cmd_home_check()") : text.index("cmd_home_remove()")]
assert "detach-current-home" not in check_body
remove_body = text[text.index("cmd_home_remove()") : text.index("copy_ssh_data_host()")]
assert remove_body.index("home removal requires --yes") < remove_body.index(
    "detach-current-home"
)
assert remove_body.index("detach-current-home") < remove_body.index(
    "execute_home_removal_on_rank"
)
PY

# Removing the current attachment unbinds the live home. Matching bytes do not
# restore receipt authority until home relocate rehashes and occupies.
rm -f "$STATE/library/home-occupancy"/*.json
if env "${BASE_ENV[@]}" "$LIBRARY" home verify \
    'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
    >"$STATE/unbound.out" 2>"$STATE/unbound.err"; then
  echo "unbound home verify unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'home relocate' "$STATE/unbound.err"
grep -q 'Do not Hub re-download' "$STATE/unbound.err"
[ "$(find "$STATE/library/download-receipts" -name '*.json' | wc -l)" -eq 1 ]
[ "$(find "$STATE/library/home-occupancy" -name '*.json' | wc -l)" -eq 0 ]
env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
env "${BASE_ENV[@]}" "$LIBRARY" home relocate "$ONE_NODE_ID" \
  --node 0 --yes --json >"$STATE/relocate.json"
python3 - "$STATE/relocate.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "attached"
assert "node_id" not in result
PY
[ "$(find "$STATE/library/home-occupancy" -name '*.json' | wc -l)" -eq 1 ]
env "${BASE_ENV[@]}" "$LIBRARY" home verify \
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --json >"$STATE/verify-after-relocate.json"
python3 - "$STATE/verify-after-relocate.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "verified"
PY

# Reacquisition after supported-style removal writes a new attachment and keeps
# the earlier receipt.
rm -rf "$STATE/cache/hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
: >"$STATE/hf.log"
env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
  --revision main --yes --json \
  >"$STATE/readd.json" 2>"$STATE/readd.err"
python3 - "$STATE/readd.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "published"
assert "node_id" not in result
assert "ctime_ns" not in open(sys.argv[1], encoding="utf-8").read()
PY
[ "$(find "$STATE/library/download-receipts" -name '*.json' | wc -l)" -eq 1 ]
[ "$(find "$STATE/library/home-occupancy" -name '*.json' | wc -l)" -eq 1 ]
env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
env "${BASE_ENV[@]}" "$LIBRARY" home verify \
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --json >"$STATE/readd-verify.json"
python3 - "$STATE/readd-verify.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "verified"
PY

if env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
    --revision main --yes \
    >"$STATE/occupied.out" 2>"$STATE/occupied.err"; then
  echo "download-receipt home add overwrote an existing home" >&2
  exit 1
fi
grep -q 'already exists' "$STATE/occupied.err"

# Sealed exact-commit home add is retired (ADR 0012).
if env "${BASE_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" --yes --json \
    >"$STATE/sealed.json" 2>"$STATE/sealed.err"; then
  echo "home add without --revision unexpectedly succeeded" >&2
  exit 1
fi
grep -q -- 'pass --revision SELECTOR' "$STATE/sealed.err"
grep -q -- 'ADR 0012' "$STATE/sealed.err"

mkdir -p "$STATE/cold"
ARCHIVE_ENV=(
  "${BASE_ENV[@]}"
  "PULSAR_COLD_ROOT=$STATE/cold"
  "PULSAR_COLD_ARCHIVE_AUTOSTART=0"
)
receipt_id=$(python3 -c 'import json,sys,pathlib; p=next(pathlib.Path(sys.argv[1]).glob("*.json")); print(json.loads(p.read_text())["receipt_id"])' "$STATE/library/download-receipts")
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home archive run --receipt "$receipt_id" --yes --json \
  >"$STATE/archive.json"
python3 - "$STATE/archive.json" <<'PY'
import json, sys
presence = json.load(open(sys.argv[1], encoding="utf-8"))
assert presence["state"] == "complete"
assert presence["kind"] == "pulsar-model-library-cold-archive-presence"
PY
receipt_replica="$STATE/cold/pulsar-control/download-receipts/${receipt_id}.json"
test -f "$receipt_replica"
[ "$(stat -c '%a' "$STATE/cold/pulsar-control")" = 700 ]
[ "$(stat -c '%a' "$STATE/cold/pulsar-control/download-receipts")" = 700 ]
[ "$(stat -c '%a' "$receipt_replica")" = 600 ]
test ! -e "$STATE/cold/pulsar-receipts/${receipt_id}/receipt.json"
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home receipt status \
  --receipt "$receipt_id" --json >"$STATE/receipt-replica-status.json"
python3 - "$STATE/receipt-replica-status.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "verified"
PY
COLUMNS=48 env "${ARCHIVE_ENV[@]}" "$LIBRARY" home receipt status \
  --receipt "$receipt_id" >"$STATE/receipt-replica-status.txt"
grep -q 'Receipt control-state replica  verified' \
  "$STATE/receipt-replica-status.txt"
python3 - "$STATE/receipt-replica-status.txt" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
assert max(map(len, lines)) <= 48
PY
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home archive status "$receipt_id" \
  >"$STATE/archive-status.json"
python3 - "$STATE/archive-status.json" <<'PY'
import json, sys
job = json.load(open(sys.argv[1], encoding="utf-8"))
assert job["state"] == "complete"
PY
rm -rf "$STATE/cache/hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
rm -f "$STATE/library/home-occupancy"/*.json
rm -f "$STATE/library/download-receipts/${receipt_id}.json"
rm -f "$STATE/library/catalog.json"
if env "${ARCHIVE_ENV[@]}" "$LIBRARY" home restore "$receipt_id" \
    --node 0 --yes --json >"$STATE/restore-missing.json" 2>"$STATE/restore-missing.err"; then
  echo "home restore recovered a missing controller receipt implicitly" >&2
  exit 1
fi
grep -q 'home receipt recover' "$STATE/restore-missing.err"
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home receipt recover \
  --receipt "$receipt_id" --yes --json >"$STATE/receipt-recover.json"
python3 - "$STATE/receipt-recover.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "recovered"
PY
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home restore "$receipt_id" --node 0 --yes --json \
  >"$STATE/restore.json"
python3 - "$STATE/restore.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "attached"
PY
test -d "$STATE/cache/hub/models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4/snapshots/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
if find "$STATE/cache/hub" -maxdepth 1 -name '.pulsar-acquire-*' | grep -q .; then
  echo "home restore left private staging behind" >&2
  exit 1
fi
env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
env "${ARCHIVE_ENV[@]}" "$LIBRARY" home verify \
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --json >"$STATE/restore-verify.json"
python3 - "$STATE/restore-verify.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["state"] == "verified"
PY

# Archive must not take the exclusive occupancy lock: a held exclusive lock
# must not delay home archive run (P1).
rm -rf "$STATE/cold/pulsar-receipts"
mkdir -p "$STATE/library"
: >"$STATE/library/lifecycle.lock"
flock -x "$STATE/library/lifecycle.lock" sleep 8 &
exclusive_holder=$!
sleep 0.1
if ! kill -0 "$exclusive_holder" 2>/dev/null; then
  echo "failed to hold exclusive lifecycle lock for archive test" >&2
  exit 1
fi
archive_start=$(date +%s)
if ! env "${ARCHIVE_ENV[@]}" \
    PULSAR_MODEL_LIBRARY_LOCK_TIMEOUT_SECONDS=3 \
    "$LIBRARY" home archive run --receipt "$receipt_id" --yes --json \
    >"$STATE/archive-under-exclusive.json" 2>"$STATE/archive-under-exclusive.err"; then
  kill "$exclusive_holder" 2>/dev/null || true
  wait "$exclusive_holder" 2>/dev/null || true
  echo "archive run failed while exclusive occupancy lock was held" >&2
  cat "$STATE/archive-under-exclusive.err" >&2
  exit 1
fi
archive_elapsed=$(( $(date +%s) - archive_start ))
kill "$exclusive_holder" 2>/dev/null || true
wait "$exclusive_holder" 2>/dev/null || true
if [ "$archive_elapsed" -ge 3 ]; then
  echo "archive run blocked on exclusive lifecycle lock (${archive_elapsed}s)" >&2
  exit 1
fi

# Autostart must complete without inheriting home add's exclusive lock.
python3 - "$REPO_DIR" "$STATE/autostart" <<'PY'
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from scripts.testlib import model_library_receipt_fixture as fixture
fixture.write_cli_fixture(pathlib.Path(sys.argv[2]), ranks=1)
PY
mkdir -p "$STATE/autostart/cold" "$STATE/autostart/home"
AUTOSTART_ENV=(
  "PATH=$STATE/autostart/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/autostart/topology.json"
  "HF_CACHE=$STATE/autostart/cache"
  "MODEL_LIBRARY_DIR=$STATE/autostart/library"
  "MODEL_LIBRARY_CATALOG=$STATE/autostart/library/catalog.json"
  "MOCK_HF_LOG=$STATE/autostart/hf.log"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/autostart/bin/hf-source-inventory.py"
  "PULSAR_COLD_ROOT=$STATE/autostart/cold"
  "PULSAR_COLD_ARCHIVE_AUTOSTART=1"
  "PULSAR_MODEL_LIBRARY_LOCK_TIMEOUT_SECONDS=3"
)
add_start=$(date +%s)
env "${AUTOSTART_ENV[@]}" "$LIBRARY" home add --draft "$NANO_DRAFT" \
  --revision main --yes --json \
  >"$STATE/autostart/add.json" 2>"$STATE/autostart/add.err"
add_elapsed=$(( $(date +%s) - add_start ))
if [ "$add_elapsed" -ge 3 ]; then
  echo "home add blocked on autostart archive lock (${add_elapsed}s)" >&2
  cat "$STATE/autostart/add.err" >&2
  exit 1
fi
autostart_receipt=$(python3 -c 'import json,sys,pathlib; p=next(pathlib.Path(sys.argv[1]).glob("*.json")); print(json.loads(p.read_text())["receipt_id"])' "$STATE/autostart/library/download-receipts")
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  state=$(env "${AUTOSTART_ENV[@]}" "$LIBRARY" home archive status "$autostart_receipt" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d or {}).get("state") or "")')
  if [ "$state" = complete ]; then
    ok=1
    break
  fi
  sleep 0.2
done
if [ "$ok" != 1 ]; then
  echo "autostart archive stayed $state (expected complete)" >&2
  ls -la "$STATE/autostart/library/cold-archive-logs" 2>/dev/null || true
  cat "$STATE/autostart/library/cold-archive-logs/"*.log 2>/dev/null || true
  exit 1
fi
test -f "$STATE/autostart/cold/pulsar-control/download-receipts/${autostart_receipt}.json"

"$LIBRARY" --help | grep -q 'home add <spec_id>|--draft'
"$LIBRARY" --help | grep -q -- '--revision SELECTOR \[--node RANK|NODE_ID\]'
"$LIBRARY" --help | grep -q 'target-local modern hf'
! "$LIBRARY" --help | grep -q 'huggingface-cli'
"$LIBRARY" --help | grep -q 'home verify'
"$LIBRARY" --help | grep -q 'home archive'
"$LIBRARY" --help | grep -q 'home receipt recover'
python3 - "$REPO_DIR/scripts/model-library.sh" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
body = text[text.index("cmd_home_verify()") : text.index("cmd_home_check()")]
assert "EXPECTED_MODEL_SEAL" not in body
assert "reviewed expected manifest" not in body
assert "download receipt" in body
assert "--required-content-bytes 1" not in body
assert "create-owned-hub-staging" in body
assert "recheck-home-acquisition-absence" in body
assert "publish-owned-hub-staging" in body
PY
echo "model-library download-receipt CLI scenarios: PASS"
