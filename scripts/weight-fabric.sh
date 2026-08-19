#!/usr/bin/env bash
# Retired live NFS/RDMA serving workflow (ADR 0005).
#
# Serving commands fail closed. Leftover site mounts may still be inspected
# (show) or removed (unmount/teardown). One-shot nfs-rdma prepare lives in
# model-library.sh and is not decided here. Replicated caches remain default.
set -euo pipefail
SCRIPT_NAME=weight-fabric
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="$REPO_DIR/scripts/weight_fabric.py"
WEIGHT_FABRIC_DIR="${WEIGHT_FABRIC_DIR:-$REPO_DIR/.weight-fabric}"
WEIGHT_FABRIC_MOUNT_ROOT="${WEIGHT_FABRIC_MOUNT_ROOT:-/mnt/pulsar-weight-fabric}"
WEIGHT_FABRIC_PORT="${WEIGHT_FABRIC_PORT:-20049}"
WEIGHT_FABRIC_HF_CLI_VERSION="${WEIGHT_FABRIC_HF_CLI_VERSION:-1.26.1}"
WEIGHT_FABRIC_SUDO_MODE="${WEIGHT_FABRIC_SUDO_MODE:-passwordless}"
case "$WEIGHT_FABRIC_SUDO_MODE" in
  passwordless|interactive) ;;
  *) die "WEIGHT_FABRIC_SUDO_MODE must be passwordless or interactive" ;;
esac

usage() {
  cat <<'EOF'
Retired live NFS/RDMA serving workflow (ADR 0005)

Live NFS/RDMA under vLLM is not a serving runtime source. Launch with
library-hot or replicated. Leftover site mounts may be shown, unmounted, or
torn down. One-shot nfs-rdma prepare is a separate experiment.

Usage:
  scripts/weight-fabric.sh show <profile> [--json]
  scripts/weight-fabric.sh unmount <profile> [--yes]
  scripts/weight-fabric.sh teardown <profile> [--yes]

Safety:
  • Serving/apply/mount/benchmark commands fail closed (ADR 0005).
  • unmount refuses any client mount still used by a container.
  • teardown removes only this configuration's export and mount state.
  • --interactive-sudo prompts in a terminal; Pulsar stores no password.
  • --yes is only for an already reviewed leftover-teardown runbook.
  • no command remaps to replicated or library-hot.
EOF
}

fabric_config_path() {
  local profile="${1:?profile required}"
  if [ -n "${WEIGHT_FABRIC_CONFIG:-}" ]; then
    printf '%s\n' "$WEIGHT_FABRIC_CONFIG"
  else
    printf '%s/%s.json\n' "$WEIGHT_FABRIC_DIR" "$profile"
  fi
}

declare -ag WF_RANK_NODE_IDS=()
declare -ag WF_RANK_HOSTNAMES=()
declare -ag WF_RANK_SSH_HOSTS=()
declare -ag WF_RANK_ROLES=()
declare -ag WF_RANK_CACHE_ROOTS=()
declare -ag WF_SERVER_IPS=()
declare -ag WF_CLIENT_IPS=()
declare -ag WF_CLIENT_NETDEVS=()
declare -ag WF_CLIENT_HCAS=()
declare -ag WF_CLIENT_NETWORKS=()
declare -ag WF_SERVER_NETDEVS=()
declare -ag WF_SERVER_HCAS=()

load_fabric() {
  local profile="${1:?profile required}" allow_legacy="${2:-0}" rows kind
  local a b c d e f g h i j k l m n o p
  local -a row_args=()
  load_conf "$profile"
  [ "$NODES" -gt 1 ] \
    || die "$profile is not a multi-node profile"
  [ "$(model_source_kind)" = hf ] \
    || die "$profile does not use a Hugging Face cache"
  require_profile_topology "$NODES" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
    || die "$profile topology is not currently confirmed"

  WF_CONFIG_PATH=$(fabric_config_path "$profile")
  [ -f "$WF_CONFIG_PATH" ] \
    || die "no fabric config for $profile; run: $0 configure $profile --owner NODE_ID"
  [ "$allow_legacy" = 1 ] && row_args+=(--allow-legacy-teardown)
  rows=$(
    "$PY_TOOL" rows "$WF_CONFIG_PATH" "$CLUSTER_TOPOLOGY_FILE" \
      --profile "$profile" --model "$MODEL" --nodes "$NODES" \
      "${row_args[@]}"
  ) || die "fabric config is invalid or stale: $WF_CONFIG_PATH"

  WF_RANK_NODE_IDS=()
  WF_RANK_HOSTNAMES=()
  WF_RANK_SSH_HOSTS=()
  WF_RANK_ROLES=()
  WF_RANK_CACHE_ROOTS=()
  WF_SERVER_IPS=()
  WF_CLIENT_IPS=()
  WF_CLIENT_NETDEVS=()
  WF_CLIENT_HCAS=()
  WF_CLIENT_NETWORKS=()
  WF_SERVER_NETDEVS=()
  WF_SERVER_HCAS=()
  while IFS=$'\t' read -r kind a b c d e f g h i j k l m n o p; do
    case "$kind" in
      META)
        WF_CONFIG_ID="$a"
        WF_TOPOLOGY_ID="$b"
        WF_PROFILE="$c"
        WF_MODEL="$d"
        WF_NODES="$e"
        WF_OWNER_RANK="$f"
        WF_OWNER_NODE_ID="$g"
        WF_OWNER_HOSTNAME="$h"
        WF_OWNER_SSH_HOST="$i"
        WF_OWNER_CACHE_ROOT="$j"
        WF_TRANSPORT="$k"
        WF_PORT="$l"
        WF_EXPORT_PATH="$m"
        WF_MOUNT_PATH="$n"
        WF_MANIFEST_RELATIVE="$o"
        WF_STORAGE_NODES="$p"
        ;;
      RANK)
        WF_RANK_NODE_IDS["$a"]="$b"
        WF_RANK_HOSTNAMES["$a"]="$c"
        WF_RANK_SSH_HOSTS["$a"]="$d"
        WF_RANK_ROLES["$a"]="$e"
        WF_RANK_CACHE_ROOTS["$a"]="$f"
        WF_SERVER_IPS["$a"]="$g"
        WF_CLIENT_IPS["$a"]="$h"
        WF_CLIENT_NETDEVS["$a"]="$i"
        WF_CLIENT_HCAS["$a"]="$j"
        WF_CLIENT_NETWORKS["$a"]="$k"
        WF_SERVER_NETDEVS["$a"]="$l"
        WF_SERVER_HCAS["$a"]="$m"
        ;;
    esac
  done <<<"$rows"
  [ -n "${WF_CONFIG_ID:-}" ] \
    || die "fabric config did not provide metadata"
  [[ "${WF_STORAGE_NODES:-}" =~ ^[0-9]+$ ]] \
    || die "fabric config did not provide a storage-node count"
  [ "${#WF_RANK_NODE_IDS[@]}" -eq "$WF_STORAGE_NODES" ] \
    || die "fabric config rank count does not match its storage scope"
  WF_MANIFEST_PATH="$WF_OWNER_CACHE_ROOT/$WF_MANIFEST_RELATIVE"
  WF_MODEL_CACHE_DIR="$WF_OWNER_CACHE_ROOT/hub/$(hf_hub_dirname "$MODEL")"
  WF_DEFAULT_MODEL_CACHE_DIR="$HF_CACHE/hub/$(hf_hub_dirname "$MODEL")"
}

node_exec() {
  local rank="${1:?rank required}"
  shift
  if [ "$rank" -eq 0 ]; then
    bash -c "$1"
  else
    local host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$1"
  fi
}

node_python() {
  local rank="${1:?rank required}"
  shift
  if [ "$rank" -eq 0 ]; then
    python3 "$PY_TOOL" "$@"
  else
    local host command
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q python3 - "$@")
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command" <"$PY_TOOL"
  fi
}

node_install_content() {
  local rank="${1:?rank required}" destination="${2:?}" mode="${3:?}"
  local content="${4-}" command host
  if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
    local encoded root_script
    encoded=$(printf '%s' "$content" | base64 -w 0)
    root_script=$'set -euo pipefail\n'
    root_script+="$(shell_join_q printf %s "$encoded")"
    root_script+=" | base64 -d | "
    root_script+="$(shell_join_q install -D -m "$mode" /dev/stdin "$destination")"
    root_script+=$'\n'
    node_interactive_root_script "$rank" "$root_script"
  elif [ "$rank" -eq 0 ]; then
    printf '%s' "$content" \
      | sudo -n install -D -m "$mode" /dev/stdin "$destination"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q sudo -n install -D -m "$mode" /dev/stdin "$destination")
    printf '%s' "$content" \
      | "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command"
  fi
}

node_privileged() {
  local rank="${1:?rank required}"
  shift
  local command host
  if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
    local root_script
    root_script=$'set -euo pipefail\n'
    root_script+="$(shell_join_q "$@")"$'\n'
    node_interactive_root_script "$rank" "$root_script"
  elif [ "$rank" -eq 0 ]; then
    sudo -n "$@"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q sudo -n "$@")
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command"
  fi
}

