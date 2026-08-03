#!/usr/bin/env bash
# Start a 2-node vLLM server: worker (headless) on $WORKER_IP via SSH, then
# head (API) on this node. Both containers run on --network=host with the
# NCCL env validated in Step 0 (cluster/cluster-env.sh).
#
#   cluster/start-cluster.sh <model-name> [--spec-decode|--no-spec-decode]
#                            [--skip-preflight] [--skip-warmup] [--dry-run]
#
# Multi-node backend: vLLM native --nnodes/--node-rank/--headless with the mp
# executor (torch.distributed over RoCE). NOT Ray — see docs/MULTINODE.md for
# the measured justification.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
require_cluster_ips || exit 1

MODEL_NAME="${1:?usage: cluster/start-cluster.sh <model-name> [--spec-decode|--no-spec-decode] [--skip-preflight] [--skip-warmup] [--dry-run] [--force]}"
shift
SPEC_MODE=auto SKIP_PREFLIGHT=0 SKIP_WARMUP=0 DRY_RUN=0 FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --spec-decode) set_spec_decode_mode SPEC_MODE on ;;
    --no-spec-decode) set_spec_decode_mode SPEC_MODE off ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    --skip-warmup) SKIP_WARMUP=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

load_conf "$MODEL_NAME"
resolve_spec_decode "$SPEC_MODE"
if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "$MODEL_NAME status=$STATUS — refuse start without --force (allowlist: tested*)" >&2
  exit 1
fi

[ "$NODES" = "2" ] || { echo "$MODEL_NAME is a single-node config; use ./serve.sh" >&2; exit 1; }
echo "[cluster] spec-decode=$([ "$SPEC_DECODE_ENABLED" = 1 ] && echo ON || echo off) ($SPEC_DECODE_SOURCE)"
if [ "$SKIP_PREFLIGHT" = "0" ]; then
  cluster/preflight.sh "$MODEL_NAME" || { echo "[cluster] preflight FAILED — not starting. (--skip-preflight to override at your own risk)" >&2; exit 1; }
fi

CONTAINER="vllm-cluster-${MODEL_NAME}"
MODELS_NFS="${MODELS_NFS:-/mnt/Models}"

# Build docker argv (arrays). Quote only when serializing for SSH.
# shellcheck disable=SC2206
build_docker_cmd() {
  local role_ip="$1"
  local role_rank="$2"
  shift 2
  local -a role_suffix=("$@")
  # Always emit bare "docker" so the same argv is safe over SSH on the worker.
  # Local head execution rewrites argv[0] to "$PULSAR_DOCKER" when needed.
  local -a cmd=(
    docker run -d
    --name "$CONTAINER"
    --label "${PULSAR_MANAGED_LABEL}=true"
    --label "${PULSAR_CONF_LABEL}=${MODEL_NAME}"
    --label "${PULSAR_RANK_LABEL}=${role_rank}"
    --network host
    --ipc host
    --gpus all
    --ulimit memlock=-1
    --ulimit stack=67108864
    --device /dev/infiniband
    -v "${HF_CACHE}:/root/.cache/huggingface"
    -v "${MODELS_NFS}:/mnt/Models:ro"
    -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}"
    -e "VLLM_HOST_IP=${role_ip}"
    -e "NCCL_IB_HCA=${NCCL_IB_HCA}"
    -e "NCCL_IB_QPS_PER_CONNECTION=${NCCL_IB_QPS_PER_CONNECTION}"
    -e "NCCL_SOCKET_IFNAME=${NCCL_SOCKET_IFNAME}"
    -e "GLOO_SOCKET_IFNAME=${GLOO_SOCKET_IFNAME}"
    -e "TP_SOCKET_IFNAME=${TP_SOCKET_IFNAME}"
    -e "NCCL_IB_DISABLE=0"
    -e "NCCL_DEBUG=${NCCL_DEBUG}"
  )
  local e
  for e in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do
    cmd+=(-e "$e")
  done
  for e in ${EXTRA_ENV:-}; do
    cmd+=(-e "$e")
  done
  cmd+=(
    "$IMAGE"
    --model "$MODEL"
    --served-model-name "$SERVED_NAME"
    --host 0.0.0.0
    --port "$PORT"
    --gpu-memory-utilization "$GPU_MEM_UTIL"
  )
  cmd+=(${ENGINE_ARGS[@]+"${ENGINE_ARGS[@]}"})
  cmd+=(
    --nnodes 2
    --master-addr "$HEAD_IP"
    --master-port "$MASTER_PORT"
  )
  [ "$SPEC_DECODE_ENABLED" = "1" ] && cmd+=("${SPEC_DECODE_ARGS[@]}")
  # Capture into a temp then copy — append_vllm_extra_args needs a named global
  _EXTRA_CMD=()
  append_vllm_extra_args _EXTRA_CMD
  cmd+=("${_EXTRA_CMD[@]+"${_EXTRA_CMD[@]}"}")
  cmd+=("${role_suffix[@]}")
  _DOCKER_CMD=("${cmd[@]}")
}

