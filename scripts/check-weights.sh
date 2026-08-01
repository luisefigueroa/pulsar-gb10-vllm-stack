#!/usr/bin/env bash
# Check whether conf weights exist on required nodes.
#   scripts/check-weights.sh <model-name> [--json]
# exit 0=ok 1=fail
set -euo pipefail
SCRIPT_NAME=check-weights
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--json]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
kind=$(model_source_kind)
head_state=missing
worker_state=n/a

if [ "$kind" = nfs ]; then
  if [ -r "$MODEL/config.json" ]; then head_state=ok
  elif [ -d "$MODEL" ]; then head_state=partial
  elif [[ "$MODEL" == /mnt/Models* ]] && [ ! -d /mnt/Models ]; then head_state=nfs-unmounted
  else head_state=missing
  fi
  if [ "$NODES" = "2" ]; then
    [ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP in .env"
    if ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" "test -r $(printf '%q' "$MODEL/config.json")" 2>/dev/null; then
      worker_state=ok
    elif ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" "test -d $(printf '%q' "$MODEL")" 2>/dev/null; then
      worker_state=partial
    elif ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" "test -d /mnt/Models" 2>/dev/null; then
      worker_state=missing
    else
      worker_state=nfs-unmounted
    fi
  fi
else
  hub=$(hf_hub_path)
  if [ -d "$hub" ] && find "$hub" -name config.json 2>/dev/null | head -1 | grep -q .; then
    head_state=ok
  elif [ -d "$hub" ]; then
    head_state=partial
  else
    head_state=missing
  fi
  if [ "$NODES" = "2" ]; then
    [ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP in .env"
    if ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" \
      "test -d $(printf '%q' "$hub") && find $(printf '%q' "$hub") -name config.json 2>/dev/null | head -1 | grep -q ."; then
      worker_state=ok
    elif ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" "test -d $(printf '%q' "$hub")" 2>/dev/null; then
      worker_state=partial
    else
      worker_state=missing
    fi
  fi
fi

state=ok
if [ "$head_state" != ok ]; then
  state=$head_state
fi
if [ "$NODES" = "2" ] && [ "$worker_state" != ok ]; then
  if [ "$state" = ok ]; then
    state=missing-on-worker
  fi
fi

path_out="$MODEL"
[ "$kind" = hf ] && path_out=$(hf_hub_path)

if [ "$JSON" = 1 ]; then
  python3 - <<PY
import json
print(json.dumps({
  "model": "$NAME",
  "source": "$kind",
  "nodes": int("$NODES"),
  "state": "$state",
  "head": "$head_state",
  "worker": "$worker_state",
  "path": r"""$path_out""",
}, indent=2))
PY
else
  if [ "${QUIET:-0}" = 1 ]; then
    if [ "$state" = ok ]; then
      echo "PASS  weights   source=$kind head=$head_state worker=$worker_state"
    else
      echo "FAIL  weights   state=$state head=$head_state worker=$worker_state"
      [ "$kind" = nfs ] && echo "      fix: mount catalog / path $path_out" || echo "      fix: scripts/pull-weights.sh $NAME"
    fi
  else
    log "$NAME source=$kind state=$state head=$head_state worker=$worker_state"
    log "path=$path_out"
    if [ "$state" != ok ]; then
      if [ "$kind" = nfs ]; then
        warn "NFS/catalog weights missing or unreadable. Mount $MODELS_NFS or copy the catalog path; pull-weights will not auto-fetch NFS models."
      else
        warn "HF weights incomplete. Run: scripts/pull-weights.sh $NAME"
      fi
    fi
  fi
fi

[ "$state" = ok ]
