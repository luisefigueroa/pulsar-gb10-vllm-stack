#!/usr/bin/env bash
# Neutral operator home screen for the Pulsar GB10 vLLM stack.
#   scripts/home.sh          (also: ./pulsar with no arguments)
#
# Starts immediately — no doctor, inventory, weights, image, or model preflight
# until the operator picks a workflow. Mutations only after explicit confirmation
# and only via scripts/down.sh for inventory-proven safe_to_stop managed confs.
#
# Narrow test hooks (selftests only):
#   HOME_WIZARD_CMD / HOME_QUICK_STATUS_CMD / HOME_INVENTORY_CMD
#   HOME_DOWN_CMD / HOME_DOCTOR_CMD / HOME_STATUS_CMD
#   HOME_INVENTORY_JSON / HOME_INVENTORY_CMD  (for stop/maintenance lists)
#   QUICK_STATUS_* forwarded when invoking quick-status
set -euo pipefail
SCRIPT_NAME=home
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
SCRIPT_NAME=home
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/ui.sh"

# ---------------------------------------------------------------------------
# Injectable commands
# ---------------------------------------------------------------------------
cmd_wizard() {
  if [ -n "${HOME_WIZARD_CMD:-}" ]; then
    "$HOME_WIZARD_CMD" "$@"
  else
    "$REPO_DIR/wizard.sh" "$@"
  fi
}

cmd_quick_status() {
  if [ -n "${HOME_QUICK_STATUS_CMD:-}" ]; then
    "$HOME_QUICK_STATUS_CMD" "$@"
    return
  fi
  # Forward home inventory fixtures into quick-status when set.
  if [ -n "${HOME_INVENTORY_JSON:-}" ] && [ -z "${QUICK_STATUS_INVENTORY_JSON:-}" ]; then
    QUICK_STATUS_INVENTORY_JSON="$HOME_INVENTORY_JSON" \
      "$REPO_DIR/scripts/quick-status.sh" "$@"
  elif { [ -n "${HOME_INVENTORY_CMD:-}" ] || [ -n "${HOME_INVENTORY_JSON_CMD:-}" ]; } \
      && [ -z "${QUICK_STATUS_INVENTORY_CMD:-}" ]; then
    QUICK_STATUS_INVENTORY_CMD="${HOME_INVENTORY_JSON_CMD:-$HOME_INVENTORY_CMD}" \
      "$REPO_DIR/scripts/quick-status.sh" "$@"
  else
    "$REPO_DIR/scripts/quick-status.sh" "$@"
  fi
}

cmd_inventory() {
  if [ -n "${HOME_INVENTORY_CMD:-}" ]; then
    "$HOME_INVENTORY_CMD" "$@"
  else
    "$REPO_DIR/scripts/inventory.sh" "$@"
  fi
}

cmd_inventory_json() {
  if [ -n "${HOME_INVENTORY_JSON:-}" ]; then
    cat "$HOME_INVENTORY_JSON"
  elif [ -n "${HOME_INVENTORY_JSON_CMD:-}" ]; then
    "$HOME_INVENTORY_JSON_CMD"
  elif [ -n "${HOME_INVENTORY_CMD:-}" ]; then
    # If inventory cmd is a JSON emitter (tests), use it; else --json.
    "$HOME_INVENTORY_CMD" --json 2>/dev/null || "$HOME_INVENTORY_CMD"
  else
    "$REPO_DIR/scripts/inventory.sh" --json
  fi
}

cmd_down() {
  if [ -n "${HOME_DOWN_CMD:-}" ]; then
    "$HOME_DOWN_CMD" "$@"
  else
    "$REPO_DIR/scripts/down.sh" "$@"
  fi
}

cmd_doctor() {
  if [ -n "${HOME_DOCTOR_CMD:-}" ]; then
    "$HOME_DOCTOR_CMD" "$@"
  else
    "$REPO_DIR/scripts/doctor.sh" "$@"
  fi
}

cmd_status_smoke() {
  # Full scripts/status.sh (includes optional completion) — only on explicit request
  if [ -n "${HOME_STATUS_CMD:-}" ]; then
    "$HOME_STATUS_CMD" "$@"
  else
    "$REPO_DIR/scripts/status.sh" "$@"
  fi
}

