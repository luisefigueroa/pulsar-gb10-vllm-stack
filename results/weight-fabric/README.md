# Historical live NFS/RDMA serving evidence

> **Superseded / not promoted.**
> [ADR 0005](../../docs/decisions/0005-reject-live-nfs-rdma-serving.md)
> rejects live NFS/RDMA under vLLM as a serving runtime source. These
> 2026-08-07/08 artifacts remain for history. Do not delete them. Do not
> rewrite PASS/FAIL rows in place. Do not promote from them.

Serving path today: the model library (`library-hot` /
`local-verified-readonly`) — the only weight-distribution mechanism per
[ADR 0006](../../docs/decisions/0006-model-library-only-weight-distribution.md).
The replicated path and the one-shot `nfs-rdma` prepare experiment were
retired by the same decision; their history here stays unmodified.
