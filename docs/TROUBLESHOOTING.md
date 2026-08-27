# Troubleshooting — failures actually hit on this cluster

Every entry below happened during this build. Inherited-wisdom entries from
the prior repo are marked [prior art] and were only included if we re-hit or
re-verified them.

## `./ wizard.sh` → `-bash: ./: Is a directory`

**Hit:** typing `./ wizard.sh` (space after `./`) fails with
`-bash: ./: Is a directory`.
**Cause:** Bash executes the directory `./` and treats `wizard.sh` as an
argument — not a missing empty command.
**Fix:** `./pulsar wizard` (preferred) or `./wizard.sh` — no space after `./`.
Note: bare `./pulsar` is the operator **home** menu; `./pulsar wizard` is the
serve/switch shortcut.

## Home “Current system status” looks fine but requests hang

**Hit:** quick status shows an advertised model, but clients hang or time out.
**Cause:** home quick status probes only `GET /v1/models` (advertisement). It
does **not** run an inference completion and does not claim inference health.
On multi-node, `/health` can also lie after a remote-rank loss (see monitoring section
in OPERATIONS.md).
**Fix:** run an explicit completion smoke (`./pulsar status` or the home
status follow-up “Full smoke check”), or:
`curl -fsS --max-time 15 http://127.0.0.1:8000/v1/completions ...`

## Wizard / home will not stop my container / “not safe_to_stop”

**Hit:** home Stop, Maintenance, or wizard switch refuses cleanup and says it
will not stop unknown/legacy/mismatch services.
**Cause:** only inventory `safe_to_stop` stack-managed services (labels
`io.pulsar.gb10.managed=true` + consistent conf/rank) are eligible. Unlabeled
legacy, mismatch, unknown GPU consumers, incomplete multi-node views, and
unreachable required cluster nodes are read-only. Stale managed containers hold no model
memory and are optional maintenance only — never auto-cleaned on doctor/start.
**Fix:** run `./pulsar inventory` (or `--json` / `--verbose`). Identify the
owner. If it is truly stack-managed and complete, `./pulsar stop <conf>` or
home Stop (after confirmation) lets `down.sh` revalidate labels. If unlabeled
or foreign, stop it yourself only when you understand the process — home and
wizard never kill it.
**Related:** hard memory FAIL never offers “continue anyway”; free memory or
stop a proven managed service first. After any stop, re-run inventory —
reclaim is not assumed.

## Gum menus look wrong / want plain text

**Hit:** pink/purple UI, or no TUI in CI/scripts.
**Fix:** color-enabled Gum always forces terminal-palette blue
(`PULSAR_ACCENT`, default `12` for choose cursor/header/selected). When stdin
or stderr is not a terminal, the CLI automatically uses the EOF-safe plain
path. Set `GUM=0` for plain numbered menus. `NO_COLOR`, `TERM=dumb`, or `PULSAR_COLOR=never`
**force the same plain path** (Gum is not invoked — empty style flags would
fall back to Charm pink/purple). `GUM_BIN` overrides the binary when color is
enabled.

## Port 8000 in use (host network)

**Hit:** doctor warns port 8000 is listening; `docker ps` shows no published
port mapping because the stack uses host networking.
**Fix:** doctor/wizard prefer stack labels + `/v1/models` when identifying
managed host-network owners. `./pulsar inventory` shows conf/ownership. Do not
start a second model on the same port.

## `LocalEntryNotFoundError: Cannot find an appropriate cached snapshot`

**Hit:** first launches of Qwen3.6-27B-FP8 and the 2-node canary.
**Cause:** several HF caches on these boxes have `snapshots/<hash>/` and
`blobs/` (and a `trees/` dir from xet-era downloads) but **no `refs/main`**.
With `HF_HUB_OFFLINE=1` (our default) huggingface_hub needs `refs/main` to
resolve the revision and fails even though all weights are present.
**Fix for offline Hugging Face loading:** restore `refs/main` to the
intended exact revision. If a download receipt exists, occupy the
complete tree with
`scripts/model-library.sh home relocate <profile> --node RANK --yes`
after a live rehash rather than Hub re-download. Only re-acquire with
`scripts/model-library.sh home add <profile> --revision <exact-commit> --yes`
when no receipt and no occupancy remain.
**Do not select the first directory from `snapshots/`:** a cache can contain
several commits and filesystem order is not identity.

