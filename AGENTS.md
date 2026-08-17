# Repository Guidelines

## Project Structure & Module Organization

This repository is an operations and validation stack for serving vLLM on NVIDIA DGX Spark GB10 systems. The preferred operator entry is `./pulsar` (home, wizard, start/stop/status). `serve.sh` and `cluster/*` are the low-level launchers. The control plane confirms an N-node topology; serving evidence currently validates one- and two-node geometries only. Model profiles are shell-style files under `models/`. Python benchmarks and correctness checks live in `validate/`, with measured artifacts in `results/` and hardware probes in `bench/`. Keep operational explanations in `docs/`; deprecated experimental overlays belong in `patches/`.

### Pulsar subsystem map

```mermaid
flowchart LR
  operator["Operator"] --> surfaces["Operator surfaces<br/>pulsar · wizard.sh · scripts/home.sh"]
  surfaces --> lifecycle["Lifecycle control<br/>scripts/up.sh · down.sh · status.sh"]

  profiles["Model policy<br/>models/*.conf · reviewed seals"] --> lifecycle
  topology["Topology and control plane<br/>scripts/lib.sh · detect-fabric.sh · doctor.sh"] --> lifecycle
  artifacts["Launch gates<br/>image · memory · weights · preflight"] --> lifecycle

  lifecycle --> single["Single-node launcher<br/>serve.sh"]
  lifecycle --> cluster["Multi-node launcher<br/>cluster/*"]
  single --> runtime["vLLM containers<br/>OpenAI-compatible API :8000"]
  cluster --> runtime

  library["Model library<br/>two-rank GA · other scopes experimental"] -. explicit opt-in .-> artifacts
  fabric["Experimental live weight fabric<br/>NFSv4.2/RDMA over confirmed rails"] -. explicit opt-in .-> artifacts

  runtime --> validation["Validation and probes<br/>validate/* · bench/*"]
  validation --> evidence["Evidence and guidance<br/>results/* · docs/*"]

  classDef optin stroke-dasharray: 5 5;
  class library,fabric optin;
```

Solid arrows show the promoted control and evidence flow. Dashed arrows are
explicit non-default weight paths. The reviewed two-rank `library-hot` path is
GA; remote one-rank and legacy-unsealed uses remain experimental. Live fabric
also remains experimental. None is a silent fallback or wizard default.
Control SSH, inference NCCL/RoCE, and weight transfer remain distinct data
planes even when they involve the same machines.

## Build, Test, and Development Commands

- `scripts/selftest.sh` runs control-plane tests and Python syntax checks without requiring Docker.
- `scripts/doctor.sh` verifies GPU, Docker, port, cache, and optional worker readiness on GB10 hardware.
- `scripts/list-models.sh --serving` lists every serving-purpose profile and its advisory status. `--legacy-tested` filters the historical `STATUS=tested*` recommendation class; `--validated` is a deprecated alias and does not mean ADR 0004 `Validated`.
- `scripts/up.sh qwen3-1.7b --dry-run` exercises launch checks without starting a server.
- `validate/run-gates.sh <served-name> --tag <label>` runs determinism captures, throughput benchmarks, and optional baseline/needle gates against an already-running server.
- `docker build -t vllm-gb10:v0.26.0 .` builds the optional metadata overlay; see `docs/BUILD.md` before changing image pins.

## Coding Style & Naming Conventions

Write Bash for orchestration (`#!/usr/bin/env bash`, `set -euo pipefail`) and Python 3 for structured logic and validation utilities. Use two-space indentation in shell blocks and four spaces in Python. Quote shell expansions, prefer arrays for command construction, and add narrowly scoped ShellCheck suppressions with a reason. Use `lowercase-hyphenated.sh` for scripts, `snake_case.py` for Python, and descriptive model IDs such as `nemotron-3-nano-30b-nvfp4`. Preserve existing config key conventions (`UPPER_SNAKE_CASE`).

### Hybrid Bash + Python (what goes where)

This repo is intentionally hybrid. Match the existing pattern
(`weight-fabric.sh` + `weight_fabric.py`, `lib.sh` + small Python helpers):
**Bash at the operator boundary, Python for data and algorithms.** Do not
rewrite the control plane into one language, and do not introduce a third
language for new features without an explicit decision.

