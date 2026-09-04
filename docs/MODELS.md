# Model support matrix — current profiles

This table reports the implementation's existing profile `STATUS=tested*` and
live library contracts (local files on every rank). Lab expected-identity files
and the retired combined identity format are not a live product
([ADR 0012](./decisions/0012-retire-expected-seal-and-schema-1-bundles.md)). It does **not** assign the Model Serving
Release statuses accepted in
[ADR 0004](./decisions/0004-model-serving-release-validation.md). In
particular, no row is automatically `Validated`: that status belongs to one
**Model Serving Release**, the immutable combination of exact model identity,
exact serving recipe, runtime/image identity, and supported hardware geometry,
after every frozen criterion and review requirement passes. Changing any one
component creates a new Model Serving Release. Descriptor and Validation
Contract checks and
read-only verification of stored ADR 0004 objects exist. Catalog, wizard, and
start output can show a reviewed decision only when a profile explicitly
sets `MODEL_SERVING_RELEASE_ID`. No current profile sets that field.
Accepted target: one release spec is the contract ([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md)); this section remains the live implementation until that staged cutover.
Local evidence-capture drafts are not in the trusted registry and do
not change this table. Maintainer-only staging can propose registry
objects, but a local command is not trusted until repository review and merge;
serving permission is status-independent and no current row is silently
relabeled.

Budget arithmetic: 121 GiB unified per node; with `--gpu-memory-utilization`
0.80-0.85 and OS overhead, plan on **~100-105 GiB usable per node** for
weights + KV, **~200-210 GiB across both**. "Active GB/tok" drives the decode
roofline: `~240 GB/s / active-bytes-per-token` (docs/HARDWARE.md).

Lifecycle scripts may discover more than two GB10 nodes. This table does not
promote a larger geometry: every serveable row is an exact profile, and no
three-node profile currently has `STATUS=tested*`. Statements such as “3 nodes
would fit” are weight arithmetic only, not correctness, stability, context, or
performance validation.

The model library is the only weight-distribution mechanism
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)).
For multi-rank profiles, ADR 0003 fixes non-home transfer to
topology-bound eight-stream SSH-over-RoCE with no fallback. This is a
distribution policy, not a model-support or release claim; it does not change
any status in the table.

## Exact profiles and recorded candidates

