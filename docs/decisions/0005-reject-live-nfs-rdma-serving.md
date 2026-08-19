# ADR 0005: Reject live NFS/RDMA as a serving runtime source

- **Status:** Accepted
- **Date:** 2026-08-19
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md),
  [ADR 0003](./0003-explicit-model-preparation-transport.md), and
  [ADR 0004](./0004-model-serving-release-validation.md)
- **Offering stop:**
  [PR #83](https://github.com/luisefigueroa/pulsar-gb10-vllm-stack/pull/83)
- **Historical evidence:** [results/weight-fabric/](../../results/weight-fabric/)
  (superseded; not promotion)

## Context

`--weight-source fabric` bind-mounts a long-lived NFSv4.2/RDMA export into
vLLM. After a rank crash, that process is gone and the next start is a cold
load. Client ranks are required not to have a local tree, so they cannot open
weights until the owner, nfsd/RPC-RDMA, the exact RoCE route, and the hard
mount are all back.

[WEIGHT_FABRIC.md](../WEIGHT_FABRIC.md) already records owner reboot as
“cannot cold-start.” Resident-in-memory survival applies only while the vLLM
process stays up. A crashed rank does not get that.

Replicated caches and `library-hot` already present local files at launch.
Live NFS serving fails rank-local restart (Requirement C in the 2026-08-08
archive) to buy a catalog property those paths already provide. That makes it
a non-starter as a runtime source for serving and onboarding.

This is not a judgment on RoCE in general. NCCL inference, topology
discovery, and topology-bound SSH-over-RoCE copy are different data planes
with different failure semantics. ADR 0003 already chose `ssh-roce` for
reviewed multi-rank prepare; vLLM still opens local files.

The 2026-08-08 archive intended to deprecate live mount if copy-then-local
won. Two-rank `library-hot` GA (2026-08-16) is that win. PR #83 stopped
offering the path. This ADR is the missing reject decision, not a silent
rewrite of measurements.

ADR 0003 still described live NFS/RDMA and one-shot `nfs-rdma` as “separate
experiments.” WEIGHT_FABRIC.md still said experimental until physical gates
pass. ADR 0004 schema v1 listed `live-remote-readonly`. Those documents are
amended here rather than rewritten in place.

## Decision

Reject live-mount as a runtime source for serving and onboarding.

| Keep | Why |
|---|---|
| NCCL/RoCE inference | Unrelated data plane |
| `detect-fabric.sh` / topology schema | Cluster membership and rails |
| ssh-roce eight-stream copy (ADR 0003) | Promoted multi-rank prepare transport; vLLM still opens local files |
| `library-hot` / `local-verified-readonly` | Product serving path |
| `replicated` | Guided default (not an ADR 0004 qualification attempt) |

| Retire | Why |
|---|---|
| `--weight-source fabric` launch | Live NFS under vLLM |
| live-mount runtime source for serving | Same |
| `live-remote-readonly` as a serving/onboarding access-contract choice | Same |
| `./pulsar weight-fabric` as a serving workflow | Same |

Launch must fail closed. `scripts/up.sh`, `cluster/start-cluster.sh`,
`cluster/preflight.sh`, `scripts/check-weights.sh`, and
`scripts/pull-weights.sh` refuse `--weight-source fabric` with an actionable
message pointing at `library-hot` or `replicated`. There is no silent remap
to replicated.

The wizard must not select this path. The onboarding skill must not offer it
(PR #83). New ADR 0004 plans must not use `live-remote-readonly`. Schema
version 1 is unchanged because the tracked registry is empty and no issued
object used that contract.

Leftover site-local exports and mounts may be removed only through
confirmation-gated, ownership-safe `unmount` / `teardown`. Publishable docs
must not invent hostnames, IPs, node IDs, or absolute cache paths.

Do not call ssh-roce “fabric” or “RoCE mount.”

## Out of scope

One-shot nfs-rdma prepare (`model-library.sh prepare --backend fabric`),
which mounts, copies, releases, then loads locally, remains a separate
experiment. ADR 0003 already chose ssh-roce for reviewed prepare. Decide
whether that candidate stays experimental or is retired in a second issue.
Shared NFS helpers must not be deleted until that decision.

This decision is not Model Serving Release qualification of any profile.

## Consequences

- Operator and agent surfaces stop presenting live NFS as a serving
  alternative. Historical WEIGHT_FABRIC measurements remain, marked
  superseded, and are not promotion evidence.
- Fail-closed launch is the control-plane enforcement of this ADR.
- `live-remote-readonly` becomes a rejection at plan/recipe validation
  without a schema bump.
- Site teardown remains available for leftover experimental state.

## Revisit triggers

Revisit only if a later design presents local files at every serving rank
before vLLM starts and still needs a long-lived NFS/RDMA runtime dependency.
Owner-reboot cold-start remains a hard blocker for the retired path.
