---
name: grok-subagent
description: Use the local Grok CLI for an independent read-only implementation review, reconcile its findings with repository evidence, obtain user approval, and optionally delegate the approved implementation to Grok in a privacy-cleared feature worktree before focused verification and publication. Use when a user asks for a Grok subagent, Grok review or second opinion, the Grok 4.6/xhigh workflow, or Grok to implement an agreed change after review.
---

# Use Grok for Review and Approved Implementation

Use Grok to challenge the proposed implementation before it edits anything.
During review Grok is advisory; after independent reconciliation and explicit
user approval, Grok may become the delegated implementer for exactly the agreed
unit. Repository authority and verified evidence remain controlling.

## Establish the review boundary

1. Read `AGENTS.md`, every applicable repository skill, and the authoritative
   design, decision, specification, and runbook sources before drafting the
   brief.
2. Inspect the relevant code, tests, current diff, branch, and worktree state.
   Separate verified facts, assumptions, and open questions.
3. Define the delegated task, desired outcome, non-goals, safety constraints,
   tentative direction, and validation needs.
4. Keep the first Grok pass read-only. Do not let it edit files, mutate Git or
   GitHub state, operate infrastructure, or change external state.
5. Remove secrets, credentials, private topology, stable site identifiers, and
   unnecessary proprietary data. Never point the review pass at the live
   worktree: gitignored files remain readable there unless a kernel sandbox
   prevents it.

Encourage useful alternatives, including conflicts with the tentative
direction. Require Grok to label conflicts with accepted repository decisions.
Do not implement a novel proposal as settled policy without the required user
approval and authority update.

## Verify the local CLI

Before every review, run `grok --version`, `grok --help`, and `grok models`.
This workflow is verified for Grok Build 1.0.4 or later. Confirm that the local
CLI supports headless prompts, client-selected session IDs, resume, model and
reasoning selection, strict sandboxing, tool filters, and turn limits. Stop on
an older or materially different CLI rather than silently weakening a guard.

Use exactly `grok-4.6` with `xhigh` reasoning unless the user authorizes another
model. Do not silently substitute the default or a newer model. If that model
is unavailable or authentication fails, report the blocker.

`/release-notes` and `/changelog` are interactive slash commands. Under
`grok -p` they are sent to the model as ordinary prompt text. For automated
checks, use `grok --version`, the installed `~/.grok/CHANGELOG.md`, and the
official changelog as a fallback.

## Run the read-only review

Create a temporary tree of tracked worktree files only, outside the live
repository:

```bash
review_tree=$(scripts/prepare-grok-review-tree.sh --print-dest)
```

If the helper cannot build a clean tree, stop. Verify that `.git`, `.env`,
`.cluster-topology.json`, `.cluster-ssh-config`, `.weight-fabric/`,
`.model-library/`, `experiments/`, raw results, and other gitignored site state
are absent. Do not pass the live repository path or unrelated credentials into
the Grok process. Preserve only the authentication mechanism Grok itself needs.

Fill [references/review-brief-template.md](references/review-brief-template.md)
and save the completed brief in a temporary file outside the repository. Use a
known session UUID so recovery never depends on “most recent” session lookup:

```bash
grok_review_session_id=$(uuidgen | tr '[:upper:]' '[:lower:]')

env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
    -u VLLM_API_KEY -u API_KEY -u OPENAI_API_KEY \
    -u GITHUB_TOKEN -u GH_TOKEN \
  grok --cwd "$review_tree" \
    --session-id "$grok_review_session_id" \
    --model grok-4.6 \
    --reasoning-effort xhigh \
    --permission-mode plan \
    --sandbox strict \
    --tools "read_file,grep,list_dir" \
    --deny MCPTool \
    --disable-web-search \
    --no-memory \
    --no-subagents \
    --max-turns 20 \
    --output-format plain \
    --prompt-file "$review_prompt_file"
```

`strict` is the important privacy boundary: `--cwd` alone does not restrict
filesystem reads. If Grok cannot initialize its own state or enforce the
sandbox, fix that condition with the required approval or stop. Never retry a
repository review by silently dropping the sandbox. The explicit read-only
tool set and MCP/web restrictions remain necessary even with the sandbox.

Require a concrete final report. If the run stops at progress, resume the exact
session for one tool-free synthesis turn using the same model, reasoning,
sandbox, memory, and web settings:

