#!/usr/bin/env bash
# Fast read-only system overview for the operator home screen.
#   scripts/quick-status.sh [--json]
#
# Consumes scripts/inventory.sh --json (ownership classifier is not reimplemented).
# Probes GET /v1/models only — never submits a completion / inference smoke.
# Narrow test hooks (selftests only):
#   QUICK_STATUS_INVENTORY_JSON=path
#   QUICK_STATUS_INVENTORY_CMD=path   executable → inventory JSON on stdout
#   QUICK_STATUS_API_JSON=path        fixed /v1/models body (skip network)
#   QUICK_STATUS_API_CMD=path         executable → models JSON on stdout; fail=unavailable
#   QUICK_STATUS_PORT=8000
set -euo pipefail
SCRIPT_NAME=quick-status
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
SCRIPT_NAME=quick-status

JSON=0
usage() {
  cat <<'EOF'
usage: scripts/quick-status.sh [--json]

  Fast read-only overview: active managed service, API model advertisement
  (not inference health), head/worker MemAvailable/MemTotal, managed GPU
  allocation per rank when measured, worker reachability, unmanaged GPU
  aggregate, and stale managed container count.

  Never calls /v1/completions or any other inference endpoint.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

cmd_inventory_json() {
  if [ -n "${QUICK_STATUS_INVENTORY_JSON:-}" ]; then
    cat "$QUICK_STATUS_INVENTORY_JSON"
  elif [ -n "${QUICK_STATUS_INVENTORY_CMD:-}" ]; then
    "$QUICK_STATUS_INVENTORY_CMD"
  else
    "$REPO_DIR/scripts/inventory.sh" --json
  fi
}

# Probe /v1/models only. Prints body to stdout; exit 0 if parseable, 1 if unavailable.
cmd_api_models() {
  local port="${QUICK_STATUS_PORT:-${PORT:-8000}}"
  if [ -n "${QUICK_STATUS_API_JSON:-}" ]; then
    if [ ! -f "$QUICK_STATUS_API_JSON" ]; then
      return 1
    fi
    cat "$QUICK_STATUS_API_JSON"
    return 0
  fi
  if [ -n "${QUICK_STATUS_API_CMD:-}" ]; then
    "$QUICK_STATUS_API_CMD" "$port"
    return $?
  fi
  curl -fsS --max-time 2 "http://127.0.0.1:${port}/v1/models" 2>/dev/null
}

if ! inv=$(cmd_inventory_json); then
  die "inventory collection failed — status is unavailable; run ./pulsar inventory"
fi
if ! inventory_json_is_valid "$inv"; then
  die "inventory returned invalid data — status is unavailable; run ./pulsar inventory"
fi

api_body=""
api_rc=1
set +e
api_body=$(cmd_api_models)
api_rc=$?
set -e

PORT_SCAN="${QUICK_STATUS_PORT:-${PORT:-8000}}" \
INV_JSON="$inv" \
API_BODY="$api_body" \
API_RC="$api_rc" \
WANT_JSON="$JSON" \
python3 - <<'PY'
import json, os, sys

inv = json.loads(os.environ.get("INV_JSON") or "{}")
api_rc = int(os.environ.get("API_RC") or "1")
api_raw = (os.environ.get("API_BODY") or "").strip()
port = int(os.environ.get("PORT_SCAN") or "8000")
want_json = os.environ.get("WANT_JSON") == "1"

def fmt_gib(v):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)

def pct_available(avail, total):
    if avail is None or total is None:
        return None
    try:
        t = float(total)
        a = float(avail)
        if t <= 0:
            return None
        return round(100.0 * a / t, 1)
    except (TypeError, ValueError):
        return None

def service_active(s):
    st = s.get("state")
    if st in ("running", "partial", "degraded"):
        return True
    return any(r.get("running") for r in (s.get("ranks") or []))

services = inv.get("services") or []
worker = inv.get("worker") or {}
nodes = inv.get("nodes") or {}
head = nodes.get("head") or {}
worker_node = nodes.get("worker") or {}
unmanaged = inv.get("unmanaged_gpu_processes") or []

active_managed = []
stale_managed = []
for s in services:
    if s.get("ownership") != "managed":
        continue
    st = s.get("state") or ""
    if st == "stale":
        stale_managed.append(s)
    elif service_active(s):
        active_managed.append(s)

# Prefer complete running; otherwise first active managed
primary = None
for s in active_managed:
    if s.get("complete") and (s.get("state") == "running"):
        primary = s
        break
if primary is None and active_managed:
    primary = active_managed[0]

# API advertisement
api_status = "unavailable"
api_models = []
if api_rc == 0 and api_raw:
    try:
        d = json.loads(api_raw)
        api_models = [x.get("id", "") for x in (d.get("data") or []) if x.get("id")]
        api_status = "ok" if api_models else "empty"
    except Exception:
        api_status = "unavailable"
        api_models = []

unmanaged_mib = 0
unmanaged_known = 0
for u in unmanaged:
    m = u.get("used_memory_mib")
    if m is not None:
        try:
            unmanaged_mib += int(m)
            unmanaged_known += 1
        except (TypeError, ValueError):
            pass

rank_gpu = []
if primary:
    for r in primary.get("ranks") or []:
        g = r.get("gpu_memory") or {}
        rank_gpu.append({
            "node": r.get("node"),
            "rank": r.get("rank"),
            "measured_mib": g.get("measured_mib"),
            "status": g.get("status"),
        })

