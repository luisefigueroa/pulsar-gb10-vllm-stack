#!/usr/bin/env bash
# Skill and helper contracts: Grok must not see the live worktree.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

skill="$REPO_DIR/skills/grok-subagent/SKILL.md"
brief="$REPO_DIR/skills/grok-subagent/references/review-brief-template.md"
helper="$REPO_DIR/scripts/prepare-grok-review-tree.sh"

[ -f "$skill" ] || fail "missing $skill"
[ -f "$brief" ] || fail "missing $brief"
[ -x "$helper" ] || fail "missing executable $helper"

grep -Fq 'scripts/prepare-grok-review-tree.sh' "$skill" \
  || fail "skill must prepare a sanitized review tree via the helper"
grep -Fq '--cwd "$review_tree"' "$skill" \
  || fail "skill must point grok --cwd at the sanitized review tree"
if grep -Fq '--cwd "$review_repo_root"' "$skill"; then
  fail "skill must not launch Grok against the live repository root"
fi
grep -Fq '.env' "$skill" \
  || fail "skill must name .env as excluded from Grok's filesystem"
grep -Fq '.cluster-topology.json' "$skill" \
  || fail "skill must name .cluster-topology.json as excluded from Grok's filesystem"

if grep -Fq 'Inspect the repository yourself' "$brief"; then
  fail "brief must not ask Grok to inspect the live repository"
fi
grep -Fq 'sanitized review tree' "$brief" \
  || fail "brief must inspect only the sanitized review tree"
grep -Fq '.env' "$brief" \
  || fail "brief must forbid reading .env"
grep -Fq '.cluster-topology.json' "$brief" \
  || fail "brief must forbid reading .cluster-topology.json"

fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
repo="$fixture/repo"
dest="$fixture/review-tree"
mkdir -p "$repo"
git -C "$repo" init >/dev/null
git -C "$repo" config user.email "review-tree@example.test"
git -C "$repo" config user.name "Review Tree"
printf '%s\n' '.env' '.cluster-topology.json' >"$repo/.gitignore"
printf 'tracked\n' >"$repo/README.md"
printf 'HF_TOKEN=secret\n' >"$repo/.env"
printf '{"hostname":"lab-node.example"}\n' >"$repo/.cluster-topology.json"
git -C "$repo" add .gitignore README.md >/dev/null

printed=$("$helper" --repo-root "$repo" --dest "$dest" --print-dest)
[ "$(realpath "$printed")" = "$(realpath "$dest")" ] \
  || fail "helper --print-dest did not return the destination"
[ -f "$dest/README.md" ] || fail "helper omitted a tracked file"
[ ! -e "$dest/.env" ] || fail "helper copied .env into the review tree"
[ ! -e "$dest/.cluster-topology.json" ] \
  || fail "helper copied .cluster-topology.json into the review tree"

if "$helper" --repo-root "$repo" --dest "$repo/inside" --print-dest \
  >/dev/null 2>&1; then
  fail "helper must refuse a destination inside the live repository"
fi

real_dest="$fixture/real-review"
"$helper" --repo-root "$REPO_DIR" --dest "$real_dest" --print-dest >/dev/null
[ -f "$real_dest/skills/grok-subagent/SKILL.md" ] \
  || fail "helper omitted the Grok skill from the real repository tree"
[ ! -e "$real_dest/.env" ] || fail "real-repo tree leaked .env"
[ ! -e "$real_dest/.cluster-topology.json" ] \
  || fail "real-repo tree leaked .cluster-topology.json"
[ ! -e "$real_dest/.git" ] || fail "real-repo tree included .git"

echo "grok review-tree isolation selftest OK"
