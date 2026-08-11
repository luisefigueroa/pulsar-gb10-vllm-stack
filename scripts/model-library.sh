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
  scripts/model-library.sh validation-bundle verify <profile> [--json]
  scripts/model-library.sh resolve <profile|model_id|/abs/path> [--json] [--no-cold]
  scripts/model-library.sh cleanup-recommend [--json]
  scripts/model-library.sh home check <profile|model_id|model_id@revision>
      [--node RANK|NODE_ID] [--allow-last-home] [--json]
  scripts/model-library.sh home remove <profile|model_id|model_id@revision>
      [--node RANK|NODE_ID] [--allow-last-home] --yes
  scripts/model-library.sh cold scan [--json] [--complete-only] [--root PATH]
  scripts/model-library.sh cold show <model_id|/abs/path> [--json]
  scripts/model-library.sh cold adopt <model_id|profile|/abs/path>
      [--cache-root PATH] [--yes]
  scripts/model-library.sh cold stage-only <profile>
      [--allow-unvalidated] [--yes] [--nodes N]
  scripts/model-library.sh activate <profile>
      [--transport ssh-control|ssh-roce|nfs-rdma] [--backend copy|fabric]
      [--allow-unvalidated] [--yes] [--interactive-sudo] [--time]
      [--copy-streams N]
  scripts/model-library.sh bench-activate <profile> [--tag TAG] [--yes]
      [--interactive-sudo] [--output PATH] [--nodes N]
  scripts/model-library.sh probe-ssh-roce <profile> [--nodes N] [--rail-index N]
  scripts/model-library.sh bench-ssh-roce <profile> [--tag TAG] [--yes]
      [--output PATH] [--nodes N] [--rail-index N]
      [--order control-first|roce-first]
      [--copy-streams N]
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
  • Catalog identity labels distinguish reviewed expected seals from
    legacy-unsealed STATUS claims; local bytes never create expected identity.
    catalog list --validated shows present entries with reviewed expected seals.
    Sealed profiles also require a content-addressed validation bundle whose
    model, image, runtime, geometry, artifact, and evidence contract matches.
    activate refuses legacy-unsealed profiles unless --allow-unvalidated marks
    an explicit experiment; that flag never bypasses a configured seal mismatch.
  • Duplicate complete homes refuse resolve until a primary is chosen.
  • home check/remove probes managed containers and every hot root on all
    confirmed nodes. Any dependent retained view blocks removal, including
    ready, verifying, or pinned hot state. Unobservable nodes fail closed.
    Removal is exact-repository-only, refuses multi-revision hub trees, and
    needs --allow-last-home before deleting the final durable copy.
  • activate --transport ssh-control|ssh-roce selects rsync SSH over the
    confirmed management or RoCE path. RoCE is TCP/IP over the NIC, not RDMA.
    --copy-streams N size-balances HF blobs over independent SSH connections
    (1..16). The current default remains ssh-control with one stream while the
    ssh-roce/8-stream promotion gates are completed.
  • activate full-verifies every rank and creates a rank-local serve witness.
    Unchanged launch checks metadata; drift visibly rehashes or fails closed.
  • activate --backend fabric uses ephemeral NFSv4.2/RDMA over confirmed RoCE
    to fill hot, then releases mounts/export. No silent fallback to copy.
  • bench-activate runs copy then fabric (purge between), writes a JSON report;
    fabric_claims_fast_path only if fabric wall time is strictly less than copy.
    Benchmark/probe commands are explicit experiments and permit legacy-unsealed
    profiles, but a configured expected-seal mismatch still fails closed.
  • probe-ssh-roce / bench-ssh-roce: experimental control SSH vs SSH-over-RoCE
    A/B (no product default change). Requires topology schema-2 SSH enrollment
    and sshd on fabric IPs. Each report is one ordered pair; repeat both
    --order values before any fast-path decision.
  • pin prevents purge. Cold stage-only hot is self-contained; warm-home
    activation currently keeps a home-rank symlink and still needs that home.
  • activate, cold stage-only, pin, and budget observe every selected rank.
    The default preserves max(64 GiB, 5% of filesystem capacity) as available
    space; PULSAR_HOT_BUDGET_BYTES optionally adds a hard cap and
    PULSAR_HOT_RESERVE_BYTES explicitly overrides the reserve. No auto-eviction
    or capacity fallback occurs.
  • Does not change wizard defaults or --weight-source fabric.
EOF
}

cmd_validation_bundle_verify() {
  local profile="${1:-}" json=0
  shift || true
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      *) die "unknown validation-bundle verify option: $1" ;;
    esac
    shift
  done
  [ -n "$profile" ] \
    || die "usage: validation-bundle verify <profile> [--json]"
  load_conf "$profile"
  [ -n "$EXPECTED_MODEL_SEAL" ] \
    || die "$profile has no reviewed expected seal or validation bundle"
  [ -n "$PROFILE_VALIDATION_BUNDLE_JSON" ] \
    || die "$profile validation bundle verification returned no result"
  if [ "$json" = 1 ]; then
    printf '%s\n' "$PROFILE_VALIDATION_BUNDLE_JSON"
    return 0
  fi
  local -a fields=()
  mapfile -t fields < <(
    printf '%s' "$PROFILE_VALIDATION_BUNDLE_JSON" |
      python3 -c '
import json, sys
d = json.load(sys.stdin)
print(d["validation_bundle"]["bundle_id"])
print(d["expected_model_seal"]["seal_id"])
print(d["expected_model_seal"]["snapshot_revision"])
print(d["validation_bundle"]["image_digest"])
print(d["validation_bundle"]["topology_class"])
print(d["validation_bundle"]["nodes"])
print(d["profile_contract_id"])
'
  )
  [ "${#fields[@]}" -eq 7 ] \
    || die "$profile validation bundle result is incomplete"
  render_human_section "Validation bundle" \
    "profile" "$profile" \
    "state" "match" \
    "bundle" "${fields[0]}" \
    "model seal" "${fields[1]}" \
    "revision" "${fields[2]}" \
    "image" "${fields[3]}" \
    "geometry" "${fields[5]} node(s) · ${fields[4]}" \
    "profile contract" "${fields[6]}"
}

# Experiment: control (default) vs roce (rsync -e ssh to fabric IPs).
COPY_SSH_MODE="${PULSAR_COPY_SSH_MODE:-control}"
case "$COPY_SSH_MODE" in
  control|roce) ;;
  *) die "PULSAR_COPY_SSH_MODE must be control or roce" ;;
esac
# Populated by load_copy_ssh_roce_map when COPY_SSH_MODE=roce.
declare -A LIBRARY_SSH_ROCE_IP=()
declare -A LIBRARY_SSH_ROCE_HOSTNAME=()
declare -A LIBRARY_SSH_ROCE_NETDEV=()
LIBRARY_SSH_ROCE_MAP_JSON=""

COPY_STREAMS="${PULSAR_COPY_STREAMS:-1}"
COPY_STREAM_STAGGER_MS="${PULSAR_COPY_STREAM_STAGGER_MS:-150}"

validate_copy_stream_settings() {
  if ! [[ "$COPY_STREAMS" =~ ^[0-9]+$ ]] \
      || [ "$COPY_STREAMS" -lt 1 ] || [ "$COPY_STREAMS" -gt 16 ]; then
    die "PULSAR_COPY_STREAMS/--copy-streams must be between 1 and 16"
  fi
  if ! [[ "$COPY_STREAM_STAGGER_MS" =~ ^[0-9]+$ ]] \
      || [ "$COPY_STREAM_STAGGER_MS" -gt 1000 ]; then
    die "PULSAR_COPY_STREAM_STAGGER_MS must be between 0 and 1000"
  fi
  if [ "$COPY_STREAMS" -gt 8 ] && [ "$COPY_STREAM_STAGGER_MS" -lt 100 ]; then
    die "copy streams above 8 require at least 100ms connection staggering"
  fi
}

validate_copy_stream_settings

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

