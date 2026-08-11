# Revalidation runbook — after any validation-bundle input changes

An image pin bump is the recurring event on this stack (upstream release or PR
#41834 rebase/merge), but it is not the only invalidation trigger. A model
revision/manifest, tokenizer or model code, draft/adapter, normalized profile
runtime configuration, resolved image digest, or serving geometry/topology
class change invalidates the applicable validation bundle. Nothing keeps its
`tested` status across such a change without new evidence. This is the public,
repository-relative sequence; expect roughly half a day, mostly machine time.

Model-library catalog schema 2 and hot schema 3 now enforce a reviewed
lab-issued expected model seal, exact commit/manifest comparison, and exact
snapshot launch. Schema-1 validation bundles additionally bind declared
external-artifact identities/digests, the digest-pinned image, normalized live
runtime/memory settings, geometry, provenance, and evidence. Existing rows
predate that binding and remain `legacy-unsealed`; no real profile seal or
bundle ships yet. Never
create an expected seal or bundle from arbitrary user-observed cache contents.
Recover the exact lab artifact used for the historical run or revalidate the
intended revision, then
follow [models/seals/README.md](../models/seals/README.md) in the evidence pull
request. A future mirror may distribute the bytes, but hosting location is not
validation identity. The rank-local serve witness is implemented: activation
creates it only after full verification, while unchanged launch uses metadata
and drift visibly rehashes before refresh. The standalone bundle verifier is
implemented; trusted issuance of a real bundle remains pending.

## 0. Prep (5 min)

```bash
# From the repository root. Multi-node commands require a confirmed topology;
# .env is only for optional runtime/path/auth overrides.
scripts/detect-fabric.sh --json

# Clear JIT caches locally, then repeat on every remote SSH target shown above.
# Stale Triton cache can silently corrupt on sm_121.
rm -rf ~/.cache/vllm ~/.triton 2>/dev/null

# After updating the candidate profile IMAGE pin, stage it to every exact rank.
scripts/sync-image.sh <profile> --pull --yes

# Free local memory if other workloads ran; repeat on every exact rank.
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches
```

Update the pin (`Dockerfile` digest or the conf's `IMAGE=`) in a branch.
Keep the OLD captures in results/ — they are the A/B baselines. Use a unique
`--tag`; the runner refuses to overwrite any matching artifact. Built-in
Python clients automatically use `VLLM_API_KEY` / `API_KEY` when configured.

## 1. Canary (10 min)

```bash
./serve.sh qwen3-1.7b -d               # healthy in ~2 min or the image is broken
validate/run-gates.sh qwen3-1.7b --tag <bump-tag>
./pulsar stop qwen3-1.7b
cluster/stop-cluster.sh --all
cluster/start-cluster.sh qwen3-1.7b-2node   # multi-node plumbing
cluster/stop-cluster.sh qwen3-1.7b-2node
```

The canary remains an exact two-node profile. On a larger confirmed topology it
uses ranks 0 and 1 only; extra ranks do not change this validation claim.

## 2. Single-node models (~1 h, mostly load time)

For each of `laguna-s-2.1-nvfp4`, `nemotron-3-nano-30b-nvfp4`,
`nemotron-3-super-120b-nvfp4`, `qwen3.6-27b-fp8`:

```bash
./serve.sh <name> -d && <wait healthy>
validate/run-gates.sh <served-name> \
    --baseline results/<prior-capture-runA>.json \
    --needle-tokens <its validated ctx: 250000 laguna / 125000 nano+qwen / 0 super> \
    --tag <bump-tag>
./pulsar stop <name>
```
Gate reading: same-run comparison is strict (`--require-identical`) by
default. It must be IDENTICAL for FLASH_ATTN-path models (qwen, nano). For
Laguna only, explicitly append `--allow-fp-equivalent-run-to-run` because its
known FLASHINFER-path noise is FP-equivalent. Vs-baseline must have ZERO hard
disagreements; an incomplete warmup or measured concurrency level exits
nonzero. Bench must remain within ~5% of the README table or be investigated.

**grep the engine log on every first boot** — backend selection changes
silently across versions:
```bash
docker logs vllm-<name> 2>&1 | grep -E "attention backend|MoE backend|LinearMethod|Unknown vLLM env"
```

## 3. gsm8k spot (per quant-sensitive model, ~10 min each)

```bash
HF_HUB_OFFLINE=0 lm_eval --model local-completions \
  --model_args "base_url=http://127.0.0.1:8000/v1/completions,model=<served>,tokenizer=<hf-id-or-path>,num_concurrent=16,max_retries=2,timeout=600,tokenized_requests=False" \
  --tasks gsm8k --num_fewshot 5 --limit 200 --output_path results/lm-eval-<name>-<tag>
```
ALWAYS `tokenized_requests=False`. Gate: within stderr (±0.035) of the
recorded score. For broken-tokenizer models run lm-eval inside the vLLM
container (TROUBLESHOOTING.md).

## 4. Flagship exact two-node profile (~2 h)

```bash
cluster/preflight.sh deepseek-v4-flash
cluster/start-cluster.sh deepseek-v4-flash  # default DSpark ship path; NCCL_DEBUG=INFO on first bump boot
docker logs vllm-cluster-deepseek-v4-flash 2>&1 | grep -m2 "NET/IB"   # RDMA, not TCP
# THE STOCK-KILLER STRESS SEQUENCE — all three killed stock v0.26.0:
validate/run-gates.sh deepseek-v4-flash --baseline results/dsv4-0731-dspark-capture.json --tag <bump-tag>
#   (gate 1 = 30 sequential captures; gate 3's fresh prefills = the livelock trigger)
API_KEY_VALUE="${VLLM_API_KEY:-${API_KEY:-}}"
AUTH_HEADER=()
[ -n "$API_KEY_VALUE" ] && AUTH_HEADER=(-H "Authorization: Bearer $API_KEY_VALUE")
for i in $(seq 1 8); do curl -fsS --max-time 300 "${AUTH_HEADER[@]}" http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"deepseek-v4-flash\",\"prompt\":\"topic $i:\",\"max_tokens\":60,\"temperature\":0}" -o /dev/null & done; wait
curl -fs "${AUTH_HEADER[@]}" http://127.0.0.1:8000/health && echo SURVIVED
# needle at the claimed context:
python3 validate/needle.py --model deepseek-v4-flash --context-tokens 450000 --depths 0.05 0.5 0.95
# gsm8k as in step 3 (expect ~0.945-0.97)
```

## 5. Soaks (promotion gate — background, ~5 h total)

```bash
python3 validate/soak.py --model deepseek-v4-flash --minutes 150 --concurrency 5 \
    --out results/soak-dsv4-<tag>.json        # then teardown, then:
./serve.sh laguna-s-2.1-nvfp4 -d
python3 validate/soak.py --model laguna-s-2.1 --minutes 150 --concurrency 4 \
    --out results/soak-laguna-<tag>.json
```
Gate: process must print `PASS soak` and exit **0** (default: any request
error fails; `completed>0`). MemAvailable shrink &gt;5% is a **WARN** finding
by default — review it; use `--fail-on-mem-shrink` only for strict CI.
Also check SM clock within ~2% of 2405 in the JSON summary.

## 6. New node-count or geometry promotion

Control-plane support is not a serving promotion. For each new `NODES`, TP, or
PP combination:

1. Create a separate exact profile variant with `TP × PP == NODES`, explicit
   topology/rail requirements, and a non-tested status. Do not alter a tested
   profile in place to cover another world size.
2. Use `scripts/up.sh <profile> --force` only for the deliberate experiment.
   The launcher still requires confirmed capacity and the exact topology.
3. Capture correctness and determinism against the appropriate single- or
   previously validated control; run concurrency/throughput sweeps, context
   gates appropriate to the model, a partial-rank/node-loss exercise, and the
   full soak. Re-measure collectives because adding ranks changes pair count
   and algorithms.
4. Store raw outputs under `results/` and add the exact image, hardware ranks,
   topology ID/class, TP/PP, flags, verdicts, and artifact paths to
   `VALIDATION.md`.
5. Promote only that variant to `STATUS=tested*` after every required gate
   passes. Until then it stays out of the wizard even when discovery finds
   enough nodes.

No three-node serving profile is promoted by the current ledger.

## 7. Experimental single-copy storage (conditional)

Do not inherit replicated-cache validation for `--weight-source fabric` or
`library-hot`. Follow `WEIGHT_FABRIC.md` and
`MODEL_LIBRARY_DESIGN.md`, and preserve unique result bundles for:

1. two-node replicated-local and fabric cold I/O/startup A/B;
2. three-node concurrent loading and interface-counter proof;
3. deterministic/correctness/long-context gates on the healthy fabric service;
4. interrupted load, link loss, NFS restart, owner reboot, restart loop, and
   soak recovery;
5. exact expected-versus-observed model seal and revision binding;
6. proof that non-home clients retain no complete durable model cache;
7. for library-hot, activation-created witness plus unchanged-launch fast-path,
   metadata-drift full-verify/fail-closed behavior on every rank, no-follow
   purge, and honest durable-home pin/restart dependency.
8. active-use durable-home removal protection on the confirmed physical
   topology:
   - normal last-home refusal and separate `--allow-last-home` acknowledgement;
   - retained `ready`, `verifying`, and `pinned` hot references;
   - running and stopped managed-container references;
   - fail-closed unreachable-node, Docker, and malformed-hot-state probes;
   - shared/exclusive lifecycle-lock race behavior; and
   - actual deletion only against a disposable exact HF repository, with
     sibling preservation and catalog refresh. Never delete a production or
     serving-validation canary durable home merely to prove the guard; create a
     disposable synthetic repository instead.
9. exact hot-storage admission before writes:
   - every selected rank is observed exactly once and unreachable/missing ranks
     fail closed;
   - warm-home charges zero model bytes on the home rank and exact manifest
     bytes on each non-home rank; cold stage-only charges every rank;
   - the flagship-sized dry-run preserves the default
     `max(64 GiB, 5% filesystem capacity)` reserve on every selected rank;
   - an explicit undersized hard cap blocks before mutation; and
   - no automatic eviction, reserve relaxation, or transport fallback occurs.

The current guard passed that deterministic and three-node physical gate on
2026-08-11. See
`results/model-library/model-library-home-removal-guard-20260811.json`.
Repeat the gate when removal targeting, reference observation, or lifecycle
locking semantics change.

Record a sanitized admission artifact without hostnames, node IDs, topology
IDs, IPs, interface names, or absolute paths. `budget --json` is site-local
input and must not be published verbatim.

The current policy passed deterministic contracts and a non-mutating physical
gate on 2026-08-11. It inventoried every confirmed rank, then proved exact
home-zero/non-home manifest accounting, default-reserve preservation, explicit
hard-cap refusal, and unchanged hot ownership on both DeepSeek-selected ranks. See
`results/model-library/model-library-hot-budget-admission-gate-20260811.json`.
Repeat it when admission arithmetic, hot-root accounting, selected-rank
barriers, or hot locking changes.

The path remains experimental if any artifact is absent, even when the same
model/profile/image is already `tested` with replicated weights.

## 8. Close out

- Update conf `STATUS`/`NOTES` and `docs/VALIDATION.md` with the measured
  numbers, exact model commit/manifest identity, resolved image digest,
  normalized runtime profile/geometry, selected backends, and artifact paths.
- Publish the complete lab-reviewed validation bundle and expected seal in the
  same evidence pull request, then add the profile `EXPECTED_MODEL_SEAL`
  reference. Run `scripts/model-library.sh validation-bundle verify <profile>`
  before merge. Never promote a locally observed user seal or bundle into
  expected identity.
- Mark the prior pin/rows **SUPERSEDED**; do not delete old evidence.
- Archive the new raw results under `results/` using a unique bump tag.
- Run a current-tree secret/path scan and inspect `git diff` before merge.
- Merge the pin branch only after every required gate passes.

Anything that fails: the pin does not land. File it in TROUBLESHOOTING.md
with the failing command and artifact path, keep the old pin, and open an
upstream issue when the evidence points outside this repository.
