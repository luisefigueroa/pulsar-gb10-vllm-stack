#!/usr/bin/env bash
# Confirmed cluster topology loader. Source only — do not execute.
# shellcheck shell=bash

if [ -n "${_PULSAR_CLUSTER_TOPOLOGY:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
_PULSAR_CLUSTER_TOPOLOGY=1

_topology_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_topology_repo="$(cd "$_topology_dir/.." && pwd)"

CLUSTER_TOPOLOGY_FILE="${CLUSTER_TOPOLOGY_FILE:-$_topology_repo/.cluster-topology.json}"
CLUSTER_TOPOLOGY_LOADED=0
CLUSTER_TOPOLOGY_SOURCE=""
CLUSTER_TOPOLOGY_ID=""
CLUSTER_TOPOLOGY_COUNT=0
CLUSTER_TOPOLOGY_FULL_MESH=0
CLUSTER_TOPOLOGY_MIN_RAILS=0
CLUSTER_PROFILE_NODE_COUNT=0

declare -ag CLUSTER_NODE_IDS=()
declare -ag CLUSTER_NODE_HOSTNAMES=()
declare -ag CLUSTER_NODE_SSH_HOSTS=()
declare -ag CLUSTER_NODE_CONTROL_IPS=()
declare -ag CLUSTER_NODE_CONTROL_IFS=()
declare -ag CLUSTER_NODE_HCAS=()
declare -ag CLUSTER_NODE_RDMA_IFS=()
declare -ag CLUSTER_PROFILE_HCAS=()
declare -ag CLUSTER_PROFILE_RDMA_IFS=()
declare -Ag CLUSTER_PAIR_RAILS=()

_cluster_topology_reset() {
  CLUSTER_TOPOLOGY_SOURCE=""
  CLUSTER_TOPOLOGY_ID=""
  CLUSTER_TOPOLOGY_COUNT=0
  CLUSTER_TOPOLOGY_FULL_MESH=0
  CLUSTER_TOPOLOGY_MIN_RAILS=0
  CLUSTER_PROFILE_NODE_COUNT=0
  CLUSTER_NODE_IDS=()
  CLUSTER_NODE_HOSTNAMES=()
  CLUSTER_NODE_SSH_HOSTS=()
  CLUSTER_NODE_CONTROL_IPS=()
  CLUSTER_NODE_CONTROL_IFS=()
  CLUSTER_NODE_HCAS=()
  CLUSTER_NODE_RDMA_IFS=()
  CLUSTER_PROFILE_HCAS=()
  CLUSTER_PROFILE_RDMA_IFS=()
  CLUSTER_PAIR_RAILS=()
}

_cluster_safe_endpoint() {
  case "${1:-}" in
    ""|-*|*[!A-Za-z0-9._:@%+-]*) return 1 ;;
    *) return 0 ;;
  esac
}

_cluster_load_legacy_topology() {
  local head="${HEAD_IP:-}" worker="${WORKER_IP:-}"
  [ -n "$head" ] || return 1
  _cluster_safe_endpoint "$head" || {
    echo "topology: invalid HEAD_IP '$head'" >&2
    return 1
  }
  if [ -n "$worker" ]; then
    _cluster_safe_endpoint "$worker" || {
      echo "topology: invalid WORKER_IP '$worker'" >&2
      return 1
    }
  fi

  local local_host
  local_host=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)
  CLUSTER_TOPOLOGY_SOURCE=legacy-env
  CLUSTER_TOPOLOGY_ID=legacy-env
  CLUSTER_NODE_IDS=(legacy-head)
  CLUSTER_NODE_HOSTNAMES=("$local_host")
  CLUSTER_NODE_SSH_HOSTS=(local)
  CLUSTER_NODE_CONTROL_IPS=("$head")
  CLUSTER_NODE_CONTROL_IFS=("${NCCL_SOCKET_IFNAME:-}")
  CLUSTER_NODE_HCAS=("${NCCL_IB_HCA:-}")
  CLUSTER_NODE_RDMA_IFS=("${NCCL_SOCKET_IFNAME:-}")
  CLUSTER_TOPOLOGY_COUNT=1
  CLUSTER_TOPOLOGY_FULL_MESH=1
  CLUSTER_TOPOLOGY_MIN_RAILS=0

  if [ -n "$worker" ]; then
    CLUSTER_NODE_IDS+=(legacy-worker)
    CLUSTER_NODE_HOSTNAMES+=("$worker")
    CLUSTER_NODE_SSH_HOSTS+=("$worker")
    CLUSTER_NODE_CONTROL_IPS+=("$worker")
    CLUSTER_NODE_CONTROL_IFS+=("${NCCL_SOCKET_IFNAME:-}")
    CLUSTER_NODE_HCAS+=("${NCCL_IB_HCA:-}")
    CLUSTER_NODE_RDMA_IFS+=("${NCCL_SOCKET_IFNAME:-}")
    CLUSTER_TOPOLOGY_COUNT=2
    local hca_count=0 hca_csv="${NCCL_IB_HCA:-}" part
    IFS=, read -r -a _legacy_hcas <<<"$hca_csv"
    for part in "${_legacy_hcas[@]}"; do
      [ -n "$part" ] && hca_count=$((hca_count + 1))
    done
    CLUSTER_PAIR_RAILS["0:1"]="$hca_count"
    CLUSTER_TOPOLOGY_MIN_RAILS="$hca_count"
  fi
  return 0
}

