#!/usr/bin/env bash
# Start a 2-node vLLM server: worker (headless) on $WORKER_IP via SSH, then
# head (API) on this node. Both containers run on --network=host with the
# NCCL env validated in Step 0 (cluster/cluster-env.sh).
#
#   cluster/start-cluster.sh <model-name> [--spec-decode] [--skip-preflight] [--dry-run]
#
# Multi-node backend: vLLM native --nnodes/--node-rank/--headless with the mp
# executor (torch.distributed over RoCE). NOT Ray — see docs/MULTINODE.md for
# the measured justification.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
. cluster/cluster-env.sh

VLLM_IMAGE_MAINLINE="${VLLM_IMAGE_MAINLINE:-vllm/vllm-openai:v0.26.0}"
VLLM_IMAGE_DSV4="${VLLM_IMAGE_DSV4:-aidendle94/sparkrun-vllm-ds4-gb10:production-ready}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"

MODEL_NAME="${1:?usage: cluster/start-cluster.sh <model-name> [--spec-decode] [--skip-preflight] [--dry-run]}"
shift
SPEC_DECODE=0 SKIP_PREFLIGHT=0 DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --spec-decode) SPEC_DECODE=1 ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

CONF="models/${MODEL_NAME}.conf"
[ -f "$CONF" ] || { echo "No such config: $CONF" >&2; exit 1; }
NODES=1 PORT=8000 GPU_MEM_UTIL=0.80 IMAGE="" IMAGE_KIND="openai"
CONTAINER_ENV=() SPEC_DECODE_ARGS=() ENGINE_ARGS=()
# shellcheck disable=SC1090
. "$CONF"
IMAGE="${IMAGE:-$VLLM_IMAGE_MAINLINE}"

[ "$NODES" = "2" ] || { echo "$MODEL_NAME is a single-node config; use ./serve.sh" >&2; exit 1; }
if [ "$SPEC_DECODE" = "1" ] && [ "${#SPEC_DECODE_ARGS[@]}" -eq 0 ]; then
  echo "$MODEL_NAME has no validated SPEC_DECODE_ARGS; refusing to guess." >&2; exit 1
fi

if [ "$SKIP_PREFLIGHT" = "0" ]; then
  cluster/preflight.sh "$MODEL_NAME" || { echo "[cluster] preflight FAILED — not starting. (--skip-preflight to override at your own risk)" >&2; exit 1; }
fi

CONTAINER="vllm-cluster-${MODEL_NAME}"

# Docker flags shared by both roles. RDMA needs the devices passed explicitly:
# --network=host does NOT expose /dev/infiniband, and NCCL silently falls back
# to TCP without it.
common_docker_flags() {
  local role_ip="$1"
  # the sparkrun/dsv4 image reads HF_HOME=/cache/huggingface (its baked
  # convention, from the validated production launcher); official images
  # default to /root/.cache/huggingface
  local hf_mount="-v ${HF_CACHE}:/root/.cache/huggingface"
  if [ "$IMAGE_KIND" = "dsv4" ]; then
    hf_mount="-v ${HF_CACHE}:/cache/huggingface -e HF_HOME=/cache/huggingface -e VLLM_CACHE_ROOT=/cache/huggingface/vllm-cache -e TRANSFORMERS_OFFLINE=1"
  fi
  echo "--network host --ipc host --gpus all \
    --ulimit memlock=-1 --ulimit stack=67108864 \
    --device /dev/infiniband \
    ${hf_mount} \
    -v ${MODELS_NFS:-/mnt/Models}:/mnt/Models:ro \
    -e HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1} \
    -e VLLM_HOST_IP=${role_ip} \
    -e NCCL_IB_HCA=${NCCL_IB_HCA} \
    -e NCCL_IB_QPS_PER_CONNECTION=${NCCL_IB_QPS_PER_CONNECTION} \
    -e NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME} \
    -e GLOO_SOCKET_IFNAME=${GLOO_SOCKET_IFNAME} \
    -e TP_SOCKET_IFNAME=${TP_SOCKET_IFNAME} \
    -e NCCL_IB_DISABLE=0 \
    -e NCCL_DEBUG=${NCCL_DEBUG}"
}

