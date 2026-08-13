# Model library preparation and serving

> **Authority: canonical model-library architecture.**
> The storage, identity, dependency, and lifecycle decisions in this document
> are normative for future implementation. Sections explicitly labeled
> **current implementation** describe the unpromoted experiment and do not
> override the accepted target. Replicated local caches remain the guided
> default until every promotion gate passes. Operator commands and current
> limitations are documented in [OPERATIONS.md](./OPERATIONS.md); the distinct
> live NFS/RDMA path remains documented in
> [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).
>
> Exploratory drafts and rejected-or-deferred option lists are archived under
> [docs/archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md).
> A descriptive snapshot of **current** code behavior lives in
> [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md).
> The maintainer-only, candidate-stage release workflow is documented in
> [MODEL_RELEASE.md](./MODEL_RELEASE.md).
> The durable rationale for the home-view and validation-identity decision is
> [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md).
> Qualification scope and evidence reuse are governed by
> [ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md).
> The transport policy for an explicitly selected experimental preparation is
> recorded in
> [ADR 0003](./decisions/0003-explicit-model-preparation-transport.md).

| Field | Value |
|---|---|
| Authority | Accepted architecture; current implementation remains experimental |
| Status | Implemented experiment (not promoted); reviewed identities are issued for `qwen3-1.7b` and flagship `deepseek-v4-flash`, and both passed applicable physical `library-hot` enforcement; the existing DeepSeek duplicate was reconciled to one persistent durable home and the exact sealed lifecycle passed again. The exact-GA strict determinism gate subsequently failed; sustained soak was not run and promotion is blocked under the current contract. |
| Settled | 2026-08-08; home-view and validation-identity policy revised 2026-08-10; first reviewed identity issued 2026-08-11; flagship identity issued and qualification boundaries revised 2026-08-12 |
| Supersedes (exploration) | [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md) |
| Accepted decisions | [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md); [ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md); [ADR 0003](./decisions/0003-explicit-model-preparation-transport.md) |
| Live experimental ops | [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) |
| Current-system peer review | [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md) |
| Default today | Replicated local Hugging Face caches |
| Experimental today | `scripts/model-library.sh` catalog/cold/prepare/hot/pin workflows; `--weight-mode library-hot`; `--weight-source fabric` live NFSv4.2/RDMA; and maintainer-only `scripts/model-release.sh` candidate assembly |

**Current implementation integrity boundary:** catalog schema 2 accepts an
optional reviewed `models/seals/*.json` trust root and binds a tested profile to
its exact Hugging Face commit. Hot schema 3 records the expected seal,
validation-bundle ID, and locally observed revision/manifest. Preparation
full-hashes every rank and atomically writes a rank-local witness before
publishing ready state. Launch first rechecks the live profile or controller
expectation, then uses the witness when canonical view and file metadata are
unchanged. A missing, invalid, or drifted witness is visible and causes a stable
full SHA-256 verification; success atomically refreshes it, while a content
mismatch fails without refresh. Launch still passes the exact
`snapshots/<revision>` path to vLLM. The one-node diagnostic `qwen3-1.7b`
profile carries the first issued seal/bundle and reaches `identity=match` on
`library-hot`. The flagship `deepseek-v4-flash` profile carries the second
issued seal/bundle and passed its applicable two-node post-issuance physical
enforcement gate. Profiles without a seal, including `qwen3-1.7b-2node`, remain
`legacy-unsealed` and require explicit `--allow-unvalidated` for model-library
experiments. Catalog refresh discovers complete snapshot commit
directories independently of mutable `refs/main`; sealed inspection,
manifest construction, verification, and launch all receive that selected
commit explicitly. Guarded home removal now requires all confirmed nodes'
managed hot state and Docker state to be observable, blocks retained hot views
and managed containers, and serializes supported readers/launchers against
deletion. Removal is limited to an exact single-revision HF repository and
rechecks its metadata immediately before retirement. A sealed profile now also
requires a content-addressed schema-1 validation bundle whose primary model,
external artifacts, lab provenance/evidence, digest-pinned image, normalized
runtime contract, and geometry match the reviewed seal and live sourced
profile. Sealed replicated profiles now request the exact commit during
download, full-verify the controller copy and every copied rank, create a
rank-local witness outside the copied repository, and launch the exact snapshot
through a read-only repository mount with the same revision/seal/bundle labels.
The distributed library now has a separate reviewed-profile acquisition
service: `home add` observes every confirmed rank, allows a one-node profile on
any confirmed rank while preserving exact multi-node geometry, selects the
eligible candidate with the most free space unless `--node` overrides it, and downloads there
into private same-filesystem staging, rechecks that no home appeared elsewhere,
full-verifies the expected manifest, and atomically publishes exactly one
durable HF repository. It neither creates hot copies nor refreshes the catalog,
so registration remains the operator's explicit next action. Target capability
discovery accepts the CLI on PATH or Pulsar's managed user-venv installation;
it does not move controller authentication to the selected rank.
Legacy-unsealed replicated profiles retain their structural `refs/main`
behavior. Live-mount launches are not yet content-bound by expected seals.
Issuing or enforcing the DeepSeek flagship identity does not by itself promote
a storage path.

`scripts/model_identity.py` is the single local owner of the profile-contract,
validation-bundle, and expected-seal schemas. `scripts/model-release.sh` can
hash an explicitly selected commit and assemble deterministic candidate
documents only under an untrusted output boundary. Candidates declare no
authority, cannot write the reviewed model directories, cannot edit profiles,
and do not affect validation status.

---

## 1. Product requirements (co-equal)

Any library / single-copy / preparation design must satisfy all three. Winning only
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
sub-metrics: preparation transfer time, time to weights resident).

Levers: bytes per rank, path bandwidth, concurrent multi-rank transfer, warm
reuse (pins), avoid unnecessary double I/O, non-I/O engine setup.

**Promotion bar for a transfer advertised as a RoCE fast path**
(`ssh-roce` or one-shot `nfs-rdma`): it must beat `ssh-control` on the same
model and topology. Approaching pure local-replica cold start is desirable but not the
must-beat gate; B may yield where needed so A and C remain intact.

### 1.3 Requirement C — stable and reliable

| Dimension | Expectation |
|---|---|
| Fail-closed correctness | Partial snapshot, wrong transport, digest mismatch, or incomplete preparation never reports healthy serving |
| Deterministic ops | Same config + topology → same checks; no silent environment shortcuts |
| Lifecycle safety | Interrupted preparation/start cleans up; stop is ownership-safe |
| Fault clarity | Documented outcomes for preparation interruption, home unavailable, link loss during transfer, etc. |
| Honest dependencies | If a mode needs library/home/cold, inventory and docs say so; if independence is claimed, it holds |
| No silent fallback | Never auto-switch fabric → full N-replica pull (or TCP NFS) without operator-visible choice |
| Evidence-backed promotion | STATUS/docs change only with reproducible artifacts; failures preserved |

