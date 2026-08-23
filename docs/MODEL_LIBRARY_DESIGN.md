# Model library preparation and serving

> **Authority: canonical model-library architecture.**
> The storage, identity, dependency, and lifecycle decisions in this document
> are normative for future implementation. Sections explicitly labeled
> **current implementation** describe the implemented maturity boundary and do not
> override the accepted target. The model library is the only
> weight-distribution mechanism
> ([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md));
> the replicated guided default was retired by that decision. Operator
> commands and current
> limitations are documented in [OPERATIONS.md](./OPERATIONS.md); the distinct
> live NFS/RDMA serving path is rejected by
> [ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md); historical
> notes remain in [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).
>
> Exploratory drafts and rejected-or-deferred option lists are archived under
> [docs/archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md).
> Current operator behavior and the live profile catalog are documented in
> [OPERATIONS.md](./OPERATIONS.md) and [MODELS.md](./MODELS.md).
> Term finder: [GLOSSARY.md](./GLOSSARY.md).
> The maintainer-only, candidate-stage release workflow is documented in
> [MODEL_RELEASE.md](./MODEL_RELEASE.md).
> The durable rationale for the home-view and validation-identity decision is
> [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md).
> Qualification scope and evidence reuse are governed by
> [ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md).
> The transport policy for explicitly selected reviewed multi-rank preparation is
> recorded in
> [ADR 0003](./decisions/0003-explicit-model-preparation-transport.md).
> Model Serving Release identity, validation contracts, decision statuses, and
> the separation of distribution provenance from release identity are governed
> by [ADR 0004](./decisions/0004-model-serving-release-validation.md).
> Live NFS/RDMA serving is rejected by
> [ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md).
> Ordinary stop retains unpinned prepared views
> ([ADR 0007](./decisions/0007-ordinary-stop-retains-unpinned-hot-views.md)).

| Field | Value |
|---|---|
| Authority | Accepted architecture; the model library is the only weight-distribution mechanism ([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)); every scope (two-rank sealed, one-rank, legacy-unsealed) is supported |
| Status | Bounded two-rank GA completed 2026-08-16 for reviewed profiles; ADR 0006 (2026-08-19) then removed the replicated and fabric paths and promoted every library scope to supported by decision, recording the open gates as accepted risks. ADR 0007 (2026-08-20) changed ordinary stop to retain unpinned prepared views. The exact DeepSeek release's strict-determinism failure remains a Model Serving Release result, not a catalog/distribution invalidation. |
| Settled | 2026-08-08; home-view and validation-identity policy revised 2026-08-10; first reviewed identity issued 2026-08-11; flagship identity issued and qualification boundaries revised 2026-08-12; Model Serving Release policy accepted 2026-08-14; bounded two-rank `library-hot` GA completed 2026-08-16; library-only distribution accepted 2026-08-19 (ADR 0006); ordinary-stop retention accepted 2026-08-20 (ADR 0007) |
| Supersedes (exploration) | [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md) |
| Accepted decisions | [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md); [ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md); [ADR 0003](./decisions/0003-explicit-model-preparation-transport.md); [ADR 0004](./decisions/0004-model-serving-release-validation.md); [ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md); [ADR 0006](./decisions/0006-model-library-only-weight-distribution.md); [ADR 0007](./decisions/0007-ordinary-stop-retains-unpinned-hot-views.md); [ADR 0008](./decisions/0008-breaking-compatibility-window.md); [ADR 0009](./decisions/0009-no-launch-trust-mode-axis.md); [ADR 0010](./decisions/0010-operator-consumes-catalog.md); [ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md) |
| Retired live NFS serving | [ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md); historical notes in [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) |
| Current operator/catalog state | [OPERATIONS.md](./OPERATIONS.md), [MODELS.md](./MODELS.md) |
| Mechanism today | The model library, for every profile (ADR 0006): one exact durable occupancy home, exact home symlink, working replicas (`sealed-hot`) on non-home ranks, portable occupancy via `home relocate` (ADR 0011), fixed eight-stream SSH-over-RoCE preparation for reviewed multi-rank profiles, exact restart, persisted replacement recovery, owned cleanup, and ordinary-stop retain of unpinned working replicas (ADR 0007) |
| Supported today | Two-rank sealed (physical GA evidence, 2026-08-16); one-rank and legacy-unsealed (supported by ADR 0006 decision). Current serving ingress is an exact Hugging Face `model_id@commit`; a local-directory import is a future ADR, not a launch token. |
| Accepted risks / pending (ADR 0006) | One-rank physical serving-integration evidence; source-attested unsealed Hugging Face `home add` kept as core catalog/artifact ingress (SIM-03, 2026-08-22) with remote-target / asymmetric-credentials as physical validation follow-ups; Hugging Face is the only current ingress format (local-directory import needs its own ADR); occupancy loss recovers via ADR 0011 relocate/restore (receipt-indexed NFS archive implementation pending); maintainer-only release planning/capture tooling and the supervised `pulsar-model-onboarding` skill remain maintainer scope |
| Retired paths | Live `live-remote-readonly` serving (ADR 0005); the replicated per-node cache path and one-shot `nfs-rdma` prepare (ADR 0006). Launch fails closed; historical `results/weight-fabric/` evidence is superseded and not promoted. |

**Current implementation integrity boundary:** catalog schema 2 accepts an
optional reviewed `models/seals/*.json` trust root and binds a profile to
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
`legacy-unsealed`; after full verification they may be prepared without a
validation-status override. `--allow-unvalidated` is removed (ADR 0008) and
fails closed; it never bypassed a configured seal mismatch. Catalog refresh discovers complete snapshot commit
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
profile. (Sealed replicated launch enforcement, described here historically,
was removed together with the replicated path by ADR 0006; sealed enforcement
now lives entirely in home acquisition and library preparation/launch.)
The distributed library has a separate acquisition service: `home add`
observes every confirmed rank, allows a one-node profile on
any confirmed rank while preserving exact multi-node geometry, selects the
eligible candidate with the most free space unless `--node` overrides it, and downloads there
into private same-filesystem staging, rechecks that no home appeared elsewhere,
full-verifies the expected manifest for sealed content, and atomically publishes
exactly one durable HF repository. For a brand-new unsealed profile,
`--revision <selector> --plan` first resolves a complete public Git/LFS inventory
to an exact commit without downloading model bytes. Separately confirmed
execution uses the selected rank's local authentication, verifies the complete
upstream set and every SHA-256, writes an immutable site-local receipt, uses
an atomic no-replace publication, and binds that receipt to the exact
published directory. `home verify` later performs an offline full
rehash against the attached receipt. Neither path creates hot copies or refreshes the
catalog, so registration remains the operator's explicit next action. Target capability
discovery accepts the CLI on PATH or Pulsar's managed user-venv installation;
it does not move controller authentication to the selected rank.
Unsealed `home add` selection consults `refs/main` at acquisition time only.
Issuing or enforcing the DeepSeek flagship identity does not by itself change
any release status.

`scripts/model_identity.py` is the single local owner of the profile-contract,
validation-bundle, and expected-seal schemas. `scripts/model-release.sh` can
hash an explicitly selected commit and assemble deterministic candidate
documents only under an untrusted output boundary. Candidates declare no
authority, cannot write the reviewed model directories, cannot edit profiles,
and do not affect validation status.

`scripts/model_serving_release.py` now owns ADR 0004 release-descriptor and
frozen Validation Contract schema version 1. The pure module normalizes and
validates the four-part release tuple, hashes only that tuple into the stable
release ID, cross-checks recipe parallelism against supported geometry, and
freezes mandatory criteria without results or issuance metadata. It also
enforces exact same-boot comparison, reviewed provenance, privacy-safe
extensible parameters, a closed review-derived provenance criterion, and
predecessor/protocol/geometry binding for relative performance budgets.
Recognized secret, path, endpoint, and deployment-only values are rejected
recursively rather than hashed into release or contract identity. Fixed-ID
fixtures and fail-closed tests are under
`scripts/testlib/`.

Its primary-model identity is source-neutral: exact Hugging Face snapshots
retain their repository and immutable commit, while any other complete model
tree uses a public logical identity, public revision, and complete
content-addressed manifest. Neither source path nor transfer mechanism enters
the release ID. `scripts/model-serving-release-plan.sh` builds and verifies
only unreviewed release/contract candidates from a sourced profile, full
manifest, explicit runtime/hardware envelope, and frozen criteria. Planner
`verify` uses the public `load_verified_release_plan_candidate(dir)` loader.
Planner output
has no status or issuance authority and cannot target the tracked registry.

