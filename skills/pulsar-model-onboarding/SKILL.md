---
name: pulsar-model-onboarding
description: Supervise onboarding of a brand-new Pulsar model under ADR 0017 Stage 4: a lab draft, exact artifact acquisition or safe reuse, a measured release spec, preparation, launch of the measured spec by id, the baseline-v1 run, and the promotion pull request. Use for new-model onboarding, resuming an interrupted onboarding, or when the user runs /pulsar-model-onboarding.
---

# Pulsar Model Onboarding

Orchestrate a brand-new model by composing existing Pulsar CLIs.
Collaborate at material decisions. This skill has no authority: never set a
spec's `review.status`, write under `releases/`, promote a path, or claim
physical behavior. A spec becomes released only through a reviewed
promotion pull request that a maintainer merges.

Read `AGENTS.md` (its ADR 0017 section), `docs/PULSAR_SPLIT_PLAN.md`,
`docs/MODEL_LIBRARY_DESIGN.md`, `docs/REVALIDATE.md`, and
[ADR 0017](../../docs/decisions/0017-release-spec-is-the-release-contract.md)
before acting. Reuse `scripts/model-library.sh`, `scripts/release-spec.sh`,
`scripts/release.sh`, `scripts/up.sh`, the normal stop path, and
`validate/baseline-v1.sh`. Do not duplicate their schemas or bypass the
model-library acquisition service.

Detailed phase checklists live in
[references/workflow-phases.md](references/workflow-phases.md). The handoff
template is [references/handoff-template.md](references/handoff-template.md).

## What a profile is now

A profile is a released spec id under `releases/`. `models/*.conf` no
longer exists. A conf-format file survives only as a **lab draft** that two
commands read: `scripts/model-library.sh home add --draft <draft.conf>` (the
first acquisition of a new identity) and `scripts/release-spec.sh from-draft
<draft.conf>` (the measured spec). Nothing in the stack starts a draft.
Before promotion the measured spec file is the startable profile:
`PULSAR_SPEC_FILE=<measured-spec> ./pulsar start <spec_id>`. The served
name and port come from the gitignored deployment overlay
(`.pulsar-overlay.json`), never from the spec.

## Hard stops

- Review status is display-only and never blocks serving. Concrete
  identity, recipe, compatibility, topology, capacity, security, ownership,
  and lifecycle failures still fail without fallback.
- Do not silently select another node, transport, storage policy, copy,
  runtime source, geometry, image, or gate.
- Do not offer live NFS/RDMA serving as a runtime-access path (ADR 0005).
- Do not mutate `refs/main` to manufacture identity. Pass the exact commit
  the plan printed, never a mutable selector, to the acquisition.
- Do not edit a measured spec by hand, and never write under `releases/`
  except through the promotion command and its reviewed pull request.
- Do not invent a missing measurement. A failed gate is evidence: keep the
  run directory and record `failed`.
- Keep the draft outside tracked paths that the stack reads. Drafts under
  `scripts/testdata/drafts/` are selftest fixtures, not onboarding input.

## Collaboration

Ask or confirm before acting at every material decision. Require
**separate confirmations** immediately before:

1. **large acquisition**
2. **launch** or replacement of a running service
3. **destructive cleanup**

Distribution/source choice must be explicit and visible. Record each
confirmed choice in the journal after the journal exists. If the user
refuses, stop and record `refused`.

## Workflow

### 1. Draft, then stop

Start from a clean synced `main` and create a feature branch. Write a
conf-format draft in a gitignored lab location (for example
`experiments/drafts/<name>.conf`) with `MODEL`, a digest-pinned
`IMAGE=...@sha256:...`, `NODES`, `GPU_MEM_UTIL`, `ENGINE_ARGS`, and
`CONTAINER_ENV`. No status, recommendation, or release field exists any
more. Review the draft with the user and **stop** until they confirm it;
nothing in the repository changes yet.

### 2. Journal and resume identity

Initialize the journal with
[scripts/onboarding_journal.py](scripts/onboarding_journal.py):

```text
python3 skills/pulsar-model-onboarding/scripts/onboarding_journal.py initialize \
  --workflow-id <safe-id> --profile <draft-name> \
  --public-model-id <org/name> \
  --repository-base-commit <main-sha> \
  --profile-base-commit <main-sha>
```

Default state is gitignored
`experiments/model-onboarding/workflows/<workflow-id>/`. Bind the exact
revision, the receipt id, and later the spec id as journal `ids` when they
exist (`--id exact_revision=<commit>`, `--id receipt_id=<digest>`,
`--id spec_id=<id>`). On resume, `verify` the journal against the base
identity and every known id before appending. Stop on identity mismatch,
an attempted id rebind, tamper, truncation, or a broken hash/sequence chain.

### 3. Exact-home assessment and safe reuse

Refresh the catalog and observe every confirmed serving rank first. If an
exact receipted home already exists for `model_id@commit`, reuse it: run
`scripts/model-library.sh home verify <model_id@revision> --json` (offline
full rehash against the immutable receipt) and skip acquisition. If the
repository is absent on every rank, resolve the upstream selector through a
read-only plan (recorded file list, no download):

