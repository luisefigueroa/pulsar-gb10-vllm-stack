#!/usr/bin/env bash
# Dry run of validate/baseline-v1.sh: fixture spec, fake producers, docker and
# curl shims. Proves the order of gates, the server-identity refusal, the
# stop-at-first-failure rule with evidence preserved, and the boot-witness
# rule. No server, no Docker, no network.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-baseline-runner.XXXXXX")
trap 'rm -rf "$STATE"' EXIT
FIXTURES="$REPO_DIR/scripts/testdata/baseline-v1/pass/measurements"

python3 - "$REPO_DIR" "$STATE" <<'PY'
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from release_spec import pretty_json_bytes, verify_spec
from scripts.testlib.release_spec_start_fixture import write_overlay, write_released_nano

root = pathlib.Path(sys.argv[2])
releases = root / "releases"
releases.mkdir()
spec, _path = write_released_nano(releases)
measured = json.loads(pretty_json_bytes(spec))
measured["state"] = "measured"
measured["review"] = {}
measured["measurements"] = []
measured["evidence"] = []
measured = verify_spec(measured)
(root / "measured.json").write_bytes(pretty_json_bytes(measured))
write_overlay(root / "overlay.json")
dataset = root / "gsm8k-fixture.parquet"
dataset.write_bytes(b"fixture dataset bytes\n")
digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
policy = json.loads((pathlib.Path(sys.argv[1]) / "policy" / "baseline-v1.json").read_text(encoding="utf-8"))
for gate in policy["gates"]:
    if gate["criterion_id"] == "gsm8k-subset":
        gate["pins"]["dataset_file_sha256"] = digest
        for item in gate["thresholds"]:
            if item["metric"] == "dataset_file_sha256":
                item["value"] = digest
(root / "policy.json").write_bytes(pretty_json_bytes(policy))
(root / "paths.json").write_text(
    json.dumps(
        {
            "spec_id": spec["spec_id"],
            "model_id": spec["identity"]["model_id"],
            "revision": spec["identity"]["snapshot_revision"],
            "image_digest": spec["identity"]["image"]["digest"],
            "served_name": "nemotron-3-nano",
        }
    )
    + "\n",
    encoding="utf-8",
)
PY
field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$STATE/paths.json" "$1"; }
spec_id=$(field spec_id)
model_id=$(field model_id)
revision=$(field revision)
image_digest=$(field image_digest)
served=$(field served_name)

python3 "$REPO_DIR/scripts/testlib/topology_manifest_fixture.py" "$STATE/topology.json" worker.test
topology_id=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' "$STATE/topology.json")
python3 "$REPO_DIR/scripts/testlib/library_hot_fixture.py" "$STATE/hot-info.json" \
  --profile "$spec_id" --model-id "$model_id" --revision "$revision" --topology-id "$topology_id" \
  --hot-root "$STATE/hot"
hub_path=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["hub_path"])' "$STATE/hot-info.json")
mkdir -p "$hub_path"
printf 'MemTotal:       268435456 kB\nMemFree:        209715200 kB\nMemAvailable:   209715200 kB\n' >"$STATE/meminfo"

export PULSAR_MEMINFO_FILE="$STATE/meminfo"
export CLUSTER_TOPOLOGY_FILE="$STATE/topology.json"
export PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py"
export FAKE_HOT_INFO_FILE="$STATE/hot-info.json"
export PULSAR_RELEASES_ROOT="$STATE/releases"
export PULSAR_OVERLAY_PATH="$STATE/overlay.json"
export PULSAR_SELFTEST=1
export VLLM_IMAGE_MAINLINE="vllm/vllm-openai@$image_digest"

expected_contract=$(REPO_DIR="$REPO_DIR" bash -c '. "$REPO_DIR/scripts/lib.sh"; launch_contract_id_for_profile "$1"' _ "$spec_id")
[ "${#expected_contract}" -eq 64 ] || { echo "FAIL fixture launch contract id: $expected_contract" >&2; exit 1; }

mkdir -p "$STATE/bin"
cat >"$STATE/bin/docker" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
label_file="$STATE/contract-label"
case "\${1:-}" in
  info) exit 0 ;;
  ps) echo "vllm-$spec_id" ;;
  image)
    [ "\${2:-}" = inspect ] || exit 64
    echo '["vllm/vllm-openai@$image_digest"]'
    ;;
  inspect)
    printf '{"running":true,"labels":{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"%s","io.pulsar.gb10.launch-contract":"%s"},"image":"sha256:fixtureimageid"}\n' \
      "$spec_id" "\$(cat "\$label_file")"
    ;;
  *) exit 0 ;;
