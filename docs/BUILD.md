# Build & image provenance

## Decision: pinned published images; source build only as a fallback

Build brief: *"vLLM built from source only if that offers significant advantages
over official published vllm images for this arch/compute capability."*

For most models it does not, as of 2026-07-27:

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
  maintenance burden.

**Exception:** DeepSeek-V4-Flash on GB10 needs **vLLM PR #41834** (source
build below). Stock `v0.26.0` livelocks under multi-node load for this model
(docs/VALIDATION.md probe series).

### Day-1 recommendation

| Goal | Path |
|---|---|
| Qwen / Nemotron / Laguna / small canaries | Pull `vllm/vllm-openai:v0.26.0` — no source build |
| DeepSeek-V4-Flash / Inkling | Pull the digest-pinned published PR-41834 image, then stage it to the worker |

### The pins

| Image | Pin | Role |
|---|---|---|
| `vllm/vllm-openai:v0.26.0` | `sha256:ffb2d59b1c059a5bd8d781320c9f5189de8293693b7d95da54befddaa54abf52` | mainline: Qwen, Nemotron, Laguna, and small canaries |
| `ghcr.io/luisefigueroa/pulsar-gb10-vllm-stack:pr41834-d64074e6f` | `sha256:260c854707e8e6db5001838998e390011b648f127bd42aa8705ad7a808fbe9e2` | **DeepSeek-V4-Flash flagship and Inkling-Small-NVFP4** |

Digest-pin discipline: tags are mutable; `Dockerfile` FROMs the digest. When
bumping, re-run the validation suite (docs/VALIDATION.md) before changing the
pin — and `rm -rf ~/.cache/vllm` + the Triton cache on both nodes after any
image change (stale Triton cache is a known silent-corruption source on
sm_121, vllm#41871).

## Building the overlay (optional metadata wrapper)

```bash
docker build -t vllm-gb10:v0.26.0 .
# and load it on the worker:
docker save vllm-gb10:v0.26.0 | ssh "$WORKER_IP" docker load
```

Build time: **~40 s** (it is an overlay: arch assert + curl + OCI labels; the
base pull is ~11 GB compressed / 17 GB on disk and dominated the first run at
~10 min over this link).

The launch tooling defaults to the upstream tag directly
(`VLLM_IMAGE_MAINLINE` in `.env`), so the overlay is optional; it exists to
give deployments an immutable local name + provenance labels.

## When to revisit a source build

- Sparse-MLA/DSA on sm_121 lands in a mainline release → could retire the
  published PR-41834 image for a digest-pinned stock tag + revalidation.
- FlashAttention adds sm_121 FA3/FA4 paths.
- A model in the matrix requires an unreleased architecture.

If that day comes: `TORCH_CUDA_ARCH_LIST=12.0f` (CUDA >= 13) or `12.1a`
(cu129 track), and beware vllm#49904 (arch auto-detect can produce a
kernel-less sm_121 build).

## Upstream-lineage DeepSeek-V4 image (PR #41834)

The qualified arm64 image is published at:

```text
ghcr.io/luisefigueroa/pulsar-gb10-vllm-stack:pr41834-d64074e6f
sha256:260c854707e8e6db5001838998e390011b648f127bd42aa8705ad7a808fbe9e2
```

Release audit (2026-08-02): Trivy 0.72.0 reported zero secrets, zero
critical findings, and zero unsuppressed fixable high findings after the
OpenSSL refresh. A CycloneDX 1.7 SBOM (1,759 components) is attached to the
image manifest as OCI artifact
`sha256:6917947b95b7a9644278b6f4298dfa6a84acd7c45802b1fc03c64170c7008ef0`.
Its payload SHA-256 is
`e92f3a637ad8cebbbd36de5d541dcaeb81af6671ef60a16cf875d651383d7b6a`.

Model confs pin its immutable registry digest. Pulling is the normal bootstrap
path; the source-build recipe below remains the reproducibility and fallback
path. See [IMAGE-LICENSES.md](./IMAGE-LICENSES.md) for component licensing.
The release wrapper also upgrades Jammy `openssl`/`libssl3` to
`3.0.2-0ubuntu1.26` for CVE-2026-45447; it does not replace qualified vLLM or
CUDA binaries. Scanner suppressions in `images/pr41834.trivyignore` are limited
to vLLM advisories whose upstream fix commits are ancestors of the qualified
PR pin.

### Stranger-reproducible source build

Stock v0.26.0 cannot serve DeepSeek-V4 on GB10 (attention-kernel livelock
under load — VALIDATION.md probe series, upstream vllm#49026). The
upstream-track fix is **[PR #41834](https://github.com/vllm-project/vllm/pull/41834)**
(DeepSeek-V4-Flash on SM12x; open against main). It includes `.cu` changes
(topk smem fix, fused DSV4 KV-insert kernel, GB10-tuned MoE/GEMM configs),
so it requires a **source build** — no overlay shortcut.

**Pin:** PR head `d64074e6f` (`fix(dsv4): bound the block-table gather...`,
188 commits over recent main at the time of qualification). Cherry-picking
onto the v0.26.0 tag was rejected: 188 commits of conflicts vs building the
PR tree as-is.

### From a clean machine

```bash
# 1) Fetch the PR head (no prior checkout assumed)
mkdir -p "$HOME/build" && cd "$HOME/build"
git clone https://github.com/vllm-project/vllm.git vllm-pr41834
cd vllm-pr41834
# Either:
git fetch origin pull/41834/head:pr-41834 && git checkout pr-41834
# Or pin the exact qualified SHA:
git fetch origin d64074e6f && git checkout d64074e6f

# 2) Build the OpenAI image (Grace: ~1h40m with max_jobs=10)
DOCKER_BUILDKIT=1 docker build --target vllm-openai \
  --build-arg max_jobs=10 --build-arg nvcc_threads=2 \
  --build-arg torch_cuda_arch_list='12.0' \
  -t vllm-gb10:pr41834-d64074e6f -f docker/Dockerfile .

# 3) Stage the local fallback to the worker (two-node)
docker save vllm-gb10:pr41834-d64074e6f | ssh "$WORKER_IP" docker load

# 4) For an offline/local fallback, change the selected model conf's IMAGE
#    from the published digest to vllm-gb10:pr41834-d64074e6f.
```

`images/pr41834-release.Dockerfile` is the publisher-side metadata and security
wrapper for the qualified local base. Run it from this stack repository, not
from the upstream vLLM checkout.

`torch_cuda_arch_list='12.0'` compiles the 12.0f family target (covers
sm_121 natively under CUDA 13.0.3) plus the 12.0a/12.1a quant-kernel gates.

**Validation:** this image earns nothing until gates in
`models/deepseek-v4-flash.conf` + docs/VALIDATION.md pass (battery, needle,
soak). Use `cluster/start-cluster.sh deepseek-v4-flash` for the default DSpark
path after the image is loaded on both nodes; `--no-spec-decode` is rollback.

### DEPRECATED: DSpark draft-path overlay (do not build by default)

`patches/pr41834-dspark-opt/` is a **deprecated** pure-Python A/B overlay.
It did not beat stock DSpark under corrected metering and is **obsolete
after vllm #49731** (same draft-head work on main). The flagship pin
`vllm-gb10:pr41834-d64074e6f` does **not** use it. See
`patches/pr41834-dspark-opt/README.md`. Not required for serving; do not
point conf `IMAGE=` at `*-dspark-opt-*` tags.