C caps unsafe optimization of A and B.

### 1.4 SSH identity binding across network planes

Selecting a confirmed RoCE address changes the **transport endpoint**, not the
identity of the node being trusted. Every SSH-backed copy or orchestration path
must keep those concepts separate:

- `HostName` is the exact confirmed control or RoCE address chosen for this
  connection;
- `HostKeyAlias` is the node's stable confirmed `ssh_host` lookup label (it is
  an alias, not a host key); and
- `StrictHostKeyChecking=yes` verifies the presented key against the enrolled
  identity instead of creating a second trust identity for each rail IP.

The confirmed topology must bind one immutable node ID to its stable SSH alias,
accepted host-key fingerprint set, control endpoint, and RoCE endpoints. A
connection to any of those endpoints is trusted only when the endpoint still
maps to that node ID and presents an enrolled key. Address reachability alone
is never proof of node identity, and a changed key is never accepted as an
automatic topology refresh.

This contract is implemented by topology schema 2 and the generated
`.cluster-ssh-config`. Ordinary fabric discovery continues to produce schema 1
and cannot enroll trust implicitly. `scripts/topology-ssh-trust.sh enroll`
collects host public keys only through normal, already-trusted OpenSSH on the
exact saved control address, verifies all control and pairwise RoCE endpoints,
and then writes schema 2. Every topology-aware SSH caller loads the generated
config; a missing or stale config makes schema 2 unloadable.

---

## 2. Layering

Do not conflate these three storage/runtime layers:

```text
1. Catalog / library (durable)  ← Requirement A
2. Load path (to memory)        ← Requirement B
3. Runtime dependency           ← Requirement C after start
```

| Layer | Question |
|---|---|
| Library | How many full trees exist on disk when nothing is running? |
| Load / preparation | What bytes move, over which path, on cold start? |
| Runtime | After ready/healthy, does serving still need library/home/NFS? |

**Product identity:** single-copy (federated) **library** + explicit **preparation**
+ rank-local runtime views + **purge/pin** policy.
**Fabric / NFS/RDMA** is a transport, not the long-term product name. Live
mount under vLLM remains an experiment ([WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md)).

### 2.1 Terminology and independent axes

- **Home** is the durable storage placement for one exact model revision. It
  may be any confirmed node and is not necessarily rank 0.
- **Owner** is reserved for the node running a live export/service, such as the
  experimental NFS/RDMA path. It is not a synonym for rank 0 or durable home.
- **Rank 0** is the API/control rank for the exact serving geometry.
- **Origin** is `huggingface`, `cold-catalog`, or `managed-home`.
- **Transfer** is `preexisting`, `ssh-control`, `ssh-roce`, or `nfs-rdma`.
- **Runtime source** is `durable-home`, `sealed-hot`, or `live-mount`.
- **Retention** is `durable`, `ephemeral`, or `pinned`.
- **Prepare / model preparation** is the user-facing operation that resolves the
  exact model, creates the required rank-local runtime views, transfers only
  non-home bytes, and verifies every rank. It does **not** start a serving
  container or establish model qualification. `activate` remains a
  backward-compatible CLI and internal-schema term only.

Evidence, labels, and future schemas should record these axes independently.
In particular, SSH/TCP over a RoCE interface is `ssh-roce`; it is not the live
NFS/RDMA runtime mode merely because both use the fabric NIC.

### 2.2 Qualification boundaries

Storage/runtime layers are also distinct from evidence scope. Pulsar evaluates
four scopes independently and combines them only for release or promotion:

| Scope | Question |
|---|---|
| Catalog and artifact service | Are the exact bytes identified, placed, transferred, retained, repaired, and cleaned up according to the library contract? |
| Serving integration | Did the selected image and launcher mount and load the intended exact runtime source, then pass health, warmup, and completion smoke? |
| Model qualification | Does the exact model/image/configuration/geometry meet correctness, determinism, performance, context, and soak requirements? |
| Release and promotion | Have all subsystem gates required for the supported profile or guided policy passed together? |

A failure in one subsystem does not erase valid evidence from another unless a
causal connection is demonstrated. It does block a release claim that requires
both. A successful health check or completion proves serving integration, not
model qualification. Similarly, catalog acceptance does not promote a profile,
storage path, wizard choice, or default policy. See
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md).

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
- Catalog schema 2 stores an operator selection by exact
  `model_id@revision`, stable node ID, and selection time in the site-local
  catalog. Selection validates the catalog rank/node against confirmed
  topology. A refresh preserves that selection. If the selected node no longer
  reports the complete home, the selection becomes `stale` and resolution
  fails closed; a different home is never auto-elected.
- A revision with exactly one complete home may use
  `automatic-single-home`. A duplicate requires an explicit
  `catalog primary set ... --node ...` selection. Clearing that selection
  deliberately returns a duplicate to unavailable/operator-required state.
- Home may be **any** node that holds a complete, sealable tree.
- **New downloads (recommended placement):** node with **most free space** on
  the HF cache filesystem among writable confirmed nodes; operator override
  `--node <id>`.
- **Implemented acquisition boundary:** `home add <sealed-profile>` accepts
  only a reviewed expected seal and exact commit. Every confirmed rank must be
  observable. A one-node profile may establish its sole serving placement on
  any confirmed rank; automatic placement chooses the eligible rank with the
  most free space, while `--node` binds an exact remote or local placement.
  Multi-node placement remains limited to the profile's exact serving ranks so
  active storage remains one durable home plus N−1 hot copies. An existing
  repository path anywhere blocks duplicate creation; an explicit ineligible
  or out-of-geometry `--node` fails without choosing another rank. The
  chosen rank must have a Hugging Face CLI, sufficient space for the complete
  manifest plus staging headroom, and upstream access/authentication. Download
  failure removes only plan-owned private staging. Before publication Pulsar
  repeats the all-rank no-home check, performs full SHA-256 verification, and
  atomically renames the repository into its durable HF home. Catalog refresh,
  hot preparation, launch, and fallback are separate actions.
- Catalog entries are **labeled**:

| Label | Meaning |
|---|---|
| **Validated** | Target contract: the observed exact revision/manifest matches the lab-issued seal in a tested validation bundle |
| **Present (unvalidated)** | Complete-looking hub tree on a Spark; Pulsar has **not** validated serving that model |
| **Partial / invalid** | Incomplete or not sealable — not a usable home |