| Prefer **Bash** | Prefer **Python 3** |
|---|---|
| Operator CLIs and entrypoints (`scripts/*.sh`, `cluster/*.sh`, wizard/home) | JSON schemas, catalogs, digests, budgets, identity keys, merge/label logic |
| `source lib.sh`, `load_conf`, topology load, flag parsing | Multi-step planning, validation, fail-closed policy decisions |
| SSH orchestration, `rsync`/`docker` argv assembly, sudo/interactive flows | Atomic read/write of site-local state files (catalog, stamps, audits) |
| Thin wrappers that call Python and print human-oriented status | Machine-oriented `--json` structures and stable error codes/messages |
| Lifecycle glue (`up` / `down` / preflight hooks) | Unit-testable pure logic and fixture generation under `scripts/testlib/` |

**Boundaries**

- One module should own each schema (usually Python). Bash must not hand-edit
  complex JSON with `sed`/`awk` when a Python helper already exists or belongs.
- Reuse topology and SSH identity rules from shared helpers; do not reimplement
  confirmed-endpoint selection in a one-off Python script.
- Profile confs remain shell-style under `models/`; Bash may `load_conf` and
  pass `MODEL` / `NODES` / `STATUS` into Python as args or a small JSON dump.
  `EXPECTED_MODEL_SEAL` is only a reviewed repository-relative reference under
  `models/seals/`; `scripts/model_identity.py` owns its strict schema and
  identity validation. Bash and operator-local state must never manufacture or
  rewrite reviewed expected seals. `scripts/model-release.sh` may assemble only
  explicitly unreviewed candidates under gitignored
  `experiments/release-candidates/` (or an explicit path outside the repo); it
  must not write trust roots, edit profiles, or change validation status.
  `scripts/model_serving_release.py` separately owns ADR 0004 release and
  contract schema version 1. `scripts/model-serving-release-plan.sh` may source
  a profile and assemble/verify only explicitly unreviewed source-neutral
  release/contract candidates under gitignored
  `experiments/model-onboarding/` (or an explicit path outside the repo). It
  must require a complete manifest plus explicit runtime/hardware and criteria
  inputs and explicit one-to-one bindings for every supplied additional
  behavior artifact, and strip local source paths. It must not acquire bytes,
  write the tracked registry, issue a decision, change status, or claim
  physical behavior. Planner `verify` uses the public schema-owning
  `load_verified_release_plan_candidate(dir)` loader after shared filesystem
  hardening. Planner and capture publish candidate JSON with the shared
  `pretty_json_bytes` encoding from `scripts/model_identity.py` (`indent=2`,
  `sort_keys=True`, `ensure_ascii=False`, trailing newline); identity digests
  remain compact `canonical_json_digest`.
  `scripts/immutable_descriptor_dir.py` owns only generic descriptor-rooted
  immutable-directory primitives and is not a schema owner. A local
  profile-reference mapping may normalize an exact argument
  value to a bound public artifact key, but the mapping itself must never be
  persisted. `scripts/model_validation_evidence.py` owns the
  content-addressed evidence-artifact, immutable run-record, validation-bundle,
  and reviewed-decision schema version 1. Keep both modules pure and
  non-issuing. `scripts/model_serving_release_registry.py` owns read-only
  filesystem loading, content verification, graph assembly, and inspection of
  stored ADR 0004 objects under `models/model-serving-releases/`; its verified
  inspection result is the only source for read-only catalog/operator release
  status projection. Profiles may bind that projection with the optional
  reviewed `MODEL_SERVING_RELEASE_ID` field. The binding is a review assertion:
  any four-part tuple change must update it to the newly derived release ID in
  the same reviewed change. Runtime projection additionally checks the selected
  model-access contract, but does not independently reconstruct the full tuple
  from shell profile fields.
  `scripts/model-serving-release-capture.sh` owns local ADR 0004
  evidence-capture candidate persistence: it composes a verified release-plan
  candidate plus an attempt-only spec, independently validates release and
  contract objects through `scripts/model_serving_release.py`, captures
  immutable run records and content-addressed evidence, assembles compatible
  runs, and independently verifies the unreviewed candidate. It must not write
  the tracked registry, issue a decision, change catalog or profile status,
  launch a release, persist a planner path or planner candidate ID, or issue
  `Untested`.
  `scripts/model-serving-release-issue.sh` owns maintainer-only ADR 0004
  issuance staging: it turns one independently verified unreviewed
  evidence-capture candidate plus an explicit review declaration into a
  staged proposal of the exact content-addressed registry objects and any
  privacy-cleared publishable evidence. The existing pure schema modules
  derive the decision status. A successful local command does not establish
  trust; repository review and merge are the trust event. It must not mutate
  the capture candidate, edit a profile, authorize serving, or claim physical
  behavior. Status remains advisory.
  `validate/validator_measurement.py` owns the closed, versioned, status-neutral
  measurement documents emitted by `validate/compare_captures.py` and
  `validate/bench_serve.py`. `scripts/model_serving_release_attempt.py` owns the
  closed attempt context and invocation-plan schemas plus mapping of those
  measurements into existing attempt-only specs. It may consume only the
  supported publishable `results/` measurement files in this slice, must
  validate both generated specs through the capture contract, and may publish
  only one exclusive unreviewed two-file directory under
  `experiments/model-serving-release-attempts/` (or an explicit safe path
  outside the repository). It must not invent missing validator output, persist
  a publishable evidence digest in the attempt spec, issue a decision, change
  status, authorize serving, or claim physical behavior. Later capture must
  independently re-read the evidence and derive its digest; regenerate the
  attempt specs if that file changes between commands.
  `skills/pulsar-model-onboarding/` is the ADR 0004 stage-4 supervised
  onboarding skill. It composes those existing CLIs for a brand-new unsealed
  model and collaborates at material decisions. It never issues a seal or
  validation decision, assigns status, binds a profile to a release, writes
  the trusted registry, promotes a path, or claims physical behavior.
  `skills/pulsar-model-onboarding/scripts/onboarding_journal.py` owns only the
  skill-local append-only workflow journal (orchestration recovery state).
  It is not a sixth ADR object, not evidence, and not a status authority.
  Default local journal state belongs under gitignored
  `experiments/model-onboarding/workflows/`, separate from release-plan
  candidate directories. For an absent brand-new unsealed repository, the
  skill may compose the source-attested `home add --revision` service: first a
  read-only plan, then a separately confirmed exact-commit acquisition. The
  service resolves and records the complete public Hugging Face Git/LFS
  inventory on the selected rank, uses that rank's local authentication,
  confines downloads and transient caches to private same-filesystem staging,
  verifies the upstream set and every SHA-256, rechecks all-rank absence,
  writes an immutable site-local receipt, and publishes with an atomic
  no-replace rename. It does not refresh the catalog, prepare a runtime view,
  launch, or create reviewed authority. A home created this way may later be
  resumed or reused only after `home verify` completes an offline full rehash
  against that receipt. An unknown or pre-existing home still requires full
  verification against a reviewed expected manifest independent of the
  observed tree; the shallow catalog label and a self-observed manifest are
  not that proof. The skill must never download directly into durable storage.
  Deterministic skill and journal tests make no physical DGX claim and create
  no release decision.
  `scripts/model_library_source_attested.py` owns the closed version-1
  Hugging Face source, identity, public plan, privacy-safe approval, immutable
  receipt, result, and home-verification schemas for that path. The thin Bash
  boundary selects the target and orchestrates its local `hf` CLI;
  `scripts/hf_source_inventory.py` uses the target's Hugging Face Python
  environment to resolve public source metadata without accepting a token.
  Source-attested acquisition creates observed/source identity and
  catalog-artifact evidence only. It does not issue reviewed identity, a seal,
  status, serving permission, a Model Serving Release decision, or physical
  evidence. Receipt-backed prepare requires the exact model ID, exact commit,
  and receipt manifest together.
