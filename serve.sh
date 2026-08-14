#!/usr/bin/env bash
# Serve a model from models/<name>.conf on this node.
#
#   ./serve.sh <model-name> [-d] [--spec-decode|--no-spec-decode]
#              [--dry-run] [--port N] [--node NODE_ID] [--force]
#              [--weight-source replicated|library-hot]
#   ./serve.sh --list
#
# <model-name> is a file in models/ without the .conf suffix.
# -d            detach (docker -d)
# --spec-decode / --no-spec-decode override the model profile's default
# --dry-run     print the docker command instead of running it
# --force       allow non-tested STATUS (see scripts/lib.sh status gate)
#
# Multi-node profiles (NODES>1): use cluster/start-cluster.sh.
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

MODEL_NAME="${1:?usage: ./serve.sh <model-name> [-d] [--spec-decode|--no-spec-decode] [--dry-run] [--port N] [--force] (see --list)}"
shift

DETACH="" SPEC_MODE=auto DRY_RUN=0 PORT_OVERRIDE="" FORCE=0 NODE_SELECTOR=""
WEIGHT_SOURCE=replicated
while [ $# -gt 0 ]; do
  case "$1" in
    -d) DETACH="-d" ;;
    --spec-decode) set_spec_decode_mode SPEC_MODE on ;;
    --no-spec-decode) set_spec_decode_mode SPEC_MODE off ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    --weight-source)
      [ "$#" -ge 2 ] || die "--weight-source requires replicated|library-hot" 2
      WEIGHT_SOURCE="$2"
      shift
      ;;
    --weight-mode)
      [ "$#" -ge 2 ] || die "--weight-mode requires library-hot or replicated" 2
      WEIGHT_SOURCE="$2"
      shift
      ;;
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port requires a value (1-65535)" 2
      PORT_OVERRIDE="$2"
      shift
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

acquire_model_library_lifecycle_lock shared
load_conf "$MODEL_NAME"
case "$WEIGHT_SOURCE" in
  replicated|library-hot) ;;
  fabric) die "serve.sh does not support fabric; use multi-node start-cluster" 2 ;;
  *) die "--weight-source must be replicated or library-hot" 2 ;;
esac
if [ "$WEIGHT_SOURCE" = library-hot ]; then
  acquire_model_library_hot_lock shared
fi
if [ -n "$PORT_OVERRIDE" ]; then
  case "$PORT_OVERRIDE" in
    *[!0-9]*|"") die "invalid --port '$PORT_OVERRIDE' (expected 1-65535)" 2 ;;
  esac
  [ "$PORT_OVERRIDE" -ge 1 ] && [ "$PORT_OVERRIDE" -le 65535 ] \
    || die "invalid --port '$PORT_OVERRIDE' (expected 1-65535)" 2
  PORT="$PORT_OVERRIDE"
fi
resolve_spec_decode "$SPEC_MODE"
LAUNCH_CONTRACT_ID=$(loaded_launch_contract_id)
SPEC_DECODE_STATE=$([ "$SPEC_DECODE_ENABLED" = 1 ] && printf on || printf off)

if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "$MODEL_NAME status=$STATUS — refuse serve without --force (allowlist: tested*)" >&2
  exit 1
fi

if [ "$NODES" != "1" ]; then
  echo "$MODEL_NAME is a ${NODES}-node config. Use: cluster/start-cluster.sh $MODEL_NAME" >&2
  exit 1
fi
resolve_single_node_placement "$NODE_SELECTOR" \
  || die "cannot resolve physical node placement '$NODE_SELECTOR'"

weight_volume="${HF_CACHE}:/root/.cache/huggingface"
runtime_model="$MODEL"
SEALED_REPLICATED=0
LIBRARY_HOT_HOME_NODE_ID=""
LIBRARY_HOT_CONTENT_ID=""
if [ "$WEIGHT_SOURCE" = replicated ] && [ -n "${EXPECTED_MODEL_SEAL:-}" ]; then
  load_replicated_identity_plan "$MODEL_NAME"
  verify_replicated_identity_selected_node serve >/dev/null ||
    die "replicated: selected node failed exact identity verification"
  replicated_container_hub="/root/.cache/huggingface/hub/$(hf_hub_dirname "$MODEL")"
  weight_volume="${REPLICATED_HUB_PATH}:${replicated_container_hub}:ro"
  runtime_model="$REPLICATED_CONTAINER_MODEL_PATH"
  SEALED_REPLICATED=1
  echo "replicated identity=match revision=${REPLICATED_REVISION:0:12} model_path=$runtime_model"
