#!/usr/bin/env bash
# Operator CLI for released ADR 0017 specs (WP1.4a). No Docker.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

expect_failure() {
  local expected_rc="$1" needle="$2" label="$3"
  shift 3
  local output rc=0
  set +e
  output=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$rc" != "$expected_rc" ]; then
    echo "FAIL $label: rc=$rc expected=$expected_rc output=$output" >&2
    exit 1
  fi
  if ! printf '%s' "$output" | grep -q -- "$needle"; then
    echo "FAIL $label: missing '$needle' in output=$output" >&2
    exit 1
  fi
  echo "OK   $label"
}

expect_empty() {
  local label="$1"
  shift
  local output
  output=$("$@")
  if [ -n "$output" ]; then
    echo "FAIL $label: expected empty list, got: $output" >&2
    exit 1
  fi
  echo "OK   $label"
}

expect_empty "pulsar release list on empty releases/" \
  "$REPO_DIR/pulsar" release list

expect_failure 2 "error:" "verify unknown spec_id exits 2" \
  "$REPO_DIR/pulsar" release verify aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

expect_failure 2 "Usage:" "release with no args is usage" \
  "$REPO_DIR/pulsar" release

help_out=$("$REPO_DIR/pulsar" help)
if ! printf '%s' "$help_out" | grep -q "release  verify|show|list released specs under releases/"; then
  if ! printf '%s' "$help_out" | grep -q "Read released specs under releases/"; then
    echo "FAIL pulsar help missing release line: $help_out" >&2
    exit 1
  fi
fi
echo "OK   pulsar help names release"

script_help=$("$REPO_DIR/scripts/release.sh" --help)
if ! printf '%s' "$script_help" | grep -q "verify <spec_id>"; then
  echo "FAIL release.sh --help: $script_help" >&2
  exit 1
fi
echo "OK   release.sh --help"
