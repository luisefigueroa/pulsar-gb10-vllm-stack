# Multi-node serving on confirmed GB10 topologies

The control plane automatically discovers cluster membership and its node
count, and can operate an arbitrary number of NVIDIA GB10 nodes. The
confirmed manifest, not a built-in
one- or two-node limit, defines available capacity. That is deliberately
separate from the serving claim: a discovered node is capacity, not a
validated model geometry.

The validation ledger currently promotes only the exact one- and two-node
profiles marked `STATUS=tested*` in `models/`. There is no promoted three-node
profile. Finding three nodes therefore does not make the wizard invent TP=3,
PP=3, or any other unmeasured launch.

Legacy `STATUS=tested*` identifies the current recommendation class; use
`scripts/list-models.sh --legacy-tested` to filter it. The deprecated
`--validated` alias does not implement the `Validated` Model Serving Release
status accepted in [ADR 0004](./decisions/0004-model-serving-release-validation.md).
Status is advisory and never grants or denies serving.

## Two independent gates

| Gate | What it proves | What it does not prove |
|---|---|---|
| Confirmed topology | GB10 identity, Docker/NVIDIA readiness, SSH endpoint, control address, active addressed RDMA links, full-mesh rails, and directed rail reachability | That a model is correct, stable, or faster at this node count |
| Exact model profile | `NODES`, TP×PP world size, image, flags, memory budget, topology class, minimum rails, and earned `STATUS` | That additional discovered nodes may be substituted automatically |

A multi-node profile is valid only when `TP × PP == NODES`, uses the native
`mp` distributed executor, and its topology requirements are met. Launchers
prepares ranks `0..NODES-1`; extra confirmed ranks remain idle. The wizard
shows all serving profiles whose exact `NODES` value fits the confirmed
capacity, displays status and notes, and orders recommended evidence-backed
choices first. Related profiles can carry family and variant labels, but
each node-count variant must earn its own status.

Non-tested, failed, blocked, and experimental labels are warnings rather than
permission gates. The legacy `--force` option is a compatibility no-op. Exact
geometry, topology, memory, image, weight integrity, lifecycle, and security
checks are unchanged and still fail closed.

## Idle capacity and one-node placement

A confirmed node may host a fitting one-node profile even when it is
not rank 0 and other confirmed nodes are busy with an exact multi-node service.
The wizard recommends an idle node that passes the profile's cold-start memory
policy; operators can select the same immutable target directly with
`--node <node-id>` on `pulsar start`, `status`, and `stop`.

This does not alter multi-node rank geometry. A two-node profile still uses
manifest ranks 0 and 1, while an independent one-node service may use rank 2.
Port 8000 is host-local, so those services do not conflict. Remote one-node
launches use the manifest's BatchMode SSH endpoint and label the container with
both topology and physical node identity. Inventory and cleanup revalidate
those labels against the current confirmed topology and fail closed if
placement is missing, duplicated, unreachable, or ambiguous.

## Discover and confirm membership

Run discovery from the node that will be rank 0 and host the API:

```bash
# Read-only preview; emits the verified discovery document but writes nothing.
scripts/detect-fabric.sh --json

# Add candidates when mDNS is absent or incomplete (repeatable).
scripts/detect-fabric.sh --candidate atlas-a --candidate 192.0.2.42
# Equivalent non-persistent input:
CLUSTER_CANDIDATES='atlas-a,atlas-b,192.0.2.42' scripts/detect-fabric.sh

# Review the rendered ranks, then confirm the exact membership interactively.
scripts/detect-fabric.sh --write-topology
```

Candidate names are only ways to reach SSH. Discovery combines the local node,
Avahi/mDNS `_ssh._tcp` advertisements, `CLUSTER_CANDIDATES`, repeated
`--candidate` values, and nodes from an existing manifest. It does not require
names such as `dgx-spark-N`.

Discovery does not trust mDNS membership. Every remote must independently
prove:

