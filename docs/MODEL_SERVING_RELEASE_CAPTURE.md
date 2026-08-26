# ADR 0004 evidence-capture candidate persistence

This is the maintainer runbook for the local, candidate-only ADR 0004
evidence-capture workflow. It composes a verified unreviewed release-plan
candidate with a separate attempt-only spec, independently validates the
release and contract objects, captures immutable run records plus
content-addressed evidence, assembles compatible run records into one
immutable evidence bundle, and independently verifies the resulting
candidate.

The workflow makes capture repeatable. It is not an issuing or promotion
authority. A successful candidate is explicitly unreviewed, has privacy
review pending, changes no catalog or profile status, launches nothing, and
never writes the tracked release registry. It does not issue `Untested`.
A pre-barrier failure means qualification did not start; absence of a
reviewed decision stays neutral.

This is **ADR 0004 evidence-capture candidate persistence**. It is not
catalog/operator status projection (ADR 0004 numbered item 3). It does not
adapt `validate/*` output itself and makes no physical DGX claim. Closed
validator-measurement documents and the separate attempt-composition service
in `scripts/model-serving-release-attempt.sh` are the producer of attempt-only
specs; this workflow still consumes those specs unchanged.
The separate source-neutral release planner documented in
[MODEL_RELEASE.md](./MODEL_RELEASE.md) publishes the release and contract
values. Capture `plan` and `capture-run` consume that planner directory
through the planner's public `load_verified_release_plan_candidate`
loader. Planning does not make those objects reviewed and does not weaken
this workflow's independent validation. Capture persists no planner path
or planner candidate ID.

## System boundary

| Subsystem | Responsibility |
|---|---|
| Attempt-only spec (this document) | Closed operator input: defensive `release_id` / `contract_id` foreign keys plus attempt fields, provenance/environment, command descriptors without program versions, criterion observations, and evidence-source metadata. Embeds neither release nor contract. |
| ADR 0004 schema (`scripts/model_serving_release.py`) | Owns release-descriptor and frozen Validation Contract schema version 1; capture validates those objects independently |
| Release-plan candidates (`scripts/model-serving-release-plan.sh`) | Unreviewed upstream release/contract values; capture consumes a verified planner directory through `load_verified_release_plan_candidate` |
| Immutable descriptor directories (`scripts/immutable_descriptor_dir.py`) | Generic descriptor-rooted immutable-directory primitives only; not a schema owner |
| ADR 0004 evidence schema (`scripts/model_validation_evidence.py`) | Owns evidence-artifact, run-record, evidence-bundle, and reviewed-decision schema version 1 |
| Capture persistence (`scripts/model-serving-release-capture.sh`) | Plans, captures, assembles, and verifies unreviewed candidates under a gitignored output boundary |
| Tracked registry (`scripts/model-serving-release-registry.sh`) | Read-only verification of reviewed objects under `models/model-serving-releases/`; this tool never writes it |
| Issuance staging (`scripts/model-serving-release-issue.sh`) | Separate maintainer workflow that can stage an untrusted proposal from a verified capture candidate; capture still never writes the registry |
| Validator measurements (`validate/compare_captures.py`, `validate/bench_serve.py`) | Optional closed measurement documents for `compare-captures` and `benchmark-serving`. Exit zero or a selftest is not a criterion pass. |
| Attempt composition (`scripts/model-serving-release-attempt.sh`) | Maps verified release-plan criteria plus caller context and validator measurements into existing attempt-only specs. This slice requires publishable `results/` files: the supplied measurement path and evidence `repository_path` must name the same stably read file. Generated specs are capture-validated against those current bytes, then published as one exclusive two-file directory under `experiments/model-serving-release-attempts/` or a safe explicit outside path. The attempt spec carries no precomputed publishable digest; later capture independently re-reads the file and derives the digest. Emits metrics and completion only. |

The Bash entrypoint is the operator boundary. Python owns attempt-spec
loading, verified release-plan composition, derivation, filesystem-safe
publication, assembly, verification, and both human and JSON rendering.
The command is not routed through `./pulsar`, the wizard, or
profile/catalog status projection, and it launches nothing.

## Commands

