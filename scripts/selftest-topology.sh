#!/usr/bin/env bash
# Deterministic hostname-agnostic three-node topology tests with local shims.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-topology-test.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT

python3 - "$tmpdir" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
nodes = [
    {
        "hostname": "atlas-lab",
        "ssh_host": "atlas-lab.local",
        "control": ("mgmt0", "192.168.50.10"),
        "rdma": [
            ("roceA0", "data01a", "10.10.1.1/24"),
            ("roceA1", "data01b", "10.10.2.1/24"),
            ("roceA2", "data02a", "10.10.3.1/24"),
            ("roceA3", "data02b", "10.10.4.1/24"),
        ],
    },
    {
        "hostname": "orion-box",
        "ssh_host": "orion-box.local",
        "control": ("admin9", "192.168.50.11"),
        "rdma": [
            ("mlxB0", "peer10a", "10.10.1.2/24"),
            ("mlxB1", "peer10b", "10.10.2.2/24"),
            ("mlxB2", "peer12a", "10.10.5.1/24"),
            ("mlxB3", "peer12b", "10.10.6.1/24"),
        ],
    },
    {
        "hostname": "zenith-gb10",
        "ssh_host": "zenith-gb10.local",
        "control": ("lan42", "192.168.50.12"),
        "rdma": [
            ("fabricC0", "left0", "10.10.3.2/24"),
            ("fabricC1", "left1", "10.10.4.2/24"),
            ("fabricC2", "right0", "10.10.5.2/24"),
            ("fabricC3", "right1", "10.10.6.2/24"),
        ],
    },
]
for rank, node in enumerate(nodes):
    record = {
        "probe_schema_version": 1,
        "local": rank == 0,
        "ssh_host": "local" if rank == 0 else node["ssh_host"],
        "node_id": f"fixture-node-{rank}",
        "hostname": node["hostname"],
        "arch": "aarch64",
        "gpu": "NVIDIA GB10",
        "docker_ok": True,
        "docker_nvidia": True,
        "control": {
            "interface": node["control"][0],
            "ip": node["control"][1],
        },
        "rdma": [
            {"hca": hca, "netdev": netdev, "cidrs": [cidr]}
            for hca, netdev, cidr in node["rdma"]
        ],
        "qualified": True,
        "reject_reasons": [],
    }
    (root / f"probe-{rank}.json").write_text(
        json.dumps(record), encoding="utf-8"
    )
PY

tool="$REPO_DIR/scripts/topology_manifest.py"
python3 "$tool" assemble \
  "$tmpdir/probe-0.json" "$tmpdir/probe-1.json" "$tmpdir/probe-2.json" \
  "$tmpdir/probe-1.json" \
  >"$tmpdir/discovery.json"
python3 "$tool" validate "$tmpdir/discovery.json"

if python3 "$tool" rows "$tmpdir/discovery.json" >/dev/null 2>&1; then
  echo "FAIL unverified topology exposed active rows" >&2
  exit 1
fi

ping_count=$(python3 "$tool" ping-plan "$tmpdir/discovery.json" | wc -l)
[ "$ping_count" -eq 12 ] \
  || { echo "FAIL expected 12 directed rail checks, got $ping_count" >&2; exit 1; }

grep -Fq '"$remote_ping" </dev/null' "$REPO_DIR/scripts/detect-fabric.sh" \
  || { echo "FAIL remote connectivity probe can consume the remaining plan" >&2; exit 1; }

python3 "$tool" mark-verified "$tmpdir/discovery.json" >"$tmpdir/verified.json"
rendered=$(COLUMNS=48 python3 "$tool" render "$tmpdir/verified.json" --skipped-ssh 15)
RENDERED="$rendered" python3 - <<'PY'
import os

text = os.environ["RENDERED"]
lines = text.splitlines()
flat = " ".join(line.strip() for line in lines)
assert lines and max(map(len, lines)) <= 48
assert lines[0] == "CLUSTER DISCOVERY"
assert "\nNODES\n" in text
assert "3 GB10 systems" in flat
assert "PASS · 12/12 connections reachable" in flat
assert "this node" in text
assert "cluster node 2" in text
assert "cluster node 3" in text
assert all(name in text for name in ("roceA0", "mlxB0", "fabricC0"))
assert "DISCOVERY NOTES" in text
assert "same system already listed as orion-box" in flat
assert "15 advertised addresses could not be checked" in flat
PY

save_rendered=$(COLUMNS=48 python3 "$tool" render-save \
  "$tmpdir/verified.json" "$tmpdir/topology.json")
SAVE_RENDERED="$save_rendered" python3 - <<'PY'
import os

text = os.environ["SAVE_RENDERED"]
lines = text.splitlines()
flat = " ".join(line.strip() for line in lines)
assert lines and max(map(len, lines)) <= 48
assert lines[0] == "SAVE CLUSTER MEMBERSHIP"
assert "3 nodes shown above" in flat
assert "exact configurations that fit capacity" in flat
assert "does not validate or create a model profile" in flat
PY