`scripts/model_validation_evidence.py` now owns the pure ADR 0004
content-addressed evidence-artifact, immutable run-record, validation-bundle,
and reviewed validation-decision schema version 1. It binds attempts to the
exact release and frozen contract, records rank-relative environment and
distribution provenance, considers every applicable observation automatically,
requires evidence-backed exclusions, recomputes measured criterion outcomes
against frozen thresholds plus required context, soak, and reviewed
comparable-predecessor lineage, rejects status disagreement, and projects only
chronologically valid, acyclic immutable supersession. It structurally checks
runtime compatibility and architecture/geometry. Every post-barrier
non-preparation attempt hash-binds its attempted criteria and accounts for each
with an observation; incomplete attempts can contribute only inconclusive
observations. Command evidence uses allowlisted programs, SHA-256-shaped
version identities, closed operations/resources, and typed criterion/site
references. Its fixed-ID and adversarial fixtures are also
under `scripts/testlib/`.

**Current implementation boundary:** the schema modules remain pure contracts.
Read-only trusted persistence and verification now live under
`models/model-serving-releases/` and
`scripts/model-serving-release-registry.sh`. That layer can load, verify, and
inspect stored objects; it does not capture evidence, issue a decision,
change recommendation policy, authorize serving, or launch a release. Its
verified inspection now supplies advisory catalog/operator projection for an
explicitly bound `MODEL_SERVING_RELEASE_ID`. Local
ADR 0004 evidence-capture candidate persistence is implemented by
`scripts/model-serving-release-capture.sh` and remains explicitly unreviewed:
it composes a verified release-plan candidate with an attempt-only spec,
independently validates the release and contract, and does not write the
tracked registry, issue a decision, persist a planner path, issue
`Untested`, or launch a release.
Closed validator-measurement documents for `compare-captures` and
`benchmark-serving` are implemented under `validate/`;
`scripts/model-serving-release-attempt.sh` composes those measurements plus
caller context into the existing attempt-only specs. Neither surface issues a
status, decision, or serving permission, and ordinary `validate/run-gates.sh`
remains a human path that does not require a release plan.
`qwen3.8-27b-fp8` binds the first reviewed ADR 0004 lineage and projects the
advisory status `Testing incomplete`; other profiles remain neutral.
Review-metadata shape checks cannot prove that
repository review or physical qualification occurred. The supervised
`pulsar-model-onboarding` skill is implemented as control-plane orchestration
around those CLIs. It collaborates at material decisions and never issues a
seal or validation decision, assigns status, binds a profile, writes the
trusted registry, promotes a path, or claims physical behavior. Current
automated mapping covers only strict same-boot and absolute
throughput/latency. An unsealed (`legacy-unsealed`) launch is not an exact
ADR 0004 qualification attempt. For an absent repository, the skill composes
the source-attested read-only plan and separately confirmed exact-commit
acquisition. A source-attested home may be resumed or reused only after a
complete offline rehash against the valid immutable receipt while occupancy
names that live directory. Occupancy may move with `home relocate` after the
same live rehash ([ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md)).
Unknown trees without a receipt still require full verification against a
reviewed expected manifest independent of the observed tree; a shallow catalog
label and self-observed manifest are insufficient.
The acquisition is catalog/artifact evidence only and creates no seal, status,
decision, serving permission, or physical claim. Its journal lives under
`experiments/model-onboarding/workflows/` and is recovery state, not evidence.
Deterministic skill and journal tests make no physical DGX claim and create no
release decision. The separate Nemotron Nano Gate 14 artifact physically
passes the bounded one-node rank-0 acquisition, attachment, verification,
prepare/reuse, cleanup, and reacquisition lifecycle across three confirmed
ranks. It does not cover a remote target or asymmetric credentials and makes no
serving or Model Serving Release claim. Maintainer-only issuance
staging can propose exact registry objects from a verified capture candidate
plus explicit review input; a successful local command is not trusted until
repository review and merge. The Qwen3.8 lineage is the first reviewed
publication. Existing schema-1 bundles and `STATUS=tested*`
labels remain separate legacy contracts and retain recommendation order; no
current profile is automatically `Validated`. Serving permission is
status-independent. These corrected
ADR 0004 objects remain schema version 1 because none was issued or persisted
before the correction. Existing legacy schema-1 seals/bundles and raw evidence are not
rewritten.

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
mount under vLLM is rejected as a serving runtime source
([ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md)).

### 2.1 Terminology and independent axes

- **Home** is the durable storage placement for one exact model revision. It
  may be any confirmed node and is not necessarily rank 0.
- **Owner** is reserved for the node running a live export/service, such as the
  retired NFS/RDMA serving path or leftover teardown. It is not a synonym for
  rank 0 or durable home.
- **Rank 0** is the API/control rank for the exact serving geometry.
- **Origin** is `huggingface`, `cold-catalog`, or `managed-home`.
- **Transfer** is `preexisting`, `ssh-control`, `ssh-roce`, or `nfs-rdma`.
- **Runtime source** is `durable-home`, `sealed-hot`, or `live-mount`.
- **Retention** is `durable`, `ephemeral`, or `pinned`.
- **Prepare / model preparation** is the user-facing operation that resolves the
  exact model, creates the required rank-local runtime views, transfers only
  non-home bytes, and verifies every rank. It does **not** start a serving
  container or establish model qualification. Public `activate` is removed
  (ADR 0008); use `prepare`. `activate` remains an internal-schema term only.

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
| Model qualification | Does the exact model/image/configuration/geometry meet stability, accuracy, throughput, latency, strict same-boot, context, and soak requirements? |
| Release and promotion | Did provenance/security and physical geometry pass, and have all subsystem gates required for the supported profile or guided policy passed together? |

A failure in one subsystem does not erase valid evidence from another unless a
causal connection is demonstrated. It does block a release claim that requires
both. A successful health check or completion proves serving integration, not
model qualification. Similarly, catalog acceptance does not promote a profile,
storage path, wizard choice, or default policy. See
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md).

The combined validation subject now has a stable name. A **Model Serving
Release** is the immutable combination of exact model identity, serving recipe,
runtime/image identity, and supported hardware geometry. Its release ID does
not include evidence, review metadata, physical placement, or the transport
used to prepare rank-local bytes. Any change to one of those four identity
parts creates a new release whose prior status does not transfer.

Qualification uses a frozen two-layer Validation Contract: repository-wide
invariants plus release-specific workloads and thresholds. `Validated`
requires satisfactory stability, accuracy, throughput, and latency results,
mandatory provenance/security review, and strict same-boot reproducibility.
FP-equivalent output is diagnostic evidence and does not satisfy the strict
gate. The full status vocabulary and object model are defined by
[ADR 0004](./decisions/0004-model-serving-release-validation.md). Its release
and contract schemas are implemented, while current `STATUS=tested*` and
schema-1 bundle behavior remain separate legacy status inputs. Advisory
release projection consumes only a verified tracked decision for an explicitly
bound profile; `qwen3.8-27b-fp8` is bound and other current profiles are
unbound.
Catalog recording and serving have no minimum validation status: the release
remains visible with its actual label. Recommendation/default projection is
separate, while operational admission checks concrete runnability rather than
status.

Criterion scope is not reviewer-selected. Stability, accuracy, throughput,
latency, and strict same-boot are `model-qualification`; serving integration is
`serving-integration`; and provenance/security plus physical geometry are
`release-promotion`. `catalog-artifact` remains an evidence scope for
acquisition, preparation, transfer, identity, and lifecycle. It cannot satisfy
a Model Serving Release validation criterion, and qualification begins only
after the exact-content/runtime-view verification barrier passes.

A decision considers every applicable observation in its evidence bundle.
Excluding an otherwise applicable observation requires an explicit,
evidence-backed record; the observation itself remains immutable. At the
criterion level, pass+fail is inconclusive, pass+inconclusive is inconclusive,
fail+inconclusive is fail, and all-pass is pass. Relative throughput/latency
baselines bind the reviewed predecessor contract, bundle, decision, and exact
run. The relevant predecessor criterion must have passed, but an unrelated
open or failed dimension does not require the predecessor release to be
globally `Validated`.

Every run separately declares the frozen criteria it attempted. Before the
qualification barrier that set is empty; after the barrier a non-preparation
attempt must declare a nonempty scope-compatible set, and its observations must
match that set exactly. Failed and interrupted attempts therefore remain
visible as inconclusive evidence instead of disappearing through an empty
observation list.

