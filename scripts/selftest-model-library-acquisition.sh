#!/usr/bin/env bash
# Thin public-CLI scenarios: sealed home add is retired (ADR 0012).
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

if env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3.8-27b-fp8 --json \
    >"$STATE/json-no-revision.out" 2>"$STATE/json-no-revision.err"; then
  echo "home add without --revision unexpectedly succeeded" >&2
  exit 1
fi
grep -q -- 'pass --revision SELECTOR' "$STATE/json-no-revision.err"
grep -q -- 'ADR 0012' "$STATE/json-no-revision.err"
[ ! -s "$STATE/hf.log" ]

if env "${BASE_ENV[@]}" "$LIBRARY" home add qwen3-1.7b --revision main --json \
    >"$STATE/missing-conf.out" 2>"$STATE/missing-conf.err"; then
  echo "home add of retired qwen3-1.7b unexpectedly succeeded" >&2
  exit 1
fi
grep -q -- 'no such config' "$STATE/missing-conf.err"
echo "model-library acquisition CLI (ADR 0012): PASS"
exit 0
