# Multi-node serving on the 2-Spark cluster

## The launcher: vLLM native `--nnodes` (torch.distributed), NOT Ray

The original build brief suggested vLLM's Ray-based path unless Step 0 found a concrete
reason otherwise. Step 0 found two:

1. **Prior art on this exact cluster.** The Ray path (official arm64 images +
   `ray[default]==2.56.1` overlay, TP=2) was previously debugged for days
   (`an earlier private GB10 build`): it
   served at concurrency 1 and **hard-hung the engine at concurrency >= 2**
   (`RPC call to sample_tokens timed out` -> `EngineDeadError`), across two
   different images and nine eliminated hypotheses, and was never fixed.
   Meanwhile the native `--nnodes/--node-rank/--headless` path (mp executor,
   torch.distributed bootstrap) is what the production DeepSeek-V4-Flash
   deployment on these two boxes has used since June 2026, validated at
   high concurrency (see docs/VALIDATION.md soaks and multi-node gates).
2. **Re-verified here on the current stack** (v0.26.0, this repo's tooling):
   Qwen3-1.7B TP=2 across nodes on the native path served 8/8 concurrent
   greedy requests correctly on first try (commit history has the run).
   DeepSeek-V4-Flash TP=2 results are in docs/VALIDATION.md.

The native path is also structurally simpler: no Ray head/worker state to
half-die (the classic wasted afternoon), no executor-version knobs, no
UUID-vs-index `CUDA_VISIBLE_DEVICES` mangling. Teardown = remove two
containers (`cluster/stop-cluster.sh` does both nodes and verifies).

## How it works

- `cluster/start-cluster.sh <model>` starts the **worker first** on
  dgx-spark-2 via SSH (`--node-rank 1 --headless`), then the **head** here
  (`--node-rank 0`, serves the OpenAI API on :8000). Both containers:
  `--network host --ipc host --gpus all --ulimit memlock=-1
  --device /dev/infiniband` (host networking does NOT expose RDMA devices;
  without the device flag NCCL silently falls back to TCP).
- torch.distributed bootstraps over `--master-addr $HEAD_IP:29500`
  (RoCE rail 0); NCCL data plane uses the env from `cluster/cluster-env.sh`
  (measured in Step 0, see docs/HARDWARE.md).
- `cluster/preflight.sh <model>` gates startup: RoCE pings both rails,
  key-based SSH, 2 ACTIVE RDMA links per node, GB10 + nvidia runtime
  present, image + weights present on BOTH nodes, no stale
  `vllm-cluster-*` containers, >=100 GiB available memory per node.

## Parallelism choice: TP=2, measured justification

Decode on GB10 is memory-bandwidth-bound (~240 GB/s effective per node).
For a model reading W GB of active weights per token:

- **TP=2** reads W/2 per node concurrently and pays 2 all-reduces per layer.
  Measured all-reduce latency at decode message sizes (8-32 KB): 25-40 µs.
  For DeepSeek-V4-Flash (43 layers, hidden 4096): 86 all-reduces x ~27 µs
  ≈ 2.3 ms/token of comms against ~24 ms/token of weight reads saved.
  Net: ~1.8-1.9x single-stream decode speedup over one node (if it fit).
- **PP=2** sends one hidden-state transfer per token (~30 µs, negligible)
  but runs stages *sequentially*: no single-stream latency win, only
  capacity. Bubbles eat throughput at low concurrency.

With measured numbers, TP=2 wins for the latency-priority case and is the
validated production layout for DeepSeek-V4-Flash on these boxes. The
caveat from the microbenchmarks: all-reduce time jumps ~4x between 32 KB
and 128 KB messages, so TP=2 advantage shrinks at batch >= 16; that regime
is covered by the concurrency sweeps in docs/VALIDATION.md rather than
extrapolated.

Cross-node TP=2 vs PP=2 is re-decided per model in models/*.conf, not
globally; nothing 2-node ships untested.

## Which images can actually do 2-node (measured 2026-07-28)

| Stack | Cross-node TP=2 result |
|---|---|
| published PR-41834 image (DeepSeek-V4 flagship) | **STABLE with CUDA graphs on** — full battery + multi-hour soaks (docs/VALIDATION.md) |
| official v0.26.0, tiny BF16 canary | works, incl. concurrency |
| official v0.26.0, real models, graphs on | **hangs within first requests** (27B GDN: wrong output then `sample_tokens` RPC timeout; Laguna: `shm_broadcast acquire_read` timeout) |
| official v0.26.0, `--enforce-eager` | works (Laguna: captures + concurrency + gsm8k parity) at ~2x decode cost |

Root cause of the stock-image hangs is the CUDA-graph path cross-node
(consistent with upstream vllm#46253) — this also resolves the prior repo's
multi-day unsolved concurrency hang, whose not-yet-tested list included
exactly this experiment. Practical rules:
- DeepSeek-V4-Flash: use the **PR #41834 local build** (`models/deepseek-v4-flash.conf`,
  recipe in docs/BUILD.md). Graphs on, DSpark recommended.
- Anything else 2-node on the official image: add `--enforce-eager` and
  accept ~2x slower decode — or don't run it 2-node (everything else in the
  matrix fits one node anyway, where graphs are stable).
- Never ship a 2-node config without a soak; `/health` cannot be trusted
  during partial failures (see node-loss finding in VALIDATION.md).

## Operational notes

- Start order matters only in that the worker must be reachable when the
  head begins rendezvous; start-cluster.sh handles it.
- **Always run `cluster/stop-cluster.sh` before relaunching.** A worker
  holding the master port or RDMA QPs makes the next head hang in
  rendezvous with no error output.
- If the head dies, the worker does NOT recover or rejoin a new head —
  tear down and relaunch (node-loss behavior documented once in
  docs/VALIDATION.md).
- Worker logs: `ssh "$WORKER_IP" docker logs vllm-cluster-<model>`.
  start-cluster.sh tails both automatically on startup failure.
