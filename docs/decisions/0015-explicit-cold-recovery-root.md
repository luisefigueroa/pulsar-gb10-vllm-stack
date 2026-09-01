# ADR 0015: Explicit-only cold recovery root

- **Status:** Accepted
- **Date:** 2026-08-28
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md),
  [ADR 0013](./0013-separate-receipt-control-replica.md), and
  [ADR 0014](./0014-operator-owns-cold-storage-failure-domain.md)
- **Amends:** ADR 0011 cold-root alias/default language and ADR 0014
  decision 1 live alias language. Live recovery configuration is
  explicit `PULSAR_COLD_ROOT` only. This does not reopen ADR 0011 archive
  layout, ADR 0013 separate receipt replicas, or ADR 0014 operator
  ownership of failure-domain suitability.
- **Amended by:**
  [ADR 0016](./0016-operator-owns-cold-storage-access-control.md) assigns the
  explicit root's access-control policy to the operator.

## Context

ADR 0011 requires a receipt-indexed cold archive at an operator-configured
root. ADR 0014 makes failure-domain suitability an operator assertion when
that root is set. The first implementation treated `MODELS_NFS` as a live
alias and defaulted unset cold recovery to `/mnt/Models`.

That made an unchosen conventional mount into recovery configuration. An
operator who had never set `PULSAR_COLD_ROOT` could still enqueue archives,
and existing non-Pulsar content under `/mnt/Models` could be treated as
the recovery namespace. Changing or disabling that implicit root was not an
explicit product action.

The intended later operator choice may still be the existing `/mnt/Models`
directory. That choice must be written as `PULSAR_COLD_ROOT`, not inferred.

## Decision

1. **Live configuration is explicit `PULSAR_COLD_ROOT` only.** Precedence:
   process-level `PULSAR_COLD_ROOT`, including explicit empty; then the
   persisted repository `.env` assignment, including explicit empty; then
   absent, which means `not-configured`.
2. **There is no live `MODELS_NFS` alias and no implicit `/mnt/Models`
   fallback.** Unset is not a default path. Empty is explicit disable.
3. **Operators configure through `./pulsar configure cold-storage`.** Direct
   commands and the workflow menu write only the preferred `.env` key.
   Operators do not hand-edit `.env` for this product path. The selected
   directory must already exist. Pulsar never creates, mounts, or
   administers it.
4. **Existing non-Pulsar bytes stay untouched.** Configuring a path does
   not migrate, delete, rewrite, or bless unrelated content. Receipt-backed
   jobs own only `$PULSAR_COLD_ROOT/pulsar-control` and
   `$PULSAR_COLD_ROOT/pulsar-receipts`.
5. **Root changes do not migrate; disable does not delete.** Conservatively
   block a switch or disable that could strand or split known Pulsar
   recovery state. Job documents currently omit a root, so any job
   document blocks root change or disable. Shallow recovery objects under
   the current explicit root also block. A controller receipt alone is not
   an archive/recovery set.
6. **Failure-domain suitability remains the operator's assertion
   (ADR 0014).** Pulsar verifies path safety and recovery-set integrity.
   It does not infer NFS or independence from device, mount, filesystem,
   export, or storage-domain identity.
7. **Legacy fill commands stay explicit.** `cold scan`, `cold show`,
   `cold adopt`, and cold-assisted resolve require `--cold-root PATH` on that
   exact invocation. They must not infer a live recovery root from
   `PULSAR_COLD_ROOT`, `MODELS_NFS`, or `/mnt/Models`.
8. **Cold recovery is not a container mount.** Launch plans carry only the
   verified local model view. They do not carry `MODELS_NFS` or bind-mount
   `/mnt/Models`; configuring recovery does not create a serving dependency.

## Current implementation

- `scripts/model_library_cold_storage.py` owns dotenv parse/write, state,
  plans, archive-job projection, and closed JSON.
- `scripts/configure-cold-storage.sh` is the thin CLI/menu boundary.
- `./pulsar configure cold-storage` and the home Configuration menu
  delegate to that CLI. First-use on `./pulsar` offers configure, disable,
  or not now only when no persisted choice exists.
- Live `configured_cold_root()` reads `PULSAR_COLD_ROOT` only.
- Launch-plan and rank-container schemas contain no `models_nfs` field or
  `/mnt/Models` mount.
- Deterministic tests make no physical NFS, DGX, archive durability,
  serving, qualification, or promotion claim.

## Rejected alternatives

- Keep `MODELS_NFS` as a live alias while adding a configure command.
- Adopt `/mnt/Models` automatically when the directory exists.
- Create or migrate into a Pulsar-owned subtree without an explicit set.
- Force, delete, or rewrite existing recovery objects to unblock a
  root change.

## Consequences

- Receipt acquisition records cold archive unavailable until the operator
  configures a path.
- Sites that relied on the implicit `/mnt/Models` default must set
  `PULSAR_COLD_ROOT` explicitly.
- Historical ADR text that named the alias remains history and is amended
  here rather than rewritten.

## Revisit triggers

Revisit if job documents gain a recorded root that can support a safe
move, or if operators need a separate product to inventory non-Pulsar
trees under a configured path. Do not restore an implicit live default.
