# Approved implementation brief

Fill every applicable section after the primary agent reconciles Grok's review
and the user approves the approach. Remove placeholders and irrelevant sections
before sending the brief to Grok.

```text
Act as the delegated implementer for the approved change in your current clean
feature worktree. The independent review is complete, its findings were checked
against repository authority, and the user approved the reconciled plan below.
If this resumes the review session, retain useful code context but treat the
approved plan below as controlling over tentative review alternatives.
Implement that plan directly, run the relevant local checks, and return a
concise handoff. Do not revisit settled policy merely because another design is
possible.

Approved task and outcome

<State the exact approved result and completion criteria.>

Authoritative repository contracts

- <Applicable AGENTS.md rule>
- <Applicable ADR/design/specification/runbook>
- <Relevant implementation and test contracts>

Approved implementation plan

1. <Concrete step and affected files>
2. <Concrete step and affected files>
3. <Tests and documentation required in the same unit>

Allowed autonomy

- Make ordinary code-level decisions within the approved design.
- Read and edit files in the current worktree.
- Run focused tests, formatting, and the repository checks needed for this unit.
- Iterate on ordinary failures and use bounded subagents for genuinely
  independent work when useful.

Hard stops

- Stop for a material scope expansion, policy deviation, destructive action,
  new external side effect, or a decision that would change the approved
  outcome.
- Do not read or expose secrets, private topology, site-local state, or paths
  outside the cleared worktree.
- Do not push, open or merge a PR, use privileged or remote infrastructure, or
  claim physical validation unless the approved plan explicitly grants that
  authority.
- Preserve unrelated work and historical failed or partial evidence.

Validation

- <Focused tests Grok should run>
- <Full or authoritative checks the primary agent will rerun>
- <Physical or external checks explicitly excluded or still pending>

Required handoff

1. Summarize the implemented behavior.
2. List changed files and any intentional compatibility effects.
3. List commands run and their outcomes.
4. Identify unresolved risks, skipped checks, or deviations. If there were no
   deviations, say so explicitly.

Do not stop at a plan or progress update. Implement the approved unit and
return the completed handoff.
```
