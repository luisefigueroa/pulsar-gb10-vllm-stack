# Glossary

Short definitions for terms that look alike and are not interchangeable.
This page is a finder, not architecture or procedure. Start with the contrast
table when two names feel similar.

| Need | Document |
|---|---|
| Why the library works this way | [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md), [ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md) |
| Commands to run | [OPERATIONS.md](./OPERATIONS.md), `scripts/model-library.sh --help` |
| What is allowed to serve | [MODELS.md](./MODELS.md), [VALIDATION.md](./VALIDATION.md) |
| Decisions | [docs/decisions/](./decisions/) |

## Nearby terms (do not mix)

| This | Is not | Difference |
|---|---|---|
| **Receipt** | Occupancy | Receipt is *what the bytes are*. Occupancy is *where the live home sits*. |
| **Occupancy / occupancy tree** | Working replica | Occupancy is the one durable home. A working replica is an extra full copy used at runtime on a non-home rank. |
| **Working replica** | Runtime view / symlink | A working replica is a full copy (`sealed-hot`). The home rank uses a `durable-home` symlink/view of the occupancy tree, not a second copy. |
| **`home add`** | `catalog refresh` / `prepare` | Add acquires the durable home. Refresh only scans trees that already exist. Prepare builds rank-local runtime views from the resolved home. None of the three launches vLLM. |
| **`home archive`** | `cold adopt` / `cold stage-only` | Archive is a receipt-indexed NFS backup of occupancy. Adopt/stage-only are layout-inferred fill from an optional cold tree. |
| **`home archive`** | Live NFS serving | Archive stores bytes on NFS. vLLM never opens NFS (ADR 0005). |
| **`home relocate`** | `home restore` / `home add` | Relocate moves occupancy from a live Spark tree (copy or occupy-in-place). Restore copies from the verified archive. Add downloads from Hugging Face. |
| **Unbound-complete** | Duplicate home | Extra complete hub trees are not homes and do not freeze resolve when occupancy exists. Two occupancy documents for one revision is store corruption. |
| **`STATUS=tested*`** | ADR 0004 `Validated` | Legacy advisory label on a profile. Not a serving gate and not a Model Serving Release decision. |
| **Warm catalog** | Hot staging | Warm is durable occupancy on Spark NVMe. Hot is the per-job (or pinned) runtime tree. |

## A–Z

**Automatic-single-home.** Catalog primary policy when exactly one complete occupancy (or, for sealed/legacy trees without a receipt, one complete hub tree) exists. A second complete occupancy-class home requires an explicit primary selection.

**Catalog.** Site-local inventory of complete hub trees on confirmed ranks, plus occupancy class, primary selection, and advisory identity labels. `catalog refresh` scans; it does not download. `resolve` picks the warm occupancy home, then optional cold fill, then fails without fallback.

**Cold adopt.** Copy a layout-inferred tree from optional cold storage into a Spark Hugging Face hub home. Does not mint a source-attested receipt.

**Cold archive (`home archive`).** Receipt-indexed backup of the occupancy tree onto the optional cold root (`PULSAR_COLD_ROOT` or `MODELS_NFS`). Started in the background after `home add` attaches occupancy. Not a serving gate; vLLM never reads it.

- **`home archive status`** — read-only job state: `pending`, `running`, `complete`, `failed`, or `unavailable`.
- **`home archive run --receipt <id> --yes`** — perform or retry the copy and rehash. Also what autostart launches.

**Cold stage-only.** Materialize cold bytes into hot for one job without creating a durable warm home. Disk-starved exception, not disaster recovery.

**Cold storage.** Optional fill/archive tier. Two different uses share the mount: legacy Official Models / hub-layout fill (`cold scan` / `adopt` / `stage-only`), and receipt-indexed `pulsar-receipts/<receipt_id>/` archives. Do not treat a nameless tree on that mount as restore identity.

**Confirmed topology.** Gitignored membership document produced by `detect-fabric.sh --write-topology`. Serving, library placement, and rank numbers are meaningful only against this document. A one-node topology is valid.

**Control plane / inference plane / weight-transfer plane.** Three data planes that may share machines: SSH/ops, NCCL/RoCE serving, and SSH-over-RoCE weight copy. They are not interchangeable. Live NFS under vLLM is a rejected fourth plane (ADR 0005).

**Control-plane store.** Site-local library state: receipts, occupancy attachments, archive job files, and catalog cache. Distinct from the occupancy tree (the model bytes).

**Current-home attachment.** Site-local occupancy record: node, durable path, and directory identity (device/inode/ctime), pointing at one receipt. One attachment per `model_id@revision`.

**Durable home.** The one occupancy-class complete model tree for an exact revision. Federated: different models may live on different Sparks. Not N durable copies, not NFS, not hot.

**`durable-home`.** Runtime-source name for the home-rank view: a symlink of the occupancy tree. Distinct from occupancy (the durable bytes) and from a working replica (`sealed-hot`).

**Expected seal.** Lab-issued reviewed identity (model id, commit, manifest). Locally observed bytes can match it; they cannot create it.

**Fail without fallback.** The operation fails if the required condition cannot be verified. It does not skip the check, remap, or continue on a weaker path. Older ADRs say **fail closed** for the same rule.

