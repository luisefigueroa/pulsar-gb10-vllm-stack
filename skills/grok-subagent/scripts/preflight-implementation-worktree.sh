#!/usr/bin/env bash
# Fail-closed privacy and branch check before Grok reviews or receives write access.
set -euo pipefail

usage() {
  cat <<'EOF'
Check a shared worktree before delegated Grok review or implementation

Usage:
  preflight-implementation-worktree.sh [--repo-root DIR]
      [--expected-head COMMIT]

Requires a clean non-default branch with no untracked or ignored state. This
prevents site-local files and unrelated work from entering the Grok context.
EOF
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

repo_root="$(pwd)"
expected_head=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --repo-root)
      [ $# -ge 2 ] || fail "--repo-root requires a directory"
      repo_root="$2"
      shift 2
      ;;
    --expected-head)
      [ $# -ge 2 ] || fail "--expected-head requires a commit"
      expected_head="$2"
      shift 2
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[ -d "$repo_root" ] || fail "repository directory does not exist"
root=$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null) \
  || fail "not a Git worktree"
root=$(realpath "$root")

branch=$(git -C "$root" symbolic-ref --quiet --short HEAD 2>/dev/null) \
  || fail "detached HEAD is not eligible"
case "$branch" in
  main|master)
    fail "default branch is not eligible; create a dedicated feature branch"
    ;;
esac

head_commit=$(git -C "$root" rev-parse HEAD 2>/dev/null) \
  || fail "cannot resolve worktree HEAD"
if [ -n "$expected_head" ] && [ "$head_commit" != "$expected_head" ]; then
  fail "worktree HEAD does not match the reviewed commit"
fi

if [ -n "$(git -C "$root" status --porcelain=v1 --untracked-files=all)" ]; then
  fail "worktree has tracked or untracked changes"
fi
if [ -n "$(git -C "$root" status --porcelain=v1 --ignored --untracked-files=all)" ]; then
  fail "worktree contains ignored state; use a fresh dedicated worktree"
fi

cat <<EOF
Grok shared worktree preflight OK
  root: $root
  branch: $branch
  head: $head_commit
  local state: none
EOF
