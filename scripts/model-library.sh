#!/usr/bin/env bash
# Federated model library: warm catalog + optional cold + hot staging.
# The model library is the only weight-distribution mechanism (ADR 0006).
set -euo pipefail
SCRIPT_NAME=model-library
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/model-library-materialize.sh"

PY_TOOL="${PULSAR_MODEL_LIBRARY_PY:-$REPO_DIR/scripts/model_library.py}"
SOURCE_ATTESTED_PY="${PULSAR_SOURCE_ATTESTED_PY:-$REPO_DIR/scripts/model_library_source_attested.py}"
COLD_ARCHIVE_PY="${PULSAR_COLD_ARCHIVE_PY:-$REPO_DIR/scripts/model_library_cold_archive.py}"
HF_SOURCE_INVENTORY_PY="${PULSAR_HF_SOURCE_INVENTORY_PY:-$REPO_DIR/scripts/hf_source_inventory.py}"
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
  scripts/model-library.sh catalog list [--json]
  scripts/model-library.sh catalog show <model_id|profile> [--json]
  scripts/model-library.sh catalog primary list [--json]
  scripts/model-library.sh catalog primary set <profile|model_id|model_id@revision>
      --node RANK|NODE_ID [--json]
  scripts/model-library.sh catalog primary clear <profile|model_id|model_id@revision>
      [--json]
  scripts/model-library.sh resolve <profile|model_id|/abs/path> [--json] [--no-cold]
  scripts/model-library.sh cleanup-recommend [--json]
  scripts/model-library.sh home add <profile>
      [--revision SELECTOR] [--node RANK|NODE_ID] [--plan] [--yes] [--json]
  scripts/model-library.sh home verify <profile|model_id|model_id@revision>
      [--json]
  scripts/model-library.sh home check <profile|model_id|model_id@revision>
      [--node RANK|NODE_ID] [--allow-last-home] [--json]
  scripts/model-library.sh home remove <profile|model_id|model_id@revision>
      [--node RANK|NODE_ID] [--allow-last-home] [--allow-unarchived-last-home] --yes
  scripts/model-library.sh home relocate <profile|model_id|model_id@revision>
      --node RANK|NODE_ID --yes [--json]
  scripts/model-library.sh home archive status <profile|model_id@revision|receipt_id> [--json]
  scripts/model-library.sh home archive run --receipt RECEIPT_ID --yes [--json]
  scripts/model-library.sh home restore <profile|model_id@revision> --node RANK --yes [--json]
  scripts/model-library.sh cold scan [--json] [--complete-only] [--root PATH]
  scripts/model-library.sh cold show <model_id|/abs/path> [--json]
  scripts/model-library.sh cold adopt <model_id|profile|/abs/path>
      [--cache-root PATH] [--yes]
  scripts/model-library.sh cold stage-only <profile>
      [--yes] [--nodes N]
  scripts/model-library.sh prepare <profile>
      [--transport ssh-control|ssh-roce]
      [--yes] [--interactive-sudo] [--time]
      [--copy-streams N] [--node RANK|NODE_ID]
  scripts/model-library.sh probe-ssh-roce <profile> [--nodes N] [--rail-index N]
  scripts/model-library.sh bench-ssh-roce <profile> [--tag TAG] [--yes]
      [--output PATH] [--nodes N] [--rail-index N]
      [--order control-first|roce-first]
      [--copy-streams N]
  scripts/model-library.sh pin <profile> [--node RANK|NODE_ID]
  scripts/model-library.sh unpin <profile> [--node RANK|NODE_ID]
  scripts/model-library.sh purge-hot <profile> [--node RANK|NODE_ID]
      [--yes] [--force-unpin]
  scripts/model-library.sh health [--json]
  scripts/model-library.sh budget [--json]

Notes:
  • Scans default HF cache hubs on confirmed topology nodes (warm catalog).
  • Optional cold archive (PULSAR_COLD_ROOT or MODELS_NFS): Official Models/
    org/name flat trees and hub/models--* layouts. Resolve: warm → cold.
  • cold adopt imports into a durable warm HF home; cold stage-only fills hot
    only (cold remains sole durable copy; pin still allows warm restart).
  • Catalog identity labels are receipt/occupancy (legacy-unsealed) or
    unvalidated. Local bytes never create an ADR 0004 decision. --reviewed-identity
    and archived combined-identity verification are retired (ADR 0012). --validated
    is removed (ADR 0008). Status labels do not grant or deny start: prepare accepts
    fully verified receipt/occupancy content. --allow-unvalidated is removed
    (ADR 0008); drop the flag.
  • Duplicate occupancy homes refuse resolve until an exact-revision primary is
    chosen. Occupancy is the current-home attachment; extra
    complete hub trees are unbound-complete and do not freeze resolve when
    occupancy exists. The selection persists in the site catalog across
    refreshes; a missing selected home is stale and fails without fallback rather than
    auto-electing another home.
  • home check/remove probes managed containers and every hot root on all
    confirmed nodes. Any dependent retained view blocks removal, including
    ready, verifying, or pinned hot state. Unobservable nodes fail without fallback.
    Removal is exact-repository-only, refuses multi-revision hub trees, and
    needs --allow-last-home before deleting the final durable copy or the last
    occupancy of an identity. Receipted last occupancy also needs a verified
    receipt-indexed cold archive on a distinct device, or
    --allow-unarchived-last-home. home check rehashes that archive on the
    controller. home remove --yes re-verifies it on the controller before
    detaching occupancy; the occupancy rank only deletes the inspected hub
    tree.
    A recognized incomplete or refs-only hub stub is
    inspectable and retireable through the same read-only check then confirmed
    remove --yes path so a later home add --revision can occupy that
    repository. Complete homes keep the complete-home contract. Duplicate
    removal requires a selected primary and can target only a non-primary home.
    home check never mutates; without --yes, home remove changes nothing.
  • home add downloads one exact revision directly on one selected serving
    rank. Hugging Face profiles require --revision (ADR 0012: lab
    expected-identity home add is retired). --plan is read-only and prints the
    public Hugging Face file-list plan without downloading model bytes. Execution
    needs --yes (or a confirmation) and writes an immutable receipt before
    publishing the home, then attaches occupancy to the exact published
    directory. For a one-node profile, that rank may be any confirmed rank;
    for multi-node profiles it remains in the exact profile geometry. With
    no --node override, the eligible serving rank with the most free space is
    selected. Eligibility includes target-local metadata access; an explicit
    --node resolves metadata only on that rank. Download uses private
    same-filesystem staging and target-local modern hf. It creates no hot
    copies, starts nothing, never refreshes the catalog, and does not create
    a lab expected-identity file or status.
  • home verify rehashes a receipt-created home against the receipt when
    occupancy still names that live directory. An unbound complete tree with a
    compatible receipt needs home relocate --node, not a Hub re-download and
    not occupancy without a live rehash. An unknown tree without a receipt
    fails without fallback (ADR 0012: expected-manifest fallback is retired). Both
    verifiers hash attested empty snapshot files. Verification assigns no
    status.
  • home relocate moves occupancy to --node after a live full rehash against
    the immutable receipt. If that rank already has matching bytes, no copy
    and no Hub download occur. Receipt selected_rank is download provenance
    only and does not block the move. Catalog refresh is a separate next
    action.
  • After a receipt is written, a receipt-indexed cold archive starts in the
    background and is not a serving gate. The cold root may be NFS, an
    external disk, or another distinct-failure-domain mount. home archive
    status|run take no occupancy lifecycle lock so they cannot block prepare,
    launch, or relocate; workers flock only their job file. home restore
    occupy from that archive after a live rehash. vLLM never reads the cold
    archive. Legacy cold scan/adopt/stage-only remain fill paths, not receipt
    identity.
  • prepare --transport ssh-control|ssh-roce selects rsync SSH over the
    confirmed management or RoCE path. RoCE is TCP/IP over the NIC, not RDMA.
    --copy-streams N size-balances HF blobs over independent SSH connections
    (1..16). Defaults follow profile geometry: one-rank uses ssh-control with
    one stream (no bulk transfer); multi-rank uses ssh-roce with eight streams
    and no fallback (ADR 0003/0006). Management-network bulk copy on a
    multi-rank profile requires explicit --transport ssh-control. Every library
    scope is supported.
  • prepare full-verifies every rank and creates a rank-local serve witness.
    Unchanged launch checks metadata; drift visibly rehashes or fails without fallback.
  • Benchmark/probe commands are explicit experiments and permit receipt/occupancy
    profiles. Lab expected-identity files are not a live product (ADR 0012).
  • probe-ssh-roce / bench-ssh-roce: experimental control SSH vs SSH-over-RoCE
    A/B (no product default change). Requires current topology SSH enrollment
    and sshd on fabric IPs. Each report is one ordered pair; repeat both
    --order values before any fast-path decision.
  • pin prevents purge. Cold stage-only hot is self-contained; warm-home
    preparation currently keeps a home-rank symlink and still needs that home.
  • health is read-only: it uses the cached catalog, shallow metadata/witness
    observations, and managed-container labels without hashing model bytes.
    Schema-1/2 hot instances are untrusted and cannot launch. hot legacy
    check|remove is removed (SIM-13). Doctor never repairs automatically.
  • prepare, cold stage-only, pin, and budget observe every selected rank.
    The default preserves max(64 GiB, 5% of filesystem capacity) as available
    space; PULSAR_HOT_BUDGET_BYTES optionally adds a hard cap and
    PULSAR_HOT_RESERVE_BYTES explicitly overrides the reserve. No auto-eviction
    or capacity fallback occurs.
  • Live NFS serving is retired (ADR 0005); the one-shot nfs-rdma prepare
    experiment is retired with it (ADR 0006).
  • activate is removed (ADR 0008): use prepare. Model preparation does
    not start a serving container or establish model qualification.
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
    || die "prepare: catalog entry lacks an exact snapshot revision"
  if ! [[ "$home_rank" =~ ^[0-9]+$ ]] \
      || [ "$home_rank" -ge "${CLUSTER_TOPOLOGY_COUNT:-0}" ]; then
    die "prepare: catalog home rank is outside confirmed topology"
  fi
  expected_node="${CLUSTER_NODE_IDS[$home_rank]:-}"
  if [ -z "$expected_node" ] || [ "$node_id" != "$expected_node" ]; then
    die "prepare: catalog home identity differs from confirmed topology"
  fi
  if [ "$home_rank" = 0 ]; then
    python3 "$PY_TOOL" inspect-hub \
      --hub-path "$hub_path" \
      --rank "$home_rank" \
      --node-id "$node_id" \
      --model-id "$model_id" \
      --revision "$revision" \
      --allow-empty-files
    return
  fi
  command=$(
    shell_join_q python3 - inspect-hub \
      --hub-path "$hub_path" \
      --rank "$home_rank" \
      --node-id "$node_id" \
      --model-id "$model_id" \
      --revision "$revision" \
      --allow-empty-files
  )
  if ! out=$(
    ssh_node "$home_rank" "$command" <"$PY_TOOL"
  ); then
    die "rank $home_rank: authoritative hub inspection failed"
  fi
  printf '%s\n' "$out"
}

