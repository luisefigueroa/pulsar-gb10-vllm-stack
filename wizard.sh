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

have_gum=0
if [ "${GUM:-1}" != 0 ] && command -v gum >/dev/null 2>&1; then
  have_gum=1
fi

choose() {
  local header="$1"; shift
  if [ "$have_gum" = 1 ]; then
    printf '%s\n' "$@" | gum choose --header "$header"
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
  if [ "$have_gum" = 1 ]; then
    gum confirm "$msg"
  else
    read -r -p "$msg [y/N] " a
    case "$a" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
  fi
}

spin() {
  local title="$1"; shift
  if [ "$have_gum" = 1 ]; then
    gum spin --title "$title" --show-output -- "$@"
  else
    log "$title"
    "$@"
  fi
}

[ "$have_gum" = 1 ] || log "gum not found — using plain menus (install https://github.com/charmbracelet/gum for a nicer UI)"

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

log "memory preflight…"
set +e
"$REPO_DIR/scripts/check-memory.sh" "$NAME"
mrc=$?
set -e
if [ "$mrc" = 1 ]; then
  die "memory preflight failed"
fi
ACCEPT=()
if [ "$mrc" = 2 ]; then
  confirm "Memory is tight. Continue anyway?" || die "aborted"
  ACCEPT=(--accept-memory-warn)
fi

SPEC_ARGS=()
if has_spec_args; then
  def_yes=0
  [ "${RECOMMENDED_SPEC}" = "1" ] && def_yes=1
  msg="Enable speculative decode (--spec-decode)?"
  [ "$def_yes" = 1 ] && msg="$msg [recommended for this conf]"
  if confirm "$msg"; then
    SPEC_ARGS=(--spec-decode)
  fi
fi

if ! confirm "Start $NAME now?"; then
  die "aborted"
fi

"$REPO_DIR/scripts/up.sh" "$NAME" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes
"$REPO_DIR/scripts/status.sh" "$NAME" || true
