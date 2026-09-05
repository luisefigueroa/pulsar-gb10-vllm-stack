# Onboarding phases (ADR 0017 Stage 4)

Checklist form of the workflow in [../SKILL.md](../SKILL.md). Each phase
names its inputs, the command that owns it, and what stops it.

## 1. Draft

Write a conf-format draft in a gitignored lab location with:

- `MODEL="<org/name>"`
- `IMAGE="vllm/vllm-openai@sha256:<digest>"` (digest-pinned; a tag blocks
  spec generation)
- `NODES`, `GPU_MEM_UTIL`, `ENGINE_ARGS=( ... )`, `CONTAINER_ENV=( ... )`

No status, recommendation, default, seal, or release field exists. Stop
until the user confirms the draft.

## 2. Journal

`onboarding_journal.py initialize` with the draft name as `--profile`. Bind
`exact_revision`, `receipt_id`, and `spec_id` as they appear. `verify`
before every resume.

## 3. Assess

`scripts/model-library.sh catalog refresh`, then either reuse an exact
receipted home (`home verify <model_id@revision> --json`) or plan the
acquisition read-only:

```text
scripts/model-library.sh home add --draft <draft.conf> --revision <selector> --plan --json
```

Stops: partial tree, another revision, duplicate home, out-of-geometry
home, a rank without modern `hf`.

## 4. Acquire (confirmation: large acquisition)

```text
scripts/model-library.sh home add --draft <draft.conf> \
  --revision <exact-commit-from-plan> --node <selected-rank> --yes --json
```

Outputs the receipt id and the published home. It does not prepare,
launch, or promote.

## 5. Measured spec

```text
scripts/release-spec.sh from-draft <draft.conf> --receipt <receipt.json> \
  --stack-version <main-sha> --out <measured-spec.json> --gap-report <gaps.json>
python3 -m release_spec id <measured-spec.json>
```

A blocking gap stops the workflow. The spec id is now the profile name.

## 6. Prepare

```text
PULSAR_SPEC_FILE=<measured-spec.json> scripts/model-library.sh prepare <spec_id> --yes
PULSAR_SPEC_FILE=<measured-spec.json> scripts/check-weights.sh <spec_id>
```

Every rank view is verified against the spec manifest. A failure is failed
preparation; measurement has not started.

## 7. Launch (confirmation: launch)

```text
PULSAR_SPEC_FILE=<measured-spec.json> scripts/up.sh <spec_id> --dry-run
PULSAR_SPEC_FILE=<measured-spec.json> ./pulsar start <spec_id>
validate/baseline-v1.sh <spec_id> --spec <measured-spec.json> --check-only
```

`--check-only` must pass before any measurement.

## 8. Baseline-v1

```text
validate/baseline-v1.sh <spec_id> --spec <measured-spec.json> \
  --out results/baseline-v1/<spec_id> --dataset <exact-dataset-file>
```

Gates in policy order: identity manifest, serving smoke, strict same-boot,
pinned GSM8K subset, 60-minute soak, performance snapshot. The runner stops
at the first failure and keeps the evidence. The proposed status is the
evaluator's; record it.

## 9. Promote (reviewed pull request)

```text
./pulsar stop <spec_id>
scripts/release-spec.sh promote <filled-spec.json> --reviewer <name> --out releases/<spec_id>.json
scripts/release.sh list --markdown   # paste into docs/MODELS.md between the markers
```

One pull request per spec with the release file, the run directory, and
the MODELS.md block; privacy scan and full selftest green. The merge is
the trust event.

## 10. Handoff and cleanup (confirmation: destructive cleanup)

Handoff per [handoff-template.md](handoff-template.md). Cleanup only what
the workflow created, through the library commands, after the owned stop.
