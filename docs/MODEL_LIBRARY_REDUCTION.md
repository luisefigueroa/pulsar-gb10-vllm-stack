# Model library reduction

Accepted 2026-09-02 as the target shape of the model library once the
release spec ([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md))
carries the exact file manifest. This document is the keep, cut, and defer
decision that scopes ADR 0017 Stages 3 and 4 for the library. It is target
architecture. The live library is still the one described in
[MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) and
[OPERATIONS.md](./OPERATIONS.md) until those stages land, and nothing here
changes a command today.

## Why the library shrinks

The library grew its receipt, occupancy, duplicate-primary, relocation, and
health machinery because identity used to come from observed trees: the
catalog had to reason about what it had found, which copy was authoritative,
and how to rebind identity to a moved tree. With a spec-first design a tree
is either verified against the spec's manifest or it is not, and most of that
reasoning has nothing left to decide. The rule applied below: keep what
moves or protects bytes, cut what reasons about ambiguous identity, defer
what recovers from losses the spec already covers.

## Keep, cut, defer

| Capability | Decision | Reason |
|---|---|---|
| Exact-commit acquisition into one durable home per identity | keep | Reduced to: download by commit, hash the tree, compare to the spec manifest. The plan, approval, and receipt objects go. |
| Home placement rule (home on a serving rank; working copies elsewhere) | keep | Correct for N-node geometry and cheap. |
| Prepare: transfer to other ranks (ssh-roce, streams), rank-local hot view, full verification after transfer | keep | The operator's only path to local files on every rank. |
| Verification stamp per view (tree digest plus size, mtime, and inode fingerprint; re-hash on change) | keep | Keeps launch fast on 30 to 120 GB trees without a full rehash. Replaces the witness and drift machinery with one record. |
| Pin, purge, hot budget, lifecycle locks | keep | Disk management and mutation safety; operator-visible. |
| Readiness check before launch | keep | Reduced to four questions: spec present, home verified, every rank view verified, computed launch contract equals the spec's. |
| Receipt service objects: plan, approval, receipt, result, occupancy, attachment | cut | The spec is the receipt. |
| `home relocate`, occupancy portability, unbound-complete classification | cut | Existed to rebind identity to trees; identity is now the spec. |
| Primary selection among duplicate homes, `cleanup-recommend` | cut | One verified home per identity per cluster; a second copy is a working view or a purge target. |
| Catalog scanning of arbitrary hub trees | cut | The catalog derives from `releases/` plus verification stamps. Unknown trees are ignored, not classified. |
| Health report engine and the interactive prepare flows behind the models view | cut | Replaced by one `pulsar models` projection: spec, status, home verified, ranks ready. |
| Guarded home-removal plans and the archive checks before last-home removal | cut | Reduced to `home remove --yes`, which refuses while a view is pinned or in use. |
| Cold archive to NFS, receipt replicas, controller-loss recovery, restore | defer | Re-download by exact commit is the recovery path; the spec is in git. Revisit only if upstream availability becomes a real risk. |
| Cold storage configuration and its wizard page | defer | Follows the archive decision. |

## Target command surface

The operator surface after Stage 4, in the order a first serve uses it:

| Command | Does |
|---|---|
| `pulsar release verify <spec_id>` / `show` / `list` | Read `releases/`; verify a spec's structure and digests; project status only when the computed launch contract matches. |
| `pulsar model acquire <spec_id> [--node RANK] --yes` | Download the exact commit into the durable home on the chosen serving rank, hash the tree, compare it to the spec manifest, write the verification stamp. Fails without fallback on any difference. |
| `pulsar model prepare <spec_id>` | Transfer to every other serving rank with the existing transport, verify each view fully, write stamps. |
| `pulsar model pin` / `purge` / budget | Unchanged in purpose. |
| `pulsar model remove <spec_id> --yes` | Remove a durable home; refuses while pinned or in use. |
| `scripts/check-weights.sh <profile>` | The four readiness questions above. |
| `pulsar models` | Four columns: spec, `review.status` (or hidden on contract mismatch), home verified, ranks ready. |

The launcher contract is unchanged: `launch_plan.py` still builds the argv,
and the strict same-boot gate still runs against what it launches.

## What this decision does not do

- It does not change a command, schema, or file today. The live library,
  its receipts, occupancy, and health report remain the implementation
  until ADR 0017 Stage 3 (specs and confs coexist; the library verifies
  against the spec manifest in addition to the receipt) and Stage 4 (the
  cut list is removed).
- It does not decide whether download receipts collapse into the spec.
  ADR 0017 keeps receipts as catalog identity and requires spec and receipt
  file lists to agree; collapsing them is named there as a revisit trigger
  and would be its own decision.
- It makes no physical claim. Acquire, prepare, and purge on GB10 hardware
  are re-proven when Stage 3 lands.

## Expected effect on decisions

When Stage 4 executes, the decisions that exist only to govern the cut
capabilities are expected to be marked historical with insert-only banners
in the same change: the occupancy and relocation rules of
[ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md), and the
receipt-replica and cold-storage rules of
[ADR 0013](./decisions/0013-separate-receipt-control-replica.md),
[ADR 0014](./decisions/0014-operator-owns-cold-storage-failure-domain.md),
[ADR 0015](./decisions/0015-explicit-cold-recovery-root.md), and
[ADR 0016](./decisions/0016-operator-owns-cold-storage-access-control.md).
[ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)
(the library is the only weight mechanism) and
[ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md) (no live
remote serving) are unaffected. Nothing is banner-marked by this document.

## Expected size

The library modules that own the cut capabilities are expected to shrink to
roughly a third of their combined size today. That is an expectation for
scoping Stage 4, not a measurement.
