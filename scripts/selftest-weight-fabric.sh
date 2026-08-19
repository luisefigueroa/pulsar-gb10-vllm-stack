#!/usr/bin/env bash
# Deterministic single-copy weight-fabric configuration and launcher tests.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-weight-fabric-test.XXXXXX")
trap 'rm -rf "$STATE_DIR"' EXIT

TOPOLOGY="$STATE_DIR/topology.json"
DRIFT_TOPOLOGY="$STATE_DIR/topology-drift.json"
CONFIG_A="$STATE_DIR/config-a.json"
CONFIG_B="$STATE_DIR/config-b.json"
HF_ROOT="$STATE_DIR/hf"
MOUNT_ROOT="$STATE_DIR/mount"
PROFILE=qwen3-1.7b-2node
MODEL=Qwen/Qwen3-1.7B
TOOL="$REPO_DIR/scripts/weight_fabric.py"

python3 - "$TOPOLOGY" "$DRIFT_TOPOLOGY" <<'PY'
import copy
import json
import pathlib
import sys

from scripts.topology_manifest import topology_digest, validate_manifest


def endpoint(hca, netdev, ip):
    return {"hca": hca, "netdev": netdev, "ip": ip}


nodes = [
    {
        "rank": 0,
        "node_id": "fixture-node-0",
        "hostname": "atlas-owner",
        "ssh_host": "local",
        "control": {"interface": "admin0", "ip": "192.0.2.10"},
        "gpu": "NVIDIA GB10",
        "rdma": [
            {"hca": "a01x", "netdev": "data01x", "cidrs": ["10.10.1.1/24"]},
            {"hca": "a01y", "netdev": "data01y", "cidrs": ["10.10.2.1/24"]},
            {"hca": "a02x", "netdev": "data02x", "cidrs": ["10.10.3.1/24"]},
            {"hca": "a02y", "netdev": "data02y", "cidrs": ["10.10.4.1/24"]},
        ],
    },
    {
        "rank": 1,
        "node_id": "fixture-node-1",
        "hostname": "orion-client",
        "ssh_host": "orion-client.test",
        "control": {"interface": "admin1", "ip": "192.0.2.11"},
        "gpu": "NVIDIA GB10",
        "rdma": [
            {"hca": "b01x", "netdev": "peer01x", "cidrs": ["10.10.1.2/24"]},
            {"hca": "b01y", "netdev": "peer01y", "cidrs": ["10.10.2.2/24"]},
            {"hca": "b12x", "netdev": "peer12x", "cidrs": ["10.10.5.1/24"]},
            {"hca": "b12y", "netdev": "peer12y", "cidrs": ["10.10.6.1/24"]},
        ],
    },
    {
        "rank": 2,
        "node_id": "fixture-node-2",
        "hostname": "zenith-idle",
        "ssh_host": "zenith-idle.test",
        "control": {"interface": "admin2", "ip": "192.0.2.12"},
        "gpu": "NVIDIA GB10",
        "rdma": [
            {"hca": "c02x", "netdev": "peer02x", "cidrs": ["10.10.3.2/24"]},
            {"hca": "c02y", "netdev": "peer02y", "cidrs": ["10.10.4.2/24"]},
            {"hca": "c12x", "netdev": "peer12x", "cidrs": ["10.10.5.2/24"]},
            {"hca": "c12y", "netdev": "peer12y", "cidrs": ["10.10.6.2/24"]},
        ],
    },
]
links = [
    {
        "ranks": [0, 1],
        "rails": [
            {
                "network": "10.10.1.0/24",
                "a": endpoint("a01x", "data01x", "10.10.1.1"),
                "b": endpoint("b01x", "peer01x", "10.10.1.2"),
            },
            {
                "network": "10.10.2.0/24",
                "a": endpoint("a01y", "data01y", "10.10.2.1"),
                "b": endpoint("b01y", "peer01y", "10.10.2.2"),
            },
        ],
    },
    {
        "ranks": [0, 2],
        "rails": [
            {
                "network": "10.10.3.0/24",
                "a": endpoint("a02x", "data02x", "10.10.3.1"),
                "b": endpoint("c02x", "peer02x", "10.10.3.2"),
            },
            {
                "network": "10.10.4.0/24",
                "a": endpoint("a02y", "data02y", "10.10.4.1"),
                "b": endpoint("c02y", "peer02y", "10.10.4.2"),
            },
        ],
    },
    {
        "ranks": [1, 2],
        "rails": [
            {
                "network": "10.10.5.0/24",
                "a": endpoint("b12x", "peer12x", "10.10.5.1"),
                "b": endpoint("c12x", "peer12x", "10.10.5.2"),
            },
            {
                "network": "10.10.6.0/24",
                "a": endpoint("b12y", "peer12y", "10.10.6.1"),
                "b": endpoint("c12y", "peer12y", "10.10.6.2"),
            },
        ],
    },
]
topology = {
    "schema_version": 1,
    "generated_at": "2026-08-07T00:00:00+00:00",
    "nodes": nodes,
    "links": links,
    "validation": {
        "class": "roce-full-mesh",
        "full_mesh": True,
        "connectivity_verified": True,
        "min_rails_per_pair": 2,
    },
}
topology["topology_id"] = topology_digest(topology)
validate_manifest(topology, require_verified=True)
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(topology, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

drift = copy.deepcopy(topology)
drift["nodes"][1]["node_id"] = "replacement-node-1"
drift["topology_id"] = topology_digest(drift)
validate_manifest(drift, require_verified=True)
pathlib.Path(sys.argv[2]).write_text(
    json.dumps(drift, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

"$TOOL" configure "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2 \
  --storage-nodes 3 \
  --owner fixture-node-0 --cache-root "$HF_ROOT" \
  --mount-root "$MOUNT_ROOT" --output "$CONFIG_A" >/dev/null
"$TOOL" configure "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2 \
  --storage-nodes 3 \
  --owner fixture-node-0 --cache-root "$HF_ROOT" \
  --mount-root "$MOUNT_ROOT" --output "$CONFIG_B" >/dev/null
cmp "$CONFIG_A" "$CONFIG_B"
echo "OK   fabric configuration is deterministic"

"$TOOL" validate "$CONFIG_A" "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2
rows=$("$TOOL" rows "$CONFIG_A" "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2)
printf '%s\n' "$rows" | grep -Fq $'RANK\t0\tfixture-node-0\tatlas-owner\tlocal\towner'
printf '%s\n' "$rows" | grep -Fq $'\tclient\t'
printf '%s\n' "$rows" | grep -Fq $'\t10.10.1.1\t10.10.1.2\tpeer01x\tb01x\t10.10.1.0/24'
printf '%s\n' "$rows" | grep -Fq \
  $'RANK\t2\tfixture-node-2\tzenith-idle\tzenith-idle.test\tclient'
printf '%s\n' "$rows" | grep -Fq \
  $'\t10.10.3.1\t10.10.3.2\tpeer02x\tc02x\t10.10.3.0/24'
if printf '%s\n' "$rows" | grep -Fq '192.0.2.10'; then
  echo "FAIL control-LAN address leaked into weight transport rows" >&2
  exit 1
fi
config_json=$(
  "$TOOL" json "$CONFIG_A" "$TOPOLOGY" \
    --profile "$PROFILE" --model "$MODEL" --nodes 2
)
CONFIG_JSON="$config_json" HF_ROOT_V="$HF_ROOT" \
MOUNT_ROOT_V="$MOUNT_ROOT" python3 - <<'PY'
import json
import os
import pathlib

config = json.loads(os.environ["CONFIG_JSON"])
model_path = pathlib.Path("hub/models--Qwen--Qwen3-1.7B")
synthetic_root = (
    pathlib.Path(os.environ["MOUNT_ROOT_V"])
    / f"{config['profile']}-{config['topology_id'][:12]}"
)
assert config["schema_version"] == 2
assert config["nodes"] == 2
assert config["storage_nodes"] == 3
assert len(config["ranks"]) == 3
assert config["transport"]["export_scope"] == "model-repository"
assert pathlib.Path(config["transport"]["export_path"]) == (
    pathlib.Path(os.environ["HF_ROOT_V"]) / model_path
)
assert pathlib.Path(config["transport"]["mount_path"]) == (
    synthetic_root / model_path
)
assert config["ranks"][0]["cache_root"] == os.environ["HF_ROOT_V"]
assert all(
    item["cache_root"] == str(synthetic_root)
    for item in config["ranks"][1:]
)
PY
provenance_json=$(
  "$TOOL" provenance "$CONFIG_A" "$TOPOLOGY" \
    --profile "$PROFILE" --model "$MODEL" --nodes 2
)
PROVENANCE_JSON="$provenance_json" HF_ROOT_PRIVATE="$HF_ROOT" \
MOUNT_ROOT_PRIVATE="$MOUNT_ROOT" python3 - <<'PY'
import json
import os

document = os.environ["PROVENANCE_JSON"]
provenance = json.loads(document)
assert provenance["kind"] == "weight-fabric-provenance"
assert (
    provenance["configuration"]["transport"]["export_scope"]
    == "model-repository"
)
assert provenance["configuration"]["serving_nodes"] == 2
assert provenance["configuration"]["storage_nodes"] == 3
for private in (
    "fixture-node-",
    "atlas-owner",
    "orion-client",
    "zenith-idle",
    "192.0.2.",
    "10.10.",
    os.environ["HF_ROOT_PRIVATE"],
    os.environ["MOUNT_ROOT_PRIVATE"],
):
    assert private not in document, private
PY
echo "OK   two-rank serving and three-node storage scopes use exact RoCE rails"
echo "OK   public provenance omits site addresses, paths, hosts, and node IDs"

STARTUP_METRIC="$STATE_DIR/startup-metric.json"
STARTUP_TOPOLOGY_ID=$(
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' \
    "$TOPOLOGY"
)
STARTUP_CONFIG_ID=$(
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1]))["configuration_id"])' \
    "$CONFIG_A"
)
startup_args=(
  startup-metric
  --output "$STARTUP_METRIC"
  --profile "$PROFILE"
  --model "$MODEL"
  --weight-source fabric
  --nodes 2
  --topology-id "$STARTUP_TOPOLOGY_ID"
  --configuration-id "$STARTUP_CONFIG_ID"
  --owner-node-id fixture-node-0
  --tag synthetic-startup
  --cache-state cold
  --started-at 2026-08-07T00:00:00.000Z
  --first-healthy-at 2026-08-07T00:00:12.345Z
  --elapsed-seconds 12.345
)
"$TOOL" "${startup_args[@]}"
python3 - "$STARTUP_METRIC" <<'PY'
import hashlib
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
document = path.read_text(encoding="utf-8")
metric = json.loads(document)
assert metric["kind"] == "container-launch-to-first-health"
assert metric["weight_source"] == "fabric"
assert metric["cache_state"] == "cold"
assert metric["time_to_first_healthy_seconds"] == 12.345
assert metric["owner_node_fingerprint"] == hashlib.sha256(
    b"fixture-node-0"
).hexdigest()[:16]
assert "fixture-node-0" not in document
assert path.stat().st_mode & 0o777 == 0o644
PY
if "$TOOL" "${startup_args[@]}" >/dev/null 2>&1; then
  echo "FAIL startup metric overwrote an existing result" >&2
  exit 1
