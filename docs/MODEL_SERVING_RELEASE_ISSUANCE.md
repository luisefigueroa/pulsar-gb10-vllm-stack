# ADR 0004 Model Serving Release issuance staging

This is the maintainer runbook for the deterministic issuance workflow.
It turns one independently verified unreviewed evidence-capture candidate
plus an explicit review declaration into a staged proposal of the exact
content-addressed Model Serving Release registry objects and any
privacy-cleared publishable evidence.

A successful local `plan` or `stage` does not establish trust. Repository
review and merge remain the trust event. The existing pure schema modules
derive the decision status. Status stays advisory and never permits or
blocks serving. This workflow does not edit a model profile, bind
`MODEL_SERVING_RELEASE_ID`, authorize serving, or claim physical DGX
behavior.

The command is not routed through `./pulsar`, the wizard, or catalog
recommendation order.

## System boundary

| Subsystem | Responsibility |
|---|---|
| Capture candidate (`scripts/model-serving-release-capture.sh`) | Independently verified unreviewed input. The issuer never mutates it. |
| Issue review declaration (this document) | Closed maintainer input. It is not a sixth ADR object, not evidence, and not status authority. |
| ADR 0004 release/contract schema (`scripts/model_serving_release.py`) | Owns release-descriptor and frozen Validation Contract schema version 1 |
| ADR 0004 evidence schema (`scripts/model_validation_evidence.py`) | Owns evidence artifacts, run records, bundles, decisions, status derivation, exclusions, and supersession |
| Tracked registry (`scripts/model-serving-release-registry.sh`) | Read-only load, graph verification, and inspection. The issuer may ask it to validate an in-memory prospective graph but does not add a write command there. |
| Issuance staging (`scripts/model-serving-release-issue.sh`) | Plans or stages the proposal. Local success is not review. |

The Bash entrypoint is the operator boundary. Python owns review-declaration
validation, deterministic rematerialization, prospective graph assembly,
safe writes, and both human and JSON rendering.

## Commands

```text
scripts/model-serving-release-issue.sh plan \
    --candidate-dir DIR --review-file FILE [--json]
scripts/model-serving-release-issue.sh stage \
    --candidate-dir DIR --review-file FILE [--json]
```

`plan` is a read-only exact preview. `stage` repeats verification and writes
the proposal. Use `--json` for the stable machine-readable payload. Human
output states that staged objects are not trusted until repository review
and merge.

`stage` operates only on a clean non-default branch in the real repository.
It refuses detached HEAD and the default branch. Unrelated dirty files fail.
Equal planned proposal files from an interrupted earlier `stage` are allowed
so retry can complete. `plan` and `stage` still hash the candidate's original
publishable `results/` measurement files even when privacy is `pending`;
those files must exist. Extra untracked files are not issuance inputs and
fail the clean-worktree check.

## Review declaration

The review file is workflow input with schema version 1 and kind
`pulsar-model-serving-release-issue-review`. It must:

- bind the exact verified candidate ID
- cover the candidate's original artifact-ID set once each
- supply each artifact's privacy result (`passed`, `failed`, or `pending`)
- supply provenance/security component outcomes
- list any explicit evidence-backed criterion exclusions
- assert the expected base status
- name a privacy-safe reviewer, reviewed-at time, closed review reference,
  and any direct superseded decision IDs

This review file is not evidence. It does not populate bundle
`review_evidence_artifact_ids`. After onboarding capture of compare and
bench only, that leftover list is empty; that is expected. Do not recapture
a maintainer essay to make it non-empty. See the 2026-08-25 interpretation
note in [ADR 0004](./decisions/0004-model-serving-release-validation.md).

Keep the review file outside the tracked worktree or under an appropriate
gitignored `experiments/` directory. A first incomplete issuance typically
looks like:

```json
{
  "schema_version": 1,
  "kind": "pulsar-model-serving-release-issue-review",
  "candidate_id": "<capture candidate SHA-256 ID>",
  "artifacts": [
    {
      "artifact_id": "<original artifact SHA-256 ID>",
      "privacy_review": "pending"
    }
  ],
  "provenance_security_review": {
    "artifact_identity": "pending",
    "runtime_identity": "pending",
    "contract_frozen_before_testing": "pending",
    "evidence_privacy": "pending",
    "security": "pending"
  },
  "criterion_exclusions": [],
  "expected_status": "testing-incomplete",
  "reviewer": "<privacy-safe maintainer ID>",
  "reviewed_at": "<RFC 3339 UTC time>",
  "review_reference": "repository-review:<privacy-safe change ID>",
  "supersedes_decision_ids": []
}
```

When every provenance/security component is `pending`, the staged decision
cites an empty leftover list. When any component is `pass` or `fail`, the
decision must cite the bundle's non-empty `review_evidence_artifact_ids`,
and those artifacts must be `release-promotion` leftovers rather than
compare/bench measurements. A publishable `results/` provenance/security
document is optional supporting evidence for that conclusive case only;
attach it through capture `review_source_keys` before `verify-candidate`.

