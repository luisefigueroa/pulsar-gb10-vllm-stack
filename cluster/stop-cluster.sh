#!/usr/bin/env bash
# Tear down 2-node serving on BOTH nodes. Always safe to run; run it before
# every start if in doubt. A half-torn-down cluster (worker still holding the
# RDMA QPs / master port) is the classic way to lose an afternoon.
#   cluster/stop-cluster.sh [model-name|--all]
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
. cluster/cluster-env.sh

PATTERN="vllm-cluster-${1:-}"
[ "${1:-—all}" = "--all" ] && PATTERN="vllm-cluster-"

stop_on() {
  local label="$1" run="$2"
  local names
  names=$($run "docker ps -a --format '{{.Names}}' | grep '^${PATTERN}' || true" 2>/dev/null)
  if [ -z "$names" ]; then echo "[stop] $label: nothing matching ${PATTERN}*"; return; fi
  for n in $names; do
    echo "[stop] $label: removing $n"
    $run "docker rm -f $n" >/dev/null 2>&1 || echo "[stop] $label: failed to remove $n" >&2
  done
}

stop_on "head  " "bash -c"
stop_on "worker" "ssh $WORKER_IP"

# verify
LEFT=$( (docker ps -a --format '{{.Names}}' | grep "^${PATTERN}"; ssh "$WORKER_IP" "docker ps -a --format '{{.Names}}' | grep '^${PATTERN}'" 2>/dev/null) | wc -l)
if [ "$LEFT" -eq 0 ]; then echo "[stop] clean — no ${PATTERN}* containers remain on either node"; else echo "[stop] WARNING: $LEFT containers remain" >&2; exit 1; fi
