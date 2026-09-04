#!/usr/bin/env bash
# Run the single-server validation gates against an ALREADY-RUNNING server.
#
#   validate/run-gates.sh <label> [--model SERVED_NAME] [--url http://host:8000] \
#       [--needle-tokens N] [--baseline results/<file>.json] [--tag LABEL] \
#       [--allow-fp-equivalent-run-to-run] \
#       [--measurement-dir DIR] [--invocation-plan FILE]
#
# <label> names the artifacts under results/ and, unless --model is given,
# is also the served model name sent to the API. Pass --model when the
# served name is not a safe file label (a spec served under its model id).
#
# Gates, in order (each writes into results/ under --tag):
#   1. greedy captures x2  -> run-to-run determinism verdict
#   2. [--baseline]        -> comparison vs a prior capture (image bump A/B)
#   3. bench sweep c=1,2,4,8 (warmup per level)
#   4. [--needle-tokens]   -> needle at that context
#
# Optional --measurement-dir writes closed compare/bench measurement
# documents. Ordinary human output does not require a release plan.
# Optional --invocation-plan applies an explicit contract-driven bench
# argv produced by scripts/model-serving-release-attempt.sh; it does not
# change the default sweep.
#
# NOT covered here (see docs/REVALIDATE.md): server lifecycle, gsm8k,
# 2-node stress sequence, soaks.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

NAME="${1:?usage: run-gates.sh <label> [--model SERVED_NAME] [--url U] [--needle-tokens N] [--baseline F] [--tag T] [--allow-fp-equivalent-run-to-run] [--measurement-dir DIR] [--invocation-plan FILE]}"
case "$NAME" in
  *[!A-Za-z0-9._-]*|"")
    echo "invalid served name or label: use only letters, numbers, dot, underscore, or hyphen" >&2
    exit 2
    ;;
esac
shift
MODEL="$NAME"
URL="http://127.0.0.1:8000"; NEEDLE=0; BASELINE=""; TAG="$(date +%Y%m%dT%H%M%S)"
ALLOW_FP_RUN=0
MEASUREMENT_DIR=""
INVOCATION_PLAN=""
ATTEMPT_PY="${PULSAR_MODEL_SERVING_RELEASE_ATTEMPT_PY:-$PWD/scripts/model_serving_release_attempt.py}"
while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      if [ "$#" -lt 2 ] || [ -z "$2" ]; then echo "--model requires a served model name" >&2; exit 2; fi
      MODEL="$2"; shift
      ;;
    --url)
      [ "$#" -ge 2 ] || { echo "--url requires a value" >&2; exit 2; }
      URL="$2"; shift
      ;;
    --needle-tokens)
      [ "$#" -ge 2 ] || { echo "--needle-tokens requires a non-negative integer" >&2; exit 2; }
      NEEDLE="$2"; shift
      ;;
    --baseline)
      [ "$#" -ge 2 ] || { echo "--baseline requires a readable file" >&2; exit 2; }
      BASELINE="$2"; shift
      ;;
    --tag)
      [ "$#" -ge 2 ] || { echo "--tag requires a value" >&2; exit 2; }
      TAG="$2"; shift
      ;;
    --allow-fp-equivalent-run-to-run) ALLOW_FP_RUN=1 ;;
    --measurement-dir)
      [ "$#" -ge 2 ] || { echo "--measurement-dir requires a value" >&2; exit 2; }
      MEASUREMENT_DIR="$2"; shift
      ;;
    --invocation-plan)
      [ "$#" -ge 2 ] || { echo "--invocation-plan requires a value" >&2; exit 2; }
      INVOCATION_PLAN="$2"; shift
      ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac; shift
done

case "$NEEDLE" in
  *[!0-9]*|"")
    echo "invalid --needle-tokens '$NEEDLE' (expected a non-negative integer)" >&2
    exit 2
    ;;
esac
if [ -n "$BASELINE" ] && [ ! -r "$BASELINE" ]; then
  echo "baseline is not readable: $BASELINE" >&2
  exit 2
fi
if [ -n "$INVOCATION_PLAN" ] && [ ! -r "$INVOCATION_PLAN" ]; then
  echo "invocation plan is not readable: $INVOCATION_PLAN" >&2
  exit 2
fi

case "$TAG" in
  *[!A-Za-z0-9._-]*|"")
    echo "invalid --tag: use only letters, numbers, dot, underscore, or hyphen" >&2
    exit 2
    ;;
esac

P="results/${NAME}-${TAG}"
mkdir -p results
FAIL=0
interrupt_gates() {
  local exit_code="$1"
  trap - INT TERM
  echo "GATES INTERRUPTED for $NAME — partial artifacts, if any, remain at ${P}-*" >&2
  exit "$exit_code"
}
trap 'interrupt_gates 130' INT
trap 'interrupt_gates 143' TERM
if compgen -G "${P}-*" >/dev/null; then
  echo "refusing to overwrite existing artifacts: ${P}-* (choose a unique --tag)" >&2
  exit 2
