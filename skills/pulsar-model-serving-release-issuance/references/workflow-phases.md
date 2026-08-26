# Issuance phase checklists

Load this file when executing a phase. The skill remains the procedure.
These checklists do not grant authority and do not replace
`docs/MODEL_SERVING_RELEASE_ISSUANCE.md` or `issue.sh`.

## 0. Branch hygiene

- Clean non-default branch in the real repository.
- Not detached HEAD. Not the default branch.
- Unrelated dirty files fail; stop and park them.
- Equal files from an interrupted earlier `stage` may remain.

## 1. Intake

```text
scripts/model-serving-release-capture.sh verify-candidate \
  --candidate-dir DIR --json
```

Stop unless verified, `unreviewed`, and the `candidate_id` matches.
Do not mutate the candidate.

## 2. Coverage

- List each `criterion_id` and whether `run_record_ids` is empty.
- Name missing required criteria as unevaluated/incomplete.
- Empty `review_evidence_artifact_ids` after compare/bench capture is
  expected. Do not recapture a maintainer essay to fill it.
- Extra measurements outside the frozen contract stay extra.
- State the likely derived status before asserting `expected_status`.

## 3. Privacy

For each `evidence_artifacts[]` row: visibility, repository path or
protected locator, explicit `passed` / `failed` / `pending`.
Scan publishable `results/` files for site identity. Leaks fail closed.

## 4. Provenance/security

Ask for each component. Do not default to `pass`:

- artifact identity
- runtime identity
- contract frozen before testing
- evidence privacy
- security

Unreviewed components stay `pending`. All five `pending` means the
decision cites no leftover review artifacts.

## 5. Review file

Write under gitignored `experiments/` or outside the repo.
Use the live schema in `docs/MODEL_SERVING_RELEASE_ISSUANCE.md`.
Bind `candidate_id`. Cover every original artifact ID once, sorted.
Confirm `expected_status` as a separate decision.

## 6. Plan

```text
scripts/model-serving-release-issue.sh plan \
  --candidate-dir DIR --review-file FILE --json
```

Derived status must equal `expected_status`. Stop on mismatch.

## 7. Stage

Separate confirmation. Then:

```text
scripts/model-serving-release-issue.sh stage \
  --candidate-dir DIR --review-file FILE --json
```

Local success is not trust.

## 8. Profile bind

Separate confirmation. Same PR as the staged lineage only.
`issue.sh` does not edit the profile.

## 9. PR and after merge

Open a ready-for-review PR. Do not merge.
After the user reports merge: `validation-bundle verify` and
`scripts/selftest.sh`. Status remains advisory.
