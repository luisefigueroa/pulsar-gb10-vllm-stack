# Recipes — every flag, verbatim, for the best measured results

These are the COMPLETE effective launch commands (rendered from
`./serve.sh <name> --dry-run` / `cluster/start-cluster.sh <name> --dry-run`,
not hand-transcribed). `models/*.conf` remain the source of truth; this page
exists so a recipe survives outside the tooling. Numbers: docs/VALIDATION.md.

Shared doctrine baked into every recipe:
- `--ipc=host --ulimit memlock=-1 --ulimit stack=67108864` (SHM + RDMA verbs)
- HF cache mounted; `HF_HUB_OFFLINE=1` (all weights local)
- 2-node adds `--network host --device /dev/infiniband` (host networking
  does NOT expose RDMA devices) and the measured NCCL env
- spec decode OFF everywhere (every method measured slower or broken —
  VALIDATION.md); CUDA graphs ON except where noted

## Flagship: DeepSeek-V4-Flash, 2-node TP=2 — 27.15 tok/s c=1, 447K ctx

Image `vllm-gb10:pr41834-d64074e6f` (source build, docs/BUILD.md).
Worker first (on dgx-spark-2), then head:

```bash
# WORKER (ssh 10.100.120.2)
docker run -d --name vllm-cluster-deepseek-v4-flash \
  --network host --ipc host --gpus all \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  --device /dev/infiniband \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v /mnt/Models:/mnt/Models:ro \
  -e HF_HUB_OFFLINE=1 -e VLLM_HOST_IP=10.100.120.2 \
  -e NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0 \
  -e NCCL_IB_QPS_PER_CONNECTION=4 \
  -e NCCL_SOCKET_IFNAME=enp1s0f0np0 -e GLOO_SOCKET_IFNAME=enp1s0f0np0 \
  -e TP_SOCKET_IFNAME=enp1s0f0np0 -e NCCL_IB_DISABLE=0 -e NCCL_DEBUG=WARN \
  vllm-gb10:pr41834-d64074e6f \
  --model deepseek-ai/DeepSeek-V4-Flash --served-model-name deepseek-v4-flash \
  --host 0.0.0.0 --port 8000 --gpu-memory-utilization 0.80 \
  --trust-remote-code --tensor-parallel-size 2 \
  --kv-cache-dtype fp8 --block-size 256 \
  --max-model-len 500000 --max-num-seqs 8 --max-num-batched-tokens 8192 \
  --enable-prefix-caching --distributed-executor-backend mp \
  --nnodes 2 --master-addr 10.100.120.1 --master-port 29500 \
  --node-rank 1 --headless

# HEAD (dgx-spark-1): identical except
#   -e VLLM_HOST_IP=10.100.120.1  and  --node-rank 0  (no --headless)
```

Load-bearing details: `--kv-cache-dtype fp8` is REQUIRED (auto asserts:
"fp8_ds_mla layout only supports fp8 kv-cache"); `--block-size 256` from the
validated production lineage; graphs stay ON (this stack passed all stress
with them — do NOT add `--enforce-eager` here). Fallback recipe = same
shape on the sparkrun image via `deepseek-v4-flash-sparkrun.conf` (different
entrypoint and HF mount at `/cache/huggingface`; MTP available there but
measured -36% — off).

## Primary single-node: Laguna-S-2.1-NVFP4 — 19.5 tok/s c=1, full 256K ctx

```bash
docker run --name vllm-laguna-s-2.1-nvfp4 -d --gpus all --ipc=host \
  --ulimit memlock=-1 --ulimit stack=67108864 -p 8000:8000 \
  -v ~/.cache/huggingface:/root/.cache/huggingface \
  -v /mnt/Models:/mnt/Models:ro \
  -e HF_HUB_OFFLINE=1 -e VLLM_LOGGING_LEVEL=INFO \
  --health-cmd 'curl -fs http://localhost:8000/health || exit 1' \
  --health-interval 30s --health-timeout 5s --health-retries 3 \
  --health-start-period 900s \
  vllm/vllm-openai:v0.26.0 \
  --model '/mnt/Models/Official Models/poolside/Laguna-S-2.1-NVFP4' \
  --served-model-name laguna-s-2.1 --host 0.0.0.0 --port 8000 \
  --gpu-memory-utilization 0.80 \
  --max-model-len 262144 --max-num-seqs 4 \
  --moe-backend marlin
```

Load-bearing: `--moe-backend marlin` (NVFP4 MoE via CUTLASS is silently
WRONG on sm_121). NFS cold load ~10 min. DFlash spec decode measured -51% —
do not re-add. lm-eval against it needs `tokenized_requests=False`.

## Nemotron-3-Super-120B-A12B-NVFP4 — 16.2 tok/s c=1, 113 agg @ c=32

Engine args on `vllm/vllm-openai:v0.26.0` (docker plumbing as Laguna, HF-id
model):
```
--model nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4
--served-model-name nemotron-3-super --gpu-memory-utilization 0.85
--max-model-len 32768 --max-num-seqs 32
--kv-cache-dtype fp8            # ckpt has no k/v scales (factor 1.0) — noted
--moe-backend marlin
```
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1` (kept here from the prior validated run;
measured perf-neutral on Laguna). MTP k=1 works only with
`"moe_backend":"triton"` inside `--speculative-config` — and measured -21%,
so it ships OFF.

## Nemotron-3-Nano-30B-A3B-NVFP4 — 61.9 tok/s c=1, 399 agg @ c=16

```
--model nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4
--served-model-name nemotron-3-nano --gpu-memory-utilization 0.80
--max-model-len 131072 --max-num-seqs 16
--moe-backend marlin
```
Env: `VLLM_MARLIN_USE_ATOMIC_ADD=1`. The fastest model on the box; needle
3/3 @124K.

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
- **Bit-exact reproducibility run** (standard-attention models only): add
  `-e VLLM_BATCH_INVARIANT=1` → greedy outputs identical across nodes AND
  boots (30/30 verified). Not for production throughput paths.
- **Cross-node TP=2 of a 1-node model on the official image** (measurement
  only): add `--enforce-eager` or it hangs by request ~2
  (`laguna-s-2.1-2node.conf`).
