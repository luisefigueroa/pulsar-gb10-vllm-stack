# Prerequisites — run the scripts on a DGX Spark

Single place for what must be true before `./serve.sh` or `cluster/*.sh`
work. Hardware numbers live in [HARDWARE.md](./HARDWARE.md); day-to-day
ops in [OPERATIONS.md](./OPERATIONS.md); multi-node detail in
[MULTINODE.md](./MULTINODE.md). This page is the gate checklist.

Serving results are validated on 2× NVIDIA DGX Spark (GB10), Ubuntu, driver
580.x, CUDA 13.0, Docker 29.x + NVIDIA Container Toolkit. The control plane
supports larger confirmed GB10 topologies, but a node count is serveable only
when an exact profile defines an executable recipe and its concrete topology,
capacity, identity, runtime, security, and lifecycle checks pass. Hostnames are
not part of cluster qualification. Host NCCL is not required; images provide it.

Here, a profile is a released spec id and its `review.status` is the
display-only ADR 0017 review. It is advisory, not a serving gate, and is not the
`Validated` Model Serving Release decision defined by
[ADR 0004](./decisions/0004-model-serving-release-validation.md). The separate
release-descriptor, frozen-contract, immutable run-record, evidence-bundle, and
reviewed-decision schema version 1 contracts are implemented. Read-only
persistence and verification of those objects is implemented under
`models/model-serving-releases/`; the tracked registry is empty.
Local ADR 0004
evidence-capture candidate persistence is implemented and remains
draft. The catalog shows reviewed status only for an explicitly bound profile;
no current profile sets `MODEL_SERVING_RELEASE_ID`. Maintainer-only staging can propose registry objects; a
successful local command is not trusted until repository review and merge.
Serving permission is status-independent. The corrected ADR 0004 schemas remain
version 1 because no ADR 0004 object was issued or persisted before the
correction. The retired lab expected-identity files are not retained in this
reset.

A Model Serving Release is the immutable combination of exact model identity,
serving recipe, runtime/image identity, and supported hardware geometry. Any
change creates a new release. Structural checks can reject an incompatible
runtime, architecture, capacity, or TP/PP geometry, but they do not prove
physical behavior. Physical serving-integration and geometry evidence must be
collected on the declared DGX geometry; documentation and selftests cannot
supply it. Catalog acquisition and preparation establish exact content and the
qualification barrier only, not a validation criterion.

Validation Contract scopes are fixed: stability, accuracy, throughput,
latency, and strict same-boot are `model-qualification`; serving integration is
`serving-integration`; provenance/security and physical geometry are
`release-promotion`. Operator command evidence is recorded structurally and
uses allowlisted programs, SHA-256-shaped program identities, closed
operations/resources, and typed criterion or protected site references. Each
post-barrier non-preparation run must account exactly for its declared
attempted criteria. Release/contract values and run evidence are structurally
screened, but trusted capture and publication privacy review remain required.

---

## Quick checks

| Mode | Minimum path |
|------|----------------|
| One rank | Confirm membership with `scripts/detect-fabric.sh --write-topology`, acquire and prepare the exact model as described in §2, then run `scripts/up.sh <exact-profile> --dry-run` |
| Multiple ranks | Confirm membership and enroll SSH identity as described in §3, acquire and prepare the exact model as described in §4, then run `cluster/preflight.sh <exact-profile>` |

`cluster/preflight.sh` exits non-zero if confirmed capacity, pairwise RoCE,
SSH, GPU, Docker, images, weights, memory, control bindings, or stale
containers fail. Fix those before `start-cluster.sh`. There are no baked-in
cluster addresses: every multi-node setup confirms a gitignored
`.cluster-topology.json`; `HEAD_IP`/`WORKER_IP` environment variables never
confirm membership and do not construct topology.

---

## 1. Hardware & host software (every node)

| Requirement | Notes |
|-------------|--------|
| **NVIDIA GB10** | `nvidia-smi --query-gpu=name --format=csv,noheader` → `NVIDIA GB10` (sm_121). Preflight enforces this on every exact active rank. Probe constants live in [PLATFORMS.md](./PLATFORMS.md). |
| **Driver + CUDA 13 host stack** | Validated: driver **580.173.02**, CUDA **13.0** (host toolkit 13.0.3). |
| **Docker + NVIDIA Container Toolkit** | `docker info` must show the **nvidia** runtime. Default host runtime can stay `runc`. |
| **GPU containers work** | `docker run --rm --gpus all <cuda-image> nvidia-smi` |
| **Memory headroom** | ~**100+ GiB** `MemAvailable` before launching a big model. Preflight warns under 100 GiB. Unified LPDDR5X is shared by CUDA, OS, and page cache — no separate VRAM (`nvidia-smi` memory is N/A). |
| **One heavy model per node** | A second workload will swap the box. After other work: `sync; echo 3 \| sudo tee /proc/sys/vm/drop_caches` |

