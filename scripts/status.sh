#!/usr/bin/env bash
# Status of vLLM containers + optional /v1 smoke.
#   scripts/status.sh [model-name]
set -euo pipefail
SCRIPT_NAME=status
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

NAME="${1:-}"
PORT_SCAN="${PORT:-8000}"

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

# Who owns the API port?
if command -v ss >/dev/null 2>&1; then
  listeners=$(ss -ltnp 2>/dev/null | grep -E ":${PORT_SCAN}\\b" || true)
  [ -n "$listeners" ] && echo "[status] listeners :${PORT_SCAN}" && echo "$listeners"
fi

if [ -n "${WORKER_IP:-}" ]; then
  echo "[status] containers (worker $WORKER_IP)"
  ssh -o BatchMode=yes -o ConnectTimeout=5 "$WORKER_IP" \
    "printf '%-40s %-30s %s\n' NAMES STATUS IMAGE; docker ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | awk -F'\t' 'BEGIN{IGNORECASE=1} \$1 ~ /vllm/ || \$3 ~ /vllm/ {printf \"%-40s %-30s %s\n\", \$1, \$2, \$3}'" \
    2>/dev/null || warn "worker unreachable"
else
  warn "WORKER_IP unset — skip worker container list (set in .env for Path B)"
fi

if [ -n "$NAME" ]; then
  load_conf "$NAME"
  PORT_SCAN="$PORT"
  cname=$(container_name_for "$NAME" "$NODES")
  echo "[status] conf=$NAME expected_container=$cname served=$SERVED_NAME port=$PORT_SCAN"
fi

echo "[status] HTTP :${PORT_SCAN}"
api_auth_args=()
api_auth_curl_args api_auth_args
if curl -fsS --max-time 3 "http://127.0.0.1:${PORT_SCAN}/health" >/dev/null 2>&1; then
  log "health OK"
  models_json=$(curl -fsS --max-time 5 "${api_auth_args[@]}" "http://127.0.0.1:${PORT_SCAN}/v1/models" 2>/dev/null || true)
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
    curl -fsS --max-time 120 "http://127.0.0.1:${PORT_SCAN}/v1/completions" \
      "${api_auth_args[@]}" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"${model_json}\",\"prompt\":\"2+2=\",\"max_tokens\":8,\"temperature\":0}" \
      && echo
  fi
  # fingerprint line if present
  printf '%s' "$models_json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("root:", d["data"][0].get("root","?"))' 2>/dev/null || true
else
  warn "nothing healthy on :${PORT_SCAN}"
  exit 1
fi
