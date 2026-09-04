# Revalidation runbook

Use this runbook after changing any input to a Model Serving Release: exact
model bytes, serving recipe, image, or supported hardware geometry. A change to
one of those inputs creates a new Model Serving Release. Prior model-specific
results do not transfer automatically.

## Current baseline

- The tracked registry under `models/model-serving-releases/` is empty.
- No current profile sets `MODEL_SERVING_RELEASE_ID`.
- `qwen3.8-27b-fp8`, `qwen3.8-27b-fp8-2node`, and
  `qwen3-1.7b-2node` are untested recipe shells. They carry no retained
  onboarding, qualification, or Model Serving Release evidence.
- Profile `STATUS=tested*` is the older recommendation class. It is not an
  ADR 0004 decision and does not authorize serving.
- Accepted target: one release spec is the contract ([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md)); this section remains the live implementation until that staged cutover.

## Qualification scopes

Keep four independent questions separate:

| Scope | What must be shown |
|---|---|
| Catalog and artifact service | Exact content, receipt and occupancy, durable placement, preparation, retention, recovery, and cleanup |
| Serving integration | The exact recipe and image load the intended local files, become ready, answer a completion, and stop through owned cleanup |
| Model qualification | Stability, accuracy, throughput, latency, strict same-boot reproducibility, context, and soak requirements frozen for this subject |
| Release and promotion | Provenance/security and physical geometry review plus every required result above |

A pass in one scope does not satisfy another. A deterministic selftest is not
physical DGX evidence.

## 1. Establish the exact subject

1. Start from a reviewed profile diff under `models/`.
2. Resolve the Hugging Face source to an exact commit and complete file list.
3. Record the digest-pinned image and exact one-rank or multi-rank geometry.
4. Freeze the Validation Contract before collecting results.
5. Keep drafts under `experiments/`; do not write the trusted registry.

For a model that has no home yet:

```bash
scripts/model-library.sh home add <profile> \
  --revision <selector> --plan --json

scripts/model-library.sh home add <profile> \
  --revision <exact-commit-from-plan> \
  --node <selected-rank-from-plan> --yes --json

scripts/model-library.sh catalog refresh
scripts/model-library.sh home verify <model_id@exact-commit> --json
```

The download creates a receipt and occupancy identity. It does not create a
Model Serving Release or a validation decision.

## 2. Run deterministic checks before hardware work

```bash
scripts/selftest.sh
scripts/model-serving-release-registry.sh verify
scripts/list-models.sh --json
```

The registry command must report a valid empty registry until a reviewed
publication is merged. Do not add a profile binding before its complete object
graph is reviewed in the same change.

## 3. Prepare the exact runtime view

For a one-rank profile:

```bash
scripts/model-library.sh prepare <one-rank-profile> --yes
scripts/up.sh <one-rank-profile> --dry-run
```

For a multi-rank profile:

```bash
scripts/topology-ssh-trust.sh enroll
scripts/topology-ssh-trust.sh check
scripts/model-library.sh prepare <multi-rank-profile> \
  --backend copy --transport ssh-roce --copy-streams 8 --yes
cluster/preflight.sh <multi-rank-profile>
```

Preparation must full-verify the receipt-backed content on every serving rank.
It must not fall back to another transport, geometry, or source.

## 4. Prove serving integration

For supervised onboarding only, start the experiment resource monitor after
the exact all-rank preparation barrier and immediately before the qualifying
launch. Use the workflow's gitignored state directory and the same one-rank
placement, when applicable:

```bash
scripts/model-serving-experiment-monitor.sh start <profile> \
  --state-dir experiments/model-onboarding/workflows/<workflow>/resources \
  [--node <selected-rank>]
```

The default one-second sampler runs locally on each exact serving rank. Raw
time-series data remains private in that directory. The monitor is not part of
`pulsar`, the wizard, `up.sh`, cluster launch, status, inventory, or ordinary
catalog serving after issuance.

Use the profile's actual launcher:

```bash
# one rank
scripts/up.sh <one-rank-profile>

# multiple ranks
cluster/start-cluster.sh <multi-rank-profile>
```

Record startup, health, warmup, one non-streaming completion, one streaming
completion, runtime identity, and owned stop. On multi-rank jobs, confirm the
intended NCCL/RoCE transport rather than inferring it from `/health`.

```bash
./pulsar status <profile>
./pulsar stop <profile>
```

## 5. Run the frozen model-qualification gates

Run only against the already-running exact subject:

```bash
validate/run-gates.sh <served-name> --tag <candidate-tag>
```

Add the contract's required accuracy, context, concurrency, and soak commands.
Do not invent missing output or relax a threshold after seeing results. Strict
same-boot reproducibility is exact; floating-point similarity is diagnostic.

For each qualifying measurement attempt window, distill one privacy-safe,
status-neutral summary under `results/`:

```bash
scripts/model-serving-experiment-monitor.sh summarize \
  --state-dir experiments/model-onboarding/workflows/<workflow>/resources \
  --started-at <attempt-start-utc> --ended-at <attempt-end-utc> \
  --qualification-scope model-qualification \
  --result-json results/<tag>/<operation>-resources.json
```

Attempt composition requires that summary as run diagnostic evidence. It may
honestly report complete, partial, or unavailable collection; it never changes
the criterion result or Model Serving Release status. If no closed summary
exists, record a capture gap rather than inventing one. Its window must equal
the attempt timestamps.

