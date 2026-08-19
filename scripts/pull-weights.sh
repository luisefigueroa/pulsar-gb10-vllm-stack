#!/usr/bin/env bash
# Download HF weights locally, then copy the full hub tree to each selected
# remote physical node. NFS/catalog profiles remain check-only.
#   scripts/pull-weights.sh <model-name> [--node NODE_ID]
#                           [--weight-source replicated] [--yes]
set -euo pipefail
SCRIPT_NAME=pull-weights
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

YES=0
NODE_SELECTOR=""
WEIGHT_SOURCE=replicated
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--node NODE_ID] [--yes]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    --weight-source)
      [ "$#" -ge 2 ] || die "--weight-source requires replicated" 2
      WEIGHT_SOURCE="$2"
      shift
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

acquire_model_library_lifecycle_lock shared
load_conf "$NAME"
case "$WEIGHT_SOURCE" in
  replicated) ;;
  fabric) refuse_retired_live_nfs_serving_weight_source fabric ;;
  *) die "--weight-source must be replicated" 2 ;;
esac
kind=$(model_source_kind)
SEALED_REPLICATED=0
TARGET_RANKS=()
REMOTE_STAGE_RANKS=()
CHECK_ARGS=()
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement $NODE_SELECTOR"
  TARGET_RANKS=("$SINGLE_NODE_INDEX")
  placement_selector="${SINGLE_NODE_ID:-$SINGLE_NODE_KEY}"
  CHECK_ARGS=(--node "$placement_selector")
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node is only valid for one-node profiles" 2
else
  require_cluster_nodes "$NODES" \
    || die "profile requires $NODES confirmed ranks before weight staging"
  for ((rank = 0; rank < NODES; rank++)); do
    TARGET_RANKS+=("$rank")
  done
fi
for rank in "${TARGET_RANKS[@]}"; do
  [ "$rank" -gt 0 ] && REMOTE_STAGE_RANKS+=("$rank")
done

if [ "$kind" = nfs ]; then
  cat >&2 <<EOF
[$SCRIPT_NAME] $NAME uses an NFS/catalog path:
  $MODEL
This tool will not download or copy catalog weights.
Fix:
  1. Ensure $MODELS_NFS is mounted on every one of the $NODES active rank(s)
  2. Confirm $MODEL/config.json is readable on every active rank
  3. Re-run: scripts/check-weights.sh $NAME ${CHECK_ARGS[*]:-}
EOF
  exit 1
fi
if [ -n "${EXPECTED_MODEL_SEAL:-}" ]; then
  load_replicated_identity_plan "$NAME"
  SEALED_REPLICATED=1
fi

if command -v hf >/dev/null 2>&1; then
  hf_bin=hf
elif command -v huggingface-cli >/dev/null 2>&1; then
  hf_bin=huggingface-cli
else
  die "need 'hf' or 'huggingface-cli' on PATH to download $MODEL"
fi

VERBOSE="${PULSAR_VERBOSE:-0}"
hub_root=$(hf_hub_cache_root)
hub=$(hf_hub_path)
legacy_hub=$(hf_legacy_hub_path)
mkdir -p "$hub_root"

node_name() {
  local rank="${1:?}" name
  name="${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
  if [ -z "$name" ]; then
    if [ "$rank" = 0 ]; then
      name=$(hostname -s)
    else
      name="${CLUSTER_NODE_SSH_HOSTS[$rank]:-cluster-node-$((rank + 1))}"
      name="${name%%.*}"
    fi
  fi
  printf '%s\n' "$name"
}

node_display() {
  local rank="${1:?}" display
  display=$(node_name "$rank")
  [ "$rank" = 0 ] && display+=" (this node)"
  printf '%s\n' "$display"
}

weight_failure() {
  local step="${1:?}" problem="${2:?}" next="${3:?}" details="${4:-}"
  local -a fields=(
    "Step" "$step"
    "Problem" "$problem"
    "Result" "Model was not started."
    "Next" "$next"
  )
  [ -n "$details" ] && fields+=("Details" "$details")
  echo
  render_human_section "MODEL FILE PREPARATION FAILED" "${fields[@]}"
  exit 1
}

