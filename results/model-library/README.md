# Model-library evidence index

This directory contains sanitized or pending-publication evidence for the
experimental federated model-library path. Evidence records measured history;
it does not override accepted architecture in
[MODEL_LIBRARY_DESIGN.md](../../docs/MODEL_LIBRARY_DESIGN.md) or
[ADR 0001](../../docs/decisions/0001-model-library-home-view-and-validation-identity.md).
Qualification scope and evidence reuse are governed by
[ADR 0002](../../docs/decisions/0002-subsystem-qualification-boundaries.md).
[ADR 0003](../../docs/decisions/0003-explicit-model-preparation-transport.md)
records the current no-fallback eight-stream SSH-over-RoCE policy for an
explicitly selected reviewed-profile experimental preparation.
[ADR 0004](../../docs/decisions/0004-model-serving-release-validation.md)
defines the immutable Model Serving Release subject, frozen validation
contracts, decision statuses, and the separation between distribution
provenance and release identity.

Every artifact has a qualification scope: catalog/artifact, serving integration,
model qualification, or combined release/promotion. A result remains valid
within its measured inputs and contract unless a causal dependency invalidates
it. Catalog health, preparation, witness, and lifecycle evidence cannot be
promoted into accuracy or determinism claims; a model-runtime failure does not
erase unchanged catalog evidence, but it blocks a combined release claim that
requires both.

The ADR 0004 criterion mapping is stricter than the four evidence buckets:
stability, accuracy, throughput, latency, and strict same-boot are
`model-qualification`; serving integration is `serving-integration`; and
provenance/security plus physical geometry are `release-promotion`.
`catalog-artifact` is valid evidence for acquisition, preparation, identity,
transfer, and lifecycle only. It cannot satisfy a validation criterion.

ADR 0004 is accepted policy, not retroactive artifact relabeling. Existing
schema-1 bundles, seals, PASS/FAIL rows, and `STATUS=tested*` claims keep their
recorded implementation meaning. None is automatically `Validated`. The pure
release descriptor identifies the exact model + serving-recipe + runtime/image
plus supported-geometry tuple separately from its frozen contract. Pure immutable
run-record, new evidence-bundle, reviewed-decision, status-derivation, and
supersession schemas are also implemented. Read-only status projection now
consumes a content-verified registry decision only for an explicitly bound
profile, while preserving neutral no-binding/no-decision states, ambiguity,
recipe mismatches, and legacy recommendation labels. No release, contract,
run, bundle, or decision instance is issued by this results tree; trusted
decision publication remains pending.

The corrected objects stay at schema version 1 because no ADR 0004 object was
issued or persisted before the correction. Existing legacy schema-1
seals/bundles and every raw or historical artifact indexed here remain
unchanged.

The schema now permits a complete non-Hugging-Face primary tree as a
`content-addressed-model`, and the maintainer planner can persist an unreviewed
release/contract candidate beneath a gitignored boundary. Its deterministic
tests are control-plane evidence only; no candidate is indexed here and no
physical or validation-status claim follows from them.

Deterministic tests under `scripts/testlib/` cover fixed release/contract IDs,
all four identity mutations, strict same-boot and reviewed-provenance
requirements, comparable-predecessor protocol/geometry binding, privacy-field
rejection, and unchanged legacy schema-1 validation. This is control-plane
schema evidence only. It does not demonstrate model behavior, storage behavior,
or physical qualification on a supported geometry, so no new result artifact
or PASS row is added here.

Deterministic projection tests cover unique reviewed status, current unbound
profiles, unavailable registry data, runtime-access recipe mismatch, advisory
launch behavior, legacy-status separation, and narrow terminal rendering. This
is control-plane evidence only; it adds no physical result artifact.

The stage-2 adversarial suite additionally freezes representative run, bundle,
and decision IDs and covers exact release/contract/run/artifact cross-links,
threshold and sample-size derivation, required context and soak conditions,
comparable-predecessor throughput/latency budgets, protocol tamper, failed
preparation before the qualification barrier, explicit Experimental subsystem
provenance, privacy state, observed image/geometry drift, strict same-boot
selection, reviewer authority shape, every base outcome, and immutable backward
supersession. The fixture's synthetic `Validated` decision demonstrates schema
consistency only; it is not lab evidence, proof of review, an issued status, or
a physical claim.

