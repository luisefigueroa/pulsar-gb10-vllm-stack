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
#   WIZARD_SKIP_FABRIC_PROMPT=1    skip topology discovery prompt
#   WIZARD_TOPOLOGY_NODES=N         test-only confirmed capacity override
#   WIZARD_INVENTORY_JSON=path     fixed inventory JSON (or cmd below)
#   WIZARD_INVENTORY_CMD=path      executable receiving no args → inventory JSON
#   WIZARD_MEMORY_JSON=path        fixed check-memory JSON body
#   WIZARD_MEMORY_RC=0|1|2         exit status for memory (default 0)
#   WIZARD_CHECK_MEMORY_CMD=path   executable: <model> [--json] → body; exit rc
#   WIZARD_API_HEALTHY=0|1         force API-healthy probe for selected profile
#   WIZARD_LIST_MODELS_JSON=path   fixed list-models --serving --json
#   WIZARD_CHECK_WEIGHTS_CMD=path  executable: <model> --json
#   WIZARD_SKIP_LIBRARY_CHECK=1    tests only: assume library views are ready
#   WIZARD_MODEL_LIBRARY_HEALTH_CMD=path  executable: --json
#   WIZARD_MODEL_LIBRARY_PREPARE_CMD=path executable: prepare <model> ...
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

WIZARD_WORK_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-wizard.XXXXXX")
trap 'rm -rf "$WIZARD_WORK_DIR"' EXIT INT TERM
MODEL_STORAGE_RENDERER="$REPO_DIR/scripts/model_storage.py"
REPLACEMENT_TRANSACTION_TOOL="$REPO_DIR/scripts/replacement_transaction.py"
REPLACEMENT_TRANSACTION_FILE="${WIZARD_REPLACEMENT_TRANSACTION_FILE:-$REPO_DIR/.model-library/replacement-transactions/wizard.json}"
ROLLBACK_ACTIVE=0
TRANSACTION_PREVIOUS_PROFILE=""
TRANSACTION_SOURCE=""

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
  shift
  if [ -n "${WIZARD_CHECK_MEMORY_CMD:-}" ]; then
    "$WIZARD_CHECK_MEMORY_CMD" "$model" "$@" --json
    return $?
  fi
  if [ -n "${WIZARD_MEMORY_JSON:-}" ]; then
    cat "$WIZARD_MEMORY_JSON"
    return "${WIZARD_MEMORY_RC:-0}"
  fi
  "$REPO_DIR/scripts/check-memory.sh" "$model" "$@" --json
}

cmd_check_weights() {
  if [ -n "${WIZARD_CHECK_WEIGHTS_CMD:-}" ]; then
    "$WIZARD_CHECK_WEIGHTS_CMD" "$@"
  else
    "$REPO_DIR/scripts/check-weights.sh" "$@"
  fi
}

cmd_model_library_health() {
  if [ -n "${WIZARD_MODEL_LIBRARY_HEALTH_CMD:-}" ]; then
    "$WIZARD_MODEL_LIBRARY_HEALTH_CMD" --json
  else
    "$REPO_DIR/scripts/model-library.sh" health --json
  fi
}

cmd_model_library_prepare() {
  if [ -n "${WIZARD_MODEL_LIBRARY_PREPARE_CMD:-}" ]; then
    "$WIZARD_MODEL_LIBRARY_PREPARE_CMD" "$@"
  else
    "$REPO_DIR/scripts/model-library.sh" "$@"
  fi
}

cmd_replacement_transaction() {
  if [ -n "${WIZARD_REPLACEMENT_TRANSACTION_CMD:-}" ]; then
    "$WIZARD_REPLACEMENT_TRANSACTION_CMD" "$@"
  else
    python3 "$REPLACEMENT_TRANSACTION_TOOL" "$@"
  fi
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
  shift 3
  local output rc

  if output=$(cmd_check_memory "$model" "$@" 2>/dev/null); then
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
    "$REPO_DIR/scripts/list-models.sh" --serving --json
  fi
}
PLACEMENT_SELECTOR=""
PLACEMENT_NODE_KEY="head"
PLACEMENT_NODE_ID=""
PLACEMENT_HOSTNAME=""
PLACEMENT_CONTROL_IP="127.0.0.1"
PLACEMENT_REMOTE=0
PLACEMENT_AWARE=0
PLACEMENT_ARGS=()

reset_placement_state() {
  PLACEMENT_SELECTOR=""
  PLACEMENT_NODE_KEY="head"
  PLACEMENT_NODE_ID=""
  PLACEMENT_HOSTNAME=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo localhost)
  PLACEMENT_CONTROL_IP="127.0.0.1"
  PLACEMENT_REMOTE=0
  PLACEMENT_AWARE=0
  PLACEMENT_ARGS=()
}

adopt_resolved_single_node_placement() {
  PLACEMENT_NODE_KEY="$SINGLE_NODE_KEY"
  PLACEMENT_NODE_ID="$SINGLE_NODE_ID"
  PLACEMENT_HOSTNAME="$SINGLE_NODE_HOSTNAME"
  PLACEMENT_CONTROL_IP="$SINGLE_NODE_CONTROL_IP"
  PLACEMENT_REMOTE="$SINGLE_NODE_REMOTE"
  PLACEMENT_SELECTOR="${SINGLE_NODE_ID:-$SINGLE_NODE_KEY}"
  PLACEMENT_AWARE=1
  PLACEMENT_ARGS=(--node "$PLACEMENT_SELECTOR")
}

LIBRARY_CHECK_JSON=""

library_scope_label() {
  if [ "$NODES" -eq 2 ]; then
    printf '%s\n' "two-rank"
  else
    printf '%s\n' "one-rank"
  fi
}

collect_library_serving_check() {
  local health_file="$WIZARD_WORK_DIR/library-health.json"
  local profiles_file="$WIZARD_WORK_DIR/library-profiles.json"
  local health_rc=0
  local -a target_args=()
  : >"$health_file"
  set +e
  cmd_model_library_health >"$health_file"
  health_rc=$?
  set -e
  case "$health_rc" in
    0|1) ;;
    *)
      warn "distributed catalog health is unavailable (status $health_rc)"
      return 1
      ;;
  esac
  cmd_list_models_json >"$profiles_file" \
    || { warn "serving-profile metadata is unavailable"; return 1; }
  if [ "$NODES" -eq 1 ]; then
    target_args=(--target-rank "$SINGLE_NODE_INDEX")
  fi
  LIBRARY_CHECK_JSON=$(python3 "$MODEL_STORAGE_RENDERER" \
    --report-file "$health_file" \
    --profiles-file "$profiles_file" \
    serving-check --profile "$NAME" "${target_args[@]}") || return 1
}

render_library_serving_check() {
  local health_file="$WIZARD_WORK_DIR/library-health.json"
  local profiles_file="$WIZARD_WORK_DIR/library-profiles.json"
  local -a target_args=()
  [ "$NODES" -ne 1 ] || target_args=(--target-rank "$SINGLE_NODE_INDEX")
  python3 "$MODEL_STORAGE_RENDERER" \
    --report-file "$health_file" \
    --profiles-file "$profiles_file" \
    serving-preview --profile "$NAME" "${target_args[@]}"
}

choose_leave() {
  local pick
  pick=$(choose "Model files are not ready for $NAME — what next?" \
    "Choose another model" \
    "Exit")
  case "$pick" in
    Choose*) return 2 ;;
    *) exit 0 ;;
  esac
}

# One-node serving must use the durable-home rank. No-op when the current
# placement already is that rank or home_rank is unknown.
offer_one_node_home_placement() {
  local home_rank="${1:-}"
  local choice
  [ "$NODES" -eq 1 ] || return 0
  [[ "$home_rank" =~ ^[0-9]+$ ]] || return 0
  [ "$home_rank" != "$SINGLE_NODE_INDEX" ] || return 0
  choice=$(choose "One-node serving must use the durable-home node — what next?" \
    "Use the durable-home node (recommended)" \
    "Choose another model" \
    "Exit")
  case "$choice" in
    Use*)
      resolve_single_node_placement "$home_rank" \
        || die "the catalog durable-home node is no longer a valid confirmed placement"
      adopt_resolved_single_node_placement
      log "selected the durable-home node for one-rank library serving: $PLACEMENT_HOSTNAME"
      return 0
      ;;
    Choose*) return 2 ;;
    *) exit 0 ;;
  esac
}

# Brand-new homes use home add --revision (ADR 0012).
offer_library_home_add() {
  warn "no durable home exists for $NAME"
  warn "acquire it first with:"
  warn "  scripts/model-library.sh home add $NAME --revision <selector> --plan --json"
  warn "  scripts/model-library.sh home add $NAME --revision <exact-commit> --yes"
  return 1
}