resolve_attached_source_attested_receipt() {
  local model_id="${1:?}" revision="${2:?}" rank="${3:?}" node_id="${4:?}"
  local hub_path="${5:?}" workdir="${6:?}" context="${7:?}"
  local attachment_present
  attachment_present=$(python3 "$SOURCE_ATTESTED_PY" \
    has-current-home-attachment \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision")
  case "$attachment_present" in
    false)
      printf '%s\n' null
      return 0
      ;;
    true) ;;
    *) die "$context: current-home attachment probe returned an invalid result" ;;
  esac
  run_model_library_on_rank "$rank" \
    inspect-live-directory-identity \
    --path "$hub_path" >"$workdir/live-identity.json" \
    || die "$context: could not inspect the durable home directory"
  python3 "$SOURCE_ATTESTED_PY" resolve-attached-receipt \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision" \
    --rank "$rank" \
    --node-id "$node_id" \
    --durable-home-path "$hub_path" \
    --live-identity "$workdir/live-identity.json" \
    --allow-missing
}

library_plan_activate() {
  local profile="${1:?profile required}"
  shift
  local home_inventory model_id revision receipt_json manifest_json
  local home_rank node_id hub_path tmp
  home_inventory=$(inspect_catalog_home "$profile") || return 1
  model_id=$(printf '%s' "$home_inventory" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$home_inventory" | python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
  home_rank=$(printf '%s' "$home_inventory" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["rank"])')
  node_id=$(printf '%s' "$home_inventory" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["node_id"])')
  hub_path=$(printf '%s' "$home_inventory" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["hub_path"])')
  local -a extra=()
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-prepare-receipt.XXXXXX")
  if ! receipt_json=$(resolve_attached_source_attested_receipt \
      "$model_id" "$revision" "$home_rank" "$node_id" "$hub_path" \
      "$tmp" "prepare"); then
    rm -rf "$tmp"
    die "prepare: source-attested receipt lookup failed"
  fi
  rm -rf "$tmp"
  if [ "$receipt_json" != null ]; then
    manifest_json=$(printf '%s' "$receipt_json" | python3 -c \
      'import json,sys; print(json.dumps(json.load(sys.stdin)["observed_manifest"]))')
    extra+=(--require-exact-revision "$revision")
    extra+=(--expected-integrity-manifest-json "$manifest_json")
  fi
  python3 "$PY_TOOL" plan-activate \
    --catalog "$CATALOG_FILE" \
    --profile "$profile" \
    --models-dir "$REPO_DIR/models" \
    --home-inventory-json "$home_inventory" \
    "${extra[@]}" \
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
  local mode="${1:?}" profile="${2:-}" ranks_csv="${3:?}"
  local observations_file expected_ranks="" observation rank rc=0
  local -a ranks=()
  IFS=, read -r -a ranks <<<"$ranks_csv"
  [ "${#ranks[@]}" -gt 0 ] || return 1
  observations_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-hot-budget.XXXXXX")
  for rank in "${ranks[@]}"; do
    [[ "$rank" =~ ^[0-9]+$ ]] || { rm -f "$observations_file"; return 1; }
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
  if [ -f "$CATALOG_FILE" ]; then
    build_args+=(--preserve-primary-from "$CATALOG_FILE")
  fi
  if [ "$json" = 1 ]; then
    build_args+=(--json)
  fi
  python3 "$PY_TOOL" "${build_args[@]}"
  python3 "$SOURCE_ATTESTED_PY" classify-catalog-occupancy \
    --library-dir "$LIBRARY_DIR" \
    --catalog "$CATALOG_FILE"
}

cmd_catalog_list() {
  local args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) args+=(--json) ;;
      --reviewed-identity)
        die "--reviewed-identity is retired (ADR 0012): expected-seal catalog filter is not a live product"
        ;;
      --validated) refuse_removed_catalog_validated_flag ;;
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

cmd_catalog_primary() {
  local action="${1:-}" query="" node_selector="" json=0
  [ -n "$action" ] || die "usage: catalog primary <list|set|clear>"
  shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *)
        [ -z "$query" ] || die "unexpected arg: $1"
        query="$1"
        ;;
    esac
    shift
  done
  require_py
  ensure_catalog
  local -a args=(catalog-primary "$action" --catalog "$CATALOG_FILE")
  case "$action" in
    list)
      [ -z "$query" ] || die "catalog primary list takes no query"
      [ -z "$node_selector" ] || die "catalog primary list does not accept --node"
      ;;
    set)
      [ -n "$query" ] || die "catalog primary set needs a model/profile/identity"
      [ -n "$node_selector" ] || die "catalog primary set requires --node"
      load_cluster_topology >/dev/null \
        || die "catalog primary: confirmed topology required"
      args+=(
        --topology-id "$CLUSTER_TOPOLOGY_ID"
        --topology-file "$CLUSTER_TOPOLOGY_FILE"
        --node "$node_selector"
        "$query"
      )
      ;;
    clear)
      [ -n "$query" ] || die "catalog primary clear needs a model/profile/identity"
      [ -z "$node_selector" ] || die "catalog primary clear does not accept --node"
      load_cluster_topology >/dev/null \
        || die "catalog primary: confirmed topology required"
      args+=(--topology-id "$CLUSTER_TOPOLOGY_ID" "$query")
      ;;
    *) die "usage: catalog primary <list|set|clear>" ;;
  esac
  [ "$json" = 0 ] || args+=(--json)
  python3 "$PY_TOOL" "${args[@]}"
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
  local profile="" yes=0 nodes=1 root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --allow-unvalidated) refuse_removed_allow_unvalidated_flag ;;
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
  [ -n "$profile" ] || die "usage: cold stage-only <profile> [--yes]"
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
  # via the same rsync preparation path when nodes>1 — rank 0 first).
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
  local rank="${1:?}" destination="${2:?}" context="${3:-home removal}"
  local ids id metadata rc host
  : >"$destination"
  if [ "$rank" = 0 ]; then
    ids=$(list_managed_container_ids_local) \
      || die "$context: cannot enumerate managed containers on rank 0"
  else
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]:-}"
    [ -n "$host" ] || die "$context: rank $rank has no confirmed SSH host"
    ids=$(list_managed_container_ids_remote "$host") \
      || die "$context: rank $rank is unreachable or Docker is unavailable"
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
      || die "$context: rank $rank container metadata became unobservable"
    printf '%s\n' "$metadata" >>"$destination"
  done <<<"$ids"
}

scan_hot_health_on_rank() {
  local rank="${1:?}" node_id="${2:?}" command
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" scan-hot-health \
      --hot-root "$HOT_ROOT" --rank "$rank" --node-id "$node_id"
    return
  fi
  command=$(shell_join_q python3 - scan-hot-health \
    --hot-root "$HOT_ROOT" --rank "$rank" --node-id "$node_id")
  ssh_node "$rank" "$command" <"$PY_TOOL"
}

write_unavailable_hot_scan() {
  local destination="${1:?}" rank="${2:?}" node_id="${3:?}" detail="${4:?}"
  python3 -c '
import json, sys
print(json.dumps({
    "schema_version": 1,
    "kind": "pulsar-model-library-hot-health-scan",
    "rank": int(sys.argv[1]),
    "node_id": sys.argv[2],
    "hot_root": "",
    "status": "error",
    "instances": [],
    "errors": [{"path": "", "detail": sys.argv[3]}],
}, indent=2, sort_keys=True))
' "$rank" "$node_id" "$detail" >"$destination"
}

collect_health_observations() {
  local destination="${1:?}" rank node_id scan_ok containers_ok
  load_cluster_topology >/dev/null || return 1
  [ "$CLUSTER_TOPOLOGY_COUNT" -gt 0 ] || return 1
  mkdir -p "$destination"
  for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    node_id="${CLUSTER_NODE_IDS[$rank]:-}"
    [ -n "$node_id" ] || return 1
    scan_ok=1
    if ! scan_hot_health_on_rank "$rank" "$node_id" \
        >"$destination/hot-$rank.json" 2>/dev/null; then
      scan_ok=0
      write_unavailable_hot_scan \
        "$destination/hot-$rank.json" "$rank" "$node_id" \
        "rank hot metadata is unreachable"
    fi
    containers_ok=1
    if ! (collect_managed_container_metadata_on_rank \
        "$rank" "$destination/containers-$rank.jsonl") >/dev/null 2>&1; then
      containers_ok=0
      : >"$destination/containers-$rank.jsonl"
    fi
    if [ "$scan_ok" = 1 ] && [ "$containers_ok" = 0 ]; then
      write_unavailable_hot_scan \
        "$destination/hot-$rank.json" "$rank" "$node_id" \
        "managed container metadata is unavailable"
    fi
  done
}

write_unavailable_health_report() {
  local destination="${1:?}" detail="${2:?}"
  python3 -c '
import json, sys
print(json.dumps({
    "schema_version": 1,
    "kind": "pulsar-model-library-health",
    "state": "unavailable",
    "catalog": {"status": "unavailable", "topology_compatible": None},
    "models": [],
    "hot_instances": [],
    "issues": [{"code": "observation-unavailable", "detail": sys.argv[1]}],
}, indent=2, sort_keys=True))
' "$detail" >"$destination"
}

build_health_report_file() {
  local destination="${1:?}" tmp rc=0
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-library-health.XXXXXX")
  if ! collect_health_observations "$tmp"; then
    write_unavailable_health_report "$destination" \
      "confirmed topology is unavailable"
    rm -rf "$tmp"
    return 1
  fi
  python3 "$PY_TOOL" build-health \
    --catalog "$CATALOG_FILE" \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --observations-dir "$tmp" \
    --library-dir "$LIBRARY_DIR" >"$destination" || rc=$?
  rm -rf "$tmp"
  return "$rc"
}

cmd_health() {
  local json=0 report rc=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unexpected arg: $1" ;;
    esac
    shift
  done
  require_py
  report=$(mktemp "${TMPDIR:-/tmp}/pulsar-library-health.XXXXXX.json")
  trap 'rm -f "$report"' RETURN
  build_health_report_file "$report" || rc=$?
  if [ "$json" = 1 ]; then
    python3 "$PY_TOOL" render-health --report-file "$report" --json || true
  else
    python3 "$PY_TOOL" render-health --report-file "$report" || true
  fi
  rm -f "$report"
  trap - RETURN
  return "$rc"
}

build_home_removal_plan() (
  set -euo pipefail
  local query="${1:?}" node_selector="${2:-}" allow_last_home="${3:-0}"
  local allow_unarchived="${4:-0}"
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
    'import json,sys; print(json.load(sys.stdin).get("revision") or "unknown")')
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
    --library-dir "$LIBRARY_DIR"
  )
  [ -z "$node_selector" ] || plan_args+=(--node "$node_selector")
  [ "$allow_last_home" = 0 ] || plan_args+=(--allow-last-home)
  [ "$allow_unarchived" = 0 ] || plan_args+=(--allow-unarchived-last-home)
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

run_model_library_on_rank() {
  local rank="${1:?rank required}" command
  shift
  if [ "$rank" = 0 ]; then
    python3 "$PY_TOOL" "$@"
    return
  fi
  command=$(shell_join_q python3 - "$@")
  ssh_node "$rank" "$command" <"$PY_TOOL"
}

home_acquisition_rank_environment() {
  local rank="${1:?rank required}" command
  if [ "$rank" = 0 ]; then
    local hf_cli=""
    if command -v hf >/dev/null 2>&1; then
      hf_cli=hf
    elif command -v huggingface-cli >/dev/null 2>&1; then
      hf_cli=huggingface-cli
    elif [ -x "$HOME/.hf-cli/venv/bin/hf" ]; then
      hf_cli="$HOME/.hf-cli/venv/bin/hf"
    fi
    printf '%s\t%s\n' "$HF_CACHE" "$hf_cli"
    return
  fi
  # The confirmed remote shell, not this controller shell, expands these vars.
  # shellcheck disable=SC2016
  command='cache_root=${HF_CACHE:-$HOME/.cache/huggingface}; hf_cli=""; '
  command+='if command -v hf >/dev/null 2>&1; then hf_cli=hf; '
  command+='elif command -v huggingface-cli >/dev/null 2>&1; then hf_cli=huggingface-cli; fi; '
  command+='if [ -z "$hf_cli" ] && [ -x "$HOME/.hf-cli/venv/bin/hf" ]; '
  command+='then hf_cli="$HOME/.hf-cli/venv/bin/hf"; fi; '
  # shellcheck disable=SC2016
  command+='printf "%s\t%s\n" "$cache_root" "$hf_cli"'
  ssh_node "$rank" "$command"
}

