#!/usr/bin/env bash
# Deterministic validation-verdict regressions (no live model server).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-validation-selftest.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

cat >"$STATE/a.json" <<'JSON'
[
  {
    "prompt": "p",
    "text": "ab",
    "tokens": ["a", "b"],
    "logprobs": [-0.1, -0.2]
  }
]
JSON
cp "$STATE/a.json" "$STATE/identical.json"

cat >"$STATE/near.json" <<'JSON'
[
  {
    "prompt": "p",
    "text": "ac",
    "tokens": ["a", "c"],
    "logprobs": [-0.1, -0.25]
  }
]
JSON

cat >"$STATE/truncated.json" <<'JSON'
[
  {
    "prompt": "p",
    "text": "a",
    "tokens": ["a"],
    "logprobs": [-0.1]
  }
]
JSON

cat >"$STATE/bad-logprobs.json" <<'JSON'
[
  {
    "prompt": "p",
    "text": "ab",
    "tokens": ["a", "b"],
    "logprobs": [-0.1]
  }
]
JSON
printf '%s\n' '[]' >"$STATE/empty.json"

python3 validate/compare_captures.py "$STATE/a.json" "$STATE/identical.json" \
  --require-identical >/dev/null
python3 validate/compare_captures.py "$STATE/a.json" "$STATE/near.json" \
  >/dev/null

set +e
python3 validate/compare_captures.py "$STATE/a.json" "$STATE/near.json" \
  --require-identical >"$STATE/strict-near.out" 2>&1
strict_near_rc=$?
python3 validate/compare_captures.py "$STATE/a.json" "$STATE/truncated.json" \
  >"$STATE/truncated.out" 2>&1
truncated_rc=$?
python3 validate/compare_captures.py "$STATE/a.json" "$STATE/empty.json" \
  >"$STATE/empty.out" 2>&1
empty_rc=$?
python3 validate/compare_captures.py "$STATE/a.json" "$STATE/bad-logprobs.json" \
  >"$STATE/bad-logprobs.out" 2>&1
bad_logprobs_rc=$?
set -e

[ "$strict_near_rc" -eq 1 ]
[ "$truncated_rc" -eq 1 ]
[ "$empty_rc" -eq 2 ]
[ "$bad_logprobs_rc" -eq 2 ]
grep -q 'NOT-IDENTICAL' "$STATE/strict-near.out"
grep -q 'truncated output' "$STATE/truncated.out"
grep -q 'non-empty JSON list' "$STATE/empty.out"
grep -q 'logprobs shorter than tokens' "$STATE/bad-logprobs.out"

PYTHONPATH="$REPO_DIR/validate" python3 - <<'PY'
import asyncio
import sys

import bench_serve as bench

seen = []
bench.make_prompt = lambda _n, seed: seen.append(seed) or str(seed)

async def fake_request(_url, _model, _prompt, _out, results, _key):
    results.append({"ttft": 0.01, "decode_tps": 1.0, "total_s": 1.0, "ntok": 2})

bench.one_request = fake_request

async def seed_check():
    await bench.run_level("u", "m", 2, 2, 16, 4, True, None)
    assert seen == [1000, 1001], seen
    seen.clear()
    await bench.run_level("u", "m", 2, 4, 16, 4, False, None)
    assert seen == [0, 1, 2, 3], seen

asyncio.run(seed_check())

async def no_results(*_args, **_kwargs):
    return [], 0.1

bench.run_level = no_results
sys.argv = ["bench_serve.py", "--model", "fixture", "--concurrency", "1"]
assert asyncio.run(bench.main()) == 1
PY

grep -q -- '--require-identical' validate/run-gates.sh
grep -q -- '--allow-fp-equivalent-run-to-run' validate/run-gates.sh
grep -q -- '--measurement-dir' validate/run-gates.sh
grep -q -- '--invocation-plan' validate/run-gates.sh
grep -q -- '--result-json' validate/run-gates.sh
grep -q -- '--num-requests' validate/bench_serve.py
grep -q 'Y%m%dT%H%M%S' validate/run-gates.sh
grep -q 'refusing to overwrite existing artifacts' validate/run-gates.sh
! grep -q '2>/dev/null' validate/run-gates.sh
grep -q 'preserve_child_stderr' validate/run-gates.sh
python3 - <<'PY'
from pathlib import Path
text = Path("validate/run-gates.sh").read_text(encoding="utf-8")
baseline = text.split("gate 2: vs baseline", 1)[1].split("gate 3:", 1)[0]
assert "--result-json" not in baseline
assert "--allow-fp-equivalent-run-to-run" in text
assert "1 2 4 8" in text
assert "INVOCATION_PLAN" in text
assert "mapfile -t BENCH_ARGS < <" not in text
assert "bench_argv_rc" in text
assert "check-measurement-dir" in text
assert 'mkdir -p "$MEASUREMENT_DIR"' not in text
PY

echo "validation verdict selftest OK"
