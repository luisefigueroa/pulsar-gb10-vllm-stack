# Build Prompt — vLLM on DGX Spark (GB10) 2-Node Cluster

> Reference prompt for the initial build of this repo. Kept for future re-runs and revisions.

## Task

This is a brand new, empty repo. Build a production-quality vLLM Docker image
targeting the NVIDIA DGX Spark (GB10 Grace-Blackwell) that this agent is running on,
plus the launch tooling to serve models across our 2-node Spark cluster.

## Step 0 — verify the environment before designing anything

Do not assume any of the following; confirm each on the actual machine and write
what you find into docs/HARDWARE.md:

- CPU arch, CUDA driver + toolkit version, compute capability (sm_*), NCCL version
- Unified memory size and measured memory bandwidth
- Interconnect between the two nodes. I believe we have NVLink; verify what the
  link actually is (`nvidia-smi topo -m`, `ibstat`, `nvidia-smi nvlink -s`.
  Confirm whether RDMA/GPUDirect is active (NCCL_DEBUG=INFO transport lines).
- Container runtime and NVIDIA container toolkit status
- Extense catalog of official open weights models at NFS mountpoint /mnt/Models/



## Deliverables

### 1. `Dockerfile`

vLLM built from source only if that offers significant advantages over official published vllm images for this arch/compute capability, pinned to a specific
upstream commit or release tag. Include the attention/quant kernel backends that
actually compile and pass tests on this target (FlashInfer, FA, CUTLASS/Machete, or
whatever survives Step 0). Multi-stage build; document build time.

### 2. Single-node launch

A `docker-compose.yml` (or `run.sh` wrapper) that encapsulates the setup that is
otherwise retyped every run:

- NVIDIA runtime / `--gpus all`
- `--ipc=host` or a large `--shm-size` — vLLM workers fail in opaque ways without it
- HF cache mounted from the host so weights survive container restarts
- port mapping for the OpenAI-compatible server
- healthcheck against `/health`
- env passthrough for `HF_TOKEN` and vLLM flags

### 3. Multi-node launch

Note that Docker Compose does not span hosts, so do NOT try to make it do so. Use
vLLM's Ray-based multi-node path or other known distributred inference frameworks (see upstream's `run_cluster.sh`) unless Step 0
turns up a concrete reason it won't work here. Deliver:

- a parameterized launch script taking a role (head/worker) and the head IP, or a
  matched pair of head/worker scripts; both containers on `--network=host` with
  matching env
- the NCCL env vars validated in Step 0 (`NCCL_IB_*`, `NCCL_SOCKET_IFNAME`,
  `NCCL_P2P_*`) baked in — not defaults copied from a blog post
- a preflight check that both nodes see each other and Ray reports the expected
  resources, run BEFORE the server starts
- a teardown script — a half-dead Ray cluster is a common way to waste an afternoon
- the parallelism strategy justified by the Step 0 nccl-tests numbers: if measured
  cross-node all-reduce bandwidth supports it, TP=2 across nodes is preferred for
  latency; if it doesn't, use pipeline parallel and say why

Do not reach for Kubernetes/LeaderWorkerSet — it's more machinery than two boxes
warrant, and we don't already run k8s.

### 4. `models/` — per-model config

One file per entry in the support matrix, holding the exact validated flags for that
model: context length, quantization, parallelism, spec-decode on/off. Serving a model
should be `./serve.sh <model-name>`, not a 20-flag command line.

### 5. `docs/`

Hardware findings, build instructions, the model support matrix, a tuning guide, and
a troubleshooting section covering failures you actually hit.

### 6. Validation suite

See below. Nothing is "done" until it passes.

## Priority order — strictly

Stability > inference accuracy > throughput > latency. Concretely:

- Never enable a flag or kernel you have not validated on this hardware.
- Any choice that changes the model math (quantization, attention backend,
  spec-decode acceptance settings) must be justified with a before/after eval
  number, not vibes.
- Features that are output-invariant by design (chunked prefill, prefix caching)
  need a greedy-output spot check on a fixed prompt set, not a full eval run.
- If a fast path is unstable or unverified, ship the slow path and document the
  tradeoff. Prefer a boring config that runs for a week over a fast one that OOMs.

## Validation (required, not optional)

