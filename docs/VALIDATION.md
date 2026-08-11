# Validation ledger

Rules: nothing is "done" until it passes here. Gates are
token-match rate + logprob closeness (not bit-exactness) across kernels /
parallelism; bit-exactness IS required run-to-run on identical config.
Raw artifacts: `results/`.

Status legend: PASS / FAIL / PENDING (not yet run) / N-A.

## Current ship set (read this first)

Everything below this box is **evidence and history**. Use this section for
“what do I run today?” Present-tense serving decisions live here; older
sections may describe superseded images or geometries — look for
HISTORICAL / SUPERSEDED markers.

| Role | What to run | Image | Notes |
|---|---|---|---|
| **Flagship (2-node)** | `cluster/start-cluster.sh deepseek-v4-flash` | published PR-41834 digest in model conf | DeepSeek-V4-Flash-**0731**; DSpark default-on at checkpoint-fixed k=5; canonical **20 GB/rank KV → 652,465 tok**, `max-num-seqs 5`, batch 16384. Earned by 447K needle + 150-min c=5 soak on 2026-08-01; the 10 GB/577k result remains historical evidence. |
| **Primary single-node** | `./serve.sh laguna-s-2.1-nvfp4 -d` | `vllm/vllm-openai:v0.26.0` | NVFP4; graphs on; DFlash off by default |
| Fast single-node | `./serve.sh nemotron-3-nano-30b-nvfp4 -d` | mainline | Fastest tok/s on box |
| Large single-node | `./serve.sh nemotron-3-super-120b-nvfp4 -d` | mainline | MTP opt-in via `--spec-decode` |
| Reasoning single-node | `./serve.sh qwen3.6-27b-fp8 -d` | mainline | **Never** ngram spec; not 2-node |
| Diagnostic canary | `./serve.sh qwen3-1.7b -d` | mainline | Build/plumbing probe; hidden from serving wizard |

**Not shipped:** stock `v0.26.0` for DeepSeek-V4 multi-node (livelock); community sparkrun binary (removed from tree); Ray multi-node; ngram on GDN hybrids.

### Experimental weight-storage status

| Path | Status | Evidence / rule |
|---|---|---|
| Replicated local HF caches | **SHIPPED DEFAULT** | Existing model/profile rows below; wizard and normal CLI use this path |
| Single authoritative copy over NFSv4.2/RDMA | **PENDING — NOT PROMOTED** | Deterministic config/manifest/route/launcher/benchmark self-tests exist, but physical two/three-node throughput, startup, faults, correctness, long-context, restart, and soak artifacts are still required by `WEIGHT_FABRIC.md` |
| Federated library to sealed local hot via 8-stream SSH-over-RoCE | **PROMOTION CANDIDATE — NOT PROMOTED** | DeepSeek full-model transfer passed at 1.898x the control-path median with plane and physical-read proof; schema-2 transfer integrity, interruption/retry, catalog-loss restart, serving, 447k context, and topology-bound SSH identity gates passed. Home-rank hot materialization is ruled out by [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md). Remaining blockers are lab-issued expected-seal/validation-bundle binding, serve-time symlink witness and lifecycle evidence, production non-home hot-budget policy, strict DeepSeek determinism, and sustained soak. The dated promotion assessment remains historical evidence; this ledger supersedes only its materialization recommendation. See `results/model-library/model-library-promotion-assessment-20260810.json` and `results/model-library/topology-ssh-trust-gate-20260810.json`. |

The storage experiment does not change any model's `tested` claim until that
exact storage source independently passes its promotion battery.

**Security:** API binds `0.0.0.0:8000` without auth — lab network only ([SECURITY.md](../SECURITY.md)).

## Build & startup

| Check | Status | Evidence |
|---|---|---|
| Overlay image builds | PASS | `vllm-gb10:v0.26.0`, build ~2 s on cached base (docs/BUILD.md) |
| Container starts clean, node 1 | PASS | qwen3-1.7b healthy 140 s; qwen3.6-27b-fp8 healthy 510 s cold |
| Container starts clean, node 2 | PASS | 2-node worker (headless) startup during canary run |

## Correctness vs reference