shell_join_q() {
  local a out="" x
  for x in "$@"; do
    out+="$(printf '%q' "$x") "
  done
  printf '%s' "${out% }"
}

build_docker_cmd "$WORKER_IP" 1 --node-rank 1 --headless
WORKER_CMD=("${_DOCKER_CMD[@]}")
build_docker_cmd "$HEAD_IP" 0 --node-rank 0
HEAD_CMD=("${_DOCKER_CMD[@]}")
# API auth on head only (worker is headless; open lab default when unset)
_api_key="${VLLM_API_KEY:-${API_KEY:-}}"
if [ -n "$_api_key" ]; then
  HEAD_CMD+=(--api-key "$_api_key")
fi

if [ "$DRY_RUN" = "1" ]; then
  echo "WORKER (ssh $WORKER_IP):"
  echo "  $(shell_join_q_redacted "${WORKER_CMD[@]}")"
  echo "HEAD (local):"
  echo "  $(shell_join_q_redacted "${HEAD_CMD[@]}")"
  if [ -n "$_api_key" ]; then
    echo "[cluster] API key auth enabled on head (VLLM_API_KEY/API_KEY)"
  else
    echo "[cluster] API open on head (no VLLM_API_KEY) — lab network only"
  fi
  exit 0
fi

# Immutable IDs created by this launch only. Abort cleanup uses IDs — never
# name-based rm (avoids clobbering a reused/replacement name).
HEAD_CID=""
WORKER_CID=""

# Best-effort teardown of containers started by this invocation only.
cluster_abort() {
  local why="${1:-cluster start failed}"
  echo "[cluster] ABORT: $why — removing launch-tracked IDs only (not by name)" >&2
  if [ -n "${HEAD_CID:-}" ]; then
    echo "[cluster] abort: remove head id=${HEAD_CID:0:12}" >&2
    remove_container_id_local "$HEAD_CID"
  fi
  if [ -n "${WORKER_CID:-}" ]; then
    echo "[cluster] abort: remove worker id=${WORKER_CID:0:12}" >&2
    remove_container_id_remote "$WORKER_IP" "$WORKER_CID"
  fi
}

echo "[cluster] removing stale stack-managed containers (ownership required)"
stale_rc=0
remove_stack_owned_cluster_pair "$MODEL_NAME" "$CONTAINER" "$WORKER_IP" || stale_rc=$?
if [ "$stale_rc" -eq 2 ]; then
  echo "[cluster] ERROR: refusing start — ownership not proven on every existing rank of $CONTAINER" >&2
  echo "[cluster] Will not partially replace an ambiguous pair. Inspect labels or stop manually." >&2
  exit 1
fi
if [ "$stale_rc" -ne 0 ]; then
  echo "[cluster] ERROR: failed while removing stale cluster containers (rc=$stale_rc)" >&2
  exit 1
fi

echo "[cluster] starting worker on $WORKER_IP"
worker_raw=""
if ! worker_raw=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$WORKER_IP" \
  "$(shell_join_q "${WORKER_CMD[@]}")"); then
  WORKER_CID=""
  cluster_abort "worker docker run failed"
  exit 1
