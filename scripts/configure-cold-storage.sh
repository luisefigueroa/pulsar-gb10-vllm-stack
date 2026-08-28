#!/usr/bin/env bash
# Explicit cold recovery storage configuration (ADR 0015).
# Thin argv / Gum / plain boundary. Python owns parse, plans, and writes.
# This script must not source .env as its parser.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/ui.sh"

PY_TOOL="${PULSAR_COLD_STORAGE_PY:-$REPO_DIR/scripts/model_library_cold_storage.py}"
ARCHIVE_OWNER="${PULSAR_COLD_STORAGE_ARCHIVE_RUN_CMD:-$REPO_DIR/scripts/model-library.sh}"

log() { printf '[configure-cold-storage] %s\n' "$*"; }
warn() { printf '[configure-cold-storage] warn: %s\n' "$*" >&2; }
die() { printf '[configure-cold-storage] ERROR: %s\n' "$*" >&2; exit "${2:-2}"; }

usage() {
  cat <<'EOF'
Configure explicit cold recovery storage

Usage:
  ./pulsar configure cold-storage
  ./pulsar configure cold-storage show [--json]
  ./pulsar configure cold-storage plan --path PATH [--json]
  ./pulsar configure cold-storage set --path PATH --yes [--json]
  ./pulsar configure cold-storage disable --yes [--json]
  ./pulsar configure cold-storage archive-jobs [--json]

Bare invocation opens the interactive workflow. show, plan, and
archive-jobs are read-only. set and disable print the exact preview,
require confirmation, recheck immediately, then write. Direct
noninteractive mutation requires --yes.

Live configuration is PULSAR_COLD_ROOT only. There is no MODELS_NFS
alias and no implicit /mnt/Models recovery fallback. The selected
directory must already exist. Pulsar never creates, mounts, or
administers it. Existing non-Pulsar content stays untouched.

Pulsar can verify path safety and recovery-set integrity. You assert
that this storage location meets your recovery and failure-domain policy.
EOF
}

run_py() {
  python3 "$PY_TOOL" "$@"
}

need_yes_or_confirm() {
  local yes="$1"
  local prompt="$2"
  if [ "$yes" = 1 ]; then
    return 0
  fi
  if [ ! -t 0 ] && [ "${PULSAR_FORCE_GUM:-0}" != 1 ] \
      && [ "${PULSAR_COLD_STORAGE_INTERACTIVE:-0}" != 1 ]; then
    die "noninteractive mutation requires --yes"
  fi
  if confirm "$prompt"; then
    return 0
  fi
  log "declined; no configuration change"
  return 1
}

prompt_path() {
  local header="${1:-Existing cold recovery directory}"
  local line=""
  if [ "${have_gum:-0}" = 1 ]; then
    local rc=0
    set +e
    line=$("$GUM_CMD" input --header "$header" --placeholder "/absolute/existing/directory")
    rc=$?
    set -e
    if [ "$rc" -ne 0 ] || [ -z "${line:-}" ]; then
      return 1
    fi
    printf '%s\n' "$line"
    return 0
  fi
  echo "$header" >&2
  if ! read -r -p "Path: " line; then
    return 1
  fi
  if [ -z "${line:-}" ]; then
    return 1
  fi
  printf '%s\n' "$line"
}

cmd_show() {
  local json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown argument: $1" ;;
    esac
    shift
  done
  if [ "$json" = 1 ]; then
    run_py show --json
  else
    run_py show
  fi
}

cmd_plan() {
  local json=0 path="" disable=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --disable) disable=1 ;;
      --path)
        shift
        [ $# -gt 0 ] || die "--path needs a directory"
        path="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown argument: $1" ;;
    esac
    shift
  done
  local args=(plan)
  if [ "$disable" = 1 ]; then
    args+=(--disable)
  else
    [ -n "$path" ] || die "plan --path is required"
    args+=(--path "$path")
  fi
  [ "$json" = 0 ] || args+=(--json)
  run_py "${args[@]}"
}

cmd_set() {
  local json=0 yes=0 path=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --yes|-y) yes=1 ;;
      --path)
        shift
        [ $# -gt 0 ] || die "--path needs a directory"
        path="$1"
        ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown argument: $1" ;;
    esac
    shift
  done
  if [ -z "$path" ]; then
    path=$(prompt_path "Existing directory to use for cold recovery storage") \
      || { log "cancelled; no configuration change"; return 0; }
  fi
  local plan_json plan_id action plan_rc=0
  set +e
  plan_json=$(run_py plan --path "$path" --json)
  plan_rc=$?
  set -e
  plan_id=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("plan_id") or "")' 2>/dev/null || true)
  action=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("action") or "")' 2>/dev/null || true)
  [ -n "$plan_id" ] || die "could not build a configuration plan"
  if [ "$json" != 1 ] || [ "$yes" != 1 ]; then
    if [ "$json" = 1 ]; then
      printf '%s\n' "$plan_json"
    else
      printf '%s\n' "$plan_json" | run_py render-plan || true
    fi
  fi
  if [ "$action" = "change-blocked" ] || [ "$plan_rc" -ne 0 ]; then
    return 1
  fi
  need_yes_or_confirm "$yes" "Write PULSAR_COLD_ROOT to the repository .env?" \
    || return 0
  local args=(set --path "$path" --yes --plan-id "$plan_id")
  [ "$json" = 0 ] || args+=(--json)
  run_py "${args[@]}"
}

