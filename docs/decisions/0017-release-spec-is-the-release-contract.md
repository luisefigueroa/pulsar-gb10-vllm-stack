# ADR 0017: The release spec is the release contract

- **Status:** Accepted
- **Date:** 2026-09-02
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
  (target; the live text still describes ADR 0004 until the staged cutover)
- **Related decisions:**
  [ADR 0001](./0001-model-library-home-view-and-validation-identity.md),
  [ADR 0002](./0002-subsystem-qualification-boundaries.md),
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0008](./0008-breaking-compatibility-window.md),
  [ADR 0009](./0009-no-launch-trust-mode-axis.md),
  [ADR 0010](./0010-operator-consumes-catalog.md),
  [ADR 0011](./0011-portable-occupancy-and-cold-archive.md), and
  [ADR 0012](./0012-retire-expected-seal-and-schema-1-bundles.md)
- **Amends:** ADR 0001 decisions 4–5 and 7 as a live expected-manifest
  identity product, restored in spec form, not as schema-1 seals; ADR 0002's
  object-model paragraph that names ADR 0004 objects as the issuance model;
  ADR 0009 decision 2 only for the operator-facing status names, not the
  no-trust-mode rule; ADR 0010 decision 1 consume-surface after Stage 3.
- **Supersedes in part:** ADR 0004 decisions 2–4 and 6 (the five separately
  persisted object kinds, the frozen two-layer contract as separately
  persisted kinds, the status ladder, issuance and staging, the
  supersession graph, and `MODEL_SERVING_RELEASE_ID`); ADR 0008 only where
  it treats `--legacy-tested` / profile `STATUS` as the standing
  recommendation vocabulary; ADR 0012 decision 5's "no reviewed expected
  manifest" and the unknown-tree consequence that there is no
  expected-manifest fallback. ADR 0012 decisions 1–4 stand: schema-1 seals
  stay retired. This ADR does not supersede ADR 0004 decision 1's
  four-part subject or display-only status.

## Context

Pulsar currently splits one serving subject across five persisted ADR 0004
object kinds (release descriptor, Validation Contract, run record, evidence
bundle, validation decision), plus issuance staging, a supersession graph,
profile `STATUS`, and `MODEL_SERVING_RELEASE_ID`. Lab expected-seals are
already retired. That is too many products for a measurement-lab versus
operator-stack split.

ADR 0004 rejected collapsing those five objects into one document
([Collapse the five objects into one Release Assessment (SIM-01)](./0004-model-serving-release-validation.md)):
a single assessment would drop cross-object deduplication and make
failed-attempt retention easier to get wrong. It kept the five roles and
said to revisit only with a new ADR that preserves failed attempts and
deterministic cross-object verification. This ADR is that revisit. Failed
attempts remain in lab records and in spec `measurements[]`; a spec whose
`review.status` is `failed` or `withdrawn` is kept, not deleted. Identity
verification is the hashed identity block plus evidence SHA-256 values.

The repository will split by persona. `pulsar-lab` is the measurement
harness, experiment records, and recipe drafts, with light governance.
`pulsar` is the operator stack: launchers, model-library acquire and
prepare, catalog, and a `releases/` directory of specs, with one reviewed
PR per spec. Code depends lab → stack. Data points stack → lab. Only the
spec schema module is shared code. This ADR adds no code, schema, or
scripts.

## Decision

1. **One spec document is the release contract** between the measurement
   lab and the operator serving stack. The four-part subject from ADR 0004
   decision 1 still holds: exact model identity, serving recipe, runtime
   and image identity, and supported hardware geometry. Changing any part
   creates a new spec. Status remains display-only and never a serving
   gate.

2. **One schema, two states.** A spec is `measured` when the lab emits it
   and `released` when a reviewed PR promotes the same document into the
   stack. `spec_id` hashes the identity block: model id, exact commit,
   snapshot file manifest, normalized engine arguments, image digest, and
   geometry `{platform_id, nodes, tp, pp, fabric}`. Platform id
   `dgx-spark-gb10` carries the GB10 capacity class. A future Spark SKU is
   a new platform file and new specs. One file per identity under
   `releases/`. Git history of that file is identity lineage, not status.
   Lifecycle is `review.status`. Clarified at Stage 1: engine arguments and container
   environment are free-form token lists in `identity`, hashed in order after
   shell splitting and `--flag=value` splitting; tensor and pipeline
   parallelism belong to geometry and may not appear in the token list. No
   vLLM flag is promoted to a schema field.