**Catalog visibility ≠ Pulsar serving guarantee.** Wizard and default serve
paths remain gated on validated profiles. Unvalidated presence is for disk
awareness and advanced/explicit flows only.

**Current implementation:** a tested profile without
`EXPECTED_MODEL_SEAL` is labeled `legacy-unsealed`. A reviewed seal under
`models/seals/` makes catalog schema 2 select only the declared immutable
commit and label it `expected-unverified`; preparation then computes the observed
manifest and must reach `match`. `catalog list --validated` includes only
entries carrying a reviewed expected seal, never legacy repository-ID-only
claims. The one-node diagnostic `qwen3-1.7b` profile is the first issued seal and
`deepseek-v4-flash` is the second. Profiles without a seal remain
legacy-unsealed.

### 3.3 Duplicates

If more than one home is registered for the **same model identity** (prefer
**hub id + revision**, not display name alone):

- Detect at catalog refresh / resolve / prepare.
- **Do not** silently pick a home for serve.
- `cleanup-recommend` lists homes, nodes, sizes, seal state, and exact
  operator commands. Before a primary exists it prints selection choices and
  no removal commands, and the removal planner blocks a direct `--node`
  attempt. After selection it prints read-only `home check` and
  separate confirmed `home remove --node ... --yes` commands only for
  non-primary homes.
- Refuse removal of the selected primary while an alternate complete home
  exists. The operator must first select the intended survivor.
- No automatic destructive delete without confirmation.

### 3.4 Cold → cluster

Two operator options:

| Mode | Durable warm home on a Spark? | Use |
|---|---|---|
| **Adopt** | Yes — import into a Spark HF home and register | Grows federated library |
| **Stage-only** | No — cold → hot for this job only | Saves Spark disk; cold remains sole durable copy |

Stage-only hot is fully materialized, so retaining or pinning it can allow a
restart without cold. Warm-home preparation is different by design: its home
rank uses a zero-copy symlink into the durable HF cache, so retaining that hot
instance does not make it independent of the durable home.

An atomic same-filesystem move is allowed only as an explicit **adopt** into a
managed durable root after the observed content matches the expected seal. It
is never a preparation shortcut into purgeable hot storage. Adoption must keep
home removal and rollback behavior explicit.

Cold may use non-hub layouts (e.g. “Official Models/…”). Import/mapping into
hub-shaped warm form (or documented absolute-path confs) must be explicit and
fail-closed on incomplete trees.

### 3.5 Absolute-path / catalog confs

Profiles whose `MODEL` is an absolute path under cold remain a first-class
entry point (check-on-ranks, no HF download)—as today. They share the same
tier story: cold is optional site storage; multi-node **preparation into hot staging** may
still apply when the product path is “library + prepare” rather than
bind-mount cold on every rank for large models.

---

## 4. Model preparation, hot staging, pins, release

### 4.1 Lifecycle

```text
resolve (warm → cold? → HF?)
    → match observed content to the expected lab seal
    → expose durable-home view + transfer sealed-hot to non-home ranks
    → full-verify each physical copy / write ready witness (all-or-nothing)
    → release transfer plane
    → launch the exact revision from rank-local read-only views
    → serve (weights in unified memory)
    → stop → purge non-home hot (default) or keep pin (opt-in)
```

### 4.2 Temporary hot disk and storage accounting

**Allowed.** Non-home ranks may hold a full (or later sharded) tree for the job
window. Hot is a working set, not a second full library of every catalog model.

For a warm-home service using `N` ranks:

```text
idle durable storage = 1 × model_size
active storage       = 1 durable home + (N - 1) sealed-hot working copies
after unpinned stop  = 1 × model_size
```

The home-rank symlink contributes no owned hot model bytes. Admission charges
the exact sealed manifest size only to ranks whose runtime source is
`sealed-hot`; a `durable-home` view requires zero additional model bytes.
Existing files anywhere below the hot root, including untracked or malformed
managed content, still count toward that rank's current owned-hot total.

### 4.3 Pins and disk budget

| State | Non-home disk | Restart contract | Durable-home dependency |
|---|---|---|---|
| Unpinned stop | Purged | Prepare again before restart | Required as preparation source |
| **Pinned warm-home** | Keep verified hot | No cold, transfer, or catalog refresh | **Still required** |
| **Pinned cold stage-only** | Keep every staged rank | May be self-contained | No warm home exists |
| Running warm-home | Sealed hot on non-home ranks | N/A | **Required on its rank** |

Pins are bounded by a per-rank filesystem-backed hot policy, not unlimited
growth. By default every selected rank must preserve available space equal to
the greater of 64 GiB or 5% of that filesystem's total capacity after the
planned write. There is no arbitrary default hard cap. An operator may set an
explicit hard cap with `PULSAR_HOT_BUDGET_BYTES` or replace the default reserve
with `PULSAR_HOT_RESERVE_BYTES`; both values apply independently on every
selected rank.

Prepare, cold stage-only, pin, and budget inventory collect an exact
observation from every selected physical rank before mutation. A missing,
duplicate, unreachable, or blocked rank fails the all-rank barrier before
model bytes change. Accounting uses filesystem space available to the service
user (`statvfs.f_bavail`), counts the complete hot root without following
symlinks, and reports pinned, reclaimable, untracked, and malformed state.
Replacement may credit the old instance against an explicit hard cap, but it
does not optimistically credit those bytes as free space before deletion.

There is no automatic eviction, transport fallback, or reserve relaxation.
The operator explicitly purges an unpinned instance or frees disk and then
rechecks. Supported hot mutations are serialized against one another, while
launch/readiness paths hold a shared hot-state lock. Pin protects non-home hot
content from purge; it does not convert the durable home into hot, duplicate
it, or claim survival after home loss.

### 4.4 Warm restart and home-loss semantics

The accepted warm-home claim is:

- restart without cold storage, a transfer plane, or catalog refresh while the
  durable home and retained non-home hot copies remain valid;
- unpinned restart prepares non-home ranks again from the durable home;
- durable-home loss is service loss for this policy.

Home-loss resilience requires an explicit durable replica on another failure
domain plus supported placement/failover behavior. A second copy on the same
rank/filesystem is not that policy. In an exact multi-node geometry, losing the
home node also removes a required compute rank.

### 4.5 Expected identity and verification tiers

A validated claim has two distinct identities:

- **Expected seal:** lab-issued model ID, exact commit/revision, complete
  `sha256-snapshot-manifest-v1` manifest ID, and provenance.
- **Observed seal:** identity computed from a user's or rank's local bytes and
  compared with the expected seal. Observed content cannot issue or replace the
  expected seal.

