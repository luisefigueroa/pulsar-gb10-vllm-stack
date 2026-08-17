# Model support matrix — current legacy tested profiles (2026-08-15)

This table reports the implementation's existing `STATUS=tested*`, reviewed
seal, and `legacy-unsealed` contracts. It does **not** assign the Model Serving
Release statuses accepted in
[ADR 0004](./decisions/0004-model-serving-release-validation.md). In
particular, no row is automatically `Validated`: that status belongs to one
**Model Serving Release**, the immutable combination of exact model identity,
exact serving recipe, runtime/image identity, and supported hardware geometry,
after every frozen criterion and review requirement passes. Changing any one
component creates a new release. Pure version-1 schema validation and
read-only verification of stored ADR 0004 objects exist. Catalog, wizard, and
start output can project a reviewed decision only when a profile explicitly
binds its exact release through `MODEL_SERVING_RELEASE_ID`. No current profile
is bound and the tracked store is empty, so current rows display the neutral
`No release binding` state. Local evidence-capture candidate persistence is
unreviewed and does not change this table. Maintainer-only issuance staging
can propose registry objects, but a local command is not trusted until
repository review and merge; serving permission is status-independent and no
current row is silently relabeled.

Budget arithmetic: 121 GiB unified per node; with `--gpu-memory-utilization`
0.80-0.85 and OS overhead, plan on **~100-105 GiB usable per node** for
weights + KV, **~200-210 GiB across both**. "Active GB/tok" drives the decode
roofline: `~240 GB/s / active-bytes-per-token` (docs/HARDWARE.md).

The control plane may discover more than two GB10 nodes. This table does not
promote a larger geometry: every serveable row is an exact profile, and no
three-node profile currently has `STATUS=tested*`. Statements such as “3 nodes
would fit” are weight arithmetic only, not correctness, stability, context, or
performance validation.

For reviewed profiles whose existing durable home is prepared through the
explicit experimental model-library action, ADR 0003 fixes non-home transfer
to topology-bound eight-stream SSH-over-RoCE with no fallback. This is a
distribution policy, not a model-support or release claim; it does not change
any status in the table or the replicated guided default.

## Exact profiles and recorded candidates

| Config name (`models/*.conf`) | Model | Quant | Disk | Nodes / parallel | Max ctx (validated) | Spec decode | Status |
|---|---|---|---|---|---|---|---|
| `qwen3-1.7b` | Qwen/Qwen3-1.7B | BF16 | 4 GB (~3.8 GiB sealed) | 1 | 32K | — | **tested diagnostic canary; lab-sealed exact identity** — hidden from serving wizard |
| `qwen3-1.7b-2node` | same, TP=2 cross-node | BF16 | 4 GB | 2 / TP=2 | 32K | — | **tested diagnostic canary** — hidden from serving wizard |
| `qwen3.6-27b-fp8-2node` | 27B split TP=2 cross-node | FP8 | 29 GB | 2 / TP=2 | — | — | **DO NOT USE** — GDN hybrids hang cross-node (VALIDATION.md) |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 (hybrid: 16 full-attn + 48 GDN layers) | FP8 block | 29 GB | 1 | 131,072 (needle 3/3 @121K; ledger only — no `results/` artifact) | ngram **FORBIDDEN** (corrupts) | **tested** |
| `nemotron-3-nano-30b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 | NVFP4 | 19 GB | 1 | 131,072 (needle 3/3 @124K; ledger only — no `results/` artifact) | MTP not offered | **tested** — 62 tok/s c=1, 399 agg c=16, run-to-run IDENTICAL |
| `nemotron-3-super-120b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | NVFP4 | 75 GB | 1 | 32,768 tested (config allows 262K, untested) | MTP k=1 **opt-in +47%** (`--spec-decode`; triton draft) | **tested** — 16.2 tok/s c=1 base, 113 agg c=32, gsm8k 0.94 |
| `laguna-s-2.1-nvfp4` | poolside/Laguna-S-2.1-NVFP4 (NFS catalog) | NVFP4 + FP8 KV | 72 GB | 1 | **262,144 tested** (needle 3/3 @261K; ledger only — no `results/` artifact) | DFlash **marginal +13%** (off by default) | **tested** — 19.5 tok/s c=1, 66 agg c=4, gsm8k 0.82 strict |
| `deepseek-v4-flash` | deepseek-ai/DeepSeek-V4-Flash-**0731** (integrated DSpark drafter) on **published, digest-pinned PR-41834 image** | FP8+FP4 experts | 167 GB | **2 / TP=2** | **500K served** (client cap; **20 GB/rank KV → 652,465 tok, 1.30x @500K**; `max-num-seqs 5`; 20 GB needle 3/3 @447K in `results/needle-dsv4-20gb-447k.log`; prior soaked 10 GB→577k) | **DSpark default-on; k=5 checkpoint-fixed** (`--no-spec-decode` rollback; **0731 benches** 43–48 tok/s c=1 — no 20 GB throughput re-run) | **`STATUS=tested`** (20 GB soak in VALIDATION.md); **lab-sealed exact identity** — flagship; **2026-08-01 defaults retuned for few long agent sessions** (see conf header + DeepSeek notes) |
| `inkling-small-nvfp4` | Thinkingmachines/Inkling-Small-NVFP4 (NFS catalog, added 07-31) | NVFP4 | 171 GB | 2 / TP=2 (would) | 131,072 in conf (checkpoint 1M; needle-gate first) | MTP-8 head ships | **BLOCKED upstream** — FA4-cute sm12x lacks paged KV (VALIDATION probe series) |
| `laguna-s-2.1-2node` | Laguna TP=2 cross-node | NVFP4 | 72 GB | 2 / TP=2 | 262,144 | — | **do-not-use** — advisory measurement-only label; stock graphs hang without `--enforce-eager` (baked in conf). Prefer 1-node `laguna-s-2.1-nvfp4` |
| (candidate) | nvidia/MiniMax-M2.7-NVFP4 (node2 cache only) | NVFP4 | 130 GB | 2 / TP=2 | — | — | not configured |
| (candidate) | Qwen3.6-35B-A3B MXFP4/FP8 | MXFP4 | 21 GB | 1 | — | MTP head exists | not configured |
| (candidate) | cyankiwi/GLM-4.7-Flash-AWQ-4bit (node1) | AWQ int4 | ~18 GB | 1 | 202,752 (prior lab profile) | — | not configured |