inspect_catalog_home() {
  local profile="${1:?profile required}" resolved home_rank hub_path node_id
  local expected_node command out model_id revision
  resolved=$(
    python3 "$PY_TOOL" resolve \
      --catalog "$CATALOG_FILE" \
      --profile "$profile" \
      --models-dir "$REPO_DIR/models" \
      --no-cold \
      --json
  ) || return 1
  home_rank=$(printf '%s' "$resolved" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  hub_path=$(printf '%s' "$resolved" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["hub_path"])')
  node_id=$(printf '%s' "$resolved" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["node_id"])')
  model_id=$(printf '%s' "$resolved" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$resolved" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
  [ -n "$revision" ] \
    || die "activate: catalog entry lacks an exact snapshot revision"
  if ! [[ "$home_rank" =~ ^[0-9]+$ ]] \
      || [ "$home_rank" -ge "${CLUSTER_TOPOLOGY_COUNT:-0}" ]; then
    die "activate: catalog home rank is outside confirmed topology"
  fi
  expected_node="${CLUSTER_NODE_IDS[$home_rank]:-}"
  if [ -z "$expected_node" ] || [ "$node_id" != "$expected_node" ]; then
    die "activate: catalog home identity differs from confirmed topology"
  fi
  if [ "$home_rank" = 0 ]; then
    python3 "$PY_TOOL" inspect-hub \
      --hub-path "$hub_path" \
      --rank "$home_rank" \
      --node-id "$node_id" \
      --model-id "$model_id" \
      --revision "$revision"
    return
  fi
  command=$(
    shell_join_q python3 - inspect-hub \
      --hub-path "$hub_path" \
      --rank "$home_rank" \
      --node-id "$node_id" \
      --model-id "$model_id" \
      --revision "$revision"
  )
  if ! out=$(
    ssh_node "$home_rank" "$command" <"$PY_TOOL"
  ); then
    die "rank $home_rank: authoritative hub inspection failed"
  fi
  printf '%s\n' "$out"
}

library_plan_activate() {
  local profile="${1:?profile required}"
  shift
  local home_inventory
  home_inventory=$(inspect_catalog_home "$profile") || return 1
  python3 "$PY_TOOL" plan-activate \
    --catalog "$CATALOG_FILE" \
    --profile "$profile" \
    --models-dir "$REPO_DIR/models" \
    --home-inventory-json "$home_inventory" \
    "$@"
}

hot_budget_policy_args() {
  HOT_BUDGET_POLICY_ARGS=()
  if [ -n "${PULSAR_HOT_BUDGET_BYTES:-}" ]; then
    HOT_BUDGET_POLICY_ARGS+=(--hard-cap-bytes "$PULSAR_HOT_BUDGET_BYTES")
  fi
  if [ -n "${PULSAR_HOT_RESERVE_BYTES:-}" ]; then
    HOT_BUDGET_POLICY_ARGS+=(--reserve-bytes "$PULSAR_HOT_RESERVE_BYTES")
  fi
}

hot_budget_observation_on_rank() {
  local rank="${1:?}" runtime_source="${2:?}" required_owned_bytes="${3:?}"
  local replacing_path="${4:-}" node_id hostname command out
  local -a args=(
    budget-admission
    --hot-root "$HOT_ROOT"
    --rank "$rank"
    --node-id "${CLUSTER_NODE_IDS[$rank]:-}"
    --hostname "${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
    --runtime-source "$runtime_source"
    --required-owned-bytes "$required_owned_bytes"
    --compact
  )
  node_id="${CLUSTER_NODE_IDS[$rank]:-}"
  hostname="${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
  [ -n "$node_id" ] || die "rank $rank: hot budget node identity is missing"
  [ -n "$hostname" ] || die "rank $rank: hot budget hostname is missing"
  [ -z "$replacing_path" ] || args+=(--replacing-path "$replacing_path")
  hot_budget_policy_args
  args+=("${HOT_BUDGET_POLICY_ARGS[@]}")
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" "${args[@]}"
    return
  fi
  command=$(shell_join_q python3 - "${args[@]}")
  if ! out=$(ssh_node "$rank" "$command" <"$PY_TOOL"); then
    die "rank $rank: hot budget observation failed"
  fi
  printf '%s\n' "$out"
}

merge_hot_budget_observation_file() {
  local observations_file="${1:?}" expected_ranks="${2:?}" mode="${3:?}"
  local profile="${4:-}" model_id="${5:-}" bytes_logical="${6:-0}"
  local -a args=(
    merge-budget-admissions
    --observations-file "$observations_file"
    --expected-ranks "$expected_ranks"
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --mode "$mode"
    --bytes-logical "$bytes_logical"
  )
  [ -z "$profile" ] || args+=(--profile "$profile")
  [ -z "$model_id" ] || args+=(--model-id "$model_id")
  python3 "$PY_TOOL" "${args[@]}"
}

build_hot_budget_plan_from_activation() {
  local activation_plan="${1:?}" mode="${2:-activate}"
  local metadata profile model_id bytes_logical
  local rank runtime_source required_owned_bytes replacing_path observation
  local observations_file expected_ranks="" rc=0
  metadata=$(printf '%s' "$activation_plan" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print("%s\t%s\t%s" % (
    d.get("profile") or "",
    d.get("model_id") or "",
    d.get("bytes_logical") or 0,
))
')
  IFS=$'\t' read -r profile model_id bytes_logical <<<"$metadata"
  observations_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-hot-budget.XXXXXX")
  while IFS=$'\t' read -r rank runtime_source required_owned_bytes replacing_path; do
    [ -n "$rank" ] || continue
    if ! observation=$(hot_budget_observation_on_rank "$rank" "$runtime_source" "$required_owned_bytes" "$replacing_path"); then
      rm -f "$observations_file"
      return 1
    fi
    printf '%s\n' "$observation" >>"$observations_file"
    if [ -n "$expected_ranks" ]; then
      expected_ranks+=","
    fi
    expected_ranks+="$rank"
  done < <(printf '%s' "$activation_plan" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for item in d.get("hot_storage_requirements") or []:
    print("%s\t%s\t%s\t%s" % (
        item["rank"],
        item["runtime_source"],
        item["required_owned_bytes"],
        item.get("replacing_path") or "",
    ))
')
  [ -n "$expected_ranks" ] || {
    rm -f "$observations_file"
    die "hot admission plan has no target ranks"
  }
  merge_hot_budget_observation_file "$observations_file" "$expected_ranks" "$mode" "$profile" "$model_id" "$bytes_logical" || rc=$?
  rm -f "$observations_file"
  return "$rc"
}

build_zero_hot_budget_plan() {
  local mode="${1:?}" profile="${2:-}" rank_count="${3:?}"
  local observations_file expected_ranks="" observation rank rc=0
  observations_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-hot-budget.XXXXXX")
  for ((rank = 0; rank < rank_count; rank++)); do
    if ! observation=$(hot_budget_observation_on_rank "$rank" "$mode" 0 ""); then
      rm -f "$observations_file"
      return 1
    fi
    printf '%s\n' "$observation" >>"$observations_file"
    if [ -n "$expected_ranks" ]; then
      expected_ranks+=","
    fi
    expected_ranks+="$rank"
  done
  merge_hot_budget_observation_file "$observations_file" "$expected_ranks" "$mode" "$profile" "" 0 || rc=$?
  rm -f "$observations_file"
  return "$rc"
}

render_hot_budget_plan_json() {
  local plan="${1:?}"
  printf '%s' "$plan" | python3 "$PY_TOOL" render-budget-plan --plan-file /dev/stdin
}

hot_budget_plan_is_eligible() {
  local plan="${1:?}"
  [ "$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')" = eligible ]
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

validate_catalog_profile_contracts() {
  local conf profile
  for conf in "$REPO_DIR"/models/*.conf; do
    [ -e "$conf" ] || continue
    profile="${conf##*/}"
    profile="${profile%.conf}"
    # A sealed profile is validated here from its sourced Bash values. The
    # Python catalog parser deliberately handles only the small declarative
    # subset and cannot reconstruct arrays, defaults, or resolved image state.
    load_conf "$profile"
  done
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
  validate_catalog_profile_contracts
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

  local plan action expected_validation_json rank budget_plan
  plan=$(python3 "$PY_TOOL" "${plan_args[@]}")
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  expected_validation_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["validation"], sort_keys=True, separators=(",", ":")))')
  budget_plan=$(build_hot_budget_plan_from_activation "$plan" cold-stage-only) \
    || die "cold stage-only: all-rank hot admission failed"
  render_hot_budget_plan_json "$budget_plan"
  hot_budget_plan_is_eligible "$budget_plan" \
    || die "cold stage-only: hot admission is blocked; no bytes were changed"
  if [ "$action" = "skip" ]; then
    log "hot already ready for $profile (stage-only skip) — verifying ranks"
    for ((rank = 0; rank < nodes; rank++)); do
      verify_hot_on_rank "$rank" \
        "$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')" \
        "$profile" "${CLUSTER_TOPOLOGY_ID}" 0 "$expected_validation_json" \
        || die "rank $rank: stage-only hot verify failed"
    done
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
    local stamp_json verifying_stamp_json
    stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
    verifying_stamp_json=$(printf '%s' "$stamp_json" | python3 -c '
import json,sys
d=json.load(sys.stdin)
d["state"]="verifying"
print(json.dumps(d))
')
    for ((rank = 1; rank < nodes; rank++)); do
      copy_hub_to_rank "$rank" "$hub_dest" "$hub_dest" 0
      write_stamp_on_rank "$rank" "$instance" "$verifying_stamp_json"
      verify_hot_on_rank "$rank" "$instance" "$profile" \
        "${CLUSTER_TOPOLOGY_ID}" 1 "$expected_validation_json"
      write_stamp_on_rank "$rank" "$instance" "$stamp_json"
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

home_removal_target_json() {
  local query="${1:?}" node_selector="${2:-}"
  local -a args=(
    resolve-home-removal-target
    --catalog "$CATALOG_FILE"
  )
  [ -z "$node_selector" ] || args+=(--node "$node_selector")
  args+=("$query")
  python3 "$PY_TOOL" "${args[@]}"
}

inspect_removable_home_on_rank() {
  local rank="${1:?}" cache_root="${2:?}" hub_path="${3:?}"
  local model_id="${4:?}" revision="${5:?}" node_id="${6:?}"
  local command
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" inspect-removable-home \
      --cache-root "$cache_root" \
      --hub-path "$hub_path" \
      --model-id "$model_id" \
      --revision "$revision" \
      --rank "$rank" \
      --node-id "$node_id"
    return
  fi
  command=$(shell_join_q python3 - inspect-removable-home \
    --cache-root "$cache_root" \
    --hub-path "$hub_path" \
    --model-id "$model_id" \
    --revision "$revision" \
    --rank "$rank" \
    --node-id "$node_id")
  ssh_node "$rank" "$command" <"$PY_TOOL"
}

scan_home_hot_references_on_rank() {
  local rank="${1:?}" node_id="${2:?}" command
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" scan-home-hot-references \
      --hot-root "$HOT_ROOT" \
      --rank "$rank" \
      --node-id "$node_id"
    return
  fi
  command=$(shell_join_q python3 - scan-home-hot-references \
    --hot-root "$HOT_ROOT" \
    --rank "$rank" \
    --node-id "$node_id")
  ssh_node "$rank" "$command" <"$PY_TOOL"
}

collect_managed_container_metadata_on_rank() {
  local rank="${1:?}" destination="${2:?}" ids id metadata rc host
  : >"$destination"
  if [ "$rank" = 0 ]; then
    ids=$(list_managed_container_ids_local) \
      || die "home removal: cannot enumerate managed containers on rank 0"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]:-}"
    [ -n "$host" ] || die "home removal: rank $rank has no confirmed SSH host"
    ids=$(list_managed_container_ids_remote "$host") \
      || die "home removal: rank $rank is unreachable or Docker is unavailable"
  fi
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    rc=0
    if [ "$rank" = 0 ]; then
      metadata=$(container_ownership_inspect_local "$id") || rc=$?
    else
      metadata=$(container_ownership_inspect_remote "$host" "$id") || rc=$?
    fi
    if [ "$rc" -eq 3 ]; then
      continue
    fi
    [ "$rc" -eq 0 ] \
      || die "home removal: rank $rank container metadata became unobservable"
    printf '%s\n' "$metadata" >>"$destination"
  done <<<"$ids"
}

build_home_removal_plan() (
  set -euo pipefail
  local query="${1:?}" node_selector="${2:-}" allow_last_home="${3:-0}"
  local target tmp rank node_id home_rank home_node_id cache_root hub_path
  local model_id revision
  local -a plan_args
  require_py
  ensure_catalog
  load_cluster_topology >/dev/null \
    || die "home removal: confirmed topology required"
  target=$(home_removal_target_json "$query" "$node_selector")
  home_rank=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  home_node_id=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["home"]["node_id"])')
  cache_root=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["home"]["cache_root"])')
  hub_path=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["home"]["hub_path"])')
  model_id=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$target" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["revision"])')
  if ! [[ "$home_rank" =~ ^[0-9]+$ ]] \
      || [ "$home_rank" -ge "$CLUSTER_TOPOLOGY_COUNT" ]; then
    die "home removal: catalog home rank is outside confirmed topology"
  fi
  [ "${CLUSTER_NODE_IDS[$home_rank]:-}" = "$home_node_id" ] \
    || die "home removal: catalog home node differs from confirmed topology"

  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-removal.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT INT TERM
  inspect_removable_home_on_rank \
    "$home_rank" "$cache_root" "$hub_path" "$model_id" "$revision" \
    "$home_node_id" >"$tmp/inspection.json" \
    || die "home removal: authoritative home inspection failed"

  for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    node_id="${CLUSTER_NODE_IDS[$rank]:-}"
    [ -n "$node_id" ] || die "home removal: rank $rank lacks a node ID"
    scan_home_hot_references_on_rank "$rank" "$node_id" \
      >"$tmp/hot-$rank.json" \
      || die "home removal: rank $rank hot references are unobservable"
    collect_managed_container_metadata_on_rank \
      "$rank" "$tmp/containers-$rank.jsonl"
  done

  plan_args=(
    plan-home-removal
    --catalog "$CATALOG_FILE"
    --topology-file "$CLUSTER_TOPOLOGY_FILE"
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --models-dir "$REPO_DIR/models"
    --inspection-file "$tmp/inspection.json"
    --observations-dir "$tmp"
  )
  [ -z "$node_selector" ] || plan_args+=(--node "$node_selector")
  [ "$allow_last_home" = 0 ] || plan_args+=(--allow-last-home)
  plan_args+=("$query")
  python3 "$PY_TOOL" "${plan_args[@]}"
)