# Confirm then prepare. Exit 0 on success. Exit 2 if the operator leaves.
# Any other nonzero is a preparation failure; the caller warns and leaves.
confirm_library_prepare() {
  local scope_label prepare_rc=0
  scope_label=$(library_scope_label)
  if ! confirm "Prepare exact model views now, then continue to a separate start confirmation?" no; then
    log "model preparation declined; no model files were changed"
    choose_leave
    return $?
  fi
  log "preparing exact $scope_label runtime views; serving is not started yet…"
  set +e
  cmd_model_library_prepare prepare "$NAME" --backend copy "$@" --yes
  prepare_rc=$?
  set -e
  return "$prepare_rc"
}

refresh_library_serving_check() {
  collect_library_serving_check \
    || die "catalog readiness became unavailable after $1"
  echo
  render_library_serving_check
}

# The model library is the only weight mechanism (ADR 0006). Establish exact
# ready runtime views (durable home + prepared working copies) for every selected rank,
# or return 2 (choose another model) / exit without changing model files.
# Admission uses local files on every rank (ADR 0012). Acquisition remains
# home add --revision, not wizard home add.
confirm_library_serving() {
  local state transport streams home_rank home_json placement_index
  local scope_label identity_label prepare_rc=0
  local -a node_args=() transport_args=()
  LIBRARY_CHECK_JSON=""
  [ "$(model_source_kind)" = hf ] \
    || die "non-HF model profiles are not servable (ADR 0006)"
  if [ "${WIZARD_SKIP_LIBRARY_CHECK:-0}" = 1 ]; then
    log "skipping library readiness (WIZARD_SKIP_LIBRARY_CHECK=1)"
    return 0
  fi
  scope_label=$(library_scope_label)
  identity_label="receipt and occupancy · not a lab expected-identity file"
  render_human_section "MODEL FILES" \
    "Mechanism" "model library · $scope_label" \
    "Identity" "$identity_label" \
    "Fallback" "none; readiness must pass its own checks"

  if ! collect_library_serving_check; then
    warn "distributed catalog readiness could not be established"
    choose_leave
    return $?
  fi
  echo
  render_library_serving_check
  state=$(json_field "$LIBRARY_CHECK_JSON" state)
  if [ "$state" = blocked ]; then
    placement_index="${SINGLE_NODE_INDEX-}"
    offer_one_node_home_placement \
      "$(json_field "$LIBRARY_CHECK_JSON" home_rank)" || return $?
    if [ "${SINGLE_NODE_INDEX-}" != "$placement_index" ]; then
      refresh_library_serving_check "changing placement"
      state=$(json_field "$LIBRARY_CHECK_JSON" state)
    fi
  fi
  if [ "$state" = blocked ] \
      && printf '%s' "$LIBRARY_CHECK_JSON" \
        | grep -q "no current primary durable home"; then
    offer_library_home_add || true
    choose_leave
    return $?
  fi
  if [ "$state" = blocked ]; then
    choose_leave
    return $?
  fi
  if [ "$state" = needs-preparation ]; then
    transport=$(json_field "$LIBRARY_CHECK_JSON" prepare_transport)
    streams=$(json_field "$LIBRARY_CHECK_JSON" copy_streams)
    if [ "$NODES" -eq 1 ]; then
      node_args=(--node "${PLACEMENT_SELECTOR:-$SINGLE_NODE_INDEX}")
    fi
    confirm_library_prepare --transport "$transport" --copy-streams "$streams" \
      "${node_args[@]}" || {
      prepare_rc=$?
      [ "$prepare_rc" -eq 2 ] && return 2
      warn "model preparation failed (status $prepare_rc); serving was not started"
      choose_leave
      return $?
    }
    if ! collect_library_serving_check; then
      die "preparation returned success, but current catalog readiness is unavailable; model was not started"
    fi
    state=$(json_field "$LIBRARY_CHECK_JSON" state)
    if [ "$state" != ready ]; then
      echo
      render_library_serving_check
      die "preparation did not publish exact ready views on every selected rank; model was not started"
    fi
  fi
  log "library runtime views are ready ($scope_label; no fallback)"
  return 0
}

