#!/usr/bin/env bash
# Check whether the exact profile's model files are prepared for serving.
#   scripts/check-weights.sh <model-name> [--node NODE_ID] [--json]
#
# The model library is the only weight-distribution mechanism (ADR 0006):
# ready means a prepared, identity-validated hot instance (or durable-home
# view) exists for the profile's confirmed topology.
set -euo pipefail
SCRIPT_NAME=check-weights
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0
NODE_SELECTOR=""
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
    --weight-source|--weight-mode)
      refuse_removed_weight_mode_flag
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done
acquire_model_library_lifecycle_lock shared
load_conf "$NAME"
acquire_model_library_hot_lock shared
[ "$(model_source_kind)" = hf ] \
  || die "non-HF model profiles are not servable (ADR 0006)"
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement '$NODE_SELECTOR'"
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node is only valid for one-node profiles" 2
fi

if [ -z "${CLUSTER_TOPOLOGY_ID:-}" ] && [ -n "${SINGLE_NODE_TOPOLOGY_ID:-}" ]; then
  CLUSTER_TOPOLOGY_ID="$SINGLE_NODE_TOPOLOGY_ID"
fi
load_cluster_topology >/dev/null 2>&1 && [ -n "${CLUSTER_TOPOLOGY_ID:-}" ] \
  || die "serving requires a confirmed topology manifest (one machine is fine): run scripts/detect-fabric.sh --write-topology"

if ! hot_info=$(library_hot_info_for_profile "$NAME"); then
  if [ "$JSON" = 1 ]; then
    NAME_V="$NAME" NODES_V="$NODES" python3 -c '
import json, os
print(json.dumps({
    "state": "missing", "source": "library-hot", "ok": False,
    "model": os.environ["NAME_V"], "nodes": int(os.environ["NODES_V"]),
}, indent=2, sort_keys=True))'
  elif [ "${QUIET:-0}" = 1 ]; then
    echo "FAIL  weights   model files are not prepared"
  else
    echo "model files are not prepared — run: scripts/model-library.sh prepare $NAME --yes" >&2
  fi
  exit 1
fi
instance=$(printf '%s' "$hot_info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
if [ "$JSON" = 1 ]; then
  printf '%s\n' "$hot_info" | NAME_V="$NAME" NODES_V="$NODES" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
print(json.dumps({"state": "ok", "source": "library-hot", "ok": True,
  "model": os.environ["NAME_V"], "nodes": int(os.environ["NODES_V"]),
  "instance_dir": d["instance_dir"], "hub_path": d["hub_path"],
  "home_node_id": d["stamp"].get("home_node_id"),
  "content_id": d["stamp"].get("content_id"),
  "revision": d["stamp"].get("revision"),
  "identity_status": (d["stamp"].get("validation") or {}).get("identity_status"),
  "model_seal_id": (((d["stamp"].get("validation") or {}).get("expected_seal") or {}).get("seal_id")),
  "validation_bundle_id": (((d["stamp"].get("validation") or {}).get("expected_seal") or {}).get("validation_bundle_id")),
  "runtime_model_path": d.get("container_model_path"),
  "pinned": bool(d["stamp"].get("pinned"))}, indent=2, sort_keys=True))
'
elif [ "${QUIET:-0}" = 1 ]; then
  identity_status=$(printf '%s' "$hot_info" | python3 -c 'import json,sys; print((json.load(sys.stdin)["stamp"].get("validation") or {}).get("identity_status") or "invalid")')
  echo "PASS  weights   model files ready · identity=$identity_status"
else
  echo "model files OK  instance=$instance"
  printf '%s\n' "$hot_info" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("hub", d["hub_path"]); print("runtime", d.get("container_model_path")); print("home", d["stamp"].get("home_node_id")); print("revision", d["stamp"].get("revision")); print("identity", (d["stamp"].get("validation") or {}).get("identity_status")); print("pinned", d["stamp"].get("pinned"))'
fi
exit 0
