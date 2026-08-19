#!/usr/bin/env bash
# Check whether exact-profile weights exist on every active rank.
#   scripts/check-weights.sh <model-name> [--node NODE_ID]
#                            [--weight-source replicated|library-hot] [--json]
set -euo pipefail
SCRIPT_NAME=check-weights
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0
NODE_SELECTOR=""
WEIGHT_SOURCE=replicated
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--node NODE_ID] [--json]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1 ;;
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    --weight-source)
      [ "$#" -ge 2 ] || die "--weight-source requires replicated|library-hot" 2
      WEIGHT_SOURCE="$2"
      shift
      ;;
    --weight-mode)
      [ "$#" -ge 2 ] || die "--weight-mode requires library-hot (or replicated)" 2
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
  replicated|library-hot) ;;
  fabric) refuse_retired_live_nfs_serving_weight_source fabric ;;
  *) die "--weight-source must be replicated or library-hot" 2 ;;
esac
if [ "$WEIGHT_SOURCE" = library-hot ]; then
  acquire_model_library_hot_lock shared
fi
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement '$NODE_SELECTOR'"
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node is only valid for one-node profiles" 2
fi



if [ "$WEIGHT_SOURCE" = library-hot ]; then
  load_cluster_topology >/dev/null 2>&1 \
    || die "library-hot requires confirmed topology"
  if ! hot_info=$(library_hot_info_for_profile "$NAME"); then
    if [ "$JSON" = 1 ]; then
      printf '%s\n' '{"state":"missing","source":"library-hot","ok":false}'
    elif [ "${QUIET:-0}" = 1 ]; then
      echo "FAIL  weights   source=library-hot · hot missing"
    else
      echo "library-hot: model files are not prepared — run: scripts/model-library.sh prepare $NAME --yes" >&2
    fi
    exit 1
  fi
  instance=$(printf '%s' "$hot_info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
  if [ "$JSON" = 1 ]; then
    printf '%s\n' "$hot_info" | python3 -c '
import json,sys
d=json.load(sys.stdin)
print(json.dumps({"state":"ok","source":"library-hot","ok":True,
  "instance_dir":d["instance_dir"],"hub_path":d["hub_path"],
  "home_node_id":d["stamp"].get("home_node_id"),
  "content_id":d["stamp"].get("content_id"),
  "revision":d["stamp"].get("revision"),
  "identity_status":(d["stamp"].get("validation") or {}).get("identity_status"),
  "model_seal_id":(((d["stamp"].get("validation") or {}).get("expected_seal") or {}).get("seal_id")),
  "validation_bundle_id":(((d["stamp"].get("validation") or {}).get("expected_seal") or {}).get("validation_bundle_id")),
  "runtime_model_path":d.get("container_model_path"),
  "pinned":bool(d["stamp"].get("pinned"))}, indent=2, sort_keys=True))
'
  elif [ "${QUIET:-0}" = 1 ]; then
    identity_status=$(printf '%s' "$hot_info" | python3 -c 'import json,sys; print((json.load(sys.stdin)["stamp"].get("validation") or {}).get("identity_status") or "invalid")')
    echo "PASS  weights   source=library-hot · hot ready · identity=$identity_status"
  else
    echo "library-hot OK  instance=$instance"
    printf '%s\n' "$hot_info" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("hub", d["hub_path"]); print("runtime", d.get("container_model_path")); print("home", d["stamp"].get("home_node_id")); print("revision", d["stamp"].get("revision")); print("identity", (d["stamp"].get("validation") or {}).get("identity_status")); print("pinned", d["stamp"].get("pinned"))'
  fi
  exit 0
fi

SEALED_REPLICATED=0
if [ "$WEIGHT_SOURCE" = replicated ] &&
    [ "$(model_source_kind)" = hf ] &&
    [ -n "${EXPECTED_MODEL_SEAL:-}" ]; then
  load_replicated_identity_plan "$NAME"
  SEALED_REPLICATED=1
fi

weight_tree_state_local() {
  local root="${1:?}" config weight_dir index
  [ -d "$root" ] || { echo missing; return; }
  if find "$root" -type f -name '*.incomplete' -print -quit 2>/dev/null \
      | grep -q .; then
    echo partial
    return
  fi
  # Model repositories may contain nested component configs (for example
  # inference/config.json). Prefer the snapshot's root config so the weight
  # search runs in the actual model directory.
  config="$root/config.json"
  if [ ! -r "$config" ]; then
    config=$(find "$root" -name config.json -print -quit 2>/dev/null || true)
  fi
  [ -n "$config" ] && [ -r "$config" ] && [ -s "$config" ] \
    || { echo partial; return; }
  weight_dir=$(dirname "$config")
  if ! find -L "$weight_dir" -maxdepth 1 -type f \
      \( -name '*.safetensors' -o -name '*.bin' -o -name '*.gguf' \) \
      -size +0c -print -quit 2>/dev/null | grep -q .; then
    echo partial
    return
  fi
  index=$(find -L "$weight_dir" -maxdepth 1 -type f -name '*.index.json' \
    -print -quit 2>/dev/null || true)
  if [ -n "$index" ] && ! python3 - "$index" <<'PY'
import json
import pathlib
import sys

index = pathlib.Path(sys.argv[1])
try:
    data = json.loads(index.read_text(encoding="utf-8"))
    names = set((data.get("weight_map") or {}).values())
except (OSError, ValueError, AttributeError):
    raise SystemExit(1)
if not names or any(
    not (index.parent / name).is_file()
    or (index.parent / name).stat().st_size == 0
    for name in names
):
    raise SystemExit(1)
PY
  then
    echo partial
    return
  fi
  echo ok
}

hf_snapshot_path_local() {
  local hub="${1:?}" ref
  if [ "$SEALED_REPLICATED" = 1 ]; then
    ref="$REPLICATED_REVISION"
  else
    [ -s "$hub/refs/main" ] || return 3
    ref=$(tr -d '\r\n' <"$hub/refs/main")
  fi
  case "$ref" in
    *[!A-Za-z0-9._-]*|"") return 3 ;;
  esac
  [ -d "$hub/snapshots/$ref" ] || return 3
  printf '%s\n' "$hub/snapshots/$ref"
}

