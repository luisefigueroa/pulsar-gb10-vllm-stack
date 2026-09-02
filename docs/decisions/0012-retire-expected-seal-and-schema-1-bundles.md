# ADR 0012: Retire expected-seal and schema-1 validation bundles as live product

- **Status:** Accepted
- **Date:** 2026-08-26
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0008](./0008-breaking-compatibility-window.md),
  [ADR 0009](./0009-no-launch-trust-mode-axis.md), and
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md)
- **Amends:** ADR 0001 decisions 4–5 and 7 as a *live serving-identity*
  product; ADR 0004 language that live loaders still consume schema-1
  seals/bundles. Does **not** change ADR 0004 object `schema_version: 1`,
  topology schema 1 bootstrap, or library-hot schema 3.

> **Superseded in part by [ADR 0017](./0017-release-spec-is-the-release-contract.md) (2026-09-02).** Decisions 1–4 stand: schema-1 expected-seals and combined bundles stay retired and are not revived as schema 2. Decision 5’s “no reviewed expected manifest” and the unknown-tree consequence that there is no expected-manifest fallback are replaced by the release spec’s file manifest. `MODEL_SERVING_RELEASE_ID` remains not a serving gate; ADR 0017 removes it as a product field at Stage 4.

## Context

Pulsar previously loaded two products for reviewed identity:

1. **Legacy expected-model seal + combined validation bundle**
   (`pulsar-expected-model-seal` / `pulsar-validation-bundle`,
   `schema_version: 1` in `scripts/model_identity.py`).
2. **ADR 0004 Model Serving Release** — five separate content-addressed
   objects under `models/model-serving-releases/`. Those objects are also
   `schema_version: 1`, of **different kinds**. They are not a v2 of the
   seal/bundle schema. There is no expected-seal schema 2.

Carrying both keeps a sealed vs unsealed catalog split, a factory
(`model-release.sh`) that cannot write the trusted directories, and a
`validation-bundle verify` CLI that fails on unsealed ADR 0004 profiles.
No future Model Serving Release will be issued as a schema-1 seal/bundle.
Profiles that depended on this product must be re-onboarded through ADR 0004.

## Decision

1. **Retire expected-seal and schema-1 validation bundles as a live
   product.** `load_conf`, catalog, wizard, prepare, launch, and home add
   must not read `EXPECTED_MODEL_SEAL` or `models/seals/` /
   `models/validation-bundles/`.
2. **Do not introduce a schema-2 of that product.** Do not auto-convert
   seals into ADR 0004 objects.
3. **Remove profiles that depended on the retired identity product.** Any
   recipe shell kept for later onboarding remains unbound, uses
   `STATUS=untested`, and carries no prior qualification claim.
4. **Do not retain the retired release history in this reset.** Delete the
   issued seal/bundle JSON and the model-specific evidence that supported it.
   Re-onboarding starts with new measurements and review artifacts.
5. **Admission remains the unsealed library-hot path.** Fail closed on
   recipe, image, geometry, topology, capacity, security, ownership,
   lifecycle, occupancy, and (for brand-new unsealed homes) source-attested
   receipt + live occupancy. `MODEL_SERVING_RELEASE_ID` stays advisory
   projection and is not a serving gate.
6. **`identity_status=match` is not a live launch class.** Remaining
   labels are `legacy-unsealed` and `unvalidated`. Leftover container
   `model-seal` / `validation-bundle` labels are untrusted observations,
   not a repair CLI (same class as leftover hot schema-1/2 after SIM-13).
7. **No guided default in this change.** Catalog and wizard still list every
   fitting serving profile (ADR 0009). A later reviewed change may set a
   default.

## Consequences

- Sealed `home add` without `--revision`, `catalog list --reviewed-identity`,
  `validation-bundle verify`, and `model-release.sh` assemble/verify-candidate
  are deleted, not aliased.
- Wizard uses one unsealed prepare/launch path. Exact replacement rollback
  that required `identity_status=match` is gone; unsealed switches already
  stop without a restore promise.
- Unknown trees without a receipt still fail closed. They no longer have a
  seal-backed “reviewed expected manifest” fallback.
- Prepared `identity_status=match` views do not match a live profile. Ordinary
  stop or purge of leftover services is site cleanup. No automatic privileged
  sweep is performed.

## Implementation note — 2026-08-26

Legacy `cold stage-only` is removed. It built a manifest from the selected
cold tree and then labeled the resulting hot state `receipt-occupancy` without
an immutable receipt or live occupancy. That contradicted decision 5 and the
unknown-tree consequence above. The public command and internal planner now
fail without fallback and name receipt-backed acquisition or recovery.
Previously created stage-only hot state is not launchable; it remains
discoverable only so `unpin` / `purge-hot --force-unpin` can remove it.
`cold scan`, `cold show`, and no-replace `cold adopt` remain non-authoritative
fill-path tools. A future cold-only serving product requires a new ADR and an
identity class that is not occupancy.

## Rejected alternatives

- **Bump seals/bundles to schema 2.** That keeps a dual identity product
  no future release will use.
- **Treat ADR 0004 `schema_version: 1` as seal schema 2.** Different
  kinds, different directories, different trust event (PR merge of
  registry objects vs seal files).
- **Make `MODEL_SERVING_RELEASE_ID` a launch permission.** Conflicts with
  ADR 0004 / 0009.
- **Carry old qualification claims onto unbound recipes.** An unbound recipe
  retained for re-onboarding starts as `STATUS=untested`.

## Revisit triggers

Re-onboard a removed or retained draft recipe as an ADR 0004 Model Serving
Release only with a new capture and reviewed publication change. Restoring a
seal-shaped live product requires a new ADR.
