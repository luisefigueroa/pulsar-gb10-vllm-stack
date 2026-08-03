#!/usr/bin/env bash
# Thin guided onboarding (gum if present, bash select fallback).
# Calls scripts/* only — no Docker/NCCL logic here.
#   ./wizard.sh
#   ./pulsar wizard   (recommended; root dispatcher)
#   ./pulsar          (neutral home; wizard is “Serve or switch a model”)
#
# Model-switch flow consumes scripts/inventory.sh --json and
# scripts/check-memory.sh; never invents its own ownership classifier.
# Stops are deferred until after final start/replace confirmation, then
# inventory + cold memory preflight re-run before launch.
#
# Narrow test hooks (selftests only — never required in production):
#   WIZARD_SKIP_DOCTOR=1           skip doctor
#   WIZARD_SKIP_WEIGHTS=1          skip weight presence
#   WIZARD_SKIP_IMAGE=1            skip image presence
#   WIZARD_SKIP_FABRIC_PROMPT=1    skip multi-node .env prompt
#   WIZARD_INVENTORY_JSON=path     fixed inventory JSON (or cmd below)
#   WIZARD_INVENTORY_CMD=path      executable receiving no args → inventory JSON
#   WIZARD_MEMORY_JSON=path        fixed check-memory JSON body
#   WIZARD_MEMORY_RC=0|1|2         exit status for memory (default 0)
#   WIZARD_CHECK_MEMORY_CMD=path   executable: <model> [--json] → body; exit rc
#   WIZARD_API_HEALTHY=0|1         force API-healthy probe for selected profile
#   WIZARD_LIST_MODELS_JSON=path   fixed list-models --validated --json
#   WIZARD_UP_CMD / WIZARD_DOWN_CMD / WIZARD_STATUS_CMD / WIZARD_DOCTOR_CMD
#   GUM=0                          plain numbered menus (stdin-driven)
set -euo pipefail
SCRIPT_NAME=wizard
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
SCRIPT_NAME=wizard
# Shared Gum/plain menus + palette policy (scripts/ui.sh)
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/ui.sh"

# ---------------------------------------------------------------------------
# Injectable command paths (test hooks)
# ---------------------------------------------------------------------------
cmd_doctor() {
  if [ -n "${WIZARD_DOCTOR_CMD:-}" ]; then
    "$WIZARD_DOCTOR_CMD" "$@"
  else
    "$REPO_DIR/scripts/doctor.sh" "$@"
  fi
}

cmd_inventory_json() {
  if [ -n "${WIZARD_INVENTORY_JSON:-}" ]; then
    cat "$WIZARD_INVENTORY_JSON"
  elif [ -n "${WIZARD_INVENTORY_CMD:-}" ]; then
    "$WIZARD_INVENTORY_CMD"
  else
    "$REPO_DIR/scripts/inventory.sh" --json
  fi
}

# Prints memory JSON to stdout; exit status is pass/warn/fail (0/2/1).
cmd_check_memory() {
  local model="$1"
  if [ -n "${WIZARD_CHECK_MEMORY_CMD:-}" ]; then
    "$WIZARD_CHECK_MEMORY_CMD" "$model" --json
    return $?
  fi
  if [ -n "${WIZARD_MEMORY_JSON:-}" ]; then
    cat "$WIZARD_MEMORY_JSON"
    return "${WIZARD_MEMORY_RC:-0}"
  fi
  "$REPO_DIR/scripts/check-memory.sh" "$model" --json
}

collect_inventory_json_or_die() {
  local destination="${1:?inventory destination required}"
  local output
  if ! output=$(cmd_inventory_json); then
    die "inventory collection failed — no lifecycle action was taken; run ./pulsar inventory"
  fi
  if ! inventory_json_is_valid "$output"; then
    die "inventory returned invalid data — no lifecycle action was taken; run ./pulsar inventory"
  fi
  local -n destination_ref="$destination"
  destination_ref="$output"
}

collect_memory_json_or_die() {
  local json_destination="${1:?memory JSON destination required}"
  local rc_destination="${2:?memory rc destination required}"
  local model="${3:?model required}"
  local output rc

  if output=$(cmd_check_memory "$model" 2>/dev/null); then
    rc=0
  else
    rc=$?
  fi
  case "$rc" in
    0|1|2) ;;
    *) die "memory preflight failed internally (exit=$rc) — no lifecycle action was taken" ;;
  esac
  if ! memory_preflight_json_is_valid "$output" "$rc"; then
    die "memory preflight returned invalid or inconsistent data (exit=$rc) — no lifecycle action was taken"
  fi

  local -n json_ref="$json_destination"
  local -n rc_ref="$rc_destination"
  json_ref="$output"
  rc_ref="$rc"
}