collect_home_acquisition_observations() {
  local output_dir="${1:?}" model_id="${2:?}" revision="${3:?}"
  local content_bytes="${4:?}" rank node_id cache_root hf_cli observation
  mkdir -p "$output_dir"
  for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    node_id="${CLUSTER_NODE_IDS[$rank]:-}"
    [ -n "$node_id" ] || die "home add: rank $rank lacks a node ID"
    IFS=$'\t' read -r cache_root hf_cli < <(home_acquisition_rank_environment "$rank")
    [ -n "$cache_root" ] || die "home add: rank $rank cache root is unobservable"
    observation=$(run_model_library_on_rank "$rank" \
      inspect-home-acquisition-target \
      --cache-root "$cache_root" \
      --model-id "$model_id" \
      --revision "$revision" \
      --required-content-bytes "$content_bytes" \
      --rank "$rank" \
      --node-id "$node_id" \
      --hf-cli "${hf_cli:-}") \
      || die "home add: rank $rank acquisition target is unobservable"
    printf '%s\n' "$observation" >"$output_dir/rank-$rank.json"
  done
}

download_home_acquisition_on_rank() {
  local rank="${1:?}" hf_cli="${2:?}" staging_root="${3:?}"
  local model_id="${4:?}" revision="${5:?}" command
  local -a args=("$hf_cli" download "$model_id" --cache-dir "$staging_root" --revision "$revision")
  [ "$hf_cli" != hf ] || args+=(--quiet)
  if [ "$rank" = 0 ]; then
    HF_HUB_OFFLINE=0 "${args[@]}" >&2
    return
  fi
  command="export HF_HUB_OFFLINE=0; $(shell_join_q "${args[@]}")"
  ssh_node "$rank" "$command" >&2
}

modern_source_attested_hf_cli() {
  local hf_cli="${1:-}"
  [ "$hf_cli" = hf ] && return 0
  [ -n "$hf_cli" ] || return 1
  case "$hf_cli" in
    */.hf-cli/venv/bin/hf) return 0 ;;
  esac
  return 1
}

run_hf_on_rank() {
  local rank="${1:?}" hf_cli="${2:?}" staging_root="${3:?}"
  shift 3
  local -a args=("$hf_cli" "$@")
  local command xet_cache assets_cache
  xet_cache="$staging_root/.pulsar-xet-cache"
  assets_cache="$staging_root/.pulsar-assets-cache"
  if [ "$rank" = 0 ]; then
    HF_HUB_OFFLINE=0 \
      HF_XET_CACHE="$xet_cache" \
      HF_ASSETS_CACHE="$assets_cache" \
      "${args[@]}"
    return
  fi
  command="export HF_HUB_OFFLINE=0; "
  command+="export HF_XET_CACHE=$(printf '%q' "$xet_cache"); "
  command+="export HF_ASSETS_CACHE=$(printf '%q' "$assets_cache"); "
  command+="$(shell_join_q "${args[@]}")"
  ssh_node "$rank" "$command"
}

resolve_source_attested_source_on_rank() {
  local rank="${1:?}" hf_cli="${2:?}" model_id="${3:?}" selector="${4:?}"
  local work_dir="${5:?}" allow_fail=0
  local inventory_file command cli_ref parsed
  if [ "${6:-}" = "--allow-fail" ]; then
    allow_fail=1
  fi
  mkdir -p "$work_dir"
  inventory_file="$work_dir/repo-inventory.json"
  [ -f "$HF_SOURCE_INVENTORY_PY" ] \
    || die "home add: Hugging Face source-inventory helper is missing"
  if [ "$hf_cli" = hf ]; then
    cli_ref='$(command -v hf)'
  else
    cli_ref=$(printf '%q' "$hf_cli")
  fi
  # The selected hf executable's shebang identifies the environment that owns
  # huggingface_hub. The helper is streamed to that interpreter; no token or
  # helper path is sent to another rank.
  command='set -eu; cli='"$cli_ref"'; '
  command+='[ -n "$cli" ] && [ -f "$cli" ]; '
  command+='resolved=$(readlink -f -- "$cli"); first=$(head -n 1 -- "$resolved"); '
  command+='case "$first" in "#!/usr/bin/env python3") py=$(command -v python3) ;; '
  command+='"#!"/*) py=${first#\#!} ;; *) exit 64 ;; esac; '
  command+='case "$py" in *[[:space:]]*) exit 64 ;; esac; '
  command+='case "${py##*/}" in python|python3|python3.*) ;; *) exit 64 ;; esac; '
  command+='[ -x "$py" ]; export HF_HUB_OFFLINE=0; exec "$py" - '
  command+="$(printf -- '%q ' --model-id "$model_id" --selector "$selector")"
  if [ "$rank" = 0 ]; then
    if ! bash -c "$command" <"$HF_SOURCE_INVENTORY_PY" >"$inventory_file"; then
      [ "$allow_fail" = 1 ] && return 1
      die "home add: could not resolve the complete Hugging Face inventory"
    fi
  else
    if ! ssh_node "$rank" "$command" <"$HF_SOURCE_INVENTORY_PY" >"$inventory_file"; then
      [ "$allow_fail" = 1 ] && return 1
      die "home add: could not resolve the complete Hugging Face inventory"
    fi
  fi
  if ! parsed=$(python3 "$SOURCE_ATTESTED_PY" parse-source \
    --model-id "$model_id" \
    --selector "$selector" \
    --repo-info "$inventory_file"); then
    [ "$allow_fail" = 1 ] && return 1
    die "home add: could not build the Hugging Face v1 source inventory"
  fi
  printf '%s\n' "$parsed"
}

cmd_home_add_source_attested() (
  set -euo pipefail
  local profile="${1:?}" selector="${2:?}" node_selector="${3:-}"
  local plan_only="${4:-0}" yes="${5:-0}" json="${6:-0}"
  local tmp model_id source_json identity_json plan_json handle_file
  local content_bytes revision selected_rank selected_node selected_hf
  local hub_root target_hub staging_json staging_root
  local observed_json receipt_json result_json approval_id
  local geometry_text match in_geometry geometry_rank
  local rank hf_cli source_try metadata_csv
  local cleanup_needed=0
  local -a geometry_ranks=() metadata_ranks=()

  [ "$plan_only" = 0 ] || [ "$yes" = 0 ] \
    || die "home add --plan is read-only and cannot be combined with --yes"
  [ "$json" = 0 ] || [ "$plan_only" = 1 ] || [ "$yes" = 1 ] \
    || die "home add --json requires --yes so stdout remains one result document"
  require_py
  load_cluster_topology >/dev/null \
    || die "home add: confirmed topology required"
  load_conf "$profile"
  [ "$(model_source_kind)" = hf ] \
    || die "home add: $profile is not a Hugging Face model profile"
  model_id="$MODEL"
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-add.XXXXXX")
  handle_file="$tmp/handle.json"
  # Invoked indirectly by the EXIT trap below.
  # shellcheck disable=SC2317
  cleanup_source_attested_home_add() {
    if [ "$cleanup_needed" = 1 ] && [ -n "${staging_root:-}" ]; then
      run_model_library_on_rank "$selected_rank" \
        cleanup-owned-hub-staging \
        --staging-root "$staging_root" \
        --owner-id "$approval_id" \
        --rank "$selected_rank" \
        --node-id "$selected_node" \
        --hub-root "$hub_root" >/dev/null 2>&1 \
        || log "rank $selected_rank: incomplete acquisition staging requires manual inspection" >&2
    fi
    rm -rf "$tmp"
  }
  trap cleanup_source_attested_home_add EXIT
  trap 'exit 130' INT TERM

  geometry_text=$(python3 "$SOURCE_ATTESTED_PY" geometry-ranks \
    --confirmed-count "$CLUSTER_TOPOLOGY_COUNT" \
    --serving-nodes "$NODES") \
    || die "home add: profile serving geometry exceeds confirmed contiguous ranks"
  read -r -a geometry_ranks <<<"$geometry_text"
  [ "${#geometry_ranks[@]}" -gt 0 ] \
    || die "home add: profile serving geometry exceeds confirmed contiguous ranks"

  mkdir -p "$tmp/sources"
  if [ -n "$node_selector" ]; then
    match=""
    for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
      if [ "$node_selector" = "$rank" ] \
          || [ "$node_selector" = "${CLUSTER_NODE_IDS[$rank]:-}" ]; then
        if [ -n "$match" ] && [ "$match" != "$rank" ]; then
          die "home add: --node must match exactly one confirmed rank or node ID"
        fi
        match="$rank"
      fi
    done
    [ -n "$match" ] \
      || die "home add: --node must match exactly one confirmed rank or node ID"
    in_geometry=0
    for geometry_rank in "${geometry_ranks[@]}"; do
      if [ "$geometry_rank" = "$match" ]; then
        in_geometry=1
        break
      fi
    done
    [ "$in_geometry" = 1 ] \
      || die "home add: selected rank is outside the profile serving geometry"
    IFS=$'\t' read -r _ hf_cli < <(home_acquisition_rank_environment "$match")
    modern_source_attested_hf_cli "$hf_cli" \
      || die "home add: selected rank does not have modern hf"
    source_json=$(resolve_source_attested_source_on_rank \
      "$match" "$hf_cli" "$model_id" "$selector" "$tmp/sources/work-$match") \
      || die "home add: could not build the Hugging Face v1 source inventory"
    printf '%s\n' "$source_json" >"$tmp/sources/rank-$match.json"
    metadata_ranks=("$match")
  else
    for rank in "${geometry_ranks[@]}"; do
      IFS=$'\t' read -r _ hf_cli < <(home_acquisition_rank_environment "$rank")
      if ! modern_source_attested_hf_cli "$hf_cli"; then
        continue
      fi
      if source_try=$(resolve_source_attested_source_on_rank \
        "$rank" "$hf_cli" "$model_id" "$selector" \
        "$tmp/sources/work-$rank" --allow-fail); then
        printf '%s\n' "$source_try" >"$tmp/sources/rank-$rank.json"
        metadata_ranks+=("$rank")
      else
        log "rank $rank: Hugging Face source metadata is unavailable; this rank is not eligible" >&2
      fi
    done
    [ "${#metadata_ranks[@]}" -gt 0 ] \
      || die "home add: no eligible rank could resolve Hugging Face source metadata"
    source_json=$(python3 "$SOURCE_ATTESTED_PY" unique-source \
      --sources-dir "$tmp/sources") \
      || die "home add: candidate ranks resolved disagreeing Hugging Face source metadata"
  fi
  printf '%s\n' "$source_json" >"$tmp/source.json"
  revision=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["snapshot_revision"])' \
    <"$tmp/source.json")
  content_bytes=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["content_bytes"])' \
    <"$tmp/source.json")

  identity_json=$(python3 "$SOURCE_ATTESTED_PY" resolve-identity \
    --source "$tmp/source.json" \
    --profile "$profile" \
    --repo-root "$REPO_DIR" \
    --release-id "${MODEL_SERVING_RELEASE_ID:-}")
  printf '%s\n' "$identity_json" >"$tmp/identity.json"

  collect_home_acquisition_observations \
    "$tmp/obs" "$model_id" "$revision" "$content_bytes"

  local -a plan_args=(
    plan
    --source "$tmp/source.json"
    --identity "$tmp/identity.json"
    --observations-dir "$tmp/obs"
    --topology-generation "$CLUSTER_TOPOLOGY_ID"
    --serving-nodes "$NODES"
    --handle-file "$handle_file"
  )
  [ -z "$node_selector" ] || plan_args+=(--node "$node_selector")
  metadata_csv=$(IFS=,; printf '%s' "${metadata_ranks[*]}")
  plan_args+=(--metadata-resolved-ranks "$metadata_csv")
  plan_args+=(--json)
  plan_json=$(python3 "$SOURCE_ATTESTED_PY" "${plan_args[@]}")
  printf '%s\n' "$plan_json" >"$tmp/plan.json"
  selected_rank=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["selected_rank"])' \
    <"$handle_file")
  selected_node=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])' \
    <"$handle_file")
  selected_hf=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hf_cli"])' \
    <"$handle_file")
  hub_root=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_root"])' \
    <"$handle_file")
  target_hub=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["target_hub"])' \
    <"$handle_file")
  approval_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["approval"]["approval_id"])' \
    <"$tmp/plan.json")
  modern_source_attested_hf_cli "$selected_hf" \
    || die "home add: selected rank does not have modern hf"
  [ -f "$tmp/sources/rank-$selected_rank.json" ] \
    || die "home add: selected rank did not independently resolve Hugging Face source metadata"

  if [ "$json" = 0 ]; then
    python3 "$SOURCE_ATTESTED_PY" render-plan --plan "$tmp/plan.json"
  fi
  if [ "$plan_only" = 1 ]; then
    if [ "$json" = 1 ]; then
      printf '%s\n' "$plan_json"
    fi
    return 0
  fi
  if [ "$json" = 0 ]; then
    library_confirm "$yes" "Download this exact revision?"
  fi

  mkdir -p "$tmp/live"
  source_json=$(resolve_source_attested_source_on_rank \
    "$selected_rank" "$selected_hf" "$model_id" "$selector" "$tmp/live") \
    || die "home add: selected rank could not re-resolve the Hugging Face source"
  printf '%s\n' "$source_json" >"$tmp/live-source.json"
  identity_json=$(python3 "$SOURCE_ATTESTED_PY" resolve-identity \
    --source "$tmp/live-source.json" \
    --profile "$profile" \
    --repo-root "$REPO_DIR" \
    --release-id "${MODEL_SERVING_RELEASE_ID:-}")
  printf '%s\n' "$identity_json" >"$tmp/live-identity.json"
  python3 "$SOURCE_ATTESTED_PY" verify-approval \
    --plan "$tmp/plan.json" \
    --source "$tmp/live-source.json" \
    --identity "$tmp/live-identity.json" \
    --topology-generation "$CLUSTER_TOPOLOGY_ID" >/dev/null \
    || die "home add: live source, identity, or topology no longer match the plan"

  staging_json=$(run_model_library_on_rank "$selected_rank" \
    create-owned-hub-staging \
    --hub-root "$hub_root" \
    --owner-id "$approval_id" \
    --rank "$selected_rank" \
    --node-id "$selected_node") \
    || die "home add: could not create plan-owned staging on rank $selected_rank"
  staging_root=$(printf '%s' "$staging_json" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["staging_root"])')
  cleanup_needed=1

  log "rank $selected_rank: downloading exact revision $revision into private staging" >&2
  run_hf_on_rank "$selected_rank" "$selected_hf" "$staging_root" download "$model_id" \
    --revision "$revision" --cache-dir "$staging_root" --quiet >&2 \
    || die "home add: Hugging Face download failed; private staging cleanup was attempted"

  local staged_hub
  staged_hub=$(python3 -c \
    'import sys
sys.path.insert(0, sys.argv[1])
import model_library
print(model_library.model_id_to_hub_dirname(sys.argv[2]))' \
    "$REPO_DIR/scripts" "$model_id")
  staged_hub="$staging_root/$staged_hub"

  observed_json=$(run_model_library_on_rank "$selected_rank" \
    inspect-snapshot-blob-identities \
    --hub-path "$staged_hub" \
    --model-id "$model_id" \
    --revision "$revision" \
    --allow-empty-files) \
    || die "home add: staged snapshot could not be hashed"
  printf '%s\n' "$observed_json" >"$tmp/observed.json"
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["files"]))' \
    <"$tmp/observed.json" >"$tmp/observed-files.json"
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["inventory"]))' \
    <"$tmp/live-source.json" >"$tmp/inventory.json"
  python3 "$SOURCE_ATTESTED_PY" compare-inventory \
    --inventory "$tmp/inventory.json" \
    --observed "$tmp/observed-files.json" >/dev/null \
    || die "home add: staged files do not match the upstream inventory"

  run_hf_on_rank "$selected_rank" "$selected_hf" "$staging_root" cache verify "$model_id" \
    --revision "$revision" --cache-dir "$staging_root" \
    --fail-on-missing-files --fail-on-extra-files >&2 \
    || die "home add: hf cache verify failed; private staging cleanup was attempted"

  python3 -c '