A **validation bundle** binds the expected model seal(s), behavior-affecting
tokenizer/draft/adapter/code artifacts, normalized profile/runtime
configuration, resolved image digest, geometry/topology class, and evidence.
Hosting location—including a future mirror—is distribution metadata, not
identity.

Verification has two tiers:

1. **Full SHA-256** at lab sealing, adoption/download, each non-home
   materialization, and whenever metadata drifts.
2. **Fast serve-time witness** only after full verification. It binds the
   canonical symlink target, local filesystem identity, exact revision, logical
   file set, and per-file device, inode, size, `mtime_ns`, and `ctime_ns`.

Launch must resolve the exact validated snapshot, not validate one revision and
then let the runtime follow mutable `main`. Witness drift causes visible full
verification against the expected seal or fails closed. A successful full
verification may atomically refresh the witness; a mismatch never auto-reseals
the changed content as validated.

**Current implementation:** hot schema 3 carries both the reviewed expected
seal projection and the observed seal. Preparation compares model ID, immutable
commit, and manifest ID, then full-verifies every rank and atomically creates
that rank's `.pulsar/witness.json` before publishing ready state. Sealed
replicated acquisition applies the same expected manifest to the exact
downloaded commit and every `rsync` destination, then writes a separate
rank-local witness under the HF cache's Pulsar state directory—not inside the
repository that is copied. Both witness schemas bind validation identity,
canonical hub/snapshot targets, filesystem device/inode identity, the exact
logical file set, and per-file
device/inode/size/`mtime_ns`/`ctime_ns`. Launch validates the current
profile/seal or controller expectation before consulting the applicable
witness. An unchanged witness hashes zero model bytes; missing, malformed, or
drifted metadata emits a visible fallback, full-verifies the sealed manifest
under stable metadata, and atomically refreshes the witness only on success.
Launch then passes the exact snapshot path through a read-only repository view.
For `identity_status=match`, that manifest is bound to the lab-issued expected
seal. A `legacy-unsealed` path never becomes validated through a witness.
`home add` also uses this reviewed manifest as its publication gate. It hashes
the private target-rank staging tree before a same-filesystem rename; it does
not create a serve witness because no runtime view has been prepared yet.
The seal points one-way to a content-addressed schema-1 validation bundle.
Profile load verifies the bundle ID, exact primary model projection,
provenance/evidence parity, declared external-artifact identities/digests, and
normalized live profile/image/geometry binding before catalog, preparation, or launch may use
the sealed claim. The bundle deliberately omits the seal ID to avoid a hash
cycle. The one-node diagnostic `qwen3-1.7b` profile carries the first
reviewed bundle and flagship `deepseek-v4-flash` carries the second. Neither
issuance promotes this storage path; the DeepSeek post-issuance physical gate
passed without resolving its disclosed duplicate durable-cache condition.

The maintainer-only release service now builds the same schema through
`scripts/model_identity.py`, but its output is explicitly `unreviewed` with
`authority=none` and `promotion=not-authorized`. Internal consistency is not
issuance; a reviewed pull request and applicable lab evidence remain the trust
boundary.

### 4.6 Prepare transfers

Transfer moves bytes only to ranks whose runtime source is `sealed-hot`.
The warm-home rank uses its existing `durable-home` view.

| Transfer | Current CLI shape | Network/path claim | Promotion role |
|---|---|---|---|
| `ssh-control` | copy backend over confirmed control SSH | Management LAN | Baseline, diagnostic, and explicit comparison path |
| `ssh-roce` | copy backend with `--transport ssh-roce` | SSH/TCP pinned to confirmed RoCE endpoint | Fixed eight-stream policy for the explicit interactive experiment; still unpromoted as a storage path |
| `nfs-rdma` | fabric backend, then release | Short-lived NFSv4.2/RDMA transfer plane | Separate candidate |
| `live-mount` | `--weight-source fabric` | Long-lived NFSv4.2/RDMA runtime dependency | Separate experiment |

Neither transfer replaces NCCL inference traffic. No candidate may silently
fall back to control transfer, TCP NFS, replicated pulls, or a different
runtime source. ADR 0003 chooses `ssh-roce` with eight streams only after an
operator explicitly selects experimental preparation and an eligible durable
home already exists; it does not alter the replicated guided default.

### 4.7 Release timing

Release tears down a temporary transfer plane after every non-home hot copy is
fully verified. The service then uses:

```text
home rank      → validated durable-home view
non-home ranks → verified sealed-hot views
```

Release occurs before launch or before claiming ready-to-serve. It does not
delete hot content; pins retain non-home hot copies. The home dependency is a
local durable-storage dependency, not a retained network transfer plane.

### 4.8 Dependency contract

| Phase | Required dependency |
|---|---|
| Resolve / prepare | Expected seal plus the selected durable/cold source |
| Launch after ready + release | Durable home on its rank; sealed hot on non-home ranks |
| Running inference | Rank files may no longer be read once resident, but declared storage dependencies remain honest |
| Warm-home restart with pin | Durable home plus pinned non-home hot; no transfer/catalog refresh |
| Warm-home restart without pin | Durable home plus preparation again |
| Cold stage-only restart with complete pin | Pinned staged trees; cold may be unavailable |
| Restart after durable-home loss | Unsupported without an explicit durable replica/failover policy |

Inventory and labels must surface home identity, per-rank runtime source,
expected/observed seal status, witness status, pin state, and transfer release.

### 4.9 Symlink and lifecycle safety

The home view must resolve to the expected canonical local durable tree. The
target is read-only to serving containers where practical. Validation and
launch operate on the same exact revision/path so a mutable alias cannot change
between them.

Hot purge must remove only the managed hot instance and never follow the home
symlink. An active launch/reference blocks durable-home removal. Removing a
home is a separate confirmation-gated operation that reports every dependent
managed container and retained hot instance, whether ready, verifying, or
pinned.

**Current implementation:** `scripts/model-library.sh home check` builds a
fail-closed plan from an authoritative home inspection plus hot-state and
Docker observations on every confirmed node. `home remove ... --yes` executes
only an eligible plan. A final durable copy requires the additional
`--allow-last-home` acknowledgement. The target must be the exact catalogued
HF repository, contain only the selected snapshot revision, use non-symlinked
`snapshots`/`refs` layout directories, and have no ref pointing elsewhere.
Before deletion, the home node repeats the shape inspection, compares a
metadata fingerprint, atomically renames the repository to a plan-bound
retirement path, removes that path without following managed hot views, and
then refreshes the catalog.

