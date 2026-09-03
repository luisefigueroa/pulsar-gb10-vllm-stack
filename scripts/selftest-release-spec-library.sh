#!/usr/bin/env bash
# WP1.4d: prepare/pin/unpin/purge-hot and stop hooks for a released spec.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-spec-library.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

expect_failure() {
  local expected_rc="$1" needle="$2" label="$3"
  shift 3
  local output rc=0
  set +e
  output=$("$@" 2>&1)
  rc=$?
  set -e
  if [ "$rc" != "$expected_rc" ]; then
    echo "FAIL $label: rc=$rc expected=$expected_rc output=$output" >&2
    exit 1
  fi
  if ! printf '%s' "$output" | grep -q -- "$needle"; then
    echo "FAIL $label: missing '$needle' in output=$output" >&2
    exit 1
  fi
  echo "OK   $label"
}

python3 - "$REPO_DIR" "$STATE" <<'PY'
import json
import os
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(repo))

from release_spec import pretty_json_bytes
from scripts import model_library
from scripts import model_library_receipt as source_attested
from scripts.testlib.model_library_receipt_fixture import write_snapshot_hub
from scripts.testlib.release_spec_start_fixture import (
    write_identity_hot_view,
    write_overlay,
    write_released_nano,
)
from scripts.testlib.test_release_consumer import receipt_for
from scripts.testlib.test_release_spec_generate import NANO, PINNED_IMAGE
from scripts.testlib.topology_manifest_fixture import build as build_topology
from scripts.topology_manifest import topology_digest, validate_manifest

spec, _path = write_released_nano(root / "releases")
write_overlay(root / "overlay.json")
receipt = source_attested.validate_source_attested_acquisition_receipt(
    json.loads(receipt_for(spec["identity"]["model_id"]).read_text(encoding="utf-8"))
)
hub = root / "durable-home"
write_snapshot_hub(hub, revision=receipt["snapshot_revision"])
live = model_library.inspect_live_directory_identity(hub)
library = root / "library"
source_attested.write_source_attested_receipt(library, receipt, operation="test")
source_attested.write_source_attested_home_attachment(
    library,
    receipt=receipt,
    node_id="fixture-node-0",
    durable_home_path=str(hub.resolve()),
    directory_identity={
        "device": live["device"],
        "inode": live["inode"],
        "ctime_ns": live["ctime_ns"],
    },
)
topology = build_topology("worker.test")
validate_manifest(topology)
topology["topology_id"] = topology_digest(topology)
(root / "topology.json").write_text(
    json.dumps(topology, indent=2) + "\n", encoding="utf-8"
)
inventory = model_library.inspect_hub_inventory(
    hub,
    rank=0,
    node_id="fixture-node-0",
    model_id=spec["identity"]["model_id"],
    revision=receipt["snapshot_revision"],
    allow_empty_files=True,
)
catalog = {
    "schema_version": 2,
    "generated_at": "2026-09-02T00:00:00.000Z",
    "topology_id": topology["topology_id"],
    "models": [
        {
            "model_id": spec["identity"]["model_id"],
            "revision": receipt["snapshot_revision"],
            "identity_key": (
                f"{spec['identity']['model_id']}@{receipt['snapshot_revision']}"
            ),
            "validation": "unvalidated",
            "profiles": [NANO],
            "profile_validation": [],
            "homes": [
                {
                    "rank": 0,
                    "node_id": "fixture-node-0",
                    "hostname": "fixture-head",
                    "ssh_host": "local",
                    "cache_root": str(root / "cache-0"),
                    "hub_path": str(hub.resolve()),
                    "state": "complete",
                    "home_class": "occupancy",
                    "occupancy": True,
                    "bytes": inventory["bytes_logical"],
                    "primary": True,
                }
            ],
            "duplicate": False,
            "has_primary": True,
            "primary_selection": {
                "status": "selected",
                "node_id": "fixture-node-0",
                "mode": "automatic-single-home",
            },
        }
    ],
    "primary_selections": [],
}
(library / "catalog.json").write_text(
    json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
)
manifest = spec["identity"]["snapshot_manifest"]
write_identity_hot_view(
    root / "hot-conf",
    profile=NANO,
    topology_id=topology["topology_id"],
    model_id=spec["identity"]["model_id"],
    revision=receipt["snapshot_revision"],
    manifest=manifest,
    content_id="confview0001",
)
write_identity_hot_view(
    root / "hot-spec",
    profile=spec["spec_id"],
    topology_id=topology["topology_id"],
    model_id=spec["identity"]["model_id"],
    revision=receipt["snapshot_revision"],
    manifest=manifest,
    content_id="specview0001",
)
rank1 = json.loads(json.dumps(catalog))
rank1["models"][0]["homes"][0]["rank"] = 1
rank1["models"][0]["homes"][0]["node_id"] = "fixture-node-1"
rank1["models"][0]["homes"][0]["hostname"] = "fixture-worker"
rank1["models"][0]["primary_selection"]["node_id"] = "fixture-node-1"
(library / "catalog-rank1.json").write_text(
    json.dumps(rank1, indent=2) + "\n", encoding="utf-8"
)
(root / "paths.json").write_text(
    json.dumps(
        {
            "spec_id": spec["spec_id"],
            "model_id": spec["identity"]["model_id"],
            "revision": receipt["snapshot_revision"],
            "manifest_id": manifest["manifest_id"],
            "manifest": manifest,
            "inventory": inventory,
            "topology_id": topology["topology_id"],
            "image": PINNED_IMAGE,
            "identity_key": (
                f"{spec['identity']['model_id']}@{receipt['snapshot_revision']}"
            ),
        }
    )
    + "\n",
    encoding="utf-8",
)
(root / "home-inventory.json").write_text(
    json.dumps(inventory) + "\n", encoding="utf-8"
)
PY

