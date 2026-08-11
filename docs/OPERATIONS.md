# Operations runbook — running this stack day to day

Diagnosis lives in TROUBLESHOOTING.md; this page is procedure. Run cluster
commands on the machine confirmed as rank 0. Remote SSH targets, control
interfaces, HCAs, and rank placement come from `.cluster-topology.json`; names
do not need to follow any pattern.

## Monitoring: never trust /health alone on multi-node

After any remote-rank loss, rank 0's `/health` **keeps returning OK for
~5 minutes** while every request hangs. The only honest liveness probe is a
real completion:

```bash
curl -fsS --max-time 15 http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<served-name>","prompt":"ok","max_tokens":1}' >/dev/null \
  && echo LIVE || echo "NOT SERVING (health may still say OK)"
```

Wire that (not /health) into anything that pages or restarts. Also useful:
`curl -s :8000/metrics | grep num_requests_` for queue state.

## Root dispatcher (`./pulsar`)

Preferred operator entry point (scripts under `scripts/` remain canonical):

| Command | Action |
|---|---|
| `./pulsar` | Neutral **operator home** (workflow menu; no preflight on entry) |
| `./pulsar wizard` | Serve/switch wizard (doctor + preflight; direct shortcut) |
| `./pulsar inventory [--json\|--verbose]` | Read-only service/memory inventory |
| `./pulsar start <model> [up args…]` | → `scripts/up.sh` |
| `./pulsar stop <model\|--all> [--node ID]` | → `scripts/down.sh` (ownership-gated) |
| `./pulsar status [model] [--node ID]` | → `scripts/status.sh` (may submit a completion) |
| `./pulsar help` | Concise usage |

**Invalid habit:** `./ wizard.sh` (space after `./`) makes Bash run the directory
`./` with `wizard.sh` as an argument, yielding `-bash: ./: Is a directory`.
Use `./pulsar wizard` or `./wizard.sh`.

## Operator home

`./pulsar` with no arguments opens `scripts/home.sh` immediately — no doctor,
inventory, weights, image, or model preflight until you pick a workflow.

Menu (default cursor: status):

1. **Current system status** — `scripts/quick-status.sh` (read-only)
2. **Serve or switch a model** — enters `wizard.sh` (its doctor/preflight)
3. **Stop a serving model** — inventory-safe active managed only; confirm → `down.sh`
4. **Maintenance** — optional clean of **stale** `safe_to_stop` managed containers
5. **Diagnostics** — run doctor, detailed inventory (read-only)
6. **Exit**

Read-only actions and cancelled subflows return home. Gum Escape/cancel and EOF
exit cleanly without mutations. `--all` is not offered in interactive stop or
maintenance; there is no automatic cleanup on doctor or startup.

### Quick status semantics

Home status is **not** `scripts/status.sh`. It consumes inventory JSON, probes
only `GET /v1/models` for API **model advertisement**, and never submits a
completion. Advertisement is **not** an inference health claim. Concise fields:
active managed conf/ranks, API models, MemAvailable + MemTotal for every
confirmed rank, managed GPU/unified per rank when measured, aggregate remote
reachability, unmanaged GPU count + aggregate MiB, and stale managed count
(nonblocking / no model memory). Optional follow-ups: refresh, detailed inventory, explicit full
smoke (`status.sh`, may complete), back. Machine-readable: `scripts/quick-status.sh --json`.

### Interactive stop and maintenance

Stop lists only **active** services with `ownership=managed`, `safe_to_stop=true`,
and proven complete ownership. Unknown, legacy, mismatch, incomplete/unproven,
foreign GPU, and any remote-unobservable multi-node services are excluded. After
selection, final confirmation is required; only `scripts/down.sh <conf>` runs
(revalidates labels/IDs). Decline → no mutation.

Maintenance “Clean stale stack-managed containers” lists only **stale** +
`safe_to_stop` managed entries, explains they are nonblocking, requires
confirmation, and delegates each conf to `down.sh`.

### UI colors

Shared helpers in `scripts/ui.sh` (home + wizard). Two deterministic modes:

