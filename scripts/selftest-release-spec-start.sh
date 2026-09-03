#!/usr/bin/env bash
# WP1.4c: start/stop/status a released spec without models/*.conf.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-spec-start.XXXXXX")
trap 'rm -rf "$STATE"; rm -f "$REPO_DIR/models/${COLLISION_HEX:-}.conf"' EXIT

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
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from release_spec import pretty_json_bytes, verify_spec
from scripts.release_spec_generate import build_spec_from_profile
from scripts.testlib.release_spec_start_fixture import write_overlay, write_released_nano
from scripts.testlib.test_release_spec_generate import (
    PINNED_IMAGE,
    STACK_VERSION,
    TWO_NODE,
    receipt_for,
    model_id_for,
)

root = pathlib.Path(sys.argv[2])
releases = root / "releases"
releases.mkdir()
spec, _path = write_released_nano(releases)
failed = json.loads(pretty_json_bytes(spec))
failed["review"]["status"] = "failed"
failed_spec = verify_spec(failed)
(root / "failed-releases").mkdir()
(root / "failed-releases" / f"{failed_spec['spec_id']}.json").write_bytes(
    pretty_json_bytes(failed_spec)
)
write_overlay(root / "overlay.json")
write_overlay(root / "overlay-place.json", node_id="fixture-node-1")
recipe = {
    "schema_version": 1,
    "kind": "pulsar-deployment-overlay",
    "defaults": {
        "port": 8000,
        "served_name": "nemotron-3-nano",
        "cache_root": None,
        "placement": None,
        "engine_args": ["--bad"],
    },
    "specs": {},
}
(root / "overlay-recipe.json").write_text(
    json.dumps(recipe) + "\n", encoding="utf-8"
)
two, report = build_spec_from_profile(
    profile=TWO_NODE,
    model_id=model_id_for(TWO_NODE),
    image=PINNED_IMAGE,
    nodes=2,
    gpu_mem_util="0.80",
    engine_args=[
        "--max-model-len",
        "131072",
        "--max-num-seqs",
        "16",
        "--tensor-parallel-size",
        "2",
        "--distributed-executor-backend",
        "mp",
    ],
    container_env=[],
    spec_decode_args=[],
    platform_id="dgx-spark-gb10",
    stack_version=STACK_VERSION,
    spec_decode=False,
    receipt_path=receipt_for(model_id_for(TWO_NODE)),
    repo_root=pathlib.Path(sys.argv[1]),
)
assert two is not None and report["generated"], report
two_doc = json.loads(pretty_json_bytes(two))
two_doc["state"] = "released"
two_doc["measurements"] = failed["measurements"]
two_doc["baselines"] = failed["baselines"]
two_doc["evidence"] = failed["evidence"]
two_doc["review"] = failed["review"]
two_spec = verify_spec(two_doc)
(root / "two-releases").mkdir()
(root / "two-releases" / f"{two_spec['spec_id']}.json").write_bytes(
    pretty_json_bytes(two_spec)
)
write_overlay(root / "overlay-two.json", served_name="qwen3.8-27b-fp8-2node")
(root / "paths.json").write_text(
    json.dumps(
        {
            "spec_id": spec["spec_id"],
            "model_id": spec["identity"]["model_id"],
            "revision": spec["identity"]["snapshot_revision"],
            "failed_id": failed_spec["spec_id"],
            "two_id": two_spec["spec_id"],
            "image": PINNED_IMAGE,
        }
    )
    + "\n",
    encoding="utf-8",
)
PY

spec_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["spec_id"])' "$STATE/paths.json")
model_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["model_id"])' "$STATE/paths.json")
revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["revision"])' "$STATE/paths.json")
failed_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["failed_id"])' "$STATE/paths.json")
two_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["two_id"])' "$STATE/paths.json")
pinned_image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image"])' "$STATE/paths.json")

python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$STATE/topology.json" worker.test
topology_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' \
  "$STATE/topology.json")
python3 "$REPO_DIR/scripts/testlib/library_hot_fixture.py" \
  "$STATE/hot-info.json" \
  --profile nemotron-3-nano-30b-nvfp4 \
  --model-id "$model_id" \
  --revision "$revision" \
  --topology-id "$topology_id"

cat >"$STATE/docker" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  info) exit 0 ;;
  image)
    [ "${2:-}" = inspect ] || exit 64
    exit 0
    ;;
  ps) exit 0 ;;
  inspect) echo '{"Id":"sha256:deadbeef"}'; exit 0 ;;
  *) exit 0 ;;
esac
SHIM
chmod +x "$STATE/docker"

export CLUSTER_TOPOLOGY_FILE="$STATE/topology.json"
export PULSAR_DOCKER="$STATE/docker"
export PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py"
export FAKE_HOT_INFO_FILE="$STATE/hot-info.json"
export VLLM_IMAGE_MAINLINE="$pinned_image"
export PULSAR_SELFTEST=1

run_up() {
  local plan="$1" name="$2"
  shift 2
  PULSAR_LAUNCH_PLAN_OUT="$plan" \
  PULSAR_RELEASES_ROOT="$STATE/releases" \
  PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
    "$REPO_DIR/scripts/up.sh" "$name" --dry-run --node fixture-node-0 "$@"
}

conf_plan="$STATE/conf-plan.json"
spec_plan="$STATE/spec-plan.json"
run_up "$conf_plan" nemotron-3-nano-30b-nvfp4 >/dev/null
run_up "$spec_plan" "$spec_id" >/dev/null
python3 - "$REPO_DIR" "$conf_plan" "$spec_plan" "$STATE/releases/$spec_id.json" <<'PY'
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from scripts.testlib.release_spec_start_fixture import compare_start_plans