head_avail = head.get("mem_available_gib")
head_total = head.get("mem_total_gib")
worker_avail = worker_node.get("mem_available_gib")
worker_total = worker_node.get("mem_total_gib")
head_pct = pct_available(head_avail, head_total)
worker_pct = pct_available(worker_avail, worker_total)

overview = {
    "schema_version": 1,
    "kind": "quick_status",
    "port": port,
    "active_managed_count": len(active_managed),
    "primary_service": None,
    "api": {
        "status": api_status,
        "port": port,
        "models": api_models,
        "note": "model advertisement only — not an inference smoke test",
    },
    "memory": {
        "head": {
            "mem_available_gib": head_avail,
            "mem_total_gib": head_total,
            "available_percent": head_pct,
            "mem_status": head.get("mem_status"),
        },
        "worker": {
            "mem_available_gib": worker_avail,
            "mem_total_gib": worker_total,
            "available_percent": worker_pct,
            "mem_status": worker_node.get("mem_status"),
        },
    },
    "worker": {
        "ip": worker.get("ip"),
        "status": worker.get("status"),
        "reason": worker.get("reason"),
    },
    "managed_gpu_per_rank": rank_gpu,
    "unmanaged_gpu": {
        "count": len(unmanaged),
        "measured_mib_aggregate": unmanaged_mib if unmanaged else 0,
        "measured_process_count": unmanaged_known,
    },
    "stale_managed": {
        "count": len(stale_managed),
        "nonblocking": True,
        "note": "stale managed containers hold no model memory; nonblocking",
        "confs": [s.get("conf") for s in stale_managed if s.get("conf")],
    },
    "inference_smoke": False,
}

if primary:
    exp = primary.get("expected_ranks") or []
    obs = primary.get("observed_ranks") or []
    overview["primary_service"] = {
        "conf": primary.get("conf"),
        "served_name": primary.get("served_name"),
        "state": primary.get("state"),
        "ownership": primary.get("ownership"),
        "safe_to_stop": primary.get("safe_to_stop"),
        "complete": primary.get("complete"),
        "expected_ranks": exp,
        "observed_ranks": obs,
        "api_port": primary.get("api_port"),
        "estimated_footprint_gib_per_rank": primary.get("estimated_footprint_gib_per_rank"),
    }

if want_json:
    json.dump(overview, sys.stdout, indent=2)
    print()
    raise SystemExit(0)

# Human concise output
print(f"[quick-status] read-only overview (no inference smoke)")
ps = overview["primary_service"]
if ps:
    exp = ",".join(str(x) for x in (ps.get("expected_ranks") or [])) or "-"
    obs = ",".join(str(x) for x in (ps.get("observed_ranks") or [])) or "-"
    print(
        f"[quick-status] managed  conf={ps.get('conf')}  served={ps.get('served_name')}"
        f"  state={ps.get('state')}  ranks exp={exp} obs={obs}"
        f"  complete={ps.get('complete')}"
    )
    if overview["active_managed_count"] > 1:
        print(f"[quick-status] note  {overview['active_managed_count']} active managed services (showing primary)")
else:
    print("[quick-status] managed  (none active)")

api = overview["api"]
if api["status"] == "ok":
    models_s = ", ".join(api["models"])
    print(f"[quick-status] API :{port}  advertised: {models_s}")
    print("[quick-status] API note  model list only — not an inference smoke test")
elif api["status"] == "empty":
    print(f"[quick-status] API :{port}  reachable but no models advertised")
else:
    print(f"[quick-status] API :{port}  unavailable")

def mem_line(label, block):
    avail = block.get("mem_available_gib")
    total = block.get("mem_total_gib")
    pct = block.get("available_percent")
    if avail is None and total is None:
        print(f"[quick-status] memory {label}: n/a")
        return
    pct_s = f"{pct:.1f}% free" if pct is not None else "free% n/a"
    print(
        f"[quick-status] memory {label}: {fmt_gib(avail)} / {fmt_gib(total)} GiB avail"
        f"  ({pct_s})"
    )

mem_line("head", overview["memory"]["head"])
wstat = (overview.get("worker") or {}).get("status") or "unset"
if wstat == "unset":
    print("[quick-status] memory worker: n/a (WORKER_IP unset)")
else:
    mem_line("worker", overview["memory"]["worker"])

if rank_gpu:
    parts = []
    for r in rank_gpu:
        m = r.get("measured_mib")
        m_s = f"{m} MiB" if m is not None else "n/a"
        parts.append(f"{r.get('node')} rank={r.get('rank')} {m_s}")
    print("[quick-status] managed GPU/unified: " + "; ".join(parts))
else:
    print("[quick-status] managed GPU/unified: n/a")

w = overview["worker"]
wr = w.get("reason") or ""
if w.get("status") == "unreachable":
    print(f"[quick-status] worker: unreachable{(' — ' + wr) if wr else ''}")
else:
    print(f"[quick-status] worker: {w.get('status') or 'unset'}")

ug = overview["unmanaged_gpu"]
if ug["count"]:
    print(
        f"[quick-status] unmanaged GPU: {ug['count']} process(es),"
        f" {ug['measured_mib_aggregate']} MiB aggregate measured"
    )
else:
    print("[quick-status] unmanaged GPU: none observed")

st = overview["stale_managed"]
if st["count"]:
    confs = ",".join(st.get("confs") or []) or "?"
    print(
        f"[quick-status] stale managed: {st['count']} ({confs})"
        f" — nonblocking, no model memory"
    )
else:
    print("[quick-status] stale managed: 0")
PY