import json,sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import model_library_source_attested as sa
identity = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
observed = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
sa.compare_observed_manifest_to_expected(
    observed["manifest"],
    expected_manifest_id=identity.get("expected_manifest_id"),
)
' "$REPO_DIR/scripts" "$tmp/live-identity.json" "$tmp/observed.json"

  collect_home_acquisition_observations \
    "$tmp/recheck" "$model_id" "$revision" "$content_bytes"
  python3 "$PY_TOOL" recheck-home-acquisition-absence \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --observations-dir "$tmp/recheck" \
    --model-id "$model_id" \
    --revision "$revision" \
    --required-content-bytes "$content_bytes" \
    --selected-rank "$selected_rank" \
    --selected-node-id "$selected_node" \
    --selected-target-hub "$target_hub" >/dev/null \
    || die "home add: one-home publication recheck failed; private staging cleanup was attempted"

  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["manifest"]))' \
    <"$tmp/observed.json" >"$tmp/manifest.json"
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["approval"]))' \
    <"$tmp/plan.json" >"$tmp/approval.json"
  receipt_json=$(python3 "$SOURCE_ATTESTED_PY" build-receipt \
    --source "$tmp/live-source.json" \
    --identity "$tmp/live-identity.json" \
    --approval "$tmp/approval.json" \
    --manifest "$tmp/manifest.json" \
    --library-dir "$LIBRARY_DIR")
  printf '%s\n' "$receipt_json" >"$tmp/receipt.json"

  run_model_library_on_rank "$selected_rank" \
    publish-owned-hub-staging \
    --staging-root "$staging_root" \
    --owner-id "$approval_id" \
    --rank "$selected_rank" \
    --node-id "$selected_node" \
    --model-id "$model_id" \
    --target-hub "$target_hub" \
    --hub-root "$hub_root" >"$tmp/publish.json" \
    || die "home add: verification or atomic publication failed; private staging cleanup was attempted"
  cleanup_needed=0
  if ! python3 "$SOURCE_ATTESTED_PY" attach-current-home \
      --library-dir "$LIBRARY_DIR" \
      --receipt "$tmp/receipt.json" \
      --publish-result "$tmp/publish.json" \
      --node-id "$selected_node" >"$tmp/attach.json"; then
    die "home add: durable home published but occupancy was not attached. Remove it with a supported home remove, then re-add, or occupy it with home relocate --node after a live receipt rehash. Do not reconstruct occupancy without that rehash."
  fi
  local staging_cleanup
  staging_cleanup=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("staging_cleanup") or "removed")' \
    <"$tmp/publish.json")
  result_json=$(python3 "$SOURCE_ATTESTED_PY" build-result \
    --receipt "$tmp/receipt.json" \
    --state published \
    --staging-cleanup "$staging_cleanup")
  if [ "$json" = 1 ]; then
    python3 "$SOURCE_ATTESTED_PY" render-result --result /dev/stdin --json <<<"$result_json"
  else
    python3 "$SOURCE_ATTESTED_PY" render-result --result /dev/stdin <<<"$result_json"
  fi
  start_cold_archive_after_receipt "$tmp/receipt.json" "$json"
)

cmd_home_add() (
  set -euo pipefail
  local profile="" node_selector="" yes=0 json=0 plan_only=0 revision_selector=""
  local tmp identity_plan_b64 content_bytes revision model_id
  local plan plan_b64
  local target_rank target_node target_hf staging_json staging_root result_file
  local cleanup_needed=0

  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --revision)
        shift
        [ $# -gt 0 ] || die "--revision needs a Hugging Face selector or commit"
        revision_selector="$1"
        ;;
      --plan) plan_only=1 ;;
      --yes|-y) yes=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] \
    || die "usage: home add <profile> [--revision SELECTOR] [--node RANK|NODE_ID] [--plan] [--yes] [--json]"
  load_conf "$profile"
  [ -n "$revision_selector" ] \
    || die "home add: pass --revision SELECTOR (ADR 0012: expected-seal exact-commit home add is retired)"
  cmd_home_add_source_attested \
    "$profile" "$revision_selector" "$node_selector" \
    "$plan_only" "$yes" "$json"
)

