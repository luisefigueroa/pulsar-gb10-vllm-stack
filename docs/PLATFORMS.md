# Operator platform reference

The operator stack names the machine class it is probing through one
platform reference file. A future DGX Spark model is a new file plus new
release specs, not a rewrite of the probes. The current production file is
`platforms/dgx-spark-gb10.json`. Values in that file are the constants the
probes already enforced; this is a relocation, not a policy change.

`platform_id` here is operator probe identity. It is not the Model Serving
Release geometry field and it is not ADR 0004 `hardware_class`
(`nvidia-dgx-spark-gb10`). Those stay separate until a later geometry
change. This document makes no physical DGX claim.

Schema owner: `scripts/platform_reference.py`. Unknown fields fail without
fallback.

## Selection

1. `PULSAR_PLATFORM_FILE` — if set, it is the only file loaded and must be
   an absolute path. The `platform_id` inside must still pass schema
   checks. Empty fails.
2. Else `PULSAR_PLATFORM` — selects `platforms/<id>.json`. Unset defaults
   to `dgx-spark-gb10`. Explicitly empty fails.
3. Unknown id, missing file, or schema mismatch fails without fallback.
   The stack never auto-detects a platform from `nvidia-smi`.

`scripts/testdata/platforms/` holds test-only files. They are not
selectable as production ids.

## Production fields (`dgx-spark-gb10`)

| Field | Value |
|---|---|
| `schema_version` | 1 |
| `kind` | `pulsar-platform-reference` |
| `platform_id` | `dgx-spark-gb10` |
| `display_name` | `GB10` |
| `gpu_name` | `NVIDIA GB10` |
| `architectures` | `aarch64`, `arm64` |
| `accelerators_per_node` | 1 (documents the first-line `nvidia-smi` read; probes do not count GPUs) |
| `rdma.min_active_links_for_qualify` | 1 |
| `rdma.verbs_device` | `/dev/infiniband/uverbs0` |
| `memory.model` | `unified` |
| `memory.hard_floor_available_gib` | 4 |
| `memory.min_os_buffer_gib` | 8 |
| `memory.launch_spike_gib` | 3 |
| `memory.overhead_gib_default` | 10 |
| `memory.preflight_warn_available_gib` | 100 |
| `memory.cold_start_footprint_slack` | 0.92 |

These four memory scalars still accept a process or `.env` override on top
of the file (`HARD_FLOOR_AVAILABLE_GIB`, `MIN_OS_BUFFER_GIB`,
`LAUNCH_SPIKE_GIB`, `OVERHEAD_GIB_DEFAULT`).

Doctor treats an architecture mismatch as a warning. Discovery still
rejects a node that is not on the expected architecture list. That
severity split is unchanged.

## Inventory

### Moved into the platform file

| Location | Constant |
|---|---|
| `scripts/probe-node.py` | Expected GPU name `NVIDIA GB10`; expected architectures `aarch64`/`arm64`; minimum active RDMA links 1. Flags omitted keep those same literals so a stdin-fed probe without flags is unchanged. |
| `scripts/doctor.sh` | GPU name (this node and other confirmed ranks); architecture list (warning on mismatch); display name in “N GB10 systems discovered”; `--json` `platform_id`. Memory hard floor still comes from `lib.sh`. |
| `scripts/lib.sh` | `MIN_OS_BUFFER_GIB=8`, `HARD_FLOOR_AVAILABLE_GIB=4`, `LAUNCH_SPIKE_GIB=3`, `OVERHEAD_GIB_DEFAULT=10`. |
| `scripts/check-memory.sh` | Cold-start footprint slack 0.92. Other memory scalars via `lib.sh`. |
| `cluster/preflight.sh` | GPU name; 100 GiB MemAvailable warning; `/dev/infiniband/uverbs0`. |
| `scripts/topology_manifest.py` | GPU name at validate; display name in “N GB10 systems”. |
| `scripts/launch_plan.py` serving probe | GPU name. |

