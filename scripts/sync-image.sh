#!/usr/bin/env bash
# Ensure conf image on head (pull published images when requested) and worker (docker save|load).
#   scripts/sync-image.sh <model-name> [--pull] [--yes]
set -euo pipefail
SCRIPT_NAME=sync-image
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PULL=0 YES=0
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--pull] [--yes]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --pull) PULL=1 ;;
    --yes|-y) YES=1 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
require_cmd docker

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  case "$IMAGE" in
    vllm/vllm-openai:*|vllm/*|ghcr.io/*)
      if [ "$PULL" = 1 ] || [ "$YES" = 1 ]; then
        log "docker pull $IMAGE"
        docker pull "$IMAGE"
      else
        die "image missing on head: $IMAGE — re-run with --pull for published images"
      fi
      ;;
    *)
      die "image missing on head: $IMAGE — build the local image (docs/BUILD.md) then re-run"
      ;;
  esac
fi

if [ "$NODES" != "2" ]; then
  log "NODES=1 — head image OK: $IMAGE"
  exit 0
fi

[ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP"
if ssh_worker "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
  log "worker already has $IMAGE"
  exit 0
fi

if [ "$YES" != 1 ]; then
  read -r -p "Load $IMAGE onto worker $WORKER_IP via docker save|ssh|load? [y/N] " ans
  case "$ans" in y|Y|yes|YES) ;; *) die "aborted" 3 ;; esac
fi

log "streaming $IMAGE to $WORKER_IP (this can take several minutes)…"
docker save "$IMAGE" | ssh_worker "docker load"

# Docker save/load can transfer all layers for a digest-pinned image yet load
# it as an untagged image ID (RepoTags=[], RepoDigests=[]). Resolve the registry
# reference on the worker after the LAN transfer; Docker reuses the local layers
# and normally fetches only manifest metadata.
if ! ssh_worker "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
  case "$IMAGE" in
    *@sha256:*)
      log "docker load omitted the digest reference; resolving it on the worker…"
      ssh_worker "docker pull $(printf '%q' "$IMAGE")"
      ;;
  esac
fi

ssh_worker "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1" \
  || die "worker still missing $IMAGE after load"
log "worker image OK"
