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

python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" arrange "$REPO_DIR" "$STATE"

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
plan_instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["instance_dir"]); assert "reuse_candidates" not in d and "copy_ranks" not in d')
case "$plan_instance" in
  "$STATE/hot-plan/$spec_id-"*) ;;
  *) echo "FAIL spec view is not keyed by the spec id: $plan_instance" >&2; exit 1 ;;
esac
echo "OK   plan-prepare --identity stamps the spec id and keys the view by it"

# Launch and readiness resolve a spec view by its spec id and verify it
# against the sealed manifest; the fixture's spec-named view under hot-spec
# carries real payload, the one under hot-rank1 does not.
launch_pick=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  PULSAR_HOT_ROOT="$2"
  CONF_SOURCE=spec MODEL="$3" SNAPSHOT_REVISION="$4" SPEC_MANIFEST_ID="$5" CLUSTER_TOPOLOGY_ID="$6" NODES=1 SINGLE_NODE_REMOTE=0
  library_hot_info_for_profile "$7" | python3 -c "import json,sys; print(json.load(sys.stdin)[\"instance_dir\"])"
' _ "$REPO_DIR" "$STATE/hot-spec" "$model_id" "$revision" "$manifest_id" "$topology_id" "$spec_id")
case "$launch_pick" in
  "$STATE/hot-spec/$spec_id-"*) ;;
  *) echo "FAIL launch lookup did not resolve the spec-named view: $launch_pick" >&2; exit 1 ;;
esac
PULSAR_HOT_ROOT="$STATE/hot-spec" "$REPO_DIR/scripts/check-weights.sh" "$spec_id" --node fixture-node-0 >/dev/null 2>&1 \
  || { echo "FAIL check-weights did not accept the verified spec-named view" >&2; exit 1; }
echo "OK   launch lookup and check-weights resolve a spec view by spec id"

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
python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" purge-docker-shim "$STATE/purge-bin/docker"
export PULSAR_DOCKER="$STATE/purge-bin/docker"

