#!/usr/bin/env bash
# Interactive model catalog and storage view.
#   scripts/model-storage.sh       (also: ./pulsar models)
#
# Consumes the stable model-library health schema. Browsing is read-only;
# catalog refresh is a separate, explicit, confirmation-gated action. This
# model preparation is a separate, explicit experimental action. This workflow
# never starts a model or runs retention, repair, or deletion mutations.
# Test hooks:
#   MODEL_STORAGE_HEALTH_JSON=path
#   MODEL_STORAGE_HEALTH_CMD=executable
#   MODEL_STORAGE_HEALTH_RC=0|1
#   MODEL_STORAGE_REFRESH_CMD=executable
#   MODEL_STORAGE_PROFILES_JSON=path
#   MODEL_STORAGE_PROFILES_CMD=executable
#   MODEL_STORAGE_PREPARE_CMD=executable
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
Browsing and health rechecks are read-only. An explicit, confirmation-gated
action can rescan confirmed ranks and update only the cached catalog. This
workflow can explicitly prepare a reviewed model through the experimental
distributed path. It does not start a model or run pin, purge, repair, or
durable-home operations.

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

cmd_catalog_refresh() {
  if [ -n "${MODEL_STORAGE_REFRESH_CMD:-}" ]; then
    "$MODEL_STORAGE_REFRESH_CMD" catalog refresh
    return $?
  fi
  "$REPO_DIR/scripts/model-library.sh" catalog refresh
}

cmd_profiles_json() {
  if [ -n "${MODEL_STORAGE_PROFILES_JSON:-}" ]; then
    cat "$MODEL_STORAGE_PROFILES_JSON"
    return 0
  fi
  if [ -n "${MODEL_STORAGE_PROFILES_CMD:-}" ]; then
    "$MODEL_STORAGE_PROFILES_CMD" --validated --serving --json
    return $?
  fi
  "$REPO_DIR/scripts/list-models.sh" --validated --serving --json
}

cmd_prepare_model() {
  local profile="${1:?profile required}"
  if [ -n "${MODEL_STORAGE_PREPARE_CMD:-}" ]; then
    "$MODEL_STORAGE_PREPARE_CMD" prepare "$profile" \
      --backend copy --transport ssh-roce --copy-streams 8 --yes
    return $?
  fi
  "$REPO_DIR/scripts/model-library.sh" prepare "$profile" \
    --backend copy --transport ssh-roce --copy-streams 8 --yes
}

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-model-storage.XXXXXX")
report_file="$work_dir/health.json"
error_file="$work_dir/health.stderr"
profiles_file="$work_dir/profiles.json"
profiles_error_file="$work_dir/profiles.stderr"
refresh_output_file="$work_dir/refresh.stdout"
refresh_error_file="$work_dir/refresh.stderr"
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

collect_profiles() {
  local profiles_rc=0
  : >"$profiles_file"
  : >"$profiles_error_file"
  set +e
  cmd_profiles_json >"$profiles_file" 2>"$profiles_error_file"
  profiles_rc=$?
  set -e
  if [ "$profiles_rc" -ne 0 ]; then
    warn "serving-profile catalog is unavailable — experimental preparation is disabled"
    printf '{"models":[]}\n' >"$profiles_file"
    return 0
  fi
  if ! python3 "$RENDERER" --report-file "$report_file" \
      --profiles-file "$profiles_file" validate 2>>"$profiles_error_file"; then
    warn "serving-profile catalog is invalid — experimental preparation is disabled"
    if [ "${PULSAR_VERBOSE:-0}" = 1 ] && [ -s "$profiles_error_file" ]; then
      sed 's/^/[model-storage] profiles: /' "$profiles_error_file" >&2
    fi
    printf '{"models":[]}\n' >"$profiles_file"
  fi
}

render() {
  python3 "$RENDERER" --report-file "$report_file" \
    --profiles-file "$profiles_file" "$@"
}

pause_back() {
  local header="$1"
  choose "$header" "Back to models" >/dev/null || true
}

