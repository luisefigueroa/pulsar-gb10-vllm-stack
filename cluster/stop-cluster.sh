#!/usr/bin/env bash
# Tear down 2-node serving on BOTH nodes. Always safe to run; run it before
# every start if in doubt. A half-torn-down cluster (worker still holding the
# RDMA QPs / master port) is the classic way to lose an afternoon.
#   cluster/stop-cluster.sh [model-name|--all]
#
# Exact name matching only for a named conf (never prefix-match
# deepseek-v4-flash onto deepseek-v4-flash-0422).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
. cluster/cluster-env.sh
require_cluster_ips || exit 1
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

ARG="${1:-}"
[ -n "$ARG" ] || { echo "usage: $0 <model-name|--all>" >&2; exit 2; }

ALL=0
EXACT=""
if [ "$ARG" = "--all" ]; then
  ALL=1
else
  EXACT="vllm-cluster-${ARG}"
fi

stop_on() {
  local label="$1" run="$2"
  local names n
  names=$($run "docker ps -a --format '{{.Names}}'" 2>/dev/null || true)
  if [ "$ALL" = 1 ]; then
    names=$(printf '%s\n' "$names" | grep -E '^vllm-cluster-' || true)
  else
    names=$(printf '%s\n' "$names" | filter_exact_container_name "$EXACT")
  fi
  if [ -z "${names//[$'\t\r\n ']/}" ]; then
    if [ "$ALL" = 1 ]; then
      echo "[stop] $label: nothing matching vllm-cluster-*"
    else
      echo "[stop] $label: nothing named exactly $EXACT"
    fi
    return
  fi
  while IFS= read -r n; do
    [ -z "$n" ] && continue
    echo "[stop] $label: removing $n"
    $run "docker rm -f $(printf '%q' "$n")" >/dev/null 2>&1 \
      || echo "[stop] $label: failed to remove $n" >&2
  done <<< "$names"
}

stop_on "head  " "bash -c"
stop_on "worker" "ssh $WORKER_IP"

# verify — exact for named model; prefix only for --all
left_head=$(docker ps -a --format '{{.Names}}' 2>/dev/null || true)
left_worker=$(ssh "$WORKER_IP" "docker ps -a --format '{{.Names}}'" 2>/dev/null || true)
if [ "$ALL" = 1 ]; then
  LEFT=$(printf '%s\n%s\n' "$left_head" "$left_worker" | grep -E '^vllm-cluster-' | wc -l | tr -d ' ')
  if [ "$LEFT" -eq 0 ]; then
    echo "[stop] clean — no vllm-cluster-* containers remain on either node"
  else
    echo "[stop] WARNING: $LEFT vllm-cluster-* containers remain" >&2
    exit 1
  fi
else
  LEFT=$(printf '%s\n%s\n' "$left_head" "$left_worker" | filter_exact_container_name "$EXACT" | wc -l | tr -d ' ')
  if [ "$LEFT" -eq 0 ]; then
    echo "[stop] clean — no container named $EXACT on either node"
  else
    echo "[stop] WARNING: $EXACT still present ($LEFT)" >&2
    exit 1
  fi
fi
