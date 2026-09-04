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

(raw sweeps in results/bench-*.json; the roofline model predicts within
6-21% for the retained rows — active-bytes arithmetic is a useful planning
tool on this hardware.)

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
  - `nemotron-3-super`: set to `1` (matches the validated conf that earned
    its STATUS row).
  - `nemotron-3-nano`: dropped on 2026-09-04. The strict same-boot gate of
    baseline-v1 failed with it set (29/30, one near-tie token flip) and
    failed the same way without it (tokens identical, logprobs of one
    prompt vary by 0.2 to 0.35 between passes on v0.26.0), so it is not
    the cause; dropping it changed no throughput (64 decode tok/s at c=1,
    296 aggregate at c=8). Left off for the simpler recipe.
  - `laguna-s-2.1-nvfp4`: profile removed by ADR 0006 (absolute-path
    catalog). Historical conf left this unset; do not cargo-cult it onto
    remaining NVFP4 models without a dedicated determinism A/B.
  - Not a correctness fix for the CUTLASS-vs-Marlin MoE bug — that is
    `--moe-backend marlin`. Atomic-add is an implementation detail of
    the Marlin path; treat it as conf-local unless you remeasure.

## NCCL (measured two-node baseline)

The Step-0 measurements used dual rail
`NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0`, yielding +47% large-message
bandwidth, plus `NCCL_IB_QPS_PER_CONNECTION=4` (+9% at ≥256 MB with no
small-message penalty). MTU remains 1500 because PCIe x4 is the bottleneck and
jumbo frames measured ~1%.

Those interface names are evidence from the measured pair, not cluster-wide
defaults. A confirmed topology supplies each rank HCA list and control
interface at launch. The launcher forces `NCCL_NET=IB`; Gloo/socket bootstrap
uses the control interface while model traffic stays on RoCE. Adding ranks can
change collective selection and contention, so no two-node NCCL or serving
number is promoted to a larger world size without remeasurement.

## Concurrency knobs

- `--max-num-seqs`: dense models keep per-stream decode acceptable up to
  ~8-16 concurrent (bandwidth divides across streams); MoE models batch
  better (expert reads amortize). Numbers per model in results/bench-*.
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
| **MTP k=1** on Nemotron-Super (triton draft head) | **opt-in WIN** | +47% c=1; lossless; `./serve.sh … --spec-decode` |
| **DFlash k=15** on Laguna | **marginal opt-in** | +13% c=1; default conf keeps it off |
| **ngram** on GDN hybrids (Qwen3.6) | **FAIL** | output corruption — never enable |

Always use `validate/bench_serve.py` (token counts from usage, not SSE chunks)
and natural prompts for new A/Bs. Historical pre-fix tables in
VALIDATION.md are retained as the retraction trail, not as ship guidance.

## Determinism knobs

- Bit-exact cross-node/cross-boot greedy: `VLLM_BATCH_INVARIANT=1`
  (verified 30/30 across nodes) — standard-attention models only; GDN/Mamba
  hybrids refuse to start with it. Costs unmeasured here; enable for
  reproducibility work, not production.
- Default configs are FP-equivalent across boots/nodes (near-tie argmax
  flips only) — per-boot compile-time kernel selection is nondeterministic.
  Within one boot, FLASH_ATTN-path models are exactly reproducible.
