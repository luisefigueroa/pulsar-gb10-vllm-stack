#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERN='(^|[^[:alnum:]-])dgx-spark-[[:digit:]]+([^[:alnum:]-]|$)'

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

contains_stable_lab_name() {
  local rc=0

  rg -qi --regexp "$PATTERN" -- "$@" || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) return 1 ;;
    *) fail "documentation privacy scan failed (rg exit $rc)" ;;
  esac
}

test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT

printf '%s\n' \
  '| Node A | Node B |' \
  'Runtime roles are rank 0 and rank 1.' \
  'Never publish stable site hostnames or durable topology identity.' \
  'The generic naming example dgx-spark-N is not a site identity.' \
  'https://docs.nvidia.com/dgx/dgx-spark/hardware.html' \
  'Documentation examples use 192.0.2.42.' \
  >"$test_root/allowed.md"

printf '%s\n' '| Node A | dgx-spark-7.local |' >"$test_root/forbidden.md"

contains_stable_lab_name "$test_root/forbidden.md" \
  || fail "stable numeric lab hostname fixture was not rejected"

if contains_stable_lab_name "$test_root/allowed.md"; then
  fail "generic node/rank labels or safety guidance were rejected"
fi

cd "$REPO_DIR"
mapfile -d '' -t markdown_files < <(git ls-files -z -- '*.md')
((${#markdown_files[@]} > 0)) || fail "no versioned Markdown files found"

if contains_stable_lab_name "${markdown_files[@]}"; then
  echo "Publishable Markdown contains a stable lab hostname:" >&2
  rg -ni --regexp "$PATTERN" -- "${markdown_files[@]}" >&2 || true
  exit 1
fi

echo "documentation privacy selftest OK"
