# ADR 0013: Separate receipt control-state replica

- **Status:** Accepted
- **Date:** 2026-08-26
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0006](./0006-model-library-only-weight-distribution.md),
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md), and
  [ADR 0012](./0012-retire-expected-seal-and-schema-1-bundles.md)
- **Amends:** ADR 0011 decision 5. A recoverable last-home archive consists
  of both a verified model archive and a protected replica of the immutable
  receipt. The receipt replica is separate control state, not a file inside
  the model archive.
- **Amended by:**
  [ADR 0014](./0014-operator-owns-cold-storage-failure-domain.md) makes the
  configured cold root's failure-domain suitability an operator responsibility;
  [ADR 0016](./0016-operator-owns-cold-storage-access-control.md) makes access
  control on that configured root an operator responsibility and removes exact
  Unix-mode enforcement there.

## Context

`home add --revision` writes the immutable receipt into the controller's
private `.model-library/download-receipts/` store before publishing the home.
The background cold archive previously copied only model bytes plus
`presence.json`. Verification and restore therefore still depended on the
controller receipt surviving. Controller loss could leave intact archived
bytes that Pulsar was unable to authenticate or restore.

Putting `receipt.json` inside the model archive would collapse two trust
boundaries. Anyone able to replace archived bytes could also replace the
document that appears to authorize them. Internal consistency or a
content-derived receipt ID is integrity, not proof that the controlled Hub
acquisition wrote that receipt. The receipt store is private control state;
the model archive may have a broader storage and access lifecycle.

## Decision

1. **Keep the receipt outside the model archive.** The model archive remains
   `$PULSAR_COLD_ROOT/pulsar-receipts/<receipt_id>/home` plus
   `presence.json`. A byte-identical receipt replica is stored separately at
   `$PULSAR_COLD_ROOT/pulsar-control/download-receipts/<receipt_id>.json`.
2. **Reuse the existing receipt contract.** The replica is not a new identity
   object and cannot mint a receipt. It must use the existing closed receipt
   schema, canonical JSON encoding, filename-to-ID check, private directories
   and file permissions, stable no-follow reads, and atomic no-replace write.
3. **Publish one recovery set asynchronously.** The background archive job
   publishes the receipt replica before publishing or verifying the separate
   model archive. Failure does not block prepare, launch, or relocate. A
   completed job means both parts exist; neither becomes a vLLM runtime source.
4. **Require both parts before last occupancy removal.** Without
   `--allow-unarchived-last-home`, the controller must verify the receipt
   replica is byte-for-byte equivalent to its canonical receipt, fully rehash
   the model archive against that receipt, and accept the configured cold root
   as the operator's storage-policy assertion (ADR 0014). `home remove --yes`
   repeats the receipt and archive checks before detaching occupancy.
5. **Receipt recovery is explicit.** `home receipt recover --receipt <id>
   --yes` may restore a missing controller receipt only from the protected
   control-state namespace. It validates the existing receipt kind, canonical
   bytes, content-derived ID, filename, store shape, and permissions before an
   atomic no-replace local write. Model bytes, `presence.json`, catalog rows,
   and occupancy documents cannot authorize this operation.
6. **Restore is exact and atomic.** `home restore` may resolve an explicit
   receipt ID or exact `model_id@revision` without a live catalog. It requires
   the controller receipt and protected replica, admits the receipt's complete
   byte count, proves the repository path absent on every confirmed rank,
   copies into private same-filesystem staging, fully rehashes the staged tree,
   repeats the all-rank absence check, publishes with atomic no-replace rename,
   and only then creates new occupancy from the published directory identity.
   Catalog refresh stays explicit.
7. **Do not replicate live placement as authority.** Occupancy, catalog rows,
   job state, paths, node identity, and inode identity are not restored from
   cold control state. New occupancy is derived from the verified live
   destination after publication; the catalog is rebuilt by refresh.
8. **Fail without fallback.** If neither the canonical receipt nor its
   protected replica exists, archived model bytes are insufficient. A future
   process that re-establishes provenance from Hub metadata or another trust
   root would be a new re-attestation product and requires its own ADR.

## Current implementation

- `model_library_cold_archive.py` owns the separate control namespace,
  backup, verification, recovery, and recovery-set checks while
  `model_library_receipt.py` remains the receipt schema owner.
- `home archive run` is idempotent for an already verified model archive and
  can add the missing receipt control-state replica without replacing either
  object.
- `home receipt status|backup|recover` exposes explicit operator control.
- `home restore` accepts a receipt ID, no longer requires the catalog for
  exact identities, uses receipt-sized admission, and publishes only from
  receipt-owned private staging.
- Deterministic tests establish control-plane behavior only. No physical NFS,
  controller-loss, remote-rank restore, or DGX serving claim is created.

## Rejected alternatives

- **Store the receipt inside each model archive.** This makes recovery bytes
  appear to carry their own authority and broadens the receipt's access and
  mutation boundary.
- **Reconstruct a receipt from archived files or `presence.json`.** A
  self-observed manifest cannot prove the controlled acquisition event.
- **Automatically import a receipt during restore.** Recovery of authority is
  a separate confirmation-gated action and must remain visible.
- **Back up occupancy and catalog as authoritative state.** Their paths,
  topology, and directory identity are live observations that must be rebuilt.

## Consequences

- Cold storage uses a small additional private control namespace.
- Existing model archives need `home receipt backup` (or an idempotent
  `home archive run`) before they satisfy the new last-home safety gate.
- Last-home removal is slightly stricter, while prepare and launch remain
  independent of background archive completion.
- The operator owns whether the configured cold root is a suitable independent
  failure domain; receipt/archive verification makes no such claim.
- Recovery remains possible after controller receipt-store loss without
  allowing archived weights to bless themselves.

## Revisit triggers

Revisit if the receipt store gains an external authenticated ledger, cold
storage cannot preserve private Unix permissions, or Pulsar adopts a reviewed
re-attestation process. Do not weaken the separate authority boundary merely
to make an incomplete archive restorable.

## Interpretation note — 2026-08-28 (ADR 0016)

ADR 0016 supersedes the cold-root permission portions of decisions 2, 4, 5,
and 6. The configured storage inherits its operator-managed access policy;
Pulsar does not require or apply `0700`/`0600` there. Receipt separation,
canonical encoding, stable no-follow reads, filename-to-ID validation, exact
comparison with the controller receipt, and complete model-archive hashing
remain required. Controller-local `.model-library` receipt state remains
private.
