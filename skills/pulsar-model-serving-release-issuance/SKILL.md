---
name: pulsar-model-serving-release-issuance
description: Supervise maintainer staging of one independently verified draft ADR 0004 capture candidate into an untrusted proposal and optional same-PR MODEL_SERVING_RELEASE_ID field. Use for Model Serving Release issuance, issue.sh plan or stage, binding MODEL_SERVING_RELEASE_ID, promoting a capture candidate into the registry, or when the user runs /pulsar-model-serving-release-issuance.
---

# Pulsar Model Serving Release Issuance

Compose existing staging CLIs for one verified draft capture candidate.
This skill has no authority. Local `plan` or `stage` does not establish trust.
Repository review and merge remain the trust event: they are what make the
objects trusted. Never invent a review
outcome, mutate a capture candidate, assign `Validated` to skip missing
criteria, authorize serving, promote a path, or claim physical behavior.

Read `AGENTS.md`, [ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md)
§6 and the 2026-08-25 extra-review-files note, and
[MODEL_SERVING_RELEASE_ISSUANCE.md](../../docs/MODEL_SERVING_RELEASE_ISSUANCE.md)
before acting. Reuse `scripts/model-serving-release-capture.sh verify-candidate`
and `scripts/model-serving-release-issue.sh`. Do not duplicate their schemas.

`pulsar-model-onboarding` stops at handoff and must not run `issue.sh`.
This skill is that later maintainer workflow. Extra measurements that were
not in the frozen contract stay extra.
Captured `observe-resources` summaries are different: they are already
same-scope run diagnostic evidence, not criteria or extra review files, and
must be preserved with their run.

There is no orchestration journal. Recovery is the verified candidate
directory, the review file, and `plan` / `stage`.

Phase checklists:
[references/workflow-phases.md](references/workflow-phases.md).
Review-file notes:
[references/review-declaration-notes.md](references/review-declaration-notes.md).

## Hard stops

- Status is advisory and never blocks serving.
- Never mutate the capture candidate.
- Never run `stage` on the default branch, detached HEAD, or a tree with
  unrelated dirty files.
- Never bind `MODEL_SERVING_RELEASE_ID` in a different change than the
  staged lineage. `issue.sh` does not edit a profile.
- Never auto-pass provenance/security components or privacy results.
- Never treat health, warmup, or completion smoke as model qualification
  ([ADR 0002](../../docs/decisions/0002-subsystem-qualification-boundaries.md)).
- Never set `FAMILY_RECOMMENDED` or `RECOMMENDED_SPEC` as part of first
  staging.
- `expected_status` is an assertion. It must equal the status derived by
  `plan`. Do not change evidence to force `validated`.
- Do not contact GitHub to prove that a `review_reference` occurred.
- Never recapture a maintainer essay to populate
  `review_evidence_artifact_ids`. That list of extra review files is empty after
  compare/bench capture; empty is expected.
- Never start, resume, or stop experiment resource monitoring during issuance.
  Monitoring belongs only to supervised physical onboarding and is not part of
  catalog serving or staging.

## Collaboration

Ask before every material decision. Require **separate confirmations**
immediately before:

1. **`expected_status`**
2. **`stage`**
3. **profile bind** (`MODEL_SERVING_RELEASE_ID` in the same PR)

Privacy results and the five provenance/security components are also
explicit maintainer answers. If the user refuses, stop.

## Workflow

### 1. Intake and re-verify

Take the assembled capture `--candidate-dir` (or the onboarding handoff
path). Re-run:

```text
scripts/model-serving-release-capture.sh verify-candidate \
  --candidate-dir DIR --json
```

Stop unless `ok` is true, `state` is `unreviewed`, and `candidate_id`
matches the directory. Do not mutate that tree.

### 2. Coverage inventory

From the candidate `evidence-bundle.json`, list each criterion and whether
it has included run records. Name unevaluated/incomplete criteria plainly.
Relative performance stays N/A unless a reviewed comparable predecessor was
in the frozen contract. Empty `review_evidence_artifact_ids` after
compose/capture of compare and bench is expected; those files are already
run evidence.
For every run, inventory the `observe-resources` diagnostic among its
`evidence_artifact_ids` and report its completion/reason. A partial or
unavailable diagnostic is preserved and does not change the derived status.
An absent diagnostic in a candidate produced by the current composer is a
candidate defect; do not collect or fabricate one during issuance.

