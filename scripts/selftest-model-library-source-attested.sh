#!/usr/bin/env bash
# Thin public-CLI scenarios for source-attested home add and verify.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-source-attested-cli.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
python3 "$REPO_DIR/scripts/testlib/model_library_source_attested_fixture.py" "$STATE"

LIBRARY="$REPO_DIR/scripts/model-library.sh"
BASE_ENV=(
  "PATH=$STATE/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/topology.json"
  "HF_CACHE=$STATE/cache"
  "MODEL_LIBRARY_DIR=$STATE/library"
  "MODEL_LIBRARY_CATALOG=$STATE/library/catalog.json"
  "MOCK_HF_LOG=$STATE/hf.log"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/bin/hf-source-inventory.py"
)

if env "${BASE_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
    --json --yes \
    >"$STATE/no-revision.out" 2>"$STATE/no-revision.err"; then
  echo "unsealed home add unexpectedly succeeded without --revision" >&2
  exit 1
fi
grep -q -- '--revision' "$STATE/no-revision.err"
[ ! -s "$STATE/hf.log" ]

# An explicit --node resolves source metadata only on that reviewed rank
# before the read-only plan is shown. This exercises the streamed metadata
# helper without downloading model bytes or asking another rank.
python3 - "$REPO_DIR" "$STATE/remote" <<'PY'
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from scripts.testlib import model_library_source_attested_fixture as fixture

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
)
if ! env "${REMOTE_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
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
from scripts.testlib import model_library_source_attested_fixture as fixture

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
  "PULSAR_SSH=$STATE/auto-meta/bin/ssh"
  "PULSAR_HF_SOURCE_INVENTORY_PY=$STATE/auto-meta/bin/hf-source-inventory.py"
)
if ! env "${AUTO_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
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

env "${BASE_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
  --revision main --plan --json \
  >"$STATE/plan.json" 2>"$STATE/plan.err"
python3 - "$STATE/plan.json" "$STATE/plan.err" <<'PY'
import json, sys
plan = json.load(open(sys.argv[1], encoding="utf-8"))
assert plan["kind"] == "pulsar-model-library-source-attested-acquisition-plan"
assert plan["source"]["selector"] == "main"
assert plan["source"]["snapshot_revision"] == "a" * 40
assert plan["identity"]["identity_class"] == "source-attested"
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
    home add nemotron-3-nano-30b-nvfp4 --revision main \
    >"$STATE/declined.out" 2>"$STATE/declined.err"; then
  echo "declined source-attested home add unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'source-attested acquisition' "$STATE/declined.out"
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
env "${BASE_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
  --revision main --yes --json \
  >"$STATE/result.json" 2>"$STATE/result.err"
python3 - "$STATE/result.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["kind"] == "pulsar-model-library-source-attested-acquisition-result"
assert result["state"] == "published"
assert result["identity_class"] == "source-attested"
assert result["snapshot_revision"] == "a" * 40
assert len(result["source_digest"]) == 64
assert len(result["approval_id"]) == 64
assert result["catalog_refreshed"] is False
assert "node_id" not in result
assert "cache_root" not in result
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
receipts=$(find "$STATE/library/source-attested-receipts" -name '*.json' | wc -l)
[ "$receipts" -eq 1 ]

env "${BASE_ENV[@]}" "$LIBRARY" catalog refresh --local-only >/dev/null
env "${BASE_ENV[@]}" "$LIBRARY" home verify \
  'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  --json >"$STATE/verify.json"
python3 - "$STATE/verify.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["kind"] == "pulsar-model-library-source-attested-home-verify-result"
assert result["state"] == "verified"
PY

if env "${BASE_ENV[@]}" "$LIBRARY" home add nemotron-3-nano-30b-nvfp4 \
    --revision main --yes \
    >"$STATE/occupied.out" 2>"$STATE/occupied.err"; then
  echo "source-attested home add overwrote an existing home" >&2
  exit 1
fi
grep -q 'already exists' "$STATE/occupied.err"

# Sealed path remains available without --revision.
python3 "$REPO_DIR/scripts/testlib/model_library_acquisition_fixture.py" "$STATE/sealed"
if env \
  "PATH=$STATE/sealed/bin:$PATH" \
  "CLUSTER_TOPOLOGY_FILE=$STATE/sealed/topology.json" \
  "HF_CACHE=$STATE/sealed/cache" \
  "PULSAR_MODEL_LIBRARY_PY=$STATE/sealed/model_library_wrapper.py" \
  "MOCK_HF_LOG=$STATE/sealed-hf.log" \
  "$LIBRARY" home add qwen3-1.7b --yes --json \
  >"$STATE/sealed.json" 2>"$STATE/sealed.err"; then
  python3 - "$STATE/sealed.json" <<'PY'
import json, sys
result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["kind"] == "pulsar-model-library-home-acquisition-result"
assert result["state"] == "published"
assert result["profile"] == "qwen3-1.7b"
PY
else
  echo "sealed home add compatibility failed" >&2
  cat "$STATE/sealed.err" >&2
  exit 1
fi

"$LIBRARY" --help | grep -q 'home add <profile>'
"$LIBRARY" --help | grep -q 'home verify'
echo "model-library source-attested CLI scenarios: PASS"
