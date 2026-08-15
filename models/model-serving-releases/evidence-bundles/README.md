# Evidence bundles

Immutable ADR 0004 evidence bundles. Each file is a regular non-symlink
JSON document named `<bundle_id>.json` whose `kind` is
`pulsar-model-serving-validation-bundle` and whose `bundle_id` matches
the filename.

A stored bundle must name an exact stored release, contract, and run-record
set. Publishable repository-relative artifacts are stream-hashed against
the checkout. Protected content-addressed artifacts are not required in
Git. Hash agreement does not prove privacy review or physical behavior.

A bundle may exist before any reviewed decision. This namespace is
independently tracked and currently empty. Do not add a real bundle or
`Validated` fixture here.
