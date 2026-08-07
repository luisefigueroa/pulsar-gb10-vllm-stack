#!/usr/bin/env bash
# Ensure an exact profile image exists on every physical node used by it.
#   scripts/sync-image.sh <model-name> [--node NODE_ID] [--pull] [--yes]
set -euo pipefail
SCRIPT_NAME=sync-image
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PULL=0
YES=0
NODE_SELECTOR=""
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--node NODE_ID] [--pull] [--yes]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL=1 ;;
    --yes|-y) YES=1 ;;
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement $NODE_SELECTOR"
  if [ "$SINGLE_NODE_REMOTE" = 1 ]; then
    if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
        "$SINGLE_NODE_SSH_HOST" true >/dev/null 2>&1; then
      die "$(single_node_display) is unreachable over BatchMode SSH"
    fi
    if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
        "$SINGLE_NODE_SSH_HOST" "docker info >/dev/null 2>&1"; then
      die "Docker is unavailable on $(single_node_display)"
    fi
    remote_inspect="$(shell_join_q docker image inspect "$IMAGE") >/dev/null 2>&1"
    if "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
        "$SINGLE_NODE_SSH_HOST" "$remote_inspect"; then
      log "$(single_node_display) image OK: $IMAGE"
      exit 0
    fi
    case "$IMAGE" in
      vllm/vllm-openai:*|vllm/*|ghcr.io/*)
        if [ "$PULL" != 1 ] && [ "$YES" != 1 ]; then
          die "image missing on $(single_node_display): $IMAGE — rerun with --pull"
        fi
        log "pulling $IMAGE on $(single_node_display)"
        remote_pull=$(shell_join_q docker pull "$IMAGE")
        "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
          "$SINGLE_NODE_SSH_HOST" "$remote_pull"
        ;;
      *)
        require_cmd docker
        "$PULSAR_DOCKER" image inspect "$IMAGE" >/dev/null 2>&1 \
          || die "image missing locally: $IMAGE — build it first (docs/BUILD.md)"
        if [ "$YES" != 1 ]; then
          read -r -p "Load $IMAGE onto $(single_node_display)? [y/N] " answer
          case "$answer" in
            y|Y|yes|YES) ;;
            *) die "aborted" 3 ;;
          esac
        fi
        log "streaming $IMAGE to $(single_node_display)…"
        "$PULSAR_DOCKER" save "$IMAGE" \
          | "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
              "$SINGLE_NODE_SSH_HOST" "docker load"
        ;;
    esac
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
      "$SINGLE_NODE_SSH_HOST" "$remote_inspect" \
      || die "$(single_node_display) still missing $IMAGE after staging"
    log "$(single_node_display) image OK: $IMAGE"
    exit 0
  fi
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node is only valid for one-node profiles" 2
fi

require_cmd docker
if [ "$NODES" -gt 1 ]; then
  require_cluster_nodes "$NODES" \
    || die "profile requires $NODES confirmed ranks"
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  case "$IMAGE" in
    vllm/vllm-openai:*|vllm/*|ghcr.io/*)
      if [ "$PULL" = 1 ] || [ "$YES" = 1 ]; then
        log "docker pull $IMAGE"
        docker pull "$IMAGE"
      else
        die "image missing on rank 0: $IMAGE — rerun with --pull"
      fi
      ;;
    *)
      die "image missing on rank 0: $IMAGE — build it first (docs/BUILD.md)"
      ;;
  esac
fi

if [ "$NODES" = 1 ]; then
  log "rank 0 image OK: $IMAGE"
  exit 0
fi

declare -a missing_ranks=()
for ((rank = 1; rank < NODES; rank++)); do
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  if "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
      "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
    log "rank $rank already has $IMAGE"
  else
    missing_ranks+=("$rank")
  fi
done
if [ "${#missing_ranks[@]}" -eq 0 ]; then
  log "image present on all $NODES ranks"
  exit 0
fi

if [ "$YES" != 1 ]; then
  echo "Load $IMAGE onto these ranks using docker save | SSH | docker load?"
  for rank in "${missing_ranks[@]}"; do
    echo "  rank $rank  ${CLUSTER_NODE_HOSTNAMES[$rank]}  ${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  done
  read -r -p "Continue? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "aborted" 3 ;;
  esac
fi

for rank in "${missing_ranks[@]}"; do
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  log "streaming $IMAGE to rank $rank ($host)…"
  docker save "$IMAGE" \
    | "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "docker load"

  # A digest-pinned save/load can omit the registry reference even when every
  # layer arrived. Pull then normally resolves only manifest metadata.
  if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
      "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
    case "$IMAGE" in
      *@sha256:*)
        log "rank $rank: docker load omitted the digest reference; resolving…"
        "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
          "docker pull $(printf '%q' "$IMAGE")"
        ;;
    esac
  fi
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
    "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1" \
    || die "rank $rank still missing $IMAGE after load"
done
log "image present on all $NODES ranks"