pin_one() {
  local hot_root="$1" label="$2"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" pin "$spec_id" --node fixture-node-0 >/dev/null
  python3 - "$REPO_DIR" "$hot_root" "$topology_id" "$spec_id" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from scripts import model_library
hot, topology_id, spec_id = sys.argv[2:]
path = model_library.find_hot_instance_for_profile(hot, spec_id, topology_id)
stamp = model_library.load_hot_stamp(path)
if stamp.get("pinned") is not True:
    raise SystemExit(f"not pinned: {stamp.get('pinned')}")
print(path)
PY
  echo "OK   pin <spec_id> on $label"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" unpin "$spec_id" --node fixture-node-0 >/dev/null
  echo "OK   unpin <spec_id> on $label"
  # Never delete a view a running service still mounts: another profile's
  # service, or the spec's own (stop first; the stop hook does both).
  local shared_out user
  for user in nemotron-3-nano-30b-nvfp4 "$spec_id"; do
    shared_out=$(FAKE_DOCKER_SHARED_CONF="$user" PULSAR_HOT_ROOT="$hot_root" \
      "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes 2>&1 || true)
    if ! printf '%s\n' "$shared_out" | grep -q "still referenced by managed container"; then
      echo "FAIL purge-hot <spec_id> deleted or ignored a view mounted by $user: $shared_out" >&2
      exit 1
    fi
  done
  echo "OK   purge-hot <spec_id> refuses a view a running service mounts, own or other ($label)"
  # A damaged stamp whose content_id disagrees with the directory name must
  # not steer the live-user query; the purge is refused instead.
  local damaged_out
  damaged_out=$(python3 - "$REPO_DIR" "$hot_root" "$topology_id" "$spec_id" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from scripts import model_library
hot, topology_id, spec_id = sys.argv[2:]
path = model_library.find_hot_instance_for_profile(hot, spec_id, topology_id)
stamp = model_library.load_hot_stamp(path)
stamp["content_id"] = "e" * 12
model_library.write_hot_stamp(path, stamp)
print(path)
PY
)
  shared_out=$(PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes 2>&1 || true)
  if ! printf '%s\n' "$shared_out" | grep -q "differs from the instance name"; then
    echo "FAIL purge-hot <spec_id> trusted a damaged stamp: $shared_out" >&2
    exit 1
  fi
  [ -d "$damaged_out" ] || { echo "FAIL damaged-stamp purge deleted the view" >&2; exit 1; }
  python3 - "$REPO_DIR" "$damaged_out" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from scripts import model_library
import pathlib
path = pathlib.Path(sys.argv[2])
stamp = model_library.load_hot_stamp(path)
stamp["content_id"] = path.name
model_library.write_hot_stamp(path, stamp)
PY
  echo "OK   purge-hot <spec_id> refuses a damaged stamp ($label)"
  PULSAR_HOT_ROOT="$hot_root" \
    "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes >/dev/null
  echo "OK   purge-hot <spec_id> on $label"
}

pin_one "$STATE/hot-spec" "a spec-named view"

# A spec-named ready stamp with no payload: enough for cleanup policy,
# never for a serving claim.
python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" rank1-view "$REPO_DIR" "$STATE" "$topology_id" "$spec_id" "$model_id" "$revision" "$manifest_json" >/dev/null
expect_failure 1 "catalog home is required" \
  "spec pin does not fall back to rank 0 when the catalog is missing" \
  env MODEL_LIBRARY_DIR="$STATE/missing-library" PULSAR_HOT_ROOT="$STATE/hot-rank1" \
    "$REPO_DIR/scripts/model-library.sh" pin "$spec_id" --node fixture-node-0

# Capacity recovery must not depend on a warm catalog home, but a released
# spec still never falls back to rank 0: cleanup needs a placement.
expect_failure 1 "no rank-0 fallback" \
  "spec purge-hot without a catalog and without a placement refuses rank 0" \
  env MODEL_LIBRARY_DIR="$STATE/missing-library" PULSAR_HOT_ROOT="$STATE/hot-rank1" \
    "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --yes --force-unpin
stranded_view=$(python3 "$REPO_DIR/scripts/model_library.py" find-hot \
  --profile "$spec_id" --topology-id "$topology_id" --hot-root "$STATE/hot-rank1" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
[ -d "$stranded_view" ] || { echo "FAIL stranded view vanished before recovery" >&2; exit 1; }
MODEL_LIBRARY_DIR="$STATE/missing-library" PULSAR_HOT_ROOT="$STATE/hot-rank1" \
  "$REPO_DIR/scripts/model-library.sh" purge-hot "$spec_id" --node fixture-node-0 --yes --force-unpin >/dev/null
[ ! -e "$stranded_view" ] || { echo "FAIL purge-hot without a catalog left the stranded view" >&2; exit 1; }
echo "OK   purge-hot <spec_id> recovers a stranded view without a catalog when a placement is given"
damaged_view=$(python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" rank1-view "$REPO_DIR" "$STATE" "$topology_id" "$spec_id" "$model_id" "$revision" "$manifest_json")

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
python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" docker-shim "$STATE/bin/docker" "$spec_id" "$STATE/docker-present"
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

# An unmet prerequisite keeps its own remediation: prepare <spec_id> would
# fail on the same missing catalog.
no_catalog_out=$(MODEL_LIBRARY_DIR="$STATE/missing-library" PULSAR_HOT_ROOT="$STATE/hot-empty" \
  "$REPO_DIR/scripts/check-weights.sh" "$spec_id" --node fixture-node-0 2>&1 || true)
printf '%s\n' "$no_catalog_out" | grep -q "catalog refresh" \
  || { echo "FAIL check-weights without a catalog lost the refresh remediation: $no_catalog_out" >&2; exit 1; }
if printf '%s\n' "$no_catalog_out" | grep -q "prepare $spec_id --yes"; then
  echo "FAIL check-weights without a catalog still advertises prepare: $no_catalog_out" >&2
  exit 1
fi
echo "OK   check-weights keeps the classified remediation for a spec"

# Purge must reach a damaged spec view on any rank: the purge lookup binds
# by name and stamp only, while prepare, pin, and launch verify content. The
# fixture view carries a ready stamp but none of the manifest's files, which
# is exactly a view whose content is missing.
lookup_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  HOT_ROOT="$2"; PY_TOOL="$1/scripts/model_library.py"
  CONF_SOURCE=spec SPEC_MANIFEST_ID="$3" CLUSTER_TOPOLOGY_ID="$4"
  ssh_node() { shift; bash -c "$1"; }
  eval "$(sed -n "/^hot_instance_for_profile_on_rank() {/,/^}/p" "$1/scripts/model-library.sh")"
  if hot_instance_for_profile_on_rank "$5" 1 0 1 >/dev/null 2>&1; then
    echo "verified-lookup-accepted-damage"
  fi
  if hot_instance_for_profile_on_rank "$5" 0 0 1 >/dev/null 2>&1; then
    echo "local-verified-lookup-accepted-damage"
  fi
  hot_instance_for_profile_on_rank "$5" 0 0 0 >/dev/null || echo "local-purge-lookup-failed"
  hot_instance_for_profile_on_rank "$5" 1 0 0 | python3 -c "import json,sys; print(json.load(sys.stdin)[\"instance_dir\"])"
' _ "$REPO_DIR" "$STATE/hot-rank1" "$manifest_id" "$topology_id" "$spec_id")
[ "$lookup_out" = "$damaged_view" ] \
  || { echo "FAIL purge lookup on a damaged spec view: $lookup_out" >&2; exit 1; }
echo "OK   purge lookup binds a damaged spec view by name on any rank; strict lookup refuses it on any rank"

# Multi-rank purge inspects every target rank on its own: a rank whose view
# is already gone is already purged, and the surviving copies are still
# returned for removal. Only a placement with no view anywhere fails.
# Absence on a rank (exit 1) is already purged; an unobservable rank (exit
# 255, SSH failure) aborts before anything is deleted anywhere.
per_rank_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  hot_instance_for_profile_on_rank() {
    case "$2" in
      1) printf "%s\n" "{\"instance_dir\": \"/hot/x-topo/cid1\", \"stamp\": {\"content_id\": \"cid1\"}}" ;;
      9) return 255 ;;
      *) return 1 ;;
    esac
  }
  eval "$(sed -n "/^hot_instances_for_profile_on_ranks() {/,/^}/p" "$1/scripts/model-library.sh")"
  hot_instances_for_profile_on_ranks spec 0 1 2>/dev/null
  if hot_instances_for_profile_on_ranks spec 0 2 2>/dev/null; then echo "unexpected-success"; fi
  rc=0; out=$(hot_instances_for_profile_on_ranks spec 1 9 2>/dev/null) || rc=$?
  echo "unobservable rc=$rc out=[$out]"
