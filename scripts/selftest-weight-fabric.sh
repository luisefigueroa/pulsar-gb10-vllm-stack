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

LIFECYCLE_CONFIG="$STATE_DIR/lifecycle-config.json"
LIFECYCLE_OWNER_ROOT="$STATE_DIR/remote-owner-hf"
LIFECYCLE_MOUNT_ROOT="$STATE_DIR/lifecycle-mount"
LIFECYCLE_CLIENT_CACHE="$STATE_DIR/lifecycle-client-hf"
"$TOOL" configure "$TOPOLOGY" \
  --profile "$PROFILE" --model "$MODEL" --nodes 2 \
  --storage-nodes 3 \
  --owner fixture-node-1 \
  --cache-root "$LIFECYCLE_OWNER_ROOT" \
  --mount-root "$LIFECYCLE_MOUNT_ROOT" \
  --output "$LIFECYCLE_CONFIG" >/dev/null
read -r LIFECYCLE_MOUNT_PATH LIFECYCLE_EXPORT_PATH \
  LIFECYCLE_CONFIG_ID LIFECYCLE_R0_SERVER LIFECYCLE_R0_CLIENT \
  LIFECYCLE_R0_NETDEV LIFECYCLE_R2_SERVER LIFECYCLE_R2_CLIENT \
  LIFECYCLE_R2_NETDEV < <(
    python3 - "$LIFECYCLE_CONFIG" <<'PY'
import json
import sys

config = json.load(open(sys.argv[1], encoding="utf-8"))
clients = {item["rank"]: item for item in config["transport"]["clients"]}
print(
    config["transport"]["mount_path"],
    config["transport"]["export_path"],
    config["configuration_id"],
    clients[0]["server_ip"],
    clients[0]["client_ip"],
    clients[0]["client_netdev"],
    clients[2]["server_ip"],
    clients[2]["client_ip"],
    clients[2]["client_netdev"],
)
PY
  )
mkdir -p "$LIFECYCLE_MOUNT_PATH"
cp -a "$HF_ROOT/hub/models--Qwen--Qwen3-1.7B/." \
  "$LIFECYCLE_MOUNT_PATH/"
mkdir -p "$LIFECYCLE_MOUNT_PATH/.pulsar/manifests"
cp "$MANIFEST_A" \
  "$LIFECYCLE_MOUNT_PATH/.pulsar/manifests/$PROFILE.manifest.json"

LIFECYCLE_BIN="$STATE_DIR/lifecycle-bin"
LIFECYCLE_SSH="$LIFECYCLE_BIN/ssh"
LIFECYCLE_LOCAL_MOUNTED="$STATE_DIR/local-mounted"
LIFECYCLE_REMOTE_MOUNTED="$STATE_DIR/remote-mounted"
LIFECYCLE_SSH_LOG="$STATE_DIR/lifecycle-ssh.log"
LIFECYCLE_SUDO_LOG="$STATE_DIR/lifecycle-sudo.log"
LIFECYCLE_DOCKER_LOG="$STATE_DIR/lifecycle-docker.log"
LIFECYCLE_EXPORT_CAPTURE="$STATE_DIR/generated.exports"
LIFECYCLE_NFS_CAPTURE="$STATE_DIR/generated-nfs.conf"
LIFECYCLE_EXPORT_FILE="/etc/exports.d/pulsar-weight-fabric-${LIFECYCLE_CONFIG_ID:0:12}.exports"
LIFECYCLE_NFS_FILE="/etc/nfs.conf.d/pulsar-weight-fabric-${LIFECYCLE_CONFIG_ID:0:12}.conf"
LIFECYCLE_COUNTER_DIR="$STATE_DIR/lifecycle-counters"
LIFECYCLE_PREREQ_DIR="$STATE_DIR/lifecycle-prereqs"
LIFECYCLE_ROOT_LOG="$STATE_DIR/lifecycle-root-scripts.log"
read -r LIFECYCLE_MANIFEST_ID LIFECYCLE_MANIFEST_BYTES \
  LIFECYCLE_SNAPSHOT LIFECYCLE_FILE_COUNT LIFECYCLE_FP_R1 \
  LIFECYCLE_FP_R2 < <(
    python3 - "$MANIFEST_A" <<'PY'
import hashlib
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
print(
    manifest["manifest_id"],
    manifest["total_bytes"],
    manifest["snapshot_revision"],
    manifest["file_count"],
    hashlib.sha256(b"fixture-node-1").hexdigest()[:16],
    hashlib.sha256(b"fixture-node-2").hexdigest()[:16],
)
PY
  )
mkdir "$LIFECYCLE_BIN"
mkdir "$LIFECYCLE_COUNTER_DIR"
mkdir "$LIFECYCLE_PREREQ_DIR"
: >"$LIFECYCLE_SSH_LOG"
: >"$LIFECYCLE_SUDO_LOG"
: >"$LIFECYCLE_DOCKER_LOG"
: >"$LIFECYCLE_ROOT_LOG"

python3 - "$LIFECYCLE_SSH" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
host="${@: -2:1}"
command="${!#}"
printf '%s|%s\n' "$host" "$command" >>"${MOCK_LIFE_SSH_LOG:?}"
prereq_mode="${MOCK_LIFE_PREREQ_MODE:-ready}"
package_marker="${MOCK_LIFE_PREREQ_DIR:?}/package-$host"
hf_marker="${MOCK_LIFE_PREREQ_DIR:?}/hf-$host"
if [ "$prereq_mode" = password ] && [ "$command" = "sudo -n true" ]; then
  exit 1