The corrected suite also covers automatic inclusion of every applicable
observation, evidence-backed exclusions, deterministic pass/fail/inconclusive
adjudication, reviewed predecessor contract/bundle/decision/run lineage with a
pass on the relevant predecessor criterion, structural runtime and
architecture/geometry compatibility, exact attempted-criterion accounting,
closed typed command descriptors, recursive release/contract value screening,
and chronologically later acyclic supersession. None of these control-plane tests
substitutes for physical DGX evidence.

ADR 0003 selects a transfer policy within the accepted catalog/artifact scope;
it does not reinterpret the failed DeepSeek determinism artifact, establish a
missing durable home, promote `library-hot`, or change the replicated guided
default. Historical transport comparisons retain their recorded outcomes.

The interactive **Models & storage** catalog-refresh wiring added on 2026-08-12
reuses the existing atomic all-rank refresh contract and adds no physical
measurement artifact. Deterministic renderer and shell scenarios prove that
browsing never refreshes automatically, confirmation is required, failures do
not claim success, and a successful refresh is followed by a new sanitized
health report. The physical health/repair artifact indexed below remains the
applicable underlying rank-observation evidence; this UI integration is not a
storage-path promotion.

The subsequent experimental-preparation wiring reuses the exact physically
measured eight-stream SSH-over-RoCE preparation service. Deterministic contracts
prove reviewed-profile eligibility, exact revision/manifest disclosure,
default-no confirmation, fixed no-fallback argv (eight-stream SSH-over-RoCE for
non-home copies; one-stream `ssh-control` for a home-only view), no unvalidated
bypass, fresh health after either outcome, and no launch/promotion claim. It
changes no identity, admission, transfer, rollback, witness, or lifecycle algorithm, so it
adds no separate physical artifact. The flagship one-home lifecycle artifact
below remains the applicable physical preparation evidence.

The serving wizard subsequently gained a separate experimental storage-source
choice. Deterministic scenarios prove that replicated remains first/recommended,
blocked catalog health cannot fall through silently, optional preparation uses
the fixed eight-stream SSH-over-RoCE policy, fresh exact readiness is required,
and launch remains separately confirmed. One-node catalog serving is bound to
the durable-home rank; ordinary stop purges unpinned views while explicit pin
retains them without adding home-loss resilience. The production wizard then
passed a physical two-node DeepSeek serving-integration gate from a clean
one-home/no-hot state: explicit experimental selection, separately confirmed
preparation and launch, fresh exact readiness, eight-stream SSH-over-RoCE,
read-only exact-snapshot serving, eight warmup phases, completion smoke, and
the interactive ordinary-stop purge all passed. The run returned to one
durable home with no hot instances. The new remote one-node wizard path still
needs its own physical serving-integration repeat before that placement can
make a physical claim, and none of this changes the failed determinism or
promotion status.

Wizard replacement is now a short-lived fail-closed transaction rather than a
restart reconstructed from current defaults. Deterministic contracts cover
capture of the exact live launch contract, placement, storage source,
speculative-decode state, catalog identity/runtime views, temporary retention,
exact failed-launch rollback, and interrupted recovery. A physical two-node
`library-hot` failed-replacement/rollback and interruption repeat remains
pending; the earlier successful-launch artifact is not relabeled as that
evidence and no storage-path or model promotion claim changes.

The 2026-08-10 promotion assessment remains immutable historical evidence. Its
recommendation to materialize the home rank is **superseded by ADR 0001**. Its
measurements and other failed/pending gates remain valid and are not rewritten.
Catalog schema 2/hot schema 3 expected-seal enforcement, content-addressed
validation-bundle schema 1, and rank-local witness schema 1 landed afterward.
No historical artifact was relabeled as lab-sealed. Maintainer-only
release-candidate tooling now has deterministic schema, exact-commit, tamper,
drift, and output-boundary selftests; candidate generation alone adds no
physical or issuance evidence. Deterministic tests also cover unchanged
metadata, drift fallback, same-size corruption, symlink retargeting, and
rank-local filesystem identity. The 2026-08-11 Qwen artifact adds physical
symlink, witness,
read-only launch, pin/restart, mismatch, and no-follow purge evidence, but it is
explicitly `legacy-unsealed` and does not claim lab-issued identity. Site-local
witness documents contain absolute paths and filesystem identifiers and are
never publishable evidence.

Later on 2026-08-11, the one-node diagnostic `qwen3-1.7b` profile became the
first reviewed lab-issued identity. Its candidate reproduced byte-for-byte,
the exact snapshot and fresh runtime gates passed privacy review, and the
issued seal/bundle then passed physical `library-hot` preparation, launch,
zero-byte witness verification, smoke, and cleanup. This does not alter the
legacy scope of the earlier two-node artifact, seal `qwen3-1.7b-2node`, or
promote model-library distribution.

