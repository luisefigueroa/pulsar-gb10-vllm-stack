#!/usr/bin/env bash
# Start an exact validated N-node vLLM profile: remote headless ranks first,
# then local rank 0 with the API. Every active rank is one GB10.
#
#   cluster/start-cluster.sh <model-name> [--spec-decode|--no-spec-decode]
#                            [--weight-source replicated|fabric]
#                            [--skip-preflight] [--skip-warmup] [--dry-run]
#
# Backend: vLLM native --nnodes/--node-rank with the mp executor over RoCE.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

MODEL_NAME="${1:?usage: cluster/start-cluster.sh <model-name> [options]}"
shift
SPEC_MODE=auto
SKIP_PREFLIGHT=0
SKIP_WARMUP=0
DRY_RUN=0
FORCE=0
WEIGHT_SOURCE=replicated
while [ $# -gt 0 ]; do
  case "$1" in
    --spec-decode) set_spec_decode_mode SPEC_MODE on ;;
    --no-spec-decode) set_spec_decode_mode SPEC_MODE off ;;
    --skip-preflight) SKIP_PREFLIGHT=1 ;;
    --skip-warmup) SKIP_WARMUP=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    --weight-source)
      [ "$#" -ge 2 ] || die "--weight-source requires replicated or fabric" 2
      WEIGHT_SOURCE="$2"
      shift
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

load_conf "$MODEL_NAME"
case "$WEIGHT_SOURCE" in
  replicated|fabric) ;;
  *) die "--weight-source must be replicated or fabric" 2 ;;
esac
resolve_spec_decode "$SPEC_MODE"
if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "$MODEL_NAME status=$STATUS — refuse start without --force (allowlist: tested*)" >&2
  exit 1
fi
if [ "$NODES" -le 1 ]; then
  echo "$MODEL_NAME is a single-node profile; use ./serve.sh" >&2
  exit 1
fi
require_profile_topology "$NODES" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
  || exit 1

declare -a WEIGHT_CACHE_ROOTS=()
for ((rank = 0; rank < NODES; rank++)); do
  WEIGHT_CACHE_ROOTS["$rank"]="$HF_CACHE"
done
WEIGHT_OWNER_ID=""
WEIGHT_CONFIG_ID=""
if [ "$WEIGHT_SOURCE" = fabric ]; then
  fabric_dir="${WEIGHT_FABRIC_DIR:-$REPO_DIR/.weight-fabric}"
  fabric_config="${WEIGHT_FABRIC_CONFIG:-$fabric_dir/$MODEL_NAME.json}"
  fabric_rows=$(
    "$REPO_DIR/scripts/weight_fabric.py" rows \
      "$fabric_config" "$CLUSTER_TOPOLOGY_FILE" \
      --profile "$MODEL_NAME" --model "$MODEL" --nodes "$NODES"
  ) || die "single-copy fabric configuration is missing, invalid, or stale"
  while IFS=$'\t' read -r kind a b c d e f g h i j k l m n o p; do
    case "$kind" in
      META)
        WEIGHT_CONFIG_ID="$a"
        WEIGHT_OWNER_ID="$g"
        ;;
      RANK)
        WEIGHT_CACHE_ROOTS["$a"]="$f"
        ;;
    esac
  done <<<"$fabric_rows"
  [ -n "$WEIGHT_CONFIG_ID" ] && [ -n "$WEIGHT_OWNER_ID" ] \
    || die "single-copy fabric configuration has incomplete runtime rows"
  "$PULSAR_WEIGHT_FABRIC_TOOL" check "$MODEL_NAME" --serving-only \
    || die "single-copy fabric is not launch-ready"
fi

echo "[cluster] exact profile: $MODEL_NAME · $NODES ranks · topology ${CLUSTER_TOPOLOGY_ID:0:12}"
echo "[cluster] weights: $WEIGHT_SOURCE$([ "$WEIGHT_SOURCE" = fabric ] && printf ' · NFS/RDMA · cold reads cross the fabric')"
echo "[cluster] spec-decode=$([ "$SPEC_DECODE_ENABLED" = 1 ] && echo ON || echo off) ($SPEC_DECODE_SOURCE)"
if [ "$SKIP_PREFLIGHT" = 0 ]; then
  cluster/preflight.sh "$MODEL_NAME" --weight-source "$WEIGHT_SOURCE" || {
    echo "[cluster] preflight FAILED — not starting. (--skip-preflight to override at your own risk)" >&2
    exit 1
  }
fi

