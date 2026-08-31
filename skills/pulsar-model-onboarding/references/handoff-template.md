# Onboarding handoff template

Use this structure at the end of a workflow or after a hard stop. The
handoff is not a validation decision and does not assign status.

## Identity

- Profile:
- Public model ID:
- Exact revision (if resolved):
- Workflow ID:
- Repository/profile base commit:
- Release ID (draft, if planned):
- Contract ID (draft, if planned):
- Runtime-access contract attempted:
- Weight source / transport (explicit choice):

## Completed evidence

List only artifacts that actually exist. Use repository-relative paths.

- Catalog/artifact:
- Exact home: download receipt and offline verification, or an explicit gap
  (unknown trees without a receipt fail without fallback)
- Source digest / acquisition approval / receipt IDs:
- Serving integration:
- Strict same-boot compare measurement:
- Absolute throughput/latency benchmark:
- Per-attempt resource diagnostic summaries (complete/partial/unavailable):
- Private raw resource trace: workflow-relative location only; not publication evidence
- Draft attempt specs:
- Draft capture candidates:
- Assembled bundle candidate:
- Extra review-file IDs (`review_evidence_artifact_ids`): empty
  unless a review source was captured; empty after compare/bench is
  expected

## Missing criteria

Current automated mapping covers only strict same-boot, absolute
throughput, and absolute latency. Name every other frozen criterion as
unevaluated/incomplete unless separately captured.

- Stability:
- Accuracy:
- Provenance/security: review-derived; not a compose output
- Serving integration:
- Physical geometry:
- Context / soak:
- Relative performance: N/A unless a valid reviewed comparable predecessor
  was explicitly supplied

## Failures and inconclusive results

Preserve failed and partial work. Do not rewrite a failure as a pass.

- Preparation/barrier failures (qualification unstarted):
- Interrupted measurements (capture gap, no invented ADR run):
- Inconclusive compare/benchmark documents:
- Identity changes that prevented combining measurements:
- Resource diagnostic gaps or monitor interruptions:

## Candidate locations

Repository-relative only. No absolute paths, hostnames, or topology IDs.

## Authority

No seal was issued. No validation decision was issued. No status was
assigned. No profile was bound to a release. Nothing was published into
the trusted registry. No path was promoted. No physical behavior is
claimed. Deterministic skill or journal checks create no release decision
and make no physical DGX claim. Staging that can become trusted is a separate
maintainer workflow (`scripts/model-serving-release-issue.sh`), supervised
by `pulsar-model-serving-release-issuance`. This skill does not run it. A
staged local proposal is not trusted until repository review and merge.