render_home_removal_plan_json() {
  local plan="${1:?}"
  python3 "$PY_TOOL" render-home-removal-plan --plan-json "$plan"
}

execute_home_removal_on_rank() {
  local plan="${1:?}" rank node_id command
  rank=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["home"]["rank"])')
  node_id=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["home"]["node_id"])')
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" execute-home-removal \
      --plan-json "$plan" --rank "$rank" --node-id "$node_id"
    return
  fi
  command=$(shell_join_q python3 - execute-home-removal \
    --plan-json "$plan" --rank "$rank" --node-id "$node_id")
  ssh_node "$rank" "$command" <"$PY_TOOL"
}

cmd_home_check() {
  local query="" node_selector="" allow_last_home=0 json=0 plan state
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --allow-last-home) allow_last_home=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] \
    || die "usage: home check <profile|model_id|identity> [--node RANK|NODE_ID]"
  plan=$(build_home_removal_plan "$query" "$node_selector" "$allow_last_home")
  if [ "$json" = 1 ]; then
    printf '%s\n' "$plan"
  else
    render_home_removal_plan_json "$plan"
  fi
  state=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["state"])')
  [ "$state" = eligible ]
}

cmd_home_remove() {
  local query="" node_selector="" allow_last_home=0 yes=0 plan state
  local result_file
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --allow-last-home) allow_last_home=1 ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] \
    || die "usage: home remove <profile|model_id|identity> [--node RANK|NODE_ID] --yes"
  plan=$(build_home_removal_plan "$query" "$node_selector" "$allow_last_home")
  render_home_removal_plan_json "$plan"
  state=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["state"])')
  [ "$state" = eligible ] \
    || die "home removal is blocked; no durable content was changed"
  [ "$yes" = 1 ] \
    || die "home removal requires --yes after reviewing the eligible plan"

  result_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-home-removed.XXXXXX")
  trap 'rm -f "$result_file"' RETURN
  execute_home_removal_on_rank "$plan" >"$result_file" \
    || die "home removal failed; inspect the reported retirement path before retrying"
  if ! cmd_catalog_refresh >/dev/null; then
    die "home was removed, but catalog refresh failed; run catalog refresh before serving"
  fi
  python3 "$PY_TOOL" render-home-removal-result --result-file "$result_file"
}

# Keep the confirmed control endpoint as the SSH identity. Transfer modes
# override only HostName, so Host/User/IdentityFile matching and the saved
# host-key identity remain tied to the confirmed topology alias.
copy_ssh_data_host() {
  local rank="${1:?}"
  local host
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]:-}"
  [ -n "$host" ] || die "rank $rank: missing control ssh host"
  if [ "$COPY_SSH_MODE" = roce ]; then
    [ -n "${LIBRARY_SSH_ROCE_IP[$rank]:-}" ] \
      || die "rank $rank: no RoCE IP in confirmed map"
  fi
  printf '%s\n' "$host"
}

copy_ssh_rsync_shell() {
  local rank="${1:?}" identity_host host_key_alias transport_ip
  local -a ssh_argv=("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}")
  identity_host=$(copy_ssh_data_host "$rank")
  host_key_alias="${identity_host##*@}"
  host_key_alias="${host_key_alias#[}"
  host_key_alias="${host_key_alias%]}"
  [ -n "$host_key_alias" ] || die "rank $rank: empty SSH host-key alias"
  if [ "$COPY_SSH_MODE" = roce ]; then
    transport_ip="${LIBRARY_SSH_ROCE_IP[$rank]:-}"
  else
    transport_ip="${CLUSTER_NODE_CONTROL_IPS[$rank]:-}"
    if [ -z "$transport_ip" ]; then
      die "rank $rank: no control IP in confirmed topology"
    fi
  fi
  ssh_argv+=(
    -o "HostName=$transport_ip"
    -o "HostKeyAlias=$host_key_alias"
    -o StrictHostKeyChecking=yes
    -o CheckHostIP=no
    -o UpdateHostKeys=no
  )
  shell_join_q "${ssh_argv[@]}"
}

verify_copy_ssh_roce_route() {
  local remote_rank="${1:?}" remote_ip local_ip local_netdev route_json
  [ "$COPY_SSH_MODE" = roce ] || return 0
  [ "$remote_rank" != 0 ] || die "RoCE bulk endpoint must be a remote rank"
  remote_ip="${LIBRARY_SSH_ROCE_IP[$remote_rank]:-}"
  local_ip="${LIBRARY_SSH_ROCE_IP[0]:-}"
  local_netdev="${LIBRARY_SSH_ROCE_NETDEV[0]:-}"
  if [ -z "$remote_ip" ] || [ -z "$local_ip" ] || [ -z "$local_netdev" ]; then
    die "rank $remote_rank: incomplete confirmed RoCE route identity"
  fi
  command -v ip >/dev/null 2>&1 || die "iproute2 is required for ssh-roce"
  if ! route_json=$(ip -j route get "$remote_ip" 2>/dev/null); then
    die "rank $remote_rank: cannot resolve live route to confirmed RoCE IP"
  fi
  python3 "$PY_TOOL" validate-ssh-roce-route \
    --route-json "$route_json" \
    --remote-ip "$remote_ip" \
    --expected-netdev "$local_netdev" \
    --expected-source-ip "$local_ip" >/dev/null \
    || die "rank $remote_rank: live route does not match confirmed RoCE rail"
}

