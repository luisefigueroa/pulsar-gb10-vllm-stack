# Expected model seals

Files in this directory are repository-reviewed trust roots for model-library
activation. A profile opts in with:

```bash
EXPECTED_MODEL_SEAL="seals/<profile>.json"
```

The reference is relative to `models/` and must resolve inside this directory.
Only `STATUS=tested*` profiles may reference a seal. This release contains no
real profile seals yet, so existing tested profiles remain `legacy-unsealed`.

## Issuance rule

A seal is issued from the exact artifact used by the Pulsar lab validation
run. It must not be generated from arbitrary bytes later observed in a user
cache. The reviewed Git change, lab provenance, and repository-relative
evidence are the trust boundary; a local user edit does not create an official
Pulsar validation claim.

Schema version 1 has these exact fields:

| Field | Contract |
|---|---|
| `schema_version` | `1` |
| `kind` | `pulsar-expected-model-seal` |
| `profile` | Exact profile filename without `.conf` |
| `model_id` | Exact Hugging Face `namespace/repository` ID |
| `revision_kind` | `huggingface-commit` |
| `snapshot_revision` | Immutable 40-64 character lowercase hexadecimal commit |
| `manifest` | `scheme=sha256-snapshot-manifest-v1` and the complete snapshot `manifest_id` |
| `provenance` | 64-character `validation_bundle_id`, non-empty issuer, RFC3339 UTC issuance time, and non-empty repository-relative evidence paths |
| `seal_id` | SHA-256 of canonical JSON for every other field |

Every evidence path must exist in the same checkout. The canonical JSON used
for `seal_id` sorts object keys, uses UTF-8, and uses separators `,` and `:`
without extra whitespace. The implementation helper
`expected_model_seal_id()` in `scripts/model_library.py` is the reference
calculation.

## Lab release workflow

1. Resolve and retain the immutable upstream commit before validation.
2. Build the complete SHA-256 snapshot manifest from the exact lab bytes.
3. Run all required model, image, profile, geometry, correctness, context, and
   soak gates and publish sanitized repository-relative evidence.
4. Produce the reviewed validation-bundle ID in the lab pipeline.
5. Commit the seal and add its `EXPECTED_MODEL_SEAL` reference to the tested
   profile in the same pull request.
6. Run `scripts/selftest.sh`, refresh the site catalog, and activate without
   `--allow-unvalidated`. Activation must full-hash the source and report
   `identity_status=match` before it can publish ready hot state.

A configured model, commit, or manifest mismatch cannot be bypassed with
`--allow-unvalidated`. Changing a seal creates a distinct hot identity; prior
hot state is not silently relabeled.

## Current implementation boundary

Catalog schema 2 validates the reviewed seal and selects only its exact
revision. Catalog refresh discovers complete snapshot commit directories
without depending on `refs/main`, so a direct commit-pinned download remains
usable and later upstream ref movement cannot retarget the profile. Hot schema
3 records expected and observed identity, and launch full-verifies the hot
manifest before passing the exact `snapshots/<revision>` path to vLLM.
Container labels and multi-node startup evidence carry revision, identity
status, seal ID, and validation-bundle ID.

The fast metadata witness is not implemented yet, so launch still performs a
full SHA-256 verification. A standalone machine-readable validation-bundle
document and lab issuance automation are also pending; the current seal carries
the lab-provided bundle ID without reconstructing that bundle from user state.
