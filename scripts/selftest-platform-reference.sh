#!/usr/bin/env bash
# Prove operator probes read the platform file: GB10 numbers stay the default,
# and a second file flips GPU/memory/topology/serving-probe conclusions.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-platform-reference.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

OTHER="$REPO_DIR/scripts/testdata/platforms/test-other.json"
case "$OTHER" in
  /*) ;;
  *) echo "test-other path is not absolute: $OTHER" >&2; exit 1 ;;
esac

python3 "$REPO_DIR/scripts/testlib/doctor_cache_fixture.py" \
  "$STATE/fixture" "$REPO_DIR/scripts/doctor.sh"
DOCTOR="$STATE/fixture/scripts/doctor.sh"
CACHE="$STATE/hf-cache"
mkdir "$CACHE"
BASE_ENV=(
  "PATH=$STATE/fixture/bin:/usr/bin:/bin"
  "GUM=0"
  "NO_COLOR=1"
  "TERM=dumb"
  "HF_CACHE=$CACHE"
)

write_nvidia_smi() {
  local name="$1"
  cat >"$STATE/fixture/bin/nvidia-smi" <<SH
#!/usr/bin/env bash
printf '%s\n' '$name'
SH
  chmod +x "$STATE/fixture/bin/nvidia-smi"
}

assert_json_field() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
path, expected = sys.argv[2], sys.argv[3]
cur = report
for part in path.split("."):
    if part.startswith("check["):
        check_id = part[len("check[") : -1]
        rows = [row for row in report["checks"] if row["id"] == check_id]
        assert len(rows) == 1, rows
        cur = rows[0]
        continue
    cur = cur[part]
if expected == "true":
    assert cur is True, cur
elif expected == "false":
    assert cur is False, cur
else:
    assert str(cur) == expected, (path, cur, expected)
PY
}

assert_check() {
  local report="$1" cid="$2" level="$3" text="$4"
  python3 - "$report" "$cid" "$level" "$text" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
rows = [row for row in report["checks"] if row["id"] == sys.argv[2]]
assert len(rows) == 1, rows
assert rows[0]["level"] == sys.argv[3], rows[0]
assert sys.argv[4] in rows[0]["message"], rows[0]
PY
}

# Default production file: GB10 shim still passes GPU and memory (16 GiB > 4).
write_nvidia_smi "NVIDIA GB10"
default_rc=0
env "${BASE_ENV[@]}" "$DOCTOR" --json >"$STATE/default.json" || default_rc=$?
[ "$default_rc" -eq 0 ]
assert_json_field "$STATE/default.json" "platform_id" "dgx-spark-gb10"
assert_check "$STATE/default.json" gpu ok "NVIDIA GB10"
assert_check "$STATE/default.json" memory ok "MemAvailable 16"

# Shim NVIDIA GB10 with test-other selected → GPU check fails.
write_nvidia_smi "NVIDIA GB10"
gb10_other_rc=0
env "${BASE_ENV[@]}" PULSAR_PLATFORM_FILE="$OTHER" \
  "$DOCTOR" --json >"$STATE/gb10-other.json" || gb10_other_rc=$?
[ "$gb10_other_rc" -ne 0 ]
assert_json_field "$STATE/gb10-other.json" "platform_id" "test-other"
assert_check "$STATE/gb10-other.json" gpu fail "want NVIDIA TESTGPU"
assert_check "$STATE/gb10-other.json" memory fail "hard floor 50"

# Shim NVIDIA TESTGPU with test-other → GPU passes; memory still fails at 16<50.
write_nvidia_smi "NVIDIA TESTGPU"
testgpu_rc=0
env "${BASE_ENV[@]}" PULSAR_PLATFORM_FILE="$OTHER" \
  "$DOCTOR" --json >"$STATE/testgpu-other.json" || testgpu_rc=$?
[ "$testgpu_rc" -ne 0 ]
assert_check "$STATE/testgpu-other.json" gpu ok "NVIDIA TESTGPU"
assert_check "$STATE/testgpu-other.json" memory fail "hard floor 50"

# probe-node.py --expected-gpu NVIDIA TESTGPU against a GB10 shim is not qualified.
mkdir "$STATE/probe-bin"
cat >"$STATE/probe-bin/nvidia-smi" <<'SH'
#!/usr/bin/env bash
printf 'NVIDIA GB10\n'
SH
chmod +x "$STATE/probe-bin/nvidia-smi"
PROBE_JSON=$(PATH="$STATE/probe-bin:$PATH" python3 \
  "$REPO_DIR/scripts/probe-node.py" --local --expected-gpu "NVIDIA TESTGPU")
PROBE_JSON="$PROBE_JSON" python3 - <<'PY'
import json
import os

probe = json.loads(os.environ["PROBE_JSON"])
assert probe.get("gpu") == "NVIDIA GB10", probe
assert probe.get("qualified") is False, probe
reasons = " ".join(probe.get("reject_reasons") or [])
assert "expected NVIDIA TESTGPU" in reasons, probe
PY
echo "OK   probe-node GPU flag rejects GB10 against TESTGPU expectation"

# Existing GB10 topology fixture fails validation under test-other.
python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" \
  "$STATE/topology.json" worker.test
topo_rc=0
PULSAR_PLATFORM_FILE="$OTHER" python3 "$REPO_DIR/scripts/topology_manifest.py" \
  validate "$STATE/topology.json" >"$STATE/topo.out" 2>"$STATE/topo.err" \
  || topo_rc=$?
[ "$topo_rc" -ne 0 ]
grep -Fq "NVIDIA TESTGPU" "$STATE/topo.err"
echo "OK   topology_manifest validate fails GB10 fixture under test-other"

# Serving probe maps GB10 JSON to not-ok when test-other is selected.
cat >"$STATE/probe.json" <<'JSON'
{
  "probe_schema_version": 2,
  "gpu": "NVIDIA GB10",
  "docker_ok": true,
  "docker_nvidia": true,
  "hostname": "spark-a",
  "node_id": "node-zero",
  "rdma": [{"hca": "roce0"}],
  "reject_reasons": []
}
JSON
PULSAR_PLATFORM_FILE="$OTHER" python3 "$REPO_DIR/scripts/launch_plan.py" \
  probe-from-node "$STATE/probe.json" --rank 0 >"$STATE/serving.json"
python3 - "$STATE/serving.json" <<'PY'
import json
import sys

probe = json.load(open(sys.argv[1], encoding="utf-8"))
assert probe.get("ok") is False, probe
gpu_checks = [row for row in probe.get("checks") or [] if row.get("id") == "gpu"]
assert gpu_checks and gpu_checks[0]["level"] == "fail", probe
assert "NVIDIA TESTGPU" in gpu_checks[0]["message"], probe
PY
echo "OK   launch_plan serving probe is not ok under test-other"

echo "platform reference probe selection: PASS"