placement_api_base() {
  local host="$PLACEMENT_CONTROL_IP"
  [ -n "$host" ] || host="127.0.0.1"
  if [[ "$host" == *:* ]] && [[ "$host" != \[*\] ]]; then
    host="[$host]"
  fi
  printf "http://%s:%s\\n" "$host" "$PORT"
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
select_single_node_placement() {
  reset_placement_state
  [ "$NODES" -eq 1 ] || return 0

  local inv rows key node_id hostname ssh_host control_ip inventory_free
  local remote occupancy selector mem_json mem_rc free result choice picked
  local best=-1 i score best_score=-1 marker
  local -a keys=() ids=() hostnames=() ssh_hosts=() control_ips=()
  local -a frees=() remotes=() occupancies=() mem_results=()
  local -a choices=() choice_indices=() fields=()

  log "evaluating confirmed physical nodes for $NAME…"
  collect_inventory_json_or_die inv
  rows=$(INV_JSON="$inv" python3 - <<'PY'
import json
import os

inv = json.loads(os.environ.get("INV_JSON") or "{}")
nodes = inv.get("nodes") or {}
services = inv.get("services") or []
unmanaged = inv.get("unmanaged_gpu_processes") or []


def node_order(item):
    key = item[0]
    index = item[1].get("topology_index")
    if isinstance(index, int):
        return (index, key)
    if key == "head":
        return (0, key)
    if key == "worker":
        return (1, key)
    if key.startswith("rank-"):
        try:
            return (int(key.split("-", 1)[1]), key)
        except ValueError:
            pass
    return (1_000_000, key)


def clean(value):
    return str(value or "").replace("\t", " ").replace("\n", " ").strip()


for key, node in sorted(nodes.items(), key=node_order):
    node_id = clean(node.get("node_id"))
    if not node_id:
        continue
    if node.get("confirmed") is False:
        continue
    remote = bool(node.get("remote")) or key != "head"
    probe = clean(node.get("probe_status")).lower()
    if remote and probe != "ok":
        continue
    if not remote and probe not in ("", "ok", "local"):
        continue

    consumers = []
    for service in services:
        for rank in service.get("ranks") or []:
            if rank.get("node") == key and rank.get("running"):
                label = service.get("conf") or service.get("served_name") or "managed service"
                if label not in consumers:
                    consumers.append(label)
    unmanaged_here = [item for item in unmanaged if item.get("node") == key]
    if unmanaged_here:
        consumers.append(f"{len(unmanaged_here)} unmanaged GPU process"
                         + ("es" if len(unmanaged_here) != 1 else ""))
    occupancy = "idle" if not consumers else "Pulsar: " + ", ".join(consumers)
    fields = [
        key,
        node_id,
        clean(node.get("hostname")) or key,
        clean(node.get("ssh_host")),
        clean(node.get("control_ip")),
        clean(node.get("mem_available_gib")),
        "1" if remote else "0",
        occupancy,
    ]
    print("\t".join(fields))
PY
)

  # Old standalone inventory has no stable physical IDs. Preserve the local
  # one-node path; the normal plan memory gate still runs before launch.
  if [ -z "$rows" ]; then
    log "no stable topology node identities in inventory; using this node"
    return 0
  fi

  while IFS=$'\t' read -r key node_id hostname ssh_host control_ip \
      inventory_free remote occupancy; do
    [ -n "$node_id" ] || continue
    selector="$node_id"
    mem_json=""
    mem_rc=0
    if mem_json=$(cmd_check_memory "$NAME" --node "$selector" --cold-start 2>/dev/null); then
      mem_rc=0
    else
      mem_rc=$?
    fi
    case "$mem_rc" in
      0|1|2) ;;
      *) die "memory preflight failed internally for $hostname (exit=$mem_rc)" ;;
    esac
    if ! memory_preflight_json_is_valid "$mem_json" "$mem_rc"; then
      die "memory preflight returned invalid data for $hostname"
    fi
    if [ "$mem_rc" = 1 ]; then
      log "excluding $hostname ($node_id): hard memory failure"
      continue
    fi

    free=$(json_field "$mem_json" head_available_gib)
    [ -n "$free" ] || free="$inventory_free"
    [ -n "$free" ] || free="n/a"
    result=$(json_field "$mem_json" result)
    keys+=("$key")
    ids+=("$node_id")
    hostnames+=("$hostname")
    ssh_hosts+=("$ssh_host")
    control_ips+=("$control_ip")
    frees+=("$free")
    remotes+=("$remote")
    occupancies+=("$occupancy")
    mem_results+=("${result:-unknown}")
  done <<<"$rows"

  [ "${#keys[@]}" -gt 0 ] \
    || die "no confirmed reachable Docker node passes the hard memory gate for $NAME"

  for i in "${!keys[@]}"; do
    score=0
    [ "${occupancies[$i]}" = idle ] && score=$((score + 1000000))
    [ "${mem_results[$i]}" = pass ] && score=$((score + 100000))
    if [[ "${frees[$i]}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      score=$((score + ${frees[$i]%.*}))
    fi
    if [ "$score" -gt "$best_score" ]; then
      best="$i"
      best_score="$score"
    fi
  done

  for i in "${!keys[@]}"; do
    marker=""
    [ "$i" -eq "$best" ] && marker=" · recommended"
    fields+=(
      "Node" "${hostnames[$i]} · ${frees[$i]} GiB free"
      "Status" "Docker reachable · ${occupancies[$i]} · memory ${mem_results[$i]}"
      "Identity" "${ids[$i]:0:12}$marker"
    )
  done
  echo
  render_human_section "ELIGIBLE PHYSICAL NODES" "${fields[@]}"

  choice="${hostnames[$best]} · recommended"
  choices+=("$choice")
  choice_indices+=("$best")
  for i in "${!keys[@]}"; do
    [ "$i" -eq "$best" ] && continue
    [ "${occupancies[$i]}" = idle ] && choice_state=idle || choice_state=occupied
    choice="${hostnames[$i]} · $choice_state"
    choices+=("$choice")
    choice_indices+=("$i")
  done

  if [ "${#choices[@]}" -eq 1 ]; then
    picked="${choices[0]}"
  else
    picked=$(choose "Choose a physical node for $NAME" "${choices[@]}") \
      || die "no physical node selected"
  fi
  for i in "${!choices[@]}"; do
    if [ "$picked" = "${choices[$i]}" ]; then
      best="${choice_indices[$i]}"
      break
    fi
  done

  PLACEMENT_NODE_KEY="${keys[$best]}"
  PLACEMENT_NODE_ID="${ids[$best]}"
  PLACEMENT_HOSTNAME="${hostnames[$best]}"
  PLACEMENT_CONTROL_IP="${control_ips[$best]:-${ssh_hosts[$best]}}"
  PLACEMENT_REMOTE="${remotes[$best]}"
  PLACEMENT_SELECTOR="$PLACEMENT_NODE_ID"
  PLACEMENT_AWARE=1
  PLACEMENT_ARGS=(--node "$PLACEMENT_SELECTOR")
  SINGLE_NODE_INDEX=$(placement_index_for_role "$PLACEMENT_NODE_KEY") \
    || die "selected physical-node role is invalid"
  SINGLE_NODE_KEY="$PLACEMENT_NODE_KEY"
  SINGLE_NODE_ID="$PLACEMENT_NODE_ID"
  SINGLE_NODE_HOSTNAME="$PLACEMENT_HOSTNAME"
  SINGLE_NODE_SSH_HOST="${ssh_hosts[$best]}"
  SINGLE_NODE_CONTROL_IP="$PLACEMENT_CONTROL_IP"
  SINGLE_NODE_REMOTE="$PLACEMENT_REMOTE"
  SINGLE_NODE_TOPOLOGY_ID="${CLUSTER_TOPOLOGY_ID:-}"
  log "selected node: $PLACEMENT_HOSTNAME"
  log "node-id: $PLACEMENT_NODE_ID"
}


render_model_selection() {
  local rank label node
  load_model_serving_release_projection local-verified-readonly
  local -a fields=(
    "Model" "$NAME"
    "Serves" "$SERVED_NAME on :$PORT"
    "Release status" "$MODEL_SERVING_RELEASE_STATUS_LABEL · display-only"
    "Legacy label" "$STATUS · display-only"
    "Recipe" "exact $NODES-node profile"
  )
  if [ -n "${NOTES:-}" ]; then
    fields+=("Profile note" "$NOTES")
  fi
  if [ "$NODES" -eq 1 ]; then
    node="$PLACEMENT_HOSTNAME"
    [ -n "$node" ] || node="this node"
    [ "$PLACEMENT_REMOTE" = 0 ] && node+=" (this node)"
    fields+=("Uses" "$node")
    [ -n "$PLACEMENT_NODE_ID" ] && fields+=("Node ID" "$PLACEMENT_NODE_ID")
  else
    for ((rank = 0; rank < NODES; rank++)); do
      node="${CLUSTER_NODE_HOSTNAMES[$rank]:-}"
      [ -n "$node" ] || node=$(human_cluster_node "$rank")
      [ "$rank" = 0 ] && node+=" (this node)"
      [ "$rank" = 0 ] && label="Uses" || label=""
      fields+=("$label" "$node")
    done
  fi
  if [ "${PULSAR_VERBOSE:-0}" = 1 ]; then
    fields+=("Image" "$IMAGE")
  fi
  render_human_section "MODEL SELECTED" "${fields[@]}"
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
emit("same_weight_source", d.get("same_weight_source") or "unknown")
emit("same_weight_source_matches", d.get("same_weight_source_matches"))
emit("worker_unreach", d.get("worker_unreachable"))
emit("partial_remote_unreach", d.get("partial_remote_unreachable"))
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
  local api_base
  api_base=$(placement_api_base)
  local -a auth_args=()
  api_auth_curl_args auth_args
  SN="$SERVED_NAME" curl -fsS --max-time 2 "${auth_args[@]}" "${api_base}/v1/models" 2>/dev/null \
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
  INV_JSON="$inv" MEM_JSON="$mem_json" PLACEMENT_KEY="$PLACEMENT_NODE_KEY" \
  PLACEMENT_HOST="$PLACEMENT_HOSTNAME" PLACEMENT_ID="$PLACEMENT_NODE_ID" \
  PLACEMENT_REMOTE="$PLACEMENT_REMOTE" \
  python3 - "$NAME" "$SERVED_NAME" "$NODES" "$PORT" "$STATUS" "$mrc" <<'PY'
import json
import os
import sys

from scripts.terminal_format import TerminalWriter

name, served, nodes, port, status, mrc = sys.argv[1:7]
inv = json.loads(os.environ.get("INV_JSON") or "{}")
mem_raw = (os.environ.get("MEM_JSON") or "").strip()
mem = json.loads(mem_raw) if mem_raw else {}
placement_key = os.environ.get("PLACEMENT_KEY") or "head"
placement_host = os.environ.get("PLACEMENT_HOST") or "this node"
placement_id = os.environ.get("PLACEMENT_ID") or ""
placement_remote = os.environ.get("PLACEMENT_REMOTE") == "1"

def fmt(v):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)

def node_display(rank):
    if int(nodes) == 1:
        suffix = "" if placement_remote else " (this node)"
        return f"{placement_host}{suffix}"
    if rank == 0:
        return "this node"
    return f"cluster node {rank + 1}"


term = TerminalWriter()
term.emit("TARGET")
term.field("Model", f"{name} · {status}")
term.field("Serves", f"{served} on :{port}")
term.field("Topology", f"{nodes} {'node' if nodes == '1' else 'nodes'}")
if int(nodes) == 1:
    term.field("Placement", f"{placement_host} · node-id {placement_id or 'standalone'}")
inventory_nodes = inv.get("nodes") or {}
remote_aggregate = inv.get("worker") or {}
for rank in range(int(nodes)):
    if int(nodes) == 1:
        node_name = placement_key
    else:
        node_name = "head" if rank == 0 else ("worker" if rank == 1 else f"rank-{rank}")
    node = inventory_nodes.get(node_name) or {}
    if int(nodes) == 1:
        probe_status = node.get("probe_status") or ("ok" if placement_remote else "local")
    elif rank == 0:
        probe_status = node.get("probe_status") or "local"
    else:
        probe_status = node.get("probe_status") or remote_aggregate.get("status") or "unset"
    term.field(
        "Memory" if rank == 0 else "",
        f"{node_display(rank)} · {fmt(node.get('mem_available_gib'))} GiB free · {probe_status}",
    )
if mem:
    term.blank()
    term.emit("PREFLIGHT")
    term.field(
        "Result",
        f"{str(mem.get('result') or '?').upper()} · {mem.get('mode') or '?'} "
        f"· check-memory exit {mrc}",
    )
    term.field(
        "Budget",
        f"footprint {fmt(mem.get('footprint_gib'))} GiB per node · "
        f"need to start {fmt(mem.get('need_start_gib'))} GiB",
    )
    if mem.get("reason"):
        term.field("Reason", mem["reason"])
PY
}

