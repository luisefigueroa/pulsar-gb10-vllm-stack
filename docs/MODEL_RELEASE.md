# Model release identity service

This is the maintainer runbook for producing **unreviewed model release
candidates**. It implements the candidate-assembly portion of the identity
design in [MODEL_LIBRARY_DESIGN.md](./MODEL_LIBRARY_DESIGN.md) and the
qualification boundaries in
[ADR 0002](./decisions/0002-subsystem-qualification-boundaries.md). It is
not an operator model-download workflow, is not routed through `./pulsar` or
the wizard, and cannot create a trusted Pulsar validation claim by itself.

**Current-schema warning:** this tool predates the Model Serving Release object
model accepted in
[ADR 0004](./decisions/0004-model-serving-release-validation.md). It assembles
schema-1 validation-bundle and expected-seal candidates, whose IDs include
evidence and issuance metadata. It does not yet create the separate ADR 0004
release descriptor, frozen Validation Contract, run records, or validation
decision, and its bundle ID must not be presented as a Model Serving Release
ID. Existing candidates and issued artifacts remain immutable.

## System boundary

The release identity service sits between lab validation and model-library
enforcement:

| Subsystem | Responsibility |
|---|---|
| Profile registry (`models/*.conf`) | Declares the exact runtime configuration to normalize |
| Validation (`validate/`, `results/`) | Produces model-qualification evidence for exact model/image/profile/geometry inputs |
| Identity schema (`scripts/model_identity.py`) | Owns canonical profile, validation-bundle, and expected-seal schemas and IDs |
| Release candidate (`scripts/model-release.sh`, `scripts/model_release.py`) | Hashes an exact local snapshot and assembles internally consistent, explicitly untrusted candidate documents |
| Library runtime (`scripts/model-library.sh`, `scripts/model_library.py`) | Enforces repository-reviewed seals/bundles during catalog, preparation, and launch |
| Release review | Combines every required subsystem scope before changing `STATUS`, guided exposure, or defaults |

The Bash entrypoint sources the profile and passes its values to Python. Python
owns normalization, digests, candidate schemas, atomic writes, and fail-closed
policy. The service does not acquire model bytes, run validation gates,
qualify a model or storage path, edit a profile, copy documents into `models/seals/` or
`models/validation-bundles/`, or change `STATUS`.

Under ADR 0004, the future supervised operator workflow is the separate
`pulsar-model-onboarding` skill. It may compose acquisition, distribution,
verification, launch, tests, and evidence capture—including explicitly selected
Experimental subsystems—but it will not inherit trusted issuance, validation,
or promotion authority from this maintainer tool.

## Issued profiles

The one-node diagnostic `qwen3-1.7b` profile is the first claim issued through
this workflow. Its reviewed trust roots are:

- expected seal
  `ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94`;
- validation bundle
  `9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380`;
- exact model commit
  `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`; and
- complete manifest
  `775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`.

The reviewed evidence is indexed in
[results/model-library/README.md](../results/model-library/README.md). A fresh
candidate reproduced byte-for-byte before publication, the trusted bundle
verifier returned `match`, and a post-issuance physical `library-hot`
preparation/launch used those exact identities. This narrowly establishes the
one-node diagnostic claim. It does not seal `qwen3-1.7b-2node`, promote the
model-library path, or content-bind live-mount and legacy-unsealed replicated
launches. The sealed replicated path now enforces this issued identity.

The flagship `deepseek-v4-flash` profile is the second issued claim. Its
reviewed trust roots are:

- expected seal
  `1ba9ca8e3c34a9143588cc1315474e9cca0724351f0856caed5bb1116b89555a`;
- validation bundle
  `8fda1d93c5e08cbba18df5b26b0632354c6559ab939d3763dbdbdf38ead6b236`;
- exact model commit
  `7872f01b1d1fe23eabc4c98b48bffcef5a386062`; and
- complete manifest
  `27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`.

Its candidate and an independent reproduction were byte-identical, and the
trusted bundle verifier returned `match`. The issuance binds the reviewed
DeepSeek GA content, digest-pinned image, normalized two-node profile, and
repository evidence. The later post-issuance and one-home physical gates passed
in the catalog/artifact and serving-integration scopes. Neither issuance nor
those gates claim bit-identical output or promote the model library; the
strict-determinism decision and sustained soak remain separate release blockers.

