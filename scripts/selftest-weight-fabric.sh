#!/usr/bin/env bash
# Leftover weight-fabric teardown honors WEIGHT_FABRIC_SUDO_MODE.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WF="$REPO_DIR/scripts/weight-fabric.sh"

grep -Fq 'WF_SUDO_MODE="${WEIGHT_FABRIC_SUDO_MODE:-passwordless}"' "$WF"
echo "OK   env initializes WF_SUDO_MODE"

grep -Fq 'WEIGHT_FABRIC_SUDO_MODE must be passwordless or interactive' "$WF"
echo "OK   invalid env is documented as fail-closed"

grep -Fq -- '--interactive-sudo) WF_SUDO_MODE=interactive' "$WF"
echo "OK   --interactive-sudo overrides the env"

"$WF" help | grep -q 'WEIGHT_FABRIC_SUDO_MODE=passwordless|interactive'
echo "OK   help documents the env"

WEIGHT_FABRIC_SUDO_MODE=passwordless "$WF" help >/dev/null
echo "OK   WEIGHT_FABRIC_SUDO_MODE=passwordless is accepted"

WEIGHT_FABRIC_SUDO_MODE=interactive "$WF" help >/dev/null
echo "OK   WEIGHT_FABRIC_SUDO_MODE=interactive is accepted"

set +e
out=$(WEIGHT_FABRIC_SUDO_MODE=bogus "$WF" help 2>&1)
rc=$?
set -e
if [ "$rc" -eq 0 ]; then
  echo "FAIL invalid WEIGHT_FABRIC_SUDO_MODE was accepted: $out" >&2
  exit 1
fi
printf '%s' "$out" | grep -q 'must be passwordless or interactive'
echo "OK   invalid WEIGHT_FABRIC_SUDO_MODE fails closed before cleanup"