The same issued identity subsequently passed the shipped replicated-cache
path: an existing exact snapshot received full manifest verification, an
unchanged second check hashed zero bytes, and launch used the exact snapshot,
identity labels, and a read-only repository view before a completion and clean
stop. Deterministic tests cover exact-revision acquisition and verification
after every copy; the physical gate intentionally did not redownload an
already complete cache. This enforces sealed profiles without retroactively
sealing legacy profiles or changing a model or storage-path promotion.

On 2026-08-13, the distributed library gained a separate reviewed-profile
`home add` acquisition service. Deterministic contracts cover every-rank
observation, automatic and explicit remote one-node placement, exact multi-node
geometry, most-free-space and exact override placement, managed target-CLI
discovery, missing capability and no-fallback behavior, existing/raced home
refusal, exact download
arguments, plan/staging tamper, full-manifest mismatch, owned cleanup, atomic
publication, confirmation, and JSON separation. The subsequent three-node
[physical acquisition gate](./qwen3-1.7b-home-acquisition-gate-20260813.json)
passed guarded last-home removal, interrupted remote cleanup, explicit and
automatic rank-2 end-to-end acquisition as distinct recorded attempts,
reviewed-manifest hashing and atomic publication for each successful attempt,
explicit refresh, and final one-home/no-hot state. This is catalog/artifact
evidence only, not serving integration, model qualification, or promotion.

On 2026-08-12, the flagship `deepseek-v4-flash` GA profile became the second
reviewed identity. Its release candidate reproduced byte-for-byte and the
trusted verifier returned `match` for commit
`7872f01b1d1fe23eabc4c98b48bffcef5a386062` and manifest
`27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`.
The issuance summary distinguishes exact-content continuity from older
behavioral lineage. The issued identity then passed the applicable two-node
physical `library-hot` gate: rank 1 used the durable-home symlink view, rank 0
received an eight-stream sealed-hot copy, both views full-verified the exact
manifest, both pre/post-launch witnesses hashed zero unchanged bytes, and the
exact read-only snapshot served and cleaned up successfully. The lab catalog
contained two pre-existing complete durable caches and needed a temporary
rank-1 primary selection; neither cache was removed. The artifact therefore
does not prove the one-durable-home steady state, persistent primary selection,
bit-identical output, sustained soak, or storage-path promotion.

Persistent exact-revision primary selection was implemented afterward in the
site-local catalog. Deterministic contracts cover atomic set/clear, refresh
preservation, stale-selection refusal without auto-election, selection-free
cleanup refusing to suggest deletion, explicit non-primary commands after
selection, and selected-primary removal blocking. A three-node physical repeat
then used only disposable synthetic HF-layout repositories and passed direct
pre-selection refusal, selection persistence across refresh, selected-primary
refusal, exact non-primary deletion, sibling preservation, and one-home catalog
state. No existing DeepSeek durable cache was removed.

The existing lab duplicate was subsequently reconciled under explicit operator
authorization: rank 1 became the persistent primary and the rank-0 redundant
repository was removed through the guarded home-removal service. Starting from
that real one-home state, a clean DeepSeek repeat passed eight-stream
SSH-over-RoCE preparation, exact full verification, durable-home/sealed-hot
placement, zero-byte witnesses, read-only launch, all eight warmup phases,
completion smoke, owned stop and hot purge, and final healthy one-home
inventory. This closes the physical steady-state condition; it does not close
strict determinism, sustained soak, or guided/default storage-path promotion.

The next exact-GA repeat then failed the current strict same-boot determinism
contract. With profile-default DSpark k=5, only 11/30 texts and 4/30 complete
records were identical between captures. Every current-pair first flip remained
inside the existing FP-equivalent heuristic, but strict identity still failed,
and comparison with the preserved GA DSpark capture reported one hard
disagreement. A forced no-spec diagnostic improved repeatability to 26/30 texts
and 25/30 complete records without reaching strict identity. Both runs used the
same reviewed GA revision, seal, image, geometry, and rank-local storage views;
therefore the current result cannot be dismissed as only a preview/GA profile
name mix-up. The service remained healthy, no fatal runtime signature appeared,
and cleanup restored one durable home with no hot instances. Sustained soak was
not run after this blocking failure.

