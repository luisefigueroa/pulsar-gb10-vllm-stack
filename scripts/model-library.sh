#!/usr/bin/env bash
# Federated model library: warm catalog + copy activate into hot staging.
# Does not change replicated defaults or experimental live fabric launch.
set -euo pipefail
SCRIPT_NAME=model-library
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_LIBRARY_PY:-$REPO_DIR/scripts/model_library.py}"
LIBRARY_DIR="${MODEL_LIBRARY_DIR:-$REPO_DIR/.model-library}"
CATALOG_FILE="${MODEL_LIBRARY_CATALOG:-$LIBRARY_DIR/catalog.json}"
HOT_ROOT="${PULSAR_HOT_ROOT:-/var/tmp/pulsar-hot}"

usage() {
  cat <<'EOF'
Federated model library (warm catalog + hot staging)

Usage:
  scripts/model-library.sh catalog refresh [--json] [--local-only]
  scripts/model-library.sh catalog list [--validated] [--json]
  scripts/model-library.sh catalog show <model_id|profile> [--json]
  scripts/model-library.sh resolve <profile|model_id> [--json]
  scripts/model-library.sh cleanup-recommend [--json]
  scripts/model-library.sh activate <profile> [--backend copy|fabric] [--allow-unvalidated] [--yes]
  scripts/model-library.sh pin <profile>
  scripts/model-library.sh unpin <profile>
  scripts/model-library.sh purge-hot <profile> [--yes] [--force-unpin]
  scripts/model-library.sh budget [--json]

Notes:
  • Scans default HF cache hubs on confirmed topology nodes (warm catalog).
  • Labels entries validated vs unvalidated using models/*.conf STATUS.
  • Duplicate complete homes refuse resolve until a primary is chosen.
  • activate --backend copy rsyncs over the control path into PULSAR_HOT_ROOT.
  • activate --backend fabric plans ephemeral NFSv4.2/RDMA transfer (RoCE);
    privileged apply/release is a follow-up; no silent fallback to copy.
  • pin keeps hot for home-independent restart; purge removes hot (budget).
  • Does not change wizard defaults or --weight-source fabric.
EOF
}

require_py() {
  [ -f "$PY_TOOL" ] || die "missing $PY_TOOL"
  command -v python3 >/dev/null 2>&1 || die "python3 required"
}

catalog_path_args() {
  printf '%s\n' --catalog "$CATALOG_FILE"
}

ensure_catalog() {
  [ -f "$CATALOG_FILE" ] || die "no catalog at $CATALOG_FILE — run: scripts/model-library.sh catalog refresh"
}

scan_rank_homes() {
  local rank="${1:?}" node_id hostname ssh_host cache_root out
  node_id="${CLUSTER_NODE_IDS[$rank]:-}"
  hostname="${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
  ssh_host="${CLUSTER_NODE_SSH_HOSTS[$rank]:-}"
  [ -n "$node_id" ] || die "rank $rank: missing node_id in topology"
  cache_root="${HF_CACHE:-$HOME/.cache/huggingface}"

  # Rank 0 is the controller running this script.
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" scan-hub \
      --cache-root "$cache_root" \
      --rank "$rank" \
      --node-id "$node_id" \
      --hostname "$hostname" \
      --ssh-host "${ssh_host:-local}"
    return 0
  fi

  [ -n "$ssh_host" ] || die "rank $rank: missing ssh_host"
  # Stream model_library.py on stdin so remotes need no repo checkout.
  # python3 - SCRIPT_ARGS...  reads the program from stdin.
  if ! out=$(
    ssh_node "$rank" \
      "python3 - scan-hub --cache-root \"\${HF_CACHE:-\$HOME/.cache/huggingface}\" \
        --rank $(printf '%q' "$rank") \
        --node-id $(printf '%q' "$node_id") \
        --hostname $(printf '%q' "$hostname") \
        --ssh-host $(printf '%q' "$ssh_host")" \
      <"$PY_TOOL"
  ); then
    die "rank $rank ($ssh_host): catalog scan failed over SSH"
  fi
  printf '%s\n' "$out"
}

cmd_catalog_refresh() {
  local json=0 local_only=0 tmp all_homes rank homes_piece
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --local-only) local_only=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  load_cluster_topology >/dev/null \
    || die "confirmed topology required (scripts/detect-fabric.sh --write-topology)"

  tmp=$(mktemp "${TMPDIR:-/tmp}/pulsar-library-homes.XXXXXX")
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" RETURN

  all_homes="[]"
  if [ "$local_only" = 1 ]; then
    all_homes=$(python3 "$PY_TOOL" scan-hub \
      --cache-root "${HF_CACHE:-$HOME/.cache/huggingface}" \
      --rank 0 \
      --node-id "${CLUSTER_NODE_IDS[0]:-local}" \
      --hostname "${CLUSTER_NODE_HOSTNAMES[0]:-$(hostname -s 2>/dev/null || echo local)}" \
      --ssh-host local)
  else
    for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
      homes_piece=$(scan_rank_homes "$rank") || die "scan failed on rank $rank"
      all_homes=$(HOMES_A="$all_homes" HOMES_B="$homes_piece" python3 - <<'PY'
import json, os
a = json.loads(os.environ["HOMES_A"])
b = json.loads(os.environ["HOMES_B"])
if not isinstance(a, list) or not isinstance(b, list):
    raise SystemExit("homes must be lists")
print(json.dumps(a + b))
PY
)
    done
  fi
  printf '%s\n' "$all_homes" >"$tmp"
  mkdir -p "$LIBRARY_DIR"
  local build_args=(
    build
    --topology-id "${CLUSTER_TOPOLOGY_ID}"
    --models-dir "$REPO_DIR/models"
    --homes-json "$tmp"
    --output "$CATALOG_FILE"
  )
  if [ "$json" = 1 ]; then
    build_args+=(--json)
  fi
  python3 "$PY_TOOL" "${build_args[@]}"
}

cmd_catalog_list() {
  local args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) args+=(--json) ;;
      --validated) args+=(--validated) ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  ensure_catalog
  python3 "$PY_TOOL" list --catalog "$CATALOG_FILE" "${args[@]}"
}

cmd_catalog_show() {
  local query="" json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: catalog show <model_id|profile> [--json]"
  require_py
  ensure_catalog
  if [ "$json" = 1 ]; then
    python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" --json "$query"
  else
    python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" "$query"
  fi
}

cmd_resolve() {
  local query="" json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: resolve <profile|model_id> [--json]"
  require_py
  # Absolute-path / NFS profiles are Phase 3
  if [[ "$query" == /* ]]; then
    die "resolve: absolute/cold paths are not supported yet (Phase 3)"
  fi
  if [[ "$query" != */* ]]; then
    # profile name — reject nfs confs early
    if [ -f "$REPO_DIR/models/${query}.conf" ]; then
      # shellcheck disable=SC1090
      MODEL=""
      # shellcheck disable=SC1090
      . "$REPO_DIR/models/${query}.conf"
      case "${MODEL:-}" in
        /*) die "resolve: profile $query uses NFS/catalog path (Phase 3); warm catalog is HF-hub only" ;;
      esac
    fi
  fi
  ensure_catalog
  if [ "$json" = 1 ]; then
    python3 "$PY_TOOL" resolve --catalog "$CATALOG_FILE" --json "$query"
  else
    python3 "$PY_TOOL" resolve --catalog "$CATALOG_FILE" "$query"
  fi
}

cmd_cleanup_recommend() {
  local json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  ensure_catalog
  if [ "$json" = 1 ]; then
    python3 "$PY_TOOL" cleanup-recommend --catalog "$CATALOG_FILE" --json
  else
    python3 "$PY_TOOL" cleanup-recommend --catalog "$CATALOG_FILE"
  fi
}

# Copy hub tree from source path to dest path on a target rank.
# Source is always read from the home node (controller uses local path or ssh).
copy_hub_to_rank() {
  local target_rank="${1:?}" hub_source="${2:?}" hub_dest="${3:?}" home_rank="${4:?}"
  local home_host dest_parent qsrc qdst remote

  dest_parent=$(dirname "$hub_dest")
  if [ "$target_rank" = 0 ]; then
    mkdir -p "$dest_parent"
    rm -rf "$hub_dest"
    mkdir -p "$hub_dest"
    if [ "$home_rank" = 0 ]; then
      rsync -a --delete "$hub_source"/ "$hub_dest"/
    else
      home_host="${CLUSTER_NODE_SSH_HOSTS[$home_rank]:-}"
      [ -n "$home_host" ] || die "home rank $home_rank: missing ssh host"
      rsync -a --delete -e "ssh ${PULSAR_SSH_OPTS[*]}" \
        "${home_host}:${hub_source}/" "$hub_dest"/
    fi
    return 0
  fi

  # Remote target: stage via ssh + rsync
  qdst=$(printf '%q' "$hub_dest")
  ssh_node "$target_rank" "mkdir -p $(printf '%q' "$dest_parent") && rm -rf $qdst && mkdir -p $qdst"
  if [ "$home_rank" = 0 ]; then
    rsync -a --delete -e "ssh ${PULSAR_SSH_OPTS[*]}" \
      "$hub_source"/ "${CLUSTER_NODE_SSH_HOSTS[$target_rank]}:${hub_dest}/"
  elif [ "$home_rank" = "$target_rank" ]; then
    # Home is this remote rank: local copy on remote
    qsrc=$(printf '%q' "$hub_source")
    ssh_node "$target_rank" "rsync -a --delete $qsrc/ $qdst/"
  else
    # home remote -> target remote (pull to controller temp then push — simpler via rsync remote-remote if permitted)
    home_host="${CLUSTER_NODE_SSH_HOSTS[$home_rank]:-}"
    [ -n "$home_host" ] || die "home rank $home_rank: missing ssh host"
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-hot-stage.XXXXXX")
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    rsync -a --delete -e "ssh ${PULSAR_SSH_OPTS[*]}" \
      "${home_host}:${hub_source}/" "$tmp"/
    rsync -a --delete -e "ssh ${PULSAR_SSH_OPTS[*]}" \
      "$tmp"/ "${CLUSTER_NODE_SSH_HOSTS[$target_rank]}:${hub_dest}/"
  fi
}

write_stamp_on_rank() {
  local rank="${1:?}" instance_dir="${2:?}" stamp_json="${3:?}"
  local stamp_file
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" write-hot-stamp \
      --instance-dir "$instance_dir" \
      --stamp-json "$stamp_json" >/dev/null
    return 0
  fi
  stamp_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-hot-stamp.XXXXXX")
  # shellcheck disable=SC2064
  trap "rm -f '$stamp_file'" RETURN
  printf '%s\n' "$stamp_json" >"$stamp_file"
  # Stream py + stamp file content
  ssh_node "$rank" "mkdir -p $(printf '%q' "$instance_dir/.pulsar")"
  rsync -a -e "ssh ${PULSAR_SSH_OPTS[*]}" \
    "$stamp_file" "${CLUSTER_NODE_SSH_HOSTS[$rank]}:${instance_dir}/.pulsar/hot.json.tmp"
  # Use remote python to atomic-replace via write-hot-stamp from stdin module
  ssh_node "$rank" \
    "python3 - write-hot-stamp --instance-dir $(printf '%q' "$instance_dir") --stamp-file $(printf '%q' "$instance_dir/.pulsar/hot.json.tmp")" \
    <"$PY_TOOL" >/dev/null
}

verify_hot_on_rank() {
  local rank="${1:?}" instance_dir="${2:?}" profile="${3:?}" topology_id="${4:?}"
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" verify-hot \
      --instance-dir "$instance_dir" \
      --profile "$profile" \
      --topology-id "$topology_id" >/dev/null
    return 0
  fi
  ssh_node "$rank" \
    "python3 - verify-hot --instance-dir $(printf '%q' "$instance_dir") --profile $(printf '%q' "$profile") --topology-id $(printf '%q' "$topology_id")" \
    <"$PY_TOOL" >/dev/null
}

cmd_activate() {
  local profile="" backend=copy allow_unvalidated=0 yes=0
  local plan plan_file stamp_json instance hub_source hub_dest home_rank rank
  local -a target_ranks=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --backend)
        [ $# -ge 2 ] || die "--backend requires copy or fabric"
        backend="$2"
        shift
        ;;
      --allow-unvalidated) allow_unvalidated=1 ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$profile" ] || die "unexpected arg: $1"
        profile="$1"
        ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: activate <profile> [--backend copy|fabric]"
  case "$backend" in
    copy|fabric) ;;
    *) die "activate: --backend must be copy or fabric" ;;
  esac
  require_py
  ensure_catalog
  load_conf "$profile"
  load_cluster_topology >/dev/null \
    || die "confirmed topology required"

  local plan_flags=(
    plan-activate
    --catalog "$CATALOG_FILE"
    --profile "$profile"
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --topology-file "$CLUSTER_TOPOLOGY_FILE"
    --hot-root "$HOT_ROOT"
    --backend "$backend"
    --nodes "$NODES"
  )
  [ "$allow_unvalidated" = 1 ] && plan_flags+=(--allow-unvalidated)

  plan=$(python3 "$PY_TOOL" "${plan_flags[@]}")
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  hub_source=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hub_source") or "")')
  hub_dest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_dest"])')
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
  mapfile -t target_ranks < <(printf '%s' "$plan" | python3 -c 'import json,sys; print("\n".join(str(x) for x in json.load(sys.stdin)["target_ranks"]))')

  log "activate $profile action=$action backend=$backend hot=$instance"

  if [ "$action" = skip ]; then
    log "hot already ready — verifying ranks"
    for rank in "${target_ranks[@]}"; do
      verify_hot_on_rank "$rank" "$instance" "$profile" "$CLUSTER_TOPOLOGY_ID" \
        || die "rank $rank: hot verify failed"
    done
    log "activate complete (reused hot)"
    return 0
  fi

  if [ "$action" = fabric-copy ]; then
    # PR-A: plan only. Privileged NFS export/mount lands in the next PR.
    if [ "$yes" != 1 ]; then
      log "fabric plan ready (ephemeral NFS/RDMA transfer → hot)"
      printf '%s\n' "$plan" | python3 -c '
import json,sys
d=json.load(sys.stdin)
t=d.get("transfer") or {}
print("home_rank", t.get("home_rank"), "clients", len(t.get("clients") or []))
for c in t.get("clients") or []:
    print("  client rank", c["rank"], c["server_ip"], "->", c["client_ip"], c["mount_path"])
print("re-run with --yes after fabric transfer plane is implemented (next PR)")
'
      return 0
    fi
    die "activate --backend fabric execution is not implemented yet (plan OK; transfer plane next). Use --backend copy --yes for now."
  fi

  if [ "$yes" != 1 ]; then
    log "will copy model to hot staging on ranks: ${target_ranks[*]}"
    log "source rank=$home_rank → $hub_dest"
    log "re-run with --yes to execute"
    return 0
  fi

  # All-or-nothing: on failure, purge partial instances on ranks we touched
  local -a touched=()
  # shellcheck disable=SC2317
  cleanup_partial() {
    local r
    for r in "${touched[@]:-}"; do
      if [ "$r" = 0 ]; then
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance" --force-unpin 2>/dev/null || true
      else
        ssh_node "$r" "rm -rf $(printf '%q' "$instance")" 2>/dev/null || true
      fi
    done
  }
  trap cleanup_partial ERR

  for rank in "${target_ranks[@]}"; do
    log "copy → rank $rank"
    copy_hub_to_rank "$rank" "$hub_source" "$hub_dest" "$home_rank"
    touched+=("$rank")
    write_stamp_on_rank "$rank" "$instance" "$stamp_json"
    verify_hot_on_rank "$rank" "$instance" "$profile" "$CLUSTER_TOPOLOGY_ID" \
      || die "rank $rank: verify failed after copy"
  done
  trap - ERR
  log "activate complete"
  printf '%s\n' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["instance_dir"]); print(d["hub_dest"])'
}

hot_instance_for_profile() {
  local profile="${1:?}"
  python3 "$PY_TOOL" find-hot \
    --profile "$profile" \
    --topology-id "${CLUSTER_TOPOLOGY_ID}" \
    --hot-root "$HOT_ROOT"
}

cmd_pin() {
  local profile="" 
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: pin <profile>"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank
  info=$(hot_instance_for_profile "$profile")
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  for ((rank = 0; rank < NODES; rank++)); do
    if [ "$rank" = 0 ]; then
      python3 "$PY_TOOL" set-pinned --instance-dir "$instance" --pinned >/dev/null
    else
      ssh_node "$rank" \
        "python3 - set-pinned --instance-dir $(printf '%q' "$instance") --pinned" \
        <"$PY_TOOL" >/dev/null
    fi
  done
  log "pinned $profile at $instance"
}

cmd_unpin() {
  local profile=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: unpin <profile>"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank
  info=$(hot_instance_for_profile "$profile")
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  for ((rank = 0; rank < NODES; rank++)); do
    if [ "$rank" = 0 ]; then
      python3 "$PY_TOOL" set-pinned --instance-dir "$instance" --no-pinned >/dev/null
    else
      ssh_node "$rank" \
        "python3 - set-pinned --instance-dir $(printf '%q' "$instance") --no-pinned" \
        <"$PY_TOOL" >/dev/null
    fi
  done
  log "unpinned $profile at $instance"
}

cmd_purge_hot() {
  local profile="" yes=0 force_unpin=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --force-unpin) force_unpin=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: purge-hot <profile> [--yes] [--force-unpin]"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank
  info=$(hot_instance_for_profile "$profile") || die "no hot instance for $profile"
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  [ "$yes" = 1 ] || die "purge-hot will delete $instance — re-run with --yes"
  for ((rank = 0; rank < NODES; rank++)); do
    if [ "$rank" = 0 ]; then
      if [ "$force_unpin" = 1 ]; then
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance" --force-unpin
      else
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance"
      fi
    else
      if [ "$force_unpin" = 1 ]; then
        ssh_node "$rank" "rm -rf $(printf '%q' "$instance")" || true
      else
        ssh_node "$rank" \
          "python3 - purge-hot --instance-dir $(printf '%q' "$instance")" \
          <"$PY_TOOL" || die "rank $rank: purge failed (pinned? use --force-unpin)"
      fi
    fi
  done
  log "purged hot for $profile"
}

cmd_budget() {
  local json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  if [ "$json" = 1 ]; then
    python3 "$PY_TOOL" budget --hot-root "$HOT_ROOT" --json
  else
    python3 "$PY_TOOL" budget --hot-root "$HOT_ROOT"
  fi
}

main() {
  [ $# -ge 1 ] || { usage; exit 2; }
  local cmd="$1"
  shift
  case "$cmd" in
    catalog)
      [ $# -ge 1 ] || { usage; exit 2; }
      local sub="$1"
      shift
      case "$sub" in
        refresh) cmd_catalog_refresh "$@" ;;
        list) cmd_catalog_list "$@" ;;
        show) cmd_catalog_show "$@" ;;
        *) usage; exit 2 ;;
      esac
      ;;
    resolve) cmd_resolve "$@" ;;
    cleanup-recommend) cmd_cleanup_recommend "$@" ;;
    activate) cmd_activate "$@" ;;
    pin) cmd_pin "$@" ;;
    unpin) cmd_unpin "$@" ;;
    purge-hot) cmd_purge_hot "$@" ;;
    budget) cmd_budget "$@" ;;
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
