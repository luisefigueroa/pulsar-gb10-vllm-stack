# Weight materialize redesign (transfer plane vs runtime plane)

> **ARCHIVED — historical exploration only.**  
> Settled product direction is now
> [docs/MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md).  
> This file keeps early materialize sketches, option lists, and discussion
> noise for archaeology. Do **not** treat it as the active design.
>
> Original status was “under future consideration.” Live NFS/RDMA serving is
> later rejected by
> [ADR 0005](../decisions/0005-reject-live-nfs-rdma-serving.md). Historical
> live-mount notes remain in [WEIGHT_FABRIC.md](../WEIGHT_FABRIC.md).

| Field | Value |
|---|---|
| Status | Under future consideration |
| Date captured | 2026-08-08 |
| Related live design | [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) |
| Related ops | [OPERATIONS.md](./OPERATIONS.md), [MULTINODE.md](./MULTINODE.md) |
| Default today | Replicated local HF caches |
| Experimental today (at capture) | `--weight-source fabric` (live NFSv4.2/RDMA mount under vLLM; later rejected by ADR 0005) |

---

## 1. Problem statement (product)

Three product requirements drive any single-copy (or hybrid) weight path. They
are co-equal; an approach that wins only a subset is incomplete for the
intended Spark multi-node user.

| ID | Requirement | One-line success |
|---|---|---|
| **A** | Catalog storage efficiency | Durable disk ~O(1) per model as N grows |
| **B** | Faster model loading | Lower wall-clock start → healthy (cold and warm) |
| **C** | Stability and reliability | Predictable, fail-closed behavior operators can trust |

### Requirement A — storage multiplies with node count

The promoted multi-node path stages a **full durable model tree on every
serving rank**. Disk use for a given model is roughly:

```text
durable_bytes ≈ N_nodes × model_size
```

On a two-node canary that cost is tolerable. On a larger Spark fleet it is the
wrong scaling law:

- Adding nodes should add **compute / memory / interconnect**, not force another
  full copy of every model the site wants to keep on disk.
- Users are better served when **extra disk goes to more models** (or larger
  models), not to the N-th replica of the same checkpoint.
- The natural ceiling for a **single-copy catalog** is “what fits on the
  authoritative storage owner(s),” not “what fits on one node, divided by N.”

```text
Replicated (default today)
  disk:  [model][model][model] ... × N
  catalog capacity: floor(disk_per_node / model) models "everywhere"

Single-copy durable catalog (desired)
  disk:  [model A][model B][model C]... on owner storage pool
  nodes: read/transfer into memory (and optional short-lived staging)
  catalog capacity: floor(owner_pool / model) — scales with storage you add,
                    not with node count
```

### Requirement B — reduce model loading time

Operators also need **faster path from “start this profile” to healthy
serving**, especially cold starts of multi-node jobs. Load time here means
wall-clock to first healthy service (and, where useful, time until weights are
resident), not resident decode tok/s after the model is up.

Relevant components of load time:

| Phase | What dominates |
|---|---|
| Weight I/O | Reading checkpoint bytes from disk or network into page cache / host memory |
| Materialization | Framework parse, dtype, TP shard placement into GB10 unified memory |
| Engine setup | CUDA graphs / compile (profile-dependent), KV init, workers |
| Multi-node join | rendezvous, NCCL init (usually smaller than large weight I/O) |

Today’s evidence (Qwen 1.7B canary, not a large-model law) already shows
**cold fabric I/O is slower than cold local replicated I/O** (~2.1 vs ~4.8
GiB/s aggregate logical on that run), while **resident serving throughput was
near parity**. So a naive “always read the live remote mount under vLLM” can
**hurt** Requirement B even when it helps Requirement A.

Load-time levers any approach should be scored on:

1. **Bytes moved per rank** — full snapshot vs TP-sharded / sparse reads.
2. **Path bandwidth** — local NVMe vs RoCE NFS/RDMA vs control-LAN copy.
3. **Concurrency** — all ranks reading at once without serializing on owner.
4. **Cache reuse** — warm page cache, retained staging, or prior load (policy
   vs Requirement A).
5. **Avoid double work** — e.g. network copy to local disk *then* local load
   can be **two** full passes unless the design streams into the loader or
   reuses the first pass carefully.
6. **Parallelism of non-I/O setup** — not solved by storage alone, but must not
   be regressed.

