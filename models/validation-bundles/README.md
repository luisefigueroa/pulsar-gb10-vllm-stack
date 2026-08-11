# Validation bundles

Files in this directory are repository-reviewed, content-addressed validation
claims produced from Pulsar lab evidence. A sealed profile's expected-model
seal names one bundle by ID, and profile loading requires the corresponding
file at:

```text
models/validation-bundles/<validation_bundle_id>.json
```

This release contains one reviewed bundle for the diagnostic `qwen3-1.7b`
profile:

```text
9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380.json
```

Every other tested profile, including `qwen3-1.7b-2node`, remains
`legacy-unsealed`. Do not manufacture a bundle from a user's cache to change
that status. The issued bundle is enforced by `library-hot`; replicated and
live-mount launch paths are not yet content-bound by it.

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