# ---------------------------------------------------------------------------
# Inventory helpers for stop / maintenance (consume classifier; do not reimplement)
# ---------------------------------------------------------------------------
# Eligible for interactive stop: active, managed, safe_to_stop, complete,
# proven ownership. Exclude unknown/legacy/mismatch/incomplete/unproven and
# multi-node when worker is unreachable.
eligible_stop_services_json() {
  local inv="$1"
  INV_JSON="$inv" python3 - <<'PY'
import json, os, sys
inv = json.loads(os.environ.get("INV_JSON") or "{}")
worker = inv.get("worker") or {}
worker_status = worker.get("status") or "unset"

def active(s):
    st = s.get("state")
    if st in ("running", "partial", "degraded"):
        return True
    return any(r.get("running") for r in (s.get("ranks") or []))

out = []
for s in inv.get("services") or []:
    if s.get("ownership") != "managed":
        continue
    if not s.get("safe_to_stop"):
        continue
    if not s.get("complete"):
        continue
    if not active(s):
        continue
    st = s.get("state") or ""
    if st == "stale":
        continue
    # Fail closed: every observed rank must be managed + safe
    ranks = s.get("ranks") or []
    if not ranks:
        continue
    if not all(
        r.get("ownership") == "managed" and r.get("safe_to_stop")
        for r in ranks
    ):
        continue
    exp_nodes = s.get("expected_nodes") or 1
    try:
        exp_nodes = int(exp_nodes)
    except (TypeError, ValueError):
        exp_nodes = 1
    if exp_nodes >= 2 and worker_status == "unreachable":
        continue
    if (s.get("observability") or "") == "unreachable":
        continue
    out.append(s)

json.dump(out, sys.stdout)
print()
PY
}

eligible_stale_services_json() {
  local inv="$1"
  INV_JSON="$inv" python3 - <<'PY'
import json, os, sys
inv = json.loads(os.environ.get("INV_JSON") or "{}")
out = []
for s in inv.get("services") or []:
    if s.get("ownership") != "managed":
        continue
    if (s.get("state") or "") != "stale":
        continue
    if not s.get("safe_to_stop"):
        continue
    ranks = s.get("ranks") or []
    if ranks and not all(
        r.get("ownership") == "managed" and r.get("safe_to_stop")
        for r in ranks
    ):
        continue
    out.append(s)
json.dump(out, sys.stdout)
print()
PY
}

format_service_choice() {
  # stdin: one service JSON object → one human choice line
  python3 - <<'PY'
import json, sys
s = json.load(sys.stdin)
conf = s.get("conf") or s.get("service_id") or "?"
state = s.get("state") or "?"
exp = ",".join(str(x) for x in (s.get("expected_ranks") or [])) or "-"
obs = ",".join(str(x) for x in (s.get("observed_ranks") or [])) or "-"
complete = "complete" if s.get("complete") else "incomplete"
# Measured GPU if any rank has it; else estimated footprint
measured = []
for r in s.get("ranks") or []:
    g = (r.get("gpu_memory") or {}).get("measured_mib")
    if g is not None:
        measured.append(f"{r.get('node')}:{g}MiB")
if measured:
    mem_s = "gpu " + ",".join(measured)
else:
    fp = s.get("estimated_footprint_gib_per_rank")
    mem_s = f"est {fp:.1f} GiB/rank" if fp is not None else "mem n/a"
print(f"{conf}  state={state}  {complete}  ranks exp={exp} obs={obs}  {mem_s}")
PY
}

# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------
workflow_status() {
  while true; do
    echo
    cmd_quick_status || true
    echo
    local pick
    if ! pick=$(choose "System status — next?" \
      "Refresh overview" \
      "Detailed inventory" \
      "Full smoke check (runs a completion)" \
      "Back"); then
      return 0
    fi
    case "$pick" in
      Refresh*) continue ;;
      Detailed*)
        cmd_inventory || true
        ;;
      Full*)
        log "running full status smoke (includes a completion request)…"
        cmd_status_smoke || true
        ;;
      Back*|*)
        return 0
        ;;
    esac
  done
}

workflow_stop() {
  local inv services_json
  log "listing inventory-safe active managed services (read-only)…"
  inv=$(cmd_inventory_json)
  services_json=$(eligible_stop_services_json "$inv")
  local count
  count=$(printf '%s' "$services_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
  if [ "${count:-0}" = 0 ]; then
    log "no eligible services to stop"
    log "only complete active managed services with safe_to_stop=true are listed"
    log "unknown/legacy/mismatch/incomplete/unproven/worker-unobservable are excluded"
    return 0
  fi

  local choices=()
  local confs=()
  local line conf
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    choices+=("$line")
  done < <(
    SERVICES_JSON="$services_json" python3 - <<'PY'
import json, os
services = json.loads(os.environ.get("SERVICES_JSON") or "[]")
for s in services:
    conf = s.get("conf") or s.get("service_id") or "?"
    state = s.get("state") or "?"
    exp = ",".join(str(x) for x in (s.get("expected_ranks") or [])) or "-"
    obs = ",".join(str(x) for x in (s.get("observed_ranks") or [])) or "-"
    complete = "complete" if s.get("complete") else "incomplete"
    measured = []
    for r in s.get("ranks") or []:
        g = (r.get("gpu_memory") or {}).get("measured_mib")
        if g is not None:
            measured.append(f"{r.get('node')}:{g}MiB")
    if measured:
        mem_s = "gpu " + ",".join(measured)
    else:
        fp = s.get("estimated_footprint_gib_per_rank")
        mem_s = f"est {fp:.1f} GiB/rank" if fp is not None else "mem n/a"
    # conf is first token for parsing
    print(f"{conf}  state={state}  {complete}  ranks exp={exp} obs={obs}  {mem_s}")
PY
  )
  while IFS= read -r conf; do
    [ -n "$conf" ] || continue
    confs+=("$conf")
  done < <(
    printf '%s' "$services_json" | python3 -c \
      'import json,sys; [print(s.get("conf") or "") for s in json.load(sys.stdin)]'
  )

  choices+=("Back")
  local pick
  if ! pick=$(choose "Stop a serving model (safe_to_stop managed only)" "${choices[@]}"); then
    log "cancelled; no containers changed"
    return 0
  fi
  case "$pick" in
    Back|"") log "back; no containers changed"; return 0 ;;
  esac

  conf=$(printf '%s\n' "$pick" | awk '{print $1}')
  [ -n "$conf" ] || { log "no selection"; return 0; }

  log "selected conf=$conf — down.sh will revalidate ownership and immutable IDs"
  if ! confirm "Stop stack-managed service conf=$conf?"; then
    log "declined; no containers changed"
    return 0
  fi
  log "stopping conf=$conf via scripts/down.sh…"
  if ! cmd_down "$conf"; then
    warn "stop failed for $conf — inspect with ./pulsar inventory"
    return 0
  fi
  log "stopped conf=$conf"
}

