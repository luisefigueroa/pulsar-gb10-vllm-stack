# ADR 0004: Model Serving Release identity and validation status

- **Status:** Accepted
- **Date:** 2026-08-14
- **Amended:** 2026-08-15 — validation status is advisory, not serving
  authorization; primary model identity is source-neutral.
  2026-08-17 — a home created by source-attested acquisition may be reused
  only after a complete offline rehash against its valid receipt; unknown
  and pre-existing homes still require a reviewed expected manifest
- **Implementation status:** Policy accepted; release-descriptor, frozen
  Validation Contract, immutable run-record, evidence-bundle, and reviewed
  validation-decision schemas implemented; read-only trusted persistence
  and verification implemented under `models/model-serving-releases/`;
  local ADR 0004 evidence-capture candidate persistence implemented
  and composed from a verified release-plan candidate plus an attempt-only spec;
  local source-neutral release-plan candidate persistence implemented with
  a public verified loader;
  closed validator-measurement documents and attempt-only spec composition
  implemented for strict same-boot and absolute throughput/latency;
  advisory catalog/operator ADR 0004 status projection implemented for
  explicitly bound profiles; supervised `pulsar-model-onboarding` skill
  implemented as control-plane orchestration; maintainer-only issuance
  staging implemented as an untrusted local proposal whose trust event is
  repository review and merge; tracked store remains empty and unbound;
  status-independent serving policy implemented;
  source-attested Hugging Face v1 planning, separately confirmed acquisition,
  immutable receipts, offline home verification, exact prepare binding, and
  onboarding-skill composition implemented as deterministic control-plane
  behavior; physical Hub/DGX acquisition evidence remains pending
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
recommended for a supported serving path. Those meanings must be separated
before onboarding can be safely automated.

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

Primary identity is source-neutral. A Hugging Face acquisition is represented
as a `huggingface-snapshot` with its exact commit and complete manifest. A
complete model tree acquired from another catalog or filesystem source is
represented as a `content-addressed-model` with a public logical artifact ID,
public revision, and the same complete-manifest integrity scheme. Local path
spelling and acquisition transport are not persisted. A `digest-artifact`
describes a behavior-affecting attachment, not a complete primary model, and
cannot satisfy the primary-model binding by itself.

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
captures exact observed host versions. Driver, container-runtime, and kernel
version envelopes use canonical numeric `>=LOW,<HIGH` ranges. Exact observed
versions may retain deployed zero-padded components and a vendor suffix (for
example a distro kernel build); comparison uses their dotted numeric core.
Architecture and required capabilities/features are exact contract fields. An
in-envelope host patch does not create a new release; changing the image digest
or compatibility envelope does.

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

Every criterion has one canonical qualification scope. The schema rejects a
criterion whose declared scope differs from this table:

| Criterion dimension | Required qualification scope |
|---|---|
| Stability | `model-qualification` |
| Accuracy | `model-qualification` |
| Throughput | `model-qualification` |
| Latency | `model-qualification` |
| Strict same-boot reproducibility | `model-qualification` |
| Serving integration | `serving-integration` |
| Provenance/security | `release-promotion` |
| Physical geometry | `release-promotion` |

`catalog-artifact` remains a valid scope for acquisition, preparation,
identity, transfer, placement, retention, repair, and cleanup evidence. It is
not a permitted scope for a Validation Contract criterion. Catalog or model
preparation evidence may establish the qualification barrier and provide
provenance, but it cannot satisfy any of the validation criteria above.

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
protocol and supported hardware geometry. The baseline must bind the reviewed
predecessor contract, evidence bundle, validation decision, and exact run used
for comparison. `relative_performance` records `predecessor_release_id`,
`predecessor_contract_id`, `predecessor_bundle_id`, and
`predecessor_decision_id`; its throughput and latency entries each record the
exact `predecessor_criterion_id` and `predecessor_run_record_id`. The relevant
predecessor throughput or latency criterion must
have a reviewed `pass` disposition in that decision. The predecessor release
does **not** need to be globally `Validated`; for example, a release whose
accuracy work is incomplete can still be a valid latency baseline when its
latency criterion passed under the bound comparable protocol. The contract
sets release-specific percentage budgets; Pulsar does not impose one
repository-wide percentage. If there is no qualifying comparable predecessor,
the relative regression gate is `N/A` and the release must still pass its
absolute throughput and latency criteria. A budget failure is
`Tested—criteria not met`; noisy or insufficient comparison evidence is
`Tested—inconclusive`.