spec_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["spec_id"])' "$STATE/paths.json")
model_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["model_id"])' "$STATE/paths.json")
revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$STATE/paths.json")
manifest_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["manifest_id"])' "$STATE/paths.json")
identity_key=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["identity_key"])' "$STATE/paths.json")
topology_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' "$STATE/paths.json")
pinned_image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "$STATE/paths.json")
manifest_json=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["manifest"]))' "$STATE/paths.json")
inventory_json=$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))["inventory"]))' "$STATE/paths.json")

export CLUSTER_TOPOLOGY_FILE="$STATE/topology.json"
export MODEL_LIBRARY_DIR="$STATE/library"
export PULSAR_HOT_ROOT="$STATE/hot"
export PULSAR_RELEASES_ROOT="$STATE/releases"
export PULSAR_OVERLAY_PATH="$STATE/overlay.json"
export VLLM_IMAGE_MAINLINE="$pinned_image"
export PULSAR_SELFTEST=1
export PULSAR_HOT_BUDGET_BYTES=$((50 * 1024 * 1024))
export PULSAR_HOT_RESERVE_BYTES=0

PY="$REPO_DIR/scripts/model_library.py"

plan=$(python3 "$PY" plan-prepare \
  --catalog "$STATE/library/catalog.json" \
  --profile "$spec_id" \
  --identity "$identity_key" \
  --spec-manifest-json "$manifest_json" \
  --topology-id "$topology_id" \
  --hot-root "$STATE/hot-plan" \
  --backend copy \
  --nodes 1 \
  --home-inventory-json "$inventory_json" \
  --require-exact-revision "$revision" \
  --expected-integrity-manifest-json "$manifest_json")
action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
stamp_profile=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["stamp"]["profile"])')
[ "$action" = copy ] || { echo "FAIL identity plan-prepare action=$action" >&2; exit 1; }
[ "$stamp_profile" = "$spec_id" ] || { echo "FAIL stamp profile=$stamp_profile" >&2; exit 1; }
echo "OK   plan-prepare --identity stamps spec id"

python3 - "$REPO_DIR" "$STATE" "$topology_id" "$model_id" "$revision" "$spec_id" "$manifest_json" <<'PY'
import json
import pathlib
import sys

repo, state, topology_id, model_id, revision, spec_id, manifest_json = sys.argv[1:]
sys.path.insert(0, repo)
from scripts import model_library
from scripts.testlib.release_spec_start_fixture import write_identity_hot_view

manifest = json.loads(manifest_json)
hot = pathlib.Path(state) / "hot-reuse"
write_identity_hot_view(
    hot,
    profile="nemotron-3-nano-30b-nvfp4",
    topology_id=topology_id,
    model_id=model_id,
    revision=revision,
    manifest=manifest,
    content_id="reuseview001",
)
# Rebuild a matching stamp so reuse compares validation.
instance = next(hot.glob("*/*"))
stamp = model_library.load_hot_stamp(instance)
stamp["validation"] = model_library.require_activation_identity(
    {"profile": spec_id, "model_id": model_id},
    manifest,
    allow_unvalidated=False,
)
stamp["content_digest"] = manifest["manifest_id"]
model_library.write_hot_stamp(instance, stamp)
print(instance)
PY

