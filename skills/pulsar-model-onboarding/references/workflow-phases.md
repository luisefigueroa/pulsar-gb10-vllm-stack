# Onboarding phase checklists

Load this file when executing a phase. The skill remains the procedure.
These checklists do not grant authority and do not replace ADR 0004 or the
owning CLIs.

## 0. Branch hygiene

- Start from a clean synced `main`.
- Create a feature branch for the draft profile only.
- Do not mix later evidence work into the profile PR.

## 1. Draft profile

Create `models/<profile>.conf` with:

- public `MODEL="org/name"`
- `SERVED_NAME`, `NODES`, memory fields, and a digest-pinned `IMAGE`
- `STATUS="untested"`
- `FIRST_RUN_CANDIDATE=0`
- no `EXPECTED_MODEL_SEAL`
- no `MODEL_SERVING_RELEASE_ID`
- no recommendation/default promotion
- explicit unbound caveats in `NOTES`

Publish a ready-for-review PR and stop. Do not initialize the journal.

## 2. After the user reports the profile PR merged

- Sync local `main` to that merged commit.
- Create a new feature branch.
- Initialize the journal bound to workflow ID, profile, public model ID,
  and the merged repository/profile base commit.
- Keep it under `experiments/model-onboarding/workflows/<workflow-id>/`, not
  the planner's `experiments/model-onboarding/<profile>/<release-id>/` tree.
- Resume identity must match on every later `verify`.

## 3. Criteria input

Confirm the complete ADR 0004 contract before testing:

- stability, accuracy, throughput, latency
- strict same-boot, provenance/security, serving-integration,
  physical-geometry
- context and soak requirements
- relative performance: N/A unless a valid reviewed comparable predecessor
  is explicitly supplied

State that current automated mapping covers only strict same-boot, absolute
throughput, and absolute latency. Other required criteria remain
unevaluated/incomplete until separately captured.

Agree and record this complete input before testing. The Validation Contract
is not frozen until the planner later combines it with the exact artifact
manifest, runtime envelope, and selected access contract.

## 4. Exact-home assessment and safe reuse

1. Resolve the selector to an exact Hugging Face commit. Do not treat
   `refs/main` as identity.
2. Refresh the catalog and observe all confirmed serving ranks before reuse.
   Do not trust the catalog's shallow `complete` label alone. Reuse one exact
   home only after full verification against a reviewed expected manifest that
   is independent of the observed tree, plus an explicit choice. Refuse a
   missing reviewed expected manifest, failed or incomplete verification,
   partial, wrong-revision, duplicate, or out-of-geometry durable homes.
3. If no independently verified reusable complete exact home exists, stop with
   the implementation gap. Current `home add` requires a reviewed expected seal.
4. Do not download directly into the durable cache. A safe future unsealed
   acquisition path must use private same-filesystem staging, repeat the
   all-rank absence check, verify completeness independently, and publish
   atomically. It also requires a separate large-acquisition confirmation.
5. Do not treat a catalog `complete` label or the later self-observed manifest
   as independent acquisition completeness proof.

## 5. Catalog and manifest

```text
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <model_id@revision>
scripts/model-release.sh manifest <profile> \
  --hub-path <hub-path> --revision <exact-commit>
```

Refuse another revision, ambiguity, a partial tree, or a durable
duplicate. Do not mutate `refs/main`. The manifest records exact identity of
the reused tree; it does not prove that an earlier download was complete.

## 6. Distribution choice

Ask which explicit path to use:

- `library-hot` → `local-verified-readonly`
- live fabric → `live-remote-readonly` only when that contract is satisfied

Record the source and transport. No silent fallback. No automatic
fallback. The default unsealed replicated path follows mutable
`refs/main`, mounts the writable HF home, and passes the repository ID; it
may be served with that honest label but is not an exact ADR 0004
qualification attempt.

## 7. Release plan

After the exact manifest and access choice exist, build and verify the
unreviewed plan before testing:

```text
scripts/model-serving-release-plan.sh build <profile> \
  --artifact-manifest FILE --runtime-envelope FILE --criteria FILE \
  --model-access-contract local-verified-readonly|live-remote-readonly
scripts/model-serving-release-plan.sh verify <profile> \
  --candidate-dir DIR \
  --model-access-contract local-verified-readonly|live-remote-readonly
```

The runtime envelope and geometry checks are structural, not physical proof.

## 8. Preparation, barrier, then launch

1. Invoke the owning library/fabric preparation subsystem for the selected
   path. Do not duplicate its transfer, retention, or cleanup logic.
2. Verify exact content and the intended runtime-access contract on every
   serving rank.
3. If that barrier fails, qualification did not start. Do not record a
   model-criterion failure.
4. Obtain a separate launch confirmation.
5. `scripts/up.sh <profile> --weight-source library-hot` or
   `--weight-source fabric`.

## 9. Identities and measurements

Derive `launch_id` and `server_boot_id` from observed container IDs, image
digest, created/started timestamps, and the launch contract. Use distinct
domain-separated canonical hashes over all serving ranks in rank order and
persist only those hashes. Never use the workflow-journal ID.

Reobserve before correctness, after correctness, and after benchmarking.
Never combine measurements when either identity changes.

Sequential only:

1. persist the frozen invocation plan and match its compare sample size
2. two `validate/greedy_capture.py` runs
3. `validate/compare_captures.py --require-identical --result-json` with its
   own wall-clock UTC start/end
4. load `bench-argv` into an array without `eval`
5. `validate/bench_serve.py --result-json` with the frozen invocation plan
   and its own wall-clock UTC start/end

Do not use `validate/run-gates.sh` as the ADR attempt wrapper. Stop after
an interruption. Preserve a complete closed failed measurement even when the
validator exits nonzero. Never fabricate a missing validator measurement.
Never share one enclosing timestamp. Attempt-composer measurement documents
must be privacy-reviewed repository-relative files under `results/`.

## 10. Capture

If the validator did not write a closed measurement, keep the journal
event and report the capture gap.

Otherwise:

```text
scripts/model-serving-release-attempt.sh compose \
  --release-plan DIR --context FILE --output-dir DIR \
  --compare-measurement FILE --benchmark-measurement FILE
scripts/model-serving-release-capture.sh capture-run \
  --release-plan DIR --attempt-spec FILE
scripts/model-serving-release-capture.sh assemble-bundle \
  --candidate-dir DIR [--candidate-dir DIR ...]
scripts/model-serving-release-capture.sh verify-candidate \
  --candidate-dir DIR
```

Capture immediately after compose.

## 11. Cleanup

Separate destructive-cleanup confirmation. Normal stop path first. Then
ownership-safe purge/teardown only for resources this workflow created.
Refuse if a managed service still uses the resource.
