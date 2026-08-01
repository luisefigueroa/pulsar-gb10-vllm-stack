#!/usr/bin/env bash
# Stop serving containers for a conf, or all vllm containers.
#   scripts/down.sh <model-name|--all>
set -euo pipefail
SCRIPT_NAME=down
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: $0 <model-name|--all>"

if [ "$TARGET" = "--all" ]; then
  log "stopping all vllm-cluster-* (if .env multi-node configured)"
  if [ -n "${HEAD_IP:-}" ] && [ -n "${WORKER_IP:-}" ]; then
    "$REPO_DIR/cluster/stop-cluster.sh" --all || true
  fi
  log "stopping local vllm-* single-node containers"
  names=$(docker ps -a --format '{{.Names}}' | grep -E '^vllm-' || true)
  for n in $names; do
    log "removing $n"
    docker rm -f "$n" >/dev/null 2>&1 || true
  done
  if [ -n "${WORKER_IP:-}" ]; then
    ssh -o BatchMode=yes "$WORKER_IP" \
      'docker ps -a --format "{{.Names}}" | grep -E "^vllm-" | xargs -r docker rm -f' 2>/dev/null || true
  fi
  log "done"
  exit 0
fi

load_conf "$TARGET"
if [ "$NODES" = "2" ]; then
  require_cluster_ips || exit 1
  exec "$REPO_DIR/cluster/stop-cluster.sh" "$TARGET"
fi

cname=$(container_name_for "$TARGET" 1)
if docker ps -a --format '{{.Names}}' | grep -qx "$cname"; then
  log "removing $cname"
  docker rm -f "$cname"
else
  log "no container named $cname"
fi
