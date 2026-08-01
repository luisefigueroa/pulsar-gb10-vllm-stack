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
  WEIGHTS_GIB="" KV_GIB="" OVERHEAD_GIB="" MEM_MIN_FREE_GIB=""
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
    # Unknown — conservative placeholder for canaries
    echo "5"
  fi
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

container_name_for() {
  local name="$1" nodes="${2:-1}"
  if [ "$nodes" = "2" ]; then
    echo "vllm-cluster-${name}"
  else
    echo "vllm-${name}"
  fi
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

container_exists_exact() {
  local want="$1"
  docker ps -a --format '{{.Names}}' 2>/dev/null | filter_exact_container_name "$want" | grep -q .
}