Requirement B is **not** “beat every local-replica cold start in all cases.”
It is: for the single-copy catalog user, **minimize time-to-healthy** (cold and
warm), and where possible beat or approach the best practical alternative they
would otherwise use (including “rsync then local load” and “live mount load”).

### Tension between A and B

| Tactic | Helps A (disk) | Helps B (load time) | Risk |
|---|---|---|---|
| Durable N local replicas | ✗ | ✓ cold local I/O | Catalog capacity collapses with N |
| Live single-copy mount, every cold load over fabric | ✓ | ✗ often slower I/O; multi-rank over-read | Owner/NFS lifecycle |
| One-shot fabric transfer + local load + purge | ✓ durable | ? transfer+load may be two passes | Staging window uses client disk |
| Warm retained staging | ✗ while retained | ✓ warm restart | Temporary N× disk |
| Rank-sharded / reduced per-rank bytes | ✓ if shards not fully replicated | ✓ less I/O per rank | Conversion + layout coupling |
| Parallel multi-rank fabric read into memory only | ✓ no client durable tree | ✓ if bandwidth & loader allow | Loader/mmap semantics; no local restart without rematerialize |

Any design under consideration must state how it optimizes **A and B**, and
what it sacrifices when they conflict—without undermining **C**.

### Requirement C — stability and reliability

A multi-node Spark weight path is only usable if operators can trust it under
routine and adverse conditions. “Works on a happy canary” is not enough.

Reliability here means at least:

| Dimension | Expectation |
|---|---|
| **Fail-closed correctness** | Wrong route, TCP fallback, partial snapshot, digest mismatch, or incomplete load never reports healthy serving |
| **Deterministic ops** | Same config + topology → same mount/transfer/launch checks; no silent environment-dependent shortcuts |
| **Lifecycle safety** | Interrupted start cleans up; stop removes only owned containers; no orphan GPU/NFS state |
| **Fault clarity** | Link loss, NFS restart, owner reboot, and materialize interrupt have documented outcomes and recovery—not undefined hangs |
| **Independence where claimed** | If a mode claims “serving does not need owner,” that must hold after load/release; if it depends on owner, inventory and docs say so |
| **No silent fallback** | Never auto-switch fabric → replicated (or TCP NFS) and hide N× disk or wrong transport |
| **Evidence-backed promotion** | STATUS and docs change only with reproducible artifacts; failures preserved |
| **Operational recoverability** | Privileged steps are attended/idempotent where required; rollback/teardown paths exist |

Requirement C often **caps** how hard A and B can be pushed:

- Maximum A (pure live remote every open) can import host NFS/boot coupling and
  hard-mount stalls → weaker C unless fault/recovery is fully proven.
- Maximum B (durable local replicas everywhere) strengthens local I/O
  reliability for load but fails A and can hide catalog policy bugs.
- Ephemeral transfer designs improve isolation (C for running services) only if
  transfer/rematerialize paths are themselves fail-closed and tested.

Pulsar’s existing culture (topology identity, sealed manifests, preflight,
ownership-safe lifecycle, privacy-audited artifacts) is already a **C**
baseline. Any new weight path must not regress it.

### Tension across A, B, and C

| Tactic | A disk | B load | C reliability |
|---|---|---|---|
| Durable N local replicas | ✗ | ✓ cold local | Strong local restart; catalog policy wrong at scale |
| Live single-copy mount under vLLM | ✓ | Often ✗ cold | Owner/NFS/boot on critical path; hard-mount semantics |
| One-shot transfer + local load + purge | ✓ durable | Cold may be two passes; warm local ✓ | Running serve decoupled if release proven; transfer path must be solid |
| Warm retained staging | ✗ while retained | ✓ restart | Simple restart; disk policy must be explicit |
| Auto fallback to full replica | ✗ hidden | May “fix” load | **Fails C** (silent policy change) |
| Rank-sharded offline conversion | ✓ if not N-full | ✓ less I/O | Conversion/layout drift is a new failure class—needs seals/tests |

Score candidates on **A + B + C** together. An approach that is fast and
disk-cheap but flaky, silent, or unrecoverable is not shippable.

### Runtime presentation (implementation shape, not the product goal)

| Claim | What it optimizes | Durable disk |
|---|---|---|
| **Single-copy catalog** | Requirement A | ~1× per model (+ optional ephemeral staging) |
| **Live shared mount under vLLM** | Zero client tree during load | ~1× always; load time = remote I/O path |
| **Transfer then local open** | Decouple serving from NFS; local second-pass I/O | ~1× if staging purged |