Not required on the host: vLLM Python install, Ray, host NCCL, jumbo MTU,
GPUDirect RDMA.

One lab-only addition: the baseline-v1 accuracy gate reads the pinned GSM8K
file, which is Parquet, so the Python 3 environment that runs
`validate/gsm8k_eval.py` needs `pyarrow`. The DGX OS system Python is
externally managed; create a venv once and run the producer with it:

```bash
python3 -m venv "$HOME/.pulsar-lab-venv" && "$HOME/.pulsar-lab-venv/bin/pip" install pyarrow
"$HOME/.pulsar-lab-venv/bin/python3" validate/gsm8k_eval.py ...
```

Serving, the model library, and every other producer stay on the standard
library and need no package.

`./pulsar` opens the workflow menu. `./pulsar wizard` (or
`./wizard.sh`) is the direct serve/switch shortcut. Both use the vendored Gum
v0.17.0 Linux ARM64 binary by default (shared `scripts/ui.sh`); no package
installation is required.

| Variable | Effect |
|---|---|
| `GUM=0` | Plain Bash menus (uncolored; good for scripts/selftests) |
| `GUM_BIN=/path/to/gum` | Override gum binary (color-enabled mode only) |
| `NO_COLOR`, `TERM=dumb`, `PULSAR_COLOR=never` | Force plain Bash menus (Gum not used; no pink defaults) |
| `PULSAR_ACCENT` | Override accent when Gum is color-enabled (default ANSI bright blue `12`) |

When color is allowed, Gum accents use terminal-palette blue (not Charm
pink/purple defaults). Forced no-color never calls Gum with empty style flags.
See `THIRD_PARTY_NOTICES.md`. Prefer `./pulsar wizard` — `./ wizard.sh` (space
after `./`) runs the directory `./` and fails with `Is a directory`.

---

## 2. First one-rank serve

Every serving profile, including a one-rank profile, needs confirmed cluster
membership and model files prepared by the model library. A cache directory or
NFS path by itself is not model identity and cannot be launched.

Minimum requirements:

1. **Docker and NVIDIA access**
   - The host must permit `--gpus all`, `--ipc=host`, the memlock/stack
     ulimits, and the NVIDIA runtime.
2. **Confirmed membership**
   - Run `scripts/detect-fabric.sh --write-topology`. One machine is a valid
     confirmed topology; Pulsar does not synthesize standalone membership.
3. **The selected image**
   - Default mainline: `vllm/vllm-openai:v0.26.0` (override with
     `VLLM_IMAGE_MAINLINE` in `.env`).
   - If the profile sets `IMAGE=`, stage that exact digest; see
     [BUILD.md](./BUILD.md).
4. **One receipt-backed home for the exact revision**
   - A **home** is the one complete on-disk copy of that revision. Pulsar also
     requires the recorded download file list and hashes (the **receipt**) plus
     the private record naming that exact live directory (**occupancy**).
   - If no home exists, the selected rank needs upstream access, its own
     Hugging Face authentication when required, and the modern `hf` CLI. Pulsar
     accepts `hf` on `PATH` or its managed `$HOME/.hf-cli/venv/bin/hf`;
     older Hugging Face CLI commands are not supported.
   - `home add --revision ... --plan` is the definitive read-only dependency
     check. It resolves the complete upstream Git/LFS file list through the
     modern CLI's Python environment and accepts no token argument.
5. **A prepared runtime view**
   - Run catalog refresh, then `prepare`. Preparation verifies the receipt and
     creates the exact read-only runtime view; it does not start the server.
6. **Optional `.env`**
   - Copy `.env.example` only for path, image, API-auth, or discovery-candidate
     overrides. Confirmed membership is never stored there. Pulsar does not
     move Hugging Face credentials between ranks.

For a new home, use the exact commit and rank printed by the plan:

