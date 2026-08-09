#!/usr/bin/env bash
# Federated model library: warm catalog + optional cold + hot staging.
# Does not change replicated defaults or experimental live fabric launch.
set -euo pipefail
SCRIPT_NAME=model-library
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_LIBRARY_PY:-$REPO_DIR/scripts/model_library.py}"
LIBRARY_DIR="${MODEL_LIBRARY_DIR:-$REPO_DIR/.model-library}"
CATALOG_FILE="${MODEL_LIBRARY_CATALOG:-$LIBRARY_DIR/catalog.json}"
HOT_ROOT="${PULSAR_HOT_ROOT:-/var/tmp/pulsar-hot}"
# Optional cold archive (site NFS). Empty PULSAR_COLD_ROOT disables cold.
# When unset, MODELS_NFS (default /mnt/Models from lib.sh) is used.
COLD_ROOT="${PULSAR_COLD_ROOT-}"
if [ -z "${PULSAR_COLD_ROOT+x}" ]; then
  COLD_ROOT="${MODELS_NFS:-}"
fi
LIBRARY_SUDO_MODE="${LIBRARY_SUDO_MODE:-passwordless}"
case "$LIBRARY_SUDO_MODE" in
  passwordless|interactive) ;;
  *) die "LIBRARY_SUDO_MODE must be passwordless or interactive" ;;
esac

