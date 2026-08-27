# Validation ledger

This ledger separates current profile labels, Model Serving Release decisions,
physical measurements, and deterministic control-plane tests. None of those
authorities substitutes for another.

## Current reviewed state

- The tracked ADR 0004 registry under `models/model-serving-releases/` is
  empty.
- No current profile sets `MODEL_SERVING_RELEASE_ID`.
- No current profile therefore has a reviewed Model Serving Release status.
- Serving remains status-independent; concrete identity, recipe, topology,
  capacity, security, ownership, and lifecycle checks still fail without
  fallback.
- `qwen3.8-27b-fp8`, `qwen3.8-27b-fp8-2node`, and
  `qwen3-1.7b-2node` are retained as untested recipe shells. Their prior
  model-specific evidence is not retained, and all three require new
  onboarding before any qualification or promotion claim.

The older profile `STATUS=tested*` class remains a recommendation input. It is
not the ADR 0004 decision `Validated` and does not authorize serving.

## Qualification scopes

| Scope | What evidence can establish |
|---|---|
| Catalog and artifact service | Exact content, receipt and occupancy, placement, transfer, working views, retention, recovery, and cleanup |
| Serving integration | Exact-source launch, readiness, warmup, completion smoke, and owned stop |
| Model qualification | Stability, accuracy, throughput, latency, strict same-boot reproducibility, context, and soak for exact runtime inputs |
| Release and promotion | Reviewed provenance/security, physical geometry, and every required scope combined for one Model Serving Release |

A failure in one scope does not erase evidence in another unless a causal
connection is shown. It does block any combined claim that requires the failed
scope. Catalog or artifact evidence never satisfies a Validation Contract
criterion.

## Current profile guidance

This is an evidence summary, not a serving allowlist. The wizard shows every
serving profile that fits the confirmed topology with its actual caveats.

| Profile | Profile label | Retained evidence summary |
|---|---|---|
| `nemotron-3-nano-30b-nvfp4` | `STATUS=tested` | 61.9 output tok/s at c=1, 399 aggregate at c=16, gsm8k 0.830, same-boot exact captures, 15-minute clean soak; Gate 14 separately covers a bounded catalog/artifact lifecycle |
| `nemotron-3-super-120b-nvfp4` | `STATUS=tested` | 16.2 output tok/s at c=1, 113 aggregate at c=32, gsm8k 0.940, 20-minute clean soak; MTP remains opt-in |
| `qwen3.6-27b-fp8` | `STATUS=tested` | 8.0 output tok/s at c=1, 93 aggregate at c=16, gsm8k 0.615, 20-minute clean soak; ngram speculative decode is forbidden |
| `qwen3.6-27b-fp8-2node` | `STATUS=do-not-use` | Cross-node GDN behavior remains unsuitable; do not infer a supported two-rank recipe from the one-rank result |
| `qwen3.8-27b-fp8` | `STATUS=untested` | Recipe shell only; no retained onboarding or Model Serving Release evidence |
| `qwen3.8-27b-fp8-2node` | `STATUS=untested` | Recipe shell only; no retained onboarding or Model Serving Release evidence |
| `qwen3-1.7b-2node` | `STATUS=untested` | Diagnostic recipe shell only; no retained onboarding or Model Serving Release evidence |

Raw retained measurements are indexed in [`results/README.md`](../results/README.md).
Changing model bytes, recipe, image, or geometry creates a new subject; these
summaries do not transfer automatically.

## Model-library evidence

| Path | Status and boundary |
|---|---|
| [`results/model-library/model-library-home-removal-guard-20260811.json`](../results/model-library/model-library-home-removal-guard-20260811.json) | Catalog/artifact evidence over disposable synthetic repositories. It proves removal-guard behavior, not model serving or qualification. |
| [`results/model-library/nemotron-3-nano-source-attested-gate-20260817.json`](../results/model-library/nemotron-3-nano-source-attested-gate-20260817.json) | **Gate 14: BOUNDED PHYSICAL GATE — catalog/artifact only.** It covers one-rank acquisition, receipt attachment, full verification, prepare/reuse, guarded cleanup, and reacquisition. Remote-target acquisition, asymmetric credentials, serving integration, model qualification, and Model Serving Release review were not run. |

No retained model-library artifact establishes a reviewed status for any
profile.

## Current deterministic control-plane coverage

The following are implementation checks, not physical DGX claims:

| Contract | Deterministic coverage |
|---|---|
| Model Serving Release schemas and registry | Closed release, Validation Contract, run-record, evidence-bundle, decision, supersession, filesystem-layout, content-ID, and graph verification tests |
| Status projection | Unique reviewed decision, neutral unbound profile, unavailable/ambiguous registry, runtime-access mismatch, launch independence, JSON separation from `STATUS`, and narrow human output |
| Hugging Face home acquisition | Exact selector resolution, complete inventory, privacy-safe plan, target eligibility, private staging, full hashes, all-rank absence checks, immutable receipt, atomic no-replace publication, and offline `home verify` |
| Cold recovery set | Separate receipt replica, model archive verification, explicit receipt recovery, receipt-ID restore, full-size admission, private staging, full rehash, and atomic publication |
| Operator-owned cold failure domain | Same-device recovery sets are accepted when content verifies; nested path hazards remain refused; Pulsar makes no storage-independence claim |
| Profile-bound relocation | Multi-rank profiles stay within their exact ranks; raw model identities require an explicit matching profile; geometry is checked before destination inspection or mutation |
| Atomic catalog refresh | Strict receipt/occupancy classification, directory identity, recomputed primary policy, one atomic mode-`0600` write, JSON/file parity, and prior-catalog preservation on invalid control state |
| Supervised onboarding and staging skills | Workflow boundaries, explicit confirmations, non-authority behavior, privacy rules, and refusal to invent evidence or status |

Run `scripts/selftest.sh` for the complete deterministic suite. Passing it does
not create model-specific evidence or a Model Serving Release decision.

## Evidence and publication rules

- Freeze the Validation Contract before collecting results.
- Bind every measurement to one exact model, recipe, image, and geometry.
- Strict same-boot reproducibility is exact. Floating-point similarity is
  diagnostic and cannot satisfy that criterion.
- Retain every applicable observation produced by a new attempt. An exclusion
  needs an explicit evidence-backed record.
- A staged proposal is not trusted. Repository review and merge establish the
  tracked registry.
- Do not set `MODEL_SERVING_RELEASE_ID` without publishing the complete reviewed
  object graph in the same change.
- State every physical gate that was not run.

Use [`REVALIDATE.md`](./REVALIDATE.md) for the next onboarding and qualification
cycle.
