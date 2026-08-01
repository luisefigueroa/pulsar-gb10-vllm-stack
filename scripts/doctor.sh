#!/usr/bin/env bash
# Host readiness for Path A; multi-node extras when WORKER_IP is set.
#   scripts/doctor.sh [--json]
# exit 0 if Path A essentials pass
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
      [ "$JSON" = 1 ] || printf '  ok   %s\n' "$msg"
      ;;
    warn)
      WARN=1
      [ "$JSON" = 1 ] || printf '  warn %s\n' "$msg"
      ;;
    fail)
      FAIL=1
      [ "$JSON" = 1 ] || printf '  FAIL %s\n' "$msg"
      ;;
  esac
}

[ "$JSON" = 1 ] || echo "[doctor] host"

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
  record ok docker "docker present"
  runtimes=$(docker info -f '{{range $k,$v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null || true)
  if echo " $runtimes " | grep -Eq '[[:space:]]nvidia[[:space:]]'; then
    record ok docker_nvidia "docker nvidia runtime registered (runtimes: $runtimes)"
  elif docker info 2>/dev/null | grep -q 'nvidia.com/gpu'; then
    record ok docker_nvidia "docker nvidia CDI devices present"
  elif command -v nvidia-container-runtime >/dev/null 2>&1 \
    || command -v nvidia-container-cli >/dev/null 2>&1; then
    record warn docker_nvidia "nvidia container tools installed but runtime not listed in docker info yet — try: sudo systemctl restart docker"
  else
    record fail docker_nvidia "docker nvidia runtime missing (expected Runtimes includes nvidia)"
  fi
else
  record fail docker "docker missing"
fi

port="${PORT:-8000}"
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then
    owner=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E ":${port}->|0.0.0.0:${port}" | head -1 || true)
    if [ -n "$owner" ]; then
      record warn port "port $port in use by container: $owner (expected if flagship already up — do not up another model on same port)"
    else
      record warn port "port $port already listening — identify the owner before up"
    fi
  else
    record ok port "port $port free"
  fi
elif command -v lsof >/dev/null 2>&1; then
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    record warn port "port $port already listening"
  else
    record ok port "port $port free"
  fi
else
  record warn port "cannot probe port $port (no ss/lsof)"
fi

mkdir -p "$HF_CACHE" 2>/dev/null || true
if [ -w "$HF_CACHE" ] || mkdir -p "$HF_CACHE" 2>/dev/null; then
  record ok hf_cache "HF_CACHE writable ($HF_CACHE)"
else
  record fail hf_cache "HF_CACHE not writable: $HF_CACHE"
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

[ "$JSON" = 1 ] || echo "[doctor] fabric (informational)"
if "$REPO_DIR/scripts/detect-fabric.sh" >/tmp/pulsar-doctor-fabric.$$ 2>&1; then
  record ok fabric "fabric detect confidence not low"
else
  record warn fabric "fabric detect low confidence (fine for single-node)"
fi
rm -f /tmp/pulsar-doctor-fabric.$$

if [ -n "${WORKER_IP:-}" ]; then
  [ "$JSON" = 1 ] || echo "[doctor] worker $WORKER_IP"
  if ssh -o BatchMode=yes -o ConnectTimeout=5 "$WORKER_IP" true 2>/dev/null; then
    record ok worker_ssh "ssh $WORKER_IP"
    wgpu=$(ssh -o BatchMode=yes "$WORKER_IP" "nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1" || true)
    if [ "$wgpu" = "NVIDIA GB10" ]; then
      record ok worker_gpu "worker GPU $wgpu"
    else
      record fail worker_gpu "worker GPU '$wgpu'"
    fi
    if ssh -o BatchMode=yes "$WORKER_IP" "docker info -f '{{range \$k,\$v := .Runtimes}}{{\$k}} {{end}}' 2>/dev/null" \
      | grep -Eq '(^|[[:space:]])nvidia([[:space:]]|$)'; then
      record ok worker_docker_nvidia "worker docker nvidia"
    else
      record fail worker_docker_nvidia "worker docker nvidia missing"
    fi
  else
    record fail worker_ssh "ssh $WORKER_IP failed (key-based BatchMode)"
  fi
else
  record warn worker "WORKER_IP unset — skip multi-node checks (Path B needs .env)"
fi

result=pass
[ "$WARN" = 1 ] && result=pass_with_warnings
[ "$FAIL" = 1 ] && result=fail

if [ "$JSON" = 1 ]; then
  python3 - "$result" "$FAIL" "$WARN" "$arch" "$avail" "$port" "${WORKER_IP:-}" "${CHECKS[@]}" <<'PY'
import json, sys
result, fail, warn, arch, avail, port, worker = sys.argv[1:8]
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
    "worker_ip_set": bool(worker),
    "checks": checks,
}, indent=2))
PY
else
  echo
  if [ "$FAIL" = 0 ]; then
    echo "[doctor] PASS (Path A essentials)$([ "$WARN" = 1 ] && echo ' with warnings')"
  else
    echo "[doctor] FAIL — fix items above before serving"
  fi
fi

[ "$FAIL" = 0 ]
