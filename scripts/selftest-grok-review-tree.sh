#!/usr/bin/env bash
# Grok skill contracts: isolated review, approved implementation preflight.
# shellcheck disable=SC2016  # Assertions intentionally match literal Markdown shell text.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

skill="$REPO_DIR/skills/grok-subagent/SKILL.md"
brief="$REPO_DIR/skills/grok-subagent/references/review-brief-template.md"
implementation_brief="$REPO_DIR/skills/grok-subagent/references/implementation-brief-template.md"
helper="$REPO_DIR/scripts/prepare-grok-review-tree.sh"
implementation_preflight="$REPO_DIR/skills/grok-subagent/scripts/preflight-implementation-worktree.sh"

[ -f "$skill" ] || fail "missing $skill"
[ -f "$brief" ] || fail "missing $brief"
[ -f "$implementation_brief" ] || fail "missing $implementation_brief"
[ -x "$helper" ] || fail "missing executable $helper"
[ -x "$implementation_preflight" ] \
  || fail "missing executable $implementation_preflight"

grep -Fq 'scripts/prepare-grok-review-tree.sh' "$skill" \
  || fail "skill must retain the tracked-only review option"
grep -Fq -- '--cwd "$review_root"' "$skill" \
  || fail "read-only review must target the privacy-cleared root"
if grep -Fq -- '--cwd "$review_repo_root"' "$skill"; then
  fail "skill must not launch Grok against the live repository root"
fi
grep -Fq '.env' "$skill" \
  || fail "skill must name .env as excluded from Grok's filesystem"
grep -Fq '.cluster-topology.json' "$skill" \
  || fail "skill must name .cluster-topology.json as excluded from Grok's filesystem"
grep -Fq 'grok --version' "$skill" \
  || fail "skill must verify the Grok CLI version"
grep -Fq -- '--sandbox strict' "$skill" \
  || fail "skill must kernel-restrict Grok filesystem access"
grep -Fq -- '--tools "read_file,grep,list_dir"' "$skill" \
  || fail "review must expose only internal read-only tool IDs"
grep -Fq -- '--deny MCPTool' "$skill" \
  || fail "skill must deny MCP tools"
grep -Fq -- '--max-turns 20' "$skill" \
  || fail "review must have a bounded multi-turn budget"
grep -Fq -- '--prompt-file "$review_prompt_file"' "$skill" \
  || fail "review must use a temporary prompt file"
grep -Fq '`grok -p` they are sent to the model as ordinary prompt text' "$skill" \
  || fail "skill must not treat interactive slash commands as headless commands"
grep -Fq 'Explicitly ask the user to approve or revise' "$skill" \
  || fail "skill must preserve the user approval boundary"
grep -Fq 'preflight-implementation-worktree.sh' "$skill" \
  || fail "shared review/implementation must run the privacy preflight"
grep -Fq -- '--cwd "$shared_worktree"' "$skill" \
  || fail "approved implementation must stay in the reviewed worktree"
grep -Fq -- '--resume "$grok_review_session_id"' "$skill" \
  || fail "approved implementation must support the same review session"
grep -Fq 'main|master) original_remote_branch=""' "$skill" \
  || fail "skill must refuse the refspec shortcut for default branches"
grep -Fq 'git push origin HEAD:"$original_remote_branch"' "$skill" \
  || fail "skill must push the temporary HEAD to the original remote branch"
grep -Fq 'never force' "$skill" \
  || fail "skill must forbid force-pushing the handoff"
grep -Fq 'never target the default branch' "$skill" \
  || fail "skill must keep default-branch publication on the normal PR path"
grep -Fq 'Do not make' "$skill" \
  || fail "skill must avoid default manual patch re-entry"

if grep -Fq 'Inspect the repository yourself' "$brief"; then
  fail "brief must not ask Grok to inspect the live repository"
fi
grep -Fq '<privacy-cleared-review-root>' "$brief" \
  || fail "brief must identify the privacy-cleared review root"
grep -Fq 'Inspect only that root' "$brief" \
  || fail "brief must constrain Grok to the cleared root"
grep -Fq '.env' "$brief" \
  || fail "brief must forbid reading .env"
grep -Fq '.cluster-topology.json' "$brief" \
  || fail "brief must forbid reading .cluster-topology.json"

grep -Fq 'The independent review is complete' "$implementation_brief" \
  || fail "implementation brief must start after review"
grep -Fq 'user approved the reconciled plan' "$implementation_brief" \
  || fail "implementation brief must require user approval"
grep -Fq 'Make ordinary code-level decisions' "$implementation_brief" \
  || fail "implementation brief must grant normal coding autonomy"
grep -Fq 'Do not push' "$implementation_brief" \
  || fail "implementation brief must reserve publication authority"

fixture=$(mktemp -d)
trap 'rm -rf -- "$fixture"' EXIT
repo="$fixture/repo"
dest="$fixture/review-tree"
mkdir -p "$repo"
git -C "$repo" init -q -b main
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

implementation_repo="$fixture/implementation-repo"
mkdir -p "$implementation_repo"
git -C "$implementation_repo" init -q -b main
git -C "$implementation_repo" config user.email "implementation@example.test"
git -C "$implementation_repo" config user.name "Implementation Worktree"
printf '%s\n' '.env' >"$implementation_repo/.gitignore"
printf 'tracked\n' >"$implementation_repo/README.md"
git -C "$implementation_repo" add .gitignore README.md
git -C "$implementation_repo" commit -qm 'initial fixture'

if "$implementation_preflight" --repo-root "$implementation_repo" \
  >/dev/null 2>&1; then
  fail "implementation preflight must reject the default branch"
fi

git -C "$implementation_repo" switch -qc feat/grok-test
reviewed_head=$(git -C "$implementation_repo" rev-parse HEAD)
preflight_output=$(
  "$implementation_preflight" \
    --repo-root "$implementation_repo" \
    --expected-head "$reviewed_head"
)
grep -Fq 'Grok shared worktree preflight OK' <<<"$preflight_output" \
  || fail "implementation preflight did not accept a clean feature branch"

printf 'secret\n' >"$implementation_repo/.env"
if "$implementation_preflight" --repo-root "$implementation_repo" \
  --expected-head "$reviewed_head" >/dev/null 2>&1; then
  fail "implementation preflight must reject ignored state"
fi
rm -f -- "$implementation_repo/.env"

printf 'scratch\n' >"$implementation_repo/scratch.txt"
if "$implementation_preflight" --repo-root "$implementation_repo" \
  --expected-head "$reviewed_head" >/dev/null 2>&1; then
  fail "implementation preflight must reject untracked state"
fi
rm -f -- "$implementation_repo/scratch.txt"

if "$implementation_preflight" --repo-root "$implementation_repo" \
  --expected-head 0000000000000000000000000000000000000000 \
  >/dev/null 2>&1; then
  fail "implementation preflight must reject a different reviewed commit"
fi

echo "grok review and implementation isolation selftest OK"
