# Pulsar Split Plan

Program plan for turning one over-built repository into a lab for AI
engineers (`pulsar-lab`) and a serving stack for operators (`pulsar`), joined
by a single release spec ([ADR 0017](./decisions/0017-release-spec-is-the-release-contract.md)).
Five phases, twenty-one work packages. This file is the resumable copy of the
plan: a new session starts from **Resume from here** below, then reads the
phase it lands in.

Revision history: written 2026-09-02; revised 2026-09-04 after the first
stable spec; state block updated 2026-09-04 during WP3.4.

## Resume from here (state as of 2026-09-04)

- **Done and merged to `main`:** Phase 0 (#123–#125), Phase 1 (#126–#138:
  spec module, policy and evaluator, generator, stack consumer, first spec
  served by id), Phase 2 as reduced (#139 runner and promote command, #140
  nano recorded `failed`). Released specs: Nemotron 3.5 Lightning
  `de2e93ce…` review `stable`; Nemotron 3 Nano `26597c10…` review `failed`.
- **In review:** WP3.4 retire confs, branch `phase3-retire-confs`, pull
  request #141. Batches 1–3 are on the branch (core, tests, docs). Bot review
  rounds one and two are fixed. What the unit decided is listed under WP3.4.
- **Next physical step (needs Luis on the Spark):** WP3.4 batch 4, the
  physical close: stop the container that was started from the retired nano
  conf (it is now unreachable by name), serve a released spec by id, run
  `validate/baseline-v1.sh <spec_id> --check-only`. Luis chooses Lightning
  or nano.
- **Then:** WP3.1 acquire by spec, WP3.2 prepare and readiness, WP3.3 remove
  the cut list, WP3.5 two-node physical session, Phase 4 split.
- **Standing rules:** no site identifiers or secrets in tracked files, PR
  bodies, or briefs (`scripts/check_publishable_privacy.py` gates every
  commit); nothing under `releases/` changes except through a reviewed
  promotion PR; physical actions (downloads, tree removal, server start or
  stop, soak) need explicit approval; the full selftest runs once per PR head
  and never overlaps a physical action; validated review findings are
  implemented by Claude, never handed to a reviewer bot; review rounds are
  handled as one batch per round; pull requests open only on Luis's word;
  spec review status is display-only and never a serving gate.

## Revision of 2026-09-04

The first physical run (Nemotron 3.5 Lightning, one Spark) reached the first
stable spec by hand before Phase 2's tooling existed. What that run changed:

- Lightning, not nano, was the first baseline job: it was the only receipted
  download. Nano needed its image pinned first.
- Two producers the policy declared did not exist (`verify_snapshot_manifest`,
  `serve_smoke`). Written during the run. Every physical job now opens with a
  dry pass that only checks each producer answers `--help`.
- Phase 2 is reduced. The runner is a thin orchestrator against an
  already-running server: no launch, no stop, no resource monitor, no state
  machine. It proves the server is the spec's (launch-contract label, image
  digest, boot witness) before and after the gates. The promote command
  exists; the two replacement skills wait for Phase 4; the deep suite waits
  until a spec needs `validated`.
- Evidence lives in tracked `results/baseline-v1/<spec_id>/`, which the spec
  references and the privacy scanner covers. LFS waits for a capture large
  enough to matter.
- Phase 3 is reordered. Confs retire before receipt objects go: conf prepare
  and the spec generator read the receipt, and a new model's first spec still
  needs the manifest its download emits. The cut is plan, approval,
  occupancy, and attachment.
- The two-node milestone moves to WP3.5. No viable two-node recipe exists
  today; one physical two-node session instead of two.
- The ADR 0017 section of AGENTS.md was pulled forward from Phase 4, because
  automated reviewers were applying ADR 0004 attempt rules to baseline-v1
  runs.
- Delegation changed. Validated review findings are implemented by Claude,
  never handed to a reviewer bot; Grok review is optional per package; review
  rounds are handled as one batch per round; the full selftest runs once per
  PR head.

## Decisions this plan assumes

- **Two repos, split by persona.** `pulsar-lab` measures; `pulsar` serves.
  Code depends lab → stack; data points stack → lab. Only the spec module is
  shared.
- **One spec schema, two states.** `measured` in the lab, `released` in the
  stack after a reviewed PR. `spec_id` hashes the identity block. One file
  per identity under `releases/`.
