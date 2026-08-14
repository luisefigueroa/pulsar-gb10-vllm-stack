# Expected model seals

Files in this directory are repository-reviewed trust roots for model-library
preparation. A profile opts in with:

```bash
EXPECTED_MODEL_SEAL="seals/<profile>.json"
```

The reference is relative to `models/` and must resolve inside this directory.
Only `STATUS=tested*` profiles may reference a seal. The one-node diagnostic
`qwen3-1.7b` profile carries the first reviewed lab-issued seal. The flagship
`deepseek-v4-flash` profile carries the second. Profiles without a reviewed
seal, including `qwen3-1.7b-2node`, remain `legacy-unsealed`.

An expected-model seal establishes reviewed **model-content identity**. It does
not by itself identify or validate the complete Model Serving Release defined
by [ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md),
which also includes the serving recipe, runtime/image identity, and supported
hardware geometry. Existing seals and their schema-1 bundle links remain
immutable during the release/status migration and are not automatically
relabeled `Validated`.

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
`expected_model_seal_id()` in `scripts/model_identity.py` is the reference
calculation.

## Lab release workflow

1. Resolve and retain the immutable upstream commit before validation, pin the
   image digest, and inspect the profile with
   `scripts/model-release.sh plan <profile> --json`.
2. Build the complete SHA-256 snapshot manifest from the exact lab bytes with
   `scripts/model-release.sh manifest <profile> --hub-path <path> --revision
   <commit>`.
3. Run all required model, image, profile, geometry, correctness, context, and
   soak gates and publish sanitized repository-relative evidence.
4. Assemble and verify the explicitly unreviewed documents with
   `scripts/model-release.sh assemble` and `verify-candidate`. Candidate output
   has no authority and cannot write this directory.
5. Review exact lab provenance, evidence privacy, profile identity, and
   reproducibility. Then deliberately place the reviewed bundle and seal in
   their trusted paths and commit them with the profile
   `EXPECTED_MODEL_SEAL` reference in one pull request.
6. Run `scripts/model-library.sh validation-bundle verify <profile>` and
   `scripts/selftest.sh`, refresh the site catalog, and prepare without
   `--allow-unvalidated`. Preparation must full-hash the source and report
   `identity_status=match` before it can publish ready hot state.

A configured model, commit, or manifest mismatch cannot be bypassed with
`--allow-unvalidated`. Under current schema-1 enforcement, changing any seal
creates a distinct hot identity; prior hot state is not silently relabeled.
That legacy hot identity is not the Model Serving Release identity. A seal
change that alters only review, provenance, evidence, or issuance metadata
retains the same Model Serving Release when its four-part tuple is unchanged;
it requires new schema-1 IDs and cross-link verification, plus refresh or
repreparation of affected hot state, but does not by itself require complete
model requalification. A content-changing seal update creates a new release.

## Current implementation boundary

Catalog schema 2 validates the reviewed seal and selects only its exact
revision. Catalog refresh discovers complete snapshot commit directories
without depending on `refs/main`, so a direct commit-pinned download remains
usable and later upstream ref movement cannot retarget the profile. Hot schema
3 records expected and observed identity. Preparation full-verifies every rank
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
including the digest-pinned image and geometry. The issued `qwen3-1.7b` seal is
`ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94`
and references validation bundle
`9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380`.
The issued `deepseek-v4-flash` seal is
`1ba9ca8e3c34a9143588cc1315474e9cca0724351f0856caed5bb1116b89555a`
and references validation bundle
`8fda1d93c5e08cbba18df5b26b0632354c6559ab939d3763dbdbdf38ead6b236`.
Trusted lab issuance remains a reviewed release activity and is never derived
from user state. Expected-seal enforcement applies to experimental
`library-hot` and sealed replicated caches; live-mount launches remain
unbound. See
[validation-bundles/README.md](../validation-bundles/README.md) and
[docs/MODEL_RELEASE.md](../../docs/MODEL_RELEASE.md). The witness is
only an accelerator for identity previously established by full verification.
It is never a seal issuer.

ADR 0004 adds a separate pure release descriptor and frozen Validation Contract
alongside this content trust root. Pure immutable run-record, new
validation-bundle, reviewed-decision, independent status-derivation, and
supersession schemas are also implemented separately. None is issued,
persisted, or referenced by the current seal path; trusted publication,
status/serving projection, and migration remain pending. Candidate tooling,
schema builders, and local content verification may demonstrate internal
consistency or a content match, but a locally constructed decision cannot
establish reviewer authority or assign a trusted Model Serving Release status.
