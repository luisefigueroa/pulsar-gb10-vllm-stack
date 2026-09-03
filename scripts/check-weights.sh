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

CATALOG_FILE="$PULSAR_MODEL_LIBRARY_CATALOG"

emit_weights_gap() {
  local reason="$1" remediation="$2" detail="${3:-}" failed_rank="${4:-}"
  if [ "$JSON" = 1 ]; then
    NAME_V="$NAME" NODES_V="$NODES" REASON_V="$reason" \
      REMEDIATION_V="$remediation" DETAIL_V="$detail" RANK_V="$failed_rank" \
      python3 -c '
import json, os
payload = {
    "state": "missing", "source": "local-files", "ok": False,
    "model": os.environ["NAME_V"], "nodes": int(os.environ["NODES_V"]),
    "reason": os.environ["REASON_V"],
    "remediation": os.environ["REMEDIATION_V"],
}
detail = os.environ.get("DETAIL_V") or ""
if detail:
    payload["detail"] = detail
rank = os.environ.get("RANK_V") or ""
if rank:
    payload["failed_rank"] = int(rank)
print(json.dumps(payload, indent=2, sort_keys=True))
'
  elif [ "${QUIET:-0}" = 1 ]; then
    echo "FAIL  weights   $detail — run: $remediation"
  else
    echo "$detail — run: $remediation" >&2
  fi
}

hot_rc=0
hot_info=$(library_hot_info_for_profile "$NAME") || hot_rc=$?
if [ "$hot_rc" -ne 0 ]; then
  one_node_rank=""
  if [ "$NODES" -eq 1 ]; then
    one_node_rank="$SINGLE_NODE_INDEX"
  fi
  if [ "$NODES" -eq 1 ] && [ "$hot_rc" -eq 255 ]; then
    emit_weights_gap \
      "rank-unreachable" \
      "./pulsar inventory" \
      "rank $SINGLE_NODE_INDEX is unreachable; restore SSH to that confirmed node, then re-check. Do not restage while the rank is unobservable" \
      "$SINGLE_NODE_INDEX"
    exit 1
  fi
  if [ "$NODES" -eq 1 ] && [ "$hot_rc" -eq 2 ]; then
    emit_weights_gap \
      "identity-mismatch" \
      "scripts/model-library.sh health" \
      "rank $SINGLE_NODE_INDEX runtime view failed verification; inspect health, then prepare $NAME --yes only if that view is missing or corrupt" \
      "$SINGLE_NODE_INDEX"
    exit 1
  fi
  classify_args=(
    --profile "$NAME"
    --catalog "$CATALOG_FILE"
    --topology-id "$CLUSTER_TOPOLOGY_ID"
  )
  if [ "${CONF_SOURCE:-conf}" = spec ]; then
    classify_args+=(--identity "${MODEL}@${SNAPSHOT_REVISION}")
  else
    classify_args+=(--models-dir "$REPO_DIR/models")
  fi
  if [ "$NODES" -eq 1 ]; then
    classify_args+=(--selected-rank "$SINGLE_NODE_INDEX")
    if [ -n "${SINGLE_NODE_ID:-}" ]; then
      classify_args+=(--selected-node-id "$SINGLE_NODE_ID")
    fi
  fi
  gap=$(python3 "$PULSAR_MODEL_LIBRARY_PY" classify-library-readiness \
    "${classify_args[@]}") || gap="{}"
  mapfile -t gap_fields < <(json_fields "$gap" reason remediation detail)
  reason="${gap_fields[0]:-views-missing}"
  remediation="${gap_fields[1]:-scripts/model-library.sh prepare $NAME --yes}"
  detail="${gap_fields[2]:-model files are not prepared}"
  if [ "${CONF_SOURCE:-conf}" = spec ]; then
    remediation="scripts/model-library.sh prepare $NAME --yes"
    if [ -z "${gap_fields[2]:-}" ]; then
      detail="no ready view for released spec $NAME (${MODEL}@${SNAPSHOT_REVISION})"
    fi
  fi
  emit_weights_gap "$reason" "$remediation" "$detail" "$one_node_rank"
  exit 1
fi
mapfile -t hot_fields < <(json_fields "$hot_info" instance_dir stamp.validation)
instance="${hot_fields[0]:-}"
expected_validation_json="${hot_fields[1]:-null}"

# Readiness is an all-rank claim: the local resolution above proves rank 0
# only, so verify every remote serving rank's view before reporting ok.
if [ "$NODES" -gt 1 ]; then
  set_library_verify_profile_args
  for ((verify_rank = 1; verify_rank < NODES; verify_rank++)); do
    verify_command=$(shell_join_q python3 - verify-hot \
      --instance-dir "$instance" \
      "${LIBRARY_VERIFY_PROFILE_ARGS[@]}" \
      --topology-id "$CLUSTER_TOPOLOGY_ID" \
      --expected-validation-json "$expected_validation_json" \
      --for-launch \
      --serve-time-witness)
    verify_rc=0
    ssh_node "$verify_rank" "$verify_command" \
        <"$PULSAR_MODEL_LIBRARY_PY" >/dev/null 2>&1 || verify_rc=$?
    if [ "$verify_rc" -eq 0 ]; then
      continue
    fi
    if [ "$verify_rc" -eq 255 ]; then
      emit_weights_gap \
        "rank-unreachable" \
        "./pulsar inventory" \
        "rank $verify_rank is unreachable; restore SSH to that confirmed node, then re-check. Do not restage while the rank is unobservable" \
        "$verify_rank"
    else
      emit_weights_gap \
        "identity-mismatch" \
        "scripts/model-library.sh health" \
        "rank $verify_rank runtime view failed verification; inspect health, then prepare $NAME --yes only if that view is missing or corrupt" \
        "$verify_rank"
    fi
    exit 1
  done
fi

if [ "$JSON" = 1 ]; then
  printf '%s\n' "$hot_info" | NAME_V="$NAME" NODES_V="$NODES" python3 -c '
import json, os, sys
d = json.load(sys.stdin)
print(json.dumps({"state": "ok", "source": "local-files", "ok": True,
  "model": os.environ["NAME_V"], "nodes": int(os.environ["NODES_V"]),
  "instance_dir": d["instance_dir"], "hub_path": d["hub_path"],
  "home_node_id": d["stamp"].get("home_node_id"),
  "content_id": d["stamp"].get("content_id"),
  "revision": d["stamp"].get("revision"),
  "identity_status": (d["stamp"].get("validation") or {}).get("identity_status"),
  "runtime_model_path": d.get("container_model_path"),
  "pinned": bool(d["stamp"].get("pinned"))}, indent=2, sort_keys=True))
'
elif [ "${QUIET:-0}" = 1 ]; then
  identity_status=$(printf '%s' "$hot_info" | python3 -c 'import json,sys; print((json.load(sys.stdin)["stamp"].get("validation") or {}).get("identity_status") or "invalid")')
  case "$identity_status" in
    receipt-occupancy) identity_label="receipt/occupancy" ;;
    *) identity_label="$identity_status" ;;
  esac
  echo "PASS  weights   model files ready · identity=$identity_label"
else
  echo "model files OK  instance=$instance"
  printf '%s\n' "$hot_info" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("hub", d["hub_path"]); print("runtime", d.get("container_model_path")); print("home", d["stamp"].get("home_node_id")); print("revision", d["stamp"].get("revision")); print("identity", (d["stamp"].get("validation") or {}).get("identity_status")); print("pinned", d["stamp"].get("pinned"))'
fi
exit 0