cmd_down() {
  if [ -n "${WIZARD_DOWN_CMD:-}" ]; then
    "$WIZARD_DOWN_CMD" "$@"
  else
    "$REPO_DIR/scripts/down.sh" "$@"
  fi
}

cmd_up() {
  if [ -n "${WIZARD_UP_CMD:-}" ]; then
    "$WIZARD_UP_CMD" "$@"
  else
    "$REPO_DIR/scripts/up.sh" "$@"
  fi
}

cmd_status() {
  if [ -n "${WIZARD_STATUS_CMD:-}" ]; then
    "$WIZARD_STATUS_CMD" "$@"
  else
    "$REPO_DIR/scripts/status.sh" "$@"
  fi
}

cmd_list_models_json() {
  if [ -n "${WIZARD_LIST_MODELS_JSON:-}" ]; then
    cat "$WIZARD_LIST_MODELS_JSON"
  else
    "$REPO_DIR/scripts/list-models.sh" --validated --json
  fi
}

# ---------------------------------------------------------------------------
# Inventory / memory presentation (consume JSON contract — do not reclassify)
# ---------------------------------------------------------------------------
# One JSON field (stringified) from a JSON object string.
json_field() {
  local json="$1" field="$2"
  printf '%s' "$json" | FIELD="$field" python3 -c \
    'import json,sys,os; d=json.load(sys.stdin); v=d.get(os.environ["FIELD"],""); print("" if v is None else v)'
}

probe_json_has_state() {
  printf '%s' "$1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
raise SystemExit(0 if isinstance(d, dict) and isinstance(d.get("state"), str) and d["state"] else 1)'
}

# Sets free, need, fp from check-memory JSON (caller-scoped variables).
read_mem_budget_fields() {
  local json="$1"
  free=$(json_field "$json" head_available_gib)
  need=$(json_field "$json" need_start_gib)
  fp=$(json_field "$json" footprint_gib)
}

# Unpack analyze_inventory JSON into shell assignments (eval by caller).
analysis_exports() {
  local json="$1"
  printf '%s' "$json" | python3 -c '
import json, sys, shlex
d = json.load(sys.stdin)

def emit(name, val):
    print(f"{name}={shlex.quote(str(val))}")

emit("same_running", d.get("same_complete_running"))
emit("worker_unreach", d.get("worker_unreachable"))
emit("has_unmanaged", d.get("has_unmanaged_gpu"))
emit("others_safe", " ".join(d.get("others_safe_confs") or []))
emit("partial_safe", " ".join(d.get("partial_safe_confs") or []))
emit("stale_same", d.get("stale_same"))
emit("stale_safe", d.get("stale_same_safe"))
emit("port_unknown", d.get("port_unknown"))
emit("unknown_ids", " ".join(d.get("unknown_ids") or []))
emit("legacy_ids", " ".join(d.get("legacy_ids") or []))
emit("mismatch_ids", " ".join(d.get("mismatch_ids") or []))
emit("others_unsafe", " ".join(d.get("others_unsafe_ids") or []))
emit("partial_unsafe", " ".join(d.get("partial_unsafe_ids") or []))
'
}

api_serves_selected() {
  # True if API on PORT advertises SERVED_NAME (or test hook forces it).
  if [ -n "${WIZARD_API_HEALTHY:-}" ]; then
    [ "${WIZARD_API_HEALTHY}" = "1" ]
    return
  fi
  local port="${PORT:-8000}"
  local -a auth_args=()
  api_auth_curl_args auth_args
  SN="$SERVED_NAME" curl -fsS --max-time 2 "${auth_args[@]}" "http://127.0.0.1:${port}/v1/models" 2>/dev/null \
    | SN="$SERVED_NAME" python3 -c '
import sys, json, os
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
ids = [x.get("id", "") for x in d.get("data", [])]
raise SystemExit(0 if os.environ.get("SN", "") in ids else 1)
' 2>/dev/null
}

render_target_summary() {
  local inv="$1" mem_json="$2" mrc="$3"
  INV_JSON="$inv" MEM_JSON="$mem_json" \
  python3 - "$NAME" "$SERVED_NAME" "$NODES" "$PORT" "$STATUS" "$mrc" <<'PY'
import json, os, sys

name, served, nodes, port, status, mrc = sys.argv[1:7]
inv = json.loads(os.environ.get("INV_JSON") or "{}")
mem_raw = (os.environ.get("MEM_JSON") or "").strip()
mem = json.loads(mem_raw) if mem_raw else {}

def fmt(v):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)