node_interactive_root_script() {
  local rank="${1:?rank required}" root_script="${2:?root script required}"
  local payload command host
  [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ] \
    || die "internal error: interactive root script requested in passwordless mode"
  payload=$(printf '%s' "$root_script" | base64 -w 0)
  [[ "$payload" =~ ^[A-Za-z0-9+/=]+$ ]] \
    || die "could not encode the interactive root operation"
  command='sudo -v'
  command+=" && $(shell_join_q printf %s "$payload")"
  command+=' | base64 -d | sudo -n bash -s'
  log "sudo authentication required on ${WF_RANK_HOSTNAMES[$rank]}…"
  if [ "$rank" -eq 0 ]; then
    bash -c "$command"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -tt -- "$host" "$command"
  fi
}


confirm_system_change() {
  local yes="${1:-0}" message="${2:?}"
  [ "$yes" = 1 ] && return 0
  printf '%s\n' "$message"
  read -r -p "Continue? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "aborted" 3 ;;
  esac
}

configure_fabric() {
  local profile="${1:?profile required}"
  shift
  local owner="" cache_root="$HF_CACHE"
  local mount_root="$WEIGHT_FABRIC_MOUNT_ROOT"
  local port="$WEIGHT_FABRIC_PORT" rail_index=0 replace=0 json=0
  local storage_nodes=""
  local -a args=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --owner)
        [ "$#" -ge 2 ] || die "--owner requires a confirmed node selector" 2
        owner="$2"
        shift
        ;;
      --cache-root)
        [ "$#" -ge 2 ] || die "--cache-root requires a path" 2
        cache_root="$2"
        shift
        ;;
      --mount-root)
        [ "$#" -ge 2 ] || die "--mount-root requires a path" 2
        mount_root="$2"
        shift
        ;;
      --storage-nodes)
        [ "$#" -ge 2 ] || die "--storage-nodes requires a count" 2
        storage_nodes="$2"
        shift
        ;;
      --port)
        [ "$#" -ge 2 ] || die "--port requires a value" 2
        port="$2"
        shift
        ;;
      --rail-index)
        [ "$#" -ge 2 ] || die "--rail-index requires a value" 2
        rail_index="$2"
        shift
        ;;
      --replace) replace=1 ;;
      --json) json=1 ;;
      *) die "unknown configure option: $1" 2 ;;
    esac
    shift
  done
  [ -n "$owner" ] || die "configure requires --owner NODE_ID" 2
  load_conf "$profile"
  [ "$NODES" -gt 1 ] || die "$profile is not multi-node"
  [ "$(model_source_kind)" = hf ] \
    || die "single-copy mode supports Hugging Face profiles only"
  require_profile_topology "$NODES" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
    || die "confirm the exact topology before configuring weight storage"

  local destination
  destination=$(fabric_config_path "$profile")
  if [ -e "$destination" ] && [ "$replace" != 1 ]; then
    die "$destination already exists; inspect it or use --replace"
  fi
  mkdir -p "$(dirname "$destination")"
  args=(
    configure "$CLUSTER_TOPOLOGY_FILE"
    --profile "$profile"
    --model "$MODEL"
    --nodes "$NODES"
    --owner "$owner"
    --cache-root "$cache_root"
    --mount-root "$mount_root"
    --port "$port"
    --rail-index "$rail_index"
    --output "$destination"
  )
  [ -n "$storage_nodes" ] && args+=(--storage-nodes "$storage_nodes")
  [ "$json" = 1 ] && args+=(--json)
  "$PY_TOOL" "${args[@]}"
  if [ "$json" != 1 ]; then
    printf '\nSaved site-local config: %s\n' "$destination"
    printf 'Next: %s prerequisites %s\n' "$0" "$profile"
  fi
}

show_fabric() {
  local profile="${1:?profile required}" json="${2:-0}"
  load_fabric "$profile"
  if [ "$json" = 1 ]; then
    "$PY_TOOL" json "$WF_CONFIG_PATH" "$CLUSTER_TOPOLOGY_FILE" \
      --profile "$profile" --model "$MODEL" --nodes "$NODES"
  else
    "$PY_TOOL" render "$WF_CONFIG_PATH" "$CLUSTER_TOPOLOGY_FILE" \
      --profile "$profile" --model "$MODEL" --nodes "$NODES"
  fi
}

seal_fabric() {
  local profile="${1:?profile required}"
  load_fabric "$profile"
  log "hashing the authoritative snapshot on $WF_OWNER_HOSTNAME…"
  node_python "$WF_OWNER_RANK" manifest-create \
    --cache-root "$WF_OWNER_CACHE_ROOT" \
    --model "$MODEL" \
    --profile "$profile" \
    --output "$WF_MANIFEST_PATH"
}

hf_lookup_command() {
  local command
  command='command -v hf 2>/dev/null'
  command+=' || command -v huggingface-cli 2>/dev/null'
  command+=' || { candidate="$HOME/.hf-cli/venv/bin/hf";'
  command+=' test -x "$candidate" && printf "%s\n" "$candidate"; }'
  printf '%s\n' "$command"
}

node_probe() {
  local rank="${1:?rank required}" command="${2:?command required}"
  node_exec "$rank" "$command" >/dev/null 2>&1
}

node_module_available() {
  local rank="${1:?rank required}" module="${2:?module required}" command
  [[ "$module" =~ ^[a-z0-9_]+$ ]] || return 1
  command="test -d /sys/module/$module"
  command+=" || { command -v modinfo >/dev/null 2>&1"
  command+=" && modinfo $module >/dev/null 2>&1; }"
  node_probe "$rank" "$command"
}

sudo_access_usable() {
  local state="${1:?sudo state required}"
  [ "$state" = ok ] \
    || { [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ] \
      && [ "$state" = password-required ]; }
}

collect_prerequisites() {
  local state_file="${1:?state file required}" rank role
  local reachable sudo_ready python_ready apt_ready nfs_client
  local xprtrdma nfs_server nfsd svcrdma hf_cli ready
  local hf_lookup
  hf_lookup=$(hf_lookup_command)
  : >"$state_file"
  WF_PREREQUISITES_READY=1

  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    role="${WF_RANK_ROLES[$rank]}"
    reachable=missing
    sudo_ready=missing
    python_ready=missing
    apt_ready=missing
    nfs_client=missing
    xprtrdma=na
    nfs_server=na
    nfsd=na
    svcrdma=na
    hf_cli=na
    ready=ready

    if node_probe "$rank" "true"; then
      reachable=ok
      if node_probe "$rank" "sudo -n true"; then
        sudo_ready=ok
      elif node_probe "$rank" "command -v sudo >/dev/null 2>&1"; then
        sudo_ready=password-required
      fi
      node_probe "$rank" "command -v python3 >/dev/null 2>&1" \
        && python_ready=ok
      node_probe "$rank" "command -v apt-get >/dev/null 2>&1" \
        && apt_ready=ok
      node_probe "$rank" "command -v mount.nfs >/dev/null 2>&1" \
        && nfs_client=ok

      if [ "$role" = client ]; then
        node_module_available "$rank" xprtrdma && xprtrdma=ok \
          || xprtrdma=missing
      else
        node_probe "$rank" "command -v exportfs >/dev/null 2>&1" \
          && nfs_server=ok || nfs_server=missing
        node_module_available "$rank" nfsd && nfsd=ok || nfsd=missing
        node_module_available "$rank" svcrdma && svcrdma=ok \
          || svcrdma=missing
        node_probe "$rank" "$hf_lookup" && hf_cli=ok || hf_cli=missing
      fi
    fi

    [ "$reachable" = ok ] || ready=blocked
    if [ "$sudo_ready" != ok ]; then
      if [ "$WEIGHT_FABRIC_SUDO_MODE" != interactive ] \
          || [ "$sudo_ready" != password-required ]; then
        ready=blocked
      fi
    fi
    [ "$python_ready" = ok ] || ready=blocked
    [ "$nfs_client" = ok ] || ready=blocked
    if [ "$role" = client ]; then
      [ "$xprtrdma" = ok ] || ready=blocked
    else
      [ "$nfs_server" = ok ] || ready=blocked
      [ "$nfsd" = ok ] || ready=blocked
      [ "$svcrdma" = ok ] || ready=blocked
      [ "$hf_cli" = ok ] || ready=blocked
    fi
    [ "$ready" = ready ] || WF_PREREQUISITES_READY=0

    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$rank" "${WF_RANK_HOSTNAMES[$rank]}" "$role" "$ready" \
      "$reachable" "$sudo_ready" "$python_ready" "$apt_ready" \
      "$nfs_client" "$xprtrdma" "$nfs_server" "$nfsd" "$svcrdma" \
      "$hf_cli" >>"$state_file"
  done
}