Runtime compatibility and architecture/geometry matching are validated
structurally: the release descriptor has a well-formed compatibility envelope,
the observed run falls within it, and the observed rank count, hardware class,
capacity, and TP/PP shape agree with the supported geometry. Those checks prove
schema and environment compatibility only. They do not prove physical serving
behavior. The serving-integration and physical-geometry criteria still require
evidence captured on the declared physical DGX geometry.

### 3. Validation decisions use explicit statuses

The status applies to one exact Model Serving Release, never to a model name,
repository ID, family, or mutable profile label.

There is no minimum status required to record or serve a release. Represent the
release and label its actual state. Validation status is descriptive evidence,
not authorization: `Untested`, incomplete, failed, inconclusive, `Validated`,
`Superseded`, legacy labels, and the absence of a reviewed decision neither
grant nor deny permission to launch.

Operator surfaces must expose every structurally valid serving profile that
fits the selected hardware capacity, together with its status and material
caveats. They may sort or recommend evidence-backed choices first, and a
guided/default policy may prefer `Validated` releases, but recommendation and
default selection are distinct from availability. They must not hide or block
another release solely because of its validation status.

Operational admission remains fail-closed and separate from status. A launch
may be refused for a concrete inability to execute the requested release
safely—for example missing, partial, or mismatched bytes; an absent or invalid
serving recipe; incompatible runtime or geometry; insufficient capacity;
stale or unreachable required topology; or security, lifecycle, integrity, and
ownership failures. Those failures describe current runnability, not the
release's validation decision. A catalog entry without an executable recipe is
shown with recipe-required guidance rather than being described as
status-blocked.

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

#### Observation inclusion and adjudication

A reviewed decision automatically considers **every applicable observation**
in its bound evidence bundle. An observation is applicable when it is bound to
the exact release and frozen contract, names the criterion's canonical scope,
and satisfies the criterion's structural protocol, geometry, and identity
requirements. Reviewers do not choose only the runs that support a preferred
outcome.

Excluding an otherwise applicable observation is exceptional. The decision
must identify the excluded observation, give an evidence-backed reason, and
retain both the observation and exclusion record in the reviewable history.
An unexplained omission is missing evidence and prevents `Validated`.

In the version-1 pure API, `build_validation_decision(...,
criterion_exclusions=[...])` is the only exclusion input. The persisted
decision does not carry a manually selected run list: each
`criterion_results[]` entry records all accepted observations in
`included_run_record_ids` and retains any exception in
`excluded_run_records`, including its reason and review-evidence artifact IDs.

Multiple included observations for one criterion are adjudicated as follows:

| Included dispositions | Criterion disposition |
|---|---|
| No applicable observation | `not-evaluated` |
| All `pass` | `pass` |
| All `fail` | `fail` |
| All `inconclusive` | `inconclusive` |
| `pass` + `fail` | `inconclusive` |
| `pass` + `inconclusive` | `inconclusive` |
| `fail` + `inconclusive` with no pass | `fail` |
| `pass` + `fail` + `inconclusive` | `inconclusive` |

A conclusive failure is not softened merely because another attempt was
inconclusive; conflicting conclusive pass/fail observations instead require
adjudication and remain inconclusive until new evidence resolves the conflict.
A completed nested context or soak observation is independently conclusive: if
it violates its frozen requirement, an `inconclusive` enclosing criterion label
cannot downgrade that failure.
After criterion-level aggregation, any failed criterion produces
`Tested—criteria not met`, otherwise any inconclusive criterion produces
`Tested—inconclusive`, any unevaluated requirement produces
`Testing incomplete`, and only an all-pass reviewed result produces
`Validated`.

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
| **Validation decision** | Records the reviewed status, automatically aggregated criterion results, explicit evidence-backed exclusions, provenance/security disposition, reviewer authority, timestamps, and supersession links |

Every attempt receives a new run record, including failed, interrupted, and
inconclusive attempts. Records are never overwritten. New attempts produce new
records; new bundles and decisions supersede earlier ones without deleting or
relabeling their history.

