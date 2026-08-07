#!/usr/bin/env bash
# Shared cluster environment for validated multi-node GB10 profiles.
#
# Every NCCL value here was measured on a dual-rail RoCE GB10 pair on
# 2026-07-27 (bench/results/step0/*.log) — see docs/HARDWARE.md. Do not add
# settings without a before/after number.
#
# A confirmed .cluster-topology.json supplies per-rank control IPs, SSH targets,
# HCAs, and interfaces. HEAD_IP/WORKER_IP remain a two-node compatibility path.

_cluster_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_cluster_env_dir/topology.sh"

# ---- topology ---------------------------------------------------------------
export HEAD_IP="${HEAD_IP:-}"
export WORKER_IP="${WORKER_IP:-}"
export MASTER_PORT="${MASTER_PORT:-29500}"

# Compatibility call for legacy two-node callers.
require_cluster_ips() {
  require_cluster_nodes 2 || return 1
  HEAD_IP="${CLUSTER_NODE_CONTROL_IPS[0]}"
  WORKER_IP="${CLUSTER_NODE_SSH_HOSTS[1]}"
  VLLM_HOST_IP_HEAD="$HEAD_IP"
  VLLM_HOST_IP_WORKER="${CLUSTER_NODE_CONTROL_IPS[1]}"
  export HEAD_IP WORKER_IP VLLM_HOST_IP_HEAD VLLM_HOST_IP_WORKER
}

# ---- NCCL: validated ship set ----------------------------------------------
# Dual rail: 13.9 -> 20.5 GB/s large-message all-reduce (allreduce-dual-rail.log)
export NCCL_IB_HCA="${NCCL_IB_HCA:-rocep1s0f0,roceP2p1s0f0}"
# +9% at >=256MB, no small-message penalty (allreduce-dual-rail-qps4.log)
export NCCL_IB_QPS_PER_CONNECTION="${NCCL_IB_QPS_PER_CONNECTION:-4}"
# Legacy .env defaults. Confirmed manifests override these per rank at launch.
export NCCL_SOCKET_IFNAME="${NCCL_SOCKET_IFNAME:-enp1s0f0np0}"
export GLOO_SOCKET_IFNAME="${GLOO_SOCKET_IFNAME:-enp1s0f0np0}"
export TP_SOCKET_IFNAME="${TP_SOCKET_IFNAME:-enp1s0f0np0}"
export NCCL_IB_DISABLE=0
export NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
# Deliberately NOT set globally:
#   NCCL_IB_GID_INDEX   - auto-detect picks the RoCEv2 GID correctly
#   NCCL_NET_GDR_LEVEL  - GDR is off on GB10 (separate PCIe root complexes)
#   MTU 9000            - +0.7..1.5% only; PCIe Gen5 x4 is the bottleneck
# Multi-node launch sets NCCL_NET=IB so a broken RDMA path fails closed instead
# of silently moving model traffic onto the shared control LAN.

# ---- vLLM on unified memory -------------------------------------------------
# GB10 has no dedicated VRAM; CUDA, OS, and page cache share 121 GiB.
# 0.70-0.85 gpu-memory-utilization is set per model in models/*.conf.
export VLLM_HOST_IP_HEAD="${HEAD_IP}"
export VLLM_HOST_IP_WORKER="${WORKER_IP}"
