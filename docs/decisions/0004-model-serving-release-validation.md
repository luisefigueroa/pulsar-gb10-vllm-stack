# ADR 0004: Model Serving Release identity and validation status

- **Status:** Accepted
- **Date:** 2026-08-14
- **Implementation status:** Policy accepted; release-descriptor, frozen
  Validation Contract, immutable run-record, evidence-bundle, and reviewed
  validation-decision schemas implemented; evidence capture/persistence,
  trusted publication, catalog/operator status projection, and
  serving-eligibility migration pending
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md), and
  [ADR 0003](./0003-explicit-model-preparation-transport.md)

## Context

Pulsar currently uses profile `STATUS=tested*`, reviewed expected-model seals,
and schema-1 validation bundles for several related jobs. The bundle combines
model and runtime inputs with evidence and issuance metadata. Its content ID
therefore changes when evidence or review metadata changes, even when the thing
being tested does not. Conversely, a model repository ID or expected seal alone
does not identify the runtime recipe and hardware geometry that earned a result.

This makes two questions unnecessarily hard to answer:

1. What exact, immutable serving subject did Pulsar test?
2. What evidence and criteria support its current qualification status?

The word `tested` is also overloaded. It can mean that a test merely ran, that
the observed result met an expectation, or that a complete reviewed release is
eligible for a supported serving path. Those meanings must be separated before
onboarding can be safely automated.

## Decision

### 1. The validation subject is a Model Serving Release

A **Model Serving Release** is:

> An immutable combination of exact model identity, serving recipe,
> runtime/image identity, and supported hardware geometry.

The four parts have these contracts:

| Part | Required identity |
|---|---|
| **Exact model identity** | A content-addressed **Model Artifact Set** containing the complete primary snapshot and every behavior-affecting external artifact |
| **Serving recipe** | The normalized effective runtime arguments, environment, memory/KV policy, speculative-decoding configuration, artifact use, and runtime model-access contract |
| **Runtime/image identity** | A digest-pinned container image plus an immutable host-compatibility envelope required by that image and recipe |
| **Supported hardware geometry** | A privacy-safe hardware class, node/accelerator count, TP/PP shape, topology/interconnect class, and relevant capacity requirements |

The Model Artifact Set includes weights, tokenizer, configuration, bundled
model code, and any separate adapter, draft model, tokenizer override,
supplemental head, or external code that can affect behavior. The serving
recipe records how each artifact is used.

The runtime model-access contract is part of the serving recipe. Replicated
cache and `library-hot` preparation may instantiate the same Model Serving
Release when both present the exact verified content through an equivalent
`local-verified-readonly` contract and produce the same effective runtime.
Physical placement, local path spelling, and the transport that moved the bytes
do not change the release. A live remote mount or another active inference-time
dependency is a different access contract and therefore a different release.

Runtime/image identity includes the exact image digest and the compatibility
envelope needed to run it safely, such as supported driver ABI or range,
container-runtime capabilities, and required kernel features. A run record
captures exact observed host versions. An in-envelope host patch does not create
a new release; changing the image digest or compatibility envelope does.

Supported hardware geometry is reusable and privacy-safe. Exact hostnames,
addresses, serial numbers, node IDs, and durable topology identifiers belong
only in protected run evidence and never in a release descriptor. Equivalent
physical nodes may instantiate the same release.

The release ID is:

```text
sha256(
  canonical Model Artifact Set
  + normalized serving recipe
  + runtime/image contract
  + supported hardware geometry
)
```

Status, evidence, reviewer, timestamps, distribution transport, physical
placement, and exact site topology are excluded. An unreviewed candidate and a
reviewed release therefore have the same release ID when their four-part tuple
is unchanged.

Any change to model identity, serving recipe, runtime/image identity, or
supported hardware geometry creates a new Model Serving Release. Qualification
results do not transfer to that new release. Prior evidence may be retained as
lineage or a comparison baseline, and unchanged subsystem evidence may remain
valid within its own scope under ADR 0002, but neither is an inherited release
pass.

### 2. Validation uses a frozen two-layer contract

Every qualification attempt uses a **Validation Contract** frozen before the
attempt begins:

1. **Repository-wide invariants** define mandatory dimensions, trust rules,
   evidence retention, fail-closed behavior, and comparison rules.
2. **Release-specific criteria** name the workloads, protocols, thresholds,
   sample sizes, context requirements, soak conditions, and any comparable
   predecessor for this release.

