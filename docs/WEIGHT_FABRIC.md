# Experimental single-copy weight fabric

> **Not promoted.** Replicated local Hugging Face caches remain the default.
> This path is an explicit `--weight-source fabric` experiment until every
> physical-hardware gate in this document has a reproducible artifact.

This feature keeps one authoritative model repository on a selected DGX Spark
and presents it read-only to the other selected Sparks over a confirmed
ConnectX-7/RoCE link. It concerns model storage and initialization only. It
does not share KV cache, replace NCCL inference traffic, or claim direct RDMA
into CUDA allocations.

This document uses **live NFS/RDMA** for that long-lived runtime dependency.
Do not conflate it with model-library one-shot `nfs-rdma` transfer followed by
release, or with `ssh-roce` (rsync over SSH/TCP pinned to a confirmed RoCE
endpoint). Those are separate transfer/runtime combinations governed by
[MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md).

## Chosen architecture

The initial GB10 implementation uses Linux NFSv4.2 over RPC/RDMA:

1. A confirmed topology manifest identifies every physical Spark, control
   interface, pair-specific RoCE address, HCA, and netdevice.
2. A site-local config in `.weight-fabric/` binds the profile, model, owner,
   serving-node count, optional storage-node count, topology ID, and one
   deterministic rail per owner/client pair.
3. The owner exports its Hugging Face cache read-only. The export ACL contains
   only the exact client RoCE IPs and uses `root_squash`; it is never writable
   or exported to the control subnet.
4. Clients use a hard, read-only NFSv4.2 mount with `proto=rdma` and port
   `20049`. The launcher bind-mounts the configured cache root read-only into
   each container.
5. A sealed manifest fixes the Hugging Face `refs/main` revision, complete
   logical file set, byte sizes, and SHA-256 for every snapshot file. Launch
   checks validate topology identity, route, mount source/options, manifest
   metadata, and absence of a complete local client cache before Docker is
   changed.
6. Container labels record `weight-source`, authoritative owner node ID, and
   configuration ID. Inventory therefore reports which running service
   depends on the owner.

The config distinguishes `nodes` from `storage_nodes`. A two-rank vLLM profile
can expose and benchmark the same copy on all three confirmed Sparks without
silently adding a third vLLM rank. Launch readiness checks only serving ranks;
full storage checks and three-node benchmarks inspect every configured node.

There is no automatic fallback. A stale topology, wrong route, TCP NFS mount,
unavailable owner, changed manifest, client replica, or incomplete snapshot
blocks fabric launch. Selecting `replicated` is an explicit operator choice.

## Why this mechanism