cmd_disable() {
  local json=0 yes=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      --yes|-y) yes=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown argument: $1" ;;
    esac
    shift
  done
  local plan_json plan_id action plan_rc=0
  set +e
  plan_json=$(run_py plan --disable --json)
  plan_rc=$?
  set -e
  plan_id=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("plan_id") or "")' 2>/dev/null || true)
  action=$(printf '%s' "$plan_json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("action") or "")' 2>/dev/null || true)
  [ -n "$plan_id" ] || die "could not build a configuration plan"
  if [ "$json" != 1 ] || [ "$yes" != 1 ]; then
    if [ "$json" = 1 ]; then
      printf '%s\n' "$plan_json"
    else
      printf '%s\n' "$plan_json" | run_py render-plan || true
    fi
  fi
  if [ "$action" = "change-blocked" ] || [ "$plan_rc" -ne 0 ]; then
    return 1
  fi
  need_yes_or_confirm "$yes" "Persist PULSAR_COLD_ROOT='' and disable cold recovery storage?" \
    || return 0
  local args=(disable --yes --plan-id "$plan_id")
  [ "$json" = 0 ] || args+=(--json)
  run_py "${args[@]}"
}

cmd_archive_jobs() {
  local json=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --json) json=1 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown argument: $1" ;;
    esac
    shift
  done
  if [ "$json" = 1 ]; then
    run_py archive-jobs --json
  else
    run_py archive-jobs
  fi
}

retry_one_job() {
  local receipt_id="$1"
  local preview
  preview=$(run_py retry-plan --receipt "$receipt_id" --json) || true
  run_py retry-plan --receipt "$receipt_id" || true
  local eligible
  eligible=$(printf '%s' "$preview" | python3 -c 'import json,sys; print("1" if json.load(sys.stdin).get("eligible") else "0")')
  if [ "$eligible" != 1 ]; then
    return 1
  fi
  if ! confirm "Retry this archive job by running home archive run --yes?"; then
    log "declined; no archive job was started"
    return 0
  fi
  "$ARCHIVE_OWNER" home archive run --receipt "$receipt_id" --yes
}

cmd_first_use() {
  local state pick
  PULSAR_COLD_STORAGE_INTERACTIVE=1
  state=$(run_py persisted-state)
  if [ "$state" != "not-configured" ]; then
    return 0
  fi
  echo
  log "cold recovery storage has no explicit persisted choice"
  if ! pick=$(choose "Cold recovery storage is not configured" \
    "Configure existing path" \
    "Disable cold recovery storage" \
    "Not now"); then
    log "cancelled; no configuration change"
    return 0
  fi
  case "$pick" in
    Configure*)
      cmd_set || true
      ;;
    Disable*)
      cmd_disable || true
      ;;
    *)
      log "not now; cold archive stays unavailable until explicit configuration"
      ;;
  esac
}

inspect_archive_jobs_interactive() {
  PULSAR_COLD_STORAGE_INTERACTIVE=1
  cmd_archive_jobs || return $?
  local jobs_json count
  jobs_json=$(run_py archive-jobs --json) || return 1
  count=$(printf '%s' "$jobs_json" | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("jobs") or []))')
  if [ "${count:-0}" = 0 ]; then
    return 0
  fi
  local choices=()
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    choices+=("$line")
  done < <(
    JOBS_JSON="$jobs_json" python3 - <<'PY'
import json, os
document = json.loads(os.environ.get("JOBS_JSON") or "{}")
for job in document.get("jobs") or []:
    if not job.get("retry_eligible"):
        continue
    print(
        f"{job.get('receipt_id')}\t{job.get('receipt_id_prefix')} · "
        f"{job.get('model_id') or '-'} · {job.get('state')}"
    )
PY
  )
  if [ "${#choices[@]}" -eq 0 ]; then
    log "no eligible archive job to retry"
    return 0
  fi
  local labels=("Skip retry")
  local ids=("")
  local entry id label
  for entry in "${choices[@]}"; do
    id="${entry%%$'\t'*}"
    label="${entry#*$'\t'}"
    ids+=("$id")
    labels+=("$label")
  done
  local index
  if ! index=$(choose_index "Retry one eligible archive job?" "${labels[@]}"); then
    log "cancelled; no archive job was started"
    return 0
  fi
  if [ "$index" = 0 ]; then
    log "no archive job was started"
    return 0
  fi
  retry_one_job "${ids[$index]}"
}

interactive_workflow() {
  PULSAR_COLD_STORAGE_INTERACTIVE=1
  while true; do
    echo
    local pick
    if ! pick=$(choose "Cold recovery storage" \
      "Show configuration and health" \
      "Set or change storage path" \
      "Disable cold recovery storage" \
      "Inspect archive jobs" \
      "Back"); then
      log "cancelled; no configuration change"
      return 0
    fi
    case "$pick" in
      Show*)
        cmd_show || true
        ;;
      Set*)
        cmd_set || true
        ;;
      Disable*)
        cmd_disable || true
        ;;
      Inspect*)
        inspect_archive_jobs_interactive || true
        ;;
      Back*|*)
        return 0
        ;;
    esac
  done
}

cmd="${1:-}"
if [ $# -gt 0 ]; then
  shift
fi

case "$cmd" in
  "" )
    interactive_workflow
    ;;
  show)
    cmd_show "$@"
    ;;
  plan)
    cmd_plan "$@"
    ;;
  set)
    cmd_set "$@"
    ;;
  disable)
    cmd_disable "$@"
    ;;
  archive-jobs)
    cmd_archive_jobs "$@"
    ;;
  inspect)
    inspect_archive_jobs_interactive
    ;;
  first-use)
    cmd_first_use
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    die "unknown command: $cmd"
    ;;
esac
