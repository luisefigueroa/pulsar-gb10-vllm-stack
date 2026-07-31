# Prerequisites — run the scripts on a DGX Spark

Single place for what must be true before `./serve.sh` or `cluster/*.sh`
work. Hardware numbers live in [HARDWARE.md](./HARDWARE.md); day-to-day
ops in [OPERATIONS.md](./OPERATIONS.md); multi-node detail in
[MULTINODE.md](./MULTINODE.md). This page is the gate checklist.

Validated on: 2× NVIDIA DGX Spark (GB10), Ubuntu, driver 580.x, CUDA 13.0,
Docker 29.x + NVIDIA Container Toolkit. Host NCCL is **not** required
(NCCL comes from the container images).

---

## Quick checks

| Mode | Gate command |
|------|----------------|
| Single-node | `./serve.sh --list` then `./serve.sh <model> --dry-run` |
| Two-node | `cluster/preflight.sh` then `cluster/preflight.sh <model>` |

`cluster/preflight.sh` exits non-zero if connectivity, RDMA, GPU, Docker,
images, weights, or stale containers fail. Fix those before
`start-cluster.sh`.

---

## 1. Hardware & host software (every node)

| Requirement | Notes |
|-------------|--------|
| **NVIDIA GB10** | `nvidia-smi --query-gpu=name --format=csv,noheader` → `NVIDIA GB10` (sm_121). Preflight enforces this on both nodes. |
| **Driver + CUDA 13 host stack** | Validated: driver **580.173.02**, CUDA **13.0** (host toolkit 13.0.3). |
| **Docker + NVIDIA Container Toolkit** | `docker info` must show the **nvidia** runtime. Default host runtime can stay `runc`. |
| **GPU containers work** | `docker run --rm --gpus all <cuda-image> nvidia-smi` |
| **Memory headroom** | ~**100+ GiB** `MemAvailable` before launching a big model. Preflight warns under 100 GiB. Unified LPDDR5X is shared by CUDA, OS, and page cache — no separate VRAM (`nvidia-smi` memory is N/A). |
| **One heavy model per node** | A second workload will swap the box. After other work: `sync; echo 3 \| sudo tee /proc/sys/vm/drop_caches` |

Not required on the host: vLLM Python install, Ray, host NCCL, jumbo MTU,
GPUDirect RDMA.

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
   - DeepSeek-V4 flagship: local PR #41834 build or Sparkrun fallback —
     see [BUILD.md](./BUILD.md) and the model conf `IMAGE=`
3. **Weights on disk** (default `HF_HUB_OFFLINE=1` — no surprise downloads)
   - Hugging Face cache under `$HF_CACHE/hub/models--ORG--NAME`
     (default `HF_CACHE=$HOME/.cache/huggingface`), **or**
   - Local / NFS path referenced by the conf (e.g. Laguna under
     `/mnt/Models/...`)
   - If you rsync HF caches manually, ensure `refs/main` exists or load
     fails with `LocalEntryNotFoundError` (see
     [TROUBLESHOOTING.md](./TROUBLESHOOTING.md))
4. **Paths mounted into the container**
   - `HF_CACHE` → `/root/.cache/huggingface`
   - `MODELS_NFS` (default `/mnt/Models`) → `/mnt/Models:ro`  
     Mount is always requested; only NFS-catalog models need content there.
5. **Optional `.env`**
   - Copy `.env.example` → `.env`. Set `HF_TOKEN` only if you pull online.
   - Defaults match this lab’s paths and IPs.

```bash
./serve.sh --list
./serve.sh laguna-s-2.1-nvfp4 -d    # detach; API on :8000
docker rm -f vllm-laguna-s-2.1-nvfp4
```

Cold load can take minutes (DeepSeek ~12–15 min). Watch `docker logs -f`
for `Loading weights took ...` before assuming a hang. Health start period
in the tooling is 900 s for this reason.

---

## 3. Two-node (`cluster/*.sh`)

Extra requirements beyond §1 on **both** head and worker. Defaults are this
cluster’s RoCE map; override in `.env` if your fabric differs.

### 3.1 Network & RDMA

| Requirement | Default / check |
|-------------|-----------------|
| RoCE rail 0 reachable | Head `10.100.120.1`, worker `10.100.120.2` — `ping` both rails (rail 1 swaps `120`→`121`) |
| RoCE rail 1 up | Dual-rail NCCL (`NCCL_IB_HCA=rocep1s0f0,roceP2p1s0f0`) |
| ≥2 ACTIVE RDMA links per node | `rdma link show` |
| `/dev/infiniband` present | Launchers pass `--device /dev/infiniband`. Host networking does **not** expose IB; without the device flag NCCL silently uses TCP. |
| Socket / HCA names | `NCCL_SOCKET_IFNAME`, `GLOO_SOCKET_IFNAME`, `TP_SOCKET_IFNAME` default to `enp1s0f0np0` (see `cluster/cluster-env.sh`) |
| Master port free | `MASTER_PORT=29500` on the head |