CONTAINER="$(container_name_for "$MODEL_NAME" "$NODES")"
MODELS_NFS="${MODELS_NFS:-/mnt/Models}"
MASTER_ADDR="${CLUSTER_NODE_CONTROL_IPS[0]}"

# Build docker argv for one rank. _DOCKER_CMD is the output array.
build_docker_cmd() {
  local role_rank="${1:?rank required}"
  shift
  local -a role_suffix=("$@")
  local role_ip="${CLUSTER_NODE_CONTROL_IPS[$role_rank]}"
  local control_if="${CLUSTER_NODE_CONTROL_IFS[$role_rank]}"
  local hcas="${CLUSTER_PROFILE_HCAS[$role_rank]}"
  local node_id="${CLUSTER_NODE_IDS[$role_rank]}"
  local weight_cache="${WEIGHT_CACHE_ROOTS[$role_rank]}"
  local weight_volume="${weight_cache}:/root/.cache/huggingface"
  [ "$WEIGHT_SOURCE" = fabric ] && weight_volume+=":ro"
  [ -n "$role_ip" ] || die "rank $role_rank has no control IP"
  [ -n "$control_if" ] || die "rank $role_rank has no control interface"
  [ -n "$hcas" ] || die "rank $role_rank has no active RDMA HCA"

  # Bare docker is serialized to remote ranks. Local rank 0 replaces argv[0]
  # with PULSAR_DOCKER immediately before execution.
  local -a cmd=(
    docker run -d
    --name "$CONTAINER"
    --label "${PULSAR_MANAGED_LABEL}=true"
    --label "${PULSAR_CONF_LABEL}=${MODEL_NAME}"
    --label "${PULSAR_RANK_LABEL}=${role_rank}"
    --label "${PULSAR_WORLD_SIZE_LABEL}=${NODES}"
    --label "${PULSAR_TOPOLOGY_LABEL}=${CLUSTER_TOPOLOGY_ID}"
    --label "${PULSAR_NODE_ID_LABEL}=${node_id}"
    --network host
    --ipc host
    --gpus all
    --ulimit memlock=-1
    --ulimit stack=67108864
    --device /dev/infiniband
    -v "$weight_volume"
    -v "${MODELS_NFS}:/mnt/Models:ro"
    -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}"
    -e "VLLM_HOST_IP=${role_ip}"
    -e "NCCL_NET=IB"
    -e "NCCL_IB_HCA=${hcas}"
    -e "NCCL_IB_QPS_PER_CONNECTION=${NCCL_IB_QPS_PER_CONNECTION}"
    -e "NCCL_SOCKET_IFNAME=${control_if}"
    -e "GLOO_SOCKET_IFNAME=${control_if}"
    -e "TP_SOCKET_IFNAME=${control_if}"
    -e "NCCL_IB_DISABLE=0"
    -e "NCCL_DEBUG=${NCCL_DEBUG}"
  )
  cmd+=(--label "${PULSAR_WEIGHT_SOURCE_LABEL}=${WEIGHT_SOURCE}")
  if [ "$WEIGHT_SOURCE" = fabric ]; then
    cmd+=(
      --label "${PULSAR_WEIGHT_OWNER_LABEL}=${WEIGHT_OWNER_ID}"
      --label "${PULSAR_WEIGHT_CONFIG_LABEL}=${WEIGHT_CONFIG_ID}"
    )
  fi
  local env_item
  for env_item in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do
    cmd+=(-e "$env_item")
  done
  for env_item in ${EXTRA_ENV:-}; do
    cmd+=(-e "$env_item")
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
    --nnodes "$NODES"
    --master-addr "$MASTER_ADDR"
    --master-port "$MASTER_PORT"
  )
  [ "$SPEC_DECODE_ENABLED" = 1 ] && cmd+=("${SPEC_DECODE_ARGS[@]}")
  _EXTRA_CMD=()
  append_vllm_extra_args _EXTRA_CMD
  cmd+=(${_EXTRA_CMD[@]+"${_EXTRA_CMD[@]}"})
  cmd+=("${role_suffix[@]}")
  _DOCKER_CMD=("${cmd[@]}")
}

shell_join_q() {
  local output="" value
  for value in "$@"; do
    output+="$(printf '%q' "$value") "
  done
  printf '%s' "${output% }"
}

declare -A REMOTE_COMMANDS=()
declare -A REMOTE_REDACTED=()
for ((rank = 1; rank < NODES; rank++)); do
  build_docker_cmd "$rank" --node-rank "$rank" --headless
  REMOTE_COMMANDS["$rank"]="$(shell_join_q "${_DOCKER_CMD[@]}")"
  REMOTE_REDACTED["$rank"]="$(shell_join_q_redacted "${_DOCKER_CMD[@]}")"
