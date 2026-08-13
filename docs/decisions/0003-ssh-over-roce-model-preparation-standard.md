# ADR 0003: SSH-over-RoCE is the standard model preparation transport

- Status: Accepted
- Date: 2026-08-12

## Context

Pulsar previously documented replicated Hugging Face caches as the promoted
default and treated model-library preparation over SSH-over-RoCE as a promotion
candidate. Physical evidence now covers topology-bound SSH identity, an
eight-stream full-model transfer, exact verification, durable-home/sealed-hot
placement, interruption and retry, read-only launch, warmup, completion smoke,
owned cleanup, and restoration of the one-home steady state.

The team has standardized model copying on SSH over the confirmed RoCE data
plane. Continuing to present full durable replication as the architectural
default makes onboarding guidance disagree with that decision and encourages
unnecessary durable copies.

## Decision

The standard preparation path for a multi-node Hugging Face model is:

1. retain one durable home for the exact reviewed revision;
2. use the home rank through its validated rank-local view;
3. copy sealed-hot content only to non-home serving ranks using
   topology-bound `ssh-roce` transport;
4. use eight parallel copy streams for the validated large-model path unless
   newer evidence establishes a different profile-specific value; and
5. full-verify every rank against the reviewed expected seal before publishing
   ready state or launching.

There is no automatic transport fallback. Failure of RoCE route proof, SSH
identity, transfer, or verification fails preparation closed. `ssh-control`
is a diagnostic and comparison transport, not a silent fallback.

Replicated durable caches remain an explicit compatibility and rollback mode.
They are not required for normal model onboarding. One-shot NFS/RDMA transfer
and long-lived live NFS/RDMA mounts remain separate experiments and are not
prerequisites for onboarding or qualification of the standard copy path.

This decision standardizes distribution mechanics; it does not grant model
qualification. Accuracy, determinism, performance, long-context, soak, and
release claims remain bound to the exact model, image, configuration, source,
and serving geometry under ADR 0002.

## Implementation status

The model-library CLI implements this path through `prepare` with
`--backend copy --transport ssh-roce --copy-streams 8` and launch through
`--weight-mode library-hot`.

The wizard and some ordinary launch/staging surfaces still select replicated
caches. That is an implementation gap, not the architecture or onboarding
policy. Documentation must identify the gap until a separately tested runtime
change aligns those surfaces; it must not imply that an automatic migration or
fallback already exists.

## Consequences

- New-model onboarding validates the standard SSH-over-RoCE preparation path.
- Replicated and live-fabric evidence remains valid within its recorded scope,
  but neither is a mandatory comparison gate for standard onboarding.
- Historical artifacts and earlier no-promotion decisions remain unchanged and
  are interpreted as evidence recorded before this decision.
- Wizard/default-path promotion still requires the corresponding control-plane
  implementation and regression evidence.