Runtime compatibility and architecture/geometry are checked structurally
against the release and observed environment. That rejects incompatible runs;
it does not establish physical behavior. Serving-integration and
physical-geometry criteria still require physical DGX evidence. Operator
command evidence is closed and typed: allowlisted program, SHA-256-shaped
program identity, program-specific operation, known repository resource,
criterion reference, or protected/rank-relative site reference bounded by the
release geometry. Recursive privacy checks reject recognized secret and
deployment-only values plus credential-bearing extensible keys while
preserving ordinary dotted public identifiers. Canonical compatibility ranges
compare the numeric core of exact deployed versions without discarding the raw
zero-padded or vendor-suffixed evidence. Completed nested context or soak
failures cannot be softened by an outer inconclusive label. Trusted capture
must verify program digests, and publication privacy review remains mandatory
because structural validation cannot identify
every private codename. Supersession requires later chronology and an acyclic
decision relationship.

---

## 3. Storage tiers

| Tier | Role | Required? | Typical location |
|---|---|---|---|
| **Warm catalog** | Federated durable homes: complete model trees in the **default HF location on any Spark** | **Yes** (core library) | Per-node `$HF_CACHE` / hub layout |
| **Cold storage** | Shared or local **archive**; preferred **before Hugging Face download** when warm misses | **Optional** | Configurable path (conventionally `/mnt/Models` / `MODELS_NFS`) |
| **Hot staging** | Per-job (or pinned) working trees on ranks for load/serve/restart | Yes when ranks need a local tree | e.g. budgeted staging root outside durable HF home |
| **Origin** | Upstream download | Separate acquisition, not a resolve step | Hugging Face Hub via `home add` (ADR 0006) |

### 3.1 Resolve order

Current `resolve` / catalog lookup does **not** download from Hugging Face:

```text
1. Warm catalog — complete home on any confirmed Spark
2. Cold storage — only if configured and available
3. Else fail closed with an explicit reason
```

Hub acquisition is a separate explicit command:
`scripts/model-library.sh home add` creates exactly one durable home (sealed,
or source-attested for unsealed profiles). It is never a silent resolve
fallback (ADR 0006 removed the replicated `pull-weights.sh` path).

| Cold config | Behavior |
|---|---|
| Unset / empty | Skip tier 2; no error; no mount required |
| Set but unavailable | Fail only when a flow needs cold (e.g. cold-path conf or explicit cold resolve); do not break pure HF-id flows that never need cold |
| Set and healthy | Prefer cold as the fill source when warm misses; do not auto-download during resolve |

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
  a reviewed expected seal and exact commit. `home add <unsealed-profile>
  --revision <selector> --plan` is a read-only source-attested plan; execution
  requires a separate confirmation and `--yes`. Every confirmed rank must be
  observable. A one-node profile may establish its sole serving placement on
  any confirmed rank; automatic placement chooses the eligible rank with the
  most free space, while `--node` binds an exact remote or local placement.
  Multi-node placement remains limited to the profile's exact serving ranks so
  active storage remains one durable home plus N−1 hot copies. An existing
  repository path anywhere blocks duplicate creation; an explicit ineligible
  or out-of-geometry `--node` fails without choosing another rank. The
  chosen rank must have a Hugging Face CLI, sufficient space for the complete
  manifest plus staging headroom, and target-local metadata access. Automatic
  placement treats a metadata or access failure as making only that candidate
  ineligible; successful candidates must agree on the exact commit and
  inventory. An explicit `--node` resolves metadata only on that rank. Download
  failure removes only plan-owned private staging. The source-attested plan
  resolves a mutable selector to an exact commit and complete upstream Git/LFS
  inventory on the selected rank without accepting or moving a token.
  Execution confines model and transient cache bytes to plan-owned private
  same-filesystem staging, checks the complete upstream set and Hugging Face
  missing/extra result, hashes every file, repeats the all-rank no-home check,
  writes an immutable site-local receipt, publishes with an atomic
  no-replace rename, and attaches occupancy to the exact published directory.
  `home verify` later performs an offline full rehash against that receipt
  when occupancy still names the live directory. Occupancy may move with
  `home relocate --node` after the same live rehash; receipt `selected_rank`
  is Hub-download provenance only ([ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md)). Extra complete hub trees are unbound-complete, not homes. Source-attested acquisition creates
  observed/source identity and catalog-artifact evidence only; it does not
  create reviewed identity, a seal, status, serving permission, a Model
  Serving Release decision, or physical evidence. Catalog refresh, hot
  preparation, launch, and fallback are separate actions. Onboarding
  must refresh the catalog and verify or prepare the exact
  `model_id@commit`; it must not rely on mutable `refs/main` or
  profile-only resolution.
- Catalog entries are **labeled**:

| Label | Meaning |
|---|---|
| **Reviewed identity match** *(legacy schema value: `expected-unverified` → `match`)* | The observed exact revision/manifest matches the lab-issued seal and schema-1 bundle. This is content identity, not the ADR 0004 Model Serving Release status. |
| **Present (unvalidated)** | Complete-looking hub tree on a Spark; Pulsar has **not** validated serving that model |
| **Partial / invalid** | Incomplete or not sealable — not a usable home |

**Catalog visibility ≠ Pulsar serving guarantee.** Validation status neither
authorizes nor blocks serving. Operator surfaces show fitting serving profiles
with their labels and caveats; recommendation/default policy may prefer
stronger evidence. Exact identity, recipe, topology, capacity, lifecycle, and
security checks still fail closed when the requested run cannot proceed.

**Current implementation:** a tested profile without
`EXPECTED_MODEL_SEAL` is labeled `legacy-unsealed`. A reviewed seal under
`models/seals/` makes catalog schema 2 select only the declared immutable
commit and label it `expected-unverified`; preparation then computes the observed
manifest and must reach `match`. `catalog list --reviewed-identity` includes
only entries carrying a reviewed expected seal, never legacy
repository-ID-only claims; `--validated` is removed (ADR 0008) and fails
closed with `--reviewed-identity`. It does not assign an ADR 0004 status. The one-node diagnostic
`qwen3-1.7b` profile is the first issued seal and
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
    → stop → retain unpinned non-home hot (default) or pin (protect) or purge-hot (explicit)
