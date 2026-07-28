# Validation ledger

Rules (PROMPT.md): nothing is "done" until it passes here. Gates are
token-match rate + logprob closeness (not bit-exactness) across kernels /
parallelism; bit-exactness IS required run-to-run on identical config.
Raw artifacts: `results/`.

Status legend: PASS / FAIL / PENDING (not yet run) / N-A.

## Build & startup

| Check | Status | Evidence |
|---|---|---|
| Overlay image builds | PASS | `vllm-gb10:v0.26.0`, build ~2 s on cached base (docs/BUILD.md) |
| Container starts clean, node 1 | PASS | qwen3-1.7b healthy 140 s; qwen3.6-27b-fp8 healthy 510 s cold |
| Container starts clean, node 2 | PASS | 2-node worker (headless) startup during canary run |

## Correctness vs reference

| Model | Comparison | Status | Numbers |
|---|---|---|---|
| Qwen3-1.7B BF16 | vLLM vs HF transformers greedy, 30 prompts x 64 tok | **PASS** | 18/30 exact text; all 12 divergences are near-ties (chosen-token logprob delta 0.03-0.30 at flip point); max logprob delta 0.141 on matched prefixes; verdict FP-EQUIVALENT (`results/qwen1.7b-*`) |
| Qwen3.6-27B-FP8 | gsm8k 5-shot, 200 samples via lm-eval | **RECORDED** | 0.630 flexible / 0.615 strict (±0.034). Absolute value reflects raw-completion prompting of a reasoning-tuned model; used as the FP8 baseline for quant-level comparison (BF16 control pending) |
| Laguna-S-2.1-NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.820 strict** / 0.45 flexible (must use `tokenized_requests=False` — see TROUBLESHOOTING for the 0.055 false reading) |
| Nemotron-3-Nano NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.830 strict** / 0.465 flexible |
| Nemotron-3-Super NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.940 strict** / 0.95 flexible |
| DeepSeek-V4-Flash (2-node TP=2) | gsm8k 5-shot, 200 samples | **RECORDED** | **0.970 strict / 0.970 flexible** — best in fleet |

## Determinism

| Check | Status | Numbers |
|---|---|---|
| Same node, same config, run-to-run (27B FP8, greedy, 30 prompts) | **PASS** | 30/30 exact text, mean prefix 1.0000, max logprob delta 0.0000 |
| Same config run-to-run on node B (Qwen3-1.7B) | **PASS** | 30/30 exact, delta 0.0000 |
| Same node across time + heavy traffic (27B FP8, runA vs runC) | **PASS** | 30/30 IDENTICAL — prefix cache/warmup does not perturb greedy output |
| Node A vs node B, same image+config (27B FP8, default flags) | **ROOT-CAUSED** | 12/30 exact, all near-tie flips. NOT node drift: weights/config/image/driver verified identical, and the same node vs ITSELF across a server restart shows the same divergence (16/30). Cause: per-boot nondeterministic kernel selection in the compile pipeline — eager mode recovers 28/30; autotune-off alone does nothing (13/30). |
| Node A vs node B with `VLLM_BATCH_INVARIANT=1` (Qwen3-1.7B) | **PASS — IDENTICAL** | 30/30 exact, max logprob delta 0.0000, across different boots AND nodes. This is the reproducibility switch — but it is unsupported for GDN/Mamba hybrid architectures (engine refuses to start: `not supported for GDN_ATTN`), so Qwen3.6-27B cannot use it. |

**Determinism guarantee this cluster actually gets:** within one engine boot,
greedy output is exactly reproducible for the FLASH_ATTN-path models
(Qwen3-1.7B, Qwen3.6-27B, Nemotron-Nano — verified through heavy traffic).
Laguna (FLASHINFER path) shows near-tie-only FP noise even run-to-run within
a boot — isolated to the execution path itself: NOT prefix caching (persists
with `--no-enable-prefix-caching`), NOT Marlin atomic-add (persists without;
perf-neutral 19.46 vs 19.48 tok/s, so the knob ships removed). Across boots
or nodes, all default configs are FP-equivalent (near-tie flips only).
Bit-exact cross-node reproducibility is available opt-in via
`VLLM_BATCH_INVARIANT=1` for standard-attention models (unsupported on
GDN/Mamba hybrids). There is no hardware or software drift between nodes.
| 1-node vs 2-node same model (TP reduction order may differ; gate on match rate) | PENDING | |

## Multi-node

| Check | Status | Numbers |
|---|---|---|
| Native --nnodes TP=2 cross-node serves (standard attention) | PASS | Qwen3-1.7B canary, correct greedy output |
| Concurrency >= 2 on 2-node (prior-stack killer) | PASS | 8/8 concurrent correct on canary |
| TP=2 cross-node with a GDN hybrid (Qwen3.6-27B) | **FAIL** | Wrong output on first request ("2+2=" -> "5"), then `RPC call to sample_tokens timed out` -> engine dead on the first capture request. Same signature as the prior repo's unsolved hang — whose "dense control" was actually ALSO a GDN hybrid, so its architecture-eliminated conclusion is corrected here: **recurrent-state hybrids (GDN/Mamba) must not be deployed TP=2 cross-node on this stack**; standard-attention models are fine. |
| RDMA (not TCP) transport in vLLM containers | **PASS** | flagship bring-up with NCCL_DEBUG=INFO: `NET/IB : Using [0]rocep1s0f0:1/RoCE`, channels `via NET/IB/0` |
| DeepSeek-V4-Flash TP=2 serves + concurrency | IN PROGRESS | healthy, correct smoke output; battery running |
| Node-loss behavior documented | PENDING | expected: no recovery; verify once |