usage() {
  cat <<'EOF'
Federated model library (warm catalog + optional cold + hot staging)

Usage:
  scripts/model-library.sh catalog refresh [--json] [--local-only]
  scripts/model-library.sh catalog list [--validated] [--json]
  scripts/model-library.sh catalog show <model_id|profile> [--json]
  scripts/model-library.sh resolve <profile|model_id|/abs/path> [--json] [--no-cold]
  scripts/model-library.sh cleanup-recommend [--json]
  scripts/model-library.sh cold scan [--json] [--complete-only] [--root PATH]
  scripts/model-library.sh cold show <model_id|/abs/path> [--json]
  scripts/model-library.sh cold adopt <model_id|profile|/abs/path>
      [--cache-root PATH] [--yes]
  scripts/model-library.sh cold stage-only <profile>
      [--allow-unvalidated] [--yes] [--nodes N]
  scripts/model-library.sh activate <profile> [--backend copy|fabric]
      [--allow-unvalidated] [--yes] [--interactive-sudo] [--time]
  scripts/model-library.sh release-transfer <profile> [--yes] [--interactive-sudo]
  scripts/model-library.sh pin <profile>
  scripts/model-library.sh unpin <profile>
  scripts/model-library.sh purge-hot <profile> [--yes] [--force-unpin]
  scripts/model-library.sh budget [--json]

Notes:
  • Scans default HF cache hubs on confirmed topology nodes (warm catalog).
  • Optional cold archive (PULSAR_COLD_ROOT or MODELS_NFS): Official Models/
    org/name flat trees and hub/models--* layouts. Resolve: warm → cold.
  • cold adopt imports into a durable warm HF home; cold stage-only fills hot
    only (cold remains sole durable copy; pin still allows warm restart).
  • Labels entries validated vs unvalidated using models/*.conf STATUS.
  • Duplicate complete homes refuse resolve until a primary is chosen.
  • activate --backend copy rsyncs over the control path into PULSAR_HOT_ROOT.
  • activate --backend fabric uses ephemeral NFSv4.2/RDMA over confirmed RoCE
    to fill hot, then releases mounts/export. No silent fallback to copy.
  • pin keeps hot for home-independent restart; purge removes hot (budget).
  • Does not change wizard defaults or --weight-source fabric.
EOF
}

cold_root_args() {
  if [ -n "${COLD_ROOT:-}" ]; then
    printf '%s\n' --cold-root "$COLD_ROOT"
  fi
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
  local query="" json=0 no_cold=0
  local args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --no-cold) no_cold=1 ;;
      --cold-root)
        shift
        [ $# -gt 0 ] || die "--cold-root needs a path"
        COLD_ROOT="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: resolve <profile|model_id|/abs/path> [--json] [--no-cold]"
  require_py
  args=(resolve --models-dir "$REPO_DIR/models")
  if [ -f "$CATALOG_FILE" ]; then
    args+=(--catalog "$CATALOG_FILE")
  else
    args+=(--allow-missing-catalog)
  fi
  if [ "$no_cold" = 1 ]; then
    args+=(--no-cold)
  else
    # shellcheck disable=SC2207
    args+=($(cold_root_args))
  fi
  if [ "$json" = 1 ]; then
    args+=(--json)
  fi
  args+=("$query")
  python3 "$PY_TOOL" "${args[@]}"
}

cmd_cold_scan() {
  local json=0 complete_only=0 root="" args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --complete-only) complete_only=1 ;;
      --root)
        shift
        [ $# -gt 0 ] || die "--root needs a path"
        root="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  args=(scan-cold)
  if [ -n "$root" ]; then
    args+=(--cold-root "$root")
  else
    # shellcheck disable=SC2207
    args+=($(cold_root_args))
  fi
  [ "$complete_only" = 1 ] && args+=(--complete-only)
  [ "$json" = 1 ] && args+=(--json)
  python3 "$PY_TOOL" "${args[@]}"
}

cmd_cold_show() {
  local query="" root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --root)
        shift
        [ $# -gt 0 ] || die "--root needs a path"
        root="$1"
        ;;
      --json) ;; # always JSON object
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: cold show <model_id|/abs/path>"
  require_py
  local args=(find-cold)
  if [ -n "$root" ]; then
    args+=(--cold-root "$root")
  else
    # shellcheck disable=SC2207
    args+=($(cold_root_args))
  fi
  args+=("$query")
  python3 "$PY_TOOL" "${args[@]}"
}

cmd_cold_adopt() {
  local query="" yes=0 cache_root="${HF_CACHE:-$HOME/.cache/huggingface}"
  local root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --cache-root)
        shift
        [ $# -gt 0 ] || die "--cache-root needs a path"
        cache_root="$1"
        ;;
      --root)
        shift
        [ $# -gt 0 ] || die "--root needs a path"
        root="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: cold adopt <model_id|profile|/abs/path> [--cache-root PATH] [--yes]"
  require_py

  local plan_args=(plan-cold-adopt --cache-root "$cache_root" --models-dir "$REPO_DIR/models")
  if [ -n "$root" ]; then
    plan_args+=(--cold-root "$root")
  else
    # shellcheck disable=SC2207
    plan_args+=($(cold_root_args))
  fi
  if [[ "$query" == /* ]]; then
    plan_args+=(--path "$query")
  elif [[ "$query" == */* ]]; then
    plan_args+=(--model "$query")
  else
    plan_args+=(--profile "$query")
  fi

  local plan
  plan=$(python3 "$PY_TOOL" "${plan_args[@]}")
  local model_id source dest bytes
  model_id=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  source=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_path"])')
  dest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dest_hub"])')
  bytes=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("bytes") or 0)')

  library_confirm "$yes" \
    "Adopt cold → warm HF home
  model:  $model_id
  source: $source
  dest:   $dest
  bytes:  $bytes
  After adopt, run: scripts/model-library.sh catalog refresh"

  # Prefer Python materialize (handles flat→hub); works on controller filesystem.
  python3 "$PY_TOOL" "${plan_args[@]}" --execute
  log "adopted $model_id into $dest"
  log "next: scripts/model-library.sh catalog refresh"
}