A repository-local shared/exclusive lifecycle lock closes races among supported
Pulsar catalog, preparation, launch, readiness, download, fabric, and removal
commands. Missing topology, unreachable nodes, unavailable Docker, or a
contradictory observation contract aborts planning. Unreadable or legacy hot
metadata remains a visible blocker instead of proving absence. This guard
cannot discover an unmanaged process or container created outside Pulsar
labels; such use remains an operator responsibility and must be stopped before
removal.

### 4.10 Read-only health and legacy-hot repair

`scripts/model-library.sh health` is the supported catalog-health service. It
reads the cached catalog and gathers shallow, no-follow hot metadata plus
managed-container observations from every confirmed rank under shared
lifecycle/hot locks. It never refreshes the catalog, refreshes a witness,
hashes model bytes, follows a runtime-view symlink, or changes state. Catalog
absence is `not-configured` because replicated weights remain the default.
Invalid/stale catalogs, duplicate homes, stale primary selection, prohibited
runtime views, witness drift/missing state, legacy metadata, and unobservable
ranks are explicit findings.

The public schema-1 report exposes rank numbers, profile aliases,
model/revision identity, cached refresh time, expected manifest identity when
reviewed, primary/duplicate classification, runtime source, retention, identity/witness status, active-reference state,
opaque repair IDs, issue codes, and remediation. It omits hostnames, addresses,
node/topology identity, absolute paths, filesystem identity, and witness IDs.
`healthy` and `not-configured` exit zero; `attention` and `unavailable` still
emit the complete report and exit nonzero.
Rank-based home and primary placement is meaningful only while
`catalog.topology_compatible=true`; interactive consumers suppress cached rank
mapping and require refresh when the confirmed topology has changed.

Historical hot schemas 1 and 2 are ownership evidence only: they are never
trusted, launchable, or migrated into schema 3. A health-issued repair ID may
be passed to `hot legacy check`; `hot legacy remove ... --yes` repeats rank
metadata and managed-container observation under the exclusive hot lock before
mutation. It refuses current, malformed, untracked, symlinked, ambiguous,
stale, active, or unobservable targets. Pinned state additionally requires
`--force-unpin`. Eligible removal atomically retires one exact non-symlink
instance and deletes it without following embedded symlinks; durable homes and
sibling instances are outside its authority. An incomplete retirement remains
discoverable and retryable.

Doctor consumes the same report as warnings. These findings do not block
replicated/default serving, while model-library preparation and destructive
lifecycle operations retain their fail-closed checks. `./pulsar models` and the
operator-home **Models & storage** entry expose a width-aware projection of
this same contract. Browsing and health rechecks are read-only. A separate,
confirmation-gated **Refresh distributed catalog** action delegates to the
existing all-confirmed-rank refresh service, preserves exact-revision primary
selections, and then renders a new sanitized health report. Refresh is never
automatic; incomplete topology or rank observation fails closed. The
projection labels replicated serving as the guided default and the distributed
catalog as experimental.

An exact model detail may also offer **Prepare for experimental serving** when
the catalog mapping is current and the matching tested serving profile carries
a reviewed expected seal. This is a separate default-no mutation, fixed to the
accepted eight-stream SSH-over-RoCE copy policy for non-home ranks with no
fallback. A one-node home-only view uses `ssh-control` with one stream and no
bulk transfer. The
interactive layer shows exact revision/manifest identity, durable-home
dependency, serving ranks, and an approximate non-home storage requirement,
then delegates to the existing preparation service. That service remains the
authority for full verification, exact all-rank storage admission, topology and
primary checks, rollback, and witness publication. The interaction never adds
`--allow-unvalidated`, starts serving, changes the replicated guided default, or
claims model qualification or storage-path promotion. Retention, repair, purge,
and durable-home removal remain separate direct-CLI operations.

The serving wizard is a distinct consumer of the same readiness contract. For
an eligible reviewed profile it offers replicated weights first and an
explicit **distributed catalog (experimental)** choice second. The wizard
shows exact revision/manifest identity, durable-home dependency, selected
ranks, fixed transfer policy, and no-fallback behavior. It may invoke the same
preparation service after a default-no confirmation, but it re-reads health and
requires every selected runtime view to be exact and ready before setting
`--weight-source library-hot`. Container launch remains behind the wizard's
separate final confirmation. A one-node catalog service is placed on its
durable-home rank and uses that local view; multi-node preparation uses the
exact profile ranks and creates sealed-hot copies only on non-home ranks.

On stop, an observed `library-hot` service purges unpinned prepared views by
default. `--pin-weights` is the explicit retention choice; a confirmed restart
pins before stopping so the same views remain available. Pinning does not copy
or protect the durable home. Explicit `--purge-hot` may remove a pin, while
durable-home deletion remains a separate direct-CLI workflow.

Current health closes supported catalog/hot observability, but container labels still do not carry
per-rank runtime-source/witness state and unmanaged processes remain outside
Pulsar's discovery boundary.

---

## 5. Relationship to current and experimental paths

| Path | Role under this direction |
|---|---|
| Replicated `pull-weights` + local launch | **Remains default** until a library+prepare path earns promotion |
| Live `--weight-source fabric` | **Experimental** proof/ops path; long-lived mount under vLLM is **not** the agreed product identity |
| Site cold path confs | Optional cold tier; keep working |
| Topology rails and NFS/RDMA helpers | Reused by fabric **preparation**; model-library schema-3 hot state carries full SHA-256 observed content plus optional expected-seal provenance, while live-fabric configuration identity remains separate |
| Materialize-as-only-mechanism drafts | Superseded as the top-level story; prepare+hot+pin is the product frame |

---

## 6. Design principles

1. **Library ≠ runtime path** — durable ownership and the path presented to a
   rank are different facts.
2. **One durable home per exact revision by default** — extra replicas are an
   explicit capacity/resilience policy.
3. **No home-rank hot materialization** — the home rank uses a validated
   symlink or equivalent local view; only non-home ranks receive sealed hot.
4. **Expected identity comes from the lab** — observed user content cannot
   self-bless or inherit validation from a repository ID.
5. **Full verification establishes trust; metadata witnesses preserve it** —
   drift visibly rehashes against the expected seal or fails closed.
6. **Cold is optional** — prefer it before HF when present; never require it for
   the minimum installation.
7. **Hot and pins are budgeted working sets**, not a replica farm.
8. **Dependency modes are explicit** — warm-home pinning still needs its
   durable home; home-loss resilience requires another failure domain.
9. **Prepare is first-class and measured** — end-to-end start-to-healthy,
   integrity, and recovery matter more than peak transport bandwidth.
10. **Transport is not product identity** — distinguish `ssh-control`,
    `ssh-roce`, one-shot `nfs-rdma`, and live mount.