3. **Spec sections.** identity; launch_contract (stack version plus argv);
   measurements (per-criterion results with the thresholds they were
   judged against); baselines (community claim versus measured); evidence
   (lab commit, relative paths, SHA-256); review (status, reviewer,
   `reviewed_at`).

4. **Display-only spec statuses.** `review.status` is one of `stable`,
   `validated`, `failed`, or `withdrawn`. It never grants or denies
   serving. There is no expiry on `stable`. The catalog may show
   `stable` since `<reviewed_at>` only when the computed launch contract
   matches the spec. `stable` means the spec passed baseline-v1 (about two
   hours): identity, serving integration, strict same-boot, a GSM8K
   subset with a pinned dataset digest and a floor, a 60-minute soak, and
   a performance snapshot. `validated` means the spec passed the deep
   suite with per-spec frozen thresholds. Baseline thresholds live in a
   lab-wide policy file; a `stable` spec records that policy's digest and
   the thresholds it was judged against in `measurements[]`. Deep
   thresholds freeze in the spec. `review.status=withdrawn` is the
   withdrawal mechanism; git history is identity lineage, not status.

5. **Recipe versus deployment overlay.** The spec is the immutable recipe.
   A local overlay holds port, served name, placement, and cache root.
   If the stack's computed launch contract (argv, image, geometry)
   differs from the spec's `launch_contract`, the stack hides that spec's
   `review.status` only. The serving row stays listed and startable when
   operational checks pass.

6. **The spec manifest is the reviewed expected file list.** The model
   library verifies trees against it. This deliberately reverses ADR 0012
   decision 5's "no reviewed expected manifest" fallback. Schema-1 seals
   stay retired (ADR 0012 decisions 1–4). Download receipt plus occupancy
   remain catalog and artifact identity. When a spec manifest and a
   receipt both exist, they must list the same files or prepare fails
   without fallback. Spec presence is not occupancy.

7. **GB10-first, N Sparks in scope.** Geometry is
   `{platform_id, nodes, tp, pp, fabric}`. `dgx-spark-gb10` carries the
   GB10 capacity class. A new Spark SKU is a new platform file plus new
   specs. N Sparks per cluster is in scope from the first spec schema.
   Each spec still has exact `nodes`. Do not invent tensor-parallel,
   pipeline-parallel, or node count from discovered membership.

8. **Existing profile `STATUS=tested*` inherits no spec status.** Those
   profiles become the first baseline jobs and stay unlabeled on this
   ladder until a spec exists.

9. **Interim through Stage 3.** Until a later
   stage lands schema and files:

   1. This ADR is accepted target architecture. Code, tests, the empty
      registry, unbound profiles, and current-state ledgers remain
      ADR 0004 until a later implementation stage lands schema and files.
   2. Through Stage 3, `./pulsar` still starts `models/*.conf`
      ([ADR 0010](./0010-operator-consumes-catalog.md)). A spec in
      `releases/` is not an executable overlay substitute until overlay
      and launch-contract comparison exist.
   3. Profile `STATUS=tested*` does not become `stable` or `validated`.
      Those rows are the first baseline jobs, still unlabeled on this
      ladder.
   4. Spec statuses are display-only. Admission remains identity, recipe,
      topology, capacity, security, ownership, and lifecycle.
   5. Download receipt plus occupancy remain live catalog identity
      ([ADR 0011](./0011-portable-occupancy-and-cold-archive.md),
      [ADR 0012](./0012-retire-expected-seal-and-schema-1-bundles.md)).
      Once a spec exists, its manifest is the reviewed expected file list
      for a `released` spec; prepare fails without fallback if spec and
      receipt file lists differ. Do not treat spec presence as occupancy.
   6. If computed argv, image, or geometry differs from the spec
      `launch_contract`, do not display spec `review.status`; still list
      and allow the profile if operational checks pass
      ([ADR 0009](./0009-no-launch-trust-mode-axis.md)).
   7. Sentences that the registry is empty and no profile sets
      `MODEL_SERVING_RELEASE_ID` stay true and must not be deleted in
      this unit.
   8. Stages are named in this ADR. Do not point at an out-of-tree plan
      as authority.