| Config name (`models/*.conf`) | Model | Quant | Disk | Nodes / parallel | Max ctx (validated) | Spec decode | Status |
|---|---|---|---|---|---|---|---|
| `qwen3.8-27b-fp8` | Qwen/Qwen3.8-27B-FP8 | FP8 | 29 GB | 1 | 131,072 configured; context not evaluated | — | draft; legacy **`STATUS=untested`**; no `MODEL_SERVING_RELEASE_ID`; not recommended |
| `qwen3.8-27b-fp8-2node` | same, TP=2 cross-node on official v0.27.1-aarch64 | FP8 | 29 GB | 2 / TP=2 | 131,072 configured; unevaluated | — | draft; legacy **`STATUS=untested`**; no `MODEL_SERVING_RELEASE_ID`; graphs on; no spec decode; not recommended |
| `qwen3-1.7b-2node` | Qwen/Qwen3-1.7B, TP=2 cross-node | BF16 | 4 GB | 2 / TP=2 | 32K configured; unevaluated | — | draft diagnostic recipe; **`STATUS=untested`**; no `MODEL_SERVING_RELEASE_ID`; hidden from serving wizard |
| `qwen3.6-27b-fp8-2node` | 27B split TP=2 cross-node | FP8 | 29 GB | 2 / TP=2 | — | — | **DO NOT USE** — GDN hybrids hang cross-node (VALIDATION.md) |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 (hybrid: 16 full-attn + 48 GDN layers) | FP8 block | 29 GB | 1 | 131,072 (needle 3/3 @121K; ledger only — no `results/` artifact) | ngram **FORBIDDEN** (corrupts) | **tested** |
| `nemotron-3-nano-30b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 | NVFP4 | 19 GB | 1 | 131,072 (needle 3/3 @124K; ledger only — no `results/` artifact) | MTP not offered | **tested** — 62 tok/s c=1, 399 agg c=16, run-to-run IDENTICAL |
| `nemotron-3.5-lightning-30b-nvfp4` | nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 | NVFP4 | 22 GB | 1 | 131,072 configured; NVIDIA publishes 1M, unevaluated by Pulsar | — | released spec `de2e93ce…` (`releases/`), review **`stable`** since 2026-09-04 (baseline-v1: identity, smoke, same-boot, gsm8k 0.93, 60-min soak, perf snapshot; evidence under `results/baseline-v1/`); legacy **`STATUS=untested`** is display-only; no `MODEL_SERVING_RELEASE_ID` |
| `nemotron-3-super-120b-nvfp4` | nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | NVFP4 | 75 GB | 1 | 32,768 tested (config allows 262K, untested) | MTP k=1 **opt-in +47%** (`--spec-decode`; triton draft) | **tested** — 16.2 tok/s c=1 base, 113 agg c=32, gsm8k 0.94 |
| `laguna-s-2.1-nvfp4` — **profile removed by ADR 0006** (absolute-path catalog; measurements remain history) | poolside/Laguna-S-2.1-NVFP4 | NVFP4 + FP8 KV | 72 GB | 1 | **262,144 tested** (needle 3/3 @261K; ledger only — no `results/` artifact) | DFlash **marginal +13%** (off by default) | **tested** — 19.5 tok/s c=1, 66 agg c=4, gsm8k 0.82 strict |
| `inkling-small-nvfp4` — **profile removed by ADR 0006** (absolute-path catalog; probe history remains) | Thinkingmachines/Inkling-Small-NVFP4 (added 07-31) | NVFP4 | 171 GB | 2 / TP=2 (would) | 131,072 in conf (checkpoint 1M; needle-gate first) | MTP-8 head ships | **BLOCKED upstream** — FA4-cute sm12x lacks paged KV (VALIDATION probe series) |
| `laguna-s-2.1-2node` — **profile removed by ADR 0006** | Laguna TP=2 cross-node | NVFP4 | 72 GB | 2 / TP=2 | 262,144 | — | **do-not-use** — advisory measurement-only label; stock graphs hang without `--enforce-eager` (baked in conf). Do not `./pulsar start` it. |
| (candidate) | nvidia/MiniMax-M2.7-NVFP4 (node2 cache only) | NVFP4 | 130 GB | 2 / TP=2 | — | — | not configured |
| (candidate) | Qwen3.6-35B-A3B MXFP4/FP8 | MXFP4 | 21 GB | 1 | — | MTP head exists | not configured |
| (candidate) | cyankiwi/GLM-4.7-Flash-AWQ-4bit (node1) | AWQ int4 | ~18 GB | 1 | 202,752 (prior lab profile) | — | not configured |

"Status" is updated by validation runs only (docs/VALIDATION.md holds the
numbers). Nothing gets `tested` from arithmetic.

**Model-content identity:** live serving identity is occupancy, rank-local
verified views, and download receipts for brand-new homes. ADR 0004 Model
Serving Release objects are display-only, not a serving gate. Retired lab
expected-identity files and their model-specific evidence are not retained in
this reset. Retained draft recipes are unbound and untested. Live-mount serving is retired (ADR 0005)
and the replicated path was removed (ADR 0006). Profile `STATUS=tested*` must
not be interpreted as validating arbitrary bytes under the same repository ID.
Do not invent a lab expected-identity file from a user cache; onboard the exact
content through the current receipt and ADR 0004 workflows. Library
preparation full-verifies before creating rank-local serve witnesses. Unchanged
launch uses the applicable metadata fast path, while drift rehashes.

The ADR 0004 registry is empty. `qwen3.8-27b-fp8` is an unbound draft recipe.

Maintainers can assemble draft Model Serving Release JSON with
`scripts/model-serving-release-plan.sh`, but that alone does not change any
row or issue a claim. See [MODEL_RELEASE.md](./MODEL_RELEASE.md),
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
  (FP8, growth portion) · Qwen3.6-27B dense GQA ~previous-gen typical ·
  Nemotron-3 hybrids 6-8 KiB (+fixed Mamba
  state). Long-context feasibility is therefore model-specific; the needle
  test in VALIDATION.md is the gate for any claimed context length. Never
  carry a per-token KV number across geometries; re-measure at the target
  configuration.