esac
SHIM
cat >"$STATE/bin/curl" <<SHIM
#!/usr/bin/env bash
set -euo pipefail
count_file="$STATE/curl-count"
n=\$(( \$(cat "\$count_file" 2>/dev/null || echo 0) + 1 ))
echo "\$n" >"\$count_file"
created=1700000000
if [ -e "$STATE/restart-after-first" ] && [ "\$n" -gt 1 ]; then created=1700009999; fi
printf '{"object":"list","data":[{"id":"%s","object":"model","created":%s}]}\n' "$served" "\$created"
SHIM
chmod +x "$STATE/bin/docker" "$STATE/bin/curl"
printf '%s\n' "$expected_contract" >"$STATE/contract-label"
export PULSAR_DOCKER="$STATE/bin/docker"
export PATH="$STATE/bin:$PATH"

mkdir -p "$STATE/producers"
for op in verify_snapshot_manifest serve_smoke gsm8k_eval soak; do
cat >"$STATE/producers/$op.py" <<PY
#!/usr/bin/env python3
import hashlib, json, pathlib, sys
sys.path.insert(0, "$REPO_DIR")
from scripts.model_identity import pretty_json_bytes
OP = "$op"
NAMES = {"verify_snapshot_manifest": "verify-snapshot-manifest", "serve_smoke": "serve-smoke", "gsm8k_eval": "evaluate-gsm8k", "soak": "validate-soak"}
argv = sys.argv[1:]
if argv == ["--help"]:
    raise SystemExit(0)
pathlib.Path("$STATE/calls.log").open("a").write(NAMES[OP] + " " + json.dumps(argv) + "\n")
if pathlib.Path("$STATE/fail-" + NAMES[OP]).exists():
    print(OP + ": fixture failure", file=sys.stderr)
    raise SystemExit(1)
def arg(flag):
    return argv[argv.index(flag) + 1]
doc = json.loads(pathlib.Path("$FIXTURES/" + NAMES[OP] + ".json").read_text())
if OP == "verify_snapshot_manifest":
    spec = json.loads(pathlib.Path(arg("--spec")).read_text())
    payload = doc["verify-snapshot-manifest"]
    payload["spec_id"] = spec["spec_id"]
    payload["manifest_id"] = spec["identity"]["snapshot_manifest"]["manifest_id"]
    payload["expected_file_count"] = spec["identity"]["snapshot_manifest"]["file_count"]
    payload["matched_file_count"] = payload["expected_file_count"]
if OP == "gsm8k_eval":
    payload = doc["evaluate-gsm8k"]
    payload["dataset_file_sha256"] = hashlib.sha256(pathlib.Path(arg("--dataset")).read_bytes()).hexdigest()
    payload["dataset_revision"] = arg("--dataset-revision")
pathlib.Path(arg("--result-json")).write_bytes(pretty_json_bytes(doc))
PY
done
cat >"$STATE/producers/run-gates.sh" <<SH
#!/usr/bin/env bash
set -euo pipefail
printf 'run-gates %s\n' "\$(printf '%q ' "\$@")" >>"$STATE/calls.log"
[ ! -e "$STATE/fail-run-gates" ] || { echo "run-gates: fixture failure" >&2; exit 1; }
dir=""
while [ \$# -gt 0 ]; do
  case "\$1" in --measurement-dir) dir="\$2"; shift ;; esac
  shift
done
cp "$FIXTURES/compare-captures.json" "\$dir/compare-captures.json"
cp "$FIXTURES/benchmark-serving.json" "\$dir/benchmark-serving.json"
SH
chmod +x "$STATE/producers/"*

LAB_COMMIT=$(printf 'd%.0s' $(seq 1 40))
run_runner() {
  local out="$1"
  shift
  : >"$STATE/calls.log"
  rm -f "$STATE/curl-count"
  "$REPO_DIR/validate/baseline-v1.sh" "$spec_id" --spec "$STATE/measured.json" \
    --out "$out" --dataset "$STATE/gsm8k-fixture.parquet" --policy "$STATE/policy.json" \
    --producer-dir "$STATE/producers" --lab-commit "$LAB_COMMIT" --tag fixture \
    --node fixture-node-0 --skip-weights-check "$@"
}
gate_order() { sed -n 's/^\([a-z0-9-]*\) .*/\1/p' "$STATE/calls.log" | tr '\n' ' ' | sed 's/ $//'; }

# 1. a clean run walks every gate in policy order and proposes stable
out1="$STATE/out-pass"
if ! run_runner "$out1" >"$STATE/run1.log" 2>&1; then
  echo "FAIL clean run exited non-zero:" >&2; cat "$STATE/run1.log" >&2; exit 1
fi
[ "$(gate_order)" = "verify-snapshot-manifest serve-smoke run-gates evaluate-gsm8k validate-soak" ] \
  || { echo "FAIL gate order: $(gate_order)" >&2; exit 1; }
