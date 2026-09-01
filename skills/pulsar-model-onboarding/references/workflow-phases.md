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

State that automated mapping covers strict same-boot, absolute throughput,
absolute latency, GSM8K accuracy, and soak stability. Context,
serving-integration, and physical geometry remain separate capture work.
Provenance/security is review-derived and is not a compose output.

Agree and record this complete input before testing. The Validation Contract
is not frozen until the planner later combines it with the exact artifact
manifest, runtime envelope, and selected access contract.

## 4. Exact-home assessment, Hugging Face download, and safe reuse

1. Refresh the catalog and observe all confirmed serving ranks before reuse.
2. If the repository is absent everywhere, run a read-only Hugging Face
   plan. It asks in-geometry candidate ranks with modern `hf` to resolve the
   selector and complete upstream Git/LFS inventory. A rank that cannot resolve
   the source is ineligible, every successful rank must report the same source,
   and the plan chooses an eligible durable-home rank that already resolved it.
   It does not download model bytes:

   ```text
   scripts/model-library.sh home add <profile> \
     --revision <selector> --plan --json
   ```

3. Review the plan's exact commit, file and byte counts, selected rank,
   serving ranks, identity class, and no-promotion boundary. Do not treat
   `refs/main` as identity.
4. Obtain the separate large-acquisition confirmation. Then pass the exact
   commit from the plan, not the mutable selector:

   ```text
   scripts/model-library.sh home add <profile> \
     --revision <exact-commit-from-plan> \
     --node <selected-rank-from-plan> --yes --json
   ```

5. The service uses the selected rank's local Hugging Face authentication and
   private same-filesystem staging. It checks the complete upstream inventory,
   Hugging Face missing/extra verification, and every file digest; repeats the
   all-rank absence check; writes an immutable receipt; publishes with an
   atomic no-replace rename; and attaches occupancy to the exact published
   directory. Do not download directly into the durable cache. Do not wait
   for a cold NFS archive before continuing.
6. Record the result's exact revision, `source_digest`, `approval_id`, and
   `receipt_id` in the journal. Acquisition is catalog/artifact evidence only;
   it does not issue a seal or decision, assign status, promote a path, prove
   physical behavior, refresh the catalog, prepare a runtime view, or launch.
7. Reuse a receipted home only after
   `scripts/model-library.sh home verify <model_id@revision> --json` completes
   an offline full SHA-256 rehash against the immutable receipt while occupancy
   names that live directory. Occupancy may move with
   `scripts/model-library.sh home relocate <profile> --node RANK --yes`. An
   older tree without a receipt fails without fallback (ADR 0012).
8. Refuse a missing required receipt, failed or incomplete
   verification, partial or wrong-revision content, a duplicate occupancy home,
   or an out-of-geometry home. Unbound-complete trees with a compatible receipt
   are relocate, not Hub re-add. Do not treat a catalog `complete` label or a
   later self-observed manifest as independent completeness proof.

## 5. Catalog and manifest

```text
scripts/model-library.sh catalog refresh
scripts/model-library.sh catalog show <model_id@revision>
scripts/model-library.sh home verify <model_id@revision> --json
```

Refuse another revision, ambiguity, a partial tree, or a durable
duplicate. Do not mutate `refs/main`. Run `home verify` for receipted
content. Unknown trees without a receipt fail without fallback (ADR 0012).
`scripts/model-release.sh` is retired.

## 6. Distribution choice

Confirm local files on every rank (`local-files`) → `local-verified-readonly`.
Do not offer live NFS/RDMA serving (`live-remote-readonly`; ADR 0005).
The model library is the only weight mechanism (ADR 0006).

Record the source and transport. No silent fallback. No automatic
fallback. A live profile serves as `receipt-occupancy` after full
verification; that honest label is not an exact ADR 0004 qualification
attempt.

## 7. Release plan

After the exact manifest and access choice exist, build and verify the
draft plan before testing:

```text
scripts/model-serving-release-plan.sh build <profile> \
  --artifact-manifest FILE --runtime-envelope FILE --criteria FILE \
  --model-access-contract local-verified-readonly
scripts/model-serving-release-plan.sh verify <profile> \
  --candidate-dir DIR \
  --model-access-contract local-verified-readonly
```

The runtime envelope and geometry checks are structural, not physical proof.

## 8. Preparation, barrier, then launch

1. Invoke the owning library preparation subsystem for the selected
   path. Do not duplicate its transfer, retention, or cleanup logic.
2. Verify exact content and the intended runtime-access contract on every
   serving rank.
3. If that barrier fails, qualification did not start. Do not record a
   model-criterion failure.
4. Start `scripts/model-serving-experiment-monitor.sh` in the private workflow
   directory, using the selected one-rank placement when applicable.
5. Obtain a separate launch confirmation.
6. `scripts/up.sh <profile>`.

The monitor is onboarding-only. Never call it from normal catalog-serving
entrypoints or leave it running after the final owned stop. If launch is
refused, fails, or the workflow stops early, retain the private samples, stop
the monitor, and record the gap without inventing a run record.

## 9. Identities and measurements

Derive `launch_id` and `server_boot_id` from observed container IDs, image
digest, created/started timestamps, and the launch contract. Use distinct
domain-separated canonical hashes over all serving ranks in rank order and
persist only those hashes. Never use the workflow-journal ID.

Reobserve before correctness, after correctness, and after benchmarking.
Never combine measurements when either identity changes.

Sequential only:

1. persist the frozen invocation plan and match its compare sample size
2. record compare `started_at`, then run two `validate/greedy_capture.py` runs
3. `validate/compare_captures.py --require-identical --result-json` with its
   own `ended_at`
4. load `bench-argv` into an array without `eval`
5. `validate/bench_serve.py --result-json` with the frozen invocation plan
   and its own wall-clock UTC start/end

After each attempt, run `model-serving-experiment-monitor.sh summarize` with
that exact window and `model-qualification` scope. Store the privacy-safe
summary in `results/`, add it to `resource_diagnostic_sources`, and journal the
reference. Complete, partial, and unavailable summaries are status-neutral;
missing summary output is a capture gap. Raw samples remain private.

After the final owned model stop, stop the experiment monitor. Monitor stop is
not a second model stop.

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

Capture immediately after compose. Empty `review_evidence_artifact_ids`
is expected; do not add a review source.
Every composed attempt contains one `run_diagnostic_source_keys` entry. It is
same-scope run evidence, not a criterion source or review evidence.

## 11. Cleanup

Separate destructive-cleanup confirmation. Normal stop path first. Then
ownership-safe purge/teardown only for resources this workflow created.
Refuse if a managed service still uses the resource.
