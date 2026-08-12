#!/usr/bin/env bash
# Read-only interactive model catalog and storage view.
#   scripts/model-storage.sh       (also: ./pulsar models)
#
# Consumes the stable model-library health schema. It never refreshes the
# catalog, prepares files, starts a model, or runs lifecycle mutations.
# Test hooks:
#   MODEL_STORAGE_HEALTH_JSON=path
#   MODEL_STORAGE_HEALTH_CMD=executable
#   MODEL_STORAGE_HEALTH_RC=0|1
set -euo pipefail

# Read dynamically by the sourced logging helpers.
# shellcheck disable=SC2034
SCRIPT_NAME=model-storage
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"
# Read dynamically by the sourced UI/logging helpers.
# shellcheck disable=SC2034
SCRIPT_NAME=model-storage
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/ui.sh"

RENDERER="${MODEL_STORAGE_RENDERER:-$REPO_DIR/scripts/model_storage.py}"

usage() {
  cat <<'EOF'
usage: scripts/model-storage.sh

Browse the cached distributed model catalog and rank-local runtime views.
This workflow is read-only. It does not refresh the catalog, move model files,
start a model, or run pin, purge, repair, or durable-home operations.

The promoted serving default remains replicated local model copies.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

cmd_health_json() {
  if [ -n "${MODEL_STORAGE_HEALTH_JSON:-}" ]; then
    cat "$MODEL_STORAGE_HEALTH_JSON"
    return "${MODEL_STORAGE_HEALTH_RC:-0}"
  fi
  if [ -n "${MODEL_STORAGE_HEALTH_CMD:-}" ]; then
    "$MODEL_STORAGE_HEALTH_CMD" --json
    return $?
  fi
  "$REPO_DIR/scripts/model-library.sh" health --json
}

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-model-storage.XXXXXX")
report_file="$work_dir/health.json"
error_file="$work_dir/health.stderr"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

collect_health() {
  local health_rc=0
  : >"$report_file"
  : >"$error_file"
  set +e
  cmd_health_json >"$report_file" 2>"$error_file"
  health_rc=$?
  set -e

  if [ "$health_rc" -gt 1 ]; then
    warn "model-library health failed with status $health_rc — no catalog action was taken"
    return 1
  fi

  if ! python3 "$RENDERER" --report-file "$report_file" validate; then
    warn "model-library health returned invalid data — no catalog action was taken"
    if [ "${PULSAR_VERBOSE:-0}" = 1 ] && [ -s "$error_file" ]; then
      sed 's/^/[model-storage] health: /' "$error_file" >&2
    fi
    return 1
  fi

  # health exits nonzero for attention/unavailable while still returning the
  # complete stable report. The interactive view renders that state instead of
  # treating it as an empty catalog.
  if [ "$health_rc" -ne 0 ] && [ "${PULSAR_VERBOSE:-0}" = 1 ] \
      && [ -s "$error_file" ]; then
    sed 's/^/[model-storage] health: /' "$error_file" >&2
  fi
  return 0
}

pause_back() {
  local header="$1"
  choose "$header" "Back to models" >/dev/null || true
}

if ! collect_health; then
  exit 1
fi

while true; do
  echo
  python3 "$RENDERER" --report-file "$report_file" summary
  echo

  model_choices=()
  while IFS= read -r line; do
    [ -n "$line" ] && model_choices+=("$line")
  done < <(python3 "$RENDERER" --report-file "$report_file" choices)

  choices=("${model_choices[@]}")
  choices+=(
    "Catalog findings"
    "How model storage works"
    "Recheck catalog health"
    "Back"
  )

  pick=""
  if ! pick=$(choose "Models & storage · read-only" "${choices[@]}"); then
    log "cancelled; no catalog or model state changed"
    exit 0
  fi

  selected_index=-1
  for index in "${!model_choices[@]}"; do
    if [ "$pick" = "${model_choices[$index]}" ]; then
      selected_index="$index"
      break
    fi
  done

  if [ "$selected_index" -ge 0 ]; then
    echo
    python3 "$RENDERER" --report-file "$report_file" \
      detail --index "$selected_index"
    echo
    pause_back "Model storage detail"
    continue
  fi

  case "$pick" in
    "Catalog findings")
      echo
      python3 "$RENDERER" --report-file "$report_file" findings
      echo
      pause_back "Catalog findings"
      ;;
    "How model storage works")
      echo
      python3 "$RENDERER" --report-file "$report_file" about
      echo
      pause_back "Model storage"
      ;;
    "Recheck catalog health")
      log "rechecking cached catalog and runtime-view health (read-only)…"
      if ! collect_health; then
        exit 1
      fi
      ;;
    "Back"|"")
      log "back; no catalog or model state changed"
      exit 0
      ;;
  esac
done
