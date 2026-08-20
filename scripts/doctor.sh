#!/usr/bin/env bash
# Serving readiness for this node plus every other confirmed cluster node.
#   scripts/doctor.sh [--json]
# exit 0 when no blocking issue is found
set -euo pipefail
SCRIPT_NAME=doctor
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0
[ "${1:-}" = "--json" ] && JSON=1

FAIL=0
WARN=0
# Parallel arrays for JSON: level|id|message
CHECKS=()

record() {
  local level="$1" id="$2" msg="$3"
  CHECKS+=("${level}|${id}|${msg}")
  case "$level" in
    ok)
      [ "$JSON" = 1 ] || print_hanging "  ok   " "$msg"
      ;;
    warn)
      WARN=1
      [ "$JSON" = 1 ] || print_hanging "  warn " "$msg"
      ;;
    fail)
      FAIL=1
      [ "$JSON" = 1 ] || print_hanging "  FAIL " "$msg"
      ;;
  esac
}

doctor_ready_line() {
  local message="$1"
  local use_color=1 colors green reset
  [ "${GUM:-1}" != 0 ] || use_color=0
  [ -z "${NO_COLOR:-}" ] || use_color=0
  case "${PULSAR_COLOR:-}" in
    never|0|no|off|false) use_color=0 ;;
  esac
  case "${TERM:-}" in
    dumb|"") use_color=0 ;;
  esac
  [ -t 1 ] || use_color=0
  if [ "$use_color" = 1 ] && command -v tput >/dev/null 2>&1; then
    colors=$(tput colors 2>/dev/null || true)
    if [[ "$colors" =~ ^[0-9]+$ ]] && [ "$colors" -ge 8 ]; then
      if green=$(tput setaf 2 2>/dev/null) && reset=$(tput sgr0 2>/dev/null); then
        printf '[doctor] %sREADY%s — %s\n' "$green" "$reset" "$message"
        return 0
      fi
    fi
  fi
  printf '[doctor] READY — %s\n' "$message"
}

[ "$JSON" = 1 ] || echo "[doctor] this node"

arch=$(uname -m)
case "$arch" in
  aarch64|arm64) record ok arch "arch=$arch" ;;
  *) record warn arch "arch=$arch (validated stack expects aarch64 GB10)" ;;
esac

if [ ! -r /proc/meminfo ]; then
  record warn host "not a Linux serve host (/proc/meminfo missing) — doctor is informational here"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//')
  if [ "$gpu" = "NVIDIA GB10" ]; then
    record ok gpu "GPU $gpu"
  else
    record fail gpu "GPU '$gpu' (want NVIDIA GB10)"
  fi
else
  record fail gpu "nvidia-smi missing"
fi

