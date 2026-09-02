# ADR 0001: Durable-home runtime view and validated model identity

- **Status:** Accepted
- **Date:** 2026-08-10
- **Scope:** Federated model library, library-hot preparation, pin/restart, and
  model validation identity
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Amended by:**
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0007](./0007-ordinary-stop-retains-unpinned-hot-views.md)
  (ordinary stop retains unpinned sealed-hot working copies; pin remains
  protection from unforced purge),
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md)
  (occupancy is portable after a live receipt rehash; NFS receipt-indexed
  archive is the recovery copy; home-rank symlink is unchanged),
  [ADR 0012](./0012-retire-expected-seal-and-schema-1-bundles.md)
  (expected-seal and schema-1 validation bundles are not a live product;
  they are not replaced by a schema-2 of that format),
  and [ADR 0014](./0014-operator-owns-cold-storage-failure-domain.md)
  (the operator owns whether the configured cold root is a suitable
  independent failure domain)

> **Superseded in part by [ADR 0017](./0017-release-spec-is-the-release-contract.md) (2026-09-02).** Decisions 4–5 and 7 no longer bind live expected identity to schema-1 seals; the rest of this ADR stands.

## Context

Pulsar is intended to use aggregate Spark storage as a federated catalog. A
model therefore has one durable home by default rather than a durable replica
on every serving node. Non-home ranks still need local bytes for the promoted
transfer-then-load design, but writing a second copy on the home rank consumes
capacity and startup time without adding a distinct node or disk failure
domain. In an exact multi-node serving geometry, loss of the home node also
removes a compute rank.

The zero-copy home-rank symlink avoids that redundant write, but a stable hot
pathname can hide a changed target, revision, or blob. Separately, the current
catalog can label any locally observed revision of a tested repository ID as
validated. A self-consistent local manifest proves transfer integrity, not that
the bytes are the ones used for Pulsar's lab validation claim.

## Decision

1. The default durable policy is one managed home per exact model revision.
2. The home rank launches through a validated symlink or equivalent rank-local
   view of that durable tree. Routine home-rank hot materialization is
   prohibited. Only non-home ranks receive sealed-hot copies.
3. Warm-home pinning retains non-home hot copies but continues to depend on the
   durable home. Home-loss resilience requires an explicit durable replica on
   another failure domain and a separately designed failover policy.
4. Validated identity originates in the lab. The expected seal binds model ID,
   exact commit/revision, complete snapshot manifest ID, and provenance. A
   locally computed observed seal may match it but cannot replace or create it.
5. In the current schema, the validation bundle additionally binds
   behavior-affecting external model artifacts, normalized profile/runtime
   configuration, resolved container image digest, serving geometry/topology
   class, and evidence references.
6. Full SHA-256 verification establishes trust at lab sealing, adoption or
   download, each non-home materialization, and after detected drift. A fast
   serve-time witness may be used only after full verification and must bind
   the canonical symlink target, local filesystem identity, exact revision,
   logical file set, and per-file device/inode/size/mtime/ctime metadata.
7. Launch resolves the exact validated snapshot rather than mutable `main`.
   Witness drift causes visible full verification against the expected seal or
   fails closed. It never auto-reseals the changed content as validated.
8. Serving receives a read-only view where practical. Hot purge never follows
   the home symlink, and an active reference prevents durable-home removal.
   Home removal remains a separate confirmed lifecycle operation.

Hosting is not identity. A future Hugging Face mirror may distribute the sealed
bytes, but locally observed or mirrored content cannot create the reviewed
authority.

## Rejected alternatives

- **Materialize the home rank into hot:** duplicates a full model on the same
  node, delays readiness, works against federated capacity, and does not add a
  distinct failure domain.
- **Move the durable model into hot:** makes authoritative content subject to a
  purge-oriented lifecycle. Same-filesystem rename remains acceptable only for
  explicit adoption into a managed durable root after seal validation.
- **Treat mutable `main` as identity:** permits different bytes to inherit a
  historical `STATUS=tested` claim.
- **Use latest mtime or a locally issued manifest as validation:** directory
  mtimes do not cover descendant changes, metadata can be preserved, and local
  observation cannot establish the lab trust root.

## Consequences

- Active N-rank warm-home serving uses one durable copy plus N−1 hot working
  copies. After unpinned cleanup, only the durable home remains.
- Warm restart can avoid cold storage, transfer, and catalog refresh while the
  durable home exists; it is not independent of durable-home loss.
- The home dependency must be visible in inventory, labels, runbooks, and
  failure messages until an explicit durable-replica policy exists.
- The current local snapshot seal remains useful for transfer integrity, but
  promotion also requires expected-seal binding and serve-time witness checks.
- Historical evidence that listed home-rank materialization as a blocker is not
  rewritten; current ledgers and indexes mark this ADR as superseding that
  architectural recommendation.