render_weight_nodes() {
  local title="${1:?}" json="${2:?}" hostnames
  hostnames=$(printf '%s\n' "${CLUSTER_NODE_HOSTNAMES[@]:-}")
  WEIGHTS_JSON="$json" NODE_HOSTNAMES="$hostnames" python3 - "$title" <<'PY'
import json
import os
import socket
import sys

from scripts.terminal_format import TerminalWriter

data = json.loads(os.environ.get("WEIGHTS_JSON") or "{}")
hostnames = os.environ.get("NODE_HOSTNAMES", "").splitlines()
friendly = {
    "ok": "ready",
    "missing": "missing",
    "partial": "incomplete",
    "unreachable": "unreachable",
    "unconfigured": "not confirmed",
}

term = TerminalWriter()
term.emit(sys.argv[1])
term.field("Model", data.get("model") or "?")
for index, item in enumerate(data.get("ranks") or []):
    rank = int(item.get("rank") or 0)
    hostname = item.get("hostname")
    if not hostname and rank < len(hostnames) and hostnames[rank]:
        hostname = hostnames[rank]
    elif not hostname and rank == 0:
        hostname = socket.gethostname().split(".", 1)[0]
    elif not hostname:
        hostname = f"cluster node {rank + 1}"
    if rank == 0 and not item.get("remote"):
        hostname += " (this node)"
    state = str(item.get("state") or "unknown")
    term.field(
        "Status" if index == 0 else "",
        f"{hostname} · {friendly.get(state, state)}",
    )
PY
}

disk_free_remote() {
  local host="${1:?}" path="${2:?}" command qpath
  qpath=$(printf '%q' "$path")
  command="path=$qpath; while ! test -e \"\$path\"; do "
  command+="parent=\$(dirname \"\$path\"); test \"\$parent\" != \"\$path\" || break; "
  command+="path=\$parent; done; df -BG \"\$path\" 2>/dev/null"
  command+=" | awk 'NR==2 {gsub(/G/,\"\"); print \$4}'"
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$command" \
    2>/dev/null || echo 0
}

# Each node needs a full local cache even though the model is sharded in RAM.
w_gib=$(estimate_weights_gib)
size_display=$(format_storage_gib "$w_gib")
model_fields=(
  "Model" "$NAME"
  "Source" "Hugging Face · $MODEL"
  "Size" "about $size_display per node"
)
for index in "${!TARGET_RANKS[@]}"; do
  rank="${TARGET_RANKS[$index]}"
  [ "$index" = 0 ] && label="Required" || label=""
  model_fields+=("$label" "$(node_display "$rank")")
done
render_human_section "MODEL FILES" "${model_fields[@]}"

if [ -e "$legacy_hub" ] && [ -e "$hub" ]; then
  conflict_details=""
  [ "$VERBOSE" = 1 ] \
    && conflict_details="Standard: $hub · older: $legacy_hub"
  weight_failure \
    "Locating cached files" \
    "This model exists in both Pulsar's standard cache and an older cache location; they will not be merged automatically." \
    "Inspect the two locations, keep one verified copy, and rerun this command." \
    "$conflict_details"
fi

headroom=10
if awk -v w="$w_gib" 'BEGIN{exit !(w+0 > 0 && w+0 < 15)}'; then
  headroom=5
fi
need_full=$(awk -v w="$w_gib" -v h="$headroom" 'BEGIN{
  if (w+0 <= 0) { print 20; exit }
  n = (w * 1.1) + h
  if (n < 5) n = 5
  printf "%.0f", n + 0.999
}')
need_head="$need_full"
cached_hub="$hub"
[ -d "$legacy_hub" ] && [ ! -e "$hub" ] && cached_hub="$legacy_hub"
if [ -d "$cached_hub" ]; then
  have=$(dir_size_gib "$cached_hub")
  need_head=$(awk -v n="$need_full" -v h="$have" 'BEGIN{
    left = n - h
    if (left < 5) left = 5
    printf "%.0f", left
  }')