- New multi-node library/fabric-style features: thin `scripts/<name>.sh` CLI +
  `scripts/<name>.py` (or a small package) for the brain—same shape as weight
  fabric.
- Selftests follow the same split: Bash scenarios invoke CLIs; Python owns
  fixture builders and parameterized mocks (see Testing Guidelines).

**Avoid**

- Pure Bash for large indexes, digests, budgets, or all-or-nothing multi-rank
  barriers (hard to test; tends to rot).
- A parallel Python-only control plane that bypasses `lib.sh` topology/lifecycle
  without a strong reason.
- Go/Rust/other binaries for ops glue unless the project explicitly adopts a
  new toolchain for all Sparks.

## Command-Line Experience

Treat human-readable command-line output as a primary product requirement. Optimize interactive and human-facing output for fast scanning with clear information hierarchy, semantic line breaks, hanging indentation, consistent labels, and readable behavior at narrow terminal widths. Avoid dense key/value streams, uncontrolled wrapping, and meaning conveyed by color alone. Keep machine-readable output, such as JSON, separate and stable. For every CLI-facing change, review the rendered human output explicitly and test representative narrow terminal widths.

### Plain technical language

Use the project's canonical terms and status names exactly as defined, while
explaining them in straightforward language.

- Lead with what happens, what it affects, and the condition that causes it.
- On first use of a specialized term, immediately explain it in ordinary words.
- Preserve defined terms such as `Model Serving Release`, `Validated`,
  `library-hot`, and `rank`; do not replace them with approximate synonyms.
