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
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; FAIL=1; }
wrn()  { printf '  warn %s\n' "$1"; WARN=1; }

echo "[doctor] host"
arch=$(uname -m)
case "$arch" in
  aarch64|arm64) ok "arch=$arch" ;;
  *) wrn "arch=$arch (validated stack expects aarch64 GB10)" ;;
esac

if [ ! -r /proc/meminfo ]; then
  wrn "not a Linux serve host (/proc/meminfo missing) — doctor is informational here"
fi

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//')
  [ "$gpu" = "NVIDIA GB10" ] && ok "GPU $gpu" || bad "GPU '$gpu' (want NVIDIA GB10)"
else
  bad "nvidia-smi missing"
fi

if command -v docker >/dev/null 2>&1; then
  ok "docker present"
  # Prefer structured runtime list. Do not use "docker info | grep nvidia":
  # on some installs info is huge / partial and the naive grep is brittle.
  runtimes=$(docker info -f '{{range $k,$v := .Runtimes}}{{$k}} {{end}}' 2>/dev/null || true)
  if echo " $runtimes " | grep -Eq '[[:space:]]nvidia[[:space:]]'; then
    ok "docker nvidia runtime registered (runtimes: $runtimes)"
  elif docker info 2>/dev/null | grep -q 'nvidia.com/gpu'; then
    ok "docker nvidia CDI devices present"
  elif command -v nvidia-container-runtime >/dev/null 2>&1 \
    || command -v nvidia-container-cli >/dev/null 2>&1; then
    wrn "nvidia container tools installed but runtime not listed in docker info yet — try: sudo systemctl restart docker"
  else
    bad "docker nvidia runtime missing (expected Runtimes includes nvidia)"
  fi
else
  bad "docker missing"
fi

port="${PORT:-8000}"
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE ":${port}\$"; then
    owner=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep -E ":${port}->|0.0.0.0:${port}" | head -1 || true)
    if [ -n "$owner" ]; then
      wrn "port $port in use by container: $owner (expected if flagship already up — do not up another model on same port)"
    else
      wrn "port $port already listening — identify the owner before up"
    fi
  else
    ok "port $port free"
  fi
elif command -v lsof >/dev/null 2>&1; then
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    wrn "port $port already listening"
  else
    ok "port $port free"
  fi
else
  wrn "cannot probe port $port (no ss/lsof)"
fi

mkdir -p "$HF_CACHE" 2>/dev/null || true
if [ -w "$HF_CACHE" ] || mkdir -p "$HF_CACHE" 2>/dev/null; then
  ok "HF_CACHE writable ($HF_CACHE)"
else
  bad "HF_CACHE not writable: $HF_CACHE"
fi

if [ -d "$MODELS_NFS" ]; then
  ok "$MODELS_NFS present"
else
  wrn "$MODELS_NFS not mounted (only needed for NFS catalog confs)"
fi

avail=$(mem_available_gib_local)
if awk -v a="$avail" -v f="$HARD_FLOOR_AVAILABLE_GIB" 'BEGIN{exit !(a+0 < f)}'; then
  bad "MemAvailable ${avail} GiB < hard floor ${HARD_FLOOR_AVAILABLE_GIB} GiB"
else
  ok "MemAvailable ${avail} GiB"
fi

echo "[doctor] fabric (informational)"
if "$REPO_DIR/scripts/detect-fabric.sh" >/tmp/pulsar-doctor-fabric.$$ 2>&1; then
  ok "fabric detect confidence not low"
else
  wrn "fabric detect low confidence (fine for single-node)"
fi
rm -f /tmp/pulsar-doctor-fabric.$$

if [ -n "${WORKER_IP:-}" ]; then
  echo "[doctor] worker $WORKER_IP"
  if ssh -o BatchMode=yes -o ConnectTimeout=5 "$WORKER_IP" true 2>/dev/null; then
    ok "ssh $WORKER_IP"
    wgpu=$(ssh -o BatchMode=yes "$WORKER_IP" "nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1" || true)
    [ "$wgpu" = "NVIDIA GB10" ] && ok "worker GPU $wgpu" || bad "worker GPU '$wgpu'"
    ssh -o BatchMode=yes "$WORKER_IP" "docker info 2>/dev/null | grep -qi nvidia" \
      && ok "worker docker nvidia" || bad "worker docker nvidia missing"
  else
    bad "ssh $WORKER_IP failed (key-based BatchMode)"
  fi
else
  wrn "WORKER_IP unset — skip multi-node checks (Path B needs .env)"
fi

echo
if [ "$FAIL" = 0 ]; then
  echo "[doctor] PASS (Path A essentials)$([ "$WARN" = 1 ] && echo ' with warnings')"
  exit 0
fi
echo "[doctor] FAIL — fix items above before serving"
exit 1
