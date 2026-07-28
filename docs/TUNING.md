# Tuning guide — GB10 specifics

Read docs/HARDWARE.md first; every recommendation here traces to a measured
number there or a benchmark in results/.

## The mental model

GB10 decode is **memory-bandwidth-bound at ~240 GB/s**. Estimate any model's
single-stream ceiling as `240 / active-GB-per-token`:

| Model | Active GB/tok | Ceiling | Measured (c=1) |
|---|---|---|---|
| Qwen3.6-27B-FP8 (hybrid GDN, no MoE) | ~28 | ~8.5 tok/s | **8.0** (results/bench-qwen27b-fp8.json) |
| Laguna-S-2.1-NVFP4 (A8B MoE) | ~9.8 | ~24 tok/s | pending |
| Nemotron-3-Super NVFP4 (A12B) | ~12.8 | ~19 tok/s | 16.5 prior art |
| DeepSeek-V4-Flash 2-node TP=2 | ~5.7/node | ~40 tok/s | pending |

Compute is comparatively plentiful: prefill runs fine, and batching scales
aggregate throughput well past the single-stream ceiling (concurrency sweeps
in results/). This asymmetry is exactly why speculative decoding is
attractive here — and why it must still prove itself per model
(verification consumes bandwidth too).

## Unified memory rules

- `--gpu-memory-utilization`: **0.85 max single-node, 0.80 for 2-node
  configs.** The pool is shared with the OS and page cache; 0.85 + swap
  pressure produced multi-GB swapping and shm busy-waits in prior art.
- Drop page cache before starting a big model after other workloads:
  `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`.
- Don't co-run anything memory-hungry (ComfyUI, etc.) beside a big model.
  `cluster/preflight.sh` warns under 100 GiB available.
- Never `--enforce-eager` for production: CUDA graphs are stable on this
  stack and worth ~2x at low concurrency (prior art measured the loss at
  ~55% throughput; our A/B in results/ confirms graphs-on is strictly
  better and greedy-identical).

## Backend selection (v0.26.0 mainline image)

Verified selections on sm_121, from engine logs (do not force what auto
already picks — but grep the log to confirm on every image bump):

- Attention: `FLASH_ATTN` (FA2) for dense GQA models. FLASHINFER and
  TRITON_ATTN available as fallbacks.
- FP8 linear: `CutlassFp8BlockScaledMMKernel` — native on the cu130 track.
  (`VLLM_TEST_FORCE_FP8_MARLIN=1` is a cu129-era relic; not needed here.)
- DeepGemm: auto-disabled for qwen3_5 with an accuracy warning — trust it.
- NVFP4 MoE: use `--moe-backend marlin` explicitly (CUTLASS FP4 MoE is
  silently wrong on sm_121 — upstream-documented, not re-derived here).
  Set `VLLM_MARLIN_USE_ATOMIC_ADD=1`.

## NCCL (2-node)

Ship set in `cluster/cluster-env.sh`, all measured on this cluster:
dual-rail `NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0` (+47% large-message),
`NCCL_IB_QPS_PER_CONNECTION=4` (+9% at >=256 MB, free elsewhere),
`NCCL_SOCKET_IFNAME=enp1s0f0np0` (bootstrap pin — default route is the
wrong NIC). MTU stays 1500 (PCIe x4 is the bottleneck; jumbo gains ~1%).
The DeepSeek config overrides to the exact single-rail env its image was
validated with; see the comment in `models/deepseek-v4-flash.conf`.

## Concurrency knobs

- `--max-num-seqs`: dense models keep per-stream decode acceptable up to
  ~8-16 concurrent (bandwidth divides across streams); MoE models batch
  better (expert reads amortize). Numbers per model in results/bench-*.
- `--max-num-batched-tokens 8192` bounds prefill chunks so decode latency
  stays stable under mixed load (validated value from the production
  DeepSeek deployment).
- Cold-start discipline for benchmarks: warm up at EACH concurrency level
  (Triton JITs per batch shape; cold numbers are ~100x artifacts).
  validate/bench_serve.py does this automatically.

## Speculative decoding

Off by default everywhere; `--spec-decode` opt-in per model where
SPEC_DECODE_ARGS exist. Decision record per model in docs/VALIDATION.md
(acceptance rate, tok/s delta, output-quality check). n-gram costs no
memory and helps repetitive/agentic workloads; MTP needs the checkpoint's
head; DFlash needs the shipped draft (Laguna). 2-node spec decode adds a
cross-node sync per verify step — a 1-node win does NOT imply a 2-node win.
