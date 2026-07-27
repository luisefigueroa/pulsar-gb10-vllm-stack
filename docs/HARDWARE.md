# Hardware Findings — 2x NVIDIA DGX Spark (GB10)

All numbers below were measured on these machines on 2026-07-27, not taken from
spec sheets. Raw benchmark logs live in `bench/results/step0/`.

## Nodes

| | dgx-spark-1 | dgx-spark-2 |
|---|---|---|
| Hostname / role | 192.168.100.30 (LAN), head | 10.100.120.2 / .121.2 (RoCE), worker |
| SoC | NVIDIA GB10 (Grace-Blackwell superchip) | identical |
| CPU | 20-core ARM (10x Cortex-X925 @ 3.9 GHz + 10x Cortex-A725 @ 2.8 GHz), aarch64 | identical |
| GPU | GB10, compute capability **12.1** (`sm_121`) | identical |
| Memory | **121 GiB unified LPDDR5X** (`MemTotal` 127.6 GB), shared CPU+GPU, no separate VRAM (`nvidia-smi` reports memory "N/A") | identical |
| OS / kernel | Ubuntu, `6.17.0-1026-nvidia` | identical |
| Driver / CUDA | driver **580.173.02**, CUDA **13.0** (host toolkit 13.0.3; V13.0.88 nvcc) | identical |
| Docker | 29.2.1, NVIDIA Container Toolkit 1.19.1, `nvidia` runtime available (default `runc`) | identical |

Host has no NCCL installed; NCCL comes from containers. The `nccl-bench:2.19.6`
image used for Step 0 measurements bundles **NCCL 2.28.3 + CUDA 13.0.1**.

## Measured memory bandwidth (the number that matters most)

GB10's unified LPDDR5X is the decode bottleneck. Measured with 2 GiB buffers,
median of 20 iters (`bench/membw.py`, PyTorch 26.06 container):

| Test | dgx-spark-1 | dgx-spark-2 |
|---|---|---|
| D2D copy (read+write) | 223.9 GB/s | 224.1 GB/s |
| Read-only reduction | 240.5 GB/s | 239.0 GB/s |
| Scale (read+write) | 223.9 GB/s | 224.6 GB/s |

≈ **240 GB/s effective read bandwidth** (~88% of the 273 GB/s theoretical).
Decode roofline: a model reading W GB of weights per token decodes at best
~240/W tok/s per node. Nodes are identical within noise — good for determinism.

## Interconnect: RoCE 200GbE, NOT NVLink

The user-reported "NVLink" between nodes is **not** what exists. Verified:

- `nvidia-smi topo -m`: no NVLink; 4 RDMA NICs visible (`rocep1s0f0/f1`,
  `roceP2p1s0f0/f1`), all PCIe-attached. NVLink-C2C exists only *inside* each
  node (Grace<->Blackwell).
- ConnectX-7 (fw 28.45.4028), **two QSFP ports active per node, 200 Gb/s each**,
  direct-attached node-to-node (no switch):
  - `enp1s0f0np0` = `rocep1s0f0`: 10.100.120.1 <-> 10.100.120.2
  - `enP2p1s0f0np0` = `roceP2p1s0f0`: 10.100.121.1 <-> 10.100.121.2
- Link layer Ethernet (RoCE), MTU 1500 (active RDMA MTU 1024).
- **Each NIC port sits on PCIe Gen5 x4** (`32.0 GT/s x4` from
  `/sys/class/infiniband/*/device`) → ~15.75 GB/s per-port ceiling. The "200GbE"
  wire rate (25 GB/s) is not the limit; PCIe is. Measured single-rail 13.9 GB/s
  is 88% of that ceiling — jumbo frames won't help (prior work measured MTU
  9000 at +0.7-1.5%, inside noise).
- RDMA is active: NCCL reports `NET/IB : Using [0]rocep1s0f0:1/RoCE` and all
  channels `via NET/IB/0` — verbs RDMA, not TCP sockets.
- GPUDirect RDMA is **off**: `Connected all rings, use ring PXN 0 GDR 0`
  (GPU and NICs are on separate PCIe root complexes). NCCL stages through host
  buffers — on GB10 unified memory that is the same physical LPDDR5X, so the
  penalty is small; the measured numbers above already include it.

## Measured NCCL performance (cross-node, container-to-container)

`all_reduce_perf`/`sendrecv_perf`, NCCL 2.28.3, 2 ranks. Logs in
`bench/results/step0/`.

| Metric | single rail | dual rail (both ports) |
|---|---|---|
| All-reduce bus BW @ 512 MB | 13.9 GB/s | **20.5 GB/s** |
| All-reduce bus BW @ 8-64 MB | 12.0-13.2 GB/s | 17.1-19.7 GB/s |
| All-reduce latency floor (<=4 KB) | 23-28 µs | 22-26 µs |
| Sendrecv P2P BW @ 512 MB | — | **21.5 GB/s** |
| Sendrecv latency floor | — | ~30 µs |

Env used: `NCCL_SOCKET_IFNAME=enp1s0f0np0,enP2p1s0f0np0`
`NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0` (dual rail). Dual rail costs nothing at
small sizes and gains ~45% at large sizes. Note a dip at exactly 4 MB on dual
rail (4.6 GB/s — NCCL channel-switch threshold artifact); single rail is smooth
there.

### What this means for parallelism

Per decode step, TP=2 issues 2 all-reduces per layer of `hidden_size * 2 B`
(batch 1: 8-32 KB → ~25-40 µs each). For a 60-layer model that is ~120 × ~30 µs
≈ **3.6 ms/token of pure comms latency**, against a weight-read saving of
`W/2 / 240 GB/s`. Break-even is W ≈ 1.7 GB — every target model is far above
that, so TP=2 across nodes is *theoretically* latency-positive. Real vLLM
behavior under concurrency is validated separately (see VALIDATION.md);
prefill and per-step sync overheads shift this in practice.

## Cluster network map

| Network | dgx-spark-1 | dgx-spark-2 | Use |
|---|---|---|---|
| LAN (1 GbE class) | 192.168.100.30 | — | NFS to 192.168.100.219, admin |
| RoCE rail 0 | 10.100.120.1 | 10.100.120.2 | NCCL, Ray control plane |
| RoCE rail 1 | 10.100.121.1 | 10.100.121.2 | NCCL rail 2 |
| Tailscale | 100.65.243.19 | — | remote access |

Passwordless SSH luis@10.100.120.2 works (used by launch tooling).

## Storage

- `/mnt/Models` — NFS from 192.168.100.219 (`15T` volume, 91% full), holds the
  official-weights catalog under `Official Models/` (see MODELS.md for the
  surveyed catalog and fit arithmetic).
- Weights load over NFS on a 1 GbE-class LAN link — expect slow cold loads;
  keep HF cache/local copies for hot models.

## Prior art on these machines

`~/Github/claude-opus-5-vllm-gb10-optimized` is an earlier build against the
same prompt (Claude Opus 5), with its own knowledge base. Its measurements were
NOT copied here — everything above was re-measured — but its compat findings
were consulted during design (credited where used, in TROUBLESHOOTING.md).