After the final owned model stop, stop the experiment monitor. This stops only
monitor processes and retains private raw samples:

```bash
scripts/model-serving-experiment-monitor.sh stop \
  --state-dir experiments/model-onboarding/workflows/<workflow>/resources
```

For closed GSM8K and soak measurements, use the frozen dataset revision,
dataset file SHA-256, sample selection, reasoning mode, duration, and
concurrency from the contract:

```bash
python3 validate/gsm8k_eval.py --model <served-name> \
  --dataset <private-exact-dataset-file> --dataset-id openai/gsm8k \
  --dataset-revision <exact-commit> --sample-size <count> \
  --result-json results/<tag>/accuracy-gsm8k.json
python3 validate/soak.py --model <served-name> \
  --minutes <minutes> --concurrency <count> \
  --result-json results/<tag>/stability-soak.json
```

The GSM8K workload parameters must freeze `dataset_file_sha256` to the digest
recorded by the measurement; a public revision label alone does not prove the
local dataset bytes.

Compose each with `model-serving-release-attempt.sh compose-extra`; this does
not evaluate thresholds, issue status, or substitute for context, integration,
geometry, or provenance evidence. Each extra-attempt context must name the
matching `observe-resources` summary for the same timestamps and scope.

The retained recipes have no carried-forward evidence. For example, running
either Qwen3.8 recipe or the Qwen3 1.7B two-rank draft begins a new onboarding
record, not a continuation of an older result.

### Baseline-v1 for a release spec

A measured [ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md)
spec is judged against the lab-wide policy in `policy/baseline-v1.json`.
Start the profile as written with `./pulsar start`, then run the six gates
in one boot with the runner:

```bash
validate/baseline-v1.sh <profile|spec_id> --spec <measured-spec> \
  --out results/baseline-v1/<spec_id> --dataset <exact-dataset-file> \
  [--tag <label>] [--soak-concurrency 8]
```

The runner refuses to start unless every producer answers `--help`, the
tracked tree equals the lab commit the evidence will name (`HEAD`, or an
explicit `--lab-commit` that the tree must match), the dataset digest equals
the policy pin, the running container carries the profile's current launch
contract, the spec's image digest, and the spec's speculative-decode state,
the spec is the identity the catalog computes for the profile (released or
not), and the served model answers `/v1/models`. One-node profiles served on
this node only; multi-node runs wait for the two-node milestone. It then
runs, in order,
`validate/verify_snapshot_manifest.py`, `validate/serve_smoke.py`,
`validate/run-gates.sh` (captures and the bench sweep at the policy's
concurrency levels), `validate/gsm8k_eval.py` with the policy's pinned
arguments, and `validate/soak.py` for the policy's duration; it stops at
the first failed gate and keeps every document
already written. A boot witness (the served model's registration epoch) is
read before and after; a restart during the run voids it. Finally it calls
`validate/baseline_v1.py`, which fills `measurements[]` and `evidence[]`
into `<out>/spec.json` and prints `proposed_status=stable|failed`, and
writes `<out>/run.json` with the gate windows and the witness. The pinned
GSM8K file is Parquet, so the lab host needs `pyarrow`
(see [PREREQUISITES.md](./PREREQUISITES.md)).

The evaluator never writes `review`. Promotion is
`scripts/release-spec.sh promote <out>/spec.json --reviewer <maintainer>
--out releases/<spec_id>.json`, committed by a reviewed PR.

## 6. Capture and stage a reviewed proposal

The onboarding skill composes planning, acquisition, preparation, launch, and
measurement capture. It remains non-issuing. After capture, the maintainer
issuance workflow may stage an untrusted proposal from the independently
verified draft and explicit review declaration.

A successful local stage command does not make the objects trusted. Repository
review and merge establish the tracked registry state. Add
`MODEL_SERVING_RELEASE_ID` only as a separately reviewed profile edit in that
same publication change.

## 7. Model-library physical follow-ups

The retained catalog and artifact ledger includes the bounded Nemotron Nano
Gate 14 acquisition lifecycle. It does not cover a remote target, asymmetric
credentials, physical cold restore, or model serving. When changing those
paths, capture the missing physical cases without extending Gate 14 beyond its
recorded scope.

The operator decides whether the configured cold root is an independent
failure domain and owns its access-control policy. Pulsar accepts inherited
ownership, modes, and ACLs while verifying operational access, path safety,
and the receipt/archive recovery set; it does not verify the operator's
storage architecture or access policy.

After removal of the legacy `/mnt/Models` container mount, rerun one-rank and
multi-rank serving integration before making a current launch claim. Confirm
the container has only the verified local model-view mount, loads the exact
receipt-backed snapshot, passes health/warmup/completion smoke, and stops
through owned cleanup. This does not require rerunning unrelated catalog
mechanics or imply model qualification.

## 8. Close out

Before publication:

```bash
git diff --check
scripts/selftest-docs-privacy.sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/testlib/test_docs_current_state.py
scripts/selftest.sh
```

Then update `docs/VALIDATION.md` and the relevant `results/` index with only
the evidence actually produced. State which physical gates were not run. A
failed or incomplete new attempt stays visible in its new evidence set; it
must not be converted into a pass.
