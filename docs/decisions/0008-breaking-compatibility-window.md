# ADR 0008: One announced breaking-compatibility window

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decides:** SIM-07 (breaking-compatibility plan)
- **Related:**
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md),
  [ADR 0006](./0006-model-library-only-weight-distribution.md),
  [ADR 0007](./0007-ordinary-stop-retains-unpinned-hot-views.md)

## Context

Pulsar still carries deprecated no-op flags, aliases, and leftover-state
helpers after the library-only cut (ADR 0006). Keeping them indefinitely
preserves test and docs branching. Removing them without an announced
window surprises operators who still type old flags.

AUD-01 already refused `HEAD_IP` / `WORKER_IP` as topology membership.
`--weight-source` / `--weight-mode` already fail closed. Other aliases
still no-op.

## Decision

Announce **one** breaking compatibility window, then delete the deprecated
public aliases listed below. During the window, each removed flag keeps a
stable error that names the replacement. Historical evidence is not
deleted. `./pulsar` is the documented supported operator interface; the
existing `scripts/*.sh` entrypoints remain the low-level CLIs until the
SWI-728 public-contract work says otherwise.

### Classification

| Path | Class | Notes |
|---|---|---|
| `--weight-source` / `--weight-mode` | Already removed | Fail closed (ADR 0006). No further window. |
| `HEAD_IP` / `WORKER_IP` as membership | Already refused | Must not construct topology (AUD-01). Drop leftover parser/docs in the window. |
| `bench-activate` | Already removed | No remaining CLI. Do not plan migration work for it. |
| Topology schema 1 | Retain as bootstrap only | `detect-fabric` still writes schema 1; `topology-ssh-trust.sh enroll` upgrades it to schema 2. Serving already requires schema 2. Do not delete the schema-1 loader until discovery writes schema 2 (or enroll no longer needs schema 1 as input). Schema 1 is not a launch format. |
| `--force` | Remove in window | Status-advisory no-op on `up.sh`. |
| `--allow-unvalidated` | Remove in window | Deprecated no-op; seals still fail closed. |
| `list-models.sh --validated` | Remove in window | Deprecated alias for `--legacy-tested` (historical `STATUS=tested*`). |
| `model-library.sh --validated` | Remove in window | Deprecated alias for `--reviewed-identity`. |
| `activate` | Remove in window | Public alias for `prepare`. |
| Leftover pair-only lifecycle helpers | Remove in window | Inventory during implementation; replace with N-rank paths. |
| `scripts/weight-fabric.sh show\|unmount\|teardown` | Retain until leftover state gone | ADR 0006 teardown window. Delete in this breaking release only after the lab confirms no `.weight-fabric/` configs. |
| Hot schema-1/2 legacy repair | Retain for one window, then remove | Migration-only; refuse once no site-local schema-1/2 hot state remains. |
| `./pulsar` plus current `scripts/` / `cluster/` / `serve.sh` | Retain | Public contract is SWI-728. This ADR does not hide low-level CLIs yet. |

Site leftover NFS exports/mounts stay confirmation-gated teardown, not a
compat alias. No automatic privileged sweep.

## Consequences

- One migration guide, one error vocabulary, then deletion.
- Implementation is a later issue; this ADR is the plan only.
- SIM-09 may drop tests whose only job was the removed aliases.

## Revisit triggers

Revisit if SWI-728 redefines the supported public surface, if leftover
fabric/hot-repair state cannot be cleared in one window, or when discovery
no longer needs topology schema 1 as enroll input.