done

build_docker_cmd 0 --node-rank 0
HEAD_CMD=("${_DOCKER_CMD[@]}")
_api_key="${VLLM_API_KEY:-${API_KEY:-}}"
if [ -n "$_api_key" ]; then
  HEAD_CMD+=(--api-key "$_api_key")
fi

if [ "$DRY_RUN" = 1 ]; then
  echo "RANKS"
  for ((rank = 1; rank < NODES; rank++)); do
    echo "  rank $rank · ssh ${CLUSTER_NODE_SSH_HOSTS[$rank]} · ${CLUSTER_NODE_HOSTNAMES[$rank]}"
    echo "    ${REMOTE_REDACTED[$rank]}"
  done
  echo "  rank 0 · local · ${CLUSTER_NODE_HOSTNAMES[0]}"
  echo "    $(shell_join_q_redacted "${HEAD_CMD[@]}")"
  if [ -n "$_api_key" ]; then
    echo "[cluster] API key auth enabled on rank 0 (secret redacted)"
  else
    echo "[cluster] API open on rank 0 (no VLLM_API_KEY) — trusted lab network only"
  fi
  exit 0
fi

declare -A TRACKED_CIDS=()

record_startup_metric() {
  local destination="${PULSAR_STARTUP_METRICS_FILE:-}"
  local started_at="${1:?}" healthy_at="${2:?}" elapsed="${3:?}"
  local -a metric_args
  [ -n "$destination" ] || return 0
  metric_args=(
    startup-metric
    --output "$destination"
    --profile "$MODEL_NAME"
    --model "$MODEL"
    --weight-source "$WEIGHT_SOURCE"
    --nodes "$NODES"
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --cache-state "${PULSAR_STARTUP_CACHE_STATE:-unspecified}"
    --started-at "$started_at"
    --first-healthy-at "$healthy_at"
    --elapsed-seconds "$elapsed"
  )
  [ -n "$WEIGHT_CONFIG_ID" ] \
    && metric_args+=(--configuration-id "$WEIGHT_CONFIG_ID")
  [ -n "$WEIGHT_OWNER_ID" ] \
    && metric_args+=(--owner-node-id "$WEIGHT_OWNER_ID")
  [ -n "${PULSAR_STARTUP_TAG:-}" ] \
    && metric_args+=(--tag "$PULSAR_STARTUP_TAG")
  "$REPO_DIR/scripts/weight_fabric.py" "${metric_args[@]}"
}

# Best-effort teardown by immutable IDs created by this invocation only.
cluster_abort() {
  local why="${1:-cluster start failed}" rank host
  echo "[cluster] ABORT: $why — removing launch-tracked IDs only" >&2
  if [ -n "${TRACKED_CIDS[0]:-}" ]; then
    echo "[cluster] abort: remove rank 0 id=${TRACKED_CIDS[0]:0:12}" >&2
    remove_container_id_local "${TRACKED_CIDS[0]}"
  fi
  for ((rank = NODES - 1; rank >= 1; rank--)); do
    [ -n "${TRACKED_CIDS[$rank]:-}" ] || continue
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    echo "[cluster] abort: remove rank $rank id=${TRACKED_CIDS[$rank]:0:12} on $host" >&2
    remove_container_id_remote "$host" "${TRACKED_CIDS[$rank]}"
  done
}

echo "[cluster] removing stale stack-managed ranks (ownership required)"
stale_rc=0
remove_stack_owned_cluster "$MODEL_NAME" "$CONTAINER" "$NODES" || stale_rc=$?
if [ "$stale_rc" -eq 2 ]; then
  echo "[cluster] ERROR: ownership not proven on every existing rank of $CONTAINER" >&2
  echo "[cluster] No ambiguous rank was removed. Inspect labels or stop manually." >&2
  exit 1
fi
if [ "$stale_rc" -ne 0 ]; then
  echo "[cluster] ERROR: failed while removing stale cluster ranks (rc=$stale_rc)" >&2
  exit 1
fi