load_cluster_topology() {
  [ "$CLUSTER_TOPOLOGY_LOADED" = 0 ] || return 0
  _cluster_topology_reset

  if [ ! -f "$CLUSTER_TOPOLOGY_FILE" ]; then
    if _cluster_load_legacy_topology; then
      CLUSTER_TOPOLOGY_LOADED=1
      return 0
    fi
    CLUSTER_TOPOLOGY_LOADED=1
    return 0
  fi

  local rows
  if ! rows=$(python3 "$_topology_repo/scripts/topology_manifest.py" rows "$CLUSTER_TOPOLOGY_FILE"); then
    echo "topology: invalid manifest $CLUSTER_TOPOLOGY_FILE" >&2
    return 1
  fi

  local kind a b c d e f g h
  while IFS=$'\t' read -r kind a b c d e f g h; do
    case "$kind" in
      META)
        CLUSTER_TOPOLOGY_COUNT="$a"
        CLUSTER_TOPOLOGY_ID="$b"
        CLUSTER_TOPOLOGY_FULL_MESH="$c"
        CLUSTER_TOPOLOGY_MIN_RAILS="$d"
        CLUSTER_TOPOLOGY_SOURCE=manifest
        ;;
      NODE)
        CLUSTER_NODE_IDS["$a"]="$b"
        CLUSTER_NODE_HOSTNAMES["$a"]="$c"
        CLUSTER_NODE_SSH_HOSTS["$a"]="$d"
        CLUSTER_NODE_CONTROL_IPS["$a"]="$e"
        CLUSTER_NODE_CONTROL_IFS["$a"]="$f"
        CLUSTER_NODE_HCAS["$a"]="$g"
        CLUSTER_NODE_RDMA_IFS["$a"]="$h"
        ;;
      LINK)
        CLUSTER_PAIR_RAILS["$a:$b"]="$c"
        ;;
    esac
  done <<<"$rows"

  if [ "${#CLUSTER_NODE_IDS[@]}" -ne "$CLUSTER_TOPOLOGY_COUNT" ]; then
    echo "topology: row count does not match manifest node count" >&2
    return 1
  fi

  # Compatibility aliases for existing two-node scripts and user .env tooling.
  HEAD_IP="${CLUSTER_NODE_CONTROL_IPS[0]:-${HEAD_IP:-}}"
  if [ "$CLUSTER_TOPOLOGY_COUNT" -ge 2 ]; then
    WORKER_IP="${CLUSTER_NODE_SSH_HOSTS[1]}"
  fi
  export HEAD_IP WORKER_IP
  CLUSTER_TOPOLOGY_LOADED=1
}

cluster_node_ssh_host() {
  local rank="${1:?rank required}"
  load_cluster_topology || return 1
  [ "$rank" -ge 0 ] 2>/dev/null || return 1
  [ "$rank" -lt "$CLUSTER_TOPOLOGY_COUNT" ] || return 1
  printf '%s\n' "${CLUSTER_NODE_SSH_HOSTS[$rank]}"
}

cluster_node_control_ip() {
  local rank="${1:?rank required}"
  load_cluster_topology || return 1
  [ "$rank" -ge 0 ] 2>/dev/null || return 1
  [ "$rank" -lt "$CLUSTER_TOPOLOGY_COUNT" ] || return 1
  printf '%s\n' "${CLUSTER_NODE_CONTROL_IPS[$rank]}"
}

cluster_remote_ranks() {
  local limit="${1:-$CLUSTER_TOPOLOGY_COUNT}" rank
  load_cluster_topology || return 1
  [ "$limit" -le "$CLUSTER_TOPOLOGY_COUNT" ] || return 1
  for ((rank = 1; rank < limit; rank++)); do
    printf '%s\n' "$rank"
  done
}

