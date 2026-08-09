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
assert cat["schema_version"] == 1
assert cat["topology_id"] == "topo-test-001"
models = {m["model_id"]: m for m in cat["models"]}
q = models["Qwen/Qwen3-1.7B"]
assert q["validation"] == "validated", q
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
assert_true "model-library.sh help" bash -c "'$REPO_DIR/scripts/model-library.sh' --help | grep -q 'Federated model library'"
assert_true "model-library.sh is executable" test -x "$REPO_DIR/scripts/model-library.sh"

# never write under HF_CACHE: build output is only catalog path
assert_true "catalog written outside node caches" test -f "$STATE/catalog.json"
assert_true "node0 hub untouched by build" test -f "$NODE0/hub/models--Qwen--Qwen3-1.7B/refs/main"

# --- hot staging: plan / stamp / budget / pin / purge (local only) ---
HOT="$STATE/hot"
export PULSAR_HOT_ROOT="$HOT"
export PULSAR_HOT_BUDGET_BYTES=$((50 * 1024 * 1024))

plan=$(python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
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
python3 "$PY" verify-hot --instance-dir "$instance" --profile qwen3-1.7b-2node --topology-id topo-test-001 >/dev/null \
  && ok "verify-hot after local copy" || not_ok "verify-hot after local copy"

plan2=$(python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
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

# budget refuse (hub fixture is tiny but still > 1 byte)
export PULSAR_HOT_BUDGET_BYTES=1
set +e
python3 "$PY" plan-activate \
  --catalog "$STATE/catalog2.json" \
  --profile qwen3-1.7b-2node \
  --topology-id topo-test-001 \
  --hot-root "$HOT" \
  --backend copy \
  --nodes 1 >/dev/null 2>"$STATE/budget.err"
brc=$?
set -e
assert_eq "plan-activate fails over budget" "$brc" "1"
assert_true "budget error text" grep -q "budget exceeded" "$STATE/budget.err"

# Launch wiring accepts library-hot flags (no docker)
assert_true "up.sh help lists library-hot" \
  grep -q library-hot "$REPO_DIR/scripts/up.sh"
assert_true "start-cluster accepts library-hot" \
  grep -q library-hot "$REPO_DIR/scripts/../cluster/start-cluster.sh"
out=$(set +e; "$REPO_DIR/scripts/check-weights.sh" qwen3-1.7b --weight-source library-hot 2>&1; true)
assert_true "check-weights library-hot fails closed without hot" \
  bash -c "printf '%s\n' $(printf '%q' "$out") | grep -Eq 'library-hot|activate|topology|hot'"
assert_true "down.sh documents pin-weights" \
  grep -q pin-weights "$REPO_DIR/scripts/down.sh"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
