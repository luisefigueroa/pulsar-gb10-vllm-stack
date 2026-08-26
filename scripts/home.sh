#!/usr/bin/env bash
# Neutral workflow menu for the Pulsar GB10 vLLM stack.
#   scripts/home.sh          (also: ./pulsar with no arguments)
#
# Starts immediately — no doctor, inventory, weights, image, or model preflight
# until the operator picks a workflow. Mutations only after explicit confirmation
# and only via scripts/down.sh for inventory-proven safe_to_stop managed confs.
#
# Narrow test hooks (selftests only):
#   HOME_WIZARD_CMD / HOME_MODELS_CMD / HOME_QUICK_STATUS_CMD
#   HOME_INVENTORY_CMD
#   HOME_DOWN_CMD / HOME_DOCTOR_CMD / HOME_STATUS_CMD
#   HOME_INVENTORY_JSON / HOME_INVENTORY_CMD  (for stop/maintenance lists)
#   HOME_STOP_HOT_GIB  (optional proven non-home restage GiB for stop disclosure)
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

cmd_models() {
  if [ -n "${HOME_MODELS_CMD:-}" ]; then
    "$HOME_MODELS_CMD"
  else
    "$REPO_DIR/scripts/model-storage.sh"
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

collect_inventory_json() {
  local destination="${1:?inventory destination required}"
  local output
  if ! output=$(cmd_inventory_json); then
    warn "inventory collection failed — no action was taken; try Diagnostics"
    return 1
  fi
  if ! inventory_json_is_valid "$output"; then
    warn "inventory returned invalid data — no action was taken; try Diagnostics"
    return 1
  fi
  local -n destination_ref="$destination"
  destination_ref="$output"
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
    probes = s.get("required_remote_probes")
    expected_remote_count = max(exp_nodes - 1, 0)
    if isinstance(probes, list) and len(probes) == expected_remote_count:
        if any(
            not isinstance(probe, dict) or probe.get("status") != "ok"
            for probe in probes
        ):
            continue
    elif exp_nodes >= 2 and worker_status != "ok":
        # Compatibility with inventory payloads that predate per-rank probes.
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

# Prints "skip" or "one-rank" or "restage" or "restage <GiB>".
# Disk size is the profile WEIGHTS_GIB (via load_conf + estimate_weights_gib),
# never inventory estimated_footprint_gib_per_rank (that is GPU/unified memory).
library_hot_stop_kind() {
  local conf="$1" services_json="$2"
  local meta source nodes override gib
  meta=$(CONF="$conf" SERVICES_JSON="$services_json" python3 -c '
import json, os

try:
    services = json.loads(os.environ.get("SERVICES_JSON") or "[]")
except Exception:
    raise SystemExit(0)
if not isinstance(services, list):
    raise SystemExit(0)
conf = os.environ.get("CONF") or ""
item = next(
    (
        service
        for service in services
        if (service.get("conf") or service.get("service_id")) == conf
    ),
    {},
)
source = item.get("weight_source") or ""
try:
    nodes = int(item.get("expected_nodes") or 1)
except (TypeError, ValueError):
    nodes = 1
print(f"{source}\t{nodes}")
') || true
  [ -n "$meta" ] || { printf 'skip\n'; return 0; }
  source="${meta%%$'\t'*}"
  nodes="${meta#*$'\t'}"
  [ "$source" = library-hot ] || { printf 'skip\n'; return 0; }
  if ! [[ "$nodes" =~ ^[0-9]+$ ]] || [ "$nodes" -lt 2 ]; then
    printf 'one-rank\n'
    return 0
  fi
  override="${HOME_STOP_HOT_GIB:-}"
  if [ -n "$override" ]; then
    printf 'restage %s\n' "$override"
    return 0
  fi
  # Subshell so load_conf does not clobber the home session. Only the conf
  # WEIGHTS_GIB is a restage disclosure; do not guess from local cache size.
  if gib=$(
    load_conf "$conf" >/dev/null
    [ -n "${WEIGHTS_GIB:-}" ]
    estimate_weights_gib
  ) 2>/dev/null; then
    printf 'restage %s\n' "$gib"
  else
    printf 'restage\n'
  fi
}

workflow_stop() {
  local inv services_json
  log "listing inventory-safe active managed services (read-only)…"
  if ! collect_inventory_json inv; then
    return 0
  fi
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
    state = str(s.get("state") or "?").upper()
    expected_count = len(s.get("expected_ranks") or [])
    observed_count = len(s.get("observed_ranks") or [])
    measured = []
    for r in s.get("ranks") or []:
        g = (r.get("gpu_memory") or {}).get("measured_mib")
        if g is not None:
            measured.append(f"{float(g) / 1024:.1f}")
    if measured:
        mem_s = "GPU " + "/".join(measured) + " GiB"
    else:
        fp = s.get("estimated_footprint_gib_per_rank")
        mem_s = f"est {fp:.1f} GiB per node" if fp is not None else "memory n/a"
    # conf is first token for parsing
    print(f"{conf} · {state} · nodes {observed_count}/{expected_count} · {mem_s}")
PY
  )

  choices+=("Back")
  local pick
  if ! pick=$(choose "Stop a serving model · managed + safe_to_stop" "${choices[@]}"); then
    log "cancelled; no containers changed"
    return 0
  fi
  case "$pick" in
    Back|"") log "back; no containers changed"; return 0 ;;
  esac

  conf=$(printf '%s\n' "$pick" | awk '{print $1}')
  [ -n "$conf" ] || { log "no selection"; return 0; }

  log "selected conf=$conf — down.sh will revalidate ownership and immutable IDs"
  local -a down_args=()
  local kind keep_label free_label restage_gib=""
  kind=$(library_hot_stop_kind "$conf" "$services_json")
  case "$kind" in
    skip|"")
      ;;
    one-rank)
      keep_label="Keep prepared views · next start can reuse the local runtime view · durable home still required"
      free_label="Free prepared views · next start recreates the runtime view from the durable home"
      ;;
    restage)
      keep_label="Keep prepared views · next start can reuse the verified local copy · durable home still required"
      free_label="Free prepared views · next start restages from the durable home"
      ;;
    restage\ *)
      restage_gib="${kind#restage }"
      keep_label="Keep prepared views · next start can reuse ~${restage_gib} GiB locally · durable home still required"
      free_label="Free prepared views · free ~${restage_gib} GiB now · next start requires a full restage"
      ;;
    *)
      keep_label="Keep prepared views · next start can reuse the verified local copy · durable home still required"
      free_label="Free prepared views · next start restages from the durable home"
      ;;
  esac
  if [ -n "${keep_label:-}" ]; then
    if ! pick=$(choose "Prepared views after stop · durable home still required" \
        "$keep_label" "$free_label" "Back"); then
      log "cancelled; no containers changed"
      return 0
    fi
    case "$pick" in
      Back|"") log "back; no containers changed"; return 0 ;;
      Keep\ prepared\ views*) down_args+=(--retain-weights) ;;
      Free\ prepared\ views*) down_args+=(--purge-hot) ;;
      *) log "no selection"; return 0 ;;
    esac
  fi
  if ! confirm "Stop stack-managed service conf=$conf?"; then
    log "declined; no containers changed"
    return 0
  fi
  log "stopping conf=$conf via scripts/down.sh…"
  if ! cmd_down "$conf" "${down_args[@]}"; then
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
  if ! collect_inventory_json inv; then
    return 0
  fi
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
    print(f"{conf} · STALE · safe_to_stop · no model memory")
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
  log "workflow menu — using $("$GUM_CMD" --version 2>/dev/null || echo gum) at $GUM_CMD"
else
  log "workflow menu — plain menus (GUM=0 / no-color / gum unavailable)"
fi

log "read-only by default; mutations require confirmation and proven stack ownership"

while true; do
  echo
  pick=""
  if ! pick=$(choose "Pulsar workflow menu" \
    "Current system status" \
    "Serve or switch a model" \
    "Stop a serving model" \
    "Models & storage" \
    "Maintenance" \
    "Diagnostics" \
    "Exit"); then
    log "cancelled; exiting menu (no containers changed)"
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
      if [ "$wz" -ne 0 ] && [ "${PULSAR_VERBOSE:-0}" = 1 ]; then
        warn "wizard exited with status $wz"
      fi
      ;;
    "Stop a serving model")
      workflow_stop
      ;;
    "Models & storage")
      log "opening model storage; browsing is read-only and mutations require explicit confirmation…"
      cmd_models || warn "model catalog view is unavailable"
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
