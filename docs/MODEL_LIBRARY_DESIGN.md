# Model library, activate, and load

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

| Field | Value |
|---|---|
| Authority | Accepted architecture; current implementation remains experimental |
| Status | Implemented experiment (not promoted); expected-seal/exact-revision enforcement, content-addressed validation-bundle verification, untrusted release-candidate assembly, serve-time witness, and guarded durable-home removal landed; reviewed issuance pending |
| Settled | 2026-08-08; home-view and validation-identity policy revised 2026-08-10 |
| Supersedes (exploration) | [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md) |
| Accepted decision | [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md) |
| Live experimental ops | [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) |
| Current-system peer review | [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md) |
| Default today | Replicated local Hugging Face caches |
| Experimental today | `scripts/model-library.sh` catalog/cold/activate/hot/pin workflows; `--weight-mode library-hot`; `--weight-source fabric` live NFSv4.2/RDMA; and maintainer-only `scripts/model-release.sh` candidate assembly |

**Current implementation integrity boundary:** catalog schema 2 accepts an
optional reviewed `models/seals/*.json` trust root and binds a tested profile to
its exact Hugging Face commit. Hot schema 3 records the expected seal,
validation-bundle ID, and locally observed revision/manifest. Activation
full-hashes every rank and atomically writes a rank-local witness before
publishing ready state. Launch first rechecks the live profile or controller
expectation, then uses the witness when canonical view and file metadata are
unchanged. A missing, invalid, or drifted witness is visible and causes a stable
full SHA-256 verification; success atomically refreshes it, while a content
mismatch fails without refresh. Launch still passes the exact
`snapshots/<revision>` path to vLLM. Existing profiles have no issued seals and
remain `legacy-unsealed`; they require explicit `--allow-unvalidated` for
model-library experiments. Catalog refresh discovers complete snapshot commit
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
profile. No production profile has an issued seal or bundle yet.

`scripts/model_identity.py` is the single local owner of the profile-contract,
validation-bundle, and expected-seal schemas. `scripts/model-release.sh` can
hash an explicitly selected commit and assemble deterministic candidate
documents only under an untrusted output boundary. Candidates declare no
authority, cannot write the reviewed model directories, cannot edit profiles,
and do not affect validation status.

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

**Promotion bar for a transfer advertised as a RoCE fast path**
(`ssh-roce` or one-shot `nfs-rdma`): it must beat `ssh-control` on the same
model and topology. Approaching pure local-replica cold start is desirable but not the
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

Evidence, labels, and future schemas should record these axes independently.
In particular, SSH/TCP over a RoCE interface is `ssh-roce`; it is not the live
NFS/RDMA runtime mode merely because both use the fabric NIC.

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
| **Validated** | Target contract: the observed exact revision/manifest matches the lab-issued seal in a tested validation bundle |
| **Present (unvalidated)** | Complete-looking hub tree on a Spark; Pulsar has **not** validated serving that model |
| **Partial / invalid** | Incomplete or not sealable — not a usable home |

**Catalog visibility ≠ Pulsar serving guarantee.** Wizard and default serve
paths remain gated on validated profiles. Unvalidated presence is for disk
awareness and advanced/explicit flows only.

**Current implementation:** a tested profile without
`EXPECTED_MODEL_SEAL` is labeled `legacy-unsealed`. A reviewed seal under
`models/seals/` makes catalog schema 2 select only the declared immutable
commit and label it `expected-unverified`; activation then computes the observed
manifest and must reach `match`. `catalog list --validated` includes only
entries carrying a reviewed expected seal, never legacy repository-ID-only
claims. No production profile seal is issued in this release.

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

Stage-only hot is fully materialized, so retaining or pinning it can allow a
restart without cold. Warm-home activation is different by design: its home
rank uses a zero-copy symlink into the durable HF cache, so retaining that hot
instance does not make it independent of the durable home.

