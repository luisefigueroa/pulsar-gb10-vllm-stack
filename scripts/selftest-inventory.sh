#!/usr/bin/env bash
# Deterministic inventory classifier tests (no Docker/SSH/nvidia-smi/hardware).
#   scripts/selftest-inventory.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INV="$REPO_DIR/scripts/inventory.sh"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

pass=0
fail=0

assert_eq() {
  local got="$1" want="$2" msg="$3"
  if [ "$got" = "$want" ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (got='$got' want='$want')" >&2
    fail=$((fail + 1))
  fi
}

assert_true() {
  local cond="$1" msg="$2"
  if [ "$cond" = "1" ] || [ "$cond" = "true" ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg" >&2
    fail=$((fail + 1))
  fi
}

assert_false() {
  local cond="$1" msg="$2"
  if [ "$cond" = "0" ] || [ "$cond" = "false" ] || [ -z "$cond" ]; then
    echo "OK   $msg"
    pass=$((pass + 1))
  else
    echo "FAIL $msg (expected false, got '$cond')" >&2
    fail=$((fail + 1))
  fi
}

# Minimal profile catalog used by fixtures (mirrors load_conf for two confs).
# Footprint numbers are fixed so tests stay deterministic.
PROFILES_JSON='{
  "qwen3-1.7b-2node": {
    "served_name": "qwen3-1.7b-2node",
    "nodes": 2,
    "port": 8000,
    "container_name": "vllm-cluster-qwen3-1.7b-2node",
    "expected_ranks": ["0", "1"],
    "estimated_footprint_gib_per_rank": 10.00
  },
  "qwen3-1.7b": {
    "served_name": "qwen3-1.7b",
    "nodes": 1,
    "port": 8000,
    "container_name": "vllm-qwen3-1.7b",
    "expected_ranks": ["single"],
    "estimated_footprint_gib_per_rank": 12.00
  },
  "deepseek-v4-flash": {
    "served_name": "deepseek-v4-flash",
    "nodes": 2,
    "port": 8000,
    "container_name": "vllm-cluster-deepseek-v4-flash",
    "expected_ranks": ["0", "1"],
    "estimated_footprint_gib_per_rank": 100.00
  }
}'

run_fixture() {
  local name="$1" body="$2"
  local f
  f=$(mktemp "${TMPDIR:-/tmp}/pulsar-inv-fixture.XXXXXX")
  printf '%s\n' "$body" >"$f"
  if ! out=$("$INV" --from-fixture "$f" --json 2>"${f}.err"); then
    echo "FAIL $name: inventory exited non-zero" >&2
    cat "${f}.err" >&2 || true
    fail=$((fail + 1))
    rm -f "$f" "${f}.err"
    echo ""
    return 1
  fi
  rm -f "$f" "${f}.err"
  printf '%s' "$out"
}

py_get() {
  local json="$1" expr="$2"
  printf '%s' "$json" | EXPR="$expr" python3 -c 'import json,sys,os; d=json.load(sys.stdin); print(eval(os.environ["EXPR"], {"d": d}))'
}

# ---------------------------------------------------------------------------
# 1) Healthy managed 2-rank service
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"hostname": "atlas-lab", "mem_available_gib": 40.5, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"hostname": "orion-box", "mem_available_gib": 38.25, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": ["--model", "Qwen/Qwen3-1.7B", "--served-model-name", "qwen3-1.7b-2node", "--node-rank", "0"],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
        "io.pulsar.gb10.weight-source": "replicated",
        "io.pulsar.gb10.launch-contract": "d" * 64,
        "io.pulsar.gb10.spec-decode": "off",
      },
      "host_pids": [1001, 1002],
    },
    {
      "node": "worker",
      "id": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": ["--model", "Qwen/Qwen3-1.7B", "--served-model-name", "qwen3-1.7b-2node", "--node-rank", "1"],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "1",
        "io.pulsar.gb10.weight-source": "replicated",
        "io.pulsar.gb10.launch-contract": "d" * 64,
        "io.pulsar.gb10.spec-decode": "off",
      },
      "host_pids": [2001],
    },
  ],
  "gpu_processes": [
    {"node": "head", "pid": 1001, "process_name": "VLLM::Worker_TP0", "used_memory_mib": 9000, "status": "ok"},
    {"node": "worker", "pid": 2001, "process_name": "VLLM::Worker_TP1", "used_memory_mib": 9100, "status": "ok"},
  ],
}
print(json.dumps(snap))
PY
)