Each attempt hash-binds a sorted `attempted_criterion_ids` declaration. A
pre-barrier preparation attempt declares none. A post-barrier non-preparation
attempt declares at least one known criterion whose scope matches the attempt,
and its observations cover that set exactly. Failed, interrupted, or otherwise
incomplete attempts record inconclusive observations for every declared
criterion. Omitting both the declaration and a failed observation is invalid,
not an evidence-selection mechanism.

Operator command evidence is structured data, not an arbitrary shell
transcript. Each `commands[]` entry names an allowlisted repository program,
records a `sha256:<digest>` program-version identity, contains exactly one
program-specific closed operation, and may add only closed repository-resource
references, attempted-criterion references, or typed site options. A site
option embeds a rank reference or protected content-addressed site reference,
never a raw host, URL, or address. Rank references must name a rank in the
release geometry. Structured `environment[]` entries contain a classification
and variable name but never a value; the working-directory marker is always
`repository-root`.

Every persisted free-form release/contract string that lacks a stricter closed
grammar rejects recognized credential values, absolute site paths, explicit
URIs, private/site endpoint forms, deployment-variable references, and private
topology assignments. Ordinary dotted public identifiers remain valid, while
credential-bearing extensible field names are rejected independently of their
values. Command environment references additionally reject credential-shaped
names. Provenance/security is review-derived and therefore has one exact
canonical criterion template;
unimplemented extra thresholds or parameters are invalid. These are structural
controls, not a proof against every unknown private codename. Trusted capture
must calculate program digests from the selected checkout, and publication
still requires privacy auditing and reviewer inspection.

Run records also make physical context reviewable without publishing a site
map. `observed_environment.cluster` records the rank-relative geometry shape,
while `observed_environment.ranks[]` records per-rank architecture and runtime
compatibility observations. A soak observation records `started_at`,
`ended_at`, and canonical `duration_seconds`; the validator checks that the
duration exactly equals the contained timestamp interval.

Existing schema-1 validation bundles and expected-model seals remain immutable.
They are legacy combined identity/evidence artifacts and are not rehashed,
rewritten, or automatically converted. Stage 1 adds a separate release
descriptor and frozen Validation Contract; stage 2 adds run records, evidence
bundles, validation decisions, and their cross-links. Both pure-schema stages
are implemented. Read-only trusted persistence and verification for those
objects now live under `models/model-serving-releases/` and
`scripts/model_serving_release_registry.py`. That layer does not capture
evidence, issue a decision, change recommendation policy, or launch a release.
Its verified inspection result now feeds read-only status projection in the
catalog, wizard, and `scripts/up.sh`. A profile opts in only through a reviewed
`MODEL_SERVING_RELEASE_ID` binding. A missing binding is shown as neutral
`No release binding`; a bound release without one unambiguous reviewed decision
is shown as neutral, ambiguous, or unavailable as appropriate, never inferred
as `Untested`. The selected runtime model-access contract must match the stored
release recipe before its decision is displayed. The binding itself is a
reviewed assertion about the full four-part tuple; this projection does not
reconstruct that complete tuple from shell profile fields. Any tuple-changing
profile edit must derive and bind the new release ID in the same reviewed
change. Existing `STATUS=tested*`,
`--validated`, reviewed seals, and legacy-unsealed behavior retain their
separate legacy meanings and recommendation order; none grants or denies
serving. No existing profile is automatically relabeled `Validated`. The
tracked ADR 0004 store currently contains no issued object and no profile is
currently bound.

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

Catalog/artifact and preparation observations cannot be selected as evidence
for stability, accuracy, throughput, latency, strict same-boot,
serving-integration, provenance/security, or physical-geometry criteria. They
can prove their own subsystem contract and the full-verification barrier, and
they remain useful immutable provenance, but qualification starts only after
that barrier passes.

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

