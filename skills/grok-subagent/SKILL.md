---
name: grok-subagent
description: Use the local Grok CLI as an independent, read-only pre-implementation reviewer, then reconcile its findings with repository evidence and obtain agreement on the final approach before editing. Use when a user asks for a Grok subagent, an independent Grok review, a second opinion before coding, or the Grok 4.6/xhigh review workflow for a delegated implementation task.
---

# Use Grok as a Pre-implementation Reviewer

Use Grok to challenge a proposed implementation, not to ratify it. Grok is an
advisory reviewer; the user, repository authority, and verified evidence remain
controlling.

## Establish the review boundary

1. Read `AGENTS.md` and every applicable repository skill and authoritative
   design, decision, specification, or runbook before drafting the brief.
2. Inspect the relevant code, tests, current diff, and worktree state. Separate
   verified facts, assumptions, and open questions.
3. Define the delegated task, desired outcome, non-goals, safety constraints,
   proposed implementation direction, and validation needs.
4. Keep Grok's pass read-only. Do not let it edit files, create a branch,
   mutate Git or GitHub state, operate infrastructure, or change external state.
5. Remove secrets, credentials, private topology, stable site identifiers, and
   unnecessary proprietary data from the brief. Do not send sensitive raw
   artifacts to an external model. Brief redaction is not enough: never give
   Grok the live worktree. Gitignored files such as `.env` and
   `.cluster-topology.json` stay readable there.

Encourage Grok to suggest valuable alternatives, including ideas that conflict
with the tentative direction. Require it to label conflicts with accepted
repository decisions explicitly. A novel proposal may be discussed without
approval, but must not be implemented or documented as accepted without the
required user approval and authority updates.

## Verify the local CLI

Run `grok --help` and `grok models` before every review. Confirm that the local
syntax supports prompt mode, model selection, and reasoning effort.

Use exactly `grok-4.6` with `xhigh` reasoning unless the user authorizes another
model. Do not silently substitute a default or newer model. If Grok 4.6 is
unavailable, authentication fails, or the CLI contract differs materially,
stop and report the blocker.

## Prepare a sanitized review tree

Create a temporary tree of tracked worktree files only, outside the live
repository:

```bash
review_tree=$(scripts/prepare-grok-review-tree.sh --print-dest)
```

If the helper cannot build a clean tree, stop. Do not copy, mount, or otherwise
expose `.env`, `.cluster-topology.json`, `.cluster-ssh-config`,
`.weight-fabric/`, `.model-library/`, raw results, `experiments/`, or any other
gitignored site-local state. Do not pass the live repository path,
`HF_TOKEN`, `VLLM_API_KEY`, `API_KEY`, or other credential variables into the
Grok process.

The current verified invocation shape is:

```bash
grok --cwd "$review_tree" \
  --model grok-4.6 \
  --reasoning-effort xhigh \
  --permission-mode plan \
  --no-memory \
  --no-subagents \
  -p "$review_brief"
```

Adapt exact flags only when the local help requires it, and disclose the
adaptation. Prefer a temporary prompt file plus `--prompt-file` when that is
safer than shell-quoting a long brief; both are single-turn prompt modes in the
current CLI. Never place the prompt, Grok transcript, or sanitized review tree
in the repository unless the user explicitly requests a reviewed, sanitized
artifact. Delete `$review_tree` after the review.

## Prepare a self-contained brief

Read and fill [references/review-brief-template.md](references/review-brief-template.md).
Give Grok enough context to investigate independently without steering it to a
rubber-stamp conclusion. Include the tentative implementation direction, but
identify it as a proposal to challenge.

Require a concrete final report, not progress notes. If the response stops at
progress, continue the same Grok session and ask for the requested final report.
Do not treat an incomplete run as agreement.

## Reconcile the findings independently

After Grok reports:

1. Verify its factual claims directly against the repository and authoritative
   sources. Reproduce important failure claims when safe.
2. Classify each recommendation as supported, unsupported, already covered,
   ambiguous, or a proposal that conflicts with accepted policy.
3. Identify where Grok changed the understanding of scope, risk, or validation.
4. Form the smallest complete implementation approach that satisfies the task
   and repository contracts. Do not copy Grok's patch or conclusions without
   independent review.

## Report and pause before implementation

Present a concise pre-implementation report containing:

- the task and goal as understood;
- Grok's conclusion, disagreements, and additional risks;
- the repository evidence used to accept or reject its findings;
- the reconciled implementation approach, scope, and tests;
- alternatives or policy deviations requiring a decision; and
- residual physical or external validation needs.

Explicitly ask the user to approve or revise the reconciled approach. Do not
edit repository files in the review phase, even when Grok agrees with the
initial direction. This pause is the agreement boundary the skill exists to
provide.

## Execute only the agreed approach

After approval, implement the agreed unit using the repository's normal skills,
safety rules, tests, and publication policy. If implementation reveals a
materially different decision, policy deviation, destructive action, or scope
expansion, stop and return to the review-and-agreement boundary.

In the final handoff, distinguish Grok's advice from independently verified
evidence and state whether the delivered change matches the approved approach.
