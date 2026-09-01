#!/usr/bin/env bash
# Compose draft ADR 0004 attempt-only specs from validator measurements and
# experiment-only per-attempt resource diagnostics supplied by the context.
# Not routed by ./pulsar. Does not issue a decision, write the tracked
# registry, change catalog/profile status, or launch a release.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=model-serving-release-attempt
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_SERVING_RELEASE_ATTEMPT_PY:-$REPO_DIR/scripts/model_serving_release_attempt.py}"

case "${1:-}" in
  -h|--help|help|"")
    python3 "$PY_TOOL" --repo-root "$REPO_DIR" help
    exit $?
    ;;
esac

command="$1"
shift

case "$command" in
  compose|compose-extra|plan-invocation|bench-argv|check-measurement-dir) ;;
  *)
    die "unknown model-serving-release-attempt command: $command"
    ;;
esac

json=0
release_plan=""
context=""
compare_measurement=""
benchmark_measurement=""
extra_operation=""
measurement=""
output_dir=""
output=""
invocation_plan=""
check_path=""
passthrough=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --json)
      json=1
      ;;
    --release-plan)
      [ "$#" -ge 2 ] || die "--release-plan requires a value"
      release_plan="$2"
      shift
      ;;
    --context)
      [ "$#" -ge 2 ] || die "--context requires a value"
      context="$2"
      shift
      ;;
    --compare-measurement)
      [ "$#" -ge 2 ] || die "--compare-measurement requires a value"
      compare_measurement="$2"
      shift
      ;;
    --benchmark-measurement)
      [ "$#" -ge 2 ] || die "--benchmark-measurement requires a value"
      benchmark_measurement="$2"
      shift
      ;;
    --operation)
      [ "$#" -ge 2 ] || die "--operation requires a value"
      extra_operation="$2"
      shift
      ;;
    --measurement)
      [ "$#" -ge 2 ] || die "--measurement requires a value"
      measurement="$2"
      shift
      ;;
    --output-dir)
      [ "$#" -ge 2 ] || die "--output-dir requires a value"
      output_dir="$2"
      shift
      ;;
    --output)
      [ "$#" -ge 2 ] || die "--output requires a value"
      output="$2"
      shift
      ;;
    --invocation-plan)
      [ "$#" -ge 2 ] || die "--invocation-plan requires a value"
      invocation_plan="$2"
      shift
      ;;
    --path)
      [ "$#" -ge 2 ] || die "--path requires a value"
      check_path="$2"
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
  compose)
    [ -n "$release_plan" ] && [ -n "$context" ] && [ -n "$output_dir" ] || die \
      "usage: model-serving-release-attempt.sh compose --release-plan DIR --context FILE --output-dir DIR [--compare-measurement FILE] [--benchmark-measurement FILE] [--json]"
    ;;
  compose-extra)
    [ -n "$release_plan" ] && [ -n "$context" ] && [ -n "$output_dir" ] \
      && [ -n "$extra_operation" ] && [ -n "$measurement" ] || die \
      "usage: model-serving-release-attempt.sh compose-extra --release-plan DIR --context FILE --operation evaluate-gsm8k|validate-soak --measurement FILE --output-dir DIR [--json]"
    ;;
  plan-invocation)
    [ -n "$release_plan" ] || die \
      "usage: model-serving-release-attempt.sh plan-invocation --release-plan DIR [--output FILE] [--json]"
    ;;
  bench-argv)
    [ -n "$invocation_plan" ] || die \
      "usage: model-serving-release-attempt.sh bench-argv --invocation-plan FILE"
    ;;
  check-measurement-dir)
    [ -n "$check_path" ] || die \
      "usage: model-serving-release-attempt.sh check-measurement-dir --path DIR"
    ;;
esac

[ -f "$PY_TOOL" ] || die "missing Python attempt-composition tool"

args=()
if [ "${#passthrough[@]}" -eq 0 ]; then
  args+=(--repo-root "$REPO_DIR")
else
  args+=("${passthrough[@]}")
fi
args+=("$command")
if [ -n "$release_plan" ]; then
  args+=(--release-plan "$release_plan")
fi
if [ -n "$context" ]; then
  args+=(--context "$context")
fi
if [ -n "$compare_measurement" ]; then
  args+=(--compare-measurement "$compare_measurement")
fi
if [ -n "$benchmark_measurement" ]; then
  args+=(--benchmark-measurement "$benchmark_measurement")
fi
if [ -n "$extra_operation" ]; then
  args+=(--operation "$extra_operation")
fi
if [ -n "$measurement" ]; then
  args+=(--measurement "$measurement")
fi
if [ -n "$output_dir" ]; then
  args+=(--output-dir "$output_dir")
fi
if [ -n "$output" ]; then
  args+=(--output "$output")
fi
if [ -n "$invocation_plan" ]; then
  args+=(--invocation-plan "$invocation_plan")
fi
if [ -n "$check_path" ]; then
  args+=(--path "$check_path")
fi
if [ "$json" -eq 1 ]; then
  args+=(--json)
fi

python3 "$PY_TOOL" "${args[@]}"