render_prerequisites_json() {
  local state_file="${1:?state file required}" state
  state=blocked
  [ "$WF_PREREQUISITES_READY" = 1 ] && state=ready
  WF_PREREQ_STATE_FILE="$state_file" WF_PREREQ_STATE="$state" \
  WF_PREREQ_PROFILE="$WF_PROFILE" WF_PREREQ_CONFIG_ID="$WF_CONFIG_ID" \
  WF_PREREQ_SUDO_MODE="$WEIGHT_FABRIC_SUDO_MODE" \
  python3 - <<'PY'
import json
import os

keys = (
    "rank",
    "hostname",
    "role",
    "state",
    "reachable",
    "passwordless_sudo",
    "python3",
    "apt",
    "nfs_client",
    "xprtrdma",
    "nfs_server",
    "nfsd",
    "svcrdma",
    "hf_cli",
)
nodes = []
with open(os.environ["WF_PREREQ_STATE_FILE"], encoding="utf-8") as handle:
    for line in handle:
        values = line.rstrip("\n").split("\t")
        item = dict(zip(keys, values, strict=True))
        item["rank"] = int(item["rank"])
        nodes.append(item)
print(json.dumps({
    "schema_version": 1,
    "kind": "weight-fabric-prerequisites",
    "profile": os.environ["WF_PREREQ_PROFILE"],
    "configuration_id": os.environ["WF_PREREQ_CONFIG_ID"],
    "sudo_mode": os.environ["WF_PREREQ_SUDO_MODE"],
    "state": os.environ["WF_PREREQ_STATE"],
    "ready": os.environ["WF_PREREQ_STATE"] == "ready",
    "nodes": nodes,
}, indent=2))
PY
}

render_prerequisites_human() {
  local state_file="${1:?state file required}" rank hostname role state
  local reachable sudo_ready python_ready apt_ready nfs_client
  local xprtrdma nfs_server nfsd svcrdma hf_cli
  local package_list package_command hf_command privilege_message setup_command
  local -a packages=()
  while IFS=$'\t' read -r rank hostname role state reachable sudo_ready \
      python_ready apt_ready nfs_client xprtrdma nfs_server nfsd svcrdma \
      hf_cli; do
    packages=()
    [ "$python_ready" = ok ] || packages+=(python3)
    [ "$nfs_client" = ok ] || packages+=(nfs-common)
    if [ "$role" = owner ]; then
      [ "$nfs_server" = ok ] || packages+=(nfs-kernel-server)
      [ "$hf_cli" = ok ] || packages+=(python3-venv)
    fi
    package_command=none
    if [ "${#packages[@]}" -gt 0 ]; then
      package_list="${packages[*]}"
      package_command="sudo apt-get update, then sudo apt-get install -y --no-install-recommends $package_list"
    fi
    hf_command=none
    if [ "$role" = owner ] && [ "$hf_cli" != ok ]; then
      hf_command="python3 -m venv \$HOME/.hf-cli/venv, then \$HOME/.hf-cli/venv/bin/python -m pip install huggingface_hub==$WEIGHT_FABRIC_HF_CLI_VERSION"
    fi
    if [ "$role" = owner ]; then
      render_human_section "NODE $((rank + 1)) · CACHE OWNER" \
        "System" "$hostname" \
        "Status" "$state" \
        "Access" "SSH $reachable · passwordless sudo $sudo_ready" \
        "Runtime" "Python $python_ready · apt $apt_ready" \
        "NFS cli" "$nfs_client" \
        "NFS srv" "tools $nfs_server · nfsd $nfsd · svcrdma $svcrdma" \
        "HF CLI" "$hf_cli" \
        "Packages" "$package_command" \
        "HF setup" "$hf_command"
    else
      render_human_section "NODE $((rank + 1)) · STORAGE CLIENT" \
        "System" "$hostname" \
        "Status" "$state" \
        "Access" "SSH $reachable · passwordless sudo $sudo_ready" \
        "Runtime" "Python $python_ready · apt $apt_ready" \
        "NFS/RDMA" "client $nfs_client · xprtrdma $xprtrdma" \
        "Packages" "$package_command"
    fi
  done <"$state_file"
  if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
    privilege_message="Interactive sudo is selected. Privileged commands request a password in a terminal and Pulsar never stores it or changes sudoers."
  else
    privilege_message="Passwordless mode requires sudo -n through every configured SSH endpoint. Pulsar never changes sudoers; select --interactive-sudo or arrange that access explicitly."
  fi
  setup_command="$0 setup-prerequisites $WF_PROFILE"
  [ "$WEIGHT_FABRIC_SUDO_MODE" != interactive ] \
    || setup_command+=" --interactive-sudo"
  if [ "$WF_PREREQUISITES_READY" = 1 ]; then
    render_human_section "PREREQUISITES" \
      "State" "ready" \
      "Privilege" "$WEIGHT_FABRIC_SUDO_MODE" \
      "Next" "$0 download $WF_PROFILE"
  else
    render_human_section "PREREQUISITES" \
      "State" "blocked" \
      "Setup" "$setup_command" \
      "Manual" "Run the listed package/CLI commands on each affected node, then rerun this check." \
      "Privilege" "$privilege_message" \
      "Kernel" "Kernel/RDMA capability is detected but never replaced automatically."
  fi
}

prerequisites_fabric() {
  local profile="${1:?profile required}" json="${2:-0}" state_file
  load_fabric "$profile"
  state_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-prereqs.XXXXXX")
  trap 'rm -f "${state_file:-}"' RETURN
  collect_prerequisites "$state_file"
  if [ "$json" = 1 ]; then
    render_prerequisites_json "$state_file"
  else
    render_prerequisites_human "$state_file"
  fi
  [ "$WF_PREREQUISITES_READY" = 1 ]
}

require_prerequisites_ready() {
  local state_file
  state_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-prereqs.XXXXXX")
  collect_prerequisites "$state_file"
  if [ "$WF_PREREQUISITES_READY" != 1 ]; then
    render_prerequisites_human "$state_file"
    rm -f "$state_file"
    die "weight-fabric prerequisites are blocked; complete the setup guidance before continuing"
  fi
  rm -f "$state_file"
}

setup_prerequisites() {
  local profile="${1:?profile required}" yes="${2:-0}" state_file rank role
  local reachable sudo_ready python_ready apt_ready nfs_client
  local xprtrdma nfs_server nfsd svcrdma hf_cli packages command pip_args
  local root_script
  local needs_hf=0 automation_blocked=0
  local -a package_args=()
  load_fabric "$profile"
  [[ "$WEIGHT_FABRIC_HF_CLI_VERSION" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]] \
    || die "invalid WEIGHT_FABRIC_HF_CLI_VERSION"
  state_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-prereqs.XXXXXX")
  trap 'rm -f "${state_file:-}"' RETURN
  collect_prerequisites "$state_file"
  if [ "$WF_PREREQUISITES_READY" = 1 ]; then
    render_prerequisites_human "$state_file"
    return 0
  fi
  while IFS=$'\t' read -r rank _hostname _role _state reachable sudo_ready \
      _python_ready _apt_ready _nfs_client _xprtrdma _nfs_server _nfsd \
      _svcrdma _hf_cli; do
    [ "$reachable" = ok ] || automation_blocked=1
    sudo_access_usable "$sudo_ready" || automation_blocked=1
  done <"$state_file"
  if [ "$automation_blocked" = 1 ]; then
    render_prerequisites_human "$state_file"
    die "automatic setup requires reachable nodes and usable sudo; use the manual guidance above"
  fi
  confirm_system_change "$yes" \
    "Install only missing NFS/Python packages on configured nodes and Hugging Face CLI $WEIGHT_FABRIC_HF_CLI_VERSION for the cache owner?"

  while IFS=$'\t' read -r rank _hostname role _state reachable sudo_ready \
      python_ready apt_ready nfs_client xprtrdma nfs_server nfsd svcrdma \
      hf_cli; do
    [ "$reachable" = ok ] \
      || die "rank $rank is unreachable; automatic setup cannot continue"
    sudo_access_usable "$sudo_ready" \
      || die "rank $rank needs usable sudo or manual package setup"
    package_args=()
    [ "$python_ready" = ok ] || package_args+=(python3)
    [ "$nfs_client" = ok ] || package_args+=(nfs-common)
    if [ "$role" = owner ]; then
      [ "$nfs_server" = ok ] || package_args+=(nfs-kernel-server)
      if [ "$hf_cli" != ok ]; then
        package_args+=(python3-venv)
        needs_hf=1
      fi
    fi
    if [ "${#package_args[@]}" -gt 0 ]; then
      [ "$apt_ready" = ok ] \
        || die "rank $rank has no apt-get; install ${package_args[*]} manually"
      log "installing missing packages on ${WF_RANK_HOSTNAMES[$rank]}…"
      if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
        root_script=$'set -euo pipefail\n'
        root_script+="$(shell_join_q apt-get update)"$'\n'
        root_script+="$(shell_join_q env DEBIAN_FRONTEND=noninteractive \
          apt-get install -y --no-install-recommends "${package_args[@]}")"$'\n'
        node_interactive_root_script "$rank" "$root_script"
      else
        node_privileged "$rank" apt-get update
        node_privileged "$rank" env DEBIAN_FRONTEND=noninteractive \
          apt-get install -y --no-install-recommends "${package_args[@]}"
      fi
    fi
    if [ "$role" = client ] && [ "$xprtrdma" != ok ]; then
      log "rank $rank still requires kernel xprtrdma support; setup will verify it after packages"
    fi
    if [ "$role" = owner ] \
        && { [ "$nfsd" != ok ] || [ "$svcrdma" != ok ]; }; then
      log "the owner still requires kernel nfsd/svcrdma support; setup will verify it after packages"
    fi
  done <"$state_file"

  if [ "$needs_hf" = 1 ]; then
    packages="huggingface_hub==$WEIGHT_FABRIC_HF_CLI_VERSION"
    pip_args=$(shell_join_q -m pip install --disable-pip-version-check \
      --no-input --upgrade "$packages")
    command='venv="$HOME/.hf-cli/venv"'
    command+=' && python3 -m venv "$venv"'
    command+=' && "$venv/bin/python" '
    command+="$pip_args"
    log "installing owner-user Hugging Face CLI $WEIGHT_FABRIC_HF_CLI_VERSION…"
    node_exec "$WF_OWNER_RANK" "$command" \
      || die "could not install the owner Hugging Face CLI"
  fi

  rm -f "$state_file"
  state_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-prereqs.XXXXXX")
  collect_prerequisites "$state_file"
  render_prerequisites_human "$state_file"
  [ "$WF_PREREQUISITES_READY" = 1 ] \
    || die "automatic setup finished, but prerequisites are still blocked; follow the manual guidance above"
}