' _ "$REPO_DIR")
[ "$per_rank_out" = $'1\t/hot/x-topo/cid1\tcid1\tfalse\nunobservable rc=255 out=[]' ] \
  || { echo "FAIL per-rank purge lookup: $per_rank_out" >&2; exit 1; }
echo "OK   purge-hot resolves surviving views per rank, treats a missing rank as purged, and aborts on an unobservable rank"

# A failed local verify must read as not ready even when tested in a
# condition, where set -e is suspended inside the helper.
printf 'raise SystemExit(1)\n' >"$STATE/failing-tool.py"
status_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  PY_TOOL="$2"; REPO_DIR="$1"; CONF_SOURCE=spec SPEC_MANIFEST_ID=m
  eval "$(sed -n "/^verify_hot_on_rank() {/,/^}/p" "$1/scripts/model-library.sh")"
  if verify_hot_on_rank 0 /hot/x spec topo-1 0 "" 2>/dev/null; then echo ready; else echo not-ready; fi
' _ "$REPO_DIR" "$STATE/failing-tool.py")
[ "$status_out" = not-ready ] || { echo "FAIL verify_hot_on_rank hid a failed local verify: $status_out" >&2; exit 1; }
echo "OK   a failed local verify is not ready inside a condition"
# Repairing a rank in place is refused while a managed container references
# the view on that rank.
guard_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  hot_view_live_users() { [ "$1" = 1 ] && echo other-service; true; }
  eval "$(sed -n "/^require_no_view_users_on_ranks() {/,/^}/p" "$1/scripts/model-library.sh")"
  require_no_view_users_on_ranks /hot/x cid1 0 && echo clear
  ( require_no_view_users_on_ranks /hot/x cid1 0 1 ) 2>&1 || true
