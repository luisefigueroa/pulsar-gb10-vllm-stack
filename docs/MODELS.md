# Model support matrix — released specs

A profile is a released spec: the `releases/<spec_id>.json` file that fixes the
exact model identity (Hugging Face `model_id@commit` with its snapshot
manifest), the exact serving recipe (engine arguments, container environment),
the runtime image digest, and the hardware geometry
([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md), Stage 4
landed 2026-09-04). Operators pass the spec id to every lifecycle command:
`./pulsar start <spec_id>`, `./pulsar status <spec_id>`, `./pulsar stop
<spec_id>`, `scripts/model-library.sh prepare <spec_id> --yes`.
`scripts/release.sh list` prints the same rows in the terminal and
`scripts/release.sh show <spec_id>` prints one spec. The served model name and
port come from the deployment overlay (`.pulsar-overlay.json`), never from the
spec.

`Review` is the spec's ADR 0017 `review.status` (`stable`, `validated`,
`failed`, `withdrawn`). It is display-only: it never grants or denies a launch,
and catalog, wizard, and start output show it only while the live launch
contract (argv, container environment, image digest, geometry) still matches
the released spec. Promotion happens through one reviewed pull request per spec
(`scripts/release-spec.sh promote`); evidence lives under
`results/baseline-v1/<spec_id>/` and in the [VALIDATION.md](./VALIDATION.md)
ledger. The ADR 0004 registry under `models/model-serving-releases/` is empty
and no row carries a Model Serving Release decision.

The table below is generated. Regenerate it with
`scripts/release.sh list --markdown` and paste the output between the markers;
`scripts/selftest.sh` fails when it drifts from `releases/`.

<!-- BEGIN generated: scripts/release.sh list --markdown -->
| Spec id (profile) | Model | Nodes | Image digest | Review |
|---|---|---|---|---|
| `26597c10902f33592414708c914b1b8bae38e66880debd04ee43f2319140bfca` | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 | 1 | `ffb2d59b1c05` | failed |
| `de2e93cec0fc2aa9064235afa82a12c6844f29fe9d84564a25fd587dbf891cdc` | nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 | 1 | `1c8e60a0841b` | stable since 2026-09-04T12:16:35Z |
<!-- END generated -->

Budget arithmetic: 121 GiB unified per node; with `--gpu-memory-utilization`
0.80-0.85 and OS overhead, plan on **~100-105 GiB usable per node** for
weights + KV, **~200-210 GiB across both**. "Active GB/tok" drives the decode
roofline: `~240 GB/s / active-bytes-per-token` (docs/HARDWARE.md).

Lifecycle scripts may discover more than two GB10 nodes. This table does not
promote a larger geometry: every serveable row is an exact spec, and no
two- or three-node spec is released today. Statements such as "3 nodes would
fit" are weight arithmetic only, not correctness, stability, context, or
performance validation.

The model library is the only weight-distribution mechanism
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)).
For multi-rank specs, ADR 0003 fixes non-home transfer to topology-bound
eight-stream SSH-over-RoCE with no fallback. This is a distribution policy,
not a model-support or release claim; it does not change any review status.

## Retired conf profiles (history, not startable)

Stage 4 deleted every `models/*.conf` profile. A conf-format file survives
only as a lab draft (`scripts/release-spec.sh from-draft <draft.conf>` and
`scripts/model-library.sh home add --draft <draft.conf>`); nothing in the
stack starts a draft. The measurements below were made under those retired
profiles and their legacy `STATUS` labels; they remain history in
[VALIDATION.md](./VALIDATION.md) and do not transfer to any spec. Onboard a
model again through a draft and a baseline run to release it.

| Retired profile | Model | Quant | Disk | Nodes | Recorded result |
|---|---|---|---|---|---|
| nemotron-3-nano-30b-nvfp4 | nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4 | NVFP4 | 19 GB | 1 | superseded by the released spec `26597c10…` (review `failed`: strict same-boot not met on image `ffb2d59b…`); the old "run-to-run IDENTICAL" claim is withdrawn |
| nemotron-3.5-lightning-30b-nvfp4 | nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 | NVFP4 | 22 GB | 1 | superseded by the released spec `de2e93ce…` (review `stable`) |
| nemotron-3-super-120b-nvfp4 | nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 | NVFP4 | 75 GB | 1 | 16.2 tok/s c=1, 113 agg c=32, gsm8k 0.94, 32,768 ctx tested; MTP k=1 opt-in +47% (triton draft) |
| qwen3.6-27b-fp8 | Qwen/Qwen3.6-27B-FP8 (hybrid: 16 full-attn + 48 GDN layers) | FP8 block | 29 GB | 1 | 8.0 tok/s c=1, 93 agg c=16, gsm8k 0.615, needle 3/3 @121K (ledger only); ngram speculative decode corrupts output |
| qwen3.6-27b-fp8-2node | same, TP=2 cross-node | FP8 | 29 GB | 2 | do not use: GDN hybrids hang cross-node (VALIDATION.md) |
| qwen3.8-27b-fp8 | Qwen/Qwen3.8-27B-FP8 | FP8 | 29 GB | 1 | untested recipe shell |
| qwen3.8-27b-fp8-2node | same, TP=2 cross-node | FP8 | 29 GB | 2 | untested recipe shell |
| qwen3-1.7b-2node | Qwen/Qwen3-1.7B, TP=2 cross-node | BF16 | 4 GB | 2 | untested diagnostic recipe |
| laguna-s-2.1-nvfp4 (removed by ADR 0006) | poolside/Laguna-S-2.1-NVFP4 | NVFP4 + FP8 KV | 72 GB | 1 | 19.5 tok/s c=1, 66 agg c=4, gsm8k 0.82 strict, needle 3/3 @261K (ledger only); DFlash marginal +13% |
| laguna-s-2.1-2node (removed by ADR 0006) | Laguna TP=2 cross-node | NVFP4 | 72 GB | 2 | do not use: stock graphs hang without `--enforce-eager` |
| inkling-small-nvfp4 (removed by ADR 0006) | Thinkingmachines/Inkling-Small-NVFP4 | NVFP4 | 171 GB | 2 | blocked upstream: FA4-cute sm12x lacks paged KV (VALIDATION probe series) |
| (candidate, never configured) | nvidia/MiniMax-M2.7-NVFP4 | NVFP4 | 130 GB | 2 | not configured |
| (candidate, never configured) | Qwen3.6-35B-A3B MXFP4/FP8 | MXFP4 | 21 GB | 1 | not configured |
| (candidate, never configured) | cyankiwi/GLM-4.7-Flash-AWQ-4bit | AWQ int4 | ~18 GB | 1 | not configured |

**Model-content identity:** live serving identity is the released spec's
snapshot manifest, occupancy, rank-local verified views, and download receipts
for brand-new homes. Live-mount serving is retired (ADR 0005) and the
replicated path was removed (ADR 0006). Do not invent a lab expected-identity
file from a user cache; onboard the exact content through the receipt workflow
and a baseline run. Library preparation full-verifies before creating
rank-local serve witnesses. Unchanged launch uses the applicable metadata fast
path, while drift rehashes.

Catalog/artifact or serving-integration evidence does not extend a model
qualification or review claim; combined release claims require every
applicable scope ([ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md)).
See [MODEL_RELEASE.md](./MODEL_RELEASE.md),
[MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md),
[ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md),
[ADR 0003](./decisions/0003-explicit-model-preparation-transport.md), and
[ADR 0004](./decisions/0004-model-serving-release-validation.md).

## Does not fit any released one- or two-node spec

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