fi

storage_ok=1
storage_fields=()
free=$(disk_free_gib "$HF_CACHE")
if awk -v f="$free" -v m="$need_head" 'BEGIN{exit !(f+0 < m)}'; then
  storage_state="not enough space"
  storage_ok=0
else
  storage_state="ready"
fi
storage_fields+=(
  "Node" "$(node_display 0)"
  "Space" "$(format_storage_gib "$free") free · $(format_storage_gib "$need_head") needed · $storage_state"
)

for rank in "${REMOTE_STAGE_RANKS[@]}"; do
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  rank_free=$(disk_free_remote "$host" "$HF_CACHE")
  if awk -v f="$rank_free" -v m="$need_full" 'BEGIN{exit !(f+0 < m)}'; then
    storage_state="not enough space"
    storage_ok=0
  else
    storage_state="ready"
  fi
  storage_fields+=(
    "Node" "$(node_display "$rank")"
    "Space" "$(format_storage_gib "$rank_free") free · $(format_storage_gib "$need_full") needed · $storage_state"
  )
done
echo
render_human_section "STORAGE CHECK" "${storage_fields[@]}"

if [ "$storage_ok" != 1 ]; then
  weight_failure \
    "Checking storage" \
    "One or more nodes do not have enough free space for a complete local model cache." \
    "Free space on the affected node, then rerun this command."
fi

if [ "$YES" != 1 ]; then
  action="Download or reuse the model files on $(node_display 0)"
  if [ "${#REMOTE_STAGE_RANKS[@]}" -gt 0 ]; then
    action+=" and copy them to each selected remote node"
  fi
  echo
  render_human_section "PREPARE MODEL FILES" "Action" "$action"
  read -r -p "Continue? [y/N] " answer
  case "$answer" in
    y|Y|yes|YES) ;;
    *) die "aborted" 3 ;;
  esac
fi

if [ -e "$legacy_hub" ]; then
  echo
  cache_fields=(
    "Status" "Existing model files found in an older cache location"
    "Action" "Reusing them in Pulsar's standard cache; no full redownload required"
  )
  if [ "$VERBOSE" = 1 ]; then
    cache_fields+=("From" "$legacy_hub" "To" "$hub")
  fi
  render_human_section "CACHE LOCATION" "${cache_fields[@]}"
  if ! mv -- "$legacy_hub" "$hub"; then
    weight_failure \
      "Adopting existing model files" \
      "The existing cache could not be moved into Pulsar's standard location." \
      "Check ownership and permissions under $HF_CACHE, then rerun this command."
  fi
fi

echo
render_human_section "DOWNLOADING MODEL FILES" \
  "Node" "$(node_display 0)" \
  "Status" "Checking the cache and downloading any missing files…"

export HF_HUB_OFFLINE=0
download_cmd=("$hf_bin" download "$MODEL" --cache-dir "$hub_root")
if [ "$SEALED_REPLICATED" = 1 ]; then
  download_cmd+=(--revision "$REPLICATED_REVISION")
fi
if [ "$hf_bin" = hf ] && [ "$VERBOSE" != 1 ]; then
  download_cmd+=(--quiet)
fi
if [ "$VERBOSE" = 1 ]; then
  if ! "${download_cmd[@]}"; then
    weight_failure \
      "Downloading model files" \
      "Hugging Face could not complete the download." \
      "Check network access, repository access, and authentication, then retry."
  fi
else
  download_output=""
  if ! download_output=$("${download_cmd[@]}" 2>&1); then
    weight_failure \
      "Downloading model files" \
      "Hugging Face could not complete the download." \
      "Check network access and authentication, then retry with PULSAR_VERBOSE=1 for third-party diagnostics."
  fi
fi

if [ ! -d "$hub" ]; then
  weight_failure \
    "Locating downloaded files" \
    "Hugging Face completed without creating Pulsar's standard cache location." \
    "Retry with PULSAR_VERBOSE=1 and report the Hugging Face output." \
    "Expected: $hub"