## Revisit triggers

Reconsider this decision only if Pulsar adds supported cross-rank compute
failover, adopts an explicit multi-home durable-replica policy, or gains
filesystem-backed immutability such as fs-verity that materially changes the
identity and lifecycle tradeoffs. Any revision requires a new ADR and physical
evidence; current implementation convenience is not sufficient.

## Interpretation note — 2026-08-14

ADR 0004 keeps this expected-versus-observed model-content trust boundary and
supersedes only the combined release-object interpretation. A separate release
descriptor owns the stable Model Serving Release ID; the Validation Contract,
run records, validation bundle, and reviewed validation decision are separate.
Their pure schema contracts are now implemented. Read-only trusted
persistence and verification of those objects is implemented and currently
empty. Local evidence-capture candidate persistence is implemented and
unreviewed. Advisory catalog/operator projection is implemented for profiles
explicitly bound to a release ID; no current profile is bound. Maintainer
issuance staging can propose registry objects, but a local command is not
trusted until repository review and merge. Serving permission is status-independent; these objects still govern
identity verification and evidence claims. Existing schema-1 bundles and seals
remain immutable legacy artifacts.

## Interpretation note — 2026-08-17

Source-attested acquisition may atomically adopt a verified exact upstream
tree into the managed durable root. The accepted sequence is private
same-filesystem staging, a complete upstream inventory and set check, complete
SHA-256 of every file, an all-rank absence recheck, and atomic publication.
That adoption creates observed and source identity only. It does not create
the lab trust root, a reviewed seal, provenance/security approval, validation
status, serving permission, or a Model Serving Release decision.

This interpretation does not weaken decision 4. Locally observed or
source-attested content may match a later reviewed identity, but it cannot
replace or create that identity. A catalog label or a manifest generated from
the current tree is still not a lab trust root.

### Implementation note — 2026-08-17

The public source-attested control plane now implements that sequence for an
absent brand-new unsealed Hugging Face home. `home add --revision ... --plan`
resolves an exact upstream commit and complete Git/LFS inventory without
downloading model bytes. Separately confirmed execution uses the selected
rank's local authentication and private same-filesystem staging, verifies the
complete upstream set and every SHA-256, repeats the all-rank absence check,
writes an immutable site-local receipt, publishes with an atomic no-replace
rename, and binds that receipt to the exact published directory through a
private current-home attachment. `home verify` performs the required later
offline full rehash only when that attachment still matches the live home.
Deterministic tests do not prove physical Hub or DGX behavior and none of the
trust exclusions above changed.

## Interpretation note — 2026-08-22 (SIM-03)

Source-attested unsealed Hugging Face `home add` remains a core
catalog/artifact ingress. SIM-03 does not weaken decision 4: a receipt
and live-directory attachment are observed/source identity only. They
cannot replace or create a lab-issued expected seal. Unknown trees without
a receipt still require a reviewed expected manifest independent of the
observed tree. A complete tree with a compatible receipt is occupied with
`home relocate` after a live rehash
([ADR 0011](./0011-portable-occupancy-and-cold-archive.md)); it is not a
Hub re-download and not occupancy without that rehash.

## Interpretation note — 2026-08-23 (ADR 0011)

Decision 2 is unchanged: the occupancy rank uses a validated symlink of
the durable tree; do not materialize a second hot copy on that rank.
Decision 3’s distinct-failure-domain replica is the receipt-indexed NFS
archive in ADR 0011, not a second Spark home. Occupancy may move with
the receipt after a live full rehash. Non-home full copies are working
replicas (on-disk `sealed-hot` unchanged).

## Interpretation note — 2026-08-26 (ADR 0012)

Decisions 4–5 and 7 no longer describe a live expected-seal or schema-1
validation-bundle product. Those model-specific files are not retained and are
not loaded by `load_conf`, catalog, wizard, prepare, or launch. There is
no schema-2 of that format. ADR 0004 Model Serving Release objects are a
different kind and remain `schema_version: 1` of *that* schema.

Live serving identity is the unsealed library-hot path: occupancy, rank-local
verified views, and source-attested receipts for brand-new unsealed homes.
A locally observed or source-attested tree still cannot create an ADR 0004
release. `MODEL_SERVING_RELEASE_ID` remains advisory. Unknown trees without
a receipt still fail closed; they no longer have a seal-backed reviewed
expected-manifest fallback.

Any retained draft recipe is unbound and untested. Re-onboarding requires new
ADR 0004 capture and reviewed publication.

## Interpretation note — 2026-08-26 (ADR 0014)

Decision 3 still requires an explicit durable recovery copy rather than a
second Spark home. The operator owns whether the configured cold root is a
suitable independent failure domain. Pulsar verifies path safety, the protected
receipt replica, model-archive content, and restore mechanics; it does not
infer storage independence from devices, mounts, filesystems, exports, or
topology.