- `aarch64` and exact GPU name `NVIDIA GB10`;
- a reachable Docker daemon with NVIDIA runtime or CDI support;
- a distinct machine identity (a hash of the machine ID, not its hostname);
- a control-plane IPv4 address and interface;
- active RDMA HCAs mapped to addressed network interfaces.

The assembler selects the largest RoCE full mesh containing local rank 0. It
then pings every shared rail in both directions for every rank pair. An
unverified or partial document cannot be loaded as active topology.

Discovery is `BatchMode=yes` with finite connection and liveness bounds.
Existing `known_hosts` is its default trust policy;
`--accept-new-host-keys` is an explicit one-time TOFU choice for discovery only.
It does not create topology-enrolled trust. A duplicate machine reached through
several names or IPs is de-duplicated by machine identity, preferring its
hostname/control endpoint over a RoCE address for SSH.

On confirmation, discovery atomically writes schema-1
`.cluster-topology.json` mode 0600. With the cluster idle, run
`scripts/topology-ssh-trust.sh enroll` to authenticate the exact saved control
endpoints through normal OpenSSH trust, verify every pairwise rail, and upgrade
to schema 2. This also writes `.cluster-ssh-config`; both files are gitignored
because they contain site-local membership and trust material. A schema-2
manifest with a missing or stale generated config cannot load.

Before replacement it proves that every rank in both the prior and proposed
membership has no running stack-managed container; an unreachable Docker query
fails closed.
Ranks from an existing manifest stay stable where possible.

## Control plane versus data plane

Each manifest node records both a control endpoint and its local RDMA HCAs.
The launcher uses:

- rank 0's control IP for torch distributed rendezvous (`--master-addr`);
- each rank's control interface for Gloo, TP coordination, and NCCL socket
  bootstrap;
- only the HCAs that connect ranks inside the exact selected profile for NCCL
  payload traffic (idle-capacity links are excluded);
- `NCCL_NET=IB`, `NCCL_IB_DISABLE=0`, and `/dev/infiniband` to prevent a silent
  shared-LAN payload fallback.

For schema 2, every shared SSH caller uses the generated config: the stable
alias remains the host-key identity while `HostName` is the exact confirmed
control address. SSH-over-RoCE changes only that transport address and retains
strict verification against the same enrolled alias/key set.

Using the administration network for bootstrap has real downsides: the launch
now depends on that network's routing, firewall, DNS/SSH reachability, and
latency. A congested or fragile admin network can delay rendezvous and control
messages. It is still preferable to using a point-to-point RoCE address as the
SSH identity, and model tensors stay on verified RoCE because the launcher
forces the IB transport. Preflight rechecks every selected pair and rail on
every launch, along with each control IP/interface binding.

Live NFS/RDMA under vLLM is retired as a serving runtime source
([ADR 0005](./decisions/0005-reject-live-nfs-rdma-serving.md)). That does not
change NCCL selection, topology discovery, or ADR 0003 `ssh-roce` prepare.
Historical notes: `WEIGHT_FABRIC.md`.

The model library is the only weight-distribution mechanism
([ADR 0006](./decisions/0006-model-library-only-weight-distribution.md)):
one durable home, a symlink/view on that rank, and sealed-hot copies only on
non-home ranks. It is not a live NFS/RDMA mount and there is no fallback.
Typical CLI: enroll SSH trust, `scripts/model-library.sh home add` if no home
exists, `catalog refresh`,
`prepare --backend copy --transport ssh-roce --copy-streams 8`, then
`scripts/up.sh <profile>`. See [OPERATIONS.md](./OPERATIONS.md) and
[ADR 0003](./decisions/0003-explicit-model-preparation-transport.md).

## Launcher: native `--nnodes`, not Ray

`cluster/start-cluster.sh <profile>` starts remote headless ranks first, then
local rank 0 with the OpenAI API on `:8000`. Every rank receives the exact
profile world size and its own `--node-rank`. Containers use host networking,
host IPC, all GPUs, locked memory, and `/dev/infiniband`, and carry immutable
ownership labels for profile, rank, world size, topology ID, and node ID.

