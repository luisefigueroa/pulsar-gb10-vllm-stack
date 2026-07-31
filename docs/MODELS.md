# Model support matrix — 2x DGX Spark GB10 (2026-07-27)

Budget arithmetic: 121 GiB unified per node; with `--gpu-memory-utilization`
0.80-0.85 and OS overhead, plan on **~100-105 GiB usable per node** for
weights + KV, **~200-210 GiB across both**. "Active GB/tok" drives the decode
roofline: `~240 GB/s / active-bytes-per-token` (docs/HARDWARE.md).

## Serveable on this cluster

| Config name (`models/*.conf`) | Model | Quant | Disk | Nodes / parallel | Max ctx (validated) | Spec decode | Status |
|---|---|---|---|---|---|---|---|
| `qwen3-1.7b` | Qwen/Qwen3-1.7B | BF16 | 3.4 GB | 1 | 32K | — | **tested** (canary) |
| `qwen3-1.7b-2node` | same, TP=2 cross-node | BF16 | 3.4 GB | 2 / TP=2 | 32K | — | **tested** (plumbing canary) |
| `qwen3.6-27b-fp8-2node` | 27B split TP=2 cross-node | FP8 | 29 GB | 2 / TP=2 | — | — | **DO NOT USE** — GDN hybrids hang cross-node (VALIDATION.md) |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 (hybrid: 16 full-attn + 48 GDN layers) | FP8 block | 29 GB | 1 | 131,072 (needle 3/3 @121K) | ngram (opt-in) | **tested** |
| `nemotron-3-nano-30b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 | NVFP4 | 19 GB | 1 | 131,072 claimed (needle pending) | MTP (opt-in, unvalidated) | **tested** — 62 tok/s c=1, 399 agg c=16, run-to-run IDENTICAL |
| `nemotron-3-super-120b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | NVFP4 | 75 GB | 1 | 32,768 tested (config allows 262K, untested) | MTP validated lossless but **-21% -> off** | **tested** — 16.2 tok/s c=1, 113 agg c=32, IDENTICAL determinism, gsm8k 0.94 |
| `laguna-s-2.1-nvfp4` | poolside/Laguna-S-2.1-NVFP4 (NFS catalog) | NVFP4 + FP8 KV | 72 GB | 1 | **262,144 tested** (needle 3/3 @261K) | DFlash **failed (-51%) -> disabled** | **tested** — 19.5 tok/s c=1, 66 agg c=4, gsm8k 0.82 strict |
| `deepseek-v4-flash` | deepseek-ai/DeepSeek-V4-Flash on **upstream-lineage image** (vllm-gb10:pr41834, source-built PR #41834) | FP8+FP4 experts | 160 GB | **2 / TP=2** | **447K tested** (needle 3/3; 500K configured) | dspark/MTP measured slower -> off | **tested+soaked** — 27.15 tok/s c=1, 105 agg c=8, gsm8k 0.945, 150-min soak 0 errors |
| `deepseek-v4-flash-sparkrun` | same model, community sparkrun binary | FP8+FP4 | 160 GB | 2 / TP=2 | 447K tested | MTP -36% -> off | fallback (fully validated; superseded) |
| `deepseek-v4-flash-0731` | DeepSeek-V4-Flash-0731 (drafter built in) | FP8+FP4 | 167 GB | **2 / TP=2** | 447K tested | DSpark 46.6% acc, parity perf | **tested** — full parity with incumbent; swap pending soak |
| `laguna-s-2.1-2node` | Laguna TP=2 cross-node | NVFP4 | 72 GB | 2 / TP=2 | 262,144 | — | parity/measurement config; **requires --enforce-eager** (VALIDATION.md), prefer 1-node |
| (candidate) | nvidia/MiniMax-M2.7-NVFP4 (node2 cache only) | NVFP4 | 130 GB | 2 / TP=2 | — | — | not configured |
| (candidate) | Qwen3.6-35B-A3B MXFP4/FP8 | MXFP4 | 21 GB | 1 | — | MTP head exists | not configured |
| (candidate) | cyankiwi/GLM-4.7-Flash-AWQ-4bit (node1) | AWQ int4 | ~18 GB | 1 | 202,752 (prior sparkrun profile) | — | not configured |

"Status" is updated by validation runs only (docs/VALIDATION.md holds the
numbers). Nothing gets `tested` from arithmetic.

## Explicitly does NOT fit (the answer to "can we serve the big ones?")

Weights alone vs ~210 GiB total budget — these are not close, and no
quantized variants exist in the catalog:

| Model (NFS catalog) | Params | Quant on disk | Disk GB | Verdict |
|---|---|---|---|---|
| deepseek-ai/DeepSeek-V4-Pro | 1.6T / A49B | FP8+FP4 | **865** | needs ~5x this cluster |
| Moonshotai/Kimi-k3 | 2.8T / A104B | MXFP4 | **1561** | needs ~8x |
| Thinkingmachines/Inkling | 975B / A41B | BF16 | **1905** | needs ~10x (no quant shipped) |
| zai-org/GLM-5.2 | ~753B | BF16 | **1507** | needs ~8x (FP8 variant exists upstream, not local) |
| Moonshotai/Kimi-k2.6 / k2.7-Code | 1T / A32B | int4 experts | **595** each | needs ~3x |
| upstage/Solar-Open2-250B | 250B / A15B | BF16 | **501** | needs ~2.5x |
| NVIDIA/Nemotron-3-Ultra-550B-A55B | 550B / A55B | NVFP4 | **352** | ~1.7x over; 3 nodes would fit |
| XiaomiMimo/MiMo-V2.5 | 310B / A15B | FP8 | **315** | ~1.5x over |
| MiniMaxAI/MiniMax-M3 | 428B / A23B | BF16 / NVFP4 | 869 / **250** | NVFP4 is 40+ GiB over the 2-node budget once KV+overhead counted |
| NVIDIA/Nemotron-3-Super-120B **BF16** | 120B / A12B | BF16 | **247** | over budget as BF16 — use the NVFP4 build (fits ONE node) |
| poolside/Laguna-S-2.1 **BF16** | 118B / A8B | BF16 | **235** | weights would consume both nodes entirely, zero KV — use NVFP4 (fits ONE node) |

Near-misses stay unserveable honestly: a config that loads with 2 GiB of KV
headroom is not a deployment.

## Notes per family (what the user asked about)

- **DeepSeek**: "DeepSeek V4 Flash" is real — `deepseek-ai/DeepSeek-V4-Flash`
  (284B total / 13B active, 1M-token config, FP8 + FP4 experts, MTP head,
  released 2026-04-22 alongside V4-Pro). Weights are in the HF cache on both
  nodes (160 GB), NOT in the NFS catalog (catalog has V4-Pro only). It is the
  cluster's 2-node flagship. The `-DSpark` variant (167 GB, also cached) adds
  the DSpark draft for speculative decoding.
  Since 2026-07-30 the flagship runs on the upstream-lineage source build
  of vLLM PR #41834 (stock release images remain non-viable: kernel-level
  livelock, VALIDATION.md); the community sparkrun binary is the documented
  fallback (`deepseek-v4-flash-sparkrun.conf`).