fi
if ! WORKER_CID=$(parse_docker_run_container_id "$worker_raw"); then
  WORKER_CID=""
  echo "[cluster] ERROR: worker docker run returned invalid id output (refuse to track/rm garbage)" >&2
  # Cannot safely bind an ID to this invocation — do not rm by name. Report only.
  report_untracked_launch_container worker "$MODEL_NAME" 1 "$CONTAINER" "$WORKER_IP"
  cluster_abort "worker docker run id invalid"
  exit 1
fi
echo "[cluster] worker id=${WORKER_CID:0:12}"

echo "[cluster] starting head on this node"
# Local injectability: tests set PULSAR_DOCKER; production is plain docker.
HEAD_RUN=("${HEAD_CMD[@]}")
HEAD_RUN[0]="$PULSAR_DOCKER"
head_raw=""
if ! head_raw=$("${HEAD_RUN[@]}"); then
  HEAD_CID=""
  cluster_abort "head docker run failed (worker was started — removed by tracked id)"
  exit 1
fi
if ! HEAD_CID=$(parse_docker_run_container_id "$head_raw"); then
  HEAD_CID=""
  echo "[cluster] ERROR: head docker run returned invalid id output (refuse to track/rm garbage)" >&2
  # Leave any untracked head container intact; still tear down tracked worker id.
  report_untracked_launch_container head "$MODEL_NAME" 0 "$CONTAINER"
  cluster_abort "head docker run id invalid"
  exit 1
fi
echo "[cluster] head id=${HEAD_CID:0:12}"

echo "[cluster] waiting for http://127.0.0.1:${PORT}/health (cold load can take ~10 min)"
API_AUTH_ARGS=()
api_auth_curl_args API_AUTH_ARGS
for i in $(seq 1 "${WAIT_ATTEMPTS:-120}"); do
  if curl -fsS --max-time 3 "${API_AUTH_ARGS[@]}" "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "[cluster] healthy."
    # /health alone is not enough (see docs/OPERATIONS.md). Prefer a real
    # completion path: post-boot warmup pays DSpark/Triton/block-FP8 JIT so
    # the first client after restart does not eat cold first-token spikes.
    if [ "$SKIP_WARMUP" = "1" ]; then
      echo "[cluster] --skip-warmup: single smoke only"
      curl -fsS --max-time 120 "http://127.0.0.1:${PORT}/v1/completions" \
        "${API_AUTH_ARGS[@]}" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${SERVED_NAME}\",\"prompt\":\"2+2=\",\"max_tokens\":4,\"temperature\":0}" && echo
    else
      echo "[cluster] post-boot warmup (short+medium, c=1/4, stream+sync):"
      python3 "$REPO_DIR/validate/warmup.py" \
        --url "http://127.0.0.1:${PORT}" \
        --model "$SERVED_NAME" || {
          echo "[cluster] warmup FAILED — server is up but first-token JIT not paid (containers left running)" >&2
          exit 1
        }
    fi
    exit 0
  fi
  # fail fast if either container died (exact name — not Docker substring filter)
  if ! container_running_exact "$CONTAINER"; then
    echo "[cluster] head container died; last logs:" >&2
    docker logs --tail 80 "$CONTAINER" >&2 || true
    cluster_abort "head container exited during wait"
    exit 1
  fi
  if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$WORKER_IP" "docker ps --format '{{.Names}}'" 2>/dev/null \
      | filter_exact_container_name "$CONTAINER" | grep -q .; then
    echo "[cluster] worker container died; last logs:" >&2
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$WORKER_IP" "docker logs --tail 80 $(printf '%q' "$CONTAINER")" >&2 || true
    cluster_abort "worker container exited during wait"
    exit 1
  fi
  sleep "${WAIT_SECONDS:-10}"
done
echo "[cluster] timed out. Head logs:" >&2
docker logs --tail 120 "$CONTAINER" >&2 || true
echo "[cluster] Worker logs:" >&2
"$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$WORKER_IP" "docker logs --tail 120 $(printf '%q' "$CONTAINER")" >&2 || true
cluster_abort "health wait timed out"
exit 1
