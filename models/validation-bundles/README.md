# Validation bundles

Files in this directory are repository-reviewed, content-addressed validation
claims produced from Pulsar lab evidence. A sealed profile's expected-model
seal names one bundle by ID, and profile loading requires the corresponding
file at:

```text
models/validation-bundles/<validation_bundle_id>.json
```

This release contains no production bundle. Existing tested profiles therefore
remain `legacy-unsealed`; do not manufacture a bundle from a user's cache to
change that status.

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
`scripts/model_library.py` is the reference calculation. The filename must
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
   validation run.
2. Run the applicable serving, correctness, determinism, context, performance,
   and soak gates with the exact normalized profile and resolved image digest.
3. Publish sanitized repository-relative evidence.
4. Author the complete bundle, calculate `bundle_id`, and save it under the
   content-addressed filename.
5. Author the expected-model seal with the same model projection, provenance,
   evidence, and `validation_bundle_id`.
6. Add both files and `EXPECTED_MODEL_SEAL` to the profile in one reviewed
   change.
7. Run `scripts/model-library.sh validation-bundle verify <profile>` and
   `scripts/selftest.sh` before release.

Profile load fails closed when the bundle is missing, malformed, renamed,
inconsistent with its seal, or different from the live sourced profile. A
local user edit or observed cache content cannot issue or refresh a lab claim.