' _ "$REPO_DIR")
printf '%s\n' "$guard_out" | grep -q "^clear$" || { echo "FAIL repair guard refused an unreferenced rank: $guard_out" >&2; exit 1; }
printf '%s\n' "$guard_out" | grep -q "refusing to re-materialize /hot/x on rank 1.*other-service" \
  || { echo "FAIL repair guard allowed a referenced rank: $guard_out" >&2; exit 1; }
echo "OK   prepare refuses to re-materialize a view a managed container references"
# The purge confirmation names every rank's path, not just the first row.
confirm_out=$(bash -c '
  set -euo pipefail
  eval "$(sed -n "/^describe_purge_rows() {/,/^}/p" "$1/scripts/model-library.sh")"
  describe_purge_rows "$(printf "0\t/hot/a")" "$(printf "1\t/hot/b")"
' _ "$REPO_DIR")
[ "$confirm_out" = "rank 0: /hot/a; rank 1: /hot/b" ] || { echo "FAIL purge confirmation text: $confirm_out" >&2; exit 1; }
echo "OK   purge-hot confirmation lists every rank's view"

# Cleanup reaches a previous one-rank placement: an explicit --node that
# differs from the overlay or from the current catalog home is accepted
# under the cleanup policy only.
cleanup_sel=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 OVERLAY_PLACEMENT_NODE_ID=fixture-node-1
  spec_overlay_node_selector fixture-node-0 cleanup
  spec_overlay_node_selector "" cleanup
' _ "$REPO_DIR")
[ "$cleanup_sel" = $'fixture-node-0\nfixture-node-1' ] || { echo "FAIL cleanup overlay selector: $cleanup_sel" >&2; exit 1; }
printf 'import json,sys\nprint(json.dumps({"home": {"rank": 0, "node_id": "n0"}}))\n' >"$STATE/resolve-stub.py"
: >"$STATE/stub-catalog.json"
historic_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 MODEL=m SNAPSHOT_REVISION=r PY_TOOL="$2" CATALOG_FILE="$3"
  resolve_single_node_placement() { SINGLE_NODE_INDEX=1; }
  eval "$(sed -n "/^resolve_hot_profile_targets() {/,/^}/p" "$1/scripts/model-library.sh")"
  resolve_hot_profile_targets spec fixture-node-1 cleanup 2>/dev/null && echo "cleanup ranks=$HOT_TARGET_RANKS_CSV"
  resolve_hot_profile_targets spec fixture-node-1 required 2>/dev/null || echo "required refused"
' _ "$REPO_DIR" "$STATE/resolve-stub.py" "$STATE/stub-catalog.json")
[ "$historic_out" = $'cleanup ranks=1\nrequired refused' ] || { echo "FAIL historical placement cleanup: $historic_out" >&2; exit 1; }
sed -n "/^cmd_purge_hot() {/,/^}/p" "$REPO_DIR/scripts/model-library.sh" | grep -c 'spec_overlay_node_selector "$node_selector" cleanup' >/dev/null \
  || { echo "FAIL purge-hot does not use the cleanup overlay policy" >&2; exit 1; }
echo "OK   purge-hot reaches a previous one-rank placement by explicit --node"

# The lifecycle commands advertise the released spec id they accept, in
# the rendered help and in each command's usage error.
help_out=$("$REPO_DIR/scripts/model-library.sh" --help)
for cmd in prepare pin unpin purge-hot; do
  printf '%s\n' "$help_out" | grep -c "model-library.sh $cmd <profile|spec_id>" >/dev/null \
    || { echo "FAIL help does not advertise spec_id for $cmd" >&2; exit 1; }
  usage_out=$("$REPO_DIR/scripts/model-library.sh" "$cmd" 2>&1 || true)
  printf '%s\n' "$usage_out" | grep -c "usage: $cmd <profile|spec_id>" >/dev/null \
    || { echo "FAIL $cmd usage error does not advertise spec_id: $usage_out" >&2; exit 1; }
done
echo "OK   lifecycle help and usage errors advertise <profile|spec_id>"

# Both lookup-failure branches in check-weights emit for any rank count, so
# neither may reference the one-node rank variable, which is unset on a
# multi-rank placement under set -u.
python3 - "$REPO_DIR/scripts/check-weights.sh" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
for reason in ("rank-unreachable", "identity-mismatch"):
    start = text.index(f'"{reason}"')
    block = text[start:text.index("exit 1", start)]
    assert "SINGLE_NODE_INDEX" not in block, (reason, block)
print("ok")
PY
echo "OK   check-weights lookup-failure branches never touch the one-node rank variable"
# The post-prepare verification must bind a spec by its sealed manifest id and
# never by profile name or conf directory (a spec has no conf file).
verify_args_log="$STATE/verify-args.log"
python3 "$REPO_DIR/scripts/testlib/release_spec_library_fixture.py" record-tool "$STATE/record-py-tool.py"
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

# Library commands apply the spec overlay placement the way up.sh does:
# --node wins, overlay placement fills an empty selector, conflicts fail.
placement_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 OVERLAY_PLACEMENT_NODE_ID=fixture-node-1
  spec_overlay_node_selector ""
  spec_overlay_node_selector fixture-node-1
  CONF_SOURCE=conf spec_overlay_node_selector ""
' _ "$REPO_DIR")
[ "$placement_out" = $'fixture-node-1\nfixture-node-1' ] \
  || { echo "FAIL spec_overlay_node_selector output: $placement_out" >&2; exit 1; }
if bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 OVERLAY_PLACEMENT_NODE_ID=fixture-node-1
  spec_overlay_node_selector fixture-node-0
' _ "$REPO_DIR" >/dev/null 2>&1; then
  echo "FAIL spec_overlay_node_selector accepted a conflicting --node" >&2
  exit 1
fi
# A rank number or hostname that names the overlay's node is the same
# placement, not a conflict (CLUSTER_TOPOLOGY_FILE is the fixture topology).
alias_out=$(bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 OVERLAY_PLACEMENT_NODE_ID=fixture-node-0
  spec_overlay_node_selector 0
  spec_overlay_node_selector fixture-head
' _ "$REPO_DIR")
[ "$alias_out" = $'0\nfixture-head' ] \
  || { echo "FAIL spec_overlay_node_selector rejected an equivalent selector: $alias_out" >&2; exit 1; }
if bash -c '
  set -euo pipefail
  . "$1/scripts/lib.sh"
  CONF_SOURCE=spec NODES=1 OVERLAY_PLACEMENT_NODE_ID=fixture-node-0
  spec_overlay_node_selector fixture-node-1
' _ "$REPO_DIR" >/dev/null 2>&1; then
  echo "FAIL spec_overlay_node_selector accepted an unknown --node against overlay placement" >&2
  exit 1
fi
echo "OK   overlay placement accepts equivalent node selectors and rejects other nodes"
for fn in cmd_prepare cmd_pin cmd_unpin cmd_purge_hot; do
  if ! sed -n "/^${fn}() {/,/^}/p" "$REPO_DIR/scripts/model-library.sh" | grep -c "spec_overlay_node_selector" >/dev/null; then
    echo "FAIL $fn does not apply the spec overlay placement" >&2
    exit 1
  fi
done
echo "OK   prepare, pin, unpin, and purge apply the spec overlay placement"
echo "OK   WP1.4d library spec selftest"
