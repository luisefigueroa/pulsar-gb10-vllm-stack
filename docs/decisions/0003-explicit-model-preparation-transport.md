# ADR 0003: Transport policy for multi-rank model preparation

- **Status:** Accepted
- **Date:** 2026-08-13
- **Amended by:**
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0005](./0005-reject-live-nfs-rdma-serving.md), and
  [ADR 0006](./0006-model-library-only-weight-distribution.md)

## Context

The model library keeps one receipt-backed home for an exact revision. A
multi-rank service needs local files on every serving rank, so non-home ranks
receive working copies during `prepare`. The transfer path must be explicit,
topology-bound, integrity-checked, and unable to fall back silently to the
control network or a different storage policy.

## Decision

Multi-rank preparation uses this fixed policy:

1. require confirmed topology and enrolled, verified SSH trust;
2. require an exact receipt-backed occupancy home;
3. use the copy backend with topology-bound `ssh-roce` transport;
4. use eight parallel copy streams for each non-home working copy;
5. full-verify every rank against the receipt before publishing ready state;
   and
6. fail without fallback if any identity, topology, capacity, transport, copy,
   or verification condition cannot be proved.

The operator command is:

```bash
scripts/model-library.sh prepare <multi-rank-profile> \
  --backend copy --transport ssh-roce --copy-streams 8 --yes
```

A one-rank profile has no inter-rank model transfer. Its local home view is
prepared and verified on the selected rank.

`ssh-control` remains available only for explicit diagnostics and experiments.
Live NFS/RDMA serving is rejected by ADR 0005. No alternate transport is an
automatic fallback.

This policy does not acquire model bytes, start a server, qualify a model,
change profile status, or issue a Model Serving Release decision. Transport is
run provenance after all serving ranks converge on the same verified local
content.

## Consequences

- Operators see one exact multi-rank preparation path and its prerequisites.
- A missing or invalid receipt, occupancy attachment, topology, SSH identity,
  RoCE endpoint, capacity check, or rank verification stops preparation.
- Catalog/artifact evidence for transfer remains separate from serving
  integration and model qualification.
- Retained untested recipe shells require fresh onboarding and physical
  evidence; this policy alone does not qualify them.

## Rejected alternatives

- **Automatic control-network fallback.** It would hide the selected data
  plane and invalidate the transfer contract.
- **Live NFS/RDMA under vLLM.** A rank cannot cold-start independently after
  export loss; ADR 0005 rejects this runtime source.
- **Sixteen streams by default.** A default change requires fresh,
  counterbalanced evidence and a reviewed policy update.
- **Treat transport as Model Serving Release identity.** Transfer is
  preparation provenance, not one of the four release identity inputs.

## Revisit triggers

Revisit when fresh evidence supports another stream count or explicit
transport, or when remote-home-to-remote-target relay becomes a supported
product path. Any alternative remains visible and opt-in; it must not become a
fallback.
