#!/usr/bin/env bash
# Shared cluster environment for 2x DGX Spark (example hostnames:
# dgx-spark-1 head / dgx-spark-2 worker).
#
# Every NCCL value here was measured on a dual-rail RoCE GB10 pair on
# 2026-07-27 (bench/results/step0/*.log) — see docs/HARDWARE.md. Do not add
# settings without a before/after number.
#
# HEAD_IP / WORKER_IP are not defaulted. Set them in a gitignored .env for
# 2-node launches (see .env.example). Single-node ./serve.sh does not need them.

# ---- topology ---------------------------------------------------------------
export HEAD_IP="${HEAD_IP:-}"
export WORKER_IP="${WORKER_IP:-}"
export MASTER_PORT="${MASTER_PORT:-29500}"

# Call from cluster/start|stop|preflight after sourcing this file.
require_cluster_ips() {
  if [ -z "${HEAD_IP}" ] || [ -z "${WORKER_IP}" ]; then
    echo "cluster-env: set HEAD_IP and WORKER_IP to this fabric's RoCE rail-0 addresses (e.g. in .env)." >&2
    echo "  Example hostnames: dgx-spark-1 (head), dgx-spark-2 (worker). See docs/HARDWARE.md and .env.example." >&2
    return 1
  fi
}

# ---- NCCL: validated ship set ----------------------------------------------
# Dual rail: 13.9 -> 20.5 GB/s large-message all-reduce (allreduce-dual-rail.log)
export NCCL_IB_HCA="${NCCL_IB_HCA:-rocep1s0f0,roceP2p1s0f0}"
# +9% at >=256MB, no small-message penalty (allreduce-dual-rail-qps4.log)
export NCCL_IB_QPS_PER_CONNECTION="${NCCL_IB_QPS_PER_CONNECTION:-4}"
# Bootstrap must be pinned to the RoCE data NIC (not the admin/LAN default route)
export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-enp1s0f0np0}"
export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-enp1s0f0np0}"
export TP_SOCKET_IFNAME="${TP_SOCKET_IFNAME:-enp1s0f0np0}"
export NCCL_IB_DISABLE=0
export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
# Deliberately NOT set (measured no effect or auto-detect is correct here):
#   NCCL_IB_GID_INDEX   - auto-detect picks the RoCEv2 GID correctly
#   NCCL_NET_GDR_LEVEL  - GDR is off on GB10 (separate PCIe root complexes);
#                         forcing it changes nothing (prior art: +/-0%)
#   MTU 9000            - +0.7..1.5% only; PCIe Gen5 x4 is the bottleneck

# ---- vLLM on unified memory ---------------------------------------------------
# GB10 has no dedicated VRAM; CUDA, OS, and page cache share 121 GiB.
# 0.70-0.85 gpu-memory-utilization is set per model in models/*.conf.
export VLLM_HOST_IP_HEAD="${HEAD_IP}"
export VLLM_HOST_IP_WORKER="${WORKER_IP}"