```

### 4.2 Temporary hot disk and storage accounting

**Allowed.** Non-home ranks may hold a full (or later sharded) tree for the job
window. Hot is a working set, not a second full library of every catalog model.

For a warm-home service using `N` ranks:

```text
idle durable storage     = 1 × model_size
active storage           = 1 durable home + (N - 1) sealed-hot working copies
after ordinary stop      = 1 durable home + (N - 1) unpinned working replicas (`sealed-hot`)
after explicit purge-hot = 1 × model_size
after pin                = 1 durable home + (N - 1) pinned working replicas (`sealed-hot`)
```

The home-rank symlink contributes no owned hot model bytes. Admission charges
the exact sealed manifest size only to ranks whose runtime source is
`sealed-hot`; a `durable-home` view requires zero additional model bytes.
Existing files anywhere below the hot root, including untracked or malformed
managed content, still count toward that rank's current owned-hot total.

### 4.3 Pins and disk budget

| State | Non-home disk | Restart contract | Durable-home dependency |
|---|---|---|---|
| Ordinary stop (unpinned retain) | Keep verified hot, still reclaimable | Reuse ready witness when identity and files match | **Still required** |
| Explicit `--purge-hot` | Purged | Prepare again before restart | Required as preparation source |
| **Pinned warm-home** | Keep verified hot, protected from unforced purge | No cold, transfer, or catalog refresh | **Still required** |
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
- ordinary stop leaves unpinned working replicas (`sealed-hot`) in place for that reuse;
- restart after explicit `--purge-hot` prepares non-home ranks again from the
  durable home;
- occupancy loss is service loss until ADR 0011 recovery.

Home-loss resilience is occupy-in-place after a live receipt rehash, or
restore from a verified receipt-indexed NFS archive, on a distinct failure
domain ([ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md)).
A second Spark durable home, and a second copy on the same rank/filesystem,
are not that policy. In an exact multi-node geometry, losing the
home node also removes a required compute rank.

### 4.5 Expected identity and verification tiers

A reviewed model-content claim has two distinct identity views:

- **Expected seal:** lab-issued model ID, exact commit/revision, complete
  `sha256-snapshot-manifest-v1` manifest ID, and provenance.
- **Observed seal:** identity computed from a user's or rank's local bytes and
  compared with the expected seal. Observed content cannot issue or replace the
  expected seal.

In the current schema, a **validation bundle** binds the expected model seal(s),
behavior-affecting tokenizer/draft/adapter/code artifacts, normalized
profile/runtime configuration, resolved image digest, geometry/topology class,
and evidence. Hosting location—including a future mirror—is distribution
metadata, not identity.

ADR 0004's implemented schemas now separate all five roles. A release descriptor
owns the stable Model Serving Release ID, a frozen Validation Contract declares
the gates, immutable run records bind observed attempts and evidence, a new
bundle binds the exact evidence set, and a reviewed decision records an explicit
status that must equal the outcome independently derived from those inputs.
Read-only trusted persistence and advisory catalog/operator projection are now
implemented. Local release planning, validator measurements, attempt
composition, and evidence-capture candidate persistence remain unreviewed and
non-issuing. Maintainer-only issuance staging can write an untrusted
proposal; repository review and merge remain the authority boundary. Serving
permission remains status-independent. Existing schema-1 bundles remain
immutable legacy combined artifacts and are not converted in place.

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
home acquisition applies the same expected manifest to the exact downloaded
commit, then writes a separate rank-local witness under the HF cache's Pulsar
state directory—not inside the published repository. Both witness schemas
bind validation identity,
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
Sealed `home add` also uses this reviewed manifest as its publication gate. The
source-attested path instead binds its complete upstream inventory and observed
manifest in an immutable receipt, then attaches that receipt to the exact
published directory. Later offline `home verify` and exact prepare use that
current attachment, not a matching tree or the lexicographically first stored
receipt. Both paths hash the private target-rank staging tree
before publication; neither creates a serve witness because no runtime view has
been prepared yet.
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
| `ssh-roce` | copy backend with `--transport ssh-roce` | SSH/TCP pinned to confirmed RoCE endpoint | Fixed eight-stream policy for reviewed multi-rank preparation (ADR 0003) |
| `nfs-rdma` | fabric backend, then release | Short-lived NFSv4.2/RDMA transfer plane | **Retired** with the fabric internals ([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)) |
| `live-mount` | long-lived NFS mount under vLLM | Long-lived NFSv4.2/RDMA runtime dependency | **Rejected** as a serving runtime source ([ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md)) |

Neither transfer replaces NCCL inference traffic. No candidate may silently
fall back to control transfer, TCP NFS, or a different runtime source.
ADR 0003 fixes `ssh-roce` with eight streams for reviewed multi-rank
preparation once an eligible durable home exists.

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
| Warm-home restart with retained unpinned views | Durable home plus remaining sealed-hot; no transfer/catalog refresh while witness and files remain valid |
| Warm-home restart after explicit purge | Durable home plus preparation again |
| Cold stage-only restart with complete pin | Pinned staged trees; cold may be unavailable |
| Restart after durable-home loss | Occupy-in-place or restore from a verified receipt-indexed NFS archive ([ADR 0011](./decisions/0011-portable-occupancy-and-cold-archive.md)); Hub re-download only when no receipt and no archive exist |

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
only an eligible plan. A final durable copy, or the last occupancy of an
identity, requires the additional `--allow-last-home` acknowledgement. A
complete home must be the exact catalogued HF repository, contain only the
selected snapshot revision, use non-symlinked `snapshots`/`refs` layout
directories, and have no ref pointing elsewhere. A recognized incomplete or
refs-only hub occupancy is a separate eligible class: the plan states that
it retires that exact repository path so a later source-attested `home add`
can proceed, and it still requires the eligible plan plus `--yes`. Binding
uses one 40-hex commit. A leftover stub with a complete survivor of that
commit is not last occupancy and does not use complete-home primary policy.
Complete homes do not use that class.
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

### 4.10 Read-only health

`scripts/model-library.sh health` is the supported catalog-health service. It
reads the cached catalog and gathers shallow, no-follow hot metadata plus
managed-container observations from every confirmed rank under shared
lifecycle/hot locks. It never refreshes the catalog, refreshes a witness,
hashes model bytes, follows a runtime-view symlink, or changes state. Catalog
absence is `not-configured`; running services are unaffected, but new
preparation requires a catalog.
Invalid/stale catalogs, duplicate homes, stale primary selection, prohibited
runtime views, witness drift/missing state, legacy metadata, and unobservable
ranks are explicit findings.

The public schema-1 report exposes rank numbers, profile aliases,
model/revision identity, cached refresh time, expected manifest identity when
reviewed, primary/duplicate classification, runtime source, retention, identity/witness status, active-reference state,
issue codes, and remediation. It omits hostnames, addresses,
node/topology identity, absolute paths, filesystem identity, and witness IDs.
`healthy` and `not-configured` exit zero; `attention` and `unavailable` still
emit the complete report and exit nonzero.
Rank-based home and primary placement is meaningful only while
`catalog.topology_compatible=true`; interactive consumers suppress cached rank
mapping and require refresh when the confirmed topology has changed.

Historical hot schemas 1 and 2 are ownership evidence only: they are never
trusted, launchable, or migrated into schema 3. Health reports them as
attention. The public `hot legacy check|remove` repair command is removed
(SIM-13). Leftover files under `PULSAR_HOT_ROOT`, if any, are site-admin
cleanup, not a Pulsar command. Durable homes and sibling instances remain
outside health's authority.

Doctor consumes the same report as warnings. These findings do not affect
already-running services, while model-library preparation and destructive
lifecycle operations retain their fail-closed checks. `./pulsar models` and the
operator-home **Models & storage** entry expose a width-aware projection of
this same contract. Browsing and health rechecks are read-only. A separate,
confirmation-gated **Refresh distributed catalog** action delegates to the
existing all-confirmed-rank refresh service, preserves exact-revision primary
selections, and then renders a new sanitized health report. Refresh is never
automatic; incomplete topology or rank observation fails closed. The
projection states that the model library is the only weight mechanism
(ADR 0006) and labels each profile's exact scope.

An exact model detail may also offer **Prepare for two-rank serving** or
**Prepare for one-rank serving**, according to profile geometry, when
the catalog mapping is current and the matching serving profile carries
a reviewed expected seal. This is a separate default-no mutation, fixed to the
accepted eight-stream SSH-over-RoCE copy policy for non-home ranks with no
fallback. A one-node home-only view uses `ssh-control` with one stream and no
bulk transfer. The
interactive layer shows exact revision/manifest identity, durable-home
dependency, serving ranks, and an approximate non-home storage requirement,
then delegates to the existing preparation service. The reviewed seal is an
identity requirement for this interactive acquisition/preparation path, not a
validation-status allowlist. That service remains the
authority for full verification, exact all-rank storage admission, topology and
primary checks, rollback, and witness publication. The interaction never adds
validation-status override, starts serving, or claims model qualification. Retention, purge,
and durable-home removal remain separate direct-CLI operations.

The serving wizard is a distinct consumer of the same readiness contract.
Every profile routes through the library (ADR 0006): a reviewed profile uses
this serving check (with a guided one-time `home add` when no durable home
exists), while an unsealed profile checks its prepared views directly. The
wizard shows exact revision/manifest identity, durable-home dependency,
selected ranks, fixed transfer policy, and no-fallback behavior. It may invoke the same
preparation service after a default-no confirmation, but it re-reads health and
requires every selected runtime view to be exact and ready before setting
`--weight-source library-hot`. Container launch remains behind the wizard's
separate final confirmation. A one-node catalog service is placed on its
durable-home rank and uses that local view; multi-node preparation uses the
exact profile ranks and creates working replicas (`sealed-hot`) only on non-home ranks.

On an ordinary stop, an observed `library-hot` service retains unpinned
prepared views by default ([ADR 0007](./decisions/0007-ordinary-stop-retains-unpinned-hot-views.md)).
`--pin-weights` protects them from a later unforced purge. `--purge-hot` is
the explicit capacity-recovery action. Site policy
`PULSAR_HOT_STOP_POLICY=retain|purge` may restore the previous purge default
for named-profile stops. Interactive home stop discloses the restage
consequence before mutation. `down.sh --all` never auto-purges.
A wizard replacement is a bounded transaction: immediately before stopping one
complete observable service whose library identity is a reviewed `match`,
Pulsar snapshots its effective launch contract, exact physical placement,
weight policy, speculative-decode state, and exact revision/seal/manifest,
per-rank runtime sources, and retention. Ephemeral catalog views are
temporarily pinned before stop. A failed replacement may restore only that
captured contract; it never reconstructs from current profile defaults or
silently switches storage source or placement. A complete, safe-to-stop
`library-hot` service without that match (`legacy-unsealed` or `unvalidated`)
is stopped without a capture, so exact rollback is unavailable — the same
guard as a leftover pre-library launch. Incomplete, multi-service,
legacy-unlabeled, drifted, or unretainable state makes automatic replacement
unavailable before stop. A leftover transaction
captured under the removed replicated mechanism cannot be rolled back. Recovery
inspects current inventory, states whether the saved profile is running,
stopped, or ambiguous, and offers a confirmation-gated archive of the original
file into a timestamped recovered directory. Exact library rollback is not
invented from that record.

The site-local transaction is short-lived recovery state, not a served-model
registry or audit history. It remains across wizard exit or interruption and is
removed after the replacement is running, or after the exact rollback is
confirmed and temporary retention is restored. Incompatible leftovers are
archived rather than left to fail every later wizard invocation. A successful replacement closes
the rollback transaction even if old-view unpin/purge needs visible direct
remediation. Pinning still does not copy or protect the durable home. Explicit `--purge-hot` may remove a pin, while
durable-home deletion remains a separate direct-CLI workflow.

Current health closes supported catalog/hot observability, but container labels still do not carry
per-rank runtime-source/witness state and unmanaged processes remain outside
Pulsar's discovery boundary.

---

## 5. Relationship to current and experimental paths

| Path | Role under this direction |
|---|---|
| Replicated `pull-weights` + local launch | **Removed** ([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)); the library is the only mechanism and `home add` is the fresh-cluster ingress |
| Live NFS/RDMA serving | **Rejected** as a serving runtime source (ADR 0005); implementation removed (ADR 0006). Historical evidence remains; not promoted. |
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
12. **Release status and content presence are distinct** — present bytes do not
    issue a reviewed identity or validation decision; duplicates recommend
    cleanup and never cause silent multi-home serve.
13. **Prefer boring recovery** — explicit verify, prepare again, and relaunch over
    hidden mount or replica behavior.
14. **Raw experiments stay local** under gitignored `/experiments/`; durable
    decisions belong in reviewed design/ADR/runbook docs and sanitized evidence.
15. **Evidence is scoped, not globally contagious** — preserve valid catalog,
    integration, and model results within their measured contracts; combine
    them only for a release claim, and expand invalidation only when inputs or a
    demonstrated causal dependency cross subsystem boundaries.
16. **Release identity is separate from evidence and transport** — a Model
    Serving Release ID names exact serving inputs; contracts, attempts,
    decisions, transfer paths, and physical placement are linked provenance,
    not hash inputs.
17. **Status informs; it does not authorize** — show every fitting serving
    profile with its status and material caveats. Keep recommendation/default
    policy and concrete operational admission as separate decisions.

---

## 7. Promotion gates

> **ADR 0006 (2026-08-19):** the library was later made the only
> weight-distribution mechanism by explicit decision, which converted the
> unmet guided-default promotion gates below into recorded accepted risks.
> The checklists remain as history and still gate release-specific claims.

These are the historical combined **release/default-promotion** gates, not a
single verdict on every subsystem. Catalog/artifact and serving-integration
results may be accepted and preserved in their own scopes while model
qualification remains open. A Model Serving Release cannot become `Validated`,
and the model-library path cannot become a recommended/default distribution policy,
until their respective applicable gates pass. A subsystem pass never changes
profile `STATUS`, Model Serving Release status, recommendation, or the default
storage path by itself.

ADR 0004 additionally separates bounded `library-hot` subsystem GA from a
particular model/runtime decision. The initial GA scope is the reviewed
two-rank path; remote one-rank placement remains outside it. The exact DeepSeek
same-boot determinism failure remains valid model-qualification evidence and
blocks that release from `Validated`, but it does not invalidate catalog or
serving-integration results and was not a subsystem-GA blocker. Bounded
subsystem GA does not make `library-hot` the default or only path.

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
[x] Leftover schema-1/2 is untrusted and cannot launch; public repair CLI is removed (SIM-13)
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
durable copy. The subsequent exact-release same-boot strict-determinism gate failed:
profile-default DSpark k=5 produced 11/30 exact texts and 4/30 fully identical
records, while a forced no-spec diagnostic improved to 26/30 and 25/30 without
passing strict identity. That Model Serving Release result remains failed. A
later subsystem-only closure ran the separate sustained serving, restart,
recovery, identity, and cleanup gates described below.
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
SIM-13 later removed the public repair command after lab confirmation that no
schema-1/2 hot instances remained; that artifact stays historical.
Failed or incomplete evidence is not rewritten because an architectural
blocker changed.

### 7.1 Initial `library-hot` GA closure — completed 2026-08-16

The bounded reviewed two-rank subsystem completed its combined GA task:

```text
[x] Remove home-rank reflink/copy fallback; failed durable-home symlink stops preparation
[x] Sustain physical serving, then restart the same prepared release
[x] Force replacement failure and restore the exact captured launch contract
[x] Reverify the reviewed identity through preparation, serving, and rollback
[x] Prove owned cleanup and the final one-durable-home/no-unpinned-hot state
[x] Publish sanitized evidence scoped to catalog/artifact, serving integration,
    and model qualification for the bounded soak and stability observations
