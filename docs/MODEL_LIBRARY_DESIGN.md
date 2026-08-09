# Model library, activate, and load (agreed direction)

> **Status: agreed direction — not implemented.**  
> This document freezes the product requirements and definitions settled in
> design discussion. It is **not** an implementation plan, **not** a change to
> defaults, and **not** a promotion of experimental fabric. The stack continues
> to use replicated local caches by default and the live NFS/RDMA path only as
> documented in [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).
>
> Exploratory drafts and rejected-or-deferred option lists are archived under
> [docs/archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md).
> A peer-review snapshot of **current** code behavior lives in
> [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md).

| Field | Value |
|---|---|
| Status | Agreed direction (future consideration for implementation) |
| Settled | 2026-08-08 |
| Supersedes (exploration) | [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md) |
| Live experimental ops | [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) |
| Current-system peer review | [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md) |
| Default today | Replicated local Hugging Face caches |
| Experimental today | `--weight-source fabric` (live NFSv4.2/RDMA under vLLM) |

---

## 1. Product requirements (co-equal)

Any library / single-copy / activate design must satisfy all three. Winning only
a subset is incomplete for multi-node Spark users.

| ID | Requirement | Success looks like |
|---|---|---|
| **A** | **Catalog storage efficiency** | Durable on-disk footprint per model stays ~O(1) as serving node count grows. Extra disk buys **more models** (or larger ones), not another full copy of every model on every node. Capacity should use **aggregated** Spark storage across the cluster. |
| **B** | **Faster model loading** | Lower wall-clock **start → healthy** (cold and warm). Not resident decode tok/s after load. |
| **C** | **Stability and reliability** | Fail-closed, predictable, recoverable; no silent transport or disk-policy fallback; explicit dependencies; evidence-backed promotion. |

### 1.1 Requirement A — storage multiplies with node count

Today’s promoted multi-node path stages a full durable model tree on every
serving rank:

```text
durable_bytes ≈ N_nodes × model_size   # wrong scaling law at fleet size
```

Desired:

```text
library_bytes ≈ 1 × model_size per model (plus optional hot/pin working set)
catalog_capacity ≈ aggregate free space across Sparks (federated homes)
                 + optional cold archive
```

Adding nodes should add compute/memory/interconnect—not force another full
library replica of every model.

### 1.2 Requirement B — reduce model loading time

Load time = wall-clock to first healthy multi-node service (and useful
sub-metrics: activate transfer time, time to weights resident).

Levers: bytes per rank, path bandwidth, concurrent multi-rank transfer, warm
reuse (pins), avoid unnecessary double I/O, non-I/O engine setup.

**Promotion bar for a RoCE/fabric activate path:** it must **beat non-RoCE
control-path copy** (LAN/SSH-style bulk transfer) on the same model and
topology. Approaching pure local-replica cold start is desirable but not the
must-beat gate; B may yield where needed so A and C remain intact.

### 1.3 Requirement C — stable and reliable

| Dimension | Expectation |
|---|---|
| Fail-closed correctness | Partial snapshot, wrong transport, digest mismatch, or incomplete activate never reports healthy serving |
| Deterministic ops | Same config + topology → same checks; no silent environment shortcuts |
| Lifecycle safety | Interrupted activate/start cleans up; stop is ownership-safe |
| Fault clarity | Documented outcomes for activate interrupt, home unavailable, link loss during transfer, etc. |
| Honest dependencies | If a mode needs library/home/cold, inventory and docs say so; if independence is claimed, it holds |
| No silent fallback | Never auto-switch fabric → full N-replica pull (or TCP NFS) without operator-visible choice |
| Evidence-backed promotion | STATUS/docs change only with reproducible artifacts; failures preserved |

C caps unsafe optimization of A and B.

---

## 2. Layering

Do not conflate these three:

```text
1. Catalog / library (durable)  ← Requirement A
2. Load path (to memory)        ← Requirement B
3. Runtime dependency           ← Requirement C after start
```

| Layer | Question |
|---|---|
| Library | How many full trees exist on disk when nothing is running? |
| Load / activate | What bytes move, over which path, on cold start? |
| Runtime | After ready/healthy, does serving still need library/home/NFS? |

**Product identity:** single-copy (federated) **library** + explicit **activate**
+ serve from **hot** (or home-local) paths + **purge/pin** policy.  
**Fabric / NFS/RDMA** is a **transport** for activate, not the long-term product
name. Live mount under vLLM remains an experiment ([WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md)).

---

## 3. Storage tiers

| Tier | Role | Required? | Typical location |
|---|---|---|---|
| **Warm catalog** | Federated durable homes: complete model trees in the **default HF location on any Spark** | **Yes** (core library) | Per-node `$HF_CACHE` / hub layout |
| **Cold storage** | Shared or local **archive**; preferred **before Hugging Face download** when warm misses | **Optional** | Configurable path (conventionally `/mnt/Models` / `MODELS_NFS`) |
| **Hot staging** | Per-job (or pinned) working trees on ranks for load/serve/restart | Yes when ranks need a local tree | e.g. budgeted staging root outside durable HF home |
| **Origin** | Upstream download | Last resort when allowed | Hugging Face Hub |