print(f"[wizard] target  conf={name}  served={served}  nodes={nodes}  port={port}  status={status}")
head = (inv.get("nodes") or {}).get("head") or {}
worker = (inv.get("nodes") or {}).get("worker") or {}
w = inv.get("worker") or {}
print(
    f"[wizard] memory  head={fmt(head.get('mem_available_gib'))} GiB free"
    f"  worker={fmt(worker.get('mem_available_gib'))} GiB"
    f"  worker_status={w.get('status') or 'unset'}"
)
if mem:
    print(
        f"[wizard] preflight  result={mem.get('result', '?')}  mode={mem.get('mode', '?')}"
        f"  footprint={fmt(mem.get('footprint_gib'))} GiB/rank"
        f"  need_start={fmt(mem.get('need_start_gib'))} GiB"
        f"  (check-memory exit={mrc})"
    )
    if mem.get("reason"):
        print(f"[wizard] preflight  reason: {mem['reason']}")
PY
}

render_relevant_services() {
  local inv="$1"
  INV_JSON="$inv" NAME="$NAME" PORT="$PORT" python3 - <<'PY'
import json, os
inv = json.loads(os.environ.get("INV_JSON") or "{}")
name = os.environ.get("NAME", "")
port = int(os.environ.get("PORT") or 8000)

def active(s):
    st = s.get("state")
    if st in ("running", "partial", "degraded"):
        return True
    return any(r.get("running") for r in (s.get("ranks") or []))

def actionable(s):
    own = s.get("ownership")
    if own == "managed" and s.get("state") in ("stale", "stopped", "partial", "degraded"):
        return True
    if own in ("mismatch", "mixed"):
        return True
    return False

def relevant(s):
    if s.get("conf") == name or s.get("profile") == name:
        return True
    if s.get("api_port") == port and active(s):
        return True
    if active(s) or actionable(s):
        return True
    return False

services = [s for s in (inv.get("services") or []) if relevant(s)]
if not services:
    print("[wizard] active/actionable services: (none relevant)")
else:
    print(f"[wizard] relevant services ({len(services)}):")
    for s in services:
        safe = "safe_to_stop" if s.get("safe_to_stop") else "not_safe_to_stop"
        complete = "complete" if s.get("complete") else "incomplete"
        exp = ",".join(str(x) for x in (s.get("expected_ranks") or [])) or "-"
        obs = ",".join(str(x) for x in (s.get("observed_ranks") or [])) or "-"
        fp = s.get("estimated_footprint_gib_per_rank")
        fp_s = f"{fp:.2f} GiB/rank est" if fp is not None else "est n/a"
        print(
            f"  • {s.get('service_id')}  state={s.get('state')} ownership={s.get('ownership')}"
            f"  {safe} {complete} obs={s.get('observability')}"
            f"  port={s.get('api_port')} ranks exp={exp} obs={obs}  {fp_s}"
        )
        for r in s.get("ranks") or []:
            g = r.get("gpu_memory") or {}
            gm = g.get("measured_mib")
            gm_s = f"{gm} MiB" if gm is not None else "n/a"
            run = "running" if r.get("running") else ("stale" if r.get("stale") else "stopped")
            print(
                f"      - {r.get('node')} rank={r.get('rank')} {r.get('container_name')}"
                f" {run} ownership={r.get('ownership')}"
                f" safe={r.get('safe_to_stop')} gpu_mem={gm_s}"
            )
        for reason in (s.get("reasons") or [])[:3]:
            print(f"      reason: {reason}")

unmanaged = inv.get("unmanaged_gpu_processes") or []
if unmanaged:
    print(f"[wizard] unmanaged GPU processes (read-only; wizard will not stop them): {len(unmanaged)}")
    for u in unmanaged[:8]:
        mem = u.get("used_memory_mib")
        mem_s = f"{mem} MiB" if mem is not None else "n/a"
        print(f"  • {u.get('node')} pid={u.get('pid')} {u.get('process_name')} mem={mem_s}")
else:
    print("[wizard] unmanaged GPU processes: (none observed)")
PY
}

show_diagnostics() {
  local inv="$1"
  log "diagnostics — inventory summary (read-only)"
  render_relevant_services "$inv"
  if [ -n "${WIZARD_STATUS_CMD:-}" ] || [ -z "${WIZARD_INVENTORY_JSON:-}${WIZARD_INVENTORY_CMD:-}" ]; then
    cmd_status "$NAME" || true
  fi
}

