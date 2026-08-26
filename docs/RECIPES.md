# Recipes — every flag, verbatim, for the best measured results

Engine flags below come from `models/*.conf`. Day-to-day operators should
launch with `./pulsar start <name>` (or `./pulsar wizard` for a guided
single- or multi-node model switch) and stop with `./pulsar stop <name>`.
Do not paste a raw unlabeled `docker run`: launchers attach stack ownership
labels, and home/wizard/`down.sh` will refuse unlabeled containers.
`models/*.conf` remain the source of truth; this page exists so the measured
flag set survives outside the tooling. Numbers: docs/VALIDATION.md.

Shared doctrine baked into every recipe:
- `--ipc=host --ulimit memlock=-1 --ulimit stack=67108864` (SHM + RDMA verbs)
- HF cache mounted; `HF_HUB_OFFLINE=1` (all weights local)
- multi-node adds `--network host --device /dev/infiniband` (host networking
  does NOT expose RDMA devices), per-rank interfaces from the confirmed
  topology, and the measured shared NCCL policy
- speculative decode follows validated profile policy: historical DeepSeek
  **DSpark was default-on** with `--no-spec-decode` as rollback; Super MTP remains
  opt-in via `--spec-decode`; **never** use ngram on GDN hybrids. Only
  profiles with validated `SPEC_DECODE_ARGS` may enable it.
- CUDA graphs ON except where noted

## HISTORICAL: DeepSeek-V4-Flash-0731, 2-node TP=2 — long-session agents @ 500K

The live `deepseek-v4-flash` profile was removed ([ADR 0012](./decisions/0012-retire-expected-seal-and-schema-1-bundles.md)).
Do not `./pulsar start deepseek-v4-flash`. Flags and numbers below are the
measured 2026-08 set.

Image: published PR-41834 digest that was pinned in that conf (docs/BUILD.md).
Historical launch was `cluster/start-cluster.sh deepseek-v4-flash` (preflight,
dual-node start, then `validate/warmup.py`). Roll back to base decode with
`--no-spec-decode`.

### Workload these flags target

| Pattern | Default choice |
|---|---|
| Few concurrent sessions (≈4, max 5) | `--max-num-seqs 5` |
| Long tool/code/repo agent traces | 20 GB/rank KV pool, prefix caching on |
| Cap at official useful max context | `--max-model-len 500000` (do not chase 1M) |
| Large prefills (code chunks) | `--max-num-batched-tokens 16384` |
| Hermes / OpenAI tool_choice=auto | `--enable-auto-tool-choice --tool-call-parser deepseek_v4 --reasoning-parser deepseek_v4 --tokenizer-mode deepseek_v4` |
| Decode throughput | DSpark default-on; k=5 fixed by checkpoint |

Not the target: high-QPS short chat, 8–16-way concurrency, or packing
KV to free-memory readings on unified memory (27.5 GB/rank OOM’d node 2).

### Engine flags (from the removed `models/deepseek-v4-flash.conf`)

```bash
# Prefer: cluster/start-cluster.sh deepseek-v4-flash
# Effective vLLM args on each rank (plus --nnodes/--node-rank/--headless):
--model deepseek-ai/DeepSeek-V4-Flash-0731
--served-model-name deepseek-v4-flash
--host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.80
--trust-remote-code --tensor-parallel-size 2
--kv-cache-dtype fp8 --block-size 256
--max-model-len 500000
--kv-cache-memory-bytes 20000000000   # 20 GB/rank; gpu_mem_util does NOT size KV
--max-num-seqs 5
--max-num-batched-tokens 16384
--enable-prefix-caching
--tokenizer-mode deepseek_v4
--enable-auto-tool-choice --tool-call-parser deepseek_v4
--reasoning-parser deepseek_v4
--distributed-executor-backend mp
# Default-on DSpark path (omit only with --no-spec-decode):
--speculative-config '{"method":"dspark","num_speculative_tokens":5}'
```

`num_speculative_tokens=5` must equal the checkpoint's
`dspark_block_size=5`; it is not a tuning knob. Larger blocks draft positions
the checkpoint cannot reach and reduce acceptance. Changing it requires a
different checkpoint/upstream contract and full revalidation.