The current experimental fabric ([WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md))
maximizes durable single-copy purity via live mounts, but can lose on
Requirement B for cold starts and couples faults to owner NFS/OS lifecycle.

This parked materialize redesign keeps Requirement A if staging is ephemeral,
and tries to help Requirement B on **warm/restart** (local open) while needing
an honest cold-start budget: either one efficient transfer-into-load path, or
accept transfer+load as the price of durable O(1) disk.

### Why this redesign was sketched

Physical fabric validation showed RoCE-backed reads and serving correctness are
achievable, but live mounts pull in host NFS lifecycle and can be slower than
local cold I/O. Once weights are resident, inference no longer needs checkpoint
pages; **catalog policy** and **time-to-healthy** are the product levers,
not steady-state decode.

Other approaches under discussion should be scored on **all three**
requirements before secondary ops preferences:

1. **A** — durable bytes per model as N grows (catalog capacity).
2. **B** — time-to-healthy (cold and warm), including bytes/rank and path
   bandwidth.
3. **C** — fail-closed behavior, fault/recovery matrix, lifecycle ownership,
   no silent fallback, evidence-backed claims.
4. Then implementation cost and host-coupling surface.

---

## 2. Goals and non-goals

### Goals

1. **Requirement A:** durable on-disk footprint per model stays **O(1)** in
   node count (authoritative owner/pool). Extra storage buys **more models**
   (or larger ones), not more replicas of the same ones.
2. **Requirement B:** reduce **model loading time** — wall-clock to first
   healthy multi-node service (cold and warm), via less redundant I/O, faster
   paths, better concurrency, and explicit warm policy—not via silent full
   N-replica staging.
3. **Requirement C:** **stable and reliable** operation — fail-closed checks,
   ownership-safe lifecycle, documented fault outcomes, recoverability, no
   silent transport or disk-policy fallback, and promotion only with
   reproducible evidence. Must not regress Pulsar’s existing control-plane
   reliability bar.
4. Keep a single authoritative sealed snapshot on an owner (or storage pool).
5. Prefer runtime independence: vLLM opens **local** files after transfer when
   that does not reintroduce durable N-copy policy (ephemeral staging + purge).
6. Keep transfer **measurable and fail-closed** (topology rails, digests, no
   silent TCP/control-LAN fallback for fabric-backed transfer).
7. After successful materialization and transfer release, a **running** service
   must not depend on a live client NFS mount; **restart** policy is explicit
   (rematerialize vs retain staging) and its load-time cost is documented.
8. Migrate from today’s `fabric` / `replicated` surfaces without rewriting
   topology discovery, NCCL selection, or profile status gates.
9. Preserve claim hygiene: sealed evidence, privacy-aware artifacts, and
   retained failure records.

### Non-goals (for this parked design’s v1)

- Beating replicated **resident** inference throughput after load (secondary;
  canary evidence already near parity for fabric vs replicated serving).
- Guaranteeing zero temporary client disk during transfer (ephemeral staging is
  allowed; durable N copies are not, under Requirement A).
- Claiming every cold start beats local N-replica I/O without a measured path
  (Requirement B is minimize and approach best practical alternatives, with
  evidence).
- Perfect availability of the weight plane during every owner reboot without a
  stated dependency model (C requires honesty about dependencies, not magic).
- Custom application-level tensor streaming into vLLM (unless a future decision
  chooses it for B).
- GPUDirect Storage / `nvidia-fs` as baseline.
- NVMe over Fabrics as baseline.
- Rank-sharded checkpoints (`sharded_state`) as required path (defer; strong
  candidate lever for Requirement B while preserving A; needs C seals/tests).
- Replacing the site `/mnt/Models` catalog story (evaluate separately on A+B+C).
- Automatic fallback from transfer failure to full replicated pull (violates A
  and C).
- Changing tensor-parallel geometry or NCCL data-plane selection.
- Immediate deprecation of live fabric before a separate decision.

---

## 3. Conceptual model

Split the overloaded `--weight-source` idea into orthogonal axes:

| Axis | Meaning | v1 values |
|---|---|---|
| **Weight source** | Where the **authoritative** snapshot lives | `local` (per-node HF cache) · `catalog` (site NFS path) · `owned` (single-copy owner tree) |
| **Weight runtime** | What the container bind-mounts at launch | `local` only in v1 |
| **Weight transfer** | How clients obtain a verified local tree | `none` · `copy` (SSH/control path) · `fabric` (RoCE NFS/RDMA **one-shot**) |

