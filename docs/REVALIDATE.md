# Revalidation runbook

Use this runbook after changing any input to a Model Serving Release: exact
model bytes, serving recipe, image, or supported hardware geometry. A change to
one of those inputs creates a new Model Serving Release. Prior model-specific
results do not transfer automatically.

## Current baseline

- The tracked registry under `models/model-serving-releases/` is empty.
- No current profile sets `MODEL_SERVING_RELEASE_ID`.
- `qwen3.8-27b-fp8`, `qwen3.8-27b-fp8-2node`, and
  `qwen3-1.7b-2node` are untested recipe shells. They carry no retained
  onboarding, qualification, or Model Serving Release evidence.
- Profile `STATUS=tested*` is the older recommendation class. It is not an
  ADR 0004 decision and does not authorize serving.

## Qualification scopes

Keep four independent questions separate:

| Scope | What must be shown |
|---|---|
| Catalog and artifact service | Exact content, receipt and occupancy, durable placement, preparation, retention, recovery, and cleanup |
| Serving integration | The exact recipe and image load the intended local files, become ready, answer a completion, and stop through owned cleanup |
| Model qualification | Stability, accuracy, throughput, latency, strict same-boot reproducibility, context, and soak requirements frozen for this subject |
| Release and promotion | Provenance/security and physical geometry review plus every required result above |

A pass in one scope does not satisfy another. A deterministic selftest is not
physical DGX evidence.

## 1. Establish the exact subject

1. Start from a reviewed profile diff under `models/`.
2. Resolve the Hugging Face source to an exact commit and complete file list.
3. Record the digest-pinned image and exact one-rank or multi-rank geometry.
4. Freeze the Validation Contract before collecting results.
5. Keep drafts under `experiments/`; do not write the trusted registry.

For a model that has no home yet:

```bash
scripts/model-library.sh home add <profile> \
  --revision <selector> --plan --json

scripts/model-library.sh home add <profile> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json

scripts/model-library.sh catalog refresh
scripts/model-library.sh home verify <model_id@exact-commit> --json
```

The download creates a receipt and occupancy identity. It does not create a
Model Serving Release or a validation decision.

## 2. Run deterministic checks before hardware work

```bash
scripts/selftest.sh
scripts/model-serving-release-registry.sh verify
scripts/list-models.sh --json
```

The registry command must report a valid empty registry until a reviewed
publication is merged. Do not add a profile binding before its complete object
graph is reviewed in the same change.

## 3. Prepare the exact runtime view

For a one-rank profile:

```bash
scripts/model-library.sh prepare <one-rank-profile> --yes
scripts/up.sh <one-rank-profile> --dry-run
```

For a multi-rank profile:

```bash
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check
scripts/model-library.sh prepare <multi-rank-profile> \
  --backend copy --transport ssh-roce --copy-streams 8 --yes
cluster/preflight.sh <multi-rank-profile>
```

Preparation must full-verify the receipt-backed content on every serving rank.
It must not fall back to another transport, geometry, or source.

## 4. Prove serving integration

Use the profile's actual launcher:

```bash
# one rank
scripts/up.sh <one-rank-profile>

# multiple ranks
cluster/start-cluster.sh <multi-rank-profile>
```

Record startup, health, warmup, one non-streaming completion, one streaming
completion, runtime identity, and owned stop. On multi-rank jobs, confirm the
intended NCCL/RoCE transport rather than inferring it from `/health`.

```bash
./pulsar status <profile>
./pulsar stop <profile>
```

## 5. Run the frozen model-qualification gates

Run only against the already-running exact subject:

```bash
validate/run-gates.sh <served-name> --tag <candidate-tag>
```

Add the contract's required accuracy, context, concurrency, and soak commands.
Do not invent missing output or relax a threshold after seeing results. Strict
same-boot reproducibility is exact; floating-point similarity is diagnostic.

The retained recipes have no carried-forward evidence. For example, running
either Qwen3.8 recipe or the Qwen3 1.7B two-rank draft begins a new onboarding
record, not a continuation of an older result.

## 6. Capture and stage a reviewed proposal

The onboarding skill composes planning, acquisition, preparation, launch, and
measurement capture. It remains non-issuing. After capture, the maintainer
issuance workflow may stage an untrusted proposal from the independently
verified draft and explicit review declaration.

A successful local stage command does not make the objects trusted. Repository
review and merge establish the tracked registry state. Add
`MODEL_SERVING_RELEASE_ID` only as a separately reviewed profile edit in that
same publication change.

## 7. Model-library physical follow-ups

The retained catalog and artifact ledger includes the bounded Nemotron Nano
Gate 14 acquisition lifecycle. It does not cover a remote target, asymmetric
credentials, physical cold restore, or model serving. When changing those
paths, capture the missing physical cases without extending Gate 14 beyond its
recorded scope.

The operator decides whether the configured cold root is an independent
failure domain. Pulsar verifies path safety and the receipt/archive recovery
set; it does not verify the operator's storage architecture.

After removal of the legacy `/mnt/Models` container mount, rerun one-rank and
multi-rank serving integration before making a current launch claim. Confirm
the container has only the verified local model-view mount, loads the exact
receipt-backed snapshot, passes health/warmup/completion smoke, and stops
through owned cleanup. This does not require rerunning unrelated catalog
mechanics or imply model qualification.

## 8. Close out

Before publication:

```bash
git diff --check
scripts/selftest-docs-privacy.sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/testlib/test_docs_current_state.py
scripts/selftest.sh
```

Then update `docs/VALIDATION.md` and the relevant `results/` index with only
the evidence actually produced. State which physical gates were not run. A
failed or incomplete new attempt stays visible in its new evidence set; it
must not be converted into a pass.