### 3.1 Resolve order

```text
1. Warm catalog — complete home on any confirmed Spark
2. Cold storage — only if configured and available
3. Hugging Face — if allowed / reachable
4. Else fail closed with an explicit reason
```

| Cold config | Behavior |
|---|---|
| Unset / empty | Skip tier 2; no error; no mount required |
| Set but unavailable | Fail only when a flow needs cold (e.g. cold-path conf or explicit cold resolve); do not break pure HF-id flows that never need cold |
| Set and healthy | Prefer over HF download when warm misses |

Cold is **not** the default multi-node runtime filesystem. It is an optional
**fill / archive** tier.

### 3.2 Warm catalog (federated homes)

- Scan **default HF hub trees on every confirmed node** so users leverage
  **aggregated** disk: model A may live only on node 0, model B only on node 1.
- **One primary home per model revision** for durable membership (no silent
  N library copies).
- Home may be **any** node that holds a complete, sealable tree.
- **New downloads (recommended placement):** node with **most free space** on
  the HF cache filesystem among writable confirmed nodes; operator override
  `--node <id>`.
- Catalog entries are **labeled**:

| Label | Meaning |
|---|---|
| **Validated** | Matches a repo profile treated as validated (`STATUS=tested*` / validated list); identity aligns with conf expectations |
| **Present (unvalidated)** | Complete-looking hub tree on a Spark; Pulsar has **not** validated serving that model |
| **Partial / invalid** | Incomplete or not sealable — not a usable home |

**Catalog visibility ≠ Pulsar serving guarantee.** Wizard and default serve
paths remain gated on validated profiles. Unvalidated presence is for disk
awareness and advanced/explicit flows only.

### 3.3 Duplicates

If more than one home is registered for the **same model identity** (prefer
**hub id + revision**, not display name alone):

- Detect at catalog refresh / resolve / activate.
- **Do not** silently pick a home for serve.
- **Recommend** a **catalog cleanup** tool: list homes, nodes, sizes, seal
  status; operator chooses primary; optional guided removal of extras.
- No automatic destructive delete without confirmation.

### 3.4 Cold → cluster

Two operator options:

| Mode | Durable warm home on a Spark? | Use |
|---|---|---|
| **Adopt** | Yes — import into a Spark HF home and register | Grows federated library |
| **Stage-only** | No — cold → hot for this job only | Saves Spark disk; cold remains sole durable copy |

Stage-only unpinned restart needs cold (or re-resolve) again. **Pin** can still
allow warm restart without cold/home if hot is retained within budget.

Cold may use non-hub layouts (e.g. “Official Models/…”). Import/mapping into
hub-shaped warm form (or documented absolute-path confs) must be explicit and
fail-closed on incomplete trees.

### 3.5 Absolute-path / catalog confs

Profiles whose `MODEL` is an absolute path under cold remain a first-class
entry point (check-on-ranks, no HF download)—as today. They share the same
tier story: cold is optional site storage; multi-node **activate to hot** may
still apply when the product path is “library + activate” rather than
bind-mount cold on every rank for large models.

---

## 4. Activate, hot staging, pins, release

### 4.1 Lifecycle

```text
resolve (warm → cold? → HF?)
    → ensure sealed / verified source
    → activate (copy | fabric) → hot roots on needed ranks
    → verify digests / ready stamp (all-or-nothing)
    → release transfer plane (unmount library connection)
    → launch from hot (or home-local) binds only
    → serve (weights in unified memory)
    → stop → purge hot (default) or keep pin (opt-in)
```

### 4.2 Temporary hot disk

**Allowed.** Clients may hold a full (or later sharded) tree for the job window.
Hot is a **working set**, not a second full library of every catalog model.

### 4.3 Pins and disk budget

| State | Client disk | Restart without library/home | Catalog A |
|---|---|---|---|
| Unpinned stop | Purge hot | No — re-activate (needs source) | Best |
| **Pinned** | Keep verified hot | **Yes** | Spends client disk until unpin |
| Running | Hot present | N/A | Temporary |

**Pins are allowed** and required for the claim: **warm restart without
owner/home**.

Pins are bounded by a **per-node disk budget** (bytes or % of disk for hot+pin),
not unlimited growth. Activate/pin **refuses** when budget would be exceeded
(fail closed). Inventory shows used / budget / pinned models.

### 4.4 Warm restart without owner/home

**Required product claim** when hot is pinned (or still present and verified):

- Re-launch from hot only; library/home/cold need not be reachable.
- Unpinned after purge: re-activate needs resolve source again.

### 4.5 Activate backends: copy vs fabric

Both move bytes from **source home** (or cold stage path) into **hot** on ranks.
Neither replaces NCCL for inference.

