# Release descriptors

Immutable ADR 0004 Model Serving Release descriptors. Each file is a
regular non-symlink JSON document named `<release_id>.json` whose `kind`
is `pulsar-model-serving-release` and whose `release_id` matches the
filename.

This namespace is empty. An empty store remains valid for later staging.
Do not add a `Validated` fixture here.

Inspect with `scripts/model-serving-release-registry.sh show-release`.
Verification does not project catalog status or launch a release. Status is
advisory and never serving authorization.
