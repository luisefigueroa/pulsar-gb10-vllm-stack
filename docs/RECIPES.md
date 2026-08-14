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
- speculative decode follows validated profile policy: **DSpark is default-on**
  for the flagship and `--no-spec-decode` is its rollback; Super MTP and
  Laguna DFlash remain opt-in via `--spec-decode`; **never** use ngram on GDN
  hybrids. Only profiles with validated `SPEC_DECODE_ARGS` may enable it.
- CUDA graphs ON except where noted

## Flagship: DeepSeek-V4-Flash-0731, 2-node TP=2 — long-session agents @ 500K

Image: published PR-41834 digest pinned in the model conf (docs/BUILD.md).
**Preferred launch:** `./pulsar start deepseek-v4-flash` or
`cluster/start-cluster.sh deepseek-v4-flash` (runs preflight, dual-node start,
then `validate/warmup.py`). Guided: `./pulsar wizard`. Roll back to base
decode with `--no-spec-decode`. Before replacing another model, use
`./pulsar inventory` and the wizard’s ownership-safe stop flow (never
`docker rm` unlabeled containers by guess).

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

### Engine flags (from `models/deepseek-v4-flash.conf`)

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

## Primary single-node: Laguna-S-2.1-NVFP4 — 19.5 tok/s c=1, full 256K ctx

Preferred: `./pulsar start laguna-s-2.1-nvfp4` (or `./serve.sh laguna-s-2.1-nvfp4 -d`).
Stop with `./pulsar stop laguna-s-2.1-nvfp4`. Engine args on
`vllm/vllm-openai:v0.26.0` (NFS catalog path):
```
--model '/mnt/Models/Official Models/poolside/Laguna-S-2.1-NVFP4'
--served-model-name laguna-s-2.1 --gpu-memory-utilization 0.80
--max-model-len 262144 --max-num-seqs 4
--moe-backend marlin
```

Load-bearing: `--moe-backend marlin` (NVFP4 MoE via CUTLASS is silently
WRONG on sm_121). NFS cold load ~10 min. DFlash is **marginal** under
corrected metering (+13% c=1) — conf keeps it off by default; opt-in only
with a fresh A/B if you care. lm-eval needs `tokenized_requests=False`.

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
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1` is set in the Super conf (not global;
Laguna leaves it unset). MTP k=1 needs `"moe_backend":"triton"` inside
`--speculative-config` (global marlin breaks the unquantized MTP head).
Under corrected metering: **+47% c=1** — opt in with
`./serve.sh nemotron-3-super-120b-nvfp4 --spec-decode`.

## Nemotron-3-Nano-30B-A3B-NVFP4 — 61.9 tok/s c=1, 399 agg @ c=16

```
--model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
--served-model-name nemotron-3-nano --gpu-memory-utilization 0.80
--max-model-len 131072 --max-num-seqs 16
--moe-backend marlin
```
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1` in the Nano conf. Fastest model on the
box; ledger records needle 3/3 @124K (no `results/` needle artifact).

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

- **Canaries**: `qwen3-1.7b` (E2E smoke, ~2 min to healthy) and
  `qwen3-1.7b-2node` (multi-node plumbing check, TP=2 mp backend).
  They are hidden from the serving wizard; list them with
  `scripts/list-models.sh --validated --diagnostic`.
- **Bit-exact reproducibility run** (standard-attention models only): add
  `-e VLLM_BATCH_INVARIANT=1` → greedy outputs identical across nodes AND
  boots (30/30 verified). Not for production throughput paths.
- **Cross-node TP=2 of a 1-node model on the official image** (measurement
  only): `laguna-s-2.1-2node` is **STATUS=do-not-use** (requires `--force`).
  Conf bakes `--enforce-eager` (stock graphs hang by request ~2 without it).
  Prefer 1-node `laguna-s-2.1-nvfp4` for real serving.