python3 "$tool" write "$tmpdir/verified.json" "$tmpdir/topology.json"

runtime_log="$tmpdir/runtime-probes.log"
docker_shim="$tmpdir/docker-shim"
ssh_shim="$tmpdir/ssh-shim"
cat >"$docker_shim" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[ "${1:-}" = ps ] || exit 2
printf '%s\n' local >>"${RUNTIME_LOG:?}"
[ "${FAIL_HOST:-}" != local ] || exit 1
[ "${RUNNING_HOST:-}" != local ] || printf '%s\n' local-managed-id
SH
chmod +x "$docker_shim"
cat >"$ssh_shim" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
host=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = -- ]; then
    shift
    host="${1:-}"
    break
  fi
  shift
done
[ -n "$host" ] || exit 2
printf '%s\n' "$host" >>"${RUNTIME_LOG:?}"
[ "$host" != "${FAIL_HOST:-}" ] || exit 1
[ "$host" != "${RUNNING_HOST:-}" ] || printf '%s\n' remote-managed-id
SH
chmod +x "$ssh_shim"

: >"$runtime_log"
CLUSTER_TOPOLOGY_FILE="$tmpdir/topology.json" \
PULSAR_DOCKER="$docker_shim" PULSAR_SSH="$ssh_shim" \
RUNTIME_LOG="$runtime_log" bash -c '
  set -euo pipefail
  . "'"$REPO_DIR"'/scripts/lib.sh"
  require_topology_rewrite_idle "$CLUSTER_TOPOLOGY_FILE"
'
grep -Fxq local "$runtime_log"
grep -Fxq orion-box.local "$runtime_log"
grep -Fxq zenith-gb10.local "$runtime_log"

if CLUSTER_TOPOLOGY_FILE="$tmpdir/topology.json" \
    PULSAR_DOCKER="$docker_shim" PULSAR_SSH="$ssh_shim" \
    RUNTIME_LOG="$runtime_log" RUNNING_HOST=zenith-gb10.local bash -c '
      . "'"$REPO_DIR"'/scripts/lib.sh"
      require_topology_rewrite_idle "$CLUSTER_TOPOLOGY_FILE"
    ' >/dev/null 2>&1; then
  echo "FAIL topology rewrite allowed a running remote managed rank" >&2
  exit 1
fi
if CLUSTER_TOPOLOGY_FILE="$tmpdir/topology.json" \
    PULSAR_DOCKER="$docker_shim" PULSAR_SSH="$ssh_shim" \
    RUNTIME_LOG="$runtime_log" FAIL_HOST=orion-box.local bash -c '
      . "'"$REPO_DIR"'/scripts/lib.sh"
      require_topology_rewrite_idle "$CLUSTER_TOPOLOGY_FILE"
    ' >/dev/null 2>&1; then
  echo "FAIL topology rewrite ignored an unobservable prior rank" >&2
  exit 1
fi

CLUSTER_TOPOLOGY_FILE="$tmpdir/topology.json" bash -c '
  set -euo pipefail
  . "'"$REPO_DIR"'/scripts/lib.sh"
  load_cluster_topology
  [ "$CLUSTER_TOPOLOGY_COUNT" = 3 ]
  [ "${CLUSTER_NODE_HOSTNAMES[1]}" = orion-box ]
  [ "${CLUSTER_NODE_CONTROL_IFS[2]}" = lan42 ]
  [ "${CLUSTER_PAIR_RAILS["0:1"]}" = 2 ]
  [ "${CLUSTER_PAIR_RAILS["0:2"]}" = 2 ]
  [ "${CLUSTER_PAIR_RAILS["1:2"]}" = 2 ]
  require_profile_topology 2 roce-full-mesh 2
  [ "${CLUSTER_PROFILE_HCAS[0]}" = roceA0,roceA1 ]
  [ "${CLUSTER_PROFILE_HCAS[1]}" = mlxB0,mlxB1 ]
  require_profile_topology 3 roce-full-mesh 2
  [ "${CLUSTER_PROFILE_HCAS[0]}" = roceA0,roceA1,roceA2,roceA3 ]
  [ "${CLUSTER_PROFILE_HCAS[1]}" = mlxB0,mlxB1,mlxB2,mlxB3 ]
  [ "${CLUSTER_PROFILE_HCAS[2]}" = fabricC0,fabricC1,fabricC2,fabricC3 ]
  ! require_cluster_nodes 4 >/dev/null 2>&1
'

# Launch resolution requires ready library views (ADR 0006); shim them.
fixture_topology_id=$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["topology_id"])' \
  "$tmpdir/topology.json")
python3 "$REPO_DIR/scripts/testlib/library_hot_fixture.py" \
  "$tmpdir/hot-info.json" --profile qwen3-1.7b-2node \
  --topology-id "$fixture_topology_id"