10. **Replacement map.** Current ADR 0004 surface maps as follows. Items
    marked live stay until Stage 4 unless noted.

    | Retired or current ADR 0004 surface | Replaced by |
    |---|---|
    | Release descriptor | spec `identity` plus `spec_id` |
    | Validation Contract | lab baseline policy plus spec-frozen deep thresholds in `measurements[]` |
    | Run record | spec `measurements[]` plus `evidence[]` |
    | Evidence bundle | spec `evidence[]` (lab commit, relative paths, SHA-256) |
    | Validation decision | spec `review` (`status`, reviewer, `reviewed_at`) |
    | Supersession graph / `Superseded` | git history of the one `releases/<spec_id>` file; `withdrawn` in `review.status` |
    | Issue review declaration `pulsar-model-serving-release-issue-review` | reviewed PR that copies `measured` → `released` |
    | `scripts/model-serving-release-plan.sh` (and `model_serving_release_plan.py`) | lab recipe draft → `measured` spec (Stage 2) |
    | `scripts/model-serving-release-attempt.sh` / `-capture.sh` | lab measurement plus spec emit (Stage 2) |
    | `scripts/model-serving-release-issue.sh` `plan`/`stage` | stack PR into `releases/` (Stage 3–4) |
    | `scripts/model-serving-release-registry.sh` `verify`/`show-*` | read the spec file; git log for lineage |
    | `models/model-serving-releases/{descriptors,contracts,run-records,evidence-bundles,decisions}/` | `releases/` one file per `spec_id` |
    | `MODEL_SERVING_RELEASE_ID` | path / `spec_id` under `releases/` |
    | Profile `STATUS` (`tested*`, `untested`, `do-not-use` as this ladder) | spec `review.status`; `do-not-use` remains an operational recipe label until confs die |
    | `skills/pulsar-model-onboarding` | lab skill (later); not this unit |
    | `skills/pulsar-model-serving-release-issuance` | ordinary reviewed PR of a spec |
    | `docs/MODEL_RELEASE.md` plus capture/issuance runbooks | lab plus stack spec runbooks (later) |
    | `EXPECTED_MODEL_SEAL` / `models/seals/` / schema-1 bundles | already retired (ADR 0012); not revived; spec manifest is a new kind |
    | `scripts/model-release.sh`, `validation-bundle verify`, `--reviewed-identity`, `--validated` | already removed (ADR 0008 / ADR 0012); stay removed |
    | Download receipt plus occupancy | not replaced in this ADR; spec manifest must match receipt files when both exist |
    | `observe-resources` diagnostic | evidence payload inside spec `evidence[]`, not a criterion |
    | Onboarding journal | lab-private orchestration; never a spec section |

## Staged implementation

- **Stage 0 (this unit).** This ADR, insert-only supersession banners, and
  one-sentence forward pointers. No code.
- **Stage 1.** Shared spec schema module only.
- **Stage 2.** The lab emits `measured` specs.
- **Stage 3.** The stack stores `released` specs under `releases/`;
  `models/*.conf` and specs coexist; overlay plus launch-contract
  comparison; the library verifies against the spec manifest in addition
  to the receipt. This is the Stage 3 coexistence window named in
  **Amends**.
- **Stage 4.** Retire the five-object registry, `MODEL_SERVING_RELEASE_ID`,
  the profile `STATUS` ladder, and issuance staging as the trust path.
  One reviewed PR per spec.

## Rejected alternatives

- **One repository per release.** Multiplies clone, review, and schema
  drift cost. One `releases/` file per identity in the operator stack is
  enough; git history is lineage.
- **Keep the five-object graph.** That is the SIM-01 holding pattern in
  ADR 0004. It blocks the lab/stack split because the contract is five
  kinds plus staging plus a profile field.
- **`provisional` or `pre-release` as the early status name.** The early
  passing status is `stable`. Those other names invite a fourth trust
  vocabulary beside profile `STATUS` and the ADR 0004 ladder.

## Revisit triggers

Revisit if a later change would collapse download receipts into the spec,
make spec `review.status` a serving gate, hide serving rows that are not
`stable`, or restore schema-1 seals.
