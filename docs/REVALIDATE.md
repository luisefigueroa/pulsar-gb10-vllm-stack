# Revalidation runbook — after any Model Serving Release input changes

An image pin bump is the recurring event on this stack (upstream release or PR
#41834 rebase/merge), but it is not the only invalidation trigger. A model
revision/manifest, tokenizer or model code, draft/adapter, normalized profile
runtime configuration, resolved image digest, or serving geometry/topology
class change creates a new **Model Serving Release** under
[ADR 0004](./decisions/0004-model-serving-release-validation.md). No prior
release status transfers across that change. In the current implementation,
the same change invalidates the applicable schema-1 validation bundle and
nothing keeps its `STATUS=tested*` serving eligibility without new evidence.
This is the public, repository-relative sequence; expect roughly half a day,
mostly machine time.

Model-library catalog schema 2 and hot schema 3 now enforce a reviewed
lab-issued expected model seal, exact commit/manifest comparison, and exact
snapshot launch. Schema-1 validation bundles additionally bind declared
external-artifact identities/digests, the digest-pinned image, normalized live
runtime/memory settings, geometry, provenance, and evidence. The one-node
diagnostic `qwen3-1.7b` profile is the first issued identity; the flagship
`deepseek-v4-flash` profile is the second. Profiles without a reviewed seal,
including `qwen3-1.7b-2node`, remain `legacy-unsealed`. Never create an
expected seal or bundle from arbitrary
user-observed cache contents. Recover the exact lab artifact used for the
historical run or revalidate the intended revision, then follow
[models/seals/README.md](../models/seals/README.md) in the evidence pull
request. A future mirror may distribute the bytes, but hosting location is not
validation identity. The rank-local serve witness is implemented for
`library-hot` and sealed replicated caches: preparation/acquisition creates it
only after full verification, while unchanged launch uses metadata and drift
visibly rehashes before refresh. Sealed replicated download pins the exact
commit and every materialized rank is verified. Legacy-unsealed replicated
profiles and live-mount launch remain unbound.
The standalone bundle verifier is implemented. Maintainer-only
`scripts/model-release.sh` can hash an exact commit and assemble/verify
deterministic unreviewed candidates; trusted publication remains a deliberate
reviewed repository change. See [MODEL_RELEASE.md](./MODEL_RELEASE.md).

**Policy versus implementation:** ADR 0004 defines a separate release
descriptor, frozen Validation Contract, immutable run records, evidence bundle,
reviewed validation decision, and the statuses `Untested`, `Testing incomplete`,
`Tested—criteria not met`, `Tested—inconclusive`, `Validated`, and
`Superseded`. The pure release-descriptor and frozen-contract schemas are now
implemented in `scripts/model_serving_release.py`, with deterministic fixtures
and fail-closed tests. Pure immutable run-record, new evidence-bundle, reviewed
validation-decision, status-derivation, and supersession schemas are implemented
in `scripts/model_validation_evidence.py`. Together they perform no command
execution, evidence capture, persistence, trusted issuance, profile update, or
serving gate. A syntactically reviewed decision cannot prove physical behavior
or that repository review occurred. Capture/persistence, trusted publication,
CLI orchestration, status projection, and serving migration remain pending.
Existing bundles, seals, profiles, and historical evidence remain unchanged and
must not be automatically relabeled `Validated`. The corrected ADR 0004
objects remain schema version 1 because none was issued or persisted before
the correction; older schema-1 seals/bundles are separate legacy formats and
remain byte-for-byte untouched.

## Qualification scope and change impact

Revalidation is scoped before commands are chosen. A release bundle still
binds all of its exact inputs in the current serving schema. In the ADR 0004
model, the implemented release descriptor binds the four-part identity and the
implemented Validation Contract freezes criteria. The implemented stage-2
schemas separately bind immutable attempts, evidence sets, and reviewed
decisions, but no current capture or trusted persistence path emits them.
Reusable subsystem evidence is not erased by an unrelated change.
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md)
defines four scopes:

