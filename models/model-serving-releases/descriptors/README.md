# Release descriptors

Immutable ADR 0004 Model Serving Release descriptors. Each file is a
regular non-symlink JSON document named `<release_id>.json` whose `kind`
is `pulsar-model-serving-release` and whose `release_id` matches the
filename.

This namespace is independently tracked. An empty store is valid: a
descriptor may exist here before any contract, run, bundle, or decision.
Do not place a real or `Validated` fixture in this directory until a
reviewed issuance change lands.

Inspect with `scripts/model-serving-release-registry.sh show-release`.
Verification does not project catalog status or launch a release. Status is
advisory and never serving authorization.
