# Model release identity service

This is the maintainer runbook for producing **unreviewed model release
candidates**. It implements the candidate-assembly portion of the identity
design in [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) and the
qualification boundaries in
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md). It is
not an operator model-download workflow, is not routed through `./pulsar` or
the wizard, and cannot create a trusted Pulsar validation claim by itself.

**Current-schema warning:** this tool predates the Model Serving Release object
model accepted in
[ADR 0004](./decisions/0004-model-serving-release-validation.md). It assembles
schema-1 validation-bundle and expected-seal candidates, whose IDs include
evidence and issuance metadata. It does not create the separate ADR 0004
release descriptor or frozen Validation Contract. Those now have a distinct
unreviewed planner, `scripts/model-serving-release-plan.sh`, backed by the pure
schemas in `scripts/model_serving_release.py`. The legacy service also does not
create the immutable
run records, new validation bundles, or reviewed decisions whose pure schemas
are implemented in `scripts/model_validation_evidence.py`. Its bundle ID must
not be presented as a Model Serving Release ID. Existing candidates and issued
artifacts remain immutable.

The corrected ADR 0004 objects remain schema version 1 because none was issued
or persisted before this pre-issuance correction. Existing schema-1 seals and
combined bundles produced by this legacy candidate service are different
formats and remain byte-for-byte unchanged.

## System boundary

The release identity service sits between lab validation and model-library
enforcement:

| Subsystem | Responsibility |
|---|---|
| Profile registry (`models/*.conf`) | Declares the exact runtime configuration to normalize |
| Validation (`validate/`, `results/`) | Produces model-qualification evidence for exact model/image/profile/geometry inputs |
| Identity schema (`scripts/model_identity.py`) | Owns canonical profile, validation-bundle, and expected-seal schemas and IDs; also owns shared `pretty_json_bytes` publication encoding |
| ADR 0004 schema (`scripts/model_serving_release.py`) | Owns pure release-descriptor and frozen Validation Contract schema version 1; performs no I/O, issuance, or status assignment |
| ADR 0004 release-plan candidates (`scripts/model-serving-release-plan.sh`) | Sources a profile and assembles/verifies unreviewed source-neutral release and contract candidates from explicit manifest, runtime/hardware, and criteria inputs; exposes `load_verified_release_plan_candidate` for planner `verify` and capture; never writes the tracked registry |
| Immutable descriptor directories (`scripts/immutable_descriptor_dir.py`) | Generic descriptor-rooted immutable-directory primitives only: exact file-set, 0700/0600, regular-file/no-symlink, fd-relative reads, mutation/replacement detection, snapshot recheck; not a schema owner |
| ADR 0004 evidence schema (`scripts/model_validation_evidence.py`) | Owns pure evidence-artifact, immutable run-record, new validation-bundle, reviewed-decision, status-derivation, and supersession schema version 1; performs no capture, persistence, or trusted issuance |
| ADR 0004 registry (`scripts/model-serving-release-registry.sh`) | Read-only load, verify, and inspect stored objects under `models/model-serving-releases/`; supplies advisory projection for explicitly bound profiles but does not capture evidence, issue a decision, authorize serving, or launch a release |
| ADR 0004 evidence-capture candidates (`scripts/model-serving-release-capture.sh`) | Composes a verified release-plan candidate with an attempt-only spec; local unreviewed persistence of run records, content-addressed evidence, and assembled bundles; never writes the tracked registry or launches a release |
| ADR 0004 issuance staging (`scripts/model-serving-release-issue.sh`) | Maintainer-only plan/stage of untrusted registry proposals from a verified capture candidate plus a closed review declaration; local success is not repository review, serving authorization, or physical qualification |
| Release candidate (`scripts/model-release.sh`, `scripts/model_release.py`) | Hashes an exact local snapshot and assembles internally consistent, explicitly untrusted candidate documents |
| Library runtime (`scripts/model-library.sh`, `scripts/model_library.py`) | Enforces repository-reviewed seals/bundles during catalog, preparation, and launch |
| Release review | Combines every required subsystem scope before changing `STATUS`, guided exposure, or defaults |

The Bash entrypoint sources the profile and passes its values to Python. Python
owns normalization, digests, candidate schemas, atomic writes, and fail-closed
policy. The service does not acquire model bytes, run validation gates,
qualify a model or storage path, edit a profile, copy documents into `models/seals/` or
`models/validation-bundles/`, or change `STATUS`.

## Model Serving Release schema boundary

The ADR 0004 stage-1 library can build and validate two in-memory or externally
loaded JSON-compatible objects:

