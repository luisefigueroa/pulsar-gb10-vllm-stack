# Retired weight-fabric paths

Live NFS/RDMA serving is rejected by
[ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md). The replicated
cache path and weight-fabric distribution commands are removed by
[ADR 0006](./decisions/0006-model-library-only-weight-distribution.md).

Do not use this document as an operator runbook. Current serving uses the model
library: one receipt-backed home, local files on every serving rank, and
topology-bound SSH-over-RoCE preparation for multi-rank profiles.

Current commands and recovery behavior are in
[`OPERATIONS.md`](./OPERATIONS.md). The canonical storage and preparation
architecture is in [`MODEL_LIBRARY_DESIGN.md`](./MODEL_LIBRARY_DESIGN.md).

Model-specific evidence and commands from the retired paths are not retained
after the repository reset. A future alternative serving or distribution path
requires a new ADR, new operator surface, and new evidence.
