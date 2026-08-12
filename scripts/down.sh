#!/usr/bin/env bash
# Stop serving containers for a conf, or all stack-managed containers.
#   scripts/down.sh <model-name|--all> [--node NODE_ID]
#                   [--pin-weights|--purge-hot]
#
# Only removes containers that prove stack ownership via
# io.pulsar.gb10.managed=true and consistent conf/rank labels. Unlabeled
# legacy or unknown containers are reported and refused, never removed.
# --all means all label-managed Pulsar containers with a known conf and
# placement-valid rank, not every vllm-* name.
#
# After a successful profile stop, optional library-hot hooks:
#   --pin-weights   protect retained hot staging from purge; warm-home
#                   preparation may still depend on its durable home symlink
#   --purge-hot     delete hot staging (default is leave hot untouched)
set -euo pipefail
SCRIPT_NAME=down
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: $0 <model-name|--all> [--node NODE_ID] [--pin-weights|--purge-hot]"
shift || true
NODE_SELECTOR=""
HOT_AFTER=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    --pin-weights)
      [ -z "$HOT_AFTER" ] || die "use only one of --pin-weights or --purge-hot" 2
      HOT_AFTER=pin
      ;;
    --purge-hot)
      [ -z "$HOT_AFTER" ] || die "use only one of --pin-weights or --purge-hot" 2
      HOT_AFTER=purge
      ;;
    *) die "unknown arg: $1" 2 ;;
  esac
  shift
done

library_hot_after_stop() {
  local profile="${1:?}"
  [ -n "$HOT_AFTER" ] || return 0
  case "$HOT_AFTER" in
    pin)
      log "pinning library-hot staging for $profile"
      "$REPO_DIR/scripts/model-library.sh" pin "$profile" || \
        warn "pin failed (no hot instance?)"
      ;;
    purge)
      log "purging library-hot staging for $profile"
      "$REPO_DIR/scripts/model-library.sh" purge-hot "$profile" --yes --force-unpin || \
        warn "purge-hot failed (no hot instance?)"
      ;;
  esac
}

if [ "$TARGET" = "--all" ]; then
  [ -z "$NODE_SELECTOR" ] || die "--node cannot be combined with --all" 2
  [ -z "$HOT_AFTER" ] || die "--pin-weights/--purge-hot cannot be combined with --all" 2
  load_cluster_topology || die "confirmed topology is invalid"
  if [ "$CLUSTER_TOPOLOGY_COUNT" -gt 1 ]; then
    exec "$REPO_DIR/cluster/stop-cluster.sh" --all
  fi
  log "stopping all local stack-managed Pulsar containers"
  rc=0
  remove_all_stack_managed_local || rc=$?
  case "$rc" in
    0) log "done"; exit 0 ;;
    2) die "one or more managed candidates were refused; left intact" ;;
    *) die "managed container cleanup reported errors" ;;
  esac
fi

load_conf "$TARGET"
if [ "$NODES" -gt 1 ]; then
  # Preserve optional hot hooks after multi-node stop returns.
  stop_rc=0
  "$REPO_DIR/cluster/stop-cluster.sh" "$TARGET" || stop_rc=$?
  if [ "$stop_rc" -eq 0 ]; then
    library_hot_after_stop "$TARGET"
  fi
  exit "$stop_rc"
fi

if [ -z "$NODE_SELECTOR" ]; then
  placement_index=""
  rc=0
  placement_index=$(discover_single_node_index_for_conf "$TARGET") || rc=$?
  case "$rc" in
    0)
      NODE_SELECTOR="${CLUSTER_NODE_IDS[$placement_index]:-}"
      [ -n "$NODE_SELECTOR" ] \
        || NODE_SELECTOR=$(single_node_key_for_index "$placement_index")
      ;;
    3)
      log "no stack-managed single-node service found for conf=$TARGET"
      exit 0
      ;;
    2)
      die "refused to stop $TARGET: placement or ownership is ambiguous"
      ;;
    *)
      die "cannot safely discover $TARGET placement across confirmed nodes"
      ;;
  esac
fi

resolve_single_node_placement "$NODE_SELECTOR" \
  || die "cannot resolve physical node placement '$NODE_SELECTOR'"
cname=$(container_name_for "$TARGET" 1)
rc=0
remove_stack_owned_single_at_resolved_node "$TARGET" || rc=$?
if [ "$rc" -eq 0 ]; then
  library_hot_after_stop "$TARGET"
  exit 0
fi
if [ "$rc" -eq 2 ]; then
  die "refused to stop $cname on $(single_node_display): ownership or node identity is not proven"
fi
die "failed to stop $cname on $(single_node_display)"