render_relevant_services() {
  local inv="$1"
  INV_JSON="$inv" NAME="$NAME" PORT="$PORT" python3 - <<'PY'
import json
import os

from scripts.terminal_format import TerminalWriter

inv = json.loads(os.environ.get("INV_JSON") or "{}")
name = os.environ.get("NAME", "")
port = int(os.environ.get("PORT") or 8000)
term = TerminalWriter()
nodes_info = inv.get("nodes") or {}

def placement_label(node_key):
    node = nodes_info.get(node_key) or {}
    hostname = str(node.get("hostname") or "").strip()
    if hostname:
        return f"{hostname} (this node)" if node_key == "head" else hostname
    if node_key == "head":
        return "this node"
    return node_key or "unknown node placement"


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
    term.emit("RELEVANT SERVICES  none")
else:
    term.emit(f"RELEVANT SERVICES  {len(services)}")
    for s in services:
        safe = "safe_to_stop" if s.get("safe_to_stop") else "not_safe_to_stop"
        complete = "complete" if s.get("complete") else "incomplete"
        expected_count = len(s.get("expected_ranks") or [])
        observed_count = len(s.get("observed_ranks") or [])
        fp = s.get("estimated_footprint_gib_per_rank")
        fp_s = f"{fp:.2f} GiB per node" if fp is not None else "n/a"
        nodes = s.get("expected_nodes")
        term.blank()
        term.emit(
            f"{str(s.get('state') or '?').upper()}  {s.get('service_id') or '?'}",
            subsequent_indent="  ",
        )
        api_port = s.get("api_port")
        endpoint = f"{s.get('served_name') or '?'} on :{api_port}" if api_port is not None else "n/a"
        term.field("serves", endpoint, indent=2)
        term.field(
            "status",
            f"{s.get('ownership') or '?'} · {complete} · {safe}",
            indent=2,
        )
        term.field(
            "nodes",
            f"{nodes} required · {observed_count}/{expected_count} observed",
            indent=2,
        )
        term.field("estimate", fp_s, indent=2)
        if (s.get("observability") or "?") != "complete":
            term.field("observe", s.get("observability") or "?", indent=2)
        for r in s.get("ranks") or []:
            g = r.get("gpu_memory") or {}
            gm = g.get("measured_mib")
            gm_s = f"{float(gm) / 1024:.1f} GiB" if gm is not None else "n/a"
            run = "running" if r.get("running") else ("stale" if r.get("stale") else "stopped")
            rank_safe = "safe_to_stop" if r.get("safe_to_stop") else "not_safe_to_stop"
            term.blank()
            term.emit(
                placement_label(r.get("node")),
                initial_indent="  ",
                subsequent_indent="    ",
            )
            term.field("container", r.get("container_name") or "?", indent=4)
            term.field(
                "status",
                f"{run} · {r.get('ownership') or '?'} · {rank_safe}",
                indent=4,
            )
            term.field("GPU", gm_s, indent=4)
        for reason in (s.get("reasons") or [])[:3]:
            term.field("reason", reason, indent=2)

unmanaged = inv.get("unmanaged_gpu_processes") or []
term.blank()
if unmanaged:
    measured = [u.get("used_memory_mib") for u in unmanaged]
    measured = [m for m in measured if isinstance(m, (int, float))]
    aggregate = f" · {int(sum(measured)):,} MiB" if measured else ""
    noun = "process" if len(unmanaged) == 1 else "processes"
    term.emit(f"UNMANAGED GPU  {len(unmanaged)} {noun}{aggregate}")
    term.emit(
        "Read-only; the wizard will not stop these processes.",
        initial_indent="  ",
        subsequent_indent="  ",
    )
    for u in unmanaged[:8]:
        mem = u.get("used_memory_mib")
        mem_s = f"{int(mem):,} MiB" if isinstance(mem, (int, float)) else "n/a"
        process_path = str(u.get("process_name") or "?")
        process_name = os.path.basename(process_path.rstrip("/")) or process_path
        term.emit(
            f"{placement_label(u.get('node'))} · PID {u.get('pid') or '?'} · {process_name} · {mem_s}",
            initial_indent="  ",
            subsequent_indent="    ",
        )
else:
    term.emit("UNMANAGED GPU  none observed")
PY
}

show_diagnostics() {
  local inv="$1"
  log "diagnostics — inventory summary (read-only)"
  render_relevant_services "$inv"
  if [ -n "${WIZARD_STATUS_CMD:-}" ] || [ -z "${WIZARD_INVENTORY_JSON:-}${WIZARD_INVENTORY_CMD:-}" ]; then
    cmd_status "$NAME" "${PLACEMENT_ARGS[@]}" || true
  fi
}