fi
if "$TOOL" startup-metric \
    --output "$STATE_DIR/invalid-replicated-startup.json" \
    --profile "$PROFILE" --model "$MODEL" \
    --weight-source replicated --nodes 2 \
    --topology-id "$STARTUP_TOPOLOGY_ID" \
    --configuration-id "$STARTUP_CONFIG_ID" \
    --owner-node-id fixture-node-0 \
    --started-at 2026-08-07T00:00:00.000Z \
    --first-healthy-at 2026-08-07T00:00:12.345Z \
    --elapsed-seconds 12.345 >/dev/null 2>&1; then
  echo "FAIL replicated startup metric accepted fabric provenance" >&2
  exit 1
fi
echo "OK   startup evidence is provenance-bound, redacted, and no-overwrite"

if "$TOOL" validate "$CONFIG_A" "$DRIFT_TOPOLOGY" \
    --profile "$PROFILE" --model "$MODEL" --nodes 2 >/dev/null 2>&1; then
  echo "FAIL topology drift accepted" >&2
  exit 1
fi
if "$TOOL" configure "$TOPOLOGY" \
    --profile "$PROFILE" --model "$MODEL" --nodes 2 \
    --owner fixture-node-2 --cache-root "$HF_ROOT" \
    --mount-root "$MOUNT_ROOT" >/dev/null 2>&1; then
  echo "FAIL idle non-serving owner accepted" >&2
  exit 1
