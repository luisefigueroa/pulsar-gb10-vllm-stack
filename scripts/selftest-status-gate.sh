#!/usr/bin/env bash
# Self-test advisory STATUS policy (no Docker).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

pass=0
fail=0
check() {
  local status="$1" expect_tested="$2"
  STATUS="$status"
  if status_is_tested; then tested=1; else tested=0; fi
  if status_is_launchable; then launchable=1; else launchable=0; fi
  if status_requires_force; then requires_force=1; else requires_force=0; fi
  if [ "$tested" = "$expect_tested" ] \
      && [ "$launchable" = 1 ] && [ "$requires_force" = 0 ]; then
    echo "OK   STATUS=$status  tested=$tested advisory=1"
    pass=$((pass + 1))
  else
    echo "FAIL STATUS=$status tested=$tested expected_tested=$expect_tested launchable=$launchable requires_force=$requires_force" >&2
    fail=$((fail + 1))
  fi
}

# Legacy tested labels remain recommendation classifiers.
check tested 1
check tested+soaked 1
check "tested-experimental" 1

# Every other label is also launchable with respect to status.
check untested 0
check do-not-use 0
check blocked-upstream 0
check blocked 0
check "?" 0
check experimental 0
check "" 0

# conf files on disk
for pair in \
  "qwen3.6-27b-fp8-2node:0" \
  "laguna-s-2.1-2node:0" \
  "inkling-small-nvfp4:0" \
  "deepseek-v4-flash:1" \
  "qwen3-1.7b:1"
do
  name="${pair%%:*}"
  exp_tested="${pair##*:}"
  load_conf "$name"
  if status_is_tested; then tested=1; else tested=0; fi
  if status_is_launchable; then launchable=1; else launchable=0; fi
  if status_requires_force; then requires_force=1; else requires_force=0; fi
  if [ "$tested" = "$exp_tested" ] \
      && [ "$launchable" = 1 ] && [ "$requires_force" = 0 ]; then
    echo "OK   conf=$name STATUS=$STATUS tested=$tested advisory=1"
    pass=$((pass + 1))
  else
    echo "FAIL conf=$name STATUS=$STATUS tested=$tested expected_tested=$exp_tested launchable=$launchable requires_force=$requires_force" >&2
    fail=$((fail + 1))
  fi
done

STATUS=do-not-use
if status_is_blocked; then
  echo "OK   blocked labels remain descriptive classifiers"
  pass=$((pass + 1))
else
  echo "FAIL do-not-use lost its descriptive classifier" >&2
  fail=$((fail + 1))
fi

if ! grep -q 'status_requires_force' \
    "$REPO_DIR/scripts/up.sh" \
    "$REPO_DIR/serve.sh" \
    "$REPO_DIR/cluster/start-cluster.sh" \
    "$REPO_DIR/wizard.sh"; then
  echo "OK   serving entrypoints contain no status-derived refusal"
  pass=$((pass + 1))
else
  echo "FAIL a serving entrypoint still calls the legacy status gate" >&2
  fail=$((fail + 1))
fi

if grep -Fq 'list-models.sh" --serving --json' "$REPO_DIR/wizard.sh" \
    && ! grep -Fq 'list-models.sh" --validated --serving --json' "$REPO_DIR/wizard.sh"; then
  echo "OK   wizard catalog selection has no status filter"
  pass=$((pass + 1))
else
  echo "FAIL wizard catalog selection still filters by status" >&2
  fail=$((fail + 1))
fi

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