```bash
cp .env.example .env   # optional overrides only
scripts/doctor.sh
scripts/detect-fabric.sh --write-topology
scripts/release.sh list          # released spec ids: a profile is a spec id
scripts/list-models.sh --serving

scripts/model-library.sh home add <spec_id> \
  --revision <selector> --plan --json
scripts/model-library.sh home add <spec_id> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json
scripts/model-library.sh catalog refresh
scripts/model-library.sh prepare <spec_id> --yes

scripts/up.sh <spec_id> --dry-run
./pulsar start <spec_id>
./pulsar status <spec_id>
./pulsar stop <spec_id>
```

The deployment overlay `.pulsar-overlay.json` (gitignored, at the repository
root) is optional: it sets the port, the served model name, the cache root,
and a placement per spec. Without it every spec serves with the defaults
(port 8000, served name = model id) and the start banner says
`overlay=defaults`.

When a compatible receipt-backed home already exists, do not download it
again. Refresh the catalog, verify or prepare the exact spec, and start.
`./serve.sh <spec_id> -d` remains the supported low-level one-rank launcher,
but it consumes the same confirmed topology and prepared runtime view.

Do not expose `:8000` outside the trusted lab network without authentication;
see [SECURITY.md](../SECURITY.md). Stop through `./pulsar stop` or
`scripts/down.sh`, which revalidate ownership instead of deleting a container
by name.

Cold load can take minutes. Watch `docker logs -f`
for `Loading weights took ...` before assuming a hang. Health start period
in the tooling is 900 s for this reason.

---

## 3. Confirmed multi-node topology (`cluster/*.sh`)

Extra requirements beyond §1 apply to every node on which an exact profile will
run. Run discovery on the machine that will be rank 0 and host the API.

### 3.1 Discovery, identity, and trust

```bash
# Preview only; no files changed.
scripts/detect-fabric.sh --json

# Add arbitrary names or addresses when mDNS is incomplete.
scripts/detect-fabric.sh --candidate atlas-a --candidate 192.0.2.42

# Review ranks and confirm exact membership.
scripts/detect-fabric.sh --write-topology
```

Discovery accepts differently named systems. It combines mDNS SSH services,
explicit candidates, `CLUSTER_CANDIDATES`, and an existing manifest, then
requires each node to prove aarch64, exact `NVIDIA GB10`, Docker NVIDIA
support, a distinct machine identity, a control address, and active addressed
RDMA links. It selects a full mesh containing local rank 0 and verifies every
shared rail in both directions for every pair.

SSH must be key-based and non-interactive. Stack probes use `BatchMode=yes`,
timeouts, liveness bounds, and existing `known_hosts`. Enroll keys normally;
`--accept-new-host-keys` is an explicit one-time TOFU option. mDNS is candidate
discovery, not a trust or membership decision.

**SSH identity enrollment policy (promotion requirement):** first-time setup
may automatically discover a node's alias, control/RoCE endpoints, and
presented public host keys, but trust must still be confirmed by the operator
or normal OpenSSH enrollment. The intended topology contract records the
confirmed `ssh_host` alias and accepted key fingerprints beside the immutable
node ID. SSH to a control or RoCE IP then uses that alias as `HostKeyAlias` with
strict checking, so every plane proves the same node identity.

This fingerprint binding and doctor drift classification are not yet complete
across all command paths. Until they are, treat SSH-over-RoCE copy as
experimental. A changed key must stop setup/preflight and trigger out-of-band
verification plus explicit re-enrollment; neither `doctor` nor discovery may
auto-accept it as an address refresh.

The confirmed `.cluster-topology.json` is written atomically, mode 0600, and is
gitignored. It records per-rank SSH target, control IP/interface, node identity,
HCAs, and pairwise rails. An unverified manifest cannot load. Topology cannot be
replaced while stack-managed containers are running.

### 3.2 Network and RDMA

| Requirement | Check |
|---|---|
| Full-mesh RoCE | Every selected rank pair has at least the profile-required `MIN_RAILS_PER_PAIR`; discovery and preflight ping every rail both ways |
| Active RDMA links | `rdma link show`; each selected node must expose enough addressed active HCAs |
| RDMA devices in containers | `/dev/infiniband` exists; launchers pass `--device /dev/infiniband` |
| Control binding | Recorded control IP is still bound to the recorded interface on each rank |
| Master port | `MASTER_PORT=29500` by default on rank 0 |

The rank 0 control IP is the rendezvous address. Gloo and socket bootstrap use
each node control interface; NCCL payloads use the recorded per-node HCAs with
`NCCL_NET=IB`. The control LAN is therefore a launch dependency: firewall,
routing, DNS/SSH, congestion, or high latency can affect rendezvous. Tensor
traffic is kept on verified RoCE instead of silently falling back to that LAN.