### Operator modes

| Mode | Source | Transfer | Runtime | Relationship to today |
|---|---|---|---|---|
| **A. Replicated (default)** | local per rank | `copy` or already present | local | `--weight-source replicated` |
| **B. Catalog** | `/mnt/Models/...` | none (pre-mounted) | catalog bind | NFS conf paths |
| **C. Owned + materialize** | owner sealed repo | `fabric` (or `copy`) once | local staging | **proposed replacement for live fabric serving** |
| **D. Live fabric mount** | owner | continuous NFS | mount path | current `--weight-source fabric` |

**Intended product arc if ever adopted:** keep A and B; develop C; deprecate D
only after C earns its own evidence. Until then, D remains the experimental
path in `WEIGHT_FABRIC.md`.

```text
Authoritative source (owned | local | catalog)
        │  sealed manifest (revision, files, sha256, sizes)
        │  weight-transfer: fabric (temporary NFS/RDMA) or copy
        ▼
Per-rank materialization root (local disk or tmpfs)
        │  complete tree + .pulsar/materialize.json
        │  states: absent → transferring → verifying → ready → pinned
        ▼
Docker bind → vLLM HF path → mmap → unified memory
        │  optional: release transfer plane (unmount / idle export)
        │  optional: purge staging after stop (policy)
        ▼
Serving independent of owner NFS (restart uses staging until purged)
```

**Invariant:** after `materialize` succeeds on a rank, that rank’s launch check
must not require NFS, export, or owner reachability—only local digests, free
space, and the usual topology checks for inference.

---

## 4. Directory and identity layout

### Owner (authoritative)

Reuse schema-2 fabric layout:

```text
$OWNER_CACHE/hub/models--org--name/
  refs/main
  snapshots/<rev>/...
  .pulsar/manifests/<profile>.manifest.json
```

### Client materialization root (new)

```text
$MATERIALIZE_ROOT/<profile>-<topology12>/<config_id12>/
  hub/models--org--name/
  .pulsar/
    materialize.json
```

Suggested defaults if implemented:

- `MATERIALIZE_ROOT`: `/var/tmp/pulsar-weights` (preferred over the durable HF
  home so “durable replica” checks stay meaningful).
- Path encodes profile, topology id prefix, and configuration id prefix so a
  topology or config change cannot silently reuse the wrong tree.

### `materialize.json` (sketch)

```json
{
  "schema_version": 1,
  "state": "ready",
  "profile": "qwen3-1.7b-2node",
  "model": "Qwen/Qwen3-1.7B",
  "configuration_id": "...",
  "topology_id": "...",
  "owner_node_id": "...",
  "revision": "...",
  "manifest_sha256": "...",
  "transfer": {
    "backend": "fabric",
    "rail_index": 0,
    "bytes_logical": 0,
    "hca_rx_bytes": 0,
    "started_at": "...",
    "finished_at": "..."
  },
  "policy": {
    "durable": false,
    "purge_after_stop": true
  }
}
```

States: `absent` → `transferring` → `verifying` → `ready` → `pinned` (container
running) → `purged`.

---

## 5. CLI surface (sketch)

| Command | Role |
|---|---|
| `pull-weights.sh <profile>` | Mode A (unchanged default) |
| `pull-weights.sh <profile> --weight-source owned` | Owner-only download + seal |
| `weight-fabric.sh configure / seal / apply / verify` | Transfer plane for backend `fabric` |
| `weight-fabric.sh materialize <profile>` | Transfer sealed tree to ranks; write ready stamp |
| `weight-fabric.sh release-transfer <profile>` | Unmount clients; optional export idle/stop **after** ready |
| `weight-fabric.sh purge-staging <profile>` | Delete materialize roots; refuse if pinned |
| `up.sh <profile>` | Replicated default |
| `up.sh <profile> --weight-mode materialize` | Launch from local staging |
| `up.sh <profile> --weight-source fabric` | Legacy live mount (D); deprecation warning if C exists |

Illustrative runbook (not enabled today):

```bash
scripts/weight-fabric.sh configure <profile> --owner <node-id> ...
scripts/weight-fabric.sh seal <profile>
scripts/weight-fabric.sh apply <profile> --interactive-sudo
scripts/weight-fabric.sh materialize <profile> --backend fabric --interactive-sudo
scripts/weight-fabric.sh release-transfer <profile> --yes
scripts/up.sh <profile> --weight-mode materialize
```