- Prefer concrete wording such as "preparation fails if the model identity
  cannot be verified" over shorthand such as "fails closed."
- When `fail closed` is the relevant policy term, retain it where policy
  precision matters and explain the behavior: the operation fails if the
  required condition cannot be verified, and it does not use a fallback.
- Explain scope words such as "bounded," "reviewed," and "two-rank" when their
  practical limits may not be obvious.
- Avoid dense noun phrases, unexplained abbreviations, and implementation terms
  when a direct description communicates the same behavior.
- Never simplify wording in a way that changes an agreed definition,
  validation requirement, authority boundary, or status meaning.

## Testing Guidelines

Run `scripts/selftest.sh` for every script or config change. Changes affecting serving behavior must also follow `docs/REVALIDATE.md`; record reproducible outputs under `results/` and update `docs/VALIDATION.md`. There is no percentage coverage target: promotion depends on correctness, determinism, benchmark, long-context, and soak evidence appropriate to the change.

### Selftest structure (avoid spaghetti mocks)

Control-plane selftests must stay maintainable. Do not grow monolithic
`scripts/selftest-*.sh` files by copy-pasting mock state machines or embedding
fixture site maps inside generic doubles.

**Layers**

1. **Scenario** (`scripts/selftest-<area>.sh`) — arrange → act → assert only;
   keep thin.
2. **Fixture** — topology, ranks, **roles** (owner/client/control), paths, and
   golden inputs. Fixtures name *who* is owner/client; they do not bury that in
   mock implementation.
3. **Mock / test double** — parameterized helpers under `scripts/testlib/` (or
   `scripts/testdata/` for static trees). Mocks accept kind, identity, and
   optional **role-driven** policy—not hard-coded hostnames.

**Rules**

- Prefer one shared mock helper over near-duplicate PATH/SSH/`cat` shims. If you
  need the same state machine a second time, extract it before extending.
- **Never** encode product roles as hostname branches in mocks (e.g.
  `if host = orion-client` ⇒ large TX). Pass `role=fabric-owner|fabric-client|…`
  (or an explicit delta map) from the fixture. Flipping owner rank must not
  require rewriting the mock.
- Prefer **Python** (or a small shared library) for fixture generation, digests,
  budgets, and multi-step mock logic. Use Bash for invoking CLIs under test,
  temp dirs, and exit-code checks. Stop growing selftests with large
  `python3 <<'PY' … write bash …` blobs that reinvent fixtures inline.
- Keep new feature areas in **their own** selftest module (e.g. model-library
  tests must not bolt onto `selftest-weight-fabric.sh`).
- Soft budget: if a `selftest-*.sh` approaches ~500 lines or a change adds a
  large paste of mock logic, split scenarios or extract `scripts/testlib/`
  helpers in the same PR (or a prerequisite cleanup PR).
- Production code may keep calling `cat`/SSH; the mock must not re-encode the
  site map. Assert product contracts (thresholds, digests, fail-closed paths),
  not the mock’s internal host table.

**Selftest PR checklist**

- [ ] New scenario reuses an existing helper, or adds one parameterized helper
- [ ] No new hostname→behavior branches in mocks
- [ ] Fixture documents owner/client (or equivalent) ranks/roles
- [ ] No unjustified multi-hundred-line growth of a single selftest file
- [ ] When fixing fabric/counter tests, prefer extracting
  `scripts/testlib/` mocks over another copy-paste path

When extending weight-fabric traffic proofs, use (or introduce) a single
parameterized counter mock (kind=`ib|netdev`, identity, role-driven deltas)
rather than additional parallel SSH/local state machines.

## Reliability, Safety, and Evidence

These rules exist because silent fallbacks, wrong networks, and unowned cleanup have caused real multi-node failures. Prefer failing loudly over “making it work.”

### Independent Grok review and approved implementation

When a user requests a Grok review or delegated implementation, use
`skills/grok-subagent/SKILL.md`. The first pass stays read-only against a
sanitized review tree. Reconcile its findings against repository authority and
obtain explicit agreement before editing. After approval, Grok may implement
the agreed unit directly in a clean dedicated feature worktree that passes the
skill's privacy preflight, and may run in-scope local tests or bounded
subagents. The primary agent reviews the resulting diff and authoritative test
results without needlessly reimplementing the change, and retains publication
responsibility. Grok must not receive secrets or site-local state, expand
policy or scope without approval, or operate external/privileged infrastructure
unless that authority is explicitly part of the approved plan.

