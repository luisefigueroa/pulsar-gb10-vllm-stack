# Validation decisions

Immutable reviewed validation decisions. Each file is a regular
non-symlink JSON document named `<decision_id>.json` whose `kind` is
`pulsar-validation-decision` and whose `decision_id` matches the
filename.

A stored decision must name an exact stored release, contract, and
evidence bundle. Supersession links are backward and must resolve to
stored prior decisions. All decisions remain; there is no mutable current
status index and no latest-timestamp winner.

Inspection may show the stored base outcome and the effective superseded
projection for that exact decision. Status is advisory and never serving
authorization.

This namespace is independently tracked and currently empty. Do not add a
real decision or `Validated` fixture here.