11. **No silent policy changes** — never change transport, runtime source,
    geometry, or replica count as a fallback.
12. **Validated vs present labels protect claim hygiene**; duplicates recommend
    cleanup and never cause silent multi-home serve.
13. **Prefer boring recovery** — explicit verify, prepare again, and relaunch over
    hidden mount or replica behavior.
14. **Raw experiments stay local** under gitignored `/experiments/`; durable
    decisions belong in reviewed design/ADR/runbook docs and sanitized evidence.
15. **Evidence is scoped, not globally contagious** — preserve valid catalog,
    integration, and model results within their measured contracts; combine
    them only for a release claim, and expand invalidation only when inputs or a
    demonstrated causal dependency cross subsystem boundaries.

---

## 7. Promotion gates

These are combined **release/promotion** gates, not a single verdict on every
subsystem. Catalog/artifact and serving-integration results may be accepted and
preserved in their own scopes while model qualification remains open. The
model-library path cannot become a wizard/default distribution policy until all
applicable scopes pass together. A subsystem pass never changes profile
`STATUS`, guided exposure, or the default storage path by itself.

The combined promotion claim first requires these SSH identity controls:

```text
[x] Confirmed topology records trusted SSH host-key fingerprints per node
[x] Every SSH-over-RoCE connection uses HostKeyAlias with strict verification
[x] Doctor validates the alias, host key, node ID, and selected endpoint binding
[x] Changed host keys require explicit, operator-confirmed re-enrollment
[x] Deterministic endpoint-drift, key-rotation, and wrong-node selftests pass
```

These are blockers, not optional hardening. All five passed deterministic and
three-node physical checks on 2026-08-10; see
`results/model-library/topology-ssh-trust-gate-20260810.json`.

The accepted symlink design replaces the former owner-materialization blocker.
Promotion now requires this identity/lifecycle evidence:

```text
[x] Content-addressed validation-bundle schema and live profile binding are enforced
[x] Deterministic candidate tooling refuses trusted roots and cannot claim authority
[x] Repo release provides the first real lab-issued seal and complete validation bundle (`qwen3-1.7b`)
[x] First sealed one-node profile passes catalog, preparation, launch, labels, smoke, and witness evidence
[x] Sealed replicated acquisition pins the reviewed commit and full-verifies every materialized rank
[x] Sealed replicated readiness/launch uses a rank-local witness, exact snapshot, read-only repository view, and identity labels
[x] The issued flagship `deepseek-v4-flash` seal/bundle passes applicable post-issuance physical identity/lifecycle evidence
[x] Catalog/preparation compare exact model, commit, and manifest
[x] Launch validates witness (or full-verifies drift) and passes exact snapshot path
[x] Home-rank preparation creates the durable-home symlink/view, not a hot copy
[x] Serve-time metadata witness covers the canonical target and exact file set
[x] Witness drift visibly full-verifies against the expected seal or fails
[x] Active-use durable-home removal guard passes
[x] Serving ranks receive read-only exact-snapshot views in physical launch evidence
[x] Hot purge and force-unpin no-follow behavior passes the physical gate
[x] Read-only health inventories cached catalog, primary state, and every confirmed rank
[x] Legacy schema-1/2 removal is repair-ID-bound, confirmation-gated, and no-follow
[x] Warm-home pin/restart reports its durable-home dependency honestly
[x] Exact all-rank admission charges durable-home as zero and sealed-hot by manifest bytes
[x] Flagship-sized non-home admission preserves the default reserve on every selected rank
```

The active-use guard has deterministic coverage for exact target shape,
all-state hot dependencies, stopped/running managed-container references,
unobservable-node failure, lifecycle locking, metadata drift, last-home
acknowledgement, and exact no-follow deletion. It passed a three-node physical
gate using disposable synthetic repositories on 2026-08-11; no production home
was removed. See
`results/model-library/model-library-home-removal-guard-20260811.json`.
The witness checks above have deterministic control-plane coverage. The
legacy-unsealed two-node Qwen canary also passed the physical symlink,
both-rank witness, read-only launch, pin/restart, mismatch, and no-follow purge
gate on 2026-08-11. The separately issued one-node diagnostic Qwen profile then
passed catalog resolution, full-hash preparation without an override,
exact-snapshot read-only launch, identity labels, smoke, cleanup, and a
zero-byte unchanged witness using the reviewed seal/bundle.
The non-mutating DeepSeek admission gate then passed exact home-zero/non-home
manifest accounting, the default filesystem reserve, an explicit hard-cap
refusal, and unchanged hot ownership. See
`results/model-library/model-library-hot-budget-admission-gate-20260811.json`.
The earlier lifecycle, removal, and admission artifacts do not issue or
retroactively acquire an identity. The issued DeepSeek identity subsequently
passed the applicable two-node physical enforcement gate: durable-home symlink
on the selected home rank, eight-stream sealed-hot on the other serving rank,
full verification of the exact manifest on both views, zero-byte unchanged
witnesses, exact read-only launch with matching labels, smoke, and no-follow
cleanup. The lab retained two pre-existing complete durable caches and used a
temporary primary selection, so the result does not prove the one-durable-home
steady state. Persistent exact-revision selection and guarded reconciliation
were implemented afterward, passed deterministic tests, and then passed a
three-node physical repeat using only disposable synthetic HF-layout
repositories. The repeat proved pre-selection and selected-primary refusal,
refresh preservation, exact non-primary deletion, one-home catalog state, and
sibling preservation. The two existing DeepSeek durable copies were not removed
in that disposable gate. The real lab duplicate was subsequently reconciled to
rank 1 as the one persistent durable home. A clean two-rank repeat then passed
eight-stream SSH-over-RoCE preparation, exact full verification,
durable-home/sealed-hot placement, zero-byte witnesses, read-only exact-snapshot
launch, warmup, completion smoke, owned stop, hot purge, and return to one
durable copy. The subsequent exact-GA same-boot strict-determinism gate failed:
profile-default DSpark k=5 produced 11/30 exact texts and 4/30 fully identical
records, while a forced no-spec diagnostic improved to 26/30 and 25/30 without
passing strict identity. Sustained soak was not run; no profile or path was
promoted.
See
`results/model-library/model-library-primary-selection-reconciliation-gate-20260812.json`
and
`results/model-library/deepseek-v4-flash-one-home-gate-20260812.json`.
Read-only health and repair-ID-bound legacy-hot removal subsequently passed a
three-node physical gate using only tiny synthetic schema-1 instances. It
proved every-rank inventory, stopped-container and pinned blockers, local and
remote repair, no-follow/sibling preservation, and continued health attention
for preserved untracked content. The affected exact disposable-home removal
subset also passed. No real hot entry, durable home, or DeepSeek duplicate was
changed. See
`results/model-library/model-library-health-legacy-repair-gate-20260812.json`.
Failed or incomplete evidence is not rewritten because an architectural
blocker changed.