cmd_cold_stage_only() {
  local profile="" yes=0 allow_unval=0 nodes=1 root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --allow-unvalidated) allow_unval=1 ;;
      --nodes)
        shift
        [ $# -gt 0 ] || die "--nodes needs a value"
        nodes="$1"
        ;;
      --root)
        shift
        [ $# -gt 0 ] || die "--root needs a path"
        root="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$profile" ] || die "unexpected arg: $1"
        profile="$1"
        ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: cold stage-only <profile> [--yes] [--allow-unvalidated]"
  require_py
  load_cluster_topology >/dev/null \
    || die "confirmed topology required (scripts/detect-fabric.sh --write-topology)"

  local plan_args=(
    plan-cold-stage
    --profile "$profile"
    --topology-id "${CLUSTER_TOPOLOGY_ID}"
    --hot-root "$HOT_ROOT"
    --models-dir "$REPO_DIR/models"
    --nodes "$nodes"
  )
  if [ -f "$CATALOG_FILE" ]; then
    plan_args+=(--catalog "$CATALOG_FILE")
  fi
  if [ -n "$root" ]; then
    plan_args+=(--cold-root "$root")
  else
    # shellcheck disable=SC2207
    plan_args+=($(cold_root_args))
  fi
  [ "$allow_unval" = 1 ] && plan_args+=(--allow-unvalidated)

  local plan action
  plan=$(python3 "$PY_TOOL" "${plan_args[@]}")
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  if [ "$action" = "skip" ]; then
    log "hot already ready for $profile (stage-only skip)"
    printf '%s\n' "$plan"
    return 0
  fi

  local model_id source hub_dest instance bytes
  model_id=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  source=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_path"])')
  hub_dest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_dest"])')
  instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  bytes=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("bytes_logical") or 0)')

  library_confirm "$yes" \
    "Stage-only cold → hot (no durable warm home)
  profile: $profile
  model:   $model_id
  source:  $source
  hot:     $hub_dest
  bytes:   $bytes
  Unpinned restart will need cold again."

  # Local controller materialize + stamp (multi-rank stage-only copies hub_dest
  # via the same rsync path as activate when nodes>1 — rank 0 first).
  python3 "$PY_TOOL" "${plan_args[@]}" --execute >/dev/null

  if [ "$nodes" -gt 1 ]; then
    local rank
    for ((rank = 1; rank < nodes; rank++)); do
      copy_hub_to_rank "$rank" "$hub_dest" "$hub_dest" 0
      local stamp_json
      stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
      write_stamp_on_rank "$rank" "$instance" "$stamp_json"
      verify_hot_on_rank "$rank" "$instance" "$profile" "${CLUSTER_TOPOLOGY_ID}"
    done
  fi
  log "stage-only ready: $instance"
  printf '%s\n' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); d["executed"]=True; print(json.dumps(d, indent=2, sort_keys=True))'
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

# --- privileged helpers (mirror weight-fabric patterns; never store passwords) ---

library_node_exec() {
  local rank="${1:?}"
  shift
  if [ "$rank" = 0 ]; then
    bash -c "$*"
  else
    ssh_node "$rank" "$@"
  fi
}

library_node_privileged() {
  local rank="${1:?}"
  shift
  local command host root_script payload
  if [ "$LIBRARY_SUDO_MODE" = interactive ]; then
    root_script=$'set -euo pipefail\n'
    root_script+="$(shell_join_q "$@")"$'\n'
    payload=$(printf '%s' "$root_script" | base64 -w 0)
    command='sudo -v'
    command+=" && $(shell_join_q printf %s "$payload")"
    command+=' | base64 -d | sudo -n bash -s'
    log "sudo authentication required on rank $rank…"
    if [ "$rank" = 0 ]; then
      bash -c "$command"
    else
      host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
      "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -tt -- "$host" "$command"
    fi
  elif [ "$rank" = 0 ]; then
    sudo -n "$@"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q sudo -n "$@")
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command"
  fi
}

library_install_content() {
  local rank="${1:?}" destination="${2:?}" mode="${3:?}" content="${4-}"
  local command host encoded root_script payload
  if [ "$LIBRARY_SUDO_MODE" = interactive ]; then
    encoded=$(printf '%s' "$content" | base64 -w 0)
    root_script=$'set -euo pipefail\n'
    root_script+="$(shell_join_q printf %s "$encoded")"
    root_script+=" | base64 -d | "
    root_script+="$(shell_join_q install -D -m "$mode" /dev/stdin "$destination")"
    root_script+=$'\n'
    payload=$(printf '%s' "$root_script" | base64 -w 0)
    command='sudo -v'
    command+=" && $(shell_join_q printf %s "$payload")"
    command+=' | base64 -d | sudo -n bash -s'
    log "sudo authentication required on rank $rank…"
    if [ "$rank" = 0 ]; then
      bash -c "$command"
    else
      host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
      "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -tt -- "$host" "$command"
    fi
  elif [ "$rank" = 0 ]; then
    printf '%s' "$content" \
      | sudo -n install -D -m "$mode" /dev/stdin "$destination"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q sudo -n install -D -m "$mode" /dev/stdin "$destination")
    printf '%s' "$content" \
      | "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command"
  fi
}

library_confirm() {
  local yes="${1:-0}" message="${2:?}"
  [ "$yes" = 1 ] && return 0
  printf '%s\n' "$message"
  read -r -p "Continue? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "aborted" 3 ;;
  esac
}

