# ADR 0004 evidence-capture candidate persistence

This is the maintainer runbook for the local, candidate-only ADR 0004
evidence-capture workflow. It validates supplied Model Serving Release and
Validation Contract objects, captures immutable run records plus
content-addressed evidence, assembles compatible run records into one
immutable evidence bundle, and independently verifies the resulting
candidate.

The workflow makes capture repeatable. It is not an issuing or promotion
authority. A successful candidate is explicitly unreviewed, has privacy
review pending, changes no catalog or profile status, launches nothing, and
never writes the tracked release registry.

This is **ADR 0004 evidence-capture candidate persistence**. It is not
catalog/operator status projection (ADR 0004 numbered item 3). It does not add validator-output adapters and
makes no physical DGX claim.
The separate source-neutral release planner documented in
[MODEL_RELEASE.md](./MODEL_RELEASE.md) produces the release and contract values
that a later adapter or supervised workflow can place into a capture spec. The
current capture CLI does not ingest a planner directory directly; its spec
still omits derived IDs as documented below. Planning does not make those
objects reviewed and does not weaken this workflow's independent validation.

## System boundary

| Subsystem | Responsibility |
|---|---|
| Capture spec (this document) | Closed operator input: complete unreviewed release and contract objects, attempt fields, provenance/environment, command descriptors without program versions, criterion observations, and evidence-source metadata |
| ADR 0004 schema (`scripts/model_serving_release.py`) | Owns release-descriptor and frozen Validation Contract schema version 1 |
| Release-plan candidates (`scripts/model-serving-release-plan.sh`) | Unreviewed upstream release/contract values; direct planner-to-capture adaptation is not implemented |
| ADR 0004 evidence schema (`scripts/model_validation_evidence.py`) | Owns evidence-artifact, run-record, evidence-bundle, and reviewed-decision schema version 1 |
| Capture persistence (`scripts/model-serving-release-capture.sh`) | Plans, captures, assembles, and verifies unreviewed candidates under a gitignored output boundary |
| Tracked registry (`scripts/model-serving-release-registry.sh`) | Read-only verification of reviewed objects under `models/model-serving-releases/`; this tool never writes it |
| Legacy validators (`validate/*`) | Heterogeneous measurement programs; exit zero or a selftest is not a criterion pass |

The Bash entrypoint is the operator boundary. Python owns spec loading,
derivation, filesystem-safe publication, assembly, verification, and both
human and JSON rendering. The command is not routed through `./pulsar`, the
wizard, or profile/catalog status projection, and it launches nothing.

## Commands

```text
scripts/model-serving-release-capture.sh plan --spec SPEC [--json]
scripts/model-serving-release-capture.sh capture-run --spec SPEC
    [--output-dir DIR] [--json]
scripts/model-serving-release-capture.sh assemble-bundle
    --candidate-dir DIR [--candidate-dir DIR ...]
    [--output-dir DIR] [--json]
scripts/model-serving-release-capture.sh verify-candidate
    --candidate-dir DIR [--json]
```

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

## Capture spec

The spec kind is `pulsar-model-serving-release-capture-spec`, schema
version 1. Closed top-level fields are:

| Field | Role |
|---|---|
| `release` | Complete four-part release object without `release_id` |
| `contract` | `repository_invariants` and `release_criteria` without `contract_id` or `release_id` |
| `attempt` | Attempt identity, phase, scope, attempted criteria, timestamps, and completion |
| `preparation_provenance` | Origin, transfer, subsystems, runtime sources, verification status only, barrier, elapsed time |
| `observed_environment` | Image digest, opaque boot/launch IDs, cluster shape, and per-rank observations; geometry ID is derived |
| `commands` | Allowlisted program, typed arguments, classified environment, and `repository-root`; no program version |
| `criterion_observations` | Measurements that reference evidence by `source_key`, not by precomputed artifact IDs. Nested context and soak sources are part of the run artifact set. |
| `evidence_sources` | Publishable `results/` files or protected digest locators |
| `review_source_keys` | Explicit, sorted source keys reserved for review evidence. Every source must be used by the run (including nested context/soak) or listed here. Review sources must use `release-promotion` scope. |

The loader rejects duplicate JSON keys, invalid UTF-8, `NaN`/`Infinity`,
unknown fields, precomputed derived IDs, precomputed program versions,
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

Self-contained unreviewed release and contract objects are allowed. If
an object with the same derived ID exists in the tracked registry, the
canonical object must be equal; otherwise capture fails closed. Missing
registry objects are allowed. An unreadable or unsafe registry path
fails closed.

## Derived identity

Capture derives, and does not invent or review:

- release ID and contract ID
- phase/scope consistency
- Model Artifact Set ID
- current checked-out allowlisted program SHA-256 versions
- evidence file SHA-256 digests and evidence-artifact IDs
- run-record ID, bundle ID, coverage, and qualification-started state
- candidate ID

Every generated evidence artifact has privacy review `pending`.

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
requires directory mode `0700` and file mode `0600`. These checks are
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
status, reviewer, or protected source path.

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
`release-promotion` scope. Those checks do not change the broader
schema enum and are not a reviewed decision.

Publishable artifact locations must still be sanitized `results/` files
and must not use any `raw` path. Protected artifacts must keep the
exact `sha256:<digest>` locator. Program or evidence drift fails
closed. There is no override. An output directory may not be an
existing candidate or any existing ancestor that contains
`candidate.json`.

## What this unit does not do

- Issue a reviewed validation decision
- Write `models/model-serving-releases/`
- Change `STATUS`, a profile's release binding, recommendation/default policy,
  or runtime state
- Adapt `validate/*` output into a trusted producer contract
- Claim physical DGX, model-download, container, or remote behavior
- Route through `./pulsar` or the wizard

Trusted privacy review and decision issuance/publication remain later units.
The separate read-only projection consumes only the tracked registry and never
this unreviewed candidate output. Serving permission is status-independent.

## Tests

Focused contracts live in
`scripts/testlib/test_model_serving_release_capture.py` and are wired
into `scripts/selftest.sh`. They prove control-plane capture,
persistence, and verification behavior only.
