#!/usr/bin/env bash
# Orchestrate checks then launch serve.sh or start-cluster.sh.
#   scripts/up.sh <model-name> [--spec-decode|--no-spec-decode] [--force]
#                 [--skip-preflight]
#                 [--skip-weights-check] [--accept-memory-warn] [--pull-image]
#                 [--dry-run] [--yes] [--verbose]
set -euo pipefail
SCRIPT_NAME=up
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [options]"
shift

SPEC_MODE=auto FORCE=0 SKIP_PF=0 SKIP_W=0 ACCEPT_MEM=0 PULL_IMG=0 DRY=0 YES=0 VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --spec-decode) set_spec_decode_mode SPEC_MODE on ;;
    --no-spec-decode) set_spec_decode_mode SPEC_MODE off ;;
    --force) FORCE=1 ;;
    --skip-preflight) SKIP_PF=1 ;;
    --skip-weights-check) SKIP_W=1 ;;
    --accept-memory-warn) ACCEPT_MEM=1 ;;
    --pull-image) PULL_IMG=1 ;;
    --dry-run) DRY=1 ;;
    --yes|-y) YES=1 ;;
    --verbose|-v) VERBOSE=1 ;;
    -h|--help)
      cat <<'EOF'
usage: scripts/up.sh <model-name> [options]

  --spec-decode          force on the conf's validated SPEC_DECODE_ARGS
  --no-spec-decode       force off speculative decode (rollback)
  --dry-run              run checks only (no launch)
  --verbose              full check logs (default is one-line gates)
  --accept-memory-warn   allow start on memory WARN
  --pull-image / --yes   attempt image pull/sync when missing
  --force                allow non-tested conf statuses (untested/do-not-use/blocked*)
  --skip-preflight       skip cluster/preflight.sh
  --skip-weights-check   skip weight presence check
EOF
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
resolve_spec_decode "$SPEC_MODE"
export QUIET=1
[ "$VERBOSE" = 1 ] && export QUIET=0

echo "┌─ up  $NAME"
echo "│  nodes=$NODES  served=$SERVED_NAME  port=$PORT  status=$STATUS"
if [ "$SPEC_DECODE_ENABLED" = 1 ]; then
  echo "│  spec-decode=ON  ($SPEC_DECODE_SOURCE)"
else
  echo "│  spec-decode=off  ($SPEC_DECODE_SOURCE)"
fi
[ "$DRY" = 1 ] && echo "│  mode=DRY-RUN (checks only)"
echo "├─ checks"

if status_requires_force && [ "$FORCE" != 1 ]; then
  echo "FAIL  conf      status=$STATUS (ship default: only tested*; use --force)"
  die "$NAME status=$STATUS — refuse start without --force (allowlist: tested*)"
fi
echo "PASS  conf      status=$STATUS"

if [ "$NODES" = "2" ]; then
  if ! require_cluster_ips; then
    echo "FAIL  fabric    HEAD_IP/WORKER_IP missing — scripts/detect-fabric.sh --write-env"
    die "2-node conf requires HEAD_IP and WORKER_IP"
  fi
  echo "PASS  fabric    HEAD_IP=$HEAD_IP  WORKER_IP=$WORKER_IP"
fi

# --- image ---
set +e
if [ "$VERBOSE" = 1 ]; then
  "$REPO_DIR/scripts/check-image.sh" "$NAME"
  img_rc=$?
else
  img_line=$(QUIET=1 "$REPO_DIR/scripts/check-image.sh" "$NAME" 2>&1)
  img_rc=$?
  echo "$img_line"
fi
img_json=$(QUIET=0 "$REPO_DIR/scripts/check-image.sh" "$NAME" --json 2>/dev/null || true)
set -e
if [ "$img_rc" != 0 ]; then
  img_state=$(printf '%s' "$img_json" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("state",""))' 2>/dev/null || echo unknown)
  case "$img_state" in
    need-worker-ip)
      die "WORKER_IP unset — cannot verify image on both nodes"
      ;;
    missing-on-worker)
      die "image on head only — run: scripts/sync-image.sh $NAME --yes"
      ;;
    missing-on-head|missing-both|unknown|"")
      if [ "$PULL_IMG" = 1 ] || [ "$YES" = 1 ]; then
        "$REPO_DIR/scripts/sync-image.sh" "$NAME" --pull --yes
        QUIET=1 "$REPO_DIR/scripts/check-image.sh" "$NAME" || die "image still missing after sync"
      else
        die "image missing ($img_state): $IMAGE"
      fi
      ;;
    *)
      die "image check failed (state=$img_state)"
      ;;
  esac
fi