# Classify services relative to selected NAME from inventory JSON.
# Sets shell variables via temp files for bash consumption.
analyze_inventory() {
  local inv="$1"
  local out
  out=$(
    INV_JSON="$inv" NAME="$NAME" PORT="$PORT" NODES="$NODES" \
      TARGET_NODE_KEY="$PLACEMENT_NODE_KEY" SERVED_NAME="$SERVED_NAME" \
      python3 - <<'PY'
import json
import os

inv = json.loads(os.environ.get("INV_JSON") or "{}")
name = os.environ["NAME"]
selected_weight_source = "local-files"
port = int(os.environ.get("PORT") or 8000)
target_count = int(os.environ.get("NODES") or 1)
selected_node = os.environ.get("TARGET_NODE_KEY") or "head"
services = inv.get("services") or []
all_unmanaged = inv.get("unmanaged_gpu_processes") or []
worker = inv.get("worker") or {}
worker_status = worker.get("status") or "unset"
nodes_info = inv.get("nodes") or {}


def node_name_for_rank(rank):
    if rank == 0:
        return "head"
    if rank == 1:
        return "worker"
    return f"rank-{rank}"


if target_count == 1:
    target_node_keys = {selected_node}
else:
    target_node_keys = {
        node_name_for_rank(rank) for rank in range(target_count)
    }


def required_remote_probes(node_count, service=None):
    probes = (service or {}).get("required_remote_probes")
    if isinstance(probes, list) and probes:
        return probes
    result = []
    for rank in range(1, int(node_count or 1)):
        node_key = node_name_for_rank(rank)
        info = nodes_info.get(node_key) or {}
        if "probe_status" in info:
            status = info.get("probe_status") or "unknown"
            reason = info.get("probe_reason")
        else:
            status = worker_status
            reason = worker.get("reason")
        result.append({
            "rank": str(rank),
            "node": node_key,
            "status": status,
            "reason": reason,
        })
    return result


if target_count == 1:
    target_info = nodes_info.get(selected_node) or {}
    target_remote_unreachable = (
        selected_node != "head"
        and (target_info.get("probe_status") or "unknown") != "ok"
    )
else:
    target_remote_unreachable = any(
        item.get("status") != "ok"
        for item in required_remote_probes(target_count)
    )


def active(service):
    state = service.get("state")
    if state in ("running", "partial", "degraded"):
        return True
    return any(rank.get("running") for rank in (service.get("ranks") or []))


def service_overlaps(service, include_stale=False):
    for rank in service.get("ranks") or []:
        if rank.get("node") not in target_node_keys:
            continue
        if rank.get("running"):
            return True
        if include_stale and rank.get("stale"):
            return True
    return False


unmanaged = [
    item for item in all_unmanaged
    if item.get("node") in target_node_keys
]
same = None
others_managed_safe = []
others_managed_unsafe = []
partial_safe = []
partial_unsafe = []
stale_same = None
unknown_blockers = []
legacy_blockers = []
mismatch_blockers = []

for service in services:
    conf = service.get("conf") or service.get("profile") or ""
    ownership = service.get("ownership") or ""
    state = service.get("state") or ""
    overlaps_active = service_overlaps(service)
    overlaps_any = service_overlaps(service, include_stale=True)
    if not overlaps_any:
        continue
    is_same = conf == name
    is_port = (
        service.get("api_port") == port
        and active(service)
        and overlaps_active
    )

    if is_same and state == "stale" and ownership == "managed":
        stale_same = service
        continue
    if (
        is_same
        and state == "running"
        and service.get("complete")
        and ownership == "managed"
        and overlaps_active
    ):
        same = service
        continue
    if is_same and state in ("partial", "degraded") and ownership == "managed":
        if service.get("safe_to_stop") and all(
            rank.get("ownership") == "managed" and rank.get("safe_to_stop")
            for rank in (service.get("ranks") or [])
        ):
            partial_safe.append(service)
        else:
            partial_unsafe.append(service)
        continue
    if is_same and ownership == "managed" and overlaps_active:
        if service.get("safe_to_stop"):
            partial_safe.append(service)
        else:
            partial_unsafe.append(service)
        continue
    if is_same:
        continue
    if not (overlaps_active or is_port):
        continue

    if ownership == "managed":
        if state in ("partial", "degraded") or not service.get("complete"):
            if service.get("safe_to_stop") and all(
                rank.get("ownership") == "managed" and rank.get("safe_to_stop")
                for rank in (service.get("ranks") or [])
            ):
                partial_safe.append(service)
            else:
                partial_unsafe.append(service)
        elif service.get("safe_to_stop") and service.get("complete"):
            others_managed_safe.append(service)
        else:
            others_managed_unsafe.append(service)
    elif ownership == "legacy":
        legacy_blockers.append(service)
    elif ownership in ("mismatch", "mixed"):
        mismatch_blockers.append(service)
    else:
        unknown_blockers.append(service)

port_unknown = False
for service in services:
    if (
        service.get("api_port") == port
        and active(service)
        and service_overlaps(service)
    ):
        conf = service.get("conf") or ""
        ownership = service.get("ownership") or ""
        if conf != name and ownership in (
            "unknown", "legacy", "mismatch", "mixed"
        ):
            port_unknown = True

partial_remote_unreachable = any(
    any(
        item.get("status") != "ok"
        for item in required_remote_probes(
            service.get("expected_nodes") or 1, service
        )
    )
    for service in [*partial_safe, *partial_unsafe]
)

print(json.dumps({
    "worker_status": worker_status,
    "worker_unreachable": target_remote_unreachable,
    "partial_remote_unreachable": partial_remote_unreachable,
    "has_unmanaged_gpu": len(unmanaged) > 0,
    "unmanaged_count": len(unmanaged),
    "same_complete_running": same is not None,
    "same_conf": (same or {}).get("conf") if same else None,
    "same_safe": bool(same and same.get("safe_to_stop")),
    "same_weight_source": (same or {}).get("weight_source") if same else None,
    "same_weight_source_matches": bool(
        same
        and (same.get("weight_source") or "replicated")
        == selected_weight_source
    ),
    "stale_same": stale_same is not None,
    "stale_same_safe": bool(stale_same and stale_same.get("safe_to_stop")),
    "stale_same_name_blocks": bool(
        stale_same and stale_same.get("container_name")
    ),
    "others_safe_confs": [
        service.get("conf") for service in others_managed_safe
        if service.get("conf")
    ],
    "others_safe_ids": [
        service.get("service_id") for service in others_managed_safe
    ],
    "others_unsafe_ids": [
        service.get("service_id") for service in others_managed_unsafe
    ],
    "partial_safe_confs": [
        service.get("conf") for service in partial_safe
        if service.get("conf")
    ],
    "partial_safe_ids": [
        service.get("service_id") for service in partial_safe
    ],
    "partial_unsafe_ids": [
        service.get("service_id") for service in partial_unsafe
    ],
    "unknown_ids": [
        service.get("service_id") for service in unknown_blockers
    ],
    "legacy_ids": [
        service.get("service_id") for service in legacy_blockers
    ],
    "mismatch_ids": [
        service.get("service_id") for service in mismatch_blockers
    ],
    "port_unknown": port_unknown,
    "readonly_block": bool(
        unknown_blockers or legacy_blockers or mismatch_blockers
        or (unmanaged and not others_managed_safe and same is None)
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
  if [ "$ROLLBACK_ACTIVE" = 0 ]; then
    SPEC_ARGS=()
  fi
}

prompt_spec_decode() {
  if [ "$ROLLBACK_ACTIVE" = 1 ]; then
    return 0
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
}

final_confirm_start() {
  local msg
  if [ "${#PENDING_STOP[@]}" -gt 0 ]; then
    msg="Stop ${PENDING_STOP[*]}, recheck, then start $NAME on $PLACEMENT_HOSTNAME with library weights?"
  else
    msg="Start $NAME on $PLACEMENT_HOSTNAME with library weights?"
  fi
  confirm "$msg"
}

collect_transaction_health() {
  local destination="${1:?health destination required}" rc=0
  set +e
  cmd_model_library_health >"$destination"
  rc=$?
  set -e
  case "$rc" in
    0|1) ;;
    *) die "model-library health is unavailable; the running service was not stopped" ;;
  esac
}

load_transaction_summary() {
  local -a fields=()
  mapfile -t fields < <(cmd_replacement_transaction show \
    --path "$REPLACEMENT_TRANSACTION_FILE" | python3 -c '
import json, sys
d = json.load(sys.stdin)
s = d["previous_service"]
w = s["weight"]
p = s["placement"]
print(s["profile"])
print(w["source"])
print("1" if d["temporary_retention"]["required"] else "0")
print(p["mode"])
print((p["ranks"][0].get("node_id") or "") if p["nodes"] == 1 else "")
print(s["spec_decode"])
')
  [ "${#fields[@]}" -eq 6 ] || die "replacement transaction is incomplete"
  TRANSACTION_PREVIOUS_PROFILE="${fields[0]}"
  TRANSACTION_SOURCE="${fields[1]}"
  TRANSACTION_TEMP_PIN="${fields[2]}"
  TRANSACTION_PLACEMENT_MODE="${fields[3]}"
  TRANSACTION_NODE_ID="${fields[4]}"
  TRANSACTION_SPEC_DECODE="${fields[5]}"
}

transaction_node_args() {
  TRANSACTION_NODE_ARGS=()
  if [ "$TRANSACTION_PLACEMENT_MODE" = confirmed-node ]; then
    [ -n "$TRANSACTION_NODE_ID" ] || die "saved one-node placement has no node identity"
    TRANSACTION_NODE_ARGS=(--node "$TRANSACTION_NODE_ID")
  fi
}

capture_running_service_transaction() {
  local conf="${1:?profile required}" inventory="$2"
  local inventory_file="$WIZARD_WORK_DIR/replacement-inventory.json"
  local health_file="$WIZARD_WORK_DIR/replacement-health.json"
  local contract_id
  printf '%s\n' "$inventory" >"$inventory_file"
  contract_id=$(launch_contract_id_for_profile "$conf")
  collect_transaction_health "$health_file"
  if ! cmd_replacement_transaction capture \
      --inventory "$inventory_file" --library-health "$health_file" \
      --profile "$conf" --launch-contract-id "$contract_id" \
      --output "$REPLACEMENT_TRANSACTION_FILE" >/dev/null; then
    die "the running service contract cannot be captured exactly; it remains running. Restart it once with current Pulsar labels or use direct lifecycle commands after reviewing ./pulsar inventory"
  fi
  load_transaction_summary
  transaction_node_args
  if [ "$TRANSACTION_TEMP_PIN" = 1 ]; then
    log "temporarily pinning the exact prepared views until replacement or rollback is confirmed…"
    if ! cmd_model_library_prepare pin "$conf" "${TRANSACTION_NODE_ARGS[@]}"; then
      die "temporary rollback retention failed; the running service remains active and the transaction record was retained"
    fi
  fi
  cmd_replacement_transaction phase \
    --path "$REPLACEMENT_TRANSACTION_FILE" --to retained >/dev/null
}

running_pending_services() {
  local inventory="$1"
  shift
  INV_JSON="$inventory" PENDING=$(IFS=,; printf '%s' "$*") python3 -c '
import json, os
inv = json.loads(os.environ["INV_JSON"])
wanted = set(filter(None, os.environ.get("PENDING", "").split(",")))
values = sorted({
    s.get("conf")
    for s in inv.get("services", [])
    if s.get("conf") in wanted
    and any(rank.get("running") is True for rank in s.get("ranks", []))
})
if values:
    print("\n".join(values))
'
}

execute_pending_stops() {
  local conf inventory
  local -a running=() down_args=()
  STOPPED_CONFS=()
  collect_inventory_json_or_die inventory
  mapfile -t running < <(running_pending_services "$inventory" "${PENDING_STOP[@]}")
  if [ "${#running[@]}" -gt 1 ]; then
    die "replacement would stop multiple running services; automatic rollback is unavailable and no service was stopped"
  fi
  if [ "${#running[@]}" -eq 1 ]; then
    [ "${#PENDING_STOP[@]}" -eq 1 ] \
      || die "replacement mixes running and stale services; no service was stopped"
    conf="${running[0]}"
    local running_source identity_status stoppable
    local -a svc_fields=()
    mapfile -t svc_fields < <(INV_JSON="$inventory" PROFILE="$conf" python3 -c '
import json, os
inv = json.loads(os.environ["INV_JSON"])
items = [s for s in inv.get("services", []) if s.get("conf") == os.environ["PROFILE"] and s.get("state") == "running"]
item = items[0] if len(items) == 1 else {}
print(item.get("weight_source") or "")
print(item.get("model_identity_status") or "")
print("1" if item.get("complete") is True and item.get("safe_to_stop") is True else "0")
')
    [ "${#svc_fields[@]}" -eq 3 ] || die "running service inventory is incomplete; no service was stopped"
    running_source="${svc_fields[0]}"
    stoppable="${svc_fields[2]}"
    # Exact restore that required identity_status=match is retired (ADR 0012).
    # Live switches stop without a restore promise. Leftover transaction
    # files are archived, not used as the live switch path.
    [ "$stoppable" = 1 ] || {
      if [ "$running_source" != local-files ]; then
        die "the running pre-library service is incomplete or unobservable; no service was stopped"
      fi
      die "the running model-library service is incomplete or unobservable; no service was stopped"
    }
    if [ "$running_source" != local-files ]; then
      warn "stopping a pre-library service; exact rollback is unavailable for it"
    else
      warn "stopping a model-library service without an exact restore contract"
    fi
    down_args=()
    if [ "$conf" = "$NAME" ] && [ "$NODES" -eq 1 ]; then
      down_args=("${PLACEMENT_ARGS[@]}")
    fi
    log "stopping stack-managed conf=$conf…"
    cmd_down "$conf" "${down_args[@]}" \
      || die "stop failed for $conf; inspect with ./pulsar inventory"
    STOPPED_CONFS+=("$conf")
    PENDING_STOP=()
    return 0
  else
    for conf in "${PENDING_STOP[@]}"; do
      down_args=()
      if [ "$conf" = "$NAME" ] && [ "$NODES" -eq 1 ]; then
        down_args=("${PLACEMENT_ARGS[@]}")
      fi
      log "removing stale stack-managed conf=$conf (ownership revalidated by down.sh)…"
      cmd_down "$conf" "${down_args[@]}" \
        || die "stale cleanup failed for $conf; inspect with ./pulsar inventory"
    done
  fi
  PENDING_STOP=()
}

prepare_exact_rollback() {
  local health_file="$WIZARD_WORK_DIR/rollback-health.json" contract_id inventory
  local inventory_file="$WIZARD_WORK_DIR/rollback-inventory.json"
  load_transaction_summary
  [ "$TRANSACTION_SOURCE" = local-files ] \
    || die "saved transaction is not a model-library contract; archive it instead of rollback"
  NAME="$TRANSACTION_PREVIOUS_PROFILE"
  load_conf "$NAME"
  contract_id=$(loaded_launch_contract_id)
  collect_inventory_json_or_die inventory
  printf '%s\n' "$inventory" >"$inventory_file"
  collect_transaction_health "$health_file"
  cmd_replacement_transaction verify-rollback \
    --path "$REPLACEMENT_TRANSACTION_FILE" \
    --launch-contract-id "$contract_id" --inventory "$inventory_file" \
    --library-health "$health_file" >/dev/null \
    || die "the captured service contract cannot be restored exactly; transaction and retained views were preserved"

  case "$TRANSACTION_SPEC_DECODE" in
    on) SPEC_ARGS=(--spec-decode) ;;
    off) SPEC_ARGS=(--no-spec-decode) ;;
    *) die "saved speculative-decode state is invalid" ;;
  esac
  case "$TRANSACTION_PLACEMENT_MODE" in
    standalone-local)
      reset_placement_state
      ;;
    confirmed-node)
      resolve_single_node_placement "$TRANSACTION_NODE_ID" \
        || die "the captured physical node is no longer in confirmed topology"
      adopt_resolved_single_node_placement
      ;;
    exact-topology)
      reset_placement_state
      ;;
    *) die "saved placement mode is unsupported" ;;
  esac
  ROLLBACK_ACTIVE=1
  STOPPED_CONFS=()
}

