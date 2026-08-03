# Revalidation runbook — after any image pin bump

The pin bump is the recurring event on this stack (upstream release or PR
#41834 rebase/merge). Nothing keeps its `tested` status across a bump. This
is the public, repository-relative sequence; expect roughly half a day,
mostly machine time.

## 0. Prep (5 min)

```bash
# From the repository root. Two-node commands require .env with HEAD_IP and
# WORKER_IP (see .env.example and docs/PREREQUISITES.md).
# clear JIT caches on BOTH nodes — stale Triton cache silently corrupts on sm_121
rm -rf ~/.cache/vllm ~/.triton 2>/dev/null
ssh "$WORKER_IP" 'rm -rf ~/.cache/vllm ~/.triton 2>/dev/null'
# stage the new image to node 2
docker save <new-image> | ssh "$WORKER_IP" docker load
# free memory if other workloads ran
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches
```
Update the pin (`Dockerfile` digest or the conf's `IMAGE=`) in a branch.
Keep the OLD captures in results/ — they are the A/B baselines. Use a unique
`--tag`; the runner refuses to overwrite any matching artifact. Built-in
Python clients automatically use `VLLM_API_KEY` / `API_KEY` when configured.

## 1. Canary (10 min)

```bash
./serve.sh qwen3-1.7b -d               # healthy in ~2 min or the image is broken
validate/run-gates.sh qwen3-1.7b --tag <bump-tag>
./pulsar stop qwen3-1.7b
cluster/stop-cluster.sh --all
cluster/start-cluster.sh qwen3-1.7b-2node   # multi-node plumbing
cluster/stop-cluster.sh qwen3-1.7b-2node
```

## 2. Single-node models (~1 h, mostly load time)

For each of `laguna-s-2.1-nvfp4`, `nemotron-3-nano-30b-nvfp4`,
`nemotron-3-super-120b-nvfp4`, `qwen3.6-27b-fp8`:

```bash
./serve.sh <name> -d && <wait healthy>
validate/run-gates.sh <served-name> \
    --baseline results/<prior-capture-runA>.json \
    --needle-tokens <its validated ctx: 250000 laguna / 125000 nano+qwen / 0 super> \
    --tag <bump-tag>
./pulsar stop <name>
```
Gate reading: same-run comparison is strict (`--require-identical`) by
default. It must be IDENTICAL for FLASH_ATTN-path models (qwen, nano). For
Laguna only, explicitly append `--allow-fp-equivalent-run-to-run` because its
known FLASHINFER-path noise is FP-equivalent. Vs-baseline must have ZERO hard
disagreements; an incomplete warmup or measured concurrency level exits
nonzero. Bench must remain within ~5% of the README table or be investigated.

**grep the engine log on every first boot** — backend selection changes
silently across versions:
```bash
docker logs vllm-<name> 2>&1 | grep -E "attention backend|MoE backend|LinearMethod|Unknown vLLM env"
```

## 3. gsm8k spot (per quant-sensitive model, ~10 min each)

```bash
HF_HUB_OFFLINE=0 lm_eval --model local-completions \
  --model_args "base_url=http://127.0.0.1:8000/v1/completions,model=<served>,tokenizer=<hf-id-or-path>,num_concurrent=16,max_retries=2,timeout=600,tokenized_requests=False" \
  --tasks gsm8k --num_fewshot 5 --limit 200 --output_path results/lm-eval-<name>-<tag>
```
ALWAYS `tokenized_requests=False`. Gate: within stderr (±0.035) of the
recorded score. For broken-tokenizer models run lm-eval inside the vLLM
container (TROUBLESHOOTING.md).

## 4. Flagship 2-node (~2 h)

```bash
cluster/preflight.sh deepseek-v4-flash
cluster/start-cluster.sh deepseek-v4-flash  # default DSpark ship path; NCCL_DEBUG=INFO on first bump boot
docker logs vllm-cluster-deepseek-v4-flash 2>&1 | grep -m2 "NET/IB"   # RDMA, not TCP
# THE STOCK-KILLER STRESS SEQUENCE — all three killed stock v0.26.0:
validate/run-gates.sh deepseek-v4-flash --baseline results/dsv4-0731-dspark-capture.json --tag <bump-tag>
#   (gate 1 = 30 sequential captures; gate 3's fresh prefills = the livelock trigger)
API_KEY_VALUE="${VLLM_API_KEY:-${API_KEY:-}}"
AUTH_HEADER=()
[ -n "$API_KEY_VALUE" ] && AUTH_HEADER=(-H "Authorization: Bearer $API_KEY_VALUE")
for i in $(seq 1 8); do curl -fsS --max-time 300 "${AUTH_HEADER[@]}" http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"deepseek-v4-flash\",\"prompt\":\"topic $i:\",\"max_tokens\":60,\"temperature\":0}" -o /dev/null & done; wait
curl -fs "${AUTH_HEADER[@]}" http://127.0.0.1:8000/health && echo SURVIVED
# needle at the claimed context:
python3 validate/needle.py --model deepseek-v4-flash --context-tokens 450000 --depths 0.05 0.5 0.95
# gsm8k as in step 3 (expect ~0.945-0.97)
```

## 5. Soaks (promotion gate — background, ~5 h total)

```bash
python3 validate/soak.py --model deepseek-v4-flash --minutes 150 --concurrency 5 \
    --out results/soak-dsv4-<tag>.json        # then teardown, then:
./serve.sh laguna-s-2.1-nvfp4 -d
python3 validate/soak.py --model laguna-s-2.1 --minutes 150 --concurrency 4 \
    --out results/soak-laguna-<tag>.json
```
Gate: process must print `PASS soak` and exit **0** (default: any request
error fails; `completed>0`). MemAvailable shrink &gt;5% is a **WARN** finding
by default — review it; use `--fail-on-mem-shrink` only for strict CI.
Also check SM clock within ~2% of 2405 in the JSON summary.

## 6. Close out

- Update conf `STATUS`/`NOTES` and `docs/VALIDATION.md` with the measured
  numbers, exact image identity, selected backends, and artifact paths.
- Mark the prior pin/rows **SUPERSEDED**; do not delete old evidence.
- Archive the new raw results under `results/` using a unique bump tag.
- Run a current-tree secret/path scan and inspect `git diff` before merge.
- Merge the pin branch only after every required gate passes.

Anything that fails: the pin does not land. File it in TROUBLESHOOTING.md
with the failing command and artifact path, keep the old pin, and open an
upstream issue when the evidence points outside this repository.