Live library launch does not trust `refs/main`. Catalog refresh discovers
complete snapshot directories; prepare and launch use the receipt plus occupancy
and the exact snapshot path. The tree must still exist and match that file list.
`scripts/check-weights.sh` (used by wizard/up) reports whether a prepared,
hashed library view exists for the profile's confirmed topology
(ADR 0006). Missing views are remediated with
`scripts/model-library.sh prepare <profile> --yes`. `home add --revision`
resolves a selector at acquisition time only.

## Download completed but Pulsar cannot find the cache

**Hit:** Hugging Face reports a completed download, followed by a missing
`$HF_CACHE/hub/models--ORG--NAME` path or an rsync source error.
**Cause:** `hf download --cache-dir` takes the exact Hub cache directory.
Passing Pulsar's Hugging Face home (`$HF_CACHE`) writes
`models--ORG--NAME` one level above Pulsar's canonical `hub/` directory.
**Fix:** use `scripts/model-library.sh home add <profile>` (ADR 0006). It
stages into same-filesystem private staging under `$HF_CACHE`, fully
verifies, and atomically publishes the durable home; conflicting copies fail
without fallback.

## Another cluster node has no internet route

**Hit:** the second node could reach this node and LAN NFS, but direct HF and
registry downloads failed. This is common on isolated compute fabrics.
**Fix:** acquire the durable home once on a connected rank
(`scripts/model-library.sh home add <profile>`), then
`scripts/model-library.sh prepare <profile> --yes` transfers only non-home
bytes to the other ranks over confirmed links. Use
`scripts/sync-image.sh <profile> --pull --yes` for images; it streams missing
images over the control LAN and repairs digest references when needed.

## `docker load` succeeds but the worker still misses a digest-pinned image

**Hit:** staging a digest-pinned image printed `Loaded image ID`, then
`sync-image.sh` failed its exact `repo@sha256:...` inspection.
**Cause:** Docker transferred every layer but registered the result only as an
untagged image ID (`RepoTags=[]`, `RepoDigests=[]`). A digest reference cannot
be restored with `docker tag`.
**Fix:** `sync-image.sh` now detects this state and runs `docker pull` for the
exact digest on the worker. Docker reuses the LAN-transferred layers and only
resolves the registry reference. Prefer the script over a bare save/load pipe.

## Multi-node: server never comes up, no error anywhere

**Hit:** intentionally reproduced during teardown testing.
**Cause:** a leftover remote-rank container from a previous run still holds the
master port / RDMA state; the new rank 0 blocks forever in torch.distributed
rendezvous.
**Fix:** `cluster/stop-cluster.sh <name>` (or `./pulsar stop <name>`) before
every start. `stop-cluster.sh` requires `<name>` or `--all`. Default
`start-cluster.sh` runs preflight first; leftover `vllm-cluster-*`
containers fail that gate and are **not** removed on the default path.
Ownership-gated cleanup inside `start-cluster.sh` is reached only with
`--skip-preflight`. Required action is stop, then start.

## NCCL silently uses TCP instead of RDMA in containers

**Cause:** `--network=host` does NOT expose `/dev/infiniband`; NCCL falls
back to sockets with no warning at default log levels, and you lose ~40% of
cross-node bandwidth plus latency stability.
**Fix:** launch tooling always passes `--device /dev/infiniband` and
`--ulimit memlock=-1` (default container memlock is 8 MiB — verbs
registration fails without it). Verify on bring-up with `NCCL_DEBUG=INFO`:
you want `NET/IB : Using [0]rocep1s0f0:1/RoCE` and channels `via NET/IB/`.
On GB10 expect `GDR 0` — GPUDirect is unsupported on Spark (GPU and NIC on
separate root complexes); staging via unified memory is normal and already
reflected in the Step 0 numbers.

## `vllm serve --help` crashes in a container without `--gpus all`

Cosmetic but confusing: the v0.26.0 CLI builds its arg parser through config
factories that touch CUDA. Run help/introspection with `--gpus all`.

## Cold loads look hung

Large models can spend several minutes loading from page-cold local NVMe. The
`/health` endpoint appears only after engine
init, and `--health-start-period` in our tooling is 900 s for this reason.
Watch `docker logs -f` (`Loading weights took ...` line) before assuming a
hang. NFS-hosted models (Laguna from /mnt/Models) add the 10 GbE ceiling.