finalize_replacement_transaction() {
  local outcome="${1:?outcome required}" cleanup_rc=0 node_hint=""
  [ -f "$REPLACEMENT_TRANSACTION_FILE" ] || return 0
  load_transaction_summary
  transaction_node_args
  [ -z "$TRANSACTION_NODE_ID" ] || node_hint=" --node $TRANSACTION_NODE_ID"
  if [ "$TRANSACTION_TEMP_PIN" = 1 ]; then
    log "restoring unpinned retention policy for the previous service…"
    if ! cmd_model_library_prepare unpin "$TRANSACTION_PREVIOUS_PROFILE" \
        "${TRANSACTION_NODE_ARGS[@]}"; then
      warn "temporary rollback retention could not be released"
      warn "remediation: scripts/model-library.sh unpin $TRANSACTION_PREVIOUS_PROFILE$node_hint"
      cleanup_rc=1
    elif [ "$outcome" = replacement ] \
        && [ "$NAME" != "$TRANSACTION_PREVIOUS_PROFILE" ]; then
      if ! cmd_model_library_prepare purge-hot "$TRANSACTION_PREVIOUS_PROFILE" \
          "${TRANSACTION_NODE_ARGS[@]}" --yes; then
        warn "the previous unpinned hot view could not be purged"
        warn "remediation: scripts/model-library.sh purge-hot $TRANSACTION_PREVIOUS_PROFILE$node_hint --yes"
        cleanup_rc=1
      fi
    fi
  fi
  if [ "$outcome" = rollback ] && [ "$cleanup_rc" -ne 0 ]; then
    return 1
  fi
  cmd_replacement_transaction complete \
    --path "$REPLACEMENT_TRANSACTION_FILE" --outcome "$outcome" >/dev/null
  PREVIOUS_PROFILE=""
  TRANSACTION_PREVIOUS_PROFILE=""
  ROLLBACK_ACTIVE=0
  return "$cleanup_rc"
}

offer_restart_previous() {
  local prev="${PREVIOUS_PROFILE:-}"
  [ -n "$prev" ] && [ -f "$REPLACEMENT_TRANSACTION_FILE" ] || return 1
  log "previous exact service contract is retained: $prev"
  local pick
  pick=$(choose "Previous service stopped — what next?" \
    "Restore previous exact service ($prev)" \
    "Choose another model" \
    "Exit stopped (keep recovery transaction)")
  case "$pick" in
    Restore*) prepare_exact_rollback; return 0 ;;
    Choose*) return 2 ;;
    *)
      log "exiting stopped; exact rollback state and any temporary pin remain available"
      exit 0
      ;;
  esac
}

recover_incompatible_transaction() {
  local report="$1"
  local reason profile service_state archive_cmd detail
  reason=$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason") or "")')
  profile=$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("profile") or "")')
  service_state=$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("service") or "unknown")')
  archive_cmd=$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("archive_command") or "")')
  detail=$(printf '%s' "$report" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("detail") or "")')
  [ -n "$archive_cmd" ] || archive_cmd="python3 scripts/replacement_transaction.py archive --path $REPLACEMENT_TRANSACTION_FILE --yes"
  warn "saved replacement transaction cannot be restored exactly"
  [ -z "$detail" ] || log "$detail"
  if [ "$reason" = replicated ]; then
    log "this record predates the library-only decision (ADR 0006)"
  fi
  if [ -n "$profile" ]; then
    log "saved profile $profile · inventory shows that service as $service_state"
  else
    log "saved profile could not be read · inventory was not used to identify a previous service"
  fi
  log "exact rollback is unavailable; archive the leftover transaction to continue"
  log "live path: $REPLACEMENT_TRANSACTION_FILE"
  log "noninteractive remediation: $archive_cmd"
  if ! confirm "Archive leftover replacement transaction and continue?"; then
    die "leftover transaction was left in place; rerun $archive_cmd after inspecting ./pulsar inventory"
  fi
  if ! cmd_replacement_transaction archive --path "$REPLACEMENT_TRANSACTION_FILE" --yes >/dev/null; then
    die "could not archive the leftover transaction; live path $REPLACEMENT_TRANSACTION_FILE"
  fi
  log "leftover transaction archived; wizard can continue without exact rollback"
  return 0
}