elif [ "$WEIGHT_SOURCE" = library-hot ]; then
  # Prefer topology from placement when available
  if [ -z "${CLUSTER_TOPOLOGY_ID:-}" ] && [ -n "${SINGLE_NODE_TOPOLOGY_ID:-}" ]; then
    CLUSTER_TOPOLOGY_ID="$SINGLE_NODE_TOPOLOGY_ID"
  fi
  load_cluster_topology >/dev/null 2>&1 \
    || die "library-hot requires confirmed topology"
  resolve_library_hot_for_profile "$MODEL_NAME"
  model_cache_name=$(hf_hub_dirname "$LIBRARY_HOT_MODEL_ID")
  weight_volume="${LIBRARY_HOT_HUB_PATH}:/root/.cache/huggingface/hub/${model_cache_name}:ro"
  runtime_model="$LIBRARY_HOT_CONTAINER_MODEL_PATH"
  echo "library-hot identity=$LIBRARY_HOT_IDENTITY_STATUS revision=${LIBRARY_HOT_REVISION:0:12} model_path=$runtime_model"
fi

CONTAINER=$(container_name_for "$MODEL_NAME" 1)

CMD=(docker run --name "$CONTAINER" ${DETACH:+$DETACH}
  --label "${PULSAR_MANAGED_LABEL}=true"
  --label "${PULSAR_CONF_LABEL}=${MODEL_NAME}"
  --label "${PULSAR_RANK_LABEL}=single"
  --label "${PULSAR_WEIGHT_SOURCE_LABEL}=${WEIGHT_SOURCE}"
  --label "${PULSAR_LAUNCH_CONTRACT_LABEL}=${LAUNCH_CONTRACT_ID}"
  --label "${PULSAR_SPEC_DECODE_LABEL}=${SPEC_DECODE_STATE}"
  --gpus all
  --ipc=host
  --ulimit memlock=-1 --ulimit stack=67108864
  -p "${PORT}:${PORT}"
  -v "$weight_volume"
  -v "${MODELS_NFS}:/mnt/Models:ro"
  -e "HF_TOKEN=${HF_TOKEN:-}"
  -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}"
  -e "VLLM_LOGGING_LEVEL=${VLLM_LOGGING_LEVEL:-INFO}"
  --health-cmd "curl -fs http://localhost:${PORT}/health || exit 1"
  --health-interval 30s --health-timeout 5s --health-retries 3
  --health-start-period "${HEALTH_START_PERIOD:-900s}"
  --restart "${RESTART_POLICY:-no}"
)
if [ "$WEIGHT_SOURCE" = library-hot ]; then
  CMD+=(
    --label "${PULSAR_WEIGHT_OWNER_LABEL}=${LIBRARY_HOT_HOME_NODE_ID}"
    --label "${PULSAR_WEIGHT_CONFIG_LABEL}=${LIBRARY_HOT_CONTENT_ID}"
    --label "${PULSAR_MODEL_REVISION_LABEL}=${LIBRARY_HOT_REVISION}"
    --label "${PULSAR_MODEL_IDENTITY_STATUS_LABEL}=${LIBRARY_HOT_IDENTITY_STATUS}"
  )
  if [ "$LIBRARY_HOT_IDENTITY_STATUS" = match ]; then
    CMD+=(
      --label "${PULSAR_MODEL_SEAL_LABEL}=${LIBRARY_HOT_MODEL_SEAL_ID}"
      --label "${PULSAR_VALIDATION_BUNDLE_LABEL}=${LIBRARY_HOT_VALIDATION_BUNDLE_ID}"
    )
  fi
fi
if [ "$SEALED_REPLICATED" = 1 ]; then
  CMD+=(
    --label "${PULSAR_MODEL_REVISION_LABEL}=${REPLICATED_REVISION}"
    --label "${PULSAR_MODEL_IDENTITY_STATUS_LABEL}=match"
    --label "${PULSAR_MODEL_SEAL_LABEL}=${REPLICATED_MODEL_SEAL_ID}"
    --label "${PULSAR_VALIDATION_BUNDLE_LABEL}=${REPLICATED_VALIDATION_BUNDLE_ID}"
  )