load_copy_ssh_roce_map() {
  local home_rank="${1:?}" nodes="${2:?}" rail_index="${3:-0}"
  local map ranks_csv r ip control_host netdev expected
  require_topology_ssh_trust \
    || die "ssh-roce requires topology-bound SSH identity enrollment"
  ranks_csv=$(seq -s, 0 $((nodes - 1)))
  map=$(python3 "$PY_TOOL" ssh-roce-map \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --home-rank "$home_rank" \
    --nodes "$nodes" \
    --ranks "$ranks_csv" \
    --rail-index "$rail_index") \
    || die "ssh-roce-map failed"
  LIBRARY_SSH_ROCE_MAP_JSON="$map"
  LIBRARY_SSH_ROCE_IP=()
  LIBRARY_SSH_ROCE_HOSTNAME=()
  LIBRARY_SSH_ROCE_NETDEV=()
  while IFS=$'\t' read -r r ip control_host netdev; do
    [ -n "$r" ] || continue
    expected="${CLUSTER_NODE_SSH_HOSTS[$r]:-}"
    if [ -z "$ip" ] || [ -z "$control_host" ] || [ -z "$netdev" ]; then
      die "rank $r: incomplete ssh-roce map entry"
    fi
    [ "$control_host" = "$expected" ] \
      || die "rank $r: ssh-roce map control identity differs from confirmed topology"
    LIBRARY_SSH_ROCE_IP["$r"]="$ip"
    LIBRARY_SSH_ROCE_NETDEV["$r"]="$netdev"
  done < <(printf '%s' "$map" | python3 -c '
import json,sys
m=json.load(sys.stdin)
for _k,v in sorted((m.get("ranks") or {}).items(), key=lambda kv: int(kv[0])):
    print("%s\t%s\t%s\t%s" % (
        v["rank"], v["roce_ip"], v.get("control_ssh_host") or "",
        v.get("roce_netdev") or "",
    ))
')
  log "copy SSH mode=roce rail=$rail_index map ranks=${!LIBRARY_SSH_ROCE_IP[*]}"
}

partition_hub_blobs_on_rank() {
  local rank="${1:?}" hub_source="${2:?}" streams="${3:?}"
  local command out
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" partition-blobs \
      --hub-path "$hub_source" \
      --streams "$streams"
    return
  fi
  command=$(shell_join_q python3 - partition-blobs \
    --hub-path "$hub_source" \
    --streams "$streams")
  if ! out=$(ssh_node "$rank" "$command" <"$PY_TOOL"); then
    die "rank $rank: blob stream planning failed"
  fi
  printf '%s\n' "$out"
}

# Parallel HF hub transfer. Metadata and snapshot symlinks are copied once;
# immutable blobs are balanced across independent SSH/rsync connections.
# direction: pull (remote source → rank 0) | push (rank 0 → remote target).
parallel_rsync_hub() (
  set -euo pipefail
  local direction="${1:?}" source_rank="${2:?}" remote_rank="${3:?}"
  local data_host="${4:?}" hub_source="${5:?}" hub_dest="${6:?}"
  local plan effective stream list_file stream_fail=0 stagger_seconds=0
  local list_dir="" rshell pid
  local -a pids=()

  # Invoked indirectly by the signal/EXIT traps below.
  # shellcheck disable=SC2317
  cleanup_parallel_rsync() {
    local rc=$? child
    trap - EXIT INT TERM
    for child in "${pids[@]:-}"; do
      kill "$child" 2>/dev/null || true
    done
    for child in "${pids[@]:-}"; do
      wait "$child" 2>/dev/null || true
    done
    [ -z "$list_dir" ] || rm -rf -- "$list_dir"
    exit "$rc"
  }
  trap cleanup_parallel_rsync EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  plan=$(partition_hub_blobs_on_rank "$source_rank" "$hub_source" "$COPY_STREAMS")
  effective=$(printf '%s' "$plan" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["effective_streams"])')
  [ "$effective" -ge 1 ] || die "parallel rsync plan has no streams"
  rshell=$(copy_ssh_rsync_shell "$remote_rank")
  list_dir=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-blob-streams.XXXXXX")

  case "$direction" in
    pull)
      rsync -a --delete --exclude='/blobs/***' -e "$rshell" \
        "${data_host}:${hub_source}/" "$hub_dest/" &
      ;;
    push)
      rsync -a --delete --exclude='/blobs/***' -e "$rshell" \
        "$hub_source/" "${data_host}:${hub_dest}/" &
      ;;
    *) die "parallel rsync direction must be pull or push" ;;
  esac
  pids=("$!")
  if ! wait "${pids[0]}"; then
    pids=()
    die "parallel rsync metadata transfer failed"
  fi
  pids=()
  if [ "$effective" -gt 8 ]; then
    printf -v stagger_seconds '%d.%03d' \
      "$((COPY_STREAM_STAGGER_MS / 1000))" \
      "$((COPY_STREAM_STAGGER_MS % 1000))"
  fi

  for ((stream = 0; stream < effective; stream++)); do
    list_file="$list_dir/$stream.files"
    python3 -c '
import json, sys
plan = json.load(sys.stdin)
print("\n".join(plan["groups"][int(sys.argv[1])]["files"]))
' "$stream" <<<"$plan" >"$list_file"
    case "$direction" in
      pull)
        rsync -a --files-from="$list_file" -e "$rshell" \
          "${data_host}:${hub_source}/" "$hub_dest/" &
        ;;
      push)
        rsync -a --files-from="$list_file" -e "$rshell" \
          "$hub_source/" "${data_host}:${hub_dest}/" &
        ;;
    esac
    pids+=("$!")
    if [ "$stagger_seconds" != 0 ] && [ "$stream" -lt $((effective - 1)) ]; then
      sleep "$stagger_seconds"
    fi
  done
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      stream_fail=1
    fi
  done
  pids=()
  [ "$stream_fail" = 0 ] \
    || die "parallel rsync failed on one or more blob streams"
  log "parallel rsync complete requested=$COPY_STREAMS effective=$effective direction=$direction"
)

# Copy hub tree from source path to dest path on a target rank.
# Source is always read from the home node (controller uses local path or ssh).
# Bulk rsync keeps the control alias as identity and may bind HostName to RoCE.
copy_hub_to_rank() {
  local target_rank="${1:?}" hub_source="${2:?}" hub_dest="${3:?}" home_rank="${4:?}"
  local home_host dest_host dest_parent qdst mode_tag home_rshell dest_rshell

  mode_tag="ssh_${COPY_SSH_MODE}"
  if [ "$COPY_STREAMS" -gt 1 ]; then
    mode_tag+="_streams${COPY_STREAMS}"
  fi

  # Home rank: zero-copy symlink / reflink when possible.
  if [ "$target_rank" = "$home_rank" ]; then
    materialize_tree_on_rank "$target_rank" "$hub_source" "$hub_dest" auto
    return 0
  fi

  dest_parent=$(dirname "$hub_dest")
  if [ "$target_rank" = 0 ]; then
    mkdir -p "$dest_parent"
    rm -rf "$hub_dest"
    mkdir -p "$hub_dest"
    if [ "$home_rank" = 0 ]; then
      # unreachable: home_rank==0 handled above
      materialize_tree_on_rank 0 "$hub_source" "$hub_dest" force-copy
    else
      home_host=$(copy_ssh_data_host "$home_rank")
      verify_copy_ssh_roce_route "$home_rank"
      if [ "$COPY_STREAMS" -gt 1 ]; then
        parallel_rsync_hub pull "$home_rank" "$home_rank" "$home_host" \
          "$hub_source" "$hub_dest"
      else
        home_rshell=$(copy_ssh_rsync_shell "$home_rank")
        rsync -a --delete -e "$home_rshell" \
          "${home_host}:${hub_source}/" "$hub_dest"/
      fi
      log "rank 0 materialize=rsync_${mode_tag} from home rank $home_rank host=$home_host"
    fi
    return 0
  fi

  # Remote non-home target (control-plane ssh_node for mkdir; rsync may use RoCE IP)
  qdst=$(printf '%q' "$hub_dest")
  ssh_node "$target_rank" "mkdir -p $(printf '%q' "$dest_parent") && rm -rf $qdst && mkdir -p $qdst"
  dest_host=$(copy_ssh_data_host "$target_rank")
  verify_copy_ssh_roce_route "$target_rank"
  dest_rshell=$(copy_ssh_rsync_shell "$target_rank")
  if [ "$home_rank" = 0 ]; then
    if [ "$COPY_STREAMS" -gt 1 ]; then
      parallel_rsync_hub push 0 "$target_rank" "$dest_host" "$hub_source" "$hub_dest"
    else
      rsync -a --delete -e "$dest_rshell" \
        "$hub_source"/ "${dest_host}:${hub_dest}/"
    fi
    log "rank $target_rank materialize=rsync_${mode_tag} from rank 0 host=$dest_host"
  else
    # home remote -> target remote (pull to controller temp then push)
    if [ "$COPY_STREAMS" -gt 1 ]; then
      die "parallel copy does not support remote-home to remote-target relay"
    fi
    home_host=$(copy_ssh_data_host "$home_rank")
    verify_copy_ssh_roce_route "$home_rank"
    home_rshell=$(copy_ssh_rsync_shell "$home_rank")
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-hot-stage.XXXXXX")
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    rsync -a --delete -e "$home_rshell" \
      "${home_host}:${hub_source}/" "$tmp"/
    rsync -a --delete -e "$dest_rshell" \
      "$tmp"/ "${dest_host}:${hub_dest}/"
    log "rank $target_rank materialize=rsync_via_controller_${mode_tag} home=$home_host dest=$dest_host"
  fi
}

write_stamp_on_rank() {
  local rank="${1:?}" instance_dir="${2:?}" stamp_json="${3:?}"
  local stamp_file rshell
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
  rshell=$(copy_ssh_rsync_shell "$rank")
  rsync -a -e "$rshell" \
    "$stamp_file" "${CLUSTER_NODE_SSH_HOSTS[$rank]}:${instance_dir}/.pulsar/hot.json.tmp"
  # Use remote python to atomic-replace via write-hot-stamp from stdin module
  ssh_node "$rank" \
    "python3 - write-hot-stamp --instance-dir $(printf '%q' "$instance_dir") --stamp-file $(printf '%q' "$instance_dir/.pulsar/hot.json.tmp")" \
    <"$PY_TOOL" >/dev/null
}

