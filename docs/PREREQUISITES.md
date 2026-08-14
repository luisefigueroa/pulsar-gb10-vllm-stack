# Prerequisites — run the scripts on a DGX Spark

Single place for what must be true before `./serve.sh` or `cluster/*.sh`
work. Hardware numbers live in [HARDWARE.md](./HARDWARE.md); day-to-day
ops in [OPERATIONS.md](./OPERATIONS.md); multi-node detail in
[MULTINODE.md](./MULTINODE.md). This page is the gate checklist.

Serving results are validated on 2× NVIDIA DGX Spark (GB10), Ubuntu, driver
580.x, CUDA 13.0, Docker 29.x + NVIDIA Container Toolkit. The control plane
supports larger confirmed GB10 topologies, but a node count is serveable only
when an exact `STATUS=tested*` profile has earned that claim. Hostnames are not
part of cluster qualification. Host NCCL is not required; images provide it.

Here, `STATUS=tested*` is the current implementation's legacy serving gate. It
is not the `Validated` Model Serving Release decision defined by
[ADR 0004](./decisions/0004-model-serving-release-validation.md); that schema
and status migration is still pending.

---

## Quick checks

| Mode | Gate command |
|------|----------------|
| Single-node | `./serve.sh --list` then `./serve.sh <model> --dry-run` |
| Multi-node | `scripts/detect-fabric.sh --write-topology`, then `cluster/preflight.sh <exact-profile>` |

`cluster/preflight.sh` exits non-zero if confirmed capacity, pairwise RoCE,
SSH, GPU, Docker, images, weights, memory, control bindings, or stale
containers fail. Fix those before `start-cluster.sh`. There are no baked-in
cluster addresses: new setups confirm a gitignored `.cluster-topology.json`;
legacy `HEAD_IP`/`WORKER_IP` is a deprecated two-node-only path.

---

## 1. Hardware & host software (every node)

| Requirement | Notes |
|-------------|--------|
| **NVIDIA GB10** | `nvidia-smi --query-gpu=name --format=csv,noheader` → `NVIDIA GB10` (sm_121). Preflight enforces this on every exact active rank. |
| **Driver + CUDA 13 host stack** | Validated: driver **580.173.02**, CUDA **13.0** (host toolkit 13.0.3). |
| **Docker + NVIDIA Container Toolkit** | `docker info` must show the **nvidia** runtime. Default host runtime can stay `runc`. |
| **GPU containers work** | `docker run --rm --gpus all <cuda-image> nvidia-smi` |
| **Memory headroom** | ~**100+ GiB** `MemAvailable` before launching a big model. Preflight warns under 100 GiB. Unified LPDDR5X is shared by CUDA, OS, and page cache — no separate VRAM (`nvidia-smi` memory is N/A). |
| **One heavy model per node** | A second workload will swap the box. After other work: `sync; echo 3 \| sudo tee /proc/sys/vm/drop_caches` |

Not required on the host: vLLM Python install, Ray, host NCCL, jumbo MTU,
GPUDirect RDMA.

