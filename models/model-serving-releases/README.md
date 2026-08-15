# Model Serving Release registry

This directory is the tracked, read-only store for ADR 0004 Model Serving
Release objects. Each namespace below is separately tracked and holds at most
one immutable JSON document per content ID:

| Namespace | Object | Filename |
|---|---|---|
| `descriptors/` | Model Serving Release descriptor | `<release_id>.json` |
| `contracts/` | Frozen Validation Contract | `<contract_id>.json` |
| `run-records/` | Immutable run record | `<run_record_id>.json` |
| `evidence-bundles/` | ADR 0004 evidence bundle | `<bundle_id>.json` |
| `decisions/` | Reviewed validation decision | `<decision_id>.json` |

This is a trust boundary, not a working cache. Policy requires every object
published here to pass repository review. The read-only verifier
(`scripts/model-serving-release-registry.sh`) loads the local checkout, checks
the filesystem layout, verifies content IDs, and assembles the object graph
through the pure schema modules. It cannot prove that repository review
occurred. It does not capture evidence, issue a decision, project catalog or
operator status, change legacy `STATUS` gates, or grant serving eligibility.

## Trust rules

- Every object file must be a regular, non-symlink file named exactly
  `<64-lowercase-hex-content-id>.json`.
- The only other allowed entry in a namespace is `README.md`.
- Unknown files, subdirectories, symlinks, and temporary leftovers fail
  closed.
- Forward references from a stored object must resolve exactly. Reverse
  lifecycle children may be absent, so a release may exist before a
  contract, a contract before runs and bundles, and a bundle before a
  decision.
- Publishable evidence-artifact descriptors are hashed against repository
  files. Protected content-addressed evidence is checked structurally only
  and is not required in Git. Neither check proves privacy review,
  repository review, or physical behavior.
- Inspection of a stored release is informational. Absence of a reviewed
  decision is not `Untested`. Multiple contract lineages or unsuperseded
  heads are ambiguous and never collapsed to one status.
- Machine-readable command output uses `schema_version: 1`; it always reports
  `serving_authorization: false` in this stage.

This store currently contains no issued release, contract, run, bundle, or
decision. Do not add a `Validated` fixture here. Legacy schema-1 seals and
combined bundles remain under `models/seals/` and
`models/validation-bundles/` and are not reused for ADR 0004 objects.

See [ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md).