# Classify services relative to selected NAME from inventory JSON.
# Sets shell variables via temp files for bash consumption.
analyze_inventory() {
  local inv="$1"
  local out
  out=$(
    INV_JSON="$inv" NAME="$NAME" PORT="$PORT" SERVED_NAME="$SERVED_NAME" python3 - <<'PY'
import json, os
inv = json.loads(os.environ.get("INV_JSON") or "{}")
name = os.environ["NAME"]
port = int(os.environ.get("PORT") or 8000)

services = inv.get("services") or []
unmanaged = inv.get("unmanaged_gpu_processes") or []
worker = inv.get("worker") or {}
worker_status = worker.get("status") or "unset"

def active(s):
    st = s.get("state")
    if st in ("running", "partial", "degraded"):
        return True
    return any(r.get("running") for r in (s.get("ranks") or []))

same = None
others_managed_safe = []
others_managed_unsafe = []
partial_safe = []
partial_unsafe = []
stale_same = None
unknown_blockers = []
legacy_blockers = []
mismatch_blockers = []

for s in services:
    conf = s.get("conf") or s.get("profile") or ""
    own = s.get("ownership") or ""
    state = s.get("state") or ""
    is_same = conf == name
    is_port = (s.get("api_port") == port) and active(s)

    if is_same and state == "stale" and own == "managed":
        stale_same = s
        continue
    if is_same and state == "running" and s.get("complete") and own == "managed":
        same = s
        continue
    if is_same and state in ("partial", "degraded") and own == "managed":
        if s.get("safe_to_stop") and all(
            r.get("ownership") == "managed" and r.get("safe_to_stop")
            for r in (s.get("ranks") or [])
        ):
            partial_safe.append(s)
        else:
            partial_unsafe.append(s)
        continue
    if is_same and own == "managed" and active(s):
        if s.get("safe_to_stop"):
            partial_safe.append(s)
        else:
            partial_unsafe.append(s)
        continue
    if is_same:
        # Same conf already classified (or inactive/non-blocking residual).
        continue

    if not (active(s) or is_port):
        continue

    if own == "managed":
        if state in ("partial", "degraded") or not s.get("complete"):
            if s.get("safe_to_stop") and all(
                r.get("ownership") == "managed" and r.get("safe_to_stop")
                for r in (s.get("ranks") or [])
            ):
                partial_safe.append(s)
            else:
                partial_unsafe.append(s)
        elif s.get("safe_to_stop") and s.get("complete"):
            others_managed_safe.append(s)
        else:
            others_managed_unsafe.append(s)
    elif own == "legacy":
        legacy_blockers.append(s)
    elif own == "mismatch" or own == "mixed":
        mismatch_blockers.append(s)
    else:
        unknown_blockers.append(s)

port_unknown = False
for s in services:
    if s.get("api_port") == port and active(s):
        conf = s.get("conf") or ""
        own = s.get("ownership") or ""
        if conf != name and own not in ("managed",):
            port_unknown = True
        if conf != name and own in ("unknown", "legacy", "mismatch", "mixed"):
            port_unknown = True

print(json.dumps({
    "worker_status": worker_status,
    "worker_unreachable": worker_status != "ok",
    "has_unmanaged_gpu": len(unmanaged) > 0,
    "unmanaged_count": len(unmanaged),
    "same_complete_running": same is not None,
    "same_conf": (same or {}).get("conf") if same else None,
    "same_safe": bool(same and same.get("safe_to_stop")),
    "stale_same": stale_same is not None,
    "stale_same_safe": bool(stale_same and stale_same.get("safe_to_stop")),
    "stale_same_name_blocks": bool(
        stale_same and stale_same.get("container_name")
    ),
    "others_safe_confs": [s.get("conf") for s in others_managed_safe if s.get("conf")],
    "others_safe_ids": [s.get("service_id") for s in others_managed_safe],
    "others_unsafe_ids": [s.get("service_id") for s in others_managed_unsafe],
    "partial_safe_confs": [s.get("conf") for s in partial_safe if s.get("conf")],
    "partial_safe_ids": [s.get("service_id") for s in partial_safe],
    "partial_unsafe_ids": [s.get("service_id") for s in partial_unsafe],
    "unknown_ids": [s.get("service_id") for s in unknown_blockers],
    "legacy_ids": [s.get("service_id") for s in legacy_blockers],
    "mismatch_ids": [s.get("service_id") for s in mismatch_blockers],
    "port_unknown": port_unknown,
    "readonly_block": bool(
        unknown_blockers or legacy_blockers or mismatch_blockers
        or (len(unmanaged) > 0 and not others_managed_safe and same is None)
    ),
}))
PY
  )
  ANALYZE_JSON="$out"
}

# ---------------------------------------------------------------------------
# Session state for stop → relaunch / rollback
# ---------------------------------------------------------------------------
STOPPED_CONFS=()          # confs stopped this plan (for restart-previous)
PREVIOUS_PROFILE=""       # last stopped conf before a failed new launch
ACCEPT=()
SPEC_ARGS=()
PENDING_STOP=()           # confs scheduled to stop after final confirm

reset_plan_state() {
  PENDING_STOP=()
  ACCEPT=()
  SPEC_ARGS=()
}

prompt_spec_decode() {
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
}