### Fail closed; no silent policy changes

- Partial weights, wrong transport, digest mismatch, stale topology, or incomplete preparation/start must **not** report healthy serving.
- Do **not** silently fall back (e.g. fabric → full N-replica pull, RoCE NFS → TCP/control-LAN NFS, confirmed rail → “any route”). If an alternate path exists, it is an **explicit** operator choice and must be visible in CLI, labels, and docs.
- Do **not** invent serving geometries (TP/PP/node counts) from “we discovered N machines.” Exact profile contracts and operational gates decide what can run; extra confirmed nodes stay idle capacity.
- The wizard exposes all serving profiles that fit confirmed capacity, with status and material caveats visible. Recommendation and default ordering prefer evidence-backed behavior. Experimental subsystems remain explicit choices and are never silent fallbacks.

### Topology, SSH, and data planes

- Treat confirmed topology (`.cluster-topology.json`) as membership truth—not mDNS names alone.
- Management SSH must use the **confirmed control endpoint** (saved alias for identity/host keys is fine; transport host must not wander onto a RoCE data rail). Reuse shared resolvers; do not reimplement per script.
- Keep planes distinct in code and docs: **control** (SSH, rendezvous), **inference** (NCCL/RoCE), **weight transfer** (library preparation / experimental fabric). Do not overload one path without saying so.
- Site-local state (`.cluster-topology.json`, `.weight-fabric/`, `.model-library/`, hot roots) is gitignored; never commit hostnames, IPs, or node IDs into publishable docs/results without redaction/audit patterns already used for fabric artifacts.
- In static hardware and measurement documentation, identify physical systems as
  `Node A`, `Node B`, and so on. Use generic rank labels such as `rank 0` and
  `rank 1` only when the runtime role itself matters; ranks are not durable
  hardware identities. Safety guidance may describe prohibited site identity
  generically, but must not quote stable site hostnames or durable topology
  identity.

### Lifecycle ownership

- Launchers own cleanup for containers **they** create (including signal traps on interrupt). Prefer immutable launch IDs and ownership-safe stop (`down.sh` / stack labels)—never broad `docker rm` of unrelated workloads.
- Destructive ops (purge hot, purge replicas, teardown exports, overwrite configs) are confirmation-gated; refuse when a managed service is still using the resource.
- Privileged changes require usable sudo policy; support attended `--interactive-sudo` where the project already does. **Never** read, log, transport, or store the operator password, and do not weaken sudoers to automate.
- All-or-nothing multi-rank steps (prepare, cluster start): on failure, roll back partial ranks or leave an explicit incomplete state that launch refuses—not a half-ready service.

### Claims, status, and artifacts

- Priority order for product decisions: **stability > accuracy > throughput > latency.**
- A **Model Serving Release** is the immutable combination of exact model
  identity, serving recipe, runtime/image identity, and supported hardware
  geometry defined by
  [ADR 0004](docs/decisions/0004-model-serving-release-validation.md). Any
  change to one of those four parts creates a new release; validation does not
  transfer across release IDs.
- Its primary artifact identity is source-neutral: use `huggingface-snapshot`
  for an exact Hugging Face commit and full manifest, or
  `content-addressed-model` for another complete model tree with a public
  logical ID, public revision, and full manifest. Never persist a local source
  path. A generic `digest-artifact` cannot be the primary model.
- The target decision statuses are `Untested`, `Testing incomplete`,
  `Tested—criteria not met`, `Tested—inconclusive`, `Validated`, and
  `Superseded`. `Validated` requires every frozen release-specific criterion
  across stability, accuracy, throughput, and latency, plus reviewed
  provenance/security and strict same-boot reproducibility. FP-equivalent
  output does not satisfy the strict gate.
- Validation status is advisory and never grants or denies serving. Catalog and
  operator surfaces must not hide or block a release solely because of status,
  including legacy `do-not-use`/`blocked` labels or no reviewed decision.
  Recommendation/default policy may prefer stronger evidence. Operational
  admission still fails closed for concrete identity, integrity, recipe,
  runtime/geometry, capacity, topology, security, lifecycle, or ownership
  failures.
- Criterion scopes are fixed: stability, accuracy, throughput, latency, and
  strict same-boot are `model-qualification`; serving integration is
  `serving-integration`; provenance/security and physical geometry are
  `release-promotion`. `catalog-artifact` is preparation/subsystem evidence and
  cannot satisfy a validation criterion.