verify_hot_on_rank() {
  local rank="${1:?}" instance_dir="${2:?}" profile="${3:?}" topology_id="${4:?}"
  local allow_verifying="${5:-0}" expected_validation_json="${6:-}" command
  local -a verify_args=(
    verify-hot
    --instance-dir "$instance_dir"
    --profile "$profile"
    --topology-id "$topology_id"
    --workers "${PULSAR_INTEGRITY_WORKERS:-8}"
    --refresh-witness
  )
  [ "$allow_verifying" = 1 ] && verify_args+=(--allow-verifying)
  [ -n "$expected_validation_json" ] \
    && verify_args+=(--expected-validation-json "$expected_validation_json")
  if [ "$rank" = 0 ]; then
    verify_args+=(--models-dir "$REPO_DIR/models")
    python3 "$PY_TOOL" "${verify_args[@]}" >/dev/null
    return 0
  fi
  command=$(shell_join_q python3 - "${verify_args[@]}")
  ssh_node "$rank" "$command" <"$PY_TOOL" >/dev/null
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

# Run a multi-line root script with a single sudo (batched privilege).
library_node_root_script() {
  local rank="${1:?}" root_script="${2:?}"
  local payload command host
  payload=$(printf '%s' "$root_script" | base64 -w 0)
  if [ "$LIBRARY_SUDO_MODE" = interactive ]; then
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
    printf '%s' "$root_script" | sudo -n bash -s
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    printf '%s' "$root_script" \
      | "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "sudo -n bash -s"
  fi
}

# Optional phase log: ACTIVATE_PHASE_LOG is tab-separated name\tseconds lines.
phase_record() {
  local name="${1:?}" seconds="${2:?}"
  log "phase ${name}=${seconds}s"
  if [ -n "${ACTIVATE_PHASE_LOG:-}" ]; then
    # flock if available (parallel rank materialize)
    if command -v flock >/dev/null 2>&1; then
      (
        flock 9
        printf '%s\t%s\n' "$name" "$seconds" >>"$ACTIVATE_PHASE_LOG"
      ) 9>>"$ACTIVATE_PHASE_LOG"
    else
      printf '%s\t%s\n' "$name" "$seconds" >>"$ACTIVATE_PHASE_LOG"
    fi
  fi
}

# Materialize source tree into hub_dest on rank.
# mode: auto | force-copy
# For durable home on the same rank (not under .transfer/), prefer symlink (zero-copy)
# then reflink, then cp -a, then rsync.
materialize_tree_on_rank() {
  local rank="${1:?}" source="${2:?}" hub_dest="${3:?}" mode="${4:-auto}"
  local dest_parent qsrc qdst qparent home_local=0 method
  dest_parent=$(dirname "$hub_dest")
  qsrc=$(printf '%q' "$source")
  qdst=$(printf '%q' "$hub_dest")
  qparent=$(printf '%q' "$dest_parent")

  if [ "$mode" = auto ] && [[ "$source" != *"/.transfer/"* ]]; then
    home_local=1
  fi

  # Local rank 0
  if [ "$rank" = 0 ]; then
    mkdir -p "$dest_parent"
    rm -rf "$hub_dest"
    if [ "$home_local" = 1 ]; then
      if ln -sfn "$source" "$hub_dest" 2>/dev/null; then
        log "rank 0 materialize=symlink_home → $hub_dest"
        return 0
      fi
      if cp -a --reflink=always "$source" "$hub_dest" 2>/dev/null; then
        log "rank 0 materialize=reflink → $hub_dest"
        return 0
      fi
      if cp -a --reflink=auto "$source" "$hub_dest" 2>/dev/null; then
        log "rank 0 materialize=cp_reflink_auto → $hub_dest"
        return 0
      fi
      if cp -a "$source" "$hub_dest" 2>/dev/null; then
        log "rank 0 materialize=cp_a → $hub_dest"
        return 0
      fi
    fi
    mkdir -p "$hub_dest"
    rsync -a --delete "$source"/ "$hub_dest"/
    log "rank 0 materialize=rsync → $hub_dest"
    return 0
  fi

  # Remote rank: single ssh script for materialize
  if [ "$home_local" = 1 ]; then
    ssh_node "$rank" \
      "set -euo pipefail
       mkdir -p $qparent
       rm -rf $qdst
       if ln -sfn $qsrc $qdst 2>/dev/null; then echo symlink_home; exit 0; fi
       if cp -a --reflink=always $qsrc $qdst 2>/dev/null; then echo reflink; exit 0; fi
       if cp -a --reflink=auto $qsrc $qdst 2>/dev/null; then echo cp_reflink_auto; exit 0; fi
       if cp -a $qsrc $qdst 2>/dev/null; then echo cp_a; exit 0; fi
       mkdir -p $qdst
       rsync -a --delete $qsrc/ $qdst/
       echo rsync" \
      | { read -r method || method=rsync; log "rank $rank materialize=${method} → $hub_dest"; }
    return 0
  fi

  # NFS mount / remote path: fresh dest + rsync (or cp -a)
  ssh_node "$rank" \
    "set -euo pipefail
     mkdir -p $qparent
     rm -rf $qdst
     mkdir -p $qdst
     if cp -a $qsrc/. $qdst/ 2>/dev/null; then echo cp_a; exit 0; fi
     rsync -a --delete $qsrc/ $qdst/
     echo rsync" \
    | { read -r method || method=rsync; log "rank $rank materialize=${method} → $hub_dest"; }
}

# Copy into hot using per-rank source path (local hub or transfer mount).
copy_from_source_on_rank() {
  local rank="${1:?}" source="${2:?}" hub_dest="${3:?}"
  materialize_tree_on_rank "$rank" "$source" "$hub_dest" auto
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

  local portlist_re export_b64 nfs_b64 owner_script client_script
  portlist_re="(rdma[[:space:]]+${port}|${port}[[:space:]]+rdma|rdma.*${port}|${port}.*rdma)"

  log "installing ephemeral export on home rank $home_rank (batched)"
  export_b64=$(printf '%s' "$export_line" | base64 -w 0)
  nfs_b64=$(printf '%s' "$nfs_conf" | base64 -w 0)
  owner_script=$'set -euo pipefail\n'
  owner_script+="printf '%s' $(printf '%q' "$export_b64") | base64 -d | install -D -m 0644 /dev/stdin $(printf '%q' "$export_file")"$'\n'
  owner_script+="printf '%s' $(printf '%q' "$nfs_b64") | base64 -d | install -D -m 0644 /dev/stdin $(printf '%q' "$nfs_file")"$'\n'
  owner_script+=$'modprobe svcrdma 2>/dev/null || true\n'
  owner_script+=$'exportfs -ra\n'
  owner_script+="if ! { test -r /proc/fs/nfsd/portlist && grep -Eq $(printf '%q' "$portlist_re") /proc/fs/nfsd/portlist; }; then"$'\n'
  owner_script+=$'  systemctl enable --now nfs-server\n'
  owner_script+=$'  systemctl restart nfs-server\n'
  owner_script+=$'fi\n'
  owner_script+="for i in \$(seq 1 30); do test -r /proc/fs/nfsd/portlist && grep -Eq $(printf '%q' "$portlist_re") /proc/fs/nfsd/portlist && exit 0; sleep 1; done"$'\n'
  owner_script+="echo 'NFS/RDMA port ${port} not in /proc/fs/nfsd/portlist after export' >&2; exit 1"$'\n'
  library_node_root_script "$home_rank" "$owner_script" \
    || die "home rank $home_rank: fabric export setup failed (try --interactive-sudo)"

  log "mounting RoCE clients (batched per rank)"
  while IFS=$'\t' read -r rank server_ip client_ip mount_path; do
    [ -n "$rank" ] || continue
    log "  mount rank $rank  $server_ip:$export_path → $mount_path"
    client_script=$'set -euo pipefail\n'
    client_script+="mkdir -p $(printf '%q' "$mount_path")"$'\n'
    client_script+="if findmnt -rn -M $(printf '%q' "$mount_path") >/dev/null 2>&1; then umount $(printf '%q' "$mount_path") || true; fi"$'\n'
    client_script+="mount -t nfs4 -o $(printf '%q' "$mount_options") $(printf '%q' "${server_ip}:${export_path}") $(printf '%q' "$mount_path")"$'\n'
    client_script+="findmnt -rn -M $(printf '%q' "$mount_path") -o OPTIONS | grep -q 'proto=rdma'"$'\n'
    library_node_root_script "$rank" "$client_script" \
      || die "rank $rank: NFS/RDMA mount failed (refusing TCP fallback)"
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
  # NODES is supplied by the sourced profile through load_conf above.
  # shellcheck disable=SC2153
  plan=$(library_plan_activate "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --hot-root "$HOT_ROOT" \
    --backend fabric \
    --allow-unvalidated \
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
  local profile="" backend=copy backend_explicit=0 transport=""
  local allow_unvalidated=0 yes=0 time_it=0
  local plan stamp_json verifying_stamp_json expected_validation_json budget_plan
  local instance hub_source hub_dest home_rank rank source
  local expected_backend requested_mode
  local start_ts end_ts elapsed
  local -a target_ranks=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --backend)
        [ $# -ge 2 ] || die "--backend requires copy or fabric"
        backend="$2"
        backend_explicit=1
        shift
        ;;
      --transport)
        [ $# -ge 2 ] || die "--transport requires ssh-control, ssh-roce, or nfs-rdma"
        transport="$2"
        shift
        ;;
      --copy-streams)
        [ $# -ge 2 ] || die "--copy-streams requires a value"
        COPY_STREAMS="$2"
        export PULSAR_COPY_STREAMS="$COPY_STREAMS"
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
  [ -n "$profile" ] \
    || die "usage: activate <profile> [--transport ssh-control|ssh-roce|nfs-rdma]"
  case "$backend" in
    copy|fabric) ;;
    *) die "activate: --backend must be copy or fabric" ;;
  esac
  if [ -n "$transport" ]; then
    case "$transport" in
      ssh-control) expected_backend=copy; requested_mode=control ;;
      ssh-roce) expected_backend=copy; requested_mode=roce ;;
      nfs-rdma) expected_backend=fabric; requested_mode=control ;;
      *) die "activate: --transport must be ssh-control, ssh-roce, or nfs-rdma" ;;
    esac
    if [ "$backend_explicit" = 1 ] && [ "$backend" != "$expected_backend" ]; then
      die "activate: --transport $transport conflicts with --backend $backend"
    fi
    backend="$expected_backend"
    COPY_SSH_MODE="$requested_mode"
    export PULSAR_COPY_SSH_MODE="$COPY_SSH_MODE"
  elif [ "$backend" = fabric ]; then
    transport=nfs-rdma
  elif [ "$COPY_SSH_MODE" = roce ]; then
    transport=ssh-roce
  else
    transport=ssh-control
  fi
  validate_copy_stream_settings
  if [ "$backend" != copy ] && [ "$COPY_STREAMS" -ne 1 ]; then
    die "activate: --copy-streams is only valid with an SSH transport"
  fi
  require_py
  ensure_catalog
  load_conf "$profile"
  load_cluster_topology >/dev/null \
    || die "confirmed topology required"

  local plan_flags=(
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --topology-file "$CLUSTER_TOPOLOGY_FILE"
    --hot-root "$HOT_ROOT"
    --backend "$backend"
    --transport "$transport"
    --nodes "$NODES"
  )
  [ "$allow_unvalidated" = 1 ] && plan_flags+=(--allow-unvalidated)

  plan=$(library_plan_activate "$profile" "${plan_flags[@]}")
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  hub_source=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hub_source") or "")')
  hub_dest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_dest"])')
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
  expected_validation_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["validation"], sort_keys=True, separators=(",", ":")))')
  mapfile -t target_ranks < <(printf '%s' "$plan" | python3 -c 'import json,sys; print("\n".join(str(x) for x in json.load(sys.stdin)["target_ranks"]))')

  budget_plan=$(build_hot_budget_plan_from_activation "$plan" activate) \
    || die "activate: all-rank hot admission failed"
  render_hot_budget_plan_json "$budget_plan"
  hot_budget_plan_is_eligible "$budget_plan" \
    || die "activate: hot admission is blocked; no bytes were changed"

  if [ "$backend" = copy ]; then
    log "activate $profile action=$action transport=$transport copy_streams=$COPY_STREAMS hot=$instance"
  else
    log "activate $profile action=$action transport=$transport hot=$instance"
  fi

  if [ "$action" = skip ]; then
    log "hot already ready — verifying ranks"
    for rank in "${target_ranks[@]}"; do
      verify_hot_on_rank "$rank" "$instance" "$profile" \
        "$CLUSTER_TOPOLOGY_ID" 0 "$expected_validation_json" \
        || die "rank $rank: hot verify failed"
    done
    log "activate complete (reused hot)"
    return 0
  fi

  # A provisional seal carries the exact manifest but is never discoverable as
  # ready. The all-rank verification barrier publishes the final stamp later.
  verifying_stamp_json=$(printf '%s' "$stamp_json" | python3 -c '
import json,sys
d=json.load(sys.stdin)
d["state"]="verifying"
print(json.dumps(d))
')

  # Promotion candidate: SSH identity stays on the confirmed control alias;
  # bulk sockets are pinned to confirmed RoCE IPs after live route validation.
  if [ "$transport" = ssh-roce ]; then
    load_copy_ssh_roce_map "$home_rank" "$NODES" "${PULSAR_FABRIC_RAIL_INDEX:-0}"
    log "ssh-roce candidate: confirmed identity + RoCE HostName binding"
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

  local -a touched=() pids=()
  local transfer_armed=0 rank_jobs_grouped=0
  local t0 t1 pid child rank_fail=0
  # shellcheck disable=SC2317
  cleanup_partial() {
    local rc=$? r attempt alive=0 cleanup_failed=0
    trap - EXIT INT TERM

    # Background rank workers run in isolated process groups. Terminate the
    # entire owned group so nested parallel-rsync/SSH children cannot outlive
    # the wrapper and recreate files after partial cleanup.
    if [ "$rank_jobs_grouped" = 1 ]; then
      set +m
      for child in "${pids[@]:-}"; do
        kill -TERM -- "-$child" 2>/dev/null || true
      done
      for ((attempt = 0; attempt < 100; attempt++)); do
        alive=0
        for child in "${pids[@]:-}"; do
          if kill -0 -- "-$child" 2>/dev/null; then
            alive=1
          fi
        done
        [ "$alive" = 1 ] || break
        sleep 0.1
      done
      for child in "${pids[@]:-}"; do
        if kill -0 -- "-$child" 2>/dev/null; then
          log "rank worker process group $child did not stop; sending SIGKILL"
          kill -KILL -- "-$child" 2>/dev/null || true
        fi
      done
    else
      for child in "${pids[@]:-}"; do
        kill "$child" 2>/dev/null || true
      done
    fi
    for child in "${pids[@]:-}"; do
      wait "$child" 2>/dev/null || true
    done
    pids=()
    if [ "$rank_jobs_grouped" = 1 ]; then
      rank_jobs_grouped=0
    fi
    if [ "$transfer_armed" = 1 ]; then
      fabric_release_transfer "$plan" 2>/dev/null || true
    fi
    for r in "${touched[@]:-}"; do
      if [ "$r" = 0 ]; then
        if ! python3 "$PY_TOOL" purge-hot \
            --instance-dir "$instance" --force-unpin >/dev/null; then
          log "rank 0 partial cleanup helper failed; retrying exact instance removal"
        fi
        if [ -e "$instance" ] || [ -L "$instance" ]; then
          rm -rf -- "$instance" 2>/dev/null || true
        fi
        if [ -e "$instance" ] || [ -L "$instance" ]; then
          log "rank 0 partial cleanup failed: $instance"
          cleanup_failed=1
        fi
      else
        if ! ssh_node "$r" \
            "rm -rf $(printf "%q" "$instance") && [ ! -e $(printf "%q" "$instance") ] && [ ! -L $(printf "%q" "$instance") ]"; then
          log "rank $r partial cleanup failed: $instance"
          cleanup_failed=1
        fi
      fi
    done
    if [ "$cleanup_failed" = 1 ] && [ "$rc" = 0 ]; then
      rc=1
    fi
    return "$rc"
  }
  verify_and_publish_hot() {
    local verify_rank verify_pid verify_fail=0 verify_t0 verify_t1
    verify_t0=$(date +%s)
    pids=()
    set -m
    rank_jobs_grouped=1
    for verify_rank in "${target_ranks[@]}"; do
      (
        set -euo pipefail
        log "full SHA-256 verify → rank $verify_rank"
        verify_hot_on_rank "$verify_rank" "$instance" "$profile" \
          "$CLUSTER_TOPOLOGY_ID" 1 "$expected_validation_json"
      ) &
      pids+=("$!")
    done
    for verify_pid in "${pids[@]}"; do
      if ! wait "$verify_pid"; then
        verify_fail=1
      fi
    done
    pids=()
    set +m
    rank_jobs_grouped=0
    [ "$verify_fail" = 0 ] \
      || die "full SHA-256 verification failed on one or more ranks"

    # Publish ready only after every rank passed. A partial publish failure is
    # still covered by cleanup_partial, which removes every touched instance.
    for verify_rank in "${target_ranks[@]}"; do
      write_stamp_on_rank "$verify_rank" "$instance" "$stamp_json" \
        || die "rank $verify_rank: ready stamp publish failed"
    done
    verify_t1=$(date +%s)
    phase_record "verify" "$((verify_t1 - verify_t0))"
  }

  trap cleanup_partial EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  # Non-interactive job control gives every asynchronous rank worker its own
  # process group, with the tracked PID as PGID. Cleanup can then terminate
  # only descendants created by this activation.
  set -m
  rank_jobs_grouped=1

  [ "$time_it" = 1 ] && start_ts=$(date +%s)

  if [ "$action" = fabric-copy ]; then
    t0=$(date +%s)
    fabric_apply_transfer "$plan" 1
    transfer_armed=1
    t1=$(date +%s)
    phase_record "fabric_setup" "$((t1 - t0))"

    # Parallel materialize + provisional seal; full verify follows.
    t0=$(date +%s)
    pids=()
    for rank in "${target_ranks[@]}"; do
      source=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["rank_sources"][sys.argv[1]])' "$rank")
      (
        set -euo pipefail
        log "fabric materialize → rank $rank from $source"
        rt0=$(date +%s)
        copy_from_source_on_rank "$rank" "$source" "$hub_dest"
        write_stamp_on_rank "$rank" "$instance" "$verifying_stamp_json"
        rt1=$(date +%s)
        phase_record "transfer_rank_${rank}" "$((rt1 - rt0))"
      ) &
      pids+=("$!")
      touched+=("$rank")
    done
    for pid in "${pids[@]}"; do
      if ! wait "$pid"; then
        rank_fail=1
      fi
    done
    pids=()
    set +m
    rank_jobs_grouped=0
    [ "$rank_fail" = 0 ] || die "fabric materialize failed on one or more ranks"
    t1=$(date +%s)
    phase_record "transfer_all" "$((t1 - t0))"

    verify_and_publish_hot

    t0=$(date +%s)
    fabric_release_transfer "$plan"
    transfer_armed=0
    t1=$(date +%s)
    phase_record "fabric_release" "$((t1 - t0))"
  else
    # control-path copy — parallel ranks when N>1
    t0=$(date +%s)
    pids=()
    for rank in "${target_ranks[@]}"; do
      (
        set -euo pipefail
        log "copy → rank $rank"
        rt0=$(date +%s)
        copy_hub_to_rank "$rank" "$hub_source" "$hub_dest" "$home_rank"
        write_stamp_on_rank "$rank" "$instance" "$verifying_stamp_json"
        rt1=$(date +%s)
        phase_record "transfer_rank_${rank}" "$((rt1 - rt0))"
      ) &
      pids+=("$!")
      touched+=("$rank")
    done
    for pid in "${pids[@]}"; do
      if ! wait "$pid"; then
        rank_fail=1
      fi
    done
    pids=()
    set +m
    rank_jobs_grouped=0
    [ "$rank_fail" = 0 ] || die "copy materialize failed on one or more ranks"
    t1=$(date +%s)
    phase_record "transfer_all" "$((t1 - t0))"

    verify_and_publish_hot
  fi

  trap - EXIT INT TERM
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
    --hot-root "$HOT_ROOT" \
    --models-dir "$REPO_DIR/models"
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
  local info instance rank budget_plan
  info=$(hot_instance_for_profile "$profile")
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  budget_plan=$(build_zero_hot_budget_plan pin "$profile" "$NODES") \
    || die "pin: all-rank hot budget observation failed"
  render_hot_budget_plan_json "$budget_plan"
  hot_budget_plan_is_eligible "$budget_plan" \
    || die "pin: current hot state exceeds policy; nothing was pinned"
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
  local json=0 plan
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown arg: $1" ;;
    esac
    shift
  done
  require_py
  load_cluster_topology >/dev/null || die "confirmed topology required"
  plan=$(build_zero_hot_budget_plan inventory "" "$CLUSTER_TOPOLOGY_COUNT") \
    || die "hot budget: all-rank observation failed"
  if [ "$json" = 1 ]; then
    printf '%s\n' "$plan"
  else
    render_hot_budget_plan_json "$plan"
  fi
  hot_budget_plan_is_eligible "$plan"
}