final_confirm_start() {
  local msg
  if [ "${#PENDING_STOP[@]}" -gt 0 ]; then
    msg="Stop ${PENDING_STOP[*]}, recheck memory, then start $NAME?"
  else
    msg="Start $NAME now?"
  fi
  confirm "$msg"
}

execute_pending_stops() {
  local conf
  STOPPED_CONFS=()
  for conf in "${PENDING_STOP[@]}"; do
    log "stopping stack-managed conf=$conf (ownership revalidated by down.sh)…"
    if ! cmd_down "$conf"; then
      die "stop failed for $conf — no further mutations; inspect with ./pulsar inventory"
    fi
    STOPPED_CONFS+=("$conf")
    PREVIOUS_PROFILE="$conf"
  done
  PENDING_STOP=()
}

offer_restart_previous() {
  # After hard fail or launch fail when a previous profile was stopped.
  local prev="${PREVIOUS_PROFILE:-}"
  if [ -z "$prev" ]; then
    return 1
  fi
  log "previous managed profile was stopped: $prev"
  log "restart uses current profile defaults from models/${prev}.conf (not a snapshot of prior runtime flags)"
  local pick
  pick=$(choose "Previous service stopped — what next?" \
    "Restart previous profile from current config ($prev)" \
    "Choose another model" \
    "Exit stopped")
  case "$pick" in
    Restart*)
      NAME="$prev"
      load_conf "$NAME"
      PREVIOUS_PROFILE=""
      STOPPED_CONFS=()
      return 0
      ;;
    Choose*)
      return 2
      ;;
    *)
      log "exiting with previous service stopped (conf=$prev)"
      exit 0
      ;;
  esac
}

