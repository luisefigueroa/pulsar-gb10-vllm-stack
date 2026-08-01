#!/usr/bin/env bash
# Preflight for 2-node serving. Run BEFORE start-cluster.sh starts anything.
#   cluster/preflight.sh [model-name]
# With a model-name it additionally verifies the image + weights on both nodes.
# Exit 0 = all checks passed.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
[ -f .env ] && { set -a; . ./.env; set +a; }
. cluster/cluster-env.sh
require_cluster_ips || exit 1
VLLM_IMAGE_MAINLINE="${VLLM_IMAGE_MAINLINE:-vllm/vllm-openai:v0.26.0}"
HF_CACHE="${HF_CACHE:-$HOME/.cache/huggingface}"

FAIL=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; FAIL=1; }
warn() { printf '  warn %s\n' "$1"; }

echo "[preflight] connectivity"
ping -c1 -W2 "$WORKER_IP" >/dev/null 2>&1 && ok "ping $WORKER_IP (RoCE rail 0)" || bad "ping $WORKER_IP"
# Optional second rail (set WORKER_IP_RAIL1 in .env if dual-rail)
if [ -n "${WORKER_IP_RAIL1:-}" ]; then
  ping -c1 -W2 "$WORKER_IP_RAIL1" >/dev/null 2>&1 && ok "ping $WORKER_IP_RAIL1 (RoCE rail 1)" || bad "ping rail 1 ($WORKER_IP_RAIL1)"
else
  warn "WORKER_IP_RAIL1 unset — skip rail-1 ping (set in .env for dual-rail checks)"
fi
ssh -o BatchMode=yes -o ConnectTimeout=5 "$WORKER_IP" true 2>/dev/null && ok "ssh $WORKER_IP" || bad "key-based ssh $WORKER_IP"

echo "[preflight] per-node checks"
check_node() {
  local name="$1" run="$2"
  local rdma gpu runtime shm
  rdma=$($run "rdma link show 2>/dev/null | grep -c ACTIVE" 2>/dev/null)
  [ "${rdma:-0}" -ge 2 ] && ok "$name: $rdma RDMA links ACTIVE" || bad "$name: only ${rdma:-0} RDMA links ACTIVE (want 2)"
  gpu=$($run "nvidia-smi --query-gpu=name --format=csv,noheader" 2>/dev/null)
  [ "$gpu" = "NVIDIA GB10" ] && ok "$name: GPU $gpu" || bad "$name: GPU '$gpu' (want NVIDIA GB10)"
  runtime=$($run "docker info 2>/dev/null | grep -c 'nvidia'" 2>/dev/null)
  [ "${runtime:-0}" -ge 1 ] && ok "$name: docker nvidia runtime" || bad "$name: docker nvidia runtime missing"
  $run "test -e /dev/infiniband/uverbs0" 2>/dev/null && ok "$name: /dev/infiniband present" || bad "$name: /dev/infiniband missing"
  # >=100 GiB free (weights + KV need most of the 121 GiB pool)
  local avail_kb
  avail_kb=$($run "awk '/MemAvailable/{print \$2}' /proc/meminfo" 2>/dev/null)
  if [ "${avail_kb:-0}" -ge 104857600 ]; then ok "$name: $((avail_kb/1048576)) GiB available"; else warn "$name: only $(( ${avail_kb:-0} /1048576)) GiB available — another workload running?"; fi
}
check_node "head  " "bash -c"
check_node "worker" "ssh $WORKER_IP"

echo "[preflight] stale state"
STALE_LOCAL=$(docker ps -a --format '{{.Names}}' | grep -c '^vllm-cluster-' || true)
STALE_REMOTE=$(ssh "$WORKER_IP" "docker ps -a --format '{{.Names}}' | grep -c '^vllm-cluster-'" 2>/dev/null || true)
[ "${STALE_LOCAL:-0}" -eq 0 ] && ok "head: no stale vllm-cluster containers" || bad "head: $STALE_LOCAL stale vllm-cluster containers (run cluster/stop-cluster.sh)"
[ "${STALE_REMOTE:-0}" -eq 0 ] && ok "worker: no stale vllm-cluster containers" || bad "worker: $STALE_REMOTE stale (run cluster/stop-cluster.sh)"

if [ -n "${1:-}" ]; then
  CONF="models/$1.conf"
  if [ -f "$CONF" ]; then
    NODES=1 IMAGE=""; CONTAINER_ENV=(); SPEC_DECODE_ARGS=(); ENGINE_ARGS=()
    # shellcheck disable=SC1090
    . "$CONF"
    IMAGE="${IMAGE:-$VLLM_IMAGE_MAINLINE}"
    echo "[preflight] model $1"
    docker image inspect "$IMAGE" >/dev/null 2>&1 && ok "head: image $IMAGE" || bad "head: image $IMAGE not pulled"
    ssh "$WORKER_IP" "docker image inspect '$IMAGE' >/dev/null 2>&1" && ok "worker: image $IMAGE" || bad "worker: image $IMAGE not pulled"
    # weights must exist on BOTH nodes (each rank loads locally); do a real read
    HFDIR="models--${MODEL//\//--}"
    if [[ "$MODEL" = /* ]]; then
      head -c 64 "$MODEL/config.json" >/dev/null 2>&1 && ok "head: weights readable at $MODEL" || bad "head: cannot read $MODEL/config.json"
      ssh "$WORKER_IP" "head -c 64 '$MODEL/config.json' >/dev/null 2>&1" && ok "worker: weights readable" || bad "worker: cannot read $MODEL/config.json"
    else
      test -d "$HF_CACHE/hub/$HFDIR" && ok "head: HF cache has $HFDIR" || bad "head: $HFDIR missing from $HF_CACHE/hub"
      ssh "$WORKER_IP" "test -d '$HF_CACHE/hub/$HFDIR'" && ok "worker: HF cache has $HFDIR" || bad "worker: $HFDIR missing on worker"
    fi
  else
    bad "no such config: $CONF"
  fi
fi

echo
if [ "$FAIL" = "0" ]; then echo "[preflight] ALL CHECKS PASSED"; else echo "[preflight] FAILURES — fix before starting"; fi
exit "$FAIL"
