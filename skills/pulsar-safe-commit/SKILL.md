---
name: pulsar-safe-commit
description: Review, test, privacy-scan, stage, and commit changes in the Pulsar GB10 vLLM Stack. Use whenever a user asks Codex to commit, create a Git commit, stage work for commit, or verify that a Pulsar commit is safe. Protects publishable documentation and evidence from hostnames, IP addresses, SSH identity, durable node or topology identity, user paths, and credential material.
---

# Pulsar Safe Commit

Create one reviewable commit without leaking site identity or including
unrelated work. This workflow does not push.

## 1. Establish scope

1. Read `AGENTS.md` and every skill required by the changed subsystem.
2. Run `git status --short --branch`.
3. Identify the exact tracked and untracked paths authorized by the user.
4. Treat existing untracked or unrelated changes as user-owned. Never stage
   them merely because they are present.
5. Review destructive deletions and generated evidence explicitly.

Do not broaden a request to commit into a push, merge, release, or history
rewrite.

## 2. Check the working tree before staging

Run:

```text
git diff --check
PYTHONDONTWRITEBYTECODE=1 python3 scripts/check_publishable_privacy.py
```

The privacy scanner includes tracked and nonignored untracked publishable
files. A finding must be removed, redacted, or moved to protected/ignored
storage. Never weaken a rule or add an allowlist merely to make a commit pass.

Run affected tests. For scripts, configuration, hooks, or agent guidance, run
the full `scripts/selftest.sh` required by `AGENTS.md`. Report unrelated
environment failures precisely; do not attribute them to the change.

## 3. Stage only intended paths

Prefer explicit paths. Use `git add -u` only when the user authorized every
tracked modification and deletion. Do not use `git add .` in a dirty
worktree.

After staging, run:

```text
git diff --cached --check
PYTHONDONTWRITEBYTECODE=1 \
  python3 scripts/check_publishable_privacy.py --staged
git diff --cached --stat
git diff --cached
```

The staged scan reads index blobs, not working-tree files. This is required
for partially staged changes.

Stop if the staged set differs from the user's requested scope, contains a
privacy finding, or relies on an unexplained failing test.

## 4. Commit

Choose a concise message describing the actual change. Create the commit only
after the staged review and required tests pass.

Afterward:

```text
git log -1 --oneline --decorate
git status --short --branch
```

Report the commit hash, message, validation, any unrelated failure, ahead/behind
state, and remaining untracked files. Do not push unless the user separately
asks.

## Hook boundary

The tracked `.githooks/pre-commit` runs the staged diff and privacy gates.
Check installation with:

```text
git config --local --get core.hooksPath
```

If it is not exactly `.githooks`, tell the user and offer:

```text
git config --local core.hooksPath .githooks
```

Do not change Git configuration without confirmation. A local hook can be
missing or bypassed, so this skill always runs the staged scanner itself.
Never use `--no-verify` to bypass a finding. The full selftest remains the
shared CI/publication enforcement path.