Internal normalization if implemented:

```text
WEIGHT_MODE=replicated|materialize|live-fabric|catalog
```

---

## 6. Config sketch (schema 3)

Extend site-local `.weight-fabric/<profile>.json` without breaking schema-2 live
configs:

- Keep existing `transport` for NFS/RDMA apply/unmount/teardown.
- Add optional `materialize` block:

```json
{
  "schema_version": 3,
  "materialize": {
    "enabled": true,
    "root": "/var/tmp/pulsar-weights",
    "backend_default": "fabric",
    "require_release_before_launch": false,
    "purge_after_stop": true,
    "verify": "full-sha256"
  }
}
```

`configuration_id` should incorporate materialize policy so artifacts stay
bound to the exact claim.

Recommended defaults **if** this design is adopted later:

| Knob | Recommended default | Why |
|---|---|---|
| `purge_after_stop` | `true` | Protects the primary goal: durable disk stays ~1× on the owner, not N× staging |
| `require_release_before_launch` | `false` then tighten | Debug convenience first; independence later |
| Staging location | `/var/tmp/pulsar-weights` | Outside durable HF home; easier to treat as ephemeral |
| First canary scope | Serving ranks only | Avoid materializing idle storage ranks by default |
| Retain warm staging | opt-in only | Warm restart vs catalog capacity is an explicit operator trade |

---

## 7. Lifecycle

```text
configure → seal → apply (transfer plane up)
                 → materialize (per client)
                 → optional release-transfer
                 → up / launch (local binds only)
                 → down / unpin
                 → optional purge-staging
```

### Launch checks by mode

| Mode | Required |
|---|---|
| replicated | Complete local hub tree per serving rank (today) |
| materialize | `materialize.json` `ready` + manifest match; NFS **not** required if released |
| live-fabric | Current fabric check (route, mount, export, no durable replica) |
| catalog | Conf path readable via `MODELS_NFS` |

### Container labels (sketch)

```text
pulsar.weight.mode=materialize|replicated|live-fabric|catalog
pulsar.weight.owner=<node_id>
pulsar.weight.config=<config_id>
pulsar.weight.materialize=<config_id12 or none>
pulsar.weight.transfer_released=true|false
```

Inventory can distinguish “depends on owner for **rematerialize**” from
“depends on owner for **live I/O**.”

---

## 8. Transfer backends

### `fabric` (reuse current plane)

1. `apply` installs owner export and client hard mounts (schema-2 subtree).
2. `materialize` copies the **manifest file set** from the mount into the
   materialize root (not a blind full-tree guess).
3. Record HCA / control counters around the copy (reuse fabric benchmark
   provenance ideas).
4. Full SHA-256 verify; atomic publish of `materialize.json` `ready`.
5. Optional `release-transfer` unmounts clients.

Owner rank may reference the authoritative path with a stub ready record
(`backend=local-owner`) to avoid double disk use.

### `copy` (optional same API)

SSH stream of the sealed tree over the control path. Same ready stamp;
fabric HCA proof not claimed. Longer term, replicated pull could share this
code path into durable `$HF_CACHE`.

---

## 9. Component touch list (if ever implemented)

| Component | Change |
|---|---|
| `scripts/weight_fabric.py` | materialize / release / purge; schema 3 |
| `scripts/weight-fabric.sh` | new subcommands |
| `cluster/start-cluster.sh`, serve path | bind materialize hub path for mode=materialize |
| `scripts/up.sh`, `check-weights.sh`, preflight | `--weight-mode`; legacy fabric alias |
| `scripts/down.sh` | unpin; optional purge hook |
| `scripts/inventory.sh` | mode + transfer_released + staging |
| Docs / selftests | new fixtures; remap evidence matrix |

**Unchanged:** topology discovery, NCCL rail selection, profile `STATUS` gates,
wizard default (replicated).

---

## 10. Suggested implementation phases (parked)

These phases are a future breakdown only. Do not treat them as committed work.

| Phase | Intent |
|---|---|
| 0 | Policy: claim shift to “single-copy distribution + local load”; live mount stays experimental |
| 1 | Materialize store + JSON + selftests (no launcher change) |
| 2 | Launch on materialize; Qwen canary correctness vs replicated / live fabric |
| 3 | `release-transfer`; launch independent of NFS; post-release owner outage tests |
| 4 | Docs, deprecation alias for live mount, operator runbook |
| 5 | Evidence matrix remap and promotion criteria for materialize (separate from live fabric) |
| 6 | Optional: ephemeral apply-only-during-materialize; unify replicated as `copy` backend; sharded_state later |

