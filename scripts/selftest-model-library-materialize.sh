#!/usr/bin/env bash
# Focused behavior checks for durable-home and copied runtime views.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-library-materialize.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

log() { :; }
warn() { :; }
ssh_node() {
  local rank="${1:?}" command="${2:?}"
  [ "$rank" = 1 ] || return 97
  bash -c "$command"
}

# shellcheck disable=SC1091
. "$REPO_DIR/scripts/model-library-materialize.sh"

mkdir -p "$STATE/local-home" "$STATE/remote-home" "$STATE/.transfer/source"
printf 'local\n' >"$STATE/local-home/weight"
printf 'remote\n' >"$STATE/remote-home/weight"
printf 'transfer\n' >"$STATE/.transfer/source/weight"

materialize_runtime_view_on_rank \
  0 0 "$STATE/local-home" "$STATE/local-view"
[ -L "$STATE/local-view" ]
[ "$(readlink -- "$STATE/local-view")" = "$STATE/local-home" ]

materialize_runtime_view_on_rank \
  1 1 "$STATE/remote-home" "$STATE/remote-view"
[ -L "$STATE/remote-view" ]
[ "$(readlink -- "$STATE/remote-view")" = "$STATE/remote-home" ]

mkdir -p "$STATE/bin"
printf '#!/usr/bin/env bash\nexit 1\n' >"$STATE/bin/ln"
chmod +x "$STATE/bin/ln"
original_path=$PATH
PATH="$STATE/bin:$original_path"

if materialize_runtime_view_on_rank \
    0 0 "$STATE/local-home" "$STATE/local-failed"; then
  echo "local durable-home materialization unexpectedly copied after symlink failure" >&2
  exit 1
fi
[ ! -e "$STATE/local-failed" ]
[ ! -L "$STATE/local-failed" ]

if materialize_runtime_view_on_rank \
    1 1 "$STATE/remote-home" "$STATE/remote-failed" \
    2>"$STATE/remote-failed.err"; then
  echo "remote durable-home materialization unexpectedly copied after symlink failure" >&2
  exit 1
fi
[ ! -e "$STATE/remote-failed" ]
[ ! -L "$STATE/remote-failed" ]
grep -q 'durable-home view requires an exact symlink' "$STATE/remote-failed.err"

PATH=$original_path
materialize_runtime_view_on_rank \
  0 1 "$STATE/.transfer/source" "$STATE/copied-view"
[ -d "$STATE/copied-view" ]
[ ! -L "$STATE/copied-view" ]
grep -qx transfer "$STATE/copied-view/weight"

materialize_runtime_view_on_rank \
  1 0 "$STATE/.transfer/source" "$STATE/remote-copied-view"
[ -d "$STATE/remote-copied-view" ]
[ ! -L "$STATE/remote-copied-view" ]
grep -qx transfer "$STATE/remote-copied-view/weight"

if materialize_tree_on_rank \
    0 "$STATE/local-home" "$STATE/invalid-view" unexpected-mode; then
  echo "unknown materialization mode unexpectedly succeeded" >&2
  exit 1
fi
[ ! -e "$STATE/invalid-view" ]

echo "model-library materialization scenarios: PASS"