| Scope | Typical evidence | What it does not prove |
|---|---|---|
| Catalog and artifact service | Seal/manifest match, placement, transfer integrity, witness/lifecycle, retention, repair, cleanup | Any Model Serving Release validation criterion or a supported release |
| Serving integration | Exact-source mount/load, health, warmup, completion smoke, owned stop | Stability, accuracy, throughput, latency, strict same-boot, context, or soak |
| Model qualification | Stability, accuracy, throughput, latency, strict same-boot, context, and soak for exact runtime inputs | Storage-policy safety outside the tested runtime source |
| Release and promotion | Provenance/security, physical geometry, and all required subsystem results bound to one supported profile/policy | Broader geometries, images, revisions, or storage paths |

A failure in one scope blocks every release claim that depends on it, but does
not invalidate another scope without a demonstrated causal connection. Select
the smallest complete gate set from this change-impact matrix:

The machine-readable criterion mapping is fixed: stability, accuracy,
throughput, latency, and strict same-boot use `model-qualification`; serving
integration uses `serving-integration`; provenance/security and physical
geometry use `release-promotion`. `catalog-artifact` evidence establishes its
own subsystem contract and the pre-qualification verification barrier only; it
cannot satisfy a validation criterion.

| Changed input or contract | Required revalidation |
|---|---|
| Model Artifact Set content or identity: model revision/snapshot manifest, tokenizer/model code, adapter/draft, or another behavior-affecting artifact | New Model Serving Release; catalog identity/full verification, serving integration, and complete model qualification |
| Expected-seal or schema-1 bundle trust metadata only, with the Model Artifact Set and every other release input unchanged | Same Model Serving Release; issue and verify the required new schema-1 artifact IDs and cross-links, review provenance/evidence/privacy, and refresh or reprepare current hot state when its legacy identity changes. Reassess the validation decision when its evidence or review basis changes, but do not automatically rerun model qualification |
| Image, dependency, engine flags, memory contract, or geometry | New Model Serving Release; serving integration and complete model qualification; retain generic catalog mechanics unless the change affects them |
| Transfer/copy algorithm or admission policy | Same release when it still converges on the identical verified `local-verified-readonly` runtime-access contract; rerun affected catalog physical gates and integration smoke, and model gates only when runtime inputs change or evidence shows a causal execution effect |
| Catalog-manifest, witness, metadata, retention, repair, or cleanup semantics | Affected identity/lifecycle gates and integration smoke when launch views change; no automatic accuracy rerun |
| Interactive catalog/health orchestration with unchanged scan and schema semantics | Focused renderer/shell contracts plus full control-plane selftest; no new physical or model-qualification claim |
| Runtime model-access contract | New Model Serving Release when the access contract changes (for example local verified bytes to a live remote dependency); catalog/lifecycle, serving integration, and complete model qualification |
| Documentation-only policy/classification | Documentation checks and control-plane regression tests; no new physical claim |

Changing an image, model, configuration, or geometry creates a new release ID
and requires a newly frozen contract and reviewed decision under ADR 0004. The
pure libraries can build and validate all five object roles, but no current
operator or serving path captures, persists, or consumes them. A new schema-1
bundle is still required by the current serving implementation before
`STATUS=tested` or a guided claim can move. Preserving unchanged catalog
evidence is scoped evidence reuse, not release-status or bundle inheritance.
Health and completion smoke are never substitutes for model qualification.

## Validation contract and status rules

Freeze the two-layer Validation Contract before testing. Repository-wide rules
require stability, accuracy, throughput, latency, strict same-boot
reproducibility, provenance/security review, and immutable evidence. The
release-specific layer declares the actual workloads, protocols, thresholds,
sample sizes, context/soak conditions, and any comparable predecessor.

`scripts/model_serving_release.py` now enforces that frozen shape and its
release cross-links, including recipe/geometry consistency and privacy-safe
descriptor fields. The review-derived provenance/security criterion is one
closed canonical template; extra requirements are rejected because the
decision evaluator would have no run metric with which to satisfy them.
Every persisted free-form release/contract string without a stricter closed
grammar is screened for secret, absolute-path, endpoint, and deployment-only
content, including artifact identifiers, criterion/workload/protocol/threshold
strings, argument/environment values, N/A reasons, and extensible keys/values.
`scripts/testlib/test_model_serving_release.py` verifies the
schema contract during `scripts/selftest.sh`; it does not collect a run or
establish that any physical criterion passed.