- `scripts/model_serving_release.py` owns the pure ADR 0004 release-descriptor
  and frozen Validation Contract schemas. `scripts/model_validation_evidence.py`
  owns pure immutable run-record, evidence-bundle, and validation-decision
  schemas. It binds exact cross-links, considers every applicable observation
  automatically, requires explicit evidence-backed exclusions, derives
  criterion outcomes from frozen thresholds plus required context, soak, and
  predecessor-relative budgets, rejects a supplied status assertion that
  disagrees with the evidence, and projects supersession without rewriting the
  earlier decision. Conflicts adjudicate as follows: pass+fail is
  inconclusive, pass+inconclusive is inconclusive, fail+inconclusive is fail,
  and all-pass is pass. A completed nested context or soak failure remains a
  conclusive failure even when its enclosing criterion observation is marked
  inconclusive.
  Every run attempt hash-binds a sorted `attempted_criterion_ids` declaration.
  A post-barrier non-preparation attempt must name at least one scope-compatible
  frozen criterion, and its observations must cover that set exactly;
  incomplete attempts may contribute only inconclusive observations. The
  review-derived provenance/security criterion uses one canonical closed
  template so unimplemented thresholds or parameters cannot be added silently.
  Relative performance binds the reviewed predecessor contract, bundle,
  decision, and run; the relevant predecessor criterion must pass, but the
  predecessor release need not be globally `Validated`. Runtime compatibility
  and architecture/geometry checks are structural only; physical behavior
  still requires physical evidence. Canonical compatibility ranges compare
  the numeric core of exact observed deployed versions, preserving accepted
  zero-padded components and vendor suffixes in run evidence. Supersession must
  be later in time and acyclic. Release/contract free-form values are screened
  recursively for recognized credentials and deployment-only data while
  ordinary dotted public identifiers remain valid; credential-bearing
  extensible field names are rejected. Command evidence uses allowlisted
  repository programs, SHA-256-shaped program-version identities, closed
  operations/resources, typed criterion references, and typed site-option
  references whose rank values are bounded by the release geometry; it must
  still pass trusted publication privacy review. Pure
  schema validation does not prove that a supplied digest names the checked-out
  executable or that no unknown private identifier escaped structural checks.
  These builders do not capture evidence, issue a trusted decision, or launch
  a release. Local source-neutral release-plan candidates can build and verify
  unreviewed release/contract objects without status or issuance authority.
  Local ADR 0004 evidence-capture candidate persistence
  composes a verified release-plan candidate with an attempt-only spec and
  can plan, capture, assemble, and verify unreviewed candidates without
  writing the tracked registry, issuing `Untested`, or launching a release. Read-only trusted
  persistence can verify exact reviewed objects under
  `models/model-serving-releases/`. Catalog, wizard, and `scripts/up.sh`
  consume that inspection as an advisory projection for profiles explicitly
  bound by `MODEL_SERVING_RELEASE_ID`; projection never changes recommendation
  order or serving permission. Absence of a profile binding or reviewed
  decision is neutral and is not inferred as `Untested`; multiple contract
  lineages or unsuperseded heads stay ambiguous. Current `STATUS=tested*`,
  `--validated`, expected seals, and schema-1 bundles remain separate legacy
  implementation contracts. Do not automatically relabel an existing profile
  or bundle `Validated`. The corrected ADR 0004 objects remain schema
  version 1 because none was issued or persisted before the correction;
  existing legacy schema-1 seals/bundles and raw evidence remain untouched.
  The tracked ADR 0004 registry currently stores no issued object.
- `STATUS`, ADR 0004 decisions, recommendations/defaults, and
  `docs/VALIDATION.md` claims change only with reproducible evidence. The wizard
  still shows other fitting profiles with accurate labels and caveats. Preserve
  failed and partial runs; do not rewrite failures as passes.
- Selftests prove control-plane contracts; they do **not** replace physical gates for serving or storage claims (`docs/REVALIDATE.md`).
- Public `results/` bundles must stay free of secrets and private site values; use existing privacy-audit patterns when adding artifact publishers.
- Document dependency honesty: if a mode needs owner/home/library after start, inventory and docs say so; if independence is claimed, tests must cover home-down restart.

### Subsystem qualification boundaries

For model distribution and serving work, classify evidence before changing claims:

- **Catalog/artifact service:** exact bytes and identity, placement, transfer, runtime views, retention, repair, and cleanup.
- **Serving integration:** the selected image mounts and loads the intended exact source, then passes health, warmup, and completion smoke.
- **Model qualification:** stability, accuracy, throughput, latency, strict
  same-boot, long context, and soak for the exact model/image/configuration/geometry.
