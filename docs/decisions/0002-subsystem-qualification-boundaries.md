# ADR 0002: Subsystem qualification boundaries

- **Status:** Accepted
- **Date:** 2026-08-12
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decision:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md)

## Context

The distributed model catalog, its serving integration, the model/runtime
combination, and the final product release evolve at different rates and fail
for different reasons. Treating all evidence as one global pass/fail result
creates two opposite errors:

- a model-runtime failure can incorrectly erase valid identity, transfer, or
  lifecycle evidence; or
- a successful activation and completion smoke can be overstated as model
  correctness, determinism, soak, or release qualification.

The DeepSeek model-library work exposed this distinction directly. Exact
sealed bytes, one-home placement, activation, witnesses, read-only launch, and
cleanup passed their catalog and integration gates. The selected runtime still
has a separately tracked strict-determinism question and lacks the sustained
soak required for storage-path promotion.

## Decision

Pulsar records and interprets evidence in four qualification scopes:

| Scope | What it proves |
|---|---|
| **Catalog and artifact service** | Exact bytes and identity, durable placement, transfer integrity, rank-local views, retention, repair, and cleanup |
| **Serving integration** | The selected image and launcher mount and load the intended exact runtime source; health, warmup, and completion smoke belong here |
| **Model qualification** | Accuracy, determinism, throughput, long context, and soak for the exact model, image, normalized runtime configuration, and geometry |
| **Release and promotion** | The conjunction of every required subsystem result for a supported profile, guided workflow, or default policy |

A failure in one subsystem does not erase valid evidence from another subsystem
unless a causal connection is demonstrated. It does block any release or
promotion claim that requires both subsystems.

Evidence must name its scope. Health and completion smoke prove serving
integration only; they do not establish model correctness, determinism,
performance, context, or soak. Catalog acceptance does not promote a profile or
storage path into the wizard or another guided default.

A change invalidates the scopes whose inputs or contracts it changes:

- a new model revision or expected seal requires catalog identity verification,
  serving integration, and complete model qualification for a release claim;
- an image, dependency, runtime flag, or serving-geometry change invalidates
  serving integration and model qualification, but does not automatically
  invalidate generic catalog placement, transfer, repair, or lifecycle evidence;
- a transfer, metadata, witness, retention, or cleanup change invalidates the
  affected catalog gates and serving integration where the runtime view changes;
  model qualification is rerun when the release claim uses a new runtime source
  or evidence shows a plausible effect on served bytes or execution; and
- documentation-only classification changes require documentation and
  control-plane regression checks, not new hardware claims.

The validation bundle remains the immutable release binding for an exact model,
image, configuration, geometry, and evidence set. Reusing generic catalog
evidence does not carry an old bundle or `STATUS=tested` claim onto changed
runtime inputs; a new release binding still requires every applicable gate.

Failed, partial, and superseded evidence remains immutable. Later conclusions
change its current interpretation and scope, not its recorded outcome.

## Rejected alternatives

### One global pass/fail verdict

This hides the failing subsystem and either discards reusable evidence or
encourages an overbroad success claim.

### Treat health or completion smoke as model validation

A model can mount, load, and answer while still failing correctness,
determinism, throughput, context, or soak requirements.

### Invalidate all catalog evidence after every image change

An image change does not alter already measured byte identity, durable
placement, transfer semantics, or deletion safety unless it changes or exposes
a causal dependency on those contracts.

### Promote the catalog subsystem directly into guided defaults

Subsystem acceptance is necessary but not sufficient. Guided/default behavior
still requires the combined release and promotion gates.

## Consequences

- Ledgers and evidence indexes distinguish catalog, integration, model, and
  release conclusions.
- Runtime work can be deferred without pretending that its failure is resolved
  or that completed catalog work failed.
- Valid subsystem evidence can be reused when its inputs and contracts are
  unchanged, reducing unnecessary physical reruns.
- Change reviews must identify the causal impact before expanding the
  revalidation scope.
- Current CLI status fields and machine-readable schemas are unchanged; this
  decision governs interpretation until a separate implementation adopts
  first-class qualification dimensions.

## Revisit triggers

Revisit this decision if Pulsar introduces machine-readable qualification
dimensions, changes the meaning of profile `STATUS`, or adopts a release model
where subsystem services have independently versioned compatibility contracts.