```text
scripts/model-serving-release-attempt.sh plan-invocation
    --release-plan DIR [--output FILE] [--json]
scripts/model-serving-release-attempt.sh compose
    --release-plan DIR --context FILE --output-dir DIR
    [--compare-measurement FILE] [--benchmark-measurement FILE] [--json]
scripts/model-serving-release-attempt.sh bench-argv --invocation-plan FILE

scripts/model-serving-release-capture.sh plan --release-plan DIR --attempt-spec FILE [--json]
scripts/model-serving-release-capture.sh capture-run --release-plan DIR --attempt-spec FILE
    [--output-dir DIR] [--json]
scripts/model-serving-release-capture.sh assemble-bundle
    --candidate-dir DIR [--candidate-dir DIR ...]
    [--output-dir DIR] [--json]
scripts/model-serving-release-capture.sh verify-candidate
    --candidate-dir DIR [--json]
```

`plan-invocation` reads the frozen contract and can persist an explicit bench
argv plan. Passing that plan to `validate/run-gates.sh --invocation-plan FILE`
changes only the benchmark arguments and fails closed on an invalid plan; it
does not change the ordinary default sweep. The compare section reports the
contract sample size but does not rewrite `validate/prompts.txt` or synthesize
prompts. If the captured prompt count differs, composition records an
inconclusive sample/protocol mismatch. A benchmark plan is refused when its
sample size is smaller than its largest declared concurrency, because that
request count cannot exercise the frozen concurrency protocol.

`compose` consumes the verified release-plan directory, the two closed
validator measurement files, and a closed caller context. The context supplies
the existing capture-contract provenance and observed environment, unique
attempt IDs/timestamps for `compare-captures` and `benchmark-serving`, typed
command environment/site options, and one publishable `application/json`
evidence source under `results/` for each operation. Its source paths must name
the same files supplied on the measurement flags. This low-level context is
assembled by the supervised `pulsar-model-onboarding` skill; the
composer validates it but does not discover topology, launch a server, infer
attempt timestamps, or create missing validator output. The skill records
separate wall-clock UTC start/end timestamps for compare and benchmark and
must not invent a missing validator measurement.

If `validate/run-gates.sh` receives SIGINT or SIGTERM, it stops before starting
another gate and exits with the signal-compatible status. Any validator output
already persisted remains partial evidence; interruption never advances into a
later capture or benchmark.

Each emitted file is an ordinary attempt-only spec accepted by the capture
commands below. Run capture immediately after composition. The output
directory is an exclusive unreviewed two-file directory; an existing target or
partial validation failure leaves it untouched.

`plan` and `capture-run` require both `--release-plan DIR` and
`--attempt-spec FILE`. The old `--spec` flag and the old kind
`pulsar-model-serving-release-capture-spec` are rejected with a migration
message that names `--release-plan DIR --attempt-spec FILE`. There is no
dual compatibility.

Default output is gitignored
`experiments/model-serving-release-captures/`. An explicit directory
outside that root is accepted only when it satisfies the same safety
rules: never `/`, the repository root, `models/`,
`models/model-serving-releases/`, `.git`, or the capture-root directory
itself.

Human output is scan-friendly at narrow widths. Machine output is stable JSON
with `schema_version: 1` and carries no serving-permission field. Neither mode prints absolute protected
evidence paths, repository paths, private topology identifiers, or
secret values.

## Attempt-only spec

The attempt-only document kind is
`pulsar-model-serving-release-capture-attempt-spec`, schema version 1.
It embeds neither a release nor a contract. Required top-level
`release_id` and `contract_id` are defensive foreign-key cross-checks
against the verified planner objects. Closed top-level fields are:

| Field | Role |
|---|---|
| `release_id` | Defensive foreign-key cross-check against the verified planner release |
| `contract_id` | Defensive foreign-key cross-check against the verified planner contract |
| `attempt` | Attempt identity, phase, scope, attempted criteria, timestamps, and completion |
| `preparation_provenance` | Origin, transfer, subsystems, runtime sources, verification status only, barrier, elapsed time |
| `observed_environment` | Image digest, opaque boot/launch IDs, cluster shape, and per-rank observations; geometry ID is derived |
| `commands` | Allowlisted program, typed arguments, classified environment, and `repository-root`; no program version |
| `criterion_observations` | Measurements that reference evidence by `source_key`, not by precomputed artifact IDs. Nested context and soak sources are part of the run artifact set. |
| `evidence_sources` | Publishable `results/` files or protected digest locators |
| `review_source_keys` | Explicit, sorted source keys reserved for leftover review artifacts (files that are not run measurements). Every source must be used by the run (including nested context/soak) or listed here. Review sources must use `release-promotion` scope. Attempt composition for compare and bench emits `[]`. Capture copies that list into bundle `review_evidence_artifact_ids` and does not invent sources. Empty is expected. |