# Fair cold-ish activate timing: purge hot, run backend.
# Activate logs go to stderr so command-substitution captures only seconds.
# Optional second arg path: write phase TSV (name\tseconds) for bench JSON.
timed_activate_backend() {
  local profile="${1:?}" backend="${2:?}" phase_out="${3:-}"
  local start_ts end_ts phase_tmp
  # Best-effort purge so skip path does not under-report times
  "$0" purge-hot "$profile" --yes --force-unpin >/dev/null 2>&1 || true
  phase_tmp=$(mktemp "${TMPDIR:-/tmp}/pulsar-activate-phases.XXXXXX")
  : >"$phase_tmp"
  start_ts=$(date +%s.%N 2>/dev/null || date +%s)
  if [ "$LIBRARY_SUDO_MODE" = interactive ]; then
    if ! ACTIVATE_PHASE_LOG="$phase_tmp" \
      "$0" activate "$profile" --backend "$backend" --allow-unvalidated --yes --interactive-sudo 1>&2; then
      rm -f "$phase_tmp"
      die "timed activate --backend $backend failed"
    fi
  else
    if ! ACTIVATE_PHASE_LOG="$phase_tmp" \
      "$0" activate "$profile" --backend "$backend" --allow-unvalidated --yes 1>&2; then
      rm -f "$phase_tmp"
      die "timed activate --backend $backend failed"
    fi
  fi
  end_ts=$(date +%s.%N 2>/dev/null || date +%s)
  if [ -n "$phase_out" ]; then
    cp -f "$phase_tmp" "$phase_out" || true
  fi
  rm -f "$phase_tmp"
  # Millisecond-resolution wall time. Short canary models lose too much signal
  # when a 6-7 second run is rounded to whole seconds.
  python3 -c "print(f'{max(0.0, float(\"$end_ts\") - float(\"$start_ts\")):.3f}')"
}