An atomic same-filesystem move is allowed only as an explicit **adopt** into a
managed durable root after the observed content matches the expected seal. It
is never an activation shortcut into purgeable hot storage. Adoption must keep
home removal and rollback behavior explicit.

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
| Unpinned stop | Purged | Re-activate before restart | Required as activation source |
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

Activate, cold stage-only, pin, and budget inventory collect an exact
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
- unpinned restart re-activates non-home ranks from the durable home;
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
seal projection and the observed seal. Activation compares model ID, immutable
commit, and manifest ID, then full-verifies every rank and atomically creates
that rank's `.pulsar/witness.json` before publishing ready state. The witness
schema binds hot and validation identity, canonical hub/snapshot targets,
filesystem device/inode identity, the exact logical file set, and per-file
device/inode/size/`mtime_ns`/`ctime_ns`. Launch validates the current
profile/seal or controller expectation before consulting it. An unchanged
witness hashes zero model bytes; missing, malformed, or drifted metadata emits a
visible fallback, full-verifies the sealed manifest under stable metadata, and
atomically refreshes the witness only on success. Launch then passes the exact
snapshot path. For `identity_status=match`, that manifest is bound to the
lab-issued expected seal. A `legacy-unsealed` experiment gets only
activation-manifest integrity and never becomes validated through the witness.
The seal points one-way to a content-addressed schema-1 validation bundle.
Profile load verifies the bundle ID, exact primary model projection,
provenance/evidence parity, declared external-artifact identities/digests, and
normalized live profile/image/geometry binding before catalog, activation, or launch may use
the sealed claim. The bundle deliberately omits the seal ID to avoid a hash
cycle. No real profile bundle is issued in the repository yet.

The maintainer-only release service now builds the same schema through
`scripts/model_identity.py`, but its output is explicitly `unreviewed` with
`authority=none` and `promotion=not-authorized`. Internal consistency is not
issuance; a reviewed pull request and applicable lab evidence remain the trust
boundary.

### 4.6 Activate transfers

Transfer moves bytes only to ranks whose runtime source is `sealed-hot`.
The warm-home rank uses its existing `durable-home` view.

| Transfer | Current CLI shape | Network/path claim | Promotion role |
|---|---|---|---|
| `ssh-control` | copy backend over confirmed control SSH | Management LAN | Baseline |
| `ssh-roce` | copy backend with `--transport ssh-roce` | SSH/TCP pinned to confirmed RoCE endpoint | Candidate fast path |
| `nfs-rdma` | fabric backend, then release | Short-lived NFSv4.2/RDMA transfer plane | Separate candidate |
| `live-mount` | `--weight-source fabric` | Long-lived NFSv4.2/RDMA runtime dependency | Separate experiment |

Neither transfer replaces NCCL inference traffic. No candidate may silently
fall back to control transfer, TCP NFS, replicated pulls, or a different
runtime source.

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
| Resolve / activate | Expected seal plus the selected durable/cold source |
| Launch after ready + release | Durable home on its rank; sealed hot on non-home ranks |
| Running inference | Rank files may no longer be read once resident, but declared storage dependencies remain honest |
| Warm-home restart with pin | Durable home plus pinned non-home hot; no transfer/catalog refresh |
| Warm-home restart without pin | Durable home plus re-activation |
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
Pulsar catalog, activation, launch, readiness, download, fabric, and removal
commands. Missing topology, unreachable nodes, unavailable Docker, or a
contradictory observation contract aborts planning. Unreadable or legacy hot
metadata remains a visible blocker instead of proving absence. This guard
cannot discover an unmanaged process or container created outside Pulsar
labels; such use remains an operator responsibility and must be stopped before
removal.

---

## 5. Relationship to current and experimental paths

| Path | Role under this direction |
|---|---|
| Replicated `pull-weights` + local launch | **Remains default** until a library+activate path earns promotion |
| Live `--weight-source fabric` | **Experimental** proof/ops path; long-lived mount under vLLM is **not** the agreed product identity |
| Site cold path confs | Optional cold tier; keep working |
| Topology rails and NFS/RDMA helpers | Reused by fabric **activate**; model-library schema-3 hot state carries full SHA-256 observed content plus optional expected-seal provenance, while live-fabric configuration identity remains separate |
| Materialize-as-only-mechanism drafts | Superseded as the top-level story; activate+hot+pin is the product frame |

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
9. **Activate is first-class and measured** — end-to-end start-to-healthy,
   integrity, and recovery matter more than peak transport bandwidth.
