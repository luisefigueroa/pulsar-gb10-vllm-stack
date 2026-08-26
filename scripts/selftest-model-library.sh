#!/usr/bin/env bash
# Unit/selftests for federated model library catalog (no Docker / no cluster SSH).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_DIR/scripts/model_library.py"
STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-model-library.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

pass=0
fail=0

ok() { echo "OK   $1"; pass=$((pass + 1)); }
not_ok() { echo "FAIL $1" >&2; fail=$((fail + 1)); }

assert_eq() {
  local label="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then ok "$label"; else
    not_ok "$label (got=$got want=$want)"
  fi
}

assert_true() {
  local label="$1"
  shift
  if "$@"; then ok "$label"; else not_ok "$label"; fi
}

# --- fixture: complete hub tree ---
make_complete_hub() {
  local root="$1" model_id="$2" rev="${3:-abc123def456}"
  local hub_name dir snap
  hub_name="models--${model_id//\//--}"
  dir="$root/hub/$hub_name"
  snap="$dir/snapshots/$rev"
  mkdir -p "$snap" "$dir/refs"
  printf '%s\n' "$rev" >"$dir/refs/main"
  printf '{"architectures":["X"]}\n' >"$snap/config.json"
  printf 'weights\n' >"$snap/model.safetensors"
}

make_partial_hub() {
  local root="$1" model_id="$2"
  local hub_name dir
  hub_name="models--${model_id//\//--}"
  dir="$root/hub/$hub_name"
  mkdir -p "$dir/refs"
  printf 'deadbeef\n' >"$dir/refs/main"
  # missing snapshot → partial
}

NODE0="$STATE/node0"
NODE1="$STATE/node1"
mkdir -p "$NODE0" "$NODE1" "$STATE/models"
make_complete_hub "$NODE0" "Qwen/Qwen3-1.7B"
make_complete_hub "$NODE1" "Qwen/Qwen3-1.7B"
make_complete_hub "$NODE0" "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4" rev2
make_partial_hub "$NODE1" "SomeOrg/Unfinished"

# Minimal confs for labeling
cat >"$STATE/models/qwen3-1.7b-2node.conf" <<'EOF'
MODEL="Qwen/Qwen3-1.7B"
STATUS="tested"
NODES=2
EOF
cat >"$STATE/models/unvalidated-demo.conf" <<'EOF'
MODEL="SomeOrg/Unfinished"
STATUS="experimental"
NODES=1
EOF
cat >"$STATE/models/nfs-demo.conf" <<'EOF'
MODEL="/mnt/Models/Official Models/demo"
STATUS="tested"
NODES=1
EOF