### Kept as mechanism; parameters come from the file

| Location | Mechanism |
|---|---|
| `scripts/probe-node.py` | `uname -m`, `nvidia-smi --query-gpu=name` (first line), `rdma link show` (ACTIVE + IPv4), Docker NVIDIA/CDI, control-plane `ip` routes. |
| `scripts/detect-fabric.sh` | Candidate discovery, SSH stdin-feed of `probe-node.py`, pairwise `ping`. Passes `--expected-gpu`, `--expected-arch`, `--min-active-rdma` from the loaded platform. |
| `scripts/lib.sh` `probe_node_json_for_rank` | Local file vs remote stdin; same flags. |
| `scripts/doctor.sh` | `/proc/meminfo` presence; `nvidia-smi` presence; MemAvailable compared to the loaded hard floor. |
| `cluster/preflight.sh` | `rdma link show` ACTIVE count vs profile `MIN_RAILS_PER_PAIR`; `test -e` on the loaded verbs path; MemAvailable vs the loaded 100 GiB warning. |
| `scripts/check-memory.sh` | Footprint arithmetic; fail/warn/pass using loaded floor, buffer, spike, overhead, and slack. |

### Left in place

| Location | Why |
|---|---|
| `cluster/cluster-env.sh` `NCCL_IB_HCA` / socket ifnames | Site-shaped ConnectX-7 names. Confirmed topology overrides them at launch. Discovery does not require those names. |
| `scripts/launch_plan.py` N>1 `devices: ["/dev/infiniband"]` | Launcher docker argv, not a probe constant. |
| `serve.sh`, `cluster/start-cluster.sh` | No probe constants. |
| `scripts/inventory.sh` | Reports MemAvailable and nvidia-smi compute apps; does not gate on GPU name or the hard floor. |
| Docker labels `io.pulsar.gb10.*` | Ownership contract, not platform identity. |
| `scripts/ui.sh` Linux aarch64 Gum binary | Authoring UX, not a serve probe. |
| Profile `TOPOLOGY_CLASS` / `MIN_RAILS_PER_PAIR` | Recipe geometry, already per profile. |
| `GPU_MEM_UTIL` default 0.80 | Recipe. |
| macOS `vm_stat` branch in `mem_available_gib_local` | Authoring-host dry-run escape, not GB10 policy. |
| ADR 0004 `hardware_class`, `driver_abi`, release geometry | Separate identity. |

## Documented, not probed

These numbers are evidence or pins. They are **not** in the platform
reference and probes do **not** fail on them.

| Fact | Where it is documented today |
|---|---|
| Driver 580.x / **580.173.02** | [HARDWARE.md](./HARDWARE.md), [PREREQUISITES.md](./PREREQUISITES.md) |
| CUDA **13.0** (host toolkit 13.0.3) | [HARDWARE.md](./HARDWARE.md), [PREREQUISITES.md](./PREREQUISITES.md) |
| Kernel `6.17.0-1026-nvidia` | [HARDWARE.md](./HARDWARE.md) |
| 121 GiB unified LPDDR5X (`MemTotal` 127.6 GB); nvidia-smi memory N/A | [HARDWARE.md](./HARDWARE.md), [PREREQUISITES.md](./PREREQUISITES.md) |
| vLLM image pin `vllm/vllm-openai:v0.26.0` | `scripts/lib.sh` `VLLM_IMAGE_MAINLINE`, [PREREQUISITES.md](./PREREQUISITES.md), [BUILD.md](./BUILD.md) |
| NCCL interface names `rocep1s0f0,roceP2p1s0f0` / `enp1s0f0np0` | `cluster/cluster-env.sh`, [HARDWARE.md](./HARDWARE.md) |

Adding any of those as live fail checks would change what probes conclude
on current GB10 hosts and is out of scope.