workflow_maintenance() {
  local pick
  if ! pick=$(choose "Maintenance" \
    "Clean stale stack-managed containers" \
    "Back"); then
    return 0
  fi
  case "$pick" in
    Clean*) ;;
    *) return 0 ;;
  esac

  local inv services_json count
  log "listing stale stack-managed containers (read-only)…"
  inv=$(cmd_inventory_json)
  services_json=$(eligible_stale_services_json "$inv")
  count=$(printf '%s' "$services_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
  if [ "${count:-0}" = 0 ]; then
    log "no eligible stale managed containers"
    log "only stale + safe_to_stop managed entries are offered (nonblocking; no model memory)"
    return 0
  fi

  log "stale managed containers do not hold model memory and are nonblocking"
  local choices=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    choices+=("$line")
  done < <(
    SERVICES_JSON="$services_json" python3 - <<'PY'
import json, os
services = json.loads(os.environ.get("SERVICES_JSON") or "[]")
for s in services:
    conf = s.get("conf") or s.get("service_id") or "?"
    print(f"{conf}  state=stale  safe_to_stop  (no model memory)")
PY
  )
  choices+=("Back")

  if ! pick=$(choose "Clean which stale stack-managed container?" "${choices[@]}"); then
    log "cancelled; no containers changed"
    return 0
  fi
  case "$pick" in
    Back|"") log "back; no containers changed"; return 0 ;;
  esac

  # One conf at a time — keep --all / bulk auto-clean out of the interactive path.
  local conf
  conf=$(printf '%s\n' "$pick" | awk '{print $1}')
  if [ -z "$conf" ]; then
    log "nothing selected"
    return 0
  fi

  log "will clean via down.sh (revalidates ownership): $conf"
  if ! confirm "Remove stale stack-managed container conf=$conf? (nonblocking; no model memory)"; then
    log "declined; no containers changed"
    return 0
  fi

  log "cleaning stale conf=$conf via scripts/down.sh…"
  if ! cmd_down "$conf"; then
    warn "cleanup failed for $conf"
  else
    log "removed stale conf=$conf"
  fi
}

workflow_diagnostics() {
  local pick
  if ! pick=$(choose "Diagnostics (read-only)" \
    "Run doctor" \
    "Detailed inventory" \
    "Back"); then
    return 0
  fi
  case "$pick" in
    Run*)
      log "running doctor (diagnostic / read-only)…"
      cmd_doctor || warn "doctor reported issues"
      ;;
    Detailed*)
      cmd_inventory || true
      ;;
    *) ;;
  esac
}

# ---------------------------------------------------------------------------
# Main home loop
# ---------------------------------------------------------------------------
if [ "$have_gum" = 1 ]; then
  log "operator home — using $("$GUM_CMD" --version 2>/dev/null || echo gum) at $GUM_CMD"
else
  log "operator home — plain menus (GUM=0 / no-color / gum unavailable)"
fi

log "read-only by default; mutations require confirmation and proven stack ownership"

while true; do
  echo
  pick=""
  if ! pick=$(choose "Pulsar operator home" \
    "Current system status" \
    "Serve or switch a model" \
    "Stop a serving model" \
    "Maintenance" \
    "Diagnostics" \
    "Exit"); then
    log "cancelled; exiting home (no containers changed)"
    exit 0
  fi

  case "$pick" in
    "Current system status")
      workflow_status
      ;;
    "Serve or switch a model")
      log "entering serve/switch wizard (doctor/preflight run inside wizard)…"
      set +e
      cmd_wizard
      wz=$?
      set -e
      if [ "$wz" -ne 0 ]; then
        warn "wizard exited with status $wz"
      fi
      ;;
    "Stop a serving model")
      workflow_stop
      ;;
    "Maintenance")
      workflow_maintenance
      ;;
    "Diagnostics")
      workflow_diagnostics
      ;;
    "Exit"|"")
      log "goodbye"
      exit 0
      ;;
    *)
      # Unknown / empty from cancel already handled; re-loop
      ;;
  esac
done