- **Release/promotion:** provenance/security, physical geometry, and every
  required subsystem result combined for a supported profile, wizard path, or
  default policy.

A failure in one subsystem does not erase valid evidence from another unless a
causal connection is demonstrated. It does block any combined claim that
requires both. Health or completion smoke is integration evidence, never model
qualification. An image/runtime change creates a new Model Serving Release and
invalidates applicable integration/model evidence; in the current schema it
also requires a new validation bundle. It does not automatically invalidate
unchanged catalog mechanics. Preserve failed evidence and state its scope.
Agents may propose a better boundary or causal model when new evidence warrants
it, but must not change the accepted policy, promotion requirements, or
interpretation as settled without explicit approval and an updated ADR. See
[ADR 0002](docs/decisions/0002-subsystem-qualification-boundaries.md),
[ADR 0004](docs/decisions/0004-model-serving-release-validation.md), and
`docs/REVALIDATE.md`.

### Model-library authority and invariants

For catalog, download, prepare, launch, pin, purge, or model-validation work,
read `docs/MODEL_LIBRARY_DESIGN.md` and the applicable record under
`docs/decisions/` before changing behavior. Authority descends from this file,
to the accepted design and decision records, to current implementation specs,
operator runbooks, and finally validation ledgers/evidence. Current code or a
new result does not silently override an accepted architectural decision.
Use `skills/change-pulsar-model-library/SKILL.md` as the repeatable workflow for
this work; the skill is procedural and does not outrank these sources.

- Use **prepare model for serving** and **model preparation** in operator-facing
  language. Preparation resolves, distributes, and verifies model files but does
  not start a container or establish model qualification. `activate` is a
  backward-compatible command and internal-schema term, not the product label.

- In the **current repository data**, each issued reviewed identity is attached
  to a legacy `STATUS=tested` profile and binds
  a schema-1 validation bundle, not a model repository ID alone. Only issued
  seals (`qwen3-1.7b`, `deepseek-v4-flash` today) have that bundle. Other
  `tested` serving profiles remain `legacy-unsealed`. Expected identity comes
  from lab validation; locally observed content can match that identity but
  cannot create or replace it. Under ADR 0004, the implemented separate release
  descriptor owns the release ID, the implemented Validation Contract freezes
  its criteria, and the implemented evidence layer validates immutable run,
  bundle, and decision objects. Read-only trusted persistence can verify
  those objects under `models/model-serving-releases/`; that store is
  currently empty. Caller-supplied predecessor and decision registries remain
  validation input, not trusted persistence. Local evidence-capture candidate
  persistence and source-neutral release-plan candidate persistence are
  implemented and remain unreviewed. Closed compare/benchmark measurements and
  candidate-only attempt composition are implemented for strict same-boot and
  absolute throughput/latency; they do not issue status or prove physical
  behavior. Read-only
  catalog/operator projection is implemented for an explicitly bound release;
  maintainer-only issuance staging can propose reviewed registry objects, but
  a local command is not the trust event and this repository still stores no
  issued object. The supervised
  `pulsar-model-onboarding` skill is implemented as control-plane
  orchestration around those CLIs; it does not issue a decision, assign
  status, or bind a profile. It can plan and, after a separate confirmation,
  acquire one absent brand-new unsealed exact Hugging Face revision through
  the source-attested service. Complete source and byte verification followed
  by an immutable receipt creates observed/source identity and catalog-artifact
  evidence only; it does not create a seal, status, serving permission, or
  Model Serving Release decision. Reuse requires receipt-backed offline full
  verification. Unknown and pre-existing homes still require a reviewed
  expected manifest independent of the observed tree. Prepare-time resolution
  for receipt-backed content requires the exact model ID and commit. These
  deterministic controls make no physical Hub, DGX, serving-integration, or
  model-qualification claim. No current profile is
  bound and the tracked store is empty, so current projections are neutral.
  Expected-seal identity and validation status are independent contracts: a
  future non-tested profile may carry a reviewed seal, and a matching seal does
  not promote its release status.
- A deterministic release candidate has no authority by itself. Trusted
  issuance remains a reviewed change that binds lab evidence; candidate tools
  must fail if output claims review/promotion or targets trusted directories.
- The default library policy is one durable home per exact model revision.
  The home rank uses that durable tree through a validated symlink or equivalent
  rank-local view; **do not materialize a second hot copy on the home rank**.
