# Build & image provenance

## Decision: pinned official image + metadata overlay, no source build

PROMPT.md: *"vLLM built from source only if that offers significant advantages
over official published vllm images for this arch/compute capability."*

It does not, as of 2026-07-27:

- `vllm/vllm-openai:v0.26.0` became **multi-arch on 2026-07-25**: the arm64
  manifest ships CUDA 13.0.x + torch 2.11.0+cu130 with kernels built for the
  `12.0f` CUDA-13 *family* target, which natively covers `sm_121` (GB10).
  Verified empirically on this machine, not from release notes:
  - FP8 block-scaled GEMM: engine selects `CutlassFp8BlockScaledMMKernel`
    (native, no forced-Marlin fallback needed — the cu129-era landmine of
    `sm_120a`-only CUTLASS kernels is gone on the cu130 track).
  - Attention: `FLASH_ATTN` (FlashAttention 2) selected and correct on
    sm_121; FLASHINFER and TRITON_ATTN present as fallbacks.
  - CUDA graphs: FULL_AND_PIECEWISE capture works (greedy outputs identical
    to eager was established for this stack family in prior art; determinism
    numbers for this image in docs/VALIDATION.md).
  - Multi-node: native `--nnodes/--node-rank/--headless` (torch.distributed)
    works cross-node over RoCE — validated with TP=2 (docs/MULTINODE.md).
- A source build on the 20-core Grace CPU takes hours, produces the same
  kernels (the wheel already includes the 12.0f family), and adds a
  maintenance burden. The one thing a source build COULD add today is
  unmerged PRs (e.g. sparse-MLA-on-sm_121, vllm#47629) — not worth carrying
  patches for models covered by the sparkrun image below.

### The pins

| Image | Pin | Role |
|---|---|---|
| `vllm/vllm-openai:v0.26.0` | `sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52` | mainline: every model except DeepSeek-V4 |
| `vllm-gb10:pr41834-d64074e6f` | local source build, vLLM PR #41834 head `d64074e6f` (see build section below) | **DeepSeek-V4-Flash flagship (promoted 2026-07-30)** |
| `aidendle94/sparkrun-vllm-ds4-gb10:production-ready` | image id `b2eb4e6ee5cc` (vLLM `0.21.1rc1.dev339+g1967a5627bc3`, torch 2.11.0+cu130) | fallback for DeepSeek-V4-Flash (superseded by the PR-41834 build) |

Digest-pin discipline: tags are mutable; `Dockerfile` FROMs the digest. When
bumping, re-run the validation suite (docs/VALIDATION.md) before changing the
pin — and `rm -rf ~/.cache/vllm` + the Triton cache on both nodes after any
image change (stale Triton cache is a known silent-corruption source on
sm_121, vllm#41871).

## Building the overlay

```
docker build -t vllm-gb10:v0.26.0 .
# and load it on the worker:
docker save vllm-gb10:v0.26.0 | ssh 10.100.120.2 docker load
```

Build time: **~40 s** (it is an overlay: arch assert + curl + OCI labels; the
base pull is ~11 GB compressed / 17 GB on disk and dominated the first run at
~10 min over this link).

The launch tooling defaults to the upstream tag directly
(`VLLM_IMAGE_MAINLINE` in `.env`), so the overlay is optional; it exists to
give deployments an immutable local name + provenance labels.

## When to revisit a source build

- Sparse-MLA/DSA on sm_121 lands upstream (vllm#45317 fix PRs) -> could
  retire the sparkrun image with a single mainline build.
- FlashAttention adds sm_121 FA3/FA4 paths.
- A model in the matrix requires an unreleased architecture.

If that day comes: `TORCH_CUDA_ARCH_LIST=12.0f` (CUDA >= 13) or `12.1a`
(cu129 track), and beware vllm#49904 (arch auto-detect can produce a
kernel-less sm_121 build).

## Upstream-lineage DeepSeek-V4 build (branch upstream-dsv4-sm121)

Stock v0.26.0 cannot serve DeepSeek-V4 on GB10 (attention-kernel livelock
under load — VALIDATION.md probe series, upstream vllm#49026). The
upstream-track fix is PR #41834 (DeepSeek-V4-Flash on SM12x; open against
main; the tree all working community recipes build from). It includes .cu
changes (topk smem fix, fused DSV4 KV-insert kernel, GB10-tuned MoE/GEMM
configs), so it requires a source build — no overlay shortcut.

Pin: PR head `d64074e6f` ("fix(dsv4): bound the block-table gather...",
188 commits over recent main). Cherry-picking onto the v0.26.0 tag was
rejected: 188 commits of conflicts vs building the community-validated tree
as-is.

```
cd ~/build/vllm-upstream/vllm && git checkout d64074e6f
DOCKER_BUILDKIT=1 docker build --target vllm-openai \
  --build-arg max_jobs=10 --build-arg nvcc_threads=2 \
  --build-arg torch_cuda_arch_list='12.0' \
  -t vllm-gb10:pr41834-d64074e6f -f docker/Dockerfile .
# stage to worker:
docker save vllm-gb10:pr41834-d64074e6f | ssh 10.100.120.2 docker load
```

`torch_cuda_arch_list='12.0'` compiles the 12.0f family target (covers
sm_121 natively under CUDA 13.0.3) plus the 12.0a/12.1a quant-kernel gates.
Build time on the 20-core Grace: **1 h 40 min** (max_jobs=10, single-arch).
Validation gates for this image: models/deepseek-v4-flash-upstream.conf and
the DSpark A/B — this image earns nothing until those pass.