fi
echo "OK   topology drift and idle owner fail closed"

python3 - "$HF_ROOT" <<'PY'
import json
import pathlib
import sys

cache = pathlib.Path(sys.argv[1])
model = cache / "hub" / "models--Qwen--Qwen3-1.7B"
snapshot = model / "snapshots" / "revision-a"
blobs = model / "blobs"
refs = model / "refs"
snapshot.mkdir(parents=True)
blobs.mkdir()
refs.mkdir()
(refs / "main").write_text("revision-a\n", encoding="utf-8")
(blobs / "config").write_text('{"model_type":"qwen3"}\n', encoding="utf-8")
(blobs / "weights").write_bytes(b"deterministic-weight-bytes\n")
(blobs / "index").write_text(
    json.dumps({
        "metadata": {},
        "weight_map": {
            "layer.weight": "model-00001-of-00001.safetensors"
        },
    }) + "\n",
    encoding="utf-8",
)
(snapshot / "config.json").symlink_to("../../blobs/config")
(snapshot / "model-00001-of-00001.safetensors").symlink_to(
    "../../blobs/weights"
)
(snapshot / "model.safetensors.index.json").symlink_to("../../blobs/index")
PY

MANIFEST_A="$STATE_DIR/manifest-a.json"
MANIFEST_B="$STATE_DIR/manifest-b.json"
"$TOOL" manifest-create --cache-root "$HF_ROOT" --model "$MODEL" \
  --profile "$PROFILE" --output "$MANIFEST_A" >/dev/null
