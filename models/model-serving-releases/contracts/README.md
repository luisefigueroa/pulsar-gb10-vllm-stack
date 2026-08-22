# Validation Contracts

Immutable frozen Validation Contracts. Each file is a regular non-symlink
JSON document named `<contract_id>.json` whose `kind` is
`pulsar-validation-contract` and whose `contract_id` matches the filename.

A stored contract must name an exact stored release. Runs, bundles, and
decisions may still be absent. Relative-performance predecessor IDs are
forward references and must resolve in this registry when present.

This namespace holds the reviewed Qwen3.8 contract. Do not add a
`Validated` fixture here.