---

## 8. Remaining deferred work

- Physical serving-integration repeat for the new remote one-node wizard path;
  deterministic orchestration is implemented, while existing one-node and
  flagship artifacts cover only their recorded placements
- Machine-readable qualification dimensions; current scope separation is a
  documentation and evidence-interpretation contract
- Issue remaining supported profiles over time
- Per-rank runtime-source/witness labels and unmanaged-reader observability
- Stable public guarantees for machine-readable JSON schemas other than the
  health schema-1 contract
- Review the explicit `--allow-unvalidated` experiment policy before promotion
- Complete physical promotion matrix, including time-to-healthy, interruption,
  dependency loss, restart, determinism, and sustained soak
- Optional durable-replica and failover policy on distinct failure domains
- Rank-sharded checkpoints (`sharded_state`) as a later requirement-B lever
- Dedicated storage-node topology for very large N

---

## 9. Decision log

| Date | Decision |
|---|---|
| 2026-08-08 | Requirements **A** (storage), **B** (load time), **C** (reliability) co-equal. |
| 2026-08-08 | Product shape: federated warm library + optional cold + hot staging + pins + prepare (copy\|fabric) + release before independent serve. |
| 2026-08-08 | Temporary hot disk allowed; pins bounded by disk budget. The original generic restart-without-home goal is superseded by ADR 0001 for warm-home preparation. |
| 2026-08-08 | Copy = non-RoCE control-path transfer; fabric = RoCE preparation transport; fabric B bar = beat copy. |
| 2026-08-08 | Cold optional; resolve warm → cold? → HF; cold preferred over HF when configured; adopt and stage-only both allowed. |
| 2026-08-08 | Implemented optional cold tier: scan Official Models + hub layouts, resolve warm→cold fall-through, cold adopt, cold stage-only (`scripts/model-library.sh cold *`). |
| 2026-08-08 | Scan all hub trees; label validated vs unvalidated; duplicates recommend cleanup tool. |
| 2026-08-08 | New download placement: most free space + `--node` override (recommended default). |
| 2026-08-08 | Release after hot verified, before launch (default independence claim). |
| 2026-08-08 | Persisted as this document; exploratory option-noise archived to `docs/archive/WEIGHT_MATERIALIZE_DESIGN.md`. |
| 2026-08-08 | Implemented federated catalog refresh/resolve, copy preparation, budgeted hot staging, pin/unpin/purge, and `library-hot` launch/stop hooks. |
| 2026-08-08 | Implemented short-lived NFS/RDMA fabric preparation with explicit release; retained live `--weight-source fabric` as a separate experiment. |
| 2026-08-09 | Added copy-versus-fabric preparation measurement and reduced avoidable setup/home-copy cost; fabric remains ineligible for a fast-path claim unless it beats copy. |
| 2026-08-10 | Implemented and physically verified topology schema-2 SSH identity binding across three nodes: exact transport address, stable `HostKeyAlias`, strict enrolled-key verification, pairwise-rail checks, and explicit re-enrollment on key change. |
| 2026-08-10 | Retired raw exploratory transcripts to gitignored `/experiments/`; only distilled decisions and sanitized evidence belong in publishable history. |
| 2026-08-10 | Upgraded library-hot to schema-2 full SHA-256 snapshot seals; same-size corruption now fails full verification. |
| 2026-08-10 | Full-model counterbalanced trials passed the performance gate: 8-stream SSH-over-RoCE was 1.898x the control-path median; 16 streams did not improve the median. |
| 2026-08-10 | **No promotion:** keep replicated guided defaults. SSH identity passed; production hot-budget policy, strict DeepSeek determinism, and sustained soak remain open. |
| 2026-08-10 | **ADR 0001 accepted:** rule out home-rank hot materialization. Use a validated durable-home symlink/view, sealed hot only on non-home ranks, lab-issued expected identity, and a serve-time metadata witness backed by full verification. |
| 2026-08-12 | **ADR 0002 accepted:** separate catalog/artifact, serving-integration, model-qualification, and release/promotion evidence. Preserve valid subsystem results unless a causal dependency invalidates them; combined promotion still requires every applicable scope. |
| 2026-08-13 | **ADR 0003 accepted:** when an operator explicitly selects reviewed-profile experimental preparation, use topology-bound eight-stream SSH-over-RoCE with no fallback. Catalog refresh does not create the required durable home, the replicated fresh-cluster/guided path remains unchanged, and transport selection does not promote `library-hot` or waive model/release gates. |
| 2026-08-10 | Implemented catalog schema 2 and hot schema 3 expected-seal enforcement: reviewed seal reference, exact immutable commit selection, expected-versus-observed manifest comparison, seal-bound hot identity, exact snapshot launch path, labels/startup provenance, and non-overridable mismatch. No real profile seal was issued. |
| 2026-08-10 | Implemented rank-local serve-witness schema 1: preparation full-verifies before atomic witness creation; unchanged launch hashes zero model bytes; missing/invalid/drifted metadata visibly falls back to full SHA-256 and refreshes only on a stable match. |
| 2026-08-11 | Qwen 1.7B physically passed durable-home symlink, non-home sealed-hot, both-rank witness fallback, exact-snapshot read-only launch, warm-home pin/restart, mismatch fail-closed, and force-unpin no-follow purge. The artifact is `legacy-unsealed`; release identity and promotion remain open. |
| 2026-08-11 | Guarded durable-home removal probes every confirmed node, blocks all managed hot/container references, serializes supported lifecycle commands, requires explicit last-home acknowledgement, and deletes only an unchanged exact single-revision repository. |
| 2026-08-11 | The durable-home removal guard passed deterministic tests and a three-node physical gate using disposable synthetic repositories; the real Qwen home and adjacent repository content were preserved. |
| 2026-08-11 | Implemented exact all-rank hot admission: sealed-hot ranks charge manifest bytes, durable-home views charge zero, the default preserves max(64 GiB, 5% filesystem capacity), optional hard caps remain explicit, and blocked capacity never auto-evicts or falls back. |
| 2026-08-11 | The non-mutating flagship gate inventoried every confirmed rank, then passed on both DeepSeek-selected ranks: 166,898,661,074 bytes on sealed-hot, zero on durable-home, default reserve preserved, one-byte hard cap blocked, and hot ownership unchanged. |
| 2026-08-11 | Implemented content-addressed validation-bundle schema 1 and fail-closed profile-load verification across exact model identity, declared external-artifact identities/digests, lab provenance/evidence, digest-pinned image, normalized runtime configuration, memory contract, and geometry. No production seal or bundle was issued. |
| 2026-08-11 | Added a maintainer-only release identity service: `model_identity.py` owns the trust schemas, while `model-release` hashes an exact commit and atomically assembles deterministic unreviewed candidates below a protected output boundary. It cannot issue, publish, edit profiles, or change status; no production seal or bundle was issued. |
| 2026-08-11 | Issued the first reviewed lab identity for the one-node diagnostic `qwen3-1.7b`: exact commit `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, complete manifest `775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`, seal `ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94`, and bundle `9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380`. Post-issuance `library-hot` preparation/launch matched physically; this does not seal the two-node profile or promote the path. |
| 2026-08-11 | Extended reviewed expected-seal enforcement to replicated HF caches: exact-commit download, full verification after every materialization, rank-local witness fast path with visible rehash-on-drift, exact-snapshot read-only launch, and revision/seal/bundle labels. The issued Qwen canary physically passed full verification, zero-byte unchanged witness, launch labels/read-only view, smoke, and cleanup; exact-revision acquisition and post-copy verification passed deterministically. Unsealed profiles retain legacy behavior; live mount remains unbound; no profile or storage path was promoted. |
| 2026-08-12 | Issued the second reviewed lab identity for flagship `deepseek-v4-flash`: exact GA commit `7872f01b1d1fe23eabc4c98b48bffcef5a386062`, complete manifest `27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`, seal `1ba9ca8e3c34a9143588cc1315474e9cca0724351f0856caed5bb1116b89555a`, and bundle `8fda1d93c5e08cbba18df5b26b0632354c6559ab939d3763dbdbdf38ead6b236`. Candidate reproduction and trusted verification matched; physical enforcement was still pending at issuance and is superseded by the next decision row. Neither storage promotion nor bit-identical output was claimed. |
| 2026-08-12 | The issued DeepSeek identity passed applicable two-node physical enforcement: rank-local durable-home/sealed-hot views, full SHA-256 verification on both ranks, zero-byte unchanged witnesses, exact read-only snapshot launch with matching identity labels, warmup/smoke, and no-follow cleanup. Duplicate durable caches and temporary primary selection were disclosed, so one-durable-home steady state, persistent primary workflow, promotion, bit-identical output, and sustained soak remain unclaimed. |
| 2026-08-12 | Catalog schema 2 gained persistent exact-revision primary selection, refresh preservation, stale-selection fail-closed behavior, and operator-confirmed non-primary reconciliation guidance. Selected-primary removal is blocked until the survivor is changed. Deterministic tests pass; existing DeepSeek duplicate bytes were not removed, so one-home physical steady state remains unclaimed. |
| 2026-08-12 | Persistent-primary targeting passed a three-node physical repeat using disposable synthetic HF-layout repositories: direct removal refused before selection, the exact selection survived refresh, selected-primary removal refused, only the non-primary home was deleted, the catalog reached one selected durable home, and an adjacent repository remained intact. The existing DeepSeek duplicate was not changed; promotion, strict determinism, and soak remain open. |
| 2026-08-12 | Added stable read-only health schema 1, Doctor warning integration, and repair-ID-bound schema-1/2 legacy-hot removal. The service uses cached catalog and metadata observations only; it does not reconcile the existing DeepSeek duplicate or mutate real hot/model state. |
| 2026-08-12 | Read-only health and guarded legacy-hot removal passed a three-node physical gate with disposable schema-1 instances, including remote repair, stopped-container and pin blockers, no-follow/sibling preservation, preserved-untracked attention, and the exact disposable-home removal subset. No production state was changed. |
| 2026-08-12 | The existing DeepSeek GA duplicate was reconciled to one persistent rank-1 durable home. A clean physical repeat passed eight-stream SSH-over-RoCE preparation to rank 0 sealed-hot, rank-1 durable-home view, full exact-manifest verification, zero-byte witnesses, read-only launch, eight warmup phases, completion smoke, owned stop/purge, and final healthy one-home state. Strict determinism, sustained soak, and promotion remain open. |
| 2026-08-12 | The exact reviewed DeepSeek GA identity failed the strict same-boot `library-hot` determinism gate. Profile-default DSpark k=5 produced 11/30 exact texts and 4/30 identical records; a forced no-spec diagnostic improved to 26/30 and 25/30 but still failed strict identity. Exact seal, image, geometry, and runtime views were held constant, the clean one-home state was restored, and no fatal runtime signature appeared. Preserve this failed evidence; do not attribute the current result only to the retired preview profile, run sustained soak as if the blocker passed, or promote the path without an explicit determinism-policy decision and new evidence. |
| 2026-08-12 | Models & storage gained an explicit confirmation-gated catalog refresh that delegates to the existing atomic all-rank service. Browsing and health rechecks remain read-only; refresh is never automatic and does not prepare, launch, retain, repair, or delete models. |
| 2026-08-12 | Exact model detail gained confirmation-gated experimental preparation for reviewed-seal tested serving profiles. It delegates to eight-stream SSH-over-RoCE copy with no fallback and re-renders health; it does not launch, expose unvalidated bypass, change replicated defaults, or claim promotion. |
| 2026-08-13 | Added reviewed-profile `home add`: target-side exact-commit download into plan-owned same-filesystem staging, any-confirmed-rank placement for one-node profiles, exact geometry for multi-node profiles, most-free-space selection with explicit in-geometry override, all-rank duplicate recheck, full expected-manifest verification, and atomic publication of one durable home. Catalog refresh, hot preparation, and launch remain separate; deterministic contracts pass and physical acquisition evidence remains pending. |
| 2026-08-13 | Reviewed acquisition passed its three-node physical catalog/artifact gate with sealed Qwen 1.7B. Guarded last-home removal, interrupted remote download cleanup, explicit rank-2 acquisition, automatic most-free-space rank-2 acquisition, full reviewed-manifest verification, atomic publication, explicit catalog refresh, and final one-home/no-hot state passed. The gate also closed target discovery for Pulsar's managed HF CLI venv. It did not prepare, launch, qualify, or promote the model or storage path. |
| 2026-08-13 | The serving wizard gained an explicit experimental distributed-catalog choice for eligible reviewed profiles while preserving replicated weights as the first/default option. Readiness is rechecked after optional preparation, launch remains separately confirmed, one-node catalog serving is constrained to its durable-home rank, and stop purges unpinned hot views by default while explicit pin retains them. Deterministic contracts pass; no new physical, model-qualification, or promotion claim is made. |
