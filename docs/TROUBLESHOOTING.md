# Troubleshooting — failures actually hit on this cluster

Every entry below happened during this build. Inherited-wisdom entries from
the prior repo are marked [prior art] and were only included if we re-hit or
re-verified them.

## `LocalEntryNotFoundError: Cannot find an appropriate cached snapshot`

**Hit:** first launches of Qwen3.6-27B-FP8 and the 2-node canary.
**Cause:** several HF caches on these boxes have `snapshots/<hash>/` and
`blobs/` (and a `trees/` dir from xet-era downloads) but **no `refs/main`**.
With `HF_HUB_OFFLINE=1` (our default) huggingface_hub needs `refs/main` to
resolve the revision and fails even though all weights are present.
**Fix:** write the ref once:
`printf '%s' "$(ls <cache>/models--ORG--NAME/snapshots/ | head -1)" > <cache>/models--ORG--NAME/refs/main`
(One-liner over all models in cluster/README or run the loop in git history.)
Preflight checks weights on both nodes but only directory existence — if you
see this error with weights on disk, it is almost always the missing ref.

## Node 2 has no internet route

**Hit:** `hf download` on dgx-spark-2 fails (repo-not-found style error);
Docker Hub pulls work only via... they don't. Node 2 reaches node 1 over
RoCE and the LAN NFS server, nothing else.
**Fix:** download on dgx-spark-1, then
`rsync -rlptD ~/.cache/huggingface/hub/models--X 10.100.120.2:.cache/huggingface/hub/`
(rsync `-a` breaks on some shares here [prior art: NFS `sec=sys` chgrp]; the
RoCE link moves ~1 GB/s+, a 160 GB model syncs in minutes.)
Also fix `refs/main` on the target (see above) — and note image pulls: build
or pull on node 1, `docker save | ssh node2 docker load`.

## 2-node: server never comes up, no error anywhere

**Hit:** intentionally reproduced during teardown testing.
**Cause:** a leftover worker container from a previous run still holds the
master port / RDMA state; the new head blocks forever in torch.distributed
rendezvous.
**Fix:** `cluster/stop-cluster.sh` before every start (start-cluster.sh
also removes same-name containers). `cluster/preflight.sh` fails on any
stale `vllm-cluster-*` container for exactly this reason.

## NCCL silently uses TCP instead of RDMA in containers

**Cause:** `--network=host` does NOT expose `/dev/infiniband`; NCCL falls
back to sockets with no warning at default log levels, and you lose ~40% of
cross-node bandwidth plus latency stability.
**Fix:** launch tooling always passes `--device /dev/infiniband` and
`--ulimit memlock=-1` (default container memlock is 8 MiB — verbs
registration fails without it). Verify on bring-up with `NCCL_DEBUG=INFO`:
you want `NET/IB : Using [0]rocep1s0f0:1/RoCE` and channels `via NET/IB/`.
On GB10 expect `GDR 0` — GPUDirect is unsupported on Spark (GPU and NIC on
separate root complexes); staging via unified memory is normal and already
reflected in the Step 0 numbers.

## `vllm serve --help` crashes in a container without `--gpus all`

Cosmetic but confusing: the v0.26.0 CLI builds its arg parser through config
factories that touch CUDA. Run help/introspection with `--gpus all`.

## Cold loads look hung

29 GB (27B FP8) took 222 s to load from local NVMe page-cold; 160 GB
DeepSeek takes ~10 min. The `/health` endpoint appears only after engine
init, and `--health-start-period` in our tooling is 900 s for this reason.
Watch `docker logs -f` (`Loading weights took ...` line) before assuming a
hang. NFS-hosted models (Laguna from /mnt/Models) add the 10 GbE ceiling.

## GPU memory numbers make no sense

`nvidia-smi` reports `memory.total: [N/A]` on GB10 — there is no VRAM; CUDA,
OS, and page cache share 121 GiB LPDDR5X. Consequences:
- `--gpu-memory-utilization` budgets against the SHARED pool. 0.85 leaves
  ~18 GiB for everything else; don't run anything heavy beside a big model.
- Before starting a big model after other workloads:
  `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches` reclaims page cache
  that otherwise inflates "used" memory during vLLM's profiling pass.
- [prior art] mem-util 0.85 + TP=2 once drove a node into swap with an
  `shm_broadcast` busy-wait signature; 0.75-0.80 is the safe band for
  2-node configs.

## Stale kernel caches after image changes

[prior art, re-applied as policy] Triton's JIT cache can silently corrupt
outputs on sm_121 across version bumps (vllm#41871). After ANY image pin
change: `rm -rf ~/.cache/vllm` and the Triton cache dir on BOTH nodes.
Symptom to watch for in benchmarks: `jit_monitor` warnings about Triton
compilation *during inference* — those numbers are cold-cache artifacts,
rerun after warmup (validate/bench_serve.py warms up per level).

## `VLLM batch_invariant mode is not supported for GDN_ATTN`

**Hit:** enabling `VLLM_BATCH_INVARIANT=1` on Qwen3.6-27B (engine dies at
startup). The model is a hybrid — 48 of its 64 layers are GDN linear
attention — and vLLM v0.26.0's batch-invariant deterministic kernels don't
cover GDN. Consequence: cross-node bit-identical greedy output is only
achievable for architectures batch-invariant mode supports (standard
attention); for GDN/Mamba hybrids the achievable guarantee is
FP-equivalence (near-tie argmax flips only). See the determinism section
of docs/VALIDATION.md for the full investigation.

## lm-eval scores absurdly low while the model answers perfectly by hand

**Hit:** Laguna gsm8k scored 0.055 via lm-eval while a manual 5-shot curl
produced exactly-formatted correct answers.
**Cause:** `local-completions` tokenizes prompts CLIENT-side and sends token
IDs by default. Laguna's shipped tokenizer has the known Mistral-family
regex bug (transformers warns "will lead to incorrect tokenization" at
load), so the eval sent subtly corrupted token IDs; the server never saw the
real prompts. Models whose tokenizers are clean (Qwen, Nemotron) were
unaffected — which made it look model-specific.
**Fix:** add `tokenized_requests=False` to `--model_args` so raw text goes
to the server and the server tokenizes. Rule of thumb: when an eval number
contradicts a manual probe, distrust the eval plumbing first.

## `PermissionError` on HF datasets cache after container evals

**Hit:** host `lm_eval` failed to acquire
`~/.cache/huggingface/datasets/.../builder.lock` after lm-eval had been run
inside a container (as root) with the same cache mounted. Root-owned lock
and metadata files block the host user afterwards.
**Fix:** re-chown (`docker run --rm -v ~/.cache/huggingface/datasets:/d
IMAGE chown -R 1000:1000 /d/<dataset>`), or run containerized tools with
`--user $(id -u):$(id -g)` from the start.