STARTUP_STARTED_NS=$(date +%s%N)
STARTUP_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
for ((rank = 1; rank < NODES; rank++)); do
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  echo "[cluster] starting rank $rank on $host"
  raw_id=""
  if ! raw_id=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
      "${REMOTE_COMMANDS[$rank]}"); then
    cluster_abort "rank $rank docker run failed"
    exit 1
  fi
  if ! TRACKED_CIDS["$rank"]=$(parse_docker_run_container_id "$raw_id"); then
    unset "TRACKED_CIDS[$rank]"
    echo "[cluster] ERROR: rank $rank docker run returned an invalid ID" >&2
    report_untracked_launch_container remote "$MODEL_NAME" "$rank" "$CONTAINER" "$host"
    cluster_abort "rank $rank docker run ID invalid"
    exit 1
  fi
  echo "[cluster] rank $rank id=${TRACKED_CIDS[$rank]:0:12}"
done

echo "[cluster] starting rank 0 locally"
HEAD_RUN=("${HEAD_CMD[@]}")
HEAD_RUN[0]="$PULSAR_DOCKER"
head_raw=""
if ! head_raw=$("${HEAD_RUN[@]}"); then
  cluster_abort "rank 0 docker run failed"
  exit 1
fi
if ! TRACKED_CIDS[0]=$(parse_docker_run_container_id "$head_raw"); then
  unset 'TRACKED_CIDS[0]'
  echo "[cluster] ERROR: rank 0 docker run returned an invalid ID" >&2
  report_untracked_launch_container head "$MODEL_NAME" 0 "$CONTAINER"
  cluster_abort "rank 0 docker run ID invalid"
  exit 1
fi
echo "[cluster] rank 0 id=${TRACKED_CIDS[0]:0:12}"

echo "[cluster] waiting for http://127.0.0.1:${PORT}/health (cold load can take ~10 min)"
API_AUTH_ARGS=()
api_auth_curl_args API_AUTH_ARGS
for _attempt in $(seq 1 "${WAIT_ATTEMPTS:-120}"); do
  if curl -fsS --max-time 3 "${API_AUTH_ARGS[@]}" \
      "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    STARTUP_HEALTHY_NS=$(date +%s%N)
    STARTUP_HEALTHY_AT=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
    STARTUP_ELAPSED=$(
      python3 -c \
        'import sys; print(f"{(int(sys.argv[2])-int(sys.argv[1]))/1e9:.3f}")' \
        "$STARTUP_STARTED_NS" "$STARTUP_HEALTHY_NS"
    )
    echo "[cluster] healthy · first-health=${STARTUP_ELAPSED}s."
    if ! record_startup_metric "$STARTUP_STARTED_AT" \
        "$STARTUP_HEALTHY_AT" "$STARTUP_ELAPSED"; then
      warn "could not write PULSAR_STARTUP_METRICS_FILE; service is healthy"
    fi
    if [ "$SKIP_WARMUP" = 1 ]; then
      echo "[cluster] --skip-warmup: single smoke only"
      curl -fsS --max-time 120 \
        "http://127.0.0.1:${PORT}/v1/completions" \
        "${API_AUTH_ARGS[@]}" \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"${SERVED_NAME}\",\"prompt\":\"2+2=\",\"max_tokens\":4,\"temperature\":0}" \
        && echo
    else
      echo "[cluster] post-boot warmup (short+medium, c=1/4, stream+sync):"
      python3 "$REPO_DIR/validate/warmup.py" \
        --url "http://127.0.0.1:${PORT}" \
        --model "$SERVED_NAME" || {
          echo "[cluster] warmup FAILED — server is up but first-token JIT not paid" >&2
          exit 1
        }
    fi
    exit 0
  fi

  if ! container_running_exact "$CONTAINER"; then
    echo "[cluster] rank 0 container died; last logs:" >&2
    "$PULSAR_DOCKER" logs --tail 80 "$CONTAINER" >&2 || true
    cluster_abort "rank 0 exited during health wait"
    exit 1
  fi
  for ((rank = 1; rank < NODES; rank++)); do
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    if ! container_running_exact_remote "$host" "$CONTAINER"; then
      echo "[cluster] rank $rank container died on $host; last logs:" >&2
      "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
        "docker logs --tail 80 $(printf '%q' "$CONTAINER")" >&2 || true
      cluster_abort "rank $rank exited during health wait"
      exit 1
    fi
  done
  sleep "${WAIT_SECONDS:-10}"
done

echo "[cluster] timed out. Rank 0 logs:" >&2
"$PULSAR_DOCKER" logs --tail 120 "$CONTAINER" >&2 || true
for ((rank = 1; rank < NODES; rank++)); do
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  echo "[cluster] rank $rank logs ($host):" >&2
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
    "docker logs --tail 120 $(printf '%q' "$CONTAINER")" >&2 || true
done
cluster_abort "health wait timed out"
exit 1