| Object | Included | Deliberately excluded |
|---|---|---|
| Model Serving Release descriptor | Complete content-addressed Model Artifact Set; normalized vLLM recipe and runtime-access contract; image digest and host-compatibility envelope; privacy-safe supported geometry; four-part `release_id` | Status, evidence, reviewer, timestamps, transport, placement, and exact site topology |
| Frozen Validation Contract | `release_id`; fixed repository invariants; release-specific workloads, protocols, sample sizes, thresholds, context/soak conditions, and comparable-predecessor rule; `contract_id` | Observed results, run records, reviewer disposition, issuance metadata, and validation status |

The stage-2 library adds the three remaining immutable roles plus
content-addressed evidence references:

| Object | Included | Deliberately excluded |
|---|---|---|
| Run record | Exact release/contract IDs, unique attempt identity and timestamps, completion condition, sorted `attempted_criterion_ids`, structured `observed_environment.cluster` and per-rank compatibility observations, opaque boot/launch IDs, closed typed `commands[].arguments[]` and value-free `commands[].environment[]` descriptors, preparation transport/subsystem provenance, full-verification barrier, current-release criterion measurements, timestamp-bound context/soak observations, and evidence IDs; `run_record_id` | Trusted review, predecessor baseline measurements, profile status, and runtime mutation |
| ADR 0004 validation bundle | Exact release/contract IDs, immutable run IDs, content-addressed artifact descriptors and privacy state, review-evidence IDs, qualification-started fact, and criterion coverage; `bundle_id` | Reviewer authority, final status, profile mutation, and legacy schema-1 seal/bundle identity |
| Validation decision | Exact release/contract/bundle IDs; every automatically aggregated disposition with `included_run_record_ids`; explicit evidence-backed `excluded_run_records`; provenance/security/privacy review; an explicit base-status assertion that must equal the derived result; repository-review metadata; and backward supersession links; `decision_id` | Proof that the named review actually occurred, trusted placement/publication, catalog projection, or a launch operation |

The descriptor cross-checks recipe TP/PP against the declared geometry. The
recipe stores only behavior-affecting, non-secret environment entries;
credential and deployment/placement environment names are rejected rather than
hashed into a release descriptor. Every persisted free-form release/contract
string without a stricter closed grammar—including artifact identifiers,
criterion/workload/protocol/threshold strings, environment values, remaining
engine and speculative-decoding arguments, and extensible parameter keys and
values—is screened for recognized credential patterns, absolute site paths,
explicit URIs, private/site endpoint forms, environment/path references, and
deployment-only assignments. Credential-bearing extensible keys fail even when
their value is opaque; ordinary dotted public identifiers remain valid.
TP/PP, GPU memory utilization, speculative decoding, artifact use, and access
contract are structured fields and cannot be repeated ambiguously in the
remaining ordered engine arguments. The
contract requires stability, accuracy, throughput, latency, exact same-boot
reproducibility, reviewed provenance/security, serving integration, and
physical-geometry criteria. Their scopes are fixed: stability, accuracy,
throughput, latency, and strict same-boot are `model-qualification`; serving
integration is `serving-integration`; and provenance/security plus physical
geometry are `release-promotion`. `catalog-artifact` evidence can establish
preparation and the qualification barrier but cannot satisfy a criterion.
Because provenance/security is derived from fixed review components rather
than a run metric, that criterion must equal the canonical closed template;
additional thresholds, parameters, workloads, or sample requirements are
rejected rather than ignored.
A relative latency/throughput budget is valid only when it binds a reviewed
predecessor contract, bundle, decision, and exact run, the relevant predecessor
criterion passed, and the benchmark protocol and supported geometry are
identical. The predecessor release need not be globally `Validated`; otherwise
the relative gate is `N/A` and absolute criteria remain.

Each run hash-binds a sorted `attempted_criterion_ids` declaration. A
post-barrier non-preparation run must declare at least one known criterion in
its scope, and its `criterion_observations` must cover that declaration exactly.
A failed, interrupted, or otherwise incomplete attempt must record
inconclusive observations for every declared criterion. The pure decision
builder accepts exclusions through `criterion_exclusions`; it includes all
other applicable observations automatically and persists each
exception under `criterion_results[].excluded_run_records`. Relative evaluation
accepts a `predecessor_evidence_registry` whose entries contain the exact
`release`, `contract`, `evidence_bundle`, `run_records`, and `decision` source
set. Effective supersession accepts a fully validated
`decision_evidence_registry`. These registries are caller-supplied validation
inputs, not a trusted store or proof of issuance. A predecessor decision
with supersession links must carry complete `prior_decision_sources` so
chronology, same-release/contract constraints, acyclicity, exact bundle/runs,
and recursive predecessor requirements can be checked.

