# vLLM on 2x NVIDIA DGX Spark (GB10) — serving stack

Production tooling for serving open-weight LLMs on this two-node
Grace-Blackwell GB10 cluster (dgx-spark-1 + dgx-spark-2, 121 GiB unified
memory each, dual-rail 200GbE RoCE between them).

## Quick start

```bash
./serve.sh --list                      # what can I serve?
./serve.sh qwen3.6-27b-fp8 -d          # single-node model on :8000
./serve.sh laguna-s-2.1-nvfp4 -d --spec-decode   # with its validated spec-decode

cluster/preflight.sh deepseek-v4-flash # check both nodes first
cluster/start-cluster.sh deepseek-v4-flash       # 2-node flagship (TP=2)
cluster/stop-cluster.sh                # ALWAYS before relaunching
```

Everything speaks the OpenAI API on port 8000. Per-model flags live in
`models/<name>.conf` — serving a model is one command, never twenty flags.

## Layout

| Path | What |
|---|---|
| `Dockerfile` | pinned overlay on `vllm/vllm-openai:v0.26.0` (digest-pinned; see docs/BUILD.md for why not source) |
| `serve.sh`, `docker-compose.yml` | single-node launch (nvidia runtime, ipc=host, memlock, HF cache mount, /health healthcheck) |
| `cluster/` | 2-node launch: `start-cluster.sh`, `preflight.sh`, `stop-cluster.sh`, shared measured NCCL env |
| `models/*.conf` | one file per supported model: exact validated flags, spec-decode opt-in |
| `validate/` | correctness vs HF reference, determinism, 1-vs-2-node parity, needle-in-haystack, bench, soak |
| `bench/` | Step 0 microbenchmarks + raw logs (membw, NCCL sweeps) |
| `docs/` | HARDWARE (measured), MODELS (support matrix), MULTINODE, BUILD, TUNING, VALIDATION, TROUBLESHOOTING |

## The three facts that shape everything here

1. **~240 GB/s memory bandwidth** (measured) bounds decode: tok/s ≈ 240 /
   active-GB-per-token. MoE + quantized models are the only fast ones.
2. **The node link is RoCE 200GbE, not NVLink** — and each NIC rides PCIe
   Gen5 x4, so the real ceiling is ~21 GB/s (measured). Small-message
   all-reduce latency floor: ~25 µs. TP=2 across nodes still wins for big
   models (docs/MULTINODE.md has the arithmetic).
3. **Only one NFS-catalog model fits as shipped** (Laguna-S-2.1-NVFP4, one
   node). The 2-node flagship is DeepSeek-V4-Flash from the HF cache. The
   giant catalog models (V4-Pro, Kimi-k3, GLM-5.2, Inkling...) do not fit on
   two nodes at any context length — numbers in docs/MODELS.md.

Priority order everywhere: **stability > accuracy > throughput > latency.**
Speculative decode is off by default per model until proven faster AND
lossless on this hardware (`--spec-decode` to opt in where validated).

## Status (2026-07-28)

Everything in PROMPT.md's validation section has run; docs/VALIDATION.md is
the ledger with numbers and raw-evidence pointers. Highlights:

- 5 models fully validated (canary, Qwen3.6-27B-FP8, Nemotron Nano + Super
  NVFP4, Laguna NVFP4) + the 2-node flagship DeepSeek-V4-Flash
  (27 tok/s c=1, gsm8k 0.97, needle 3/3 @447K).
- Soaks: flagship 2-node 150 min / 3403 req / 0 errors; Laguna 150 min /
  1873 req / 0 errors; smoke-soaks clean on the rest. No leaks, no NCCL
  timeouts, no thermal throttling.
- Correctness: FP-equivalent vs HF transformers; FP8 quantization justified
  vs a BF16 control (gsm8k parity); bit-exact cross-node reproducibility
  available via VLLM_BATCH_INVARIANT=1 (standard-attention models).
- Spec decode: measured off — every method was slower or broken on GB10
  (numbers in the ledger).
- Failures found and documented, not papered over: GDN-hybrid ngram
  corruption, cross-node CUDA-graph hang on the official image (root cause
  of prior repo's unsolved bug; --enforce-eager is the workaround), /health
  lying after node loss, and the eval-tokenizer false-low trap.