### Evidence remap (if C is ever promoted)

| Live-fabric style gate | Materialize analogue |
|---|---|
| Cold fabric I/O benchmark | Transfer benchmark during materialize (HCA proof) |
| Cold fabric vLLM startup | Local load after materialize (optional cache drop on staging) |
| Serving A/B vs replicated | Same, from materialize roots |
| Link / NFS faults during load | Faults during **materialize** only |
| Owner reboot during serve (post load) | Post-**release** owner reboot while service up / while staging present |
| No durable client replica | Staging explicit and purgable; owner remains sealed authority |

Historical live-fabric artifacts remain historical; they must not be rewritten
as materialize passes.

---

## 11. Failure matrix (target claim)

| Scenario | Expected under materialize design |
|---|---|
| Materialize interrupted | No ready stamp; launch refused; retry materialize |
| Link loss during transfer | Materialize fails; no service started |
| Owner reboot after release, service up | Service continues |
| Owner reboot after release, service down, staging ready | Relaunch from staging without owner |
| Owner reboot before materialize complete | Fail closed; recover owner/apply; rematerialize |
| Purge while running | Refused (pinned) |
| Digest mismatch | Fail closed; rematerialize |

---

## 12. What this design does *not* claim

- It does not claim live NFS/RDMA under vLLM is wrong for experiments or
  traffic proof.
- It does not claim better resident inference throughput than replicated
  (existing canary evidence showed near parity after load).
- It does not claim lower cold-start latency than local replicas (transfer
  still costs fabric bandwidth; local load follows).
- It does not authorize implementation, default changes, or deprecation of
  `--weight-source fabric`.

---

## 13. Open questions for later discussion

Recorded so a future revisit does not invent answers:

1. **Scorecard:** rank alternatives on (A) durable bytes per model as N grows,
   (B) time-to-healthy cold/warm, and (C) reliability/fault matrix, before
   secondary implementation preferences.
2. What is the reference baseline for B—replicated local cold start, live
   fabric cold start, or “rsync then local”? Must a winner beat all of them?
3. What is the minimum **C** bar for promotion (which fault scenarios must
   pass; which may remain documented limitations)?
4. Staging durability: always purge after stop (favor A), or opt-in warm
   staging (favor B on restart)—and how C documents the dependency?
5. Is **zero client disk at serve time** required, or is **ephemeral staging
   during load** acceptable if A holds, B is measured end-to-end, and C’s
   lifecycle is proven?
6. How to avoid **double I/O** (network to staging, then staging to memory) on
   cold materialize paths—stream, splice, or accept two passes with a budget?
7. Must transfer be released before launch, or is “materialize with mount still
   up” allowed for debug?
8. Staging filesystem: `/var/tmp` vs dedicated disk vs tmpfs for small canaries?
9. Materialize all `storage_nodes` or serving ranks only?
10. Relationship to site catalog models that already use a different NFS path
    (often closer to single-copy durable storage; load time may be LAN-bound).
11. Whether rank-sharded conversion should leapfrog pure materialize for large
    models (strong B lever; still need durable catalog story for A and seals
    for C).
12. Multi-owner or storage-pool topologies: one Spark as catalog disk server vs
    dedicated storage node vs external pool—O(1) durable copies relative to
    **serving** node count.
13. How other approaches under discussion score on **A + B + C** together.

---

## 14. Decision log

| Date | Decision |
|---|---|
| 2026-08-08 | Captured as **under future consideration**. No implementation. Other approaches to be discussed first. Current experimental path remains [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md). |
| 2026-08-08 | **Requirement A:** replicated N× disk wastes catalog capacity; single-copy should let added storage hold more models, not pay another full copy per node. Materialize only serves A if client staging is ephemeral/purged. |
| 2026-08-08 | **Requirement B:** reduce model loading time (time-to-healthy). Co-equal with A. Live remote cold I/O can hurt B; designs must state cold/warm budgets, bytes/rank, and double-pass risk. |
| 2026-08-08 | **Requirement C:** stability and reliability—fail-closed, lifecycle-safe, documented faults, no silent fallback, evidence-backed promotion. Co-equal with A and B; caps unsafe optimization of A/B. Score alternatives on **A + B + C**. |
| 2026-08-08 | Added §15 **Ideas to explore**: multi-option rethink against A+B+C. Not a decision; original materialize sketch (§3–11) remains one parked mechanism. |

