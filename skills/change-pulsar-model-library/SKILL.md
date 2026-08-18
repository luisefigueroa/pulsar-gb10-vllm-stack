---
name: change-pulsar-model-library
description: Guide reviews, experiments, designs, implementations, and documentation changes affecting Pulsar's model catalog, downloads, durable homes, preparation and transfer paths, rank-local runtime views, pin/purge lifecycle, validation identity, or model-library promotion. Use for changes to model-library scripts, library-hot launch behavior, weight-fabric interactions, model distribution policy, seals and witnesses, storage or resilience claims, and their operations, validation, or evidence documents.
---

# Change Pulsar Model Library

Use this playbook to keep model-library work consistent without suppressing
useful architectural ideas. Treat it as procedure, not as an architectural
source of truth; read the repository's live authority instead of copying its
doctrine into this skill.

## Establish authority

Work from the repository root. Before evaluating or changing behavior:

1. Read the model-library section of `AGENTS.md`.
2. Read `docs/MODEL_LIBRARY_DESIGN.md` completely.
3. Read every applicable accepted or superseding record under
   `docs/decisions/`, especially ADR 0001 for home views and validation
   identity and [ADR 0002](../../docs/decisions/0002-subsystem-qualification-boundaries.md)
   for qualification scope and causal invalidation.
4. Read `docs/MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md` when current code,
   schemas, states, or implementation gaps matter.
5. Read the affected runbooks, validation ledger, revalidation instructions,
   and evidence index when the request touches operations, claims, promotion,
   or results.

Apply the authority order in `AGENTS.md`. Treat current code and measurements as
evidence about implementation, not as silent amendments to accepted design.

## Classify the work

Identify the request before acting:

- **Explain or review:** inspect and report; do not mutate merely because a
  change seems useful.
- **Experiment:** keep the path explicit and opt-in, define the hypothesis and
  comparison, and avoid changing promoted defaults or claims.
- **Implement within accepted design:** state the affected contracts and make
  the smallest complete change with proportionate verification.
- **Reconsider accepted design:** follow the proposal and approval protocol
  below before making a conflicting change.

Separate accepted target architecture, current experimental implementation,
and immutable historical evidence in every analysis.

## Classify qualification scope

Before choosing tests or changing a claim, classify each affected result:

- **Catalog/artifact service:** exact content/identity, durable placement, transfer, runtime views, retention, repair, and cleanup.
- **Serving integration:** the selected image and launcher use the intended exact runtime source; health, warmup, completion smoke, and owned stop.
- **Model qualification:** stability, accuracy, throughput, latency, strict
  same-boot, long context, and soak for exact runtime inputs.
- **Release/promotion:** provenance/security, physical geometry, and the
  conjunction required for a supported profile, wizard path, or default policy.

For ADR 0004 objects, criterion scope is canonical rather than reviewer
selected: stability, accuracy, throughput, latency, and strict same-boot use
`model-qualification`; serving integration uses `serving-integration`; and
provenance/security plus physical geometry use `release-promotion`.
`catalog-artifact` is acquisition/preparation evidence and cannot satisfy a
validation criterion.

Apply [ADR 0002](../../docs/decisions/0002-subsystem-qualification-boundaries.md):
a failure does not erase valid evidence from another scope
without a demonstrated causal connection, but it blocks every combined claim
that requires the failure to pass. Never treat health or completion smoke as
model qualification. Do not carry `STATUS=tested` or an old validation bundle
onto changed model, image, runtime, or geometry inputs merely because generic
catalog evidence remains reusable.

For a reviewed decision, include every applicable observation automatically.
An exclusion must be explicit and evidence-backed. Apply ADR 0004's conflict
rules rather than selecting favorable runs. Relative performance must bind the
reviewed predecessor contract, bundle, decision, and run whose relevant
criterion passed; the predecessor need not be globally `Validated`.
Structural compatibility checks never substitute for physical DGX evidence.
Every post-barrier non-preparation run must hash-bind its attempted frozen
criteria and account for each with a complete or inconclusive observation;
incomplete attempts may not report a complete observation. Keep operator
command evidence on ADR 0004's closed program/operation/resource schema, use
typed criterion and protected site references, and require trusted privacy
review rather than treating structural screening as proof. Require later
acyclic supersession.

Build a change-impact statement before running gates:

- model revision/seal changes require catalog identity, integration, and model qualification for release;
- image, dependency, runtime, or geometry changes require integration and model qualification, not automatic reruns of unchanged catalog mechanics;
- transfer, witness, metadata, retention, repair, or cleanup changes require affected catalog gates plus integration where runtime views change; expand model qualification only for a new release runtime source or a plausible causal effect; and
- documentation-only classification changes require docs/control-plane checks and create no physical claim.

