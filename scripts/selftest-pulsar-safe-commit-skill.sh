#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
skill="$REPO_DIR/skills/pulsar-safe-commit/SKILL.md"
agent_yaml="$REPO_DIR/skills/pulsar-safe-commit/agents/openai.yaml"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[[ -f $skill ]] || fail "missing safe-commit skill"
[[ -f $agent_yaml ]] || fail "missing safe-commit agents metadata"
[[ ! -e $REPO_DIR/skills/pulsar-safe-commit/README.md ]] \
  || fail "skill package must not add a README"
(( $(wc -l <"$skill") < 500 )) || fail "safe-commit SKILL.md is too long"

grep -Fq 'name: pulsar-safe-commit' "$skill" \
  || fail "skill frontmatter name is wrong"
grep -Fq 'asks Codex to commit' "$skill" \
  || fail "description must trigger on commit requests"
grep -Fq 'display_name: "Pulsar Safe Commit"' "$agent_yaml" \
  || fail "generated display name is missing"
grep -Fq 'git status --short --branch' "$skill" \
  || fail "skill must inspect worktree state"
grep -Fq 'scripts/check_publishable_privacy.py' "$skill" \
  || fail "skill must run working-tree privacy scan"
grep -Fq 'scripts/check_publishable_privacy.py --staged' "$skill" \
  || fail "skill must scan staged blobs"
grep -Fq 'git diff --cached --check' "$skill" \
  || fail "skill must check staged whitespace"
grep -Fq 'scripts/selftest.sh' "$skill" \
  || fail "skill must honor the full test gate"
grep -Fq 'Never stage' "$skill" \
  || fail "skill must preserve unrelated user work"
grep -Fq 'Do not push' "$skill" \
  || fail "skill must not infer push authority"
grep -Fq 'Never use `--no-verify`' "$skill" \
  || fail "skill must forbid hook bypass"
grep -Fq 'Do not change Git configuration without confirmation' "$skill" \
  || fail "skill must not silently install hooks"

echo "OK   pulsar-safe-commit skill package contracts"
