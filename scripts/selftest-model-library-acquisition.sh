#!/usr/bin/env bash
# Thin public-CLI scenarios for reviewed one-home model acquisition.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-add-shell.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
python3 "$REPO_DIR/scripts/testlib/model_library_acquisition_fixture.py" "$STATE"

LIBRARY="$REPO_DIR/scripts/model-library.sh"
BASE_ENV=(
  "PATH=$STATE/bin:$PATH"
  "CLUSTER_TOPOLOGY_FILE=$STATE/topology.json"
  "HF_CACHE=$STATE/cache"
  "PULSAR_MODEL_LIBRARY_PY=$STATE/model_library_wrapper.py"
  "MOCK_HF_LOG=$STATE/hf.log"
)

if env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3-1.7b --json \
    >"$STATE/json-no-yes.out" 2>"$STATE/json-no-yes.err"; then
  echo "home add --json unexpectedly succeeded without --yes" >&2
  exit 1
fi
grep -q -- '--json requires --yes' "$STATE/json-no-yes.err"
[ ! -s "$STATE/hf.log" ]

if printf 'n\n' | COLUMNS=52 env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3-1.7b \
    >"$STATE/declined.out" 2>"$STATE/declined.err"; then
  echo "declined home add unexpectedly succeeded" >&2
  exit 1
fi
grep -q 'Add this reviewed model' "$STATE/declined.out"
python3 - "$STATE/declined.out" <<'PY'
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
assert lines
assert max(map(len, lines)) <= 52, max(lines, key=len)
PY
[ ! -s "$STATE/hf.log" ]

env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3-1.7b --yes --json \
  >"$STATE/result.json" 2>"$STATE/result.err"
python3 - "$STATE/result.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
assert result["schema_version"] == 1
assert result["kind"] == "pulsar-model-library-home-acquisition-result"
assert result["state"] == "published"
assert result["profile"] == "qwen3-1.7b"
assert result["model_id"] == "Qwen/Qwen3-1.7B"
assert result["rank"] == 0
assert result["catalog_refreshed"] is False
PY
grep -q -- 'download Qwen/Qwen3-1.7B' "$STATE/hf.log"
grep -q -- '--revision 70d244cc86ccca08cf5af4e1e306ecf908b1ad5e' "$STATE/hf.log"
grep -q -- '--cache-dir .*\.pulsar-acquire-' "$STATE/hf.log"

mkdir -p "$STATE/cache/hub/models--Qwen--Qwen3-1.7B"
: >"$STATE/hf.log"
if env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3-1.7b --yes \
    >"$STATE/occupied.out" 2>"$STATE/occupied.err"; then
  echo "home add unexpectedly overwrote an existing repository" >&2
  exit 1
fi
grep -q 'catalog refresh' "$STATE/occupied.err"
[ ! -s "$STATE/hf.log" ]

"$LIBRARY" --help | grep -q 'home add <sealed-profile>'
echo "model-library acquisition shell scenarios: PASS"
