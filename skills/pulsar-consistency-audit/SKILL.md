---
name: pulsar-consistency-audit
description: Audit the Pulsar GB10 vLLM Stack for discrepancies among accepted architecture, implementation, operator interfaces, tests, documentation, and validation evidence. Use for comprehensive repository consistency reviews, doctrine-to-code audits, documentation-drift investigations, claim-to-evidence checks, or prioritized gap assessments. This skill is read-only unless the user separately authorizes remediation.
---

# Audit Pulsar Consistency

Produce an evidence-backed, prioritized consistency audit. Treat the audit as
read-only: identify and recommend changes, but do not edit files, mutate
site-local state, operate containers or model data, create issues, or publish a
branch unless the user separately authorizes that follow-up work.

## Establish authority and scope

1. Work from the repository root and read `AGENTS.md` completely.
2. Record the current branch, commit, comparison base, worktree status, and
   whether the audit covers committed state, local changes, or both. Never
   describe unpublished local work as released repository behavior.
3. Inventory sources by role: normative architecture/ADRs, descriptive
   current-system specifications, operator procedures, validation ledgers,
   and immutable historical evidence.
4. Load [references/audit-checklist.md](references/audit-checklist.md).
5. When model-library behavior is in scope, also use
   `skills/change-pulsar-model-library/SKILL.md` and read the authority it
   requires. Apply other repository skills when their trigger conditions match.

Use the authority order in `AGENTS.md`. Code is evidence of implementation,
not a silent amendment to accepted architecture. Target design is not proof of
current behavior. Historical or superseded evidence is not a current contract.

## Audit end to end

Trace representative workflows through this chain:

```text
doctrine/ADR -> specification -> implementation -> operator surface
             -> automated test -> physical evidence -> published status
```

Review claims in both directions: documentation may overstate or omit code,
and code, tests, output, or evidence may conflict with accepted policy or with
each other. Compare public command arguments, defaults, exit semantics, human
output, JSON contracts, confirmations, remediation text, and interactive versus
direct-CLI policy behavior.

Build a coverage ledger for every required area with one disposition:

- reviewed with no discrepancy;
- reviewed with findings;
- intentionally out of scope; or
- unable to verify.

Silence must never imply coverage.

## Control false positives

Before recording a discrepancy, check whether the apparent conflict:

- distinguishes accepted target architecture from current implementation;
- is explicitly historical or superseded;
- is generated from another authoritative source;
- is constrained by a visible experimental gate;
- was changed by a later ADR;
- is an intentional and honestly disclosed implementation gap; or
- is wording-only with no behavioral or operator consequence.

Classify every candidate as exactly one of:

- `confirmed-discrepancy`;
- `likely-discrepancy` requiring unavailable or physical verification;
- `intentional-gap`;
- `missing-evidence`; or
- `improvement-opportunity`.

Missing proof is not proof of a defect. Deduplicate findings by root cause:
report one primary finding with all affected surfaces when one defect creates
several downstream inconsistencies.

## Prioritize findings

Assign priority from operational impact, exposure, and confidence:

- **P0 Critical:** credible destructive data loss, security compromise,
  corrupted trusted identity, or unsafe operation without an effective guard.
- **P1 High:** wrong model/revision/source, settled invariant violation, silent
  fallback, unsafe removal, false serving/qualification/promotion claim, or a
  blocked supported primary workflow.
- **P2 Medium:** materially misleading operator information, recoverable
  workflow failure, meaningful default-versus-direct-CLI drift, important
  missing enforcement with compensating controls, or an important untested
  branch.
- **P3 Low:** minor documentation, terminology, usability, or maintainability
  discrepancy without material safety, identity, lifecycle, or serving impact.

Raise priority for guided/default and supported CLI paths. Lower exposure—not
confidence—for explicitly experimental, internal/unreachable, or historical
surfaces. Do not assign defect priority to improvement proposals.

## Preserve independent judgment

Apply accepted decisions when judging current behavior, but surface a credible
better architecture, boundary, or policy when evidence supports it. Label it an
`improvement-opportunity`, identify the accepted decision it challenges, and
describe benefits, tradeoffs, failure modes, and the smallest safe experiment.
Do not reinterpret settled policy or recommend acting on a conflicting proposal
as though it were accepted; implementation requires explicit approval and the
applicable ADR changes.

## Report evidence and limitations

Load [references/report-template.md](references/report-template.md) and follow
it. Every discrepancy needs exact file and line evidence, authoritative
expectation, observed behavior, impact, failure scenario, resolution, closure
validation, and affected code/docs/tests/evidence/ADR surfaces.

For tested, supported, validated, or promoted model claims, compare the exact
revision, expected seal and manifest, image digest, normalized configuration,
geometry, relevant runtime flags, evidence date, and supersession status.

Report limitations instead of mutating state when verification would require
topology/catalog refresh, container lifecycle actions, model transfer or
deletion, unavailable hardware, credentials, or private unsanitized evidence.
Conclude with small reviewable remediation units, but keep implementation,
Linear issue creation, commits, and pull requests as separately authorized work.