grep -q -- "--model $served" "$STATE/calls.log" || { echo "FAIL run-gates did not receive --model" >&2; exit 1; }
grep -q "^run-gates spec-${spec_id:0:12} " "$STATE/calls.log" || { echo "FAIL run-gates label is not spec-<12hex>" >&2; exit 1; }
python3 - "$out1" "$spec_id" "$LAB_COMMIT" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
run = json.loads((out / "run.json").read_text())
assert run["kind"] == "pulsar-baseline-run" and run["spec_id"] == sys.argv[2]
assert run["boot_witness"]["same_boot"] is True and run["proposed_status"] == "stable"
assert [g["name"] for g in run["gates"]] == ["verify-snapshot-manifest", "serve-smoke", "run-gates", "evaluate-gsm8k", "validate-soak"]
assert all(g["rc"] == 0 for g in run["gates"])
spec = json.loads((out / "spec.json").read_text())
assert spec["state"] == "measured" and len(spec["measurements"]) == 6
assert all(m["outcome"] == "pass" for m in spec["measurements"])
assert all(e["lab_commit"] == sys.argv[3] for e in spec["evidence"])
assert all(e["path"].startswith("results/baseline-v1/") for e in spec["evidence"])
PY
grep -q "proposed_status=stable" "$STATE/run1.log"
echo "OK   clean dry run walks the six gates in order and proposes stable"

# 2. a failed gate stops the run, keeps earlier documents, still records and evaluates
out2="$STATE/out-stop"
touch "$STATE/fail-run-gates"
if run_runner "$out2" >"$STATE/run2.log" 2>&1; then
  echo "FAIL a failed gate did not fail the run" >&2; exit 1
fi
rm -f "$STATE/fail-run-gates"
[ "$(gate_order)" = "verify-snapshot-manifest serve-smoke run-gates" ] \
  || { echo "FAIL stop order: $(gate_order)" >&2; exit 1; }
grep -q "stopped at run-gates" "$STATE/run2.log"
[ -f "$out2/serve-smoke.json" ] && [ ! -f "$out2/compare-captures.json" ]
python3 - "$out2" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
run = json.loads((out / "run.json").read_text())
assert [(g["name"], g["rc"]) for g in run["gates"]] == [("verify-snapshot-manifest", 0), ("serve-smoke", 0), ("run-gates", 1)]
assert run["proposed_status"] == "failed"
spec = json.loads((out / "spec.json").read_text())
outcomes = {m["criterion_id"]: m["outcome"] for m in spec["measurements"]}
assert outcomes["serving-integration-smoke"] == "pass" and outcomes["strict-same-boot-captures"] == "incomplete"
PY
echo "OK   failed gate stops the run, keeps earlier documents, records incomplete outcomes"

# 3. a restart between gates voids the run without evaluating
out3="$STATE/out-restart"
touch "$STATE/restart-after-first"
if run_runner "$out3" >"$STATE/run3.log" 2>&1; then
  echo "FAIL a changed boot witness did not fail the run" >&2; exit 1
fi
rm -f "$STATE/restart-after-first"
grep -q "boot witness changed" "$STATE/run3.log"
[ ! -e "$out3/spec.json" ] || { echo "FAIL evaluator ran after a boot change" >&2; exit 1; }
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["boot_witness"]["same_boot"] is False and r["proposed_status"] is None' "$out3/run.json"
echo "OK   boot witness change voids the run and skips evaluation"

# 4. a container launched from a different contract is refused before any gate
printf '%s\n' "$(printf 'e%.0s' $(seq 1 64))" >"$STATE/contract-label"
if run_runner "$STATE/out-refused" >"$STATE/run4.log" 2>&1; then
  echo "FAIL a mismatched launch contract was accepted" >&2; exit 1
fi
printf '%s\n' "$expected_contract" >"$STATE/contract-label"
grep -q "launch contract differs from the profile as written" "$STATE/run4.log"
[ ! -s "$STATE/calls.log" ] || { echo "FAIL gates ran against a refused container" >&2; exit 1; }
echo "OK   container with a different launch contract is refused before any gate"

# 5. a dataset that is not the policy pin is refused before any gate
printf 'other bytes\n' >"$STATE/other.parquet"
if "$REPO_DIR/validate/baseline-v1.sh" "$spec_id" --spec "$STATE/measured.json" --out "$STATE/out-pin" \
    --dataset "$STATE/other.parquet" --policy "$STATE/policy.json" --producer-dir "$STATE/producers" \
    --lab-commit "$LAB_COMMIT" --node fixture-node-0 --skip-weights-check >"$STATE/run5.log" 2>&1; then
  echo "FAIL an unpinned dataset was accepted" >&2; exit 1
fi
grep -q "is not the policy pin" "$STATE/run5.log"
echo "OK   dataset that is not the policy pin is refused"

echo "baseline-v1 runner dry run: PASS"
