---
name: pulsar-model-onboarding
description: Supervise onboarding of a brand-new, not-yet-sealed Pulsar model through a separately reviewed draft profile, exact artifact acquisition assessment or safe reuse, explicit qualifying distribution, verification, Model Serving Release planning, launch, currently supported physical measurements, unreviewed evidence capture, handoff, and ownership-safe cleanup. Use for new-model onboarding, qualification planning, Model Serving Release evidence collection, resuming an interrupted onboarding, or when the user runs /pulsar-model-onboarding.
---

# Pulsar Model Onboarding

Orchestrate a brand-new, not-yet-sealed model by composing existing Pulsar
CLIs. Collaborate at material decisions. This skill has no authority: never issue a seal or validation decision, assign a status, bind a profile to a
release, publish into the trusted registry, promote a path, or claim physical behavior.

Read `AGENTS.md`, `docs/MODEL_LIBRARY_DESIGN.md`, and ADRs 0001–0004 before
acting. ADR 0004 section 7 and staged item 4 govern this skill. Reuse
`scripts/model-serving-release-plan.sh`,
`scripts/model-serving-release-attempt.sh`,
`scripts/model-serving-release-capture.sh`, `scripts/model-library.sh`,
`scripts/up.sh`, the normal stop path, and
`validate/{greedy_capture.py,compare_captures.py,bench_serve.py}`. Do not
duplicate their schemas or bypass the model-library acquisition service.

Detailed phase checklists live in
[references/workflow-phases.md](references/workflow-phases.md). The handoff
template is [references/handoff-template.md](references/handoff-template.md).

## Hard stops

- Status is advisory and never blocks serving. Concrete identity, recipe,
  compatibility, topology, capacity, security, ownership, and lifecycle
  failures still fail closed.
- Do not silently select another node, transport, storage policy, copy,
  runtime source, geometry, or validation criterion.
- Do not offer live NFS/RDMA serving (`live-remote-readonly`) as a
  qualifying runtime-access path (ADR 0005). A crashed rank cannot
  cold-start without the owner export.
- Do not mutate `refs/main` to manufacture identity.
- Do not use `validate/run-gates.sh` as the ADR attempt wrapper.
- Do not invent a missing validator measurement or share one enclosing
  timestamp across compare and benchmark.
- Do not represent an unsealed (`identity_status=legacy-unsealed`) launch as an exact ADR 0004 qualification attempt.
- Distribution transport is run provenance, not release identity. A failure
  before exact all-rank verification leaves qualification unstarted.
- The journal is orchestration recovery state, not a sixth ADR object and
  not evidence.

## Collaboration

Ask or confirm before acting at every material decision. Require **separate
confirmations** immediately before:

1. **large acquisition**
2. **launch** or replacement
3. **destructive cleanup**

Distribution/source choice must also be explicit and visible. Record each
confirmed choice in the journal after the journal exists. If the user
refuses, stop and record `refused`.

## Workflow

### 1. Draft profile PR, then stop

Start from a clean synced `main` and create a feature branch. First create
**only** a draft profile recipe:

- `STATUS="untested"`
- `FIRST_RUN_CANDIDATE=0`
- digest-pinned `IMAGE=...@sha256:...`
- no `EXPECTED_MODEL_SEAL`
- no `MODEL_SERVING_RELEASE_ID`
- no recommendation or default promotion (`FAMILY_RECOMMENDED=0`,
  `RECOMMENDED_SPEC=0`)
- explicit unbound caveats in `NOTES` (no reviewed identity, no release
  binding, advisory status only, not a recommendation)

Publish that profile as its own ready-for-review PR and **stop**.
Do not begin the onboarding journal until the user reports that PR merged.
Then this agent syncs local main and creates a new feature branch.

### 2. After merge: journal and resume identity

Sync local `main` to the merged commit. Create a new feature branch. Only
then initialize the journal with
[scripts/onboarding_journal.py](scripts/onboarding_journal.py):

```text
python3 skills/pulsar-model-onboarding/scripts/onboarding_journal.py initialize \
  --workflow-id <safe-id> --profile <profile> \
  --public-model-id <org/name> \
  --repository-base-commit <merged-main-sha> \
  --profile-base-commit <merged-main-sha>
```

Default state is gitignored
`experiments/model-onboarding/workflows/<workflow-id>/`. This namespace is
separate from the planner's `<profile>/<release-id>/` default.
Bind later exact revision, source digest, acquisition approval, receipt,
release ID, and contract ID as journal `ids` when they exist. On resume,
`verify` the journal against the base identity and each ID already known, for
example `--id exact_revision=<commit> --id receipt_id=<digest> --id
release_id=<id> --id contract_id=<id>`, before appending. Stop on identity
mismatch, an attempted ID rebind, tamper, truncation, or a broken hash/sequence
chain.

### 3. Agree the complete criteria input