NFS/RDMA preserves the path, symlink, `mmap`, and ordinary POSIX read behavior
expected by Hugging Face, SafeTensors, PyTorch, and vLLM while changing only
the backing transport. Linux documents NFS/RDMA service on port 20049 and
client `proto=rdma` mounts; the NFS export needs `insecure` because an RPC/RDMA
client does not use a reserved source port. See the
[Linux NFS/RDMA guide](https://docs.kernel.org/admin-guide/nfs/nfs-rdma.html),
[nfs(5)](https://man7.org/linux/man-pages/man5/nfs.5.html),
[nfs.conf(5)](https://man7.org/linux/man-pages/man5/nfs.conf.5.html), and
[exports(5)](https://man7.org/linux/man-pages/man5/exports.5.html).

SafeTensors commonly memory-maps checkpoint files. Remote pages therefore
enter the Linux client page cache before the model is materialized in GB10
unified memory. Cold startup consumes fabric bandwidth and can temporarily
reduce `MemAvailable`; warm startup can be served partly from page cache.
Unless a checkpoint was converted to a rank-specific format, each tensor
parallel process may inspect or read much more than its final tensor share.
The benchmark deliberately reports both per-rank bytes and aggregate logical
bytes rather than assuming one network read per checkpoint byte.

### Rejected or deferred alternatives

| Mechanism | Decision |
|---|---|
| NFS over TCP/control LAN | Rejected. It cannot prove the model used RoCE and permits the silent fallback this design must prevent. |
| NVMe over Fabrics | Deferred. It presents a block device, not safe concurrent filesystem semantics. A multi-reader cluster filesystem or per-client snapshot layer would add another ownership and recovery system. |
| vLLM `sharded_state` | Deferred for general use. It requires an offline checkpoint conversion tied to a tensor-parallel layout. It may reduce per-rank reads for selected models, but is not a transparent Hugging Face cache. See the [vLLM sharded-state example](https://docs.vllm.ai/en/stable/examples/features/sharded_state/). |
| Application-level streaming/sharding | Deferred. It would modify or wrap vLLM/PyTorch loading and must reproduce SafeTensors indexing, failure recovery, and per-format correctness. |
| Third-party streamed loaders | Deferred until their GB10/aarch64 dependencies, cache semantics, licensing, and vLLM image integration are validated. vLLM's supported loader surface is described in its [load configuration](https://docs.vllm.ai/en/stable/api/vllm/config/load/). |
| GPUDirect Storage | Not the baseline. NVIDIA documents DGX Spark GDS as compatibility mode, and the current vLLM/SafeTensors path does not invoke cuFile. Do not load `nvidia-fs` merely for this feature. See the [DGX Spark hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) and [GDS release notes](https://docs.nvidia.com/gpudirect-storage/release-notes/index.html). |
| Federated library + activate + rank-local views | **Implemented, experimental, and not promoted.** The model-library path scans federated durable homes, keeps the home rank on a validated symlink/view, and materializes sealed hot only on non-home ranks via `ssh-control`, `ssh-roce`, or short-lived `nfs-rdma`. It is operationally distinct from this long-lived live mount. See [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md), [ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md), and [OPERATIONS.md](./OPERATIONS.md). Historical exploration: [archive/WEIGHT_MATERIALIZE_DESIGN.md](./archive/WEIGHT_MATERIALIZE_DESIGN.md). This document remains the live NFS/RDMA **experiment** runbook. |

The vLLM distributed-filesystem guidance also expects every node to see a
shared model path; this design supplies that path while keeping inference
communication on the existing native multi-node backend. See
[vLLM distributed serving](https://docs.vllm.ai/en/v0.10.0/serving/distributed_serving.html).

## Prerequisites

Start from an idle, confirmed two- or three-Spark topology:

```bash
scripts/detect-fabric.sh --write-topology
scripts/doctor.sh
```

Every selected node needs Python, the NFS client, RPC/RDMA kernel support, and
usable privilege through its configured SSH endpoint. The owner also needs
`nfs-kernel-server`, `nfsd`, `svcrdma`, and `hf` (or the legacy
`huggingface-cli`) when the authoritative snapshot is not already present.
After `configure`, inspect every configured node without changing it:

```bash
scripts/weight-fabric.sh prerequisites qwen3-1.7b-2node
scripts/weight-fabric.sh prerequisites qwen3-1.7b-2node --json
```

The human report distinguishes missing packages, missing kernel capabilities,
unreachable nodes, and sudo that requires a password. On supported Ubuntu
nodes, the explicit setup command installs only missing `python3`,
`python3-venv`, `nfs-common`, and owner `nfs-kernel-server` packages. It also
creates an owner-user `$HOME/.hf-cli/venv` when no `hf` command exists:

```bash
scripts/weight-fabric.sh setup-prerequisites qwen3-1.7b-2node
```

The command is confirmation-gated and idempotent. It does not download a
model, configure an export, mount a client, replace a kernel, or change
sudoers. `WEIGHT_FABRIC_HF_CLI_VERSION` pins the owner CLI package; its default
matches the management environment documented by this revision.

Passwordless sudo is the fail-closed default for automation: `sudo -n true`
must succeed through every configured SSH endpoint. When the existing sudo
policy requires a password, add `--interactive-sudo` to commands run directly
from an operator terminal:

```bash
scripts/weight-fabric.sh prerequisites qwen3-1.7b-2node \
  --interactive-sudo
scripts/weight-fabric.sh setup-prerequisites qwen3-1.7b-2node \
  --interactive-sudo
scripts/weight-fabric.sh apply qwen3-1.7b-2node \
  --interactive-sudo
```

This mode requests authentication in that terminal and groups related root
changes into one script per affected node. Preflight and the grouped change
may each prompt because remote sudo timestamps can be terminal-specific.
Pulsar never reads, transports, logs, or stores the password, and does not
change sudoers. The same mode is available for cold-cache benchmarks,
`drop-caches`, `unmount`, and `teardown`. Set
`WEIGHT_FABRIC_SUDO_MODE=interactive` instead of repeating the option only for
an attended terminal session.

Without usable sudo, `prerequisites` prints the exact package and user-venv
commands to run manually on each affected node. Rerun the read-only check
after manual setup.

For the CLI, `download` checks `hf`, `huggingface-cli`, and the explicit
user-venv path `$HOME/.hf-cli/venv/bin/hf`. The last path works even when
noninteractive SSH does not include `$HOME/.local/bin`; keep its package
version aligned with the management node.

Keep the API and NFS/RDMA service on a trusted lab network. The generated
export is read-only and exact-address scoped, but it is not an authentication
boundary against a hostile RoCE peer.

## Configure and load one authoritative copy

Choose an owner from the profile's serving ranks. `--storage-nodes 3` also
prepares the idle third Spark as a read-only consumer; omit it for an exact
two-node storage scope.

```bash
scripts/weight-fabric.sh configure qwen3-1.7b-2node \
  --owner <topology-node-id> \
  --storage-nodes 3

scripts/weight-fabric.sh show qwen3-1.7b-2node
scripts/weight-fabric.sh prerequisites qwen3-1.7b-2node
scripts/weight-fabric.sh setup-prerequisites qwen3-1.7b-2node
scripts/weight-fabric.sh download qwen3-1.7b-2node
scripts/weight-fabric.sh apply qwen3-1.7b-2node
scripts/weight-fabric.sh verify qwen3-1.7b-2node
```

`setup-prerequisites` can be omitted when the read-only check is already
`ready`. `download` invokes the Hugging Face CLI only on the owner, then seals
the snapshot; it fails closed if neither CLI name is discoverable.
Before its first system write, `apply` verifies every client route, absence of
durable replicas, mount-target ownership, and sudo readiness. It then installs
configuration-specific files under
`/etc/exports.d/` and `/etc/nfs.conf.d/`, starts the RDMA NFS listener, verifies
the exact route, and mounts each client. Both commands require confirmation;
`--yes` is available for an already reviewed runbook.

Useful machine output:

```bash
scripts/weight-fabric.sh show qwen3-1.7b-2node --json
scripts/weight-fabric.sh check qwen3-1.7b-2node --json
scripts/check-weights.sh qwen3-1.7b-2node \
  --weight-source fabric --json
```

`check` covers all configured storage nodes. `check --serving-only` covers only
the exact vLLM ranks. `verify` performs a full SHA-256 read on every configured
node; routine launch uses the cheaper sealed metadata/file-size check.

## Launch and lifecycle

Fabric mode is explicit:

```bash
scripts/up.sh qwen3-1.7b-2node --weight-source fabric
scripts/inventory.sh
scripts/status.sh qwen3-1.7b-2node
scripts/down.sh qwen3-1.7b-2node
```

The wizard remains on the validated replicated default. It tells the operator
that missing Hugging Face weights will be copied to every serving rank; it
does not select or fall back to fabric mode. Use the CLI above for this
experiment.

Stop the tracked service before storage teardown:

```bash
scripts/down.sh qwen3-1.7b-2node
scripts/weight-fabric.sh unmount qwen3-1.7b-2node \
  --interactive-sudo
scripts/weight-fabric.sh teardown qwen3-1.7b-2node \
  --interactive-sudo
```

`unmount` refuses any client mount still used by a container. `teardown`
removes only this configuration's export and mount state; it preserves the
authoritative model and site-local config.

## Explicit replicated fallback

Replicated weights remain the default and require no fabric config at launch:

```bash
scripts/pull-weights.sh qwen3-1.7b-2node \
  --weight-source replicated --yes
scripts/up.sh qwen3-1.7b-2node --weight-source replicated
```

No failure automatically executes those commands. This avoids an unnoticed
full-catalog copy after an owner or link failure.

If replicated copies were created for the comparison benchmark, stop all
Pulsar services and remove only this config's validated client model roots:

```bash
scripts/weight-fabric.sh purge-replicas qwen3-1.7b-2node
```

That command is deliberately destructive, confirmation-gated, refuses active
Pulsar containers, never touches the owner, and validates the exact Hugging
Face model-cache path before deletion.

## Reproducible measurements

The benchmark runs all selected reads concurrently. It first performs full
manifest verification, optionally drops page caches, snapshots the configured
RoCE and control-interface counters, reads the complete snapshot on each
rank, and records:

- per-rank and aggregate logical bytes, wall time, and GiB/s;
- process user/system CPU and CPU utilization;
- `MemAvailable`, `Cached`, and `SReclaimable` before/after deltas;
- maximum process RSS;
- RoCE client RX, owner TX, and control-LAN byte deltas;
- configuration, topology, owner, revision, and manifest identities.

Cold fabric reports fail unless each client observes model-sized RX on its
configured RoCE netdevice, the owner observes corresponding TX, and control
traffic stays below a bounded threshold. Cold replicated reports invert that
proof: model-sized traffic must not appear on RoCE or the control LAN.

Two-node replicated baseline:

```bash
scripts/weight-fabric.sh benchmark qwen3-1.7b-2node \
  --source replicated \
  --serving-only \
  --cold \
  --tag qwen17b-replicated-2node \
  --yes
```

Two-node fabric:

```bash
scripts/weight-fabric.sh benchmark qwen3-1.7b-2node \
  --source fabric \
  --serving-only \
  --cold \
  --tag qwen17b-fabric-2node \
  --yes
```

Three-node concurrent loading:

```bash
scripts/weight-fabric.sh benchmark qwen3-1.7b-2node \
  --source fabric \
  --all-configured \
  --cold \
  --tag qwen17b-fabric-3node \
  --yes
```

Results are written without overwrite under
`results/weight-fabric/<tag>/`. `benchmark.json` is the summary; the bundle
also contains public provenance, the sealed manifest, integrity results, raw
per-rank JSON, stderr, and before/after network counters. Private site
addresses, SSH targets, node IDs, and cache paths are replaced by topology and
configuration identities plus deterministic node/rail fingerprints. Private
inputs are staged outside the bundle, and `artifact-audit.json` records the
publish-time privacy/containment check. Add
`--verify-sha256` only when measuring checksum CPU cost; the normal I/O run
measures buffered reads after a separate integrity pass.

### Time to first healthy

The cluster launcher measures from the first Docker start through the first
successful `/health`. Reset caches only while all selected Sparks are idle,
then request an atomic result file at a new path; existing evidence is never
overwritten:

```bash
scripts/weight-fabric.sh drop-caches qwen3-1.7b-2node \
  --serving-only --interactive-sudo --yes

PULSAR_STARTUP_METRICS_FILE="$PWD/results/weight-fabric/qwen17b-fabric-startup.json" \
PULSAR_STARTUP_TAG=qwen17b-fabric-cold \
PULSAR_STARTUP_CACHE_STATE=cold \
scripts/up.sh qwen3-1.7b-2node \
  --weight-source fabric \
  --skip-warmup
```

Repeat with `--weight-source replicated` and a different result path. Startup
files report `time_to_first_healthy_seconds`; the benchmark reports storage
throughput/CPU/memory separately so warmup or smoke-completion time is not
misclassified as weight I/O.

## Fault and recovery matrix

Use a small tested model and keep an independent management path. Never run a
link or owner fault while unrelated services are active.

| Scenario | Expected result | Recovery and required evidence |
|---|---|---|
| Interrupted client read/start | vLLM never reaches `/health`; launch cleanup removes only IDs created by that invocation. | Restore storage, run full `verify`, relaunch, then correctness gates. Preserve launcher logs and inventory JSON. |
| Configured RoCE link down before launch | Route/mount/readiness check fails; it must not remount over TCP or the control LAN. | Restore the exact netdevice, rerun `check --full`, and capture interface counters. |
| Link loss during cold load | Hard NFS I/O blocks or the process fails; no incomplete service is reported healthy. | Restore link, stop any tracked partial service, full verify, relaunch. Record failure and recovery latency. |
| NFS service restart | Hard mounts wait for the owner service. A completed in-memory service may remain healthy because it no longer needs checkpoint pages; a new/reloading service must fail closed until recovery. | Restart NFS, confirm RDMA port 20049, run `check --full`, then restart and gate the service. |
| Owner reboot | Owner rank and export disappear; the multi-node service is degraded or stopped and cannot cold-start. | Wait for owner, NFS/RDMA, and exact route; full verify; ownership-safe stop/restart; record time to recovered health. |
| Interrupted owner download | `.incomplete`, missing shard, changed ref, or manifest mismatch blocks sealing/launch. | Resume the owner-only download, reseal, full verify. Do not copy a partial cache. |

An already fully loaded service can legitimately continue inference after an
owner outage because the weights are resident. The safety requirement is that
no *incompletely loaded* service becomes healthy and no later restart silently
uses another transport or creates replicas.

For a reproducible link-loss window with a small canary, pace the audited
two-node read from one terminal and operate the selected link from an
independent management path:

```bash
scripts/weight-fabric.sh benchmark qwen3-1.7b-2node \
  --source fabric \
  --serving-only \
  --cold \
  --max-mib-s 64 \
  --tag qwen17b-link-loss \
  --yes
```

The result is labeled `measurement_kind=fault-injection`,
`throughput_comparable=false`, and records the rate. `--max-mib-s` is for fault
injection only; never use a paced result as a throughput number. The low-level
`weight_fabric.py io-benchmark` command exposes the same option for focused
diagnostics.

## Promotion gates

Keep this feature experimental until artifacts cover both the two-node
serving path and three-node concurrent loading:

1. replicated-local and fabric cold benchmarks with the same revision;
2. two-node and three-node interface-counter traffic proof;
3. deterministic captures from repeated cold starts;
4. correctness and baseline comparison;
5. long-context/needle gate appropriate to the profile;
6. interrupted load and clean recovery;
7. link loss, NFS restart, and owner reboot;
8. restart loop and sustained soak with memory/network samples;
9. proof that client model-cache roots are absent after the experiment;
10. final `scripts/selftest.sh`, inventory, and ownership-safe lifecycle audit.

Use `validate/run-gates.sh` against each healthy fabric launch and store every
artifact under `results/`. Record the exact image, profile, topology ID,
configuration ID, manifest ID, command, timestamps, and any physical action in
`docs/VALIDATION.md`. Passing synthetic self-tests alone is not promotion
evidence.