# scan-hub
scan0=$(python3 "$PY" scan-hub --cache-root "$NODE0" --rank 0 --node-id node-a --hostname host-a)
scan1=$(python3 "$PY" scan-hub --cache-root "$NODE1" --rank 1 --node-id node-b --hostname host-b)
count0=$(printf '%s' "$scan0" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
assert_eq "scan node0 finds 2 hub dirs" "$count0" "2"

# merge homes
printf '%s\n' "$scan0" >"$STATE/h0.json"
printf '%s\n' "$scan1" >"$STATE/h1.json"
python3 - <<PY
import json
from pathlib import Path
homes = []
for p in ("$STATE/h0.json", "$STATE/h1.json"):
    homes.extend(json.loads(Path(p).read_text()))
Path("$STATE/homes.json").write_text(json.dumps(homes), encoding="utf-8")
PY

python3 "$PY" build \
  --topology-id topo-test-001 \
  --models-dir "$STATE/models" \
  --homes-json "$STATE/homes.json" \
  --output "$STATE/catalog.json" \
  --json >"$STATE/catalog.out.json"

if STATE="$STATE" python3 - <<'PY'
import json, os
from pathlib import Path
cat = json.loads(Path(os.environ["STATE"], "catalog.json").read_text())
assert cat["schema_version"] == 2
assert cat["topology_id"] == "topo-test-001"
models = {m["model_id"]: m for m in cat["models"]}
q = models["Qwen/Qwen3-1.7B"]
assert q["validation"] == "legacy-unsealed", q
assert q["duplicate"] is True
assert q["has_primary"] is False
assert "qwen3-1.7b-2node" in q["profiles"]
nano = models["nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"]
assert nano["validation"] == "unvalidated"
assert nano["duplicate"] is False
assert nano["has_primary"] is True
assert "/mnt/Models" not in json.dumps(cat)
PY
then
  ok "catalog labels + duplicates"
else
  not_ok "catalog labels + duplicates"
fi

# resolve without primary must fail
set +e
python3 "$PY" resolve --catalog "$STATE/catalog.json" --json qwen3-1.7b-2node >"$STATE/resolve.err" 2>&1
rc=$?
set -e
assert_eq "resolve duplicate without primary fails" "$rc" "1"
assert_true "resolve error mentions cleanup" grep -q "cleanup-recommend\|duplicate" "$STATE/resolve.err"

# with primary override
python3 "$PY" build \
  --topology-id topo-test-001 \
  --models-dir "$STATE/models" \
  --homes-json "$STATE/homes.json" \
  --primary "Qwen/Qwen3-1.7B=node-b" \
  --output "$STATE/catalog2.json" >/dev/null 2>&1

python3 "$PY" resolve --catalog "$STATE/catalog2.json" --json qwen3-1.7b-2node >"$STATE/resolve.ok"
home_node=$(python3 -c 'import json; print(json.load(open("'"$STATE/resolve.ok"'"))["home"]["node_id"])')
assert_eq "resolve uses primary node-b" "$home_node" "node-b"

# cleanup-recommend lists qwen
recs=$(python3 "$PY" cleanup-recommend --catalog "$STATE/catalog.json" --json)
python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["recommendations"]; assert d["recommendations"][0]["model_id"]=="Qwen/Qwen3-1.7B"' <<<"$recs" \
  && ok "cleanup-recommend lists duplicate" || not_ok "cleanup-recommend lists duplicate"

# CLI wrapper local-only path needs topology — skip full refresh; smoke --help
assert_true "model-library.sh help" bash -c "'$REPO_DIR/scripts/model-library.sh' --help | grep -q 'Model library'"
assert_true "model-library.sh is executable" test -x "$REPO_DIR/scripts/model-library.sh"
assert_true "validation-bundle verify is not documented in CLI" \
  bash -c "! '$REPO_DIR/scripts/model-library.sh' --help | grep -q 'validation-bundle verify'"
set +e
reviewed_list_out=$(MODEL_LIBRARY_CATALOG="$STATE/catalog.json" \
  "$REPO_DIR/scripts/model-library.sh" catalog list --reviewed-identity --json 2>&1)
reviewed_list_rc=$?
bundle_out=$("$REPO_DIR/scripts/model-library.sh" \
  validation-bundle verify qwen3-1.7b-2node --json 2>&1)
bundle_rc=$?
validated_alias_out=$(MODEL_LIBRARY_CATALOG="$STATE/catalog.json" \
  "$REPO_DIR/scripts/model-library.sh" catalog list --validated --json 2>&1)
validated_alias_rc=$?
set -e
assert_eq "catalog list --reviewed-identity fails closed" "$reviewed_list_rc" "1"
assert_true "catalog list --reviewed-identity names ADR 0012" \
  grep -q "ADR 0012" <<<"$reviewed_list_out"
assert_eq "validation-bundle verify fails closed" "$bundle_rc" "1"
assert_true "validation-bundle verify names ADR 0012" \
  grep -q "ADR 0012" <<<"$bundle_out"
assert_eq "catalog list --validated fails closed" "$validated_alias_rc" "2"

# never write under HF_CACHE: build output is only catalog path
assert_true "catalog written outside node caches" test -f "$STATE/catalog.json"
assert_true "node0 hub untouched by build" test -f "$NODE0/hub/models--Qwen--Qwen3-1.7B/refs/main"

# --- hot staging: plan / stamp / budget / pin / purge (local only) ---
HOT="$STATE/hot"
export PULSAR_HOT_ROOT="$HOT"
export PULSAR_HOT_BUDGET_BYTES=$((50 * 1024 * 1024))
export PULSAR_HOT_RESERVE_BYTES=0

plan=$(python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --backend copy \
  --nodes 1)
action=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
assert_eq "plan-activate wants copy" "$action" "copy"
instance=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
hub_src=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_source"])')
hub_dst=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.load(sys.stdin)["hub_dest"])')
assert_true "hot dest not under HF cache" bash -c "[[ '$hub_dst' != *'/.cache/huggingface'* ]]"

