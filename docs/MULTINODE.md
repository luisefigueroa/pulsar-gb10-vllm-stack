# Multi-node serving on confirmed GB10 topologies

The control plane can discover and operate an arbitrary number of NVIDIA GB10
nodes. That is deliberately separate from the serving claim: a discovered
node is capacity, not a validated model geometry.

The validation ledger currently promotes only the exact one- and two-node
profiles marked `STATUS=tested*` in `models/`. There is no promoted three-node
profile. Finding three nodes therefore does not make the wizard invent TP=3,
PP=3, or any other unmeasured launch.

## Two independent gates

| Gate | What it proves | What it does not prove |
|---|---|---|
| Confirmed topology | GB10 identity, Docker/NVIDIA readiness, SSH endpoint, control address, active addressed RDMA links, full-mesh rails, and directed rail reachability | That a model is correct, stable, or faster at this node count |
| Exact model profile | `NODES`, TP×PP world size, image, flags, memory budget, topology class, minimum rails, and earned `STATUS` | That additional discovered nodes may be substituted automatically |

A multi-node profile is valid only when `TP × PP == NODES`, uses the native
`mp` distributed executor, and its topology requirements are met. Launchers
activate ranks `0..NODES-1`; extra confirmed ranks remain idle. The wizard
shows only `STATUS=tested*` profiles whose exact `NODES` value fits the
confirmed capacity. Related profiles can carry family and variant labels, but
each node-count variant must earn its own status.

Non-tested profiles remain available only through the deliberate CLI
`--force` path. `--force` bypasses the status gate; it does not synthesize a
geometry or weaken topology checks.

## Idle capacity and one-node placement

A confirmed node may host a validated one-node profile even when it is not rank
0 and other confirmed nodes are busy with an exact multi-node service. The
wizard recommends an idle node that passes the profile's cold-start memory
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

SSH is `BatchMode=yes` with finite connection and liveness bounds. Existing
`known_hosts` is the default trust policy. Enroll host keys beforehand, or use
`--accept-new-host-keys` for an explicit one-time TOFU decision. A duplicate
machine reached through several names or IPs is de-duplicated by machine
identity, preferring its hostname/control endpoint over a RoCE address for SSH.

On confirmation, the tool atomically writes `.cluster-topology.json` mode 0600.
The file is gitignored because it contains site-local addresses and membership.
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

Using the administration network for bootstrap has real downsides: the launch
now depends on that network's routing, firewall, DNS/SSH reachability, and
latency. A congested or fragile admin network can delay rendezvous and control
messages. It is still preferable to using a point-to-point RoCE address as the
SSH identity, and model tensors stay on verified RoCE because the launcher
forces the IB transport. Preflight rechecks every selected pair and rail on
every launch, along with each control IP/interface binding.

Experimental single-copy model initialization is a separate data-plane use of
those same confirmed pair links. `--weight-source fabric` pins each NFS/RDMA
client to one recorded RoCE rail and rejects TCP/control-LAN mounts; it does not
change NCCL selection or the vLLM world size. A two-rank profile may configure
a third storage-visible node for loading benchmarks without inventing TP=3.
Replicated local caches remain the default. See `WEIGHT_FABRIC.md`.

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
   `TOPOLOGY_CLASS=roce-full-mesh`, `MIN_RAILS_PER_PAIR`, image, flags, and a
   non-tested status.
2. Launch it only as a deliberate CLI experiment with `--force` on that exact
   physical topology.
3. Run the appropriate correctness, determinism, concurrency, long-context,
   node-loss, and soak gates from `REVALIDATE.md`; archive raw artifacts under
   `results/` and record the outcome in `VALIDATION.md`.
4. Promote that exact profile to `STATUS=tested*` only after the evidence
   passes. The wizard will then expose it automatically when confirmed capacity
   is at least its `NODES` value.

A failed geometry stays non-tested and out of the wizard. Discovery code needs
no change when a profile is promoted.

## Operations

```bash
scripts/detect-fabric.sh --write-topology
cluster/preflight.sh deepseek-v4-flash
cluster/start-cluster.sh deepseek-v4-flash
cluster/stop-cluster.sh deepseek-v4-flash
```

The optional single-copy storage lifecycle is deliberately outside the wizard:

```bash
scripts/weight-fabric.sh show <profile>
scripts/up.sh <profile> --weight-source fabric
```

Use `WEIGHT_FABRIC.md` for setup, teardown, integrity, benchmark, and failure
recovery requirements.

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

`HEAD_IP`/`WORKER_IP` in `.env` remain a deprecated two-node compatibility
path. They cannot describe per-rank HCAs or an N-node rail mesh, so new setups
should confirm `.cluster-topology.json` instead.