fi

if [ "$SEALED_REPLICATED" = 1 ]; then
  if ! verify_replicated_identity_local full >/dev/null; then
    weight_failure "Verifying downloaded model identity" "The exact downloaded snapshot does not match the reviewed model seal." "Remove the corrupt or unexpected snapshot, then rerun this command."
  fi
fi

local_size=$(dir_size_gib "$hub")
echo
render_human_section "DOWNLOAD COMPLETE" \
  "Node" "$(node_display 0)" \
  "Stored" "$(format_storage_gib "$local_size")"

if [ "${#REMOTE_STAGE_RANKS[@]}" -gt 0 ]; then
  command -v rsync >/dev/null 2>&1 \
    || weight_failure \
      "Copying model files" \
      "The rsync command is not installed on this node." \
      "Install rsync, then rerun this command."
  remote_hub=$(hf_hub_path)
  rsync_opts=(-aH)
  if [ "$VERBOSE" = 1 ]; then
    rsync_opts+=(--info=progress2 --human-readable)
  else
    rsync_opts+=(--quiet)
  fi
  if [ -n "${RSYNC_BWLIMIT:-}" ]; then
    rsync_opts+=(--bwlimit="$RSYNC_BWLIMIT")
  fi
  rsync_remote_shell=$(pulsar_rsync_remote_shell)
  for rank in "${REMOTE_STAGE_RANKS[@]}"; do
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    echo
    render_human_section "COPYING MODEL FILES" \
      "From" "$(node_display 0)" \
      "To" "$(node_display "$rank")" \
      "Size" "about $(format_storage_gib "$local_size")"
    remote_output=""
    if ! remote_output=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
        "mkdir -p $(printf '%q' "$(dirname "$remote_hub")")" 2>&1); then
      weight_failure \
        "Preparing $(node_display "$rank")" \
        "The destination cache directory could not be created." \
        "Check SSH access and cache permissions on $(node_name "$rank"), then retry."
    fi
    if [ "$VERBOSE" = 1 ]; then
      if ! rsync "${rsync_opts[@]}" -e "$rsync_remote_shell" \
          "$hub/" "$host:$remote_hub/"; then
        weight_failure \
          "Copying model files to $(node_display "$rank")" \
          "The network copy did not complete." \
          "Check SSH, storage, and network connectivity, then retry."
      fi
    else
      copy_output=""
      if ! copy_output=$(rsync "${rsync_opts[@]}" -e "$rsync_remote_shell" \
          "$hub/" "$host:$remote_hub/" 2>&1); then
        weight_failure \
          "Copying model files to $(node_display "$rank")" \
          "The network copy did not complete." \
          "Check SSH, storage, and network connectivity, then retry with PULSAR_VERBOSE=1 for rsync diagnostics."
      fi
    fi
    if [ "$SEALED_REPLICATED" = 1 ]; then
      if ! verify_replicated_identity_remote "$host" full >/dev/null; then
        weight_failure "Verifying model identity on $(node_display "$rank")" "The copied snapshot does not match the reviewed model seal." "Remove the incomplete destination copy on $(node_name "$rank"), then rerun this command."
      fi
    fi
    render_human_section "COPY COMPLETE" \
      "Node" "$(node_display "$rank")" \
      "Stored" "$(format_storage_gib "$local_size")"
  done
fi

weights_json=""
weights_rc=0
weights_json=$("$REPO_DIR/scripts/check-weights.sh" "$NAME" "${CHECK_ARGS[@]}" --json) \
  || weights_rc=$?
if [ "$weights_rc" != 0 ]; then
  echo
  render_weight_nodes "MODEL FILE VERIFICATION" "$weights_json"
  weight_failure \
    "Verifying model files" \
    "One or more required nodes still have missing or incomplete files." \
    "Rerun with PULSAR_VERBOSE=1 or use scripts/check-weights.sh $NAME ${CHECK_ARGS[*]:-} for diagnostics."
fi

echo
render_weight_nodes "MODEL FILES READY" "$weights_json"