`scripts/model_validation_evidence.py` validates the next evidence layer. Each
run names the exact release and contract, hash-binds sorted
`attempted_criterion_ids`, uses rank-relative observations and
opaque boot/launch identities, records exact command descriptors and
distribution/subsystem provenance, and distinguishes failure before the full
verification barrier from qualification. Evidence artifacts are content
addressed and explicitly `publishable` or `protected`, with privacy-review
state. Before the barrier, a preparation failure declares no attempted
criterion. After the barrier, every non-preparation attempt must declare at
least one scope-compatible criterion and provide exactly one corresponding
complete or inconclusive observation for each; an incomplete attempt may only
provide inconclusive observations. A bundle must resolve the exact run and
artifact sets. A decision must
consider every applicable observation automatically; its supplied status must
equal the independently derived result. Excluding an otherwise applicable
observation requires an entry in the builder's `criterion_exclusions` input,
and the decision retains it under
`criterion_results[].excluded_run_records`; omission is not a selection
mechanism. Included observations are recorded in
`criterion_results[].included_run_record_ids`. For one criterion, pass+fail
and pass+inconclusive aggregate to
inconclusive, fail+inconclusive aggregates to fail, and all-pass aggregates to
pass. The validator recomputes those results, required context and soak
conditions, applicable predecessor-relative performance budgets, and the only
permissible status. Missing comparison evidence cannot validate, and an
over-budget comparison is a conclusive failure. A mismatch fails closed.
Experimental distribution maturity is provenance and does not cap status.

A relative baseline must cross-link the reviewed predecessor contract,
evidence bundle, validation decision, and exact run. The relevant predecessor
throughput or latency criterion must have passed in that decision. The
predecessor release itself need not be globally `Validated`; unrelated criteria
do not invalidate a criterion-specific baseline. The benchmark protocol and
supported geometry must still be identical. The current pure resolver fails
closed if the selected predecessor decision itself has supersession links,
because its source registry does not yet carry the complete prior-decision
evidence lineage needed to validate them.

Observed runtime compatibility and architecture/geometry are checked
structurally against the release envelope. This can reject an incompatible run
but cannot prove physical behavior; serving-integration and physical-geometry
criteria need physical DGX evidence. Command descriptors use a closed schema:
an allowlisted repository program, a `sha256:<digest>` program-version
identity, exactly one allowed operation, closed repository resources, typed
criterion references, typed protected/rank references for site-bearing
options, `environment[]` references without values or credential-shaped names,
and the generic
repository-root working directory. `observed_environment.cluster` and
per-rank compatibility observations must match the release structurally, and a
soak observation's `started_at`, `ended_at`, and `duration_seconds` must agree
exactly. A later
decision points backward to prior decision IDs only with strictly later
chronology and an acyclic relationship; readers project the older one as
`Superseded` without rewriting it. `predecessor_evidence_registry` and
`decision_evidence_registry` are complete caller-supplied source registries for
pure validation; neither is trusted persistence or evidence of issuance.

These functions validate supplied JSON-compatible objects only. Until capture
and persistence land, continue retaining the existing raw/sanitized artifacts
and current schema-1 release materials described below. Do not create a
`Validated` claim merely by calling a builder or copying a synthetic document.

Structural privacy checks are fail-closed for recognized credential, path,
endpoint, and topology forms, but they are not a complete privacy proof. The
future trusted capture path must compute program digests from the checked-out
files, and publication must still run the repository privacy audit and reviewer
inspection for unknown site codenames.

