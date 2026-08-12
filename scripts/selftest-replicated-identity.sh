#!/usr/bin/env bash
# Thin integration scenarios for sealed replicated readiness and launch wiring.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-replicated-identity.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT
export CLUSTER_TOPOLOGY_FILE="$STATE_DIR/no-topology.json"

revision=70d244cc86ccca08cf5af4e1e306ecf908b1ad5e
seal=ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94
bundle=9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380
manifest=775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1
hub="$STATE_DIR/hf/hub/models--Qwen--Qwen3-1.7B"
snapshot="$hub/snapshots/$revision"
mkdir -p "$STATE_DIR/bin" "$snapshot"
printf '{}\n' >"$snapshot/config.json"
printf 'fixture\n' >"$snapshot/model.safetensors"

cat >"$STATE_DIR/bin/model-library" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

command = sys.argv[1] if len(sys.argv) > 1 else ""
revision = "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
seal = "ebe6f19548be033865e6c4055b367ea44e5b8e7225eab93d08cd3d7a6f1f7e94"
bundle = "9c5593879b3db1d1665e62d775784489e79aab0033d426a5c3bc324aa5113380"
manifest = "775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1"
if command == "verify-profile-bundle":
    print('{"state":"match"}')
elif command == "replicated-plan":
    plan = {
        "snapshot_revision": revision,
        "validation": {"expected_seal": {
            "seal_id": seal,
            "validation_bundle_id": bundle,
        }},
        "manifest": {"manifest_id": manifest},
    }
    if "--transport-envelope" in sys.argv:
        print(json.dumps({"encoded_plan": "encoded-plan", "plan": plan}))
    elif "--encoded" in sys.argv:
        print("encoded-plan")
    else:
        print(json.dumps(plan))
elif command == "verify-replicated":
    if os.environ.get("REPLICATED_VERIFY_MODE") == "fail":
        print("fixture mismatch", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({
        "state": "ok",
        "identity_status": "match",
        "snapshot_revision": revision,
    }))
else:
    raise SystemExit(64)
PY
chmod +x "$STATE_DIR/bin/model-library"

common_env=(
  HF_CACHE="$STATE_DIR/hf"
  PULSAR_MODEL_LIBRARY_PY="$STATE_DIR/bin/model-library"
  PULSAR_DOCKER=docker
)

ready=$(env "${common_env[@]}" "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json)
READY_JSON="$ready" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["READY_JSON"])
assert data["state"] == "ok", data
assert data["identity_status"] == "match", data
assert data["model_revision"] == "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e"
assert data["ranks"][0]["identity_status"] == "match", data
PY
echo "OK   sealed readiness resolves the exact snapshot without refs/main"

launch=$(env "${common_env[@]}" "$REPO_DIR/serve.sh" qwen3-1.7b --dry-run)
printf '%s\n' "$launch" | grep -Fq -- "--model /root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B/snapshots/$revision"
printf '%s\n' "$launch" | grep -Fq -- "$hub:/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B:ro"
printf '%s\n' "$launch" | grep -Fq -- "io.pulsar.gb10.model-revision=$revision"
printf '%s\n' "$launch" | grep -Fq -- "io.pulsar.gb10.model-seal=$seal"
printf '%s\n' "$launch" | grep -Fq -- "io.pulsar.gb10.validation-bundle=$bundle"
echo "OK   sealed launch uses exact path, read-only cache, and identity labels"

set +e
mismatch=$(env "${common_env[@]}" REPLICATED_VERIFY_MODE=fail "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --json 2>/dev/null)
mismatch_rc=$?
set -e
[ "$mismatch_rc" -ne 0 ]
MISMATCH_JSON="$mismatch" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["MISMATCH_JSON"])
assert data["state"] == "identity-mismatch", data
assert data["identity_status"] == "mismatch", data
assert data["ranks"][0]["state"] == "identity-mismatch", data
PY
echo "OK   configured seal mismatch fails closed with stable JSON"

legacy_hub="$STATE_DIR/legacy/hub/models--Qwen--Qwen3.6-27B-FP8"
legacy_snapshot="$legacy_hub/snapshots/main-fixture"
mkdir -p "$legacy_snapshot" "$legacy_hub/refs"
printf '{}\n' >"$legacy_snapshot/config.json"
printf 'fixture\n' >"$legacy_snapshot/model.safetensors"
printf 'main-fixture\n' >"$legacy_hub/refs/main"
legacy=$(HF_CACHE="$STATE_DIR/legacy" "$REPO_DIR/scripts/check-weights.sh" qwen3.6-27b-fp8 --json)
LEGACY_JSON="$legacy" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["LEGACY_JSON"])
assert data["state"] == "ok", data
assert data["identity_status"] == "legacy-unsealed", data
assert data["model_revision"] is None, data
PY
echo "OK   unsealed profiles retain legacy refs/main readiness"