fi
if [ "$prereq_mode" = missing ] && [[ "$command" == *"apt-get install"* ]]; then
  : >"$package_marker"
  exit 0
fi
if [ "$prereq_mode" = missing ] \
    && [[ "$command" == *"python3 -m venv"* ]] \
    && [[ "$command" == *"huggingface_hub=="* ]]; then
  : >"$hf_marker"
  exit 0
fi
if [ "$prereq_mode" = missing ] \
    && [ "$host" = "orion-client.test" ] \
    && [[ "$command" == *"command -v exportfs"* ]] \
    && [ ! -e "$package_marker" ]; then
  exit 1
fi
if [ "$prereq_mode" = missing ] \
    && [ "$host" = "zenith-idle.test" ] \
    && [[ "$command" == *"command -v mount.nfs"* ]] \
    && [ ! -e "$package_marker" ]; then
  exit 1
fi
if [ "$prereq_mode" = missing ] \
    && [ "$host" = "orion-client.test" ] \
    && [[ "$command" == *"command -v hf"* ]] \
    && [ ! -e "$hf_marker" ]; then
  exit 1
fi
if [[ "$command" == *"sudo -v"* ]] \
    && [[ "$command" == *"sudo -n bash -s"* ]]; then
  read -r _sudo _validate _and _printf _format payload _rest <<<"$command"
  root_script=$(printf '%s' "$payload" | base64 -d)
  {
    printf 'HOST %s\n' "$host"
    printf '%s\n' "$root_script"
    printf '%s\n' 'END ROOT'
  } >>"${MOCK_LIFE_ROOT_LOG:?}"
  if [[ "$root_script" == *"apt-get install"* ]]; then
    : >"$package_marker"
  fi
  while IFS= read -r line; do
    if [[ "$line" == *"/etc/exports.d/"* ]] \
        && [[ "$line" == *"base64 -d"* ]] \
        && [[ "$line" == *"install -D"* ]]; then
      read -r _printf _format inner_payload _rest <<<"$line"
      printf '%s' "$inner_payload" | base64 -d \
        >"${MOCK_LIFE_EXPORT_CAPTURE:?}"
    elif [[ "$line" == *"/etc/nfs.conf.d/"* ]] \
        && [[ "$line" == *"base64 -d"* ]] \
        && [[ "$line" == *"install -D"* ]]; then
      read -r _printf _format inner_payload _rest <<<"$line"
      printf '%s' "$inner_payload" | base64 -d \
        >"${MOCK_LIFE_NFS_CAPTURE:?}"
    fi
  done <<<"$root_script"
  if [[ "$root_script" == *"mount -t nfs4"* ]]; then
    : >"${MOCK_LIFE_REMOTE_MOUNTED:?}"
  fi
  if [[ "$root_script" == *"umount "* ]]; then
    rm -f -- "${MOCK_LIFE_REMOTE_MOUNTED:?}"
  fi
  if [[ "$root_script" == *"rm -f /etc/exports.d/"* ]]; then
    rm -f -- "${MOCK_LIFE_EXPORT_CAPTURE:?}" "${MOCK_LIFE_NFS_CAPTURE:?}"
  fi
  exit 0
fi
if [[ "$command" == *"command -v hf"* ]] \
    && [[ "$command" == *".hf-cli/venv/bin/hf"* ]]; then
  printf '%s\n' '/mock-owner/.hf-cli/venv/bin/hf'
  exit 0
fi
if [[ "$command" == *"python3 - repository-access"* ]]; then
  cat >/dev/null
  printf '%s\n' '{"state":"ok","uid":1000,"gid":1000}'
  exit 0
fi
if [[ "$command" == *"/proc/fs/nfsd/exports"* ]] \
    && [[ "$command" == *"/var/lib/nfs/etab"* ]]; then
  [ ! -e "${MOCK_LIFE_EXPORT_CAPTURE:?}" ] \
    || cat "${MOCK_LIFE_EXPORT_CAPTURE:?}"
  exit 0
fi
if [[ "$command" == find\ /etc/exports.d* ]] \
    && [[ "$command" == *"pulsar-weight-fabric-"* ]]; then
  [ ! -e "${MOCK_LIFE_EXPORT_CAPTURE:?}" ] \
    || printf '%s\n' "${MOCK_LIFE_EXPORT_FILE:?}"
  exit 0