"$TOOL" manifest-create --cache-root "$HF_ROOT" --model "$MODEL" \
  --profile "$PROFILE" --output "$MANIFEST_B" >/dev/null
cmp "$MANIFEST_A" "$MANIFEST_B"
verify_json=$("$TOOL" manifest-verify --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" --json)
VERIFY_JSON="$verify_json" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["VERIFY_JSON"])
assert data["state"] == "ok"
assert data["mode"] == "full"
assert data["file_count"] == 3
assert data["bytes_hashed"] == data["total_bytes"]
PY
echo "OK   full SHA-256 manifest is deterministic and verifies"

INCOMPLETE_MARKER="$HF_ROOT/hub/models--Qwen--Qwen3-1.7B/blobs/interrupted.incomplete"
: >"$INCOMPLETE_MARKER"
if "$TOOL" manifest-create \
    --cache-root "$HF_ROOT" --model "$MODEL" --profile "$PROFILE" \
    --output "$STATE_DIR/incomplete-manifest.json" >/dev/null 2>&1; then
  echo "FAIL interrupted owner download marker was accepted" >&2
  exit 1
fi
rm "$INCOMPLETE_MARKER"
echo "OK   interrupted owner download fails closed before sealing"

echo "--- live NFS serving is refused (ADR 0005) ---"