The loader rejects duplicate JSON keys, invalid UTF-8, `NaN`/`Infinity`,
unknown fields, embedded release or contract objects, precomputed derived
IDs other than the defensive foreign keys, precomputed program versions,
precomputed publishable file digests, privacy `passed`, decisions,
statuses, reviewer claims, authority claims, serving-authorization
claims, and process-exit or validator-output adapters. Protected
evidence necessarily supplies a content digest; that locator is
accepted and is not treated as a derived ID. A conclusive
criterion miss is a completed attempt with a complete failing
measurement. A crash, signal, unusable or missing output, or incomplete
attempt remains `failed` / `interrupted` / `inconclusive` and may
contribute only inconclusive observations. Process exit status is never
translated into a pass.

Release and contract bytes come from the verified release-plan
candidate, not from the attempt-only spec. Capture independently
validates those objects through `scripts/model_serving_release.py`.
If an object with the same derived ID exists in the tracked registry,
the canonical object must be equal; otherwise capture fails closed.
Missing registry objects are allowed. An unreadable or unsafe registry
path fails closed. The planner path and planner candidate ID are not
copied into the capture candidate or any ADR object it emits.

## Derived identity

Capture derives, and does not invent or review:

- release ID and contract ID, taken from the independently validated
  planner objects and cross-checked against the attempt-only foreign keys
- phase/scope consistency
- Model Artifact Set ID
- current checked-out allowlisted program SHA-256 versions
- evidence file SHA-256 digests and evidence-artifact IDs
- run-record ID, bundle ID, coverage, and qualification-started state
- candidate ID

Published candidate JSON uses the shared `pretty_json_bytes` encoding from
`scripts/model_identity.py` (`indent=2`, `sort_keys=True`,
`ensure_ascii=False`, trailing newline). Canonical identity digests remain
compact `canonical_json_digest`. Every generated evidence artifact has
privacy review `pending`. No planner path or planner candidate ID is
persisted.

## Evidence classes

| Class | Input | Stored in the candidate |
|---|---|---|
| Publishable | Sanitized regular file under tracked `results/`, excluding every `results/**/raw/` subtree | Content-addressed copy plus a repository-relative location |
| Protected | Opaque SHA-256 digest and approved non-sensitive metadata | Digest locator only; never a source path or bytes |

Symlinks and non-regular files are rejected. Dot-dot traversal is
rejected, and output locations are compared as normalized absolute
paths so a lexical capture root cannot escape into `models/`, the
tracked registry, `.git`, the repository root, `/`, or the capture
root itself. Ordinary `.` components are normalized by path parsing
before component checks and are not a separate raw-argument rejection.

Reads walk every path component from a trusted first absolute directory
with no-follow, file-descriptor-relative opens. After a no-follow
directory stat, the opened directory identity must match that preview
before the walk continues. File reads use `O_NONBLOCK` so a FIFO
substitution between stat and open cannot hang. Stable reads compare
device, inode, size, mode, `mtime_ns`, and `ctime_ns` before and after
the read. Publication holds the destination parent directory fd,
creates a private staging directory, and finishes with dirfd-relative
`renameat2(RENAME_NOREPLACE)` plus a parent fsync. Verify keeps the
candidate-root fd open, rechecks the exact directory snapshot, and
requires directory mode `0700` and file mode `0600`. Generic
descriptor-rooted exact-file-set, mode, regular-file/no-symlink,
fd-relative read, mutation/replacement, and snapshot-recheck primitives
are owned by `scripts/immutable_descriptor_dir.py`; that helper is not
a schema owner. These checks are
control-plane integrity only; they do not prove physical behavior.

## Candidate layout

One-run captures are published beneath
`<release-id>/runs/<run-record-id>/`. Assembled multi-run candidates are
published beneath `<release-id>/bundles/<bundle-id>/`. Each candidate is
self-contained:

```text
candidate.json
release.json
contract.json
run-records/<run-record-id>.json
evidence-bundle.json
evidence/<sha256>    # publishable copies only
```

The manifest kind is `pulsar-model-serving-release-capture-candidate`,
schema version 1. It identifies itself as unreviewed, authority none, privacy
pending, and promotion not authorized. It binds the release ID, contract ID,
sorted run-record IDs,
bundle ID, exact file map of every file except `candidate.json`, and the
candidate ID. It does not carry a decision, review outcome, validation
status, reviewer, protected source path, planner path, or planner
candidate ID.

Publication writes a private same-filesystem staging directory (mode
`0700`, files mode `0600`), fsyncs files and directories, and finishes
with a concurrency-safe no-replace rename. An existing destination or
concurrent writer fails without damaging either candidate. Historical
failed or partial evidence is preserved; this tool does not rewrite
another candidate.

## Assembly and verification

`assemble-bundle` accepts only independently verify-passing candidates
produced by this tool. They must share identical release and contract
objects, unique attempt IDs, and compatible immutable content. The same
content ID must mean byte/canonical equality. The same publishable
location must never resolve to conflicting digests. Multiple run records
are supported in this unit.

`verify-candidate` holds the candidate-root directory descriptor for the
whole operation. It records a snapshot of root and subdirectory
identities, modes, exact entry-name sets, and each file's stat
fingerprint plus bytes. After schema and cross-link checks, it
re-checks that snapshot through the held descriptors, confirms each
`run-records` / `evidence` name on the candidate-root fd still names
the same subdirectory inode, and confirms a fresh no-follow open of
the supplied path still names the same root inode.
Additions, removals, replacements, or path swaps fail closed.

After the pure ADR bundle schema validates, capture-candidate policy
still requires every evidence artifact to keep `privacy_review` pending
and every `review_evidence_artifact_ids` entry to use
`release-promotion` scope. An empty leftover list is valid. Those checks
do not change the broader schema enum and are not a reviewed decision.

Publishable artifact locations must still be sanitized `results/` files
and must not use any `raw` path. Protected artifacts must keep the
exact `sha256:<digest>` locator. Program or evidence drift fails
closed. There is no override. An output directory may not be an
existing candidate or any existing ancestor that contains
`candidate.json`.

## What this unit does not do

- Issue a reviewed validation decision or `Untested`
- Write `models/model-serving-releases/`
- Invent review sources or treat an empty `review_evidence_artifact_ids`
  list as a capture defect
- Change `STATUS`, a profile's release binding, recommendation/default policy,
  or runtime state
- Persist a planner path or planner candidate ID
- Adapt `validate/*` output itself; that mapping lives in
  `scripts/model-serving-release-attempt.sh`
- Claim physical DGX, model-download, container, or remote behavior
- Route through `./pulsar` or the wizard

Capture still does not issue a decision. Maintainer issuance staging is a
separate workflow in
[MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md);
a successful local issue command is not trusted until repository review and
merge.
The attempt composer covers only strict same-boot plus absolute
throughput/latency in this slice. Protected digest locators are not accepted
as measurement evidence here. Composition proves the measurement and evidence
paths name the same current file and rechecks that digest around capture
validation; it does not create an immutable binding. A mutation after compose
and before later capture is an inter-command window. Capture immediately, or
regenerate and compose again if the evidence file changes. The composer does
not invent a validator measurement; supply the validator `--result-json`,
including incomplete validator output. Selftests prove control-plane contracts
only and do not prove physical DGX behavior.
The separate read-only projection consumes only the tracked registry and never
this unreviewed candidate output. Serving permission is status-independent.

## Tests

Focused contracts live in
`scripts/testlib/test_model_serving_release_capture.py`,
`scripts/testlib/test_validator_measurement.py`, and
`scripts/testlib/test_model_serving_release_attempt.py`; all are wired into
`scripts/selftest.sh`. They prove control-plane measurement, composition,
capture, persistence, and verification behavior only.
The supervised `pulsar-model-onboarding` skill composes these commands and
has its own control-plane tests; those tests make no physical DGX claim and
create no release decision. Issuance of a verified candidate is a later
maintainer workflow; see
[MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md).
Onboarding capture does not produce provenance review leftovers.