remote_exec() {
  local host="$1"
  shift
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" "$@"
}

hf_snapshot_path_remote() {
  local host="${1:?}" hub="${2:?}" qhub command
  qhub=$(printf '%q' "$hub")
  command="hub=$qhub; "
  if [ "$SEALED_REPLICATED" = 1 ]; then
    command+="ref=$(printf '%q' "$REPLICATED_REVISION"); "
  else
    command+="test -s \"\$hub/refs/main\" || exit 3; "
    command+="ref=\$(tr -d '\\r\\n' <\"\$hub/refs/main\"); "
  fi
  command+="case \"\$ref\" in ''|*[!A-Za-z0-9._-]*) exit 3;; esac; "
  command+="test -d \"\$hub/snapshots/\$ref\" || exit 3; "
  command+="printf '%s\\n' \"\$hub/snapshots/\$ref\""
  remote_exec "$host" "$command" 2>/dev/null
}

weight_tree_state_remote() {
  local host="${1:?}" root="${2:?}" qroot command python_check qpython
  qroot=$(printf '%q' "$root")
  python_check='import json,pathlib,sys; p=pathlib.Path(sys.argv[1]); data=json.loads(p.read_text(encoding="utf-8")); names=set((data.get("weight_map") or {}).values()); sys.exit(0 if names and all((p.parent / name).is_file() and (p.parent / name).stat().st_size > 0 for name in names) else 1)'
  qpython=$(printf '%q' "$python_check")
  command="root=$qroot; test -d \"\$root\" || { echo missing; exit 0; }; "
  command+="test -z \"\$(find \"\$root\" -type f -name '*.incomplete' -print -quit 2>/dev/null)\" "
  command+="|| { echo partial; exit 0; }; "
  command+="config=\"\$root/config.json\"; "
  command+="test -r \"\$config\" || "
  command+="config=\$(find \"\$root\" -name config.json -print -quit 2>/dev/null); "
  command+="test -n \"\$config\" -a -r \"\$config\" -a -s \"\$config\" "
  command+="|| { echo partial; exit 0; }; "
  command+="dir=\$(dirname \"\$config\"); "
  command+="test -n \"\$(find -L \"\$dir\" -maxdepth 1 -type f "
  command+="\\( -name '*.safetensors' -o -name '*.bin' -o -name '*.gguf' \\) "
  command+="-size +0c -print -quit 2>/dev/null)\" "
  command+="|| { echo partial; exit 0; }; "
  command+="index=\$(find -L \"\$dir\" -maxdepth 1 -type f "
  command+="-name '*.index.json' -print -quit 2>/dev/null); "
  command+="test -z \"\$index\" || python3 -c $qpython \"\$index\" "
  command+="|| { echo partial; exit 0; }; "
  command+="echo ok"
  remote_exec "$host" "$command" 2>/dev/null
}

