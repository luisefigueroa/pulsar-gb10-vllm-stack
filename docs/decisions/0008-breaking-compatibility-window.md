# ADR 0008: One announced breaking-compatibility window

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decides:** SIM-07 (breaking-compatibility plan)
- **Related:**
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md),
  [ADR 0006](./0006-model-library-only-weight-distribution.md),
  [ADR 0007](./0007-ordinary-stop-retains-unpinned-hot-views.md),
  [ADR 0009](./0009-no-launch-trust-mode-axis.md)

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
existing `scripts/*.sh` entrypoints remain the low-level CLIs.
[ADR 0009](./0009-no-launch-trust-mode-axis.md) closed the SWI-728
public-contract parking lot without hiding those CLIs.

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
| `scripts/weight-fabric.sh show\|unmount\|teardown` | Removed (SIM-12) | Lab confirmed leftover configs gone; helper deleted. |
| Hot schema-1/2 legacy repair | Removed (SIM-13) | Lab confirmed no leftover schema-1/2 hot instances; public `hot legacy check\|remove` refuses. Health still observes leftover schema-1/2 as untrusted. |
| `./pulsar` plus current `scripts/` / `cluster/` / `serve.sh` | Retain | ADR 0009: low-level CLIs stay. This ADR does not hide them. |

Site leftover NFS exports/mounts, if any still exist after SIM-12, are
site-admin cleanup, not a Pulsar command. Leftover schema-1/2 hot files after
SIM-13, if any, are the same: site-admin cleanup, not a Pulsar command. No
automatic privileged sweep.

## Consequences

- One migration guide, one error vocabulary, then deletion.
- Implementation is a later issue; this ADR is the plan only.
- SIM-09 may drop tests whose only job was the removed aliases.

## Revisit triggers

Revisit if leftover fabric/hot-repair state cannot be cleared in one window,
or when discovery no longer needs topology schema 1 as enroll input. Hiding
low-level CLIs is a later public-contract issue, not this window.

## Implementation (SIM-11, 2026-08-22)

The window is executed for the public aliases classified **Remove in window**:

- `--force` on `up.sh`, `serve.sh`, and `cluster/start-cluster.sh` parses then
  exits 2. Status labels never block serving.
- `--allow-unvalidated` on the model-library CLI and Python planners parses
  then exits 2. Seals still fail closed.
- `list-models.sh --validated` parses then exits 2; use `--legacy-tested`.
- `model-library.sh catalog list --validated` and Python `--validated` parse
  then exit 2; use `--reviewed-identity`.
- Public `activate` parses then exits 2; use `prepare`. Internal planner
  command `plan-activate` and schema term `activate` remain.

N≥2 `check-image.sh` JSON emits `rank-unreachable` / `rank-docker-error` /
`missing-on-rank` (or `missing-both`). Pair-only `worker-*` names are no
longer emitted. N=1 `head-*` / `target-*` / `missing-on-head` stay because
`up.sh` remediations differ (`missing-on-head` still `sync-image --pull`;
`missing-on-rank` does not). `up.sh` and the wizard still accept the old
pair-only names if they appear.

Not in this slice: `--force-unpin`, inventory keys `head`/`worker`/`rank-N`,
`worker_available_gib`, leftover `weight-fabric.sh show|unmount|teardown`
(removed later, SIM-12), hot schema-1/2 repair (removed later, SIM-13),
topology schema 1 as `detect-fabric` output, DSpark. Root Compose was
removed later by ADR 0010 / SWI-730.

`HEAD_IP`/`WORKER_IP` remain refuse-only: they never confirm membership
(AUD-01). There is no membership parser to delete.

## Implementation (SIM-13, 2026-08-22)

Lab confirmation: cached `health --json` on the confirmed topology reported
`hot_instances: []`. Empty leftover group directories under the default hot
root were removed with `rmdir`. No schema-1/2 `hot.json` remained. Durable
homes were not deleted.

Public `scripts/model-library.sh hot legacy check|remove` parses then exits 2.
Health still classifies leftover schema-1/2 as `metadata_status=legacy` and
attention; it does not advertise a repair command. Internal Python repair
planners are deleted. `--force-unpin` on `purge-hot` is unchanged. Topology
schema 1 remains enroll bootstrap only. The retired model-specific repair
artifact is not retained in this reset.