mkdir -p "$(dirname "$hub_dst")"
rm -rf "$hub_dst"
mkdir -p "$hub_dst"
rsync -a "$hub_src"/ "$hub_dst"/
stamp_json=$(printf '%s' "$plan" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["stamp"]))')
python3 "$PY" write-hot-stamp --instance-dir "$instance" --stamp-json "$stamp_json" >/dev/null
python3 "$PY" verify-hot --instance-dir "$instance" --profile qwen3-1.7b-2node --topology-id topo-test-001 --models-dir "$STATE/models" >/dev/null \
  && ok "verify-hot after local copy" || not_ok "verify-hot after local copy"

plan2=$(python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --backend copy \
  --nodes 1)
action2=$(printf '%s' "$plan2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
assert_eq "second plan skips matching hot" "$action2" "skip"

python3 "$PY" set-pinned --instance-dir "$instance" --pinned >/dev/null
set +e
python3 "$PY" purge-hot --instance-dir "$instance" >/dev/null 2>&1
prc=$?
set -e
assert_eq "purge refuses pinned" "$prc" "1"
python3 "$PY" purge-hot --instance-dir "$instance" --force-unpin >/dev/null \
  && ok "purge with force-unpin" || not_ok "purge with force-unpin"
assert_true "instance removed" bash -c "test ! -d '$instance'"

# budget math
python3 "$PY" budget --hot-root "$HOT" --json >"$STATE/budget.json"
python3 -c 'import json; d=json.load(open("'"$STATE/budget.json"'")); assert d["budget_bytes"]==50*1024*1024; assert d["used_bytes"]==0' \
  && ok "budget empty after purge" || not_ok "budget empty after purge"

# Per-rank admission reports an explicit hard-cap refusal without writing.
export PULSAR_HOT_BUDGET_BYTES=1
python3 "$PY" budget-admission \
  --hot-root "$HOT" \
  --rank 0 \
  --node-id node-a \
  --runtime-source sealed-hot \
  --required-owned-bytes 2 \
  --compact >"$STATE/budget-blocked.json"
python3 -c 'import json; d=json.load(open("'"$STATE/budget-blocked.json"'")); assert d["state"]=="blocked"; assert d["blockers"][0]["code"]=="hard-cap-exceeded"' \
  && ok "budget admission refuses hard cap" || not_ok "budget admission refuses hard cap"

# --- fabric plan (rails) without privileged NFS ---
export PULSAR_HOT_BUDGET_BYTES=$((50 * 1024 * 1024))
STATE="$STATE" REPO_DIR="$REPO_DIR" python3 - <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["REPO_DIR"])
from scripts.topology_manifest import topology_digest
state = Path(os.environ["STATE"])
topo = {
  "schema_version": 1,
  "class": "roce-full-mesh",
  "connectivity_verified": True,
  "validation": {
    "class": "roce-full-mesh",
    "full_mesh": True,
    "min_rails_per_pair": 1,
    "connectivity_verified": True,
  },
  "nodes": [
    {
      "rank": 0,
      "node_id": "node-a",
      "hostname": "host-a",
      "ssh_host": "host-a.local",
      "arch": "aarch64",
      "gpu": "NVIDIA GB10",
      "control": {"interface": "mgmt0", "ip": "192.168.1.10"},
      "rdma": [
        {"hca": "roce0", "netdev": "enp1s0f0", "cidrs": ["10.0.0.1/24"]}
      ],
    },
    {
      "rank": 1,
      "node_id": "node-b",
      "hostname": "host-b",
      "ssh_host": "host-b.local",
      "arch": "aarch64",
      "gpu": "NVIDIA GB10",
      "control": {"interface": "mgmt0", "ip": "192.168.1.11"},
      "rdma": [
        {"hca": "roce0", "netdev": "enp1s0f0", "cidrs": ["10.0.0.2/24"]}
      ],
    },
  ],
  "links": [
    {
      "ranks": [0, 1],
      "rails": [
        {
          "network": "10.0.0.0/24",
          "a": {"hca": "roce0", "netdev": "enp1s0f0", "ip": "10.0.0.1"},
          "b": {"hca": "roce0", "netdev": "enp1s0f0", "ip": "10.0.0.2"},
        }
      ],
    }
  ],
}
topo["topology_id"] = topology_digest(topo)
(state / "topology.json").write_text(json.dumps(topo), encoding="utf-8")
(state / "topology_id.txt").write_text(topo["topology_id"], encoding="utf-8")
PY
TOPO_ID=$(cat "$STATE/topology_id.txt")