There is no universal numeric threshold that is meaningful for every model.
The release-specific contract defines satisfactory results, but it may not omit
the four core dimensions: **stability, accuracy, throughput, and latency**.
Provenance/security review and strict same-boot reproducibility are mandatory
repository-wide prerequisites for `Validated`.

The contract must also include every applicable serving-integration and
physical-geometry prerequisite for the declared runtime-access contract.
Control-plane selftests, documentation, preparation success, health, or a
single completion cannot substitute for physical qualification of release
behavior.

Strict same-boot reproducibility compares the contract-defined deterministic
requests and fields against the same unchanged live server boot. It requires
exact equality under that protocol. An FP-equivalent or otherwise
numerically-close result is useful diagnostic evidence, but it does not satisfy
the strict gate and cannot produce `Validated` status. `FP-equivalent` here is
an output-comparison verdict for bounded floating-point variation; it is not a
floating-point quantization format.

Latency and throughput regression budgets are automatic only when the frozen
contract names a comparable predecessor tested with the identical benchmark
protocol and supported hardware geometry. The contract sets release-specific
percentage budgets; Pulsar does not impose one repository-wide percentage. If
there is no comparable predecessor, the relative regression gate is `N/A` and
the release must still pass its absolute throughput and latency criteria. A
budget failure is `Tested—criteria not met`; noisy or insufficient comparison
evidence is `Tested—inconclusive`.

### 3. Validation decisions use explicit statuses

The status applies to one exact Model Serving Release, never to a model name,
repository ID, family, or mutable profile label.

There is no minimum status required to record a release in the catalog or keep
its evidence: represent the release and label its actual state. Eligibility for
a reviewed serving path is a separate policy gate and must not rewrite or hide
the validation status.

| Status | Meaning |
|---|---|
| **Untested** | Qualification has not begun under a frozen contract. Acquisition or distribution failure before the qualification barrier leaves this status unchanged. |
| **Testing incomplete** | Qualification began, but required gates, evidence, or provenance/security review are still missing. Passing behavioral tests before reviewed issuance remains here. |
| **Tested—criteria not met** | The evidence is sufficient to conclude that one or more frozen criteria failed. |
| **Tested—inconclusive** | Testing ran, but noise, interruption, conflict, or insufficient evidence prevents a pass/fail conclusion. |
| **Validated** | Every frozen criterion and applicable integration/physical prerequisite passed, including stability, accuracy, throughput, latency, strict same-boot reproducibility, and provenance/security review; the reviewed decision binds the exact release and evidence. |
| **Superseded** | A later reviewed decision declares an earlier decision no longer current. The earlier decision and all underlying results remain immutable and discoverable. |

There is no separate `Tested—meets criteria` state. Meeting every requirement
is `Validated`; otherwise the status states why validation was not reached.
`Superseded` is a lifecycle result, not a rewrite of the earlier outcome. The
new superseding decision carries the lineage link; the earlier record is not
edited to add it.

The schema stores the new decision's reviewed base outcome (`Untested`,
`Testing incomplete`, `Tested—criteria not met`, `Tested—inconclusive`, or
`Validated`) and its backward supersession links. `Superseded` is projected as
the earlier decision's effective lifecycle status when a later reviewed
decision names it. This preserves the earlier outcome byte-for-byte while
making its non-current state machine-readable.

### 4. Identity, evidence, and authority are separate objects

The machine-readable model has five immutable object roles:

| Object | Responsibility |
|---|---|
| **Release descriptor** | Defines the four-part Model Serving Release and its release ID |
| **Validation Contract** | Freezes repository-wide invariants plus release-specific gates before testing |
| **Run record** | Records one attempt, exact observed environment, commands/protocols, outputs, and completion condition |
| **Validation bundle** | Binds the release descriptor, frozen contract, and referenced run records into a reviewable evidence set |
| **Validation decision** | Records the reviewed status, criterion results, provenance/security disposition, reviewer authority, timestamps, and supersession links |

Every attempt receives a new run record, including failed, interrupted, and
inconclusive attempts. Records are never overwritten. New attempts produce new
records; new bundles and decisions supersede earlier ones without deleting or
relabeling their history.

