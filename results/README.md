# Results index

Present-tense claims live in [`docs/VALIDATION.md`](../docs/VALIDATION.md).
This directory contains the publishable measurements that remain after the
model-specific repository reset.

| Path | What it contains |
|---|---|
| `qwen27b-*`, `bench-qwen27b-*`, `lm-eval-qwen27b-*`, `soak-qwen27b-*` | Qwen3.6 27B measurements |
| `laguna-*`, `bench-laguna-*`, `lm-eval-laguna-*`, `soak-laguna-*` | Laguna measurements retained for their existing ledger claims |
| `nano-*`, `super-*`, `bench-nano.json`, `bench-super-*`, `lm-eval-nano/`, `lm-eval-super/`, `soak-nano-*`, `soak-super-*` | Nemotron measurements |
| [`model-library/`](./model-library/) | Current catalog and artifact evidence index |
| [`model-onboarding/`](./model-onboarding/) | Empty until a model is onboarded again |
| [`weight-fabric/`](./weight-fabric/) | Pointer for retired distribution paths; no model-specific evidence retained |
| `../bench/results/step0/` | NCCL transport measurements |

Do not infer a reviewed Model Serving Release from a raw result file. The
tracked registry under `models/model-serving-releases/` is the authority for
reviewed status, and it is currently empty.

Before staging any result, run
`PYTHONDONTWRITEBYTECODE=1 python3 scripts/check_publishable_privacy.py`.
The full selftest and optional tracked pre-commit hook run the same scanner.
