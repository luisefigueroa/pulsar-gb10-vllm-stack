#!/usr/bin/env bash
# Download HF weights into HF_CACHE; for NODES=2 rsync hub dir to worker.
# NFS confs: refuse with instructions (check-only policy).
#   scripts/pull-weights.sh <model-name> [--yes]
set -euo pipefail
SCRIPT_NAME=pull-weights
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

YES=0
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--yes]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
kind=$(model_source_kind)

if [ "$kind" = nfs ]; then
  cat >&2 <<EOF
[$SCRIPT_NAME] $NAME uses an NFS/catalog path:
  $MODEL
This tool will not download or copy catalog weights.
Fix:
  1. Ensure $MODELS_NFS is mounted on every node that will load the model
  2. Confirm $MODEL/config.json is readable on head$([ "$NODES" = 2 ] && echo " and worker")
  3. Re-run: scripts/check-weights.sh $NAME
EOF
  exit 1
fi

hf_bin=""
if command -v hf >/dev/null 2>&1; then
  hf_bin=hf
elif command -v huggingface-cli >/dev/null 2>&1; then
  hf_bin=huggingface-cli
else
  die "need 'hf' or 'huggingface-cli' on PATH to download $MODEL"
fi

hub=$(hf_hub_path)
mkdir -p "$HF_CACHE/hub"

free=$(disk_free_gib "$HF_CACHE")
# Space gate: WEIGHTS_GIB (conf) or estimate × 1.1 + headroom; never trust a flat 20 GiB for 167 GiB flagships.
w_gib=$(estimate_weights_gib)
headroom=10
if awk -v w="$w_gib" 'BEGIN{exit !(w+0 > 0 && w+0 < 15)}'; then
  headroom=5
fi
# required = max(ceil(w*1.1)+headroom, small floor)
need=$(awk -v w="$w_gib" -v h="$headroom" 'BEGIN{
  if (w+0 <= 0) { print 20; exit }
  n = (w * 1.1) + h
  if (n < 5) n = 5
  printf "%.0f", n + 0.999
}')
# if already partially present, reduce need by existing hub size (best-effort)
if [ -d "$hub" ]; then
  have=$(du -sb "$hub" 2>/dev/null | awk '{printf "%.2f", $1/1024/1024/1024}' || echo 0)
  need=$(awk -v n="$need" -v h="$have" 'BEGIN{
    left = n - h
    if (left < 5) left = 5
    printf "%.0f", left
  }')
fi
log "disk: free=${free} GiB under $HF_CACHE; need≥${need} GiB (weights≈${w_gib} GiB ×1.1 + ${headroom} GiB headroom)"
if awk -v f="$free" -v m="$need" 'BEGIN{exit !(f+0 < m)}'; then
  die "only ${free} GiB free under $HF_CACHE (need ≥ ${need} GiB for ~${w_gib} GiB weights). Free disk and retry."
fi

if [ "$YES" != 1 ]; then
  echo "About to download: $MODEL"
  echo "  into: $HF_CACHE"
  [ "$NODES" = "2" ] && echo "  then rsync hub dir to worker $WORKER_IP"
  read -r -p "Continue? [y/N] " ans
  case "$ans" in
    y|Y|yes|YES) ;;
    *) die "aborted" 3 ;;
  esac
fi

log "downloading $MODEL (HF_HUB_OFFLINE unset for this pull)…"
export HF_HUB_OFFLINE=0
if [ "$hf_bin" = hf ]; then
  hf download "$MODEL" --cache-dir "$HF_CACHE"
else
  huggingface-cli download "$MODEL" --cache-dir "$HF_CACHE"
fi

if [ ! -d "$hub" ]; then
  # some hf versions use different cache layouts; re-check via find
  if ! find "$HF_CACHE" -path "*${MODEL##*/}*" -name config.json 2>/dev/null | head -1 | grep -q .; then
    die "download finished but hub path not found: $hub"
  fi
  warn "hub path $hub missing after download; layout may differ — run check-weights"
fi

if [ "$NODES" = "2" ]; then
  [ -n "${WORKER_IP:-}" ] || die "NODES=2 requires WORKER_IP"
  require_cmd rsync
  remote_hub=$(hf_hub_path)
  log "syncing to worker $WORKER_IP:$remote_hub …"
  ssh_worker "mkdir -p '$(dirname "$remote_hub")'"
  rsync_opts=(-aH --info=progress2)
  if [ -n "${RSYNC_BWLIMIT:-}" ]; then
    rsync_opts+=(--bwlimit="$RSYNC_BWLIMIT")
  fi
  rsync "${rsync_opts[@]}" "$hub/" "$WORKER_IP:$remote_hub/"
fi

log "done. verifying…"
"$REPO_DIR/scripts/check-weights.sh" "$NAME"