Existing schema-1 validation bundles and expected-model seals remain immutable.
They are legacy combined identity/evidence artifacts and are not rehashed,
rewritten, or automatically converted. The first implementation stage adds a
separate release descriptor and frozen Validation Contract; later stages add
run records, evidence bundles, validation decisions, and their cross-links.
Existing `STATUS=tested*`, `--validated`, reviewed seals, and legacy-unsealed
behavior retain their current implementation meaning until that migration
lands. No existing profile is automatically relabeled `Validated`.

### 5. Distribution is preparation provenance, not release status

The supervised onboarding flow may use any available subsystem, including one
currently labeled Experimental. Subsystem maturity and transfer choice do not
cap the eventual Model Serving Release status. Experimental use is an explicit
provenance fact, not an exception or validation waiver; no separate exception
framework is introduced.

Before qualification starts, distribution must complete, every serving rank
must expose the intended runtime-access contract, and exact content must pass
the applicable full verification barrier. A distribution failure is a failed
preparation/onboarding attempt: the release remains `Untested`, rather than
being classified as `Tested—criteria not met`. The chosen transport, placement,
timings, and subsystem versions are recorded as run provenance.

Catalog/artifact, serving-integration, model-qualification, and
release/promotion evidence retain the independent scopes defined by ADR 0002.
A subsystem failure blocks every combined claim that depends on it but does not
erase unrelated valid evidence. Selftests prove control-plane contracts only;
they never substitute for physical behavior on the supported geometry.

### 6. Reviewed issuance remains the trust boundary

Candidate tooling may resolve an immutable upstream revision, hash the complete
artifact set, normalize a recipe, calculate the release ID, and run tests with
unreviewed identity assurance. It must not write reviewed trust roots, assign
`Validated`, promote a profile, or grant itself issuance authority.

Reviewed provenance confirms that the artifacts and image are the exact inputs
used for the referenced runs, every behavior-affecting external artifact is
bound, evidence is privacy-safe, and the frozen contract was not changed after
results were known. Locally observed content can match a reviewed identity but
cannot create it. Repository-reviewed issuance plus content digests remains the
initial authority model; cryptographic signing is deferred.

### 7. The onboarding skill is orchestration, not authority

The future supervised end-to-end skill is named
`pulsar-model-onboarding`. It should use available subsystems for acquisition,
distribution across serving ranks, verification, launch, testing, evidence
capture, and cleanup. Experimental subsystems are allowed when explicitly
selected and their contracts fit the task.

The skill is recipe-bound and may not silently change transport, storage
policy, runtime source, geometry, or validation criteria. It asks for operator
confirmation before large acquisition, launch, and destructive cleanup. It has
no authority to issue seals, assign `Validated`, or promote a serving path;
those remain reviewed repository decisions. Reusable product behavior belongs
in Pulsar subsystems, while the skill supervises and composes those subsystems.

### 8. `library-hot` subsystem GA is a separate decision

Model Serving Release validation does not depend on how verified local bytes
were distributed. Conversely, `library-hot` subsystem maturity does not depend
on a particular model/runtime passing strict determinism.

The initial `library-hot` GA scope is the reviewed two-rank path. Remote
one-rank placement is outside that initial scope. One combined GA closure task
remains:

1. remove the home-rank reflink/copy fallback so a failed durable-home symlink
   fails closed;
2. physically exercise sustained serving and restart;
3. force a replacement failure and prove exact rollback;
4. reverify identity through the lifecycle;
5. prove owned cleanup and final one-home state; and
6. publish sanitized evidence for those exact claims.

Passing that task can make the bounded subsystem GA. It does not automatically
make `library-hot` the default or only path, and it does not convert the
DeepSeek strict-determinism failure into a pass. That failure belongs to the
affected Model Serving Release.

## Staged implementation

Stage 1 is implemented by `scripts/model_serving_release.py`. It provides pure
builders and fail-closed validators for the release descriptor and frozen
Validation Contract, including deterministic IDs, privacy-safe geometry,
recipe/geometry parallelism consistency, strict same-boot exactness, reviewed
provenance requirements, and comparable-predecessor protocol/geometry binding.
The fixed-ID fixture and adversarial contracts live under `scripts/testlib/`.
This code performs no filesystem or network I/O, emits no reviewed artifact,
assigns no status, changes no profile, and grants no serving eligibility.
Control-plane selftests establish only these schema contracts; no physical DGX
claim follows from them.