download_fabric() {
  local profile="${1:?profile required}" yes="${2:-0}" command hf_bin
  local hf_lookup
  load_fabric "$profile"
  confirm_system_change "$yes" \
    "Download $MODEL once on $WF_OWNER_HOSTNAME, with no rank copies?"
  hf_lookup=$(hf_lookup_command)
  hf_bin=$(node_exec "$WF_OWNER_RANK" "$hf_lookup") \
    || die "Hugging Face CLI is missing on $WF_OWNER_HOSTNAME; run: $0 setup-prerequisites $profile"
  command=$(shell_join_q env HF_HUB_OFFLINE=0 "$hf_bin" download "$MODEL" \
    --cache-dir "$WF_OWNER_CACHE_ROOT/hub")
  if [ "${PULSAR_VERBOSE:-0}" != 1 ] && [ "$(basename "$hf_bin")" = hf ]; then
    command+=" --quiet"
  fi
  log "downloading only on $WF_OWNER_HOSTNAME…"
  node_exec "$WF_OWNER_RANK" "$command" \
    || die "download failed on $WF_OWNER_HOSTNAME"
  seal_fabric "$profile"
}

route_matches() {
  local rank="${1:?}" server_ip="${2:?}" client_ip="${3:?}" netdev="${4:?}"
  local command output
  command=$(shell_join_q ip -4 route get "$server_ip")
  output=$(node_exec "$rank" "$command" 2>/dev/null) || return 1
  [[ " $output " == *" dev $netdev "* ]] || return 1
  [[ " $output " == *" src $client_ip "* ]]
}

mount_matches() {
  local rank="${1:?}" mount_path="${2:?}" server_ip="${3:?}" export_path="${4:?}"
  local command output source fstype options
  command=$(shell_join_q findmnt -rn -M "$mount_path" -o SOURCE,FSTYPE,OPTIONS)
  output=$(node_exec "$rank" "$command" 2>/dev/null) || return 1
  IFS=' ' read -r source fstype options <<<"$output"
  [ "$source" = "$server_ip:$export_path" ] || return 1
  [ "$fstype" = nfs4 ] || [ "$fstype" = nfs ] || return 1
  case ",$options," in
    *,ro,*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *,proto=rdma,*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *,vers=4.2,*|*,vers=4,minorversion=2,*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *",port=$WF_PORT,"*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *,hard,*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *,timeo=600,*) ;;
    *) return 1 ;;
  esac
  case ",$options," in
    *,retrans=2,*) ;;
    *) return 1 ;;
  esac
}

owner_rdma_ready() {
  local command
  command="test -r /proc/fs/nfsd/portlist"
  command+=" && grep -Eq "
  command+="$(printf '%q' "(rdma.*${WF_PORT}|${WF_PORT}.*rdma)")"
  command+=" /proc/fs/nfsd/portlist"
  node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1
}

fabric_export_file() {
  printf '/etc/exports.d/pulsar-weight-fabric-%s.exports\n' \
    "${WF_CONFIG_ID:0:12}"
}

fabric_nfs_file() {
  printf '/etc/nfs.conf.d/pulsar-weight-fabric-%s.conf\n' \
    "${WF_CONFIG_ID:0:12}"
}

owner_config_files_consistent() {
  local export_file nfs_file command export_exists=0 nfs_exists=0
  export_file=$(fabric_export_file)
  nfs_file=$(fabric_nfs_file)
  command=$(shell_join_q test -e "$export_file")
  node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1 \
    && export_exists=1
  command=$(shell_join_q test -e "$nfs_file")
  node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1 \
    && nfs_exists=1
  [ "$export_exists" = "$nfs_exists" ]
}

owner_config_files_absent() {
  local export_file nfs_file command
  export_file=$(fabric_export_file)
  nfs_file=$(fabric_nfs_file)
  command=$(shell_join_q test ! -e "$export_file")
  command+=" && $(shell_join_q test ! -e "$nfs_file")"
  node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1
}

owner_repository_access() {
  local output identity
  if ! output=$(node_python "$WF_OWNER_RANK" repository-access \
      --repository "$WF_EXPORT_PATH"); then
    return 1
  fi
  if ! identity=$(printf '%s' "$output" | python3 -c \
      'import json,sys
value=json.load(sys.stdin)
print(value["uid"], value["gid"])'); then
    return 1
  fi
  read -r WF_REPOSITORY_UID WF_REPOSITORY_GID <<<"$identity"
  [[ "$WF_REPOSITORY_UID" =~ ^[1-9][0-9]*$ ]] \
    && [[ "$WF_REPOSITORY_GID" =~ ^[0-9]+$ ]]
}

validate_owner_export_scope() {
  local state="${1:-allowed}" active_file export_files_file command rc=0 rank
  local export_file
  local -a args=()
  export_file=$(fabric_export_file)
  active_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-active-exports.XXXXXX")
  export_files_file=$(mktemp \
    "${TMPDIR:-/tmp}/pulsar-export-files.XXXXXX")
  command="if $(shell_join_q test -r /proc/fs/nfsd/exports); then "
  command+="$(shell_join_q cat /proc/fs/nfsd/exports)"
  command+="; else $(shell_join_q cat /var/lib/nfs/etab); fi"
  if ! node_exec "$WF_OWNER_RANK" "$command" >"$active_file"; then
    rm -f "$active_file" "$export_files_file"
    return 1
  fi
  command=$(shell_join_q find /etc/exports.d -maxdepth 1 \
    -name 'pulsar-weight-fabric-*.exports' -print)
  if ! node_exec "$WF_OWNER_RANK" "$command" >"$export_files_file"; then
    rm -f "$active_file" "$export_files_file"
    return 1
  fi
  args=(
    export-scope
    --active-exports "$active_file"
    --pulsar-export-files "$export_files_file"
    --expected-export-file "$export_file"
    --export-path "$WF_EXPORT_PATH"
    --anonuid "$WF_REPOSITORY_UID"
    --anongid "$WF_REPOSITORY_GID"
  )
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    args+=(--client "${WF_CLIENT_IPS[$rank]}")
  done
  case "$state" in
    allowed) ;;
    required) args+=(--require-active --require-export-file) ;;
    absent) args+=(--forbid-active --forbid-export-file) ;;
    *)
      rm -f "$active_file" "$export_files_file"
      die "internal error: unknown export scope state $state"
      ;;
  esac
  if "$PY_TOOL" "${args[@]}" >/dev/null; then
    rc=0
  else
    rc=$?
  fi
  rm -f "$active_file" "$export_files_file"
  return "$rc"
}

model_replica_absent() {
  local rank="${1:?}" command
  [ "$rank" -eq "$WF_OWNER_RANK" ] && return 0
  command=$(shell_join_q test ! -e "$WF_MODEL_CACHE_DIR")
  command+=" && $(shell_join_q test ! -L "$WF_MODEL_CACHE_DIR")"
  if [ "$WF_DEFAULT_MODEL_CACHE_DIR" != "$WF_MODEL_CACHE_DIR" ]; then
    command+=" && $(shell_join_q test ! -e "$WF_DEFAULT_MODEL_CACHE_DIR")"
    command+=" && $(shell_join_q test ! -L "$WF_DEFAULT_MODEL_CACHE_DIR")"
  fi
  node_exec "$rank" "$command" >/dev/null 2>&1
}

verify_rank_manifest() {
  local rank="${1:?}" mode="${2:-metadata}" root manifest
  local -a args
  root="${WF_RANK_CACHE_ROOTS[$rank]}"
  manifest="$root/$WF_MANIFEST_RELATIVE"
  args=(
    manifest-verify
    --cache-root "$root"
    --manifest "$manifest"
    --profile "$WF_PROFILE"
    --model "$WF_MODEL"
    --json
  )
  [ "$mode" = metadata ] && args+=(--metadata-only)
  node_python "$rank" "${args[@]}"
}