cmd_home_verify() (
  local query="" json=0 resolved model_id revision home_rank node_id hub_path
  local receipt_json observed_json tmp
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] \
    || die "usage: home verify <profile|model_id|model_id@revision> [--json]"
  require_py
  ensure_catalog
  load_cluster_topology >/dev/null \
    || die "home verify: confirmed topology required"
  local show_query="$query" entry attachment_json rank
  if [[ "$query" != */* ]] && [[ "$query" != *@* ]]; then
    load_conf "$query"
    show_query="${MODEL:-$query}"
  fi
  entry=$(python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" "$show_query" --json) \
    || die "home verify: could not resolve $query"
  model_id=$(printf '%s' "$entry" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$entry" | python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
  [ -n "$revision" ] && [ "$revision" != unknown ] && [ "$revision" != missing ] \
    || die "home verify: catalog entry lacks an exact snapshot revision"
  attachment_json=$(python3 "$SOURCE_ATTESTED_PY" show-current-home-attachment \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision" \
    --allow-missing)
  if [ "$attachment_json" != null ] && [ -n "$attachment_json" ]; then
    node_id=$(printf '%s' "$attachment_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["node_id"])')
    hub_path=$(printf '%s' "$attachment_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["durable_home_path"])')
    home_rank=""
    for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
      if [ "${CLUSTER_NODE_IDS[$rank]:-}" = "$node_id" ]; then
        home_rank="$rank"
        break
      fi
    done
    [ -n "$home_rank" ] \
      || die "home verify: occupancy node is not in the confirmed topology"
  else
    resolved=$(
      python3 "$PY_TOOL" resolve \
        --catalog "$CATALOG_FILE" \
        "$query" \
        --models-dir "$REPO_DIR/models" \
        --no-cold \
        --json
    ) || resolved=""
    if [ -z "$resolved" ]; then
      receipt_lookup=$(python3 "$SOURCE_ATTESTED_PY" find-receipt \
        --library-dir "$LIBRARY_DIR" \
        --model-id "$model_id" \
        --revision "$revision" \
        --allow-missing)
      if [ "$receipt_lookup" != null ] && [ -n "$receipt_lookup" ]; then
        die "home verify: occupancy is missing for a source-attested receipt. Occupy the complete tree with scripts/model-library.sh home relocate $query --node RANK --yes after a live rehash. Do not Hub re-download. Do not reconstruct occupancy without that rehash."
      fi
      die "home verify: could not resolve $query"
    fi
    home_rank=$(printf '%s' "$resolved" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
    node_id=$(printf '%s' "$resolved" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["home"]["node_id"])')
    hub_path=$(printf '%s' "$resolved" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["home"]["hub_path"])')
  fi
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-verify.XXXXXX")
  trap 'if [ -n "${tmp:-}" ]; then rm -rf "$tmp"; fi' EXIT
  receipt_json=$(resolve_attached_source_attested_receipt \
      "$model_id" "$revision" "$home_rank" "$node_id" "$hub_path" \
      "$tmp" "home verify") \
    || die "home verify: source-attested receipt lookup failed"
  if [ "$receipt_json" != null ]; then
    printf '%s\n' "$receipt_json" >"$tmp/receipt.json"
    observed_json=$(run_model_library_on_rank "$home_rank" \
      inspect-snapshot-blob-identities \
      --hub-path "$hub_path" \
      --model-id "$model_id" \
      --revision "$revision" \
      --allow-empty-files) \
      || die "home verify: could not rehash the durable home"
    printf '%s\n' "$observed_json" >"$tmp/observed.json"
    python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["manifest"]))' \
      <"$tmp/observed.json" >"$tmp/manifest.json"
    local -a verify_args=(
      verify-home
      --receipt "$tmp/receipt.json"
      --manifest "$tmp/manifest.json"
      --model-id "$model_id"
      --revision "$revision"
    )
    [ "$json" = 0 ] || verify_args+=(--json)
    python3 "$SOURCE_ATTESTED_PY" "${verify_args[@]}"
    return 0
  fi
  receipt_lookup=$(python3 "$SOURCE_ATTESTED_PY" find-receipt \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision" \
    --allow-missing)
  if [ "$receipt_lookup" != null ] && [ -n "$receipt_lookup" ]; then
    die "home verify: occupancy is missing for a source-attested receipt. Occupy the complete tree with scripts/model-library.sh home relocate $query --node RANK --yes after a live rehash. Do not Hub re-download. Do not reconstruct occupancy without that rehash."
  fi
  die "home verify: unknown or pre-existing home has no source-attested receipt (ADR 0012: expected-manifest fallback is retired)"
)

confirmed_rank_from_node_selector() {
  local selector="${1:?}" match="" rank
  for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    if [ "$selector" = "$rank" ] \
        || [ "$selector" = "${CLUSTER_NODE_IDS[$rank]:-}" ]; then
      if [ -n "$match" ] && [ "$match" != "$rank" ]; then
        die "home relocate: --node must match exactly one confirmed rank or node ID"
      fi
      match="$rank"
    fi
  done
  [ -n "$match" ] \
    || die "home relocate: --node must match exactly one confirmed rank or node ID"
  printf '%s\n' "$match"
}

cmd_home_relocate() {
  local query="" node_selector="" yes=0 json=0 dest_rank dest_node
  local model_id revision receipt_json attachment_json dest_hub dest_state
  local cache_root hf_cli content_bytes tmp observed_json live_json
  local source_node source_path source_rank occupy_json
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --yes|-y) yes=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] && [ -n "$node_selector" ] \
    || die "usage: home relocate <profile|model_id|model_id@revision> --node RANK|NODE_ID --yes [--json]"
  [ "$yes" = 1 ] \
    || die "home relocate requires --yes after reviewing the destination rank"
  require_py
  ensure_catalog
  load_cluster_topology >/dev/null \
    || die "home relocate: confirmed topology required"
  dest_rank=$(confirmed_rank_from_node_selector "$node_selector")
  dest_node="${CLUSTER_NODE_IDS[$dest_rank]:-}"
  [ -n "$dest_node" ] || die "home relocate: rank $dest_rank lacks a node ID"

  local show_query="$query"
  if [[ "$query" != */* ]] && [[ "$query" != *@* ]]; then
    load_conf "$query"
    show_query="${MODEL:-$query}"
  fi
  local entry
  entry=$(python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" "$show_query" --json) \
    || die "home relocate: catalog has no entry for $query; refresh, then retry"
  model_id=$(printf '%s' "$entry" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$entry" | python3 -c \
    'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
  [ -n "$revision" ] && [ "$revision" != unknown ] && [ "$revision" != missing ] \
    || die "home relocate: catalog entry lacks an exact snapshot revision"

  receipt_json=$(python3 "$SOURCE_ATTESTED_PY" find-receipt \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision" \
    --allow-missing)
  [ "$receipt_json" != null ] && [ -n "$receipt_json" ] \
    || die "home relocate: no source-attested receipt for $model_id@$revision; sealed homes keep expected-manifest verify, and Hub re-download is home add"

  content_bytes=$(printf '%s' "$receipt_json" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["observed_manifest"]["total_bytes"])')
  IFS=$'\t' read -r cache_root hf_cli < <(home_acquisition_rank_environment "$dest_rank")
  [ -n "$cache_root" ] || die "home relocate: rank $dest_rank cache root is unobservable"
  local dest_obs
  dest_obs=$(run_model_library_on_rank "$dest_rank" \
    inspect-home-acquisition-target \
    --cache-root "$cache_root" \
    --model-id "$model_id" \
    --revision "$revision" \
    --required-content-bytes "$content_bytes" \
    --rank "$dest_rank" \
    --node-id "$dest_node" \
    --hf-cli "${hf_cli:-}") \
    || die "home relocate: destination rank is unobservable"
  dest_hub=$(printf '%s' "$dest_obs" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target_hub"])')
  dest_state=$(printf '%s' "$dest_obs" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target_state"])')

  attachment_json=$(python3 "$SOURCE_ATTESTED_PY" show-current-home-attachment \
    --library-dir "$LIBRARY_DIR" \
    --model-id "$model_id" \
    --revision "$revision" \
    --allow-missing)
  source_node=""
  source_path=""
  source_rank=""
  if [ "$attachment_json" != null ] && [ -n "$attachment_json" ]; then
    source_node=$(printf '%s' "$attachment_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["node_id"])')
    source_path=$(printf '%s' "$attachment_json" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["durable_home_path"])')
    local rank
    for ((rank = 0; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
      if [ "${CLUSTER_NODE_IDS[$rank]:-}" = "$source_node" ]; then
        source_rank="$rank"
        break
      fi
    done
  fi

  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-relocate.XXXXXX")
  trap 'if [ -n "${tmp:-}" ]; then rm -rf "$tmp"; fi' RETURN
  printf '%s\n' "$receipt_json" >"$tmp/receipt.json"

  if [ "$dest_state" = occupied ]; then
    observed_json=$(run_model_library_on_rank "$dest_rank" \
      inspect-snapshot-blob-identities \
      --hub-path "$dest_hub" \
      --model-id "$model_id" \
      --revision "$revision" \
      --allow-empty-files) \
      || die "home relocate: destination tree is not a complete matching snapshot; refuse Hub re-download"
  elif [ "$dest_state" = absent ]; then
    [ -n "$source_rank" ] && [ -n "$source_path" ] \
      || die "home relocate: destination has no complete tree and occupancy is missing; restore from a verified cold archive (home restore) or acquire with home add"
    [ "$source_rank" != "$dest_rank" ] \
      || die "home relocate: occupancy is already on this rank but the destination hub path is absent"
    COPY_SSH_MODE=roce
    if [ "$dest_rank" != 0 ] && [ "$source_rank" != 0 ]; then
      COPY_STREAMS=1
    else
      COPY_STREAMS=8
    fi
    export PULSAR_COPY_STREAMS="$COPY_STREAMS"
    load_copy_ssh_roce_map "$source_rank" "$source_rank,$dest_rank" 0
    copy_hub_to_rank "$dest_rank" "$source_path" "$dest_hub" "$source_rank" \
      || die "home relocate: copy from current occupancy failed"
    observed_json=$(run_model_library_on_rank "$dest_rank" \
      inspect-snapshot-blob-identities \
      --hub-path "$dest_hub" \
      --model-id "$model_id" \
      --revision "$revision" \
      --allow-empty-files) \
      || die "home relocate: copied destination failed snapshot inspection"
  else
    die "home relocate: destination hub path is not a usable directory ($dest_state)"
  fi

  printf '%s\n' "$observed_json" >"$tmp/observed.json"
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["manifest"]))' \
    <"$tmp/observed.json" >"$tmp/manifest.json"
  live_json=$(run_model_library_on_rank "$dest_rank" \
    inspect-live-directory-identity --path "$dest_hub") \
    || die "home relocate: could not inspect the destination directory identity"
  printf '%s\n' "$live_json" >"$tmp/live-identity.json"

  occupy_json=$(python3 "$SOURCE_ATTESTED_PY" occupy-current-home \
    --library-dir "$LIBRARY_DIR" \
    --receipt "$tmp/receipt.json" \
    --manifest "$tmp/manifest.json" \
    --node-id "$dest_node" \
    --durable-home-path "$dest_hub" \
    --live-identity "$tmp/live-identity.json") \
    || die "home relocate: live rehash did not match the receipt; occupancy was not moved"

  if [ -n "$source_path" ] && [ "$source_path" != "$dest_hub" ] && [ -n "$source_rank" ]; then
    if ! build_home_removal_plan "$model_id@$revision" "$source_rank" 0 >/dev/null; then
      log "home relocate: occupancy moved; source tree remains unbound-complete (removal blocked)"
    else
      local retire_plan retire_state
      retire_plan=$(build_home_removal_plan "$model_id@$revision" "$source_rank" 0)
      retire_state=$(printf '%s' "$retire_plan" | python3 -c \
        'import json,sys; print(json.load(sys.stdin)["state"])')
      if [ "$retire_state" = eligible ]; then
        execute_home_removal_on_rank "$retire_plan" >/dev/null \
          || log "home relocate: occupancy moved; source retirement failed and the old tree is unbound-complete"
      else
        log "home relocate: occupancy moved; source tree remains unbound-complete (removal blocked)"
      fi
    fi
  fi

  if [ "$json" = 1 ]; then
    printf '%s\n' "$occupy_json"
  else
    log "Receipt occupancy moved after live rehash. Catalog refresh is still required. Cold archive is not a serving gate."
    python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), indent=2, sort_keys=True))' \
      <<<"$occupy_json"
  fi
}

start_cold_archive_after_receipt() {
  local receipt_file="${1:?}" json="${2:-0}" job_json state receipt_id autostart
  job_json=$(python3 "$COLD_ARCHIVE_PY" enqueue \
    --library-dir "$LIBRARY_DIR" \
    --receipt "$receipt_file") \
    || return 0
  state=$(printf '%s' "$job_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  receipt_id=$(printf '%s' "$job_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_id"])')
  if [ "$json" != 1 ]; then
    if [ "$state" = pending ]; then
      log "Cold archive pending (not a serving gate)."
    elif [ "$state" = unavailable ]; then
      log "Cold archive unavailable; last-home remove will need --allow-unarchived-last-home."
    fi
  fi
  autostart="${PULSAR_COLD_ARCHIVE_AUTOSTART:-1}"
  if [ "$autostart" != 0 ] && [ "$state" = pending ]; then
    mkdir -p "$LIBRARY_DIR/cold-archive-logs"
    (
      if [[ "${PULSAR_MODEL_LIBRARY_LOCK_FD:-}" =~ ^[0-9]+$ ]]; then
        eval "exec ${PULSAR_MODEL_LIBRARY_LOCK_FD}>&-"
      fi
      if [[ "${PULSAR_MODEL_LIBRARY_HOT_LOCK_FD:-}" =~ ^[0-9]+$ ]]; then
        eval "exec ${PULSAR_MODEL_LIBRARY_HOT_LOCK_FD}>&-"
      fi
      unset PULSAR_MODEL_LIBRARY_LOCK_FD PULSAR_MODEL_LIBRARY_LOCK_MODE
      unset PULSAR_MODEL_LIBRARY_HOT_LOCK_FD PULSAR_MODEL_LIBRARY_HOT_LOCK_MODE
      exec setsid nohup env \
        PULSAR_COLD_ARCHIVE_AUTOSTART=0 \
        "$REPO_DIR/scripts/model-library.sh" home archive run --receipt "$receipt_id" --yes \
        >>"$LIBRARY_DIR/cold-archive-logs/${receipt_id}.log" 2>&1 < /dev/null
    ) &
  fi
}

cmd_home_archive() {
  local action="${1:-}"
  [ $# -gt 0 ] && shift
  case "$action" in
    status) cmd_home_archive_status "$@" ;;
    run) cmd_home_archive_run "$@" ;;
    *) die "usage: home archive <status|run>" ;;
  esac
}

cmd_home_archive_status() {
  local query="" json=0 receipt_id
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] || die "usage: home archive status <profile|model_id@revision|receipt_id>"
  require_py
  if [[ "$query" =~ ^[0-9a-f]{64}$ ]]; then
    receipt_id="$query"
  else
    local show_query="$query" entry model_id revision receipt_json
    ensure_catalog
    if [[ "$query" != */* ]] && [[ "$query" != *@* ]]; then
      load_conf "$query"
      show_query="${MODEL:-$query}"
    fi
    entry=$(python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" "$show_query" --json) \
      || die "home archive status: catalog has no entry for $query"
    model_id=$(printf '%s' "$entry" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
    revision=$(printf '%s' "$entry" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
    receipt_json=$(python3 "$SOURCE_ATTESTED_PY" find-receipt \
      --library-dir "$LIBRARY_DIR" --model-id "$model_id" --revision "$revision" --allow-missing)
    [ "$receipt_json" != null ] && [ -n "$receipt_json" ] \
      || die "home archive status: no source-attested receipt"
    receipt_id=$(printf '%s' "$receipt_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_id"])')
  fi
  python3 "$COLD_ARCHIVE_PY" show-job \
    --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" --allow-missing
}

cmd_home_archive_run() {
  local receipt_id="" yes=0 json=0 receipt_file attachment_json hub_path node_id rank
  local cold_root job_json
  while [ $# -gt 0 ]; do
    case "$1" in
      --receipt)
        shift
        [ $# -gt 0 ] || die "--receipt needs a receipt id"
        receipt_id="$1"
        ;;
      --yes|-y) yes=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unexpected arg: $1" ;;
    esac
    shift
  done
  [ -n "$receipt_id" ] || die "usage: home archive run --receipt RECEIPT_ID --yes"
  [ "$yes" = 1 ] || die "home archive run requires --yes"
  require_py
  receipt_file="$LIBRARY_DIR/source-attested-receipts/${receipt_id}.json"
  [ -f "$receipt_file" ] || die "home archive run: receipt is missing"
  cold_root="${PULSAR_COLD_ROOT-}"
  if [ -z "${PULSAR_COLD_ROOT+x}" ]; then
    cold_root="${MODELS_NFS:-}"
  fi
  [ -n "$cold_root" ] || die "home archive run: cold root is not configured"
  local archive_job_lock archive_job_fd
  archive_job_lock="$LIBRARY_DIR/cold-archive-jobs/${receipt_id}.lock"
  mkdir -p "$LIBRARY_DIR/cold-archive-jobs"
  exec {archive_job_fd}>"$archive_job_lock" \
    || die "home archive run: cannot open job lock"
  flock -x -w "${PULSAR_COLD_ARCHIVE_JOB_LOCK_TIMEOUT_SECONDS:-2}" "$archive_job_fd" || {
    exec {archive_job_fd}>&-
    die "home archive run: another archive worker holds this receipt"
  }
  _fd_cloexec "$archive_job_fd"
  python3 "$COLD_ARCHIVE_PY" set-job-state \
    --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" \
    --state running --detail "copying occupancy tree to receipt-indexed cold archive" \
    >/dev/null || python3 "$COLD_ARCHIVE_PY" enqueue \
      --library-dir "$LIBRARY_DIR" --receipt "$receipt_file" --cold-root "$cold_root" \
      >/dev/null
  python3 "$COLD_ARCHIVE_PY" set-job-state \
    --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" \
    --state running --detail "copying occupancy tree to receipt-indexed cold archive" \
    >/dev/null
  local model_id revision
  model_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["model_id"])' "$receipt_file")
  revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["snapshot_revision"])' "$receipt_file")
  attachment_json=$(python3 "$SOURCE_ATTESTED_PY" show-current-home-attachment \
    --library-dir "$LIBRARY_DIR" --model-id "$model_id" --revision "$revision" --allow-missing)
  if [ "$attachment_json" = null ] || [ -z "$attachment_json" ]; then
    python3 "$COLD_ARCHIVE_PY" set-job-state \
      --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" \
      --state failed --detail "occupancy is missing; archive needs a live home" >/dev/null
    die "home archive run: occupancy is missing"
  fi
  hub_path=$(printf '%s' "$attachment_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["durable_home_path"])')
  node_id=$(printf '%s' "$attachment_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])')
  load_cluster_topology >/dev/null || true
  rank=""
  local r
  for ((r = 0; r < ${CLUSTER_TOPOLOGY_COUNT:-0}; r++)); do
    if [ "${CLUSTER_NODE_IDS[$r]:-}" = "$node_id" ]; then
      rank="$r"
      break
    fi
  done
  local source_hub="$hub_path"
  local tmp=""
  if [ -n "$rank" ] && [ "$rank" != 0 ]; then
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-cold-archive.XXXXXX")
    trap 'rm -rf "${tmp:-}"' RETURN
    COPY_SSH_MODE="${COPY_SSH_MODE:-control}"
    COPY_STREAMS=1
    copy_hub_to_rank 0 "$hub_path" "$tmp/home" "$rank" \
      || {
        python3 "$COLD_ARCHIVE_PY" set-job-state \
          --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" \
          --state failed --detail "copy from occupancy failed" >/dev/null
        die "home archive run: copy from occupancy failed"
      }
    source_hub="$tmp/home"
  fi
  if ! job_json=$(python3 "$COLD_ARCHIVE_PY" publish \
      --library-dir "$LIBRARY_DIR" \
      --receipt "$receipt_file" \
      --cold-root "$cold_root" \
      --source-hub "$source_hub"); then
    python3 "$COLD_ARCHIVE_PY" set-job-state \
      --library-dir "$LIBRARY_DIR" --receipt-id "$receipt_id" \
      --state failed --detail "cold archive publish failed" >/dev/null
    die "home archive run: publish failed"
  fi
  trap - RETURN
  if [ "$json" = 1 ]; then
    printf '%s\n' "$job_json"
  else
    log "Receipt-indexed cold archive complete (not a serving gate)."
  fi
}

cmd_home_restore() {
  local query="" node_selector="" yes=0 json=0 dest_rank dest_node
  local model_id revision receipt_json receipt_id receipt_file cold_root
  local dest_hub dest_obs cache_root hf_cli occupy_json tmp
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --yes|-y) yes=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] && [ -n "$node_selector" ] \
    || die "usage: home restore <profile|model_id@revision> --node RANK --yes"
  [ "$yes" = 1 ] || die "home restore requires --yes"
  require_py
  ensure_catalog
  load_cluster_topology >/dev/null \
    || die "home restore: confirmed topology required"
  dest_rank=$(confirmed_rank_from_node_selector "$node_selector")
  dest_node="${CLUSTER_NODE_IDS[$dest_rank]:-}"
  local show_query="$query" entry
  if [[ "$query" != */* ]] && [[ "$query" != *@* ]]; then
    load_conf "$query"
    show_query="${MODEL:-$query}"
  fi
  entry=$(python3 "$PY_TOOL" show --catalog "$CATALOG_FILE" "$show_query" --json) \
    || die "home restore: catalog has no entry for $query; refresh after restore is still required"
  model_id=$(printf '%s' "$entry" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model_id"])')
  revision=$(printf '%s' "$entry" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("revision") or "")')
  [ -n "$revision" ] && [ "$revision" != unknown ] \
    || die "home restore: catalog entry lacks an exact snapshot revision"
  receipt_json=$(python3 "$SOURCE_ATTESTED_PY" find-receipt \
    --library-dir "$LIBRARY_DIR" --model-id "$model_id" --revision "$revision" --allow-missing)
  [ "$receipt_json" != null ] && [ -n "$receipt_json" ] \
    || die "home restore: no source-attested receipt"
  receipt_id=$(printf '%s' "$receipt_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["receipt_id"])')
  receipt_file="$LIBRARY_DIR/source-attested-receipts/${receipt_id}.json"
  cold_root="${PULSAR_COLD_ROOT-}"
  if [ -z "${PULSAR_COLD_ROOT+x}" ]; then
    cold_root="${MODELS_NFS:-}"
  fi
  [ -n "$cold_root" ] || die "home restore: cold root is not configured"
  python3 "$COLD_ARCHIVE_PY" verify-presence \
    --receipt "$receipt_file" --cold-root "$cold_root" >/dev/null \
    || die "home restore: verified receipt-indexed archive is missing"
  local archive_hub
  archive_hub="$cold_root/pulsar-receipts/${receipt_id}/home"
  IFS=$'\t' read -r cache_root hf_cli < <(home_acquisition_rank_environment "$dest_rank")
  dest_obs=$(run_model_library_on_rank "$dest_rank" \
    inspect-home-acquisition-target \
    --cache-root "$cache_root" \
    --model-id "$model_id" \
    --revision "$revision" \
    --required-content-bytes 1 \
    --rank "$dest_rank" \
    --node-id "$dest_node" \
    --hf-cli "${hf_cli:-}") \
    || die "home restore: destination rank is unobservable"
  dest_hub=$(printf '%s' "$dest_obs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_hub"])')
  dest_state=$(printf '%s' "$dest_obs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_state"])')
  if [ "$dest_state" = absent ]; then
    if [ "$dest_rank" = 0 ]; then
      mkdir -p "$(dirname "$dest_hub")"
      python3 -c 'import shutil,sys; shutil.copytree(sys.argv[1], sys.argv[2], symlinks=False)' \
        "$archive_hub" "$dest_hub" \
        || die "home restore: copy from cold archive failed"
    else
      COPY_SSH_MODE=roce
      COPY_STREAMS=1
      load_copy_ssh_roce_map 0 "0,$dest_rank" 0
      copy_hub_to_rank "$dest_rank" "$archive_hub" "$dest_hub" 0 \
        || die "home restore: copy from cold archive failed"
    fi
  elif [ "$dest_state" != occupied ]; then
    die "home restore: destination hub path is not usable ($dest_state)"
  fi
  tmp=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-home-restore.XXXXXX")
  trap 'rm -rf "${tmp:-}"' RETURN
  printf '%s\n' "$receipt_json" >"$tmp/receipt.json"
  observed_json=$(run_model_library_on_rank "$dest_rank" \
    inspect-snapshot-blob-identities \
    --hub-path "$dest_hub" --model-id "$model_id" --revision "$revision" --allow-empty-files) \
    || die "home restore: destination failed snapshot inspection"
  printf '%s\n' "$observed_json" >"$tmp/observed.json"
  python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["manifest"]))' \
    <"$tmp/observed.json" >"$tmp/manifest.json"
  live_json=$(run_model_library_on_rank "$dest_rank" \
    inspect-live-directory-identity --path "$dest_hub") \
    || die "home restore: could not inspect destination identity"
  printf '%s\n' "$live_json" >"$tmp/live-identity.json"
  occupy_json=$(python3 "$SOURCE_ATTESTED_PY" occupy-current-home \
    --library-dir "$LIBRARY_DIR" \
    --receipt "$tmp/receipt.json" \
    --manifest "$tmp/manifest.json" \
    --node-id "$dest_node" \
    --durable-home-path "$dest_hub" \
    --live-identity "$tmp/live-identity.json") \
    || die "home restore: live rehash did not match the receipt"
  trap - RETURN
  if [ "$json" = 1 ]; then
    printf '%s\n' "$occupy_json"
  else
    log "Restored occupancy from receipt-indexed cold archive after live rehash."
  fi
}