- **Qwen**: newest local are Qwen3.6-27B (hybrid GDN; FP8 and NVFP4 variants
  cached with full weights — the "BF16" cache entry was an empty stub,
  downloaded fresh 2026-07-28 for the quant-control eval) and
  Qwen3.6-35B-A3B (MoE). No Qwen weights in the NFS catalog.
- **Llama**: nothing newer than Llama 4 exists (mid-2026); no Llama weights
  present anywhere on this cluster — not in the matrix. Llama-4-Scout would
  fit 1 node quantized if ever needed.
- **GPT-OSS**: no raw weights on disk; only NIM containers (gpt-oss-120b).
  gpt-oss-120b MXFP4 (~63 GB) would fit one node under vLLM if downloaded;
  known sm_121 caveats (vllm#37030 first-token Marlin bug). Left out of the
  matrix until weights exist locally.
- **KV cache reference** (BF16 bytes/token, from config.json): Laguna 24 KiB
  (FP8, growth portion) · DeepSeek-V4 MLA ~69 KiB pre-compression · Qwen3.6-27B
  dense GQA ~previous-gen typical · Nemotron-3 hybrids 6-8 KiB (+fixed Mamba
  state). Long-context feasibility is therefore model-specific; the needle
  test in VALIDATION.md is the gate for any claimed context length.