---

## 15. Ideas to explore (discussion set)

> **Status:** exploration only. This section records options discussed after
> fixing requirements A/B/C. It does **not** replace or approve the materialize
> sketch in §3–11, and it does **not** change live fabric or defaults.
> Settle a direction before promoting any option into an active design.

### 15.1 Layering (how to think before picking a mechanism)

```text
1. Catalog (durable)     ← Requirement A
2. Load path (to memory) ← Requirement B
3. Runtime dependency    ← Requirement C (“what can break after start?”)
```

| Layer | Question |
|---|---|
| Catalog | How many full trees exist on disk when nothing is running? |
| Load | What bytes move, over which path, on cold start? |
| Runtime | After `/health`, does serving still need owner/NFS/staging? |

Designs that conflate these three are hard to score. Prefer explicit layering.

### 15.2 Option set

#### Option 1 — Live fabric as catalog + load (current experiment, harden C)

One owner tree; ranks open NFSv4.2/RDMA under vLLM for load (and often restart).

| Axis | Assessment |
|---|---|
| A | Strong — durable ~1× |
| B | **Weak cold** — cold time-to-healthy often worse than local replicas when most bytes come over fabric (canary cold fabric I/O slower than cold local; resident serving near parity) |
| C | Hard — owner/NFS/boot on critical path until fault matrix fully proven |

**Fits:** refuse any client disk, even temporary.  
**Weak for:** product default when cold start and owner lifecycle dominate pain.  
**Role:** storage/traffic **proof**, not necessarily the long-term product path.

#### Option 2 — Naive materialize (transfer → local open → purge)

Mechanism sketched in §3–11: one-shot transfer, local launch, optional release.

| Axis | Assessment |
|---|---|
| A | Strong **if** purge default |
| B | Fragile cold if transfer + local load are **two full passes** |
| C | Strong for **running** serve after release; restart needs rematerialize or retained staging |

**Fits:** owner independence after start; accept staging window.  
**Weak for:** cold B unless double I/O is engineered away.

#### Option 3 — Hot-model staging + single-copy catalog (product-shaped)

**Policy-first**, transport-second:

1. **Catalog** = sealed owner (or storage pool): many models, ~1× each (A).
2. **Activate** = measured multi-rank bring-up of **one** (or K) model into
   client hot staging and/or page cache via fabric or copy.
3. **Serve** = vLLM opens local (or agreed) paths; weights resident in memory;
   **release** library mount when claimed independent.
4. **Stop** = default **purge** staging (protect A); **pin** optional for warm
   restart (explicit A↔B trade).
5. **Switch model** = purge previous hot set, activate next from catalog.

Client disk is a **budget for active/pinned models**, not a replica farm of the
whole library. Materialize (§3) is one **mechanism** inside this policy; live
fabric is a **transport** for activate—not the product identity.

| Axis | Assessment |
|---|---|
| A | Strong for library; client disk bounded by K / pins |
| B | Medium → strong with parallel RoCE activate, later fewer bytes/rank |
| C | Strong if dependency modes are explicit and release/purge proven |

**Noted in discussion as best product-target candidate to explore further.**

#### Option 4 — Thin compute + dedicated storage owner/pool

Catalog on fat disk node(s) or external pool; serve Sparks only stage active jobs.

| Axis | Assessment |
|---|---|
| A | Strong at large N — storage scales with disks you add |
| B | Same levers as Option 3 on the activate path |
| C | Clear dependency class (storage node vs compute) |

**Fits:** large fleets. Design owner IDs so owner need not be a TP rank later.
Not required for 2–3 node labs on day one.

#### Option 5 — Rank-sharded checkpoints (`sharded_state` / offline)

Each rank loads only its TP slice.

| Axis | Assessment |
|---|---|
| A | Strong if full N-copies of full trees are avoided |
| B | Strong — less bytes/rank, less multi-rank over-read |
| C | Medium until shard set + TP layout are sealed and tested |

**Role:** Phase-2 **B** lever under Option 3/4, not the first control-plane rewrite.

#### Option 6 — On-demand pull + purge (boring MVP)

Library single-copy; job start copies (SSH) or fabric-pulls to ranks; serve
local; purge on stop.

| Axis | Assessment |
|---|---|
| A | Strong for library; temporary N only for active job |
| B | Medium — copy may use control path until fabric backend exists |
| C | Strong / simple — easy to reason about |