The supervised end-to-end skill is named
`pulsar-model-onboarding`. It uses available subsystems for source-attested
acquisition or exact-home reuse, distribution across serving ranks,
verification, launch, testing, evidence capture, and cleanup. Experimental
subsystems are allowed when explicitly selected and their contracts fit the
task. For an absent brand-new unsealed Hugging Face home, the skill composes a
read-only exact-source plan and requires a separate confirmation before the
acquisition service downloads the exact commit. Direct durable-cache download
is not an accepted substitute. A source-attested home is reusable only after
offline full verification against its valid immutable receipt. An unknown or
pre-existing home is reusable only after full verification against a reviewed
expected manifest that is independent of the observed tree. A shallow catalog
label and a manifest generated from that same tree are insufficient.

### Interpretation note — 2026-08-17

An unknown or pre-existing home retains the reviewed-expected-manifest reuse
rule above. A source-attested home created by this workflow may be resumed
or reused only after a complete offline rehash against the valid immutable
site-local receipt attached to that exact live directory for the same public
source identity. A catalog label, a matching historical receipt without the
attachment, or a manifest generated from the current tree remains insufficient.

The implemented source-attested control plane resolves and hashes an immutable
upstream revision, downloads through target-local authentication into private
same-filesystem staging after confirmation, checks the complete upstream set,
repeats all-rank absence, writes the immutable receipt, publishes with an
atomic no-replace rename, and records the private current-home attachment.
`home verify` performs the later receipt-backed offline full rehash only when
that attachment matches the live home, and receipt-backed prepare additionally
requires the exact model ID and commit. This tooling still may not issue trust,
a seal, status, serving permission, or a Model Serving Release decision. Its
deterministic tests do not prove physical Hub/DGX behavior.

The skill is recipe-bound and may not silently change transport, storage
policy, runtime source, geometry, or validation criteria. It asks for operator
confirmation before large acquisition, launch, and destructive cleanup. It has
no authority to issue seals, assign `Validated`, or promote a serving path;
those remain reviewed repository decisions. Reusable product behavior belongs
in Pulsar subsystems, while the skill supervises and composes those subsystems.
Current automated measurement mapping covers only strict same-boot and
absolute throughput/latency. The default unsealed replicated path may be
served with its honest label but is not an exact ADR 0004 qualification
attempt. The skill-local journal is isolated under
`experiments/model-onboarding/workflows/`; it is orchestration recovery state,
not a sixth ADR object. Deterministic skill and journal tests make no physical
DGX claim and create no release decision.

### 8. `library-hot` subsystem GA is a separate decision

Model Serving Release validation does not depend on how verified local bytes
were distributed. Conversely, `library-hot` subsystem maturity does not depend
on a particular model/runtime passing strict determinism.

The initial `library-hot` GA scope is the reviewed two-rank path. Remote
one-rank placement is outside that initial scope. The combined GA closure task
completed on 2026-08-16:

1. the home-rank reflink/copy fallback was removed, so preparation fails if the
   exact durable-home symlink cannot be created and verified;
2. sustained serving and exact restart passed physically;
3. a forced replacement launch failure left a persisted stopped transaction,
   and a new wizard process restored the exact captured contract;
4. reviewed identity matched through preparation, serving, restart, recovery,
   and final durable-home verification;
5. owned cleanup removed the target service and unpinned hot views while
   preserving one durable home; and
6. sanitized catalog/artifact, serving-integration, and model-qualification
   evidence for the bounded soak and stability observations was published at
   `results/model-library/deepseek-v4-flash-library-hot-ga-closure-20260816.json`.

The reviewed two-rank subsystem is therefore GA. It remains explicit and
non-default. Remote one-rank and legacy-unsealed use remain experimental. This
decision does not convert the DeepSeek strict-determinism failure into a pass;
that failure belongs to the affected Model Serving Release.

## Staged implementation

Stage 1 is implemented by `scripts/model_serving_release.py`. It provides pure
builders and fail-closed validators for the release descriptor and frozen
Validation Contract, including deterministic IDs, privacy-safe geometry,
recipe/geometry parallelism consistency, canonical criterion-scope mapping,
strict same-boot exactness, reviewed provenance requirements, and reviewed
comparable-predecessor lineage plus protocol/geometry binding.
The fixed-ID fixture and adversarial contracts live under `scripts/testlib/`.
This code performs no filesystem or network I/O, emits no reviewed artifact,
assigns no status, changes no profile, and launches nothing.
Control-plane selftests establish only these schema contracts; no physical DGX
claim follows from them.