FP-equivalent output does not pass strict same-boot reproducibility. If no
reviewed criterion-passing comparable predecessor exists, the relative gate is
`N/A` and absolute release-specific criteria still apply. Record every attempt,
including interrupted, failed, and inconclusive runs. A pre-qualification
acquisition or distribution failure leaves the release `Untested`; a completed
criterion failure is
`Tested—criteria not met`; noisy or insufficient evidence is
`Tested—inconclusive`; missing gates or review is `Testing incomplete`; and
only a complete reviewed pass is `Validated`.

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
3. Capture correctness and determinism against the appropriate control; run
   concurrency/throughput sweeps, context gates appropriate to the model, a
   partial-rank/node-loss exercise, and the full soak. A relative performance
   control must bind a reviewed predecessor contract, bundle, decision, and run
   whose relevant criterion passed; the predecessor need not be globally
   `Validated`. Re-measure collectives because adding ranks changes pair count
   and algorithms.
4. Store raw outputs under `results/` and add the exact image, hardware ranks,
   topology ID/class, TP/PP, flags, verdicts, and artifact paths to
   `VALIDATION.md`.
5. Promote only that variant to `STATUS=tested*` after every required gate
   passes. Until then it stays out of the wizard even when discovery finds
   enough nodes.

No three-node serving profile is promoted by the current ledger.

## 7. Experimental single-copy storage (conditional)

Do not inherit **serving integration or model qualification** from the
replicated-cache path for `--weight-source fabric` or `library-hot`. Generic
catalog evidence may be reused only when its measured identity, placement,
transfer, and lifecycle contracts are unchanged. Follow `WEIGHT_FABRIC.md`,
`MODEL_LIBRARY_DESIGN.md`, and
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md), and
preserve unique result bundles for the affected scopes:

When the reviewed-profile interactive experiment is the subject of the gate,
[ADR 0003](./decisions/0003-explicit-model-preparation-transport.md) fixes its
copy policy to topology-bound `ssh-roce` with eight streams and no fallback.
Catalog refresh is not model acquisition; establish and verify the exact
durable home separately before running preparation.

1. two-node replicated-local and fabric cold I/O/startup A/B;
2. three-node concurrent loading and interface-counter proof;
3. deterministic/correctness/long-context gates on the healthy fabric service;
4. interrupted load, link loss, NFS restart, owner reboot, restart loop, and
   soak recovery;
5. exact expected-versus-observed model seal and revision binding;
6. proof that non-home clients retain no complete durable model cache;
7. for library-hot, preparation-created witness plus unchanged-launch fast-path,
   metadata-drift full-verify/fail-closed behavior on every rank, no-follow
   purge, and honest durable-home pin/restart dependency.
8. active-use durable-home removal protection on the confirmed physical
   topology:
   - duplicate discovery refuses resolution before selection;
   - exact-revision primary selection survives catalog refresh;
   - primary selection rejects a catalog rank/node that differs from confirmed
     topology;
   - a missing selected home becomes `stale` without auto-election;
   - cleanup guidance emits no removal command before selection and targets
     only explicit non-primary ranks afterward;
   - direct `home remove --node` remains blocked before selection;
   - selected-primary removal remains blocked until the intended survivor is
     selected;
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
10. read-only health and legacy-hot repair on every confirmed physical rank:
   - use only an isolated disposable hot root with tiny synthetic schema-1/2
     instances; never target real `/var/tmp/pulsar-hot` or model caches;
   - health reports every rank, cached-catalog/primary state, legacy metadata,
     and Docker/SSH loss without hashing or mutation;
   - stopped managed-container and pinned state block removal;
   - removal requires a current health-issued ID plus `--yes`, and pinned state
     additionally requires `--force-unpin`;
   - stale ID, schema 3, malformed ownership, symlinked root/target, and
     ambiguous/unobservable targets fail closed;
   - atomic retirement preserves a sibling instance and an external sentinel
     reached only through an embedded symlink;
   - incomplete retirement is rediscovered and retryable; and
   - the disposable legacy removal unblocks `home check`, followed by an exact
     disposable-home removal repeat and sibling-preservation proof.
