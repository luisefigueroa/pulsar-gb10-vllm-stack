#!/usr/bin/env bash
# Tear down 2-node serving on BOTH nodes. Always safe to run; run it before
# every start if in doubt. A half-torn-down cluster (worker still holding the
# RDMA QPs / master port) is the classic way to lose an afternoon.
#   cluster/stop-cluster.sh [model-name|--all]
#
# Exact name matching only for a named conf (never prefix-match
# deepseek-v4-flash onto deepseek-v4-flash-0422). Only removes containers that
# prove stack ownership (managed + conf/rank). Unlabeled/legacy are refused.
# Worker SSH/Docker errors are operational failures — never treated as absence.
#
# Named stops load models/<name>.conf and require NODES=2 before any
# inspect/remove. Unknown confs and single-node confs fail closed.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

ARG="${1:-}"
[ -n "$ARG" ] || { echo "usage: $0 <model-name|--all>" >&2; exit 2; }

if [ "$ARG" = "--all" ]; then
  require_cluster_ips || exit 1
  echo "[stop] removing all stack-managed containers on head and worker"
  # Probe worker before any head mutation.
  if ! list_managed_container_ids_remote "$WORKER_IP" >/dev/null; then
    echo "[stop] ERROR: worker unreachable or docker error on $WORKER_IP — refusing --all (no head removals)" >&2
    exit 1
  fi
  rc=0
  step=0
  remove_all_stack_managed_local || step=$?
  rc=$(lifecycle_merge_rc "$rc" "$step")
  step=0
  remove_all_stack_managed_remote "$WORKER_IP" || step=$?
  rc=$(lifecycle_merge_rc "$rc" "$step")
  if [ "$rc" -eq 0 ]; then
    echo "[stop] clean — no stack-managed containers remain (or none were present)"
    exit 0
  fi
  if [ "$rc" -eq 2 ]; then
    echo "[stop] WARNING: some managed candidates refused (unknown conf / bad placement)" >&2
    exit 1
  fi
  echo "[stop] WARNING: managed cleanup reported errors" >&2
  exit 1
fi

# Named conf: validate profile before any inspect/remove.
load_conf "$ARG"
if [ "$NODES" != "2" ]; then
  echo "[stop] ERROR: $ARG is a single-node conf (NODES=$NODES); use scripts/down.sh $ARG" >&2
  exit 1
fi
require_cluster_ips || exit 1

# Both ranks must be proven or absent; refuse partial/ambiguous.
# Worker unreachable ⇒ operational error (do not remove head alone).
EXACT=$(container_name_for "$ARG" 2)
echo "[stop] stopping stack-managed cluster conf=$ARG container=$EXACT"
rc=0
remove_stack_owned_cluster_pair "$ARG" "$EXACT" "$WORKER_IP" || rc=$?
if [ "$rc" -eq 0 ]; then
  left_head=0
  left_worker=0
  probe=0
  container_ownership_inspect_local "$EXACT" >/dev/null 2>&1 && left_head=1 || {
    probe=$?
    if [ "$probe" -eq 1 ]; then
      echo "[stop] WARNING: local docker error verifying $EXACT gone" >&2
      exit 1
    fi
  }
  probe=0
  container_ownership_inspect_remote "$WORKER_IP" "$EXACT" >/dev/null 2>&1 && left_worker=1 || {
    probe=$?
    if [ "$probe" -eq 1 ]; then
      echo "[stop] WARNING: worker error verifying $EXACT gone" >&2
      exit 1
    fi
  }
  LEFT=$((left_head + left_worker))
  if [ "$LEFT" -eq 0 ]; then
    echo "[stop] clean — no container named $EXACT on either node"
    exit 0
  fi
  echo "[stop] WARNING: $EXACT still present on $LEFT node(s)" >&2
  exit 1
fi
if [ "$rc" -eq 2 ]; then
  echo "[stop] refused: ownership not proven for every existing rank of $EXACT" >&2
  exit 1
fi
echo "[stop] failed to stop $EXACT (operational error; no partial remove)" >&2
exit 1
