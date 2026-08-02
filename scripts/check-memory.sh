#!/usr/bin/env bash
# Memory preflight for conf (+ optional max-model-len note).
#   scripts/check-memory.sh <model-name> [--max-model-len N] [--json]
# exit 0=pass 1=fail 2=warn (tight)
#
# Cold start: require MemAvailable >= footprint + launch spike, residual buffer.
# Already serving this conf: only enforce hard floor + residual buffer (weights/KV
# are already resident — free RAM is OS headroom, not cold capacity).
set -euo pipefail
SCRIPT_NAME=check-memory
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0
OVERRIDE_LEN=""
NAME="${1:-}"
[ -n "$NAME" ] || die "usage: $0 <model-name> [--max-model-len N] [--json]"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1 ;;
    --max-model-len) OVERRIDE_LEN="${2:-}"; shift ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

load_conf "$NAME"
weights=$(estimate_weights_gib)
kv=$(estimate_kv_gib)
overhead="${OVERHEAD_GIB:-$OVERHEAD_GIB_DEFAULT}"
buffer="${MEM_MIN_FREE_GIB:-$MIN_OS_BUFFER_GIB}"
spike="${LAUNCH_SPIKE_GIB}"
floor="${HARD_FLOOR_AVAILABLE_GIB}"

if [ "$NODES" = "2" ]; then
  w_rank=$(awk -v w="$weights" 'BEGIN{printf "%.2f", w/2}')
else
  w_rank="$weights"
fi

need_footprint=$(awk -v w="$w_rank" -v k="$kv" -v o="$overhead" \
  'BEGIN{printf "%.2f", w+k+o}')
need_start=$(awk -v f="$need_footprint" -v s="$spike" 'BEGIN{printf "%.2f", f+s}')

mml=$(parse_max_model_len)
[ -n "$OVERRIDE_LEN" ] && mml="$OVERRIDE_LEN"
kv_bytes=$(parse_kv_cache_bytes)
kv_fixed=0
[ -n "$kv_bytes" ] && kv_fixed=1

# --- already serving this model? ---
cname=$(container_name_for "$NAME" "$NODES")
already=0
already_how=""
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$cname"; then
  already=1
  already_how="container $cname running"
elif SN="$SERVED_NAME" curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/v1/models" 2>/dev/null \
  | SN="$SERVED_NAME" python3 -c 'import sys,json,os; d=json.load(sys.stdin); ids=[x.get("id","") for x in d.get("data",[])]; raise SystemExit(0 if os.environ.get("SN","") in ids else 1)' 2>/dev/null; then
  already=1
  already_how="API :${PORT} serves id=$SERVED_NAME"
fi

head_avail=$(mem_available_gib_local)
worker_avail="n/a"
result=pass
reason=""
mode="cold-start"

check_node_cold() {
  local label="$1" avail="$2"
  if awk -v a="$avail" -v f="$floor" 'BEGIN{exit !(a+0 < f)}'; then
    result=fail
    reason="${reason}${label}: available ${avail} GiB < hard floor ${floor} GiB; "
    return
  fi
  # Hard fail only when free is clearly below the footprint estimate (8% slack
  # for WEIGHTS_GIB/overhead pad). require free >= footprint+spike was too
  # strict on 121 GiB Sparks (flagship estimate ~118 need vs ~113 free while
  # the geometry is known to run with ~4 GiB residual).
  if awk -v a="$avail" -v f="$need_footprint" 'BEGIN{exit !(a+0 < f*0.92)}'; then
    result=fail
    reason="${reason}${label}: available ${avail} GiB << footprint ${need_footprint} GiB (cannot fit); "
    return
  fi
  if awk -v a="$avail" -v n="$need_start" 'BEGIN{exit !(a+0 < n)}'; then
    if [ "$result" = pass ]; then result=warn; fi
    reason="${reason}${label}: available ${avail} GiB < ideal start ${need_start} GiB (footprint+spike); tight but may run; "
  fi
  residual=$(awk -v a="$avail" -v f="$need_footprint" 'BEGIN{printf "%.2f", a-f}')
  if awk -v r="$residual" -v b="$buffer" 'BEGIN{exit !(r+0 < b)}'; then
    if [ "$result" = pass ]; then result=warn; fi
    reason="${reason}${label}: projected residual ${residual} GiB < buffer ${buffer} GiB; "
  fi
}

# Warm: model already resident — only residual OS headroom matters.
check_node_warm() {
  local label="$1" avail="$2"
  if awk -v a="$avail" -v f="$floor" 'BEGIN{exit !(a+0 < f)}'; then
    result=fail
    reason="${reason}${label}: residual ${avail} GiB < hard floor ${floor} GiB (already-loaded geometry under pressure); "
    return
  fi
  # Prefer buffer target; soak lived near ~3.5–4 GiB — warn if below buffer but above floor
  if awk -v a="$avail" -v b="$buffer" 'BEGIN{exit !(a+0 < b)}'; then
    if [ "$result" = pass ]; then result=warn; fi
    reason="${reason}${label}: residual ${avail} GiB < preferred buffer ${buffer} GiB (model already loaded — expected under 20GB flagship); "
  fi
}