render_check_json() {
  local state_file="${1:?}" overall="${2:?}" mode="${3:?}"
  WF_STATE_FILE="$state_file" WF_OVERALL="$overall" WF_MODE="$mode" \
  WF_PROFILE_V="$WF_PROFILE" WF_CONFIG_ID_V="$WF_CONFIG_ID" \
  WF_OWNER_ID_V="$WF_OWNER_NODE_ID" python3 - <<'PY'
import json
import os

nodes = []
with open(os.environ["WF_STATE_FILE"], encoding="utf-8") as handle:
    for line in handle:
        fields = line.rstrip("\n").split("\t")
        nodes.append({
            "rank": int(fields[0]),
            "hostname": fields[1],
            "role": fields[2],
            "route": fields[3] == "ok",
            "mount": fields[4] == "ok",
            "integrity": fields[5] == "ok",
            "replica_absent": fields[6] == "ok",
            "detail": fields[7] or None,
        })
print(json.dumps({
    "schema_version": 1,
    "profile": os.environ["WF_PROFILE_V"],
    "source": "fabric",
    "transport": "nfs-rdma",
    "state": os.environ["WF_OVERALL"],
    "ok": os.environ["WF_OVERALL"] == "ok",
    "mode": os.environ["WF_MODE"],
    "configuration_id": os.environ["WF_CONFIG_ID_V"],
    "owner_node_id": os.environ["WF_OWNER_ID_V"],
    "nodes": nodes,
}, indent=2))
PY
}

check_fabric() {
  local profile="${1:?}" mode="${2:-metadata}" json="${3:-0}"
  local serving_only="${4:-0}" rank_limit
  local state_file overall=ok rank route_state mount_state integrity_state
  local replica_state detail verify_output status
  load_fabric "$profile"
  rank_limit="$WF_STORAGE_NODES"
  [ "$serving_only" = 1 ] && rank_limit="$NODES"
  state_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-fabric.XXXXXX")
  trap 'rm -f "${state_file:-}"' RETURN

  if ! owner_config_files_consistent; then
    overall="owner-unready"
  elif ! owner_repository_access; then
    overall="owner-unready"
  elif ! validate_owner_export_scope required; then
    overall="owner-unready"
  fi
  if ! owner_rdma_ready; then
    overall="owner-unready"
  fi
  for ((rank = 0; rank < rank_limit; rank++)); do
    route_state=ok
    mount_state=ok
    integrity_state=ok
    replica_state=ok
    detail=""
    if [ "${WF_RANK_ROLES[$rank]}" = client ]; then
      if ! route_matches "$rank" "${WF_SERVER_IPS[$rank]}" \
          "${WF_CLIENT_IPS[$rank]}" "${WF_CLIENT_NETDEVS[$rank]}"; then
        route_state=fail
        detail="route is not pinned to the configured RoCE rail"
        [ "$overall" = ok ] && overall=route-mismatch
      fi
      if ! mount_matches "$rank" "$WF_MOUNT_PATH" \
          "${WF_SERVER_IPS[$rank]}" "$WF_EXPORT_PATH"; then
        mount_state=fail
        [ -n "$detail" ] || detail="NFS/RDMA mount is absent or differs"
        [ "$overall" = ok ] && overall=unmounted
      fi
      if ! model_replica_absent "$rank"; then
        replica_state=fail
        [ -n "$detail" ] || detail="durable local model cache still exists"
        [ "$overall" = ok ] && overall=replica-present
      fi
    fi
    if [ "$mount_state" = ok ]; then
      if ! verify_output=$(verify_rank_manifest "$rank" "$mode" 2>/dev/null); then
        integrity_state=fail
        [ -n "$detail" ] || detail="model manifest verification failed"
        [ "$overall" = ok ] && overall=integrity-failed
      fi
    else
      integrity_state=fail
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$rank" "${WF_RANK_HOSTNAMES[$rank]}" "${WF_RANK_ROLES[$rank]}" \
      "$route_state" "$mount_state" "$integrity_state" "$replica_state" \
      "$detail" >>"$state_file"
  done

  if [ "$json" = 1 ]; then
    render_check_json "$state_file" "$overall" "$mode"
  else
    render_human_section "SINGLE-COPY WEIGHT CHECK" \
      "Profile" "$profile" \
      "State" "$overall" \
      "Transport" "NFSv4.2/RDMA · config ${WF_CONFIG_ID:0:12}" \
      "Integrity" "$mode"
    while IFS=$'\t' read -r rank host role route_state mount_state \
        integrity_state replica_state detail; do
      status=ready
      [ "$route_state$mount_state$integrity_state$replica_state" = okokokok ] \
        || status="blocked · $detail"
      render_human_section "NODE $((rank + 1))" \
        "System" "$host · $role" \
        "Status" "$status"
    done <"$state_file"
  fi
  [ "$overall" = ok ]
}

WF_APPLY_ROLLBACK_ARMED=0
WF_APPLY_CREATED_EXPORT_FILE=0
WF_APPLY_CREATED_NFS_FILE=0
WF_APPLY_EXPORT_FILE=""
WF_APPLY_NFS_FILE=""
declare -ag WF_APPLY_MOUNTED_RANKS=()