### 3.3 SSH, artifacts, and launch

| Requirement | Notes |
|---|---|
| Key-based SSH from this node | Required to every other active cluster node |
| Docker + NVIDIA support | Required independently on every node used by the profile |
| Same image | `scripts/sync-image.sh <profile> --pull --yes` stages every required node |
| Complete model files | `home add --revision` plan + exact-commit execution, catalog refresh, and `prepare` publish verified views on every rank used by the profile (ADR 0006) |
| Retired live NFS serving | Not a serving path (ADR 0005). The leftover teardown helper is removed (SIM-12). History: `WEIGHT_FABRIC.md`. |
| No stale managed container | A leftover container can retain rendezvous/RDMA state; stop the exact profile before relaunch |

```bash
scripts/check-image.sh <profile>
scripts/check-weights.sh <profile>
cluster/preflight.sh <profile>
cluster/start-cluster.sh <profile>
cluster/stop-cluster.sh <profile>
```

Other nodes used by the profile start headless first; this node starts last and
serves the API. The profile contract requires `TP × PP == NODES`, native `mp`,
and an explicit topology class/rail minimum. Extra discovered nodes stay idle.
The wizard offers every exact serving profile that fits capacity, displays its
status and caveats, and does not infer a larger geometry.

`HEAD_IP`/`WORKER_IP` environment variables never confirm membership and do
not construct topology: multi-node launch, preflight, and cluster start refuse
without a confirmed manifest. Confirm membership with `--write-topology`.

---

## 4. Storage layout

| Path | Role |
|------|------|
| `$HOME/.cache/huggingface` | Default root for receipt-backed Hugging Face homes; arbitrary cache presence is not serving identity |
| `.weight-fabric/` | Gitignored leftover dir if a site still has one; not an operator command (SIM-12) |
| `/mnt/Models` | Conventional later operator-chosen directory; never an implicit live recovery root and never a live serving source |
| Docker image store | Multi‑GB images on **each** node that will run a container |

Copy `.env.example` to override `HF_CACHE`, image pins,
auth, or discovery candidate hints. Set cold recovery storage with
`./pulsar configure cold-storage` (`PULSAR_COLD_ROOT` only; empty disables;
unset means not-configured). Confirmed membership is not stored there.
Doctor only inspects `HF_CACHE`; it never creates or modifies the path. A
missing cache is reported as a warning, while model download or preparation
creates the path when the selected workflow requires it.

### Weight acquisition (common cases)

```bash
# The model library is the only acquisition path (ADR 0006).
# Inspect the Hugging Face file plan first, then confirm the exact commit
# reported by that plan.
scripts/model-library.sh home add <profile> \
  --revision <selector> --plan --json
scripts/model-library.sh home add <profile> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json
scripts/model-library.sh catalog refresh
scripts/model-library.sh home verify <model_id@exact-commit> --json
```

The selected rank needs the modern `hf` CLI, upstream access, and its own
Hugging Face authentication when the repository is gated. The normal view uses
width-aware Pulsar sections instead of streaming third-party progress
renderers. `PULSAR_VERBOSE=1` is intended for diagnosis.
For a one-node profile, `home add` defaults to the eligible confirmed rank with
the most free space and `--node RANK|NODE_ID` may select any exact confirmed
rank. Multi-node profiles remain limited to their exact serving geometry. An
explicit override never falls back. The command uses
same-filesystem private staging and full verification before atomic
publication. Acquisition verifies the complete upstream inventory and every
file, writes an immutable receipt, and requires receipt-backed offline
verification for later reuse. It creates no Model Serving Release decision,
serving permission, or physical claim.

After acquisition, `scripts/model-library.sh prepare <profile> --yes`
publishes the exact runtime views the launch checks require.

---

## 5. Adapting another GB10 cluster

1. Enroll key-based SSH host keys from the future rank 0 to every candidate.
2. Run read-only discovery with mDNS, arbitrary `--candidate` values, or
   `CLUSTER_CANDIDATES`; inspect every accepted and rejected system.
3. Confirm the exact full-mesh membership with `--write-topology`.
4. Stage the selected exact profile image and weights to its required ranks.
5. Re-run `cluster/preflight.sh <profile>` until it is green.
6. Treat a new node count as unvalidated until a separate profile completes
   `REVALIDATE.md` and is promoted in `VALIDATION.md`.

