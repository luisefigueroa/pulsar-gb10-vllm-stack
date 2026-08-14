# Pulsar consistency audit report template

## Contents

1. Executive summary
2. Coverage ledger
3. Prioritized findings
4. Finding details
5. Cross-cutting summaries
6. Remediation plan
7. Conclusion

## Executive summary

State:

- overall consistency assessment;
- repository branch, commit, base, worktree state, and audited scope;
- finding counts by priority and disposition;
- three most important risks;
- whether a supported/default workflow is unsafe or inaccurately documented;
- whether model-library implementation matches accepted architecture;
- commands/checks executed; and
- important areas not verified.

If no P0 or P1 findings exist, say so explicitly.

## Coverage ledger

| Area | Disposition | Sources reviewed | Limitations |
|---|---|---|---|
| Example | reviewed with findings | files/commands | none |

Use only: `reviewed with no discrepancy`, `reviewed with findings`,
`intentionally out of scope`, or `unable to verify`.

## Prioritized findings

Order strictly by P0, P1, P2, then P3; within a priority, order by impact,
operator exposure, and confidence.

| ID | Priority | Confidence | Disposition | Category | Subsystem | Finding | Impact | Primary evidence |
|---|---|---|---|---|---|---|---|---|

Use stable IDs. Use clickable absolute file links with line numbers. Keep
improvement opportunities outside the defect-priority table.

## Finding details

For every finding include:

- **ID and title**
- **Priority, confidence, and disposition**
- **Category and subsystem**
- **Exposure:** guided/default, supported CLI, experimental, internal, or
  historical
- **Authoritative expectation**
- **Observed behavior**
- **Evidence:** exact files/lines and relevant command output
- **Impact and concrete failure scenario**
- **Root cause and related/duplicate symptoms**
- **Recommended resolution**
- **Surfaces to change:** code, docs, tests, evidence, and/or ADR
- **Validation required to close**
- **Dependencies and related findings**

## Cross-cutting summaries

Include separate sections for:

1. **Intentional implementation gaps** — accepted but unimplemented behavior,
   current disclosure, and whether guardrails are sufficient.
2. **Documentation-only discrepancies** — stale, incomplete, contradictory, or
   misclassified guidance.
3. **Code-only or undocumented behavior** — implemented contracts missing from
   canonical docs/runbooks.
4. **Test and evidence gaps** — automation, physical validation, privacy, and
   reproducibility deficiencies.
5. **Terminology consistency** — canonical definition, conflicting use,
   consequence, and proposed correction.
6. **Cross-document authority conflicts** — conflicting sources, precedence,
   and whether an ADR/approval is required.
7. **Improvement opportunities** — valuable new ideas kept distinct from
   existing-contract defects.

## Remediation plan

Group root causes into small reviewable units. For each proposed PR state:

- included finding IDs;
- code/docs/tests/evidence scope;
- acceptance criteria;
- dependencies;
- whether an ADR or product approval is required; and
- whether physical DGX Spark validation is required.

Separate documentation corrections from behavioral changes unless they are one
inseparable contract update. Do not combine unrelated subsystems.

Provide suggested Linear issue titles, scope, acceptance criteria,
dependencies, and priority, but do not create them.

## Conclusion

Name:

- the smallest safe first PR;
- the highest-risk unresolved discrepancy;
- the most consequential missing test;
- the most important documentation correction; and
- any decision required before implementation can proceed.