rollback_fabric_apply() {
  local original_status=$? index rank command failed=0 refresh=0
  trap - EXIT
  [ "$WF_APPLY_ROLLBACK_ARMED" = 1 ] || return "$original_status"
  [ "$original_status" -ne 0 ] || original_status=1
  set +e
  warn "apply failed; rolling back this configuration's exact mounts and files"
  for ((index = ${#WF_APPLY_MOUNTED_RANKS[@]} - 1; index >= 0; index--)); do
    rank="${WF_APPLY_MOUNTED_RANKS[$index]}"
    if mount_matches "$rank" "$WF_MOUNT_PATH" \
        "${WF_SERVER_IPS[$rank]}" "$WF_EXPORT_PATH"; then
      if ! node_privileged "$rank" umount "$WF_MOUNT_PATH"; then
        failed=1
      fi
    else
      command=$(shell_join_q findmnt -rn -M "$WF_MOUNT_PATH")
      if node_exec "$rank" "$command" >/dev/null 2>&1; then
        warn "rollback left an unexpected mount on rank $rank"
        failed=1
      fi
    fi
  done
  if [ "$WF_APPLY_CREATED_EXPORT_FILE" = 1 ]; then
    node_privileged "$WF_OWNER_RANK" rm -f "$WF_APPLY_EXPORT_FILE" \
      || failed=1
    refresh=1
  fi
  if [ "$WF_APPLY_CREATED_NFS_FILE" = 1 ]; then
    node_privileged "$WF_OWNER_RANK" rm -f "$WF_APPLY_NFS_FILE" \
      || failed=1
    refresh=1
  fi
  if [ "$refresh" = 1 ]; then
    node_privileged "$WF_OWNER_RANK" exportfs -ra || failed=1
    node_privileged "$WF_OWNER_RANK" systemctl restart nfs-server \
      || failed=1
  fi
  if [ "$failed" = 1 ]; then
    warn "rollback is incomplete; run weight-fabric teardown before launch"
  else
    warn "rolled back the partial weight-fabric apply"
  fi
  return "$original_status"
}

apply_fabric() {
  local profile="${1:?}" yes="${2:-0}" rank
  local export_line nfs_conf export_file nfs_file command mount_options
  local export_encoded nfs_encoded owner_script client_script guidance
  load_fabric "$profile"
  require_prerequisites_ready
  command=$(shell_join_q test -s "$WF_MANIFEST_PATH")
  node_exec "$WF_OWNER_RANK" "$command" \
    || die "seal the authoritative snapshot before apply: $0 seal $profile"
  if ! node_exec "$WF_OWNER_RANK" "command -v exportfs >/dev/null 2>&1"; then
    guidance="nfs-kernel-server is not installed on $WF_OWNER_HOSTNAME; "
    guidance+="run: $0 setup-prerequisites $profile"
    die "$guidance"
  fi
  owner_repository_access \
    || die "owner repository identity/access check failed"
  owner_config_files_consistent \
    || die "owner has incomplete files from an earlier apply; run teardown"
  validate_owner_export_scope allowed \
    || die "owner has a broader or conflicting export"
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    route_matches "$rank" "${WF_SERVER_IPS[$rank]}" \
      "${WF_CLIENT_IPS[$rank]}" "${WF_CLIENT_NETDEVS[$rank]}" \
      || die "rank $rank route is not the configured RoCE rail"
    model_replica_absent "$rank" \
      || die "rank $rank still has a durable model replica; purge it before apply"
    command=$(shell_join_q findmnt -rn -M "$WF_MOUNT_PATH")
    if node_exec "$rank" "$command" >/dev/null 2>&1 \
        && ! mount_matches "$rank" "$WF_MOUNT_PATH" \
          "${WF_SERVER_IPS[$rank]}" "$WF_EXPORT_PATH"; then
      die "rank $rank mount target is occupied by a different filesystem"
    fi
  done
  confirm_system_change "$yes" \
    "Install one read-only export on $WF_OWNER_HOSTNAME and mount exact RoCE clients?"
  node_privileged "$WF_OWNER_RANK" true \
    || die "owner privilege preflight failed"
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    node_privileged "$rank" true \
      || die "rank $rank privilege preflight failed"
  done

  export_line="\"$WF_EXPORT_PATH\""
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    export_line+=" ${WF_CLIENT_IPS[$rank]}(ro,sync,insecure,root_squash,"
    export_line+="anonuid=$WF_REPOSITORY_UID,anongid=$WF_REPOSITORY_GID,no_subtree_check)"
  done
  export_line+=$'\n'
  nfs_conf=$'[nfsd]\n'
  nfs_conf+="rdma = $WF_PORT"$'\n'
  export_file=$(fabric_export_file)
  nfs_file=$(fabric_nfs_file)

  WF_APPLY_EXPORT_FILE="$export_file"
  WF_APPLY_NFS_FILE="$nfs_file"
  WF_APPLY_MOUNTED_RANKS=()
  command=$(shell_join_q test -e "$export_file")
  if node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1; then
    WF_APPLY_CREATED_EXPORT_FILE=0
  else
    WF_APPLY_CREATED_EXPORT_FILE=1
  fi
  command=$(shell_join_q test -e "$nfs_file")
  if node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1; then
    WF_APPLY_CREATED_NFS_FILE=0
  else
    WF_APPLY_CREATED_NFS_FILE=1
  fi
  WF_APPLY_ROLLBACK_ARMED=1
  trap rollback_fabric_apply EXIT

  if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
    export_encoded=$(printf '%s' "$export_line" | base64 -w 0)
    nfs_encoded=$(printf '%s' "$nfs_conf" | base64 -w 0)
    owner_script=$'set -euo pipefail\n'
    owner_script+="$(shell_join_q printf %s "$export_encoded")"
    owner_script+=" | base64 -d | "
    owner_script+="$(shell_join_q install -D -m 0644 /dev/stdin "$export_file")"
    owner_script+=$'\n'
    owner_script+="$(shell_join_q printf %s "$nfs_encoded")"
    owner_script+=" | base64 -d | "
    owner_script+="$(shell_join_q install -D -m 0644 /dev/stdin "$nfs_file")"
    owner_script+=$'\n'
    owner_script+="$(shell_join_q modprobe svcrdma)"$'\n'
    owner_script+="$(shell_join_q exportfs -ra)"$'\n'
    owner_script+="$(shell_join_q systemctl enable --now nfs-server)"$'\n'
    owner_script+="$(shell_join_q systemctl restart nfs-server)"$'\n'
    node_interactive_root_script "$WF_OWNER_RANK" "$owner_script"
  else
    node_install_content "$WF_OWNER_RANK" "$export_file" 0644 "$export_line"
    node_install_content "$WF_OWNER_RANK" "$nfs_file" 0644 "$nfs_conf"
    node_privileged "$WF_OWNER_RANK" modprobe svcrdma
    node_privileged "$WF_OWNER_RANK" exportfs -ra
    node_privileged "$WF_OWNER_RANK" systemctl enable --now nfs-server
    node_privileged "$WF_OWNER_RANK" systemctl restart nfs-server
  fi
  owner_rdma_ready \
    || die "NFS server did not expose RDMA port $WF_PORT"
  validate_owner_export_scope required \
    || die "NFS active export does not match the exact repository policy"

  mount_options="ro,vers=4.2,proto=rdma,port=$WF_PORT,hard,timeo=600,retrans=2"
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    route_matches "$rank" "${WF_SERVER_IPS[$rank]}" \
      "${WF_CLIENT_IPS[$rank]}" "${WF_CLIENT_NETDEVS[$rank]}" \
      || die "rank $rank route is not the configured RoCE rail"
    if mount_matches "$rank" "$WF_MOUNT_PATH" \
        "${WF_SERVER_IPS[$rank]}" "$WF_EXPORT_PATH"; then
      continue
    fi
    if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
      client_script=$'set -euo pipefail\n'
      client_script+="$(shell_join_q mkdir -p "$WF_MOUNT_PATH")"$'\n'
      client_script+="$(shell_join_q mount -t nfs4 -o "$mount_options" \
        "${WF_SERVER_IPS[$rank]}:$WF_EXPORT_PATH" "$WF_MOUNT_PATH")"$'\n'
      node_interactive_root_script "$rank" "$client_script"
    else
      command=$(shell_join_q mkdir -p "$WF_MOUNT_PATH")
      node_privileged "$rank" bash -c "$command"
      node_privileged "$rank" mount -t nfs4 -o "$mount_options" \
        "${WF_SERVER_IPS[$rank]}:$WF_EXPORT_PATH" "$WF_MOUNT_PATH"
    fi
    WF_APPLY_MOUNTED_RANKS+=("$rank")
  done
  check_fabric "$profile" metadata 0
  WF_APPLY_ROLLBACK_ARMED=0
  trap - EXIT
}

unmount_clients() {
  local profile="${1:?}" yes="${2:-0}" rank command in_use
  load_fabric "$profile" 1
  confirm_system_change "$yes" \
    "Unmount the single-copy weight view from every configured client?"
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    command=$(shell_join_q docker ps -q --filter "volume=$WF_MOUNT_PATH")
    in_use=$(node_exec "$rank" "$command" 2>/dev/null) \
      || die "cannot inspect Docker on rank $rank"
    [ -z "$in_use" ] \
      || die "rank $rank still has a container using $WF_MOUNT_PATH"
  done
  for ((rank = WF_STORAGE_NODES - 1; rank >= 0; rank--)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    command=$(shell_join_q findmnt -rn -M "$WF_MOUNT_PATH")
    if node_exec "$rank" "$command" >/dev/null 2>&1; then
      node_privileged "$rank" umount "$WF_MOUNT_PATH"
    fi
  done
}

teardown_fabric() {
  local profile="${1:?}" yes="${2:-0}" export_file nfs_file root_script
  load_fabric "$profile" 1
  confirm_system_change "$yes" \
    "Unmount clients and remove this config's owner export?"
  unmount_clients "$profile" 1
  export_file=$(fabric_export_file)
  nfs_file=$(fabric_nfs_file)
  if [ "$WEIGHT_FABRIC_SUDO_MODE" = interactive ]; then
    root_script=$'set -euo pipefail\n'
    root_script+="$(shell_join_q rm -f "$export_file" "$nfs_file")"$'\n'
    root_script+="$(shell_join_q exportfs -ra)"$'\n'
    root_script+="$(shell_join_q systemctl restart nfs-server)"$'\n'
    node_interactive_root_script "$WF_OWNER_RANK" "$root_script"
  else
    node_privileged "$WF_OWNER_RANK" rm -f "$export_file" "$nfs_file"
    node_privileged "$WF_OWNER_RANK" exportfs -ra
    node_privileged "$WF_OWNER_RANK" systemctl restart nfs-server
  fi
  WF_REPOSITORY_UID="${WF_REPOSITORY_UID:-1}"
  WF_REPOSITORY_GID="${WF_REPOSITORY_GID:-1}"
  owner_config_files_absent \
    || die "teardown is incomplete: configuration files remain; retry teardown"
  validate_owner_export_scope absent \
    || die "teardown is incomplete: export state remains; retry teardown"
  log "removed export and mounts; model files and site config were preserved"
}

purge_client_replicas() {
  local profile="${1:?}" yes="${2:-0}" rank target command
  local -a targets=()
  load_fabric "$profile"
  confirm_system_change "$yes" \
    "Permanently delete this model's complete local cache from every configured client?"
  require_cold_cache_idle "$WF_STORAGE_NODES"
  for ((rank = 0; rank < WF_STORAGE_NODES; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    targets=("$WF_MODEL_CACHE_DIR")
    if [ "$WF_DEFAULT_MODEL_CACHE_DIR" != "$WF_MODEL_CACHE_DIR" ]; then
      targets+=("$WF_DEFAULT_MODEL_CACHE_DIR")
    fi
    for target in "${targets[@]}"; do
      case "$target" in
        *"/hub/models--"*--*) ;;
        *) die "refusing unsafe model-cache target: $target" ;;
      esac
      command=$(shell_join_q rm -rf -- "$target")
      node_exec "$rank" "$command" \
        || die "could not remove $target on ${WF_RANK_HOSTNAMES[$rank]}"
    done
    model_replica_absent "$rank" \
      || die "client replica still exists on ${WF_RANK_HOSTNAMES[$rank]}"
    log "removed client model replica from ${WF_RANK_HOSTNAMES[$rank]}"
  done
}

fingerprint_value() {
  python3 -c \
    'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest()[:16])' \
    "${1:?}"
}

interface_counter() {
  local rank="${1:?}" hostname="${2:?}" role="${3:?}" netdev="${4:?}"
  local node_id="${5:?}" command output node_fingerprint
  local -a values=()
  command=$(shell_join_q cat \
    "/sys/class/net/$netdev/statistics/rx_bytes" \
    "/sys/class/net/$netdev/statistics/tx_bytes")
  output=$(node_exec "$rank" "$command" 2>/dev/null) \
    || die "cannot read $netdev counters on $hostname"
  mapfile -t values <<<"$output"
  [ "${#values[@]}" -eq 2 ] \
    && [[ "${values[0]}" =~ ^[0-9]+$ ]] \
    && [[ "${values[1]}" =~ ^[0-9]+$ ]] \
    || die "invalid $netdev counters on $hostname"
  node_fingerprint=$(fingerprint_value "$node_id")
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$rank" "$node_fingerprint" "$role" "$netdev" \
    "${values[0]}" "${values[1]}"
}

capture_network_snapshot() {
  local destination="${1:?}" rank_limit="${2:?}" rank netdev key
  declare -A seen=()
  : >"$destination"
  for ((rank = 0; rank < rank_limit; rank++)); do
    netdev="${CLUSTER_NODE_CONTROL_IFS[$rank]}"
    key="$rank|control|$netdev"
    if [ -z "${seen[$key]:-}" ]; then
      interface_counter "$rank" "${WF_RANK_HOSTNAMES[$rank]}" \
        control "$netdev" "${WF_RANK_NODE_IDS[$rank]}" >>"$destination"
      seen["$key"]=1
    fi
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    netdev="${WF_CLIENT_NETDEVS[$rank]}"
    key="$rank|fabric-client|$netdev"
    if [ -z "${seen[$key]:-}" ]; then
      interface_counter "$rank" "${WF_RANK_HOSTNAMES[$rank]}" \
        fabric-client "$netdev" "${WF_RANK_NODE_IDS[$rank]}" >>"$destination"
      seen["$key"]=1
    fi
  done
  for ((rank = 0; rank < rank_limit; rank++)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    netdev="${WF_SERVER_NETDEVS[$rank]}"
    key="$WF_OWNER_RANK|fabric-owner|$netdev"
    if [ -z "${seen[$key]:-}" ]; then
      interface_counter "$WF_OWNER_RANK" "$WF_OWNER_HOSTNAME" \
        fabric-owner "$netdev" "$WF_OWNER_NODE_ID" >>"$destination"
      seen["$key"]=1
    fi
  done
}

require_cold_cache_idle() {
  local rank_limit="${1:?}" rank command active
  command=$(shell_join_q docker ps -q \
    --filter "label=${PULSAR_MANAGED_LABEL}=true")
  for ((rank = 0; rank < rank_limit; rank++)); do
    active=$(node_exec "$rank" "$command" 2>/dev/null) \
      || die "cannot inspect Docker on ${WF_RANK_HOSTNAMES[$rank]}"
    [ -z "$active" ] \
      || die "--cold refuses active Pulsar containers on ${WF_RANK_HOSTNAMES[$rank]}"
  done
}

drop_rank_page_caches() {
  local rank_limit="${1:?}" rank
  require_cold_cache_idle "$rank_limit"
  for ((rank = 0; rank < rank_limit; rank++)); do
    node_privileged "$rank" sh -c \
      'sync; echo 3 > /proc/sys/vm/drop_caches'
  done
}

drop_configured_caches() {
  local profile="${1:?}" all_configured="${2:-0}" yes="${3:-0}"
  local rank_limit
  load_fabric "$profile"
  rank_limit="$NODES"
  [ "$all_configured" = 1 ] && rank_limit="$WF_STORAGE_NODES"
  confirm_system_change "$yes" \
    "Drop Linux page caches on $rank_limit idle Sparks?"
  drop_rank_page_caches "$rank_limit"
  log "dropped page caches on $rank_limit idle Sparks"
}

node_upload_file() {
  local rank="${1:?}" source="${2:?}" destination="${3:?}"
  local host command
  if [ "$rank" -eq 0 ]; then
    install -m 0600 "$source" "$destination"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    command=$(shell_join_q sh -c \
      'umask 077; cat > "$1"' sh "$destination")
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command" <"$source"
  fi
}

BENCHMARK_STAGE=""
BENCHMARK_PRIVATE_STAGE=""
BENCHMARK_TEMP_MANIFEST=""
BENCHMARK_TEMP_RANK_LIMIT=0

cleanup_benchmark() {
  local rank command
  set +e
  if [ -n "$BENCHMARK_TEMP_MANIFEST" ]; then
    command=$(shell_join_q rm -f "$BENCHMARK_TEMP_MANIFEST")
    for ((rank = 0; rank < BENCHMARK_TEMP_RANK_LIMIT; rank++)); do
      node_exec "$rank" "$command" >/dev/null 2>&1
    done
  fi
  if [ -n "$BENCHMARK_STAGE" ] && [ -d "$BENCHMARK_STAGE" ]; then
    rm -rf -- "$BENCHMARK_STAGE"
  fi
  if [ -n "$BENCHMARK_PRIVATE_STAGE" ] \
      && [ -d "$BENCHMARK_PRIVATE_STAGE" ]; then
    rm -rf -- "$BENCHMARK_PRIVATE_STAGE"
  fi
}

sanitize_integrity_artifact() {
  local source="${1:?}" destination="${2:?}" config="${3:?}"
  python3 - "$source" "$destination" "$config" <<'PY'
import hashlib
import json
import os
import pathlib
import tempfile
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
config_path = pathlib.Path(sys.argv[3])
data = json.loads(source.read_text(encoding="utf-8"))
config = json.loads(config_path.read_text(encoding="utf-8"))
fingerprints = {
    item["rank"]: hashlib.sha256(item["node_id"].encode()).hexdigest()[:16]
    for item in config["ranks"]
}
data.pop("path", None)
owner_node_id = data.pop("owner_node_id", None)
if owner_node_id:
    data["owner"] = {
        "rank": config["owner"]["topology_rank"],
        "node_fingerprint": hashlib.sha256(
            owner_node_id.encode()
        ).hexdigest()[:16],
    }
data.pop("placement", None)
for collection in ("nodes", "ranks"):
    for node in data.get(collection, []):
        rank = node.get("rank")
        for private_field in (
            "node_id",
            "hostname",
            "ssh_host",
            "cache_root",
            "path",
        ):
            node.pop(private_field, None)
        if rank in fingerprints:
            node["node_fingerprint"] = fingerprints[rank]
descriptor, temporary = tempfile.mkstemp(
    prefix=f".{destination.name}.",
    suffix=".tmp",
    dir=destination.parent,
)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PY
}

benchmark_fabric() {
  local profile="${1:?}"
  shift
  local tag="" source=fabric scope=serving cache_state=warm cold=0 yes=0 verify=0
  local max_mib_s=""
  local output="" rank_limit serving_only=1 rank manifest_command
  local stage destination parent failed=0 pid report_rc=0
  local private_stage private_config private_topology private_integrity
  local -a pids=() report_args=() benchmark_roots=() manifest_paths=()
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --tag)
        [ "$#" -ge 2 ] || die "--tag requires a value" 2
        tag="$2"
        shift
        ;;
      --source)
        [ "$#" -ge 2 ] || die "--source requires fabric or replicated" 2
        source="$2"
        shift
        ;;
      --serving-only)
        scope=serving
        serving_only=1
        ;;
      --all-configured)
        scope=all-configured
        serving_only=0
        ;;
      --cold) cold=1; cache_state=cold ;;
      --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
      --verify-sha256) verify=1 ;;
      --max-mib-s)
        [ "$#" -ge 2 ] || die "--max-mib-s requires a rate" 2
        max_mib_s="$2"
        shift
        ;;
      --output)
        [ "$#" -ge 2 ] || die "--output requires a directory" 2
        output="$2"
        shift
        ;;
      --yes|-y) yes=1 ;;
      *) die "unknown benchmark option: $1" 2 ;;
    esac
    shift
  done
  [[ "$tag" =~ ^[A-Za-z0-9._-]+$ ]] \
    || die "benchmark requires --tag using letters, numbers, dot, underscore, or hyphen" 2
  case "$source" in
    fabric|replicated) ;;
    *) die "--source must be fabric or replicated" 2 ;;
  esac
  if [ "$source" = replicated ] && [ "$scope" != serving ]; then
    die "replicated comparison supports --serving-only; stage extra ranks explicitly"
  fi
  load_fabric "$profile"
  rank_limit="$NODES"
  [ "$scope" = all-configured ] && rank_limit="$WF_STORAGE_NODES"
  if [ -n "$output" ]; then
    destination="$output"
  else
    destination="$REPO_DIR/results/weight-fabric/$tag"
  fi
  [ "$destination" != / ] && [ "$destination" != . ] \
    || die "refusing unsafe benchmark output directory"
  [ ! -e "$destination" ] \
    || die "benchmark output already exists: $destination"
  parent=$(dirname "$destination")
  mkdir -p "$parent"
  stage=$(mktemp -d "$parent/.${tag}.XXXXXX")
  BENCHMARK_STAGE="$stage"
  private_stage=$(mktemp -d "$parent/.${tag}.private.XXXXXX")
  BENCHMARK_PRIVATE_STAGE="$private_stage"
  BENCHMARK_TEMP_RANK_LIMIT="$rank_limit"
  trap cleanup_benchmark EXIT
  private_config="$private_stage/config.json"
  private_topology="$private_stage/topology.json"
  private_integrity="$private_stage/integrity.json"
  install -m 0600 "$WF_CONFIG_PATH" "$private_config"
  install -m 0600 "$CLUSTER_TOPOLOGY_FILE" "$private_topology"
  "$PY_TOOL" provenance "$private_config" "$private_topology" \
    --profile "$profile" --model "$MODEL" --nodes "$NODES" \
    --output "$stage/provenance.json" >/dev/null
  manifest_command=$(shell_join_q cat "$WF_MANIFEST_PATH")
  node_exec "$WF_OWNER_RANK" "$manifest_command" >"$stage/manifest.json" \
    || die "cannot capture the sealed owner manifest"

  if [ "$source" = fabric ]; then
    if [ "$serving_only" = 1 ]; then
      check_fabric "$profile" full 1 1 >"$private_integrity"
    else
      check_fabric "$profile" full 1 0 >"$private_integrity"
    fi
    sanitize_integrity_artifact "$private_integrity" \
      "$stage/integrity.json" "$private_config"
    for ((rank = 0; rank < rank_limit; rank++)); do
      benchmark_roots["$rank"]="${WF_RANK_CACHE_ROOTS[$rank]}"
      manifest_paths["$rank"]="${WF_RANK_CACHE_ROOTS[$rank]}/$WF_MANIFEST_RELATIVE"
    done
  else
    "$REPO_DIR/scripts/check-weights.sh" "$profile" \
      --weight-source replicated --json >"$private_integrity"
    sanitize_integrity_artifact "$private_integrity" \
      "$stage/integrity.json" "$private_config"
    BENCHMARK_TEMP_MANIFEST="/tmp/pulsar-weight-manifest-${WF_CONFIG_ID:0:12}-${tag}.json"
    for ((rank = 0; rank < rank_limit; rank++)); do
      node_upload_file "$rank" "$stage/manifest.json" \
        "$BENCHMARK_TEMP_MANIFEST"
      benchmark_roots["$rank"]="$HF_CACHE"
      manifest_paths["$rank"]="$BENCHMARK_TEMP_MANIFEST"
      node_python "$rank" manifest-verify \
        --cache-root "$HF_CACHE" \
        --manifest "$BENCHMARK_TEMP_MANIFEST" \
        --profile "$WF_PROFILE" \
        --model "$WF_MODEL" \
        --json >"$stage/integrity-rank-$rank.json"
    done
  fi
  if [ "$cold" = 1 ]; then
    confirm_system_change "$yes" \
      "Drop Linux page caches on $rank_limit idle Sparks before this benchmark?"
    drop_rank_page_caches "$rank_limit"
  fi

  capture_network_snapshot "$stage/network-before.tsv" "$rank_limit"

  log "starting concurrent $source $scope $cache_state reads on $rank_limit nodes…"
  for ((rank = 0; rank < rank_limit; rank++)); do
    (
      local -a io_args=(
        io-benchmark
        --cache-root "${benchmark_roots[$rank]}"
        --manifest "${manifest_paths[$rank]}"
        --profile "$WF_PROFILE"
        --model "$WF_MODEL"
        --rank "$rank"
        --role "${WF_RANK_ROLES[$rank]}"
        --source "$source"
        --label "$tag"
        --node-id "${WF_RANK_NODE_IDS[$rank]}"
      )
      [ "$verify" = 1 ] && io_args+=(--verify-sha256)
      [ -z "$max_mib_s" ] || io_args+=(--max-mib-s "$max_mib_s")
      node_python "$rank" "${io_args[@]}"
    ) >"$stage/rank-$rank.json" 2>"$stage/rank-$rank.stderr" &
    pids+=("$!")
  done
  for ((rank = 0; rank < rank_limit; rank++)); do
    pid="${pids[$rank]}"
    if ! wait "$pid"; then
      warn "rank $rank benchmark failed:"
      sed -n '1,40p' "$stage/rank-$rank.stderr" >&2
      failed=1
    fi
  done
  [ "$failed" = 0 ] || die "one or more concurrent model reads failed"
  capture_network_snapshot "$stage/network-after.tsv" "$rank_limit"

  report_args=(
    benchmark-report
    --config "$private_config"
    --manifest "$stage/manifest.json"
    --network-before "$stage/network-before.tsv"
    --network-after "$stage/network-after.tsv"
    --tag "$tag"
    --source "$source"
    --scope "$scope"
    --cache-state "$cache_state"
    --output "$stage/benchmark.json"
  )
  for ((rank = 0; rank < rank_limit; rank++)); do
    report_args+=(--result "$stage/rank-$rank.json")
  done
  "$PY_TOOL" "${report_args[@]}" >"$stage/report.stdout" || report_rc=$?
  "$PY_TOOL" artifact-audit \
    --directory "$stage" \
    --config "$private_config" \
    --topology "$private_topology" \
    --output "$stage/artifact-audit.json" >/dev/null
  rm -rf -- "$private_stage"
  private_stage=""
  BENCHMARK_PRIVATE_STAGE=""
  mv "$stage" "$destination"
  stage=""
  BENCHMARK_STAGE=""

  python3 - "$destination/benchmark.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
