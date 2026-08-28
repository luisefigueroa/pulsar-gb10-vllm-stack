#!/usr/bin/env bash
# Verify one canonical public skill tree and harness-native projections.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
canonical_root="$REPO_DIR/skills"
agents_root="$REPO_DIR/.agents/skills"
claude_root="$REPO_DIR/.claude/skills"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

[ -L "$agents_root" ] || fail ".agents/skills must be a symlink"
[ "$(readlink "$agents_root")" = "../skills" ] \
  || fail ".agents/skills must point to ../skills"
[ "$(realpath "$agents_root")" = "$(realpath "$canonical_root")" ] \
  || fail ".agents/skills does not resolve to canonical skills/"

[ -d "$claude_root" ] && [ ! -L "$claude_root" ] \
  || fail ".claude/skills must be a real projection directory"

mapfile -t canonical_names < <(
  find "$canonical_root" -mindepth 2 -maxdepth 2 -type f -name SKILL.md \
    -printf '%h\n' | xargs -r -n1 basename | sort
)
[ "${#canonical_names[@]}" -gt 0 ] || fail "no canonical public skills found"

mapfile -t claude_names < <(
  find "$claude_root" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort
)
[ "${canonical_names[*]}" = "${claude_names[*]}" ] \
  || fail "Claude projections do not exactly match canonical public skills"

for skill_name in "${canonical_names[@]}"; do
  projection="$claude_root/$skill_name"
  [ -L "$projection" ] || fail "Claude projection is not a symlink: $skill_name"
  [ "$(readlink "$projection")" = "../../skills/$skill_name" ] \
    || fail "Claude projection has the wrong target: $skill_name"
  [ "$(realpath "$projection")" = "$(realpath "$canonical_root/$skill_name")" ] \
    || fail "Claude projection escapes canonical skills: $skill_name"
done

[ ! -e "$canonical_root/grok-subagent/SKILL.md" ] \
  || fail "private grok-subagent must not be published in canonical skills"
! grep -Fq 'skills/grok-subagent' "$REPO_DIR/AGENTS.md" \
  || fail "AGENTS.md must not reference the private Grok skill path"

for projection_root in .cursor/skills .codex/skills .grok/skills .hermes/skills; do
  if git -C "$REPO_DIR" ls-files -- "$projection_root" | grep -q .; then
    fail "duplicate tracked skill projection exists under $projection_root"
  fi
done

git -C "$REPO_DIR" check-ignore -q \
  skills/pulsar-model-onboarding/scripts/__pycache__/fixture.py \
  || fail "skill-package Python caches must remain ignored"

echo "OK   canonical public skills and harness projections"