- Build succeeds and container starts clean on both nodes.
- Correctness vs reference: greedy-decode comparison against HF transformers on
  this machine (or upstream vLLM on x86 if available) for a fixed prompt set.
  Gate on token-match rate and logprob closeness, not bit-exact equality —
  different kernels legitimately produce different floating-point results. Plus a
  short lm-eval-harness run (e.g. gsm8k) at each quantization level you support.
  Record the numbers.
- Determinism across identical nodes: the same image, config, and single greedy
  request must produce identical output run-to-run on one node AND on node A vs
  node B (identical hardware, identical math — use `VLLM_BATCH_INVARIANT=1` if
  batching noise interferes). A difference here IS a bug: driver, image, or
  hardware drift between the nodes.
- Single-node vs 2-node: compare greedy output on the same prompt set, but gate on
  token-match rate and eval-score parity, not bit-exact equality — vLLM documents
  that outputs may differ across parallelism configs (TP changes reduction order).
  Investigate systematic divergence. Exception: pipeline parallel does not split
  intra-op reductions, so PP=2 may match single-node bit-exactly — check
  empirically and document which guarantee this cluster actually gets.
- Long-context: needle-in-haystack or equivalent at the max context you claim to
  support. Claiming a context length you haven't tested is a bug.
- Stability: one full multi-hour load soak with concurrent requests on the flagship
  2-node config and one on the primary single-node config; short smoke-soaks
  (~15-30 min) for the rest of the matrix. Report memory growth, crashes, NCCL
  timeouts, and any thermal throttling. Document node-loss behavior once as a
  finding (expected: the Ray cluster does not recover — teardown and relaunch),
  not per model.
- Report all results in docs/, including what failed.

## Speculative decoding

Spec decode is likely the highest-leverage optimization on this hardware: decode is
memory-bandwidth-bound, and GB10's unified LPDDR5X bandwidth is far below datacenter
HBM while its compute is comparatively healthy. Benchmark it scoped, not
exhaustively:

- n-gram first, on every model — it needs no extra weights and cheaply answers
  whether spec decode helps on GB10 at all.
- MTP (multi-token prediction) only for models that ship MTP weights (e.g. recent
  DeepSeek releases).
- EAGLE-3 only where trained heads exist for the exact target model.
- 2-node benchmarks only for configs that won on one node — verification adds
  cross-node sync per step, so a single-node win does not imply a 2-node win.

For each config actually benchmarked: measure acceptance rate and end-to-end
tokens/sec, and verify output quality is unchanged vs non-spec-decode. Spec decoding
must be off by default until proven both faster and lossless here; ship it as a
documented opt-in flag.

## Models

Target the latest open-source models that fit. I'm interested in DeepSeek's recent
releases (I've referred to one as "DeepSeek V4 Flash" — verify the exact current
model name and its actual weights/config before building around it) plus Qwen,
Llama, and GPT-OSS families.

Before committing to any model, do the arithmetic: weights + KV cache at the target
context, against the cluster's real usable memory across both nodes. If the largest
models don't fit at full context on 2 nodes, tell me that explicitly with the numbers
rather than building toward something that can't work. Give me the support matrix as
model x quantization x max context x parallelism config, marked tested/untested.

## Working style

- Work incrementally: get a small model serving end-to-end on one node first, then
  scale to 2 nodes, then to large models, then spec decode. Don't build everything
  and debug at the end.
- Pin every version. Reproducible builds matter more than bleeding-edge.
- Check the current state of vLLM's aarch64/Blackwell support upstream (issues, PRs,
  release notes) before assuming something works — this is a fast-moving target and
  your training data may be stale.
- Surface blockers early. If something upstream is broken on this platform, tell me
  rather than working around it silently.

## Open questions carried into the build

- **Interconnect.** The user reports NVLink as the cluster interface. DGX Spark's
  documented node-to-node link is the ConnectX-7 200GbE QSFP port (RDMA/RoCE);
  NVLink-C2C is the intra-node Grace↔Blackwell link. Step 0 must settle this by
  measurement, since TP-across-nodes vs pipeline-parallel depends entirely on it.
- **"DeepSeek V4 Flash."** Exact model name unconfirmed — verify against the current
  upstream release before designing around its config.
- **Max context feasibility.** Whether the largest target models fit at full context
  on 2 nodes is unproven. KV cache at long context can dwarf the weights; do the
  arithmetic before committing.