aggregate = report["aggregate"]
print("WEIGHT STORAGE BENCHMARK")
print(
    f"  Result      {report['tag']} · {report['source']} · "
    f"{report['cache_state']} · {report['scope']}"
)
print(f"  Ranks       {aggregate['rank_count']} concurrent")
if report["measurement_kind"] == "fault-injection":
    print(
        "  Mode        fault injection · paced at "
        f"{report['rate_limit_mib_s']:.2f} MiB/s per rank"
    )
    print(f"  Duration    {aggregate['max_rank_seconds']:.2f} s")
else:
    print(
        "  Throughput  "
        f"{aggregate['logical_throughput_gib_s']:.2f} logical GiB/s"
    )
print(f"  Traffic     {report['traffic_proof']['state']}")
print(f"  Artifact    {sys.argv[1]}")
PY
  [ "$report_rc" -eq 0 ] \
    || die "benchmark completed, but its cold traffic proof failed (artifact preserved)"
}

command="${1:-}"
[ -n "$command" ] || { usage; exit 2; }
shift
[ "$command" = help ] || [ "$command" = -h ] || [ "$command" = --help ] \
  || acquire_model_library_lifecycle_lock shared
case "$command" in
  configure|prerequisites|setup-prerequisites|download|seal|apply|check|verify|benchmark|drop-caches|purge-replicas)
    refuse_retired_live_nfs_serving_workflow
    ;;