State the likely derived status **before** drafting `expected_status`.
Incomplete required gates typically derive `testing-incomplete`. Reviews
do not have to produce `Validated`. Do not copy the one-rank Qwen
workaround of recapturing a provenance essay so the extra-review-file list is
non-empty.

### 3. Privacy scan

For every `evidence_artifacts[]` entry, record visibility, location, and an
explicit `passed` / `failed` / `pending` privacy result. Scan publishable
`results/` files for site paths, hosts, addresses, node IDs, and topology
identifiers. A leak is `failed` or `pending`, not a silent `passed`.
Run `python3 scripts/check_publishable_privacy.py` before `plan`; after
staging, run `python3 scripts/check_publishable_privacy.py --staged`.
A declared review result cannot override a scanner finding.
`privacy_review=passed` on every artifact forces `evidence_privacy=pass`,
which is conclusive and requires extra `release-promotion` review
files. Incomplete staging with an empty extra-review-file list must keep
**all five** provenance components `pending`, including privacy.

### 4. Provenance and security

The five review-declaration components are human judgments, not defaults:

- artifact identity
- runtime identity
- contract frozen before testing
- evidence privacy
- security

Walk them against the candidate's release, contract, measurements, and
publishable files. If a component was not actually reviewed, it is
`pending`, not `pass`. When all five are `pending`, the decision cites
the empty extra-review-file list. Recapture a publishable provenance document only
when a conclusive `pass` or `fail` exists and that file is captured as
`release-promotion` review evidence. The gitignored issue-review file is
not that evidence.

### 5. Draft the review file

Write a closed `pulsar-model-serving-release-issue-review` under gitignored
`experiments/` (or outside the worktree). Fill IDs from the candidate; leave
judgments blank until confirmed. Follow the live schema in
`docs/MODEL_SERVING_RELEASE_ISSUANCE.md`. Extraction notes are in
[references/review-declaration-notes.md](references/review-declaration-notes.md).

Do not embed release, contract, run-record, or decision objects.

### 6. Plan, then compare status

On a **clean non-default branch**:

```text
scripts/model-serving-release-issue.sh plan \
  --candidate-dir DIR --review-file FILE --json
```

Compare asserted `expected_status` with the derived status in the plan.
If they differ, stop and correct the assertion. Do not rewrite measurements.
Empty extra review-file IDs with all five provenance components `pending` is a
legal `testing-incomplete` plan.
Do not invent review evidence to make `plan` succeed.

### 7. Stage after confirmation

After the `stage` confirmation:

```text
scripts/model-serving-release-issue.sh stage \
  --candidate-dir DIR --review-file FILE --json
```

Retry of an interrupted equal proposal is allowed. Unrelated dirty files
and default-branch writes fail without fallback. `plan`/`stage` still hash the
candidate's original `results/` measurement files even when privacy is
`pending`; those files must exist. Extra untracked files (raw captures,
extra sweeps, caches) are not staging inputs: park them or commit only
the candidate measurement files first so the worktree is otherwise clean.

### 8. Optional same-PR profile bind

After a successful `stage`, a separately confirmed edit may set
`MODEL_SERVING_RELEASE_ID` to the staged `release_id` on the matching
profile. Same publication only. Do not change `STATUS`, recommendation
flags unless that is an explicit, in-scope
reviewed change with the REVALIDATE conjunction.

### 9. Pull request, then stop

Open a ready-for-review PR. Do not merge. Local stage is not trust.

After the user reports merge, sync main and run:

```text
scripts/model-serving-release-registry.sh verify
scripts/selftest.sh
```

If the profile was bound, inspect that `release_id`. Do not run
`validation-bundle verify` as the ADR 0004 check: that command is the
schema-1 seal/bundle verifier and fails on live ADR 0004 profiles. Those
checks still do not authorize serving.

## Resume

Re-verify the candidate, re-read the review file, and `plan` again. If
`stage` was interrupted, retry `stage` on the same branch. Do not invent
missing lineage.

## Handoff

List the candidate directory, review file (gitignored), staged object IDs,
derived status, unevaluated criteria, run diagnostic coverage, whether the
profile was bound, the PR, and that no serving permission or physical claim
was produced.
