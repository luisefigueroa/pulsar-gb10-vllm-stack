#!/usr/bin/env bash
# Thin guided onboarding (gum if present, bash select fallback).
# Calls scripts/* only — no Docker/NCCL logic here.
#   ./wizard.sh
set -euo pipefail
SCRIPT_NAME=wizard
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
SCRIPT_NAME=wizard

VENDORED_GUM="$REPO_DIR/third_party/gum/linux-arm64/gum"
GUM_CMD=""
have_gum=0
if [ "${GUM:-1}" != 0 ]; then
  if [ -n "${GUM_BIN:-}" ]; then
    if [ -x "$GUM_BIN" ]; then
      GUM_CMD="$GUM_BIN"
    else
      warn "GUM_BIN is not executable: $GUM_BIN"
    fi
  elif [ "$(uname -s)" = Linux ] && [ "$(uname -m)" = aarch64 ] \
      && [ -x "$VENDORED_GUM" ]; then
    GUM_CMD="$VENDORED_GUM"
  elif command -v gum >/dev/null 2>&1; then
    GUM_CMD=$(command -v gum)
  fi
fi
[ -n "$GUM_CMD" ] && have_gum=1

choose() {
  local header="$1"; shift
  if [ "$have_gum" = 1 ]; then
    printf '%s\n' "$@" | "$GUM_CMD" choose --header "$header"
  else
    echo "$header" >&2
    PS3="Select number: "
    select opt in "$@"; do
      [ -n "${opt:-}" ] && { echo "$opt"; break; }
    done
  fi
}

confirm() {
  local msg="$1"
  local default="${2:-no}"
  if [ "$have_gum" = 1 ]; then
    if [ "$default" = yes ]; then
      "$GUM_CMD" confirm --default=true "$msg"
    else
      "$GUM_CMD" confirm "$msg"
    fi
  else
    local prompt="[y/N]"
    [ "$default" = yes ] && prompt="[Y/n]"
    read -r -p "$msg $prompt " a
    case "$a" in
      y|Y|yes|YES) return 0 ;;
      n|N|no|NO) return 1 ;;
      "") [ "$default" = yes ] ;;
      *) return 1 ;;
    esac
  fi
}

spin() {
  local title="$1"; shift
  if [ "$have_gum" = 1 ]; then
    "$GUM_CMD" spin --title "$title" --show-output -- "$@"
  else
    log "$title"
    "$@"
  fi
}

if [ "$have_gum" = 1 ]; then
  log "using $("$GUM_CMD" --version 2>/dev/null || echo gum) at $GUM_CMD"
else
  log "gum unavailable or disabled — using plain menus"
fi

log "running doctor…"
if ! "$REPO_DIR/scripts/doctor.sh"; then
  die "doctor failed — fix host issues first"
fi

if [ -z "${WORKER_IP:-}" ] || [ -z "${HEAD_IP:-}" ]; then
  if confirm "Configure multi-node .env from fabric detect? (optional for single-node)"; then
    "$REPO_DIR/scripts/detect-fabric.sh" || true
    if confirm "Write detected HEAD_IP/NCCL_* into .env? (you must still set WORKER_IP)"; then
      "$REPO_DIR/scripts/detect-fabric.sh" --write-env || true
      warn "edit .env and set WORKER_IP to the peer RoCE IP before Path B"
    fi
  fi
fi

mapfile -t choices < <(
  "$REPO_DIR/scripts/list-models.sh" --validated --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
for m in d.get('models', []):
    fr = ' first-run' if m.get('first_run_candidate') else ''
    print('%s  [%s] nodes=%s src=%s spec=%s%s' % (
        m['id'], m['status'], m['nodes'], m['source'], m['spec'], fr))
"
)
if [ "${#choices[@]}" -eq 0 ]; then
  die "no validated models found"
fi

pick=$(choose "Validated models (Path A: 1-node first; Path B: 2-node flagship)" "${choices[@]}")
NAME=$(echo "$pick" | awk '{print $1}')
[ -n "$NAME" ] || die "no selection"

load_conf "$NAME"
log "selected $NAME status=$STATUS nodes=$NODES served=$SERVED_NAME image=$IMAGE"

if status_requires_force; then
  die "$NAME status=$STATUS is not ship-default (need tested*). Not offered for guided start; use scripts/up.sh --force only if you mean it."
fi

log "checking weights…"
if ! "$REPO_DIR/scripts/check-weights.sh" "$NAME"; then
  kind=$(model_source_kind)
  if [ "$kind" = hf ]; then
    if confirm "Weights missing. Download HF model now${NODES:+ (and sync worker if 2-node)}?"; then
      spin "Downloading weights…" "$REPO_DIR/scripts/pull-weights.sh" "$NAME" --yes
    else
      die "cannot start without weights"
    fi
  else
    die "NFS weights missing — mount $MODELS_NFS and ensure $MODEL exists (no auto-download)"
  fi
fi

log "checking image…"
if ! "$REPO_DIR/scripts/check-image.sh" "$NAME"; then
  case "$IMAGE" in
    vllm/vllm-openai:*|vllm/*|ghcr.io/*)
      if confirm "Image missing. docker pull + sync worker if needed?"; then
        spin "Syncing image…" "$REPO_DIR/scripts/sync-image.sh" "$NAME" --pull --yes
      else
        die "image required"
      fi
      ;;
    *)
      cat <<EOF
Unsupported image source: $IMAGE
All validated profiles use published vLLM or GHCR images. Update the model
configuration to a published registry reference, then re-run ./wizard.sh.
EOF
      exit 1
      ;;
  esac
fi

run_memory_preflight() {
  set +e
  "$REPO_DIR/scripts/check-memory.sh" "$NAME"
  mrc=$?
  set -e
}

log "memory preflight…"
run_memory_preflight
ACCEPT=()
STOP_CURRENT=0
if [ "$mrc" != 0 ] && profile_service_is_stack_owned "$NAME"; then
  if confirm "Memory is tight while stack-managed $NAME is running. Stop it immediately before relaunch and retry?"; then
    STOP_CURRENT=1
  else
    log "leaving the current service running; no containers changed"
    exit 0
  fi
elif [ "$mrc" = 1 ]; then
  die "memory preflight failed"
elif [ "$mrc" = 2 ]; then
  confirm "Memory is tight. Continue anyway?" || die "aborted"
  ACCEPT=(--accept-memory-warn)
fi

SPEC_ARGS=()
if has_spec_args; then
  if [ "${RECOMMENDED_SPEC}" = "1" ]; then
    if ! confirm "Use the validated speculative-decode fast path?" yes; then
      SPEC_ARGS=(--no-spec-decode)
    fi
  elif confirm "Enable the optional validated speculative-decode path?"; then
    SPEC_ARGS=(--spec-decode)
  fi
fi

if ! confirm "Start $NAME now?"; then
  log "aborted; no containers changed"
  exit 0
fi

if [ "$STOP_CURRENT" = 1 ]; then
  log "stopping verified stack-managed $NAME before cold-start preflight…"
  "$REPO_DIR/scripts/down.sh" "$NAME"
  log "re-running memory preflight with the old model unloaded…"
  run_memory_preflight
  if [ "$mrc" = 1 ]; then
    die "memory preflight still fails after stopping $NAME"
  fi
  if [ "$mrc" = 2 ]; then
    confirm "Memory is still tight after stopping $NAME. Continue anyway?" \
      || die "aborted with $NAME stopped"
    ACCEPT=(--accept-memory-warn)
  fi
fi

"$REPO_DIR/scripts/up.sh" "$NAME" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes
"$REPO_DIR/scripts/status.sh" "$NAME" || true