| | **copy** | **fabric** |
|---|---|---|
| Meaning | Bulk transfer over **management/control** path (SSH/rsync-style) | Bulk transfer over **confirmed RoCE** (e.g. short-lived NFSv4.2/`proto=rdma`) |
| Claims RoCE for weights? | **No** | **Yes** (rail-pinned; optional HCA proof) |
| Setup cost | Low | Higher (export/mount window, sudo) |
| B role | **Baseline** | Must **beat copy** to claim fast path |
| A role | Same if only hot is written and purge/pin policy holds | Same |

Operator/config choice: `--backend copy|fabric`.  
**No silent** fabric→copy or fabric→full-replica fallback without visibility.

### 4.6 Release timing

**Release** = tear down the transfer plane (client unmounts; optional idle
export) so ranks are not tied to library/home over NFS for ordinary opens.

**Default claim:**

```text
activate → verify hot complete on all ranks → release transfer plane
         → launch from hot only → serve
```

Prefer **release after hot verified and before launch** (or before claiming
ready-to-serve). That makes load+serve independent of live library mount.

“Release only after `/health`” is a weaker debug posture, not the default
independence claim. Release does **not** delete hot; pins retain hot for restart.

### 4.7 Dependency contract

| Phase | Needs library / home / cold? |
|---|---|
| Resolve / activate | **Yes** (appropriate source) |
| Launch after hot ready + release | **No** |
| Running inference | **No** (weights resident) |
| Restart with **pin** | **No** |
| Restart **without** pin | **Yes** (re-activate) |

Inventory/labels should surface mode, home, hot ready, pinned, library released.

---

## 5. Relationship to current and experimental paths

| Path | Role under this direction |
|---|---|
| Replicated `pull-weights` + local launch | **Remains default** until a library+activate path earns promotion |
| Live `--weight-source fabric` | **Experimental** proof/ops path; long-lived mount under vLLM is **not** the agreed product identity |
| Site cold path confs | Optional cold tier; keep working |
| Sealed manifests, topology rails, HCA proof | Reuse for library integrity and fabric **activate** transport |
| Materialize-as-only-mechanism drafts | Superseded as the top-level story; activate+hot+pin is the product frame |

---

## 6. Design principles

1. **Library ≠ runtime path** — durable single-copy is a catalog property.
2. **Federated warm homes** — aggregate Spark disk; one primary home per revision.
3. **Cold is optional** — prefer before HF when present; never required for minimum install.
4. **At most a budgeted hot/pin set on clients** — not a replica farm of the library.
5. **Activate is first-class** — measured, fail-closed, not only hidden inside `docker run`.
6. **Dependency modes are explicit.**
7. **B is end-to-end time-to-healthy**; fabric must beat non-RoCE copy.
8. **C forbids silent N× disk** and silent transport downgrades.
9. **Prefer boring recovery** — re-activate + relaunch over clever hard-mount resume when clarity matters.
10. **Fabric is a transport**, not the product name.
11. **Validated vs present** labels protect claim hygiene when scanning all hub trees.
12. **Duplicates recommend cleanup**, never silent multi-home serve.

---

## 7. Intentionally deferred (not required to freeze this package)

- Exact CLI names and JSON schemas  
- Numeric pin budget defaults  
- Cleanup tool UX details  
- Whether unvalidated models are activate-able only with explicit force  
- Full promotion fault matrix (must-pass list)  
- Rank-sharded checkpoints (`sharded_state`) as phase-2 B lever  
- Dedicated storage-node topology for very large N  
- Implementation phases and PR breakdown  

---

## 8. Decision log

| Date | Decision |
|---|---|
| 2026-08-08 | Requirements **A** (storage), **B** (load time), **C** (reliability) co-equal. |
| 2026-08-08 | Product shape: federated warm library + optional cold + hot staging + pins + activate (copy\|fabric) + release before independent serve. |
| 2026-08-08 | Temporary hot disk allowed; warm restart without home required via pins; pins bounded by disk budget. |
| 2026-08-08 | Copy = non-RoCE control-path transfer; fabric = RoCE activate transport; fabric B bar = beat copy. |
| 2026-08-08 | Cold optional; resolve warm → cold? → HF; cold preferred over HF when configured; adopt and stage-only both allowed. |
| 2026-08-08 | Implemented optional cold tier: scan Official Models + hub layouts, resolve warm→cold fall-through, cold adopt, cold stage-only (`scripts/model-library.sh cold *`). |
| 2026-08-08 | Scan all hub trees; label validated vs unvalidated; duplicates recommend cleanup tool. |
| 2026-08-08 | New download placement: most free space + `--node` override (recommended default). |
| 2026-08-08 | Release after hot verified, before launch (default independence claim). |
| 2026-08-08 | Persisted as this document; exploratory option-noise archived to `docs/archive/WEIGHT_MATERIALIZE_DESIGN.md`. |
