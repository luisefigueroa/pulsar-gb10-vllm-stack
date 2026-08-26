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
occurred. Its verified inspection result supplies read-only catalog/operator
status projection for profiles explicitly bound by `MODEL_SERVING_RELEASE_ID`.
It does not capture evidence, issue a decision, change recommendation policy,
authorize serving, or launch a release. Validation status is advisory.

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
- A profile binding is a reviewed assertion about the exact release tuple.
  Projection also requires the selected runtime model-access contract to match
  the stored serving recipe. Missing bindings are neutral and legacy `STATUS`
  remains a separate recommendation label.
- Machine-readable command output uses `schema_version: 1` and reports only
  inspection results; it carries no serving-permission field.

The store is empty. An empty registry is valid. No current profile sets
`MODEL_SERVING_RELEASE_ID`. Local ADR 0004
evidence-capture candidate persistence writes only under gitignored
`experiments/model-serving-release-captures/` (or an explicit safe external
directory) and must never write this registry. The maintainer issuance
workflow can stage an untrusted proposal into these namespaces; repository
review and merge remain the trust event. That workflow is documented in
[MODEL_SERVING_RELEASE_ISSUANCE.md](../../docs/MODEL_SERVING_RELEASE_ISSUANCE.md)
and each proposed lineage remains untrusted until repository review and merge.
Legacy schema-1 seals and combined bundles are archived under
`docs/archive/schema-1-expected-seal/` and are not loaded (ADR 0012). They are
not reused for ADR 0004 objects.

See [ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md).