- Reviewed upstream acquisition creates exactly one durable home: observe every
  confirmed rank, download the immutable commit on the selected target into
  same-filesystem private staging, recheck absence elsewhere, full-verify the
  expected seal, then publish atomically. Accepted policy also allows a
  source-attested exact upstream tree to follow that same staging, complete
  inventory/set check, complete SHA-256, all-rank absence recheck, immutable
  receipt, and atomic no-replace publication sequence. Source-attested adoption
  is observed/source identity only. Public `home add <unsealed-profile>
  --revision <selector> --plan` is read-only; execution requires `--yes` and
  repeats source and topology checks before downloading the exact commit on
  the selected rank. `home verify <model_id@commit>` performs receipt-backed
  offline full verification. The home must be one of the current profile's
  serving ranks so active storage remains one home plus N−1 hot copies. Do not
  silently choose another node,
  create a controller copy, refresh the catalog, prepare hot views, or launch.
  Onboarding must explicitly refresh the catalog and verify or prepare the exact
  `model_id@commit`; it must not rely on mutable `refs/main` or profile-only
  resolution.
- Only non-home ranks receive temporary or pinned sealed-hot copies. Symlinks
  and bind mounts are runtime views, not extra ownership or resilience.
- Full content verification happens at trust boundaries. A serve-time metadata
  witness may accelerate an unchanged launch only after full verification;
  drift causes visible full verification against the expected seal or fails
  closed. Never auto-reseal drift as validated content.
- Warm-home pinning retains non-home hot copies but still requires the durable
  home. Home-loss resilience and extra durable replicas are separate, explicit
  policies on distinct failure domains.
- For an explicitly chosen reviewed multi-rank model preparation, use
  topology-bound `ssh-roce` copy with eight streams and no automatic fallback,
  as recorded in ADR 0003. The reviewed two-rank `library-hot` path is GA but
  remains explicit and non-default. Remote one-rank and legacy-unsealed uses
  remain experimental. This transport policy does not create a missing durable
  home or change the replicated guided default.
- Distribution transport is run provenance, not Model Serving Release
  identity. Experimental distribution subsystems are allowed when explicitly
  selected, but qualification starts only after exact content and the intended
  runtime-access contract verify on every serving rank. A failure before that
  barrier is failed preparation and leaves the release `Untested`.
- Preserve historical evidence and mark it superseded rather than rewriting it.
  A contract change must update the canonical design, implementation spec,
  operations, validation ledger, and evidence index together.

### Operational hygiene

- Prefer atomic writes for site-local JSON/state (write temp + rename).
- Idempotent setup where practical; incomplete rollback must be explicit and recoverable (`teardown` / purge / prepare again).
- Human CLI output remains a product surface (see Command-Line Experience); keep `--json` stable for automation.
- Scope PRs tightly: one concern per change; do not mix experimental fabric promotion with unrelated refactors.

## Commit & Pull Request Guidelines

**Branch and merge policy:** Do all work on a feature or fix branch. Do **not**
commit or push directly to `main`. Land changes only by opening a pull request
and merging through review—never force-push or fast-path commits onto `main`
outside that process.

**Automatic publication after completed work:** Completion of a planned unit of
work is standing authorization to publish that unit. Once its relevant checks
pass, agents must not wait for a separate commit, push, or pull-request request.
They must:

1. Stage every change that belongs to the completed unit, including its tests,
   documentation, and sanitized evidence.
2. Commit the complete unit with a focused message.
3. Push the current feature or fix branch to `origin`.
4. Open a ready-for-review pull request against the default branch.

`Commit all` means all completed **in-scope** work. It never includes unrelated
user changes, secrets, site-local state, model data, or unsanitized raw evidence.
If a mixed worktree cannot be separated safely, stop and ask. Do not publish
known-incomplete or failing work as ready, and do not merge, force-push, or
bypass review without explicit authorization.

History favors concise imperative subjects, usually Conventional Commit style: `fix(memory): ...`, `feat(serve): ...`, or `docs(patches): ...`. Keep commits focused. Pull requests should explain affected models and hardware paths, link relevant issues, list commands run, and include result artifact paths. Highlight image/config changes and any behavior not validated on physical GB10 hardware. Call out fail-closed behavior, new fallbacks (there should be none silent), and whether hardware validation was run or is still required.

## Security & Configuration

Never commit `.env`, API/Hugging Face tokens, SSH keys, or model weights. The API binds to `0.0.0.0:8000`; keep it on a trusted lab network or configure `VLLM_API_KEY` and an authenticating proxy. Do not embed credentials in scripts, logs, or artifacts. Report vulnerabilities privately as described in `SECURITY.md`.
