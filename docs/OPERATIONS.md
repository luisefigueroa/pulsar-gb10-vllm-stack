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

## Start / stop

- Single node: `./serve.sh <name> -d`; stop with `docker rm -f vllm-<name>`.
- 2-node: `cluster/preflight.sh <name>` then `cluster/start-cluster.sh <name>`.
  **ALWAYS `cluster/stop-cluster.sh` before any relaunch** — a leftover
  worker holds the master port and the new head hangs in rendezvous with no
  error output.
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
docker save IMAGE | ssh "$WORKER_IP" docker load
```
(The refs/main line prevents the LocalEntryNotFoundError trap.)

## Expected steady-state numbers (alert if far off)

Flagship under load (DSpark recommended): ~27 tok/s base / **~43–48 tok/s**
spec-on single-stream; ~105 tok/s aggregate at c=8 base path; node temps
≤81–84 C, SM clock ≥2380 MHz. Memory
available fluctuates ±2 GiB with page cache — only a monotonic decline over
hours is a leak signal (none observed in 150-min soaks).

## Safety rails

- Spec decode is **not** "try random methods": only enable paths that have
  validated `SPEC_DECODE_ARGS` and a positive ledger entry
  (docs/VALIDATION.md). **Do** use `--spec-decode` on the flagship (DSpark
  k=5 recommended). **Do not** enable ngram on GDN hybrids (corrupts
  output). Super MTP is opt-in; Laguna DFlash is marginal.
- Never run a GDN hybrid (qwen3.6-27b) cross-node.
- After any image change: clear `~/.cache/vllm` + Triton cache on BOTH
  nodes, then run docs/REVALIDATE.md before calling it production.