The native vLLM `--nnodes/--node-rank/--headless` path with the `mp` executor is
the validated path here. Earlier Ray testing on this hardware served at
concurrency 1 but hard-hung at concurrency 2 or above. Native TP=2 passed the
small canary and the flagship correctness, concurrency, long-context, and soak
gates. It also avoids a second cluster-control system whose partial failure
would complicate ownership and teardown.

`cluster/preflight.sh <profile>` checks every node used by the profile: key-based SSH,
pairwise RoCE rails in both directions, GB10, Docker/NVIDIA, RDMA devices,
control bindings, memory, image, weights, and stale containers. Artifact
helpers and inventory likewise visit every active rank. Stop and rollback
first prove ownership everywhere, then remove the other nodes before removing
the container on this node.

## What is actually validated today

The measured production layout remains two-node TP=2. Decode on GB10 is
memory-bandwidth-bound (~240 GB/s effective per node), while measured TP=2
all-reduce latency at decode-sized messages is roughly 25–40 µs. For
DeepSeek-V4-Flash, overlapping the per-node weight-read reduction outweighs the
communication cost; the exact evidence and soaks are in `VALIDATION.md`.

Cross-node layout is still decided per profile, not globally:

- the published PR-41834 DeepSeek image is stable with CUDA graphs and is the
  flagship path;
- the official v0.26.0 image works for the tiny TP=2 plumbing canary;
- real two-node models on the official image require their recorded workaround
  (often `--enforce-eager`) and must retain the status earned by that exact
  configuration;
- GDN hybrids are not approved cross-node.

These two-node findings must not be extrapolated to three or more nodes. More
rank pairs change collective behavior, failure surface, memory layout, and
throughput. `/health` also remains insufficient during partial rank loss.

## Promoting a new node count

To make a three-node or larger option appear in the wizard:

1. Add a separate profile variant with explicit `NODES`, TP/PP values,
   `TOPOLOGY_CLASS=roce-full-mesh`, `MIN_RAILS_PER_PAIR`, image, flags, and an
   accurate untested/incomplete legacy status.
2. Launch it deliberately on that exact physical topology. The wizard will
   display its warning label; the direct CLI needs no status override.
3. Run the appropriate correctness, determinism, concurrency, long-context,
   node-loss, and soak gates from `REVALIDATE.md`; archive raw artifacts under
   `results/` and record the outcome in `VALIDATION.md`.
4. Update that exact profile's evidence label only after the evidence passes.
   Recommendation ordering may then change, but availability does not.

A failed geometry keeps its failed label and evidence but remains visible when
it fits confirmed capacity. Discovery code needs no change when its label or
recommendation changes.

## Operations

```bash
scripts/detect-fabric.sh --write-topology
cluster/preflight.sh deepseek-v4-flash
cluster/start-cluster.sh deepseek-v4-flash
cluster/stop-cluster.sh deepseek-v4-flash
```

Live NFS/RDMA serving is retired (ADR 0005), and the whole weight-mode axis
was removed (ADR 0006): `--weight-source`/`--weight-mode` fail closed.
Leftover mounts:

```bash
scripts/weight-fabric.sh show <profile>
scripts/weight-fabric.sh unmount <profile>
scripts/weight-fabric.sh teardown <profile>
```

The model library is separate from live NFS and serves every profile
(ADR 0006).

Always tear down a multi-node service before relaunching. A surviving remote
rank can retain rendezvous state or RDMA resources and make the next launch
hang. If any rank dies, the service does not recover or let that rank rejoin;
tear down the exact profile, re-run preflight, and relaunch.

For a rank-to-host map or remote logs:

```bash
. cluster/topology.sh
load_cluster_topology
for ((rank = 1; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
  host=${CLUSTER_NODE_SSH_HOSTS[$rank]}
  printf 'rank %s  %s\n' "$rank" "$host"
  ssh "$host" docker logs --tail 80 vllm-cluster-<profile>
done
```

`HEAD_IP`/`WORKER_IP` in `.env` are not honored for topology. Multi-node
launch, preflight, and cluster start require a confirmed
`.cluster-topology.json`; without one they refuse and direct you to
`scripts/detect-fabric.sh --write-topology`.
