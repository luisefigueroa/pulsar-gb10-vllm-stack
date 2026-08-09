#!/usr/bin/env bash
# Federated model library (Phase 0–1): warm catalog scan, resolve, cleanup hints.
# Does not change replicated defaults or experimental live fabric launch.
set -euo pipefail
SCRIPT_NAME=model-library
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_MODEL_LIBRARY_PY:-$REPO_DIR/scripts/model_library.py}"
LIBRARY_DIR="${MODEL_LIBRARY_DIR:-$REPO_DIR/.model-library}"
CATALOG_FILE="${MODEL_LIBRARY_CATALOG:-$LIBRARY_DIR/catalog.json}"

usage() {
  cat <<'EOF'
Federated model library (warm catalog)

Usage:
  scripts/model-library.sh catalog refresh [--json] [--local-only]
  scripts/model-library.sh catalog list [--validated] [--json]
  scripts/model-library.sh catalog show <model_id|profile> [--json]
  scripts/model-library.sh resolve <profile|model_id> [--json]
  scripts/model-library.sh cleanup-recommend [--json]

Notes:
  • Scans default HF cache hubs on confirmed topology nodes (warm catalog).
  • Labels entries validated vs unvalidated using models/*.conf STATUS.
  • Duplicate complete homes refuse resolve until a primary is chosen later.
  • Cold storage and activate/hot are not implemented in this command set yet.
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
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