verify_ssh_shim="$tmpdir/verify-ssh-shim"
cat >"$verify_ssh_shim" <<'SH'
#!/usr/bin/env bash
cat >/dev/null
exit 0
SH
chmod +x "$verify_ssh_shim"
dry_output=$(CLUSTER_TOPOLOGY_FILE="$tmpdir/topology.json" \
  PULSAR_MODEL_LIBRARY_PY="$REPO_DIR/scripts/testlib/fake_model_library.py" \
  FAKE_HOT_INFO_FILE="$tmpdir/hot-info.json" \
  PULSAR_SSH="$verify_ssh_shim" \
  "$REPO_DIR/cluster/start-cluster.sh" qwen3-1.7b-2node \
    --dry-run --skip-preflight --skip-warmup)
printf '%s\n' "$dry_output" | grep -q -- '--nnodes 2'
printf '%s\n' "$dry_output" | grep -q -- 'NCCL_NET=IB'
printf '%s\n' "$dry_output" | grep -q -- 'NCCL_SOCKET_IFNAME=admin9'
printf '%s\n' "$dry_output" | grep -q -- 'mlxB0'
printf '%s\n' "$dry_output" | grep -Fq -- 'NCCL_IB_HCA=roceA0\,roceA1'
printf '%s\n' "$dry_output" | grep -Fq -- 'NCCL_IB_HCA=mlxB0\,mlxB1'
if printf '%s\n' "$dry_output" | grep -Eq 'roceA[23]|mlxB[23]'; then
  echo "FAIL two-node profile inherited HCAs for idle rank 2" >&2
  exit 1
fi
if printf '%s\n' "$dry_output" | grep -q '^  rank 2 '; then
  echo "FAIL exact two-node profile started discovered rank 2" >&2
  exit 1
fi

COLUMNS=50 python3 "$tool" render "$tmpdir/topology.json" >"$tmpdir/rendered"
if awk 'length($0) > 60 {exit 1}' "$tmpdir/rendered"; then
  :
else
  echo "FAIL topology output is too wide at COLUMNS=50" >&2
  exit 1
fi

# AUD-01: legacy HEAD_IP/WORKER_IP environment variables never confirm
# membership. Every multi-node admission path refuses before network or
# Docker work when the confirmed manifest is absent.
legacy_rc=0
legacy_out=$(CLUSTER_TOPOLOGY_FILE="$tmpdir/absent-topology.json" bash -c '
  export HEAD_IP=192.0.2.1 WORKER_IP=192.0.2.2 NCCL_IB_HCA=hca0,hca1
  . "'"$REPO_DIR"'/cluster/topology.sh"
  require_profile_topology 2 roce-full-mesh 2
' 2>&1) || legacy_rc=$?
[ "$legacy_rc" -ne 0 ] \
  || { echo "FAIL legacy env vars admitted a two-node profile topology" >&2; exit 1; }
printf '%s\n' "$legacy_out" | grep -q 'detect-fabric.sh --write-topology' \
  || { echo "FAIL refusal does not direct to confirmed discovery" >&2; exit 1; }

: >"$runtime_log"
legacy_rc=0
CLUSTER_TOPOLOGY_FILE="$tmpdir/absent-topology.json" \
  HEAD_IP=192.0.2.1 WORKER_IP=192.0.2.2 \
  PULSAR_DOCKER="$docker_shim" PULSAR_SSH="$ssh_shim" \
  RUNTIME_LOG="$runtime_log" \
  "$REPO_DIR/cluster/preflight.sh" qwen3-1.7b-2node \
  >/dev/null 2>&1 || legacy_rc=$?
[ "$legacy_rc" -ne 0 ] \
  || { echo "FAIL preflight accepted legacy env topology" >&2; exit 1; }
[ ! -s "$runtime_log" ] \
  || { echo "FAIL preflight probed hosts without a confirmed manifest" >&2; exit 1; }

: >"$runtime_log"
legacy_rc=0
CLUSTER_TOPOLOGY_FILE="$tmpdir/absent-topology.json" \
  HEAD_IP=192.0.2.1 WORKER_IP=192.0.2.2 \
  PULSAR_DOCKER="$docker_shim" PULSAR_SSH="$ssh_shim" \
  RUNTIME_LOG="$runtime_log" \
  "$REPO_DIR/cluster/start-cluster.sh" qwen3-1.7b-2node \
  --dry-run --skip-preflight --skip-warmup >/dev/null 2>&1 || legacy_rc=$?
[ "$legacy_rc" -ne 0 ] \
  || { echo "FAIL start-cluster accepted legacy env topology" >&2; exit 1; }
[ ! -s "$runtime_log" ] \
  || { echo "FAIL start-cluster probed hosts without a confirmed manifest" >&2; exit 1; }
echo "OK   legacy env vars cannot admit multi-node launch paths"

echo "selftest-topology PASS"
