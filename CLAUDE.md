# CLAUDE.md

vLLM serving stack for the 2x DGX Spark GB10 cluster (dgx-spark-1 head /
dgx-spark-2 worker). Read docs/HARDWARE.md first — every design choice traces
to a measured number there.

## Ground rules (from PROMPT.md, all still binding)

- Priority: stability > accuracy > throughput > latency.
- Never enable a flag/kernel not validated on THIS hardware. Statuses in
  models/*.conf and docs/VALIDATION.md are earned by runs, not vibes.
- Anything touching model math needs a before/after eval number.
- Spec decode: verdicts CORRECTED 2026-07-31 after a harness metering bug
  (docs/VALIDATION.md retraction trail). DSpark on the flagship is +79%,
  Super MTP +47% — both validated opt-in via --spec-decode; default-on is
  gated on a spec-enabled soak. The standing hard failure: ngram on GDN
  hybrids corrupts output — never enable it there. Pre-07-31 "all spec
  decode loses" claims anywhere are stale; the ledger wins.

## Operational cheat sheet

- `./serve.sh <name> -d` (single node), `cluster/start-cluster.sh <name>`
  (2-node; ALWAYS `cluster/stop-cluster.sh` first if in doubt).
- Node 2 has NO internet. Stage weights with rsync over 10.100.120.2, images
  with `docker save | ssh ... docker load`. Fix missing HF `refs/main` after
  manual cache surgery (TROUBLESHOOTING.md).
- THREE image pins: `Dockerfile` (official v0.26.0 digest, mainline),
  `vllm-gb10:pr41834-d64074e6f` (local source build of vLLM PR #41834 —
  the DeepSeek-V4 flagship since 2026-07-30; recipe in docs/BUILD.md), and
  the sparkrun fallback in `models/deepseek-v4-flash-sparkrun.conf`.
  After ANY pin bump: clear vLLM/Triton caches on both nodes and re-run
  the validation suite (validate/ + docs/VALIDATION.md gates).
- Known landmines: GDN/Mamba hybrids must not run cross-node TP=2 on the
  official image; cross-node TP=2 on the official image needs
  `--enforce-eager`; DeepSeek-V4 on STOCK release images livelocks under
  prefill load (that's why the flagship uses the PR-41834 build); `/health`
  lies for ~5 min after a node loss; lm-eval needs
  `tokenized_requests=False` for models with broken tokenizer regex; never
  put a `pkill -f` in a compound command whose own cmdline matches the
  pattern (it kills its own shell — bracket-trick the pattern instead).

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