if command -v docker >/dev/null 2>&1; then
  if docker_info=$(docker info 2>/dev/null); then
    record ok docker "docker present; daemon reachable"
    runtimes=$(docker info -f '{{range $k,$v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null || true)
    if echo " $runtimes " | grep -Eq '[[:space:]]nvidia[[:space:]]'; then
      record ok docker_nvidia "docker nvidia runtime registered (runtimes: $runtimes)"
    elif printf '%s' "$docker_info" | grep -q 'nvidia.com/gpu'; then
      record ok docker_nvidia "docker nvidia CDI devices present"
    elif command -v nvidia-container-runtime >/dev/null 2>&1 \
      || command -v nvidia-container-cli >/dev/null 2>&1; then
      record warn docker_nvidia "nvidia container tools installed but runtime not listed in docker info yet — try: sudo systemctl restart docker"
    else
      record fail docker_nvidia "docker nvidia runtime missing (expected Runtimes includes nvidia)"
    fi
  else
    record fail docker "docker present but daemon unavailable (start/fix Docker, then retry)"
  fi
else
  record fail docker "docker missing"
fi

port="${PORT:-8000}"
# Identify port owner: published ports, then stack-managed host-network services
# (labels + /v1/models). Unknown ownership stays a blocking-style warn (read-only).
port_owner_msg=""
if command -v ss >/dev/null 2>&1; then
  port_listening=0
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then
    port_listening=1
  fi
elif command -v lsof >/dev/null 2>&1; then
  port_listening=0
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    port_listening=1
  fi
else
  port_listening=-1
fi

if [ "$port_listening" = 1 ]; then
  owner=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
    | grep -E ":${port}->|0.0.0.0:${port}" | head -1 || true)
  if [ -n "$owner" ]; then
    port_owner_msg="port $port in use by container: $owner (expected if a model is already up — do not up another on same port)"
  else
    # Host-network managed containers do not show published ports. Prefer labels.
    managed_hit=""
    while IFS= read -r cname; do
      [ -n "$cname" ] || continue
      meta=$(docker inspect --format \
        '{{index .Config.Labels "io.pulsar.gb10.managed"}} {{index .Config.Labels "io.pulsar.gb10.conf"}} {{.HostConfig.NetworkMode}}' \
        "$cname" 2>/dev/null || true)
      # shellcheck disable=SC2086
      set -- $meta
      mflag="${1:-}" conf_l="${2:-}" net="${3:-}"
      if [ "$mflag" = "true" ] && [ -n "$conf_l" ]; then
        if [ "$net" = "host" ] || [ "$net" = "default" ]; then
          managed_hit="$cname conf=$conf_l net=$net"
          break
        fi
      fi
    done < <(docker ps --format '{{.Names}}' 2>/dev/null || true)

    api_ids=""
    api_auth_args=()
    api_auth_curl_args api_auth_args
    if api_json=$(curl -fsS --max-time 2 "${api_auth_args[@]}" "http://127.0.0.1:${port}/v1/models" 2>/dev/null); then
      api_ids=$(printf '%s' "$api_json" | python3 -c \
        'import sys,json; d=json.load(sys.stdin); print(",".join(x.get("id","") for x in d.get("data",[])))' \
        2>/dev/null || true)
    fi

    if [ -n "$managed_hit" ] && [ -n "$api_ids" ]; then
      port_owner_msg="port $port in use by stack-managed host-network service ($managed_hit; API models=$api_ids)"
    elif [ -n "$managed_hit" ]; then
      port_owner_msg="port $port in use by stack-managed service ($managed_hit; API not confirmed)"
    elif [ -n "$api_ids" ]; then
      port_owner_msg="port $port listening with OpenAI API models=$api_ids (ownership not proven via stack labels — inventory before replace)"
    else
      port_owner_msg="port $port already listening — owner unknown (not a proven stack-managed service); identify before up; wizard will not stop unknown owners"
    fi
  fi
  record warn port "$port_owner_msg"
elif [ "$port_listening" = 0 ]; then
  record ok port "port $port free"
else
  record warn port "cannot probe port $port (no ss/lsof)"
fi

if [ ! -e "$HF_CACHE" ] && [ ! -L "$HF_CACHE" ]; then
  hf_cache_ancestor="$HF_CACHE"
  while [ ! -e "$hf_cache_ancestor" ] && [ ! -L "$hf_cache_ancestor" ]; do
    hf_cache_parent=$(dirname -- "$hf_cache_ancestor")
    [ "$hf_cache_parent" != "$hf_cache_ancestor" ] || break
    hf_cache_ancestor="$hf_cache_parent"
  done
  if [ -d "$hf_cache_ancestor" ] \
      && [ -w "$hf_cache_ancestor" ] && [ -x "$hf_cache_ancestor" ]; then
    record warn hf_cache \
      "HF_CACHE missing ($HF_CACHE) · create it before downloading or serving Hugging Face models"
  else
    record fail hf_cache \
      "HF_CACHE cannot be created ($HF_CACHE) · nearest existing path is not a writable, searchable directory: $hf_cache_ancestor"
  fi
elif [ ! -d "$HF_CACHE" ]; then
  record fail hf_cache "HF_CACHE is not a directory: $HF_CACHE"
elif [ ! -r "$HF_CACHE" ] || [ ! -w "$HF_CACHE" ] || [ ! -x "$HF_CACHE" ]; then
  record fail hf_cache \
    "HF_CACHE permissions do not allow read, write, and directory access: $HF_CACHE"