When evidence crosses scopes ambiguously, state the hypothesis and choose the smallest safe experiment that can establish or reject the causal link.

## Challenge accepted decisions constructively

Treat accepted decisions as constraints on unapproved action, not limits on
analysis. Actively surface credible alternatives when requirements,
technology, evidence, failure modes, or operating assumptions evolve—even
when an alternative conflicts with the current design.

Label a conflicting idea as a **proposal** and explain:

- which accepted decision or invariant it challenges;
- what changed or what new information makes it worth reconsidering;
- its expected benefit, tradeoffs, and failure modes;
- the evidence and smallest safe experiment needed to evaluate it; and
- which ADRs, contracts, defaults, and claims would change if adopted.

Do not implement, enable, promote, or document a conflicting proposal as
accepted—and do not run a state-changing experiment that depends on the
deviation—without explicit user or maintainer approval that acknowledges the
conflict. Approval for an adjacent objective is not approval to override the
decision. Read-only investigation, comparison, and presentation of the idea do
not require approval.

After approval, add or supersede the governing ADR and update dependent
architecture, implementation, operations, validation, and evidence surfaces as
part of the change. Preserve the previous decision and its evidence as history.

## Analyze affected contracts

Map the proposed work across these independent axes before editing:

- durable ownership and placement;
- origin, transfer, runtime source, and retention;
- expected identity, observed identity, source-attested identity, validation
  bundle, and witness state;
- control, inference, and weight-transfer planes;
- rank geometry, topology identity, and trust boundaries;
- preparation, launch, pin, purge, restart, home loss, and rollback;
- human CLI behavior and machine-readable schemas; and
- experimental, candidate, promoted, and historically tested claims.

Reviewed Model Serving Release bindings, legacy expected seals, and unbound
source-attested identity are distinct. Source-attested adoption is
`catalog-artifact` observed/source identity only: it does not create a seal,
status, serving permission, or a Model Serving Release decision. Unknown and
pre-existing homes still require a reviewed expected manifest independent of
the observed tree. A home created by source-attested acquisition may be reused
only after a complete offline rehash against its valid receipt. The public
read-only plan, separately confirmed exact-commit acquisition, immutable
receipt, offline `home verify`, exact prepare binding, and onboarding-skill
composition are implemented as deterministic control-plane behavior. They make
no physical Hub/DGX, serving-integration, model-qualification, status, or
promotion claim.

Call out implementation gaps instead of writing as though accepted target
behavior already exists. Identify trust-boundary and destructive-lifecycle
changes explicitly.

## Make the change

Follow the repository's Bash/Python boundary and lifecycle rules. Keep
fallbacks, alternate transports, replica policies, and geometry changes
explicit and operator-visible. Preserve fail-closed behavior and avoid
expanding the request into unrelated promotion or cleanup work.

Update the relevant authority and support surfaces together:

- architecture contract: `docs/MODEL_LIBRARY_DESIGN.md` and an ADR;
- current behavior or schema: `docs/MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md`;
- operator behavior or dependency: `docs/OPERATIONS.md` and, when applicable,
  `docs/WEIGHT_FABRIC.md`;
- tested claim, qualification scope, or invalidation rule: `docs/VALIDATION.md`, `docs/REVALIDATE.md`,
  and `docs/MODELS.md`;
- durable evidence status: `results/model-library/README.md`; and
- cross-agent safety contract: `AGENTS.md`.

Update only the surfaces the change actually affects. Never fabricate a seal,
promotion result, or hardware claim from local state. Preserve failed, partial,
and superseded evidence.

## Verify and hand off

Run validation in proportion to the change:

1. Run `git diff --check`.
2. Run the repository's documentation or link checks when present.
3. Run `scripts/selftest.sh` for script, configuration, or agent-guidance
   changes.
4. Search active guidance for contradictory ownership, identity, pin,
   resilience, transport, qualification-scope, smoke, and causal-invalidation claims; allow old language only in immutable
   historical evidence with a current supersession pointer.
5. Privacy-scan publishable documentation and results for site-specific paths,
   hosts, addresses, node IDs, and topology identifiers.
6. Follow `docs/REVALIDATE.md` for serving, storage, or promotion changes and
   state plainly which physical gates were or were not run.

In the handoff, distinguish catalog/artifact, serving-integration, model-qualification, and release/promotion conclusions; distinguish accepted behavior from proposals; identify any approved deviation and its ADR; list verification performed; and disclose
remaining implementation or hardware-evidence gaps.