require_cluster_nodes() {
  local required="${1:?required node count}"
  if ! [[ "$required" =~ ^[1-9][0-9]*$ ]]; then
    echo "topology: invalid required node count '$required'" >&2
    return 1
  fi
  load_cluster_topology || return 1
  if [ "$CLUSTER_TOPOLOGY_COUNT" -lt "$required" ]; then
    echo "topology: profile requires exactly $required active node(s), but only $CLUSTER_TOPOLOGY_COUNT confirmed." >&2
    echo "  Run scripts/detect-fabric.sh --write-topology to discover and confirm cluster membership." >&2
    return 1
  fi
}

select_cluster_profile_fabric() {
  local required="${1:?required node count}"
  local rank hcas rdma_ifs fabric_rows
  require_cluster_nodes "$required" || return 1

  CLUSTER_PROFILE_NODE_COUNT=0
  CLUSTER_PROFILE_HCAS=()
  CLUSTER_PROFILE_RDMA_IFS=()
  if [ "$CLUSTER_TOPOLOGY_SOURCE" = manifest ]; then
    if ! fabric_rows=$(python3 "$_topology_repo/scripts/topology_manifest.py" \
        profile-fabric "$CLUSTER_TOPOLOGY_FILE" "$required"); then
      echo "topology: cannot resolve RDMA fabric for $required selected ranks" >&2
      return 1
    fi
    while IFS=$'\t' read -r rank hcas rdma_ifs; do
      [ -n "$rank" ] || continue
      CLUSTER_PROFILE_HCAS["$rank"]="$hcas"
      CLUSTER_PROFILE_RDMA_IFS["$rank"]="$rdma_ifs"
    done <<<"$fabric_rows"
  else
    for ((rank = 0; rank < required; rank++)); do
      CLUSTER_PROFILE_HCAS["$rank"]="${CLUSTER_NODE_HCAS[$rank]:-}"
      CLUSTER_PROFILE_RDMA_IFS["$rank"]="${CLUSTER_NODE_RDMA_IFS[$rank]:-}"
    done
  fi

  if [ "${#CLUSTER_PROFILE_HCAS[@]}" -ne "$required" ]; then
    echo "topology: selected fabric row count does not match $required ranks" >&2
    return 1
  fi
  for ((rank = 0; rank < required; rank++)); do
    [ -n "${CLUSTER_PROFILE_HCAS[$rank]:-}" ] || {
      echo "topology: rank $rank has no HCA in the selected profile fabric" >&2
      return 1
    }
  done
  CLUSTER_PROFILE_NODE_COUNT="$required"
}


require_profile_topology() {
  local required="${1:?required node count}"
  local topology_class="${2:-roce-full-mesh}"
  local min_rails="${3:-2}"
  require_cluster_nodes "$required" || return 1
  [ "$required" -gt 1 ] || return 0

  case "$topology_class" in
    roce-full-mesh) ;;
    *)
      echo "topology: unsupported profile topology class '$topology_class'" >&2
      return 1
      ;;
  esac
  if [ "$CLUSTER_TOPOLOGY_FULL_MESH" != 1 ]; then
    echo "topology: confirmed topology is not a full mesh" >&2
    return 1
  fi

  local a b rails
  for ((a = 0; a < required; a++)); do
    [ -n "${CLUSTER_NODE_HCAS[$a]:-}" ] || {
      echo "topology: rank $a has no active RDMA HCA" >&2
      return 1
    }
    for ((b = a + 1; b < required; b++)); do
      rails="${CLUSTER_PAIR_RAILS["$a:$b"]:-0}"
      if [ "$rails" -lt "$min_rails" ]; then
        echo "topology: ranks $a/$b expose $rails shared RoCE rail(s); profile requires $min_rails" >&2
        return 1
      fi
    done
  done
  select_cluster_profile_fabric "$required" || return 1
}

reload_cluster_topology() {
  CLUSTER_TOPOLOGY_LOADED=0
  load_cluster_topology
}

cluster_topology_summary() {
  load_cluster_topology || return 1
  printf '%s node%s confirmed' "$CLUSTER_TOPOLOGY_COUNT" \
    "$([ "$CLUSTER_TOPOLOGY_COUNT" = 1 ] || printf s)"
  if [ -n "$CLUSTER_TOPOLOGY_ID" ]; then
    printf ' · topology %s' "${CLUSTER_TOPOLOGY_ID:0:12}"
  fi
  printf '\n'
}