# Copy into hot using per-rank source path (local hub or transfer mount).
copy_from_source_on_rank() {
  local rank="${1:?}" source="${2:?}" hub_dest="${3:?}"
  local dest_parent qsrc qdst
  dest_parent=$(dirname "$hub_dest")
  qsrc=$(printf '%q' "$source")
  qdst=$(printf '%q' "$hub_dest")
  if [ "$rank" = 0 ]; then
    mkdir -p "$dest_parent"
    rm -rf "$hub_dest"
    mkdir -p "$hub_dest"
    rsync -a --delete "$source"/ "$hub_dest"/
    return 0
  fi
  ssh_node "$rank" "mkdir -p $(printf '%q' "$dest_parent") && rm -rf $qdst && mkdir -p $qdst"
  # Prefer remote-local rsync when source is already on the target (mount path).
  ssh_node "$rank" "rsync -a --delete $qsrc/ $qdst/" \
    || die "rank $rank: rsync from $source failed"
}

fabric_release_transfer() {
  local plan_json="${1:?}" home_rank export_file nfs_file rank mount_path export_path
  home_rank=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"]["home_rank"])')
  export_file=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"].get("export_file") or "")')
  nfs_file=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; t=json.load(sys.stdin)["transfer"]; print(t.get("nfs_file") or "")')
  export_path=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"].get("export_path") or "")')

  # Unmount clients first (best-effort).
  while IFS=$'\t' read -r rank mount_path; do
    [ -n "$rank" ] || continue
    if [ "$rank" = 0 ]; then
      if findmnt -rn -M "$mount_path" >/dev/null 2>&1; then
        library_node_privileged 0 umount "$mount_path" 2>/dev/null || true
      fi
    else
      if library_node_exec "$rank" "findmnt -rn -M $(printf '%q' "$mount_path")" >/dev/null 2>&1; then
        library_node_privileged "$rank" umount "$mount_path" 2>/dev/null || true
      fi
    fi
  done < <(printf '%s' "$plan_json" | python3 -c '
import json,sys
t=json.load(sys.stdin).get("transfer") or {}
for c in t.get("clients") or []:
    print("%s\t%s" % (c["rank"], c["mount_path"]))
')

  if [ -n "$export_file" ]; then
    if [ -z "$nfs_file" ]; then
      nfs_file="/etc/nfs.conf.d/$(basename "$export_file" .exports).conf"
    fi
    library_node_privileged "$home_rank" rm -f "$export_file" "$nfs_file" 2>/dev/null || true
    library_node_privileged "$home_rank" exportfs -ra 2>/dev/null || true
    # Do not stop nfs-server globally — other exports may exist.
  fi
  log "released fabric transfer plane"
}

fabric_apply_transfer() {
  local plan_json="${1:?}" yes="${2:-0}"
  local home_rank export_path export_file nfs_file port mount_options
  local export_line nfs_conf uid_gid uid gid client_line rank mount_path server_ip

  home_rank=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"]["home_rank"])')
  export_path=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"]["export_path"])')
  export_file=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"]["export_file"])')
  port=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"].get("port") or 20049)')
  mount_options=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["transfer"].get("mount_options") or "")')
  nfs_file="/etc/nfs.conf.d/$(basename "$export_file" .exports).conf"

  # Prerequisites
  library_node_exec "$home_rank" "command -v exportfs >/dev/null 2>&1" \
    || die "home rank $home_rank: nfs-kernel-server/exportfs missing (see weight-fabric setup-prerequisites)"
  while IFS=$'\t' read -r rank _rest; do
    [ -n "$rank" ] || continue
    library_node_exec "$rank" "command -v mount.nfs >/dev/null 2>&1 || command -v mount.nfs4 >/dev/null 2>&1" \
      || die "rank $rank: NFS client tools missing"
  done < <(printf '%s' "$plan_json" | python3 -c '
import json,sys
for c in (json.load(sys.stdin).get("transfer") or {}).get("clients") or []:
    print(c["rank"], "x", sep="\t")
')

  library_confirm "$yes" \
    "Arm ephemeral NFS/RDMA export on home rank $home_rank and mount RoCE clients for library activate?"

  # Owner identity for root_squash mapping
  if [ "$home_rank" = 0 ]; then
    uid_gid=$(stat -c '%u:%g' "$export_path" 2>/dev/null) \
      || die "cannot stat export path $export_path"
  else
    uid_gid=$(library_node_exec "$home_rank" "stat -c '%u:%g' $(printf '%q' "$export_path")") \
      || die "cannot stat export path on home rank $home_rank"
  fi
  uid=${uid_gid%%:*}
  gid=${uid_gid##*:}

  export_line="\"$export_path\""
  while IFS=$'\t' read -r rank server_ip client_ip mount_path; do
    [ -n "$rank" ] || continue
    export_line+=" ${client_ip}(ro,sync,insecure,root_squash,all_squash,anonuid=${uid},anongid=${gid},no_subtree_check)"
  done < <(printf '%s' "$plan_json" | python3 -c '
import json,sys
for c in (json.load(sys.stdin).get("transfer") or {}).get("clients") or []:
    print("%s\t%s\t%s\t%s" % (c["rank"], c["server_ip"], c["client_ip"], c["mount_path"]))
')
  export_line+=$'\n'
  nfs_conf=$'[nfsd]\n'
  nfs_conf+="rdma = ${port}"$'\n'

  # Privilege preflight
  library_node_privileged "$home_rank" true \
    || die "home privilege preflight failed (try --interactive-sudo)"
  while IFS=$'\t' read -r rank _a _b _c; do
    [ -n "$rank" ] || continue
    library_node_privileged "$rank" true \
      || die "rank $rank privilege preflight failed (try --interactive-sudo)"
  done < <(printf '%s' "$plan_json" | python3 -c '
import json,sys
for c in (json.load(sys.stdin).get("transfer") or {}).get("clients") or []:
    print("%s\t.\t.\t." % c["rank"])
')

  log "installing ephemeral export on home rank $home_rank"
  library_install_content "$home_rank" "$export_file" 0644 "$export_line"
  library_install_content "$home_rank" "$nfs_file" 0644 "$nfs_conf"
  library_node_privileged "$home_rank" modprobe svcrdma || true
  library_node_privileged "$home_rank" exportfs -ra
  library_node_privileged "$home_rank" systemctl enable --now nfs-server
  library_node_privileged "$home_rank" systemctl restart nfs-server

  # Wait for RDMA NFS port
  local waited=0
  while [ "$waited" -lt 30 ]; do
    if library_node_exec "$home_rank" "ss -ln | grep -q ':${port} '"; then
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  library_node_exec "$home_rank" "ss -ln | grep -q ':${port} '" \
    || die "NFS/RDMA port $port not listening on home after export"

  log "mounting RoCE clients"
  while IFS=$'\t' read -r rank server_ip client_ip mount_path; do
    [ -n "$rank" ] || continue
    log "  mount rank $rank  $server_ip:$export_path → $mount_path"
    library_node_privileged "$rank" mkdir -p "$mount_path"
    # Replace stale mount if present
    if library_node_exec "$rank" "findmnt -rn -M $(printf '%q' "$mount_path")" >/dev/null 2>&1; then
      library_node_privileged "$rank" umount "$mount_path" || true
    fi
    library_node_privileged "$rank" mount -t nfs4 -o "$mount_options" \
      "${server_ip}:${export_path}" "$mount_path"
    # Fail closed: must be rdma
    library_node_exec "$rank" "findmnt -rn -M $(printf '%q' "$mount_path") -o OPTIONS" \
      | grep -q 'proto=rdma' \
      || die "rank $rank: mount is not proto=rdma (refusing TCP fallback)"
  done < <(printf '%s' "$plan_json" | python3 -c '
import json,sys
for c in (json.load(sys.stdin).get("transfer") or {}).get("clients") or []:
    print("%s\t%s\t%s\t%s" % (c["rank"], c["server_ip"], c["client_ip"], c["mount_path"]))
')
  log "fabric transfer plane armed"
}

cmd_release_transfer() {
  local profile="" yes=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --interactive-sudo) LIBRARY_SUDO_MODE=interactive ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: release-transfer <profile> [--yes]"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  ensure_catalog
  local plan
  plan=$(python3 "$PY_TOOL" plan-activate \
    --catalog "$CATALOG_FILE" \
    --profile "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --hot-root "$HOT_ROOT" \
    --backend fabric \
    --nodes "$NODES")
  local action
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  if [ "$action" != fabric-copy ]; then
    log "no multi-rank fabric transfer plane for this profile (action=$action)"
    return 0
  fi
  library_confirm "$yes" "Release ephemeral library-activate NFS/RDMA mounts/export for $profile?"
  fabric_release_transfer "$plan"
}

cmd_activate() {
  local profile="" backend=copy allow_unvalidated=0 yes=0 time_it=0
  local plan stamp_json instance hub_source hub_dest home_rank rank source
  local start_ts end_ts elapsed
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
      --interactive-sudo) LIBRARY_SUDO_MODE=interactive ;;
      --time) time_it=1 ;;
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

  if [ "$yes" != 1 ]; then
    if [ "$action" = fabric-copy ]; then
      log "fabric plan ready (ephemeral NFS/RDMA transfer → hot)"
      printf '%s\n' "$plan" | python3 -c '
import json,sys
d=json.load(sys.stdin)
t=d.get("transfer") or {}
print("home_rank", t.get("home_rank"), "clients", len(t.get("clients") or []))
for c in t.get("clients") or []:
    print("  client rank", c["rank"], c["server_ip"], "->", c["client_ip"], c["mount_path"])
print("re-run with --yes to execute (no silent fallback to copy)")
'
    else
      log "will copy model to hot staging on ranks: ${target_ranks[*]}"
      log "source rank=$home_rank → $hub_dest"
      log "re-run with --yes to execute"
    fi
    return 0
  fi

  local -a touched=()
  local transfer_armed=0
  # shellcheck disable=SC2317
  cleanup_partial() {
    local r
    if [ "$transfer_armed" = 1 ]; then
      fabric_release_transfer "$plan" 2>/dev/null || true
    fi
    for r in "${touched[@]:-}"; do
      if [ "$r" = 0 ]; then
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance" --force-unpin 2>/dev/null || true
      else
        ssh_node "$r" "rm -rf $(printf '%q' "$instance")" 2>/dev/null || true
      fi
    done
  }
  trap cleanup_partial ERR

  [ "$time_it" = 1 ] && start_ts=$(date +%s)

  if [ "$action" = fabric-copy ]; then
    fabric_apply_transfer "$plan" 1
    transfer_armed=1
    for rank in "${target_ranks[@]}"; do
      source=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["rank_sources"][sys.argv[1]])' "$rank")
      log "fabric copy → rank $rank from $source"
      copy_from_source_on_rank "$rank" "$source" "$hub_dest"
      touched+=("$rank")
      write_stamp_on_rank "$rank" "$instance" "$stamp_json"
      verify_hot_on_rank "$rank" "$instance" "$profile" "$CLUSTER_TOPOLOGY_ID" \
        || die "rank $rank: verify failed after fabric copy"
    done
    fabric_release_transfer "$plan"
    transfer_armed=0
  else
    # control-path copy
    for rank in "${target_ranks[@]}"; do
      log "copy → rank $rank"
      copy_hub_to_rank "$rank" "$hub_source" "$hub_dest" "$home_rank"
      touched+=("$rank")
      write_stamp_on_rank "$rank" "$instance" "$stamp_json"
      verify_hot_on_rank "$rank" "$instance" "$profile" "$CLUSTER_TOPOLOGY_ID" \
        || die "rank $rank: verify failed after copy"
    done
  fi

  trap - ERR
  if [ "$time_it" = 1 ]; then
    end_ts=$(date +%s)
    elapsed=$((end_ts - start_ts))
    log "activate wall_time_seconds=$elapsed backend=$backend"
  fi
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
    cold)
      [ $# -ge 1 ] || { usage; exit 2; }
      local cold_sub="$1"
      shift
      case "$cold_sub" in
        scan) cmd_cold_scan "$@" ;;
        show) cmd_cold_show "$@" ;;
        adopt) cmd_cold_adopt "$@" ;;
        stage-only) cmd_cold_stage_only "$@" ;;
        *) usage; exit 2 ;;
      esac
      ;;
    activate) cmd_activate "$@" ;;
    release-transfer) cmd_release_transfer "$@" ;;
    pin) cmd_pin "$@" ;;
    unpin) cmd_unpin "$@" ;;
    purge-hot) cmd_purge_hot "$@" ;;
    budget) cmd_budget "$@" ;;
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
