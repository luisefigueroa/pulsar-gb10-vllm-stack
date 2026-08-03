#!/usr/bin/env bash
# Shared helpers for onboarding scripts. Source only — do not execute.
# shellcheck shell=bash

if [ -n "${_PULSAR_SCRIPTS_LIB:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
_PULSAR_SCRIPTS_LIB=1

_scripts_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$_scripts_dir/.." && pwd)"
cd "$REPO_DIR"

if [ -f "$REPO_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO_DIR/.env"
  set +a
fi

# shellcheck disable=SC1091
. "$REPO_DIR/cluster/cluster-env.sh"

VLLM_IMAGE_MAINLINE="${VLLM_IMAGE_MAINLINE:-vllm/vllm-openai:v0.26.0}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"
MODELS_NFS="${MODELS_NFS:-/mnt/Models}"

# Memory policy defaults (GB10 unified memory)
MIN_OS_BUFFER_GIB="${MIN_OS_BUFFER_GIB:-8}"
HARD_FLOOR_AVAILABLE_GIB="${HARD_FLOOR_AVAILABLE_GIB:-4}"
LAUNCH_SPIKE_GIB="${LAUNCH_SPIKE_GIB:-3}"
OVERHEAD_GIB_DEFAULT="${OVERHEAD_GIB_DEFAULT:-10}"

log()  { printf '[%s] %s\n' "${SCRIPT_NAME:-pulsar}" "$*"; }
warn() { printf '[%s] warn: %s\n' "${SCRIPT_NAME:-pulsar}" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "${SCRIPT_NAME:-pulsar}" "$*" >&2; exit "${2:-1}"; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1 (install it and retry)"
}

# Load models/<name>.conf into caller shell. Resets optional fields first.
load_conf() {
  local name="${1:?load_conf: model name required}"
  local conf="$REPO_DIR/models/${name}.conf"
  [ -f "$conf" ] || die "no such config: $conf (try scripts/list-models.sh)"

  MODEL="" SERVED_NAME="" IMAGE="" NOTES="" STATUS="?"
  NODES=1 PORT=8000 GPU_MEM_UTIL=0.80
  ENGINE_ARGS=() CONTAINER_ENV=() SPEC_DECODE_ARGS=()
  WEIGHTS_GIB="" WEIGHTS_RAM_GIB="" KV_GIB="" OVERHEAD_GIB="" MEM_MIN_FREE_GIB=""
  RECOMMENDED_SPEC=0 FIRST_RUN_CANDIDATE=0

  # shellcheck disable=SC1090
  . "$conf"

  [ -n "$MODEL" ] || die "$name: MODEL unset in conf"
  SERVED_NAME="${SERVED_NAME:-$name}"
  IMAGE="${IMAGE:-$VLLM_IMAGE_MAINLINE}"
  NODES="${NODES:-1}"
  PORT="${PORT:-8000}"
  GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.80}"
  STATUS="${STATUS:-?}"
  OVERHEAD_GIB="${OVERHEAD_GIB:-$OVERHEAD_GIB_DEFAULT}"
  MEM_MIN_FREE_GIB="${MEM_MIN_FREE_GIB:-$MIN_OS_BUFFER_GIB}"
  RECOMMENDED_SPEC="${RECOMMENDED_SPEC:-0}"
  FIRST_RUN_CANDIDATE="${FIRST_RUN_CANDIDATE:-0}"
  CONF_NAME="$name"
  CONF_PATH="$conf"
}

model_source_kind() {
  # hf | nfs
  case "${MODEL:-}" in
    /*) echo nfs ;;
    *)  echo hf ;;
  esac
}

hf_hub_dirname() {
  # org/name -> models--org--name
  local m="${1:-$MODEL}"
  echo "models--${m//\//--}"
}

hf_hub_path() {
  echo "${HF_CACHE}/hub/$(hf_hub_dirname "${1:-$MODEL}")"
}

has_spec_args() {
  [ "${#SPEC_DECODE_ARGS[@]}" -gt 0 ]
}

# Set a caller-owned speculative-decode mode while rejecting contradictory
# overrides. Valid modes are auto (profile policy), on, and off.
set_spec_decode_mode() {
  local var_name="${1:?set_spec_decode_mode: variable name required}"
  local requested="${2:?set_spec_decode_mode: requested mode required}"
  local -n mode_ref="$var_name"
  case "$requested" in
    on|off) ;;
    *) die "invalid speculative-decode mode: $requested" ;;
  esac
  if [ "$mode_ref" != "auto" ] && [ "$mode_ref" != "$requested" ]; then
    die "--spec-decode and --no-spec-decode are mutually exclusive"
  fi
  mode_ref="$requested"
}

# Resolve profile policy plus an explicit CLI override. Call after load_conf.
# RECOMMENDED_SPEC is executable policy: 1 means the validated fast path is the
# default. SPEC_DECODE_ENABLED and SPEC_DECODE_SOURCE are set for the caller.
resolve_spec_decode() {
  local mode="${1:-auto}"
  case "$mode" in
    auto)
      if [ "${RECOMMENDED_SPEC:-0}" = "1" ]; then
        has_spec_args || die "$CONF_NAME sets RECOMMENDED_SPEC=1 without validated SPEC_DECODE_ARGS"
        SPEC_DECODE_ENABLED=1
        SPEC_DECODE_SOURCE="profile-default"
      else
        SPEC_DECODE_ENABLED=0
        SPEC_DECODE_SOURCE="profile-default"
      fi
      ;;
    on)
      has_spec_args || die "$CONF_NAME has no validated SPEC_DECODE_ARGS; refusing --spec-decode"
      SPEC_DECODE_ENABLED=1
      SPEC_DECODE_SOURCE="forced-on"
      ;;
    off)
      SPEC_DECODE_ENABLED=0
      SPEC_DECODE_SOURCE="forced-off"
      ;;
    *) die "invalid speculative-decode mode: $mode" ;;
  esac
}

status_is_tested() {
  case "${STATUS}" in
    tested|tested+soaked|tested*) return 0 ;;
    *) return 1 ;;
  esac
}

# Historical name: true for blocked* only. Prefer status_requires_force for gates.
status_is_blocked() {
  case "${STATUS}" in
    blocked*|BLOCKED*|do-not-use|DO-NOT-USE*) return 0 ;;
    *) return 1 ;;
  esac
}

# Ship default: only STATUS=tested* may launch without --force.
status_is_launchable() {
  status_is_tested
}

status_requires_force() {
  if status_is_launchable; then
    return 1
  fi
  return 0
}

mem_available_gib_local() {
  if [ -r /proc/meminfo ]; then
    local kb
    kb=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    awk -v kb="${kb:-0}" 'BEGIN { printf "%.2f", kb / 1048576 }'
    return
  fi
  # macOS authoring host — not a serve target; report high so dry-runs can proceed
  if command -v vm_stat >/dev/null 2>&1; then
    python3 - <<'PY' 2>/dev/null || echo "64.00"
import subprocess, re
out = subprocess.check_output(["vm_stat"], text=True)
page = 4096
m = re.search(r"page size of (\d+)", out)
if m:
    page = int(m.group(1))
free = speculative = inactive = 0
for line in out.splitlines():
    if line.startswith("Pages free"):
        free = int(line.split(":")[1].strip().rstrip("."))
    elif line.startswith("Pages speculative"):
        speculative = int(line.split(":")[1].strip().rstrip("."))
    elif line.startswith("Pages inactive"):
        inactive = int(line.split(":")[1].strip().rstrip("."))
# rough available ≈ free+speculative+inactive
print(f"{(free+speculative+inactive)*page/1073741824:.2f}")
PY
    return
  fi
  echo "0"
}


mem_available_gib_remote() {
  local host="${1:?}"
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" \
    "awk '/MemAvailable:/ {printf \"%.2f\", \$2/1048576}' /proc/meminfo" 2>/dev/null \
    || echo "0"
}

disk_free_gib() {
  local path="${1:-$HF_CACHE}"
  df -BG "$path" 2>/dev/null | awk 'NR==2 {gsub(/G/,""); print $4}' || echo 0
}

# Free space on worker under the same path (HF_CACHE layout).
disk_free_gib_remote() {
  local path="${1:-$HF_CACHE}"
  [ -n "${WORKER_IP:-}" ] || { echo 0; return; }
  ssh_worker "df -BG $(printf '%q' "$path") 2>/dev/null | awk 'NR==2 {gsub(/G/,\"\"); print \$4}'" 2>/dev/null || echo 0
}

parse_kv_cache_bytes() {
  local i=0
  while [ $i -lt ${#ENGINE_ARGS[@]} ]; do
    case "${ENGINE_ARGS[$i]}" in
      --kv-cache-memory-bytes)
        i=$((i + 1))
        echo "${ENGINE_ARGS[$i]}"
        return 0
        ;;
      --kv-cache-memory-bytes=*)
        echo "${ENGINE_ARGS[$i]#--kv-cache-memory-bytes=}"
        return 0
        ;;
    esac
    i=$((i + 1))
  done
  echo ""
}

parse_max_model_len() {
  local i=0
  while [ $i -lt ${#ENGINE_ARGS[@]} ]; do
    case "${ENGINE_ARGS[$i]}" in
      --max-model-len)
        i=$((i + 1))
        echo "${ENGINE_ARGS[$i]}"
        return 0
        ;;
      --max-model-len=*)
        echo "${ENGINE_ARGS[$i]#--max-model-len=}"
        return 0
        ;;
    esac
    i=$((i + 1))
  done
  echo ""
}

bytes_to_gib() {
  awk -v b="${1:-0}" 'BEGIN { printf "%.2f", b / 1073741824 }'
}

# Best-effort directory size in GiB (du -sb or -sk).
dir_size_gib() {
  local p="$1"
  [ -e "$p" ] || { echo 0; return; }
  if du -sb "$p" >/dev/null 2>&1; then
    awk -v b="$(du -sb "$p" 2>/dev/null | awk '{print $1}')" 'BEGIN{printf "%.2f", b/1073741824}'
  else
    awk -v k="$(du -sk "$p" 2>/dev/null | awk '{print $1}')" 'BEGIN{printf "%.2f", k/1048576}'
  fi
}

estimate_weights_gib() {
  # Disk footprint for pull-weights (full HF/NFS tree).
  if [ -n "${WEIGHTS_GIB}" ]; then
    echo "$WEIGHTS_GIB"
    return
  fi
  local kind sz
  kind=$(model_source_kind)
  if [ "$kind" = nfs ]; then
    sz=$(dir_size_gib "$MODEL")
  else
    sz=$(dir_size_gib "$(hf_hub_path)")
  fi
  if awk -v s="$sz" 'BEGIN{exit !(s>0.1)}'; then
    echo "$sz"
  else
    echo "5"
  fi
}

# Unified-memory weight footprint for check-memory (full model GiB).
# Prefer WEIGHTS_RAM_GIB when disk ≠ resident RAM (quantized / MoE).
estimate_weights_ram_gib() {
  if [ -n "${WEIGHTS_RAM_GIB:-}" ]; then
    echo "$WEIGHTS_RAM_GIB"
    return
  fi
  estimate_weights_gib
}

estimate_kv_gib() {
  if [ -n "${KV_GIB}" ]; then
    echo "$KV_GIB"
    return
  fi
  local b
  b=$(parse_kv_cache_bytes)
  if [ -n "$b" ]; then
    bytes_to_gib "$b"
    return
  fi
  # No fixed KV — modest default; confs should set KV_GIB when known
  if [ -n "${WEIGHTS_GIB}" ] && awk -v w="$WEIGHTS_GIB" 'BEGIN{exit !(w+0 < 10)}'; then
    echo "2"
  else
    echo "6"
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()[:-1] if False else sys.argv[1]))' "$1" 2>/dev/null \
    || printf '"%s"' "${1//\"/\\\"}"
}

# Validate the stable inventory JSON boundary before callers make lifecycle
# decisions from it. Empty, malformed, or structurally incomplete output is an
# observability failure, never an empty-machine state.
inventory_json_is_valid() {
  local payload="${1:-}"
  [ -n "$payload" ] || return 1
  printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
ok = (
    isinstance(d, dict)
    and d.get("schema_version") == 1
    and isinstance(d.get("services"), list)
    and isinstance(d.get("worker"), dict)
    and isinstance(d.get("nodes"), dict)
    and isinstance(d["nodes"].get("head"), dict)
)
raise SystemExit(0 if ok else 1)
' >/dev/null 2>&1
}

# Validate both check-memory's JSON contract and its three documented statuses.
# This prevents crashes, signals, and malformed helper output from becoming an
# implicit PASS.
memory_preflight_json_is_valid() {
  local payload="${1:-}" rc="${2:-}"
  [ -n "$payload" ] || return 1
  case "$rc" in
    0|1|2) ;;
    *) return 1 ;;
  esac
  printf '%s' "$payload" | RC="$rc" python3 -c '
import json, os, sys
try:
    d = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
expected = {"0": "pass", "1": "fail", "2": "warn"}[os.environ["RC"]]
required = ("result", "mode", "footprint_gib", "need_start_gib", "head_available_gib")
ok = (
    isinstance(d, dict)
    and all(k in d for k in required)
    and d.get("result") == expected
    and d.get("mode") in ("cold-start", "already-loaded")
)
raise SystemExit(0 if ok else 1)
' >/dev/null 2>&1
}

pulsar_api_key() {
  printf '%s' "${VLLM_API_KEY:-${API_KEY:-}}"
}

# Fill a caller-owned argv array with the OpenAI-compatible Authorization
# header when API auth is configured. Keeping the header as two argv elements
# avoids shell re-parsing and command injection.
api_auth_curl_args() {
  local destination="${1:?auth argv destination required}"
  local key
  key=$(pulsar_api_key)
  local -n destination_ref="$destination"
  destination_ref=()
  if [ -n "$key" ]; then
    destination_ref=(-H "Authorization: Bearer $key")
  fi
}

# Render diagnostic/dry-run argv without disclosing credentials. Execution
# paths must continue to use the original arrays.
shell_join_q_redacted() {
  local out="" arg shown redact_next=0
  for arg in "$@"; do
    shown="$arg"
    if [ "$redact_next" = 1 ]; then
      shown="<redacted>"
      redact_next=0
    else
      case "$arg" in
        --api-key)
          redact_next=1
          ;;
        HF_TOKEN=*|VLLM_API_KEY=*|API_KEY=*)
          shown="${arg%%=*}=<redacted>"
          ;;
      esac
    fi
    out+="$(printf '%q' "$shown") "
  done
  printf '%s' "${out% }"
}

print_shell_command_redacted() {
  shell_join_q_redacted "$@"
  printf '\n'
}

# Injectable docker/ssh for deterministic tests. Production leaves these default.
PULSAR_DOCKER="${PULSAR_DOCKER:-docker}"
PULSAR_SSH="${PULSAR_SSH:-ssh}"
PULSAR_SSH_CONNECT_TIMEOUT="${PULSAR_SSH_CONNECT_TIMEOUT:-8}"
PULSAR_SSH_OPTS=(
  -o BatchMode=yes
  -o "ConnectTimeout=${PULSAR_SSH_CONNECT_TIMEOUT}"
  -o ConnectionAttempts=1
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=2
)

ssh_worker() {
  [ -n "${WORKER_IP:-}" ] || die "WORKER_IP unset (set in .env for multi-node)"
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$WORKER_IP" "$@"
}

PULSAR_MANAGED_LABEL="io.pulsar.gb10.managed"
PULSAR_CONF_LABEL="io.pulsar.gb10.conf"
PULSAR_RANK_LABEL="io.pulsar.gb10.rank"

# Inspect / list probe codes (not remove codes):
#   0 = present / success (payload on stdout)
#   1 = operational error (SSH/Docker unreachable or failed)
#   3 = absent (healthy daemon, object not found)
# Remove codes: 0 ok, 1 operational error, 2 ownership refusal.

container_name_for() {
  local name="$1" nodes="${2:-1}"
  if [ "$nodes" = "2" ]; then
    echo "vllm-cluster-${name}"
  else
    echo "vllm-${name}"
  fi
}

# Expected rank label for a conf on a placement node (head|worker).
expected_rank_for_nodes() {
  local nodes="${1:?}" placement="${2:-head}"
  if [ "$nodes" = "2" ]; then
    case "$placement" in
      head|0) echo 0 ;;
      worker|1) echo 1 ;;
      *) die "expected_rank_for_nodes: bad placement $placement" ;;
    esac
  else
    echo single
  fi
}

# NODES from models/<conf>.conf without sourcing the full profile (default 1).
profile_nodes_for_conf() {
  local conf="${1:?}"
  local path="$REPO_DIR/models/${conf}.conf"
  [ -f "$path" ] || return 1
  local nodes
  nodes=$(awk -F= '
    /^[[:space:]]*NODES=/ {
      v=$0
      sub(/^[^=]*=/, "", v)
      gsub(/[[:space:]"'\'']/, "", v)
      print v
      found=1
      exit
    }
    END { if (!found) print "1" }
  ' "$path")
  [ -n "$nodes" ] || nodes=1
  printf '%s' "$nodes"
}

# True when conf exists and rank is valid for placement on head|worker.
# head: NODES=1 → single; NODES=2 → 0
# worker: NODES=2 → 1 only (never single/0; never any rank for NODES=1)
placement_rank_allowed() {
  local conf="${1:?}" rank="${2:?}" placement="${3:?}"
  local nodes
  nodes=$(profile_nodes_for_conf "$conf") || return 1
  case "$placement" in
    head)
      if [ "$nodes" = "2" ]; then
        [ "$rank" = "0" ]
      else
        [ "$rank" = "single" ]
      fi
      ;;
    worker)
      [ "$nodes" = "2" ] && [ "$rank" = "1" ]
      ;;
    *) return 1 ;;
  esac
}

# docker-inspect --format payload for lifecycle ownership checks (id + labels only).
container_ownership_inspect_format() {
  printf '%s' \
    '{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}}}'
}

# Parse ownership inspect JSON → tab fields: id, name, managed, conf, rank
# name has leading '/' stripped. Empty fields when labels missing.
container_ownership_fields() {
  local metadata="${1:?}"
  printf '%s' "$metadata" | python3 -c '
import json, sys
meta = json.load(sys.stdin)
labels = meta.get("labels") or {}
if labels is None:
    labels = {}
cid = str(meta.get("id") or "")
name = str(meta.get("name") or "").lstrip("/")
managed = str(labels.get(sys.argv[1], "") or "")
conf = str(labels.get(sys.argv[2], "") or "")
rank = str(labels.get(sys.argv[3], "") or "")
print("\t".join([cid, name, managed, conf, rank]))
' "$PULSAR_MANAGED_LABEL" "$PULSAR_CONF_LABEL" "$PULSAR_RANK_LABEL"
}

# Exactly managed="true" (launchers/inventory); never 1/yes/TRUE.
container_managed_label_is_true() {
  local managed="${1:-}"
  [ "$managed" = "true" ]
}

# True when labels prove stack management for want_conf/want_rank.
# want_conf / want_rank empty means "not constrained by caller" for that field
# (still requires non-empty conf/rank and managed=true). Used by named stops.
# Lifecycle removal never accepts unlabeled/legacy argv fallbacks.
container_ownership_is_proven() {
  local metadata="${1:?}" want_conf="${2:-}" want_rank="${3:-}"
  local id name managed conf rank
  IFS=$'\t' read -r id name managed conf rank < <(container_ownership_fields "$metadata")
  [ -n "$id" ] || return 1
  container_managed_label_is_true "$managed" || return 1
  [ -n "$conf" ] || return 1
  [ -n "$rank" ] || return 1
  if [ -n "$want_conf" ] && [ "$conf" != "$want_conf" ]; then
    return 1
  fi
  if [ -n "$want_rank" ] && [ "$rank" != "$want_rank" ]; then
    return 1
  fi
  return 0
}

# --all candidate: managed=true, conf in models/*.conf, rank valid for placement.
container_all_candidate_is_safe() {
  local metadata="${1:?}" placement="${2:?}"
  local id name managed conf rank
  IFS=$'\t' read -r id name managed conf rank < <(container_ownership_fields "$metadata")
  [ -n "$id" ] || return 1
  container_managed_label_is_true "$managed" || return 1
  [ -n "$conf" ] || return 1
  [ -n "$rank" ] || return 1
  [ -f "$REPO_DIR/models/${conf}.conf" ] || return 1
  placement_rank_allowed "$conf" "$rank" "$placement"
}

# Describe why ownership failed (for refuse messages). Never claims safe to remove.
container_ownership_refuse_reason() {
  local metadata="${1:?}" want_conf="${2:-}" want_rank="${3:-}"
  local id name managed conf rank
  IFS=$'\t' read -r id name managed conf rank < <(container_ownership_fields "$metadata")
  if [ -z "$id" ]; then
    echo "missing container id"
    return
  fi
  if ! container_managed_label_is_true "$managed"; then
    if [ -z "$managed" ] && [ -z "$conf" ] && [ -z "$rank" ]; then
      echo "unlabeled/legacy container (no ${PULSAR_MANAGED_LABEL})"
    else
      echo "not stack-managed (managed='${managed}'; require exactly 'true')"
    fi
    return
  fi
  if [ -z "$conf" ] || [ -z "$rank" ]; then
    echo "managed label set but conf/rank incomplete (conf='${conf}' rank='${rank}')"
    return
  fi
  if [ -n "$want_conf" ] && [ "$conf" != "$want_conf" ]; then
    echo "conf label mismatch (have='${conf}' want='${want_conf}')"
    return
  fi
  if [ -n "$want_rank" ] && [ "$rank" != "$want_rank" ]; then
    echo "rank label mismatch (have='${rank}' want='${want_rank}')"
    return
  fi
  echo "ownership not proven"
}

container_all_refuse_reason() {
  local metadata="${1:?}" placement="${2:?}"
  local id name managed conf rank nodes
  IFS=$'\t' read -r id name managed conf rank < <(container_ownership_fields "$metadata")
  if ! container_managed_label_is_true "$managed"; then
    container_ownership_refuse_reason "$metadata" "" ""
    return
  fi
  if [ -z "$conf" ] || [ -z "$rank" ]; then
    echo "managed label set but conf/rank incomplete (conf='${conf}' rank='${rank}')"
    return
  fi
  if [ ! -f "$REPO_DIR/models/${conf}.conf" ]; then
    echo "unknown conf '${conf}' (no models/${conf}.conf)"
    return
  fi
  if ! nodes=$(profile_nodes_for_conf "$conf"); then
    echo "cannot read NODES for conf '${conf}'"
    return
  fi
  echo "placement mismatch on ${placement}: conf=${conf} nodes=${nodes} rank=${rank}"
}

# Validate docker run -d stdout: exactly one 64-hex id (optional trailing newline).
# Prints normalized id on success; returns 1 on garbage/extra stdout.
parse_docker_run_container_id() {
  local raw="${1-}"
  local id
  id=$(printf '%s' "$raw")
  id="${id%"${id##*[![:space:]]}"}"
  id="${id#"${id%%[![:space:]]*}"}"
  case "$id" in
    *$'\n'*|*$'\r'*|*' '*) return 1 ;;
  esac
  [[ "$id" =~ ^[0-9a-fA-F]{64}$ ]] || return 1
  printf '%s' "$id"
}

# Merge lifecycle remove exit codes. Severity: 1 (operational) > 2 (refuse) > 0.
# Used so a later refusal never masks an earlier operational error.
lifecycle_merge_rc() {
  local cur="${1:-0}" new="${2:-0}"
  if [ "$new" -eq 0 ]; then
    printf '%s' "$cur"
    return 0
  fi
  if [ "$cur" -eq 1 ] || [ "$new" -eq 1 ]; then
    printf '%s' 1
    return 0
  fi
  if [ "$cur" -eq 2 ] || [ "$new" -eq 2 ]; then
    printf '%s' 2
    return 0
  fi
  printf '%s' "$new"
}

# When docker run appears to succeed but stdout is not a valid container id,
# refuse arbitrary cleanup. Optionally read-only inspect exact name; if labels
# prove ownership for conf/rank, mention the short id — never remove it.
# Prints guidance: scripts/inventory.sh then scripts/down.sh <conf>.
# Usage: report_untracked_launch_container <role> <conf> <rank> <cname> [worker_host]
# role is head|worker (worker requires host).
report_untracked_launch_container() {
  local role="${1:?}" conf="${2:?}" rank="${3:?}" cname="${4:?}" host="${5:-}"
  local meta rc=0 id short where

  case "$role" in
    head) where="local head" ;;
    worker) where="worker ${host:-?}" ;;
    *) where="$role" ;;
  esac

  warn "docker run on ${where} returned invalid container id output — refusing arbitrary cleanup"
  warn "a managed container for conf=${conf} may have been created and was deliberately left untouched"
  warn "safe remediation: scripts/inventory.sh   then   scripts/down.sh ${conf}"

  meta=""
  if [ "$role" = "worker" ]; then
    [ -n "$host" ] || return 0
    meta=$(container_ownership_inspect_remote "$host" "$cname") || rc=$?
  else
    meta=$(container_ownership_inspect_local "$cname") || rc=$?
  fi
  if [ "$rc" -eq 0 ] && container_ownership_is_proven "$meta" "$conf" "$rank"; then
    IFS=$'\t' read -r id _ < <(container_ownership_fields "$meta")
    short="${id:0:12}"
    warn "read-only inspect: exact name ${cname} on ${where} has proven labels id=${short} (not removed)"
  fi
  return 0
}

# Local inspect: 0 present (JSON stdout), 3 absent, 1 docker error.
container_ownership_inspect_local() {
  local ref="${1:?}"
  local format meta
  format=$(container_ownership_inspect_format)
  if meta=$("$PULSAR_DOCKER" inspect --format "$format" "$ref" 2>/dev/null); then
    [ -n "$meta" ] || return 1
    printf '%s' "$meta"
    return 0
  fi
  if ! "$PULSAR_DOCKER" info >/dev/null 2>&1; then
    return 1
  fi
  return 3
}

# Remote inspect with explicit error-vs-absent. Never masks SSH/Docker failure
# as absence. 0 present, 3 absent, 1 unreachable/error.
container_ownership_inspect_remote() {
  local host="${1:?}" ref="${2:?}"
  local format remote_cmd out status meta
  format=$(container_ownership_inspect_format)
  # Remote protocol (one status line, optional JSON body):
  #   PRESENT\n<json> | ABSENT | DOCKER_ERROR
  remote_cmd="if ! docker info >/dev/null 2>&1; then printf '%s\\n' DOCKER_ERROR; exit 0; fi; "
  remote_cmd+="if meta=\$(docker inspect --format $(printf '%q' "$format") $(printf '%q' "$ref") 2>/dev/null); then "
  remote_cmd+="printf 'PRESENT\\n%s\\n' \"\$meta\"; exit 0; fi; "
  remote_cmd+="printf '%s\\n' ABSENT; exit 0"

  if ! out=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" "$remote_cmd" 2>/dev/null); then
    return 1
  fi
  status=$(printf '%s\n' "$out" | head -n1 | tr -d '\r')
  case "$status" in
    PRESENT)
      meta=$(printf '%s\n' "$out" | tail -n +2)
      # Drop a single trailing newline from the body for JSON parse stability.
      meta="${meta%"${meta##*[![:space:]]}"}"
      [ -n "$meta" ] || return 1
      printf '%s' "$meta"
      return 0
      ;;
    ABSENT)
      return 3
      ;;
    DOCKER_ERROR|*)
      return 1
      ;;
  esac
}

# Verify immutable id is gone. 0 absent, 1 still present or probe error.
container_id_absent_local() {
  local id="${1:?}" rc=0
  container_ownership_inspect_local "$id" >/dev/null 2>&1 && return 1
  rc=$?
  [ "$rc" -eq 3 ]
}

container_id_absent_remote() {
  local host="${1:?}" id="${2:?}" rc=0
  container_ownership_inspect_remote "$host" "$id" >/dev/null 2>&1 && return 1
  rc=$?
  [ "$rc" -eq 3 ]
}

# Remove one local container by exact name only if labels prove ownership.
# Exit 0: removed or absent. Exit 2: present but refused. Exit 1: operational error.
remove_stack_owned_container_local() {
  local name="${1:?}" want_conf="${2:-}" want_rank="${3:-}"
  local meta id name_have managed conf rank meta2 id2 reason short rc

  rc=0
  meta=$(container_ownership_inspect_local "$name") || rc=$?
  if [ "$rc" -eq 3 ]; then
    log "no container named $name"
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    warn "local docker error while inspecting $name"
    return 1
  fi

  IFS=$'\t' read -r id name_have managed conf rank < <(container_ownership_fields "$meta")
  if ! container_ownership_is_proven "$meta" "$want_conf" "$want_rank"; then
    reason=$(container_ownership_refuse_reason "$meta" "$want_conf" "$want_rank")
    warn "refusing to remove $name: $reason"
    return 2
  fi

  short="${id:0:12}"
  rc=0
  meta2=$(container_ownership_inspect_local "$id") || rc=$?
  if [ "$rc" -eq 3 ]; then
    log "container $name ($short) already gone before remove"
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    warn "local docker error during revalidation of $name id=$short"
    return 1
  fi
  IFS=$'\t' read -r id2 _ < <(container_ownership_fields "$meta2")
  if [ "$id2" != "$id" ] || ! container_ownership_is_proven "$meta2" "$want_conf" "$want_rank"; then
    reason=$(container_ownership_refuse_reason "$meta2" "$want_conf" "$want_rank")
    warn "refusing to remove $name: ownership revalidation failed ($reason)"
    return 2
  fi

  log "removing $name id=$short (managed conf=${conf} rank=${rank})"
  if ! "$PULSAR_DOCKER" rm -f "$id" >/dev/null; then
    warn "docker rm -f failed for $name id=$short"
    return 1
  fi
  if ! container_id_absent_local "$id"; then
    warn "container id=$short still present or unverifiable after docker rm -f"
    return 1
  fi
  return 0
}

# Same as local, via SSH to host. Remote docker binary is always "docker".
remove_stack_owned_container_remote() {
  local host="${1:?}" name="${2:?}" want_conf="${3:-}" want_rank="${4:-}"
  local meta id name_have managed conf rank meta2 id2 reason short remote_rm rc

  rc=0
  meta=$(container_ownership_inspect_remote "$host" "$name") || rc=$?
  if [ "$rc" -eq 3 ]; then
    log "no container named $name on $host"
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    warn "worker unreachable or docker error inspecting $name on $host"
    return 1
  fi

  IFS=$'\t' read -r id name_have managed conf rank < <(container_ownership_fields "$meta")
  if ! container_ownership_is_proven "$meta" "$want_conf" "$want_rank"; then
    reason=$(container_ownership_refuse_reason "$meta" "$want_conf" "$want_rank")
    warn "refusing to remove $name on $host: $reason"
    return 2
  fi

  short="${id:0:12}"
  rc=0
  meta2=$(container_ownership_inspect_remote "$host" "$id") || rc=$?
  if [ "$rc" -eq 3 ]; then
    log "container $name ($short) on $host already gone before remove"
    return 0
  fi
  if [ "$rc" -ne 0 ]; then
    warn "worker unreachable during revalidation of $name id=$short on $host"
    return 1
  fi
  IFS=$'\t' read -r id2 _ < <(container_ownership_fields "$meta2")
  if [ "$id2" != "$id" ] || ! container_ownership_is_proven "$meta2" "$want_conf" "$want_rank"; then
    reason=$(container_ownership_refuse_reason "$meta2" "$want_conf" "$want_rank")
    warn "refusing to remove $name on $host: ownership revalidation failed ($reason)"
    return 2
  fi

  log "removing $name on $host id=$short (managed conf=${conf} rank=${rank})"
  remote_rm="docker rm -f $(printf '%q' "$id") >/dev/null"
  if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" "$remote_rm"; then
    warn "docker rm -f failed for $name id=$short on $host"
    return 1
  fi
  if ! container_id_absent_remote "$host" "$id"; then
    warn "container id=$short still present or unverifiable on $host after docker rm -f"
    return 1
  fi
  return 0
}

# Best-effort remove by immutable ID only (current-launch cleanup). Never by name.
# Only accepts a validated 64-hex id — never arbitrary docker run stdout.
remove_container_id_local() {
  local id="${1:-}" normalized
  [ -n "$id" ] || return 0
  if ! normalized=$(parse_docker_run_container_id "$id"); then
    warn "refusing id cleanup: invalid container id"
    return 0
  fi
  "$PULSAR_DOCKER" rm -f "$normalized" >/dev/null 2>&1 || true
}

remove_container_id_remote() {
  local host="${1:-}" id="${2:-}" normalized
  [ -n "$host" ] && [ -n "$id" ] || return 0
  if ! normalized=$(parse_docker_run_container_id "$id"); then
    warn "refusing remote id cleanup: invalid container id"
    return 0
  fi
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" \
    "docker rm -f $(printf '%q' "$normalized") >/dev/null 2>&1 || true" 2>/dev/null || true
}

# List IDs of containers with managed=true.
# 0 success (stdout may be empty); 1 operational error — never mask as empty.
list_managed_container_ids_local() {
  if ! "$PULSAR_DOCKER" info >/dev/null 2>&1; then
    return 1
  fi
  "$PULSAR_DOCKER" ps -aq --filter "label=${PULSAR_MANAGED_LABEL}=true" 2>/dev/null || return 1
  return 0
}

list_managed_container_ids_remote() {
  local host="${1:?}" out status
  local remote_cmd filter_q
  filter_q=$(printf '%q' "label=${PULSAR_MANAGED_LABEL}=true")
  # Protocol status line (never print OK before a successful docker ps):
  #   OK\n[ids...] | DOCKER_INFO_ERROR | DOCKER_PS_ERROR
  # Both error statuses ⇒ operational failure (not empty success).
  remote_cmd="if ! docker info >/dev/null 2>&1; then printf '%s\\n' DOCKER_INFO_ERROR; exit 0; fi; "
  remote_cmd+="if ! ids=\$(docker ps -aq --filter ${filter_q}); then printf '%s\\n' DOCKER_PS_ERROR; exit 0; fi; "
  remote_cmd+="printf '%s\\n' OK; "
  remote_cmd+="if [ -n \"\$ids\" ]; then printf '%s\\n' \"\$ids\"; fi"

  if ! out=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" "$remote_cmd" 2>/dev/null); then
    return 1
  fi
  status=$(printf '%s\n' "$out" | head -n1 | tr -d '\r')
  case "$status" in
    OK)
      printf '%s\n' "$out" | tail -n +2
      return 0
      ;;
    DOCKER_INFO_ERROR|DOCKER_PS_ERROR)
      return 1
      ;;
    *)
      return 1
      ;;
  esac
}

# Remove local --all candidates that are safe for head placement.
# Exit 0 all ok; 1 operational error; 2 any refused candidate left intact.
# Operational error (1) is sticky: later refusals must not overwrite it.
remove_all_stack_managed_local() {
  local ids id meta conf rank reason rc=0 short probe
  ids=$(list_managed_container_ids_local) || {
    warn "local docker error listing managed containers"
    return 1
  }
  if [ -z "${ids//[$' \t\r\n']/}" ]; then
    log "no stack-managed containers on local docker"
    return 0
  fi
  for id in $ids; do
    [ -n "$id" ] || continue
    probe=0
    meta=$(container_ownership_inspect_local "$id") || probe=$?
    if [ "$probe" -eq 3 ]; then
      continue
    fi
    if [ "$probe" -ne 0 ]; then
      warn "local docker error inspecting managed id=${id:0:12}"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    IFS=$'\t' read -r _ _ _ conf rank < <(container_ownership_fields "$meta")
    if ! container_all_candidate_is_safe "$meta" "head"; then
      reason=$(container_all_refuse_reason "$meta" "head")
      warn "refusing managed candidate id=${id:0:12} on head: $reason"
      rc=$(lifecycle_merge_rc "$rc" 2)
      continue
    fi
    short="${id:0:12}"
    probe=0
    meta=$(container_ownership_inspect_local "$id") || probe=$?
    if [ "$probe" -eq 3 ]; then
      continue
    fi
    if [ "$probe" -ne 0 ]; then
      warn "local docker error revalidating id=$short"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    if ! container_all_candidate_is_safe "$meta" "head"; then
      reason=$(container_all_refuse_reason "$meta" "head")
      warn "refusing id=$short on head after revalidation: $reason"
      rc=$(lifecycle_merge_rc "$rc" 2)
      continue
    fi
    log "removing stack-managed id=$short conf=$conf rank=$rank (head)"
    if ! "$PULSAR_DOCKER" rm -f "$id" >/dev/null; then
      warn "docker rm -f failed for id=$short"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    if ! container_id_absent_local "$id"; then
      warn "container id=$short still present or unverifiable after docker rm -f"
      rc=$(lifecycle_merge_rc "$rc" 1)
    fi
  done
  return "$rc"
}

remove_all_stack_managed_remote() {
  local host="${1:?}"
  local ids id meta conf rank reason rc=0 short remote_rm probe
  ids=$(list_managed_container_ids_remote "$host") || {
    warn "worker unreachable or docker error listing managed containers on $host"
    return 1
  }
  if [ -z "${ids//[$' \t\r\n']/}" ]; then
    log "no stack-managed containers on $host"
    return 0
  fi
  for id in $ids; do
    [ -n "$id" ] || continue
    probe=0
    meta=$(container_ownership_inspect_remote "$host" "$id") || probe=$?
    if [ "$probe" -eq 3 ]; then
      continue
    fi
    if [ "$probe" -ne 0 ]; then
      warn "worker error inspecting managed id=${id:0:12} on $host"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    IFS=$'\t' read -r _ _ _ conf rank < <(container_ownership_fields "$meta")
    if ! container_all_candidate_is_safe "$meta" "worker"; then
      reason=$(container_all_refuse_reason "$meta" "worker")
      warn "refusing managed candidate id=${id:0:12} on worker: $reason"
      rc=$(lifecycle_merge_rc "$rc" 2)
      continue
    fi
    short="${id:0:12}"
    probe=0
    meta=$(container_ownership_inspect_remote "$host" "$id") || probe=$?
    if [ "$probe" -eq 3 ]; then
      continue
    fi
    if [ "$probe" -ne 0 ]; then
      warn "worker error revalidating id=$short on $host"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    if ! container_all_candidate_is_safe "$meta" "worker"; then
      reason=$(container_all_refuse_reason "$meta" "worker")
      warn "refusing id=$short on worker after revalidation: $reason"
      rc=$(lifecycle_merge_rc "$rc" 2)
      continue
    fi
    log "removing stack-managed on $host id=$short conf=$conf rank=$rank (worker)"
    remote_rm="docker rm -f $(printf '%q' "$id") >/dev/null"
    if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" "$remote_rm"; then
      warn "docker rm -f failed for id=$short on $host"
      rc=$(lifecycle_merge_rc "$rc" 1)
      continue
    fi
    if ! container_id_absent_remote "$host" "$id"; then
      warn "container id=$short still present or unverifiable on $host after docker rm -f"
      rc=$(lifecycle_merge_rc "$rc" 1)
    fi
  done
  return "$rc"
}

# Two-node stale/replacement gate: prove ownership on every existing rank, then
# remove proven IDs. Worker probe errors are operational failures (never treated
# as absence). Refuse entirely (no partial remove) if any existing rank is
# unowned/mismatched. After each rm, verify the immutable ID is absent.
# Exit 0 removed/absent OK; 2 refuse; 1 error.
remove_stack_owned_cluster_pair() {
  local conf="${1:?}" cname="${2:?}" worker_ip="${3:?}"
  local head_meta worker_meta head_id="" worker_id="" reason
  local head_rc=0 worker_rc=0 refuse=0

  # Probe worker first so SSH/Docker failure never looks like "worker absent"
  # and never allows a partial head-only removal.
  worker_meta=$(container_ownership_inspect_remote "$worker_ip" "$cname") || worker_rc=$?
  if [ "$worker_rc" -eq 1 ]; then
    warn "worker unreachable or docker error inspecting $cname on $worker_ip — not removing any rank"
    return 1
  fi

  head_meta=$(container_ownership_inspect_local "$cname") || head_rc=$?
  if [ "$head_rc" -eq 1 ]; then
    warn "local docker error inspecting $cname — not removing any rank"
    return 1
  fi

  if [ "$head_rc" -eq 0 ]; then
    if container_ownership_is_proven "$head_meta" "$conf" "0"; then
      IFS=$'\t' read -r head_id _ < <(container_ownership_fields "$head_meta")
    else
      reason=$(container_ownership_refuse_reason "$head_meta" "$conf" "0")
      warn "head rank ownership not proven for $cname: $reason"
      refuse=1
    fi
  fi

  if [ "$worker_rc" -eq 0 ]; then
    if container_ownership_is_proven "$worker_meta" "$conf" "1"; then
      IFS=$'\t' read -r worker_id _ < <(container_ownership_fields "$worker_meta")
    else
      reason=$(container_ownership_refuse_reason "$worker_meta" "$conf" "1")
      warn "worker rank ownership not proven for $cname on $worker_ip: $reason"
      refuse=1
    fi
  fi

  if [ "$refuse" = 1 ]; then
    warn "refusing cluster replacement for conf=$conf: prove ownership on every existing rank (no partial remove)"
    return 2
  fi

  if [ "$head_rc" -eq 3 ] && [ "$worker_rc" -eq 3 ]; then
    log "no existing cluster containers named $cname"
    return 0
  fi

  # Revalidate both IDs before any mutation. Failure ⇒ refuse (no partial).
  if [ -n "$head_id" ]; then
    head_rc=0
    head_meta=$(container_ownership_inspect_local "$head_id") || head_rc=$?
    if [ "$head_rc" -eq 1 ]; then
      warn "local docker error revalidating head id for $cname"
      return 1
    fi
    if [ "$head_rc" -ne 0 ] || ! container_ownership_is_proven "$head_meta" "$conf" "0"; then
      warn "refusing: head id revalidation failed for $cname"
      return 2
    fi
  fi
  if [ -n "$worker_id" ]; then
    worker_rc=0
    worker_meta=$(container_ownership_inspect_remote "$worker_ip" "$worker_id") || worker_rc=$?
    if [ "$worker_rc" -eq 1 ]; then
      warn "worker error revalidating worker id for $cname — not removing any rank"
      return 1
    fi
    if [ "$worker_rc" -ne 0 ] || ! container_ownership_is_proven "$worker_meta" "$conf" "1"; then
      warn "refusing: worker id revalidation failed for $cname"
      return 2
    fi
  fi

  if [ -n "$worker_id" ]; then
    log "removing worker $cname id=${worker_id:0:12}"
    if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$worker_ip" \
      "docker rm -f $(printf '%q' "$worker_id") >/dev/null"; then
      warn "failed to remove worker id=${worker_id:0:12}"
      return 1
    fi
    if ! container_id_absent_remote "$worker_ip" "$worker_id"; then
      warn "worker id=${worker_id:0:12} still present or unverifiable after rm — not removing head"
      return 1
    fi
  fi
  if [ -n "$head_id" ]; then
    log "removing head $cname id=${head_id:0:12}"
    if ! "$PULSAR_DOCKER" rm -f "$head_id" >/dev/null; then
      warn "failed to remove head id=${head_id:0:12}"
      return 1
    fi
    if ! container_id_absent_local "$head_id"; then
      warn "head id=${head_id:0:12} still present or unverifiable after rm"
      return 1
    fi
  fi
  return 0
}

# Append VLLM_EXTRA_ARGS onto a named bash array using shlex (handles
# spaces/quotes). Bare word-split of VLLM_EXTRA_ARGS is intentionally avoided.
# Usage: append_vllm_extra_args DEST_ARRAY_NAME
#   VLLM_EXTRA_ARGS='--foo "bar baz"' append_vllm_extra_args CMD
append_vllm_extra_args() {
  local dest="${1:?append_vllm_extra_args: array name required}"
  [ -n "${VLLM_EXTRA_ARGS:-}" ] || return 0
  local line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    eval "$dest+=(\"\$line\")"
  done < <(VLLM_EXTRA_ARGS="$VLLM_EXTRA_ARGS" python3 - <<'PY'
import os, shlex
for a in shlex.split(os.environ.get("VLLM_EXTRA_ARGS", "")):
    print(a)
PY
)
}

# Exact container name match (Docker --filter name= is substring — unsafe for
# conf prefixes like deepseek-v4-flash vs deepseek-v4-flash-0422).
# Usage: echo "$names_one_per_line" | filter_exact_container_name "$want"
filter_exact_container_name() {
  local want="$1"
  local n
  while IFS= read -r n; do
    [ -z "$n" ] && continue
    if [ "$n" = "$want" ]; then
      printf '%s\n' "$n"
    fi
  done
}

# True if a running container has this exact name (local docker).
container_running_exact() {
  local want="$1"
  "$PULSAR_DOCKER" ps --format '{{.Names}}' 2>/dev/null \
    | filter_exact_container_name "$want" | grep -q .
}
container_running_exact_remote() {
  local host="${1:?}" want="${2:?}"
  "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" "$host" \
    "docker ps --format '{{.Names}}'" 2>/dev/null \
    | filter_exact_container_name "$want" | grep -q .
}


# Validate targeted docker-inspect metadata without exposing container env.
# Labeled containers are authoritative. Unlabeled containers are accepted only
# as a transition path when their exact vLLM argv matches the selected profile.
container_metadata_matches_profile() {
  local metadata="$1" conf="$2" model="$3" served="$4" rank="${5:-}"
  printf '%s' "$metadata" | python3 -c '
import json
import sys

conf, model, served, rank, managed_key, conf_key, rank_key = sys.argv[1:]
meta = json.load(sys.stdin)
if not meta.get("running"):
    raise SystemExit(1)

labels = meta.get("labels") or {}
if str(labels.get(managed_key, "")).lower() == "true":
    ok = labels.get(conf_key) == conf
    if rank:
        ok = ok and labels.get(rank_key) == rank
    raise SystemExit(0 if ok else 1)

cmd = meta.get("cmd") or []
def value(flag):
    try:
        return cmd[cmd.index(flag) + 1]
    except (ValueError, IndexError):
        return None

ok = value("--model") == model and value("--served-model-name") == served
if rank:
    ok = ok and value("--node-rank") == rank
raise SystemExit(0 if ok else 1)
' "$conf" "$model" "$served" "$rank" \
    "$PULSAR_MANAGED_LABEL" "$PULSAR_CONF_LABEL" "$PULSAR_RANK_LABEL"
}

container_profile_owned_local() {
  local name="$1" conf="$2" model="$3" served="$4" rank="${5:-}"
  local format metadata
  format='{"running":{{json .State.Running}},"labels":{{json .Config.Labels}},"cmd":{{json .Config.Cmd}}}'
  metadata=$(docker inspect --format "$format" "$name" 2>/dev/null) || return 1
  container_metadata_matches_profile "$metadata" "$conf" "$model" "$served" "$rank"
}

container_profile_owned_worker() {
  local name="$1" conf="$2" model="$3" served="$4" rank="${5:-}"
  local format metadata remote_cmd
  format='{"running":{{json .State.Running}},"labels":{{json .Config.Labels}},"cmd":{{json .Config.Cmd}}}'
  remote_cmd="docker inspect --format $(printf '%q' "$format") $(printf '%q' "$name")"
  metadata=$(ssh_worker "$remote_cmd" 2>/dev/null) || return 1
  container_metadata_matches_profile "$metadata" "$conf" "$model" "$served" "$rank"
}

# True only when the selected profile's exact running service is owned by this
# stack. Two-node ownership requires matching head and worker ranks.
profile_service_is_stack_owned() {
  local conf="$1" cname
  cname=$(container_name_for "$conf" "$NODES")
  if [ "$NODES" = "2" ]; then
    container_profile_owned_local "$cname" "$conf" "$MODEL" "$SERVED_NAME" 0 \
      && container_profile_owned_worker "$cname" "$conf" "$MODEL" "$SERVED_NAME" 1
  else
    container_profile_owned_local "$cname" "$conf" "$MODEL" "$SERVED_NAME"
  fi
}

# Strict loaded-state proof for memory exemptions. Unlike the transition
# classifier above, this accepts labels only; argv resemblance is insufficient.
profile_service_is_proven_running() {
  local conf="$1" cname head_meta worker_meta
  local head_rc=0 worker_rc=0
  cname=$(container_name_for "$conf" "$NODES")

  container_running_exact "$cname" || return 1
  head_meta=$(container_ownership_inspect_local "$cname") || head_rc=$?
  [ "$head_rc" -eq 0 ] || return 1
  if [ "$NODES" = "2" ]; then
    container_ownership_is_proven "$head_meta" "$conf" "0" || return 1
    [ -n "${WORKER_IP:-}" ] || return 1
    container_running_exact_remote "$WORKER_IP" "$cname" || return 1
    worker_meta=$(container_ownership_inspect_remote "$WORKER_IP" "$cname") \
      || worker_rc=$?
    [ "$worker_rc" -eq 0 ] || return 1
    container_ownership_is_proven "$worker_meta" "$conf" "1"
  else
    container_ownership_is_proven "$head_meta" "$conf" "single"
  fi
}

container_exists_exact() {
  local want="$1"
  docker ps -a --format '{{.Names}}' 2>/dev/null | filter_exact_container_name "$want" | grep -q .
}
