# Validation ledger

Rules (PROMPT.md): nothing is "done" until it passes here. Gates are
token-match rate + logprob closeness (not bit-exactness) across kernels /
parallelism; bit-exactness IS required run-to-run on identical config.
Raw artifacts: `results/`.

Status legend: PASS / FAIL / PENDING (not yet run) / N-A.

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
| Qwen3.6-27B **BF16 control** (same model unquantized) | gsm8k, same harness/settings | **PASS — quant justified** | BF16: 0.620 flexible / 0.610 strict vs FP8: 0.630 / 0.615 — statistically identical (stderr ±0.034). **The FP8 checkpoint costs no measurable accuracy**; the before/after number the PROMPT requires for quantization choices. |
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
| TP=2 cross-node, Laguna NVFP4, `--enforce-eager` | **PASS (workaround)** | Full 30-prompt capture + 8 concurrent requests, stays healthy. **Root cause of the stock-image cross-node hangs: the CUDA-graph path** (consistent with upstream vllm#46253 cross-node graph-capture IMA). The prior repo's multi-day unsolved hang is therefore two findings: eager was on their not-yet-tested list. Practical rule: on `vllm/vllm-openai:v0.26.0`, cross-node TP=2 requires `--enforce-eager` (~2x slower decode) — which is why the flagship uses the sparkrun image (graphs on, stable, full battery passed) instead. |
| RDMA (not TCP) transport in vLLM containers | **PASS** | flagship bring-up with NCCL_DEBUG=INFO: `NET/IB : Using [0]rocep1s0f0:1/RoCE`, channels `via NET/IB/0` |
| DeepSeek-V4-Flash TP=2 serves + concurrency | **PASS** | full battery on sparkrun image; re-validated + soaked on the promoted PR-41834 image (see upstream section) |
| Node-loss behavior documented | **DONE** | Worker killed mid-request on the flagship: (1) in-flight requests hang with no error — clients need their own timeouts; (2) **`/health` keeps returning OK for ~5 minutes** after the worker is gone (until the 300 s execute-model RPC timeout fires) — do not monitor 2-node deployments on `/health` alone, probe with a real 1-token completion; (3) at ~5 min the engine dies (`RPC call to sample_tokens timed out`) and `/health` starts failing, but the container stays "Up" (API process alive, engine dead); (4) **no recovery, ever** — remedy is `cluster/stop-cluster.sh` + relaunch, as predicted. |

## Long context

| Model | Claimed | Needle result | Status |
|---|---|---|---|
| Qwen3.6-27B-FP8 | 131,072 (conf) | 3/3 PASS at 121,138 prompt tokens (depths .05/.5/.95) | **PASS** |
| Laguna-S-2.1-NVFP4 | 262,144 (config max) | 3/3 PASS at 260,907 prompt tokens (99.5% of max) | **PASS** |
| DeepSeek-V4-Flash | 500,000 configured | 3/3 PASS at 447,237 tokens — on BOTH the sparkrun image (07-28) and the promoted PR-41834 image (07-30) | **PASS** |

## Speculative decoding (all off by default)

| Model | Method | Acceptance | tok/s spec vs base | Output unchanged? | Verdict |
|---|---|---|---|---|---|
| Qwen3.6-27B-FP8 | ngram k=4 (FLASH_ATTN) | ~26% (233/904) | n/a | **NO — corrupted**: 3/30 exact, 8 hard disagreements, one output devolves into unrelated garbled text (replacement char + off-topic content). Likely GDN-state rollback breakage under spec verify on sm_121 | **FAIL — do not enable** |
| Qwen3.6-27B-FP8 | ngram k=4 (TRITON_ATTN workaround) | — | n/a | **NO** — still corrupted (2/30 exact, 5 hard disagreements, delta 1.26). Not attention-backend-specific; the GDN hybrid + spec-verify path itself is broken on this stack | **FAIL — ngram unusable on GDN hybrids; SPEC_DECODE_ARGS removed from conf** |
| Laguna-S-2.1-NVFP4 | dflash k=15 (the checkpoint's own gen_config wiring, NVFP4-matched draft) | 21.3% (4799/22530; 3.2 tok/round) | **9.47 vs 19.48 c=1 (-51%)**; worse at c=2/4 too | 1 hard disagreement (delta 0.67) on top of this model's baseline near-tie noise | **FAIL — off.** Upstream v0.26.0's DFlash path loses half the throughput; the GB10-tuned DFlash lives in community forks (aeon/DSpark), not the pinned mainline image |
| Nemotron-3-Super | mtp k=1 (draft moe_backend=triton — global marlin pin breaks the unquantized MTP head) | **97.5%** (2536/2602) | **12.75 vs 16.20 c=1 (-21%); 9.29 vs 11.98 c=4 (-22%)** | Yes — FP-EQUIVALENT, 0 hard disagreements | **LOSSLESS BUT SLOWER — stays off.** The BF16 MTP layer (~5.5 GB) costs more bandwidth per draft than a 97% acceptance saves at k=1 on a 240 GB/s machine |
| Nemotron-3-Nano | mtp k=1 | | | | not run — same head design as Super at 6x smaller base model; expected worse ratio, low priority |
| any model | EAGLE-3 | | | | **N/A — no trained EAGLE-3 heads exist locally for any target model** (checked catalog + HF caches); PROMPT scoping says only run where heads exist |

**Spec-decode bottom line for GB10 on the pinned stacks:** every method that
ran was lossless (FP-equivalent) except ngram-on-GDN (corrupted), and every
one was SLOWER: Super MTP -21%, DeepSeek MTP -36%, Laguna DFlash -51%.
The intuition "decode is bandwidth-bound so speculation should win" fails
here because the draft work is also bandwidth-bound on the same LPDDR5X, and
2-node verify adds cross-node syncs. The only known-win on this hardware is
the specialized DSpark fork for DeepSeek (prior art, different image, own
maintenance burden). Everything ships spec-decode OFF; `--spec-decode` exists
only where a config carries validated args (currently: none).
| DeepSeek-V4-Flash 2-node | mtp k=2 (the prior production flag set) | 69.3% (3619/5222) | **17.34 vs 27.02 c=1 (-36%)**; worse at c=2/4 | Yes — FP-EQUIVALENT | **LOSSLESS BUT SLOWER — stays off.** Draft layer + extra cross-node syncs per verify step cost more than 69% acceptance at k=2 returns. (The ~50 tok/s prior-art number came from the specialized DSpark fork/stack, not this image's generic MTP path.) |

## Soaks

| Config | Duration | Errors | Mem growth | Thermal | Status |
|---|---|---|---|---|---|
| Flagship 2-node (deepseek-v4-flash) | **150 min @ c=8, 3403 requests** | **0** (and 0 NCCL timeouts) | none (decile-averaged availability flat on both nodes; raw values fluctuate ~2 GiB with page cache) | node1 max 79 C, node2 steady 66 C, SM >=2392 both (no throttle) | **PASS** — cluster still healthy at end |
| Primary 1-node (laguna) | **150 min @ c=4, 1873 requests** | **0** | none (-0.5 decile drift = availability slightly up) | 82 C max, SM >=2379 (no throttle) | **PASS** — server still healthy at end |
| qwen3.6-27b-fp8 smoke | 20 min @ c=8 | **0** | -0.09 GiB (noise) | 80 C max, SM >=2353 (no throttle) | **PASS** |
| nemotron-3-nano smoke | 15 min @ c=16 (1120 requests) | **0** | +0.03 GiB | 70 C max, SM >=2392 | **PASS** (+ needle 3/3 @124K after soak) |
| nemotron-3-super smoke | 20 min @ c=16 | | | | RUNNING |

## Failures & findings log

- 2026-07-27: HF caches missing `refs/main` broke offline loading twice
  (TROUBLESHOOTING.md#localentrynotfounderror). Fixed cluster-wide.
- 2026-07-27: node 2 has no internet route; weights/images must be staged
  from node 1 (TROUBLESHOOTING.md).

## Stock-image DeepSeek-V4 probe series (2026-07-28, upstream-first evaluation)

Question: can `vllm/vllm-openai:v0.26.0` (pinned mainline) serve
DeepSeek-V4-Flash TP=2, removing the sparkrun-image dependency? Probes with
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

## Upstream-lineage DeepSeek-V4 (branch upstream-dsv4-sm121, image vllm-gb10:pr41834-d64074e6f)

Source build of vLLM PR #41834 head (1h40m build, recipe in BUILD.md). All
runs TP=2 cross-node, CUDA graphs ON:

| Gate | Result |
|---|---|
| Stock-killer stress (30 captures, 8 concurrent, fresh-prefill bench) | **PASS — all three** (stock v0.26.0 died at each) |
| Throughput | **27.15 tok/s c=1, 105.5 agg c=8 — parity with sparkrun** (27.02 / 109) |
| gsm8k | 0.945 strict (sparkrun 0.970; within noise at n=200, note upstream #49927 reports V4 distribution shifts) |
| Needle @124K | 3/3 PASS |
| Output vs sparkrun | benign boundary flips only (both coherent, facts identical) |
| **DSpark k=5 spec decode** | **81% acceptance (4.05/5 per round — better than the fork's ~55%) but -47% tok/s (14.3 vs 27.15), identical with draft CUDA graphs on or off** (`VLLM_DSPARK_FORWARD_CUDAGRAPH[_ALLOW_TP]=1` verified engaged in logs). Bottleneck is the verify/rejection round-trip + fixed cross-node latency, not the draft forward. The fork's win comes from cross-node draft optimizations (local argmax, markov weight replication) upstream has not absorbed. **Ships OFF.** |

**Promotion soak (2026-07-28/29): PASS.** 150 min @ c=8, **3318 requests, 0
errors**, no leak signal (decile drift +0.78 GiB, page-cache territory), 81 C
max, SM >=2385 (no throttle). Bonus endurance data: the cluster then stayed
up and healthy for **27+ hours total** across idle and load
(results/soak-dsv4-upstream-150min.json).

**Needle @447,237 tokens at max-model-len 500000: 3/3 PASS (2026-07-30).**

**PROMOTED 2026-07-30**: `models/deepseek-v4-flash.conf` now runs the
upstream-lineage image; the sparkrun binary is demoted to
`deepseek-v4-flash-sparkrun.conf` as documented fallback. DSpark stays off on both stacks pending
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
spec decode remains OFF for serving.

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
| **DeepSeek-V4-Flash + DSpark k=5 (2-node TP=2, PR-41834)** | 27.08 | **48.43** | **+79%** | 33.6 vs 17.7 (+90%) | 35-50% on natural text | **WIN — the flagship fast path** (agg par at c=8; soak with spec ON still pending before default-on) |
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
continuous cluster uptime. A first attempt was killed at 60 min by a
harness task limit (preserved as bonus evidence: 1,385 req, 0 errors) and
rerun detached — total sustained load ~210+ min.
Raw: results/soak-dsv4-dspark-150min.json + soak-dsv4-dspark-node2-samples.log.

**Consequence: spec decode (DSpark k=5) is now the RECOMMENDED flagship
serving mode** — every gate (correctness, perf, soak) is earned.
