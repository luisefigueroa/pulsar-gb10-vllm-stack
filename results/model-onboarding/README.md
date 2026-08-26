# Model onboarding evidence

This directory contains privacy-reviewed, repository-publishable evidence for
ADR 0004 Model Serving Release onboarding. Raw captures, workflow journals,
source-attested receipts, site topology, and candidate trees remain under
gitignored local state and are not published here.

## Qwen3.8-27B-FP8 — 2026-08-19 (historical)

The tracked registry no longer holds this lineage. Former release
`8fd9c4380205214c3671a00cc92b275adfd66f1231d52e72995c88fc836a96a7`
had advisory status `Testing incomplete`.

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

## Qwen3.8-27B-FP8 2-node TP=2 — 2026-08-26 (historical)

The tracked registry no longer holds this lineage. Former release
`2c653ea4fc96bed639978a7da7eb15347432e20874b8598ea2b1bafdb60e0933`
had advisory status `Testing incomplete`. Provenance/security components are
all `pending`; leftover `review_evidence_artifact_ids` are empty. The
reviewed evidence artifacts are protected.

- `qwen3.8-27b-fp8-2node/compare.json`: strict same-boot comparison, 30/30 exact.
- `qwen3.8-27b-fp8-2node/bench.json`: 32 requests at concurrency 1, 15.116712
  aggregate output tokens/s, 298.248664 ms p95 TTFT.

These closed measurement files are capture sources. They do not authorize
serving, bind `MODEL_SERVING_RELEASE_ID`, change legacy `STATUS`, or claim
`Validated`. Stability, accuracy, serving integration, and physical geometry
remain unevaluated.
