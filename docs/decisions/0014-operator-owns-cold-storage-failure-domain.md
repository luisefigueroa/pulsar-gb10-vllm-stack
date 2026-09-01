# ADR 0014: Operator owns cold-storage failure-domain suitability

- **Status:** Accepted
- **Date:** 2026-08-26
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md), and
  [ADR 0013](./0013-separate-receipt-control-replica.md)
- **Amends:** ADR 0011 decision 5 and ADR 0013 decision 4. The operator, not
  Pulsar, decides whether the configured cold root is a suitable independent
  failure domain.
- **Amended by:**
  [ADR 0015](./0015-explicit-cold-recovery-root.md) removes the live
  `MODELS_NFS` alias and implicit `/mnt/Models` default from cold-recovery
  configuration. Setting explicit `PULSAR_COLD_ROOT` remains the operator's
  failure-domain assertion;
  [ADR 0016](./0016-operator-owns-cold-storage-access-control.md) also makes
  access control on the configured cold root the operator's responsibility.

## Context

ADR 0011 requires a cold recovery copy for last-home removal and describes it
as storage on another failure domain. The first implementation attempted to
enforce part of that policy by comparing `st_dev` when the home was on rank 0.
It skipped the comparison for remote homes because device numbers are local to
each mount namespace and cannot prove storage independence across hosts.

Filesystem device numbers, mount paths, filesystem types, NFS export names,
and topology observations are not reliable general proofs that two paths fail
independently. Adding a storage-domain identity product would also duplicate
site infrastructure knowledge that belongs to the operator. The operator has
the deployment context needed to decide whether NFS, an external disk, or
another configured path meets the site's durability requirements.

Pulsar still owns the integrity and lifecycle safety of the recovery set. That
is a different question from whether its physical storage is independent.

## Decision

1. **The operator owns failure-domain suitability.** Setting
   `PULSAR_COLD_ROOT` (or its legacy `MODELS_NFS` alias) asserts that the
   selected location meets the operator's recovery and failure-domain policy.
2. **Pulsar does not prove or disprove that assertion.** It must not compare
   device numbers, mount identities, filesystem types, export identities,
   storage-domain IDs, or topology observations to accept or reject the cold
   root as an independent failure domain.
3. **Pulsar retains operational path-safety checks.** The configured root must
   be usable, writable where mutation requires it, and must not be nested with
   the occupancy tree in a way that can recurse during copying or couple
   archive deletion to home deletion. Those checks prevent direct software
   corruption; they do not establish storage independence.
4. **Pulsar retains recovery-set verification.** Last occupancy removal still
   requires the canonical receipt, its protected replica, and a full rehash of
   the separate model archive unless the operator passes
   `--allow-unarchived-last-home`. These checks prove content and recovery
   mechanics, not physical durability.
5. **The unarchived override keeps its narrow meaning.** It acknowledges that
   the required receipt replica or model archive is missing, unreadable, or
   invalid. It is not required because two usable paths share a device.
6. **Claims remain bounded.** Deterministic and physical archive/restore tests
   may prove functional copying, verification, atomic publication, and
   recovery. They must not claim that Pulsar certified an independent failure
   domain. Any such infrastructure claim belongs to the operator.

## Current implementation

- The `st_dev` comparison and `PULSAR_COLD_ALLOW_SAME_DEVICE` lab override are
  removed.
- Same-device recovery sets are accepted when the operator configured the cold
  root and the receipt replica plus model archive verify.
- A cold root below a local occupancy tree remains refused because archive copy
  would recurse. Remote path strings are not compared across host namespaces.
  This is copy safety, not failure-domain proof.
- Last-home planning and execution continue to verify the exact recovery set
  before occupancy detachment.

## Rejected alternatives

- **Cross-host device comparison.** Device numbers are namespace-local and do
  not identify a shared physical failure domain.
- **A Pulsar storage-domain registry.** It duplicates site infrastructure
  authority and would still depend on operator assertions about backing
  storage.
- **Filesystem or NFS-type allowlists.** Storage type does not prove physical
  independence, replication, backup policy, or recoverability.
- **Removing recovery-set verification.** Operator ownership of placement does
  not weaken Pulsar's responsibility to verify the receipt and archived bytes.

## Consequences

- AUD-04 is retired as a product defect; cold-storage independence is an
  explicit operator responsibility.
- Pulsar accepts a verified recovery set on the same observed device without a
  special environment override.
- Operator documentation must state the assertion clearly before last-home
  removal guidance.
- Catalog/artifact evidence can prove recovery mechanics but cannot certify the
  operator's storage architecture.

## Revisit triggers

Revisit only if operators request an optional infrastructure-audit product with
a separately defined authority source. Do not reintroduce implicit storage-
domain inference into ordinary model-library admission or removal.

## Interpretation note — 2026-08-28 (ADR 0016)

The same operator-ownership boundary now covers access control. Pulsar accepts
the configured cold root's inherited ownership, modes, and ACLs. It continues
to verify operational usability, path safety, canonical receipt identity,
exact receipt equality, and archived model content.
