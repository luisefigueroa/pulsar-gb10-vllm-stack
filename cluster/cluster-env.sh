#!/usr/bin/env bash
# Shared cluster environment for 2x DGX Spark (dgx-spark-1 / dgx-spark-2).
#
# Every NCCL value here was measured on THIS cluster on 2026-07-27
# (bench/results/step0/*.log) — see docs/HARDWARE.md. Do not add settings
# without a before/after number.

# ---- topology ---------------------------------------------------------------
export HEAD_IP="${HEAD_IP:-10.100.120.1}"      # dgx-spark-1, RoCE rail 0
export WORKER_IP="${WORKER_IP:-10.100.120.2}"  # dgx-spark-2, RoCE rail 0
export MASTER_PORT="${MASTER_PORT:-29500}"

# ---- NCCL: validated ship set ----------------------------------------------
# Dual rail: 13.9 -> 20.5 GB/s large-message all-reduce (allreduce-dual-rail.log)
export NCCL_IB_HCA="${NCCL_IB_HCA:-rocep1s0f0,roceP2p1s0f0}"
# +9% at >=256MB, no small-message penalty (allreduce-dual-rail-qps4.log)
export NCCL_IB_QPS_PER_CONNECTION="${NCCL_IB_QPS_PER_CONNECTION:-4}"
# Bootstrap must be pinned: the default route is the 192.168.100.x LAN NIC
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
export VLLM_HOST_IP_HEAD="$HEAD_IP"
export VLLM_HOST_IP_WORKER="$WORKER_IP"