out=$(run_fixture "healthy-2rank" "$body")
assert_eq "$(py_get "$out" 'd["schema_version"]')" "1" "schema_version=1"
assert_eq "$(py_get "$out" 'd["nodes"]["head"]["hostname"]')" "atlas-lab" "head hostname preserved"
assert_eq "$(py_get "$out" 'd["nodes"]["worker"]["hostname"]')" "orion-box" "worker hostname preserved"
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "managed" "healthy: ownership=managed"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "True" "healthy: safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "running" "healthy: state=running"
assert_eq "$(py_get "$out" 'chr(44).join(d["services"][0]["observed_ranks"])')" "0,1" "healthy: both ranks"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["gpu_memory"]["measured_mib"]')" "9000" "healthy: gpu mem correlated"
assert_eq "$(py_get "$out" 'd["services"][0]["estimated_footprint_gib_per_rank"]')" "10.0" "healthy: profile footprint"
assert_eq "$(py_get "$out" 'len(d.get("unmanaged_gpu_processes") or [])')" "0" "healthy: no unmanaged GPU"
assert_eq "$(py_get "$out" 'd["services"][0]["weight_source"]')" "replicated" "healthy: weight source aggregated"
assert_eq "$(py_get "$out" 'd["services"][0]["launch_contract_id"]')" "$(printf 'd%.0s' {1..64})" "healthy: launch contract aggregated"
assert_eq "$(py_get "$out" 'd["services"][0]["spec_decode"]')" "off" "healthy: spec state aggregated"

body_mixed_contract=$(BODY="$body" python3 - <<PY
import json
import os

snap = json.loads(os.environ["BODY"])
del snap["containers"][1]["labels"]["io.pulsar.gb10.launch-contract"]
print(json.dumps(snap))
PY
)
out=$(run_fixture "mixed-launch-contract" "$body_mixed_contract")
assert_eq "$(py_get "$out" 'd["services"][0]["launch_contract_id"] is None')" \
  "True" "mixed contract: aggregate is unavailable"
assert_eq "$(py_get "$out" 'any("lack launch contract" in x for x in d["services"][0]["reasons"])')" \
  "True" "mixed contract: missing rank label is visible"

