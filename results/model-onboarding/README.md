# Model onboarding evidence

This directory contains privacy-reviewed, repository-publishable evidence for
ADR 0004 Model Serving Release onboarding. Raw captures, workflow journals,
source-attested receipts, site topology, and candidate trees remain under
gitignored local state and are not published here.

## Qwen3.8-27B-FP8 — 2026-08-19

The reviewed release
`8fd9c4380205214c3671a00cc92b275adfd66f1231d52e72995c88fc836a96a7`
has advisory status `Testing incomplete`.

- `qwen3.8-27b-fp8/compare.json`: strict same-boot comparison, 30/30 exact.
- `qwen3.8-27b-fp8/bench.json`: 32 requests at concurrency 1, 8.376791
  aggregate output tokens/s, 449.527272 ms p95 TTFT.
- `qwen3.8-27b-fp8/provenance-security-review.json`: privacy-safe review basis
  for artifact identity, runtime identity, contract freeze, evidence privacy,
  and security.

Stability, accuracy, serving integration, and physical geometry remain
unevaluated in the reviewed bundle. These artifacts do not authorize serving,
promote experimental one-rank `library-hot`, change legacy `STATUS`, or claim
`Validated`.