phases_tsv_to_json() {
  local path="${1:?}"
  python3 - <<'PY' "$path"
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
out = {}
if path.is_file():
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or "\t" not in line:
            continue
        name, sec = line.split("\t", 1)
        try:
            out[name] = int(float(sec))
        except ValueError:
            out[name] = sec
print(json.dumps(out, sort_keys=True))
PY
}

cmd_bench_activate() {
  local profile="" tag="" yes=0 output="" nodes_override=""
  local copy_s fabric_s model_id bytes_logical nodes topo_id
  while [ $# -gt 0 ]; do
    case "$1" in
      --tag)
        [ $# -ge 2 ] || die "--tag requires a value"
        tag="$2"
        shift
        ;;
      --output)
        [ $# -ge 2 ] || die "--output requires a path"
        output="$2"
        shift
        ;;
      --nodes)
        [ $# -ge 2 ] || die "--nodes requires a value"
        nodes_override="$2"
        shift
        ;;
      --yes|-y) yes=1 ;;
      --interactive-sudo) LIBRARY_SUDO_MODE=interactive ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$profile" ] || die "unexpected arg: $1"
        profile="$1"
        ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: bench-activate <profile> [--tag TAG] [--yes] [--nodes N]"
  [ "$yes" = 1 ] || die "bench-activate is destructive to hot staging — re-run with --yes"
  require_py
  ensure_catalog
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  nodes="${nodes_override:-$NODES}"
  if ! [[ "$nodes" =~ ^[0-9]+$ ]] || [ "$nodes" -lt 1 ]; then
    die "bench-activate: --nodes must be a positive integer"
  fi
  if [ "$nodes" -gt "$CLUSTER_TOPOLOGY_COUNT" ]; then
    die "bench-activate: --nodes $nodes exceeds topology count $CLUSTER_TOPOLOGY_COUNT"
  fi
  [ -n "$tag" ] || tag="activate-bench-$(date -u +%Y%m%dT%H%M%SZ)"
  [ -n "$output" ] || output="$REPO_DIR/results/model-library/${profile}-${tag}.json"
  mkdir -p "$(dirname "$output")"

  local plan
  plan=$(library_plan_activate "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --hot-root "$HOT_ROOT" \
    --backend copy \
    --allow-unvalidated \
    --nodes "$nodes") \
    || die "bench-activate: plan-activate failed (catalog primary or model identity?)"
  model_id=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  bytes_logical=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bytes_logical"])')
  topo_id="$CLUSTER_TOPOLOGY_ID"
  log "bench-activate $profile tag=$tag"
  local copy_phases_file fabric_phases_file copy_phases_json fabric_phases_json
  copy_phases_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-bench-copy-phases.XXXXXX")
  fabric_phases_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-bench-fabric-phases.XXXXXX")
  # shellcheck disable=SC2064
  trap "rm -f '$copy_phases_file' '$fabric_phases_file'" RETURN

  log "running timed copy activate…"
  copy_s=$(timed_activate_backend "$profile" copy "$copy_phases_file")
  log "copy wall_time_seconds=$copy_s"
  copy_phases_json=$(phases_tsv_to_json "$copy_phases_file")

  log "running timed fabric activate…"
  fabric_s=$(timed_activate_backend "$profile" fabric "$fabric_phases_file")
  log "fabric wall_time_seconds=$fabric_s"
  fabric_phases_json=$(phases_tsv_to_json "$fabric_phases_file")

  python3 "$PY_TOOL" compare-bench \
    --profile "$profile" \
    --topology-id "$topo_id" \
    --model-id "$model_id" \
    --bytes-logical "$bytes_logical" \
    --copy-seconds "$copy_s" \
    --fabric-seconds "$fabric_s" \
    --tag "$tag" \
    --nodes "$nodes" \
    --copy-phases-json "$copy_phases_json" \
    --fabric-phases-json "$fabric_phases_json" \
    --notes "home-rank prefers symlink/reflink; ranks materialize in parallel; fabric setup batched" \
    --output "$output" \
    --json

  local verdict
  verdict=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$output")
  if [ "$verdict" = fabric_faster ]; then
    log "B-gate: fabric claims fast path (fabric < copy)"
  else
    log "B-gate: fabric does NOT claim fast path (verdict=$verdict); keep advertising copy as default activate"
  fi
}

# Probe: can we SSH to each rank's RoCE IP (BatchMode) and match expected hostname?
cmd_probe_ssh_roce() {
  local profile="" nodes_override="" rail_index="${PULSAR_FABRIC_RAIL_INDEX:-0}"
  local plan home_rank nodes map expected_host ip rank identity_host host_key_alias rc=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --nodes)
        [ $# -ge 2 ] || die "--nodes requires a value"
        nodes_override="$2"
        shift
        ;;
      --rail-index)
        [ $# -ge 2 ] || die "--rail-index requires a value"
        rail_index="$2"
        shift
        ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$profile" ] || die "unexpected arg: $1"
        profile="$1"
        ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: probe-ssh-roce <profile> [--nodes N] [--rail-index N]"
  require_py
  ensure_catalog
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  nodes="${nodes_override:-$NODES}"
  plan=$(library_plan_activate "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --hot-root "$HOT_ROOT" \
    --backend copy \
    --allow-unvalidated \
    --nodes "$nodes") \
    || die "probe-ssh-roce: plan-activate failed"
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  load_copy_ssh_roce_map "$home_rank" "$nodes" "$rail_index"
  log "probe-ssh-roce profile=$profile home_rank=$home_rank nodes=$nodes"
  printf '%s\n' "$LIBRARY_SSH_ROCE_MAP_JSON" | python3 -c '
import json,sys
m=json.load(sys.stdin)
print("network", m.get("network"))
for _k,v in sorted((m.get("ranks") or {}).items(), key=lambda kv: int(kv[0])):
    print("rank", v["rank"], "role", v["role"],
          "control", v.get("control_ssh_host") or "-",
          "roce_ip", v["roce_ip"],
          "host", v.get("hostname") or "-")
'
  for rank in $(seq 0 $((nodes - 1))); do
    ip="${LIBRARY_SSH_ROCE_IP[$rank]:-}"
    expected_host="${LIBRARY_SSH_ROCE_HOSTNAME[$rank]:-}"
    identity_host="${CLUSTER_NODE_SSH_HOSTS[$rank]:-}"
    host_key_alias="${identity_host##*@}"
    host_key_alias="${host_key_alias#[}"
    host_key_alias="${host_key_alias%]}"
    [ -n "$ip" ] || { warn "rank $rank: missing RoCE IP"; rc=1; continue; }
    [ -n "$identity_host" ] \
      || { warn "rank $rank: missing SSH identity alias"; rc=1; continue; }
    # Rank 0 may be this controller — still SSH to its RoCE IP to prove path,
    # while the topology alias remains the pinned host-key identity.
    if ! out=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -o BatchMode=yes \
        -o ConnectTimeout=8 -o "HostName=$ip" \
        -o "HostKeyAlias=$host_key_alias" -- "$identity_host" \
        'hostname -s; hostname' 2>&1); then
      warn "rank $rank: SSH to RoCE IP $ip FAILED: $out"
      rc=1
      continue
    fi
    got=$(printf '%s\n' "$out" | head -1 | tr -d '\r')
    if [ -n "$expected_host" ] && [ "$got" != "$expected_host" ] \
        && [ "$got" != "${expected_host%%.*}" ]; then
      # Accept short vs FQDN mismatch only if one prefixes the other
      case "$expected_host" in
        "$got"|"$got".*) ;;
        *)
          warn "rank $rank: RoCE $ip hostname mismatch expected=$expected_host got=$got"
          rc=1
          continue
          ;;
      esac
    fi
    log "rank $rank: SSH OK roce_ip=$ip hostname=$got"
  done
  if [ "$rc" -ne 0 ]; then
    die "probe-ssh-roce: one or more ranks failed (sshd on fabric IP? route? host keys?)"
  fi
  log "probe-ssh-roce: all ranks OK"
  return 0
}