| Model | Comparison | Status | Numbers |
|---|---|---|---|
| Qwen3-1.7B BF16 | vLLM vs HF transformers greedy, 30 prompts x 64 tok | **PASS** | 18/30 exact text; all 12 divergences are near-ties (chosen-token logprob delta 0.03-0.30 at flip point); max logprob delta 0.141 on matched prefixes; verdict FP-EQUIVALENT (`results/qwen1.7b-*`) |
| Qwen3.6-27B-FP8 | gsm8k 5-shot, 200 samples via lm-eval | **RECORDED** | 0.630 flexible / 0.615 strict (±0.034). Absolute value reflects raw-completion prompting of a reasoning-tuned model |
| Qwen3.6-27B **BF16 control** (same model unquantized) | gsm8k, same harness/settings | **PASS — quant justified** | BF16: 0.620 flexible / 0.610 strict vs FP8: 0.630 / 0.615 — statistically identical (stderr ±0.034). **The FP8 checkpoint costs no measurable accuracy**; the before/after number the project rules require for quantization choices. |
| Laguna-S-2.1-NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.820 strict** / 0.45 flexible (must use `tokenized_requests=False` — see TROUBLESHOOTING for the 0.055 false reading) |
| Nemotron-3-Nano NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.830 strict** / 0.465 flexible |
| Nemotron-3-Super NVFP4 | gsm8k 5-shot, 200 samples | **RECORDED** | **0.940 strict** / 0.95 flexible |
| DeepSeek-V4-Flash (2-node TP=2) | gsm8k 5-shot, 200 samples | **RECORDED** | **0.970 strict / 0.970 flexible** — best in fleet |

## Determinism

| Check | Status | Numbers |
|---|---|---|
| Same node, same config, run-to-run (27B FP8, greedy, 30 prompts) | **PASS** | 30/30 exact text, mean prefix 1.0000, max logprob delta 0.0000 |
| Same config run-to-run on node B (Qwen3-1.7B) | **PASS** | 30/30 exact, delta 0.0000 |
| Same node across time + heavy traffic (27B FP8, runA vs runC) | **PASS** | 30/30 IDENTICAL — prefix cache/warmup does not perturb greedy output |
| Node A vs node B, same image+config (27B FP8, default flags) | **ROOT-CAUSED** | 12/30 exact, all near-tie flips. NOT node drift: weights/config/image/driver verified identical, and the same node vs ITSELF across a server restart shows the same divergence (16/30). Cause: per-boot nondeterministic kernel selection in the compile pipeline — eager mode recovers 28/30; autotune-off alone does nothing (13/30). |
| Node A vs node B with `VLLM_BATCH_INVARIANT=1` (Qwen3-1.7B) | **PASS — IDENTICAL** | 30/30 exact, max logprob delta 0.0000, across different boots AND nodes. This is the reproducibility switch — but it is unsupported for GDN/Mamba hybrid architectures (engine refuses to start: `not supported for GDN_ATTN`), so Qwen3.6-27B cannot use it. |

**Determinism guarantee this cluster actually gets:** within one engine boot,
greedy output is exactly reproducible for the FLASH_ATTN-path models
(Qwen3-1.7B, Qwen3.6-27B, Nemotron-Nano — verified through heavy traffic).
Laguna (FLASHINFER path) shows near-tie-only FP noise even run-to-run within
a boot — isolated to the execution path itself: NOT prefix caching (persists
with `--no-enable-prefix-caching`), NOT Marlin atomic-add (persists without;
perf-neutral 19.46 vs 19.48 tok/s, so the knob ships removed). Across boots
or nodes, all default configs are FP-equivalent (near-tie flips only).
Bit-exact cross-node reproducibility is available opt-in via
`VLLM_BATCH_INVARIANT=1` for standard-attention models (unsupported on
GDN/Mamba hybrids). There is no hardware or software drift between nodes.
| 1-node vs 2-node same model (TP reduction order may differ; gate on match rate + eval parity) | **PASS** | Laguna 1-node (graphs) vs 2-node TP=2 (eager): greedy divergences are near-tie-only (0 hard disagreements at flip points), and **gsm8k parity 0.820 vs 0.825 strict** — statistically identical. Note PP=2 was not shipped for any model (TP=2 chosen from measured link numbers, docs/MULTINODE.md), so the "PP may be bit-exact" question is moot on this cluster. |

## Multi-node