recover_replacement_transaction() {
  [ -f "$REPLACEMENT_TRANSACTION_FILE" ] || return 0
  local inventory inventory_file="$WIZARD_WORK_DIR/recovery-inventory.json"
  local result state rc=0
  warn "an unfinished serving replacement transaction was found"
  collect_inventory_json_or_die inventory
  printf '%s\n' "$inventory" >"$inventory_file"
  result=$(cmd_replacement_transaction recovery-state \
      --path "$REPLACEMENT_TRANSACTION_FILE" --inventory "$inventory_file") || rc=$?
  if ! printf '%s' "$result" | python3 -c 'import json,sys; json.load(sys.stdin)' >/dev/null 2>&1; then
    warn "replacement recovery did not return a usable report; no lifecycle action was taken"
    log "live path: $REPLACEMENT_TRANSACTION_FILE"
    log "noninteractive remediation: python3 scripts/replacement_transaction.py archive --path $REPLACEMENT_TRANSACTION_FILE --yes"
    die "inspect ./pulsar inventory, then archive or resolve the leftover transaction"
  fi
  state=$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
  case "$state" in
    incompatible)
      recover_incompatible_transaction "$result"
      return 0
      ;;
    previous-running|stopped)
      [ "$rc" -eq 0 ] || die "replacement recovery state is inconsistent"
      ;;
    ambiguous)
      warn "replacement recovery is ambiguous; no lifecycle action was taken"
      log "live path: $REPLACEMENT_TRANSACTION_FILE"
      log "remediation: inspect ./pulsar inventory"
      log "if exact rollback is impossible: python3 scripts/replacement_transaction.py archive --path $REPLACEMENT_TRANSACTION_FILE --yes"
      die "resolve partial or newly running managed services directly, then rerun ./pulsar wizard"
      ;;
    *)
      die "replacement recovery state is unsupported"
      ;;
  esac
  load_transaction_summary
  PREVIOUS_PROFILE="$TRANSACTION_PREVIOUS_PROFILE"
  case "$state" in
    previous-running)
      log "the exact previous service is already running; closing the recovered transaction"
      NAME="$TRANSACTION_PREVIOUS_PROFILE"
      finalize_replacement_transaction rollback \
        || die "the previous service is running, but temporary retention cleanup is incomplete"
      return 0
      ;;
    stopped)
      local restart_rc=0
      offer_restart_previous || restart_rc=$?
      case "$restart_rc" in
        0) return 10 ;;
        2) return 0 ;;
        *) die "replacement recovery selection failed" ;;
      esac
      ;;
  esac
}

prelaunch_inventory_is_clear() {
  local inv="$1" reason
  reason=$(INV_JSON="$inv" TARGET_NODE_KEY="$PLACEMENT_NODE_KEY" \
    TARGET_NODE_ID="$PLACEMENT_NODE_ID" PLACEMENT_AWARE="$PLACEMENT_AWARE" \
    NODES="$NODES" python3 - <<'PY'
import json
import os

inv = json.loads(os.environ.get("INV_JSON") or "{}")
nodes = inv.get("nodes") or {}
node_count = int(os.environ.get("NODES") or 1)
selected_key = os.environ.get("TARGET_NODE_KEY") or "head"
selected_id = os.environ.get("TARGET_NODE_ID") or ""
placement_aware = os.environ.get("PLACEMENT_AWARE") == "1"

if node_count == 1:
    target_nodes = {selected_key}
else:
    target_nodes = {
        "head" if rank == 0 else ("worker" if rank == 1 else f"rank-{rank}")
        for rank in range(node_count)
    }

if placement_aware:
    node = nodes.get(selected_key)
    if not isinstance(node, dict):
        print(f"selected node {selected_key} disappeared from inventory")
        raise SystemExit(1)
    if node.get("confirmed") is False:
        print(f"selected node {selected_key} is no longer confirmed")
        raise SystemExit(1)
    if selected_id and node.get("node_id") != selected_id:
        print("selected physical node identity changed before launch")
        raise SystemExit(1)
    if selected_key != "head" and (node.get("probe_status") or "") != "ok":
        print(f"selected node {selected_key} is no longer reachable with Docker")
        raise SystemExit(1)

for service in inv.get("services") or []:
    for rank in service.get("ranks") or []:
        if rank.get("node") in target_nodes and rank.get("running"):
            label = service.get("conf") or service.get("service_id") or "unknown"
            print(f"service {label} appeared on the selected physical node")
            raise SystemExit(1)
raise SystemExit(0)
PY
  ) || {
    warn "prelaunch inventory changed: ${reason:-selected node is not clear}"
    return 1
  }
}

run_post_stop_memory() {
  # Always refresh selected-node inventory and memory before launch.
  local phase="${1:-after-stop}" inv mem_json mrc
  if [ "$phase" = prelaunch ]; then
    log "re-running inventory and memory immediately before launch…"
  else
    log "re-running inventory after stop (memory reclaim is not assumed)…"
  fi
  collect_inventory_json_or_die inv
  prelaunch_inventory_is_clear "$inv" || return 1
  render_relevant_services "$inv"
  log "cold memory preflight for $NAME on $PLACEMENT_HOSTNAME…"
  collect_memory_json_or_die mem_json mrc "$NAME" "${PLACEMENT_ARGS[@]}"
  render_target_summary "$inv" "$mem_json" "$mrc"
  if [ "$mrc" = 1 ]; then
    warn "memory preflight FAIL immediately before launch — will not launch"
    local free need fp
    read_mem_budget_fields "$mem_json"
    log "target=$NAME node=$PLACEMENT_HOSTNAME free=${free:-?} GiB footprint=${fp:-?} need_start=${need:-?} GiB"
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
    log "memory WARN: node=$PLACEMENT_HOSTNAME free=${free:-?} GiB footprint=${fp:-?} need_start=${need:-?} GiB for $NAME"
    if confirm "Memory is tight on $PLACEMENT_HOSTNAME. Continue with start anyway?"; then
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
    collect_memory_json_or_die mem_json mrc "$NAME" "${PLACEMENT_ARGS[@]}"

    render_target_summary "$inv" "$mem_json" "$mrc"
    render_relevant_services "$inv"
    analyze_inventory "$inv"
    analysis="$ANALYZE_JSON"

    local same_running same_weight_source same_weight_source_matches
    local worker_unreach partial_remote_unreach has_unmanaged
    local others_safe partial_safe stale_same port_unknown
    local unknown_ids legacy_ids mismatch_ids others_unsafe partial_unsafe
    local stale_safe
    # shellcheck disable=SC2034 # assigned via eval from analysis_exports
    eval "$(analysis_exports "$analysis")"

    # ----- same profile running (complete managed) -----
    if [ "$same_running" = "True" ]; then
      if [ "$same_weight_source_matches" != "True" ]; then
        log "running service predates the library-only decision (weights=$same_weight_source)"
        pick=$(choose "Same model runs a pre-library launch — what next?" \
          "Restart with library weights (stop after final confirm)" \
          "Keep current service and exit" \
          "Show status" \
          "Choose another model")
      elif api_serves_selected; then
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
        Restart\ with*)
          PENDING_STOP=("$NAME")
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
          if ! cmd_up "$NAME" "${PLACEMENT_ARGS[@]}" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes; then
            warn "launch failed after restart of $NAME"
            local rc=0
            offer_restart_previous || rc=$?
            if [ "$rc" = 0 ]; then return 10; fi
            if [ "$rc" = 2 ]; then return 2; fi
            exit 1
          fi
          cmd_status "$NAME" "${PLACEMENT_ARGS[@]}" || true
          finalize_replacement_transaction replacement \
            || die "replacement is running; previous-view cleanup needs the direct remediation shown above"
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

    # ----- any remote failure blocks multi-node cleanup/replacement -----
    if [ "$worker_unreach" = "True" ] \
        && { [ "$NODES" -gt 1 ] || [ "$PLACEMENT_REMOTE" = 1 ]; }; then
      log "one or more cluster nodes required by this model are unreachable — refusing automatic cleanup/replacement"
      log "restore key-based SSH and Docker access on every required cluster node"
      pick=$(choose "Required cluster node unreachable — what next?" \
        "Exit" \
        "Choose another model" \
        "Show diagnostics")
      case "$pick" in
        Exit*) exit 0 ;;
        Choose*) return 2 ;;
        *) show_diagnostics "$inv"; continue ;;
      esac
    fi
    # Partial multi-node evidence also needs every required node observable.
    if [ "$partial_remote_unreach" = "True" ] \
        && { [ -n "$partial_safe" ] || [ -n "$partial_unsafe" ]; }; then
      log "a cluster node is unreachable while the existing service is incomplete — refusing automatic cleanup"
      pick=$(choose "Cluster node unreachable — what next?" \
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
      log "observed cluster nodes may be incomplete — Wizard will not imply completeness or force cleanup"
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
      log "partial/degraded managed service(s) with inventory-safe observed nodes: $partial_safe"
      log "cleanup covers observed nodes only — not a claim of full cluster completeness"
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

    local prc=0
    if [ "${#PENDING_STOP[@]}" -gt 0 ]; then
      log "stops scheduled: ${PENDING_STOP[*]}"
      execute_pending_stops
      run_post_stop_memory after-stop || prc=$?
    elif [ "$PLACEMENT_AWARE" = 1 ]; then
      run_post_stop_memory prelaunch || prc=$?
    fi
    if [ "$prc" = 2 ]; then return 2; fi
    if [ "$prc" = 10 ]; then return 10; fi
    if [ "$prc" != 0 ]; then exit 1; fi

    log "launching $NAME…"
    if ! cmd_up "$NAME" "${PLACEMENT_ARGS[@]}" "${SPEC_ARGS[@]+"${SPEC_ARGS[@]}"}" "${ACCEPT[@]+"${ACCEPT[@]}"}" --yes; then
      warn "launch failed for $NAME"
      if [ -n "${PREVIOUS_PROFILE:-}" ] && [ -f "$REPLACEMENT_TRANSACTION_FILE" ]; then
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
    cmd_status "$NAME" "${PLACEMENT_ARGS[@]}" || true
    local outcome=replacement
    [ "$ROLLBACK_ACTIVE" = 0 ] || outcome=rollback
    finalize_replacement_transaction "$outcome" \
      || die "$outcome is running, but transaction cleanup is incomplete; rerun ./pulsar wizard"
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

reload_cluster_topology || die "confirmed topology is invalid"
if [ "$CLUSTER_TOPOLOGY_COUNT" -eq 0 ]; then
  # Serving requires a confirmed topology manifest (ADR 0006): the model
  # library binds durable homes and hot views to confirmed topology identity,
  # and topology identity is never synthesized.
  log "no confirmed topology manifest exists; serving requires one (one machine is fine)"
  if [ "${WIZARD_SKIP_FABRIC_PROMPT:-0}" = 1 ] \
      || ! confirm "Discover and confirm topology membership now? (required before serving)"; then
    die "serving requires a confirmed topology manifest: run scripts/detect-fabric.sh --write-topology"
  fi
  "$REPO_DIR/scripts/detect-fabric.sh" --write-topology
  reload_cluster_topology || die "newly written topology failed validation"
  [ "$CLUSTER_TOPOLOGY_COUNT" -ge 1 ] \
    || die "topology confirmation did not record any node; rerun scripts/detect-fabric.sh --write-topology"
elif [ "${WIZARD_SKIP_FABRIC_PROMPT:-0}" != 1 ] \
    && [ "$CLUSTER_TOPOLOGY_COUNT" -le 1 ]; then
  if confirm "Discover and confirm additional GB10 cluster membership? (one confirmed node remains available if skipped)"; then
    "$REPO_DIR/scripts/detect-fabric.sh" --write-topology
    reload_cluster_topology || die "newly written topology failed validation"
  fi
fi
if [ -n "${WIZARD_TOPOLOGY_NODES:-}" ]; then
  topology_capacity="$WIZARD_TOPOLOGY_NODES"
else
  topology_capacity="$CLUSTER_TOPOLOGY_COUNT"
fi
[[ "$topology_capacity" =~ ^[1-9][0-9]*$ ]] \
  || die "invalid confirmed topology capacity '$topology_capacity'"
topology_context="${topology_capacity} confirmed nodes available"
log "$topology_capacity confirmed nodes available · all fitting serving profiles shown"

recovery_rc=0
recover_replacement_transaction || recovery_rc=$?
while [ "$recovery_rc" = 10 ]; do
  log "planning exact rollback of previous service $NAME"
  recovery_rc=0
  plan_selected_model || recovery_rc=$?
done
case "$recovery_rc" in
  0|2) ;;
  *) exit "$recovery_rc" ;;