# --- weights ---
if [ "$SKIP_W" != 1 ]; then
  set +e
  if [ "$VERBOSE" = 1 ]; then
    "$REPO_DIR/scripts/check-weights.sh" "$NAME"
    w_rc=$?
  else
    QUIET=1 "$REPO_DIR/scripts/check-weights.sh" "$NAME"
    w_rc=$?
  fi
  set -e
  if [ "$w_rc" != 0 ]; then
    kind=$(model_source_kind)
    if [ "$kind" = hf ]; then
      die "weights missing — scripts/pull-weights.sh $NAME"
    else
      die "NFS weights missing — mount catalog at $MODEL"
    fi
  fi
else
  echo "SKIP  weights"
fi

# --- memory ---
set +e
if [ "$VERBOSE" = 1 ]; then
  "$REPO_DIR/scripts/check-memory.sh" "$NAME"
  mem_rc=$?
else
  QUIET=1 "$REPO_DIR/scripts/check-memory.sh" "$NAME"
  mem_rc=$?
fi
set -e
case "$mem_rc" in
  0) ;;
  1)
    die "memory preflight FAILED"
    ;;
  2)
    if [ "$DRY" = 1 ]; then
      echo "      (WARN accepted for dry-run)"
    elif [ "$ACCEPT_MEM" = 1 ]; then
      echo "      (WARN accepted via --accept-memory-warn)"
    else
      die "memory WARN — re-run with --accept-memory-warn to launch"
    fi
    ;;
  *)
    die "memory preflight failed internally (exit=$mem_rc) — refusing launch"
    ;;
esac

# --- multi-node preflight ---
if [ "$NODES" = "2" ]; then
  if [ "$SKIP_PF" != 1 ]; then
    if [ "$DRY" = 1 ]; then
      echo "PASS  preflight would-run cluster/preflight.sh $NAME"
    else
      echo "│  running cluster preflight…"
      if [ "$VERBOSE" = 1 ]; then
        "$REPO_DIR/cluster/preflight.sh" "$NAME" || die "cluster preflight failed"
      else
        _pf_log=$(mktemp "${TMPDIR:-/tmp}/pulsar-preflight.XXXXXX")
        # shellcheck disable=SC2064
        trap 'rm -f "${_pf_log:-}"' RETURN
        if "$REPO_DIR/cluster/preflight.sh" "$NAME" >"$_pf_log" 2>&1; then
          echo "PASS  preflight cluster OK"
          rm -f "$_pf_log"
        else
          echo "FAIL  preflight — see $_pf_log"
          tail -20 "$_pf_log" >&2 || true
          die "cluster preflight failed"
        fi
      fi
    fi
  else
    echo "SKIP  preflight"
  fi
fi

spec_flag=()
case "$SPEC_MODE" in
  on) spec_flag=(--spec-decode) ;;
  off) spec_flag=(--no-spec-decode) ;;
  auto) ;;
esac

echo "└─"

if [ "$DRY" = 1 ]; then
  cat <<EOF

DRY-RUN OK
  conf:     $NAME
  served:   $SERVED_NAME
  would:    $([ "$NODES" = 2 ] && echo "cluster/start-cluster.sh $NAME ${spec_flag[*]:-}" || echo "serve.sh $NAME -d ${spec_flag[*]:-}")
  live:     scripts/status.sh $NAME
  note:     no containers changed
EOF
  exit 0
fi

# --- launch ---
if [ "$NODES" = "2" ]; then
  log "starting 2-node cluster…"
  "$REPO_DIR/cluster/start-cluster.sh" "$NAME" ${spec_flag[@]+"${spec_flag[@]}"}
else
  log "starting single-node…"
  "$REPO_DIR/serve.sh" "$NAME" -d ${spec_flag[@]+"${spec_flag[@]}"}
  cname=$(container_name_for "$NAME" 1)
  log "waiting for http://127.0.0.1:${PORT}/health (cold load can take minutes)"
  ok=0
  for i in $(seq 1 "${WAIT_ATTEMPTS:-90}"); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      ok=1
      break
    fi
    if ! docker ps --format '{{.Names}}' | grep -qx "$cname"; then
      warn "container died; last logs:"
      docker logs --tail 80 "$cname" >&2 || true
      exit 1
    fi
    sleep "${WAIT_SECONDS:-5}"
  done
  if [ "$ok" != 1 ]; then
    warn "timed out waiting for health; logs:"
    docker logs --tail 100 "$cname" >&2 || true
    exit 1
  fi
  log "healthy — smoke completion"
  curl -fsS --max-time 120 "http://127.0.0.1:${PORT}/v1/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"${SERVED_NAME}\",\"prompt\":\"2+2=\",\"max_tokens\":4,\"temperature\":0}" \
    && echo
fi

cat <<EOF

READY
  conf:     $NAME
  served:   $SERVED_NAME
  url:      http://127.0.0.1:${PORT}/v1
  smoke:    curl -fsS http://127.0.0.1:${PORT}/v1/models
  status:   scripts/status.sh $NAME
  stop:     scripts/down.sh $NAME
  security: do not expose :${PORT} without auth (SECURITY.md)
EOF
