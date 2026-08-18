---
name: grok-subagent
description: Use the local Grok CLI for an independent read-only implementation review, reconcile its findings with repository evidence, obtain user approval, and optionally continue the same Grok session in the same privacy-cleared feature worktree for approved implementation before focused verification and publication. Use when a user asks for a Grok subagent, Grok review or second opinion, the Grok 4.6/xhigh workflow, or Grok to implement an agreed change after review.
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
   unnecessary proprietary data. Never point Grok at an existing operator
   worktree. Use either a tracked-files-only review tree or a fresh dedicated
   feature worktree that passes the privacy preflight with no tracked,
   untracked, or ignored state.

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

## Choose one privacy-cleared review root

For a review-only request, create a temporary tree of tracked worktree files
outside the live repository:

```bash
review_root=$(scripts/prepare-grok-review-tree.sh --print-dest)
```

If the helper cannot build a clean tree, stop. Verify that `.git`, `.env`,
`.cluster-topology.json`, `.cluster-ssh-config`, `.weight-fabric/`,
`.model-library/`, `experiments/`, raw results, and other gitignored site state
are absent.

When the user expects Grok to implement after review, prefer one fresh
dedicated feature worktree for both phases. Record the exact reviewed commit
and original remote PR branch, create a temporary local feature branch from
that commit, and run the preflight **before review**:

```bash
reviewed_head=$(git rev-parse HEAD)
original_branch=$(git branch --show-current)
case "$original_branch" in
  main|master) original_remote_branch="" ;;
  *) original_remote_branch="$original_branch" ;;
esac
git worktree add -b "$grok_work_branch" "$shared_worktree" "$reviewed_head"
skills/grok-subagent/scripts/preflight-implementation-worktree.sh \
  --repo-root "$shared_worktree" \
  --expected-head "$reviewed_head"
review_root="$shared_worktree"
```

Do not check out the original local branch in two worktrees. The temporary
branch exists only to host review, implementation, verification, and a commit.
Do not pass an existing operator worktree or unrelated credentials into Grok.
Preserve only the authentication mechanism Grok itself needs.

## Run the read-only review

Fill [references/review-brief-template.md](references/review-brief-template.md)
and save the completed brief in a temporary file outside the repository. Use a
known session UUID so recovery never depends on “most recent” session lookup:

```bash
grok_review_session_id=$(uuidgen | tr '[:upper:]' '[:lower:]')

env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
    -u VLLM_API_KEY -u API_KEY -u OPENAI_API_KEY \
    -u GITHUB_TOKEN -u GH_TOKEN \
  grok --cwd "$review_root" \
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
grok --cwd "$review_root" \
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

## Continue the approved implementation

After approval, let Grok implement directly when that saves time. Do not make
the primary agent retype or manually import a patch by default.

When review used the shared worktree, verify immediately before granting write
access that its HEAD still equals the reviewed commit and that the privacy
preflight still passes. A changed head or any tracked, untracked, or ignored
state stops the transition:

```bash
skills/grok-subagent/scripts/preflight-implementation-worktree.sh \
  --repo-root "$shared_worktree" \
  --expected-head "$reviewed_head"
```

Resume the **same review session** in the **same shared worktree** when the
review was bounded, the head and approved scope are unchanged, and preserving
context is useful. The approval pause remains mandatory; only the permission
envelope changes.

Fill
[references/implementation-brief-template.md](references/implementation-brief-template.md)
with the reconciled plan. Resume with the implementation restrictions and no
`--restore-code`; the shared worktree already contains the reviewed code:

```bash
env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN \
    -u VLLM_API_KEY -u API_KEY -u OPENAI_API_KEY \
    -u GITHUB_TOKEN -u GH_TOKEN \
  grok --cwd "$shared_worktree" \
    --resume "$grok_review_session_id" \
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

Use a fresh implementation session with a compact approved brief instead when
the review was long or noisy, hit recovery limits, the head or scope changed,
or implementation was not anticipated and review used a tracked-only tree.
When review already used the shared worktree, keep that same worktree even if
the session is fresh. Otherwise create a dedicated worktree at the exact
approved head. In either case rerun the preflight first. If it fails, do not
delete or move user data to make it pass. Do not reuse context by exposing an
existing operator worktree.

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

When the shared worktree was created from an already checked-out non-default PR
branch and the user authorized updating that PR, commit on the temporary local
branch and push its HEAD directly to the original remote branch:

```bash
git push origin HEAD:"$original_remote_branch"
```

Confirm that the remote branch still has the reviewed ancestry first. Use a
normal push and let a non-fast-forward update fail; never force merely to
preserve the handoff. If the remote advanced, stop, reconcile against the new
head, and rerun affected verification. The original local worktree remains
behind after the push and must later be fast-forwarded only when its tracked
state is clean. When `original_remote_branch` is empty because review began on
`main` or `master`, publish the temporary feature branch through the normal new
PR workflow; never target the default branch with this refspec shortcut.

In the final handoff, distinguish Grok's review advice, Grok-authored changes,
and independently verified evidence. State whether the delivered change still
matches the approved approach and identify any physical or external validation
that remains.

Delete temporary review trees and prompt files after use. Do not commit Grok
transcripts unless the user explicitly requests a separately reviewed and
sanitized artifact.
