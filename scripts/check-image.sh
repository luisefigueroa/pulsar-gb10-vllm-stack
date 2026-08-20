#!/usr/bin/env bash
# Check Docker image presence on every active rank of an exact profile.
#   scripts/check-image.sh <model-name> [--node NODE_ID] [--json]
set -euo pipefail
SCRIPT_NAME=check-image
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
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement '$NODE_SELECTOR'"
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node is only valid for one-node profiles" 2
fi
state=ok
declare -a rank_states=()
for ((rank = 0; rank < NODES; rank++)); do
  rank_states[$rank]=unchecked
done

if [ "$NODES" -eq 1 ] && [ "$SINGLE_NODE_REMOTE" = 1 ]; then
  if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$SINGLE_NODE_SSH_HOST" true \
      >/dev/null 2>&1; then
    state=target-unreachable
    rank_states[0]=unreachable
  elif ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$SINGLE_NODE_SSH_HOST" \
      "docker info >/dev/null 2>&1"; then
    state=target-docker-error
    rank_states[0]=docker-error
  elif "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$SINGLE_NODE_SSH_HOST" \
      "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
    rank_states[0]=ok
  else
    state=missing-on-target
    rank_states[0]=missing
  fi
elif ! "$PULSAR_DOCKER" info >/dev/null 2>&1; then
  state=head-docker-error
  rank_states[0]=docker-error
elif "$PULSAR_DOCKER" image inspect "$IMAGE" >/dev/null 2>&1; then
  rank_states[0]=ok
else
  state=missing-on-head
  rank_states[0]=missing
fi

if [ "$NODES" -gt 1 ] && [ "$state" != head-docker-error ]; then
  if ! require_cluster_nodes "$NODES"; then
    state=need-topology
  else
    for ((rank = 1; rank < NODES; rank++)); do
      host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
      if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" true \
          >/dev/null 2>&1; then
        rank_states[$rank]=unreachable
        if [ "$rank" = 1 ] && [ "$NODES" = 2 ]; then
          state=worker-unreachable
        else
          state=rank-unreachable
        fi
        continue
      fi
      if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
          "docker info >/dev/null 2>&1"; then
        rank_states[$rank]=docker-error
        if [ "$rank" = 1 ] && [ "$NODES" = 2 ]; then
          state=worker-docker-error
        else
          state=rank-docker-error
        fi
        continue
      fi
      if "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
          "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
        rank_states[$rank]=ok
      else
        rank_states[$rank]=missing
        if [ "$rank" = 1 ] && [ "$NODES" = 2 ] \
            && [ "${rank_states[0]}" = ok ]; then
          state=missing-on-worker
        elif [ "${rank_states[0]}" = missing ]; then
          state=missing-both
        else
          state=missing-on-rank
        fi
      fi
    done
  fi
fi

head_ok=0
worker_ok=0
[ "${rank_states[0]:-}" = ok ] && head_ok=1
[ "${rank_states[1]:-}" = ok ] && worker_ok=1

if [ "$JSON" = 1 ]; then
  states_file=$(mktemp "${TMPDIR:-/tmp}/pulsar-image-states.XXXXXX")
  trap 'rm -f "$states_file"' EXIT
  for ((rank = 0; rank < NODES; rank++)); do
    printf '%s\t%s\n' "$rank" "${rank_states[$rank]}" >>"$states_file"
  done
  MODEL_NAME_V="$NAME" IMAGE_V="$IMAGE" NODES_V="$NODES" STATE_V="$state" \
  HEAD_OK_V="$head_ok" WORKER_OK_V="$worker_ok" STATES_FILE="$states_file" \
  PLACEMENT_INDEX_V="${SINGLE_NODE_INDEX:-}" \
  PLACEMENT_KEY_V="${SINGLE_NODE_KEY:-}" \
  PLACEMENT_ID_V="${SINGLE_NODE_ID:-}" \
  PLACEMENT_HOSTNAME_V="${SINGLE_NODE_HOSTNAME:-}" \
  PLACEMENT_SSH_V="${SINGLE_NODE_SSH_HOST:-}" \
  PLACEMENT_REMOTE_V="${SINGLE_NODE_REMOTE:-0}" \
    python3 - <<'PY'
import json
import os

ranks = []
with open(os.environ["STATES_FILE"], encoding="utf-8") as handle:
    for line in handle:
        rank, state = line.rstrip("\n").split("\t", 1)
        ranks.append({"rank": int(rank), "state": state, "ok": state == "ok"})
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
    "model": os.environ["MODEL_NAME_V"],
    "image": os.environ["IMAGE_V"],
    "nodes": int(os.environ["NODES_V"]),
    "state": os.environ["STATE_V"],
    "head_ok": os.environ["HEAD_OK_V"] == "1",
    "worker_ok": os.environ["WORKER_OK_V"] == "1",
    "placement": placement,
    "ranks": ranks,
}, indent=2))
PY
else
  summary=""
  for ((rank = 0; rank < NODES; rank++)); do
    summary+=" r${rank}=${rank_states[$rank]}"
  done
  if [ "${QUIET:-0}" = 1 ]; then
    if [ "$state" = ok ]; then
      echo "PASS  image     $IMAGE ·${summary# }"
    else
      echo "FAIL  image     state=$state ·${summary# }"
    fi
  else
    log "$NAME image=$IMAGE state=$state ·${summary# }"
    case "$state" in
      ok) ;;
      need-topology)
        warn "confirm at least $NODES nodes: scripts/detect-fabric.sh --write-topology"
        ;;
      head-docker-error|target-docker-error)
        warn "Docker is unavailable on the selected node"
        ;;
      target-unreachable)
        warn "selected node is unreachable over BatchMode SSH"
        ;;
      worker-unreachable|rank-unreachable)
        warn "one or more required ranks are unreachable over BatchMode SSH"
        ;;
      worker-docker-error|rank-docker-error)
        warn "one or more required ranks have an unavailable Docker daemon"
        ;;
      *)
        warn "stage $IMAGE to every active rank: scripts/sync-image.sh $NAME"
        ;;
    esac
  fi
fi

[ "$state" = ok ]
