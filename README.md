# vLLM on 2x NVIDIA DGX Spark (GB10) — serving stack

Production serving stack for this two-node Grace-Blackwell GB10 cluster
(dgx-spark-1 head + dgx-spark-2 worker; 121 GiB unified LPDDR5X each,
dual-rail 200GbE RoCE between them). Built and validated 2026-07-27..30
against PROMPT.md; every claim below traces to a measured run in
`docs/VALIDATION.md` with raw evidence in `results/`.

Priority order everywhere: **stability > accuracy > throughput > latency.**

## Quick start

```bash
./serve.sh --list                        # what can I serve?
./serve.sh laguna-s-2.1-nvfp4 -d         # primary single-node model on :8000
cluster/preflight.sh deepseek-v4-flash   # check both nodes
cluster/start-cluster.sh deepseek-v4-flash   # 2-node flagship (TP=2)
cluster/stop-cluster.sh                  # ALWAYS before relaunching
```

All servers speak the OpenAI API on :8000. Per-model validated flags live in
`models/<name>.conf` — serving is one command, never twenty flags.

## Images: what runs, and what was patched

| Image | What it is | Serves |
|---|---|---|
| `vllm/vllm-openai:v0.26.0` (digest-pinned in `Dockerfile`) | Official multi-arch release — first arm64/CUDA-13 tag with native sm_121 kernels (12.0f family). No source build needed for these models (`docs/BUILD.md` has the decision record). | Everything except DeepSeek-V4 |
| `vllm-gb10:pr41834-d64074e6f` | **Local source build of vLLM PR #41834 HEAD** (see below) | DeepSeek-V4-Flash flagship (promoted 2026-07-30) |
| `aidendle94/sparkrun-vllm-ds4-gb10:production-ready` | Community binary, fully validated here | Flagship fallback only (`deepseek-v4-flash-sparkrun.conf`) |

### The PR #41834 build (the only "patch" in the stack)

Stock release images **cannot** serve DeepSeek-V4 on GB10 — the
`FLASHINFER_MLA_SPARSE_SM120` attention kernel livelocks under prefill load
(upstream vllm#49026; reproduced here, probe series in VALIDATION.md). The
fix is not merged upstream, so the flagship image is a source build of the
**head of vLLM PR #41834** ("DeepSeek-V4-Flash on SM12x", pinned at commit
`d64074e6f`) — built as the PR tree, not cherry-picked, because it is the
community-validated lineage (188 commits) and includes:

- working DSA/sparse-MLA attention for sm_121 (Triton path)
- the long-context cooperative top-k shared-memory fix (`topk.cu`)
- a fused DeepSeek-V4 qnorm+rope+KV-insert kernel
- **GB10-specific tuned MoE/GEMM configs** (`device_name=NVIDIA_GB10` JSONs)
- DSpark drafter rejection-sampling fixes

Build: `torch_cuda_arch_list='12.0'` (12.0f family = native sm_121 under
CUDA 13.0.3), 1 h 40 min on the 20-core Grace. Full recipe in
`docs/BUILD.md`. When PR #41834 merges upstream this collapses to a stock
pin bump + revalidation.

## Optimizations applied (all measured on THIS cluster)

- **NCCL**: dual-rail RoCE (`NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0`, +47%
  large-message bandwidth) + `NCCL_IB_QPS_PER_CONNECTION=4` (+9% at ≥256 MB),
  bootstrap pinned off the mgmt NIC. MTU stays 1500 — jumbo measured ≤+1.5%
  (PCIe Gen5 x4 is the real ceiling, ~21 GB/s).
- **CUDA graphs ON everywhere they are stable** (worth ~2x at low
  concurrency); the one exception is cross-node TP=2 on the *official*
  image, which requires `--enforce-eager` (root-caused graph-path hang).
- **`--moe-backend marlin` for all NVFP4 MoE** — CUTLASS FP4 MoE silently
  produces wrong output on sm_121.
- **FP8 checkpoints justified by control**: Qwen3.6-27B FP8 vs BF16 gsm8k
  0.615 vs 0.610 — quantization is free here.
- **Deliberately OFF, by measurement** (not vibes):
  - *Speculative decoding — every method, every model*: ngram corrupts GDN
    hybrids; Nemotron-Super MTP **-21%** at 97.5% acceptance; DeepSeek MTP
    **-36%**; Laguna DFlash **-51%**; upstream DSpark **-47%** at 81%
    acceptance (draft CUDA graphs verified engaged, no change). Drafts
    compete for the same 240 GB/s the target needs.
  - `VLLM_MARLIN_USE_ATOMIC_ADD` (perf-neutral), MTU 9000, Ray (native
    `--nnodes` mp backend is the validated multi-node path).

