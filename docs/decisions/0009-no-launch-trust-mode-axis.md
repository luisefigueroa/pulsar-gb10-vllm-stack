# ADR 0009: No launch-trust-mode axis

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decides:** [SWI-728](https://linear.app/swiftsource/issue/SWI-728)
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0008](./0008-breaking-compatibility-window.md),
  [ADR 0010](./0010-operator-consumes-catalog.md)

## Context

SWI-728 proposed that operators explicitly choose a **reviewed/qualified** or
**unreviewed catalog** launch mode so that catalog delivery or Docker Compose
could not silently imply that a model is reviewed or qualified.

Pulsar already labels each serving profile at selection and launch: legacy
`STATUS=tested*`, reviewed expected-seal / identity-match state, and an
optional ADR 0004 Model Serving Release projection. Those labels are not
synonyms. Validation status is advisory and does not grant or deny serving
(ADR 0004). One operator path (`./pulsar`, the wizard, `scripts/up.sh`) starts
any structurally runnable serving profile that fits confirmed capacity.

A second choose-a-mode step would restate those labels. It would not change
admission, and it would invite a false synonym for `Validated`, seal identity,
and `STATUS=tested*`.

## Decision

1. **No launch-trust-mode axis.** Do not add a reviewed/unreviewed mode flag,
   wizard prompt, or launch-time declaration. Existing labels are the trust
   contract. The operator reads them and starts, or does not.
2. **Do not invent a fourth operator-facing trust name.** Seal identity,
   `STATUS=tested*`, and ADR 0004 projection stay distinct.
3. **Catalog delivery is not qualification.** A durable home, source-attested
   receipt, or catalog row does not make a Model Serving Release `Validated`.
4. **Compose is not a trust mode.** Root `docker-compose.yml` was not an
   operator-facing launch path and did not inherit profile labels.
   [ADR 0010](./0010-operator-consumes-catalog.md) removes that file. A later
   Compose experiment would still not be `./pulsar`.
5. **Admission is unchanged.** Operational checks fail without fallback
   (identity, recipe, topology, capacity, security, ownership, lifecycle).
   Status still does not grant or deny serving.
6. **Low-level CLIs stay.** `serve.sh`, `scripts/*.sh`, and `cluster/*` remain
   documented low-level entry points. This ADR does not hide them. ADR 0008's
   deferral of that public-contract question to SWI-728 is closed without
   hiding those CLIs.

Promotion from an unreviewed or unbound profile to a reviewed Model Serving
Release remains ADR 0004 issuance and a reviewed `MODEL_SERVING_RELEASE_ID`
binding after repository review and merge. It is not a launch-mode switch.

## Consequences

- [SWI-729](https://linear.app/swiftsource/issue/SWI-729) (explicit unreviewed
  catalog mode UI) is not implemented. Unreviewed launches already exist as
  labeled profiles on the one operator path.
- Compose deletion is [ADR 0010](./0010-operator-consumes-catalog.md) /
  [SWI-730](https://linear.app/swiftsource/issue/SWI-730). [SWI-752](https://linear.app/swiftsource/issue/SWI-752)
  DSpark overlay remains separate.
- Follow-on work must not add a `--trust-mode` (or equivalent) flag.

## Revisit triggers

Revisit if a launch path has no profile labels (no `models/*.conf`), or if a
new Compose file is presented as equivalent to `./pulsar start`.
