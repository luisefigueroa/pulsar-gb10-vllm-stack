#!/usr/bin/env bash
# Memory estimates for released specs: every spec loads as a profile under
# set -u and yields numeric estimates, and one spec's estimate never bleeds
# into the next load. Runs against fixture specs, never the site's releases/.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-memory-profiles.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

python3 - "$REPO_DIR" "$STATE" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from release_spec import pretty_json_bytes, verify_spec
from scripts.release_spec_generate import build_spec_from_profile
from scripts.testlib.release_spec_start_fixture import write_overlay, write_released_nano
from scripts.testlib.test_release_spec_generate import PINNED_IMAGE, STACK_VERSION, TWO_NODE, model_id_for, receipt_for

root = pathlib.Path(sys.argv[2])
releases = root / "releases"
releases.mkdir()
nano, _ = write_released_nano(releases)
two, report = build_spec_from_profile(
    profile=TWO_NODE, model_id=model_id_for(TWO_NODE), image=PINNED_IMAGE, nodes=2,
    gpu_mem_util="0.80",
    engine_args=["--max-model-len", "131072", "--max-num-seqs", "16", "--tensor-parallel-size", "2", "--distributed-executor-backend", "mp"],
    container_env=[], spec_decode_args=[], platform_id="dgx-spark-gb10", stack_version=STACK_VERSION,
    spec_decode=False, receipt_path=receipt_for(model_id_for(TWO_NODE)), repo_root=pathlib.Path(sys.argv[1]),
)
assert two is not None, report
doc = json.loads(pretty_json_bytes(two))
doc["state"] = "released"
doc["measurements"] = nano["measurements"]; doc["evidence"] = nano["evidence"]; doc["baselines"] = nano["baselines"]; doc["review"] = nano["review"]
two_spec = verify_spec(doc)
(releases / f"{two_spec['spec_id']}.json").write_bytes(pretty_json_bytes(two_spec))
write_overlay(root / "overlay.json")
(root / "ids").write_text(f"{nano['spec_id']}\n{two_spec['spec_id']}\n", encoding="utf-8")
PY
mapfile -t ids <"$STATE/ids"
export PULSAR_RELEASES_ROOT="$STATE/releases"
export PULSAR_OVERLAY_PATH="$STATE/overlay.json"
export VLLM_IMAGE_MAINLINE="vllm/vllm-openai@sha256:$(printf 'c%.0s' $(seq 1 64))"

# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

assert_ne() {
  [ "$1" != "$2" ] || { echo "FAIL $3 ($1 == $2)" >&2; exit 1; }
}

for name in "${ids[@]}"; do
  load_conf "$name"
  ram=$(estimate_weights_ram_gib)
  disk=$(estimate_weights_gib)
  kv=$(estimate_kv_gib)
  case "$ram" in ''|*[!0-9.]*|.*) echo "$name: bad ram estimate: $ram" >&2; exit 1 ;; esac
  case "$disk" in ''|*[!0-9.]*|.*) echo "$name: bad disk estimate: $disk" >&2; exit 1 ;; esac
  case "$kv" in ''|*[!0-9.]*|.*) echo "$name: bad kv estimate: $kv" >&2; exit 1 ;; esac
  echo "OK   ${name:0:12} ram=$ram disk=$disk kv=$kv"
done

# A subsequent load must not inherit the previous profile's weight fields
# (bleed would inflate check-memory): poison them, load again, and the
# estimate must come from the spec's manifest, not the poisoned values.
load_conf "${ids[1]}"
WEIGHTS_GIB=999 WEIGHTS_RAM_GIB=999
load_conf "${ids[0]}"
second=$(estimate_weights_ram_gib)
assert_ne "$second" "999" "load_conf must reset WEIGHTS fields before loading the next spec"

# Explicit empty reset: WEIGHTS_RAM_GIB must be set (to empty) after load_conf
# so set -u consumers never see an unbound optional field.
load_conf "${ids[0]}"
: "${WEIGHTS_RAM_GIB}"
if [ -n "${WEIGHTS_RAM_GIB}" ]; then
  echo "WEIGHTS_RAM_GIB unexpectedly set to '$WEIGHTS_RAM_GIB'" >&2
  exit 1
fi

echo "memory profile selftest OK"
