#!/usr/bin/env bash
# Read-only inspection of the tracked ADR 0004 Model Serving Release registry.
# Not routed by ./pulsar. Does not capture evidence, issue a decision,
# project catalog status, or launch a release.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-release-registry
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_SERVING_RELEASE_REGISTRY_PY:-$REPO_DIR/scripts/model_serving_release_registry.py}"

usage() {
  cat <<'EOF'
Inspect the read-only Model Serving Release registry

Usage:
  scripts/model-serving-release-registry.sh verify [--json]
  scripts/model-serving-release-registry.sh show-release RELEASE_ID [--json]
  scripts/model-serving-release-registry.sh show-decision DECISION_ID [--json]

This command only verifies and displays stored ADR 0004 objects. It does
not capture evidence, issue a decision, project catalog status, or launch a
release. Validation status is advisory and never serving authorization.

Publishable evidence hashing does not prove privacy review, repository
review, or physical behavior. Absence of a reviewed decision is not
Untested.
EOF
}

case "${1:-}" in
  -h|--help|help|"")
    usage
    exit 0
    ;;
esac

command="$1"
shift

json=0
release_id=""
decision_id=""
passthrough=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --json)
      json=1
      ;;
    --repo-root)
      [ "$#" -ge 2 ] || die "--repo-root requires a value"
      passthrough+=(--repo-root "$2")
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --*)
      die "unknown option: $1"
      ;;
    *)
      if [ "$command" = "show-release" ] && [ -z "$release_id" ]; then
        release_id="$1"
      elif [ "$command" = "show-decision" ] && [ -z "$decision_id" ]; then
        decision_id="$1"
      else
        die "unexpected argument: $1"
      fi
      ;;
  esac
  shift
done

case "$command" in
  verify) ;;
  show-release)
    [ -n "$release_id" ] || die "usage: model-serving-release-registry.sh show-release RELEASE_ID [--json]"
    ;;
  show-decision)
    [ -n "$decision_id" ] || die "usage: model-serving-release-registry.sh show-decision DECISION_ID [--json]"
    ;;
  *)
    die "unknown model-serving-release-registry command: $command"
    ;;
esac

[ -f "$PY_TOOL" ] || die "missing $PY_TOOL"

args=()
if [ "${#passthrough[@]}" -eq 0 ]; then
  args+=(--repo-root "$REPO_DIR")
else
  args+=("${passthrough[@]}")
fi
args+=("$command")
if [ -n "$release_id" ]; then
  args+=("$release_id")
fi
if [ -n "$decision_id" ]; then
  args+=("$decision_id")
fi
if [ "$json" -eq 1 ]; then
  args+=(--json)
fi

python3 "$PY_TOOL" "${args[@]}"
