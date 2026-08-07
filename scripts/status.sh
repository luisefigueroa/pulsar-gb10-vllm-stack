#!/usr/bin/env bash
# Status of vLLM containers + optional /v1 smoke.
#   scripts/status.sh [model-name] [--node NODE_ID]
set -euo pipefail
SCRIPT_NAME=status
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

NAME=""
NODE_SELECTOR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --node)
      [ "$#" -ge 2 ] || die "--node requires a topology node id or hostname" 2
      NODE_SELECTOR="$2"
      shift
      ;;
    -h|--help)
      echo "usage: scripts/status.sh [model-name] [--node NODE_ID]"
      exit 0
      ;;
    -*) die "unknown arg: $1" 2 ;;
    *)
      [ -z "$NAME" ] || die "only one model name may be specified" 2
      NAME="$1"
      ;;
  esac
  shift
done

PORT_SCAN="${PORT:-8000}"
API_BASE="http://127.0.0.1:${PORT_SCAN}"
if [ -n "$NAME" ]; then
  load_conf "$NAME"
  PORT_SCAN="$PORT"
  API_BASE="http://127.0.0.1:${PORT_SCAN}"
  if [ "$NODES" -eq 1 ]; then
    if [ -z "$NODE_SELECTOR" ]; then
      placement_rc=0
      placement_index=$(discover_single_node_index_for_conf "$NAME") || placement_rc=$?
      case "$placement_rc" in
        0) NODE_SELECTOR=$(single_node_key_for_index "$placement_index") ;;
        3) NODE_SELECTOR="" ;;
        2) die "refusing ambiguous or unproven placement for $NAME" ;;
        *) die "cannot safely discover placement for $NAME" ;;
      esac
    fi
    resolve_single_node_placement "$NODE_SELECTOR" \
      || die "cannot resolve physical node placement $NODE_SELECTOR"
    API_BASE=$(single_node_api_base_url "$PORT_SCAN")
  elif [ -n "$NODE_SELECTOR" ]; then
    die "--node is only valid for one-node profiles" 2
  fi
elif [ -n "$NODE_SELECTOR" ]; then
  die "--node requires a model name" 2
fi

list_containers() {
  # Avoid `docker ps --format table | grep` — fragile under pipefail/head.
  printf '%-40s %-30s %s\n' "NAMES" "STATUS" "IMAGE"
  docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null \
    | awk -F'\t' 'BEGIN{IGNORECASE=1} $1 ~ /vllm/ || $3 ~ /vllm/ {printf "%-40s %-30s %s\n", $1, $2, $3}'
}

echo "[status] containers (head)"
n_head=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -ci vllm || true)
if [ "${n_head:-0}" -eq 0 ]; then
  warn "no docker names matching 'vllm' on head (endpoint may still be up via host network / other name)"
  docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null | head -8 || true
else
  list_containers
fi

# Who owns the API port on the selected physical node?
listeners=""
if [ "${SINGLE_NODE_REMOTE:-0}" = 1 ]; then
  listeners=$("$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- \
    "$SINGLE_NODE_SSH_HOST" "ss -ltnp 2>/dev/null" 2>/dev/null \
    | grep -E ":${PORT_SCAN}\\b" || true)
elif command -v ss >/dev/null 2>&1; then
  listeners=$(ss -ltnp 2>/dev/null | grep -E ":${PORT_SCAN}\\b" || true)
fi
[ -n "$listeners" ] && echo "[status] listeners on ${SINGLE_NODE_HOSTNAME:-head} :${PORT_SCAN}" && echo "$listeners"

topology_ok=1
load_cluster_topology || topology_ok=0
if [ "$topology_ok" = 1 ] && [ "$CLUSTER_TOPOLOGY_COUNT" -gt 1 ]; then
  for ((rank = 1; rank < CLUSTER_TOPOLOGY_COUNT; rank++)); do
    host="${CLUSTER_NODE_SSH_HOSTS[$rank]}"
    echo "[status] containers (rank $rank · $host)"
    if ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" true \
        >/dev/null 2>&1; then
      warn "rank $rank SSH unreachable"
    elif ! "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
        "docker info >/dev/null 2>&1"; then
      warn "rank $rank reachable but Docker unavailable"
    else
      "$PULSAR_SSH" "${PULSAR_SSH_OPTS[@]}" -- "$host" \
        "printf '%-40s %-30s %s\\n' NAMES STATUS IMAGE; docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}' | awk -F'\\t' 'BEGIN{IGNORECASE=1} \$1 ~ /vllm/ || \$3 ~ /vllm/ {printf \"%-40s %-30s %s\\n\", \$1, \$2, \$3}'" \
        2>/dev/null || warn "rank $rank Docker container listing failed"
    fi
  done
elif [ "$topology_ok" != 1 ]; then
  warn "confirmed topology invalid — remote ranks not listed"
else
  warn "no confirmed remote ranks"
fi

if [ -n "$NAME" ]; then
  cname=$(container_name_for "$NAME" "$NODES")
  if [ "$NODES" -eq 1 ]; then
    echo "[status] conf=$NAME expected_container=$cname served=$SERVED_NAME port=$PORT_SCAN placement=$(single_node_display) node-id=${SINGLE_NODE_ID:-standalone}"
  else
    echo "[status] conf=$NAME expected_container=$cname served=$SERVED_NAME port=$PORT_SCAN"
  fi
fi

echo "[status] HTTP $API_BASE"
api_auth_args=()
api_auth_curl_args api_auth_args
if curl -fsS --max-time 3 "${api_auth_args[@]}" "${API_BASE}/health" >/dev/null 2>&1; then
  log "health OK"
  models_json=$(curl -fsS --max-time 5 "${api_auth_args[@]}" "${API_BASE}/v1/models" 2>/dev/null || true)
  if [ -n "$models_json" ]; then
    echo "$models_json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("models:", ", ".join(x["id"] for x in d.get("data",[])))' 2>/dev/null \
      || echo "$models_json" | head -c 400
    echo
  fi
  if [ -n "$NAME" ]; then
    model_json="$SERVED_NAME"
  else
    model_json=$(printf '%s' "$models_json" \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["data"][0]["id"] if d.get("data") else "")' 2>/dev/null || true)
  fi
  if [ -n "${model_json:-}" ]; then
    log "smoke completion model=$model_json"
    curl -fsS --max-time 120 "${API_BASE}/v1/completions" \
      "${api_auth_args[@]}" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"${model_json}\",\"prompt\":\"2+2=\",\"max_tokens\":8,\"temperature\":0}" \
      && echo
  fi
  # fingerprint line if present
  printf '%s' "$models_json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("root:", d["data"][0].get("root","?"))' 2>/dev/null || true
else
  warn "nothing healthy at $API_BASE"
  exit 1
fi