10. **Transport is not product identity** — distinguish `ssh-control`,
    `ssh-roce`, one-shot `nfs-rdma`, and live mount.
11. **No silent policy changes** — never change transport, runtime source,
    geometry, or replica count as a fallback.
12. **Validated vs present labels protect claim hygiene**; duplicates recommend
    cleanup and never cause silent multi-home serve.
13. **Prefer boring recovery** — explicit verify, re-activate, and relaunch over
    hidden mount or replica behavior.
14. **Raw experiments stay local** under gitignored `/experiments/`; durable
    decisions belong in reviewed design/ADR/runbook docs and sanitized evidence.

---

## 7. Promotion gates

The model-library path cannot become a wizard/default distribution policy until
all of these SSH identity controls are implemented and evidenced:

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
[ ] Repo release provides a real lab-issued seal and complete validation bundle
[x] Catalog/activation compare exact model, commit, and manifest
[x] Launch validates witness (or full-verifies drift) and passes exact snapshot path
[x] Home-rank activation creates the durable-home symlink/view, not a hot copy
[x] Serve-time metadata witness covers the canonical target and exact file set
[x] Witness drift visibly full-verifies against the expected seal or fails
[x] Active-use durable-home removal guard passes
[x] Serving ranks receive read-only exact-snapshot views in physical launch evidence
[x] Hot purge and force-unpin no-follow behavior passes the physical gate
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
legacy-unsealed Qwen canary also passed the physical symlink, both-rank witness,
read-only launch, pin/restart, mismatch, and no-follow purge gate on 2026-08-11.
The non-mutating DeepSeek admission gate then passed exact home-zero/non-home
manifest accounting, the default filesystem reserve, an explicit hard-cap
refusal, and unchanged hot ownership. See
`results/model-library/model-library-hot-budget-admission-gate-20260811.json`.
None of these artifacts issues a real seal or replaces strict DeepSeek
determinism or sustained soak. Failed or incomplete evidence is not rewritten
because an architectural blocker changed.

---

## 8. Remaining deferred work