The fixed deterministic fixture and mutation suite are
`scripts/testlib/model_serving_release_fixture.py` and
`scripts/testlib/test_model_serving_release.py`. They prove schema and hashing
contracts only. They do not prove physical behavior, issue a release, create a
reviewed decision, or alter the current serving path. Stage 1 still has no
trusted writer; its planner persists explicitly unreviewed candidates only.
Read-only verification of later stored objects uses
`scripts/model-serving-release-registry.sh`.

`scripts/testlib/model_validation_evidence_fixture.py` and
`scripts/testlib/test_model_validation_evidence.py` fix the stage-2 IDs and
exercise threshold/sample/protocol tamper, release/contract/run/artifact
cross-links, required context and soak conditions, automatic relative
throughput/latency budgets, experimental distribution provenance, the
pre-qualification barrier, privacy state, exact same-boot selection, independent
status derivation, and immutable supersession. Every declared attempt is
accounted for, and every applicable observation is included automatically;
explicit evidence-backed exclusions and pass/fail/inconclusive conflict rules
are validated. Runtime compatibility and architecture/geometry are checked
structurally. Canonical compatibility ranges compare the dotted numeric core of
exact observed versions while preserving deployed zero-padding and vendor
suffixes in the run record. Commands use an allowlisted repository program, a
`sha256:<digest>` version identity, exactly one program-specific closed
operation, closed repository resources, typed criterion references, typed
`--host`/`--rank`/`--url` site references with rank references bounded by the
release geometry, and value-free classified environment references. A
completed nested context or soak failure remains conclusive even if the outer
criterion is inconclusive. Soak timestamps must
exactly agree with `duration_seconds`, and supersession must be chronologically
later and acyclic. A later decision stores its normal reviewed outcome and points
backward; readers project the older decision as `Superseded` without changing
the older bytes. These remain control-plane schema tests, not evidence capture
or physical qualification.

Read-only persistence and verification for stored ADR 0004 objects now live
under `models/model-serving-releases/` and
`scripts/model-serving-release-registry.sh`. That CLI does not capture
evidence, issue a decision, or launch a release. Optional reviewed
`MODEL_SERVING_RELEASE_ID` binding and advisory catalog/operator projection
are implemented; `qwen3.8-27b-fp8` binds the first reviewed lineage and
projects `Testing incomplete`, while other current profiles remain neutral.
Local ADR 0004 evidence-capture
candidate persistence is documented in
[MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md) and
is a separate unreviewed workflow. A locally constructed decision
whose fields say `Validated` is only a syntactically consistent document until
repository review deliberately publishes it into that store.

These structural checks reject known leak classes; they do not inspect the
working tree to prove that a supplied program digest is correct and cannot
recognize every possible private codename. Trusted capture must compute the
digest, and publication still requires the artifact privacy review and current
tree privacy audit.

Under ADR 0004, the supervised operator workflow is the separate
`pulsar-model-onboarding` skill. It may compose exact-home reuse, distribution,
verification, launch, tests, and evidence capture—including explicitly
selected Experimental subsystems—but it does not inherit trusted issuance,
validation, or promotion authority from this maintainer tool. For an absent
brand-new unsealed Hugging Face repository, the skill may compose the
source-attested read-only plan and separately confirmed exact-commit
acquisition. Reuse of that home requires receipt-backed offline full
verification. Unknown and pre-existing homes still require full verification
against a reviewed expected manifest independent of the observed tree. The
acquisition creates catalog/artifact evidence only. Current automated mapping
covers only strict same-boot and absolute throughput/latency. Deterministic
skill and journal tests make no physical DGX claim and create no release
decision.

## Unreviewed ADR 0004 release planning

Use the separate planner when the desired output is the ADR 0004 release and
frozen contract rather than a legacy seal/bundle candidate:

```text
scripts/model-serving-release-plan.sh build <profile>
    --artifact-manifest FILE
    --runtime-envelope FILE
    --criteria FILE
    --model-access-contract local-verified-readonly
    [--artifact FILE --artifact-binding ARTIFACT_KEY=USE ...]
    [--artifact-reference ARTIFACT_KEY=PROFILE_REFERENCE ...]
    [--output-dir DIR] [--json]

scripts/model-serving-release-plan.sh verify <profile>
    --candidate-dir DIR
    --model-access-contract local-verified-readonly
    [--artifact-reference ARTIFACT_KEY=PROFILE_REFERENCE ...]
    [--json]
```