The unreviewed Stage-1 planning boundary is implemented by
`scripts/model-serving-release-plan.sh` and
`scripts/model_serving_release_plan.py`. It sources one profile, consumes an
already computed complete snapshot manifest, requires an explicit
runtime/image and hardware envelope plus frozen criteria, accepts explicitly
bound descriptors for every additional behavior-affecting artifact, and writes
only release and contract candidates beneath `experiments/model-onboarding/`
(or an explicit path outside the repository). It strips deployment-local
source paths from source-neutral identity, checks the profile image, TP/PP, node count,
topology, and rail contract against the supplied envelope, and can verify the
candidate against the current profile. Planner `verify` uses the public
schema-owning `load_verified_release_plan_candidate(dir)` loader: shared
filesystem hardening from `scripts/immutable_descriptor_dir.py`, then
validation of `candidate.json`, `release.json`, and
`validation-contract.json`, derived IDs, file map, and cross-links.
Published candidate JSON uses the shared `pretty_json_bytes` encoding from
`scripts/model_identity.py`; identity digests remain compact
`canonical_json_digest`. It does not acquire bytes, prove the
manifest was measured from the claimed source, prove physical compatibility,
capture evidence, issue a decision, assign status, or write the trusted
registry. Explicit profile-reference mappings may replace a local artifact
argument with its public artifact key before hashing; those mappings must match
an argument and are never persisted.

Stage 2 is implemented by `scripts/model_validation_evidence.py`. It provides
pure builders and fail-closed validators for content-addressed evidence
references, immutable attempt/run records, Model Serving Release validation
bundles, and reviewed validation decisions. A run binds one release and frozen
contract to exact timestamps, completion condition, rank-relative observed
hardware/runtime versions, opaque boot/launch identities, commands, selected
subsystem maturity and distribution provenance, the pre-qualification
verification barrier, criterion measurements, and content-addressed evidence.
Bundles require the exact immutable run and artifact sets. Decisions explicitly
record a supplied base-status assertion but independently recompute criterion
dispositions from frozen thresholds, required context depths/token minimums,
soak duration/concurrency/error limits, and applicable predecessor-relative
throughput/latency budgets. Every attempted criterion is accounted for and
every applicable observation is considered automatically; an exclusion
requires an explicit evidence-backed record, and
the deterministic conflict rules in this ADR are enforced. A status or result
mismatch fails.
Missing required evidence remains incomplete, inconclusive evidence remains
inconclusive, and a conclusive requirement miss fails. Strict evidence cannot
span live server boots, bundles reject reused attempt identities, and review
timestamps cannot precede their evidence or the decisions they supersede.
Incomplete privacy or provenance review cannot become `Validated`, and a failed
distribution before the qualification barrier derives `Untested`. Experimental
subsystem use is recorded but does not cap the result. Runtime compatibility
and architecture/geometry are checked structurally without claiming physical
behavior. Command descriptors use the closed typed schema and reject raw site
values. Relative
baselines bind the predecessor contract, bundle, decision, and run and require
a pass for the relevant predecessor criterion, not a globally `Validated`
predecessor. Relative evaluation accepts an external
`predecessor_evidence_registry`; each entry must contain the exact `release`,
`contract`, `evidence_bundle`, `run_records`, and `decision` source set named
by the frozen predecessor IDs. The registry is validation input, not trusted
persistence or part of the current decision. When a predecessor decision
itself has supersession links, the matching source set must also carry
complete `prior_decision_sources`: each nested entry is a full source set
for a prior decision in that same release/contract lineage. The resolver
validates chronology, same-release/contract constraints, acyclicity, exact
bundle/runs, and recursive predecessor requirements. Shape-only prior
decisions and incomplete lineage fail closed. This is a caller-supplied
registry-contract extension, not a persisted schema-version change.
Later decisions carry immutable
backward supersession links; chronology must be strictly later and the
relationship must remain acyclic. Effective `Superseded` projection accepts a
fully validated `decision_evidence_registry` and does not mutate the prior
decision or establish that any supplied registry was repository-issued.

