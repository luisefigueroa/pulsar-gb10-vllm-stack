# Model Serving Release (ADR 0004)

Expected-model seals and schema-1 validation bundles are **not** a live
product ([ADR 0012](./decisions/0012-retire-expected-seal-and-schema-1-bundles.md)).
Archived JSON lives under `docs/archive/schema-1-expected-seal/`. There is
no schema-2 of that format.

Live reviewed identity for a Model Serving Release is the ADR 0004 object
graph:

- [MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md)
- [MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md)
- [ADR 0004](./decisions/0004-model-serving-release-validation.md)

`scripts/model-release.sh` is removed. Do not assemble seal/bundle
candidates.