python3 "$PY" build \
  --topology-id "$TOPO_ID" \
  --models-dir "$STATE/models" \
  --homes-json "$STATE/homes.json" \
  --primary "Qwen/Qwen3-1.7B=node-b" \
  --output "$STATE/catalog-fabric.json" >/dev/null 2>&1

# Retired fabric preparation fails closed (ADR 0006).
set +e
python3 "$PY" plan-activate \
  --catalog "$STATE/catalog-fabric.json" \
  --profile qwen3-1.7b-2node \
  --topology-id "$TOPO_ID" \
  --topology-file "$STATE/topology.json" \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --backend fabric \
  --nodes 2 >/dev/null 2>"$STATE/plan-fabric.err"
fab_rc=$?
python3 "$PY" plan-activate \
  --catalog "$STATE/catalog-fabric.json" \
  --profile qwen3-1.7b-2node \
  --topology-id "$TOPO_ID" \
  --topology-file "$STATE/topology.json" \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --transport nfs-rdma \
  --nodes 2 >/dev/null 2>"$STATE/plan-nfs-rdma.err"
nfs_rc=$?
python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --backend nfs >/dev/null 2>&1
bad_rc=$?
set -e
assert_true "retired fabric backend rejected" test "$fab_rc" -ne 0
assert_true "retired nfs-rdma transport rejected" test "$nfs_rc" -ne 0
assert_true "unknown backend rejected" test "$bad_rc" -ne 0

# Launch wiring is library-only (no docker)
assert_true "up.sh has no weight-mode axis" \
  bash -c "! grep -q 'WEIGHT_SOURCE=' '$REPO_DIR/scripts/up.sh'"
assert_true "start-cluster labels launches library-hot" \
  bash -c "grep -q write_launch_plan_file '$REPO_DIR/cluster/start-cluster.sh' && grep -q 'LABEL_WEIGHT_SOURCE' '$REPO_DIR/scripts/launch_plan.py' && grep -q 'STORAGE_MECHANISM = \"library-hot\"' '$REPO_DIR/scripts/launch_plan.py'"
assert_true "cluster launch validates remote expected identity" \
  grep -q -- --expected-validation-json "$REPO_DIR/cluster/start-cluster.sh"
assert_true "cluster launch uses serve-time witness with full-verify fallback" \
  grep -q -- --serve-time-witness "$REPO_DIR/cluster/start-cluster.sh"
assert_true "preparation full-verifies and refreshes rank-local witnesses" \
  grep -q -- --refresh-witness "$REPO_DIR/scripts/model-library.sh"