Under ADR 0004 this failure blocks `Validated` for that exact Model Serving
Release. It does not invalidate the already measured catalog/artifact or
serving-integration behavior and does not block the separately scoped initial
two-rank `library-hot` subsystem GA closure. Remote one-rank placement remains
outside that initial GA scope.

The active-use durable-home removal guard subsequently passed deterministic and
three-node physical checks using only disposable synthetic repositories. Its
artifact closed the then-current lifecycle gate without changing model
identity, profile status, guided defaults, or the Qwen home's retained state.
It remains the physical baseline for the broader lifecycle matrix. The later
selected-primary targeting contract is covered by the 2026-08-12 repeat.

The filesystem-backed admission policy is implemented and deterministic tests
cover default reserve arithmetic, optional hard caps, durable-home zero charge,
sealed-hot manifest charge, replacement accounting, no-follow inventory,
malformed/untracked bytes, exact all-rank merge, narrow output, and hot-lock
contention. The sanitized non-mutating flagship-sized physical artifact passed
on 2026-08-11; raw `budget --json` output remains site-local because it contains
node and path identity.

Read-only catalog health and repair-ID-bound legacy-hot removal are implemented
after the artifacts below. Deterministic Python and role-driven multi-rank
shell contracts cover sanitized schema-1 output, duplicate/primary state,
schema-1/2 recognition without trust promotion, schema-3 witness metadata,
active/pinned/stale/ambiguous/symlink/malformed refusals, atomic retirement,
incomplete-retirement retry, sibling preservation, and embedded-symlink
no-follow deletion. The affected isolated three-node lifecycle repeat passed:
all ranks were inventoried, stopped-container and pin blockers held, local and
remote repairs succeeded, preserved untracked siblings remained visible as
attention, and the exact disposable-home removal subset passed. No result below
was rewritten, and no real legacy hot entry, durable cache, or DeepSeek
duplicate was changed by this work.