kind=$(model_source_kind)
declare -a rank_states=()
for ((rank = 0; rank < NODES; rank++)); do
  rank_states[$rank]=unchecked
done

if [ "$NODES" -gt 1 ] && ! require_cluster_nodes "$NODES"; then
  for ((rank = 1; rank < NODES; rank++)); do
    rank_states[$rank]=unconfigured
  done
fi

if [ "$NODES" -eq 1 ] && [ "$SINGLE_NODE_REMOTE" = 1 ]; then
  host="$SINGLE_NODE_SSH_HOST"
  if ! remote_exec "$host" true >/dev/null 2>&1; then
    rank_states[0]=unreachable
  elif [ "$kind" = nfs ]; then
    remote_rc=0
    rank_states[0]=$(weight_tree_state_remote "$host" "$MODEL") || remote_rc=$?
    if [ "$remote_rc" != 0 ]; then
      rank_states[0]=unreachable
    elif [ "${rank_states[0]}" = missing ] \
        && ! remote_exec "$host" "test -d /mnt/Models" 2>/dev/null; then
      rank_states[0]=nfs-unmounted
    fi
  else
    hub=$(hf_hub_path)
    remote_ref_rc=0
    remote_snapshot=$(hf_snapshot_path_remote "$host" "$hub") || remote_ref_rc=$?
    case "$remote_ref_rc" in
      0)
        remote_rc=0
        rank_states[0]=$(weight_tree_state_remote "$host" "$remote_snapshot") \
          || remote_rc=$?
        [ "$remote_rc" = 0 ] || rank_states[0]=unreachable
        ;;
      3)
        remote_rc=0
        remote_presence=$(remote_exec "$host" \
          "test -d $(printf '%q' "$hub") && echo partial || echo missing") \
          || remote_rc=$?
        [ "$remote_rc" = 0 ] \
          && rank_states[0]="$remote_presence" || rank_states[0]=unreachable
        ;;
      *) rank_states[0]=unreachable ;;
    esac
  fi
elif [ "$kind" = nfs ]; then
  rank_states[0]=$(weight_tree_state_local "$MODEL")
  if [ "${rank_states[0]}" = missing ] \
      && [[ "$MODEL" == /mnt/Models* ]] && [ ! -d /mnt/Models ]; then
    rank_states[0]=nfs-unmounted
  fi
else
  hub=$(hf_hub_path)
  local_ref_rc=0
  snapshot=$(hf_snapshot_path_local "$hub") || local_ref_rc=$?
  case "$local_ref_rc" in
    0) rank_states[0]=$(weight_tree_state_local "$snapshot") ;;
    *) [ -d "$hub" ] && rank_states[0]=partial || rank_states[0]=missing ;;
  esac
fi

for ((rank = 1; rank < NODES; rank++)); do
  [ "${rank_states[$rank]}" = unconfigured ] && continue
  host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
  if ! remote_exec "$host" true >/dev/null 2>&1; then
    rank_states[$rank]=unreachable
    continue
  fi
  if [ "$kind" = nfs ]; then
    remote_rc=0
    rank_states[$rank]=$(weight_tree_state_remote "$host" "$MODEL") || remote_rc=$?
    if [ "$remote_rc" != 0 ]; then
      rank_states[$rank]=unreachable
    elif [ "${rank_states[$rank]}" = missing ] \
        && ! remote_exec "$host" "test -d /mnt/Models" 2>/dev/null; then
      rank_states[$rank]=nfs-unmounted
    fi
  else
    remote_ref_rc=0
    remote_snapshot=$(hf_snapshot_path_remote "$host" "$hub") || remote_ref_rc=$?
    case "$remote_ref_rc" in
      0)
        remote_rc=0
        rank_states[$rank]=$(weight_tree_state_remote "$host" "$remote_snapshot") \
          || remote_rc=$?
        [ "$remote_rc" = 0 ] || rank_states[$rank]=unreachable
        ;;
      3)
        remote_rc=0
        remote_presence=$(remote_exec "$host" \
          "test -d $(printf '%q' "$hub") && echo partial || echo missing") \
          || remote_rc=$?
        if [ "$remote_rc" = 0 ]; then
          rank_states[$rank]="$remote_presence"
        else
          rank_states[$rank]=unreachable
        fi
        ;;
      *) rank_states[$rank]=unreachable ;;
    esac
  fi
done

