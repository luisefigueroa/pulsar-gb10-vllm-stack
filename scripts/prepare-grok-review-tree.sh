#!/usr/bin/env bash
# Build a sanitized tree for an external Grok review.
# Do not source lib.sh: that loads .env into the process environment.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PY_TOOL="${PULSAR_GROK_REVIEW_TREE_PY:-$SCRIPT_DIR/grok_review_tree.py}"

usage() {
  cat <<'EOF'
Prepare a sanitized Grok review tree

Usage:
  scripts/prepare-grok-review-tree.sh [--repo-root DIR] [--dest DIR]
  scripts/prepare-grok-review-tree.sh --print-dest [--repo-root DIR] [--dest DIR]
  scripts/prepare-grok-review-tree.sh --json [--repo-root DIR] [--dest DIR]

Copies tracked worktree files only. Gitignored site-local files such as
.env and .cluster-topology.json are omitted. The destination must stay
outside the live repository. Delete the tree after the review.
EOF
}

repo_root="$REPO_DIR"
dest=""
json=0
print_dest=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help|help)
      usage
      exit 0
      ;;
    --repo-root)
      [ $# -ge 2 ] || { echo "ERROR: --repo-root requires a directory" >&2; exit 1; }
      repo_root="$2"
      shift 2
      ;;
    --dest)
      [ $# -ge 2 ] || { echo "ERROR: --dest requires a directory" >&2; exit 1; }
      dest="$2"
      shift 2
      ;;
    --json)
      json=1
      shift
      ;;
    --print-dest)
      print_dest=1
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [ ! -f "$PY_TOOL" ]; then
  echo "ERROR: missing $PY_TOOL" >&2
  exit 1
fi

args=(prepare --repo-root "$repo_root")
if [ -n "$dest" ]; then
  args+=(--dest "$dest")
fi
if [ "$json" = 1 ]; then
  args+=(--json)
fi
if [ "$print_dest" = 1 ]; then
  args+=(--print-dest)
fi

exec python3 "$PY_TOOL" "${args[@]}"
