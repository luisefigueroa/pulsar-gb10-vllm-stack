# Review-declaration notes

The closed schema lives in
[MODEL_SERVING_RELEASE_ISSUANCE.md](../../../../docs/MODEL_SERVING_RELEASE_ISSUANCE.md).
Do not fork it here.

## Extract IDs from the candidate

From the verified `--candidate-dir`:

- `candidate.json` → `candidate_id`, `release_id`, `contract_id`, `bundle_id`
- `evidence-bundle.json` → `evidence_artifacts[]` and
  `review_evidence_artifact_ids`

`review_evidence_artifact_ids` are extra review files besides the runs, not a
second copy of compare/bench. Empty after onboarding capture is expected.
The gitignored issue-review file does not populate that list.

For each artifact take `artifact_id`, `visibility`, `privacy_review`
(still `pending` on a draft candidate), and `location.value`.
The review file must list every original `artifact_id` once, sorted.

Publishable locations are repository-relative `results/` paths. Protected
locations are content-addressed and are not copied as raw publishable bytes.

## Privacy answers

`privacy_review` on each review-file artifact is a maintainer answer:
`passed`, `failed`, or `pending`. Issuance copies publishable bytes only
when `passed` and the file digest still matches. `failed` or `pending`
does not publish raw bytes. `evidence_privacy` must match that
disposition. Passing every artifact makes `evidence_privacy` `pass`,
which requires extra `release-promotion` review files. Keep
privacy `pending` when that extra-review-file list is empty and provenance is not
judged.

## Provenance answers

The five `provenance_security_review` fields are maintainer component
outcomes (`pass` / `fail` / `pending` per the live schema). They are not
inferred from a green `verify-candidate`. Health and completion smoke do
not prove them. All five `pending` is the incomplete path; do not recapture
a `results/` essay to create extra review-file IDs. Cite extra
`release-promotion` review files only when any component is `pass` or `fail`.

## Expected status

`expected_status` must equal the status `issue.sh plan` derives. Incomplete
required criteria typically yield `testing-incomplete`. Do not assert
`validated` to hide gaps.

## Exclusions

If used, follow the runbook: original `criterion_id`, `run_record_id`,
ordinary-language `reason`, and original `review_evidence_artifact_ids`.
Sorted and unique. Do not exclude a failure to manufacture a pass.

## Extra measurements

Files not named as candidate evidence (for example an extra concurrency
sweep) are not staging inputs unless they were in the frozen contract
and captured into that candidate. They still fail `stage` if left
untracked in the worktree. Park them. Candidate `results/` measurement
files must still exist so `plan`/`stage` can hash them.
