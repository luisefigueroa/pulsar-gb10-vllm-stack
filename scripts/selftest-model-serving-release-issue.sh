#!/usr/bin/env bash
# Scenario coverage for maintainer ADR 0004 issuance staging.
# Control-plane only: no physical DGX claim and no production registry object.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cli="$REPO_DIR/scripts/model-serving-release-issue.sh"
py="$REPO_DIR/scripts/model_serving_release_issue.py"
[ -x "$cli" ] || fail "missing executable $cli"
[ -f "$py" ] || fail "missing $py"

help_out="$("$cli" help)"
printf '%s\n' "$help_out" | grep -Fq 'not trusted until repository review and merge' \
  || fail "help must state the review-and-merge trust event"
printf '%s\n' "$help_out" | grep -Fq 'scripts/model-serving-release-issue.sh plan' \
  || fail "help must document plan"
printf '%s\n' "$help_out" | grep -Fq 'scripts/model-serving-release-issue.sh stage' \
  || fail "help must document stage"
printf '%s\n' "$help_out" | grep -Fq 'repository-review:' \
  || fail "help must document the closed review-reference grammar"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pulsar-msri-selftest.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT
if "$cli" publish >"$tmpdir/unknown.err" 2>&1; then
  fail "unknown command must fail"
fi
grep -Fq 'unknown model-serving-release-issue command' "$tmpdir/unknown.err" \
  || fail "unknown command must name the issuance command"

if "$cli" plan >"$tmpdir/usage.err" 2>&1; then
  fail "plan without required flags must fail"
fi
grep -Fq 'usage: model-serving-release-issue.sh plan' "$tmpdir/usage.err" \
  || fail "plan must print usage when flags are missing"

echo "model-serving-release-issue selftest OK"
