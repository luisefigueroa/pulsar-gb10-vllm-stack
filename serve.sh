#!/usr/bin/env bash
# Serve a model from models/<name>.conf on this node.
#
#   ./serve.sh <model-name> [-d] [--spec-decode] [--dry-run] [--port N]
#   ./serve.sh --list
#
# <model-name> is a file in models/ without the .conf suffix.
# -d            detach (docker -d) — use this for anything long-running
# --spec-decode enable the model's validated speculative-decode config (off by default)
# --dry-run     print the docker command instead of running it
#
# 2-node models (NODES=2 in the conf) are refused here: use cluster/start-cluster.sh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

[ -f .env ] && { set -a; . ./.env; set +a; }
. cluster/cluster-env.sh

# Pinned defaults; override via .env
VLLM_IMAGE_MAINLINE="${VLLM_IMAGE_MAINLINE:-vllm/vllm-openai:v0.26.0}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
MODELS_NFS="${MODELS_NFS:-/mnt/Models}"

if [ "${1:-}" = "--list" ]; then
  for f in models/*.conf; do
    # shellcheck disable=SC1090
    ( . "$f"; printf "%-28s nodes=%s status=%-9s %s\n" "$(basename "$f" .conf)" "${NODES:-1}" "${STATUS:-?}" "${MODEL:-?}" )
  done
  exit 0
fi

MODEL_NAME="${1:?usage: ./serve.sh <model-name> [-d] [--spec-decode] [--dry-run] [--port N] (see --list)}"
shift
CONF="models/${MODEL_NAME}.conf"
[ -f "$CONF" ] || { echo "No such config: $CONF (try ./serve.sh --list)" >&2; exit 1; }

DETACH="" SPEC_DECODE=0 DRY_RUN=0 PORT_OVERRIDE=""
while [ $# -gt 0 ]; do
  case "$1" in
    -d) DETACH="-d" ;;
    --spec-decode) SPEC_DECODE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --port) PORT_OVERRIDE="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

# Conf contract: MODEL SERVED_NAME [IMAGE] [NODES] [PORT] [GPU_MEM_UTIL]
# ENGINE_ARGS[] [CONTAINER_ENV[]] [SPEC_DECODE_ARGS[]] [STATUS] [NOTES]
NODES=1 PORT=8000 GPU_MEM_UTIL=0.80 IMAGE="" CONTAINER_ENV=() SPEC_DECODE_ARGS=()
# shellcheck disable=SC1090
. "$CONF"
IMAGE="${IMAGE:-$VLLM_IMAGE_MAINLINE}"
PORT="${PORT_OVERRIDE:-$PORT}"

if [ "$NODES" != "1" ]; then
  echo "$MODEL_NAME is a ${NODES}-node config. Use: cluster/start-cluster.sh $MODEL_NAME" >&2
  exit 1
fi

if [ "$SPEC_DECODE" = "1" ] && [ "${#SPEC_DECODE_ARGS[@]}" -eq 0 ]; then
  echo "$MODEL_NAME has no validated SPEC_DECODE_ARGS; refusing to guess." >&2
  exit 1
fi

CONTAINER="vllm-${MODEL_NAME}"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

CMD=(docker run --name "$CONTAINER" $DETACH
  --gpus all
  --ipc=host                       # vLLM workers die opaquely without big SHM
  --ulimit memlock=-1 --ulimit stack=67108864
  -p "${PORT}:${PORT}"
  -v "${HF_CACHE}:/root/.cache/huggingface"
  -v "${MODELS_NFS}:/mnt/Models:ro"
  -e HF_TOKEN="${HF_TOKEN:-}"
  -e HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"    # weights are local; no surprise downloads
  -e VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-INFO}"
  --health-cmd "curl -fs http://localhost:${PORT}/health || exit 1"
  --health-interval 30s --health-timeout 5s --health-retries 3
  --health-start-period "${HEALTH_START_PERIOD:-900s}"   # cold weight load is slow (NFS/EXT4)
  --restart "${RESTART_POLICY:-no}"
)
for e in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do CMD+=(-e "$e"); done
# EXTRA_ENV="A=1 B=2" escape hatch, mirroring VLLM_EXTRA_ARGS for env vars
for e in ${EXTRA_ENV:-}; do CMD+=(-e "$e"); done

CMD+=("$IMAGE"
  --model "$MODEL"
  --served-model-name "$SERVED_NAME"
  --host 0.0.0.0 --port "$PORT"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
  ${ENGINE_ARGS[@]+"${ENGINE_ARGS[@]}"}
)
[ "$SPEC_DECODE" = "1" ] && CMD+=("${SPEC_DECODE_ARGS[@]}")
# Escape hatch for experiments; never required by a shipped config
[ -n "${VLLM_EXTRA_ARGS:-}" ] && CMD+=($VLLM_EXTRA_ARGS)

if [ "$DRY_RUN" = "1" ]; then printf '%q ' "${CMD[@]}"; echo; exit 0; fi

echo "[serve] $MODEL_NAME ($MODEL) on port $PORT, image $IMAGE"
[ -n "${NOTES:-}" ] && echo "[serve] notes: $NOTES"
exec "${CMD[@]}"