if [ "$already" = 1 ]; then
  mode="already-loaded"
  check_node_warm head "$head_avail"
  if [ "$NODES" = "2" ]; then
    if [ -z "${WORKER_IP:-}" ]; then
      reason="${reason}WORKER_IP unset (worker residual not checked); "
    else
      worker_avail=$(mem_available_gib_remote "$WORKER_IP")
      check_node_warm worker "$worker_avail"
    fi
  fi
else
  check_node_cold head "$head_avail"
  if [ "$NODES" = "2" ]; then
    if [ -z "${WORKER_IP:-}" ]; then
      result=fail
      reason="WORKER_IP unset (required for NODES=2); "
      worker_avail=0
    else
      worker_avail=$(mem_available_gib_remote "$WORKER_IP")
      check_node_cold worker "$worker_avail"
    fi
  fi
fi

note=""
if [ "$kv_fixed" = 1 ]; then
  note="KV reserved via --kv-cache-memory-bytes (${kv} GiB/rank); lowering max-model-len does not free that reservation."
fi
if [ "$already" = 1 ]; then
  note="${note:+$note }Mode=already-loaded (${already_how}): free RAM is residual OS headroom, not cold-start capacity."
fi

if [ "$JSON" = 1 ]; then
  python3 - <<PY
import json
print(json.dumps({
  "model": "$NAME",
  "result": "$result",
  "mode": "$mode",
  "already_loaded": bool($already),
  "already_how": """$already_how""",
  "footprint_gib": float("$need_footprint"),
  "need_start_gib": float("$need_start"),
  "weights_gib_total": float("$weights"),
  "weights_gib_per_rank": float("$w_rank"),
  "kv_gib": float("$kv"),
  "overhead_gib": float("$overhead"),
  "buffer_gib": float("$buffer"),
  "spike_gib": float("$spike"),
  "hard_floor_gib": float("$floor"),
  "head_available_gib": float("$head_avail"),
  "worker_available_gib": None if "$worker_avail" == "n/a" else float("$worker_avail"),
  "max_model_len": "$mml" or None,
  "kv_fixed": bool($kv_fixed),
  "note": """$note""",
  "reason": """$reason""".strip(),
}, indent=2))
PY
else
  if [ "${QUIET:-0}" = 1 ]; then
    case "$result" in
      pass)
        if [ "$already" = 1 ]; then
          echo "PASS  memory    already loaded · residual head=${head_avail} GiB worker=${worker_avail} GiB (floor ${floor})"
        else
          echo "PASS  memory    cold-start OK · free head=${head_avail} GiB need ${need_start} GiB"
        fi
        ;;
      warn)
        if [ "$already" = 1 ]; then
          echo "WARN  memory    already loaded · residual ~${head_avail} GiB < preferred ${buffer} GiB (normal for 20GB flagship)"
        else
          echo "WARN  memory    tight · free ${head_avail} GiB vs need ${need_start} GiB"
        fi
        ;;
      *)
        if [ "$already" = 1 ]; then
          echo "FAIL  memory    residual under hard floor ${floor} GiB (head=${head_avail})"
        else
          echo "FAIL  memory    free ${head_avail} GiB < cold-start need ${need_start} GiB"
        fi
        ;;
    esac
  else
    log "$NAME result=$result mode=$mode footprint=${need_footprint} GiB/rank start_need=${need_start} (w_rank=$w_rank kv=$kv oh=$overhead +spike=$spike; buffer_target=$buffer)"
    log "available: head=${head_avail} GiB worker=${worker_avail} GiB"
    [ "$already" = 1 ] && log "already loaded: $already_how"
    [ -n "$mml" ] && log "max-model-len=${mml}${OVERRIDE_LEN:+ (override)}"
    [ -n "$note" ] && log "$note"
    [ -n "$reason" ] && warn "$reason"
    case "$result" in
      pass) ;;
      warn)
        if [ "$already" = 1 ]; then
          warn "residual headroom tight but model already serving — OK for dry-run/status; cold relaunch needs free memory first"
        else
          warn "memory is tight — start only if you accept risk (up.sh --accept-memory-warn)"
        fi
        exit 2
        ;;
      fail)
        if [ "$already" = 1 ]; then
          warn "already-loaded geometry under hard floor — risk of OOM/earlyoom; free memory or restart with smaller geometry"
        else
          warn "free memory, stop other GPU jobs, or pick a smaller model/geometry"
        fi
        exit 1
        ;;
    esac
  fi
fi

case "$result" in
  pass) exit 0 ;;
  warn) exit 2 ;;
  *) exit 1 ;;
esac
