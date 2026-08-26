# Archived expected-seal and schema-1 validation bundles

Historical `pulsar-expected-model-seal` and `pulsar-validation-bundle`
JSON (schema_version 1). **Not a live product.** Loaders must not consume
these files. There is no schema-2 of this format.

[ADR 0012](../../decisions/0012-retire-expected-seal-and-schema-1-bundles.md)
retired the live path on 2026-08-26. The former catalog profiles
`qwen3-1.7b` and `deepseek-v4-flash` were dropped rather than converted.
Re-onboard onto ADR 0004 only as a later explicit change.

Live reviewed Model Serving Releases live under
`models/model-serving-releases/` and are a different kind.

Gate measurements cited by these archives remain under `results/`.