### 3.2 SSH & Docker on the worker

| Requirement | Notes |
|-------------|--------|
| **Passwordless SSH** head → worker | `ssh -o BatchMode=yes $WORKER_IP true` — preflight fails without it |
| Docker + nvidia runtime on **worker** | Worker container is started over SSH |
| Same image on **both** nodes | Preflight: `docker image inspect` head and worker |
| Weights on **both** nodes | Each TP rank loads locally; missing worker cache fails at load |
| No stale `vllm-cluster-*` containers | Leftover worker holds master port → silent rendezvous hang. Always `cluster/stop-cluster.sh` before relaunch. |

### 3.3 Node without internet (common on this lab’s worker)

Node 2 may only reach the head over RoCE and an NFS server. Stage from the head:

```bash
# Weights
rsync -rlptD ~/.cache/huggingface/hub/models--ORG--NAME \
  10.100.120.2:.cache/huggingface/hub/
# Fix refs after manual copy (see TROUBLESHOOTING.md)
ssh 10.100.120.2 'd=~/.cache/huggingface/hub/models--ORG--NAME; \
  [ -e $d/refs/main ] || { mkdir -p $d/refs; \
  ls $d/snapshots | head -1 | tr -d "\n" > $d/refs/main; }'

# Images
docker save IMAGE | ssh 10.100.120.2 docker load
```

Use `rsync -rlptD` (not plain `-a`) on some NFS-backed trees here.

### 3.4 Start sequence

```bash
cluster/preflight.sh <model>
cluster/start-cluster.sh <model>          # optional: --spec-decode
cluster/stop-cluster.sh                   # before every relaunch
```

Worker starts first (`--node-rank 1 --headless`), then head
(`--node-rank 0`, OpenAI API on :8000). Both use `--network host --ipc host
--gpus all --ulimit memlock=-1 --device /dev/infiniband`.

---

## 4. Storage layout

| Path | Role |
|------|------|
| `$HOME/.cache/huggingface` | Default HF hub cache (mounted into containers) |
| `/mnt/Models` | Optional NFS catalog (`Official Models/…`); required only for confs that point there |
| Docker image store | Multi‑GB images on **each** node that will run a container |
| `/mnt/Models/knowledge` | Shared OKF knowledge base (ops convenience, not required to start a server) |

Copy `.env.example` to override `HF_CACHE`, `MODELS_NFS`, image pins, or
cluster IPs.

---

## 5. Adapting another DGX Spark pair

Scripts default to this lab’s hostnames and IPs. On a different pair:

1. Set `HEAD_IP`, `WORKER_IP`, `MASTER_PORT` in `.env`.
2. Set `NCCL_IB_HCA` and socket interface names to match `rdma link` /
   `ip -br a` on your boxes.
3. Ensure passwordless SSH and identical images/weights on both nodes.
4. Point `MODELS_NFS` at your catalog mount, or change confs to local paths.
5. Re-run `cluster/preflight.sh <model>` until it is green.

Single-node only needs §1–§2; multi-node needs §3 as well.

---

## 6. Checklist (print / paste)

**Single-node**

```text
[ ] nvidia-smi → NVIDIA GB10
[ ] docker + nvidia runtime; docker run --gpus all works
[ ] image present for the conf you want
[ ] weights in HF cache or MODELS_NFS path
[ ] ~100 GiB free; no second heavy GPU workload
[ ] ./serve.sh --list works
```

**Two-node (add)**

```text
[ ] dual-rail RoCE; ≥2 ACTIVE RDMA links per node
[ ] /dev/infiniband on both nodes
[ ] passwordless SSH head → worker
[ ] HEAD_IP / WORKER_IP / NIC / HCA names match fabric
[ ] same image + weights on both nodes (refs/main if rsynced)
[ ] no stale vllm-cluster-* containers
[ ] cluster/preflight.sh <model> exits 0
```

---

## Related docs

| Doc | When |
|-----|------|
| [HARDWARE.md](./HARDWARE.md) | Measured bandwidth, RoCE map, storage |
| [MULTINODE.md](./MULTINODE.md) | Why native `--nnodes`, TP=2, graph hang workarounds |
| [BUILD.md](./BUILD.md) | PR #41834 image build for DeepSeek-V4 |
| [OPERATIONS.md](./OPERATIONS.md) | Start/stop, monitoring, staging to node 2 |
| [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) | Offline node, missing `refs/main`, TCP fallback, cold load |
| [REVALIDATE.md](./REVALIDATE.md) | After any image pin change |
| [MODELS.md](./MODELS.md) | What fits which node count |
