#!/usr/bin/env bash
# Deterministic width/hanging-indent tests for shared human terminal output.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

pass=0
fail=0

assert_true() {
  local cond="$1" msg="$2"
  if [ "$cond" = 1 ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg" >&2
    fail=$((fail + 1))
  fi
}

assert_true "$([ "$(COLUMNS=10 terminal_width)" = 32 ] && echo 1 || echo 0)" \
  "shell terminal width has a readable minimum"
assert_true "$([ "$(COLUMNS=200 terminal_width)" = 100 ] && echo 1 || echo 0)" \
  "shell terminal width avoids overly long records"

shell_output=$(COLUMNS=40 print_hanging "  warn " \
  "port 8000 is owned by an unknown service and must be identified before launch")
assert_true "$(printf '%s\n' "$shell_output" | python3 -c \
  'import sys; lines=sys.stdin.read().splitlines(); print(int(max(map(len, lines)) <= 40))')" \
  "shell hanging output honors COLUMNS"
assert_true "$(printf '%s\n' "$shell_output" | python3 -c \
  'import sys; lines=sys.stdin.read().splitlines(); print(int(len(lines) > 1 and lines[1].startswith("       ") and all(line == line.rstrip() for line in lines)))')" \
  "shell continuations align without trailing whitespace"

python_output=$(COLUMNS=40 python3 - <<'PY'
from scripts.terminal_format import TerminalWriter

term = TerminalWriter()
term.field("Status", "managed · complete · safe_to_stop")
term.field("Reason", "this deliberately long explanation wraps with its label")
term.emit("x" * 70, initial_indent="  ", subsequent_indent="    ")
PY
)
assert_true "$(printf '%s\n' "$python_output" | python3 -c \
  'import sys; lines=sys.stdin.read().splitlines(); print(int(max(map(len, lines)) <= 40))')" \
  "Python terminal fields honor COLUMNS"
assert_true "$(printf '%s\n' "$python_output" | python3 -c \
  'import sys; lines=sys.stdin.read().splitlines(); print(int(lines[1].startswith("          ") and len(lines) >= 4))')" \
  "Python fields and long tokens use hanging indentation"

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