Ask for or confirm release-specific criteria **before testing**. Freeze the
criteria input before any measurement, but do not claim that a Validation
Contract exists yet: the planner also needs the exact artifact manifest and
selected runtime-access contract. State clearly that current automated mapping
covers only **strict same-boot**, **absolute throughput**, and **absolute
latency**. Other required criteria remain unevaluated/incomplete until
separately captured. Relative performance is **N/A** unless a valid reviewed
comparable predecessor is explicitly supplied.

### 4. Exact-home assessment and safe reuse

Refresh the catalog and observe every confirmed serving rank first. If one exact home
already exists, follow the reuse rules below. If the repository path is absent
on every rank, resolve the upstream selector through a read-only
source-attested plan:

```text
scripts/model-library.sh home add <profile> --revision <selector> --plan --json
```

The plan asks in-geometry candidate ranks with modern `hf` to resolve the
selector and complete upstream Git/LFS inventory. A rank that cannot resolve
the source is ineligible, and every successful rank must report the same
source. The plan observes every confirmed rank and selects one eligible
durable-home rank that already resolved that source. It does not download
model bytes.
Review its exact commit, file and byte counts, selected rank, serving ranks,
identity class, and explicit no-promotion boundary.

The source-attested plan refuses to create a duplicate if a repository appears
between assessment and planning. Reuse a home created by this service only after
`home verify` completes an offline full SHA-256 rehash against the immutable
site-local receipt while occupancy names that live directory. Occupancy may
move with `scripts/model-library.sh home relocate <profile> --node RANK --yes`
after that same live rehash; do not Hub re-download. An older tree without a
receipt still fails closed (ADR 0012: expected-manifest fallback is retired).
Refuse a
missing required receipt or reviewed manifest, failed verification, a partial
tree, another revision, a duplicate occupancy home, or an out-of-geometry home.
An unbound-complete tree with a compatible receipt is relocate, not re-add.
Neither the catalog's shallow `complete` label nor a self-observed manifest is
independent completeness evidence for an older home.

If the exact home is absent, ask for the separate **large acquisition**
confirmation against the exact commit shown by the plan. After confirmation,
pass that commit—not the mutable selector—to the supported service:

```text
scripts/model-library.sh home add <profile> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json
```

The service rechecks the source and topology on that reviewed rank, downloads
there using that rank's local Hugging Face authentication, confines model and Xet
cache bytes to private same-filesystem staging, verifies the complete upstream
inventory, runs Hugging Face missing/extra verification, hashes every file,
rechecks all-rank absence, writes the immutable site-local receipt, publishes
the home atomically, and attaches occupancy to the exact published directory.
Do not wait for a cold NFS archive; that is durability, not a serving gate.
Record archive pending in the journal when a receipt exists. Prepare and
launch do not require archive-complete.
It does not refresh the catalog, prepare a
runtime view, launch, issue a seal or decision, assign status, or promote a
path. Record the result's exact revision, `source_digest`, `approval_id`, and
`receipt_id` in the journal. Do not run a Hugging Face download directly into
the durable cache or silently select another node, transport, storage policy,
or copy.

### 5. Catalog, resolve, manifest

Explicitly refresh the catalog, re-resolve the exact `model_id@revision`,
run the receipt-backed offline verification for a source-attested home, and
refuse another revision, ambiguity, a partial tree, or a durable duplicate:

```text
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <model_id@revision>
scripts/model-library.sh home verify <model_id@revision> --json
```

The source-attested receipt and `home verify` full-hash are the live identity
for the planner's artifact manifest. `scripts/model-release.sh` is retired
(ADR 0012). Do not assemble expected-seal or schema-1 bundle candidates.

### 6. Select qualifying runtime access

For ADR qualification of a brand-new unsealed model, confirm
`library-hot` as `local-verified-readonly` after the exact rank-local
verified views exist.

Do not offer live NFS/RDMA (`live-remote-readonly`) as a serving or
onboarding alternative (ADR 0005). A crashed rank cannot cold-start without
the owner's export, NFS/RDMA stack, and exact route. Library serving
already presents local files on every rank.

Name the chosen distribution/source and transport. There is no silent fallback
and no automatic fallback.

An unsealed profile serves with `identity_status=legacy-unsealed` after
full verification; an unattested `home add` acquisition follows mutable
`refs/main` at selection time. Both may be served with their honest labels,
but neither is an exact ADR 0004 qualification attempt on its own. Do not
add a product-code fix for that gap.

### 7. Build and verify the release plan

After the complete manifest exists and the runtime-access choice is explicit,
build the unreviewed release and frozen Validation Contract from the agreed
criteria and runtime envelope:

```text
scripts/model-serving-release-plan.sh build <profile> \
  --artifact-manifest <snapshot-manifest.json> \
  --runtime-envelope <runtime-envelope.json> \
  --criteria <criteria.json> \
  --model-access-contract local-verified-readonly
scripts/model-serving-release-plan.sh verify <profile> \
  --candidate-dir <release-plan-dir> \
  --model-access-contract local-verified-readonly
```

