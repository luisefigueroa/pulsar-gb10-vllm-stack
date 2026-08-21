#!/usr/bin/env bash
# Leftover live NFS/RDMA site-state cleanup (ADR 0005, ADR 0006).
#
# Live NFS/RDMA serving was rejected (ADR 0005) and the workflow internals
# were removed with the rest of the distribution-mode surface (ADR 0006).
# This helper only inspects (show) and removes (unmount/teardown) leftover
# site-local exports and client mounts recorded in gitignored
# .weight-fabric/<profile>.json configs. It never serves, copies, or remaps.
set -euo pipefail
SCRIPT_NAME=weight-fabric
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

WEIGHT_FABRIC_DIR="${WEIGHT_FABRIC_DIR:-$REPO_DIR/.weight-fabric}"

usage() {
  cat <<'EOF'
Leftover live NFS/RDMA site-state cleanup (ADR 0005, ADR 0006)

Live NFS/RDMA under vLLM is not a serving runtime source and its workflow
was removed. The model library is the only weight mechanism. This helper
only inspects and removes leftover site exports and mounts.

Usage:
  scripts/weight-fabric.sh show <profile> [--json]
  scripts/weight-fabric.sh unmount <profile> [--yes] [--interactive-sudo]
  scripts/weight-fabric.sh teardown <profile> [--yes] [--interactive-sudo]

Safety:
  • unmount refuses any client mount still used by a container.
  • teardown removes only this configuration's export and mount state;
    model files and the site config file are preserved.
  • privileged steps use passwordless sudo (sudo -n) on each node;
    --interactive-sudo prompts in the operator terminal instead and never
    stores a password. WEIGHT_FABRIC_SUDO_MODE=passwordless|interactive
    sets the default; the flag overrides it.
  • --yes is only for an already reviewed leftover-teardown runbook.
  • no command serves, copies, or remaps weights.
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

declare -ag WF_RANK_IDS=()
declare -ag WF_RANK_ROLES=()
declare -ag WF_RANK_NODE_IDS=()
declare -ag WF_RANK_HOSTNAMES=()
declare -ag WF_RANK_EXEC_HOSTS=()

wf_config_field() {
  # Whitespace-safe scalar extraction (paths may contain spaces).
  FIELD="$1" python3 - "$WF_CONFIG_PATH" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
value = config
for part in os.environ["FIELD"].split("."):
    value = value[part]
sys.stdout.write(str(value))
PY
}

# Read only the fields cleanup needs, straight from the site config, after
# recomputing its content identity. Endpoints are then resolved through the
# CONFIRMED topology by immutable node identity — a recorded ssh_host is
# never trusted directly, so a reassigned hostname cannot receive privileged
# cleanup (see AGENTS.md topology invariants).
load_fabric() {
  local profile="${1:?profile required}" rows line
  WF_CONFIG_PATH=$(fabric_config_path "$profile")
  [ -f "$WF_CONFIG_PATH" ] \
    || die "no leftover fabric config for $profile at $WF_CONFIG_PATH"
  rows=$(python3 - "$WF_CONFIG_PATH" <<'PY'
import base64
import hashlib
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
if config.get("schema_version") not in (1, 2):
    raise SystemExit("unsupported fabric config schema")

# Recompute the content identity exactly as the retired tool wrote it; an
# edited or corrupted config must not steer privileged teardown.
identity = {k: v for k, v in config.items() if k != "configuration_id"}
canonical = json.dumps(
    identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True
).encode("utf-8")
if hashlib.sha256(canonical).hexdigest() != config.get("configuration_id"):
    raise SystemExit("configuration_id does not match config content")


def b64(value):
    return base64.b64encode(str(value).encode("utf-8")).decode("ascii")


print("id", str(config["configuration_id"]))
for record in config["ranks"]:
    print(
        "rank",
        int(record["rank"]),
        b64(record["role"]),
        b64(record["node_id"]),
        b64(record["hostname"]),
    )
PY
  ) || die "fabric config is invalid: $WF_CONFIG_PATH"

  WF_CONFIG_ID="" WF_OWNER_RANK=""
  WF_RANK_IDS=() WF_RANK_ROLES=() WF_RANK_NODE_IDS=() WF_RANK_HOSTNAMES=()
  WF_RANK_EXEC_HOSTS=()
  local kind a b c d role
  while read -r kind a b c d; do
    case "$kind" in
      id) WF_CONFIG_ID="$a" ;;
      rank)
        role=$(printf '%s' "$b" | base64 -d)
        WF_RANK_IDS+=("$a")
        WF_RANK_ROLES+=("$role")
        WF_RANK_NODE_IDS+=("$(printf '%s' "$c" | base64 -d)")
        WF_RANK_HOSTNAMES+=("$(printf '%s' "$d" | base64 -d)")
        [ "$role" = owner ] && WF_OWNER_RANK="$a"
        ;;
    esac
  done <<<"$rows"

  [[ "$WF_CONFIG_ID" =~ ^[0-9a-f]{64}$ ]] \
    || die "fabric config id is invalid: $WF_CONFIG_PATH"
  WF_MODEL=$(wf_config_field model)
  WF_PROFILE=$(wf_config_field profile)
  WF_EXPORT_PATH=$(wf_config_field transport.export_path)
  WF_MOUNT_PATH=$(wf_config_field transport.mount_path)
  WF_OWNER_HOSTNAME=$(wf_config_field owner.hostname)
  [ "$WF_PROFILE" = "$profile" ] \
    || die "fabric config names profile $WF_PROFILE, not $profile"
  [ -n "$WF_OWNER_RANK" ] || die "fabric config has no owner rank"
  case "$WF_EXPORT_PATH" in
    /*) ;;
    *) die "fabric export path is not absolute" ;;
  esac
  case "$WF_MOUNT_PATH" in
    /*) ;;
    *) die "fabric mount path is not absolute" ;;
  esac

  # Resolve every recorded rank to its CURRENT confirmed endpoint by node
  # identity. A node that is no longer confirmed fails closed: re-confirm it
  # (scripts/detect-fabric.sh --write-topology) or clean it up manually.
  load_cluster_topology \
    || die "leftover-fabric cleanup requires a confirmed topology manifest"
  local index node_id confirmed resolved
  for index in "${!WF_RANK_NODE_IDS[@]}"; do
    node_id="${WF_RANK_NODE_IDS[$index]}"
    resolved=""
    for ((confirmed = 0; confirmed < CLUSTER_TOPOLOGY_COUNT; confirmed++)); do
      if [ "${CLUSTER_NODE_IDS[$confirmed]:-}" = "$node_id" ]; then
        resolved="${CLUSTER_NODE_SSH_HOSTS[$confirmed]:-}"
        break
      fi
    done
    [ -n "$resolved" ] \
      || die "config rank ${WF_RANK_IDS[$index]} (node identity not in the confirmed topology) — re-confirm membership or clean up that machine manually"
    if [ "$resolved" != local ]; then
      [[ "$resolved" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "confirmed endpoint for rank ${WF_RANK_IDS[$index]} is not a bare hostname"
    fi
    WF_RANK_EXEC_HOSTS+=("$resolved")
  done
}

node_exec() {
  local rank="${1:?rank required}" endpoint
  shift
  endpoint="${WF_RANK_EXEC_HOSTS[$rank]:?rank endpoint unresolved}"
  if [ "$endpoint" = local ]; then
    bash -c "$1"
  else
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$endpoint" "$1"
  fi
}

WF_SUDO_MODE="${WEIGHT_FABRIC_SUDO_MODE:-passwordless}"
case "$WF_SUDO_MODE" in
  passwordless|interactive) ;;
  *) die "WEIGHT_FABRIC_SUDO_MODE must be passwordless or interactive" ;;
esac

node_privileged() {
  local rank="${1:?rank required}" endpoint command
  shift
  endpoint="${WF_RANK_EXEC_HOSTS[$rank]:?rank endpoint unresolved}"
  if [ "$WF_SUDO_MODE" = interactive ]; then
    # Attended sudo: authentication happens in the operator terminal;
    # Pulsar never stores a password. Remote ranks need a TTY, so batch
    # mode is explicitly overridden for this one invocation.
    command="sudo $(shell_join_q "$@")"
    if [ "$endpoint" = local ]; then
      bash -c "$command" \
        || die "rank $rank privileged step failed: $*"
    else
      "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -o BatchMode=no -tt \
        -- "$endpoint" "$command" \
        || die "rank $rank privileged step failed: $*"
    fi
    return 0
  fi
  command="sudo -n $(shell_join_q "$@")"
  node_exec "$rank" "$command" \
    || die "rank $rank privileged step failed (passwordless sudo required; retry with --interactive-sudo in a terminal): $*"
}

confirm_teardown() {
  local yes="${1:?}" prompt="${2:?}" answer
  [ "$yes" = 1 ] && return 0
  [ -t 0 ] || die "refusing without --yes on a non-interactive stdin"
  printf '%s\nType yes to continue: ' "$prompt" >&2
  read -r answer
  [ "$answer" = yes ] || die "cancelled"
}

fabric_export_file() {
  printf '/etc/exports.d/pulsar-weight-fabric-%s.exports\n' \
    "${WF_CONFIG_ID:0:12}"
}

fabric_nfs_file() {
  printf '/etc/nfs.conf.d/pulsar-weight-fabric-%s.conf\n' \
    "${WF_CONFIG_ID:0:12}"
}

client_mounted() {
  local rank="${1:?}"
  node_exec "$rank" "$(shell_join_q findmnt -rn -M "$WF_MOUNT_PATH")" \
    >/dev/null 2>&1
}

show_fabric() {
  local profile="${1:?profile required}" json="${2:-0}" rank state
  local export_file exists
  load_fabric "$profile"
  export_file=$(fabric_export_file)
  if node_exec "$WF_OWNER_RANK" \
      "$(shell_join_q test -e "$export_file")" >/dev/null 2>&1; then
    exists=1
  else
    exists=0
  fi
  if [ "$json" = 1 ]; then
    local -a mount_rows=()
    for rank in "${!WF_RANK_ROLES[@]}"; do
      [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
      if client_mounted "$rank"; then state=mounted; else state=absent; fi
      mount_rows+=("$rank=$state")
    done
    MOUNTS="${mount_rows[*]-}" EXPORT_EXISTS="$exists" \
      CONFIG_PATH="$WF_CONFIG_PATH" CONFIG_ID="${WF_CONFIG_ID:0:12}" \
      PROFILE="$WF_PROFILE" MODEL="$WF_MODEL" \
      EXPORT_PATH="$WF_EXPORT_PATH" MOUNT_PATH="$WF_MOUNT_PATH" \
      python3 - <<'PY'
import json
import os

mounts = {}
for pair in os.environ["MOUNTS"].split():
    rank, _, state = pair.partition("=")
    mounts[rank] = state
print(
    json.dumps(
        {
            "status": "leftover-cleanup-only",
            "profile": os.environ["PROFILE"],
            "model": os.environ["MODEL"],
            "config_path": os.environ["CONFIG_PATH"],
            "configuration_id": os.environ["CONFIG_ID"],
            "export_path": os.environ["EXPORT_PATH"],
            "mount_path": os.environ["MOUNT_PATH"],
            "owner_export_file_present": os.environ["EXPORT_EXISTS"] == "1",
            "client_mounts": mounts,
        },
        indent=2,
        sort_keys=True,
    )
)
PY
    return 0
  fi
  echo "leftover live-NFS state (cleanup only — ADR 0005/0006)"
  echo "  profile   $WF_PROFILE"
  echo "  model     $WF_MODEL"
  echo "  config    $WF_CONFIG_PATH (${WF_CONFIG_ID:0:12})"
  echo "  export    $WF_EXPORT_PATH"
  echo "  mount     $WF_MOUNT_PATH"
  if [ "$exists" = 1 ]; then
    echo "  owner     export file present ($(fabric_export_file))"
  else
    echo "  owner     export file absent"
  fi
  for rank in "${!WF_RANK_ROLES[@]}"; do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    if client_mounted "$rank"; then state=mounted; else state=absent; fi
    echo "  client    rank $rank ${WF_RANK_HOSTNAMES[$rank]} · $state"
  done
}

unmount_clients() {
  local profile="${1:?}" yes="${2:-0}" rank command in_use
  load_fabric "$profile"
  # Leftover exports point into a durable home; serialize against removal.
  acquire_model_library_lifecycle_lock shared
  confirm_teardown "$yes" \
    "Unmount the leftover weight view from every configured client?"
  for rank in "${!WF_RANK_ROLES[@]}"; do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    command=$(shell_join_q docker ps -q --filter "volume=$WF_MOUNT_PATH")
    in_use=$(node_exec "$rank" "$command" 2>/dev/null) \
      || die "cannot inspect Docker on rank $rank"
    [ -z "$in_use" ] \
      || die "rank $rank still has a container using $WF_MOUNT_PATH"
  done
  for ((rank = ${#WF_RANK_ROLES[@]} - 1; rank >= 0; rank--)); do
    [ "${WF_RANK_ROLES[$rank]}" = client ] || continue
    if client_mounted "$rank"; then
      node_privileged "$rank" umount "$WF_MOUNT_PATH"
    fi
  done
  log "leftover client mounts removed"
}

teardown_fabric() {
  local profile="${1:?}" yes="${2:-0}" export_file nfs_file command
  load_fabric "$profile"
  confirm_teardown "$yes" \
    "Unmount clients and remove this config's owner export?"
  unmount_clients "$profile" 1
  export_file=$(fabric_export_file)
  nfs_file=$(fabric_nfs_file)
  node_privileged "$WF_OWNER_RANK" rm -f "$export_file" "$nfs_file"
  node_privileged "$WF_OWNER_RANK" exportfs -ra
  node_privileged "$WF_OWNER_RANK" systemctl restart nfs-server
  command="$(shell_join_q test ! -e "$export_file")"
  command+=" && $(shell_join_q test ! -e "$nfs_file")"
  node_exec "$WF_OWNER_RANK" "$command" >/dev/null 2>&1 \
    || die "teardown is incomplete: configuration files remain; retry teardown"
  log "removed export and mounts; model files and site config were preserved"
}

command="${1:-help}"
shift || true

case "$command" in
  show)
    profile="${1:-}"
    [ -n "$profile" ] || die "usage: $0 show <profile> [--json]"
    shift
    json=0
    [ "${1:-}" = --json ] && json=1
    show_fabric "$profile" "$json"
    ;;
  unmount|teardown)
    subcommand="$command"
    profile="${1:-}"
    [ -n "$profile" ] \
      || die "usage: $0 $subcommand <profile> [--yes] [--interactive-sudo]"
    shift
    yes=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --yes) yes=1 ;;
        --interactive-sudo) WF_SUDO_MODE=interactive ;;
        *) die "unknown arg: $1" ;;
      esac
      shift
    done
    if [ "$subcommand" = unmount ]; then
      unmount_clients "$profile" "$yes"
    else
      teardown_fabric "$profile" "$yes"
    fi
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    die "weight-fabric only supports show/unmount/teardown (ADR 0005/0006)"
    ;;
esac