else
  hf_cache_free=$(disk_free_gib "$HF_CACHE")
  record ok hf_cache "HF_CACHE ready · ${hf_cache_free} GiB free ($HF_CACHE)"
fi

if [ -d "$MODELS_NFS" ]; then
  record ok nfs "$MODELS_NFS present"
else
  record warn nfs "$MODELS_NFS not mounted (only needed for NFS catalog confs)"
fi

avail=$(mem_available_gib_local)
if awk -v a="$avail" -v f="$HARD_FLOOR_AVAILABLE_GIB" 'BEGIN{exit !(a+0 < f)}'; then
  record fail memory "MemAvailable ${avail} GiB < hard floor ${HARD_FLOOR_AVAILABLE_GIB} GiB"
else
  record ok memory "MemAvailable ${avail} GiB"
fi

[ "$JSON" = 1 ] || echo "[doctor] cluster network"
_fab_log=$(mktemp "${TMPDIR:-/tmp}/pulsar-doctor-fabric.XXXXXX")
# shellcheck disable=SC2064
trap 'rm -f "${_fab_log:-}"' RETURN
fabric_nodes=0
if "$REPO_DIR/scripts/detect-fabric.sh" --json >"$_fab_log" 2>&1; then
  fabric_nodes=$(python3 - "$_fab_log" 2>/dev/null <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    document = json.load(handle)
topology = document.get("topology") if "topology" in document else document
nodes = topology.get("nodes") if isinstance(topology, dict) else None
if not isinstance(nodes, list) or not nodes:
    raise SystemExit(1)
print(len(nodes))
PY
  ) || fabric_nodes=0
  if [[ "$fabric_nodes" =~ ^[1-9][0-9]*$ ]]; then
    fabric_system_word=system
    [ "$fabric_nodes" = 1 ] || fabric_system_word=systems
    record ok fabric "GB10 cluster network check passed · $fabric_nodes GB10 $fabric_system_word discovered"
  else
    record warn fabric "cluster discovery returned unreadable results (single-node models remain available)"
  fi
else
  record warn fabric "cluster network could not be verified (single-node models remain available)"
fi
rm -f "$_fab_log"
trap - RETURN

topology_load_ok=1
load_cluster_topology || topology_load_ok=0
if [ "$topology_load_ok" = 0 ]; then
  record fail topology "confirmed topology or generated SSH trust config is invalid"