fi
if [ -n "$SINGLE_NODE_TOPOLOGY_ID" ]; then
  CMD+=(--label "${PULSAR_TOPOLOGY_LABEL}=${SINGLE_NODE_TOPOLOGY_ID}")
fi
if [ -n "$SINGLE_NODE_ID" ]; then
  CMD+=(--label "${PULSAR_NODE_ID_LABEL}=${SINGLE_NODE_ID}")
fi
for e in ${CONTAINER_ENV[@]+"${CONTAINER_ENV[@]}"}; do CMD+=(-e "$e"); done
for e in ${EXTRA_ENV:-}; do CMD+=(-e "$e"); done

CMD+=(
  "$IMAGE"
  --model "$runtime_model"
  --served-model-name "$SERVED_NAME"
  --host 0.0.0.0 --port "$PORT"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
)
CMD+=(${ENGINE_ARGS[@]+"${ENGINE_ARGS[@]}"})
[ "$SPEC_DECODE_ENABLED" = "1" ] && CMD+=("${SPEC_DECODE_ARGS[@]}")
append_vllm_extra_args CMD
_api_key="${VLLM_API_KEY:-${API_KEY:-}}"
if [ -n "$_api_key" ]; then
  CMD+=(--api-key "$_api_key")
fi

if [ "$DRY_RUN" = "1" ]; then
  if [ "$SINGLE_NODE_REMOTE" = 1 ]; then
    remote_redacted=$(shell_join_q_redacted "${CMD[@]}")
    print_shell_command_redacted "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
      "$SINGLE_NODE_SSH_HOST" "$remote_redacted"
  else
    CMD[0]="$PULSAR_DOCKER"
    print_shell_command_redacted "${CMD[@]}"
  fi
  exit 0
fi

# Replace only when the exact name is provably stack-managed for this conf.
# Capture ID, revalidate labels, remove by ID — never blind docker rm -f by name.
stale_rc=0
remove_stack_owned_single_at_resolved_node "$MODEL_NAME" || stale_rc=$?
if [ "$stale_rc" -eq 2 ]; then
  echo "[serve] ERROR: refusing to replace $CONTAINER on $(single_node_display) — ownership or physical node identity is not proven" >&2
  echo "[serve] Inspect labels (${PULSAR_MANAGED_LABEL}/${PULSAR_CONF_LABEL}/${PULSAR_RANK_LABEL}/${PULSAR_NODE_ID_LABEL}) or remove manually if you intend to clobber it." >&2
  exit 1
fi
if [ "$stale_rc" -ne 0 ]; then
  echo "[serve] ERROR: failed while removing prior container $CONTAINER on $(single_node_display) (rc=$stale_rc)" >&2
  exit 1
fi

port_probe='import socket,sys; s=socket.socket(); s.bind(("0.0.0.0",int(sys.argv[1]))); s.close()'
if [ "$SINGLE_NODE_REMOTE" = 1 ]; then
  port_cmd="python3 -c $(printf '%q' "$port_probe") $(printf '%q' "$PORT")"
  if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$SINGLE_NODE_SSH_HOST" \
      "$port_cmd" >/dev/null 2>&1; then
    die "port $PORT is unavailable on $(single_node_display); refusing launch"
  fi
elif ! python3 -c "$port_probe" "$PORT" >/dev/null 2>&1; then
  die "port $PORT is unavailable on $(single_node_display); refusing launch"
fi

echo "[serve] $MODEL_NAME ($MODEL) on $(single_node_display), port $PORT, image $IMAGE container=$CONTAINER"
echo "[serve] spec-decode=$([ "$SPEC_DECODE_ENABLED" = 1 ] && echo ON || echo off) ($SPEC_DECODE_SOURCE)"
[ -n "${NOTES:-}" ] && echo "[serve] notes: $NOTES"
if [ -n "$_api_key" ]; then
  echo "[serve] API key auth enabled (VLLM_API_KEY/API_KEY)"
else
  echo "[serve] API open (no VLLM_API_KEY) — lab network only"
fi
if [ "$SINGLE_NODE_REMOTE" = 1 ]; then
  remote_cmd=$(shell_join_q "${CMD[@]}")
  exec "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
    "$SINGLE_NODE_SSH_HOST" "$remote_cmd"
fi
CMD[0]="$PULSAR_DOCKER"
exec "${CMD[@]}"
