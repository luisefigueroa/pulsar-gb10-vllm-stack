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
4. Author the complete validation bundle under
   `models/validation-bundles/<validation_bundle_id>.json` from those lab
   inputs and evidence.
5. Commit the seal, bundle, and profile `EXPECTED_MODEL_SEAL` reference in the
   same pull request.
6. Run `scripts/model-library.sh validation-bundle verify <profile>` and
   `scripts/selftest.sh`, refresh the site catalog, and activate without
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
3 records expected and observed identity. Activation full-verifies every rank
and atomically creates a rank-local serve witness before publishing ready.
Launch validates the live profile/controller expectation, uses that witness
when canonical view and file metadata are unchanged, and visibly falls back to
full SHA-256 on missing/invalid/drifted metadata. Only a stable content match
refreshes the witness. The exact `snapshots/<revision>` path is then passed to
vLLM. Container labels and multi-node startup evidence carry revision, identity
status, seal ID, and validation-bundle ID.

Schema-1 validation-bundle loading is implemented. The seal's
`validation_bundle_id` must resolve to the content-addressed document under
`models/validation-bundles/`. Profile load verifies its bundle ID, exact
primary model identity, provenance/evidence parity with the seal, declared
external-artifact identities/digests, and the normalized live profile contract
including the digest-pinned image and geometry. No real profile seal or bundle ships yet;
trusted lab issuance remains a reviewed release activity and is never derived
from user state. See
[validation-bundles/README.md](../validation-bundles/README.md). The witness is
only an accelerator for identity previously established by full verification.
It is never a seal issuer.