**`home add`.** Acquire exactly one durable home: sealed (expected manifest) or source-attested (plan, then confirmed exact-commit download). Writes a receipt and occupancy attachment on the source-attested path. Does not refresh the catalog, prepare, or launch.

**`home archive`.** See **Cold archive**.

**`home check` / `home remove`.** Read-only plan, then confirmed retirement of one exact hub repository. Last occupancy needs `--allow-last-home`. Receipted last occupancy without a verified archive also needs `--allow-unarchived-last-home`.

**`home relocate`.** Move occupancy to `--node` after a live full SHA-256 against the receipt. Occupy-in-place if that rank already has matching bytes; otherwise copy. No Hub re-download. Receipt `selected_rank` does not block the move.

**`home restore`.** Copy from a verified receipt-indexed archive onto a Spark, live-rehash, then occupy. The NFS path never becomes the home.

**`home verify`.** Offline full rehash of occupancy against the attached receipt (or expected seal). An unbound complete tree with a receipt needs `home relocate`, not Hub re-add.

**Hot staging.** Rank-local trees used to load, serve, and restart (`PULSAR_HOT_ROOT`). On the occupancy rank this is a symlink/view. On other ranks it is a working replica. Pin protects from unforced purge; ordinary stop retains unpinned copies (ADR 0007); `--purge-hot` is explicit.

**library-hot.** The only weight-distribution mechanism (ADR 0006): occupancy home plus rank-local files before vLLM starts. There is no `--weight-source` / `--weight-mode` axis.

**Lifecycle lock.** Occupancy mutations (`home add` / `remove` / `relocate` / `restore`) take the exclusive form so they do not race each other or a remove. Prepare/launch and `home verify` / `check` take shared. `home archive` takes none (job-file flock only).

**Live NFS serving.** Rejected (ADR 0005): bind-mounting NFS into vLLM. A crashed rank cannot cold-start without the export. Historical notes: [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).

**local-verified-readonly.** Runtime access contract: vLLM opens local verified files. Distinct from `live-remote-readonly` (rejected).

**Model Serving Release.** Immutable four-part tuple: exact model, serving recipe, runtime/image, supported geometry (ADR 0004). Any change is a new release. Status is advisory and does not authorize serving.

**Occupancy.** Which live Spark directory currently holds the durable home for `model_id@revision`. Transferable with `home relocate` after a live rehash.

**Occupancy tree.** The complete Hugging Face hub snapshot at that occupancy path — the bytes occupancy names.

**Occupy-in-place.** Relocate that attaches occupancy to a complete tree already on the destination rank after a live rehash, without copying and without Hub re-download.

**Observed manifest.** SHA-256 file set computed from a live tree. Proves transfer integrity. Is not a lab seal and is not a receipt by itself.

**Origin.** Hugging Face Hub via explicit `home add`. Never a silent resolve fallback.

**Pin / purge / retain.** Pin: do not unforced-purge this hot tree. Retain: ordinary stop leaves unpinned hot copies. Purge: explicit `--purge-hot` capacity recovery.

**`prepare`.** Build exact rank-local runtime views from the resolved occupancy home (`durable-home` symlink on the home rank; working replicas on others). Does not download, refresh the catalog, or launch.

**Primary home.** The occupancy (or, for sealed/legacy, the selected complete tree) `resolve` will use. Missing or stale selection fails without auto-electing another node.

**Rank.** Zero-based index in the confirmed topology. Rank 0 is the controller running library commands.

**Receipt.** Immutable, content-addressed record of a source-attested acquisition: public source, complete manifest, Hub-download provenance (`selected_rank`). Lives in the control-plane store. Survives home remove. Does not by itself grant occupancy.

**Receipt-indexed archive.** Cold copy keyed by `receipt_id`, not by display name. Restore identity. Contrast layout-inferred `Official Models/` trees.

**`selected_rank`.** Provenance: where the Hub download ran. Not a relocate or occupy predicate.

**Sealed / source-attested / legacy-unsealed.** Sealed: reviewed expected seal. Source-attested: receipt from `home add --revision`. Legacy-unsealed: complete tree served after full observed verification, no seal; not an exact ADR 0004 attempt.

**Sealed-hot.** On-disk runtime-source name for a non-home full copy. Operator name: **working replica**. Schema and `PULSAR_HOT_ROOT` keep `sealed-hot`.

**ssh-roce.** Topology-bound SSH/rsync over the RoCE NIC for multi-rank prepare (and relocate-copy). TCP/IP on that NIC, not RDMA, not NCCL.

**Unbound-complete.** A complete hub tree with no matching occupancy attachment. Not a home. Occupy with `home relocate` or remove. Does not freeze catalog resolve when occupancy exists.

**Validation Contract.** Frozen ADR 0004 criteria for one release. Distinct from the release ID and from `STATUS`.

**Warm catalog.** Durable occupancy homes on Spark local disk, scanned across confirmed ranks.

**Witness.** Fast serve-time metadata check used only after a full SHA-256. Drift causes a visible full rehash or a failure without fallback. Never auto-reseals changed bytes.

**Working replica.** Full local copy of the model used at runtime on a rank that is not the occupancy home. Today’s sealed-hot copy. Not a pointer, not a durable home, not a remote NFS load.