The structural runtime envelope and geometry do not prove physical behavior.
Do not continue if verification differs from the exact artifact, profile,
runtime/image identity, geometry, or selected access contract.

### 8. Prepare, verify every rank, then launch

Invoke the owning library preparation subsystem for the selected path.
Do not duplicate its transfer, retention, or cleanup logic.

Verify the exact all-rank runtime-access barrier before qualification. A
failure here is failed preparation: qualification did not start. Then
require a separate **launch** confirmation and invoke the normal launcher
(the model library is the only weight mechanism — ADR 0006):

```text
scripts/up.sh <profile>
```

### 9. Same-boot identities

Derive privacy-safe `launch_id` and `server_boot_id` from immutable observed
runtime/container facts and the launch contract. Never use the workflow-journal
ID. Use domain-separated canonical hashes: bind `launch_id` to the public launch
contract plus every serving-rank container ID and creation timestamp in rank
order; bind `server_boot_id` to that launch ID plus every rank's observed
container start timestamp. Persist only the hashes.
Reobserve them before correctness, after correctness, and after benchmarking.
Never combine measurements when either identity changes.

### 10. Sequential measurements

Do not use `validate/run-gates.sh` as the ADR attempt wrapper. Invoke the
existing programs sequentially:

1. Persist the frozen invocation plan before measuring:
   `scripts/model-serving-release-attempt.sh plan-invocation --release-plan
   <dir> --output <invocation-plan.json>`. Confirm that the non-empty prompt
   set has the plan's exact compare sample size.
2. Reobserve `launch_id` / `server_boot_id`.
3. `python3 validate/greedy_capture.py --model <served-name> --out <runA>`
4. `python3 validate/greedy_capture.py --model <served-name> --out <runB>`
5. Reobserve identities.
6. Record compare wall-clock UTC `started_at`, then run
   `python3 validate/compare_captures.py <runA> <runB> --require-identical
   --result-json <results/.../compare.json>`, then record compare `ended_at`.
7. Reobserve identities.
8. Load benchmark arguments without `eval`, for example with Bash `mapfile`,
   from `scripts/model-serving-release-attempt.sh bench-argv --invocation-plan
   <invocation-plan.json>`.
9. Record benchmark wall-clock UTC `started_at`, then run
   `python3 validate/bench_serve.py --model <served-name>
   "${bench_args[@]}" --result-json <results/.../bench.json>`, then record
   benchmark `ended_at`.
10. Reobserve identities.

Use separate wall-clock UTC start/end timestamps for compare and
benchmark. Never share one enclosing timestamp. Stop after an
interruption. A nonzero validator exit with a complete closed measurement is
failed evidence and must be preserved, not rewritten or discarded.
Never fabricate a missing validator measurement. Closed measurement documents
used by the attempt composer must live at privacy-reviewed repository-relative
`results/` paths.

### 11. Attempt, capture, verify

If a signal prevents the validator from writing its closed measurement,
retain the journal event and report the current capture gap rather than
inventing an ADR run.

Otherwise compose attempt specs through
`scripts/model-serving-release-attempt.sh compose`, capture each immediately
through `scripts/model-serving-release-capture.sh capture-run`, assemble
compatible runs, and verify the unreviewed candidate. Capture immediately
after compose. Do not persist a planner path or planner candidate ID.

### 12. Handoff

Follow [references/handoff-template.md](references/handoff-template.md).
List completed evidence, missing criteria, failures/inconclusive results,
candidate locations, and that no reviewed status or authority was produced.
Do not run `scripts/model-serving-release-issue.sh`; that maintainer
workflow is a later, separate trust event. Use
`skills/pulsar-model-serving-release-issuance/` after this handoff.
Compose/capture of compare and bench leaves
`review_evidence_artifact_ids` empty; that is expected. This skill does
not produce provenance review leftovers.

### 13. Ownership-safe cleanup

Require a separate **destructive cleanup** confirmation. Use the normal
stop path (`scripts/down.sh` / `./pulsar` stop). Then use the owning
library cleanup only for resources this workflow created. Refuse
when a managed service still uses the resource. Do not `docker rm`
unrelated workloads.

## Resume

On resume, `verify` then `show` the journal. Continue from the last
completed phase. Do not skip the profile-PR merge gate if the journal does
not exist. Do not restart acquisition, launch, or cleanup without a fresh
confirmation.

## Journal helper

```text
python3 skills/pulsar-model-onboarding/scripts/onboarding_journal.py \
  initialize|append|verify|show [options]
```

Keep only phases, outcomes, explicit choices, IDs/digests, and safe
repository-relative candidate/evidence references. Reject credentials, raw
environment values, hostnames/addresses, topology/node identifiers,
absolute paths, and embedded release/contract/run/bundle/decision objects.
