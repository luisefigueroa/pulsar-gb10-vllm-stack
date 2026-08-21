#!/usr/bin/env bash
# Stop serving containers for a conf, or all stack-managed containers.
#   scripts/down.sh <model-name|--all> [--node NODE_ID]
#                   [--retain-weights|--pin-weights|--purge-hot]
#
# Only removes containers that prove stack ownership via
# io.pulsar.gb10.managed=true and consistent conf/rank labels. Unlabeled
# legacy or unknown containers are reported and refused, never removed.
# --all means all label-managed Pulsar containers with a known conf and
# placement-valid rank, not every vllm-* name.
#
# After a successful profile stop, library-hot retention hooks:
#   --retain-weights  leave unpinned prepared views in place (product default)
#   --pin-weights     protect retained hot staging from unforced purge; warm-home
#                     preparation may still depend on its durable home symlink
#   --purge-hot       delete hot staging, including an existing pin
# Ordinary stop of a library-hot service retains unpinned views (ADR 0007).
# PULSAR_HOT_STOP_POLICY=retain|purge may select the named-profile default;
# flags override it. --all never auto-purges. Legacy containers without a
# library-hot label (pre-ADR 0006 launches) stop cleanly and never invoke
# model-library cleanup.
set -euo pipefail
SCRIPT_NAME=down
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-}"
[ -n "$TARGET" ] || die "usage: $0 <model-name|--all> [--node NODE_ID] [--retain-weights|--pin-weights|--purge-hot]"
shift || true
NODE_SELECTOR=""
HOT_AFTER=""
MODEL_LIBRARY_CMD="${PULSAR_MODEL_LIBRARY_CMD:-$REPO_DIR/scripts/model-library.sh}"
HOT_FLAG_ERR="use only one of --retain-weights, --pin-weights, or --purge-hot"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    --retain-weights)
      [ -z "$HOT_AFTER" ] || die "$HOT_FLAG_ERR" 2
      HOT_AFTER=retain
      ;;
    --pin-weights)
      [ -z "$HOT_AFTER" ] || die "$HOT_FLAG_ERR" 2
      HOT_AFTER=pin
      ;;
    --purge-hot)
      [ -z "$HOT_AFTER" ] || die "$HOT_FLAG_ERR" 2
      HOT_AFTER=purge-explicit
      ;;
    *) die "unknown arg: $1" 2 ;;
  esac
  shift
done

validate_hot_stop_policy() {
  if [ "${PULSAR_HOT_STOP_POLICY+x}" != x ]; then
    return 0
  fi
  case "${PULSAR_HOT_STOP_POLICY}" in
    retain|purge) return 0 ;;
    *) die "PULSAR_HOT_STOP_POLICY must be retain or purge" 2 ;;
  esac
}
validate_hot_stop_policy

library_hot_after_stop() {
  local profile="${1:?}"
  [ -n "$HOT_AFTER" ] || return 0
  local -a node_args=()
  if [ "${NODES:-1}" -eq 1 ] && [ -n "$NODE_SELECTOR" ]; then
    node_args=(--node "$NODE_SELECTOR")
  fi
  case "$HOT_AFTER" in
    retain)
      log "retaining unpinned library-hot staging for $profile (next start can reuse verified views; durable home still required; --purge-hot frees disk)"
      ;;
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
      log "purging unpinned library-hot staging for $profile (site policy purge)"
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
  local profile="${1:?}" cname="${2:?}" source policy
  [ -z "$HOT_AFTER" ] || return 0
  if ! source=$(observe_profile_weight_source "$profile" "$cname"); then
    warn "could not prove the service weight policy; hot storage will be left unchanged"
    return 0
  fi
  if [ "$source" = library-hot ]; then
    policy="${PULSAR_HOT_STOP_POLICY:-retain}"
    case "$policy" in
      retain)
        HOT_AFTER=retain
        log "library-hot service detected; unpinned prepared views will be retained after stop (next start can reuse them; durable home still required)"
        ;;
      purge)
        HOT_AFTER=purge-default
        log "library-hot service detected; site policy purge will remove unpinned prepared views after stop (next start restages from the durable home)"
        ;;
      *)
        die "PULSAR_HOT_STOP_POLICY must be retain or purge" 2
        ;;
    esac
  fi
}

if [ "$TARGET" = "--all" ]; then
  [ -z "$NODE_SELECTOR" ] || die "--node cannot be combined with --all" 2
  [ -z "$HOT_AFTER" ] || die "--retain-weights/--pin-weights/--purge-hot cannot be combined with --all" 2
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