`./pulsar` opens the neutral operator home menu. `./pulsar wizard` (or
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

## 2. Single-node (`./serve.sh`)

Minimum to serve one model on the box where you run the script:

1. **Docker flags the launcher uses** (must be allowed by the host):
   - `--gpus all`
   - `--ipc=host` (large SHM; workers die opaquely without it)
   - `--ulimit memlock=-1` and stack ulimit
2. **Container image present locally**
   - Default mainline: `vllm/vllm-openai:v0.26.0` (override with
     `VLLM_IMAGE_MAINLINE` in `.env`)
   - DeepSeek-V4/Inkling: published, digest-pinned PR #41834 image — see
     [BUILD.md](./BUILD.md) and the selected model conf's `IMAGE=`
3. **Weights on disk** (default `HF_HUB_OFFLINE=1` — no surprise downloads)
   - The `hf` CLI is required on this node only when downloading an uncached
     Hugging Face model; already-cached and NFS models do not need it.
     Experimental distributed-library `home add` instead requires the CLI,
     upstream access, and any repository authentication on its selected target
     rank; it never transports credentials from the controller. Acquisition
     discovers `hf`, `huggingface-cli`, or Pulsar's managed
     `$HOME/.hf-cli/venv/bin/hf` installation on that target.
   - Hugging Face cache under `$HF_CACHE/hub/models--ORG--NAME`
     (default `HF_CACHE=$HOME/.cache/huggingface`), **or**
   - Use `scripts/pull-weights.sh <profile> --yes` for downloads. It passes
     `$HF_CACHE/hub` as the CLI cache directory and copies the verified model
     to every other node used by the profile. If an older Pulsar run placed the
     same repository directly under `$HF_CACHE`, the helper adopts it safely.
   - Local / NFS path referenced by the conf (e.g. Laguna under
     `/mnt/Models/...`)
   - If you rsync HF caches for replicated/offline Hugging Face loading,
     ensure `refs/main` names the intended commit or load fails with
     `LocalEntryNotFoundError`. Sealed `library-hot` does not trust that ref,
     but it does require the exact commit directory from its reviewed seal (see
     [TROUBLESHOOTING.md](./TROUBLESHOOTING.md))
4. **Paths mounted into the container**
   - `HF_CACHE` → `/root/.cache/huggingface`
   - `MODELS_NFS` (default `/mnt/Models`) → `/mnt/Models:ro`  
     Mount is always requested; only NFS-catalog models need content there.
5. **Optional `.env`**
   - Copy `.env.example` → `.env`. Set `HF_TOKEN` only if you pull online.
   - Defaults for paths/images are conservative. Confirmed topology is kept
     outside `.env`; candidate hints may be placed there.

```bash
cp .env.example .env   # optional for single-node path overrides
./serve.sh --list
./serve.sh laguna-s-2.1-nvfp4 -d    # detach; API on :8000
# Preferred equivalent: ./pulsar start laguna-s-2.1-nvfp4
# Lab network only — do not expose :8000 without auth (SECURITY.md)
curl -fsS http://127.0.0.1:8000/v1/models
./pulsar stop laguna-s-2.1-nvfp4
# Do not docker rm -f by name; home/wizard/down.sh only stop labeled,
# ownership-proven containers.
```

Cold load can take minutes (DeepSeek ~12–15 min). Watch `docker logs -f`
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
| Complete weights | `scripts/pull-weights.sh <profile> --yes` downloads here and copies to every other node used by the profile; NFS profiles must be mounted everywhere |
| Experimental single-copy weights | Optional only: run `scripts/weight-fabric.sh prerequisites <profile>` after configuration; it checks per-node Python, sudo, NFSv4.2/RPC-RDMA, owner server tools, and owner-side `hf`, then offers guarded Ubuntu setup or exact manual guidance. Add `--interactive-sudo` for an attended terminal when existing policy requires a password; Pulsar does not store it or change sudoers. Follow `WEIGHT_FABRIC.md`. |
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
The wizard offers only exact `STATUS=tested*` profiles that fit capacity; it
does not infer a larger geometry.

`HEAD_IP`/`WORKER_IP` remains supported for old two-node setups, but cannot
prove per-rank HCAs or an N-node mesh. Migrate with `--write-topology`.

---

## 4. Storage layout

| Path | Role |
|------|------|
| `$HOME/.cache/huggingface` | Default HF hub cache (mounted into containers) |
| `.weight-fabric/` | Gitignored, topology-bound configs for the experimental single-copy path |
| `/mnt/Models` | Optional NFS catalog (`Official Models/…`); required only for confs that point there |
| Docker image store | Multi‑GB images on **each** node that will run a container |

Copy `.env.example` to override `HF_CACHE`, `MODELS_NFS`, image pins,
auth, or discovery candidate hints. Confirmed membership is not stored there.
Doctor only inspects `HF_CACHE`; it never creates or modifies the path. A
missing cache is reported as a warning, while model download or preparation
creates the path when the selected workflow requires it.

### Weight acquisition (common cases)

```bash
# Downloads once on this node, then copies and verifies every required node.
scripts/pull-weights.sh deepseek-v4-flash --yes

# Optional: expose raw Hugging Face and rsync diagnostics.
PULSAR_VERBOSE=1 scripts/pull-weights.sh deepseek-v4-flash --yes

# Explicit experimental distributed library: one reviewed durable home only.
scripts/model-library.sh home add <sealed-profile> --yes
scripts/model-library.sh catalog refresh
```

The normal view uses width-aware Pulsar sections instead of streaming
third-party progress renderers. `PULSAR_VERBOSE=1` is intended for diagnosis.
For a one-node profile, `home add` defaults to the eligible confirmed rank with
the most free space and `--node RANK|NODE_ID` may select any exact confirmed
rank. Multi-node profiles remain limited to their exact serving geometry. An
explicit override never falls back. The command uses
same-filesystem private staging and full expected-manifest verification before
atomic publication. The replicated flow remains the guided default.

NFS-catalog models (e.g. Laguna) expect the path already present under
`MODELS_NFS` as referenced in the conf.

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
[ ] image present for the conf you want (mainline or published PR-41834 image)
[ ] weights in HF cache or MODELS_NFS path (refs/main if rsynced)
[ ] ~100 GiB free; no second heavy GPU workload
[ ] ./serve.sh --list works
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
| [WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md) | Experimental one-copy NFS/RDMA design, operations, faults, and gates |
| [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) | **Canonical architecture** — durable home, rank-local views, validation identity, preparation/hot/pin policy; experiment not promoted |
| [decisions/0001-model-library-home-view-and-validation-identity.md](./decisions/0001-model-library-home-view-and-validation-identity.md) | Accepted rationale: reviewed exact-content home symlink, non-home hot only, expected seal and serve-time witness |
| [decisions/0002-subsystem-qualification-boundaries.md](./decisions/0002-subsystem-qualification-boundaries.md) | Accepted rationale: catalog, integration, model, and release evidence scopes plus causal invalidation |
| [decisions/0003-explicit-model-preparation-transport.md](./decisions/0003-explicit-model-preparation-transport.md) | Accepted rationale: explicit reviewed-profile preparation uses topology-bound eight-stream SSH-over-RoCE with no fallback |
| [decisions/0004-model-serving-release-validation.md](./decisions/0004-model-serving-release-validation.md) | Accepted Model Serving Release identity, contract, evidence, status, onboarding, and subsystem-GA boundaries; descriptor/contract schemas implemented, later records/decisions/status/serving migration pending |
| [MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md](./MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md) | Descriptive current implementation, evidence boundaries, and known gaps |
| [MODEL_RELEASE.md](./MODEL_RELEASE.md) | Maintainer-only exact-manifest and unreviewed release-candidate workflow; no issuance authority |
| [models/seals/README.md](../models/seals/README.md) | Reviewed expected-seal schema, lab issuance boundary, and current migration status |
| [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md) | Archived exploration / option history only |
| [BUILD.md](./BUILD.md) | Published PR #41834 image, provenance, and source-build fallback |
| [OPERATIONS.md](./OPERATIONS.md) | Start/stop, monitoring, staging every exact rank |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Offline node, missing `refs/main`, TCP fallback, cold load |
| [REVALIDATE.md](./REVALIDATE.md) | After any image pin change |
| [MODELS.md](./MODELS.md) | What fits which node count |
| [SECURITY.md](../SECURITY.md) | Do not expose `:8000` without auth |
