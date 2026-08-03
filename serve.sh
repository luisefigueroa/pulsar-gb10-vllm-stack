#!/usr/bin/env bash
# Serve a model from models/<name>.conf on this node.
#
#   ./serve.sh <model-name> [-d] [--spec-decode|--no-spec-decode]
#              [--dry-run] [--port N] [--force]
#   ./serve.sh --list
#
# <model-name> is a file in models/ without the .conf suffix.
# -d            detach (docker -d)
# --spec-decode / --no-spec-decode override the model profile's default
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

MODEL_NAME="${1:?usage: ./serve.sh <model-name> [-d] [--spec-decode|--no-spec-decode] [--dry-run] [--port N] [--force] (see --list)}"
shift

DETACH="" SPEC_MODE=auto DRY_RUN=0 PORT_OVERRIDE="" FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -d) DETACH="-d" ;;
    --spec-decode) set_spec_decode_mode SPEC_MODE on ;;
    --no-spec-decode) set_spec_decode_mode SPEC_MODE off ;;
    --dry-run) DRY_RUN=1 ;;
    --force) FORCE=1 ;;
    --port) PORT_OVERRIDE="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

load_conf "$MODEL_NAME"
[ -n "$PORT_OVERRIDE" ] && PORT="$PORT_OVERRIDE"
resolve_spec_decode "$SPEC_MODE"

if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "$MODEL_NAME status=$STATUS — refuse serve without --force (allowlist: tested*)" >&2
  exit 1
fi

if [ "$NODES" != "1" ]; then
  echo "$MODEL_NAME is a ${NODES}-node config. Use: cluster/start-cluster.sh $MODEL_NAME" >&2
  exit 1
fi

CONTAINER=$(container_name_for "$MODEL_NAME" 1)

CMD=(docker run --name "$CONTAINER" ${DETACH:+$DETACH}
  --label "${PULSAR_MANAGED_LABEL}=true"
  --label "${PULSAR_CONF_LABEL}=${MODEL_NAME}"
  --label "${PULSAR_RANK_LABEL}=single"
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
[ "$SPEC_DECODE_ENABLED" = "1" ] && CMD+=("${SPEC_DECODE_ARGS[@]}")
append_vllm_extra_args CMD
_api_key="${VLLM_API_KEY:-${API_KEY:-}}"
if [ -n "$_api_key" ]; then
  CMD+=(--api-key "$_api_key")
fi

if [ "$DRY_RUN" = "1" ]; then printf '%q ' "${CMD[@]}"; echo; exit 0; fi

# Replace only when the exact name is provably stack-managed for this conf.
# Capture ID, revalidate labels, remove by ID — never blind docker rm -f by name.
stale_rc=0
remove_stack_owned_container_local "$CONTAINER" "$MODEL_NAME" "single" || stale_rc=$?
if [ "$stale_rc" -eq 2 ]; then
  echo "[serve] ERROR: refusing to replace $CONTAINER — not provably stack-managed for conf=$MODEL_NAME rank=single" >&2
  echo "[serve] Inspect labels (${PULSAR_MANAGED_LABEL}/${PULSAR_CONF_LABEL}/${PULSAR_RANK_LABEL}) or remove manually if you intend to clobber it." >&2
  exit 1
fi
if [ "$stale_rc" -ne 0 ]; then
  echo "[serve] ERROR: failed while removing prior container $CONTAINER (rc=$stale_rc)" >&2
  exit 1
fi

echo "[serve] $MODEL_NAME ($MODEL) on port $PORT, image $IMAGE container=$CONTAINER"
echo "[serve] spec-decode=$([ "$SPEC_DECODE_ENABLED" = 1 ] && echo ON || echo off) ($SPEC_DECODE_SOURCE)"
[ -n "${NOTES:-}" ] && echo "[serve] notes: $NOTES"
if [ -n "$_api_key" ]; then
  echo "[serve] API key auth enabled (VLLM_API_KEY/API_KEY)"
else
  echo "[serve] API open (no VLLM_API_KEY) — lab network only"
fi
exec "${CMD[@]}"
