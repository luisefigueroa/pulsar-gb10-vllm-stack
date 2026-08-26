# Run records

Immutable ADR 0004 attempt and run records. Each file is a regular
non-symlink JSON document named `<run_record_id>.json` whose `kind` is
`pulsar-validation-run-record` and whose `run_record_id` matches the
filename.

A stored run must name an exact stored release and contract. Full run
validation needs the evidence-artifact descriptors carried by a stored
bundle that includes the run. Orphan run files without a covering bundle
fail without fallback.

This namespace holds the reviewed Qwen3.8 run records. Do not add a
`Validated` fixture here.
