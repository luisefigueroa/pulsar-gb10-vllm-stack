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
| Laguna-S-2.1-NVFP4 | gsm8k + greedy spot-check | PENDING | |
| DeepSeek-V4-Flash | gsm8k + greedy spot-check (2-node) | PENDING | |

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
| Native --nnodes TP=2 cross-node serves | PASS | Qwen3-1.7B canary, correct greedy output |
| Concurrency >= 2 on 2-node (prior-stack killer) | PASS | 8/8 concurrent correct on canary |
| RDMA (not TCP) transport in vLLM containers | PENDING | verified for bench containers; capture NCCL_DEBUG=INFO on flagship bring-up |
| DeepSeek-V4-Flash TP=2 serves + concurrency | PENDING | |
| Node-loss behavior documented | PENDING | expected: no recovery; verify once |

## Long context

| Model | Claimed | Needle result | Status |
|---|---|---|---|
| Qwen3.6-27B-FP8 | 131,072 (conf) | | PENDING |
| Laguna-S-2.1-NVFP4 | 262,144 (config max) | | PENDING |
| DeepSeek-V4-Flash | 500,000 (prior prod value) | | PENDING |

## Speculative decoding (all off by default)

| Model | Method | Acceptance | tok/s spec vs base | Output unchanged? | Verdict |
|---|---|---|---|---|---|
| Qwen3.6-27B-FP8 | ngram k=4 (FLASH_ATTN) | ~26% (233/904) | n/a | **NO — corrupted**: 3/30 exact, 8 hard disagreements, one output devolves into unrelated garbled text (replacement char + off-topic content). Likely GDN-state rollback breakage under spec verify on sm_121 | **FAIL — do not enable** |
| Qwen3.6-27B-FP8 | ngram k=4 (TRITON_ATTN workaround) | — | n/a | **NO** — still corrupted (2/30 exact, 5 hard disagreements, delta 1.26). Not attention-backend-specific; the GDN hybrid + spec-verify path itself is broken on this stack | **FAIL — ngram unusable on GDN hybrids; SPEC_DECODE_ARGS removed from conf** |
| Laguna-S-2.1-NVFP4 | dflash k=15 (ships in gen_config) | | | | PENDING |
| Nemotron-3-Nano/Super | mtp k=1 | | | | PENDING |
| DeepSeek-V4-Flash | mtp k=2 (prior prod) | | | | PENDING |

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