refuse_out=$(
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"   WEIGHT_FABRIC_CONFIG="$CONFIG_A"   "$REPO_DIR/scripts/weight-fabric.sh" apply "$PROFILE" --yes 2>&1
) && { echo "FAIL weight-fabric apply still launched live NFS serving" >&2; exit 1; }
printf '%s\n' "$refuse_out" | grep -Fq 'ADR 0005'   || { echo "FAIL weight-fabric apply missing ADR 0005 message" >&2; printf '%s\n' "$refuse_out" >&2; exit 1; }
printf '%s\n' "$refuse_out" | grep -Fq 'library-hot'   || { echo "FAIL weight-fabric apply missing library-hot remediation" >&2; exit 1; }
printf '%s\n' "$refuse_out" | grep -Fq 'replicated'   || { echo "FAIL weight-fabric apply missing replicated remediation" >&2; exit 1; }

for serving_cmd in configure prerequisites setup-prerequisites download seal check verify benchmark drop-caches purge-replicas; do
  if CLUSTER_TOPOLOGY_FILE="$TOPOLOGY" WEIGHT_FABRIC_CONFIG="$CONFIG_A"       "$REPO_DIR/scripts/weight-fabric.sh" "$serving_cmd" "$PROFILE" --yes >/dev/null 2>&1; then
    echo "FAIL weight-fabric $serving_cmd still accepted live serving" >&2
    exit 1
  fi
done

if CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"     "$REPO_DIR/scripts/up.sh" "$PROFILE" --weight-source fabric --dry-run >/dev/null 2>&1; then
  echo "FAIL up.sh --weight-source fabric did not fail closed" >&2
  exit 1
fi
up_out=$(
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"   "$REPO_DIR/scripts/up.sh" "$PROFILE" --weight-source fabric --dry-run 2>&1
) || true
printf '%s\n' "$up_out" | grep -Fq 'ADR 0005'   || { echo "FAIL up.sh fabric refusal missing ADR 0005" >&2; printf '%s\n' "$up_out" >&2; exit 1; }
printf '%s\n' "$up_out" | grep -Fq 'remap'   || { echo "FAIL up.sh fabric refusal must deny remap" >&2; exit 1; }

if CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"     "$REPO_DIR/cluster/start-cluster.sh" "$PROFILE"       --weight-source fabric --dry-run --skip-preflight --skip-warmup >/dev/null 2>&1; then
  echo "FAIL start-cluster --weight-source fabric did not fail closed" >&2
  exit 1
fi
if CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"     "$REPO_DIR/scripts/check-weights.sh" "$PROFILE" --weight-source fabric >/dev/null 2>&1; then
  echo "FAIL check-weights --weight-source fabric did not fail closed" >&2
  exit 1
fi
if CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"     "$REPO_DIR/scripts/pull-weights.sh" "$PROFILE" --weight-source fabric --yes >/dev/null 2>&1; then
  echo "FAIL pull-weights --weight-source fabric did not fail closed" >&2
  exit 1
fi
if grep -Eq -- '--weight-source fabric' "$REPO_DIR/wizard.sh"; then
  echo "FAIL wizard still mentions --weight-source fabric" >&2
  exit 1
fi

unmount_out=$(
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY"   WEIGHT_FABRIC_CONFIG="$CONFIG_A"   "$REPO_DIR/scripts/weight-fabric.sh" unmount "$PROFILE" --yes 2>&1
) && true
printf '%s\n' "$unmount_out" | grep -Fq 'retired (ADR 0005)'   && { echo "FAIL leftover unmount was treated as a serving workflow" >&2; printf '%s\n' "$unmount_out" >&2; exit 1; }
echo "OK   live NFS serving launch is refused"
echo "OK   leftover unmount/teardown remain available"

io_json=$(
  "$TOOL" io-benchmark --cache-root "$HF_ROOT" \
    --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
    --rank 0 --role owner --source fabric --label synthetic \
    --node-id fixture-node-0 \
    --verify-sha256
)
IO_JSON="$io_json" python3 - <<'PY'
import json
import os

result = json.loads(os.environ["IO_JSON"])
assert result["state"] == "ok"
assert result["kind"] == "model-io"
assert result["rank"] == 0
assert result["role"] == "owner"
assert result["source"] == "fabric"
assert result["sha256_verified"] is True
assert result["bytes_read"] > 0
assert result["seconds"] > 0
assert result["throughput_gib_s"] > 0
assert result["cpu_seconds"] >= 0
assert "mem_available_bytes" in result["memory_delta"]
PY
echo "OK   I/O benchmark records throughput, CPU, and memory pressure"

