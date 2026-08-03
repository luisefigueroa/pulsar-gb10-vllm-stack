#!/usr/bin/env bash
# Run the single-server validation gates against an ALREADY-RUNNING server.
#
#   validate/run-gates.sh <served-name> [--url http://host:8000] \
#       [--needle-tokens N] [--baseline results/<file>.json] [--tag LABEL] \
#       [--allow-fp-equivalent-run-to-run]
#
# Gates, in order (each writes into results/ under --tag):
#   1. greedy captures x2  -> run-to-run determinism verdict
#   2. [--baseline]        -> comparison vs a prior capture (image bump A/B)
#   3. bench sweep c=1,2,4,8 (warmup per level)
#   4. [--needle-tokens]   -> needle at that context
#
# NOT covered here (see docs/REVALIDATE.md): server lifecycle, gsm8k,
# 2-node stress sequence, soaks.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

NAME="${1:?usage: run-gates.sh <served-name> [--url U] [--needle-tokens N] [--baseline F] [--tag T] [--allow-fp-equivalent-run-to-run]}"
shift
URL="http://127.0.0.1:8000"; NEEDLE=0; BASELINE=""; TAG="$(date +%Y%m%dT%H%M%S)"
ALLOW_FP_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift ;;
    --needle-tokens) NEEDLE="$2"; shift ;;
    --baseline) BASELINE="$2"; shift ;;
    --tag) TAG="$2"; shift ;;
    --allow-fp-equivalent-run-to-run) ALLOW_FP_RUN=1 ;;
    *) echo "unknown arg $1" >&2; exit 2 ;;
  esac; shift
done

case "$TAG" in
  *[!A-Za-z0-9._-]*|"")
    echo "invalid --tag: use only letters, numbers, dot, underscore, or hyphen" >&2
    exit 2
    ;;
esac

P="results/${NAME}-${TAG}"
mkdir -p results
FAIL=0
if compgen -G "${P}-*" >/dev/null; then
  echo "refusing to overwrite existing artifacts: ${P}-* (choose a unique --tag)" >&2
  exit 2
fi
RUN_COMPARE_ARGS=(--require-identical)
[ "$ALLOW_FP_RUN" = 1 ] && RUN_COMPARE_ARGS=()


echo "== gate 1: greedy determinism (captures x2)"
python3 validate/greedy_capture.py --url "$URL" --model "$NAME" --out "${P}-runA.json" 2>/dev/null
python3 validate/greedy_capture.py --url "$URL" --model "$NAME" --out "${P}-runB.json" 2>/dev/null
python3 validate/compare_captures.py "${P}-runA.json" "${P}-runB.json" \
  --label-a runA --label-b runB "${RUN_COMPARE_ARGS[@]}" | tail -7 || FAIL=1

if [ -n "$BASELINE" ]; then
  echo "== gate 2: vs baseline $BASELINE (near-tie flips OK, hard disagreements are not)"
  python3 validate/compare_captures.py "$BASELINE" "${P}-runA.json" --label-a baseline --label-b new | tail -6 || FAIL=1
fi

echo "== gate 3: bench sweep (warmup per level)"
python3 validate/bench_serve.py --url "$URL" --model "$NAME" \
  --concurrency 1 2 4 8 --out "${P}-bench.json" | tail -6 || FAIL=1

if [ "$NEEDLE" -gt 0 ]; then
  echo "== gate 4: needle @ ${NEEDLE} tokens"
  python3 validate/needle.py --url "$URL" --model "$NAME" --context-tokens "$NEEDLE" --depths 0.05 0.5 0.95 | tail -4 || FAIL=1
fi

echo
if [ "$FAIL" = 0 ]; then echo "GATES PASSED for $NAME (artifacts: ${P}-*)"; else echo "GATE FAILURE for $NAME — see above; do not promote"; fi
exit $FAIL
