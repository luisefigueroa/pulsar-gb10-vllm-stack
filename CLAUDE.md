# CLAUDE.md

vLLM serving stack for the 2x DGX Spark GB10 cluster (dgx-spark-1 head /
dgx-spark-2 worker). Read docs/HARDWARE.md first — every design choice traces
to a measured number there.

## Ground rules (from PROMPT.md, all still binding)

- Priority: stability > accuracy > throughput > latency.
- Never enable a flag/kernel not validated on THIS hardware. Statuses in
  models/*.conf and docs/VALIDATION.md are earned by runs, not vibes.
- Anything touching model math needs a before/after eval number.
- Spec decode is OFF everywhere by measurement (all methods slower or broken
  here — docs/VALIDATION.md "Speculative decoding"). Don't re-enable without
  a new A/B.

## Operational cheat sheet

- `./serve.sh <name> -d` (single node), `cluster/start-cluster.sh <name>`
  (2-node; ALWAYS `cluster/stop-cluster.sh` first if in doubt).
- Node 2 has NO internet. Stage weights with rsync over 10.100.120.2, images
  with `docker save | ssh ... docker load`. Fix missing HF `refs/main` after
  manual cache surgery (TROUBLESHOOTING.md).
- The two image pins live in `Dockerfile` (digest) and `.env.example`.
  After ANY pin bump: clear vLLM/Triton caches on both nodes and re-run
  the validation suite (validate/ + docs/VALIDATION.md gates).
- Known landmines: GDN/Mamba hybrids must not run cross-node TP=2 on the
  official image; cross-node TP=2 on the official image needs
  `--enforce-eager`; `/health` lies for ~5 min after a node loss; lm-eval
  needs `tokenized_requests=False` for models with broken tokenizer regex.

## Layout

- `models/*.conf` — one validated flag set per model (bash-sourced).
- `cluster/` — 2-node launch/preflight/teardown + measured NCCL env.
- `validate/` — capture/compare/needle/bench/soak harness. compare gives
  IDENTICAL / FP-EQUIVALENT / DIVERGENT verdicts; hard disagreements
  (logprob delta > 0.5 at divergence) are the red flag, near-ties are not.
- `results/` — raw evidence for every claim (see results/README.md).
- Prior art: ~/Github/claude-opus-5-vllm-gb10-optimized (earlier build,
  useful knowledge base; its cross-node hang is root-caused in our
  VALIDATION.md), Keys-Concurrency repo (DSpark fork results).