printf '%s\n' "$io_json" >"$STATE_DIR/rank-0.json"
"$TOOL" io-benchmark --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
  --rank 1 --role client --source fabric --label synthetic \
  --node-id fixture-node-1 \
  >"$STATE_DIR/rank-1.json"
"$TOOL" io-benchmark --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
  --rank 2 --role client --source fabric --label synthetic \
  --node-id fixture-node-2 \
  >"$STATE_DIR/rank-2.json"
manifest_bytes=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["total_bytes"])' \
  "$MANIFEST_A")
NETWORK_BEFORE="$STATE_DIR/network-before.tsv"
NETWORK_AFTER="$STATE_DIR/network-after.tsv"
python3 - "$NETWORK_BEFORE" "$NETWORK_AFTER" "$manifest_bytes" <<'PY'
import pathlib
import sys

before_path = pathlib.Path(sys.argv[1])
after_path = pathlib.Path(sys.argv[2])
model_bytes = int(sys.argv[3])
import hashlib

fingerprints = {
    rank: hashlib.sha256(f"fixture-node-{rank}".encode()).hexdigest()[:16]
    for rank in range(3)
}
rows = [
    (0, fingerprints[0], "control", "admin0"),
    (0, fingerprints[0], "fabric-owner", "data01x"),
    (0, fingerprints[0], "fabric-owner", "data02x"),
    (1, fingerprints[1], "control", "admin1"),
    (1, fingerprints[1], "fabric-client", "peer01x"),
    (2, fingerprints[2], "control", "admin2"),
    (2, fingerprints[2], "fabric-client", "peer02x"),
]
before = []
after = []
for rank, node_fingerprint, role, netdev in rows:
    rx = 1000
    tx = 2000
    before.append(
        f"{rank}\t{node_fingerprint}\t{role}\t{netdev}\t{rx}\t{tx}"
    )
    rx_delta = 4096
    tx_delta = 4096
    if role == "fabric-client":
        rx_delta = model_bytes + 65536
    if role == "fabric-owner":
        tx_delta = model_bytes + 65536
    after.append(
        f"{rank}\t{node_fingerprint}\t{role}\t{netdev}\t"
        f"{rx + rx_delta}\t{tx + tx_delta}"
    )
before_path.write_text("\n".join(before) + "\n", encoding="utf-8")
after_path.write_text("\n".join(after) + "\n", encoding="utf-8")
PY
REPORT="$STATE_DIR/benchmark.json"
"$TOOL" benchmark-report --config "$CONFIG_A" \
  --manifest "$MANIFEST_A" \
  --result "$STATE_DIR/rank-0.json" \
  --result "$STATE_DIR/rank-1.json" \
  --result "$STATE_DIR/rank-2.json" \
  --network-before "$NETWORK_BEFORE" --network-after "$NETWORK_AFTER" \
  --tag synthetic-3node --source fabric \
  --scope all-configured --cache-state cold \
  --output "$REPORT" >/dev/null
python3 - "$REPORT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["state"] == "ok"
assert report["concurrent"] is True
assert report["scope"] == "all-configured"
assert report["aggregate"]["rank_count"] == 3
assert report["aggregate"]["remote_logical_bytes"] > 0
assert report["traffic_proof"]["state"] == "pass"
assert all(check["pass"] for check in report["traffic_proof"]["checks"])
document = json.dumps(report)
for private in (
    "fixture-node-",
    "atlas-owner",
    "orion-client",
    "zenith-idle",
    "192.0.2.",
    "10.10.",
):
    assert private not in document, private
PY
echo "OK   three-node concurrent report proves RoCE traffic, not control LAN"

WRONG_FINGERPRINT="$STATE_DIR/network-wrong-fingerprint.tsv"
python3 - "$NETWORK_AFTER" "$WRONG_FINGERPRINT" <<'PY'
import pathlib
import sys

lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
fields = lines[0].split("\t")
fields[1] = "0" * 16
lines[0] = "\t".join(fields)
pathlib.Path(sys.argv[2]).write_text(
    "\n".join(lines) + "\n", encoding="utf-8"
)
PY
if "$TOOL" benchmark-report --config "$CONFIG_A" \
    --manifest "$MANIFEST_A" \
    --result "$STATE_DIR/rank-0.json" \
    --result "$STATE_DIR/rank-1.json" \
    --result "$STATE_DIR/rank-2.json" \
    --network-before "$NETWORK_BEFORE" \
    --network-after "$WRONG_FINGERPRINT" \
    --tag wrong-fingerprint --source fabric \
    --scope all-configured --cache-state cold \
    --output "$STATE_DIR/wrong-fingerprint.json" >/dev/null 2>&1; then
  echo "FAIL benchmark report accepted a wrong counter node identity" >&2
  exit 1
fi
WRONG_INTERFACE_BEFORE="$STATE_DIR/network-wrong-interface-before.tsv"
WRONG_INTERFACE_AFTER="$STATE_DIR/network-wrong-interface-after.tsv"
sed 's/peer01x/wrong01x/g' "$NETWORK_BEFORE" \
  >"$WRONG_INTERFACE_BEFORE"
sed 's/peer01x/wrong01x/g' "$NETWORK_AFTER" \
  >"$WRONG_INTERFACE_AFTER"
if "$TOOL" benchmark-report --config "$CONFIG_A" \
    --manifest "$MANIFEST_A" \
    --result "$STATE_DIR/rank-0.json" \
    --result "$STATE_DIR/rank-1.json" \
    --result "$STATE_DIR/rank-2.json" \
    --network-before "$WRONG_INTERFACE_BEFORE" \
    --network-after "$WRONG_INTERFACE_AFTER" \
    --tag wrong-interface --source fabric \
    --scope all-configured --cache-state cold \
    --output "$STATE_DIR/wrong-interface.json" >/dev/null 2>&1; then
  echo "FAIL benchmark report accepted a wrong RoCE interface" >&2
  exit 1
fi
echo "OK   traffic proof binds counters to exact nodes and RoCE interfaces"

PUBLIC_BUNDLE="$STATE_DIR/public-bundle"
mkdir "$PUBLIC_BUNDLE"
"$TOOL" provenance "$CONFIG_A" "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2 \
  --output "$PUBLIC_BUNDLE/provenance.json" >/dev/null
cp "$MANIFEST_A" "$PUBLIC_BUNDLE/manifest.json"
cp "$REPORT" "$PUBLIC_BUNDLE/benchmark.json"
cp "$STATE_DIR"/rank-{0,1,2}.json "$PUBLIC_BUNDLE/"
cp "$NETWORK_BEFORE" "$PUBLIC_BUNDLE/network-before.tsv"
cp "$NETWORK_AFTER" "$PUBLIC_BUNDLE/network-after.tsv"
AUDIT="$PUBLIC_BUNDLE/artifact-audit.json"
"$TOOL" artifact-audit \
  --directory "$PUBLIC_BUNDLE" \
  --config "$CONFIG_A" \
  --topology "$TOPOLOGY" \
  --output "$AUDIT" >/dev/null
python3 - "$AUDIT" <<'PY'
import json
import sys

audit = json.load(open(sys.argv[1], encoding="utf-8"))
assert audit["kind"] == "weight-fabric-artifact-audit"
assert audit["state"] == "pass"
assert audit["files_scanned"] >= 8
assert all(audit["checks"].values())
PY
printf '%s\n' 'orion-client.test' >"$PUBLIC_BUNDLE/private-value.txt"
if "$TOOL" artifact-audit \
    --directory "$PUBLIC_BUNDLE" \
    --config "$CONFIG_A" \
    --topology "$TOPOLOGY" \
    --output "$PUBLIC_BUNDLE/private-value-audit.json" \
    >/dev/null 2>&1; then
  echo "FAIL artifact audit accepted a private SSH target" >&2
  exit 1