run_post_stop_memory() {
  # After any stop: reinventory + cold memory. Never assume reclaim.
  local inv mem_json mrc
  log "re-running inventory after stop (memory reclaim is not assumed)…"
  collect_inventory_json_or_die inv
  render_relevant_services "$inv"
  log "cold memory preflight for $NAME…"
  collect_memory_json_or_die mem_json mrc "$NAME"
  render_target_summary "$inv" "$mem_json" "$mrc"
  if [ "$mrc" = 1 ]; then
    warn "memory preflight FAIL after stop — will not launch"
    local free need fp
    read_mem_budget_fields "$mem_json"
    log "target=$NAME free_head=${free:-?} GiB footprint=${fp:-?} need_start=${need:-?} GiB"
    log "hard memory failure never offers continue-anyway"
    if [ -n "${PREVIOUS_PROFILE:-}" ]; then
      local rc=0
      offer_restart_previous || rc=$?
      if [ "$rc" = 0 ]; then
        # restart previous: treat as new selection path — caller handles
        return 10
      fi
      if [ "$rc" = 2 ]; then
        return 2
      fi
    fi
    return 1
  fi
  if [ "$mrc" = 2 ]; then
    local free need fp
    read_mem_budget_fields "$mem_json"
    log "memory WARN: free_head=${free:-?} GiB footprint=${fp:-?} need_start=${need:-?} GiB for $NAME"
    if confirm "Memory is still tight after cleanup. Continue with start anyway?"; then
      ACCEPT=(--accept-memory-warn)
    else
      warn "aborted after stop; previous profile may be down"
      if [ -n "${PREVIOUS_PROFILE:-}" ]; then
        local rc=0
        offer_restart_previous || rc=$?
        [ "$rc" = 0 ] && return 10
        [ "$rc" = 2 ] && return 2
      fi
      return 1
    fi
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Core plan loop for one selected model (until start, keep, exit, or another)
# Returns: 0=started or keep/exit handled, 2=choose another model
# ---------------------------------------------------------------------------
plan_selected_model() {
  reset_plan_state
  local inv mem_json mrc analysis
  local pick

  while true; do
    log "collecting inventory (read-only)…"
    collect_inventory_json_or_die inv
    collect_memory_json_or_die mem_json mrc "$NAME"

    render_target_summary "$inv" "$mem_json" "$mrc"
    render_relevant_services "$inv"
    analyze_inventory "$inv"
    analysis="$ANALYZE_JSON"

    local same_running worker_unreach has_unmanaged
    local others_safe partial_safe stale_same port_unknown
    local unknown_ids legacy_ids mismatch_ids others_unsafe partial_unsafe
    local stale_safe
    # shellcheck disable=SC2034 # assigned via eval from analysis_exports
    eval "$(analysis_exports "$analysis")"

    # ----- same profile running (complete managed) -----
    if [ "$same_running" = "True" ]; then
      if api_serves_selected; then
        log "selected profile $NAME is already running and API healthy"
        pick=$(choose "Same model already serving — what next?" \
          "Keep running (recommended)" \
          "Restart (stop after final confirm, then start)" \
          "Show status" \
          "Choose another model")
      else
        log "selected profile $NAME containers look complete but API is not healthy"
        pick=$(choose "Same model present but API unhealthy — what next?" \
          "Restart (stop after final confirm, then start)" \
          "Show status" \
          "Choose another model" \
          "Exit")
      fi
      case "$pick" in
        Keep*)
          log "keeping $NAME running; no containers changed"
          exit 0
          ;;
        Restart*)
          PENDING_STOP=("$NAME")
          prompt_spec_decode
          if ! final_confirm_start; then
            log "aborted; no containers changed"
            exit 0
          fi
          execute_pending_stops
          local prc=0
          run_post_stop_memory || prc=$?
          if [ "$prc" = 2 ]; then return 2; fi
          if [ "$prc" = 10 ]; then return 10; fi
          if [ "$prc" != 0 ]; then exit 1; fi
          if ! cmd_up "$NAME" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes; then
            warn "launch failed after restart of $NAME"
            pick=$(choose "Launch failed — what next?" \
              "Retry start $NAME from current config" \
              "Exit stopped")
            case "$pick" in
              Retry*)
                cmd_up "$NAME" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes \
                  || die "retry launch failed"
                ;;
              *) log "exiting stopped"; exit 1 ;;
            esac
          fi
          cmd_status "$NAME" || true
          exit 0
          ;;
        Show*)
          show_diagnostics "$inv"
          continue
          ;;
        Exit*)
          log "exiting; no containers changed"
          exit 0
          ;;
        *)
          return 2
          ;;
      esac
    fi

    # ----- unknown / legacy / mismatch / unmanaged: read-only, never stop -----
    if [ -n "$unknown_ids" ] || [ -n "$legacy_ids" ] || [ -n "$mismatch_ids" ] \
        || [ "$port_unknown" = "True" ]; then
      log "blocking non-managed or unproven ownership detected"
      [ -n "$unknown_ids" ] && log "unknown services: $unknown_ids — Wizard will not stop them"
      [ -n "$legacy_ids" ] && log "legacy (unlabeled) services: $legacy_ids — Wizard will not stop them"
      [ -n "$mismatch_ids" ] && log "mismatch services: $mismatch_ids — Wizard will not stop them"
      [ "$port_unknown" = "True" ] && log "port $PORT has an owner that is not a proven managed service for this plan"
      log "Wizard will not stop unlabeled, legacy, mismatch, unknown, incomplete, or unreachable services"
      if [ "$mrc" = 1 ]; then
        log "memory preflight FAIL with unknown/unmanaged consumers — no continue-anyway; no stop offered"
      fi
      pick=$(choose "Cannot auto-replace unproven ownership — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) log "exiting; no containers changed"; exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    # Unmanaged GPU alone with hard memory fail → read-only (no stop/continue)
    if [ "$has_unmanaged" = "True" ] && [ "$mrc" = 1 ] \
        && [ -z "$others_safe" ] && [ -z "$partial_safe" ] \
        && [ "$same_running" != "True" ]; then
      log "hard memory fail with unmanaged GPU consumer(s) — Wizard will not stop them"
      log "no continue-anyway on hard memory failure"
      pick=$(choose "Memory FAIL + unmanaged GPU load — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    # ----- worker unreachable blocks multi-node cleanup/replacement -----
    if [ "$worker_unreach" = "True" ] && [ "$NODES" = "2" ]; then
      log "worker is unreachable — refusing automatic multi-node cleanup/replacement"
      log "prove worker SSH/Docker, or clear WORKER_IP for single-node plans"
      pick=$(choose "Worker unreachable — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi
    # Partial multi-node with worker unreachable on any 2-node partial
    if [ "$worker_unreach" = "True" ] && { [ -n "$partial_safe" ] || [ -n "$partial_unsafe" ]; }; then
      log "worker unreachable with partial/degraded multi-node evidence — refusing automatic cleanup"
      pick=$(choose "Worker unreachable (partial cluster) — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    # ----- partial/degraded managed -----
    if [ -n "$partial_unsafe" ]; then
      log "partial/degraded managed service is not fully inventory-safe: $partial_unsafe"
      log "observed ranks may be incomplete — Wizard will not imply completeness or force cleanup"
      pick=$(choose "Partial managed service (not fully safe) — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    if [ -n "$partial_safe" ]; then
      log "partial/degraded managed service(s) with inventory-safe observed ranks: $partial_safe"
      log "cleanup covers observed ranks only — not a claim of full cluster completeness"
      pick=$(choose "Partial managed service — what next?" \
        "Stop listed stack-managed service(s), recheck, then start $NAME" \
        "Keep current service and exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Stop*)
          # shellcheck disable=SC2206
          PENDING_STOP=($partial_safe)
          ;;
        Keep*) log "leaving current service; no containers changed"; exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    # ----- different complete managed blockers -----
    if [ -n "$others_safe" ] && [ -z "${PENDING_STOP[*]:-}" ]; then
      log "different complete managed service(s) may block target memory/port: $others_safe"
      log "each listed conf is inventory safe_to_stop; down.sh revalidates labels before remove"
      pick=$(choose "Managed service blocks target — what next?" \
        "Stop listed stack-managed service(s), recheck, then start $NAME" \
        "Keep current service and exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Stop*)
          # shellcheck disable=SC2206
          PENDING_STOP=($others_safe)
          ;;
        Keep*) log "keeping current service; no containers changed"; exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    if [ -n "$others_unsafe" ] && [ -z "${PENDING_STOP[*]:-}" ]; then
      log "managed-looking service(s) are not safe_to_stop: $others_unsafe"
      log "Wizard will not stop them"
      pick=$(choose "Unsafe managed blocker — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi

    # ----- stale managed for exact selected name -----
    if [ "$stale_same" = "True" ] && [ -z "${PENDING_STOP[*]:-}" ]; then
      log "stale managed container for $NAME does not hold model memory (exited/created only)"
      if [ "$stale_safe" = "True" ]; then
        pick=$(choose "Stale managed container for $NAME — what next?" \
          "Remove stale stack-managed container for $NAME, recheck, then start" \
          "Choose another model" \
          "Show diagnostics" \
          "Exit")
        case "$pick" in
          Remove*)
            PENDING_STOP=("$NAME")
            ;;
          Choose*) return 2 ;;
          Show*) show_diagnostics "$inv"; continue ;;
          *) exit 0 ;;
        esac
      else
        log "stale container not inventory-safe — left untouched"
        pick=$(choose "Stale container not safe to remove — what next?" \
          "Exit" "Choose another model" "Show diagnostics")
        case "$pick" in
          Exit*) exit 0 ;;
          Choose*) return 2 ;;
          *) show_diagnostics "$inv"; continue ;;
        esac
      fi
    fi

    # If we scheduled stops from partial/other/stale, continue to confirm path below.
    # Otherwise evaluate memory for a clean start.

    # ----- memory on path with no pending stop -----
    if [ "${#PENDING_STOP[@]}" -eq 0 ]; then
      if [ "$mrc" = 1 ]; then
        warn "memory preflight FAIL — will not launch"
        log "hard memory failure never offers continue-anyway"
        if [ "$has_unmanaged" = "True" ]; then
          log "unmanaged GPU consumers present — Wizard will not stop them"
        fi
        pick=$(choose "Memory FAIL — what next?" \
          "Exit" \
          "Choose another model" \
          "Show diagnostics")
        case "$pick" in
          Exit*) exit 1 ;;
          Choose*) return 2 ;;
          *) show_diagnostics "$inv"; continue ;;
        esac
      fi
      if [ "$mrc" = 2 ]; then
        local free need fp
        read_mem_budget_fields "$mem_json"
        log "memory WARN: free=${free:-?} GiB footprint=${fp:-?} need_start=${need:-?} GiB"
        if confirm "Memory is tight. Continue with start anyway?"; then
          ACCEPT=(--accept-memory-warn)
        else
          log "aborted on memory WARN; no containers changed"
          exit 0
        fi
      fi
    fi

    # ----- final confirm (with any pending stops) -----
    prompt_spec_decode
    if ! final_confirm_start; then
      log "aborted; no containers changed"
      exit 0
    fi

    if [ "${#PENDING_STOP[@]}" -gt 0 ]; then
      log "stops scheduled: ${PENDING_STOP[*]}"
      execute_pending_stops
      local prc=0
      run_post_stop_memory || prc=$?
      if [ "$prc" = 2 ]; then return 2; fi
      if [ "$prc" = 10 ]; then return 10; fi
      if [ "$prc" != 0 ]; then exit 1; fi
    fi

    log "launching $NAME…"
    if ! cmd_up "$NAME" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes; then
      warn "launch failed for $NAME"
      if [ -n "${PREVIOUS_PROFILE:-}" ] && [ "${PREVIOUS_PROFILE}" != "$NAME" ]; then
        local rc=0
        offer_restart_previous || rc=$?
        if [ "$rc" = 0 ]; then return 10; fi
        if [ "$rc" = 2 ]; then return 2; fi
        exit 1
      fi
      pick=$(choose "Launch failed — what next?" \
        "Exit stopped" \
        "Choose another model")
      case "$pick" in
        Choose*) return 2 ;;
        *) exit 1 ;;
      esac
    fi
    cmd_status "$NAME" || true
    exit 0
  done
}

