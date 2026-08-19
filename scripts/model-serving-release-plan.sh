#!/usr/bin/env bash
# Maintainer-only unreviewed ADR-0004 release planning. Not routed by ./pulsar.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-release-plan
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_SERVING_RELEASE_PLAN_PY:-$REPO_DIR/scripts/model_serving_release_plan.py}"

usage() {
  cat <<'EOF'
Build and verify unreviewed Model Serving Release plans

Usage:
  scripts/model-serving-release-plan.sh build <profile>
      --artifact-manifest FILE --runtime-envelope FILE --criteria FILE
      --model-access-contract local-verified-readonly
      [--artifact FILE --artifact-binding ARTIFACT_KEY=USE ...]
      [--artifact-reference ARTIFACT_KEY=PROFILE_REFERENCE ...]
      [--output-dir DIR] [--json]
  scripts/model-serving-release-plan.sh verify <profile>
      --candidate-dir DIR
      --model-access-contract local-verified-readonly
      [--artifact-reference ARTIFACT_KEY=PROFILE_REFERENCE ...]
      [--json]

Candidate safety:
  • Output is unreviewed and has no validation or serving authority.
  • Repository-local output is restricted to gitignored
    experiments/model-onboarding/.
  • This tool never writes the trusted registry, issues a decision, changes a
    catalog/profile status, or authorizes serving.
  • Runtime compatibility and hardware geometry are explicit inputs; schema
    verification does not prove physical behavior.
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
[ -n "$profile" ] || die "usage: model-serving-release-plan.sh $command <profile> [options]"
shift 2

case "$command" in
  build|verify) ;;
  *) die "unknown model-serving-release-plan command: $command" ;;
esac

[ -f "$PY_TOOL" ] || die "missing $PY_TOOL"
load_conf "$profile"

source_kind=$(model_source_kind)
if [ "$source_kind" = nfs ]; then
  source_kind=content-addressed
fi
args=(
  "$command"
  --repo-root "$REPO_DIR"
  --profile "$CONF_NAME"
  --source-kind "$source_kind"
)
append_loaded_profile_contract_args args
python3 "$PY_TOOL" "${args[@]}" "$@"
