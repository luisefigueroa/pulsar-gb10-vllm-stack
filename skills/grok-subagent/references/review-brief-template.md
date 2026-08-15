# Independent pre-implementation review brief

Fill every applicable section. Remove placeholders and irrelevant sections
before sending the brief to Grok.

```text
Act as an independent, read-only implementation reviewer for the sanitized
review tree at <sanitized-review-tree>. Inspect only that tree; do not assume
the facts or proposed direction below are correct. Do not read gitignored
site-local files such as `.env`, `.cluster-topology.json`,
`.cluster-ssh-config`, `.weight-fabric/`, or `.model-library/`, and do not
search outside the provided tree. Do not edit files, create branches,
mutate Git or GitHub state, operate infrastructure, or make external changes.
Return a concrete final report, not progress notes.

Delegated task

<Describe the exact work requested and why Grok is reviewing it.>

Goal and acceptance outcome

<Describe the user-visible or system outcome and how completion will be judged.>

Repository authority

- <AGENTS.md rule or other controlling policy>
- <Applicable ADR/design/specification/runbook>
- <Relevant implementation and test contracts>

Known context and evidence

- <Current behavior with exact file/function/line evidence>
- <Observed failure, discrepancy, or requirement>
- <Relevant branch, diff, test, or operational state>
- <Facts already verified independently>

Tentative implementation direction to challenge

<Describe the smallest proposed change, affected files, and expected tests.
This is a proposal, not a conclusion. Identify assumptions and uncertainties.>

Constraints and non-goals

- <Safety, compatibility, lifecycle, privacy, and no-fallback constraints>
- <Explicitly excluded subsystems or follow-up work>
- <Actions requiring user approval>

Questions for independent review

1. Does repository evidence support the stated problem and goal?
2. Does anything contradict the tentative implementation direction?
3. What risks, failure cases, compatibility concerns, or authority conflicts
   are missing?
4. Is there a smaller or safer complete approach?
5. Which tests, documentation, evidence, or physical validation are required?
6. Which decisions require user approval before implementation?

Required final report

1. Restate the task and the evidence you independently inspected.
2. State agreement or disagreement with the problem and proposed direction.
3. List corrections, additional risks, and authority conflicts with exact
   repository evidence where possible.
4. Recommend the smallest complete implementation approach and affected files.
5. Provide a focused validation plan and residual validation gaps.
6. Clearly identify alternative ideas, policy deviations, or decisions that
   require approval.

Do not implement the change. If evidence is unavailable or contradictory, say
what cannot be established and recommend whether implementation should stop.
```