11. interactive model-storage delegation when its eligibility or command
    contract changes:
    - browsing and health recheck remain mutation-free;
    - stale topology, missing expected identity, missing primary, unsealed
      profile metadata, and invalid profile JSON suppress preparation;
    - the preview names exact revision/manifest, durable-home dependency,
      approximate non-home storage, and the selected transfer policy;
    - confirmation defaults to no and decline invokes no mutation;
    - multi-node acceptance delegates exactly to reviewed-profile eight-stream
      SSH-over-RoCE copy; one-node acceptance targets the durable-home rank with
      `ssh-control`, one stream, and no bulk transfer; neither path has fallback
      or `--allow-unvalidated`;
    - success and failure both trigger fresh health, never claim launch or
      promotion, and preserve service diagnostics; and
    - repeat physical preparation only if the underlying identity, topology,
      admission, transfer, rollback, witness, or lifecycle contract changes.
      Pure UI delegation may reuse matching physical service evidence.
12. serving-wizard catalog delegation when source selection, placement,
    readiness, restart, or stop-retention behavior changes:
    - replicated weights remain the first/default choice and never invoke
      catalog health, preparation, or hot cleanup;
    - the experimental choice discloses exact revision/manifest,
      durable-home dependency, selected ranks, transfer policy, and no fallback;
    - stale or invalid health blocks the catalog path but may offer replicated
      only as a separate operator choice;
    - successful preparation is followed by fresh health and exact all-rank
      readiness before `--weight-source library-hot` reaches weight preflight
      or launch;
    - preparation and launch retain separate confirmations, and failed or
      incomplete preparation cannot launch;
    - one-node catalog preparation and launch use the durable-home rank; a
      non-home choice fails closed instead of creating a second hot copy;
    - a replacement snapshots the exact live launch contract, physical placement,
      source, spec state, and library identity/runtime/retention before stop;
      ephemeral catalog views are pinned until confirmed replacement or exact
      rollback, while incomplete, ambiguous, legacy-unlabeled, or drifted state
      fails before mutation;
    - failed launch and interrupted-wizard recovery restore only the saved source,
      placement, and spec state, and remove transaction state only after success;
    - confirmed same-source restart pins before stop, ordinary stop purges only
      unpinned views, and explicit pin remains durable-home dependent; and
    - repeat physical serving integration when the selected placement or
      runtime-resolution algorithm changes. Existing evidence may be reused
      only for the exact placement and contract it measured.
13. reviewed durable-home acquisition when download, placement, staging, or
    publication behavior changes:
    - every confirmed rank is observed and exact rank/node identity is bound;
    - only a sealed profile and immutable reviewed commit are accepted;
    - automatic placement chooses the eligible most-free-space serving rank;
      one-node profiles may select any confirmed rank while multi-node profiles
      remain in their exact geometry, preserving one home plus N−1 hot copies,
      and an explicit ineligible or out-of-geometry `--node` fails without
      fallback;
    - target-side Hugging Face CLI, authentication/egress, and capacity failure
      leave no published home;
    - download occurs in plan-owned same-filesystem staging and interruption or
      verification failure removes only that staging tree;
    - an existing repository before download or one appearing on any rank
      during download blocks publication;
    - full expected-manifest SHA-256 verification precedes the atomic rename;
    - one exact durable home is visible after explicit catalog refresh, with no
      non-home durable repository or hot copy created; and
    - the result states that catalog refresh, preparation, launch, model
      qualification, and release promotion did not occur.

The reviewed acquisition contract passed this three-node physical gate on
2026-08-13 using the sealed `qwen3-1.7b` profile. The run proved guarded removal
of the prior final home, interrupted remote staging cleanup, explicit remote
placement, automatic most-free-space remote placement, full reviewed-manifest
verification, atomic publication, explicit catalog refresh, and a final
one-home/no-hot state. See
`results/model-library/qwen3-1.7b-home-acquisition-gate-20260813.json`.