# ---------------------------------------------------------------------------
# Bootstrap: doctor once; model selection loop without re-doctor
# ---------------------------------------------------------------------------
if [ "$have_gum" = 1 ]; then
  log "using $("$GUM_CMD" --version 2>/dev/null || echo gum) at $GUM_CMD"
else
  log "gum unavailable or disabled — using plain menus"
fi

if [ "${WIZARD_SKIP_DOCTOR:-0}" != 1 ]; then
  log "running doctor…"
  if ! cmd_doctor; then
    die "doctor failed — fix host issues first"
  fi
else
  log "skipping doctor (WIZARD_SKIP_DOCTOR=1)"
fi

if [ "${WIZARD_SKIP_FABRIC_PROMPT:-0}" != 1 ]; then
  if [ -z "${WORKER_IP:-}" ] || [ -z "${HEAD_IP:-}" ]; then
    if confirm "Configure multi-node .env from fabric detect? (optional for single-node)"; then
      "$REPO_DIR/scripts/detect-fabric.sh" || true
      if confirm "Write detected HEAD_IP/NCCL_* into .env? (you must still set WORKER_IP)"; then
        "$REPO_DIR/scripts/detect-fabric.sh" --write-env || true
        warn "edit .env and set WORKER_IP to the peer RoCE IP before Path B"
      fi
    fi
  fi