esac
case "$command" in
  configure)
    [ "$#" -ge 1 ] || die "configure requires a profile" 2
    profile="$1"
    shift
    configure_fabric "$profile" "$@"
    ;;
  show)
    [ "$#" -ge 1 ] || die "show requires a profile" 2
    profile="$1"
    shift
    json=0
    [ "${1:-}" = --json ] && { json=1; shift; }
    [ "$#" -eq 0 ] || die "unknown show option: $1" 2
    show_fabric "$profile" "$json"
    ;;
  prerequisites)
    [ "$#" -ge 1 ] || die "prerequisites requires a profile" 2
    profile="$1"
    shift
    json=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --json) json=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        *) die "unknown prerequisites option: $1" 2 ;;
      esac
      shift
    done
    prerequisites_fabric "$profile" "$json"
    ;;
  setup-prerequisites)
    [ "$#" -ge 1 ] || die "setup-prerequisites requires a profile" 2
    profile="$1"
    shift
    yes=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --yes|-y) yes=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        *) die "unknown setup-prerequisites option: $1" 2 ;;
      esac
      shift
    done
    setup_prerequisites "$profile" "$yes"
    ;;
  download)
    [ "$#" -ge 1 ] || die "download requires a profile" 2
    profile="$1"
    shift
    yes=0
    case "${1:-}" in --yes|-y) yes=1; shift ;; esac
    [ "$#" -eq 0 ] || die "unknown download option: $1" 2
    download_fabric "$profile" "$yes"
    ;;
  seal)
    [ "$#" -eq 1 ] || die "seal requires exactly one profile" 2
    seal_fabric "$1"
    ;;
  apply)
    [ "$#" -ge 1 ] || die "apply requires a profile" 2
    profile="$1"
    shift
    yes=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --yes|-y) yes=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        *) die "unknown apply option: $1" 2 ;;
      esac
      shift
    done
    apply_fabric "$profile" "$yes"
    ;;
  check|verify)
    [ "$#" -ge 1 ] || die "$command requires a profile" 2
    profile="$1"
    shift
    mode=metadata
    json=0
    serving_only=0
    [ "$command" = verify ] && mode=full
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --full) mode=full ;;
        --json) json=1 ;;
        --serving-only) serving_only=1 ;;
        *) die "unknown $command option: $1" 2 ;;
      esac
      shift
    done
    check_fabric "$profile" "$mode" "$json" "$serving_only"
    ;;
  benchmark)
    [ "$#" -ge 1 ] || die "benchmark requires a profile" 2
    profile="$1"
    shift
    benchmark_fabric "$profile" "$@"
    ;;
  drop-caches)
    [ "$#" -ge 1 ] || die "drop-caches requires a profile" 2
    profile="$1"
    shift
    all_configured=0
    yes=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --serving-only) all_configured=0 ;;
        --all-configured) all_configured=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        --yes|-y) yes=1 ;;
        *) die "unknown drop-caches option: $1" 2 ;;
      esac
      shift
    done
    drop_configured_caches "$profile" "$all_configured" "$yes"
    ;;
  purge-replicas)
    [ "$#" -ge 1 ] || die "purge-replicas requires a profile" 2
    profile="$1"
    shift
    yes=0
    case "${1:-}" in --yes|-y) yes=1; shift ;; esac
    [ "$#" -eq 0 ] || die "unknown purge-replicas option: $1" 2
    purge_client_replicas "$profile" "$yes"
    ;;
  unmount)
    [ "$#" -ge 1 ] || die "unmount requires a profile" 2
    profile="$1"
    shift
    yes=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --yes|-y) yes=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        *) die "unknown unmount option: $1" 2 ;;
      esac
      shift
    done
    unmount_clients "$profile" "$yes"
    ;;
  teardown)
    [ "$#" -ge 1 ] || die "teardown requires a profile" 2
    profile="$1"
    shift
    yes=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --yes|-y) yes=1 ;;
        --interactive-sudo) WEIGHT_FABRIC_SUDO_MODE=interactive ;;
        *) die "unknown teardown option: $1" 2 ;;
      esac
      shift
    done
    teardown_fabric "$profile" "$yes"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    die "unknown command: $command (try --help)" 2
    ;;
esac
