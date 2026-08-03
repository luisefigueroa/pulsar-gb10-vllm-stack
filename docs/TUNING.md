# Tuning guide — GB10 specifics

Read docs/HARDWARE.md first; every recommendation here traces to a measured
number there or a benchmark in results/.

## The mental model

GB10 decode is **memory-bandwidth-bound at ~240 GB/s**. Estimate any model's
single-stream ceiling as `240 / active-GB-per-token`:

| Model | Active GB/tok | Ceiling | Measured c=1 | Aggregate peak |
|---|---|---|---|---|
| Qwen3.6-27B-FP8 (hybrid GDN, no MoE) | ~28 | ~8.5 tok/s | **8.0 (94%)** | 93 @ c=16 |
| Laguna-S-2.1-NVFP4 (A8B MoE) | ~9.8 | ~24 tok/s | **19.5 (79%)** | 66 @ c=4 |
| Nemotron-3-Nano NVFP4 (A3B) | ~3.3 | ~72 tok/s | **61.9 (86%)** | 399 @ c=16 |
| Nemotron-3-Super NVFP4 (A12B) | ~12.8 | ~19 tok/s | **16.2 (85%)** | 113 @ c=32 |
| DeepSeek-V4-Flash 2-node TP=2 | ~5.7/node | ~40 | **27.0 (68%)** | 109 @ c=8 |

(raw sweeps in results/bench-*.json; the roofline model predicts within
6-21% everywhere — active-bytes arithmetic is a reliable planning tool on
this hardware. The flagship's larger gap is cross-node all-reduce time,
consistent with the measured ~2.3 ms/token comms budget.)

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
- `VLLM_MARLIN_USE_ATOMIC_ADD`: real vLLM env (passed via conf
  `CONTAINER_ENV`). **Model-conf owned, not a global default.**
  - `nemotron-3-nano` / `nemotron-3-super`: set to `1` (matches the
    validated confs that earned their STATUS rows).
  - `laguna-s-2.1-nvfp4`: intentionally **unset** (removed pending a
    dedicated determinism A/B; do not cargo-cult it back without that
    gate).
  - Not a correctness fix for the CUTLASS-vs-Marlin MoE bug — that is
    `--moe-backend marlin`. Atomic-add is an implementation detail of
    the Marlin path; treat it as conf-local unless you remeasure.

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

## Speculative decoding: measure honestly, enable where it wins

**Current doctrine (post 2026-07-31 metering fix — see VALIDATION.md
retraction trail):** draft work still competes for LPDDR5X bandwidth, so
speculation is **not** free. Early "everything loses" numbers were partly
**instrument error** (`bench_serve` under-counted accepted draft tokens by
the acceptance factor). Re-measured with fixed metering + natural prompts:

| Method | Status | Notes |
|---|---|---|
| **DSpark k=5** on DeepSeek-V4-Flash (PR-41834, 2-node) | **DEFAULT** | +79% c=1 (48.4 vs 27.1); 150-min soaks PASS; roll back with `--no-spec-decode` |
| **MTP k=1** on Nemotron-Super (triton draft head) | **opt-in WIN** | +47% c=1; lossless; `./serve.sh … --spec-decode` |
| **DFlash k=15** on Laguna | **marginal opt-in** | +13% c=1; default conf keeps it off |
| **ngram** on GDN hybrids (Qwen3.6) | **FAIL** | output corruption — never enable |
| Generic **MTP** on DSV4 (pre-DSpark path) | superseded | use DSpark on the flagship image instead |

Always use `validate/bench_serve.py` (token counts from usage, not SSE chunks)
and natural prompts for new A/Bs. Historical pre-fix tables in
VALIDATION.md are retained as the retraction trail, not as ship guidance.

DSpark k is not part of the tuning surface. For DeepSeek-V4-Flash-0731,
`num_speculative_tokens` must equal the checkpoint's `dspark_block_size=5`.
Larger values draft structurally unreachable positions and reduce acceptance
([vLLM PR #41834](https://github.com/vllm-project/vllm/pull/41834)); changing k
requires a checkpoint/upstream contract change and full revalidation.

## Determinism knobs

- Bit-exact cross-node/cross-boot greedy: `VLLM_BATCH_INVARIANT=1`
  (verified 30/30 across nodes) — standard-attention models only; GDN/Mamba
  hybrids refuse to start with it. Costs unmeasured here; enable for
  reproducibility work, not production.
- Default configs are FP-equivalent across boots/nodes (near-tie argmax
  flips only) — per-boot compile-time kernel selection is nondeterministic.
  Within one boot, FLASH_ATTN-path models are exactly reproducible.