- **Status ladder:** `stable` (baseline-v1 passed, about two hours),
  `validated` (deep suite), `failed`, `withdrawn`. Display-only; identity
  checks are the only serving gate.
- **GB10-first, platform-isolated.** OS, driver, CUDA, fabric, and memory
  assumptions live in one platform reference (`dgx-spark-gb10`). A future
  Spark model is a new platform file plus new specs. `platform_id` is part of
  geometry and therefore of identity.
- **N Sparks per cluster in v1.** Geometry is `{platform_id, nodes, tp, pp,
  fabric}`. The baseline runner drives cluster launches from the first
  version.
- **Spec is the receipt.** The exact file manifest travels in the spec; the
  library verifies trees against it.
- **Existing tested profiles inherit nothing.** They are the first baseline
  jobs.
- **Build the seam before the split.** Phases 0 to 3 happen in the current
  repo.

## Model library: keep, cut, defer

The library existed because identity used to come from observed trees, so it
needed receipts, occupancy, duplicates, relocation, and a health engine to
reason about what it had found. With a spec-first design a tree is either
verified against the spec or it is not, and most of that reasoning
disappears. Keep what moves or protects bytes, cut what reasons about
ambiguous identity, defer what recovers from losses the spec already covers.
The signed-off table is [MODEL_LIBRARY_REDUCTION.md](./MODEL_LIBRARY_REDUCTION.md).

| Capability | Call | Reason |
|---|---|---|
| Exact-commit acquisition into one durable home per identity | keep | Reduced to: download by commit, hash the tree, compare to the spec manifest. Plan/approval/receipt objects go. |
| Home placement rule (home on a serving rank; working copies elsewhere) | keep | Correct for N-node and cheap. |
| Prepare: transfer to other ranks (ssh-roce, streams), rank-local hot view, full verification after transfer | keep | The operator's only path to local files on every rank. |
| Verification stamp per view (tree digest plus size/mtime fingerprint; re-hash on change) | keep | Keeps launch fast on 30 to 120 GB trees without a full rehash. |
| Pin, purge, hot budget, lifecycle locks | keep | Disk management and mutation safety; operator-visible. |
| Readiness check before launch | keep | Reduced to four questions: spec present, home verified, every rank view verified, launch contract equals the spec's. |
| Receipt service objects: plan, approval, receipt, result, occupancy, attachment | cut | The spec is the receipt (the download keeps emitting the manifest a first spec needs). |
| `home relocate`, occupancy portability, unbound-complete classification | cut | Existed to rebind identity to trees; identity is now the spec. |
| Primary selection among duplicate homes, cleanup-recommend | cut | One verified home per identity per cluster; a second copy is a working view or a purge target. |
| Catalog scanning of arbitrary hub trees | cut | The catalog derives from `releases/` plus verification stamps. |
| Health report engine and model-storage prepare flows | cut | Replaced by one projection: spec, status, home verified, ranks ready. |
| Guarded home removal plan, archive checks before last-home removal | cut | Reduced to `home remove --yes` that refuses while pinned or in use. |
| Cold archive to NFS, receipt replicas, controller-loss recovery, restore | defer | Re-download by exact commit is the recovery path; the spec is in git. |
| Cold storage configuration and its wizard page | defer | Follows the archive decision. |

Expected outcome: the library modules shrink to roughly a third of today's
combined size, and ADR 0001, 0011, 0014, 0015, and 0016 become historical.

## Phases and work packages

Each package states what it produces, what proves it, and whether it can be
closed without hardware (`deterministic`) or needs Luis and the GB10
(`physical`).

### Phase 0 · Decisions and platform boundary (done)