| Artifact | Gate / model identity | Status | Privacy review |
|---|---|---|---|
| [`deepseek-v4-flash-serving-wizard-gate-20260813.json`](./deepseek-v4-flash-serving-wizard-gate-20260813.json) | Production interactive serving-wizard and operator-home stop flow for the exact sealed DeepSeek GA profile: explicit experimental choice, fixed preparation, exact readiness, read-only launch, warmup, completion, owned stop, purge, and one-home closeout | Current two-node serving-integration PASS; remote one-node wizard placement, model qualification, and storage-path promotion remain open | Reviewed; site topology, paths, hosts, nodes, interfaces, containers, witnesses, and filesystem identity omitted |
| [`deepseek-v4-flash-library-hot-determinism-20260812.json`](./deepseek-v4-flash-library-hot-determinism-20260812.json) | Exact sealed DeepSeek GA same-boot strict captures with profile-default DSpark plus forced no-spec diagnostic, standard benchmarks, and clean one-home closeout | Current strict determinism FAIL; no-spec improves but does not eliminate variance; `Validated` is blocked for this exact release while subsystem GA remains separate | Reviewed; site topology, paths, hosts, nodes, interfaces, containers, witnesses, and filesystem identity omitted |
| [`deepseek-v4-flash-one-home-gate-20260812.json`](./deepseek-v4-flash-one-home-gate-20260812.json) | Real flagship duplicate reconciliation plus clean two-rank eight-stream SSH-over-RoCE preparation, exact identity/witness/read-only launch, warmup, completion, cleanup, and final one-home inventory | Current physical one-home PASS; release qualification and guided/default promotion remain open; subsystem GA closure is separate | Reviewed; site topology, paths, hosts, nodes, interfaces, containers, witnesses, and filesystem identity omitted |
| [`model-library-health-legacy-repair-gate-20260812.json`](./model-library-health-legacy-repair-gate-20260812.json) | Three-node read-only health, repair-ID-bound schema-1/2 removal, stopped-container/pinned blockers, no-follow/sibling preservation, and exact disposable-home removal; synthetic data only | Current health/legacy-repair physical PASS; no real cleanup, reconciliation, or storage-path promotion | Reviewed; site topology, paths, hosts, nodes, containers, repair IDs, and filesystem identity omitted |
| [`model-library-primary-selection-reconciliation-gate-20260812.json`](./model-library-primary-selection-reconciliation-gate-20260812.json) | Three-node persistent exact-revision selection and guarded non-primary reconciliation; disposable synthetic HF-layout repositories only | Current selected-primary targeting PASS; existing DeepSeek duplicate unchanged; not a promotion | Reviewed; site topology, paths, hosts, nodes, interfaces, containers, and filesystem identity omitted |
| [`deepseek-v4-flash-release-validation-20260812.json`](./deepseek-v4-flash-release-validation-20260812.json) | Reviewed release summary for DeepSeek GA revision `7872f01b1d1fe23eabc4c98b48bffcef5a386062`, manifest `27ab362a4898eadac54d61da14e1073f15b2acf5172de082575f8ee7f1c9ec9e`, exact two-node profile, and digest-pinned PR-41834 image | Current second-identity issuance input PASS; paired physical enforcement artifact now passes; not a storage-path promotion or bit-identical-output claim | Reviewed; repository-relative evidence only; site identity omitted |
| [`deepseek-v4-flash-sealed-enforcement-gate-20260812.json`](./deepseek-v4-flash-sealed-enforcement-gate-20260812.json) | Post-issuance two-node exact-seal preparation, full verification, durable-home/sealed-hot placement, read-only launch, identity labels, zero-byte witnesses, smoke, and cleanup | Current flagship sealed `library-hot` enforcement PASS with duplicate-durable-cache/temporary-primary condition disclosed; not steady-state storage proof or a promotion | Reviewed; site topology, paths, hosts, nodes, interfaces, containers, and filesystem identity omitted |
| [`deepseek-v4-flash-snapshot-manifest-20260812.json`](./deepseek-v4-flash-snapshot-manifest-20260812.json) | Complete `sha256-snapshot-manifest-v1` for the exact issued DeepSeek revision; 74 files, 166,898,661,074 bytes | Current expected-seal content input PASS | Reviewed; logical snapshot paths and content hashes only |
| [`qwen3-1.7b-release-validation-20260811.json`](./qwen3-1.7b-release-validation-20260811.json) | Reviewed release summary for Qwen revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, manifest `775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1`, exact one-node profile, and digest-pinned image | Current first-identity issuance input PASS; not a storage-path promotion | Reviewed; repository-relative evidence only; site identity omitted |
| [`qwen3-1.7b-home-acquisition-gate-20260813.json`](./qwen3-1.7b-home-acquisition-gate-20260813.json) | Three-node reviewed durable-home acquisition: guarded last-home removal, interrupted remote cleanup, distinct explicit and automatic rank-2 end-to-end runs, full expected-manifest verification, atomic publication, explicit refresh, and final one-home/no-hot inventory | Current catalog/artifact acquisition PASS; serving integration, model qualification, and promotion not run or claimed | Reviewed; reproducible rank-only commands included; site topology, paths, hosts, nodes, plan IDs, and filesystem identity omitted |
| [`qwen3-1.7b-snapshot-manifest-20260811.json`](./qwen3-1.7b-snapshot-manifest-20260811.json) | Complete `sha256-snapshot-manifest-v1` for the exact issued Qwen revision; 12 files, 4,079,450,110 bytes | Current expected-seal content input PASS | Reviewed; logical snapshot paths and content hashes only |
| [`qwen3-1.7b-release-identity-20260811-runA.json`](../qwen3-1.7b-release-identity-20260811-runA.json) | Fresh exact-profile greedy capture A for the issued one-node Qwen identity | Current release validation PASS; 30-prompt source capture | Reviewed; prompts/results contain no site identity or secrets |
| [`qwen3-1.7b-release-identity-20260811-runB.json`](../qwen3-1.7b-release-identity-20260811-runB.json) | Fresh exact-profile greedy capture B for the issued one-node Qwen identity | Current determinism PASS; 30/30 identical to run A | Reviewed; prompts/results contain no site identity or secrets |
| [`qwen3-1.7b-release-identity-20260811-bench.json`](../qwen3-1.7b-release-identity-20260811-bench.json) | Fresh throughput capture for the issued one-node Qwen identity | Current release performance record; c=1/2/4/8 complete | Reviewed; no site identity or secrets |
| [`qwen3-1.7b-sealed-enforcement-gate-20260811.json`](./qwen3-1.7b-sealed-enforcement-gate-20260811.json) | Post-issuance exact-seal catalog, preparation, read-only launch, identity-label, zero-byte witness, smoke, and cleanup proof | Current sealed `library-hot` enforcement PASS; not included in its own issuance bundle and not a promotion | Reviewed; site topology, paths, hosts, nodes, interfaces, and filesystem identity omitted |
| [`qwen3-1.7b-replicated-seal-enforcement-gate-20260811.json`](./qwen3-1.7b-replicated-seal-enforcement-gate-20260811.json) | Shipped replicated-cache full verification, zero-byte witness, exact-snapshot read-only launch, identity labels, smoke, and cleanup for the issued Qwen identity | Current sealed replicated enforcement PASS; fresh download/copy covered deterministically but not physically rerun; not a promotion | Reviewed; site topology, paths, hosts, nodes, interfaces, and filesystem identity omitted |
| `model-library-hot-budget-admission-gate-20260811.json` | Exact all-rank filesystem admission; DeepSeek revision `7872f01b1d1fe23eabc4c98b48bffcef5a386062`; capacity only, not model identity | Current hot-budget gate PASS; no model bytes changed; not a promotion | Reviewed; site topology, paths, hosts, node/interface identity, and filesystem identity omitted |
| `model-library-home-removal-guard-20260811.json` | Physical all-node active-use removal guard; disposable synthetic HF-layout repositories only | Historical baseline PASS for the broader lifecycle matrix; the later selected-primary targeting repeat now also passes; no production model was deleted; not a promotion | Reviewed; site topology, paths, node/container identity, and filesystem identity omitted |
| `qwen3-1.7b-2node-witness-lifecycle-gate-20260811.json` | Physical durable-home symlink, sealed-hot, witness fallback, exact-snapshot launch, pin/restart, mismatch, and no-follow purge; Qwen revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, observed manifest only | Current lifecycle gate PASS; identity remains `legacy-unsealed`; not a promotion | Reviewed; site topology, paths, filesystem identity, and witness IDs omitted |
| `model-library-promotion-assessment-20260810.json` | Consolidated DeepSeek/Qwen promotion assessment; schema-2 local content digest, not a lab-issued expected seal | Historical assessment; materialization recommendation superseded | Embedded redaction declaration; topology/site fields omitted |
| `topology-ssh-trust-gate-20260810.json` | Topology-bound SSH alias/key/endpoint gate and Qwen sealed preparation | Current gate evidence | Embedded redaction declaration |
| `deepseek-v4-flash-ssh-roce8-vs-control8-counterbalanced-20260810.json` | DeepSeek full-model `ssh-control` versus `ssh-roce` performance/traffic proof | Current performance evidence | Embedded redaction declaration |
| `deepseek-v4-flash-parallel-rsync-roce-8v16-alternating-20260810.json` | DeepSeek 8-versus-16-stream alternating trials | Current stream-selection evidence | Embedded redaction declaration |
| `deepseek-v4-flash-parallel-rsync-roce-16stream-20260810.json` | Initial variable 16-stream exploration | Historical exploratory evidence | Embedded redaction declaration |
| *(omitted)* | Earlier copy-versus-NFS/RDMA bench JSON/logs (`deepseek-v4-flash-dsv4-*.json`, `dsv4-bench-*.log`) and `codex-fabric-review-20260809.md` | Never published: pending-sanitation drafts with site paths or topology IDs. Not in this tree. Home-view conclusion is ADR 0001, not those files. | **Unpublished — omitted** |

