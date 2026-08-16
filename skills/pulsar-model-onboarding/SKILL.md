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
duplicate their schemas or add a product acquisition service.

Detailed phase checklists live in
[references/workflow-phases.md](references/workflow-phases.md). The handoff
template is [references/handoff-template.md](references/handoff-template.md).

## Hard stops

- Status is advisory and never blocks serving. Concrete identity, recipe,
  compatibility, topology, capacity, security, ownership, and lifecycle
  failures still fail closed.
- Do not silently select another node, transport, storage policy, copy,
  runtime source, geometry, or validation criterion.
- Do not mutate `refs/main` to manufacture identity.
- Do not use `validate/run-gates.sh` as the ADR attempt wrapper.
- Do not invent a missing validator measurement or share one enclosing
  timestamp across compare and benchmark.
- Do not represent the default unsealed replicated path as an exact ADR 0004 qualification attempt.
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
Bind later exact revision, release ID, and contract ID as journal `ids`
when they exist. On resume, `verify` the journal against the base identity and
each ID already known, for example `--id exact_revision=<commit> --id
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

Resolve the upstream selector to an immutable exact Hugging Face commit.
Refresh the catalog first and observe every confirmed serving rank. If that
exact revision already has one durable home on an eligible serving rank, do not
trust the catalog's shallow `complete` label by itself. Reuse requires full
verification against a reviewed expected manifest that is independent of the
observed tree, followed by an explicit operator choice. Refuse a missing
reviewed expected manifest, failed or incomplete verification, a partial tree,
another revision presented as the target, a duplicate durable home, or a home
outside the supported serving geometry.

If no independently verified reusable complete exact home exists, **stop with
the implementation gap**. `scripts/model-library.sh home add` requires a
reviewed expected seal, and no current subsystem safely acquires a brand-new
unsealed model through private same-filesystem staging, a repeated all-rank
absence check, independent completeness verification, and atomic durable-home
publication. Do not run a Hugging Face download directly into the durable
cache, and do not treat a catalog label or self-observed manifest as independent
proof that an interrupted download is complete. Do not silently select another
node, transport, storage policy, or copy. A future supported large-acquisition
path still requires its own confirmation.

### 5. Catalog, resolve, manifest

Explicitly refresh the catalog, re-resolve the exact `model_id@revision`,
and refuse another revision, ambiguity, a partial tree, or a durable
duplicate:

```text
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <model_id@revision>
scripts/model-release.sh manifest <profile> \
  --hub-path <hub/models--namespace--name> --revision <exact-commit>
```

Use `manifest` only to build and full-verify the complete unreviewed
manifest consumed by the ADR planner. It records the reused tree's exact
identity; it does not retroactively prove acquisition completeness.

### 6. Select qualifying runtime access

For ADR qualification of a brand-new unsealed model, offer only an
explicitly verified runtime-access path that matches the release recipe:

- `library-hot` as `local-verified-readonly`
- explicitly selected live fabric as `live-remote-readonly` when its exact
  contract is actually satisfied

Name the chosen distribution/source and transport. There is no silent fallback
and no automatic fallback. Experimental subsystems are allowed only when
explicitly selected.

The current default unsealed replicated path follows mutable `refs/main`,
mounts the writable HF home, and passes the repository ID. It may still be
served with its honest label, but it must not be represented as an exact
ADR 0004 qualification attempt. Do not add a product-code fix for that gap.

### 7. Build and verify the release plan

After the complete manifest exists and the runtime-access choice is explicit,
build the unreviewed release and frozen Validation Contract from the agreed
criteria and runtime envelope:

```text
scripts/model-serving-release-plan.sh build <profile> \
  --artifact-manifest <snapshot-manifest.json> \
  --runtime-envelope <runtime-envelope.json> \
  --criteria <criteria.json> \
  --model-access-contract local-verified-readonly|live-remote-readonly
scripts/model-serving-release-plan.sh verify <profile> \
  --candidate-dir <release-plan-dir> \
  --model-access-contract local-verified-readonly|live-remote-readonly
```

The structural runtime envelope and geometry do not prove physical behavior.
Do not continue if verification differs from the exact artifact, profile,
runtime/image identity, geometry, or selected access contract.

### 8. Prepare, verify every rank, then launch

Invoke the owning library/fabric preparation subsystem for the selected path.
Do not duplicate its transfer, retention, or cleanup logic.

Verify the exact all-rank runtime-access barrier before qualification. A
failure here is failed preparation: qualification did not start. Then
require a separate **launch** confirmation and invoke the normal launcher
with the matching explicit weight source:

```text
scripts/up.sh <profile> --weight-source library-hot
# or, only when the live fabric contract is satisfied:
scripts/up.sh <profile> --weight-source fabric
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

### 13. Ownership-safe cleanup

Require a separate **destructive cleanup** confirmation. Use the normal
stop path (`scripts/down.sh` / `./pulsar` stop). Then use the owning
library/fabric cleanup only for resources this workflow created. Refuse
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