```

The physical run used the exact reviewed two-rank DeepSeek profile. Preparation
created a sealed-hot non-home view and an exact durable-home symlink, then both
ranks matched the reviewed manifest. The service reached health, completed all
warmup phases, handled 587 requests with zero errors over 30 minutes, restarted,
and recovered the captured contract in a new wizard process after a deliberately
failed replacement launch. Cleanup removed the owned service and both unpinned
views while preserving one identity-matched durable home. The soak retained a
1.14 GiB first-to-last-decile memory-shrink warning; it is not hidden or
reclassified as a request failure. See
`results/model-library/deepseek-v4-flash-library-hot-ga-closure-20260816.json`.

This task did not rerun or waive release-specific accuracy, strict same-boot
reproducibility, throughput, latency, long-context, or Model Serving Release
soak criteria. Those belong to the affected Model Serving Release and its
frozen Validation Contract. (At closure time, remote one-rank and
legacy-unsealed use remained experimental and replicated copies remained the
guided default; ADR 0006 later promoted every library scope to supported and
removed the replicated path.)

---

## 8. Remaining deferred work

Items marked **(ADR 0006 accepted risk)** are now product-wide follow-ups
rather than promotion blockers.

- Physical source-attested acquisition on a remote durable-home target and
  asymmetric per-rank Hugging Face credentials; the bounded one-node rank-0
  Gate 14 lifecycle has passed **(ADR 0006 accepted risk)**
- Physical serving-integration repeat for the new remote one-node wizard path;
  deterministic orchestration is implemented and the production two-node
  DeepSeek wizard path has passed physically, while existing one-node evidence
  does not exercise this new remote interactive placement
- Trusted publication of each Model Serving Release requires repository review
  and merge. The maintainer issuance workflow can stage an untrusted proposal
  from a verified capture candidate plus explicit review input. The Qwen3.8
  lineage is the first reviewed publication and profile binding.
  Release, frozen-contract, immutable run-record, new bundle, and
  reviewed-decision schema version 1 remain the pure contracts; read-only
  persistence and verification remain under `models/model-serving-releases/`;
  local evidence-capture candidate persistence remains unreviewed; advisory
  catalog/operator status projection remains for explicitly bound profiles;
  the supervised `pulsar-model-onboarding` skill remains non-authorizing
  control-plane orchestration; current schema-1 bundles and `STATUS=tested*`
  remain legacy implementation contracts
- Physical serving-integration evidence for remote one-rank library serving
  **(ADR 0006 accepted risk — the scope is supported by decision)**
- Issue remaining supported profiles over time
- Per-rank runtime-source/witness labels and unmanaged-reader observability
- Issuance and publication guarantees beyond local untrusted staging, the
  read-only ADR 0004 registry verifier, its advisory projection, the local
  evidence-capture candidate workflow, and health schema 1. A staged
  proposal is not trusted until repository review and merge.
- (Closed by ADR 0006) The guided/default promotion matrix: the library was
  made the only mechanism by decision; release-specific validation gates are
  untouched
- Durable-replica and failover policy on distinct failure domains
  **(ADR 0006 accepted risk — home loss is now product-wide service loss)**
- Budget-based or last-N hot eviction; ADR 0007 keeps capacity recovery explicit
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
| 2026-08-12 | The exact reviewed DeepSeek GA identity failed the strict same-boot `library-hot` determinism gate. Profile-default DSpark k=5 produced 11/30 exact texts and 4/30 identical records; a forced no-spec diagnostic improved to 26/30 and 25/30 but still failed strict identity. Exact seal, image, geometry, and runtime views were held constant, the clean one-home state was restored, and no fatal runtime signature appeared. Preserve this failed evidence; do not attribute the result only to the retired preview profile or run sustained soak as if the blocker passed. ADR 0004 later classified this as a Model Serving Release blocker rather than a distribution-subsystem GA blocker. |
| 2026-08-12 | Models & storage gained an explicit confirmation-gated catalog refresh that delegates to the existing atomic all-rank service. Browsing and health rechecks remain read-only; refresh is never automatic and does not prepare, launch, retain, repair, or delete models. |
| 2026-08-12 | Exact model detail gained confirmation-gated experimental preparation for reviewed-seal tested serving profiles. It delegates to eight-stream SSH-over-RoCE copy with no fallback and re-renders health; it does not launch, expose unvalidated bypass, change replicated defaults, or claim promotion. |
| 2026-08-13 | Added reviewed-profile `home add`: target-side exact-commit download into plan-owned same-filesystem staging, any-confirmed-rank placement for one-node profiles, exact geometry for multi-node profiles, most-free-space selection with explicit in-geometry override, all-rank duplicate recheck, full expected-manifest verification, and atomic publication of one durable home. Catalog refresh, hot preparation, and launch remain separate; deterministic contracts pass and physical acquisition evidence remains pending. |
| 2026-08-13 | Reviewed acquisition passed its three-node physical catalog/artifact gate with sealed Qwen 1.7B. Guarded last-home removal, interrupted remote download cleanup, explicit rank-2 acquisition, automatic most-free-space rank-2 acquisition, full reviewed-manifest verification, atomic publication, explicit catalog refresh, and final one-home/no-hot state passed. The gate also closed target discovery for Pulsar's managed HF CLI venv. It did not prepare, launch, qualify, or promote the model or storage path. |
| 2026-08-13 | The serving wizard gained an explicit experimental distributed-catalog choice for eligible reviewed profiles while preserving replicated weights as the first/default option. Readiness is rechecked after optional preparation, launch remains separately confirmed, one-node catalog serving is constrained to its durable-home rank, and stop purges unpinned hot views by default while explicit pin retains them. Deterministic contracts pass; no new physical, model-qualification, or promotion claim is made. |
| 2026-08-13 | The production two-node serving wizard passed its physical DeepSeek catalog integration gate from a clean one-home state: explicit experimental selection, separate preparation and launch confirmations, eight-stream SSH-over-RoCE, fresh exact readiness, read-only exact-snapshot serving, eight warmup phases, completion, interactive owned stop, unpinned purge, and return to one durable home. This is serving-integration evidence only; remote one-node placement, release-specific strict determinism/soak, and guided/default promotion remain open. |
| 2026-08-14 | Wizard replacement became a short-lived fail-closed transaction. New launch labels bind the operational launch contract and actual speculative-decode state; inventory plus catalog health capture exact placement, storage source, revision identity, runtime sources, and retention before stop. Ephemeral catalog views are pinned until replacement or exact rollback succeeds. Deterministic contracts pass; the physical failed-replacement/rollback repeat remains pending and no storage-path or model promotion claim changes. |
| 2026-08-14 | **ADR 0004 accepted:** name the immutable model/artifact + serving-recipe + runtime/image + supported-geometry tuple a **Model Serving Release**; separate its release descriptor from frozen contracts, run records, evidence bundles, and reviewed decisions; adopt `Untested`, `Testing incomplete`, `Tested—criteria not met`, `Tested—inconclusive`, `Validated`, and `Superseded`; require strict same-boot reproducibility and reviewed provenance/security for `Validated`; and treat transfer as run provenance after a full pre-qualification verification barrier. Existing schema-1 artifacts and `STATUS=tested*` are not relabeled. The future supervised skill is `pulsar-model-onboarding`. Initial `library-hot` GA is scoped to the reviewed two-rank path and depends on the section 7.1 closure task, not on the DeepSeek release's strict-determinism result. |
| 2026-08-14 | Implemented ADR 0004 stage 1 as pure Python release-descriptor and frozen Validation Contract schemas with fixed deterministic IDs and fail-closed tests. The release ID hashes exactly the Model Artifact Set, normalized serving recipe/access contract, digest-pinned runtime compatibility envelope, and privacy-safe supported geometry. Contract checks require all core/prerequisite dimensions, exact same-boot equality, reviewed provenance, and protocol/geometry-bound relative budgets. Legacy schema-1 artifacts and serving/status behavior are unchanged; no physical qualification or `Validated` decision was produced. |
| 2026-08-14 | Implemented ADR 0004 stage 2 as pure Python content-addressed evidence-artifact, immutable run-record, validation-bundle, and reviewed-decision schemas. Cross-link verification binds release, frozen contract, exact run/artifact sets, observed rank/runtime and distribution provenance, frozen protocols/sample sizes/thresholds, required context and soak observations, comparable-predecessor regression evidence, review/privacy state, and immutable supersession. The supplied base-status assertion is independently derived and mismatches fail. The implementation performs no capture, persistence, trusted issuance, catalog projection, profile migration, or physical qualification; existing schema-1 and serving behavior remain unchanged. |
| 2026-08-14 | Corrected the unissued ADR 0004 schema-1 contracts before persistence: criterion scopes are canonical; catalog/preparation evidence cannot satisfy validation criteria; the review-derived provenance criterion is closed; release/contract values reject recognized private data; every post-barrier attempt declares and exactly accounts for its criteria; every applicable observation is included unless explicitly excluded with evidence; conflicts use deterministic adjudication; relative baselines bind a reviewed predecessor contract/bundle/decision/run whose relevant criterion passed; runtime and architecture/geometry checks remain structural; command evidence uses closed typed descriptors; and supersession is later and acyclic. No ADR 0004 object had been issued or persisted, so schema version 1 remains appropriate. Legacy schema-1 seals/bundles and raw evidence are untouched, and no physical claim follows from this correction. |
| 2026-08-14 | Implemented the read-only trusted-persistence foundation for ADR 0004 objects: tracked namespaces under `models/model-serving-releases/`, fail-closed filesystem and graph verification, publishable evidence hashing, predecessor-decision lineage validation, closed review-reference grammar, and `verify` / `show-release` / `show-decision` inspection. The store contains no issued object. Evidence capture, decision issuance, catalog/operator projection, serving-eligibility migration, and physical qualification remain pending. |
| 2026-08-15 | Implemented ADR 0004 evidence-capture candidate persistence: a local, unreviewed `plan` / `capture-run` / `assemble-bundle` / `verify-candidate` workflow that validates supplied release and contract objects, hashes checked-out allowlisted programs and evidence, publishes immutable candidates under a gitignored output boundary, and independently verifies them. It does not issue a decision, write the tracked registry, change catalog or profile status, or authorize serving. Validator adapters, trusted privacy review, and physical qualification remain pending. |
| 2026-08-15 | **ADR 0004 advisory-status amendment:** validation labels communicate evidence and confidence but never authorize serving. The wizard and serving catalog expose every fitting serving profile with status and caveats, legacy `--force` status overrides are no-ops, and unsealed preparation no longer needs a validation-status override. Recommendation/default ordering remains evidence-backed; exact identity, recipe, topology, capacity, security, and lifecycle failures still block the concrete operation. This is a control-plane policy change only and creates no physical qualification claim. |
| 2026-08-15 | Implemented read-only advisory Model Serving Release status projection. An optional reviewed `MODEL_SERVING_RELEASE_ID` profile binding selects a content-verified registry release; catalog JSON, human catalog, wizard, and `scripts/up.sh` display its one unambiguous reviewed effective status. No binding and no reviewed decision remain neutral, ambiguity/unavailability stays visible, and a different runtime model-access contract cannot inherit the decision. Legacy `STATUS` remains separate and continues to drive recommendation order. Projection never issues a decision, authorizes serving, or creates a physical claim; the empty registry and unbound current profiles therefore remain neutral. |
| 2026-08-15 | Expanded the unissued ADR 0004 schema-1 primary identity to be source-neutral: exact Hugging Face snapshots and other complete content-addressed model trees are valid primary artifacts, while generic digest attachments are not. Added a maintainer-only planner that sources a profile and persists unreviewed release/contract candidates from a complete manifest, explicit runtime/hardware envelope, frozen criteria, and explicitly bound behavior artifacts. Local source references can normalize to public artifact keys but are never persisted. The planner cannot acquire bytes, write the tracked registry, issue a decision, assign status, or prove physical behavior. The registry is empty, so schema version 1 remains appropriate; legacy schema-1 seals/bundles are untouched. |
| 2026-08-15 | Capture `plan` and `capture-run` now compose a verified release-plan candidate with a separate attempt-only spec (`--release-plan DIR --attempt-spec FILE`). The old embedded `--spec` / `pulsar-model-serving-release-capture-spec` path is rejected with a migration message. Planner `verify` uses the public `load_verified_release_plan_candidate` loader; planner and capture publish candidate JSON with shared `pretty_json_bytes`. No planner path or planner candidate ID is persisted. Capture still independently validates release/contract objects and does not issue `Untested`. Measurement/validator-output adapters, trusted privacy review, and physical qualification remain pending. This is control-plane/schema plumbing only. |
| 2026-08-15 | Implemented the first reusable measurement and attempt-composition foundation for a future `pulsar-model-onboarding` skill: `validate/compare_captures.py` and `validate/bench_serve.py` can emit closed versioned measurement documents; `validate/run-gates.sh` can optionally preserve them without requiring a release plan; and `scripts/model-serving-release-attempt.sh` composes those measurements into existing ADR 0004 attempt-only specs. Mapping is limited to strict same-boot, absolute throughput, and absolute latency. Missing, corrupt, interrupted, short-sample, or protocol-mismatched work stays incomplete/inconclusive. Capture still derives program versions and evidence digests. No status, decision, trusted publication, physical claim, or serving gate was added. |
| 2026-08-15 | Hardened that foundation fail-closed: invocation plans are closed and type-checked before argv emission; run-gates refuses a failed or empty bench-argv instead of keeping the default sweep; compose requires the measurement path and publishable evidence path to name the same stably read file, capture-validates both specs, then publishes one exclusive two-file directory. Writes use descriptor-rooted no-follow parents. Compare/bench persist incomplete `--result-json` on ordinary failure; missing validator output is refused rather than invented. The attempt spec still has no precomputed digest, so later capture must re-read the file. Protected locators remain out of this slice. Control-plane tests only. |
| 2026-08-15 | Corrected the unmerged measurement foundation after review: benchmark request count must be at least the largest declared concurrency at the CLI, measurement, and invocation-plan boundaries; unreadable measurement evidence fails through the sanitized error path without publishing an attempt; and SIGINT/SIGTERM stop `run-gates` before any later gate while preserving already-written partial output. Control-plane tests only; no physical or status claim changed. |
| 2026-08-15 | Implemented ADR 0004 stage 4 as the repository-local `pulsar-model-onboarding` skill. It supervises a brand-new unsealed model through a separately reviewed draft profile, exact-home assessment or safe reuse, explicit qualifying distribution, verification, unreviewed release/contract planning, launch, sequential supported measurements, unreviewed evidence capture, handoff, and ownership-safe cleanup. It permits reuse only after full verification against a reviewed expected manifest independent of the observed tree; a shallow catalog label and self-observed manifest are insufficient. It stops when no such home exists because the current sealed-only acquisition service is the only path with private staging, independent completeness verification, and atomic publication; a direct durable-cache download is forbidden. It collaborates at material decisions and has no seal, status, binding, registry, or promotion authority. Current automated mapping covers only strict same-boot and absolute throughput/latency. The default unsealed replicated path is not an exact ADR 0004 qualification attempt. The skill-local journal is isolated under `experiments/model-onboarding/workflows/`, is recovery state rather than evidence, and cannot collide with default `<profile>/<release-id>` plan output. Deterministic skill and journal tests make no physical DGX claim and create no release decision. |
| 2026-08-16 | **Bounded `library-hot` GA completed:** the reviewed two-rank path now requires an exact home symlink with no copy fallback and physically passed 30-minute serving, exact restart, forced replacement failure, persisted new-process recovery, reviewed-identity re-verification, owned cleanup, and one-home closeout. The corrected soak completed 587 requests with zero errors and retained its 1.14 GiB memory-shrink warning. Remote one-rank and legacy-unsealed use remain experimental; replicated remains the guided default; no Model Serving Release status changed. |
| 2026-08-16 | Implemented maintainer-only ADR 0004 issuance staging: `plan` previews and `stage` writes an untrusted proposal from one independently verified capture candidate plus a closed review declaration. Pure schema modules derive status. Writes are content-addressed and idempotent; an interrupted stage may be retried without deleting unrelated files; the normal registry verifier does not accept an incomplete proposal. The command does not edit a profile, bind `MODEL_SERVING_RELEASE_ID`, or add a production registry object. Local success is not review or physical qualification. |
| 2026-08-17 | Accepted source-attested acquisition policy and added internal Hugging Face v1 planning contracts: a versioned source/inventory schema, identity precedence for a reviewed Model Serving Release binding then a legacy expected seal then unbound source-attested identity, and a privacy-safe approval identifier that binds source, commit, inventory, rank, geometry, capacity, policy, and internal topology generation without emitting site identity. Sealed `home add` schemas and behavior are unchanged. Public unsealed execution, receipts, `home verify`, prepare-time exact-revision enforcement, skill composition, and physical Hub/DGX evidence remain later work. |
| 2026-08-17 | Implemented the public source-attested acquisition control plane for an absent brand-new unsealed Hugging Face home: read-only exact-commit/inventory planning on the selected rank, separate confirmation, target-local authentication, private same-filesystem download and transient caches, complete Git/LFS and SHA-256 verification, repeated all-rank absence, immutable site-local receipt, atomic no-replace publication, receipt-backed offline `home verify`, exact prepare binding, and onboarding-skill composition. Deterministic tests pass. No physical Hub/DGX acquisition, serving integration, model qualification, seal, status, decision, permission, or promotion claim was produced. |
| 2026-08-17 | Bound source-attested receipt authority to the exact live durable-home directory published by that acquisition. A private site-local current-home attachment, written only after successful no-replace publication, selects the owning receipt. Missing, stale, restored, or replaced trees have no receipt authority and still require a reviewed expected manifest. Supported home removal detaches the pointer before mutation and keeps immutable receipts. Interrupted writer temps that match the exclusive writer grammar are ignored during enumeration. Control-plane implemented; physical Hub/DGX gate pending. |
| 2026-08-17 | The bounded Nemotron Nano source-attested Gate 14 physically passed on a three-rank topology with a one-node rank-0 target: legacy-home refusal/removal, exact public source resolution, two complete 19,362,748,480-byte acquisitions, immutable receipt plus current-home attachment, independent offline rehash, exact prepare/reuse without download, active-view removal blocker, controlled missing-attachment refusal, guarded detach/removal, receipt preservation, reacquisition, and final healthy one-home/no-hot state. Remote target execution, asymmetric credentials, an actual external new-inode restore, serving integration, model qualification, status, and promotion were not run or claimed. |
| 2026-08-18 | Guarded `home check` / `home remove --yes` can inspect and retire a recognized incomplete or refs-only Hugging Face hub occupancy that blocks source-attested `home add`. The plan states the retire-path-absent action, public model identity, bound commit when live `refs/main` names one, rank role, eligibility, and delete/retain scope. Last occupancy still needs `--allow-last-home`. `home check` is read-only; no `--yes` means no mutation; catalog refresh never auto-deletes. Complete homes, multi-revision trees, attached homes, and unbound `@unknown` rows stay on the previous fail-closed contract. Deterministic tests only; no physical Hub/DGX removal was run. |
| 2026-08-19 | **ADR 0005 accepted:** reject live NFS/RDMA under vLLM (`--weight-source fabric`, `live-remote-readonly`) as a serving runtime source. Rank-local restart cannot cold-start without the owner export. Keep NCCL/RoCE, topology discovery, and ADR 0003 `ssh-roce` prepare. Launch fails closed with no remap. One-shot `nfs-rdma` prepare remains a separate experiment. Historical `results/weight-fabric/` evidence is superseded and not rewritten. |
| 2026-08-19 | Issued and bound the first ADR 0004 Model Serving Release lineage for `qwen3.8-27b-fp8`. Exact same-boot, absolute throughput, absolute latency, and reviewed provenance/security passed; stability, accuracy, serving integration, and physical geometry remain unevaluated, so the advisory decision is `Testing incomplete`. Legacy `STATUS=untested`, recommendation/default policy, serving permission, expected-seal state, and experimental one-rank `library-hot` maturity remain unchanged. |
| 2026-08-19 | **ADR 0006 accepted:** the model library is the only weight-distribution mechanism. The `--weight-source`/`--weight-mode` axis, the replicated per-node cache path, the fabric workflow internals, the one-shot `nfs-rdma` prepare experiment, and the absolute-path catalog profiles were removed; every library scope (two-rank sealed, one-rank, legacy-unsealed) is supported by decision; a confirmed topology manifest (one-node valid) is a serving prerequisite. Open gates are recorded as accepted risks: one-rank physical serving-integration evidence, source attestation as primary ingress (SIM-03), and durable-home failover. Historical evidence is preserved and marked superseded. |
| 2026-08-20 | **ADR 0007 accepted:** ordinary stop retains unpinned prepared views. `--purge-hot` is explicit capacity recovery; `--pin-weights` remains protection from unforced purge; retain is not pin; `PULSAR_HOT_STOP_POLICY=retain|purge` may restore the previous named-profile default; `down.sh --all` never auto-purges; wizard replacement still pins then purges the previous view on a successful different-profile switch. No automatic eviction. Library-only distribution (ADR 0006) is unchanged. |
| 2026-08-21 | Wizard recovery no longer wedges on a leftover pre-library replacement transaction. An unrestorable record is classified against current inventory, exact rollback is refused, and a confirmation-gated archive moves the original bytes into a timestamped recovered directory. Noninteractive remediation names the live path and `archive --yes` command. Deterministic coverage uses a frozen main-era replicated fixture. |
| 2026-08-21 | Canonical `prepare` is profile-aware: one-rank defaults to ssh-control/one stream; multi-rank defaults to ssh-roce/eight streams with no fallback. Management-network bulk copy on multi-rank requires explicit `--transport ssh-control`. `check-weights` / `up.sh` remediation names topology, home add, catalog refresh, prepare, unreachable rank, or identity inspection instead of always restaging. |
| 2026-08-21 | Leftover weight-fabric teardown honors `WEIGHT_FABRIC_SUDO_MODE=passwordless\|interactive` (`--interactive-sudo` overrides; invalid env fails closed). Live operator surfaces drop remaining AUD-02 replicated/experimental/non-default/storage-choice language. `docs/RECIPES.md` no longer launches deleted Laguna/Inkling profiles. ADR 0006 states Hugging Face `model_id@commit` as the current ingress product limit; local-directory import remains a future ADR. |
| 2026-08-21 | Wizard library serving is one flow. Sealed vs unsealed is identity data on that path (catalog serving-check still requires a reviewed seal; unsealed observes prepared views). Shared helpers own one-node home placement and prepare confirmation. After a library-hot capture, replacement stop/rollback/unpin no longer branch on `weight.source`. |
| 2026-08-21 | Home stop restage disclosure uses `load_conf` + `estimate_weights_gib` (`WEIGHTS_GIB`) instead of walking seal/bundle JSON. Startup evidence records are always library-hot / sealed-hot; `--weight-source`, `--configuration-id`, and `--cache-state` are no longer CLI flags. |
| 2026-08-21 | Named stop and `--all` share one label-driven stoppability predicate (`container_all_candidate_is_safe`). A missing conf file no longer has a parallel probe loop; retired Laguna/Inkling containers still stop from proven labels. Unobservable nodes remain fail-closed. |
| 2026-08-21 | Wizard replacement captures an exact rollback contract only for `library-hot` services whose identity is a reviewed `match`. A complete, safe-to-stop unsealed or unvalidated library-hot service (including first-run Nemotron) is switched with a guarded stop and no restore promise, instead of aborting capture while the previous service stays running. |
| 2026-08-21 | `classify_library_readiness` (used by `check-weights` / `up.sh`) names the command that can repair the gap: sealed `home add --yes`, unsealed plan-then-`--yes`, `cleanup-recommend` / `catalog primary set` for duplicate or unset primaries, refresh-then-select for a stale primary, and `prepare` only when a durable home is already present. |
| 2026-08-21 | One-node `check-weights --node` keeps the selected rank and probe cause. SSH-unreachable reports `rank-unreachable` (inventory, do not restage). A catalog home on a different rank reports `wrong-placement` instead of `prepare`. Verification failure on the selected home uses the same identity-mismatch remediation as a remote multi-rank view. |
| 2026-08-22 | **AUD-03:** removed the stale `MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md` current-system snapshot instead of keeping inaccurate present-tense claims behind a history banner. Active catalog/state docs are OPERATIONS.md and MODELS.md. Git history retains the 2026-08-19 snapshot. |
| 2026-08-22 | **SIM-03:** keep source-attested unsealed Hugging Face `home add` as a core catalog/artifact feature. Plan → confirm → execute remains the brand-new unsealed path. Receipts stay. Not a seal and not a non-HF import. |
| 2026-08-22 | **SIM-01:** keep ADR 0004's five separately persisted object roles. The Qwen3.8 reviewed lineage is the first real issuance; collapsing to one Release Assessment is rejected until a second issuance or a migration ADR. |
| 2026-08-22 | **SIM-05:** model profiles remain executable shell-style `models/*.conf`. Declarative TOML is rejected; a partial format migration would be worse than today's dual Bash/Python readers. |
| 2026-08-22 | **SIM-06:** wizard switching keeps exact rollback only for reviewed-match `library-hot` services. Unsealed switches stop without a restore promise. Explicit stop-then-start for every switch is rejected. |
| 2026-08-22 | **SIM-07 / ADR 0008:** one announced breaking-compatibility window. Already-removed: `--weight-source`/`--weight-mode`, `bench-activate`. Topology schema 1 stays as bootstrap input only. `--validated` is classified per CLI. `activate` and status no-ops drop after the window. |
| 2026-08-22 | **SIM-11:** executed ADR 0008 for public aliases. `--force`, `--allow-unvalidated`, `list-models.sh --validated`, catalog `--validated`, and public `activate` parse then exit 2 with a named replacement. N≥2 `check-image.sh` JSON emits `rank-*` / `missing-on-rank` (not pair-only `worker-*`). N=1 `head-*` / `target-*` / `missing-on-head` stay because `up.sh` remediations differ. `--force-unpin`, inventory keys, `plan-activate`, leftover fabric teardown, and hot schema-1/2 repair are unchanged. |
| 2026-08-22 | **SIM-08:** documentation roles are ADR (decision), DESIGN (architecture), OPERATIONS (procedure), MODELS/conf (live catalog), VALIDATION/results (evidence). No generated tables in this change; no replacement implementation spec. |
| 2026-08-22 | **SIM-09:** deterministic tests are quick / affected / full as the target. Until those entrypoints exist, local script/config work still runs `scripts/selftest.sh`. |
| 2026-08-22 | **ADR 0009 / SWI-728:** no launch-trust-mode axis. Existing profile labels are the trust contract. Operators are not asked to re-declare reviewed vs unreviewed at start. Catalog delivery and Compose do not imply qualification. Low-level `serve.sh` / `scripts/*` / `cluster/*` stay. Compose's remaining role is SWI-730 / SIM-10. |
| 2026-08-22 | **ADR 0010 / SWI-730:** operator-facing Pulsar consumes the in-repo catalog for now. Recipe craft and onboarding stay maintainer tooling. Root `docker-compose.yml` is removed; it did not assist serving or recipe craft. DSpark overlay remains SIM-10. |
| 2026-08-22 | **SIM-10:** removed `patches/pr41834-dspark-opt/`. Perf-neutral A/B stays in VALIDATION.md and git history. Flagship DeepSeek DSpark-in-checkpoint serving is unchanged. Compose was already removed by ADR 0010. |
| 2026-08-22 | **SIM-12:** leftover `weight-fabric.sh show|unmount|teardown` and `./pulsar weight-fabric` removed after lab confirmation. Live NFS serving stays refused. Historical `results/weight-fabric/` and `WEIGHT_FABRIC.md` remain. |
| 2026-08-22 | **SIM-13:** public `hot legacy check|remove` removed after lab confirmation that no schema-1/2 hot instances remained. Health still observes leftover schema-1/2 as untrusted and cannot launch. Historical `results/model-library/model-library-health-legacy-repair-gate-20260812.json` remains. `--force-unpin` on `purge-hot` is unchanged. |
| 2026-08-23 | **ADR 0011 accepted:** portable occupancy, `home relocate` after a live receipt rehash, download-rank as provenance only, unbound-complete trees are not homes, NFS receipt-indexed archive as the distinct-failure-domain replica. Relocate, occupancy classification, `home archive` / `home restore`, and `--allow-unarchived-last-home` are implemented as control plane. No physical NFS/DGX archive claim. |