cmd_home_check() {
  local query="" node_selector="" allow_last_home=0 allow_unarchived=0 json=0 plan state
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --allow-last-home) allow_last_home=1 ;;
      --allow-unarchived-last-home) allow_unarchived=1 ;;
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] \
    || die "usage: home check <profile|model_id|identity> [--node RANK|NODE_ID]"
  plan=$(build_home_removal_plan "$query" "$node_selector" "$allow_last_home" "$allow_unarchived")
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
  local query="" node_selector="" allow_last_home=0 allow_unarchived=0 yes=0 plan state
  local result_file
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        shift
        [ $# -gt 0 ] || die "--node needs a rank or node ID"
        node_selector="$1"
        ;;
      --allow-last-home) allow_last_home=1 ;;
      --allow-unarchived-last-home) allow_unarchived=1 ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$query" ] || die "unexpected arg: $1"; query="$1" ;;
    esac
    shift
  done
  [ -n "$query" ] \
    || die "usage: home remove <profile|model_id|identity> [--node RANK|NODE_ID] --yes"
  plan=$(build_home_removal_plan "$query" "$node_selector" "$allow_last_home" "$allow_unarchived")
  render_home_removal_plan_json "$plan"
  state=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["state"])')
  [ "$state" = eligible ] \
    || die "home removal is blocked; no durable content was changed"
  [ "$yes" = 1 ] \
    || die "home removal requires --yes after reviewing the eligible plan"

  python3 "$PY_TOOL" reverify-last-home-archive \
      --plan-json "$plan" \
      --library-dir "$LIBRARY_DIR" \
    || die "home removal: cold archive re-verify failed; occupancy was not detached and the home was not removed"

  local detach_model detach_revision detach_rank detach_node detach_path
  detach_model=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["model_id"])')
  detach_revision=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["revision"])')
  if [ -z "$detach_revision" ] || [ "$detach_revision" = unknown ]; then
    die "home removal: live inspection did not bind one exact revision; nothing was changed"
  fi
  detach_rank=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["home"]["rank"])')
  detach_node=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["home"]["node_id"])')
  detach_path=$(printf '%s' "$plan" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["target"]["home"]["hub_path"])')
  python3 "$SOURCE_ATTESTED_PY" detach-current-home \
      --library-dir "$LIBRARY_DIR" \
      --model-id "$detach_model" \
      --revision "$detach_revision" \
      --rank "$detach_rank" \
      --node-id "$detach_node" \
      --durable-home-path "$detach_path" \
      >/dev/null \
    || die "home removal: current-home attachment store is unusable; receipts were not changed and the home was not removed"

  result_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-home-removed.XXXXXX")
  trap 'rm -f "$result_file"' RETURN
  execute_home_removal_on_rank "$plan" >"$result_file" \
    || die "home removal failed after detaching any current-home attachment; receipts were kept. If the home remains, it is unbound-complete. Retry supported removal or occupy it with home relocate --node after a live receipt rehash. Do not reconstruct occupancy without that rehash."
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
  local home_rank="${1:?}" ranks_csv="${2:?}" rail_index="${3:-0}"
  local map r ip control_host netdev expected
  require_topology_ssh_trust \
    || die "ssh-roce requires topology-bound SSH identity enrollment"
  map=$(python3 "$PY_TOOL" ssh-roce-map \
    --topology-file "$CLUSTER_TOPOLOGY_FILE" \
    --home-rank "$home_rank" \
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

  # Home rank: the runtime view must be an exact symlink to the durable home.
  if [ "$target_rank" = "$home_rank" ]; then
    materialize_runtime_view_on_rank \
      "$target_rank" "$home_rank" "$hub_source" "$hub_dest"
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

# --- privileged helpers (never store passwords) ---

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

cmd_activate() {
  local profile="" backend=copy backend_explicit=0 transport=""
  local yes=0 time_it=0 node_selector=""
  local plan stamp_json verifying_stamp_json expected_validation_json budget_plan
  local instance hub_source hub_dest home_rank rank source
  local expected_backend requested_mode
  local start_ts end_ts elapsed
  local copy_streams_explicit=0 ssh_mode_explicit=0
  if [ -n "${PULSAR_COPY_STREAMS:-}" ]; then
    copy_streams_explicit=1
  fi
  if [ -n "${PULSAR_COPY_SSH_MODE:-}" ]; then
    ssh_mode_explicit=1
  fi
  local -a target_ranks=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --backend)
        [ $# -ge 2 ] || die "--backend requires copy"
        backend="$2"
        backend_explicit=1
        shift
        ;;
      --transport)
        [ $# -ge 2 ] || die "--transport requires ssh-control or ssh-roce"
        transport="$2"
        shift
        ;;
      --copy-streams)
        [ $# -ge 2 ] || die "--copy-streams requires a value"
        COPY_STREAMS="$2"
        export PULSAR_COPY_STREAMS="$COPY_STREAMS"
        copy_streams_explicit=1
        shift
        ;;
      --node)
        [ $# -ge 2 ] || die "--node requires a topology rank, node ID, or hostname"
        node_selector="$2"
        shift
        ;;
      --allow-unvalidated) refuse_removed_allow_unvalidated_flag ;;
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
    || die "usage: prepare <profile> [--transport ssh-control|ssh-roce]"
  case "$backend" in
    copy) ;;
    *) die "prepare: --backend must be copy" ;;
  esac
  require_py
  load_conf "$profile"
  if [ -z "$transport" ]; then
    if [ "$ssh_mode_explicit" = 1 ]; then
      case "$COPY_SSH_MODE" in
        roce) transport=ssh-roce ;;
        control) transport=ssh-control ;;
        *) die "PULSAR_COPY_SSH_MODE must be control or roce" ;;
      esac
    elif [ "$NODES" -gt 1 ]; then
      transport=ssh-roce
    else
      transport=ssh-control
    fi
  fi
  case "$transport" in
    ssh-control) expected_backend=copy; requested_mode=control ;;
    ssh-roce) expected_backend=copy; requested_mode=roce ;;
    *) die "prepare: --transport must be ssh-control or ssh-roce" ;;
  esac
  if [ "$backend_explicit" = 1 ] && [ "$backend" != "$expected_backend" ]; then
    die "prepare: --transport $transport conflicts with --backend $backend"
  fi
  backend="$expected_backend"
  if [ "$NODES" -eq 1 ] && [ "$transport" = ssh-roce ]; then
    die "prepare: one-rank profiles have no non-home transfer; use ssh-control"
  fi
  COPY_SSH_MODE="$requested_mode"
  export PULSAR_COPY_SSH_MODE="$COPY_SSH_MODE"
  if [ "$copy_streams_explicit" != 1 ]; then
    if [ "$transport" = ssh-roce ]; then
      COPY_STREAMS=8
    else
      COPY_STREAMS=1
    fi
    export PULSAR_COPY_STREAMS="$COPY_STREAMS"
  fi
  validate_copy_stream_settings
  load_cluster_topology >/dev/null \
    || die "confirmed topology required"
  ensure_catalog

  local target_rank="" target_ranks_csv needs_bulk_transfer
  if [ "$NODES" -eq 1 ]; then
    resolve_single_node_placement "$node_selector" \
      || die "prepare: cannot resolve physical node placement '$node_selector'"
    target_rank="$SINGLE_NODE_INDEX"
  elif [ -n "$node_selector" ]; then
    die "prepare: --node is only valid for one-node profiles"
  fi

  local plan_flags=(
    --topology-id "$CLUSTER_TOPOLOGY_ID"
    --topology-file "$CLUSTER_TOPOLOGY_FILE"
    --hot-root "$HOT_ROOT"
    --backend "$backend"
    --transport "$transport"
    --nodes "$NODES"
  )
  [ -z "$target_rank" ] || plan_flags+=(--target-rank "$target_rank")

  plan=$(library_plan_activate "$profile" "${plan_flags[@]}")
  action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
  instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  hub_source=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hub_source") or "")')
  hub_dest=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_dest"])')
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
  expected_validation_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["validation"], sort_keys=True, separators=(",", ":")))')
  mapfile -t target_ranks < <(printf '%s' "$plan" | python3 -c 'import json,sys; print("\n".join(str(x) for x in json.load(sys.stdin)["target_ranks"]))')
  target_ranks_csv=$(IFS=,; printf '%s' "${target_ranks[*]}")

  budget_plan=$(build_hot_budget_plan_from_activation "$plan" activate) \
    || die "prepare: all-rank hot admission failed"
  render_hot_budget_plan_json "$budget_plan"
  hot_budget_plan_is_eligible "$budget_plan" \
    || die "prepare: hot admission is blocked; no bytes were changed"

  log "preparing model files profile=$profile action=$action transport=$transport copy_streams=$COPY_STREAMS hot=$instance"

  if [ "$action" = skip ]; then
    log "hot already ready — verifying ranks"
    for rank in "${target_ranks[@]}"; do
      verify_hot_on_rank "$rank" "$instance" "$profile" \
        "$CLUSTER_TOPOLOGY_ID" 0 "$expected_validation_json" \
        || die "rank $rank: hot verify failed"
    done
    log "model files prepared (reused verified runtime views; model not started)"
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
    needs_bulk_transfer=$(printf '%s' "$plan" | python3 -c '
import json,sys
d=json.load(sys.stdin)
home=int(d["home"]["rank"])
print("1" if any(int(rank) != home for rank in d["target_ranks"]) else "0")
')
    if [ "$needs_bulk_transfer" = 1 ]; then
      load_copy_ssh_roce_map "$home_rank" "$target_ranks_csv" \
        "${PULSAR_FABRIC_RAIL_INDEX:-0}"
      log "ssh-roce candidate: confirmed identity + RoCE HostName binding"
    else
      log "no non-home transfer is required; preparing the durable-home local view"
    fi
  fi

  if [ "$yes" != 1 ]; then
    log "will copy model to hot staging on ranks: ${target_ranks[*]}"
    log "source rank=$home_rank → $hub_dest"
    log "re-run with --yes to execute"
    return 0
  fi

  local -a touched=() pids=()
  local rank_jobs_grouped=0
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
  # only descendants created by this preparation.
  set -m
  rank_jobs_grouped=1

  [ "$time_it" = 1 ] && start_ts=$(date +%s)

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

  trap - EXIT INT TERM
  if [ "$time_it" = 1 ]; then
    end_ts=$(date +%s)
    elapsed=$((end_ts - start_ts))
    log "prepare wall_time_seconds=$elapsed backend=$backend"
  fi
  log "model files prepared on all serving ranks; model not started"
  printf '%s\n' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["instance_dir"]); print(d["hub_dest"])'
}

