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
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" \
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

ssh_worker() {
  [ -n "${WORKER_IP:-}" ] || die "WORKER_IP unset (set in .env for multi-node)"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$WORKER_IP" "$@"
}

PULSAR_MANAGED_LABEL="io.pulsar.gb10.managed"
PULSAR_CONF_LABEL="io.pulsar.gb10.conf"
PULSAR_RANK_LABEL="io.pulsar.gb10.rank"

container_name_for() {
  local name="$1" nodes="${2:-1}"
  if [ "$nodes" = "2" ]; then
    echo "vllm-cluster-${name}"
  else
    echo "vllm-${name}"
  fi
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
  docker ps --format '{{.Names}}' 2>/dev/null | filter_exact_container_name "$want" | grep -q .
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

container_exists_exact() {
  local want="$1"
  docker ps -a --format '{{.Names}}' 2>/dev/null | filter_exact_container_name "$want" | grep -q .
}