elif [ "$CLUSTER_TOPOLOGY_COUNT" -gt 1 ]; then
  if [ "$CLUSTER_TOPOLOGY_SSH_TRUSTED" = 1 ]; then
    trust_report=$(mktemp "${TMPDIR:-/tmp}/pulsar-doctor-ssh-trust.XXXXXX")
    trust_error="${trust_report}.err"
    trust_rc=0
    python3 "$REPO_DIR/scripts/topology_ssh_trust.py" check \
      --topology "$CLUSTER_TOPOLOGY_FILE" \
      --ssh-config "$CLUSTER_SSH_CONFIG_FILE" \
      --probe "$REPO_DIR/scripts/probe-node.py" \
      --ssh-bin "$PULSAR_SSH" --json >"$trust_report" 2>"$trust_error" \
      || trust_rc=$?
    if python3 "$REPO_DIR/scripts/topology_ssh_trust.py" doctor-rows \
        "$trust_report" >"${trust_report}.rows" 2>/dev/null; then
      while IFS=$'\t' read -r trust_level trust_id trust_message; do
        [ -n "$trust_level" ] || continue
        record "$trust_level" "$trust_id" "$trust_message"
      done <"${trust_report}.rows"
    else
      trust_detail=$(tail -n 1 "$trust_error" 2>/dev/null || true)
      [ -n "$trust_detail" ] || trust_detail="identity check returned unreadable output"
      record fail ssh_trust "topology-bound SSH identity check failed (rc=$trust_rc) · $trust_detail"
    fi
    rm -f "$trust_report" "$trust_error" "${trust_report}.rows"
  else
    record warn ssh_trust \
      "SSH identity fingerprints are not enrolled · SSH-over-RoCE is blocked; run scripts/topology-ssh-trust.sh enroll"
  fi
  [ "$JSON" = 1 ] || echo "[doctor] other confirmed cluster nodes"
  for ((rank = 1; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    node_label=$(human_cluster_node "$rank")
    if "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" true 2>/dev/null; then
      record ok "rank_${rank}_ssh" "$node_label · SSH reachable at $host"
      rank_gpu=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
        "nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1" \
        2>/dev/null || true)
      if [ "$rank_gpu" = "NVIDIA GB10" ]; then
        record ok "rank_${rank}_gpu" "$node_label · GPU $rank_gpu"
      else
        record fail "rank_${rank}_gpu" "$node_label · GPU '$rank_gpu' (want NVIDIA GB10)"
      fi
      if "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
          "docker info 2>/dev/null | grep -Eq 'nvidia|nvidia.com/gpu'" 2>/dev/null; then
        record ok "rank_${rank}_docker_nvidia" "$node_label · Docker NVIDIA ready"
      else
        record fail "rank_${rank}_docker" "$node_label · Docker NVIDIA unavailable"
      fi
    else
      record fail "rank_${rank}_ssh" "$node_label · key-based SSH failed at $host"
    fi
  done
elif [[ "$fabric_nodes" =~ ^[1-9][0-9]*$ ]] && [ "$fabric_nodes" -gt 1 ]; then
  topology_message="$fabric_nodes GB10 systems discovered, but cluster membership is not confirmed."
  topology_message+=$'\n'
  topology_message+="Next: run ./pulsar wizard and confirm cluster discovery to enable multi-node models."
  record warn topology "$topology_message"
else
  record ok topology "no cluster membership confirmed · single-node models remain available"
fi

[ "$JSON" = 1 ] || echo "[doctor] model library"
library_report=$(mktemp "${TMPDIR:-/tmp}/pulsar-doctor-library.XXXXXX.json")
library_rows="${library_report}.rows"
library_render_rc=0
"$REPO_DIR/scripts/model-library.sh" health --json \
  >"$library_report" 2>/dev/null || true
python3 "$REPO_DIR/scripts/model_library.py" render-health \
    --report-file "$library_report" --doctor-rows \
    >"$library_rows" 2>/dev/null || library_render_rc=$?
# A valid attention/unavailable report emits complete rows and exits 1 by
# contract. Only malformed rendering (>1) or an empty projection uses fallback.
if [ "$library_render_rc" -le 1 ] && [ -s "$library_rows" ]; then
  while IFS=$'\t' read -r library_level library_id library_message; do
    [ -n "$library_level" ] || continue
    # Model-library findings are informational to replicated/default serving.
    [ "$library_level" = ok ] || library_level=warn
    record "$library_level" "$library_id" "$library_message"
  done <"$library_rows"
else
  record warn model_library \
    "model-library health is unavailable (replicated weights remain available)"
fi
rm -f "$library_report" "$library_rows"

result=pass
[ "$WARN" = 1 ] && result=pass_with_warnings
[ "$FAIL" = 1 ] && result=fail

if [ "$JSON" = 1 ]; then
  python3 - "$result" "$FAIL" "$WARN" "$arch" "$avail" "$port" "${CLUSTER_TOPOLOGY_COUNT:-0}" "${CHECKS[@]}" <<'PY'
import json, sys
result, fail, warn, arch, avail, port, confirmed = sys.argv[1:8]
checks = []
for item in sys.argv[8:]:
    level, cid, msg = item.split("|", 2)
    checks.append({"level": level, "id": cid, "message": msg})
print(json.dumps({
    "result": result,
    "fail": int(fail),
    "warn": int(warn),
    "arch": arch,
    "mem_available_gib": float(avail) if avail not in ("", "n/a") else None,
    "port": int(port) if str(port).isdigit() else port,
    "worker_confirmed": int(confirmed) >= 2,
    "checks": checks,
}, indent=2))
PY
else
  echo
  if [ "$FAIL" = 0 ]; then
    if [ "$WARN" = 1 ]; then
      doctor_ready_line "no blocking issues found; review warnings above"
    else
      doctor_ready_line "no blocking issues found"
    fi
  else
    echo "[doctor] NOT READY — fix blocking issues above before serving"
  fi
fi

[ "$FAIL" = 0 ]