**Role:** incremental path. Fabric accelerates B; not the product name.

#### Option 7 — Unify catalog backends

One **model library** concept over `site-nfs` (`/mnt/Models`), `owned-hf`
fabric/owner tree, later object—same activate/purge lifecycle.

| Axis | Assessment |
|---|---|
| A | Strong — one capacity story |
| B | Backend-dependent |
| C | Strong operator UX — fewer mental models |

**Role:** organizational early design choice even if transports differ.

### 15.3 Comparative sketch

| Option | A | B | C | Notes |
|---|---|---|---|---|
| 1 Live fabric forever | Strong | Weak **cold** | Hard (owner/NFS) | Proof / niche |
| 2 Naive materialize | Strong if purge | Weak if double pass | Strong after release | Needs B engineering |
| **3 Hot-model staging + catalog** | Strong | Medium→strong | Strong if explicit | **Product-target candidate** |
| 4 Thin compute + storage pool | Strong at scale | Same as 3 | Clear deps | Topology evolution |
| 5 Sharded_state | Strong | Strong | Medium until sealed | Phase 2 for B |
| 6 On-demand pull + purge | Strong library | Medium | Strong / simple | Good MVP |
| 7 Unify catalog backends | Strong | Depends | Strong UX | Do early in design |

**“Weak cold”** means cold start/load is a relative weak spot for Requirement B
(e.g. remote full-snapshot I/O slower than local), not “unreliable” (C) and not
“slow after the model is already resident.”

### 15.4 North-star statement (candidate, not decided)

> Single-copy **model library** on O(1) durable storage; nodes stage only what
> is **running** or **explicitly pinned**; load is a measured **activate** step;
> serve does not depend on the library mount once that independence is claimed;
> optional RoCE accelerates activate; later sharding reduces bytes/rank.

### 15.5 Candidate phased exploration (not committed work)

| Phase | Intent |
|---|---|
| 0 | Product rules only: A/B/C acceptance bars (durable client trees, time-to-healthy budget, post-release owner independence, no silent replicated fallback) |
| 1 | MVP for A+C, B good enough: on-demand pull-or-fabric → local path → launch → purge on stop; **no** live mount under vLLM required for promotion |
| 2 | B without killing A: parallel RoCE activate + HCA proof; reduce double pass; optional pin for warm restart |
| 3 | B at large models: sharded_state / reduced per-rank bytes; seal shard set to profile TP |
| 4 | Scale catalog: storage-node / multi-owner pool; compute stays thin |
| Last | Deprecate long-lived live mount under vLLM only if Phases 1–2 win on A+B+C |

### 15.6 Design principles (settle before locking a direction)

1. **Library ≠ runtime path** — durable single copy is a catalog property.
2. **At most K hot models on client disk** — default K=1 or a pinned set.
3. **Activate is first-class** — measured, fail-closed, not hidden only inside
   `docker run`.
4. **Dependency modes are explicit** — whether serving still needs the library
   after start.
5. **B is end-to-end time-to-healthy**, not only microbench GiB/s.
6. **C forbids silent N× disk** — replicas only by operator choice; inventory
   shows them.
7. **Prefer boring recovery** — rematerialize + relaunch over clever hard-mount
   resume when ops clarity matters.
8. **Fabric is a transport**, not the product name.

### 15.7 Deprioritize for now

- Promoting live-mount fault gates as the main product bet.
- Full model-registry microservice before activate/purge semantics exist.
- GDS / NVMe-oF as baseline on Spark.
- Unifying on materialize without a cold-start double-I/O plan.

### 15.8 Discussion prompts (to settle a direction)

1. Is temporary client disk during an active job OK?
2. Is warm restart without owner a must?
3. MVP backend: SSH copy or fabric-first?
4. Catalog home: owner HF only, site `/mnt/Models`, or unified library API?
5. Success bar for B: beat live fabric only, or approach replicated cold within X%?
6. Minimum C matrix for “usable”: must-pass faults vs documented limits?

### 15.9 Working bottom line (discussion)

Given A+B+C, strongest **product** story under discussion is neither “always
NFS under vLLM” nor “always N full replicas,” but:

> **Single-copy library + explicit activate + serve from memory/local hot
> staging + purge/pin policy + optional RoCE + later shard for large models.**

Materialize remains a **mechanism**; live fabric a **transport/experiment**;
reliability a **dependency contract**; storage and load time what operators feel.
