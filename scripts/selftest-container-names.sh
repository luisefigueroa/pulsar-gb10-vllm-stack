#!/usr/bin/env bash
# Self-test exact container name matching (no docker required).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

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

LIST=$(printf '%s\n' \
  vllm-cluster-deepseek-v4-flash \
  vllm-cluster-deepseek-v4-flash-0422 \
  vllm-cluster-qwen3-1.7b-2node \
  vllm-deepseek-v4-flash)

got=$(printf '%s\n' "$LIST" | filter_exact_container_name "vllm-cluster-deepseek-v4-flash")
assert_eq "$got" "vllm-cluster-deepseek-v4-flash" "exact flash does not pull 0422"

got=$(printf '%s\n' "$LIST" | filter_exact_container_name "vllm-cluster-deepseek-v4-flash-0422")
assert_eq "$got" "vllm-cluster-deepseek-v4-flash-0422" "exact 0422"

got=$(printf '%s\n' "$LIST" | filter_exact_container_name "vllm-cluster-deepseek-v4")
assert_eq "$got" "" "prefix alone matches nothing"

got=$(printf '%s\n' "$LIST" | filter_exact_container_name "vllm-deepseek-v4-flash")
assert_eq "$got" "vllm-deepseek-v4-flash" "single-node name exact"

# Simulate old buggy prefix grep
buggy=$(printf '%s\n' "$LIST" | grep -E '^vllm-cluster-deepseek-v4-flash' || true)
buggy_n=$(printf '%s\n' "$buggy" | grep -c . || true)
if [ "$buggy_n" -ge 2 ]; then
  echo "OK   (control) old prefix grep would match $buggy_n names — reason this test exists"
  pass=$((pass + 1))
else
  echo "FAIL control: expected prefix grep to over-match" >&2
  fail=$((fail + 1))
fi

assert_eq "$(container_name_for deepseek-v4-flash 2)" "vllm-cluster-deepseek-v4-flash" "cluster name for flash"
assert_eq "$(container_name_for deepseek-v4-flash-0422 2)" "vllm-cluster-deepseek-v4-flash-0422" "cluster name for 0422"

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