hot_instance_for_profile_on_rank() {
  local profile="${1:?}" rank="${2:?}" command info stamp
  if [ "$rank" -eq 0 ]; then
    python3 "$PY_TOOL" find-hot \
      --profile "$profile" \
      --topology-id "${CLUSTER_TOPOLOGY_ID}" \
      --hot-root "$HOT_ROOT" \
      --models-dir "$REPO_DIR/models"
    return
  fi
  command=$(shell_join_q python3 - find-hot \
    --profile "$profile" \
    --topology-id "$CLUSTER_TOPOLOGY_ID" \
    --hot-root "$HOT_ROOT")
  info=$(ssh_node "$rank" "$command" <"$PY_TOOL") || return 1
  stamp=$(printf '%s' "$info" | python3 -c \
    'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"], sort_keys=True, separators=(",", ":")))') \
    || return 1
  printf '%s' "$stamp" | python3 "$PY_TOOL" validate-hot-stamp \
    --profile "$profile" --models-dir "$REPO_DIR/models" \
    --stamp-file /dev/stdin >/dev/null || return 1
  printf '%s\n' "$info"
}

resolve_hot_profile_targets() {
  local profile="${1:?}" node_selector="${2:-}" resolved home_rank rank
  local -a ranks=()
  home_rank=""
  if [ -f "$CATALOG_FILE" ]; then
    resolved=$(python3 "$PY_TOOL" resolve \
      --catalog "$CATALOG_FILE" --profile "$profile" \
      --models-dir "$REPO_DIR/models" --no-cold --json 2>/dev/null) || resolved=""
    if [ -n "$resolved" ]; then
      home_rank=$(printf '%s' "$resolved" | python3 -c \
        'import json,sys; print(json.load(sys.stdin)["home"]["rank"])') || return 1
    fi
  fi
  if [ "$NODES" -eq 1 ]; then
    if [ -n "$node_selector" ]; then
      resolve_single_node_placement "$node_selector" || return 1
      rank="$SINGLE_NODE_INDEX"
    elif [ -n "$home_rank" ]; then
      rank="$home_rank"
      resolve_single_node_placement "$rank" || return 1
    else
      # Cold stage-only and historical hot state may have no warm catalog home;
      # those existing workflows use the profile's original rank-0 geometry.
      rank=0
      resolve_single_node_placement "$rank" || return 1
    fi
    [ -z "$home_rank" ] || [ "$rank" = "$home_rank" ] || {
      warn "$profile: library-hot one-node lifecycle must target durable-home rank $home_rank"
      return 1
    }
    ranks=("$rank")
  else
    [ -z "$node_selector" ] || {
      warn "$profile: --node is only valid for one-node profiles"
      return 1
    }
    for ((rank = 0; rank < NODES; rank++)); do
      ranks+=("$rank")
    done
  fi
  HOT_TARGET_RANKS=("${ranks[@]}")
  HOT_TARGET_RANKS_CSV=$(IFS=,; printf '%s' "${ranks[*]}")
}

