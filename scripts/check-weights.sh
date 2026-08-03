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

weight_tree_state_local() {
  local root="${1:?}" config weight_dir index
  [ -d "$root" ] || { echo missing; return; }
  if find "$root" -type f -name '*.incomplete' -print -quit 2>/dev/null \
    | grep -q .; then
    echo partial
    return
  fi
  config=$(find "$root" -name config.json -print -quit 2>/dev/null || true)
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
if not names or any(not (index.parent / name).is_file() or (index.parent / name).stat().st_size == 0 for name in names):
    raise SystemExit(1)
PY
  then
    echo partial
    return
  fi
  echo ok
}

weight_tree_state_remote() {
  local root="${1:?}" qroot cmd
  qroot=$(printf '%q' "$root")
  cmd="root=$qroot; test -d \"\$root\" || { echo missing; exit 0; }; "
  cmd+="test -z \"\$(find \"\$root\" -type f -name '*.incomplete' -print -quit 2>/dev/null)\" || { echo partial; exit 0; }; "
  cmd+="config=\$(find \"\$root\" -name config.json -print -quit 2>/dev/null); test -n \"\$config\" -a -r \"\$config\" -a -s \"\$config\" || { echo partial; exit 0; }; "
  cmd+="dir=\$(dirname \"\$config\"); test -n \"\$(find -L \"\$dir\" -maxdepth 1 -type f \( -name '*.safetensors' -o -name '*.bin' -o -name '*.gguf' \) -size +0c -print -quit 2>/dev/null)\" || { echo partial; exit 0; }; echo ok"
  ssh_worker "$cmd" 2>/dev/null
}

kind=$(model_source_kind)
head_state=missing
worker_state=n/a

if [ "$kind" = nfs ]; then
  head_state=$(weight_tree_state_local "$MODEL")
  if [ "$head_state" = missing ] && [[ "$MODEL" == /mnt/Models* ]] && [ ! -d /mnt/Models ]; then
    head_state=nfs-unmounted
  fi
  if [ "$NODES" = "2" ]; then
    [ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP in .env"
    if ! ssh_worker true >/dev/null 2>&1; then
      worker_state=unreachable
    else
      worker_state=$(weight_tree_state_remote "$MODEL")
      if [ "$worker_state" = missing ] \
        && ! ssh_worker "test -d /mnt/Models" 2>/dev/null; then
        worker_state=nfs-unmounted
      fi
    fi
  fi
else
  hub=$(hf_hub_path)
  head_state=$(weight_tree_state_local "$hub")
  if [ "$NODES" = "2" ]; then
    [ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP in .env"
    if ! ssh_worker true >/dev/null 2>&1; then
      worker_state=unreachable
    else
      worker_state=$(weight_tree_state_remote "$hub")
    fi
  fi
fi

state=ok
if [ "$head_state" != ok ]; then
  state=$head_state
fi
if [ "$NODES" = "2" ] && [ "$worker_state" != ok ]; then
  case "$worker_state" in
    unreachable) state=worker-unreachable ;;
    partial) [ "$state" = ok ] && state=partial-on-worker ;;
    *) [ "$state" = ok ] && state=missing-on-worker ;;
  esac
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
      if [ "$state" = worker-unreachable ]; then
        warn "worker SSH unreachable — no weight download or sync attempted"
      elif [ "$kind" = nfs ]; then
        warn "NFS/catalog weights missing or unreadable. Mount $MODELS_NFS or copy the catalog path; pull-weights will not auto-fetch NFS models."
      else
        warn "HF weights missing or incomplete. Run: scripts/pull-weights.sh $NAME"
      fi
    fi
  fi
fi

[ "$state" = ok ]
