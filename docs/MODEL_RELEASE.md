# Model Serving Release (ADR 0004)

Lab expected-identity files and the retired combined identity format are
**not** a live product
([ADR 0012](./decisions/0012-retire-expected-seal-and-schema-1-bundles.md)).
Those model-specific files are not retained in this reset. There is no v2 of
that format.

Live reviewed identity for a Model Serving Release is the ADR 0004 object
graph (descriptor, Validation Contract, run records, evidence bundle,
decision):

- [MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md)
- [MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md)
- [ADR 0004](./decisions/0004-model-serving-release-validation.md)

Accepted target: one release spec is the contract ([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md)); this section remains the live implementation until that staged cutover.

`scripts/model-release.sh` is removed. Do not assemble those retired
formats. Use `scripts/model-serving-release-plan.sh` for draft JSON that is
not in the trusted registry.