esac

# Selection loop: "Choose another model" returns here without re-running doctor.
while true; do
  mapfile -t choices < <(
    cmd_list_models_json | MAX_NODES="$topology_capacity" python3 -c "
import json
import os
import sys

capacity = int(os.environ.get('MAX_NODES') or 1)
models = [
    model for model in json.load(sys.stdin).get('models', [])
    if int(model.get('nodes') or 1) <= capacity
]
def recommended(model):
    return str(model.get('status') or '').startswith('tested')

def release_status(model):
    release = model.get('model_serving_release') or {}
    return str(release.get('effective_status_label') or 'No release binding')

models.sort(key=lambda model: (
    not recommended(model),
    str(model.get('id') or '').casefold(),
))

family_models = {}
for model in models:
    family = model.get('family') or model.get('served_name') or model['id']
    family_models.setdefault(family, []).append(model)
family_min = {}
for model in models:
    family = model.get('family') or model.get('served_name') or model['id']
    family_min[family] = min(family_min.get(family, 10**9), int(model['nodes']))

name_width = max((len(str(model['id'])) for model in models), default=0)
for model in models:
    family = model.get('family') or model.get('served_name') or model['id']
    nodes = int(model['nodes'])
    suggested = recommended(model) and (bool(model.get('family_recommended')) or (
        len(family_models[family]) > 1 and nodes == family_min[family]
    ))
    marks = []
    if suggested:
        marks.append('suggested')
    if model.get('first_run_candidate'):
        marks.append('first run')
    marks.append('release={}'.format(release_status(model)))
    marks.append('legacy={}'.format(model.get('status') or '?'))
    suffix = (' · ' + ' · '.join(marks)) if marks else ''
    node_word = 'node' if nodes == 1 else 'nodes'
    print(f\"{model['id']:<{name_width}}  {nodes} {node_word}{suffix}\")
"
  )
  if [ "${#choices[@]}" -eq 0 ]; then
    die "no serving profile fits the $topology_capacity confirmed node(s)"
  fi

  pick=$(choose "Choose a model · status labels are display-only · $topology_context" "${choices[@]}")
  NAME=$(echo "$pick" | awk '{print $1}')
  [ -n "$NAME" ] || die "no selection"

  load_conf "$NAME"
  if [ "$NODES" -eq 1 ]; then
    select_single_node_placement
  else
    reset_placement_state
  fi
  echo
  render_model_selection

  source_rc=0
  confirm_library_serving || source_rc=$?
  if [ "$source_rc" = 2 ]; then
    log "choose another model — returning to selection"
    continue
  fi
  [ "$source_rc" = 0 ] || exit "$source_rc"

  if [ "${WIZARD_SKIP_WEIGHTS:-0}" != 1 ]; then
    log "checking weights…"
    if ! cmd_check_weights "$NAME" "${PLACEMENT_ARGS[@]}" --json >/dev/null; then
      # Readiness was established moments earlier; a failure here is genuine
      # inconsistency, never a download prompt.
      die "library runtime views are not ready on the selected ranks; no fallback was attempted"
    fi
  fi

  if [ "${WIZARD_SKIP_IMAGE:-0}" != 1 ]; then
    log "checking image…"
    image_json=""
    image_rc=0
    image_json=$("$REPO_DIR/scripts/check-image.sh" "$NAME" "${PLACEMENT_ARGS[@]}" --json) || image_rc=$?
    if [ "$image_rc" != 0 ]; then
      if ! probe_json_has_state "$image_json"; then
        die "image preflight returned invalid data — no pull, sync, or launch attempted"
      fi
      image_state=$(json_field "$image_json" state)
      log "image state=$image_state"
      case "$image_state" in
        head-docker-error|target-docker-error)
          die "Docker is unavailable on $PLACEMENT_HOSTNAME — no image staging attempted"
          ;;
        worker-unreachable|rank-unreachable|target-unreachable)
          die "one or more cluster nodes required by this model are unreachable — no image copy attempted"
          ;;
        worker-docker-error|rank-docker-error)
          die "Docker is unavailable on one or more cluster nodes required by this model"
          ;;
        need-topology)
          die "fewer cluster nodes are confirmed than this model requires"
          ;;
        *)
          case "$IMAGE" in
            vllm/vllm-openai:*|vllm/*|ghcr.io/*)
              if confirm "Image missing. Stage it on $PLACEMENT_HOSTNAME now?"; then
                spin "Syncing image…" "$REPO_DIR/scripts/sync-image.sh" "$NAME" "${PLACEMENT_ARGS[@]}" --pull --yes
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
    # Exact captured contract is already loaded; re-enter the ordinary gates.
    log "planning exact rollback of previous service $NAME"
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
