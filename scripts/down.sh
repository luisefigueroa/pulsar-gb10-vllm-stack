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
# After a successful profile stop, library-hot retention hooks:
#   --pin-weights   protect retained hot staging from purge; warm-home
#                   preparation may still depend on its durable home symlink
#   --purge-hot     delete hot staging, including an existing pin
# A stopped library-hot service purges unpinned views by default. Legacy
# containers without a library-hot label (pre-ADR 0006 launches) stop
# cleanly and never invoke model-library cleanup.
set -euo pipefail
SCRIPT_NAME=down
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: $0 <model-name|--all> [--node NODE_ID] [--pin-weights|--purge-hot]"
shift || true
NODE_SELECTOR=""
HOT_AFTER=""
MODEL_LIBRARY_CMD="${PULSAR_MODEL_LIBRARY_CMD:-$REPO_DIR/scripts/model-library.sh}"
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
      HOT_AFTER=purge-explicit
      ;;
    *) die "unknown arg: $1" 2 ;;
  esac
  shift
done

library_hot_after_stop() {
  local profile="${1:?}"
  [ -n "$HOT_AFTER" ] || return 0
  local -a node_args=()
  if [ "${NODES:-1}" -eq 1 ] && [ -n "$NODE_SELECTOR" ]; then
    node_args=(--node "$NODE_SELECTOR")
  fi
  case "$HOT_AFTER" in
    pin)
      log "pinning library-hot staging for $profile"
      "$MODEL_LIBRARY_CMD" pin "$profile" "${node_args[@]}" || \
        warn "pin failed (no hot instance?)"
      ;;
    purge-explicit)
      log "purging library-hot staging for $profile"
      "$MODEL_LIBRARY_CMD" purge-hot "$profile" "${node_args[@]}" \
        --yes --force-unpin || \
        warn "purge-hot failed (no hot instance?)"
      ;;
    purge-default)
      log "purging unpinned library-hot staging for $profile (stop default)"
      "$MODEL_LIBRARY_CMD" purge-hot "$profile" "${node_args[@]}" --yes || \
        warn "hot staging was retained (it may be pinned); inspect with ./pulsar models"
      ;;
  esac
}

observe_profile_weight_source() {
  local profile="${1:?}" cname="${2:?}" rank metadata rc source observed=""
  local count="${NODES:-1}" expected_rank found=0
  local first_rank=0 last_rank=$((count - 1))
  if [ "$count" -eq 1 ] && [ -n "${SINGLE_NODE_INDEX:-}" ]; then
    first_rank="$SINGLE_NODE_INDEX"
    last_rank="$SINGLE_NODE_INDEX"
  fi
  for ((rank = first_rank; rank <= last_rank; rank++)); do
    metadata=""
    rc=0
    metadata=$(container_ownership_inspect_on_node "$rank" "$cname") || rc=$?
    if [ "$rc" -eq 3 ]; then
      continue
    fi
    [ "$rc" -eq 0 ] || return 1
    if [ "$count" -eq 1 ]; then
      expected_rank=single
    else
      expected_rank="$rank"
    fi
    container_ownership_is_proven "$metadata" "$profile" "$expected_rank" \
      || return 1
    source=$(container_weight_source_field "$metadata") || return 1
    if [ -n "$observed" ] && [ "$source" != "$observed" ]; then
      return 1
    fi
    observed="$source"
    found=$((found + 1))
  done
  [ "$found" -gt 0 ] || return 1
  printf '%s\n' "$observed"
}

set_default_hot_policy() {
  local profile="${1:?}" cname="${2:?}" source
  [ -z "$HOT_AFTER" ] || return 0
  if ! source=$(observe_profile_weight_source "$profile" "$cname"); then
    warn "could not prove the service weight policy; hot storage will be left unchanged"
    return 0
  fi
  if [ "$source" = library-hot ]; then
    HOT_AFTER=purge-default
    log "library-hot service detected; unpinned prepared views will be purged after stop"
  fi
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
  load_cluster_topology >/dev/null || die "confirmed topology required"
  set_default_hot_policy "$TARGET" "$(container_name_for "$TARGET" "$NODES")"
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
set_default_hot_policy "$TARGET" "$cname"
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
