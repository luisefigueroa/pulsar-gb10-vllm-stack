# Results index

| File | What |
|---|---|
| `qwen1.7b-*` | canary: vLLM vs HF reference (FP-EQUIVALENT), node2 run-to-run, batch-invariant bit-identity across nodes |
| `qwen27b-fp8-run{A,B,C,D}*.json` | 27B determinism: same-boot IDENTICAL, cross-boot/cross-node near-tie flips |
| `qwen27b-noat/eager/BI-*` | divergence isolation: autotune-off, eager, batch-invariant |
| `qwen27b-ngram*.json` | ngram spec decode FAIL (corrupted on GDN hybrid, both attention backends) |
| `qwen27b-tp2-2node.json` | (absent — engine hung before capture completed; see VALIDATION) |
| `laguna-*.json` | Laguna determinism (FLASHINFER-path noise isolation), DFlash FAIL, 2-node eager parity |
| `super-*.json`, `nano-*.json` | Nemotron captures + MTP A/B |
| `dsv4-*.json` | flagship captures, MTP A/B |
| `bench-*.json` | concurrency sweeps (validate/bench_serve.py, warmup per level) |
| `weight-fabric/<tag>/` | experimental live NFS/RDMA storage bundles: public provenance/manifest, rank I/O/CPU/memory, interface counters, integrity, traffic proof, and privacy audit |
| `model-library/` | federated-library activation, SSH-over-RoCE performance, topology trust, promotion assessment, and current/superseded evidence index |
| `soak-*.json` | soak reports (errors, memory drift, thermals) |
| `lm-eval-*/` | gsm8k runs (5-shot, 200 samples). NOTE: `lm-eval-laguna/` is the INVALID 0.055 run (client-side tokenization bug, kept as evidence); `lm-eval-laguna-textmode/` is the valid one |
| `../bench/results/step0/` | NCCL sweeps + transport verification logs |