1. **Plain Bash menus (uncolored)** — forced by `GUM=0`, `NO_COLOR`,
   `PULSAR_COLOR=never`, or `TERM=dumb` (also empty `TERM`). Gum is not invoked
   at all (same path as `GUM=0`), so Charm pink/purple defaults cannot appear.
   Non-interactive stdin/stderr also selects this EOF-safe path instead of
   starting a hidden Gum TUI that can wait forever.
2. **Color-enabled Gum** — only when Gum is available and color is allowed.
   Always overrides Charm defaults with terminal-palette blue: bright blue
   **12** for choose cursor/header/**selected** and confirm prompt; confirm
   selected button blue bg **4** + bright fg **15**. Ordinary list items have
   no colored background.

| Variable | Effect |
|---|---|
| `GUM=0` | Plain Bash menus (uncolored); Gum not used |
| `GUM_BIN` | Override gum binary path (only when color-enabled) |
| `NO_COLOR` / `TERM=dumb` / `PULSAR_COLOR=never` | Force plain Bash menus (not “Gum with no flags”) |
| `PULSAR_ACCENT` | Override accent when Gum is color-enabled (default `12`) |

Plain menus never emit raw ANSI.

### Human output conventions

Human readability is a primary CLI requirement. Snapshot-style commands use
short semantic sections, aligned labels, and width-aware hanging indentation;
interactive choices stay on one compact line and show detail after selection.
Chronological launch/validation output remains log-oriented because source and
event order matter there.

`scripts/terminal_format.py` owns the shared Python field/wrapping behavior,
while `terminal_width` and `print_hanging` in `scripts/lib.sh` cover Bash
diagnostics. Human views must remain meaningful without color and are tested at
narrow terminal widths. Automation must consume the documented JSON modes, not
parse the human presentation.

## Inventory and ownership

`scripts/inventory.sh` (also `./pulsar inventory`) is **read-only**. It never
stops containers. JSON contract is `schema_version=1` with:

- `services[]`: `conf`, `state` (running/partial/degraded/stale/…), `ownership`
  (managed/legacy/mismatch/unknown/mixed), `safe_to_stop`, `complete`,
  `observability`, ranks, estimated footprint, optional GPU memory
- `nodes.*.mem_available_gib` / optional additive `mem_total_gib`; names include
  `head`, legacy `worker`, and `rank-N` for additional confirmed ranks
- `unmanaged_gpu_processes[]`: diagnostics only — **no kill action**
- `worker.status`: compatibility aggregate for all other confirmed nodes (`ok`,
  `unset`, `unreachable`, or error)

Machine-readable JSON and Docker labels retain vLLM's zero-based `rank`
field: rank 0 is this node, rank 1 is the second cluster node, and so on.
Human-facing output uses those node names instead.

**`safe_to_stop` is true only** when every *observed* rank has
`io.pulsar.gb10.managed=true` and conf/rank labels that map consistently to a
repo profile. Lifecycle scripts (`down.sh`, cluster stop) **revalidate** labels
and IDs before remove. Unlabeled legacy, mismatch, unknown, incomplete, or
remote-unobservable situations are never auto-stopped.

Live inventory fails closed if rank 0 Docker cannot answer `info`, enumerate
containers, or return a valid snapshot. It probes every other confirmed node;
any unreachable rank, Docker error, or enumeration error is accumulated in the
compatibility `worker.status`/reason and blocks automatic multi-node
stop/replacement. An operational failure is never converted into an empty
“nothing is running” inventory.

Weight readiness means more than “the cache directory exists.” HF profiles
must have `refs/main` resolving to a snapshot with a readable non-empty
`config.json` and at least one non-empty weight file; `.incomplete` markers and
local shard indexes that reference missing/empty files fail preflight. Multi-node profiles are checked on every exact active rank. Docker/SSH failures are reported as
operational failures and never offered as a download/pull problem.

The memory checker grants “already loaded” mode only to a running exact-name
service whose stack ownership, selected conf, and every expected rank are
proven by labels. API model identity or an unlabeled lookalike is insufficient.

Human default output uses width-aware stacked sections for active + actionable
managed stale/mismatch services. `--verbose` also includes inactive
unknown/legacy detail, IDs, probe sources, and full process paths; `--json` is
always full.

## Model switch (wizard)

`./pulsar wizard` (not the no-arg home) runs doctor once, offers cluster
discovery/confirmation when no remote topology is active, then enters a
**selection loop** (“Choose another model” does not re-run doctor). The menu is
capacity-aware but validation-gated: it includes only exact `STATUS=tested*`
profiles with `NODES` no greater than confirmed capacity. It never derives a
new TP/PP geometry from the discovered node count.

After a one-node model is selected, the wizard evaluates every confirmed
physical node independently. A candidate must retain the confirmed immutable
node ID, be reachable through its BatchMode SSH endpoint when remote, have a
working Docker daemon, and avoid a hard failure under the model's cold-start
memory policy. The placement screen shows hostname, free memory, Docker state,
Pulsar/unmanaged occupancy, and the recommended idle target. Artifact checks,
port ownership, API health, and the final confirmation are then scoped to that
selected node. Running services on non-overlapping nodes remain visible but do
not become blockers.

Before each start plan it consumes inventory JSON + `check-memory.sh` and shows
a short target summary (not raw JSON). Decision highlights:

1. **Same profile running + API healthy** — Keep running (recommended), Restart
   (stop only after final confirm), Show status, Choose another model. Never
   silent replacement.
2. **Different complete managed blocker** — names conf/state/ranks/memory and
   `safe_to_stop`; offers stop listed stack-managed service(s) → recheck →
   start, keep current, choose another, or diagnostics.
3. **Partial/degraded managed** — explains observed vs expected ranks; cleanup
   only if every observed rank is inventory-safe and remote-rank observability
   permits lifecycle revalidation. Never implies completeness. Reinventory
   after any stop.
4. **Unknown/unmanaged GPU or unknown port owner** — read-only diagnostics;
   wizard **will not stop** it. Exit / choose another / diagnostics only.
5. **Stale managed** — does not hold model memory; safe cleanup only when it
   blocks the selected exact name and is `safe_to_stop`. Unknown stale
   summarized and left alone.
6. **Memory WARN** after cleanup — shows free/footprint/need; explicit continue
   allowed. **Memory FAIL** — no launch, no continue-anyway. If a previous
   profile was stopped, offer restart from **current** `models/<conf>.conf`
   defaults, choose another, or exit stopped.
7. **Launch fails after replacement** — report failure; offer restart previous
   from current config or exit stopped. No auto-restart loop.

**Stops are always deferred** until after the final start/replace confirmation.
After any stop, inventory + cold memory preflight run again (never assume
reclaim). Speculative-decode prompt defaults are unchanged (flagship
recommended fast path default-on with confirm).

## Start / stop

- Preferred: `./pulsar start <name>` / `./pulsar stop <name>` (or
  `scripts/up.sh` / `scripts/down.sh`).
- To place a one-node profile explicitly, pass `--node <node-id>` to
  `start`, `status`, or `stop`; get the immutable ID from
  `./pulsar inventory --json`. Named status/stop without `--node` probes all
  confirmed nodes and proceeds only when it proves one unique placement.
- Single node low-level: `./serve.sh <name> -d`. Do **not** `docker rm -f` by
  name unless inventory proves ownership — prefer `./pulsar stop <name>`.
- Multi-node exact profile: `cluster/preflight.sh <name>` then
  `cluster/start-cluster.sh <name>`. **ALWAYS `cluster/stop-cluster.sh <name>`
  or `./pulsar stop <name>` before relaunch** — a surviving cluster node can
  retain rendezvous or RDMA state and make the new rank 0 hang.
- After multi-node health, `start-cluster.sh` runs `validate/warmup.py` once
  (short+medium prompts, c=1 and c=4, stream and non-stream). That pays
  DSpark/Triton/block-FP8 JIT so the first real client is not the cold path.
  Skip with `--skip-warmup` (falls back to a single smoke completion).
  Manual: `python3 validate/warmup.py --url http://127.0.0.1:8000 --model <served>`.
- **Flagship DeepSeek defaults** (`models/deepseek-v4-flash.conf`) target
  **few long agent sessions** (≤5 concurrent, 500K client cap, tools/code),
  not high-QPS chat: 20 GB/rank KV, `max-num-seqs 5`, batch 16384, tool+
  reasoning parsers. Before resizing KV further: `drop_caches` on both exact flagship ranks,
  step only (never ≥27.5 GB/rank — known OOM), read boot "GPU KV cache size",
  soak. Details in the conf header and docs/RECIPES.md / docs/MODELS.md.
- One big model per node, ever. gpu-mem-util 0.85 leaves ~18 GiB for the OS
  on a 121 GiB shared pool; a second workload swaps the box.
  Before starting after other work: `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`.

## What "slow startup" looks like when it is fine

| Model | Cold load to healthy |
|---|---|
| qwen3-1.7b | ~2 min |
| qwen 27B / nano | ~4-9 min |
| laguna (NFS weights) | ~11 min |
| deepseek-v4-flash (160 GB, both nodes) | ~12-15 min |

`--health-start-period` is 900 s for this reason. Watch
`docker logs -f` for `Loading weights took ...` before suspecting a hang.

## Node-loss playbook (measured behavior)

1. In-flight requests hang silently — client timeouts are your only signal.
2. ~5 min later: `sample_tokens RPC timed out` → engine dead; `/health`
   finally fails; container still shows "Up" (API alive, engine dead).
3. **There is no recovery.** Do not wait for one:
   `cluster/stop-cluster.sh && cluster/preflight.sh <name> && cluster/start-cluster.sh <name>`
   (~15 min back to serving).

If a hang does NOT follow a node event, walk TROUBLESHOOTING.md (multi-cause
`sample_tokens` RPC timeout tree) before restarting in a loop.

## Logs

- Rank 0: `docker logs vllm-cluster-<name>` (or `vllm-<name>` single-node).
- `./pulsar inventory --verbose` shows every observed rank and node placement.
  Use the corresponding SSH target from the confirmed topology for remote
  `docker logs`. `start-cluster.sh` automatically tails every rank on startup
  failure.
- On any first boot / after upgrades, grep for
  `attention backend|MoE backend|Unknown vLLM env`; vLLM can drop environment
  variables silently across versions.

## Staging images and weights to exact ranks

```bash
scripts/pull-weights.sh <profile> --yes
scripts/sync-image.sh <profile> --pull --yes

# A one-node profile selected for a remote confirmed node:
scripts/pull-weights.sh <profile> --node <node-id> --yes
scripts/sync-image.sh <profile> --node <node-id> --pull --yes
```

The weight helper downloads once on this node and copies the complete HF hub
tree to every other node required by that profile, preserving `refs/main`.
Its normal output uses readable stages and hostnames; set `PULSAR_VERBOSE=1`
to expose raw Hugging Face and rsync diagnostics. The image helper loads every
missing required node and repairs digest references that a bare `docker load`
can omit. NFS/catalog profiles are not copied: mount the same readable path on
every required node and run `scripts/check-weights.sh`.

### Confirmed SSH identity and endpoint drift

Topology schema 2 binds each selected transport address to the confirmed
node's stable SSH alias, enrolled host-key set, and immutable node ID. Enroll
and check it explicitly while the cluster is idle:

```bash
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check
scripts/doctor.sh
```

Enrollment writes the gitignored `.cluster-ssh-config` alongside the topology.
The shared loader rejects schema 2 if that generated config is missing or
stale. `doctor` reports the selected endpoint, expected/observed node identity
and key fingerprints, and drift class. It is read-only: it never rewrites
topology or OpenSSH trust and never accepts a replacement key. Use this
remediation policy:

| Doctor finding | Safe response |
|---|---|
| Endpoint changed; node ID and key still match | Run `scripts/detect-fabric.sh --json`, review all rails, explicitly write topology, then re-run SSH trust enrollment |
| Alias changed; node ID and key still match | Confirm the rename, explicitly rewrite topology, then re-run SSH trust enrollment |
| Host key changed; node ID still matches | Stop. Verify reimage/key rotation out of band, update normal OpenSSH trust deliberately, then run `scripts/topology-ssh-trust.sh enroll --accept-key-change` |
| Node ID changed at the old alias/address | Treat it as a replacement node; re-qualify and confirm membership |
| Rail IP presents another node's key | Stop immediately; repair duplicate/stale addressing or the topology mapping |

Do not “fix” a changed-key failure by disabling strict checking or broadly
deleting `known_hosts` entries. If replacement is legitimate, preserve the old
and new fingerprints in the incident/change record and enroll only the key
verified through an independent channel.

### Experimental single-copy weights

The wizard and ordinary launch remain replicated.

**Live fabric (NFS/RDMA under vLLM):** the opt-in command
`scripts/up.sh <profile> --weight-source fabric` uses a topology-bound,
read-only NFSv4.2/RDMA cache view and records its owner/config IDs in container
labels and inventory. It never creates a replica or falls back automatically.
Run `scripts/weight-fabric.sh prerequisites <profile>` for a read-only
per-node setup report. The explicit `setup-prerequisites` command installs
missing supported Ubuntu packages and an owner-user `hf` environment when
passwordless sudo is available. On hosts whose existing policy requires a
password, run attended setup and storage commands with `--interactive-sudo`;
authentication stays in the operator terminal, Pulsar stores no password, and
sudoers is unchanged. Otherwise the report gives manual commands and explains
the remaining privilege requirement. Setup, exact commands, benchmark
artifacts, destructive replica cleanup, owner/link fault semantics, and
recovery are in
[WEIGHT_FABRIC.md](./WEIGHT_FABRIC.md).

**Library-hot (federated catalog + local hot staging):** experimental path
aligned with [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md). Typical
flow:

```bash
scripts/model-library.sh catalog refresh
scripts/model-library.sh activate <profile> --backend copy --yes
scripts/up.sh <profile> --weight-mode library-hot
# optional after stop:
scripts/down.sh <profile> --pin-weights   # protect retained hot from purge
scripts/down.sh <profile> --purge-hot     # free hot disk budget
```

`pin` marks non-home hot content as purge-protected. Cold stage-only hot may
be fully self-contained. Warm-home activation is deliberately different: the
home rank uses a zero-copy symlink/runtime view of its authoritative durable HF
cache, and only non-home ranks own sealed-hot copies. Home-rank hot
materialization is ruled out by
[ADR 0001](./decisions/0001-model-library-home-view-and-validation-identity.md).

A warm-home pin permits restart without cold storage, a transfer plane, or
catalog refresh while the durable home remains. It does **not** claim survival
after home loss. Do not remove or unmount the home while a running or pinned
instance depends on it. Home-loss resilience requires an explicit durable
replica on another failure domain and supported failover.

Hot trees live under `PULSAR_HOT_ROOT` (default `/var/tmp/pulsar-hot`), not as
durable N copies in every node’s HF cache. For a warm-home N-rank service the
accepted accounting is one durable home plus N−1 hot working copies. Hot purge
must never follow the home symlink target. Defaults and the wizard stay on
replicated weights until this path is promoted.

**Accepted serve-time identity contract (not yet implemented):** the home view
must resolve to the expected canonical local durable tree, and model ID, exact
commit, and manifest ID must match a lab-issued validation bundle. A fast
metadata witness may be used only after a full verification and must cover the
canonical target/filesystem, exact revision/file set, and per-file
device/inode/size/mtime/ctime. Drift visibly triggers full verification against
the expected seal or fails closed; it never auto-reseals changed content.
Current code instead full-verifies a manifest derived from the locally observed
source and does not yet have the lab-issued trust anchor or fast witness.

**Optional cold archive:** shared/local fill tier (conventionally
`MODELS_NFS=/mnt/Models`, overridable with `PULSAR_COLD_ROOT`; empty
`PULSAR_COLD_ROOT` disables cold). Layouts scanned:

- `Official Models/<org>/<name>/` (and `Community Models/…`) — flat trees
- `hub/models--org--name/` or `.cache/huggingface/hub/…` — HF hub trees

Resolve order is **warm complete home → cold (if configured) → fail closed**.
Cold is preferred over a fresh Hugging Face download when warm misses; it is
**not** the multi-node runtime filesystem.

```bash
# inventory
scripts/model-library.sh cold scan --json
scripts/model-library.sh cold show poolside/Laguna-S-2.1-NVFP4
scripts/model-library.sh resolve laguna-s-2.1-nvfp4 --json   # warm, else cold

# grow federated library (durable warm home on this node’s HF cache)
scripts/model-library.sh cold adopt poolside/Laguna-S-2.1-NVFP4 --yes
scripts/model-library.sh catalog refresh

# or stage for this job only (cold remains sole durable copy)
scripts/model-library.sh cold stage-only laguna-s-2.1-nvfp4 --yes
scripts/up.sh laguna-s-2.1-nvfp4 --weight-mode library-hot
```

Unset/empty cold config skips the tier (no mount required). If cold is
configured but unreadable, flows that **need** cold (warm miss, absolute-path
conf, explicit `cold *`) fail closed; pure warm-catalog hits never require it.
See [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) §3.

**One-shot NFS/RDMA activate (legacy `--backend fabric` CLI):** ephemeral NFSv4.2/`proto=rdma`
from the catalog primary home over confirmed RoCE rails into hot staging, then
**release** of mounts/export (not a long-lived mount under vLLM):

```bash
scripts/model-library.sh activate <profile> --backend fabric --yes
# optional attended sudo:
scripts/model-library.sh activate <profile> --backend fabric --yes --interactive-sudo
# measure wall time:
scripts/model-library.sh activate <profile> --backend fabric --yes --time
# emergency cleanup of transfer plane:
scripts/model-library.sh release-transfer <profile> --yes
```

No silent fallback to control-path copy. Fabric may only be advertised as the
fast path when wall-clock activate time beats `--backend copy` on the same
model/topology (see [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md)).

**Activate A/B (B-gate):**

```bash
# multi-rank models need a warm primary + confirmed topology; fabric often needs sudo
scripts/model-library.sh catalog refresh
# large models: ensure hot budget (bench auto-raises if PULSAR_HOT_BUDGET_BYTES unset)
scripts/model-library.sh bench-activate <profile> --yes [--interactive-sudo] \
  [--tag my-run] [--nodes N] [--output results/model-library/<file>.json]
```

Runs timed **copy** then timed **fabric** (purges hot between), writes a JSON
report with `verdict` (`fabric_faster` | `copy_faster` | `tie` | `inconclusive`)
and `fabric_claims_fast_path` (true only if fabric is strictly faster). Prefer a
**multi-rank** profile (e.g. `NODES=2`) so fabric uses RoCE transfer; single-rank
fabric is local-only and is not a meaningful B-gate. Default output:
`results/model-library/<profile>-<tag>.json`.

**Activate performance:** the accepted home-rank behavior is a validated
durable-home symlink/view with no second full write; non-home ranks materialize
in parallel. Current experimental code prefers a symlink but can fall back to
reflink/copy if symlink creation fails. That fallback is not accepted promotion
behavior: the promoted path must fail closed when the durable-home view cannot
be established. NFS export/mount setup is batched and skips `nfs-server`
restart when RDMA is already listening. Bench JSON may include `copy_phases`
and `fabric_phases`; wall-clock is often limited by materialization and setup,
not raw RoCE line rate.

**SSH-over-RoCE experiment (not product default):** same copy activate
(rsync + SSH), but SSH targets are topology **RoCE IPs** so bulk TCP rides the
fabric NIC without NFS/RDMA. It requires enrolled topology schema 2,
`sshd` reachable on fabric IPs, and routes via the confirmed RoCE netdev. The
transport IP is never a separate trust identity: strict checking always uses
the saved alias and enrolled key. Product default remains replicated caches.

```bash
# 1) Enroll exact control/RoCE identity while idle, then verify it
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check

# 2) Prove the model-library RoCE map for the profile
scripts/model-library.sh probe-ssh-roce deepseek-v4-flash

# 3) A/B: control SSH copy vs SSH-over-RoCE copy (purges hot between)
#    Stay for the run; no sudo required for pure copy paths.
export PULSAR_HOT_BUDGET_BYTES=$((450 * 1024 * 1024 * 1024))  # if auto budget insufficient
scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
  --tag "ssh-roce-$(date -u +%Y%m%dT%H%M%SZ)"

# Parallel bulk copy for large, many-blob models (experimental). Repeat both
# orders before comparing paths; 150 ms staggering avoids an sshd admission
# burst at 16 streams.
PULSAR_COPY_STREAM_STAGGER_MS=150 \
  scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
    --copy-streams 16 --order roce-first --tag parallel-roce-first
PULSAR_COPY_STREAM_STAGGER_MS=150 \
  scripts/model-library.sh bench-ssh-roce deepseek-v4-flash --yes \
    --copy-streams 16 --order control-first --tag parallel-control-first

# Manual one-shot over RoCE TCP with the enrolled alias/key identity:
scripts/model-library.sh activate <profile> --transport ssh-roce \
  --backend copy --copy-streams 8 --yes
```

`--copy-streams` accepts 1-16 and defaults to 1. Above eight streams, the
connection stagger must be at least 100 ms (the default is 150 ms); the command
fails closed otherwise. Parallel copy currently supports a local endpoint on
one side of the transfer. A remote-home to remote-target relay fails explicitly
instead of silently reverting to one stream.

An initial two-run test produced a promising but highly variable 16-stream
result (46.42 s and 79.92 s), without proving physical source reads or
accounting for destination writeback. A later six-run alternating test
synchronized both filesystems and applied `POSIX_FADV_DONTNEED` before every
trial. Block counters then proved a full ~155.45 GiB source read each time:

- 8 streams: 75.60, 83.87, and 88.82 s (83.87 s median).
- 16 streams: 81.03, 85.09, and 88.61 s (85.09 s median).

The 1.4% median difference is below the observed run-order/storage variance, so
16 streams has no demonstrated full-model advantage over 8. After the first
trial, destination block-I/O busy time was 88-95% and source busy time was
84-93%; aggregate CPU was only 27-32%. Treat 8 streams as the current
experimental knee, and attribute the earlier 46-80 s spread mainly to
cache/writeback state rather than RoCE or SSH scaling. The small Qwen profile
also showed no benefit. Product defaults remain unchanged. See the
[alternating 8-vs-16 artifact](../results/model-library/deepseek-v4-flash-parallel-rsync-roce-8v16-alternating-20260810.json)
and the [earlier exploratory artifact](../results/model-library/deepseek-v4-flash-parallel-rsync-roce-16stream-20260810.json).

Report: `results/model-library/<profile>-ssh-roce-<tag>.json` with
`verdict` (`ssh_roce_faster` | `control_faster` | `tie` | `inconclusive`),
phase maps, and the RoCE IP map used. Use this before deciding whether to
rethink one-shot `nfs-rdma` versus `ssh-roce` (copy over RoCE TCP).

## Expected steady-state numbers (alert if far off)

Flagship under load (DSpark default-on): ~27 tok/s rollback/base /
**~43–48 tok/s** default single-stream; ~105 tok/s aggregate at c=8 base path; node temps
≤81–84 C, SM clock ≥2380 MHz. Memory
available fluctuates ±2 GiB with page cache — only a monotonic decline over
hours is a leak signal (none observed in 150-min soaks).

## Safety rails

- Spec decode is **not** "try random methods": only use paths that have
  validated `SPEC_DECODE_ARGS` and a positive ledger entry
  (docs/VALIDATION.md). The flagship defaults to DSpark; use
  `--no-spec-decode` as the operational rollback. Its k=5 is fixed by the
  checkpoint, not tunable. **Do not** enable ngram on GDN hybrids (corrupts
  output). Super MTP is opt-in; Laguna DFlash is marginal.
- Launcher-created containers carry stack ownership, profile, rank, topology,
  and physical-node labels (`io.pulsar.gb10.managed`, `.conf`, `.rank`,
  `.topology`, `.node-id`). Wizard and `down.sh` only stop
  services inventory marks `safe_to_stop`; `down.sh` revalidates before remove.
  Legacy unlabeled containers are refused. Any unobservable required node blocks
  automatic multi-node cleanup/replacement. See “Model switch (wizard)” above.
- Remote SSH uses BatchMode, a finite connect timeout/attempt count, and
  liveness bounds. Host values are passed after the SSH option terminator.
- Built-in API probes and Python validators honor `VLLM_API_KEY` / `API_KEY`.
  Dry-run command rendering redacts HF and API credentials. Prefer environment
  configuration over putting secrets in shell history.
- Rollback after a failed switch: restart the previous conf from **current**
  profile defaults (`models/<conf>.conf`), not a snapshot of prior CLI flags.
  Example: `./pulsar start deepseek-v4-flash` or wizard “Restart previous
  profile from current config”.
- Never run a GDN hybrid (qwen3.6-27b) cross-node.
- After any image change: clear `~/.cache/vllm` + Triton cache on every exact active rank, then run docs/REVALIDATE.md before calling it production.
