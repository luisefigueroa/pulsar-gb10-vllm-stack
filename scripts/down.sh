#!/usr/bin/env bash
# Stop serving containers for a conf, or all stack-managed containers.
#   scripts/down.sh <model-name|--all>
#
# Only removes containers that prove stack ownership via
# io.pulsar.gb10.managed=true and consistent conf/rank labels. Unlabeled
# legacy or unknown containers are reported and refused, never removed.
# --all means all label-managed Pulsar containers with a known conf and
# placement-valid rank, not every vllm-* name.
set -euo pipefail
SCRIPT_NAME=down
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: $0 <model-name|--all>"

if [ "$TARGET" = "--all" ]; then
  log "stopping all stack-managed Pulsar containers (label ${PULSAR_MANAGED_LABEL}=true)"
  # If a worker is configured, prove it is reachable BEFORE any head mutation.
  # Unreachable worker must not report success or remove the head rank alone.
  if [ -n "${WORKER_IP:-}" ]; then
    if ! list_managed_container_ids_remote "$WORKER_IP" >/dev/null; then
      die "worker unreachable or docker error on $WORKER_IP — refusing --all (no head removals)"
    fi
  fi

  rc=0
  step=0
  remove_all_stack_managed_local || step=$?
  rc=$(lifecycle_merge_rc "$rc" "$step")

  if [ -n "${WORKER_IP:-}" ]; then
    log "stopping stack-managed containers on worker $WORKER_IP"
    step=0
    remove_all_stack_managed_remote "$WORKER_IP" || step=$?
    rc=$(lifecycle_merge_rc "$rc" "$step")
  else
    log "WORKER_IP unset — skip remote managed cleanup"
  fi

  if [ "$rc" -eq 0 ]; then
    log "done"
    exit 0
  fi
  if [ "$rc" -eq 2 ]; then
    die "one or more managed candidates were refused (unknown conf / bad placement / incomplete labels); left intact"
  fi
  die "managed container cleanup reported errors"
fi

load_conf "$TARGET"
if [ "$NODES" = "2" ]; then
  require_cluster_ips || exit 1
  exec "$REPO_DIR/cluster/stop-cluster.sh" "$TARGET"
fi

cname=$(container_name_for "$TARGET" 1)
rc=0
remove_stack_owned_container_local "$cname" "$TARGET" "single" || rc=$?
if [ "$rc" -eq 0 ]; then
  exit 0
fi
if [ "$rc" -eq 2 ]; then
  die "refused to stop $cname: not provably stack-managed for conf=$TARGET rank=single"
fi
die "failed to stop $cname"