## Long context

| Model | Claimed | Needle result | Status |
|---|---|---|---|
| Qwen3.6-27B-FP8 | 131,072 (conf) | 3/3 PASS at 121,138 prompt tokens (depths .05/.5/.95) | **PASS** |
| Laguna-S-2.1-NVFP4 | 262,144 (config max) | 3/3 PASS at 260,907 prompt tokens (99.5% of max) | **PASS** |
| DeepSeek-V4-Flash | 500,000 (prior prod value) | | PENDING |

## Speculative decoding (all off by default)

| Model | Method | Acceptance | tok/s spec vs base | Output unchanged? | Verdict |
|---|---|---|---|---|---|
| Qwen3.6-27B-FP8 | ngram k=4 (FLASH_ATTN) | ~26% (233/904) | n/a | **NO — corrupted**: 3/30 exact, 8 hard disagreements, one output devolves into unrelated garbled text (replacement char + off-topic content). Likely GDN-state rollback breakage under spec verify on sm_121 | **FAIL — do not enable** |
| Qwen3.6-27B-FP8 | ngram k=4 (TRITON_ATTN workaround) | — | n/a | **NO** — still corrupted (2/30 exact, 5 hard disagreements, delta 1.26). Not attention-backend-specific; the GDN hybrid + spec-verify path itself is broken on this stack | **FAIL — ngram unusable on GDN hybrids; SPEC_DECODE_ARGS removed from conf** |
| Laguna-S-2.1-NVFP4 | dflash k=15 (the checkpoint's own gen_config wiring, NVFP4-matched draft) | 21.3% (4799/22530; 3.2 tok/round) | **9.47 vs 19.48 c=1 (-51%)**; worse at c=2/4 too | 1 hard disagreement (delta 0.67) on top of this model's baseline near-tie noise | **FAIL — off.** Upstream v0.26.0's DFlash path loses half the throughput; the GB10-tuned DFlash lives in community forks (aeon/DSpark), not the pinned mainline image |
| Nemotron-3-Super | mtp k=1 (draft moe_backend=triton — global marlin pin breaks the unquantized MTP head) | **97.5%** (2536/2602) | **12.75 vs 16.20 c=1 (-21%); 9.29 vs 11.98 c=4 (-22%)** | Yes — FP-EQUIVALENT, 0 hard disagreements | **LOSSLESS BUT SLOWER — stays off.** The BF16 MTP layer (~5.5 GB) costs more bandwidth per draft than a 97% acceptance saves at k=1 on a 240 GB/s machine |
| Nemotron-3-Nano | mtp k=1 | | | | not run — same head design as Super at 6x smaller base model; expected worse ratio, low priority |
| any model | EAGLE-3 | | | | **N/A — no trained EAGLE-3 heads exist locally for any target model** (checked catalog + HF caches); PROMPT scoping says only run where heads exist |

**Spec-decode bottom line for GB10 on the pinned stacks:** every method that
ran was lossless (FP-equivalent) except ngram-on-GDN (corrupted), and every
one was SLOWER: Super MTP -21%, DeepSeek MTP -36%, Laguna DFlash -51%.
The intuition "decode is bandwidth-bound so speculation should win" fails
here because the draft work is also bandwidth-bound on the same LPDDR5X, and
2-node verify adds cross-node syncs. The only known-win on this hardware is
the specialized DSpark fork for DeepSeek (prior art, different image, own
maintenance burden). Everything ships spec-decode OFF; `--spec-decode` exists
only where a config carries validated args (currently: none).
| DeepSeek-V4-Flash 2-node | mtp k=2 (the prior production flag set) | 69.3% (3619/5222) | **17.34 vs 27.02 c=1 (-36%)**; worse at c=2/4 | Yes — FP-EQUIVALENT | **LOSSLESS BUT SLOWER — stays off.** Draft layer + extra cross-node syncs per verify step cost more than 69% acceptance at k=2 returns. (The ~50 tok/s prior-art number came from the specialized DSpark fork/stack, not this image's generic MTP path.) |

## Soaks

| Config | Duration | Errors | Mem growth | Thermal | Status |
|---|---|---|---|---|---|
| Flagship 2-node (deepseek-v4-flash) | multi-hour target | | | | PENDING |
| Primary 1-node | multi-hour target | | | | PENDING |
| Rest of matrix | 15-30 min smoke each | | | | PENDING |

## Failures & findings log

- 2026-07-27: HF caches missing `refs/main` broke offline loading twice
  (TROUBLESHOOTING.md#localentrynotfounderror). Fixed cluster-wide.
- 2026-07-27: node 2 has no internet route; weights/images must be staged
  from node 1 (TROUBLESHOOTING.md).