- Promotion into the wizard or other guided defaults
- Review and issue a real-profile seal and complete immutable validation bundle
- Per-rank runtime-source/witness labels and unmanaged-reader observability
- Stable public guarantees for machine-readable JSON schemas
- Destructive duplicate-home cleanup beyond the current recommendation flow
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
| 2026-08-08 | Product shape: federated warm library + optional cold + hot staging + pins + activate (copy\|fabric) + release before independent serve. |
| 2026-08-08 | Temporary hot disk allowed; pins bounded by disk budget. The original generic restart-without-home goal is superseded by ADR 0001 for warm-home activation. |
| 2026-08-08 | Copy = non-RoCE control-path transfer; fabric = RoCE activate transport; fabric B bar = beat copy. |
| 2026-08-08 | Cold optional; resolve warm → cold? → HF; cold preferred over HF when configured; adopt and stage-only both allowed. |
| 2026-08-08 | Implemented optional cold tier: scan Official Models + hub layouts, resolve warm→cold fall-through, cold adopt, cold stage-only (`scripts/model-library.sh cold *`). |
| 2026-08-08 | Scan all hub trees; label validated vs unvalidated; duplicates recommend cleanup tool. |
| 2026-08-08 | New download placement: most free space + `--node` override (recommended default). |
| 2026-08-08 | Release after hot verified, before launch (default independence claim). |
| 2026-08-08 | Persisted as this document; exploratory option-noise archived to `docs/archive/WEIGHT_MATERIALIZE_DESIGN.md`. |
| 2026-08-08 | Implemented federated catalog refresh/resolve, copy activate, budgeted hot staging, pin/unpin/purge, and `library-hot` launch/stop hooks. |
| 2026-08-08 | Implemented short-lived NFS/RDMA fabric activate with explicit release; retained live `--weight-source fabric` as a separate experiment. |
| 2026-08-09 | Added copy-versus-fabric activation measurement and reduced avoidable setup/home-copy cost; fabric remains ineligible for a fast-path claim unless it beats copy. |
| 2026-08-10 | Implemented and physically verified topology schema-2 SSH identity binding across three nodes: exact transport address, stable `HostKeyAlias`, strict enrolled-key verification, pairwise-rail checks, and explicit re-enrollment on key change. |
| 2026-08-10 | Retired raw exploratory transcripts to gitignored `/experiments/`; only distilled decisions and sanitized evidence belong in publishable history. |
| 2026-08-10 | Upgraded library-hot to schema-2 full SHA-256 snapshot seals; same-size corruption now fails full verification. |
| 2026-08-10 | Full-model counterbalanced trials passed the performance gate: 8-stream SSH-over-RoCE was 1.898x the control-path median; 16 streams did not improve the median. |
| 2026-08-10 | **No promotion:** keep replicated guided defaults. SSH identity passed; production hot-budget policy, strict DeepSeek determinism, and sustained soak remain open. |
| 2026-08-10 | **ADR 0001 accepted:** rule out home-rank hot materialization. Use a validated durable-home symlink/view, sealed hot only on non-home ranks, lab-issued expected identity, and a serve-time metadata witness backed by full verification. |
| 2026-08-10 | Implemented catalog schema 2 and hot schema 3 expected-seal enforcement: reviewed seal reference, exact immutable commit selection, expected-versus-observed manifest comparison, seal-bound hot identity, exact snapshot launch path, labels/startup provenance, and non-overridable mismatch. No real profile seal was issued. |
| 2026-08-10 | Implemented rank-local serve-witness schema 1: activation full-verifies before atomic witness creation; unchanged launch hashes zero model bytes; missing/invalid/drifted metadata visibly falls back to full SHA-256 and refreshes only on a stable match. |
| 2026-08-11 | Qwen 1.7B physically passed durable-home symlink, non-home sealed-hot, both-rank witness fallback, exact-snapshot read-only launch, warm-home pin/restart, mismatch fail-closed, and force-unpin no-follow purge. The artifact is `legacy-unsealed`; release identity and promotion remain open. |
| 2026-08-11 | Guarded durable-home removal probes every confirmed node, blocks all managed hot/container references, serializes supported lifecycle commands, requires explicit last-home acknowledgement, and deletes only an unchanged exact single-revision repository. |
| 2026-08-11 | The durable-home removal guard passed deterministic tests and a three-node physical gate using disposable synthetic repositories; the real Qwen home and adjacent repository content were preserved. |
| 2026-08-11 | Implemented exact all-rank hot admission: sealed-hot ranks charge manifest bytes, durable-home views charge zero, the default preserves max(64 GiB, 5% filesystem capacity), optional hard caps remain explicit, and blocked capacity never auto-evicts or falls back. |
| 2026-08-11 | The non-mutating flagship gate inventoried every confirmed rank, then passed on both DeepSeek-selected ranks: 166,898,661,074 bytes on sealed-hot, zero on durable-home, default reserve preserved, one-byte hard cap blocked, and hot ownership unchanged. |
| 2026-08-11 | Implemented content-addressed validation-bundle schema 1 and fail-closed profile-load verification across exact model identity, declared external-artifact identities/digests, lab provenance/evidence, digest-pinned image, normalized runtime configuration, memory contract, and geometry. No production seal or bundle was issued. |
| 2026-08-11 | Added a maintainer-only release identity service: `model_identity.py` owns the trust schemas, while `model-release` hashes an exact commit and atomically assembles deterministic unreviewed candidates below a protected output boundary. It cannot issue, publish, edit profiles, or change status; no production seal or bundle was issued. |
