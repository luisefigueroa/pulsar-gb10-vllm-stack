#!/usr/bin/env bash
# Maintainer-only model release candidate assembly. Not routed by ./pulsar.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-release
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_RELEASE_PY:-$REPO_DIR/scripts/model_release.py}"

usage() {
  cat <<'EOF'
Build and verify untrusted model release candidates

Usage:
  scripts/model-release.sh plan <profile> [--json]
  scripts/model-release.sh manifest <profile>
      --hub-path PATH --revision COMMIT [--output-dir DIR] [--json]
  scripts/model-release.sh assemble <profile>
      --manifest PATH --issuer NAME --issued-at RFC3339_UTC
      --evidence REPO_RELATIVE_PATH [--evidence PATH ...]
      [--external-artifact JSON] [--output-dir DIR] [--json]
  scripts/model-release.sh verify-candidate <profile>
      --candidate-dir DIR [--json]

Candidate safety:
  • Output is unreviewed and has no validation authority.
  • Repository-local output is restricted to gitignored
    experiments/release-candidates/.
  • This tool never writes models/seals/, models/validation-bundles/, or a
    profile, and it cannot promote STATUS.
  • assemble and verify-candidate require the current profile to normalize to
    the same digest-pinned runtime contract as the candidate.
EOF
}

case "${1:-}" in
  -h|--help|help|"")
    usage
    exit 0
    ;;
esac

command="$1"
profile="${2:-}"
[ -n "$profile" ] || die "usage: model-release.sh $command <profile> [options]"
shift 2

case "$command" in
  plan|manifest|assemble|verify-candidate) ;;
  *) die "unknown model-release command: $command" ;;
esac

[ -f "$PY_TOOL" ] || die "missing $PY_TOOL"
load_conf "$profile"

args=(
  "$command"
  --repo-root "$REPO_DIR"
  --profile "$CONF_NAME"
)
append_loaded_profile_contract_args args
python3 "$PY_TOOL" "${args[@]}" "$@"