# Extra confirmed capacity must not affect an exact two-rank service.
# The compatibility worker status aggregates all remotes and is deliberately
# unreachable here because idle rank 2 is down; rank 1 remains observable.
body_extra=$(BODY="$body" python3 - <<'PY'
import json
import os

snap = json.loads(os.environ["BODY"])
snap["worker_status"] = "unreachable"
snap["worker_reason"] = "rank 2 SSH unreachable"
snap["nodes"]["head"]["probe_status"] = "ok"
snap["nodes"]["worker"]["probe_status"] = "ok"
snap["nodes"]["rank-2"] = {
    "hostname": "zenith-gb10",
    "mem_available_gib": None,
    "mem_status": "unreachable",
    "mem_source": "unreachable",
    "probe_status": "unreachable",
    "probe_reason": "rank 2 SSH unreachable",
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "extra-rank-unreachable" "$body_extra")
assert_eq "$(py_get "$out" 'd["worker"]["status"]')" "unreachable" \
  "extra rank: aggregate remote status preserved"
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "running" \
  "extra rank: exact two-rank service stays running"
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "True" \
  "extra rank: exact two-rank service remains complete"
assert_eq "$(py_get "$out" 'd["services"][0]["observability"]')" "complete" \
  "extra rank: exact required ranks remain observable"
assert_eq "$(py_get "$out" '[(p["node"], p["status"]) for p in d["services"][0]["required_remote_probes"]]')" \
  "[('worker', 'ok')]" "extra rank: only required remote probe participates"

hf=$(mktemp)
printf '%s' "$body_extra" >"$hf"
extra_human=$(COLUMNS=48 "$INV" --from-fixture "$hf")
rm -f "$hf"
assert_true "$(printf '%s' "$extra_human" | python3 -c '
import re
import sys

text = sys.stdin.read()
memory_nodes = []
for line in text.splitlines():
    match = re.match(r"(?:Memory\s+)?(this node|cluster node \d+)\s+·", line.strip())
    if match:
        memory_nodes.append(match.group(1))
flat = " ".join(text.split())
summary_ok = "Nodes" in text and "2 other cluster nodes confirmed" in flat
width_ok = all(len(line) <= 48 for line in text.splitlines())
print(int(memory_nodes[:3] == ["this node", "cluster node 2", "cluster node 3"] and summary_ok and width_ok))
')" "extra node: human inventory lists every node at narrow width"

# ---------------------------------------------------------------------------
# 2) Partial / degraded (missing worker rank)
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"mem_available_gib": 40.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": 100.0, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [1001],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "partial" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "partial" "partial: state=partial"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "True" "partial: head still safe_to_stop"
assert_true "$(py_get "$out" 'int(any("missing expected rank" in r for r in d["services"][0]["reasons"]))')" \
  "partial: reason mentions missing rank"

# ---------------------------------------------------------------------------
# 3) Label / profile mismatch
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": None,
  "worker_status": "unset",
  "worker_reason": "WORKER_IP unset",
  "nodes": {
    "head": {"mem_available_gib": 50.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unset", "mem_source": "unset"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "name": "vllm-ghost",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "does-not-exist",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
    {
      "node": "head",
      "id": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "name": "vllm-qwen3-1.7b",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "mismatch" "$body")
assert_eq "$(py_get "$out" 'next(s["ownership"] for s in d["services"] if s["conf"]=="does-not-exist")')" \
  "mismatch" "mismatch: unknown conf → mismatch"
assert_eq "$(py_get "$out" 'next(s["safe_to_stop"] for s in d["services"] if s["conf"]=="does-not-exist")')" \
  "False" "mismatch: not safe_to_stop"
assert_eq "$(py_get "$out" 'next(s["ownership"] for s in d["services"] if s["conf"]=="qwen3-1.7b")')" \
  "mismatch" "mismatch: rank 0 invalid for single-node profile"
assert_eq "$(py_get "$out" 'next(s["safe_to_stop"] for s in d["services"] if s["conf"]=="qwen3-1.7b")')" \
  "False" "mismatch: bad rank not safe_to_stop"

# ---------------------------------------------------------------------------
# 4) Unlabeled legacy (name/argv match — never safe_to_stop)
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": None,
  "worker_status": "unset",
  "worker_reason": "WORKER_IP unset",
  "nodes": {
    "head": {"mem_available_gib": 60.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unset", "mem_source": "unset"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "name": "vllm-qwen3-1.7b",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": ["--model", "Qwen/Qwen3-1.7B", "--served-model-name", "qwen3-1.7b"],
      "labels": {},
      "host_pids": [3001],
    },
  ],
  "gpu_processes": [
    {"node": "head", "pid": 3001, "process_name": "VLLM::Engine", "used_memory_mib": 4000, "status": "ok"},
  ],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "legacy" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "legacy" "legacy: ownership=legacy"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "False" "legacy: never safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["safe_to_stop"]')" "False" "legacy rank: not safe_to_stop"
assert_true "$(py_get "$out" 'int(any("legacy" in r.lower() for r in d["services"][0]["ranks"][0]["reasons"]))')" \
  "legacy: reason notes unlabeled"

# ---------------------------------------------------------------------------
# 5) Unknown vLLM container
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": None,
  "worker_status": "unset",
  "worker_reason": "WORKER_IP unset",
  "nodes": {
    "head": {"mem_available_gib": 70.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unset", "mem_source": "unset"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "name": "my-custom-vllm-server",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:nightly",
      "cmd": ["--model", "some/org/mystery", "--served-model-name", "mystery"],
      "labels": {},
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "unknown" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "unknown" "unknown: ownership=unknown"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "False" "unknown: not safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["profile"]')" "None" "unknown: no profile"

# ---------------------------------------------------------------------------
# 6) Unmanaged GPU PID (not in any container host_pids)
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": None,
  "worker_status": "unset",
  "worker_reason": "WORKER_IP unset",
  "nodes": {
    "head": {"mem_available_gib": 80.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unset", "mem_source": "unset"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "name": "vllm-qwen3-1.7b",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b",
        "io.pulsar.gb10.rank": "single",
      },
      "host_pids": [4001],
    },
  ],
  "gpu_processes": [
    {"node": "head", "pid": 4001, "process_name": "VLLM::Worker", "used_memory_mib": 5000, "status": "ok"},
    {"node": "head", "pid": 99999, "process_name": "/opt/comfy/.venv/bin/python3", "used_memory_mib": 1200, "status": "ok"},
  ],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "unmanaged-gpu" "$body")
assert_eq "$(py_get "$out" 'len(d["unmanaged_gpu_processes"])')" "1" "unmanaged: one leftover GPU pid"
assert_eq "$(py_get "$out" 'd["unmanaged_gpu_processes"][0]["pid"]')" "99999" "unmanaged: pid 99999"
assert_eq "$(py_get "$out" 'd["unmanaged_gpu_processes"][0]["process_name"]')" \
  "/opt/comfy/.venv/bin/python3" "unmanaged: JSON preserves full process path"
assert_true "$(py_get "$out" 'int("kill" not in (d["unmanaged_gpu_processes"][0].get("note") or "").lower() or "no kill" in (d["unmanaged_gpu_processes"][0].get("note") or "").lower())')" \
  "unmanaged: note is read-only (no kill action)"
# ensure no action field
assert_eq "$(py_get "$out" '"action" in d["unmanaged_gpu_processes"][0]')" "False" "unmanaged: no action field"

hf=$(mktemp)
printf '%s' "$body" >"$hf"
unmanaged_human=$(COLUMNS=60 "$INV" --from-fixture "$hf")
unmanaged_verbose=$(COLUMNS=60 "$INV" --from-fixture "$hf" --verbose)
rm -f "$hf"
assert_true "$(printf '%s' "$unmanaged_human" | python3 -c 'import sys; s=sys.stdin.read(); print(int("UNMANAGED GPU  1 process · 1,200 MiB" in s and "python3" in s and "/opt/comfy" not in s and s.count("Pulsar will not stop these processes.") == 1))')" \
  "human unmanaged section is compact and states safety once"
assert_true "$(printf '%s' "$unmanaged_verbose" | python3 -c 'import sys; s=sys.stdin.read(); print(int("/opt/comfy/.venv/bin/python3" in s))')" \
  "verbose human output includes full process path"

# ---------------------------------------------------------------------------
# 7) Worker unreachable
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "unreachable",
  "worker_reason": "WORKER_IP=10.0.0.2 SSH unreachable (BatchMode)",
  "nodes": {
    "head": {"mem_available_gib": 35.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unreachable", "mem_source": "unreachable"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
      "name": "vllm-cluster-deepseek-v4-flash",
      "running": True,
      "status": "running",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "deepseek-v4-flash",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "worker-unreachable" "$body")
assert_eq "$(py_get "$out" 'd["worker"]["status"]')" "unreachable" "worker unreachable status"
assert_eq "$(py_get "$out" 'd["nodes"]["worker"]["mem_status"]')" "unreachable" "worker mem status"
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "degraded" "unreachable: service degraded"
assert_true "$(py_get "$out" 'int(any("unreachable" in r.lower() for r in d["services"][0]["reasons"]))')" \
  "unreachable: reason mentions worker"

# ---------------------------------------------------------------------------
# 8) Stale managed container
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": None,
  "worker_status": "unset",
  "worker_reason": "WORKER_IP unset",
  "nodes": {
    "head": {"mem_available_gib": 110.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": None, "mem_status": "unset", "mem_source": "unset"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
      "name": "vllm-qwen3-1.7b",
      "running": False,
      "status": "exited",
      "image": "vllm/vllm-openai:v0.26.0",
      "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b",
        "io.pulsar.gb10.rank": "single",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "stale" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "stale" "stale: state=stale"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["stale"]')" "True" "stale: rank.stale"
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "managed" "stale: still managed"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "True" "stale managed: safe_to_stop for cleanup"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["gpu_memory"]["measured_mib"]')" "None" "stale: gpu mem null"
assert_eq "$(py_get "$out" 'd["worker"]["status"]')" "unset" "single-node: worker unset ok"

# Human output still works on fixture
human=$("$INV" --from-fixture <(printf '%s' "$body") 2>/dev/null || true)
if printf '%s' "$human" | grep -q 'safe_to_stop'; then
  echo "OK   human output renders"
  pass=$((pass + 1))
else
  # write temp for human path without process substitution portability issues
  hf=$(mktemp)
  printf '%s' "$body" >"$hf"
  human=$("$INV" --from-fixture "$hf")
  rm -f "$hf"
  if printf '%s' "$human" | grep -q 'safe_to_stop'; then
    echo "OK   human output renders"
    pass=$((pass + 1))
  else
    echo "FAIL human output missing safe_to_stop" >&2
    fail=$((fail + 1))
  fi
fi

hf=$(mktemp)
printf '%s' "$body" >"$hf"
narrow_human=$(COLUMNS=48 "$INV" --from-fixture "$hf")
rm -f "$hf"
assert_true "$(printf '%s\n' "$narrow_human" | python3 -c 'import sys; lines=sys.stdin.read().splitlines(); print(int(bool(lines) and max(map(len, lines)) <= 48))')" \
  "human output honors narrow terminal width"
assert_true "$(printf '%s' "$narrow_human" | python3 -c 'import sys; s=sys.stdin.read(); print(int(s.startswith("INVENTORY\n") and "\nSERVICES  " in s and "\nUNMANAGED GPU  " in s and "[inventory]" not in s))')" \
  "human output uses stacked semantic sections"

# Labels must never dump arbitrary keys / env in output
assert_eq "$(py_get "$out" 'chr(44).join(sorted(d["services"][0]["ranks"][0]["labels"].keys()))')" \
  "io.pulsar.gb10.conf,io.pulsar.gb10.managed,io.pulsar.gb10.rank" \
  "only three ownership labels exposed"

# Healthy path must report complete topology (for wizard completeness checks)
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"mem_available_gib": 40.5, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": 38.25, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [1001],
    },
    {
      "node": "worker",
      "id": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "1",
      },
      "host_pids": [2001],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "complete-flag" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "True" "healthy: complete=true"
assert_eq "$(py_get "$out" 'd["services"][0]["observability"]')" "complete" "healthy: observability=complete"

# ---------------------------------------------------------------------------
# 9) Swapped ranks / wrong node placement
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"mem_available_gib": 40.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": 40.0, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "worker",
      "id": "sha256:5555555555555555555555555555555555555555555555555555555555555555",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
    {
      "node": "head",
      "id": "sha256:6666666666666666666666666666666666666666666666666666666666666666",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "1",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "swapped-ranks" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "mismatch" "swapped: ownership=mismatch"
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "degraded" "swapped: state=degraded"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "False" "swapped: service not safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "False" "swapped: complete=false"
assert_eq "$(py_get "$out" 'all(r["ownership"]=="mismatch" for r in d["services"][0]["ranks"])')" \
  "True" "swapped: each rank mismatch"
assert_eq "$(py_get "$out" 'all(r["safe_to_stop"] is False for r in d["services"][0]["ranks"])')" \
  "True" "swapped: each rank unsafe"
assert_true "$(py_get "$out" 'int(any("expected on" in r for r in d["services"][0]["reasons"]))')" \
  "swapped: reasons mention expected node"

# single-node profile placed on worker
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"mem_available_gib": 50.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": 50.0, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "worker",
      "id": "sha256:7777777777777777777777777777777777777777777777777777777777777777",
      "name": "vllm-qwen3-1.7b",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b",
        "io.pulsar.gb10.rank": "single",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "single-on-worker" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "mismatch" "single-on-worker: mismatch"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["safe_to_stop"]')" "False" \
  "single-on-worker: not safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "False" "single-on-worker: incomplete"

# ---------------------------------------------------------------------------
# Node-ID and topology labels make the same remote placement authoritative.
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json
import os

profiles = json.loads(os.environ["PROFILES_JSON"])
topology_id = "fixture-topology"
worker_id = "worker-node-id"
snap = {
  "topology_id": topology_id,
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {
      "hostname": "atlas-lab",
      "node_id": "head-node-id",
      "topology_index": 0,
      "probe_status": "ok",
      "mem_available_gib": 50.0,
      "mem_status": "ok",
      "mem_source": "proc_meminfo",
    },
    "worker": {
      "hostname": "orion-box",
      "node_id": worker_id,
      "topology_index": 1,
      "probe_status": "ok",
      "mem_available_gib": 50.0,
      "mem_status": "ok",
      "mem_source": "ssh_proc_meminfo",
    },
  },
  "containers": [{
    "node": "worker",
    "id": "sha256:" + ("8" * 64),
    "name": "vllm-qwen3-1.7b",
    "running": True,
    "status": "running",
    "image": "vllm/vllm-openai:v0.26.0",
    "cmd": [],
    "labels": {
      "io.pulsar.gb10.managed": "true",
      "io.pulsar.gb10.conf": "qwen3-1.7b",
      "io.pulsar.gb10.rank": "single",
      "io.pulsar.gb10.topology": topology_id,
      "io.pulsar.gb10.node-id": worker_id,
    },
    "host_pids": [],
  }],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "single-on-worker-with-node-id" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["ownership"]')" "managed" \
  "remote single: node-id placement is managed"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "True" \
  "remote single: node-id placement is safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "True" \
  "remote single: service is complete"
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "running" \
  "remote single: service is running"
assert_eq "$(py_get "$out" 'd["services"][0]["ranks"][0]["expected_node"]')" "worker" \
  "remote single: expected physical node follows node-id"
hf=$(mktemp)
printf '%s' "$body" >"$hf"
remote_single_human=$(COLUMNS=48 "$INV" --from-fixture "$hf")
rm -f "$hf"
assert_true "$(printf '%s' "$remote_single_human" | python3 -c 'import sys; print(int("orion-box" in sys.stdin.read()))')" \
  "remote single: human inventory shows physical hostname"


# 10) Duplicate rank (two rank-0 on head); individuals may be safe, service incomplete
# ---------------------------------------------------------------------------
body=$(PROFILES_JSON="$PROFILES_JSON" python3 - <<'PY'
import json, os
profiles = json.loads(os.environ["PROFILES_JSON"])
snap = {
  "profiles": profiles,
  "worker_ip": "10.0.0.2",
  "worker_status": "ok",
  "worker_reason": None,
  "nodes": {
    "head": {"mem_available_gib": 30.0, "mem_status": "ok", "mem_source": "proc_meminfo"},
    "worker": {"mem_available_gib": 90.0, "mem_status": "ok", "mem_source": "ssh_proc_meminfo"},
  },
  "containers": [
    {
      "node": "head",
      "id": "sha256:8888888888888888888888888888888888888888888888888888888888888888",
      "name": "vllm-cluster-qwen3-1.7b-2node",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
    {
      "node": "head",
      "id": "sha256:9999999999999999999999999999999999999999999999999999999999999999",
      "name": "vllm-cluster-qwen3-1.7b-2node-b",
      "running": True, "status": "running", "image": "vllm/vllm-openai:v0.26.0", "cmd": [],
      "labels": {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": "qwen3-1.7b-2node",
        "io.pulsar.gb10.rank": "0",
      },
      "host_pids": [],
    },
  ],
  "gpu_processes": [],
}
print(json.dumps(snap))
PY
)
out=$(run_fixture "duplicate-rank" "$body")
assert_eq "$(py_get "$out" 'd["services"][0]["state"]')" "degraded" "dup: state=degraded"
assert_eq "$(py_get "$out" 'd["services"][0]["complete"]')" "False" "dup: complete=false"
assert_eq "$(py_get "$out" 'd["services"][0]["observability"]')" "degraded" "dup: observability=degraded"
assert_eq "$(py_get "$out" 'all(r["ownership"]=="managed" and r["safe_to_stop"] for r in d["services"][0]["ranks"])')" \
  "True" "dup: individuals remain managed+safe_to_stop"
assert_eq "$(py_get "$out" 'd["services"][0]["safe_to_stop"]')" "True" \
  "dup: service safe_to_stop (observed only; not complete)"
assert_true "$(py_get "$out" 'int(any("duplicate rank" in r for r in d["services"][0]["reasons"]))')" \
  "dup: reason mentions duplicate rank"
assert_true "$(py_get "$out" 'int(any("duplicate node" in r for r in d["services"][0]["reasons"]))')" \
  "dup: reason mentions duplicate node placement"

# Human output reports node counts without exposing internal rank lists
hf=$(mktemp)
printf '%s' "$body" >"$hf"
human=$("$INV" --from-fixture "$hf" --verbose)
rm -f "$hf"
if printf '%s' "$human" | grep -E -q 'nodes +2 required · 2/2 observed'; then
  echo "OK   human node counts"
  pass=$((pass + 1))
elif printf '%s' "$human" | grep -q 'ranks expected'; then
  echo "FAIL human output exposes internal rank lists" >&2
  fail=$((fail + 1))
else
  # Accept either fixture profile size, but require the plain node-count form.
  if printf '%s' "$human" | grep -E -q 'nodes +[12] required · [0-9]+/[12] observed'; then
    echo "OK   human node counts"
    pass=$((pass + 1))
  else
    echo "FAIL human node format unexpected: $(printf '%s' "$human" | grep -E 'nodes|ranks' || true)" >&2
    fail=$((fail + 1))
  fi
fi

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