Stage 2 is implemented by `scripts/model_validation_evidence.py`. It provides
pure builders and fail-closed validators for content-addressed evidence
references, immutable attempt/run records, Model Serving Release validation
bundles, and reviewed validation decisions. A run binds one release and frozen
contract to exact timestamps, completion condition, rank-relative observed
hardware/runtime versions, opaque boot/launch identities, commands, selected
subsystem maturity and distribution provenance, the pre-qualification
verification barrier, criterion measurements, and content-addressed evidence.
Bundles require the exact immutable run and artifact sets. Decisions explicitly
record the reviewer-selected status but independently recompute criterion
dispositions from frozen thresholds, required context depths/token minimums,
soak duration/concurrency/error limits, and applicable predecessor-relative
throughput/latency budgets in the selected run records; a mismatch fails.
Missing required evidence remains incomplete, inconclusive evidence remains
inconclusive, and a conclusive requirement miss fails. Strict evidence cannot
span live server boots, bundles reject reused attempt identities, and review
timestamps cannot precede their evidence or the decisions they supersede.
Incomplete privacy or provenance review cannot become `Validated`, and a failed
distribution before the qualification barrier derives `Untested`. Experimental
subsystem use is recorded but does not cap the result. Later decisions carry
immutable backward supersession links, and effective `Superseded` projection
does not mutate the prior decision.

Stage 2 still performs no command execution, evidence capture, filesystem or
network I/O, trusted publication, catalog update, profile edit, or serving
gate. A syntactically valid review block or `Validated` fixture is not proof
that repository review or physical qualification occurred. No current release
received an ADR 0004 decision from this implementation unit.

Implement this decision in focused, reviewable units:

1. **Implemented:** add canonical release-descriptor identity and Validation
   Contract schemas in Python with deterministic fixtures, without modifying
   schema-1 artifacts;
2. **Implemented:** add immutable attempt/run records, evidence bundles, and
   validation decisions with fail-closed cross-link verification, independent
   status derivation, and explicit supersession;
3. project the new statuses into catalog and operator surfaces, then migrate
   serving eligibility only through explicit reviewed decisions—never by
   converting `STATUS=tested*` automatically;
4. create the supervised `pulsar-model-onboarding` skill around the supported
   subsystem CLIs and confirmation boundaries; and
5. complete and publish the separate bounded `library-hot` GA closure evidence.

Each unit must update the canonical design, current implementation spec,
operator/revalidation docs, validation ledger, and evidence index when its
claims actually change. Physical claims require physical evidence in that unit.

## Rejected alternatives

### Use a model repository ID as the release identity

A repository ID does not bind exact bytes, runtime behavior, image, or hardware
geometry and can silently span materially different serving subjects.

### Rename the existing schema-1 bundle ID as the release ID

The current bundle hashes evidence and issuance metadata. Identical serving
inputs can therefore receive different bundle IDs, which is incompatible with
a stable release identity.

### Include distribution transport in the release ID

Transport ends before serving for local verified views. Including it would
create different releases for equivalent runtime behavior and would
unnecessarily couple model qualification to subsystem maturity.

### Add `Tested—meets criteria` below `Validated`

Two positive states with the same gates would create an unexplained authority
gap. Passing all frozen criteria and review is exactly what `Validated` means.

### Use one global performance threshold

Model size, workload, context, and serving purpose differ too much for one
throughput or latency number to express satisfactory behavior honestly.

### Accept FP-equivalent output as strict reproducibility

It is valuable for diagnosis and cross-boot comparison, but it is weaker than
the agreed same-boot invariant and would make `Validated` ambiguous.

### Let the onboarding skill seal or promote its own result

An orchestrator that both produces and authorizes evidence collapses the trust
boundary and can turn local observation into an official claim.

## Consequences

- `Validated` becomes a precise claim about one immutable serving tuple.
- A tuple change always creates a new release and requires a new decision.
- Criteria are explicit before testing, while model-specific thresholds remain
  possible.
- Failed and inconclusive work remains useful, visible evidence rather than a
  hidden or overwritten attempt.
- Distribution experiments can improve onboarding without contaminating model
  status, provided the pre-qualification verification barrier holds.
- Current commands and schemas continue to work during migration, but their
  legacy `tested`/`validated` labels must not be presented as implementation of
  this ADR until the new objects and enforcement exist.
- Physical qualification is still required for physical claims. Documentation
  and selftests alone cannot produce a `Validated` decision.
