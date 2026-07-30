# Revalidation runbook — after ANY image pin bump

The pin bump is the recurring event on this stack (upstream release, PR
#41834 rebase/merge, sparkrun update). Nothing keeps its `tested` status
across a bump. This is the exact sequence; expect ~half a day, mostly
machine time.

## 0. Prep (5 min)

```bash
# clear JIT caches on BOTH nodes — stale Triton cache silently corrupts on sm_121
rm -rf ~/.cache/vllm ~/.triton 2>/dev/null
ssh 10.100.120.2 'rm -rf ~/.cache/vllm ~/.triton 2>/dev/null'
# stage the new image to node 2
docker save <new-image> | ssh 10.100.120.2 docker load
# free memory if other workloads ran
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches
```
Update the pin (`Dockerfile` digest or the conf's `IMAGE=`) in a branch.
Keep the OLD captures in results/ — they are the A/B baselines.

## 1. Canary (10 min)

```bash
./serve.sh qwen3-1.7b -d               # healthy in ~2 min or the image is broken
validate/run-gates.sh qwen3-1.7b --tag <bump-tag>
docker rm -f vllm-qwen3-1.7b
cluster/stop-cluster.sh; cluster/start-cluster.sh qwen3-1.7b-2node   # multi-node plumbing
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
docker rm -f vllm-<name>
```
Gate reading: run-to-run must be IDENTICAL for FLASH_ATTN-path models
(qwen, nano) and FP-EQUIVALENT for Laguna (known FLASHINFER-path noise);
vs-baseline must have ZERO hard disagreements; bench within ~5% of the
README table or investigate.

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
cluster/start-cluster.sh deepseek-v4-flash        # NCCL_DEBUG=INFO on first bump boot
docker logs vllm-cluster-deepseek-v4-flash 2>&1 | grep -m2 "NET/IB"   # RDMA, not TCP
# THE STOCK-KILLER STRESS SEQUENCE — all three killed stock v0.26.0:
validate/run-gates.sh deepseek-v4-flash --baseline results/dsv4-pr41834-capture.json --tag <bump-tag>
#   (gate 1 = 30 sequential captures; gate 3's fresh prefills = the livelock trigger)
for i in $(seq 1 8); do curl -fsS --max-time 300 http://127.0.0.1:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"deepseek-v4-flash\",\"prompt\":\"topic $i:\",\"max_tokens\":60,\"temperature\":0}" -o /dev/null & done; wait
curl -fs http://127.0.0.1:8000/health && echo SURVIVED
# needle at the claimed context:
python3 validate/needle.py --model deepseek-v4-flash --context-tokens 450000 --depths 0.05 0.5 0.95
# gsm8k as in step 3 (expect ~0.945-0.97)
```

## 5. Soaks (promotion gate — background, ~5 h total)

```bash
python3 validate/soak.py --model deepseek-v4-flash --minutes 150 --concurrency 8 \
    --out results/soak-dsv4-<tag>.json        # then teardown, then:
./serve.sh laguna-s-2.1-nvfp4 -d
python3 validate/soak.py --model laguna-s-2.1 --minutes 150 --concurrency 4 \
    --out results/soak-laguna-<tag>.json
```
Gate: 0 errors, no monotonic memory decline, SM clock within ~2% of 2405.

## 6. Close out

- Update conf STATUS/NOTES + docs/VALIDATION.md rows with the new numbers.
- Run the KB stack-supersession ritual: new `/stacks/` entry,
  `kb.py todo --stack <old>` triage, `kb.py validate && kb.py index`
  (see `.claude/skills/knowledge-capture`).
- Copy new raw results into `/mnt/Models/knowledge/evidence/<tag>/`.
- Merge the pin branch.

Anything that fails: the pin does not land. File it in TROUBLESHOOTING.md
and the KB, keep the old pin, and check the failure against the known
signatures first (`kb.py query --tag stability`).