if [ ! -f "$REPO_DIR/models/${TARGET}.conf" ]; then
  # Retired profile (e.g. removed by ADR 0006): the conf file is gone, but a
  # container that proves ownership through its labels must stay stoppable.
  # Geometry comes from the containers themselves; hot retention hooks need
  # the serving profile and are unavailable here.
  [ -z "$HOT_AFTER" ] \
    || die "--retain-weights/--pin-weights/--purge-hot need a current serving profile; retired confs stop plainly" 2
  log "conf $TARGET has no models/${TARGET}.conf; stopping by proven container labels"
  load_cluster_topology || die "confirmed topology is invalid"
  retired_cluster_name=$(container_name_for "$TARGET" 2)
  # Probe EVERY confirmed node for the cluster container: a partially torn
  # down retired profile may have live remote ranks with no local rank 0,
  # and after a membership reconfirm a surviving rank may sit at any index.
  # Ranks are therefore removed at the node where each was OBSERVED, and
  # only when every confirmed node was observable (an unobserved node could
  # host a live rank that removal would strand).
  retired_found_indices=()
  retired_found_ids=()
  retired_unobservable=""
  retired_count="$CLUSTER_TOPOLOGY_COUNT"
  [ "$retired_count" -gt 0 ] || retired_count=1
  for ((retired_index = 0; retired_index < retired_count; retired_index++)); do
    rc=0
    probe_meta=$(container_ownership_inspect_on_node \
      "$retired_index" "$retired_cluster_name") || rc=$?
    case "$rc" in
      3) continue ;;
      0)
        IFS=$'\t' read -r probe_id _ probe_managed probe_conf probe_rank \
          < <(container_ownership_fields "$probe_meta")
        container_managed_label_is_true "$probe_managed" \
          || die "refused: $retired_cluster_name on node $retired_index is not stack-managed"
        [ "$probe_conf" = "$TARGET" ] \
          || die "refused: $retired_cluster_name on node $retired_index carries conf '$probe_conf'"
        [[ "$probe_rank" =~ ^[0-9]+$ ]] \
          || die "refused: $retired_cluster_name on node $retired_index has rank '$probe_rank'"
        probe_world=$(container_world_size_field "$probe_meta")
        [[ "$probe_world" =~ ^[2-9][0-9]*$ ]] && [ "$probe_rank" -lt "$probe_world" ] \
          || die "refused: $retired_cluster_name on node $retired_index has no usable world-size label"
        retired_found_indices+=("$retired_index")
        retired_found_ids+=("$probe_id")
        ;;
      *) retired_unobservable+=" $retired_index" ;;
    esac
  done
  if [ "${#retired_found_indices[@]}" -gt 0 ]; then
    [ -z "$NODE_SELECTOR" ] || die "--node is only valid for one-node services" 2
    [ -z "$retired_unobservable" ] \
      || die "confirmed node(s)$retired_unobservable are unobservable — not removing retired $TARGET ranks (an unobserved live rank could be stranded); restore those nodes or clean up manually"
    rc=0
    for retired_index in "${!retired_found_indices[@]}"; do
      # Stop at the FIRST failure: continuing past an error or a race could
      # strand a live rank behind a node that just became unobservable.
      remove_retired_cluster_rank_on_node \
        "${retired_found_indices[$retired_index]}" \
        "${retired_found_ids[$retired_index]}" "$TARGET" || { rc=$?; break; }
    done
    case "$rc" in
      0) log "done"; exit 0 ;;
      2) die "refused: $retired_cluster_name ownership changed during removal; re-run after inspecting ./pulsar inventory" ;;
      *) die "failed while removing $retired_cluster_name ranks (rc=$rc); re-run after restoring node observability" ;;
    esac
  fi
  [ -z "$retired_unobservable" ] \
    || die "confirmed node(s)$retired_unobservable are unobservable — cannot conclude retired $TARGET has no cluster ranks"
  if [ -z "$NODE_SELECTOR" ]; then
    rc=0
    placement_index=$(discover_single_node_index_for_conf "$TARGET") || rc=$?
    case "$rc" in
      0)
        NODE_SELECTOR="${CLUSTER_NODE_IDS[$placement_index]:-}"
        [ -n "$NODE_SELECTOR" ] \
          || NODE_SELECTOR=$(single_node_key_for_index "$placement_index")
        ;;
      3)
        log "no stack-managed service found for retired conf=$TARGET"
        exit 0
        ;;
      2) die "refused to stop $TARGET: placement or ownership is ambiguous" ;;
      *) die "cannot safely discover $TARGET placement across confirmed nodes" ;;
    esac
  fi
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement '$NODE_SELECTOR'"
  rc=0
  remove_stack_owned_single_at_resolved_node "$TARGET" || rc=$?
  case "$rc" in
    0) exit 0 ;;
    2) die "refused to stop $(container_name_for "$TARGET" 1) on $(single_node_display): ownership or node identity is not proven" ;;
    *) die "failed to stop $(container_name_for "$TARGET" 1) on $(single_node_display)" ;;
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