Stage 2 still performs no command execution, evidence capture, filesystem or
network I/O, trusted publication, catalog update, profile edit, or serving
gate. A syntactically valid review block or `Validated` fixture is not proof
that repository review or physical qualification occurred. Reviewer must be a
privacy-safe identifier. `review_reference` uses a closed repository-review
grammar (`pr:<id>`, `commit:<40-or-64 hex>`, or
`repository-review:<identifier>`). Shape validation cannot prove the named
review occurred. No current release received an ADR 0004 decision from this
implementation unit.

Read-only trusted persistence is implemented by
`scripts/model_serving_release_registry.py` and
`scripts/model-serving-release-registry.sh`. The tracked store is
`models/model-serving-releases/` with separately tracked namespaces
`descriptors/`, `contracts/`, `run-records/`, `evidence-bundles/`, and
`decisions/`. The commands are `verify`, `show-release`, and
`show-decision`. This layer verifies filesystem layout, content IDs, the
object graph, and publishable evidence hashes. A stored Validation Contract
with a required relative-performance gate must resolve and semantically
validate every frozen predecessor source even before a current decision
exists. More than one later decision that directly supersedes the same
record fails verify through the canonical supersession check. It does not
capture evidence, issue a decision, change recommendation policy, authorize
serving, or launch a release. Inspection of a release is informational: one
contract lineage
with one unsuperseded reviewed decision may display that decision's
evidence-derived effective status; no reviewed decision is a neutral
no-reviewed-decision state, never inferred `Untested`; multiple contract
lineages or unsuperseded heads are ambiguous and fail closed when one
reviewed status is requested. Catalog and operator surfaces consume this
verified inspection only for profiles explicitly bound to a release ID. The
tracked store currently contains no issued object.

Local ADR 0004 evidence-capture candidate persistence is implemented by
`scripts/model_serving_release_capture.py` and
`scripts/model-serving-release-capture.sh`. That workflow composes a
verified release-plan candidate with a separate attempt-only spec
(`--release-plan DIR --attempt-spec FILE`), independently validates the
release and contract through `scripts/model_serving_release.py`, checks
tracked-registry equality when those IDs exist, captures immutable run
records and content-addressed evidence, assembles compatible run records,
and independently verifies the resulting candidate under a gitignored
output boundary. It persists no planner path or planner candidate ID. The
old embedded `--spec` / `pulsar-model-serving-release-capture-spec` path is
rejected with a migration message; there is no dual compatibility. A
successful candidate is unreviewed, has privacy review pending, changes no
catalog or profile status, launches nothing, never writes the tracked
registry, and does not issue `Untested`. A pre-barrier failure means
qualification did not start; absence of a reviewed decision stays
neutral. It is not a `validate/*` measurement adapter, a reviewed
decision, or a physical DGX claim. Closed validator-measurement documents
and `scripts/model-serving-release-attempt.sh` now compose attempt-only
specs for strict same-boot and absolute throughput/latency; capture still
consumes those specs and derives program versions and evidence digests.
See
[MODEL_SERVING_RELEASE_CAPTURE.md](../MODEL_SERVING_RELEASE_CAPTURE.md).

### Pre-issuance schema correction

The source-neutral primary artifact, observation, predecessor-lineage,
structural-compatibility, command, chronology, and acyclicity rules above are a
pre-issuance correction to ADR 0004 schema version 1. No ADR 0004 release
descriptor, Validation Contract, run record, evidence bundle, or validation
decision has been issued, persisted through a trusted publication path,
referenced by a profile, or consumed by a serving gate. There is therefore no
released object to migrate and no reason to introduce schema version 2.
Earlier local unreviewed release-plan candidates, if any, are disposable and
must be rebuilt; this does not alter legacy schema-1 seals or bundles.

This statement does not apply retroactively to the older schema-1 expected
seals and combined validation bundles under `models/`. They are different,
legacy schemas owned by `scripts/model_identity.py`; their bytes, IDs,
historical evidence, and current enforcement behavior remain untouched.

Implement this decision in focused, reviewable units:

1. **Implemented:** add canonical source-neutral release-descriptor identity,
   Validation Contract schemas, and unreviewed release-plan candidate
   persistence in Python with deterministic fixtures, without modifying
   schema-1 artifacts;
2. **Implemented:** add immutable attempt/run records, evidence bundles, and
   validation decisions with fail-closed cross-link verification, independent
   status derivation, and explicit supersession;
