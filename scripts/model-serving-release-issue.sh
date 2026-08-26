#!/usr/bin/env bash
# Maintainer ADR 0004 staging.
# Not routed by ./pulsar. Stages an untrusted proposal only. Repository
# review and merge are what make the objects trusted. Does not edit a profile, authorize
# serving, or claim physical behavior.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-release-issue
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_SERVING_RELEASE_ISSUE_PY:-$REPO_DIR/scripts/model_serving_release_issue.py}"

case "${1:-}" in
  -h|--help|help|"")
    python3 "$PY_TOOL" --repo-root "$REPO_DIR" help
    exit $?
    ;;
esac

command="$1"
shift

case "$command" in
  plan|stage) ;;
  *)
    die "unknown model-serving-release-issue command: $command"
    ;;
esac

json=0
candidate_dir=""
review_file=""
passthrough=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --json)
      json=1
      ;;
    --candidate-dir)
      [ "$#" -ge 2 ] || die "--candidate-dir requires a value"
      candidate_dir="$2"
      shift
      ;;
    --review-file)
      [ "$#" -ge 2 ] || die "--review-file requires a value"
      review_file="$2"
      shift
      ;;
    --repo-root)
      [ "$#" -ge 2 ] || die "--repo-root requires a value"
      passthrough+=(--repo-root "$2")
      shift
      ;;
    --help|-h)
      python3 "$PY_TOOL" --repo-root "$REPO_DIR" help
      exit 0
      ;;
    --*)
      die "unknown option: $1"
      ;;
    *)
      die "unexpected argument: $1"
      ;;
  esac
  shift
done

[ -n "$candidate_dir" ] && [ -n "$review_file" ] || die \
  "usage: model-serving-release-issue.sh $command --candidate-dir DIR --review-file FILE [--json]"

[ -f "$PY_TOOL" ] || die "missing Python staging tool"

args=()
if [ "${#passthrough[@]}" -eq 0 ]; then
  args+=(--repo-root "$REPO_DIR")
else
  args+=("${passthrough[@]}")
fi
args+=("$command")
args+=(--candidate-dir "$candidate_dir")
args+=(--review-file "$review_file")
if [ "$json" -eq 1 ]; then
  args+=(--json)
fi

python3 "$PY_TOOL" "${args[@]}"