fi
if [ -n "$MEASUREMENT_DIR" ]; then
  python3 "$ATTEMPT_PY" --repo-root "$PWD" check-measurement-dir \
    --path "$MEASUREMENT_DIR" || {
    echo "measurement directory is not a safe results path or explicit outside path" >&2
    exit 2
  }
  if [ -e "$MEASUREMENT_DIR/compare-captures.json" ] || \
     [ -e "$MEASUREMENT_DIR/benchmark-serving.json" ]; then
    echo "refusing to overwrite existing measurements in $MEASUREMENT_DIR" >&2
    exit 2
  fi
fi
RUN_COMPARE_ARGS=(--require-identical)
[ "$ALLOW_FP_RUN" = 1 ] && RUN_COMPARE_ARGS=()

BENCH_ARGS=(--concurrency 1 2 4 8)
if [ -n "$INVOCATION_PLAN" ]; then
  set +e
  BENCH_ARGV_OUT=$(python3 "$ATTEMPT_PY" bench-argv --invocation-plan "$INVOCATION_PLAN")
  bench_argv_rc=$?
  set -e
  if [ "$bench_argv_rc" -ne 0 ] || [ -z "${BENCH_ARGV_OUT}" ]; then
    echo "invocation plan is invalid or produced no bench arguments" >&2
    exit 2
  fi
  mapfile -t BENCH_ARGS <<< "$BENCH_ARGV_OUT"
  if [ "${#BENCH_ARGS[@]}" -eq 0 ]; then
    echo "invocation plan produced empty bench arguments" >&2
    exit 2
  fi
fi

preserve_child_stderr() {
  local log="$1"
  local rc=0
  shift
  "$@" 2>"$log" || rc=$?
  if [ -s "$log" ]; then
    cat "$log" >&2
  fi
  return "$rc"
}

echo "== gate 1: greedy determinism (captures x2)"
set +e
preserve_child_stderr "${P}-runA.stderr.log" \
  python3 validate/greedy_capture.py --url "$URL" --model "$MODEL" --out "${P}-runA.json"
cap_a=$?
preserve_child_stderr "${P}-runB.stderr.log" \
  python3 validate/greedy_capture.py --url "$URL" --model "$MODEL" --out "${P}-runB.json"
cap_b=$?
COMPARE_CMD=(
  python3 validate/compare_captures.py
  "${P}-runA.json" "${P}-runB.json"
  --label-a runA --label-b runB
)
COMPARE_CMD+=("${RUN_COMPARE_ARGS[@]}")
if [ -n "$MEASUREMENT_DIR" ]; then
  COMPARE_CMD+=(--result-json "$MEASUREMENT_DIR/compare-captures.json")
fi
"${COMPARE_CMD[@]}" | tail -7
cmp_rc=${PIPESTATUS[0]}
set -e
if [ "$cap_a" -ne 0 ] || [ "$cap_b" -ne 0 ] || [ "$cmp_rc" -ne 0 ]; then
  FAIL=1
fi

if [ -n "$BASELINE" ]; then
  echo "== gate 2: vs baseline $BASELINE (near-tie flips OK, hard disagreements are not)"
  set +e
  python3 validate/compare_captures.py "$BASELINE" "${P}-runA.json" --label-a baseline --label-b new | tail -6
  base_rc=${PIPESTATUS[0]}
  set -e
  [ "$base_rc" -eq 0 ] || FAIL=1
fi

echo "== gate 3: bench sweep (warmup per level)"
BENCH_CMD=(
  python3 validate/bench_serve.py
  --url "$URL"
  --model "$MODEL"
)
BENCH_CMD+=("${BENCH_ARGS[@]}")
BENCH_CMD+=(--out "${P}-bench.json")
if [ -n "$MEASUREMENT_DIR" ]; then
  BENCH_CMD+=(--result-json "$MEASUREMENT_DIR/benchmark-serving.json")
fi
set +e
"${BENCH_CMD[@]}" | tail -6
bench_rc=${PIPESTATUS[0]}
set -e
[ "$bench_rc" -eq 0 ] || FAIL=1

if [ "$NEEDLE" -gt 0 ]; then
  echo "== gate 4: needle @ ${NEEDLE} tokens"
  set +e
  python3 validate/needle.py --url "$URL" --model "$MODEL" --context-tokens "$NEEDLE" --depths 0.05 0.5 0.95 | tail -4
  needle_rc=${PIPESTATUS[0]}
  set -e
  [ "$needle_rc" -eq 0 ] || FAIL=1
fi

echo
if [ "$FAIL" = 0 ]; then echo "GATES PASSED for $NAME (artifacts: ${P}-*)"; else echo "GATE FAILURE for $NAME — see above; do not promote"; fi
exit $FAIL