# Experiment A/B: control-path SSH copy vs SSH-over-RoCE-IP copy (no NFS/RDMA).
cmd_bench_ssh_roce() {
  local profile="" tag="" output="" nodes_override="" yes=0 order="control-first"
  local rail_index="${PULSAR_FABRIC_RAIL_INDEX:-0}"
  local nodes plan model_id bytes_logical topo_id home_rank
  local control_s roce_s control_phases_file roce_phases_file
  local control_phases_json roce_phases_json
  local leg phase_file
  local -a bench_order=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --tag)
        [ $# -ge 2 ] || die "--tag requires a value"
        tag="$2"
        shift
        ;;
      --output)
        [ $# -ge 2 ] || die "--output requires a path"
        output="$2"
        shift
        ;;
      --nodes)
        [ $# -ge 2 ] || die "--nodes requires a value"
        nodes_override="$2"
        shift
        ;;
      --rail-index)
        [ $# -ge 2 ] || die "--rail-index requires a value"
        rail_index="$2"
        shift
        ;;
      --order)
        [ $# -ge 2 ] || die "--order requires control-first or roce-first"
        order="$2"
        shift
        ;;
      --copy-streams)
        [ $# -ge 2 ] || die "--copy-streams requires a value"
        COPY_STREAMS="$2"
        export PULSAR_COPY_STREAMS="$COPY_STREAMS"
        shift
        ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$profile" ] || die "unexpected arg: $1"
        profile="$1"
        ;;
    esac
    shift
  done
  [ -n "$profile" ] \
    || die "usage: bench-ssh-roce <profile> [--tag TAG] [--yes] [--nodes N] [--order control-first|roce-first] [--copy-streams N]"
  [ "$yes" = 1 ] || die "bench-ssh-roce is destructive to hot staging — re-run with --yes"
  validate_copy_stream_settings
  case "$order" in
    control-first) bench_order=(control ssh_roce) ;;
    roce-first) bench_order=(ssh_roce control) ;;
    *) die "--order must be control-first or roce-first" ;;
  esac
  require_py
  ensure_catalog
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  nodes="${nodes_override:-$NODES}"
  if ! [[ "$nodes" =~ ^[0-9]+$ ]] || [ "$nodes" -lt 2 ]; then
    die "bench-ssh-roce: need --nodes >= 2 (single-rank is not a RoCE path test)"
  fi
  if [ "$nodes" -gt "$CLUSTER_TOPOLOGY_COUNT" ]; then
    die "bench-ssh-roce: --nodes $nodes exceeds topology count $CLUSTER_TOPOLOGY_COUNT"
  fi
  [ -n "$tag" ] || tag="ssh-roce-bench-$(date -u +%Y%m%dT%H%M%SZ)"
  [ -n "$output" ] || output="$REPO_DIR/results/model-library/${profile}-ssh-roce-${tag}.json"
  mkdir -p "$(dirname "$output")"

  plan=$(library_plan_activate "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --hot-root "$HOT_ROOT" \
    --backend copy \
    --allow-unvalidated \
    --nodes "$nodes") \
    || die "bench-ssh-roce: plan-activate failed"
  model_id=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  bytes_logical=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["bytes_logical"])')
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  topo_id="$CLUSTER_TOPOLOGY_ID"
  # Fail closed before long copies if fabric SSH is broken.
  cmd_probe_ssh_roce "$profile" --nodes "$nodes" --rail-index "$rail_index"

  control_phases_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-bench-ctrl-phases.XXXXXX")
  roce_phases_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-bench-roce-phases.XXXXXX")
  # shellcheck disable=SC2064
  trap "rm -f '$control_phases_file' '$roce_phases_file'" RETURN

  log "bench-ssh-roce $profile tag=$tag home_rank=$home_rank order=$order copy_streams=$COPY_STREAMS"
  for leg in "${bench_order[@]}"; do
    case "$leg" in
      control)
        log "running timed CONTROL (mgmt) SSH copy activate…"
        COPY_SSH_MODE=control
        export PULSAR_COPY_SSH_MODE=control
        phase_file="$control_phases_file"
        control_s=$(timed_activate_backend "$profile" copy "$phase_file")
        log "control wall_time_seconds=$control_s"
        ;;
      ssh_roce)
        log "running timed SSH-over-RoCE copy activate…"
        COPY_SSH_MODE=roce
        export PULSAR_COPY_SSH_MODE=roce
        export PULSAR_FABRIC_RAIL_INDEX="$rail_index"
        phase_file="$roce_phases_file"
        # Map reloads inside activate; keep the selected rail visible to child.
        roce_s=$(timed_activate_backend "$profile" copy "$phase_file")
        log "ssh_roce wall_time_seconds=$roce_s"
        ;;
    esac
  done
  control_phases_json=$(phases_tsv_to_json "$control_phases_file")
  roce_phases_json=$(phases_tsv_to_json "$roce_phases_file")

  # Restore default for rest of shell
  COPY_SSH_MODE=control
  export PULSAR_COPY_SSH_MODE=control

  python3 "$PY_TOOL" compare-ssh-roce-bench \
    --profile "$profile" \
    --topology-id "$topo_id" \
    --model-id "$model_id" \
    --bytes-logical "$bytes_logical" \
    --control-seconds "$control_s" \
    --ssh-roce-seconds "$roce_s" \
    --tag "$tag" \
    --nodes "$nodes" \
    --home-rank "$home_rank" \
    --run-order "$order" \
    --copy-streams "$COPY_STREAMS" \
    --control-phases-json "$control_phases_json" \
    --ssh-roce-phases-json "$roce_phases_json" \
    --ssh-roce-map-json "$LIBRARY_SSH_ROCE_MAP_JSON" \
    --notes "experiment: same rsync activate; control SSH host vs topology RoCE IP (TCP over RoCE NIC, not NFS/RDMA). Product default unchanged." \
    --output "$output"

  local verdict
  verdict=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["verdict"])' "$output")
  if [ "$verdict" = ssh_roce_faster ]; then
    log "SSH-RoCE single-pair result: RoCE wall time is lower (verdict=$verdict)"
    log "repeat both run orders before any fast-path decision; product default unchanged"
  else
    log "SSH-RoCE single-pair result: RoCE does not beat control (verdict=$verdict)"
    log "repeat both run orders before any fast-path decision; product default unchanged"
  fi
  log "report: $output"
}

main() {
  [ $# -ge 1 ] || { usage; exit 2; }
  local cmd="$1"
  shift
  case "$cmd" in
    -h|--help|help) ;;
    home)
      acquire_model_library_lifecycle_lock exclusive
      ;;
    *)
      acquire_model_library_lifecycle_lock shared
      ;;
  esac
  case "$cmd" in
    activate|pin|unpin|purge-hot)
      acquire_model_library_hot_lock exclusive
      ;;
    cold)
      if [ "${1:-}" = stage-only ]; then
        acquire_model_library_hot_lock exclusive
      fi
      ;;
    budget)
      acquire_model_library_hot_lock shared
      ;;
  esac
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
    validation-bundle)
      [ $# -ge 1 ] || { usage; exit 2; }
      local bundle_sub="$1"
      shift
      case "$bundle_sub" in
        verify) cmd_validation_bundle_verify "$@" ;;
        *) usage; exit 2 ;;
      esac
      ;;
    resolve) cmd_resolve "$@" ;;
    cleanup-recommend) cmd_cleanup_recommend "$@" ;;
    home)
      [ $# -ge 1 ] || { usage; exit 2; }
      local home_sub="$1"
      shift
      case "$home_sub" in
        check) cmd_home_check "$@" ;;
        remove) cmd_home_remove "$@" ;;
        *) usage; exit 2 ;;
      esac
      ;;
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
    bench-activate) cmd_bench_activate "$@" ;;
    probe-ssh-roce) cmd_probe_ssh_roce "$@" ;;
    bench-ssh-roce) cmd_bench_ssh_roce "$@" ;;
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