A later all-pass review still uses the same kind, with `privacy_review`
and provenance components set to `passed`/`pass` and
`expected_status` matching the derived result (`validated` only when every
frozen criterion passed).

Artifact entries, exclusions, and superseded decision IDs must be sorted and
unique. An exclusion names the original candidate `criterion_id`,
`run_record_id`, ordinary-language `reason`, and one or more original
leftover `review_evidence_artifact_ids`; issuance remaps those IDs into the
reviewed object graph. Exclusions still require leftover review artifacts
because the reason document is not the run being excluded.

Allowed review references are `pr:<id>`, `commit:<40-or-64-hex>`, and
`repository-review:<privacy-safe-id>`. That syntax cannot prove review
occurred. This workflow does not contact GitHub or any network.

The expected status is an assertion. The command fails if it differs from
the status derived by `build_validation_decision`. Reviews do not have to
pass merely to record an accurate non-`Validated` status. Empty leftover
`review_evidence_artifact_ids` with every provenance/security component
`pending` is a legal incomplete issuance. Do not invent review evidence.

## Materialization

The issuer never rewrites the capture candidate. It builds reviewed
evidence artifacts, then rebuilds every dependent run record and bundle
through the existing pure builders. Attempt IDs, timestamps, measurements,
commands, environment observations, completion facts, and evidence content
digests are preserved. Every top-level and nested criterion, context,
soak, review, and exclusion evidence reference is remapped exactly.

Privacy handling:

- `privacy_review=passed` keeps the candidate visibility. A publishable
  artifact may be copied to its already-declared `results/` path only when
  the bytes match the declared digest.
- `privacy_review=failed` or `pending` does not publish raw bytes. If the
  candidate artifact was publishable, the reviewed artifact becomes
  `visibility=protected` with the schema's content-addressed locator. The
  content digest, media type, and qualification scope stay the same.
- `evidence_privacy` must match that artifact disposition. Passing every
  artifact makes `evidence_privacy` `pass`, which is conclusive and
  requires leftover `release-promotion` review artifacts. Incomplete
  issuance with an empty leftover list keeps every provenance component
  `pending`, including privacy.

Predecessor and supersession source sets come only from the tracked registry.
Normal planning fully verifies that registry first. If an interrupted earlier
stage left an incomplete exact proposal, issuance performs a strict layout,
JSON, kind, and filename-to-declared-ID scan so it can assemble the completed
prospective graph. Content identity and every other normal graph rule must pass
before any write. Unrelated invalid objects still make the operation fail. The
issuer does not invent missing lineage.

## Staging writes

Before the first write, `stage` prepares a complete prospective graph from
verified existing objects plus the proposed objects and validates that graph.
During interrupted-stage recovery, existing objects receive the strict scan
described above and the complete merged graph must pass every normal graph
rule. Candidate evidence bytes and all destinations are checked first.

Writes are content-addressed and idempotent, in dependency order: publishable
evidence, descriptors, contracts, run records, evidence bundles, then the
decision. An equal existing object or evidence file is reused. Any unequal
destination collision, symlink, unsafe path, unexpected filesystem type, or
path escape fails. Destination parent directories must already exist. Writes
use no-follow exclusive creation, the shared `pretty_json_bytes` encoding,
and file and parent-directory fsyncs. Existing directory modes are not
changed.

Cross-file atomicity is not required. An interrupted `stage` may leave a
visibly incomplete proposal. Retry performs the strict scan described above,
merges the completed graph in memory, validates that prospective graph, and
continues. The normal registry verifier does not report the store valid
until every required object exists. Recovery never deletes unrelated or
preexisting content.

After a complete write, `stage` runs the normal `load_registry` verifier.

## Profile binding

This command does not edit a model profile. A later issuance pull request
may add `MODEL_SERVING_RELEASE_ID` only in the same reviewed publication
that stores and verifies the exact lineage. Existing registry, catalog, and
dry-run projection checks verify that separate edit.

## Trust and scope

- Local schema shape is not the trust event.
- Validation status is advisory.
- Deterministic selftests prove control-plane contracts only.
- This workflow makes no physical DGX claim.
- After repository merge, verify with
  `scripts/model-serving-release-registry.sh verify`. Schema-1
  `validation-bundle verify` is a different command and fails on unsealed
  ADR 0004 profiles.

The supervised `pulsar-model-serving-release-issuance` skill composes these
commands after an onboarding handoff. It has no issuance authority.

See [ADR 0004](./decisions/0004-model-serving-release-validation.md)
(including the 2026-08-25 leftover-list note),
[MODEL_SERVING_RELEASE_CAPTURE.md](./MODEL_SERVING_RELEASE_CAPTURE.md),
and [REVALIDATE.md](./REVALIDATE.md).
