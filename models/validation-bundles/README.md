# Validation bundles

Files in this directory are repository-reviewed, content-addressed validation
claims produced from Pulsar lab evidence. A sealed profile's expected-model
seal names one bundle by ID, and profile loading requires the corresponding
file at:

```text
models/validation-bundles/<validation_bundle_id>.json
```

These are **schema-1 legacy combined artifacts**. Their IDs hash the release
inputs, evidence paths, and issuance metadata together. Under
[ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md), a
separate release descriptor now owns the stable Model Serving Release ID and a
separate frozen Validation Contract owns its criteria. Those pure schemas are
not consumed by this legacy directory. Pure immutable run-record, new ADR 0004
validation-bundle, reviewed-decision, status-derivation,
and supersession schemas are now implemented separately in
`scripts/model_validation_evidence.py`; they are not persisted or consumed by
this directory either. A schema-1 `bundle_id` is not a Model Serving Release ID.
Existing files remain immutable and are not automatically converted or
relabeled `Validated`.

This release contains reviewed bundles for the diagnostic `qwen3-1.7b`
profile and the flagship `deepseek-v4-flash` profile:

```text
9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380.json
8fda1d93c5e08cbba18df5b26b0632354c6559ab939d3763dbdbdf38ead6b236.json
```

Profiles without a reviewed bundle, including `qwen3-1.7b-2node`, remain
`legacy-unsealed`. Do not manufacture a bundle from a user's cache to change
that status. Issued bundles are enforced by `library-hot` and sealed
replicated caches; live-mount launch remains unbound.

## Identity and binding

Schema version 1 has these exact top-level fields:

| Field | Contract |
|---|---|
| `schema_version` | `1` |
| `kind` | `pulsar-validation-bundle` |
| `profile` | Exact profile filename without `.conf` |
| `models` | One or more exact model identities, with exactly one `primary` role |
| `external_artifacts` | Behavior-affecting artifacts not contained in a listed model snapshot |
| `profile_contract` | Normalized live serving, image, memory, and geometry contract |
| `evidence` | Non-empty repository-relative evidence paths |
| `provenance` | Non-empty issuer and RFC3339 UTC issuance time |
| `bundle_id` | SHA-256 of canonical JSON for every other field |

The canonical JSON sorts object keys, uses UTF-8, and uses separators `,` and
`:` without extra whitespace. `validation_bundle_id()` in
`scripts/model_identity.py` is the reference calculation. The filename must
equal `bundle_id`.

Each `models` entry contains:

```json
{
  "role": "primary",
  "model_id": "namespace/repository",
  "revision_kind": "huggingface-commit",
  "snapshot_revision": "<40-64 lowercase hexadecimal commit>",
  "manifest": {
    "scheme": "sha256-snapshot-manifest-v1",
    "manifest_id": "<64 lowercase hexadecimal characters>"
  }
}
```

The primary entry must exactly match the expected-model seal. Other model
entries may describe a separately resolved draft model or other complete model
snapshot.

An `external_artifacts` entry has `role`, `artifact_id`, `revision`, and a
digest object with `scheme=sha256` and a 64-character `value`. Allowed roles
are `tokenizer`, `draft-model`, `adapter`, `model-code`, and `other`. The list
may be empty only when every behavior-affecting artifact is already covered by
the listed snapshot manifests.

The expected-model seal points to the bundle ID. The bundle deliberately does
not include the seal ID: that one-way reference avoids a hash cycle while the
bundle's primary-model projection, provenance, and evidence must still equal
the seal.

This combined binding is the current implementation, not the target object
boundary. ADR 0004 release-descriptor and frozen-contract schema version 1 are
implemented separately in `scripts/model_serving_release.py`; run, bundle, and
decision schema version 1 is implemented in
`scripts/model_validation_evidence.py`. No trusted artifact here references
those objects yet. Read-only persistence and verification of ADR 0004 objects
is implemented separately under `models/model-serving-releases/` and is
currently empty. Local evidence-capture candidate persistence is implemented
and unreviewed. Issuance/publication and catalog/operator projection remain
pending. Multiple immutable attempts, evidence sets, and
reviewed decisions can already refer to one unchanged four-part release ID
without rewriting these legacy files.

The ADR 0004 objects remain schema version 1 after the current correction
because none was issued or persisted. This directory's schema-1 files are a
different legacy schema and remain byte-for-byte unchanged. Future decisions
consider every applicable observation automatically, allow only explicit
evidence-backed exclusions, and apply deterministic conflict adjudication.
Every post-barrier non-preparation run declares a nonempty set of attempted
criteria and supplies exactly one complete or inconclusive observation for
each; incomplete attempts use inconclusive observations.
Criterion scopes are canonical; `catalog-artifact` preparation evidence cannot
satisfy a validation criterion. Relative performance binds a reviewed
predecessor contract, bundle, decision, and run whose relevant criterion
passed, without requiring the predecessor release to be globally `Validated`.
Structural runtime and architecture/geometry checks do not replace physical
DGX evidence. Closed command descriptors and recursive value screening reduce
privacy risk but do not replace trusted capture or publication privacy review.

## Normalized profile contract

`profile_contract` binds the live sourced profile values that can affect the
validated claim:

- exact model and served name;
- digest-pinned image reference and extracted `sha256` digest;
- port, GPU memory utilization, engine arguments, container environment,
  speculative-decoding arguments, and recommendation flag;
- node count, tensor and pipeline parallel sizes, topology class, and minimum
  rails per pair;
- profile purpose and declared memory budgets.

Lists preserve order because argument order can affect runtime behavior.
Decimal memory/utilization values use canonical strings. A sealed profile must
use an image reference ending in `@sha256:<digest>`; mutable image tags cannot
establish the validated runtime identity.

## Lab release workflow

1. Resolve every model and external artifact to immutable identity before the
   validation run, pin the image digest, and inspect the normalized contract
   with `scripts/model-release.sh plan <profile> --json`.
2. Build the complete snapshot manifest with `scripts/model-release.sh
   manifest <profile> --hub-path <path> --revision <commit>` using the exact
   retained lab bytes.
3. Run the applicable serving, correctness, determinism, context, performance,
   and soak gates with the exact normalized profile and resolved image digest.
4. Publish sanitized repository-relative evidence.
5. Run `scripts/model-release.sh assemble <profile> ...` and then
   `scripts/model-release.sh verify-candidate <profile> --candidate-dir <dir>`.
   The generated bundle/seal are deterministic **unreviewed candidates**, not
   trusted files.
6. Review provenance, evidence privacy, exact inputs, and candidate
   reproducibility. In one deliberate pull request, place the reviewed bundle
   under its content-addressed filename, place the reviewed seal under the
   profile name, and add `EXPECTED_MODEL_SEAL` to the profile.
7. Run `scripts/model-library.sh validation-bundle verify <profile>` and
   `scripts/selftest.sh` before release.

Profile load fails closed when the bundle is missing, malformed, renamed,
inconsistent with its seal, or different from the live sourced profile. A
local user edit or observed cache content cannot issue or refresh a lab claim.
See [docs/MODEL_RELEASE.md](../../docs/MODEL_RELEASE.md) for exact candidate
commands and the current manual review/publication boundary.