reuse_plan=$(python3 "$PY" plan-prepare \
  --catalog "$STATE/library/catalog.json" \
  --profile "$spec_id" \
  --identity "$identity_key" \
  --spec-manifest-json "$manifest_json" \
  --topology-id "$topology_id" \
  --hot-root "$STATE/hot-reuse" \
  --backend copy \
  --nodes 1 \
  --home-inventory-json "$inventory_json" \
  --require-exact-revision "$revision" \
  --expected-integrity-manifest-json "$manifest_json")
reuse_action=$(printf '%s' "$reuse_plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
reuse_reason=$(printf '%s' "$reuse_plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["reason"])')
reuse_profile=$(printf '%s' "$reuse_plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["stamp"]["profile"])')
[ "$reuse_action" = skip ] || { echo "FAIL reuse action=$reuse_action" >&2; exit 1; }
printf '%s' "$reuse_reason" | grep -q reuse || { echo "FAIL reuse reason=$reuse_reason" >&2; exit 1; }
[ "$reuse_profile" = "nemotron-3-nano-30b-nvfp4" ] || { echo "FAIL reuse left stamp profile=$reuse_profile" >&2; exit 1; }
echo "OK   identity prepare reuses a conf-named ready view"

mismatch_json=$(printf '%s' "$manifest_json" | python3 -c '
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path("'"$REPO_DIR"'")))
from scripts import model_library
manifest = json.load(sys.stdin)
manifest["files"][0]["sha256"] = "f" * 64
manifest["manifest_id"] = model_library.snapshot_manifest_id(manifest)
print(json.dumps(manifest))
')
expect_failure 1 "differs from receipt" \
  "spec vs receipt mismatch names both manifests" \
  python3 "$PY" plan-prepare \
    --catalog "$STATE/library/catalog.json" \
    --profile "$spec_id" \
    --identity "$identity_key" \
    --spec-manifest-json "$mismatch_json" \
    --topology-id "$topology_id" \
    --hot-root "$STATE/hot-mismatch" \
    --backend copy \
    --nodes 1 \
    --home-inventory-json "$inventory_json" \
    --require-exact-revision "$revision" \
    --expected-integrity-manifest-json "$manifest_json"

printf 'not-a-spec\n' >"$STATE/releases/${spec_id}.json"
expect_failure 1 "invalid" \
  "corrupt release file fails without fallback" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
    python3 - "$REPO_DIR" "$STATE/releases" "$STATE/paths.json" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.release_consumer import matching_release_for_profile
paths = json.loads(open(sys.argv[3], encoding="utf-8").read())
matching_release_for_profile(
    repo_root=sys.argv[1],
    releases_root=sys.argv[2],
    model_id=paths["model_id"],
    image=paths["image"],
    nodes=1,
    gpu_mem_util="0.80",
    engine_args=["--max-model-len", "131072", "--max-num-seqs", "16", "--moe-backend", "marlin"],
    container_env=["VLLM_MARLIN_USE_ATOMIC_ADD=1"],
    spec_decode_args=[],
    platform_id="dgx-spark-gb10",
    snapshot_revision=paths["revision"],
    files=paths["manifest"]["files"],
    receipt_model_id=paths["model_id"],
    recommended_spec=False,
)
PY
python3 - "$REPO_DIR" "$STATE/releases" <<'PY'
import pathlib, sys
sys.path.insert(0, sys.argv[1])
from scripts.testlib.release_spec_start_fixture import write_released_nano
write_released_nano(pathlib.Path(sys.argv[2]))
PY
echo "OK   restored released spec after corrupt-file check"

conf_plan=$(python3 "$PY" plan-prepare \
  --catalog "$STATE/library/catalog.json" \
  --profile nemotron-3-nano-30b-nvfp4 \
  --models-dir "$REPO_DIR/models" \
  --spec-manifest-json "$manifest_json" \
  --topology-id "$topology_id" \
  --hot-root "$STATE/hot-conf-plan" \
  --backend copy \
  --nodes 1 \
  --home-inventory-json "$inventory_json" \
  --require-exact-revision "$revision" \
  --expected-integrity-manifest-json "$manifest_json")
conf_action=$(printf '%s' "$conf_plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
[ "$conf_action" = copy ] || { echo "FAIL conf plan with matching spec action=$conf_action" >&2; exit 1; }
echo "OK   conf plan-prepare with matching spec manifest passes"

expect_failure 1 "differs from receipt" \
  "conf plan-prepare with mutated spec manifest fails" \
  python3 "$PY" plan-prepare \
    --catalog "$STATE/library/catalog.json" \
    --profile nemotron-3-nano-30b-nvfp4 \
    --models-dir "$REPO_DIR/models" \
    --spec-manifest-json "$mismatch_json" \
    --topology-id "$topology_id" \
    --hot-root "$STATE/hot-conf-bad" \
    --backend copy \
    --nodes 1 \
    --home-inventory-json "$inventory_json" \
    --require-exact-revision "$revision" \
    --expected-integrity-manifest-json "$manifest_json"

# Docker shim for purge cases: no managed containers unless
# FAKE_DOCKER_SHARED_CONF names a profile that still mounts the view.
mkdir -p "$STATE/purge-bin"
cat >"$STATE/purge-bin/docker" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = ps ]; then
  case "$*" in
    *weight-config=*) [ -z "${FAKE_DOCKER_SHARED_CONF:-}" ] || printf '%s\n' "$FAKE_DOCKER_SHARED_CONF" ;;
  esac
  exit 0
fi
exit 0
SHIM
chmod +x "$STATE/purge-bin/docker"
export PULSAR_DOCKER="$STATE/purge-bin/docker"

pin_one() {
  local hot_root="$1" label="$2"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" pin "$spec_id" --node fixture-node-0 >/dev/null
  python3 - "$REPO_DIR" "$hot_root" "$topology_id" "$identity_key" "$manifest_id" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from scripts import model_library
hot, topology_id, identity, manifest_id = sys.argv[2:]
path = model_library.find_hot_instance_for_identity(
    hot, identity, topology_id, manifest_id=manifest_id
)
stamp = model_library.load_hot_stamp(path)
if stamp.get("pinned") is not True:
    raise SystemExit(f"not pinned: {stamp.get('pinned')}")
print(path)
PY
  echo "OK   pin <spec_id> on $label"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" unpin "$spec_id" --node fixture-node-0 >/dev/null
  echo "OK   unpin <spec_id> on $label"
  # Never delete a view another running service still mounts.
  local shared_out
  shared_out=$(FAKE_DOCKER_SHARED_CONF=nemotron-3-nano-30b-nvfp4 PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes 2>&1 || true)
  if ! printf '%s\n' "$shared_out" | grep -q "still mounted by running service"; then
    echo "FAIL purge-hot <spec_id> deleted or ignored a shared view: $shared_out" >&2
    exit 1
  fi
  echo "OK   purge-hot <spec_id> refuses a view another live service mounts ($label)"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes >/dev/null
  echo "OK   purge-hot <spec_id> on $label"
}

# find-hot --identity walks PULSAR_HOT_ROOT; pin uses HOT_ROOT from the library script.
pin_one "$STATE/hot-conf" "a conf-named view"
pin_one "$STATE/hot-spec" "a spec-named view"

# Recreate a conf-named view for the rank-0 fallback refusal.
python3 - "$REPO_DIR" "$STATE" "$topology_id" "$model_id" "$revision" "$manifest_json" <<'PY'
import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
from scripts.testlib.release_spec_start_fixture import write_identity_hot_view
manifest = json.loads(sys.argv[6])
write_identity_hot_view(
    pathlib.Path(sys.argv[2]) / "hot-rank1",
    profile="nemotron-3-nano-30b-nvfp4",
    topology_id=sys.argv[3],
    model_id=sys.argv[4],
    revision=sys.argv[5],
    manifest=manifest,
    content_id="rank1view0001",
)
PY
expect_failure 1 "catalog home is required" \
  "spec pin does not fall back to rank 0 when the catalog is missing" \
  env MODEL_LIBRARY_DIR="$STATE/missing-library" PULSAR_HOT_ROOT="$STATE/hot-rank1" \
    "$REPO_DIR/scripts/model-library.sh" pin "$spec_id" --node fixture-node-0

set +e
rank1_out=$(MODEL_LIBRARY_CATALOG="$STATE/library/catalog-rank1.json" \
  PULSAR_HOT_ROOT="$STATE/hot-rank1" \
  "$REPO_DIR/scripts/model-library.sh" pin "$spec_id" 2>&1)
rank1_rc=$?
set -e
[ "$rank1_rc" != 0 ] || { echo "FAIL rank-1 home pin succeeded locally: $rank1_out" >&2; exit 1; }
echo "OK   spec pin with catalog home on rank 1 does not fall back to rank 0"

mkdir -p "$STATE/bin"
cat >"$STATE/bin/model-library" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_LIBRARY_LOG"
exit 0
SHIM
chmod +x "$STATE/bin/model-library"
python3 - "$STATE/bin/docker" "$spec_id" "$STATE/docker-present" <<'PY'
from pathlib import Path
import sys
spec_id = sys.argv[2]
flag = sys.argv[3]
Path(flag).write_text("1\n", encoding="utf-8")
Path(sys.argv[1]).write_text(
    f"""#!/usr/bin/env bash
set -euo pipefail
case "${{1:-}}" in
  info) exit 0 ;;
  inspect)
    [ -f {flag!r} ] || exit 1
    printf '%s\\n' '{{"id":"{'a'*64}","name":"/vllm-{spec_id}","labels":{{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"{spec_id}","io.pulsar.gb10.rank":"single","io.pulsar.gb10.weight-source":"local-files"}}}}'
    exit 0
    ;;
  rm)
    rm -f {flag!r}
    exit 0
    ;;
  ps) exit 0 ;;
  *) exit 0 ;;
esac
""",
    encoding="utf-8",
)
PY
chmod +x "$STATE/bin/docker"
: >"$STATE/library.log"
down_out=$(PULSAR_DOCKER="$STATE/bin/docker" \
  PULSAR_MODEL_LIBRARY_CMD="$STATE/bin/model-library" \
  FAKE_LIBRARY_LOG="$STATE/library.log" \
  "$REPO_DIR/scripts/down.sh" "$spec_id" --purge-hot --node fixture-node-0 2>&1 || true)
if ! grep -q "purge-hot $spec_id" "$STATE/library.log"; then
  echo "FAIL down.sh --purge-hot did not invoke library hook: down=$down_out log=$(cat "$STATE/library.log")" >&2
  exit 1
fi
echo "OK   down.sh <spec_id> --purge-hot reaches library_hot_after_stop"

check_out=$(PULSAR_HOT_ROOT="$STATE/hot-empty" \
  "$REPO_DIR/scripts/check-weights.sh" "$spec_id" --node fixture-node-0 2>&1 || true)
printf '%s\n' "$check_out" | grep -q "prepare $spec_id --yes" \
  || { echo "FAIL check-weights missing prepare spec_id: $check_out" >&2; exit 1; }
if printf '%s\n' "$check_out" | grep -q "profile that produced this spec"; then
  echo "FAIL check-weights still names the source profile: $check_out" >&2
  exit 1
fi
echo "OK   check-weights advertises prepare <spec_id> --yes"

# The post-prepare verification must bind a spec by its sealed manifest id and
# never by profile name or conf directory (the view may carry a conf's name).
verify_args_log="$STATE/verify-args.log"
cat >"$STATE/record-py-tool.py" <<'PY'
import os, sys
with open(os.environ["VERIFY_ARGS_LOG"], "a", encoding="utf-8") as fh:
    fh.write("\n".join(sys.argv[1:]) + "\n--END--\n")
PY
: >"$verify_args_log"
VERIFY_ARGS_LOG="$verify_args_log" bash -c '
  set -euo pipefail
  REPO_DIR="$1"; PY_TOOL="$2"; manifest="$3"; spec="$4"
  die() { echo "die: $*" >&2; exit 1; }
  ssh_node() { return 0; }
  shell_join_q() { printf "%q " "$@"; }
  eval "$(sed -n "/^verify_hot_on_rank() {/,/^}/p" "$REPO_DIR/scripts/model-library.sh")"
  CONF_SOURCE=spec SPEC_MANIFEST_ID="$manifest" verify_hot_on_rank 0 /tmp/instance "$spec" topo-1
  CONF_SOURCE=conf verify_hot_on_rank 0 /tmp/instance nemotron-3-nano-30b-nvfp4 topo-1
' _ "$REPO_DIR" "$STATE/record-py-tool.py" "$manifest_id" "$spec_id"
python3 - "$verify_args_log" "$manifest_id" "$spec_id" <<'PY'
import sys
log, manifest_id, spec_id = sys.argv[1:]
records = [r.split("\n") for r in open(log, encoding="utf-8").read().split("--END--\n") if r.strip()]
assert len(records) == 2, records
spec_call, conf_call = records
assert "--expected-manifest-id" in spec_call and manifest_id in spec_call, spec_call
assert "--profile" not in spec_call and "--models-dir" not in spec_call, spec_call
assert spec_id not in spec_call, spec_call
assert "--profile" in conf_call and "--models-dir" in conf_call, conf_call
assert "--expected-manifest-id" not in conf_call, conf_call
print("ok")
PY
echo "OK   post-prepare verification binds a spec by manifest id, a conf by profile"
echo "OK   WP1.4d library spec selftest"