The artifact input is an existing complete
`model-library-snapshot-manifest`, not a caller-supplied bare digest. For a
Hugging Face profile, its public model ID and exact commit must match the
profile. For an absolute-path/catalog profile, the manifest supplies a public
logical artifact ID and revision; the source path is never persisted. The
runtime-envelope document has kind
`pulsar-model-serving-release-runtime-envelope`, schema version 1, and contains
the complete `runtime_image_identity` and `supported_hardware_geometry`
objects. The planner requires the image digest, node count, TP/PP, topology,
and rail contract to match the sourced profile instead of inferring physical
compatibility. The criteria document contains exactly `criteria`,
`context_requirement`, `soak_requirement`, and `relative_performance`.
Repeat `--artifact` with one schema-valid artifact object and
`--artifact-binding` with its `ARTIFACT_KEY=USE` mapping for every separate
draft, adapter, tokenizer override, model-code payload, or other
behavior-affecting artifact. The planner requires a one-to-one binding and
never infers omitted artifacts from opaque engine arguments. When a sourced
profile argument names an artifact by a deployment-local path or source
identifier, `--artifact-reference ARTIFACT_KEY=PROFILE_REFERENCE` replaces
that exact value (including an exact JSON string value) with the public
artifact key before release hashing. The mapping must match a profile argument,
must be supplied again to `verify`, and is never persisted.

Default output is the new, gitignored
`experiments/model-onboarding/<profile>/<release-id>/` tree containing
`candidate.json`, `release.json`, and `validation-contract.json`. Published
candidate JSON uses the shared `pretty_json_bytes` encoding from
`scripts/model_identity.py` (`indent=2`, `sort_keys=True`,
`ensure_ascii=False`, trailing newline). Canonical identity digests remain
compact `canonical_json_digest`. The
candidate is `unreviewed`, has authority `none`, privacy review `pending`, and
promotion `not-authorized`. Existing output, tracked repository locations,
and `models/` are refused. Planner `verify` uses the public schema-owning
`load_verified_release_plan_candidate(dir)` loader. That loader applies the
generic descriptor-directory primitives in
`scripts/immutable_descriptor_dir.py`, then validates `candidate.json`,
`release.json`, and `validation-contract.json`, derived IDs, file map, and
cross-links. Verification also checks the current profile
recipe/image/geometry. It
does not prove that the manifest came from the claimed source, that the runtime
envelope works on physical hardware, or that any criterion passed.

Capture `plan` and `capture-run` consume that same verified loader
directly through `--release-plan DIR`. They do not persist the planner path
or planner candidate ID. See
[MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md).

## Issued profiles

The one-node diagnostic `qwen3-1.7b` profile is the first claim issued through
this workflow. Its reviewed trust roots are:

- expected seal
  `ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94`;
- validation bundle
  `9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380`;
- exact model commit
  `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`; and
- complete manifest
  `775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`.

The reviewed evidence is indexed in
[results/model-library/README.md](../results/model-library/README.md). A fresh
candidate reproduced byte-for-byte before publication, the trusted bundle
verifier returned `match`, and a post-issuance physical `library-hot`
preparation/launch used those exact identities. This narrowly establishes the
one-node diagnostic claim. It does not seal `qwen3-1.7b-2node` or content-bind
legacy-unsealed launches. Live-mount serving is retired (ADR 0005); the model
library is the only weight mechanism (ADR 0006), and sealed acquisition and
launch enforce this issued identity.

The flagship `deepseek-v4-flash` profile is the second issued claim. Its
reviewed trust roots are:

- expected seal
  `1ba9ca8e3c34a9143588cc1315474e9cca0724351f0856caed5bb1116b89555a`;
- validation bundle
  `8fda1d93c5e08cbba18df5b26b0632354c6559ab939d3763dbdbdf38ead6b236`;
- exact model commit
  `7872f01b1d1fe23eabc4c98b48bffcef5a386062`; and
- complete manifest
  `27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`.

Its candidate and an independent reproduction were byte-identical, and the
trusted bundle verifier returned `match`. The issuance binds the reviewed
DeepSeek GA content, digest-pinned image, normalized two-node profile, and
repository evidence. The later post-issuance and one-home physical gates passed
in the catalog/artifact and serving-integration scopes. Neither issuance nor
those gates claim bit-identical output or promote the model library; the
strict-determinism decision and sustained soak remain separate release blockers.

## Trust boundary

Every generated descriptor says:

```text
state=unreviewed
authority=none
privacy=pending
promotion=not-authorized
```

Repository-local output is allowed only below the gitignored
`experiments/release-candidates/` tree. An explicit output outside the
repository is also allowed. Output below `models/`, elsewhere in the tracked
repository, at a broad root, or over an existing directory is refused.

Candidate generation proves that the documents are deterministic and
internally consistent. It does **not** establish catalog acceptance, serving
integration, model qualification, or release promotion. It also does not prove
that the bytes were used in the lab, that the evidence is sufficient or
private-data-free, or that maintainers approved the claim. Those remain review
decisions.

Even a candidate whose behavioral gates pass remains `Testing incomplete`
under ADR 0004 until every required gate and provenance/security review is
complete and a reviewed validation decision exists. The current tooling does
not assemble or publish that decision. The pure stage-2 schema can reject an
internally inconsistent decision candidate, but it cannot establish reviewer
authority or change the current serving/status implementation.

## Preconditions

- Work on a feature branch.
- Resolve the Hugging Face model to an exact 40–64 character lowercase commit.
- Retain the complete snapshot used for validation.
- Pin the profile image with `@sha256:<digest>` before `plan`, `assemble`, or
  `verify-candidate`. Mutable image tags cannot form a validation bundle.
- Publish sanitized, repository-relative evidence for the exact model,
  runtime, image, and geometry being claimed.
- Identify every behavior-affecting external tokenizer, draft model, adapter,
  or model-code artifact not already covered by a snapshot manifest.

## Candidate workflow

First inspect the normalized profile contract:

```bash
scripts/model-release.sh plan <profile> --json
```

Hash the exact snapshot. The command does not follow `refs/main`; the commit is
required explicitly. Empty snapshot files are hashed (size 0) so a
source-attested home that already accepted them can produce an observed
manifest; this still does not issue a seal or status.

```bash
scripts/model-release.sh manifest <profile> \
  --hub-path /path/to/hub/models--namespace--repository \
  --revision <exact-commit> \
  --json
```

The default observed-manifest directory is:

```text
experiments/release-candidates/<profile>/<commit>/observed/
```

After the evidence exists, assemble the validation-bundle and expected-seal
candidates. Repeat `--evidence` for every repository-relative artifact. An
optional `--external-artifact <json>` supplies one schema-valid external
artifact descriptor per occurrence.

```bash
scripts/model-release.sh assemble <profile> \
  --manifest experiments/release-candidates/<profile>/<commit>/observed/snapshot-manifest.json \
  --issuer <lab-issuer> \
  --issued-at <RFC3339-UTC> \
  --evidence results/<path-to-evidence>.json \
  --json
```

The default assembled directory is:

```text
experiments/release-candidates/<profile>/<commit>/release-candidate/
```

It contains exactly:

```text
candidate.json
expected-model-seal.json
snapshot-manifest.json
validation-bundle.json
```

Verify the candidate against the currently sourced profile and current
repository evidence:

```bash
scripts/model-release.sh verify-candidate <profile> \
  --candidate-dir experiments/release-candidates/<profile>/<commit>/release-candidate \
  --json
```

Verification checks the candidate content address, exact manifest, seal/bundle
cross-links, normalized live profile, evidence presence, file set, and
unreviewed authority state. Any profile drift or document tampering fails.

## Review and issuance

The repository can stage an untrusted ADR 0004 issuance proposal with
`scripts/model-serving-release-issue.sh`. That local command is not trusted
until repository review and merge. Every
release pull request must receive maintainer review that confirms:

- the manifest came from the exact snapshot used by the recorded lab run;
- the complete candidate is reproducible from the claimed inputs;
- model, external-artifact, image, runtime, memory, and geometry identities
  match the evidence;
- every referenced artifact exists and passed privacy review;
- the bundle filename is its `bundle_id` and the seal is reviewed under the
  profile name;
- adding `EXPECTED_MODEL_SEAL` is justified by exact identity evidence; and
- any `STATUS`, guided-exposure, or default-policy change is justified by every
  applicable subsystem gate in `REVALIDATE.md`.

Only after that review may the pull request deliberately place the reviewed
documents under the trusted model directories and update the profile. The
candidate tool has no command for that action. After the reviewed files and
profile reference exist, run:

```bash
scripts/model-library.sh validation-bundle verify <profile>
scripts/selftest.sh
```

Candidate output is never itself release evidence and never changes the
validation ledger. Preserve failed validation evidence according to
[REVALIDATE.md](./REVALIDATE.md); do not turn locally observed user content into
an expected identity.
