# Results index

Present-tense ship claims live in `docs/VALIDATION.md` (current ship set).
This directory is the raw evidence map. Historical/superseded files stay;
do not quote them as today's geometry or spec-decode verdicts.

| File | What |
|---|---|
| `qwen1.7b-*` | canary: vLLM vs HF reference (FP-EQUIVALENT), node2 run-to-run, batch-invariant bit-identity across nodes |
| `qwen27b-fp8-run{A,B,C,D}*.json` | 27B determinism: same-boot IDENTICAL, cross-boot/cross-node near-tie flips |
| `qwen27b-noat/eager/BI-*` | divergence isolation: autotune-off, eager, batch-invariant |
| `qwen27b-ngram*.json` | ngram spec decode FAIL (corrupted on GDN hybrid, both attention backends) |
| `qwen27b-tp2-2node.json` | (absent — engine hung before capture completed; see VALIDATION) |
| `laguna-*.json` | Laguna determinism (FLASHINFER-path noise isolation), 2-node eager parity. DFlash: historical FAIL under the broken meter; corrected +13% optional in `bench-laguna-dflash-natural-fixed.json` |
| `super-*.json`, `nano-*.json` | Nemotron captures + MTP A/B |
| `dsv4-*.json`, `*-0731*`, `*-20gb*` | flagship captures and 0731 / 20 GB geometry gates. Canonical soak: `soak-dsv4-20gb-150min.json` (c=5, 3201 req). Pre-20 GB soaks remain historical. gsm8k 0.925 lives in `lm-eval-dsv4-0731-500kv/` (10 GB / 500K), not the 20 GB section |
| `needle-dsv4-20gb-447k.log` | only shipped needle transcript: 20 GB DeepSeek 3/3 @447K. Qwen 27B / Laguna / Nano needle PASS rows in VALIDATION.md have no `results/` file |
| `bench-*.json` | concurrency sweeps (validate/bench_serve.py, warmup per level) |
| `weight-fabric/<tag>/` | experimental live NFS/RDMA storage bundles: public provenance/manifest, rank I/O/CPU/memory, interface counters, integrity, traffic proof, and privacy audit |
| `model-library/` | federated-library preparation, SSH-over-RoCE, topology trust, seals/bundles, and current/superseded evidence index (`model-library/README.md`) |
| `soak-*.json` | soak reports (errors, memory drift, thermals) |
| `lm-eval-*/` | gsm8k runs (5-shot, 200 samples). Current 0731 flagship: `lm-eval-dsv4-0731/` (0.935). `lm-eval-dsv4/` 0.970 is pre-0731. NOTE: `lm-eval-laguna/` is the INVALID 0.055 run (client-side tokenization bug, kept as evidence); `lm-eval-laguna-textmode/` is the valid one |
| `../bench/results/step0/` | NCCL sweeps + transport verification logs |