"Status" is updated by validation runs only (docs/VALIDATION.md holds the
numbers). Nothing gets `tested` from arithmetic.

**Model-content identity transition:** these rows are historical profile
validation claims except for the issued `qwen3-1.7b` and
`deepseek-v4-flash` rows. The diagnostic Qwen claim binds exact commit
`70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, manifest
`775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`,
digest-pinned image, normalized one-node profile, and repository evidence.
The flagship claim binds commit
`7872f01b1d1fe23eabc4c98b48bffcef5a386062`, manifest
`27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`,
the digest-pinned PR-41834 image, normalized two-node profile, and reviewed
evidence. Profiles without seals—including `qwen3-1.7b-2node`—remain
`legacy-unsealed`. Replicated download/readiness/launch enforces the
reviewed seal for profiles that have one; unsealed replicated profiles and all
current live-mount launches remain mutable and are not content-bound.
`STATUS=tested*` must not be interpreted as validating arbitrary bytes under
the same repository ID. Do not generate an expected seal from a user cache;
recover the lab artifact used for the run or revalidate the exact content.
`library-hot` preparation and sealed replicated acquisition both full-verify
before creating their distinct rank-local serve witnesses. Unchanged launch
uses the applicable metadata fast path, while drift rehashes. Those mechanisms
preserve an established identity but cannot turn legacy rows into lab-sealed
claims.

Maintainers can now assemble deterministic unreviewed candidates with
`scripts/model-release.sh`, but candidate generation alone does not change any
row or issue a claim. See [MODEL_RELEASE.md](./MODEL_RELEASE.md),
[models/seals/README.md](../models/seals/README.md),
[models/validation-bundles/README.md](../models/validation-bundles/README.md),
[MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md),
[ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md),
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md),
[ADR 0003](./decisions/0003-explicit-model-preparation-transport.md), and
[ADR 0004](./decisions/0004-model-serving-release-validation.md).
Catalog/artifact or serving-integration evidence does not extend a model
qualification or `STATUS` claim; combined release claims require every
applicable scope. Stability, accuracy, throughput, latency, and strict
same-boot criteria use `model-qualification`; serving checks use
`serving-integration`; provenance/security and physical-geometry criteria use
`release-promotion`. Catalog/artifact preparation is never a release-validation
criterion. All applicable observations are considered automatically, except
for explicit review-evidence-backed exclusions, and selftests or schema checks
do not substitute for physical DGX evidence.

## Does not fit any current legacy tested serving profile

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

## Notes per family

- **DeepSeek**: "DeepSeek V4 Flash" is real — `deepseek-ai/DeepSeek-V4-Flash`
  (284B total / 13B active, 1M-token config, FP8 + FP4 experts, MTP head,
  released 2026-04-22 alongside V4-Pro). Weights are in the HF cache on both
  nodes, NOT in the NFS catalog (catalog has V4-Pro only). Since 2026-07-31
  the flagship serves the **0731 refresh** (167 GB), which builds the DSpark
  drafter INTO the checkpoint — the old separate `-DSpark` variant and its
  conf are retired (git history), as is the superseded 04-22 checkpoint.
  Since 2026-07-30 the flagship runs on the published, digest-pinned
  upstream-lineage image built from vLLM PR #41834 (stock release images remain
  non-viable: kernel-level livelock, VALIDATION.md). Pull the image named by the
  model conf; [BUILD.md](BUILD.md) retains the source-build fallback for
  offline or independently reproduced deployments.
  **Default geometry (2026-08-01) is optimized for few long agent sessions**,
  not high-QPS short chat: ≤5 concurrent (Hermes + occasional sub-agents),
  long tool/code/repo traces, client context capped at **500K** (official
  useful max; recall drops beyond). Engine defaults in
  `models/deepseek-v4-flash.conf`: `--max-model-len 500000`,
  `--max-num-seqs 5`, `--max-num-batched-tokens 16384`,
  `--kv-cache-memory-bytes 20000000000` (~20 GB/rank; boot-measured
  **652,465-token** pool, 1.30x concurrency at 500K — not linear from the
  old 10 GB figure), plus DeepSeek tool/reasoning parsers for agent clients.
  Do **not** size toward 27.5 GB/rank (known node-2 OOM) or assume old
  “~2M token” profiles from other geometries transfer. With explicit
  `kv_cache_memory_bytes`, twiddling `gpu_memory_utilization` does not
  grow KV. Prior soaked reference remains 10 GB → 577,640 tokens (VALIDATION.md).
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
  **DSV4-0731 caveat: measured bytes/token is GEOMETRY-DEPENDENT** (~55 KB
  effective at 131K max-model-len vs ~18 KB at 500K — length-scaled tail
  compress_ratios). Never carry a per-token KV number across max-model-len
  values; re-measure at the target geometry (VALIDATION.md 500K-KV section).
