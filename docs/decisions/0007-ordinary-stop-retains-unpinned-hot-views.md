# ADR 0007: Ordinary stop retains unpinned prepared views

- **Status:** Accepted
- **Date:** 2026-08-20
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md),
  [ADR 0003](./0003-explicit-model-preparation-transport.md),
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md), and
  [ADR 0006](./0006-model-library-only-weight-distribution.md)
- **Amends:** the ordinary-stop hot-retention default in
  [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md) §4.1–4.4 and §4.8.
  Does not reopen ADR 0006 (the model library remains the only
  weight-distribution mechanism).

## Context

ADR 0006 made every new service `library-hot`. The earlier stop default was
written when `library-hot` was an explicit opt-in: after a successful stop,
`scripts/down.sh` selected `purge-default` and called
`model-library.sh purge-hot … --yes` unless the operator passed
`--pin-weights`. That deleted unpinned sealed-hot copies from non-home ranks.

For any large multi-rank model, deleting the non-home working copy makes the
next start repeat transfer and full verification before vLLM load.

The library still has no automatic eviction. The live choice was therefore
“keep only by explicit pin” or “delete on every ordinary stop.” Pin is
protection from later unforced purge, not the only way to leave a verified
working copy on disk. Interactive home stop confirmed only that the service
should stop, then inherited the silent purge.

Requirement A (one durable home) still holds. Requirement B (time-to-healthy /
last locally verified secondary copy) was being spent by the default stop.

## Decision

1. **Separate service lifecycle from cache retention.** Stopping proven
   stack-managed containers does not imply deleting prepared views.
2. **Ordinary stop retains unpinned prepared views.** The just-stopped
   service’s sealed-hot copies stay on disk with retention `ephemeral`. The
   next same-profile start may reuse a matching ready witness without restage.
   The durable home remains required. This is not home-loss resilience.
3. **Retain is not pin.** `--pin-weights` still marks the instance `pinned`
   so a later unforced `purge-hot` refuses. Default retain does not auto-pin.
4. **`--purge-hot` is the explicit capacity-recovery action.** It may remove a
   pin (`--force-unpin` on the `down.sh` hook). Interactive home stop offers
   retain vs free and states the restage consequence when a byte count can be
   proven. Non-interactive CLI does not prompt; flags and site policy decide.
5. **No automatic eviction.** Ordinary stop of service S retains S’s views. It
   does not delete other unpinned hot instances. If a later prepare cannot
   admit, fail closed and report reclaimable state (`budget`). Last-N or
   budget-based eviction is future work.
6. **Site policy** `PULSAR_HOT_STOP_POLICY=retain|purge` selects the named-
   profile ordinary-stop default. Unset means `retain`. Invalid or empty-but-
   set values fail closed. `--retain-weights`, `--pin-weights`, and
   `--purge-hot` override the site policy for that invocation.
   `down.sh --all` never auto-purges.
7. **Wizard replacement is unchanged.** Temporary pin before stop; successful
   different-profile replacement still unpins and purges the previous view.

## Consequences

After ordinary stop, accounting is one durable home plus N−1 unpinned
sealed-hot working copies. Explicit `--purge-hot` returns idle storage to
one durable home. Home-rank symlinks still contribute no owned hot model
bytes. Hot purge still must never follow the home symlink.

Operators who want the previous storage-first default set
`PULSAR_HOT_STOP_POLICY=purge` or pass `--purge-hot`.

## Qualification

Catalog/artifact retention and serving-integration restart. Existing 2026-08-16
restart-with-views-present evidence remains applicable. This decision does not
change a Model Serving Release tuple or require model qualification.

## Interpretation note — 2026-08-22 (SIM-06)

Wizard model switching keeps the current library-hot contract. Exact
rollback is only for a running `library-hot` service whose identity is a
reviewed `match`. Unsealed or unvalidated library-hot switches use a
guarded stop with no restore promise. Leftover pre-library replacement
records are archived, not rolled back. SIM-06 rejects requiring every
switch to be an explicit stop then start, and rejects “rollback only for
replicated” (that source was removed by ADR 0006). Ownership-safe stop,
confirmation, identity checks, and no automatic restart loop remain
mandatory. SIM-04 may later unify the engine without changing this
operator contract.

## Revisit triggers

Revisit with a new ADR if a site needs automatic last-N or budget-based
eviction, or if retained unpinned copies prove operationally unbounded.
