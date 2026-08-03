#!/usr/bin/env bash
# Regression: load_conf memory field resets and estimate_* under set -u.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

assert_ne() {
  local unexpected="$1" actual="$2" label="$3"
  if [ "$actual" = "$unexpected" ]; then
    echo "$label: unexpectedly got '$actual'" >&2
    exit 1
  fi
}

# Every profile must load and yield numeric memory estimates under set -u.
shopt -s nullglob
for conf in "$REPO_DIR"/models/*.conf; do
  name=$(basename "$conf" .conf)
  load_conf "$name"
  ram=$(estimate_weights_ram_gib)
  disk=$(estimate_weights_gib)
  kv=$(estimate_kv_gib)
  # Must be non-empty and look numeric (allow decimals).
  case "$ram" in ''|*[!0-9.]*|.*) echo "$name: bad ram estimate: $ram" >&2; exit 1 ;; esac
  case "$disk" in ''|*[!0-9.]*|.*) echo "$name: bad disk estimate: $disk" >&2; exit 1 ;; esac
  case "$kv" in ''|*[!0-9.]*|.*) echo "$name: bad kv estimate: $kv" >&2; exit 1 ;; esac
  echo "OK   $name ram=$ram disk=$disk kv=$kv"
done

# DeepSeek sets WEIGHTS_RAM_GIB=156; a subsequent profile without that key
# must not inherit the resident figure (bleed would inflate check-memory).
load_conf deepseek-v4-flash
ram=$(estimate_weights_ram_gib)
if [ "$ram" != "156" ]; then
  echo "deepseek-v4-flash: expected WEIGHTS_RAM_GIB=156, got $ram" >&2
  exit 1
fi

load_conf qwen3-1.7b
ram=$(estimate_weights_ram_gib)
assert_ne 156 "$ram" "qwen3-1.7b after deepseek must not inherit WEIGHTS_RAM_GIB"
# qwen has WEIGHTS_GIB=4 and no WEIGHTS_RAM_GIB → ram falls back to disk.
if [ "$ram" != "4" ]; then
  echo "qwen3-1.7b: expected ram estimate 4 (disk fallback), got $ram" >&2
  exit 1
fi

# Explicit empty reset: WEIGHTS_RAM_GIB must be set (to empty) after load_conf
# so set -u consumers never see an unbound optional field.
load_conf nemotron-3-nano-30b-nvfp4
: "${WEIGHTS_RAM_GIB}"
if [ -n "${WEIGHTS_RAM_GIB}" ]; then
  echo "nemotron-3-nano: WEIGHTS_RAM_GIB unexpectedly set to '$WEIGHTS_RAM_GIB'" >&2
  exit 1
fi

echo "memory profile selftest OK"