The current guard passed that deterministic and three-node physical gate on
2026-08-11. See
`results/model-library/model-library-home-removal-guard-20260811.json`.
Repeat the gate when removal targeting, reference observation, or lifecycle
locking semantics change. Persistent-primary implementation changes removal
targeting by adding a selected-primary blocker. Its deterministic contracts and
a three-node disposable-repository physical targeting repeat passed on
2026-08-12; see
`results/model-library/model-library-primary-selection-reconciliation-gate-20260812.json`.
Repeat again after any later targeting, observation, or locking change. The
repeat did not reconcile the existing DeepSeek duplicate or promote the path.
Health/reference observation and hot-lock behavior changed with the guarded
legacy repair service. Gate 10 and the affected disposable home-removal subset
passed a three-node repeat on 2026-08-12. The run proved local and remote
repair, stopped-container and pinned blockers, no-follow/sibling preservation,
continued attention for preserved untracked content, and exact disposable-home
removal. See
`results/model-library/model-library-health-legacy-repair-gate-20260812.json`.

Gate 13 has deterministic Python and thin public-CLI coverage plus the physical
Qwen acquisition artifact cited above. Gate 12 has deterministic wizard,
placement, and lifecycle coverage. Its production two-node DeepSeek wizard
flow passed physically on 2026-08-13, including explicit source selection,
separate preparation and launch confirmations, fresh exact readiness,
read-only exact-snapshot serving, warmup, completion, interactive owned stop,
unpinned purge, and return to one durable home; see
`results/model-library/deepseek-v4-flash-serving-wizard-gate-20260813.json`.
The new remote one-node wizard path still needs its own serving-integration
repeat before that placement receives a physical claim. The short-lived exact
replacement transaction has deterministic Python, inventory, replicated-switch,
and catalog rollback coverage. Because it changes pre-stop retention and
failed-launch recovery, repeat a physical two-node `library-hot` replacement
failure/rollback plus interrupted recovery before claiming that new failure
path on DGX hardware. Existing successful-launch and model-qualification
evidence is not relabeled as that result. Neither gate promotes
the storage path or supplies model-qualification evidence.

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

Catalog and integration gates may be recorded as accepted in their own scopes.
The path remains ineligible for guided/default release promotion if any required
artifact is absent, even when the same model/profile/image is already `tested`
with replicated weights.

## 8. Close out

- Classify each result as catalog/artifact, serving integration, model
  qualification, or release/promotion. Include every applicable observation,
  record any evidence-backed exclusion, and let the frozen-contract rules
  derive the ADR 0004 decision status without hiding conflicts, failures, or
  missing evidence. Until the
  status migration is implemented, update conf `STATUS`/`NOTES` only under its
  current legacy contract and only when the complete serving-eligibility scope
  passes. Update `docs/VALIDATION.md` with the measured
  numbers, exact model commit/manifest identity, resolved image digest,
  normalized runtime profile/geometry, selected backends, and artifact paths.
- Build the exact manifest and unreviewed documents with
  `scripts/model-release.sh manifest` and `assemble`, then run
  `verify-candidate` against the final profile. Candidate output is not a
  trusted claim and stays outside `models/`.
- Review provenance, evidence privacy, exact inputs, the frozen contract, and
  reproducibility. Only then publish the complete lab-reviewed current-schema
  validation bundle and expected seal in the same evidence pull request and add
  the profile
  `EXPECTED_MODEL_SEAL` reference. Run
  `scripts/model-library.sh validation-bundle verify <profile>` before merge.
  Never promote a locally observed user seal or bundle into expected identity.
- Mark the prior pin/rows **SUPERSEDED**; do not delete old evidence.
- Archive the new raw results under `results/` using a unique bump tag.
- Store publishable command provenance as structured, sanitized descriptors;
  never include environment values, secrets, absolute site paths, or private
  topology identifiers.
- Run a current-tree secret/path scan and inspect `git diff` before merge.
- Merge the pin branch only after every required gate passes.

Anything required by a `Validated` claim that fails prevents that decision.
Record `Tested—criteria not met`, `Tested—inconclusive`, or
`Testing incomplete` as applicable; do not invent `Tested—meets criteria`.
Preserve successful evidence in its narrower scope, classify the failure, and
file it in TROUBLESHOOTING.md with the failing command and artifact path, keep
the old pin, and open an
upstream issue when the evidence points outside this repository.
