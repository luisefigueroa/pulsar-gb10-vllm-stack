# Operations runbook — running this stack day to day

Diagnosis lives in TROUBLESHOOTING.md; this page is procedure. Assume
dgx-spark-1 is where you stand; dgx-spark-2 is `ssh "$WORKER_IP"`.

## Monitoring: never trust /health alone on 2-node

After a worker/node loss, the head's `/health` **keeps returning OK for
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
| `./pulsar stop <model\|--all>` | → `scripts/down.sh` (ownership-gated) |
| `./pulsar status [model]` | → `scripts/status.sh` (may submit a completion) |
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
active managed conf/ranks, API models, head/worker MemAvailable + MemTotal and
available %, managed GPU/unified per rank when measured, worker reachability,
unmanaged GPU count + aggregate MiB, stale managed count (nonblocking / no
model memory). Optional follow-ups: refresh, detailed inventory, explicit full
smoke (`status.sh`, may complete), back. Machine-readable: `scripts/quick-status.sh --json`.

### Interactive stop and maintenance

Stop lists only **active** services with `ownership=managed`, `safe_to_stop=true`,
and proven complete ownership. Unknown, legacy, mismatch, incomplete/unproven,
foreign GPU, and worker-unobservable multi-node services are excluded. After
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

## Inventory and ownership

`scripts/inventory.sh` (also `./pulsar inventory`) is **read-only**. It never
stops containers. JSON contract is `schema_version=1` with:

- `services[]`: `conf`, `state` (running/partial/degraded/stale/…), `ownership`
  (managed/legacy/mismatch/unknown/mixed), `safe_to_stop`, `complete`,
  `observability`, ranks, estimated footprint, optional GPU memory
- `nodes.*.mem_available_gib` / optional additive `mem_total_gib`
- `unmanaged_gpu_processes[]`: diagnostics only — **no kill action**
- `worker.status`: `ok` / `unset` / `unreachable` / error

**`safe_to_stop` is true only** when every *observed* rank has
`io.pulsar.gb10.managed=true` and conf/rank labels that map consistently to a
repo profile. Lifecycle scripts (`down.sh`, cluster stop) **revalidate** labels
and IDs before remove. Unlabeled legacy, mismatch, unknown, incomplete, or
worker-unreachable situations are never auto-stopped.

Live inventory fails closed if head Docker cannot answer `info`, enumerate
containers, or return a valid snapshot. A reachable worker with an unavailable
Docker daemon is reported as `worker.status=docker-error`; every non-`ok`
worker status blocks automatic two-node stop/replacement. An operational probe
failure is never converted into an empty “nothing is running” inventory.

Weight readiness means more than “the cache directory exists.” HF profiles
must have `refs/main` resolving to a snapshot with a readable non-empty
`config.json` and at least one non-empty weight file; `.incomplete` markers and
local shard indexes that reference missing/empty files fail preflight. Two-node
profiles are checked on both nodes. Docker/SSH failures are reported as
operational failures and never offered as a download/pull problem.

The memory checker grants “already loaded” mode only to a running exact-name
service whose stack ownership, selected conf, and every expected rank are
proven by labels. API model identity or an unlabeled lookalike is insufficient.

Human default output is concise (active + actionable managed stale/mismatch);
`--verbose` includes inactive unknown/legacy detail; `--json` is always full.

## Model switch (wizard)

`./pulsar wizard` (not the no-arg home) runs doctor once, then a **selection loop**
(“Choose another model” returns to the list without re-running doctor unless you exit).

Before each start plan it consumes inventory JSON + `check-memory.sh` and shows
a short target summary (not raw JSON). Decision highlights:

1. **Same profile running + API healthy** — Keep running (recommended), Restart
   (stop only after final confirm), Show status, Choose another model. Never
   silent replacement.
2. **Different complete managed blocker** — names conf/state/ranks/memory and
   `safe_to_stop`; offers stop listed stack-managed service(s) → recheck →
   start, keep current, choose another, or diagnostics.
3. **Partial/degraded managed** — explains observed vs expected ranks; cleanup
   only if every observed rank is inventory-safe and worker observability
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
- Single node low-level: `./serve.sh <name> -d`. Do **not** `docker rm -f` by
  name unless inventory proves ownership — prefer `./pulsar stop <name>`.
- 2-node: `cluster/preflight.sh <name>` then `cluster/start-cluster.sh <name>`.
  **ALWAYS `cluster/stop-cluster.sh` or `./pulsar stop <name>` before any
  relaunch** — a leftover worker holds the master port and the new head hangs
  in rendezvous with no error output.
- After 2-node health, `start-cluster.sh` runs `validate/warmup.py` once
  (short+medium prompts, c=1 and c=4, stream and non-stream). That pays
  DSpark/Triton/block-FP8 JIT so the first real client is not the cold path.
  Skip with `--skip-warmup` (falls back to a single smoke completion).
  Manual: `python3 validate/warmup.py --url http://127.0.0.1:8000 --model <served>`.
- **Flagship DeepSeek defaults** (`models/deepseek-v4-flash.conf`) target
  **few long agent sessions** (≤5 concurrent, 500K client cap, tools/code),
  not high-QPS chat: 20 GB/rank KV, `max-num-seqs 5`, batch 16384, tool+
  reasoning parsers. Before resizing KV further: `drop_caches` both nodes,
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

- Head: `docker logs vllm-cluster-<name>` (or `vllm-<name>` single-node).
- Worker: `ssh "$WORKER_IP" docker logs vllm-cluster-<name>` — the head's
  log usually shows only the RPC timeout; the cause is worker-side.
- On any first boot / after upgrades, grep selections:
  `grep -E "attention backend|MoE backend|Unknown vLLM env" ` — vLLM drops
  env vars silently across versions.

## Staging anything to node 2 (no internet there)

```bash
rsync -rlptD ~/.cache/huggingface/hub/models--ORG--NAME "$WORKER_IP":.cache/huggingface/hub/
ssh "$WORKER_IP" 'd=~/.cache/huggingface/hub/models--ORG--NAME; [ -e $d/refs/main ] || { mkdir -p $d/refs; ls $d/snapshots | head -1 | tr -d "\n" > $d/refs/main; }'
scripts/sync-image.sh <model> --pull --yes
```
(The refs/main line prevents the LocalEntryNotFoundError trap. The image helper
also repairs digest references that a bare `docker load` can omit.)

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
- Launcher-created containers carry stack ownership, profile, and rank labels
  (`io.pulsar.gb10.managed`, `.conf`, `.rank`). Wizard and `down.sh` only stop
  services inventory marks `safe_to_stop`; `down.sh` revalidates before remove.
  Legacy unlabeled containers are refused. Worker unreachable blocks automatic
  multi-node cleanup/replacement. See “Model switch (wizard)” above.
- Worker SSH uses BatchMode, a finite connect timeout/attempt count, and
  liveness bounds. Host values are passed after the SSH option terminator.
- Built-in API probes and Python validators honor `VLLM_API_KEY` / `API_KEY`.
  Dry-run command rendering redacts HF and API credentials. Prefer environment
  configuration over putting secrets in shell history.
- Rollback after a failed switch: restart the previous conf from **current**
  profile defaults (`models/<conf>.conf`), not a snapshot of prior CLI flags.
  Example: `./pulsar start deepseek-v4-flash` or wizard “Restart previous
  profile from current config”.
- Never run a GDN hybrid (qwen3.6-27b) cross-node.
- After any image change: clear `~/.cache/vllm` + Triton cache on BOTH
  nodes, then run docs/REVALIDATE.md before calling it production.
