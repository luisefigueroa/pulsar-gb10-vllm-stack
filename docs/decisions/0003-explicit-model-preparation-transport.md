# ADR 0003: Transport policy for explicit experimental model preparation

- Status: Accepted
- Date: 2026-08-13

## Context

Pulsar's model-library path keeps one durable home for an exact reviewed
revision, presents that tree through a validated home-rank view, and creates
sealed-hot copies only on non-home serving ranks. Physical trials showed that
eight-stream SSH over a topology-confirmed RoCE endpoint materially beat the
control-network copy for the full DeepSeek checkpoint, while sixteen streams
did not improve the median. Topology-bound SSH identity, transfer integrity,
capacity admission, exact-seal verification, interruption/retry, and the
one-home lifecycle have applicable evidence.

That catalog/artifact and serving-integration evidence is enough to choose a
bounded transfer policy for an operator who explicitly opts into experimental
model preparation. It is not enough to promote `library-hot`, replace the
replicated guided default, or waive the failed DeepSeek strict-determinism gate
and remaining release work.

The implementation also has an important onboarding boundary: catalog refresh
only inventories existing durable homes, and preparation requires an eligible
primary home. Pulsar does not yet provide a general command that downloads one
exact Hugging Face revision directly to one selected durable home. The existing
`pull-weights.sh` workflow intentionally creates the replicated layout used by
the guided path.

## Decision

When an operator explicitly selects experimental multi-rank model preparation
for an eligible reviewed profile, Pulsar uses this fixed policy:

1. require confirmed topology with enrolled and verified schema-2 SSH trust;
2. require an existing exact primary durable home and reviewed expected seal;
3. use the copy backend with topology-bound `ssh-roce` transport;
4. use eight parallel copy streams for non-home sealed-hot materialization;
5. full-verify every rank against the expected seal before publishing ready;
   and
6. fail closed without changing transport, storage policy, or replica count.

The corresponding command is:

```bash
scripts/model-library.sh prepare <sealed-profile> \
  --backend copy --transport ssh-roce --copy-streams 8 --yes
```

`ssh-control` remains available for explicit diagnostics, comparison, and
maintainer-directed experiments. One-shot `nfs-rdma` transfer and long-lived
live NFS/RDMA mounts remain separate experiments. None is an automatic
fallback for this policy.

This decision fixes the policy used by the interactive experimental action. It
does not change the low-level CLI's compatibility defaults, create a durable
home, start a container, qualify a model, change profile status, or promote a
storage path. Replicated weights remain the guided serving and fresh-cluster
default until the model-library path separately earns release promotion and a
complete durable-home acquisition workflow exists.

## Rejected alternatives

- **Treat catalog refresh as model acquisition.** Refresh is read-only with
  respect to model bytes and cannot create the durable home preparation needs.
- **Replace the fresh-cluster replicated quick start now.** That would document
  a workflow the current implementation cannot complete from an empty cluster.
- **Fall back automatically to control-network SSH.** This would hide the
  selected data plane and invalidate the transfer claim.
- **Use sixteen streams by default.** Alternating full-model trials showed no
  median improvement over eight streams and added connection pressure.
- **Call all RoCE-backed paths “fabric.”** SSH/TCP over RoCE, one-shot
  NFS/RDMA, live NFS/RDMA, and NCCL inference have different dependencies and
  failure semantics.

## Consequences

- The interactive preparation preview can state one exact, evidence-backed
  transport and stream policy with no hidden picker or fallback.
- Operators must enroll/check SSH trust and establish an eligible durable home
  before preparation; failures remain actionable and closed.
- The replicated quick start stays truthful for new clusters.
- Catalog/artifact and serving-integration acceptance remain separate from the
  failed DeepSeek strict-determinism result and combined release promotion.
- Historical comparison artifacts retain their recorded status; this ADR
  governs current interpretation rather than rewriting measurements.

## Revisit triggers

Revisit this policy when a supported one-home acquisition service lands, when
new counterbalanced full-model evidence supports a different stream count or
transport, when remote-home-to-remote-target copy is supported, or when
`library-hot` completes its combined release-promotion gates.