conf = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
spec_plan = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
spec = json.loads(pathlib.Path(sys.argv[4]).read_text(encoding="utf-8"))
compare_start_plans(conf, spec_plan, spec)
print("equal")
PY
echo "OK   equality proof (comparable contract, overlay, library view, ranks)"

banner=$(PULSAR_LAUNCH_PLAN_OUT="$STATE/banner-plan.json" \
  PULSAR_RELEASES_ROOT="$STATE/releases" \
  PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
  "$REPO_DIR/scripts/up.sh" "$spec_id" --dry-run --node fixture-node-0)
printf '%s\n' "$banner" | grep -q "source=spec $spec_id"
echo "OK   spec start banner names source=spec"

expect_failure 1 ".pulsar-overlay.json" "missing overlay names the overlay file" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/missing.pulsar-overlay.json" \
      "$REPO_DIR/scripts/up.sh" "$spec_id" --dry-run --node fixture-node-0

expect_failure 1 "recipe field" "recipe key in overlay fails" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay-recipe.json" \
      "$REPO_DIR/scripts/up.sh" "$spec_id" --dry-run --node fixture-node-0

expect_failure 2 "identity is fixed" "--spec-decode on a spec id is refused" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
      "$REPO_DIR/scripts/up.sh" "$spec_id" --dry-run --spec-decode --node fixture-node-0

PULSAR_LAUNCH_PLAN_OUT="$STATE/conf-again.json" \
  PULSAR_RELEASES_ROOT="$STATE/releases" \
  "$REPO_DIR/scripts/up.sh" nemotron-3-nano-30b-nvfp4 --dry-run --node fixture-node-0 \
  >/dev/null
echo "OK   conf dry-run unchanged"

expect_failure 1 "detect-fabric.sh --write-topology" \
  "N>1 spec requires confirmed topology" \
  env CLUSTER_TOPOLOGY_FILE="$STATE/no-topology.json" \
      PULSAR_RELEASES_ROOT="$STATE/two-releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay-two.json" \
      "$REPO_DIR/scripts/up.sh" "$two_id" --dry-run

expect_failure 2 "differs from overlay placement" \
  "--node vs overlay placement conflict" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay-place.json" \
      "$REPO_DIR/scripts/up.sh" "$spec_id" --dry-run --node fixture-node-0

missing_hex=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
expect_failure 1 "${missing_hex}.json" "missing released spec names releases/<id>.json" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
      "$REPO_DIR/scripts/up.sh" "$missing_hex" --dry-run --node fixture-node-0

PULSAR_LAUNCH_PLAN_OUT="$STATE/failed-plan.json" \
  PULSAR_RELEASES_ROOT="$STATE/failed-releases" \
  PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
  "$REPO_DIR/scripts/up.sh" "$failed_id" --dry-run --node fixture-node-0 >/dev/null
echo "OK   review.status=failed still dry-runs"

status_out=$(PULSAR_RELEASES_ROOT="$STATE/releases" \
  PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
  "$REPO_DIR/scripts/status.sh" "$spec_id" --node fixture-node-0 2>&1 || true)
printf '%s\n' "$status_out" | grep -q "expected_container=vllm-$spec_id"
echo "OK   status accepts a spec id"

down_out=$(PULSAR_RELEASES_ROOT="$STATE/releases" \
  "$REPO_DIR/scripts/down.sh" "$spec_id" --node fixture-node-0 2>&1 || true)
if printf '%s\n' "$down_out" | grep -q "no such config"; then
  echo "FAIL down.sh still requires a conf for spec id: $down_out" >&2
  exit 1
fi
echo "OK   down accepts a spec id without a conf"

COLLISION_HEX=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
printf 'MODEL=collision/model\n' >"$REPO_DIR/models/${COLLISION_HEX}.conf"
: >"$STATE/releases/${COLLISION_HEX}.json"
expect_failure 1 "both models/${COLLISION_HEX}.conf" \
  "conf named like a spec id alongside a release file is refused" \
  env PULSAR_RELEASES_ROOT="$STATE/releases" \
      PULSAR_OVERLAY_PATH="$STATE/overlay.json" \
      "$REPO_DIR/scripts/up.sh" "$COLLISION_HEX" --dry-run
rm -f "$REPO_DIR/models/${COLLISION_HEX}.conf"

python3 - "$REPO_DIR" "$STATE" "$topology_id" "$model_id" "$revision" <<'PY'
import pathlib
import subprocess
import sys

repo, state, topology_id, model_id, revision = sys.argv[1:]
sys.path.insert(0, repo)
from scripts.testlib.release_spec_start_fixture import write_identity_hot_view

hot = pathlib.Path(state) / "identity-hot"
write_identity_hot_view(
    hot,
    profile="nemotron-3-nano-30b-nvfp4",
    topology_id=topology_id,
    model_id=model_id,
    revision=revision,
)
identity = f"{model_id}@{revision}"
result = subprocess.run(
    [
        sys.executable,
        str(pathlib.Path(repo) / "scripts" / "model_library.py"),
        "find-hot",
        "--identity",
        identity,
        "--topology-id",
        topology_id,
        "--hot-root",
        str(hot),
    ],
    cwd=repo,
    capture_output=True,
    text=True,
    check=False,
)
if result.returncode != 0:
    raise SystemExit(result.stderr or result.stdout)
if "nemotron-3-nano-30b-nvfp4" not in result.stdout:
    raise SystemExit(f"identity lookup missed conf-named view: {result.stdout}")
print("ok")
PY
echo "OK   find-hot --identity locates a view prepared under the conf name"

help_out=$("$REPO_DIR/pulsar" help)
printf '%s\n' "$help_out" | grep -q "start <model|spec_id>"
echo "OK   pulsar help names spec_id"

echo "release-spec-start selftest OK"