fi
if [[ "$command" == test\ -e\ /etc/exports.d/* ]]; then
  [ -e "${MOCK_LIFE_EXPORT_CAPTURE:?}" ]
  exit
fi
if [[ "$command" == test\ -e\ /etc/nfs.conf.d/* ]]; then
  [ -e "${MOCK_LIFE_NFS_CAPTURE:?}" ]
  exit
fi
if [[ "$command" == *"sudo -n rm -f"* ]] \
    && [[ "$command" == *"pulsar-weight-fabric-"* ]]; then
  rm -f -- \
    "${MOCK_LIFE_EXPORT_CAPTURE:?}" "${MOCK_LIFE_NFS_CAPTURE:?}"
  exit 0
fi
if [[ "$command" == *"install -D"* ]] \
    && [[ "$command" == *"/etc/exports.d/"* ]]; then
  cat >"${MOCK_LIFE_EXPORT_CAPTURE:?}"
  exit 0
fi
if [[ "$command" == *"install -D"* ]] \
    && [[ "$command" == *"/etc/nfs.conf.d/"* ]]; then
  cat >"${MOCK_LIFE_NFS_CAPTURE:?}"
  exit 0
fi
if [[ "$command" == *"python3 - manifest-verify"* ]]; then
  cat >/dev/null
  printf '%s\n' '{"state":"ok"}'
  exit 0
fi
if [[ "$command" == *"python3 - manifest-create"* ]]; then
  cat >/dev/null
  printf '%s\n' 'manifest mocked'
  exit 0
fi
if [[ "$command" == *"python3 - io-benchmark"* ]]; then
  cat >/dev/null
  if [[ "$command" == *"--rank 1"* ]]; then
    rank=1
    role=owner
    node_fingerprint="${MOCK_LIFE_FP_R1:?}"
  else
    rank=2
    role=client
    node_fingerprint="${MOCK_LIFE_FP_R2:?}"
  fi
  rate_limit=null
  [[ "$command" != *"--max-mib-s 1"* ]] || rate_limit=1.0
  cat <<JSON
{
  "schema_version": 1,
  "kind": "model-io",
  "state": "ok",
  "label": "mocked-lifecycle",
  "node_fingerprint": "$node_fingerprint",
  "rank": $rank,
  "role": "$role",
  "source": "fabric",
  "read_pattern": "sequential-buffered-full-snapshot",
  "sha256_verified": false,
  "rate_limit_mib_s": $rate_limit,
  "profile": "${MOCK_LIFE_PROFILE:?}",
  "model": "${MOCK_LIFE_MODEL:?}",
  "manifest_id": "${MOCK_LIFE_MANIFEST_ID:?}",
  "snapshot_revision": "${MOCK_LIFE_SNAPSHOT:?}",
  "file_count": ${MOCK_LIFE_FILE_COUNT:?},
  "bytes_read": ${MOCK_LIFE_MANIFEST_BYTES:?},
  "started_at": "2026-08-07T00:00:00.000Z",
  "finished_at": "2026-08-07T00:00:00.010Z",
  "seconds": 0.01,
  "throughput_gib_s": 0.001,
  "cpu_user_seconds": 0.001,
  "cpu_system_seconds": 0.001,
  "cpu_seconds": 0.002,
  "cpu_utilization_percent": 20.0,
  "max_rss_bytes": 1048576,
  "memory_before": {},
  "memory_after": {},
  "memory_delta": {}
}
JSON
  exit 0
fi
if [[ "$command" == *"/sys/class/net/"*"/statistics/rx_bytes"* ]]; then
  netdev=$(
    printf '%s\n' "$command" \
      | sed -n 's#.*net/\([^/]*\)/statistics/rx_bytes.*#\1#p'
  )
  counter_file="${MOCK_LIFE_COUNTER_DIR:?}/remote-${host}-${netdev}"
  count=0
  [ ! -f "$counter_file" ] || read -r count <"$counter_file"
  printf '%s\n' "$((count + 1))" >"$counter_file"
  rx=1000
  tx=2000
  if [ "$count" -gt 0 ]; then
    rx=1010
    tx=2010
    if [[ "$netdev" != admin* ]] \
        && [ "$host" = "orion-client.test" ]; then
      tx=3000
    elif [[ "$netdev" != admin* ]]; then
      rx=2000
    fi
  fi
  printf '%s\n%s\n' "$rx" "$tx"
  exit 0
fi
if [[ "$command" == cat\ * ]] && [[ "$command" == *".manifest.json"* ]]; then
  /usr/bin/cat "${MOCK_LIFE_MANIFEST_FILE:?}"
  exit 0
fi
if [[ "$command" == *"/proc/fs/nfsd/portlist"* ]]; then
  [ "${MOCK_LIFE_OWNER_RDMA:-up}" = up ]
  exit
fi
if [[ "$command" == *"ip -4 route get"* ]]; then
  printf '%s dev %s src %s\n' \
    "${MOCK_LIFE_R2_SERVER:?}" "${MOCK_LIFE_R2_NETDEV:?}" \
    "${MOCK_LIFE_R2_CLIENT:?}"
  exit 0
fi
if [[ "$command" == *"findmnt -rn -M"* ]]; then
  if [ -e "${MOCK_LIFE_REMOTE_MOUNTED:?}" ]; then
    options="${MOCK_LIFE_MOUNT_OPTIONS:-ro,vers=4.2,proto=rdma,port=20049,hard,timeo=600,retrans=2}"
    printf '%s:%s nfs4 %s\n' \
      "${MOCK_LIFE_R2_SERVER:?}" "${MOCK_LIFE_EXPORT_PATH:?}" \
      "$options"
    exit 0
  fi
  exit 1
fi
if [[ "$command" == *" mount -t nfs4 "* ]]; then
  if [ "${MOCK_LIFE_MOUNT_FAIL:-0}" = 1 ]; then
    exit 1
  fi
  : >"${MOCK_LIFE_REMOTE_MOUNTED:?}"
  exit 0
fi
if [[ "$command" == *" umount "* ]]; then
  rm -f -- "${MOCK_LIFE_REMOTE_MOUNTED:?}"
  exit 0
fi
exit 0
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

python3 - "$LIFECYCLE_BIN/ip" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s dev %s src %s\n' \
  "${MOCK_LIFE_R0_SERVER:?}" "${MOCK_LIFE_R0_NETDEV:?}" \
  "${MOCK_LIFE_R0_CLIENT:?}"
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

python3 - "$LIFECYCLE_BIN/findmnt" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
if [ -e "${MOCK_LIFE_LOCAL_MOUNTED:?}" ]; then
  options="${MOCK_LIFE_MOUNT_OPTIONS:-ro,vers=4.2,proto=rdma,port=20049,hard,timeo=600,retrans=2}"
  printf '%s:%s nfs4 %s\n' \
    "${MOCK_LIFE_R0_SERVER:?}" "${MOCK_LIFE_EXPORT_PATH:?}" \
    "$options"
  exit 0
fi
exit 1
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

python3 - "$LIFECYCLE_BIN/sudo" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${MOCK_LIFE_SUDO_LOG:?}"
[ "${1:-}" != -n ] || shift
if [ "${1:-}" = bash ] && [ "${2:-}" = -s ]; then
  root_script=$(cat)
  {
    printf '%s\n' 'HOST local'
    printf '%s\n' "$root_script"
    printf '%s\n' 'END ROOT'
  } >>"${MOCK_LIFE_ROOT_LOG:?}"
  if [[ "$root_script" == *"mount -t nfs4"* ]]; then
    : >"${MOCK_LIFE_LOCAL_MOUNTED:?}"
  fi
  if [[ "$root_script" == *"umount "* ]]; then
    rm -f -- "${MOCK_LIFE_LOCAL_MOUNTED:?}"
  fi
  exit 0
fi
case "${1:-}" in
  mount) : >"${MOCK_LIFE_LOCAL_MOUNTED:?}" ;;
  umount) rm -f -- "${MOCK_LIFE_LOCAL_MOUNTED:?}" ;;
esac
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

python3 - "$LIFECYCLE_BIN/docker" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${MOCK_LIFE_DOCKER_LOG:?}"
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

python3 - "$LIFECYCLE_BIN/cat" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    r'''#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -eq 2 ] \
    && [[ "$1" == /sys/class/net/* ]] \
    && [ "$(basename "$1")" = rx_bytes ] \
    && [[ "$2" == /sys/class/net/* ]] \
    && [ "$(basename "$2")" = tx_bytes ]; then
  netdev="${1#/sys/class/net/}"
  netdev="${netdev%%/*}"
  counter_file="${MOCK_LIFE_COUNTER_DIR:?}/local-${netdev}"
  count=0
  [ ! -f "$counter_file" ] || read -r count <"$counter_file"
  printf '%s\n' "$((count + 1))" >"$counter_file"
  rx=1000
  tx=2000
  if [ "$count" -gt 0 ]; then
    rx=1010
    tx=2010
    if [[ "$netdev" != admin* ]]; then
      rx=2000
    fi
  fi
  printf '%s\n%s\n' "$rx" "$tx"
  exit 0
fi
exec /usr/bin/cat "$@"
''',
    encoding="utf-8",
)
path.chmod(0o755)
PY

lifecycle_cmd() {
  env \
    "PATH=$LIFECYCLE_BIN:$PATH" \
    "PULSAR_SSH=$LIFECYCLE_SSH" \
    "PULSAR_SSH_CONNECT_TIMEOUT=1" \
    "CLUSTER_TOPOLOGY_FILE=$TOPOLOGY" \
    "WEIGHT_FABRIC_CONFIG=$LIFECYCLE_CONFIG" \
    "HF_CACHE=$LIFECYCLE_CLIENT_CACHE" \
    "MOCK_LIFE_SSH_LOG=$LIFECYCLE_SSH_LOG" \
    "MOCK_LIFE_SUDO_LOG=$LIFECYCLE_SUDO_LOG" \
    "MOCK_LIFE_DOCKER_LOG=$LIFECYCLE_DOCKER_LOG" \
    "MOCK_LIFE_EXPORT_CAPTURE=$LIFECYCLE_EXPORT_CAPTURE" \
    "MOCK_LIFE_NFS_CAPTURE=$LIFECYCLE_NFS_CAPTURE" \
    "MOCK_LIFE_EXPORT_FILE=$LIFECYCLE_EXPORT_FILE" \
    "MOCK_LIFE_NFS_FILE=$LIFECYCLE_NFS_FILE" \
    "MOCK_LIFE_COUNTER_DIR=$LIFECYCLE_COUNTER_DIR" \
    "MOCK_LIFE_PREREQ_DIR=$LIFECYCLE_PREREQ_DIR" \
    "MOCK_LIFE_ROOT_LOG=$LIFECYCLE_ROOT_LOG" \
    "MOCK_LIFE_MANIFEST_FILE=$MANIFEST_A" \
    "MOCK_LIFE_MANIFEST_ID=$LIFECYCLE_MANIFEST_ID" \
    "MOCK_LIFE_MANIFEST_BYTES=$LIFECYCLE_MANIFEST_BYTES" \
    "MOCK_LIFE_SNAPSHOT=$LIFECYCLE_SNAPSHOT" \
    "MOCK_LIFE_FILE_COUNT=$LIFECYCLE_FILE_COUNT" \
    "MOCK_LIFE_FP_R1=$LIFECYCLE_FP_R1" \
    "MOCK_LIFE_FP_R2=$LIFECYCLE_FP_R2" \
    "MOCK_LIFE_PROFILE=$PROFILE" \
    "MOCK_LIFE_MODEL=$MODEL" \
    "MOCK_LIFE_LOCAL_MOUNTED=$LIFECYCLE_LOCAL_MOUNTED" \
    "MOCK_LIFE_REMOTE_MOUNTED=$LIFECYCLE_REMOTE_MOUNTED" \
    "MOCK_LIFE_EXPORT_PATH=$LIFECYCLE_EXPORT_PATH" \
    "MOCK_LIFE_R0_SERVER=$LIFECYCLE_R0_SERVER" \
    "MOCK_LIFE_R0_CLIENT=$LIFECYCLE_R0_CLIENT" \
    "MOCK_LIFE_R0_NETDEV=${LIFECYCLE_MOCK_R0_NETDEV:-$LIFECYCLE_R0_NETDEV}" \
    "MOCK_LIFE_R2_SERVER=$LIFECYCLE_R2_SERVER" \
    "MOCK_LIFE_R2_CLIENT=$LIFECYCLE_R2_CLIENT" \
    "MOCK_LIFE_R2_NETDEV=$LIFECYCLE_R2_NETDEV" \
    "MOCK_LIFE_R2_HOST=zenith-idle.test" \
    "MOCK_LIFE_MOUNT_OPTIONS=${LIFECYCLE_MOCK_MOUNT_OPTIONS:-ro,vers=4.2,proto=rdma,port=20049,hard,timeo=600,retrans=2}" \
    "MOCK_LIFE_OWNER_RDMA=${LIFECYCLE_MOCK_OWNER_RDMA:-up}" \
    "MOCK_LIFE_PREREQ_MODE=${LIFECYCLE_MOCK_PREREQ_MODE:-ready}" \
    "MOCK_LIFE_MOUNT_FAIL=${LIFECYCLE_MOCK_MOUNT_FAIL:-0}" \
    "$REPO_DIR/scripts/weight-fabric.sh" "$@"
}

ready_prereq_json=$(lifecycle_cmd prerequisites "$PROFILE" --json)
PREREQ_JSON="$ready_prereq_json" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["PREREQ_JSON"])
assert report["kind"] == "weight-fabric-prerequisites"
assert report["state"] == "ready"
assert report["ready"] is True
assert len(report["nodes"]) == 3
assert {node["role"] for node in report["nodes"]} == {"owner", "client"}
PY

ssh_lines_before=$(wc -l <"$LIFECYCLE_SSH_LOG")
if LIFECYCLE_MOCK_PREREQ_MODE=password \
    lifecycle_cmd setup-prerequisites "$PROFILE" --yes \
      >"$STATE_DIR/prerequisites-password.out" 2>&1; then
  echo "FAIL setup accepted password-protected remote sudo" >&2
  exit 1
fi
grep -Fq 'password-required' "$STATE_DIR/prerequisites-password.out"
tail -n "+$((ssh_lines_before + 1))" "$LIFECYCLE_SSH_LOG" \
  >"$STATE_DIR/prerequisites-password.ssh"
if grep -Fq 'apt-get install' "$STATE_DIR/prerequisites-password.ssh"; then
  echo "FAIL setup mutated packages before reporting sudo guidance" >&2
  exit 1
fi
interactive_prereq_json=$(
  LIFECYCLE_MOCK_PREREQ_MODE=password \
    lifecycle_cmd prerequisites "$PROFILE" --interactive-sudo --json
)
PREREQ_JSON="$interactive_prereq_json" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["PREREQ_JSON"])
assert report["state"] == "ready"
assert report["sudo_mode"] == "interactive"
assert report["ready"] is True
remote = [node for node in report["nodes"] if node["rank"] in (1, 2)]
assert all(node["passwordless_sudo"] == "password-required" for node in remote)
PY

rm -f -- "$LIFECYCLE_PREREQ_DIR"/*
if LIFECYCLE_MOCK_PREREQ_MODE=missing \
    lifecycle_cmd prerequisites "$PROFILE" --json \
      >"$STATE_DIR/prerequisites-missing.json"; then
  echo "FAIL prerequisites accepted missing owner/client tooling" >&2
  exit 1
fi
python3 - "$STATE_DIR/prerequisites-missing.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["state"] == "blocked"
nodes = {node["rank"]: node for node in report["nodes"]}
assert nodes[1]["nfs_server"] == "missing"
assert nodes[1]["hf_cli"] == "missing"
assert nodes[2]["nfs_client"] == "missing"
PY
ssh_lines_before=$(wc -l <"$LIFECYCLE_SSH_LOG")
LIFECYCLE_MOCK_PREREQ_MODE=missing \
  lifecycle_cmd setup-prerequisites "$PROFILE" --yes \
    >"$STATE_DIR/prerequisites-setup.out"
grep -Fq 'State     ready' "$STATE_DIR/prerequisites-setup.out"
tail -n "+$((ssh_lines_before + 1))" "$LIFECYCLE_SSH_LOG" \
  >"$STATE_DIR/prerequisites-setup.ssh"
grep -F 'orion-client.test|' "$STATE_DIR/prerequisites-setup.ssh" \
  | grep -F 'apt-get install -y --no-install-recommends' \
  | grep -F 'nfs-kernel-server' \
  | grep -Fq 'python3-venv'
grep -F 'zenith-idle.test|' "$STATE_DIR/prerequisites-setup.ssh" \
  | grep -F 'apt-get install -y --no-install-recommends' \
  | grep -Fq 'nfs-common'
grep -F 'orion-client.test|' "$STATE_DIR/prerequisites-setup.ssh" \
  | grep -F 'python3 -m venv' \
  | grep -Fq 'huggingface_hub==1.26.1'
if grep -Fq 'atlas-owner|' "$STATE_DIR/prerequisites-setup.ssh"; then
  echo "FAIL prerequisite setup treated the local fixture as an SSH target" >&2
  exit 1
fi
echo "OK   prerequisite workflow detects, guides, and installs only missing items"

lifecycle_cmd download "$PROFILE" --yes >/dev/null
grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -F '.hf-cli/venv/bin/hf' \
  | grep -Fq 'command -v hf'
grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -F '/mock-owner/.hf-cli/venv/bin/hf download' \
  | grep -F -- "--cache-dir $LIFECYCLE_OWNER_ROOT/hub" \
  | grep -Fq -- '--quiet'
grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -Fq 'python3 - manifest-create'
if grep -v '^orion-client.test|' "$LIFECYCLE_SSH_LOG" \
    | grep -Fq ' download '; then
  echo "FAIL authoritative download ran on a non-owner" >&2
  exit 1
fi
echo "OK   download discovers the owner venv and creates no rank copies"

LIFECYCLE_MODEL_DIR="$LIFECYCLE_OWNER_ROOT/hub/models--Qwen--Qwen3-1.7B"
LIFECYCLE_DEFAULT_MODEL_DIR="$LIFECYCLE_CLIENT_CACHE/hub/models--Qwen--Qwen3-1.7B"
mkdir -p "$LIFECYCLE_MODEL_DIR" "$LIFECYCLE_DEFAULT_MODEL_DIR"
: >"$LIFECYCLE_MODEL_DIR/replica"
: >"$LIFECYCLE_DEFAULT_MODEL_DIR/replica"
if lifecycle_cmd apply "$PROFILE" --yes >/dev/null 2>&1; then
  echo "FAIL apply accepted a durable client replica" >&2
  exit 1
fi
[ ! -e "$LIFECYCLE_EXPORT_CAPTURE" ]
[ ! -e "$LIFECYCLE_NFS_CAPTURE" ]
lifecycle_cmd purge-replicas "$PROFILE" --yes >/dev/null
[ ! -e "$LIFECYCLE_MODEL_DIR" ]
[ ! -e "$LIFECYCLE_DEFAULT_MODEL_DIR" ]
if grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
    | grep -Fq 'rm -rf'; then
  echo "FAIL purge attempted to delete the owner copy" >&2
  exit 1
fi

: >"$LIFECYCLE_ROOT_LOG"
LIFECYCLE_MOCK_PREREQ_MODE=password \
  lifecycle_cmd apply "$PROFILE" --interactive-sudo --yes >/dev/null
[ -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ -e "$LIFECYCLE_REMOTE_MOUNTED" ]
grep -Fq "\"$LIFECYCLE_EXPORT_PATH\"" "$LIFECYCLE_EXPORT_CAPTURE"
grep -Fq '[nfsd]' "$LIFECYCLE_NFS_CAPTURE"
grep -Fq 'rdma = 20049' "$LIFECYCLE_NFS_CAPTURE"
grep -Fq 'HOST orion-client.test' "$LIFECYCLE_ROOT_LOG"
grep -Fq 'systemctl enable --now nfs-server' "$LIFECYCLE_ROOT_LOG"
grep -Fq 'mount -t nfs4 -o' "$LIFECYCLE_ROOT_LOG"
grep -Fq 'proto=rdma' "$LIFECYCLE_ROOT_LOG"
grep -Fq 'port=20049' "$LIFECYCLE_ROOT_LOG"
grep -Fq 'sudo -v' "$LIFECYCLE_SSH_LOG"
LIFECYCLE_MOCK_PREREQ_MODE=password \
  lifecycle_cmd teardown "$PROFILE" --interactive-sudo --yes >/dev/null
[ ! -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ ! -e "$LIFECYCLE_REMOTE_MOUNTED" ]
[ ! -e "$LIFECYCLE_EXPORT_CAPTURE" ]
[ ! -e "$LIFECYCLE_NFS_CAPTURE" ]
echo "OK   interactive sudo applies and tears down with grouped per-node root scripts"

if LIFECYCLE_MOCK_R0_NETDEV=wrong0 \
    lifecycle_cmd apply "$PROFILE" --yes >/dev/null 2>&1; then
  echo "FAIL apply accepted a wrong client route" >&2
  exit 1
fi
[ ! -e "$LIFECYCLE_EXPORT_CAPTURE" ]
[ ! -e "$LIFECYCLE_NFS_CAPTURE" ]
if LIFECYCLE_MOCK_MOUNT_FAIL=1 \
    lifecycle_cmd apply "$PROFILE" --yes \
      >"$STATE_DIR/apply-rollback.out" 2>&1; then
  echo "FAIL apply reported success after a client mount failure" >&2
  exit 1
fi
[ ! -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ ! -e "$LIFECYCLE_REMOTE_MOUNTED" ]
[ ! -e "$LIFECYCLE_EXPORT_CAPTURE" ]
[ ! -e "$LIFECYCLE_NFS_CAPTURE" ]
grep -Fq 'rolled back the partial weight-fabric apply' \
  "$STATE_DIR/apply-rollback.out"
echo "OK   post-write apply failure rolls back exact files and mounts"
lifecycle_cmd apply "$PROFILE" --yes >/dev/null
[ -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ -e "$LIFECYCLE_REMOTE_MOUNTED" ]
grep -Fq "\"$LIFECYCLE_EXPORT_PATH\"" "$LIFECYCLE_EXPORT_CAPTURE"
grep -Fq \
  "$LIFECYCLE_R0_CLIENT(ro,sync,insecure,root_squash,anonuid=1000,anongid=1000,no_subtree_check)" \
  "$LIFECYCLE_EXPORT_CAPTURE"
grep -Fq \
  "$LIFECYCLE_R2_CLIENT(ro,sync,insecure,root_squash,anonuid=1000,anongid=1000,no_subtree_check)" \
  "$LIFECYCLE_EXPORT_CAPTURE"
if grep -Fq '192.0.2.' "$LIFECYCLE_EXPORT_CAPTURE"; then
  echo "FAIL export ACL used a control-LAN address" >&2
  exit 1
fi
grep -Fxq '[nfsd]' "$LIFECYCLE_NFS_CAPTURE"
grep -Fxq 'rdma = 20049' "$LIFECYCLE_NFS_CAPTURE"
grep -Fq \
  'mount -t nfs4 -o ro,vers=4.2,proto=rdma,port=20049,hard,timeo=600,retrans=2' \
  "$LIFECYCLE_SUDO_LOG"
grep -F "$LIFECYCLE_R2_SERVER:$LIFECYCLE_EXPORT_PATH" \
  "$LIFECYCLE_SSH_LOG" | grep -Fq 'proto=rdma'

if LIFECYCLE_MOCK_MOUNT_OPTIONS='ro,vers=4.2,proto=rdma' \
    lifecycle_cmd check "$PROFILE" --full --json >/dev/null 2>&1; then
  echo "FAIL readiness accepted incomplete NFS/RDMA mount policy" >&2
  exit 1
fi
owner_down_rc=0
owner_down_json=$(
  LIFECYCLE_MOCK_OWNER_RDMA=down \
    lifecycle_cmd check "$PROFILE" --full --json 2>/dev/null
) || owner_down_rc=$?
OWNER_DOWN_JSON="$owner_down_json" OWNER_DOWN_RC="$owner_down_rc" \
  python3 - <<'PY'
import json
import os

result = json.loads(os.environ["OWNER_DOWN_JSON"])
assert os.environ["OWNER_DOWN_RC"] == "1"
assert result["state"] == "owner-unready"
assert result["ok"] is False
PY
lifecycle_json=$(lifecycle_cmd check "$PROFILE" --full --json)
LIFECYCLE_JSON="$lifecycle_json" python3 - <<'PY'
import json
import os

result = json.loads(os.environ["LIFECYCLE_JSON"])
assert result["state"] == "ok"
assert result["ok"] is True
assert result["mode"] == "full"
assert len(result["nodes"]) == 3
assert all(
    node["route"]
    and node["mount"]
    and node["integrity"]
    and node["replica_absent"]
    for node in result["nodes"]
)
PY

LIFECYCLE_BUNDLE="$STATE_DIR/lifecycle-benchmark"
lifecycle_cmd benchmark "$PROFILE" \
  --source fabric \
  --serving-only \
  --cold \
  --yes \
  --max-mib-s 1 \
  --tag mocked-lifecycle \
  --output "$LIFECYCLE_BUNDLE" >/dev/null
python3 - "$LIFECYCLE_BUNDLE" <<'PY'
import json
import pathlib
import sys

bundle = pathlib.Path(sys.argv[1])
benchmark = json.loads(
    (bundle / "benchmark.json").read_text(encoding="utf-8")
)
audit = json.loads(
    (bundle / "artifact-audit.json").read_text(encoding="utf-8")
)
assert benchmark["state"] == "ok"
assert benchmark["source"] == "fabric"
assert benchmark["scope"] == "serving"
assert benchmark["cache_state"] == "cold"
assert benchmark["aggregate"]["rank_count"] == 2
assert benchmark["traffic_proof"]["state"] == "pass"
assert benchmark["measurement_kind"] == "fault-injection"
assert benchmark["rate_limit_mib_s"] == 1.0
assert benchmark["throughput_comparable"] is False
assert audit["state"] == "pass"
assert all(audit["checks"].values())
assert (bundle / "provenance.json").is_file()
assert (bundle / "manifest.json").is_file()
assert (bundle / "network-before.tsv").is_file()
assert (bundle / "network-after.tsv").is_file()
PY
if rg -q \
    'fixture-node-|atlas-owner|orion-client|zenith-idle|192\.0\.2\.|10\.10\.|remote-owner-hf|lifecycle-mount' \
    "$LIFECYCLE_BUNDLE"; then
  echo "FAIL shell benchmark bundle leaked private site data" >&2
  exit 1
fi
find "$LIFECYCLE_COUNTER_DIR" -type f -delete
LIFECYCLE_BUNDLE_3="$STATE_DIR/lifecycle-benchmark-3node"
lifecycle_cmd benchmark "$PROFILE" \
  --source fabric \
  --all-configured \
  --cold \
  --yes \
  --tag mocked-lifecycle-3node \
  --output "$LIFECYCLE_BUNDLE_3" >/dev/null
python3 - "$LIFECYCLE_BUNDLE_3" <<'PY'
import json
import pathlib
import sys

bundle = pathlib.Path(sys.argv[1])
benchmark = json.loads(
    (bundle / "benchmark.json").read_text(encoding="utf-8")
)
audit = json.loads(
    (bundle / "artifact-audit.json").read_text(encoding="utf-8")
)
assert benchmark["state"] == "ok"
assert benchmark["scope"] == "all-configured"
assert benchmark["aggregate"]["rank_count"] == 3
assert benchmark["traffic_proof"]["state"] == "pass"
assert benchmark["measurement_kind"] == "performance"
assert benchmark["rate_limit_mib_s"] is None
assert benchmark["throughput_comparable"] is True
assert audit["state"] == "pass"
assert all(audit["checks"].values())
PY
if rg -q \
    'fixture-node-|atlas-owner|orion-client|zenith-idle|192\.0\.2\.|10\.10\.|remote-owner-hf|lifecycle-mount' \
    "$LIFECYCLE_BUNDLE_3"; then
  echo "FAIL three-node benchmark bundle leaked private site data" >&2
  exit 1
fi
echo "OK   two/three-node shell benchmarks publish audited public evidence"

: >"$LIFECYCLE_SSH_LOG"
lifecycle_cmd unmount "$PROFILE" --yes >/dev/null
[ ! -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ ! -e "$LIFECYCLE_REMOTE_MOUNTED" ]
grep -F 'zenith-idle.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -Fq "docker ps -q --filter volume=$LIFECYCLE_MOUNT_PATH"

lifecycle_cmd apply "$PROFILE" --yes >/dev/null
: >"$LIFECYCLE_SSH_LOG"
lifecycle_cmd teardown "$PROFILE" --yes >/dev/null
[ ! -e "$LIFECYCLE_LOCAL_MOUNTED" ]
[ ! -e "$LIFECYCLE_REMOTE_MOUNTED" ]
grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -Fq \
      "/etc/exports.d/pulsar-weight-fabric-${LIFECYCLE_CONFIG_ID:0:12}.exports"
grep -F 'orion-client.test|' "$LIFECYCLE_SSH_LOG" \
  | grep -Fq \
      "/etc/nfs.conf.d/pulsar-weight-fabric-${LIFECYCLE_CONFIG_ID:0:12}.conf"
echo "OK   mocked lifecycle preserves owner and exact read-only RDMA scope"

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

FABRIC_SHIM="$STATE_DIR/fabric-shim"
python3 - "$FABRIC_SHIM" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
path.write_text(
    """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >>"${FABRIC_LOG:?}"
case " $* " in
  *" --json "*)
    printf '%s\\n' '{"state":"ok","source":"fabric","ok":true}'
    ;;
esac
""",
    encoding="utf-8",
)
path.chmod(0o755)
PY
: >"$STATE_DIR/fabric.log"

dry=$(
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY" \
  WEIGHT_FABRIC_CONFIG="$CONFIG_A" \
  PULSAR_WEIGHT_FABRIC_TOOL="$FABRIC_SHIM" \
  FABRIC_LOG="$STATE_DIR/fabric.log" \
  "$REPO_DIR/cluster/start-cluster.sh" "$PROFILE" \
    --weight-source fabric --dry-run --skip-preflight --skip-warmup
)
printf '%s\n' "$dry" | grep -Fq '[cluster] weights: fabric · NFS/RDMA'
printf '%s\n' "$dry" | grep -Fq -- \
  "-v $HF_ROOT/hub/models--Qwen--Qwen3-1.7B:/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B:ro"
printf '%s\n' "$dry" \
  | grep -F -- "-v $MOUNT_ROOT/$PROFILE-" \
  | grep -Fq -- \
      '/hub/models--Qwen--Qwen3-1.7B:/root/.cache/huggingface/hub/models--Qwen--Qwen3-1.7B:ro'
if printf '%s\n' "$dry" | grep -Fq -- \
    "-v $HF_ROOT:/root/.cache/huggingface"; then
  echo "FAIL fabric launch exposed the owner's full Hugging Face home" >&2
  exit 1
fi
printf '%s\n' "$dry" | grep -Fq -- \
  '--label io.pulsar.gb10.weight-source=fabric'
printf '%s\n' "$dry" | grep -Eq -- \
  '--label io\.pulsar\.gb10\.launch-contract=[0-9a-f]{64}'
printf '%s\n' "$dry" | grep -Fq -- \
  '--label io.pulsar.gb10.spec-decode=off'
printf '%s\n' "$dry" | grep -Fq -- \
  '--label io.pulsar.gb10.weight-owner=fixture-node-0'
config_id=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["configuration_id"])' \
  "$CONFIG_A")
printf '%s\n' "$dry" | grep -Fq -- \
  "--label io.pulsar.gb10.weight-config=$config_id"
grep -Fxq "check $PROFILE --serving-only" "$STATE_DIR/fabric.log"
echo "OK   dry launch exposes only the exact repository with provenance labels"

: >"$STATE_DIR/fabric.log"
PULSAR_WEIGHT_FABRIC_TOOL="$FABRIC_SHIM" FABRIC_LOG="$STATE_DIR/fabric.log" \
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY" WEIGHT_FABRIC_CONFIG="$CONFIG_A" \
  "$REPO_DIR/scripts/check-weights.sh" "$PROFILE" \
    --weight-source fabric --json >/dev/null
grep -Fxq "check $PROFILE --serving-only --json" \
  "$STATE_DIR/fabric.log"

: >"$STATE_DIR/fabric.log"
PULSAR_WEIGHT_FABRIC_TOOL="$FABRIC_SHIM" FABRIC_LOG="$STATE_DIR/fabric.log" \
  CLUSTER_TOPOLOGY_FILE="$TOPOLOGY" WEIGHT_FABRIC_CONFIG="$CONFIG_A" \
  "$REPO_DIR/scripts/pull-weights.sh" "$PROFILE" \
    --weight-source fabric --yes >/dev/null
grep -Fxq "download $PROFILE --yes" "$STATE_DIR/fabric.log"
echo "OK   standard weight commands preserve explicit fabric semantics"

echo "selftest-weight-fabric PASS"