Load-bearing details: `--kv-cache-dtype fp8` is REQUIRED (auto asserts:
"fp8_ds_mla layout only supports fp8 kv-cache"); `--block-size 256` from the
validated production lineage; graphs stay ON (do NOT add `--enforce-eager`
here). 10 GB KV + batch 16384 failed init (~15.66 GiB floor for one 500K
seq). Boot with 20 GB (2026-08-01): reserved 18.63 GiB → **652,465-token**
KV, **1.30x** max concurrency at 500K; post-boot warmup ok; MemAvailable
~7 GiB both nodes (tighter than 10 GB geometry — watch headroom). Stay
below 27.5 GB/rank (known OOM). Prior soaked ref: 10 GB → 577,640 tokens,
needle 3/3 @447K (VALIDATION.md).

## Primary single-node: Nemotron-3-Nano-30B-A3B-NVFP4 — 61.9 tok/s c=1, 399 agg @ c=16

Preferred: `./pulsar start nemotron-3-nano-30b-nvfp4`. Stop with
`./pulsar stop nemotron-3-nano-30b-nvfp4`. Engine args on
`vllm/vllm-openai:v0.26.0` (Hugging Face id):
```
--model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
--served-model-name nemotron-3-nano --gpu-memory-utilization 0.80
--max-model-len 131072 --max-num-seqs 16
--moe-backend marlin
```
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1` in the Nano conf. Fastest current
single-node serving profile; ledger records needle 3/3 @124K (no `results/`
needle artifact).

## Nemotron-3-Super-120B-A12B-NVFP4 — 16.2 tok/s c=1, 113 agg @ c=32

Preferred: `./pulsar start nemotron-3-super-120b-nvfp4`. Engine args on
`vllm/vllm-openai:v0.26.0` (HF-id model):
```
--model nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4
--served-model-name nemotron-3-super --gpu-memory-utilization 0.85
--max-model-len 32768 --max-num-seqs 32
--kv-cache-dtype fp8            # ckpt has no k/v scales (factor 1.0) — noted
--moe-backend marlin
```
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1` is set in the Super conf (not global).
MTP k=1 needs `"moe_backend":"triton"` inside
`--speculative-config` (global marlin breaks the unquantized MTP head).
Under corrected metering: **+47% c=1** — opt in with
`./serve.sh nemotron-3-super-120b-nvfp4 --spec-decode`.

## Qwen3.6-27B-FP8 — 8.0 tok/s c=1 (94% of roofline)

```
--model Qwen/Qwen3.6-27B-FP8
--served-model-name qwen3.6-27b-fp8 --gpu-memory-utilization 0.85
--max-model-len 131072 --max-num-seqs 16
```
No forced-Marlin flag on this stack (native `CutlassFp8BlockScaledMMKernel`).
**Single-node ONLY** — it is a GDN hybrid: cross-node TP=2 gives wrong
output then hangs; ngram spec decode corrupts output; `VLLM_BATCH_INVARIANT`
unsupported.

## Utility recipes

- **Canaries**: `qwen3-1.7b` was removed (ADR 0012). `qwen3-1.7b-2node`
  remains the tiny two-node test (TP=2 mp backend, hidden from the serving
  wizard). List it with
  `scripts/list-models.sh --legacy-tested --diagnostic`. `--validated` is
  removed (ADR 0008); use `--legacy-tested`. It does not report ADR 0004
  decision status.
- **Bit-exact reproducibility run** (standard-attention models only): add
  `-e VLLM_BATCH_INVARIANT=1` → greedy outputs identical across nodes AND
  boots (30/30 verified). Not for production throughput paths.
- **Removed absolute-path profiles (not runnable):** `laguna-s-2.1-nvfp4`,
  `laguna-s-2.1-2node`, and `inkling-small-nvfp4` were deleted by ADR 0006.
  They had no exact Hugging Face `model_id@commit` home. Historical numbers
  remain in [MODELS.md](./MODELS.md) and [VALIDATION.md](./VALIDATION.md).
  Do not `./pulsar start` them.