if [ "$SEALED_REPLICATED" = 1 ]; then
  for ((rank = 0; rank < NODES; rank++)); do
    [ "${rank_states[$rank]}" = ok ] || continue
    verify_rc=0
    if [ "$NODES" = 1 ] && [ "${SINGLE_NODE_REMOTE:-0}" = 1 ]; then
      verify_replicated_identity_remote "$SINGLE_NODE_SSH_HOST" serve >/dev/null ||
        verify_rc=$?
    elif [ "$rank" = 0 ]; then
      verify_replicated_identity_local serve >/dev/null || verify_rc=$?
    else
      verify_replicated_identity_remote "${CLUSTER_NODE_SSH_HOSTS[$rank]}" serve >/dev/null ||
        verify_rc=$?
    fi
    if [ "$verify_rc" != 0 ]; then
      rank_states[$rank]=identity-mismatch
    fi
  done
fi

state=ok
for ((rank = 0; rank < NODES; rank++)); do
  rank_state="${rank_states[$rank]}"
  [ "$rank_state" = ok ] && continue
  if [ "$rank_state" = identity-mismatch ]; then
    state=identity-mismatch
    continue
  fi
  if [ "$rank" = 0 ]; then
    state="$rank_state"
  elif [ "$rank_state" = unreachable ]; then
    [ "$rank" = 1 ] && [ "$NODES" = 2 ] \
      && state=worker-unreachable || state=rank-unreachable
  elif [ "$rank_state" = unconfigured ]; then
    state=need-topology
  elif [ "$rank_state" = partial ]; then
    if [ "$state" = ok ]; then
      [ "$rank" = 1 ] && [ "$NODES" = 2 ] \
        && state=partial-on-worker || state=partial-on-rank
    fi
  else
    if [ "$state" = ok ]; then
      [ "$rank" = 1 ] && [ "$NODES" = 2 ] \
        && state=missing-on-worker || state=missing-on-rank
    fi
  fi
done
for ((rank = 0; rank < NODES; rank++)); do
  if [ "${rank_states[$rank]}" = identity-mismatch ]; then
    state=identity-mismatch
  fi
done

path_out="$MODEL"
[ "$kind" = hf ] && path_out=$(hf_hub_path)
head_state="${rank_states[0]}"
worker_state="${rank_states[1]:-n/a}"
identity_status="unvalidated"
model_revision=""
model_seal_id=""
validation_bundle_id=""
manifest_id=""
if [ "$SEALED_REPLICATED" = 1 ]; then
  identity_status=unverified
  [ "$state" = ok ] && identity_status=match
  [ "$state" = identity-mismatch ] && identity_status=mismatch
  model_revision="$REPLICATED_REVISION"
  model_seal_id="$REPLICATED_MODEL_SEAL_ID"
  validation_bundle_id="$REPLICATED_VALIDATION_BUNDLE_ID"
  manifest_id="$REPLICATED_MANIFEST_ID"
elif [[ "$STATUS" == tested* ]]; then
  identity_status=legacy-unsealed
fi

if [ "$JSON" = 1 ]; then
  states_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-weight-states.XXXXXX")
  trap 'rm -f "$states_file"' EXIT
  for ((rank = 0; rank < NODES; rank++)); do
    printf '%s\t%s\n' "$rank" "${rank_states[$rank]}" >>"$states_file"
  done
  MODEL_V="$NAME" KIND_V="$kind" NODES_V="$NODES" STATE_V="$state" \
  HEAD_V="$head_state" WORKER_V="$worker_state" PATH_V="$path_out" \
  IDENTITY_V="$identity_status" REVISION_V="$model_revision" \
  SEAL_V="$model_seal_id" BUNDLE_V="$validation_bundle_id" \
  MANIFEST_V="$manifest_id" \
  PLACEMENT_INDEX_V="${SINGLE_NODE_INDEX:-}" \
  PLACEMENT_KEY_V="${SINGLE_NODE_KEY:-}" \
  PLACEMENT_ID_V="${SINGLE_NODE_ID:-}" \
  PLACEMENT_HOSTNAME_V="${SINGLE_NODE_HOSTNAME:-}" \
  PLACEMENT_SSH_V="${SINGLE_NODE_SSH_HOST:-}" \
  PLACEMENT_REMOTE_V="${SINGLE_NODE_REMOTE:-0}" \
  STATES_FILE="$states_file" python3 - <<'PY'
import json
import os