- **WP0.1 Release-spec ADR** (deterministic, done as ADR 0017, #123):
  the spec as the release contract and the four-word status ladder;
  supersession notes on ADR 0004, 0008, and 0012.
- **WP0.2 Platform reference and probe isolation** (deterministic, done,
  #124): `platforms/dgx-spark-gb10.json` and `scripts/platform_reference.py`;
  probes read from it.
- **WP0.3 Library keep/cut sign-off** (deterministic, done, #125):
  `docs/MODEL_LIBRARY_REDUCTION.md`.

### Phase 1 · The seam (done)

- **WP1.1 Spec module** (#126): `release_spec/` with schema v1, states,
  statuses, `spec_id` hashing, engine-argument normalization fixtures, the
  verifier, and the CLI. Recipe is free-form token lists; geometry owns tp/pp;
  overlay flags are rejected from the recipe.
- **WP1.2 Baseline-v1 policy and evaluator** (#127): `policy/baseline-v1.json`
  (six gates: identity manifest, serving smoke, strict same-boot, pinned
  GSM8K subset with a lab-wide floor, 60-minute soak, performance snapshot)
  and `validate/baseline_v1.py`.
- **WP1.3 Spec generator** (#128): `scripts/release-spec.sh` generating a
  measured spec from a profile, an explicit receipt, and a stack version,
  with a gap report. Stage 4 replaced `from-profile` with `from-draft`.
- **WP1.4 Stack consumer** (#129–#136, four sub-packages): `releases/`,
  `scripts/release_consumer.py` (overlay schema, projector, comparison,
  `./pulsar release verify|show|list`), catalog display of the review
  status, spec-start through `load_conf`, prepare checking the spec manifest
  against the receipt. Identity-based view reuse was tried and dropped: one
  name, one directory.
- **WP1.5 Serve one profile from a spec** (physical, done with Lightning,
  #137/#138): `up.sh <spec_id>` reached health with a defaults-only overlay;
  promotion followed the same day.

### Phase 2 · Lab runner to first stable (done as reduced)

- **WP2.1 Evidence layout** (decided): tracked
  `results/baseline-v1/<spec_id>/` with the six measurements, the filled spec,
  and `run.json`.
- **WP2.2 Baseline runner** (#139): `validate/baseline-v1.sh <spec_id>
  --spec F --out D --dataset F` against an already-running server; proves the
  server is the spec's before and after the gates; stops at the first failed
  gate keeping evidence; `--check-only`. Dropped: launch, stop, monitor,
  state machine.
- **WP2.3 Deep suite adapter** (deferred until a spec needs `validated`).
- **WP2.4 Promotion path** (#139): `scripts/release-spec.sh promote`; git
  stays the operator's job; replacement skills move to WP4.2.
- **WP2.5 First stable spec** (physical, done): Lightning `stable`; nano
  went through the runner twice on the v0.26.0 image and failed strict
  same-boot both times (intermittent logprob variance on one prompt, not
  caused by the atomic-add setting); recorded as the first `failed` release
  (#140). Trying nano on a newer image is a product decision, not a gate.

### Phase 3 · Model library reduction (in progress)

Order revised 2026-09-04: WP3.4 runs before WP3.1.

- **WP3.4 Retire confs** (deterministic, in review as #141). Produces:
  `models/*.conf` deleted; a profile is a spec id under `releases/`;
  `load_conf` loads spec plus overlay everywhere; the legacy STATUS ladder
  and its filters gone; `docs/MODELS.md` generated from `releases/`
  (`scripts/release.sh list --markdown`, drift-tested); `docs/RECIPES.md`
  deleted. Decisions taken in the unit:
  - D1: the served name and port come from the deployment overlay, never
    the spec; the wizard picker lists `<spec_id>  <served name>  N nodes`.
  - D2: the conf format survives only as a lab draft (`release-spec.sh
    from-draft`, `home add --draft`); the model library binds catalog
    profiles from `releases/` only.
  - D3: MODELS.md generated block plus a history table of retired profiles.
  - Lab path before promotion: `PULSAR_SPEC_FILE=<measured-spec>` makes the
    measured spec's id a startable profile; the runner exports it for its
    children and refuses a conf name; prepare's manifest lookup honors it.
  - A released spec named by its own id projects its review without a
    download receipt (its own manifest stands in), hidden only on
    launch-contract drift.
  - A missing `.pulsar-overlay.json` is the default overlay (port 8000,
    served name = model id); an unreadable one is refused.
  - Batch 4 (physical close) is still open; see the state block.
- **WP3.1 Acquire by spec** (deterministic): `pulsar model acquire
  <spec_id> [--node R] --yes`: download the exact commit into the durable
  home on the chosen rank, hash, compare to the manifest, write the stamp.
  Receipt, approval, occupancy, and attachment objects removed (the manifest
  the download emits stays). Proof: matching tree passes, one altered byte
  fails, extra file fails, partial download leaves no home.
- **WP3.2 Prepare and readiness** (deterministic): `pulsar model prepare
  <spec_id>` for N ranks with full verification and stamps per view; the
  four-question readiness check; pin, purge, budget retained;
  `check-weights.sh` rewritten to the four questions.
- **WP3.3 Remove the cut list** (deterministic, largest diff): relocation,
  primary selection, cleanup-recommend, hub-tree scanning, the health engine,
  guarded removal plans, cold archive and cold storage configuration, and
  their selftests and docs; `pulsar models` reduced to the four-column
  projection; retired ADRs banner-marked. Proof: full selftest green; module
  line counts recorded before and after; no operator doc references a
  removed command. Split by module across subagents with one integrator.
- **WP3.5 Serve N-node from a spec after reduction** (physical): acquire,
  prepare, and serve one single-node and one two-node stable spec with the
  reduced library. The two-node spec is first produced here; the only
  plausible candidate today is the two-node Qwen 3.8 shell (now a draft).
  Proof: serving smoke on both geometries; purge and re-prepare round trip.

### Phase 4 · Split into two repositories

- **WP4.1 Create pulsar-lab** (deterministic): `validate/`, `bench/`, the
  monitor, the lab CLI, `policy/`, `experiments/`, historical `results/` and
  ledgers, the "run an experiment" skill, its own AGENTS.md, the stack pinned
  by version. Proof: lab selftest green in a fresh clone; dry-run baseline
  against the pinned stack.
- **WP4.2 Stack cleanup** (deterministic, parallel): remove plan, attempt,
  capture, issue, and registry tooling, both current skills, the ADR 0004
  runbooks, `models/model-serving-releases/`, and their selftests; add the
  "adopt a release" skill; rewrite AGENTS.md for operators; archive tag of
  the pre-split tree. Proof: stack selftest and privacy scan green; no
  reference to a removed command in docs.
- **WP4.3 Shared spec module packaging and CI** (deterministic):
  `release_spec` published or vendored with a pinned digest in both repos;
  CI in each; a cross-repo check that the lab's pinned stack version matches
  the launch-plan schema the spec expects. Proof: a deliberate schema bump
  fails both pipelines until pins are updated.
- **WP4.4 Cross-repo promotion** (physical): one baseline run in
  `pulsar-lab` proposed and merged into `pulsar/releases/`, then served by an
  operator checkout that has never seen the lab.

## Order and parallelism

Phase 0 packages ran in parallel. WP1.1 gated WP1.2 through WP1.4. WP1.5 was
the first hardware checkpoint. Phase 2 is reduced to the runner and the
promote command; WP2.3 waits for a spec that needs `validated`. WP2.5 (single
node) is the milestone before any deletion. Phase 3 depends on WP0.3 and
WP2.5 and is the largest body of deletion; split WP3.3 by module. Phase 4
begins only when Phase 3's full selftest is green on both geometries.

## Delegation notes

- **Briefs.** Every deterministic package can be briefed with the decision
  list, ADR 0017, the target command surface, the proof section, and the rule
  that nothing physical may be claimed. Library packages also carry the
  keep/cut table.
- **Who does what.** Claude implements every package and every validated
  review finding; Codex and Cursor review PRs only and never receive fix
  requests; Grok read-only review is optional per package and most useful for
  the deletion packages (WP3.3, WP4.2). Review rounds are one batch: validate
  the round, one fix commit, reply on every thread, one push. The full
  selftest runs once per PR head, never overlapped with a physical action.
- **Physical checkpoints.** Four packages need Luis and hardware: WP1.5
  (done), WP2.5 (done), WP3.5, WP4.4. Everything else is verified
  deterministically, so work can run ahead to the next checkpoint.
- **Risks to watch.** Normalization drift between repos (WP1.1 fixtures and
  WP4.3 pins); the baseline runner reproducing the identities the stack
  computes (it reuses `launch_plan.py`, never reimplements it); the library
  reduction quietly keeping code by omission (WP3.3's line-count proof).

## Deliberately not in this plan

- A launcher interface for non-Spark hardware. The platform reference is the
  hook; a second implementation waits for a second platform.
- Cryptographic signing of specs. Review and merge remain the trust event.
- Migrating old tested results into status. They became lab jobs after WP2.5.
- Object storage for raw captures. WP2.1 defines the rule.
