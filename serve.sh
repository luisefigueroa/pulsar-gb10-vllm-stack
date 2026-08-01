#!/usr/bin/env bash
# Serve a model from models/<name>.conf on this node.
#
#   ./serve.sh <model-name> [-d] [--spec-decode] [--dry-run] [--port N] [--force]
#   ./serve.sh --list
#
# <model-name> is a file in models/ without the .conf suffix.
# -d            detach (docker -d)
# --spec-decode enable the model's validated speculative-decode config
# --dry-run     print the docker command instead of running it
# --force       allow non-tested STATUS (see scripts/lib.sh status gate)
#
# 2-node models (NODES=2): use cluster/start-cluster.sh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

if [ "${1:-}" = "--list" ]; then
  for f in models/*.conf; do
    name=$(basename "$f" .conf)
    # shellcheck disable=SC1090
    (
      load_conf "$name"
      printf "%-28s nodes=%s status=%-14s %s\n" "$name" "$NODES" "$STATUS" "$MODEL"
    )
  done
  exit 0
fi

MODEL_NAME="${1:?usage: ./serve.sh <model-name> [-d] [--spec-decode] [--dry-run] [--port N] [--force] (see --list)}"
shift

DETACH="" SPEC_DECODE=0 DRY_RUN=0 PORT_OVERRIDE="" FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -d) DETACH="-d" ;;
    --spec-decode) SPEC_DECODE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    --port) PORT_OVERRIDE="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

load_conf "$MODEL_NAME"
[ -n "$PORT_OVERRIDE" ] && PORT="$PORT_OVERRIDE"

if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "$MODEL_NAME status=$STATUS — refuse serve without --force (allowlist: tested*)" >&2
  exit 1
fi

if [ "$NODES" != "1" ]; then
  echo "$MODEL_NAME is a ${NODES}-node config. Use: cluster/start-cluster.sh $MODEL_NAME" >&2
  exit 1
fi

if [ "$SPEC_DECODE" = "1" ] && ! has_spec_args; then
  echo "$MODEL_NAME has no validated SPEC_DECODE_ARGS; refusing to guess." >&2
  exit 1
fi

CONTAINER=$(container_name_for "$MODEL_NAME" 1)
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

CMD=(docker run --name "$CONTAINER" ${DETACH:+$DETACH}
  --gpus all
  --ipc=host
  --ulimit memlock=-1 --ulimit stack=67108864
  -p "${PORT}:${PORT}"
  -v "${HF_CACHE}:/root/.cache/huggingface"
  -v "${MODELS_NFS}:/mnt/Models:ro"
  -e "HF_TOKEN=${HF_TOKEN:-}"
  -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}"
  -e "VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO}"
  --health-cmd "curl -fs http://localhost:${PORT}/health || exit 1"
  --health-interval 30s --health-timeout 5s --health-retries 3
  --health-start-period "${HEALTH_START_PERIOD:-900s}"
  --restart "${RESTART_POLICY:-no}"
)
for e in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do CMD+=(-e "$e"); done
for e in ${EXTRA_ENV:-}; do CMD+=(-e "$e"); done

CMD+=(
  "$IMAGE"
  --model "$MODEL"
  --served-model-name "$SERVED_NAME"
  --host 0.0.0.0 --port "$PORT"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
)
CMD+=(${ENGINE_ARGS[@]+"${ENGINE_ARGS[@]}"})
[ "$SPEC_DECODE" = "1" ] && CMD+=("${SPEC_DECODE_ARGS[@]}")
# shellcheck disable=SC2206
[ -n "${VLLM_EXTRA_ARGS:-}" ] && CMD+=($VLLM_EXTRA_ARGS)
_api_key="${VLLM_API_KEY:-${API_KEY:-}}"
if [ -n "$_api_key" ]; then
  CMD+=(--api-key "$_api_key")
fi

if [ "$DRY_RUN" = "1" ]; then printf '%q ' "${CMD[@]}"; echo; exit 0; fi

echo "[serve] $MODEL_NAME ($MODEL) on port $PORT, image $IMAGE container=$CONTAINER"
[ -n "${NOTES:-}" ] && echo "[serve] notes: $NOTES"
if [ -n "$_api_key" ]; then
  echo "[serve] API key auth enabled (VLLM_API_KEY/API_KEY)"
else
  echo "[serve] API open (no VLLM_API_KEY) — lab network only"
fi
exec "${CMD[@]}"