cmd_pin() {
  local profile="" node_selector=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        [ $# -ge 2 ] || die "--node requires a topology rank, node ID, or hostname"
        node_selector="$2"
        shift
        ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: pin <profile> [--node RANK|NODE_ID]"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank budget_plan
  local -a HOT_TARGET_RANKS=()
  local HOT_TARGET_RANKS_CSV=""
  resolve_hot_profile_targets "$profile" "$node_selector" \
    || die "pin: cannot resolve exact library-hot placement"
  info=$(hot_instance_for_profile_on_rank "$profile" "${HOT_TARGET_RANKS[0]}")
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  budget_plan=$(build_zero_hot_budget_plan pin "$profile" "$HOT_TARGET_RANKS_CSV") \
    || die "pin: all-rank hot budget observation failed"
  render_hot_budget_plan_json "$budget_plan"
  hot_budget_plan_is_eligible "$budget_plan" \
    || die "pin: current hot state exceeds policy; nothing was pinned"
  for rank in "${HOT_TARGET_RANKS[@]}"; do
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
  local profile="" node_selector=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)
        [ $# -ge 2 ] || die "--node requires a topology rank, node ID, or hostname"
        node_selector="$2"
        shift
        ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: unpin <profile> [--node RANK|NODE_ID]"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank
  local -a HOT_TARGET_RANKS=()
  local HOT_TARGET_RANKS_CSV=""
  resolve_hot_profile_targets "$profile" "$node_selector" \
    || die "unpin: cannot resolve exact library-hot placement"
  info=$(hot_instance_for_profile_on_rank "$profile" "${HOT_TARGET_RANKS[0]}")
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  for rank in "${HOT_TARGET_RANKS[@]}"; do
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
  local profile="" yes=0 force_unpin=0 node_selector=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --yes|-y) yes=1 ;;
      --force-unpin) force_unpin=1 ;;
      --node)
        [ $# -ge 2 ] || die "--node requires a topology rank, node ID, or hostname"
        node_selector="$2"
        shift
        ;;
      -h|--help) usage; return 0 ;;
      *) [ -z "$profile" ] || die "unexpected arg: $1"; profile="$1" ;;
    esac
    shift
  done
  [ -n "$profile" ] || die "usage: purge-hot <profile> [--node RANK|NODE_ID] [--yes] [--force-unpin]"
  require_py
  load_conf "$profile"
  load_cluster_topology >/dev/null || die "confirmed topology required"
  local info instance rank command
  local -a HOT_TARGET_RANKS=()
  local HOT_TARGET_RANKS_CSV=""
  resolve_hot_profile_targets "$profile" "$node_selector" \
    || die "purge-hot: cannot resolve exact library-hot placement"
  info=$(hot_instance_for_profile_on_rank "$profile" "${HOT_TARGET_RANKS[0]}") \
    || die "no hot instance for $profile on the selected serving placement"
  instance=$(printf '%s' "$info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  [ "$yes" = 1 ] || die "purge-hot will delete $instance — re-run with --yes"
  for rank in "${HOT_TARGET_RANKS[@]}"; do
    if [ "$rank" = 0 ]; then
      if [ "$force_unpin" = 1 ]; then
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance" --force-unpin
      else
        python3 "$PY_TOOL" purge-hot --instance-dir "$instance"
      fi
    else
      command=$(shell_join_q python3 - purge-hot --instance-dir "$instance")
      if [ "$force_unpin" = 1 ]; then
        command+=" --force-unpin"
        ssh_node "$rank" "$command" <"$PY_TOOL" \
          || die "rank $rank: purge failed"
      else
        ssh_node "$rank" "$command" \
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
  local inventory_ranks
  inventory_ranks=$(seq -s, 0 $((CLUSTER_TOPOLOGY_COUNT - 1)))
  plan=$(build_zero_hot_budget_plan inventory "" "$inventory_ranks") \
    || die "hot budget: all-rank observation failed"
  if [ "$json" = 1 ]; then
    printf '%s\n' "$plan"
  else
    render_hot_budget_plan_json "$plan"
  fi
  hot_budget_plan_is_eligible "$plan"
}

# Fair cold-ish preparation timing: purge hot, run backend.
# Preparation logs go to stderr so command-substitution captures only seconds.
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
      "$0" prepare "$profile" --backend "$backend" --yes --interactive-sudo 1>&2; then
      rm -f "$phase_tmp"
      die "timed preparation --backend $backend failed"
    fi
  else
    if ! ACTIVATE_PHASE_LOG="$phase_tmp" \
      "$0" prepare "$profile" --backend "$backend" --yes 1>&2; then
      rm -f "$phase_tmp"
      die "timed preparation --backend $backend failed"
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
    --nodes "$nodes") \
    || die "probe-ssh-roce: preparation plan failed"
  home_rank=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["home"]["rank"])')
  load_copy_ssh_roce_map "$home_rank" "$(seq -s, 0 $((nodes - 1)))" "$rail_index"
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
    --nodes "$nodes") \
    || die "bench-ssh-roce: preparation plan failed"
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
        log "running timed CONTROL (mgmt) SSH preparation…"
        COPY_SSH_MODE=control
        export PULSAR_COPY_SSH_MODE=control
        phase_file="$control_phases_file"
        control_s=$(timed_activate_backend "$profile" copy "$phase_file")
        log "control wall_time_seconds=$control_s"
        ;;
      ssh_roce)
        log "running timed SSH-over-RoCE preparation…"
        COPY_SSH_MODE=roce
        export PULSAR_COPY_SSH_MODE=roce
        export PULSAR_FABRIC_RAIL_INDEX="$rail_index"
        phase_file="$roce_phases_file"
        # Map reloads inside preparation; keep the selected rail visible to child.
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
    --notes "experiment: same rsync preparation; control SSH host vs topology RoCE IP (TCP over RoCE NIC, not NFS/RDMA). Product default unchanged." \
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
  # Refuse before locks so the old public alias does not wait on a hot lock.
  [ "$cmd" = activate ] && refuse_removed_activate_command
  case "$cmd:${1:-}:${2:-}" in
    -h:*|--help:*|help:*) ;;
    home:archive:*)
      # Archive reads occupancy and writes NFS/job state. No lifecycle lock:
      # exclusive would block prepare/launch; shared would block relocate for
      # the whole copy. Last-home remove already requires a complete archive.
      ;;
    home:verify:*|home:check:*)
      acquire_model_library_lifecycle_lock shared
      ;;
    home:*|catalog:refresh:*|catalog:primary:set|catalog:primary:clear)
      acquire_model_library_lifecycle_lock exclusive
      ;;
    *)
      acquire_model_library_lifecycle_lock shared
      ;;
  esac
  case "$cmd" in
    prepare|pin|unpin|purge-hot)
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
    health)
      acquire_model_library_hot_lock shared
      ;;
    hot)
      if [ "${1:-}" = legacy ] && [ "${2:-}" = remove ]; then
        acquire_model_library_hot_lock exclusive
      else
        acquire_model_library_hot_lock shared
      fi
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
        primary) cmd_catalog_primary "$@" ;;
        *) usage; exit 2 ;;
      esac
      ;;
    validation-bundle)
      die "validation-bundle is retired (ADR 0012): expected-seal and schema-1 bundles are not a live product; use scripts/model-serving-release-registry.sh verify"
      ;;
    resolve) cmd_resolve "$@" ;;
    cleanup-recommend) cmd_cleanup_recommend "$@" ;;
    home)
      [ $# -ge 1 ] || { usage; exit 2; }
      local home_sub="$1"
      shift
      case "$home_sub" in
        add) cmd_home_add "$@" ;;
        verify) cmd_home_verify "$@" ;;
        check) cmd_home_check "$@" ;;
        remove) cmd_home_remove "$@" ;;
        relocate) cmd_home_relocate "$@" ;;
        archive) cmd_home_archive "$@" ;;
        restore) cmd_home_restore "$@" ;;
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
    prepare) cmd_activate "$@" ;;
    activate) refuse_removed_activate_command ;;
    probe-ssh-roce) cmd_probe_ssh_roce "$@" ;;
    bench-ssh-roce) cmd_bench_ssh_roce "$@" ;;
    pin) cmd_pin "$@" ;;
    unpin) cmd_unpin "$@" ;;
    purge-hot) cmd_purge_hot "$@" ;;
    health) cmd_health "$@" ;;
    hot) refuse_removed_hot_legacy_command ;;
    budget) cmd_budget "$@" ;;
    -h|--help|help) usage ;;
    *) usage; exit 2 ;;
  esac
}

main "$@"