out=$(set +e; CLUSTER_TOPOLOGY_FILE="$STATE/no-topology.json" \
  "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b-2node 2>&1; true)
assert_true "check-weights fails closed without confirmed topology" \
  bash -c "printf '%s\n' $(printf '%q' "$out") | grep -q 'confirmed topology manifest'"
out=$(set +e; "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --weight-source library-hot 2>&1; true)
assert_true "removed weight-mode flag fails closed" \
  bash -c "printf '%s\n' $(printf '%q' "$out") | grep -q 'ADR 0006'"
assert_true "down.sh documents pin-weights" \
  grep -q pin-weights "$REPO_DIR/scripts/down.sh"
assert_true "fabric transfer plane is fully removed" \
  bash -c "! grep -q fabric_apply_transfer '$REPO_DIR/scripts/model-library.sh'"

# --- optional cold storage tier ---
COLD="$STATE/cold"
FLAT_ORG="$COLD/Official Models/DemoOrg"
FLAT_COMPLETE="$FLAT_ORG/Demo-Model-Complete"
FLAT_PARTIAL="$FLAT_ORG/Demo-Model-Partial"
HUB_COLD="$COLD/hub/models--ColdOrg--HubModel"
mkdir -p "$FLAT_COMPLETE" "$FLAT_PARTIAL" "$HUB_COLD/snapshots/revcold1" "$HUB_COLD/refs"
# flat complete
printf '{"architectures":["X"]}\n' >"$FLAT_COMPLETE/config.json"
printf 'weights\n' >"$FLAT_COMPLETE/model.safetensors"
# flat partial (config only)
printf '{"architectures":["X"]}\n' >"$FLAT_PARTIAL/config.json"
# hub complete under cold
printf 'revcold1\n' >"$HUB_COLD/refs/main"
printf '{"architectures":["X"]}\n' >"$HUB_COLD/snapshots/revcold1/config.json"
printf 'weights\n' >"$HUB_COLD/snapshots/revcold1/model.safetensors"

# profile that only exists on cold (HF id matching Official Models org/name)
cat >"$STATE/models/demo-cold-only.conf" <<'EOF'
MODEL="DemoOrg/Demo-Model-Complete"
STATUS="tested"
NODES=1
EOF
cat >"$STATE/models/demo-cold-abs.conf" <<'EOF'
MODEL="/mnt/Models/Official Models/DemoOrg/Demo-Model-Complete"
STATUS="tested"
NODES=1
EOF

# empty warm homes catalog for cold-only resolve
python3 "$PY" build \
  --topology-id topo-cold-001 \
  --models-dir "$STATE/models" \
  --homes-json "$STATE/homes.json" \
  --output "$STATE/catalog-cold.json" >/dev/null 2>&1

export PULSAR_COLD_ROOT="$COLD"
unset MODELS_NFS || true

scan_cold=$(python3 "$PY" scan-cold --cold-root "$COLD" --json)
cold_count=$(printf '%s' "$scan_cold" | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])')
assert_eq "scan-cold finds 3 trees" "$cold_count" "3"

complete_cold=$(python3 "$PY" scan-cold --cold-root "$COLD" --complete-only --json)
complete_n=$(printf '%s' "$complete_cold" | python3 -c 'import json,sys; print(json.load(sys.stdin)["count"])')
assert_eq "scan-cold complete-only is 2" "$complete_n" "2"

find_flat=$(python3 "$PY" find-cold --cold-root "$COLD" --json DemoOrg/Demo-Model-Complete 2>/dev/null || \
  python3 "$PY" find-cold --cold-root "$COLD" DemoOrg/Demo-Model-Complete)
flat_layout=$(printf '%s' "$find_flat" | python3 -c 'import json,sys; print(json.load(sys.stdin)["layout"])')
assert_eq "find-cold flat layout" "$flat_layout" "flat"

find_hub=$(python3 "$PY" find-cold --cold-root "$COLD" ColdOrg/HubModel)
hub_layout=$(printf '%s' "$find_hub" | python3 -c 'import json,sys; print(json.load(sys.stdin)["layout"])')
assert_eq "find-cold hub layout" "$hub_layout" "hub"

# warm miss → cold hit
resolve_cold=$(PULSAR_COLD_ROOT="$COLD" python3 "$PY" resolve \
  --catalog "$STATE/catalog-cold.json" \
  --models-dir "$STATE/models" \
  --cold-root "$COLD" \
  --json demo-cold-only)
tier=$(printf '%s' "$resolve_cold" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')
assert_eq "resolve warm miss falls through to cold" "$tier" "cold"
src=$(printf '%s' "$resolve_cold" | python3 -c 'import json,sys; print(json.load(sys.stdin)["source_path"])')
assert_true "resolve cold source is flat complete" test -f "$src/config.json"

# warm hit prefers warm (no cold needed)
resolve_warm=$(PULSAR_COLD_ROOT="$COLD" python3 "$PY" resolve \
  --catalog "$STATE/catalog2.json" \
  --models-dir "$STATE/models" \
  --cold-root "$COLD" \
  --json qwen3-1.7b-2node)
tier_w=$(printf '%s' "$resolve_warm" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')
assert_eq "resolve prefers warm when present" "$tier_w" "warm"

# cold disabled → warm miss fails without cold
set +e
PULSAR_COLD_ROOT='' python3 "$PY" resolve \
  --catalog "$STATE/catalog-cold.json" \
  --models-dir "$STATE/models" \
  --no-cold \
  --json demo-cold-only >"$STATE/resolve-nocold.err" 2>&1
nc_rc=$?
set -e
assert_eq "resolve --no-cold fails without warm home" "$nc_rc" "1"

# configured but missing root fails when needed
set +e
python3 "$PY" resolve \
  --catalog "$STATE/catalog-cold.json" \
  --models-dir "$STATE/models" \
  --cold-root "$STATE/missing-cold-root" \
  --json demo-cold-only >"$STATE/resolve-badcold.err" 2>&1
bc_rc=$?
set -e
assert_eq "resolve fails when cold needed but unavailable" "$bc_rc" "1"
assert_true "unavailable cold error text" grep -qi "cold\|unavailable\|not exist" "$STATE/resolve-badcold.err"

# absolute path resolve under cold root (use real temp path)
abs_path="$FLAT_COMPLETE"
resolve_abs=$(python3 "$PY" resolve \
  --allow-missing-catalog \
  --cold-root "$COLD" \
  --json "$abs_path")
tier_a=$(printf '%s' "$resolve_abs" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tier"])')
assert_eq "resolve absolute cold path" "$tier_a" "cold"

# adopt flat → warm hub
ADOPT_CACHE="$STATE/adopt-cache"
mkdir -p "$ADOPT_CACHE"
adopt=$(python3 "$PY" plan-cold-adopt \
  --cold-root "$COLD" \
  --model DemoOrg/Demo-Model-Complete \
  --cache-root "$ADOPT_CACHE" \
  --execute)
adopt_state=$(printf '%s' "$adopt" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dest_state"])')
assert_eq "adopt produces complete hub" "$adopt_state" "complete"
adopt_dest=$(printf '%s' "$adopt" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dest_hub"])')
assert_true "adopt dest under cache hub" test -f "$adopt_dest/refs/main"
assert_true "adopt snapshot has weights" \
  test -f "$adopt_dest/snapshots/$(cat "$adopt_dest/refs/main")/model.safetensors"

# adopt hub layout as-is
adopt_hub=$(python3 "$PY" plan-cold-adopt \
  --cold-root "$COLD" \
  --model ColdOrg/HubModel \
  --cache-root "$ADOPT_CACHE" \
  --execute)
assert_eq "adopt hub dest complete" \
  "$(printf '%s' "$adopt_hub" | python3 -c 'import json,sys; print(json.load(sys.stdin)["dest_state"])')" \
  "complete"

# stage-only cold → hot
export PULSAR_HOT_BUDGET_BYTES=$((50 * 1024 * 1024))
stage=$(python3 "$PY" plan-cold-stage \
  --cold-root "$COLD" \
  --profile demo-cold-only \
  --topology-id topo-cold-001 \
  --hot-root "$HOT" \
  --catalog "$STATE/catalog-cold.json" \
  --models-dir "$STATE/models" \
  --execute)
stage_action=$(printf '%s' "$stage" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
assert_eq "stage-only action" "$stage_action" "stage-only"
stage_inst=$(printf '%s' "$stage" | python3 -c 'import json,sys; print(json.load(sys.stdin)["instance_dir"])')
python3 "$PY" verify-hot --instance-dir "$stage_inst" --profile demo-cold-only --topology-id topo-cold-001 --models-dir "$STATE/models" >/dev/null \
  && ok "verify-hot after cold stage-only" || not_ok "verify-hot after cold stage-only"

stage2=$(python3 "$PY" plan-cold-stage \
  --cold-root "$COLD" \
  --profile demo-cold-only \
  --topology-id topo-cold-001 \
  --hot-root "$HOT" \
  --catalog "$STATE/catalog-cold.json" \
  --models-dir "$STATE/models")
stage2_action=$(printf '%s' "$stage2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action"])')
assert_eq "stage-only skip when hot ready" "$stage2_action" "skip"

# Preparation stays warm-only (the internal plan-activate command rejects a cold-only profile)
set +e
python3 "$PY" plan-activate \
  --catalog "$STATE/catalog-cold.json" \
  --profile demo-cold-only \
  --topology-id topo-cold-001 \
  --hot-root "$HOT" \
  --models-dir "$STATE/models" \
  --backend copy \
  --nodes 1 >/dev/null 2>"$STATE/activate-cold.err"
ac_rc=$?
set -e
assert_eq "plan-activate refuses cold-only model" "$ac_rc" "1"

assert_true "model-library.sh documents cold" \
  bash -c "'$REPO_DIR/scripts/model-library.sh' --help | grep -q 'cold'"

assert_true "prepare is the preferred CLI command" \
  bash -c "'$REPO_DIR/scripts/model-library.sh' --help | grep -q 'prepare <profile>'"
assert_true "multi-rank prepare defaults to ssh-roce" \
  bash -c "'$REPO_DIR/scripts/model-library.sh' --help | grep -q 'multi-rank uses ssh-roce'"
assert_true "prepare help does not call one-rank experimental" \
  bash -c "! '$REPO_DIR/scripts/model-library.sh' --help | grep -q 'one-rank and legacy-unsealed'"
set +e
activate_out=$("$REPO_DIR/scripts/model-library.sh" activate qwen3-1.7b 2>&1)
activate_rc=$?
set -e
assert_eq "public activate fails closed" "$activate_rc" "2"
assert_true "activate names prepare" \
  grep -q 'use prepare' <<<"$activate_out"
assert_true "bench-ssh-roce documented in CLI" \
  grep -q bench-ssh-roce "$REPO_DIR/scripts/model-library.sh"
assert_true "probe-ssh-roce documented in CLI" \
  grep -q probe-ssh-roce "$REPO_DIR/scripts/model-library.sh"
assert_true "parallel copy streams documented in CLI" \
  grep -q -- --copy-streams "$REPO_DIR/scripts/model-library.sh"

if PULSAR_COPY_STREAMS=17 \
    "$REPO_DIR/scripts/model-library.sh" help \
    >"$STATE/copy-stream-invalid.err" 2>&1; then
  not_ok "copy streams above cap fail closed"
else
  assert_true "copy streams above cap explain range" \
    grep -q 'between 1 and 16' "$STATE/copy-stream-invalid.err"
fi
if PULSAR_COPY_STREAMS=16 PULSAR_COPY_STREAM_STAGGER_MS=0 \
    "$REPO_DIR/scripts/model-library.sh" help \
    >"$STATE/copy-stagger-invalid.err" 2>&1; then
  not_ok "high stream count without stagger fails closed"
else
  assert_true "high stream count explains stagger floor" \
    grep -q 'at least 100ms' "$STATE/copy-stagger-invalid.err"
fi
assert_true "validated 16-stream settings reach help" \
  env PULSAR_COPY_STREAMS=16 PULSAR_COPY_STREAM_STAGGER_MS=150 \
    "$REPO_DIR/scripts/model-library.sh" help

python3 "$PY" compare-ssh-roce-bench \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --model-id Qwen/Qwen3-1.7B \
  --bytes-logical 1000 \
  --control-seconds 100 \
  --ssh-roce-seconds 70 \
  --tag unit-ssh-roce-win \
  --nodes 2 \
  --home-rank 1 \
  --output "$STATE/ssh-roce-win.json" >/dev/null
v=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-win.json"'"))["verdict"])')
assert_eq "ssh-roce bench ssh_roce_faster when roce < control" "$v" "ssh_roce_faster"
fp=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-win.json"'"))["ssh_roce_claims_faster"])')
assert_eq "ssh_roce_claims_faster true" "$fp" "True"
pd=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-win.json"'"))["product_default_unchanged"])')
assert_eq "ssh-roce product_default_unchanged" "$pd" "True"
order=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-win.json"'"))["run_order"])')
assert_eq "ssh-roce report records default run order" "$order" "control-first"