refresh_catalog() {
  echo
  render refresh
  echo
  if ! confirm "Refresh the distributed catalog now?" no; then
    log "catalog refresh cancelled; cached catalog and model files were not changed"
    return 0
  fi

  : >"$refresh_output_file"
  : >"$refresh_error_file"
  log "refreshing the distributed catalog from every confirmed rank…"
  if ! cmd_catalog_refresh >"$refresh_output_file" 2>"$refresh_error_file"; then
    warn "catalog refresh did not complete — model files and the serving default were not changed"
    if [ -s "$refresh_error_file" ]; then
      sed 's/^/[model-storage] refresh: /' "$refresh_error_file" >&2
    fi
    return 0
  fi

  log "catalog cache updated; checking the refreshed inventory…"
  if ! collect_health; then
    warn "catalog refresh completed, but the refreshed health report is unavailable"
    return 1
  fi
}

prepare_model() {
  local model_index="${1:?}" candidate_index="${2:?}" profile prepare_rc=0
  profile=$(render prepare-profile --index "$model_index" \
    --candidate-index "$candidate_index")
  echo
  render prepare-preview --index "$model_index" \
    --candidate-index "$candidate_index"
  echo
  if ! confirm "Prepare $profile through the experimental distributed catalog?" no; then
    log "model preparation cancelled; no model files were changed"
    return 0
  fi

  log "starting experimental model preparation; this does not start serving…"
  set +e
  cmd_prepare_model "$profile"
  prepare_rc=$?
  set -e
  if [ "$prepare_rc" -ne 0 ]; then
    warn "model preparation did not complete (status $prepare_rc) — serving was not started"
  else
    log "model files prepared and verified; serving was not started"
  fi
  log "checking current catalog and runtime-view health…"
  if ! collect_health; then
    return 1
  fi
  return 0
}

browse_model() {
  local model_index="${1:?}" detail_index candidate_count
  local -a prepare_choices detail_choices
  while true; do
    echo
    render detail --index "$model_index"
    echo
    prepare_choices=()
    while IFS= read -r line; do
      [ -n "$line" ] && prepare_choices+=("$line")
    done < <(render prepare-choices --index "$model_index")
    detail_choices=("${prepare_choices[@]}" "Back to models")
    if ! detail_index=$(choose_index "Model storage detail" "${detail_choices[@]}"); then
      return 0
    fi
    candidate_count=${#prepare_choices[@]}
    if [ "$detail_index" -eq "$candidate_count" ]; then
      return 0
    fi
    if [ "$detail_index" -lt "$candidate_count" ]; then
      prepare_model "$model_index" "$detail_index"
      return $?
    fi
    die "model-storage detail chooser returned an invalid index"
  done
}

if ! collect_health; then
  exit 1
fi
collect_profiles

while true; do
  echo
  render summary
  echo

  model_choices=()
  while IFS= read -r line; do
    [ -n "$line" ] && model_choices+=("$line")
  done < <(render choices)

  choices=("${model_choices[@]}")
  choices+=(
    "Catalog findings"
    "How model storage works"
    "Refresh distributed catalog"
    "Recheck catalog health"
    "Back"
  )

  choice_index=""
  if ! choice_index=$(choose_index \
      "Models & storage" "${choices[@]}"); then
    log "cancelled; no catalog or model state changed"
    exit 0
  fi

  model_count=${#model_choices[@]}
  if [ "$choice_index" -lt "$model_count" ]; then
    browse_model "$choice_index"
    continue
  fi

  action_index=$((choice_index - model_count))
  case "$action_index" in
    0)
      echo
      render findings
      echo
      pause_back "Catalog findings"
      ;;
    1)
      echo
      render about
      echo
      pause_back "Model storage"
      ;;
    2)
      refresh_catalog
      ;;
    3)
      log "rechecking cached catalog and runtime-view health (read-only)…"
      if ! collect_health; then
        exit 1
      fi
      ;;
    4)
      log "back; no catalog or model state changed"
      exit 0
      ;;
    *)
      die "model-storage chooser returned an invalid index"
      ;;
  esac
done