| Check | Status | Numbers |
|---|---|---|
| Native --nnodes TP=2 cross-node serves (standard attention) | PASS | Qwen3-1.7B canary, correct greedy output |
| Concurrency >= 2 on 2-node (prior-stack killer) | PASS | 8/8 concurrent correct on canary |
| TP=2 cross-node with a GDN hybrid (Qwen3.6-27B) | **FAIL** | Wrong output on first request ("2+2=" -> "5"), then `RPC call to sample_tokens timed out` -> engine dead on the first capture request. Same signature as the prior repo's unsolved hang. |
| TP=2 cross-node, Laguna NVFP4 (standard attention), CUDA graphs on | **FAIL** | Healthy, correct smoke output, then fatal `shm_broadcast acquire_read TimeoutError` during the second request. So the stock-image hang is NOT hybrid-specific after all. |
| TP=2 cross-node, Laguna NVFP4, `--enforce-eager` | **PASS (workaround)** | Full 30-prompt capture + 8 concurrent requests, stays healthy. **Root cause of the stock-image cross-node hangs: the CUDA-graph path** (consistent with upstream vllm#46253 cross-node graph-capture IMA). The prior repo's multi-day unsolved hang is therefore two findings: eager was on their not-yet-tested list. Practical rule: on `vllm/vllm-openai:v0.26.0`, cross-node TP=2 requires `--enforce-eager` (~2x slower decode) — which is why DeepSeek-V4 uses the published PR-41834 image (graphs on), not stock v0.26.0. |
| RDMA (not TCP) transport in vLLM containers | **PASS** | flagship bring-up with NCCL_DEBUG=INFO: `NET/IB : Using [0]rocep1s0f0:1/RoCE`, channels `via NET/IB/0` |
| DeepSeek-V4-Flash TP=2 serves + concurrency | **PASS** | full battery + soaks on the promoted PR-41834 image (see upstream section); earlier community-binary runs retained as historical baselines |
| Node-loss behavior documented | **DONE** | Worker killed mid-request on the flagship: (1) in-flight requests hang with no error — clients need their own timeouts; (2) **`/health` keeps returning OK for ~5 minutes** after the worker is gone (until the 300 s execute-model RPC timeout fires) — do not monitor 2-node deployments on `/health` alone, probe with a real 1-token completion; (3) at ~5 min the engine dies (`RPC call to sample_tokens timed out`) and `/health` starts failing, but the container stays "Up" (API process alive, engine dead); (4) **no recovery, ever** — remedy is `cluster/stop-cluster.sh` + relaunch, as predicted. |

## Long context

| Model | Claimed | Needle result | Status |
|---|---|---|---|
| Qwen3.6-27B-FP8 | 131,072 (conf) | 3/3 PASS at 121,138 prompt tokens (depths .05/.5/.95) | **PASS** |
| Laguna-S-2.1-NVFP4 | 262,144 (config max) | 3/3 PASS at 260,907 prompt tokens (99.5% of max) | **PASS** |
| DeepSeek-V4-Flash | 500,000 configured | 3/3 PASS at 447,237 tokens — on BOTH the sparkrun image (07-28) and the promoted PR-41834 image (07-30) | **PASS** |

## Speculative decoding

### Current ship doctrine (read this first)

After the 2026-07-31 harness metering fix (section below), **present-tense**
guidance is:

| Path | Default | Override |
|---|---|---|
| DeepSeek-V4-Flash **DSpark k=5** (PR-41834) | **on** | `--no-spec-decode` rolls back to base decode |
| Nemotron-Super **MTP k=1** | base; **opt-in win** (+47% c=1) | `./serve.sh nemotron-3-super-120b-nvfp4 --spec-decode` |
| Laguna **DFlash k=15** | **off** (marginal +13%) | only with a fresh A/B |
| **ngram** on GDN hybrids | **never** | removed from conf — corrupts output |
| Generic DSV4 **MTP** | superseded by DSpark on the flagship image | — |

Configs ship `SPEC_DECODE_ARGS` only where validated. `RECOMMENDED_SPEC=1`
makes that validated path the default; `--no-spec-decode` is the explicit
rollback. Optional profiles remain off unless `--spec-decode` is supplied.

For the flagship, k=5 is a checkpoint invariant: it must equal
`dspark_block_size=5`. It is not a tuning knob; larger blocks draft unreachable
positions and reduce acceptance
([vLLM PR #41834](https://github.com/vllm-project/vllm/pull/41834)).

### Historical pre-fix table (INSTRUMENT ERROR — do not ship from this)

The following table and bottom-line were measured with broken token
counting and/or synthetic prompts. **Throughput deltas are not trustworthy.**
Corrected numbers and soaks are in **§ SPEC-DECODE VERDICTS CORRECTED** and
**§ Spec-enabled flagship soak**. ngram-on-GDN **FAIL** still stands.

| Model | Method | Acceptance | tok/s spec vs base | Output unchanged? | Verdict (historical) |
|---|---|---|---|---|---|
| Qwen3.6-27B-FP8 | ngram k=4 (FLASH_ATTN) | ~26% (233/904) | n/a | **NO — corrupted**: 3/30 exact, 8 hard disagreements, one output devolves into unrelated garbled text (replacement char + off-topic content). Likely GDN-state rollback breakage under spec verify on sm_121 | **FAIL — do not enable** |
| Qwen3.6-27B-FP8 | ngram k=4 (TRITON_ATTN workaround) | — | n/a | **NO** — still corrupted (2/30 exact, 5 hard disagreements, delta 1.26). Not attention-backend-specific; the GDN hybrid + spec-verify path itself is broken on this stack | **FAIL — ngram unusable on GDN hybrids; SPEC_DECODE_ARGS removed from conf** |
| Laguna-S-2.1-NVFP4 | dflash k=15 (the checkpoint's own gen_config wiring, NVFP4-matched draft) | 21.3% (4799/22530; 3.2 tok/round) | **9.47 vs 19.48 c=1 (-51%)** *(undercounted)*; worse at c=2/4 too | 1 hard disagreement (delta 0.67) on top of this model's baseline near-tie noise | **HISTORICAL** — corrected A/B is **+13% marginal**; conf keeps off by default |
| Nemotron-3-Super | mtp k=1 (draft moe_backend=triton — global marlin pin breaks the unquantized MTP head) | **97.5%** (2536/2602) | **12.75 vs 16.20 c=1 (-21%); 9.29 vs 11.98 c=4 (-22%)** *(undercounted)* | Yes — FP-EQUIVALENT, 0 hard disagreements | **HISTORICAL** — corrected A/B is **+47% WIN / opt-in** (see § VERDICTS CORRECTED) |
| Nemotron-3-Nano | mtp k=1 | | | | not run — same head design as Super at 6x smaller base model; expected worse ratio, low priority |
| any model | EAGLE-3 | | | | **N/A — no trained EAGLE-3 heads exist locally for any target model** (checked catalog + HF caches); scoping says only run where heads exist |

**Historical bottom line (SUPERSEDED 2026-07-31):** the paragraph above this
table once concluded that every method was slower and that nothing should
ship with spec decode. That conclusion mixed real ngram-on-GDN corruption
with **undercounted** throughput. See the corrected A/B table and flagship
spec-on soaks later in this file. **Do not use this historical bottom line
for serving decisions.**
| DeepSeek-V4-Flash 2-node | mtp k=2 (the prior production flag set) | 69.3% (3619/5222) | **17.34 vs 27.02 c=1 (-36%)** *(undercounted)*; worse at c=2/4 | Yes — FP-EQUIVALENT | **HISTORICAL** — generic MTP superseded by **default DSpark k=5** on PR-41834 |

## Soaks

| Config | Duration | Errors | Mem growth | Thermal | Status |
|---|---|---|---|---|---|
| Flagship 2-node (deepseek-v4-flash) | **150 min @ c=8, 3403 requests** | **0** (and 0 NCCL timeouts) | none (decile-averaged availability flat on both nodes; raw values fluctuate ~2 GiB with page cache) | node1 max 79 C, node2 steady 66 C, SM >=2392 both (no throttle) | **PASS** — cluster still healthy at end |
| Primary 1-node (laguna) | **150 min @ c=4, 1873 requests** | **0** | none (-0.5 decile drift = availability slightly up) | 82 C max, SM >=2379 (no throttle) | **PASS** — server still healthy at end |
| qwen3.6-27b-fp8 smoke | 20 min @ c=8 | **0** | -0.09 GiB (noise) | 80 C max, SM >=2353 (no throttle) | **PASS** |
| nemotron-3-nano smoke | 15 min @ c=16 (1120 requests) | **0** | +0.03 GiB | 70 C max, SM >=2392 | **PASS** (+ needle 3/3 @124K after soak) |
| nemotron-3-super smoke | 20 min @ c=16 (348 req) | **0** | start 7.39 → end 5.72 GiB avail (shrink 1.05; page-cache noise class) | temp max 80 C, SM >=2392 | **PASS** (`results/soak-super-smoke.json`) |

## Failures & findings log

- 2026-07-27: HF caches missing `refs/main` broke offline loading twice
  (TROUBLESHOOTING.md#localentrynotfounderror). Fixed cluster-wide.
- 2026-07-27: node 2 has no internet route; weights/images must be staged
  from node 1 (TROUBLESHOOTING.md).

## Stock-image DeepSeek-V4 probe series (2026-07-28, upstream-first evaluation)

Question: can `vllm/vllm-openai:v0.26.0` (pinned mainline) serve
DeepSeek-V4-Flash TP=2, removing the then-in-use community-binary dependency? Probes with
real weights, `--kv-cache-dtype fp8` (required by the DSV4 path), 16K ctx:

| Config | Result |
|---|---|
| defaults (graphs on) | loads, serves 1 smoke request coherently, then **engine dead within a few sequential requests** (5x `sample_tokens` RPC timeout) |
| `--enforce-eager` | survives 30 sequential captures + 8 concurrent, output coherent and FP-equivalent vs the sparkrun baseline (1 boundary near-tie flip, no garbling) — then **worker wedges under bench load** (fresh 512-token prefills), same RPC-timeout signature |

Interpretation: eager fixes the graph-path hang (as with Laguna) but NOT
this model — the failure under prefill pressure persists in eager, pointing
at the `FLASHINFER_MLA_SPARSE_SM120` attention kernel itself (upstream
vllm#49026 documents exactly this livelock on GB10 with cuda-gdb evidence).
**Verdict: stock v0.26.0 is not viable for DeepSeek-V4 on GB10.** The
upstream-lineage fix is replacing the sparse-attention kernel: PR #47629
(TRITON_MLA_SPARSE, generic DSA) and PR #41834 (the V4-Flash-on-SM12x path
all working community recipes build from). Both open against main, neither
merged, no maintainer review as of 2026-07-28. See git history for the
probe commands. `nvfp4_ds_mla` KV (1M ctx) remains fork-only upstream.

## Upstream-lineage DeepSeek-V4 (branch upstream-dsv4-sm121, published PR-41834 image)

Underlying source build of vLLM PR #41834 head (1h40m build, recipe in BUILD.md). All
runs TP=2 cross-node, CUDA graphs ON:

| Gate | Result |
|---|---|
| Stock-killer stress (30 captures, 8 concurrent, fresh-prefill bench) | **PASS — all three** (stock v0.26.0 died at each) |
| Throughput | **27.15 tok/s c=1, 105.5 agg c=8 — parity with sparkrun** (27.02 / 109) |
| gsm8k | 0.945 strict (sparkrun 0.970; within noise at n=200, note upstream #49927 reports V4 distribution shifts) |
| Needle @124K | 3/3 PASS |
| Output vs sparkrun | benign boundary flips only (both coherent, facts identical) |
| **DSpark k=5 spec decode** | **HISTORICAL (pre-metering-fix):** 81% acceptance but **-47% tok/s reported** (14.3 vs 27.15) with graphs on/off. That throughput is **instrument error** — see corrected +79% A/B. Ported draft opts remain perf-neutral under the fixed meter. **Current: DSpark is default-on**; use `--no-spec-decode` only for rollback. |

**Promotion soak (2026-07-28/29): PASS.** 150 min @ c=8, **3318 requests, 0
errors**, no leak signal (decile drift +0.78 GiB, page-cache territory), 81 C
max, SM >=2385 (no throttle). Bonus endurance data: the cluster then stayed
up and healthy for **27+ hours total** across idle and load
(results/soak-dsv4-upstream-150min.json).

**Needle @447,237 tokens at max-model-len 500000: 3/3 PASS (2026-07-30).**

**PROMOTED 2026-07-30**: `models/deepseek-v4-flash.conf` now runs the
upstream-lineage image (PR-41834). DSpark is the validated flagship default
(see corrected A/B + soaks). Community-binary experiments are historical only
and are not an in-tree launch path. Note historical lines above
pending
upstream work — precisely scoped: port the fork's draft-path cross-node
optimizations (local argmax, markov weight replication) onto PR #41834.

## DSpark draft-optimization port A/B (branch dspark-draft-optimizations, 2026-07-31)

Ported the fork's two cross-node draft optimizations onto PR-41834 as a
pure-Python overlay (`patches/pr41834-dspark-opt/`, image
`vllm-gb10:pr41834-dspark-opt-v1`): LOCAL_ARGMAX (draft selection on
vocab-sharded logits — kills the batch x k x vocab cross-TP gather) and
REPLICATE_MARKOV_W1 (per-rank Markov embedding — kills the per-position
all-reduce). Both verified engaged (log lines + output deltas + acceptance
parity at 80.8%).

**Result: 14.26 tok/s c=1 — identical to stock DSpark (14.29/14.31across
three variants). The draft-path collectives were NOT the bottleneck.**
A DSpark round costs ~350 ms (~9.5 base decode steps) where draft+verify
should cost ~1.5; the structural cost sits in the upstream verify/round
machinery (rejection trim, MHC bookkeeping, or host-sync stalls), not in
draft comms. Pinpointing it needs a torch/nsys profile of a single round —
scoped as future work. The port is kept (correct, harmless, upstreamable);
spec decode serving decisions follow the corrected doctrine (DSpark default-on), not this pre-fix A/B.

Corrected hypothesis trail: "draft comms dominate" (from the 10 MB/round
arithmetic) is now REFUTED by direct experiment — the arithmetic was right
about the bytes but wrong about what the round actually waits on.

## SPEC-DECODE VERDICTS CORRECTED (2026-07-31) — harness metering bug

**Every pre-07-31 spec-decode throughput number above is INSTRUMENT ERROR
and is retained only as the retraction trail.** Two compounding harness
bugs (TROUBLESHOOTING.md "Spec-decode throughput undercounted"):
(1) bench_serve counted SSE chunks as tokens — under spec decode one chunk
is a verified block, dividing reported throughput by the accepted-block
size (measured 3.46x on DSpark); (2) the synthetic repeat-prompts also
depress draft acceptance vs natural text. Discovered via a torch-profiler
trace whose wall-clock (45 tok/s) contradicted the bench (14 tok/s) —
found while chasing a wrong hypothesis (draft-path comms) that the same
bad numbers had motivated.

Corrected A/Bs (fixed metering, natural prompts, temperature 0):

| Config | base c=1 | spec c=1 | delta | c=4 | acceptance | verdict |
|---|---|---|---|---|---|---|
| **DeepSeek-V4-Flash + DSpark k=5 (2-node TP=2, PR-41834)** | 27.08 | **48.43** | **+79%** | 33.6 vs 17.7 (+90%) | 35-50% on natural text | **WIN — the flagship fast path** (agg par at c=8; **150-min spec-on soaks PASS** — see below) |
| **Nemotron-Super + MTP k=1 (triton draft)** | 16.10 | **23.72** | **+47%** | 12.6 vs 10.4 (+21%) | 86.6% | **WIN** (soak pending) |
| Laguna + DFlash k=15 | 19.20 | 21.63 | +13% | par | 11% (block drafting wastes on k=15) | marginal — opt-in only; smaller k untested |
| ngram on GDN hybrid | — | — | — | — | — | **FAIL stands** (output corruption is real, not a metering issue) |
| DSV4 MTP k=2 | not re-run | (est ~41 by factor) | — | — | — | superseded by DSpark on the same model |

The ported draft optimizations (patches/pr41834-dspark-opt) remain
perf-neutral under the corrected meter too (39.9 vs 39.1 tok/s manual
probes) — kept as documentation, not needed for the win.

## Spec-enabled flagship soak (2026-07-31) — the default-on gate: PASS

`deepseek-v4-flash-dspark` (PR-41834 image + dspark k=5), 150 min @ c=8:
**3,440 requests, 0 errors**; memory flat (0.07 GiB decile drift, node-2
sampler stable at ~14 GiB avail); no thermal throttle (node1 84 C max /
SM >=2385, node2 77 C max); spec decode engaged throughout (run window:
2,033,880 drafted / 542,869 accepted = 26.7% — depressed as expected by the
soak's random-word prompts; the perf case is the natural-prompt A/B at
+79%); server answered a coherent real completion at the end after 9+ h of
continuous cluster uptime. Per-10-min acceptance was FLAT at 26-27% across
all 21 snapshots (zero drift; draft rate steady ~133K/10min — raw trail in
results/soak-dsv4-dspark-counter-snapshots.log), and engine logs confirmed
the draft path fully engaged (DSpark draft CUDA graph captured, fused
o_proj/shared-expert quant kernels active). Tuning lead: spec settings cap
max_num_scheduled_tokens at 8160 — raising --max-num-batched-tokens may
recover headroom. A first attempt was killed at 60 min by a
harness task limit (preserved as bonus evidence: 1,385 req, 0 errors) and
rerun detached — total sustained load ~210+ min.
Raw: results/soak-dsv4-dspark-150min.json + soak-dsv4-dspark-node2-samples.log.

**Consequence: spec decode (DSpark k=5) is the default flagship serving
mode** — every gate (correctness, perf, soak) is earned.

## DeepSeek-V4-Flash-0731 candidate battery (2026-07-31) — PARITY, tested

Full battery on the PR-41834 image, TP=2, graphs on (raw: results/*0731*):

| Gate | 0731 | Incumbent | Verdict |
|---|---|---|---|
| Stress (captures + 8-concurrent + natural bench) | all pass | all pass | = |
| Base decode c=1 | 27.15 tok/s | 27.15 | identical (bandwidth-bound, as physics predicts) |
| DSpark natural c=1 / agg c=8 | 43.4 / 103.9 | 48.4 / 91.1 | parity (trade blows within boot noise) |
| DSpark acceptance | 46.6% cumulative | 35-50% | top of incumbent range |
| Spec output equivalence | FP-equivalent, 0 hard | same | = |
| gsm8k strict | 0.935 (+-0.018) | 0.945 (+-0.016) | statistical parity |
| Needle | 3/3 @ 124K and 3/3 @ 447K (500K boot; note 0731 revises tail compress_ratios) | 3/3 @ 447K | = |

**Honest read: our gates measure PARITY, not the release's claimed "huge
improvements"** — whatever improved lives outside this gate set (likely
capability domains we don't eval). What IS concretely better: the DSpark
drafter is now BUILT INTO the checkpoint (dspark_* config keys), so one
167 GB artifact replaces the base+DSpark pair — simpler staging, single
conf, and the separate -DSpark weights become retirable. No regressions
anywhere. Flagship swap remains gated on the standard 150-min soak.

## Inkling-Small-NVFP4 probe series (2026-07-31) — BLOCKED upstream

Three boot walls, each root-caused via dummy-load probes on the PR-41834
image (the only image with the Inkling NVFP4 loader, #49258):

1. Global `--moe-backend marlin` rejected — the unquantized (excluded)
   multimodal MoE modules hit the known marlin/unquantized signature.
   RESOLVED: no global pin, auto selection (conf comment).
2. `RuntimeError: cross-node TP is supported only on MNNVL fabric` — the
   fused Lamport RS+sconv op refuses RoCE. RESOLVED: `LAMPORT_RS_SCONV=0`
   selects the standard fallback (lamport.py:750).
3. `AssertionError: Paged KV not supported on SM 12.0 in this PR`
   (vllm_flash_attn/cute/interface.py) — Inkling's rel-position-bias
   attention is implemented ONLY via the FA4/CuTe score-mod kernel, called
   directly (nvidia/attention.py:312); `--attention-backend
   FLASHINFER/TRITON_ATTN` never reach this path (both probed, identical
   assertion), no reference implementation exists in the tree, and the
   assertion is TP-INDEPENDENT — it blocks any GB10 config, so PP or
   bigger future nodes don't help either. **UNRESOLVABLE here**; requires
   upstream FA4-cute sm12x paged-KV support (vendored flash-attention).

Verdict: `blocked-upstream`. Retest at the next flagship image bump.

## Flagship swap soak — DeepSeek-V4-Flash-0731, spec-on (2026-07-31) — PASS, PROMOTED

The 0731 candidate's promotion gate: 150 min, c=8, mixed-length prompts,
temp 0.7, **spec decode ON** (DSpark k=5 via the integrated drafter) — the
same protocol every prior flagship soak used, so the numbers compare
directly. Result: **3,951 requests, 0 errors** — the highest completion
count of any 150-min soak on this cluster (+15% over the incumbent's
spec-on 3,440, consistent with the higher acceptance below).

| Gate | Node 1 (head) | Node 2 (worker) | Verdict |
|---|---|---|---|
| Errors | 0 / 3,951 | — | PASS |
| Mem drift (first→last decile) | +0.97 GiB used | +0.65 GiB used | PASS (within the 1.05 precedent; 13+ GiB avail at end on both) |
| GPU temp max | **86 °C** | 80 °C | PASS with note below |
| SM clock min | 2379 MHz | 2379 MHz | PASS (no throttle; same floor as the Laguna passing soak) |

Temp note: node 1 peaked 1 °C above the 85 °C guideline for the first
time in any soak (prior max 84 °C), but the clock floor never moved —
2379 MHz is exactly the passing Laguna soak's floor, i.e. no thermal
throttling engaged. Watch this number in summer ambients.

Acceptance: **29.5% window acceptance, ruler-flat** — 2,203,695 drafted /
650,278 accepted over the full run, and the 1-hour, 2-hour, and final
checkpoints all read 29.5% to the decimal. That is ~3 points above the
incumbent drafter's 26.7% on the same adversarial random-word traffic.
Liveness confirmed with a real greedy completion (coherent) after 2.5 h
of continuous load, not `/health`. Raw: results/soak-dsv4-0731-150min.json
(300 samples) + results/soak-dsv4-0731-node2-samples.log (152 samples).

**Consequence: 0731 is the flagship.** `models/deepseek-v4-flash.conf` now
serves DeepSeek-V4-Flash-0731 with the integrated drafter (DSpark k=5
default-on with `--no-spec-decode` rollback). The fully validated 04-22
checkpoint was superseded and its serving profile has since been retired;
the separate
`deepseek-v4-flash-dspark.conf` is retired — the integrated drafter
supersedes the standalone -DSpark checkpoint (conf recoverable from git
history). Next: extend the canonical geometry to a 500K-token KV cache
(memory-gated; see the task ledger).

## Flagship 500K-token KV geometry (2026-07-31) — PASS, historical (10 GB/rank soaked ref)

User directive: serve a 500K-token KV cache. Delivered: **577,640-token
capacity** (`--max-model-len 500000 --kv-cache-memory-bytes 10000000000`,
1.16x concurrency at 500K/request), spec-on, using ~3 GiB LESS memory per
rank than the 131K-geometry boot it replaces (~17 GiB OS-available on both
nodes vs ~14 during the passing soak).

**The sizing lesson (why the first attempt OOM'd).** DSV4-0731 KV
bytes/token is GEOMETRY-DEPENDENT: the 131K boot profiled 245,618 tokens
from ~13.5 GB (~55 KB/tok effective), but at 500K max-model-len vLLM's own
requirement line reads 8.84 GiB for one 500K sequence (~18 KB/tok) — the
per-layer tail compress_ratios scale with configured length, so long
contexts are ~3x cheaper per token than the short-geometry number
predicts. Sizing 500K tokens at the stale 55 KB/tok rate (27.5 GB/rank)
OOM-killed node 2 during attention warmup: **`kv-cache-memory-bytes`
skips memory profiling entirely** and trusts an "Initial free memory:
111 GiB" reading that counts reclaimable page cache on unified memory
(driver NV_ERR_NO_MEMORY in dmesg, worker death, no OS OOM-kill). A
second attempt at 8.38 GiB under-shot vLLM's stated 8.84 GiB floor and
fail-fasted cleanly at init. 10 GB/rank was the first earned geometry.

Gates at the final geometry (all spec-on, DSpark k=5):

| Gate | Result |
|---|---|
| Greedy captures vs battery refs | FP-equivalent — 0 hard forks vs soaked-geometry ref; per-pair 0-1 marginal forks (0.50-0.75) at different prompts each comparison = cross-boot FP noise, same envelope as the accepted battery (which scores max delta 1.01 under the same verdict fn) |
| Needle @447K (3 depths) | **3/3 PASS** (444,237 prompt tokens); mem floors 16.8 / 17.4 GiB during fill, swap flat |
| gsm8k strict (200, 5-shot) | **0.925 +-0.019** vs recorded 0.935 +-0.017 — within the +-0.035 band. NOTE: must run INSIDE the container (transformers 5.14.1); host 5.5.4 cannot parse 0731's rope_parameters config format |
| 20-min c=8 smoke | 518 req / **0 errors**, mem drift -0.2 GiB (negative), temps 82/77 max, clocks >=2385, acceptance 29.0% (matches soak's 29.5%) |
| Liveness | coherent greedy completion post-smoke |

One 447K needle attempt mid-series was invalidated by an operator-side
cluster teardown (concurrent session — not a server fault; memory was
healthy at kill time) and rerun clean on a fresh boot.

Raw: results/smoke-dsv4-0731-500kv-20min.json,
results/smoke-dsv4-0731-500kv-node2-samples.log,
results/lm-eval-dsv4-0731-500kv/. This geometry was canonical until the
20 GB/rank promotion below; it remains the lower-pressure soaked reference.


## Flagship 20 GB/rank KV geometry (2026-08-01) — PASS, canonical

**Supersedes** the 10 GB/rank / 577,640-token geometry as the shipped
default in `models/deepseek-v4-flash.conf`: 20,000,000,000 bytes/rank,
**652,465-token** KV capacity (1.30x at 500K), `max-num-seqs 5`,
`max-num-batched-tokens 16384`, DSpark k=5, with the published PR-41834
image digest pinned in the model conf.

| Gate | Result |
|---|---|
| Boot geometry | **PASS** — 18.63 GiB reserved/rank, 652,465 tokens; `results/dsv4-20gb-gates.stdout` |
| Needle @447K (3 depths) | **3/3 PASS** — 444,237 prompt tokens at depths 0.05/0.50/0.95; `results/needle-dsv4-20gb-447k.log` |
| 150-min soak @ c=5 | **PASS** — 3,201 requests, 0 errors, 300 resource samples; `results/soak-dsv4-20gb-150min.json` |
| Thermals / clocks | 82 C max; 2,385 MHz minimum (within 0.8% of 2,405 MHz nominal) |
| Post-soak liveness | **PASS** — non-empty 8-token completion; `results/liveness-post-20gb-soak.json` |

**Memory-pressure finding, reviewed:** first-decile versus last-decile
`MemAvailable` shrank 0.49 GiB (4.14 to 3.65 GiB), above the harness's 5%
finding threshold. It was not a continuing leak: the trace contained
recoveries and ended on a 3.65–3.67 GiB plateau; roughly eight hours later
the live service remained healthy with 3.9 GiB available on the head and
4.1 GiB on the worker, no sustained swap I/O, and no request errors.
Evidence: `results/post-soak-memory-20gb-20260801.txt`. Treat approximately
3.5 GiB as the observed headroom floor and retain `max-num-seqs 5`.

Prior 10 GB evidence remains valid as the lower-pressure rollback geometry.
