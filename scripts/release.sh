#!/usr/bin/env bash
# Operator CLI for released ADR 0017 specs under releases/.
#   scripts/release.sh list|verify|show [spec_id]
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=release
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_RELEASE_CONSUMER_PY:-$REPO_DIR/scripts/release_consumer.py}"

usage() {
  cat <<'EOF'
Read released ADR 0017 specs under releases/

Usage:
  scripts/release.sh list [--json]
  scripts/release.sh verify <spec_id>
  scripts/release.sh show <spec_id>

  • Verifies each file on read. Only state=released files named
    <spec_id>.json are accepted.
  • Does not start a server, write releases/, or change catalog status.
  • Routed as ./pulsar release …
EOF
}

case "${1:-}" in
  -h|--help|help)
    usage
    exit 0
    ;;
  "")
    usage
    exit 2
    ;;
esac

[ -f "$PY_TOOL" ] || die "missing $PY_TOOL"
exec python3 "$PY_TOOL" --repo-root "$REPO_DIR" "$@"
