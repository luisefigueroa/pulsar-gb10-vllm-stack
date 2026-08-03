#!/usr/bin/env bash
# Check docker image presence for a conf on required nodes.
#   scripts/check-image.sh <model-name> [--json]
set -euo pipefail
SCRIPT_NAME=check-image
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
head_ok=0 worker_ok=0 state=ok

if [ "$NODES" = "2" ]; then
  if [ -z "${WORKER_IP:-}" ]; then
    state=need-worker-ip
    if [ "$JSON" = 1 ]; then
      python3 - <<PY
import json
print(json.dumps({"model":"$NAME","image":"$IMAGE","nodes":2,"state":"need-worker-ip","head_ok":False,"worker_ok":False}, indent=2))
PY
    else
      log "$NAME image=$IMAGE state=need-worker-ip"
      warn "set WORKER_IP in .env for 2-node image checks"
    fi
    exit 1
  fi
fi

if ! "$PULSAR_DOCKER" info >/dev/null 2>&1; then
  state=head-docker-error
elif "$PULSAR_DOCKER" image inspect "$IMAGE" >/dev/null 2>&1; then
  head_ok=1
else
  state=missing-on-head
fi

if [ "$NODES" = "2" ] && [ "$state" != head-docker-error ]; then
  if ! ssh_worker true >/dev/null 2>&1; then
    state=worker-unreachable
  elif ! ssh_worker "docker info >/dev/null 2>&1"; then
    state=worker-docker-error
  elif ssh_worker "docker image inspect $(printf '%q' "$IMAGE") >/dev/null 2>&1"; then
    worker_ok=1
  else
    if [ "$head_ok" = 1 ]; then
      state=missing-on-worker
    else
      state=missing-both
    fi
  fi
fi

if [ "$JSON" = 1 ]; then
  python3 - <<PY
import json
print(json.dumps({
  "model": "$NAME",
  "image": "$IMAGE",
  "nodes": int("$NODES"),
  "state": "$state",
  "head_ok": bool($head_ok),
  "worker_ok": bool($worker_ok),
}, indent=2))
PY
else
  if [ "${QUIET:-0}" = 1 ]; then
    if [ "$state" = ok ]; then
      echo "PASS  image     $IMAGE  head=$head_ok worker=$worker_ok"
    else
      echo "FAIL  image     state=$state  $IMAGE"
    fi
  else
    log "$NAME image=$IMAGE state=$state head=$head_ok worker=$worker_ok"
    if [ "$state" != ok ]; then
      case "$state" in
        head-docker-error)
          warn "head Docker daemon unavailable — start/fix Docker, then retry"
          ;;
        worker-unreachable)
          warn "worker SSH unreachable — no image sync attempted"
          ;;
        worker-docker-error)
          warn "worker Docker daemon unavailable — start/fix Docker on $WORKER_IP"
          ;;
        *)
          case "$IMAGE" in
            vllm/vllm-openai:*|vllm/*|ghcr.io/*)
              warn "pull: docker pull $IMAGE"
              [ "$NODES" = 2 ] && warn "then: scripts/sync-image.sh $NAME"
              ;;
            *)
              warn "local/custom image. Build: docs/BUILD.md"
              warn "stage to worker: scripts/sync-image.sh $NAME"
              ;;
          esac
          ;;
      esac
    fi
  fi
fi

[ "$state" = ok ]