3. **Implemented:** project the new statuses into catalog and operator surfaces without
   converting `STATUS=tested*` automatically. Status remains advisory, while
   recommendation/default policy remains the separate legacy projection.
   Profiles use an optional reviewed `MODEL_SERVING_RELEASE_ID` binding; no
   binding and no reviewed decision are neutral rather than inferred
   `Untested`. Read-only trusted persistence and verification supply the
   projected result. Local evidence-capture candidate persistence remains
   separate and does not project status or launch a release;
4. **Implemented:** create the supervised `pulsar-model-onboarding` skill
   around the supported subsystem CLIs and confirmation boundaries. The
   skill is control-plane orchestration only: it never issues a seal or
   validation decision, assigns status, binds a profile to a release,
   writes the trusted registry, promotes a path, or claims physical
   behavior. Current automated mapping covers only strict same-boot and
   absolute throughput/latency. The default unsealed replicated path is
   not an exact ADR 0004 qualification attempt. The skill may compose the
   source-attested service for an absent exact Hugging Face home, but that
   acquisition remains catalog/artifact evidence and creates no validation
   authority or physical claim.
   The skill-local journal is isolated under the `workflows/` namespace and
   is recovery state, not evidence. Deterministic skill/journal tests make no
   physical DGX claim; and
5. **Implemented:** complete and publish the separate bounded `library-hot` GA
   closure evidence. The reviewed two-rank subsystem is GA, while remote
   one-rank and legacy-unsealed use remain experimental. This stage did not
   issue a Model Serving Release decision or change the guided default.

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

### Gate serving permission on validation status

Users need to run experimental, incomplete, failed, or superseded releases for
evaluation, diagnosis, and workloads whose priorities differ from the frozen
criteria. Making status an allowlist would turn an evidence label into access
control and hide useful, accurately described choices. Pulsar instead preserves
strict `Validated` meaning, visible warnings, and separate operational safety
checks.

### Use one global performance threshold

Model size, workload, context, and serving purpose differ too much for one
throughput or latency number to express satisfactory behavior honestly.

### Accept FP-equivalent output as strict reproducibility

It is valuable for diagnosis and cross-boot comparison, but it is weaker than
the agreed same-boot invariant and would make `Validated` ambiguous.

### Let the onboarding skill seal or promote its own result

An orchestrator that both produces and authorizes evidence collapses the trust
boundary and can turn local observation into an official claim.

### Let reviewers select only favorable runs

Manual run selection can hide a valid failure or manufacture certainty from a
conflicted result. Every applicable observation is therefore included
automatically, with explicit evidence-backed exclusions and deterministic
adjudication.

### Require the predecessor release to be globally Validated

That would discard valid criterion-specific baselines for unrelated reasons.
Relative performance instead requires a reviewed pass for the exact predecessor
criterion, contract, bundle, decision, run, protocol, and geometry being used.

### Treat structural compatibility as physical qualification

Schema and envelope checks can reject mismatched architecture, runtime, or
geometry without running a model. They cannot show that the release behaves
correctly or reliably on physical hardware, so physical evidence remains
mandatory.

## Consequences

- `Validated` becomes a precise claim about one immutable serving tuple.
- A tuple change always creates a new release and requires a new decision.
- Criteria are explicit before testing, while model-specific thresholds remain
  possible.
- Failed and inconclusive work remains useful, visible evidence rather than a
  hidden or overwritten attempt.
- Attempt declarations must be covered exactly, applicable evidence cannot be
  cherry-picked, and conflicts have one deterministic adjudication rule.
- Criterion-specific performance lineage can reuse a reviewed passing
  predecessor result without overstating that predecessor's global status.
- Distribution experiments can improve onboarding without contaminating model
  status, provided the pre-qualification verification barrier holds.
- Status communicates confidence and results but never grants or denies serving.
  Recommendation/default policy may prefer stronger evidence; operational
  admission may still reject an unrunnable or unsafe launch for concrete reasons.
- Current commands and legacy schemas continue to work during migration.
  Catalog/operator surfaces distinguish the ADR 0004 release decision from
  legacy `STATUS`; the latter still drives recommendation order and is never
  automatically converted. Read-only projection does not create a decision or
  physical claim.
- Physical qualification is still required for physical claims. Documentation
  and selftests alone cannot produce a `Validated` decision.