fi

# Selection loop: "Choose another model" returns here without re-running doctor.
while true; do
  mapfile -t choices < <(
    cmd_list_models_json | python3 -c "
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

  if [ "${WIZARD_SKIP_WEIGHTS:-0}" != 1 ]; then
    log "checking weights…"
    weights_json=""
    weights_rc=0
    weights_json=$("$REPO_DIR/scripts/check-weights.sh" "$NAME" --json) || weights_rc=$?
    if [ "$weights_rc" != 0 ]; then
      if ! probe_json_has_state "$weights_json"; then
        die "weight preflight returned invalid data — no download or launch attempted"
      fi
      weights_state=$(json_field "$weights_json" state)
      log "weights state=$weights_state"
      case "$weights_state" in
        worker-unreachable)
          die "worker SSH unavailable — cannot verify weights; no download or sync attempted"
          ;;
      esac
      kind=$(model_source_kind)
      if [ "$kind" = hf ]; then
        weights_scope=""
        [ "$NODES" = 2 ] && weights_scope=" and sync it to the worker"
        if confirm "Weights missing or incomplete. Download HF model now${weights_scope}?"; then
          spin "Downloading weights…" "$REPO_DIR/scripts/pull-weights.sh" "$NAME" --yes
        else
          die "cannot start without weights"
        fi
      else
        die "NFS weights missing — mount $MODELS_NFS and ensure $MODEL exists (no auto-download)"
      fi
    fi
  fi

  if [ "${WIZARD_SKIP_IMAGE:-0}" != 1 ]; then
    log "checking image…"
    image_json=""
    image_rc=0
    image_json=$("$REPO_DIR/scripts/check-image.sh" "$NAME" --json) || image_rc=$?
    if [ "$image_rc" != 0 ]; then
      if ! probe_json_has_state "$image_json"; then
        die "image preflight returned invalid data — no pull, sync, or launch attempted"
      fi
      image_state=$(json_field "$image_json" state)
      log "image state=$image_state"
      case "$image_state" in
        head-docker-error)
          die "head Docker daemon unavailable — no image pull or sync attempted"
          ;;
        worker-unreachable)
          die "worker SSH unavailable — no image pull or sync attempted"
          ;;
        worker-docker-error)
          die "worker Docker daemon unavailable — no image pull or sync attempted"
          ;;
        need-worker-ip)
          die "WORKER_IP unset — cannot verify or sync a two-node image"
          ;;
        *)
          case "$IMAGE" in
            vllm/vllm-openai:*|vllm/*|ghcr.io/*)
              if confirm "Image missing. docker pull + sync worker if needed?"; then
                spin "Syncing image…" "$REPO_DIR/scripts/sync-image.sh" "$NAME" --pull --yes
              else
                die "image required"
              fi
              ;;
            *)
              die "unsupported local/custom image source — build it first (docs/BUILD.md)"
              ;;
          esac
          ;;
      esac
    fi
  fi

  plan_rc=0
  plan_selected_model || plan_rc=$?
  if [ "$plan_rc" = 2 ]; then
    log "choose another model — returning to selection (doctor not re-run)"
    continue
  fi
  if [ "$plan_rc" = 10 ]; then
    # Restart previous profile from current config: re-enter plan with new NAME
    log "planning restart of previous profile $NAME from current config defaults"
    plan_rc=0
    plan_selected_model || plan_rc=$?
    if [ "$plan_rc" = 2 ]; then
      log "choose another model — returning to selection"
      continue
    fi
    if [ "$plan_rc" = 10 ]; then
      continue
    fi
  fi
  # plan_selected_model exits on start/keep; if it returns 0 without exit, loop
  break
done
