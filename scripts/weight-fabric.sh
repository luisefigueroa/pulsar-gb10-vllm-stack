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
  scripts/weight-fabric.sh unmount <profile> [--yes]
  scripts/weight-fabric.sh teardown <profile> [--yes]

Safety:
  • unmount refuses any client mount still used by a container.
  • teardown removes only this configuration's export and mount state;
    model files and the site config file are preserved.
  • privileged steps use passwordless sudo (sudo -n) on each node.
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
declare -ag WF_RANK_SSH_HOSTS=()
declare -ag WF_RANK_HOSTNAMES=()

# Read only the fields cleanup needs, straight from the site config. The
# retired workflow revalidated configs against live topology; leftover
# cleanup must keep working after membership changed, so it trusts the
# recorded rank endpoints instead (validated to bare hostnames below).
load_fabric() {
  local profile="${1:?profile required}" rows line
  WF_CONFIG_PATH=$(fabric_config_path "$profile")
  [ -f "$WF_CONFIG_PATH" ] \
    || die "no leftover fabric config for $profile at $WF_CONFIG_PATH"
  rows=$(python3 - "$WF_CONFIG_PATH" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
if config.get("schema_version") not in (1, 2):
    raise SystemExit("unsupported fabric config schema")
transport = config["transport"]
print("id", str(config["configuration_id"]))
print("model", str(config["model"]))
print("profile", str(config["profile"]))
print("export", str(transport["export_path"]))
print("mount", str(transport["mount_path"]))
owner = config["owner"]
for record in config["ranks"]:
    print(
        "rank",
        int(record["rank"]),
        str(record["role"]),
        str(record["ssh_host"]),
        str(record["hostname"]),
    )
print("ownerhost", str(owner["hostname"]))
PY
  ) || die "fabric config is unreadable: $WF_CONFIG_PATH"

  WF_CONFIG_ID="" WF_MODEL="" WF_PROFILE="" WF_EXPORT_PATH="" WF_MOUNT_PATH=""
  WF_OWNER_HOSTNAME="" WF_OWNER_RANK=""
  WF_RANK_IDS=() WF_RANK_ROLES=() WF_RANK_SSH_HOSTS=() WF_RANK_HOSTNAMES=()
  local kind a b c d
  while read -r kind a b c d; do
    case "$kind" in
      id) WF_CONFIG_ID="$a" ;;
      model) WF_MODEL="$a" ;;
      profile) WF_PROFILE="$a" ;;
      export) WF_EXPORT_PATH="$a" ;;
      mount) WF_MOUNT_PATH="$a" ;;
      ownerhost) WF_OWNER_HOSTNAME="$a" ;;
      rank)
        WF_RANK_IDS+=("$a")
        WF_RANK_ROLES+=("$b")
        WF_RANK_SSH_HOSTS+=("$c")
        WF_RANK_HOSTNAMES+=("$d")
        [ "$b" = owner ] && WF_OWNER_RANK="$a"
        ;;
    esac
  done <<<"$rows"

  [[ "$WF_CONFIG_ID" =~ ^[0-9a-f]{64}$ ]] \
    || die "fabric config id is invalid: $WF_CONFIG_PATH"
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
  local index
  for index in "${!WF_RANK_SSH_HOSTS[@]}"; do
    [[ "${WF_RANK_SSH_HOSTS[$index]}" =~ ^[A-Za-z0-9._-]+$ ]] \
      || die "rank ${WF_RANK_IDS[$index]} ssh host is not a bare hostname"
  done
}

node_exec() {
  local rank="${1:?rank required}"
  shift
  if [ "$rank" -eq 0 ]; then
    bash -c "$1"
  else
    "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "${WF_RANK_SSH_HOSTS[$rank]}" "$1"
  fi
}

node_privileged() {
  local rank="${1:?rank required}"
  shift
  local command
  command="sudo -n $(shell_join_q "$@")"
  node_exec "$rank" "$command" \
    || die "rank $rank privileged step failed (passwordless sudo required): $*"
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
  unmount)
    profile="${1:-}"
    [ -n "$profile" ] || die "usage: $0 unmount <profile> [--yes]"
    shift
    yes=0
    [ "${1:-}" = --yes ] && yes=1
    unmount_clients "$profile" "$yes"
    ;;
  teardown)
    profile="${1:-}"
    [ -n "$profile" ] || die "usage: $0 teardown <profile> [--yes]"
    shift
    yes=0
    [ "${1:-}" = --yes ] && yes=1
    teardown_fabric "$profile" "$yes"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    die "weight-fabric only supports show/unmount/teardown (ADR 0005/0006)"
    ;;
esac
