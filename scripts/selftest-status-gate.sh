#!/usr/bin/env bash
# Self-test STATUS launch gate (no docker). Exit 0 if all cases match policy.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

pass=0
fail=0
check() {
  local status="$1" expect_force="$2" # expect_force: 0=launchable, 1=requires force
  STATUS="$status"
  if status_requires_force; then
    got=1
  else
    got=0
  fi
  if [ "$got" = "$expect_force" ]; then
    echo "OK   STATUS=$status  requires_force=$got"
    pass=$((pass + 1))
  else
    echo "FAIL STATUS=$status  requires_force=$got expected=$expect_force" >&2
    fail=$((fail + 1))
  fi
}

# allowlist
check tested 0
check tested+soaked 0
check "tested-experimental" 0

# refuse without --force
check untested 1
check do-not-use 1
check blocked-upstream 1
check blocked 1
check "?" 1
check experimental 1
check "" 1

# conf files on disk
for pair in \
  "qwen3.6-27b-fp8-2node:1" \
  "laguna-s-2.1-2node:1" \
  "inkling-small-nvfp4:1" \
  "deepseek-v4-flash:0" \
  "qwen3-1.7b:0"
do
  name="${pair%%:*}"
  exp="${pair##*:}"
  load_conf "$name"
  if status_requires_force; then got=1; else got=0; fi
  if [ "$got" = "$exp" ]; then
    echo "OK   conf=$name STATUS=$STATUS requires_force=$got"
    pass=$((pass + 1))
  else
    echo "FAIL conf=$name STATUS=$STATUS requires_force=$got expected=$exp" >&2
    fail=$((fail + 1))
  fi
done

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