```bash
grok --cwd "$review_tree" \
  --resume "$grok_review_session_id" \
  --model grok-4.6 \
  --reasoning-effort xhigh \
  --permission-mode plan \
  --sandbox strict \
  --tools "" \
  --deny MCPTool \
  --disable-web-search \
  --no-memory \
  --no-subagents \
  --max-turns 1 \
  --output-format plain \
  -p "Synthesize the evidence already collected and return the required final report now. Make no tool calls."
```

Grok Build 1.0.4 can expose an incomplete end through a `StopCancelled` hook,
including `max_turns`, but that event is diagnostic and does not synthesize the
missing report. If direct output is truncated, recover it with
`grok export "$grok_review_session_id"`. Do not treat progress-only or
cancelled output as agreement.

## Reconcile and obtain approval

After Grok reports:

1. Verify material factual claims against the repository and authoritative
   sources. Reproduce important failure claims when safe.
2. Classify each recommendation as supported, unsupported, already covered,
   ambiguous, or conflicting with accepted policy.
3. Form the smallest complete implementation approach. Do not copy Grok's
   conclusions without independent review.
4. Present Grok's conclusions, the evidence used to accept or reject them, the
   reconciled scope and tests, alternatives needing a decision, and residual
   physical or external validation gaps.
5. Explicitly ask the user to approve or revise the reconciled approach. Do not
   edit repository files before that approval.

This pause is mandatory even when Grok agrees with the tentative direction.

## Delegate the approved implementation

After approval, let Grok implement directly when that saves time. Do not make
the primary agent retype or manually import a patch by default.

Use a clean, dedicated feature worktree at the exact reviewed base. Before
exposing it to Grok, run:

```bash
skills/grok-subagent/scripts/preflight-implementation-worktree.sh \
  --repo-root "$implementation_worktree" \
  --expected-head "$reviewed_head"
```

The preflight requires a non-default branch, the reviewed commit, and no
tracked changes, untracked files, or ignored state. If it fails, do not delete
or move user data to make it pass. Create a fresh dedicated worktree or return
to the user if that would change scope. Once it passes, Grok may edit that
worktree directly and run the approved local checks.

Fill
[references/implementation-brief-template.md](references/implementation-brief-template.md)
with the reconciled plan. Start a named implementation session; a concise
handoff is preferable to weakening worktree isolation merely to reuse review
context:

```bash
grok_implementation_session_id=$(uuidgen | tr '[:upper:]' '[:lower:]')

env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
    -u VLLM_API_KEY -u API_KEY -u OPENAI_API_KEY \
    -u GITHUB_TOKEN -u GH_TOKEN \
  grok --cwd "$implementation_worktree" \
    --session-id "$grok_implementation_session_id" \
    --model grok-4.6 \
    --reasoning-effort xhigh \
    --permission-mode auto \
    --sandbox strict \
    --deny MCPTool \
    --deny "Bash(git push*)" \
    --deny "Bash(gh*)" \
    --deny "Bash(sudo*)" \
    --deny "Bash(ssh*)" \
    --deny "Bash(docker*)" \
    --deny "Bash(podman*)" \
    --disable-web-search \
    --no-memory \
    --max-turns 40 \
    --output-format plain \
    --prompt-file "$implementation_prompt_file"
```

This phase intentionally allows normal read, edit, shell, test, formatting,
and bounded subagent behavior inside the cleared worktree. Let Grok make
ordinary coding decisions and iterate on test failures without returning for
approval. Add `--no-subagents` only when parallel work would not help or the
user excludes it.

Remove a restriction only when the approved task actually needs that
capability. Public documentation lookup, dependency download, containers,
remote systems, privileged commands, and physical hardware are not implied by
permission to edit. Disclose and obtain any additional authority required by
the repository or user. Material scope expansion, a policy deviation, a
destructive action, or an external side effect returns to the approval
boundary.

## Verify and publish efficiently

After Grok finishes, the primary agent must inspect the complete diff, confirm
that only approved files changed, review security- and policy-sensitive logic,
and rerun the authoritative checks appropriate to risk. Do not duplicate the
implementation merely to prove independence. Fix identified issues directly
or return them to Grok when another iteration is more efficient.

A fresh Grok diff review is optional and risk-based, not mandatory. The primary
agent retains responsibility for final repository compliance and handles
commit, push, PR creation, and review responses under the repository's normal
publication policy.

In the final handoff, distinguish Grok's review advice, Grok-authored changes,
and independently verified evidence. State whether the delivered change still
matches the approved approach and identify any physical or external validation
that remains.

Delete temporary review trees and prompt files after use. Do not commit Grok
transcripts unless the user explicitly requests a separately reviewed and
sanitized artifact.