# Engine args shared by both roles (node-rank/headless appended per role).
server_args() {
  local args=(
    --served-model-name "$SERVED_NAME"
    --host 0.0.0.0 --port "$PORT"
    --gpu-memory-utilization "$GPU_MEM_UTIL"
    "${ENGINE_ARGS[@]}"
    --nnodes 2 --master-addr "$HEAD_IP" --master-port "$MASTER_PORT"
  )
  [ "$SPEC_DECODE" = "1" ] && args+=("${SPEC_DECODE_ARGS[@]}")
  printf '%q ' "${args[@]}"
}

container_cmd() {
  # role-specific suffix: worker gets --node-rank 1 --headless
  local role_args="$1"
  case "$IMAGE_KIND" in
    openai)
      echo "$(printf '%q ' --model "$MODEL") $(server_args) $role_args" ;;
    dsv4)
      # sparkrun image: custom entrypoint wraps vllm serve
      echo "--entrypoint bash $IMAGE -lc 'exec /usr/local/bin/dsv4-vllm-entrypoint serve $(printf '%q' "$MODEL") $(server_args) $role_args'"
      return ;;
    *) echo "unknown IMAGE_KIND=$IMAGE_KIND" >&2; exit 1 ;;
  esac
}

env_flags() { local out=""; for e in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do out+=" -e $(printf '%q' "$e")"; done; echo "$out"; }

WORKER_RUN="docker run -d --name $CONTAINER $(common_docker_flags "$WORKER_IP") $(env_flags)"
HEAD_RUN="docker run -d --name $CONTAINER $(common_docker_flags "$HEAD_IP") $(env_flags)"
if [ "$IMAGE_KIND" = "dsv4" ]; then
  WORKER_RUN+=" $(container_cmd '--node-rank 1 --headless')"
  HEAD_RUN+=" $(container_cmd '--node-rank 0')"
else
  WORKER_RUN+=" $IMAGE $(container_cmd '--node-rank 1 --headless')"
  HEAD_RUN+=" $IMAGE $(container_cmd '--node-rank 0')"
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "WORKER (ssh $WORKER_IP):"; echo "  $WORKER_RUN"; echo "HEAD (local):"; echo "  $HEAD_RUN"; exit 0
fi

echo "[cluster] removing stale containers"
ssh "$WORKER_IP" "docker rm -f $CONTAINER >/dev/null 2>&1 || true"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo "[cluster] starting worker on $WORKER_IP"
ssh "$WORKER_IP" "$WORKER_RUN"
echo "[cluster] starting head on this node"
eval "$HEAD_RUN"

echo "[cluster] waiting for http://127.0.0.1:${PORT}/health (cold load can take ~10 min)"
for i in $(seq 1 "${WAIT_ATTEMPTS:-120}"); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "[cluster] healthy. Smoke request:"
    curl -fsS --max-time 120 "http://127.0.0.1:${PORT}/v1/completions" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"${SERVED_NAME}\",\"prompt\":\"2+2=\",\"max_tokens\":4,\"temperature\":0}" && echo
    exit 0
  fi
  # fail fast if either container died
  if ! docker ps -q --filter "name=$CONTAINER" | grep -q . ; then
    echo "[cluster] head container died; last logs:" >&2
    docker logs --tail 80 "$CONTAINER" >&2 || true
    exit 1
  fi
  if ! ssh "$WORKER_IP" "docker ps -q --filter name=$CONTAINER" | grep -q . ; then
    echo "[cluster] worker container died; last logs:" >&2
    ssh "$WORKER_IP" "docker logs --tail 80 $CONTAINER" >&2 || true
    exit 1
  fi
  sleep "${WAIT_SECONDS:-10}"
done
echo "[cluster] timed out. Head logs:" >&2
docker logs --tail 120 "$CONTAINER" >&2 || true
echo "[cluster] Worker logs:" >&2
ssh "$WORKER_IP" "docker logs --tail 120 $CONTAINER" >&2 || true
exit 1