## GPU memory numbers make no sense

`nvidia-smi` reports `memory.total: [N/A]` on GB10 — there is no VRAM; CUDA,
OS, and page cache share 121 GiB LPDDR5X. Consequences:
- `--gpu-memory-utilization` budgets against the SHARED pool. 0.85 leaves
  ~18 GiB for everything else; don't run anything heavy beside a big model.
- Before starting a big model after other workloads:
  `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches` reclaims page cache
  that otherwise inflates "used" memory during vLLM's profiling pass.
- [prior art] mem-util 0.85 + TP=2 once drove a node into swap with an
  `shm_broadcast` busy-wait signature; 0.75-0.80 is the safe band for
  2-node configs.

## Stale kernel caches after image changes

[prior art, re-applied as policy] Triton's JIT cache can silently corrupt
outputs on sm_121 across version bumps (vllm#41871). After ANY image pin
change: `rm -rf ~/.cache/vllm` and the Triton cache dir on every exact active rank.
Symptom to watch for in benchmarks: `jit_monitor` warnings about Triton
compilation *during inference* — those numbers are cold-cache artifacts,
rerun after warmup (validate/bench_serve.py warms up per level).

## `VLLM batch_invariant mode is not supported for GDN_ATTN`

**Hit:** enabling `VLLM_BATCH_INVARIANT=1` on Qwen3.6-27B (engine dies at
startup). The model is a hybrid — 48 of its 64 layers are GDN linear
attention — and vLLM v0.26.0's batch-invariant deterministic kernels don't
cover GDN. Consequence: cross-node bit-identical greedy output is only
achievable for architectures batch-invariant mode supports (standard
attention); for GDN/Mamba hybrids the achievable guarantee is
FP-equivalence (near-tie argmax flips only). See the determinism section
of docs/VALIDATION.md for the full investigation.

## lm-eval scores absurdly low while the model answers perfectly by hand

**Hit:** Laguna gsm8k scored 0.055 via lm-eval while a manual 5-shot curl
produced exactly-formatted correct answers.
**Cause:** `local-completions` tokenizes prompts CLIENT-side and sends token
IDs by default. Laguna's shipped tokenizer has the known Mistral-family
regex bug (transformers warns "will lead to incorrect tokenization" at
load), so the eval sent subtly corrupted token IDs; the server never saw the
real prompts. Models whose tokenizers are clean (Qwen, Nemotron) were
unaffected — which made it look model-specific.
**Fix:** add `tokenized_requests=False` to `--model_args` so raw text goes
to the server and the server tokenizes. Rule of thumb: when an eval number
contradicts a manual probe, distrust the eval plumbing first.

## `PermissionError` on HF datasets cache after container evals

**Hit:** host `lm_eval` failed to acquire
`~/.cache/huggingface/datasets/.../builder.lock` after lm-eval had been run
inside a container (as root) with the same cache mounted. Root-owned lock
and metadata files block the host user afterwards.
**Fix:** re-chown (`docker run --rm -v ~/.cache/huggingface/datasets:/d
IMAGE chown -R 1000:1000 /d/<dataset>`), or run containerized tools with
`--user $(id -u):$(id -g)` from the start.

## Spec-decode throughput undercounted by the acceptance factor (harness bug)

**Hit:** speculative-decoding A/B results were undercounted even though the
runtime trace showed higher token throughput.
**Cause:** `validate/bench_serve.py` counted SSE stream chunks as tokens.
Without spec decode, one chunk = one token and the number is right. Under
speculative decoding, vLLM emits one chunk per VERIFIED BLOCK (up to k+1
tokens), so throughput was silently divided by the mean accepted-block size
(the factor depends on the accepted block size). A second-order variant of the
same trap is that synthetic "repeat this sequence" prompts can depress draft
acceptance vs natural text.
**Fix:** request `stream_options: {"include_usage": true}` and take token
counts from `usage.completion_tokens` (bench_serve does this now); use
`--prompt-style natural` for any spec-decode measurement.
**Moral:** for any metric derived from a stream, verify the stream's unit.
Re-run any affected measurement with the corrected harness; do not carry an
old verdict forward.
