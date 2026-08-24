# ADR 0011: Portable occupancy, relocate, and receipt-indexed NFS archive

- **Status:** Accepted
- **Date:** 2026-08-23
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md),
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md), and
  [ADR 0006](./0006-model-library-only-weight-distribution.md)
- **Amends:** ADR 0001 home-loss replica/failover language; ADR 0006 accepted
  risk that durable-home loss is service loss until Hub re-acquisition; the
  source-attested “receipt is bound to one live inode and cannot move”
  interpretation in ADR 0001/0004. Does **not** reopen ADR 0001 decision 2
  (home-rank symlink, no second hot copy on the home rank) or ADR 0005
  (NFS is never a vLLM runtime source).

## Context

Source-attested `home add` writes an immutable receipt, then a current-home
attachment bound to the published directory’s device, inode, and ctime.
`home verify` and receipt-backed prepare required that live directory. Moving
the same bytes to another Spark, or removing the original tree and keeping a
complete copy elsewhere, left an orphan receipt and an unbound tree. Recovery
was Hub re-download or a reviewed expected manifest.

That is the wrong failure domain for 30 GB checkpoints. The receipt already
identifies the bytes. Occupancy should be able to travel with those bytes
after a live full rehash. Recipes share one `model@revision`; they do not each
need a durable home. Extra complete Spark trees are not homes.

ADR 0001 already required home-loss resilience on a **distinct failure
domain**, not a second Spark replica. The cold root (`PULSAR_COLD_ROOT`,
legacy alias `MODELS_NFS`) is that domain when it stores a receipt-indexed
archive: site NFS, an external disk, or another mount that is not occupancy
NVMe. vLLM never opens it (ADR 0005).

## Decision

1. **Receipt and occupancy are separate.** The receipt is immutable
   control-plane identity (manifest, source, Hub-download provenance). The
   current-home attachment is occupancy: `node_id`, durable path, and
   directory identity. One occupancy per `model_id@revision`.
2. **`selected_rank` is provenance only.** It records where Hub download
   happened. It is not a relocate or occupy predicate.
3. **Occupancy is granted only after a live full SHA-256** of that exact
   destination tree against the receipt. Silent reconstruct-from-bytes remains
   forbidden. `home relocate --node` is the supported command: occupy-in-place
   when the destination already holds matching bytes; copy from current
   occupancy with topology-bound `ssh-roce` when it does not. No Hub
   re-download.
4. **Catalog classes.** Occupancy (attachment matches the live tree) is the
   durable home. Extra complete hub trees are **unbound-complete**, not homes,
   and do not freeze resolve when occupancy exists. Working replicas (today’s
   sealed-hot full copies) are not homes. Receipt-indexed cold archives are not
   homes. Two occupancy documents for one revision remain store corruption and
   fail closed. Sealed/legacy trees with no source-attested receipt keep the
   complete-tree primary-selection rule.
5. **A receipt-indexed cold archive is the durable replica**, required after
   receipt issuance, on a distinct failure domain (different device from
   occupancy). NFS is one example, not the type. It starts immediately after
   occupancy attach as a **background** job and must not block prepare,
   launch, or experiments. Last occupancy remove fails closed without a
   content-verified archive unless `--allow-unarchived-last-home`. Restore is
   copy from the verified archive, live rehash, then occupy. vLLM never opens
   the cold root (ADR 0005).
6. **Home-rank runtime view stays a symlink** of the occupancy tree
   (ADR 0001 decision 2). Operator-facing name for non-home full copies is
   **working replica**. On-disk hot schema 3 and `sealed-hot` are unchanged.
7. **Home-loss recovery** is occupy-in-place of an unbound-complete tree, or
   restore from a verified cold archive, or Hub `home add` only when no
   receipt and no archive exist.

## Current implementation

- Occupancy match no longer uses download rank. `home relocate` occupy-after-rehash
  and catalog unbound-complete classification are implemented.
- Receipt-indexed `home archive status|run`, background enqueue after
  `home add`, `home restore`, and `--allow-unarchived-last-home` are
  implemented as catalog/artifact control plane. Archive workers take **no
  occupancy lifecycle lock** (job-file flock only) so they cannot block
  prepare, launch, or relocate. Last occupancy remove of a receipted identity
  rehashes the cold archive on the controller before occupancy detach
  (`home check`, and again after `--yes`). Rank-local execute only deletes
  the inspected hub tree. Distinct-device proof is same-host (rank 0) only.
  Unbound-complete trees are not last-home alternates. Physical cold archive
  and restore on DGX hardware are not claimed. Legacy `cold scan` /
  `cold adopt` / `cold stage-only` remain fill paths without receipt identity.

## Consequences

- A complete tree without occupancy is not a serving home.
- Relocate does not refresh the catalog, prepare working replicas, or launch.
- Physical relocate, new-inode restore, and receipt-indexed cold archive
  remain catalog/artifact evidence to capture; they are not claimed by this
  ADR’s acceptance.

## Rejected alternatives

- Second Spark durable replica as the failover policy (capacity; not a distinct
  storage failure domain).
- Live NFS under vLLM as restore or serving (ADR 0005).
- Hub re-download as the only occupancy-transfer path.
- Granting occupancy from matching bytes without a live rehash.
- Making archive-complete a prepare/launch gate.

## Revisit triggers

Archive job reliability, NFS layout, or a need for two concurrent occupancy
homes. Any of those requires a new ADR. Convenience of the current inode
attachment is not sufficient to revert portable occupancy.