## Publication rules

- Use repository-relative links only; never publish user home/workspace paths.
- Omit or redact hostnames, IPs, SSH aliases/keys, node IDs, interface names,
  topology IDs, credentials, and other stable site identifiers.
- State the exact model revision/manifest or explicitly say when an artifact
  predates lab-issued expected-seal binding.
- Declare the qualification scope and do not infer model correctness from
  health, preparation, or completion smoke.
- Do not use `catalog-artifact` evidence to satisfy a validation criterion.
- Retain every applicable observation. Any exclusion must name the observation,
  cite evidence, and remain visible in the review history.
- Declare every attempted criterion and account for it with a complete or
  inconclusive observation; incomplete attempts use inconclusive observations.
- Record operator commands with the closed typed descriptor schema; omit raw
  secrets, environment values, absolute site paths, and private topology
  identifiers. Verify program digests during trusted capture and retain the
  publication privacy audit.
- Preserve valid cross-release subsystem evidence only when its measured inputs
  and contracts are unchanged; document any causal invalidation explicitly.
- A relative performance baseline must bind the reviewed predecessor contract,
  bundle, decision, and run whose relevant criterion passed. Do not require or
  imply a globally `Validated` predecessor when only that criterion matters.
- Never transfer a validation status to a changed Model Serving Release. Reused
  subsystem evidence is linked scope evidence, not an inherited release pass.
- Mark evidence `current`, `historical`, `superseded`, `failed`, or
  `partial`; never delete or rewrite a failure into a pass.
- A superseding ADR or ledger changes the current decision, not the historical
  measurements.