Single-node only needs §1–§2; multi-node also needs §3. Discovery capacity is
not permission to serve an unmeasured geometry.

---

## 6. Checklist (print / paste)

**Single-node**

```text
[ ] nvidia-smi → NVIDIA GB10
[ ] docker + nvidia runtime; docker run --gpus all works
[ ] one-node .cluster-topology.json explicitly confirmed
[ ] exact image present for the selected profile
[ ] modern hf available on the selected rank when a new home is required
[ ] receipt-backed home registered by catalog refresh
[ ] exact profile prepared by the model library
[ ] ~100 GiB free; no second heavy GPU workload
[ ] scripts/up.sh <profile> --dry-run passes
```

**Multi-node (add)**

```text
[ ] key-based SSH + known_hosts from rank 0 to every candidate
[ ] detect-fabric preview accepts the intended GB10 nodes only
[ ] every selected pair has the required verified RoCE rails
[ ] .cluster-topology.json was explicitly confirmed (and remains gitignored)
[ ] /dev/infiniband, Docker NVIDIA, image, and weights on every exact rank
[ ] profile has an earned status for this exact NODES / TP / PP geometry
[ ] no stale vllm-cluster-* containers
[ ] cluster/preflight.sh <profile> exits 0
```

---

## Related docs

| Doc | When |
|-----|------|
| [HARDWARE.md](./HARDWARE.md) | Measured bandwidth, RoCE map, storage |
| [MULTINODE.md](./MULTINODE.md) | Discovery/manifest contract, native `--nnodes`, validation policy |
| [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) | Superseded live NFS/RDMA serving notes and leftover teardown (ADR 0005) |
| [decisions/0005-reject-live-nfs-rdma-serving.md](./decisions/0005-reject-live-nfs-rdma-serving.md) | Reject live-mount as a serving runtime source; keep ssh-roce / NCCL / topology |
| [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) | **Canonical architecture** — durable home, rank-local views, validation identity, preparation/hot/pin policy; the model library is the only weight mechanism (ADR 0006) |
| [decisions/0001-model-library-home-view-and-validation-identity.md](./decisions/0001-model-library-home-view-and-validation-identity.md) | Accepted rationale: exact-content home symlink, working copies on non-home ranks only, receipt/occupancy identity and serve-time witness (lab expected-identity files retired by ADR 0012) |
| [decisions/0002-subsystem-qualification-boundaries.md](./decisions/0002-subsystem-qualification-boundaries.md) | Accepted rationale: catalog, integration, model, and release evidence scopes plus causal invalidation |
| [decisions/0003-explicit-model-preparation-transport.md](./decisions/0003-explicit-model-preparation-transport.md) | Accepted rationale: explicit reviewed-profile preparation uses topology-bound eight-stream SSH-over-RoCE with no fallback |
| [decisions/0004-model-serving-release-validation.md](./decisions/0004-model-serving-release-validation.md) | Accepted Model Serving Release identity, contract, evidence, status, onboarding, and subsystem-GA boundaries; the catalog shows reviewed status and start does not use it as permission; descriptor, contract, immutable run, evidence-bundle, and reviewed-decision schemas implemented; read-only persistence implemented and empty/unbound; local evidence-capture candidate persistence implemented as draft JSON; maintainer staging writes untrusted local registry files until a PR is merged |
| [MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md) | Maintainer-only ADR 0004 evidence-capture candidate persistence; no issuance and no runtime launch |
| [MODEL_SERVING_RELEASE_ISSUANCE.md](./MODEL_SERVING_RELEASE_ISSUANCE.md) | Maintainer-only ADR 0004 issuance staging; local success is not repository review or serving authorization |
| [MODEL_RELEASE.md](./MODEL_RELEASE.md) | Pointer: lab expected-identity files are retired (ADR 0012); live identity is ADR 0004 capture and staging |
| [BUILD.md](./BUILD.md) | Image-pin policy, optional overlay, and source-build boundary |
| [OPERATIONS.md](./OPERATIONS.md) | Start/stop, monitoring, staging every exact rank |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Missing receipt/occupancy, offline node, TCP fallback, cold load |
| [REVALIDATE.md](./REVALIDATE.md) | After any image pin change |
| [COMMIT_SAFETY.md](./COMMIT_SAFETY.md) | Publishable privacy scanner, safe-commit workflow, and optional local hook |
| [MODELS.md](./MODELS.md) | What fits which node count |
| [SECURITY.md](../SECURITY.md) | Do not expose `:8000` without auth |
