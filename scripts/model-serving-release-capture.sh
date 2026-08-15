#!/usr/bin/env bash
# Local ADR 0004 evidence-capture candidate persistence.
# Not routed by ./pulsar. Does not issue a decision, write the tracked
# registry, change catalog/profile status, or launch a release.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-release-capture
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_SERVING_RELEASE_CAPTURE_PY:-$REPO_DIR/scripts/model_serving_release_capture.py}"

case "${1:-}" in
  -h|--help|help|"")
    python3 "$PY_TOOL" --repo-root "$REPO_DIR" help
    exit $?
    ;;
esac

command="$1"
shift

case "$command" in
  plan|capture-run|assemble-bundle|verify-candidate) ;;
  *)
    die "unknown model-serving-release-capture command: $command"
    ;;
esac

json=0
spec=""
output_dir=""
candidate_dirs=()
passthrough=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --json)
      json=1
      ;;
    --spec)
      [ "$#" -ge 2 ] || die "--spec requires a value"
      spec="$2"
      shift
      ;;
    --output-dir)
      [ "$#" -ge 2 ] || die "--output-dir requires a value"
      output_dir="$2"
      shift
      ;;
    --candidate-dir)
      [ "$#" -ge 2 ] || die "--candidate-dir requires a value"
      candidate_dirs+=("$2")
      shift
      ;;
    --repo-root)
      [ "$#" -ge 2 ] || die "--repo-root requires a value"
      passthrough+=(--repo-root "$2")
      shift
      ;;
    --help|-h)
      python3 "$PY_TOOL" --repo-root "$REPO_DIR" help
      exit $?
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

case "$command" in
  plan|capture-run)
    [ -n "$spec" ] || die "usage: model-serving-release-capture.sh $command --spec SPEC [--json]"
    ;;
  assemble-bundle)
    [ "${#candidate_dirs[@]}" -gt 0 ] || die \
      "usage: model-serving-release-capture.sh assemble-bundle --candidate-dir DIR [...]"
    ;;
  verify-candidate)
    [ "${#candidate_dirs[@]}" -eq 1 ] || die \
      "usage: model-serving-release-capture.sh verify-candidate --candidate-dir DIR [--json]"
    ;;
esac

[ -f "$PY_TOOL" ] || die "missing Python capture tool"

args=()
if [ "${#passthrough[@]}" -eq 0 ]; then
  args+=(--repo-root "$REPO_DIR")
else
  args+=("${passthrough[@]}")
fi
args+=("$command")
if [ -n "$spec" ]; then
  args+=(--spec "$spec")
fi
if [ -n "$output_dir" ]; then
  args+=(--output-dir "$output_dir")
fi
if [ "${#candidate_dirs[@]}" -gt 0 ]; then
  for candidate_dir in "${candidate_dirs[@]}"; do
    args+=(--candidate-dir "$candidate_dir")
  done
fi
if [ "$json" -eq 1 ]; then
  args+=(--json)
fi

python3 "$PY_TOOL" "${args[@]}"
