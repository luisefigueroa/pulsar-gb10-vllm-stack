# ADR 0016: Operator owns cold-storage access control

- **Status:** Accepted
- **Date:** 2026-08-28
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md),
  [ADR 0013](./0013-separate-receipt-control-replica.md),
  [ADR 0014](./0014-operator-owns-cold-storage-failure-domain.md), and
  [ADR 0015](./0015-explicit-cold-recovery-root.md)
- **Amends:** ADR 0013 decisions 2, 4, 5, and 6 only where they require
  Pulsar-managed private Unix permissions in the configured cold root. Receipt
  separation, canonical identity, exact comparison, and archive verification
  remain unchanged.

## Context

The first receipt-replica implementation required `0700` directories and
`0600` files under `PULSAR_COLD_ROOT`. That made Pulsar responsible for an
access-control policy on operator-selected storage. Some NFS appliances apply
their own ownership, mode, or ACL inheritance and can report a usable,
read/write recovery set as group-accessible even after a client requested
owner-only modes.

ADR 0014 already assigns failure-domain suitability to the operator. Access
control has the same site-specific authority: Pulsar cannot know which local
users, groups, NAS identities, ACLs, or storage administrators the operator
intends to authorize. Exact Unix modes are therefore not recovery integrity.

Pulsar still needs to detect malformed, substituted, incomplete, or changed
recovery objects. Removing permission enforcement does not remove those
content and path checks.

## Decision

1. **The operator owns cold-storage access control.** Configuring
   `PULSAR_COLD_ROOT` asserts that its inherited ownership, mode, ACL, export,
   and administrator policy are acceptable for the recovery objects stored
   there.
2. **Pulsar accepts inherited access permissions.** Cold recovery creation may
   request conservative creation modes and ordinary copies may preserve source
   metadata, but Pulsar does not impose exact access modes or reject the
   resulting directories or files because of their access bits. Receipt
   control-state publication does not rewrite existing cold permissions.
3. **Pulsar keeps structural and content verification.** The cold control
   namespace must still contain regular directories and regular receipt files,
   reject symlinks and unexpected store entries, use stable no-follow reads,
   validate canonical JSON and filename-to-receipt identity, compare the cold
   replica exactly with the controller receipt when both exist, and fully hash
   the separate model archive before destructive last-home operations or
   restore.
4. **Controller-local state stays private.** This change applies only below
   the configured cold root. Repository `.env`, `.model-library` receipts,
   occupancy, catalogs, locks, staging, and witnesses keep their existing
   private-mode requirements.
5. **Operational access still fails without fallback.** A missing,
   unreadable, unwritable when mutation is required, malformed, unstable, or
   content-mismatched object remains an error. Pulsar does not bypass those
   checks or derive a receipt from archived model bytes.
6. **Existing recovery objects need no permission rewrite.** If their shape,
   canonical receipt, exact receipt comparison, and model archive hashes pass,
   inherited access permissions do not make the recovery set incomplete.
7. **Claims remain bounded.** Pulsar verifies recovery mechanics and content.
   It does not claim to verify who can access or modify the configured storage.

## Current implementation

- Cold receipt publication and loading accept inherited directory and file
  modes while retaining no-follow/type, canonical encoding, filename identity,
  exact receipt comparison, and archive hashing.
- Controller-local receipt publication continues to enforce `0700` stores and
  `0600` files.
- Deterministic tests cover inherited group-accessible modes without making a
  physical NFS, access-control, archive-durability, or DGX claim.

## Rejected alternatives

- **Keep exact `0700`/`0600` admission.** This enforces a site access policy
  that is outside Pulsar's responsibility and excludes usable storage that
  applies inherited permissions.
- **Add an allow-permissions override.** Access control is always operator
  owned; a second mode would create two recovery contracts and a silent policy
  axis.
- **Remove receipt or archive integrity checks.** Access-policy ownership does
  not make malformed or mismatched recovery content usable.
- **Store the receipt inside the model archive.** Receipt separation remains
  necessary for explicit recovery identity and lifecycle behavior.

## Consequences

- NFS and other cold roots may use their native inherited ownership, modes,
  and ACLs.
- Operators are responsible for every access-control consequence of the
  configured storage.
- Permission bits are no longer evidence that a cold recovery set is complete
  or incomplete.
- Recovery integrity still depends on the canonical receipt and complete model
  archive verification.

## Revisit triggers

Revisit if Pulsar adopts signed receipts, an authenticated external receipt
ledger, or an explicit access-audit product. Do not reintroduce ordinary Unix
permission enforcement into recovery integrity checks without a new decision.