## Trust boundary

Every generated descriptor says:

```text
state=unreviewed
authority=none
privacy=pending
promotion=not-authorized
```

Repository-local output is allowed only below the gitignored
`experiments/release-candidates/` tree. An explicit output outside the
repository is also allowed. Output below `models/`, elsewhere in the tracked
repository, at a broad root, or over an existing directory is refused.

Candidate generation proves that the documents are deterministic and
internally consistent. It does **not** establish catalog acceptance, serving
integration, model qualification, or release promotion. It also does not prove
that the bytes were used in the lab, that the evidence is sufficient or
private-data-free, or that maintainers approved the claim. Those remain review
decisions.

Even a candidate whose behavioral gates pass remains `Testing incomplete`
under ADR 0004 until every required gate and provenance/security review is
complete and a reviewed validation decision exists. The current tooling does
not assign that status in machine-readable form.

## Preconditions

- Work on a feature branch.
- Resolve the Hugging Face model to an exact 40–64 character lowercase commit.
- Retain the complete snapshot used for validation.
- Pin the profile image with `@sha256:<digest>` before `plan`, `assemble`, or
  `verify-candidate`. Mutable image tags cannot form a validation bundle.
- Publish sanitized, repository-relative evidence for the exact model,
  runtime, image, and geometry being claimed.
- Identify every behavior-affecting external tokenizer, draft model, adapter,
  or model-code artifact not already covered by a snapshot manifest.

## Candidate workflow

First inspect the normalized profile contract:

```bash
scripts/model-release.sh plan <profile> --json
```

Hash the exact snapshot. The command does not follow `refs/main`; the commit is
required explicitly.

```bash
scripts/model-release.sh manifest <profile> \
  --hub-path /path/to/hub/models--namespace--repository \
  --revision <exact-commit> \
  --json
```

The default observed-manifest directory is:

```text
experiments/release-candidates/<profile>/<commit>/observed/
```

After the evidence exists, assemble the validation-bundle and expected-seal
candidates. Repeat `--evidence` for every repository-relative artifact. An
optional `--external-artifact <json>` supplies one schema-valid external
artifact descriptor per occurrence.

```bash
scripts/model-release.sh assemble <profile> \
  --manifest experiments/release-candidates/<profile>/<commit>/observed/snapshot-manifest.json \
  --issuer <lab-issuer> \
  --issued-at <RFC3339-UTC> \
  --evidence results/<path-to-evidence>.json \
  --json
```

The default assembled directory is:

```text
experiments/release-candidates/<profile>/<commit>/release-candidate/
```

It contains exactly:

```text
candidate.json
expected-model-seal.json
snapshot-manifest.json
validation-bundle.json
```

Verify the candidate against the currently sourced profile and current
repository evidence:

```bash
scripts/model-release.sh verify-candidate <profile> \
  --candidate-dir experiments/release-candidates/<profile>/<commit>/release-candidate \
  --json
```

Verification checks the candidate content address, exact manifest, seal/bundle
cross-links, normalized live profile, evidence presence, file set, and
unreviewed authority state. Any profile drift or document tampering fails.

## Review and issuance

The repository does not automate trusted issuance or publication. Every
release pull request must receive maintainer review that confirms:

- the manifest came from the exact snapshot used by the recorded lab run;
- the complete candidate is reproducible from the claimed inputs;
- model, external-artifact, image, runtime, memory, and geometry identities
  match the evidence;
- every referenced artifact exists and passed privacy review;
- the bundle filename is its `bundle_id` and the seal is reviewed under the
  profile name;
- adding `EXPECTED_MODEL_SEAL` is justified by exact identity evidence; and
- any `STATUS`, guided-exposure, or default-policy change is justified by every
  applicable subsystem gate in `REVALIDATE.md`.

Only after that review may the pull request deliberately place the reviewed
documents under the trusted model directories and update the profile. The
candidate tool has no command for that action. After the reviewed files and
profile reference exist, run:

```bash
scripts/model-library.sh validation-bundle verify <profile>
scripts/selftest.sh
```

Candidate output is never itself release evidence and never changes the
validation ledger. Preserve failed validation evidence according to
[REVALIDATE.md](./REVALIDATE.md); do not turn locally observed user content into
an expected identity.
