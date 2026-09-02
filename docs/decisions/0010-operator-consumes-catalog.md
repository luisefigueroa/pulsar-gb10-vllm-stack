# ADR 0010: Operator Pulsar consumes the catalog

- **Status:** Accepted
- **Date:** 2026-08-22
- **Decides:** [SWI-730](https://linear.app/swiftsource/issue/SWI-730)
  (Compose removal). Records the current operator vs maintainer split.
- **Canonical design:** [MODEL_LIBRARY_DESIGN.md](../MODEL_LIBRARY_DESIGN.md)
- **Related decisions:**
  [ADR 0004](./0004-model-serving-release-validation.md),
  [ADR 0009](./0009-no-launch-trust-mode-axis.md)

> **Superseded in part by [ADR 0017](./0017-release-spec-is-the-release-contract.md) (2026-09-02).** Decision 1's consume-surface of `models/*.conf` no longer applies after ADR 0017 Stage 3; the rest of this ADR stands.

## Context

Pulsar has two jobs that can be confused:

1. **Consume** labeled profiles already in `models/` through `./pulsar`.
2. **Craft** a new recipe: draft profile, image pin, vLLM flags, acquire,
   measure, record unreviewed ADR 0004 candidates.

The second job already has maintainer tooling (`skills/pulsar-model-onboarding/`,
`docs/MODEL_RELEASE.md`, `scripts/model-serving-release-*.sh`). It is not
routed through `./pulsar` or the wizard. Root `docker-compose.yml` was a
generic vLLM sketch. It did not load a profile, did not record measurements,
and was not invoked by Pulsar tools. Keeping it at the operator root looked
like a serving path.

## Decision

1. **For now, operator-facing Pulsar consumes the catalog.** `./pulsar`, the
   wizard, `start` / `stop` / `status`, and `scripts/up.sh` serve existing
   `models/*.conf` profiles. They do not grow a recipe-authoring UX.
2. **Recipe craft and onboarding stay maintainer tooling.** Draft profile PRs,
   source-attested `home add`, `validate/*`, and unreviewed release
   plan/capture/issuance remain that path. Promotion is still repository
   review and merge (ADR 0004). This split can be revisited; it is not a
   permanent ban on operator-facing craft later.
3. **Root `docker-compose.yml` is removed.** It did not assist operators or
   maintainers. Git history keeps the sketch. A new Compose file may be added
   later only if a real experiment needs it; it is not an operator launch
   path (ADR 0009).

Low-level `serve.sh` / `cluster/*` stay.

## Consequences

- Warning-only Compose tests and docs whose sole job was the root file go
  away with it.
- Do not add `docker compose up` to operator guidance.
- SWI-730 is this deletion. SIM-10's remaining work is the DSpark overlay.

## Revisit triggers

Revisit if operator-facing recipe craft becomes a product, or if a new
Compose file is added as a real experiment (it still must not be `./pulsar`).
