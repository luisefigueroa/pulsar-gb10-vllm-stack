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
- speculative decode follows validated profile policy: Super MTP remains
  opt-in via `--spec-decode`; **never** use ngram on GDN hybrids. Only
  profiles with validated `SPEC_DECODE_ARGS` may enable it.
- CUDA graphs ON except where noted

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

- **Two-rank draft:** `qwen3-1.7b-2node` is an untested TP=2 recipe hidden
  from the serving wizard. List it with `scripts/list-models.sh --diagnostic`.
  It carries no retained model-specific evidence or ADR 0004 decision.
- **Bit-exact reproducibility run** (standard-attention models only): add
  `-e VLLM_BATCH_INVARIANT=1` → greedy outputs identical across nodes AND
  boots (30/30 verified). Not for production throughput paths.
- **Removed absolute-path profiles (not runnable):** `laguna-s-2.1-nvfp4`,
  `laguna-s-2.1-2node`, and `inkling-small-nvfp4` were deleted by ADR 0006.
  They had no exact Hugging Face `model_id@commit` home. Historical numbers
  remain in [MODELS.md](./MODELS.md) and [VALIDATION.md](./VALIDATION.md).
  Do not `./pulsar start` them.