ranks = []
with open(os.environ["STATES_FILE"], encoding="utf-8") as handle:
    for line in handle:
        rank, state = line.rstrip("\n").split("\t", 1)
        identity = os.environ["IDENTITY_V"]
        if identity in {"match", "mismatch", "unverified"}:
            if state == "ok":
                identity = "match"
            elif state == "identity-mismatch":
                identity = "mismatch"
            else:
                identity = "unverified"
        ranks.append({
            "rank": int(rank),
            "state": state,
            "ok": state == "ok",
            "identity_status": identity,
        })
placement = None
if int(os.environ["NODES_V"]) == 1:
    placement = {
        "topology_index": int(os.environ.get("PLACEMENT_INDEX_V") or 0),
        "node_key": os.environ.get("PLACEMENT_KEY_V") or "head",
        "node_id": os.environ.get("PLACEMENT_ID_V") or None,
        "hostname": os.environ.get("PLACEMENT_HOSTNAME_V") or None,
        "ssh_host": os.environ.get("PLACEMENT_SSH_V") or None,
        "remote": os.environ.get("PLACEMENT_REMOTE_V") == "1",
    }
    ranks[0].update(placement)
print(json.dumps({
    "model": os.environ["MODEL_V"],
    "source": os.environ["KIND_V"],
    "nodes": int(os.environ["NODES_V"]),
    "state": os.environ["STATE_V"],
    "head": os.environ["HEAD_V"],
    "worker": os.environ["WORKER_V"],
    "identity_status": os.environ["IDENTITY_V"],
    "model_revision": os.environ["REVISION_V"] or None,
    "model_seal_id": os.environ["SEAL_V"] or None,
    "validation_bundle_id": os.environ["BUNDLE_V"] or None,
    "manifest_id": os.environ["MANIFEST_V"] or None,
    "placement": placement,
    "ranks": ranks,
    "path": os.environ["PATH_V"],
}, indent=2))
PY
else
  summary=""
  for ((rank = 0; rank < NODES; rank++)); do
    summary+=" r${rank}=${rank_states[$rank]}"
  done
  if [ "${QUIET:-0}" = 1 ]; then
    [ "$state" = ok ] \
      && echo "PASS  weights   source=$kind · identity=$identity_status ·${summary}" \
      || echo "FAIL  weights   state=$state · identity=$identity_status ·${summary}"
  else
    [ "$state" = ok ] \
      && title="MODEL FILES READY" || title="MODEL FILES NOT READY"
    [ "$kind" = hf ] \
      && source_display="Hugging Face" || source_display="Local or NFS"
    fields=("Model" "$NAME" "Source" "$source_display" "Identity" "$identity_status")
    for ((rank = 0; rank < NODES; rank++)); do
      if [ "$NODES" -eq 1 ]; then
        node="$SINGLE_NODE_HOSTNAME"
      else
        node="${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
      fi
      if [ -z "$node" ]; then
        if [ "$rank" = 0 ]; then
          node=$(hostname -s)
        else
          node="${CLUSTER_NODE_SSH_HOSTS[$rank]:-cluster-node-$((rank + 1))}"
          node="${node%%.*}"
        fi
      fi
      if [ "$NODES" -eq 1 ]; then
        [ "$SINGLE_NODE_REMOTE" = 0 ] && node+=" (this node)"
      elif [ "$rank" = 0 ]; then
        node+=" (this node)"
      fi
      case "${rank_states[$rank]}" in
        ok) display_state="ready" ;;
        partial) display_state="incomplete" ;;
        missing) display_state="missing" ;;
        unreachable) display_state="unreachable" ;;
        unconfigured) display_state="not confirmed" ;;
        nfs-unmounted) display_state="NFS not mounted" ;;
        identity-mismatch) display_state="identity mismatch" ;;
        *) display_state="${rank_states[$rank]}" ;;
      esac
      [ "$rank" = 0 ] && label="Status" || label=""
      fields+=("$label" "$node · $display_state")
    done
    if [ "${PULSAR_VERBOSE:-0}" = 1 ]; then
      fields+=("Location" "$path_out")
    fi
    if [ "$state" != ok ]; then
      case "$state" in
        worker-unreachable|rank-unreachable)
          next="Restore SSH access to every node used by this model, then retry."
          ;;
        need-topology)
          next="Run ./pulsar wizard and confirm at least $NODES nodes."
          ;;
        *)
          [ "$kind" = nfs ] \
            && next="Mount the model path on every required node, then retry." \
            || next="Run scripts/pull-weights.sh $NAME --yes."
          ;;
      esac
      fields+=("Next" "$next")
    fi
    render_human_section "$title" "${fields[@]}"
  fi
fi

[ "$state" = ok ]