```text
scripts/model-library.sh home add --draft <draft.conf> \
  --revision <selector> --plan --json
```

Review its exact commit, file and byte counts, selected rank, and serving
ranks. Refuse a partial tree, another revision, a duplicate home, or an
out-of-geometry home. An older tree without a receipt fails without
fallback.

### 4. Acquire

Ask for the separate **large acquisition** confirmation against the exact
commit the plan showed, then pass that commit to the service:

```text
scripts/model-library.sh home add --draft <draft.conf> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json
```

The service downloads on that rank with its local Hugging Face
authentication, verifies the complete upstream inventory, hashes every file,
writes the immutable receipt, and publishes the home atomically. It does not
refresh the catalog, prepare a runtime view, launch, or promote anything.
Record the exact revision and `receipt_id` in the journal.

### 5. Measured spec

Generate the measured spec from the draft, the receipt, and the stack
version, and keep it in the lab evidence location:

```text
scripts/release-spec.sh from-draft <draft.conf> \
  --receipt <receipt.json> --stack-version <main-sha> \
  --out <measured-spec.json> --gap-report <gaps.json>
python3 -m release_spec id <measured-spec.json>   # the spec id
```

A blocking gap (unpinned image, missing receipt) stops here. Record the
spec id in the journal. From this point the spec id is the profile name.

### 6. Catalog, prepare, verify every rank

```text
scripts/model-library.sh catalog refresh
PULSAR_SPEC_FILE=<measured-spec.json> \
  scripts/model-library.sh prepare <spec_id> --yes
PULSAR_SPEC_FILE=<measured-spec.json> \
  scripts/check-weights.sh <spec_id>
```

Preparation full-verifies each rank view against the spec manifest. A
failure here is failed preparation: measurement did not start.

### 7. Launch the measured spec

Require the separate **launch** confirmation, then start by spec id with
the measured file as the profile:

```text
PULSAR_SPEC_FILE=<measured-spec.json> scripts/up.sh <spec_id> --dry-run
PULSAR_SPEC_FILE=<measured-spec.json> ./pulsar start <spec_id>
validate/baseline-v1.sh <spec_id> --spec <measured-spec.json> --check-only
```

`--check-only` proves in seconds that the running container carries the
spec's launch contract and image. If it refuses, stop; do not measure a
server that is not the spec's.

### 8. Baseline-v1 run

Run the six gates in one boot against the running server (about two hours,
mostly the soak). Every producer must answer `--help` first; the runner
checks that itself:

```text
validate/baseline-v1.sh <spec_id> --spec <measured-spec.json> \
  --out results/baseline-v1/<spec_id> --dataset <exact-dataset-file>
```

The runner stops at the first failed gate and keeps every document. The
evaluator proposes `stable` or `failed`; the run directory holds the six
measurements, the filled spec, and `run.json`. Do not rerun a failed gate
to chase a pass; record the outcome. Changing the recipe means a new draft,
a new spec id, and a new run.

### 9. Promotion pull request

After the final owned stop (`./pulsar stop <spec_id>`), build the released
document and open one reviewed pull request per spec:

```text
scripts/release-spec.sh promote <filled-spec.json> --reviewer <name> \
  --out releases/<spec_id>.json
```

The promote command refuses `stable` unless the exact six criteria passed
under one policy digest; a failed run promotes as `failed`. The pull
request carries `releases/<spec_id>.json`, `results/baseline-v1/<spec_id>/`,
and the regenerated `docs/MODELS.md` block
(`scripts/release.sh list --markdown`). The tracked tree must pass
`scripts/check_publishable_privacy.py` and the full `scripts/selftest.sh`.
The maintainer who merges is the reviewer named in the `review` block.

### 10. Handoff

Follow [references/handoff-template.md](references/handoff-template.md).
List the spec id, the receipt id, the evidence directory, the proposed
review status with the gate that decided it, the promotion pull request,
and that no review status was assigned by this skill.

### 11. Ownership-safe cleanup

Require a separate **destructive cleanup** confirmation. Use the normal
stop path (`./pulsar stop <spec_id>`). Then use the owning library cleanup
(`purge-hot`, `home remove`) only for resources this workflow created.
Refuse when a managed service still uses the resource. Do not `docker rm`
unrelated workloads.

## Resume

On resume, `verify` then `show` the journal. Continue from the last
completed phase. Do not restart acquisition, launch, or cleanup without a
fresh confirmation. A measured spec whose run directory exists resumes at
step 9; one without a run resumes at step 6.

## Journal helper

```text
python3 skills/pulsar-model-onboarding/scripts/onboarding_journal.py \
  initialize|append|verify|show [options]
```

Keep only phases, outcomes, explicit choices, ids/digests, and safe
repository-relative evidence references. Reject credentials, raw
environment values, hostnames/addresses, topology/node identifiers,
absolute paths, and embedded spec or receipt documents.