python3 "$PY" compare-ssh-roce-bench \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --model-id Qwen/Qwen3-1.7B \
  --bytes-logical 1000 \
  --control-seconds 100 \
  --ssh-roce-seconds 70 \
  --tag unit-ssh-roce-reversed \
  --nodes 2 \
  --home-rank 1 \
  --run-order roce-first \
  --output "$STATE/ssh-roce-reversed.json" >/dev/null
order=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-reversed.json"'"))["run_order"])')
assert_eq "ssh-roce report records reversed run order" "$order" "roce-first"

python3 "$PY" compare-ssh-roce-bench \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --model-id Qwen/Qwen3-1.7B \
  --bytes-logical 1000 \
  --control-seconds 80 \
  --ssh-roce-seconds 100 \
  --tag unit-ssh-roce-lose \
  --nodes 2 \
  --home-rank 1 \
  --output "$STATE/ssh-roce-lose.json" >/dev/null
v=$(python3 -c 'import json; print(json.load(open("'"$STATE/ssh-roce-lose.json"'"))["verdict"])')
assert_eq "ssh-roce bench control_faster when roce > control" "$v" "control_faster"

# tree_bytes must not double-count HF snapshot symlinks
if STATE="$STATE" REPO_DIR="$REPO_DIR" python3 - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ.get("REPO_DIR", "."))
# import from script path
import importlib.util
spec = importlib.util.spec_from_file_location(
    "model_library", Path(os.environ["REPO_DIR"]) / "scripts" / "model_library.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
root = Path(os.environ["STATE"]) / "byte-test"
blob = root / "blobs" / "x"
snap = root / "snapshots" / "rev"
blob.parent.mkdir(parents=True)
snap.mkdir(parents=True)
blob.write_bytes(b"hello-world-12345")
(snap / "model.safetensors").symlink_to(blob)
# size should be blob once (~17), not twice
n = mod.tree_bytes(root)
assert n == blob.stat().st_size, n
PY
then
  ok "tree_bytes skips symlink targets double-count"
else
  not_ok "tree_bytes skips symlink targets double-count"
fi

assert_true "privileged node steps batch one root script" \
  grep -q library_node_root_script "$REPO_DIR/scripts/model-library.sh"
assert_true "parallel copy uses size-balanced blob planner" \
  grep -q partition_hub_blobs_on_rank "$REPO_DIR/scripts/model-library.sh"
assert_true "parallel relay geometry fails closed" \
  grep -q 'does not support remote-home to remote-target relay' "$REPO_DIR/scripts/model-library.sh"
assert_true "home materialize requires exact symlink" \
  grep -q durable-home-symlink "$REPO_DIR/scripts/model-library-materialize.sh"
assert_true "copy materialize uses explicit home-rank selection" \
  grep -q 'copy_hub_to_rank "$rank" "$hub_source" "$hub_dest" "$home_rank"' \
    "$REPO_DIR/scripts/model-library.sh"
assert_true "prepare materializes ranks in parallel" \
  grep -q 'pids+=("$!")' "$REPO_DIR/scripts/model-library.sh"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