fi
rm "$PUBLIC_BUNDLE/private-value.txt"
printf '%s\n' '{"hostname":"redacted"}' \
  >"$PUBLIC_BUNDLE/private-field.json"
if "$TOOL" artifact-audit \
    --directory "$PUBLIC_BUNDLE" \
    --config "$CONFIG_A" \
    --topology "$TOPOLOGY" \
    --output "$PUBLIC_BUNDLE/private-field-audit.json" \
    >/dev/null 2>&1; then
  echo "FAIL artifact audit accepted a private JSON field" >&2
  exit 1
fi
rm "$PUBLIC_BUNDLE/private-field.json"
echo "OK   publish audit rejects private site values and JSON fields"

"$TOOL" io-benchmark --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
  --rank 0 --role owner --source replicated --label synthetic-local \
  --node-id fixture-node-0 \
  >"$STATE_DIR/local-rank-0.json"
"$TOOL" io-benchmark --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
  --rank 1 --role client --source replicated --label synthetic-local \
  --node-id fixture-node-1 \
  >"$STATE_DIR/local-rank-1.json"
LOCAL_BEFORE="$STATE_DIR/network-local-before.tsv"
LOCAL_AFTER="$STATE_DIR/network-local-after.tsv"
python3 - "$NETWORK_BEFORE" "$LOCAL_BEFORE" "$LOCAL_AFTER" <<'PY'
import pathlib
import sys

source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
before = []
after = []
for line in source:
    rank, host, role, netdev, rx, tx = line.split("\t")
    if int(rank) >= 2 or netdev == "data02x":
        continue
    before.append(line)
    after.append(
        "\t".join(
            [rank, host, role, netdev, str(int(rx) + 4096), str(int(tx) + 4096)]
        )
    )
pathlib.Path(sys.argv[2]).write_text(
    "\n".join(before) + "\n", encoding="utf-8"
)
pathlib.Path(sys.argv[3]).write_text(
    "\n".join(after) + "\n", encoding="utf-8"
)
PY
LOCAL_REPORT="$STATE_DIR/benchmark-local.json"
"$TOOL" benchmark-report --config "$CONFIG_A" \
  --manifest "$MANIFEST_A" \
  --result "$STATE_DIR/local-rank-0.json" \
  --result "$STATE_DIR/local-rank-1.json" \
  --network-before "$LOCAL_BEFORE" --network-after "$LOCAL_AFTER" \
  --tag synthetic-local --source replicated \
  --scope serving --cache-state cold \
  --output "$LOCAL_REPORT" >/dev/null
python3 - "$LOCAL_REPORT" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["source"] == "replicated"
assert report["transport"] == "local-replicated"
assert report["aggregate"]["rank_count"] == 2
assert report["traffic_proof"]["state"] == "pass"
PY
echo "OK   replicated-local comparison uses the same metrics and proves local reads"

python3 - "$HF_ROOT" <<'PY'
import pathlib
import sys

path = (
    pathlib.Path(sys.argv[1])
    / "hub"
    / "models--Qwen--Qwen3-1.7B"
    / "blobs"
    / "weights"
)
path.write_bytes(b"tampered-weight-bytes\n")
PY
if "$TOOL" manifest-verify --cache-root "$HF_ROOT" \
    --manifest "$MANIFEST_A" >/dev/null 2>&1; then
  echo "FAIL weight tamper accepted" >&2
  exit 1
fi
echo "OK   manifest rejects same-path weight tampering"

python3 - "$HF_ROOT" <<'PY'
import pathlib
import sys

path = (
    pathlib.Path(sys.argv[1])
    / "hub"
    / "models--Qwen--Qwen3-1.7B"
    / "blobs"
    / "weights"
)
path.write_bytes(b"deterministic-weight-bytes\n")
PY
"$TOOL" manifest-verify --cache-root "$HF_ROOT" \
  --manifest "$MANIFEST_A" --profile "$PROFILE" --model "$MODEL" \
  >/dev/null
echo "OK   restored snapshot returns to full verified state"

echo "selftest-weight-fabric PASS"