## Models tested

| Config | c=1 tok/s (% of roofline) | Aggregate | gsm8k strict | Needle | Soak |
|---|---|---|---|---|---|
| **deepseek-v4-flash** (2-node TP=2, PR-41834) | **27.15** (68%) | 105 @ c=8 | 0.945 | 3/3 @ **447K** | **150 min, 3318 req, 0 err** (+27 h uptime) |
| laguna-s-2.1-nvfp4 (1-node, NFS catalog) | 19.5 (79%) | 66 @ c=4 | 0.820 | 3/3 @ 261K | 150 min, 1873 req, 0 err |
| nemotron-3-super-120b-nvfp4 | 16.2 (85%) | 113 @ c=32 | 0.940 | — | 20 min clean |
| nemotron-3-nano-30b-nvfp4 | 61.9 (86%) | 399 @ c=16 | 0.830 | 3/3 @ 124K | 15 min clean |
| qwen3.6-27b-fp8 (GDN hybrid, 1-node only) | 8.0 (94%) | 93 @ c=16 | 0.615 | 3/3 @ 121K | 20 min clean |
| deepseek-v4-flash-sparkrun (fallback) | 27.02 | 109 @ c=8 | 0.970 | 3/3 @ 447K | 150 min, 0 err |

Roofline = 240 GB/s measured bandwidth / active-bytes-per-token; it predicts
within 6–21% for every model. The big catalog models (V4-Pro 865 GB,
Kimi-k3 1.5 TB, GLM-5.2 1.5 TB, Inkling 1.9 TB…) **do not fit two nodes** —
arithmetic in `docs/MODELS.md`.

## Validation summary

Full ledger: `docs/VALIDATION.md`. Gates passed: correctness vs HF
transformers (FP-equivalent), quantization control (FP8=BF16), determinism
(bit-exact same-boot; cross-node bit-identity via `VLLM_BATCH_INVARIANT=1`
for standard-attention models; per-boot compile nondeterminism root-caused),
1-vs-2-node parity (gsm8k 0.820 vs 0.825), long context by needle at each
claimed length, node-loss behavior characterized, and soaks (zero errors,
no leaks, no thermal throttling anywhere).

**Failures found and documented, not papered over:**
- Cross-node TP=2 + CUDA graphs hangs on official images (resolves the
  prior repo's multi-day unsolved bug; `--enforce-eager` is the workaround).
- GDN hybrids (e.g. Qwen3.6-27B) break three ways: cross-node TP (wrong
  output then hang), ngram spec decode (corrupted output), batch-invariant
  mode (refuses to start). Single-node plain serving is perfect.
- DeepSeek-V4 on stock images: kernel livelock under prefill pressure.
- `/health` lies for ~5 min after a node loss — monitor 2-node deployments
  with a real 1-token completion, never the health endpoint alone.
- lm-eval client-side tokenization + broken tokenizer regex = falsely
  catastrophic scores (`tokenized_requests=False` fixes).

## Upstream tracking

- **vllm PR #41834** — flagship image lineage. On merge: retire the local
  build for a stock pin, rerun the validation gates.
- **vllm #49026 / #46253** — the two stock-image blockers we reproduced.
- Remaining DSpark opportunity: port the fork's cross-node draft
  optimizations (draft-local argmax, markov W1 replication) onto PR #41834 —
  the fork's +85% single-stream win at *worse* acceptance says the headroom
  is real. The patched fork source trees are preserved in the
  `vllm-spark-dspark-sm121-source:*` images.

## Layout

| Path | What |
|---|---|
| `models/*.conf` | one validated flag set per model; statuses earned by runs |
| `cluster/` | 2-node launch/preflight/teardown + measured NCCL env |
| `validate/` | capture/compare (IDENTICAL / FP-EQUIVALENT / DIVERGENT verdicts), needle, bench (per-level warmup), soak |
| `results/` | raw evidence for every number (`results/README.md` is the map) |
| `bench/` | Step 0 microbenchmarks (membw, NCCL sweeps) |
| `docs/` | HARDWARE, MODELS, **RECIPES** (flag-exact), MULTINODE, BUILD, TUNING, VALIDATION, **REVALIDATE** (pin-bump runbook), **OPERATIONS** (on-call runbook), TROUBLESHOOTING |
| `.claude/skills/knowledge-capture` | capture discipline for the shared KB |

Durable cross-repo knowledge lives in the shared OKF bundle at
`/mnt/Models/knowledge` (39 concepts; query with
`python3 /mnt/Models/knowledge/kb.py query --tag <facet>`). Check it
**before** investigating anything on this cluster.
