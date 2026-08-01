#!/usr/bin/env bash
# Detect GPU + RoCE fabric; propose cluster .env values (optional --write-env).
# Peer WORKER_IP is discovered via assumed hostnames (dgx-spark-1 / dgx-spark-2).
#   scripts/detect-fabric.sh [--json] [--write-env]
#   WORKER_HOST=... HEAD_HOST=...  # override peer hostname if needed
set -euo pipefail
SCRIPT_NAME=detect-fabric
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0 WRITE=0 YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json) JSON=1 ;;
    --write-env) WRITE=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help)
      cat <<'EOF'
usage: scripts/detect-fabric.sh [--json] [--write-env] [--yes]

Discovers local RoCE + NCCL_* and, when possible, the peer Spark's
same-rail IP via hostname convention:
  head   = ${HEAD_HOST:-dgx-spark-1.local}
  worker = ${WORKER_HOST:-dgx-spark-2.local}

--write-env   propose writing HEAD_IP / WORKER_IP / NCCL_* to repo .env
              (shows both IPs and asks for confirmation unless --yes)
--yes         skip confirmation (for automation only)
EOF
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

HEAD_HOST="${HEAD_HOST:-dgx-spark-1.local}"
WORKER_HOST="${WORKER_HOST:-dgx-spark-2.local}"

gpu="unknown"
if command -v nvidia-smi >/dev/null 2>&1; then
  gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 | sed 's/^ *//')
fi

hcas=()
socket_if=""
head_ip=""
worker_ip=""
peer_host=""
local_role="unknown"
confidence=low
detail=""
peer_detail=""

# Map RDMA devices -> netdevs
if command -v rdma >/dev/null 2>&1; then
  while read -r line; do
    if echo "$line" | grep -q 'state ACTIVE'; then
      hca=$(echo "$line" | awk '{print $2}' | cut -d/ -f1)
      nd=$(echo "$line" | sed -n 's/.*netdev //p' | awk '{print $1}')
      [ -n "$hca" ] && hcas+=("$hca")
      if [ -z "$socket_if" ] && [ -n "$nd" ]; then
        socket_if="$nd"
      fi
    fi
  done < <(rdma link show 2>/dev/null || true)
fi

if [ ${#hcas[@]} -eq 0 ] && command -v ibdev2netdev >/dev/null 2>&1; then
  while read -r hca _arrow nd state; do
    [ -n "$hca" ] || continue
    hcas+=("$hca")
    if [ -z "$socket_if" ] && [ -n "$nd" ]; then
      socket_if="$nd"
    fi
  done < <(ibdev2netdev 2>/dev/null || true)
fi

if [ -n "$socket_if" ]; then
  head_ip=$(ip -4 -o addr show dev "$socket_if" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)
fi

# Hostname → role (Spark pair convention)
local_hn=$(hostname -s 2>/dev/null || hostname 2>/dev/null || echo unknown)
local_hn_lc=$(printf '%s' "$local_hn" | tr '[:upper:]' '[:lower:]')
case "$local_hn_lc" in
  *spark-1*|*spark1*)
    local_role=head
    peer_host="$WORKER_HOST"
    ;;
  *spark-2*|*spark2*)
    local_role=worker
    peer_host="$HEAD_HOST"
    # On worker node, local RoCE IP is WORKER_IP; HEAD_IP comes from peer
    worker_ip="$head_ip"
    head_ip=""
    ;;
  *)
    local_role=unknown
    peer_host="$WORKER_HOST"
    peer_detail="hostname '$local_hn' not spark-1/spark-2; trying WORKER_HOST=$WORKER_HOST as peer"
    ;;
esac

# Resolve peer IPv4 on the same data-plane interface name
peer_ip_on_if() {
  local host="$1" ifname="$2"
  [ -n "$host" ] && [ -n "$ifname" ] || return 1
  ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "$host" \
    "ip -4 -o addr show dev $(printf '%q' "$ifname") 2>/dev/null | awk '{print \$4}' | cut -d/ -f1 | head -1" \
    2>/dev/null || return 1
}

try_peer_hosts() {
  local base="$1"
  local candidates=("$base")
  # also try bare short name if .local given, and vice versa
  if [[ "$base" == *.local ]]; then
    candidates+=("${base%.local}")
  else
    candidates+=("${base}.local")
  fi
  local h ip
  for h in "${candidates[@]}"; do
    ip=$(peer_ip_on_if "$h" "$socket_if" || true)
    if [ -n "$ip" ]; then
      peer_host="$h"
      echo "$ip"
      return 0
    fi
  done
  return 1
}

if [ -n "$socket_if" ] && [ -n "$peer_host" ]; then
  if peer_ip=$(try_peer_hosts "$peer_host"); then
    if [ "$local_role" = worker ]; then
      head_ip="$peer_ip"
    else
      worker_ip="$peer_ip"
    fi
  else
    peer_detail="could not SSH ${peer_host} for same-if ${socket_if} IP (BatchMode SSH keys?)"
  fi
fi

n_hca=${#hcas[@]}
if [ "$gpu" = "NVIDIA GB10" ] && [ "$n_hca" -ge 2 ] && [ -n "$socket_if" ] && [ -n "$head_ip" ] && [ -n "$worker_ip" ]; then
  confidence=high
elif [ "$gpu" = "NVIDIA GB10" ] && [ "$n_hca" -ge 2 ] && [ -n "$socket_if" ] && [ -n "$head_ip" ]; then
  confidence=medium
  detail="${detail}WORKER_IP not resolved — set WORKER_HOST or edit .env. "
elif [ "$n_hca" -ge 1 ] && [ -n "$socket_if" ]; then
  confidence=medium
else
  confidence=low
  detail="incomplete RDMA/IP detection — set HEAD_IP/WORKER_IP and NCCL_* manually in .env"
fi
[ -n "$peer_detail" ] && detail="${detail}${peer_detail}"

hca_csv=$(IFS=,; echo "${hcas[*]:-}")

if [ "$JSON" = 1 ]; then
  python3 - <<PY
import json
print(json.dumps({
  "gpu": "$gpu",
  "local_hostname": "$local_hn",
  "local_role": "$local_role",
  "peer_host": "$peer_host",
  "nccl_ib_hca": "$hca_csv",
  "nccl_socket_ifname": "$socket_if",
  "head_ip": "$head_ip",
  "worker_ip": "$worker_ip",
  "confidence": "$confidence",
  "detail": """$detail""",
  "rdma_count": $n_hca,
}, indent=2))
PY
else
  log "gpu=$gpu confidence=$confidence role=$local_role host=$local_hn"
  log "NCCL_IB_HCA=${hca_csv:-<unset>}"
  log "NCCL_SOCKET_IFNAME=${socket_if:-<unset>}"
  log "HEAD_IP=${head_ip:-<unset>}"
  log "WORKER_IP=${worker_ip:-<unset>}  (peer host tried: ${peer_host:-none})"
  [ -n "$detail" ] && warn "$detail"
  cat <<EOF

# Proposed .env fragment (review before use)
#HEAD_IP=${head_ip}
#WORKER_IP=${worker_ip}
#NCCL_IB_HCA=${hca_csv}
#NCCL_SOCKET_IFNAME=${socket_if}
#GLOO_SOCKET_IFNAME=${socket_if}
#TP_SOCKET_IFNAME=${socket_if}
EOF
fi

if [ "$WRITE" = 1 ]; then
  [ -n "$head_ip" ] || die "cannot --write-env without HEAD_IP"
  envf="$REPO_DIR/.env"

  echo
  echo "About to write cluster fabric into: $envf"
  echo "  local host : $local_hn  (role=$local_role)"
  echo "  peer host  : ${peer_host:-n/a}"
  echo "  data IF    : ${socket_if:-n/a}"
  echo "  HEAD_IP    : $head_ip"
  if [ -n "$worker_ip" ]; then
    echo "  WORKER_IP  : $worker_ip"
  else
    echo "  WORKER_IP  : (not resolved — will leave existing or unset)"
  fi
  [ -n "$hca_csv" ] && echo "  NCCL_IB_HCA: $hca_csv"
  [ -n "$socket_if" ] && echo "  NCCL_SOCKET_IFNAME / GLOO / TP: $socket_if"
  if [ -f "$envf" ]; then
    echo
    echo "Current .env values (if any):"
    grep -E '^(HEAD_IP|WORKER_IP|NCCL_IB_HCA|NCCL_SOCKET_IFNAME)=' "$envf" 2>/dev/null | sed 's/^/  /' || echo "  (none of those keys set)"
  fi
  echo

  if [ "$YES" != 1 ]; then
    if [ ! -t 0 ]; then
      die "refusing --write-env without TTY; re-run interactively or pass --yes"
    fi
    printf 'Write these values to .env? [y/N] '
    read -r ans
    case "$ans" in
      y|Y|yes|YES) ;;
      *)
        log "aborted — .env not modified"
        exit 0
        ;;
    esac
  fi

  touch "$envf"
  upsert() {
    local k="$1" v="$2"
    if grep -q "^${k}=" "$envf" 2>/dev/null; then
      if sed --version >/dev/null 2>&1; then
        sed -i "s|^${k}=.*|${k}=${v}|" "$envf"
      else
        sed -i.bak "s|^${k}=.*|${k}=${v}|" "$envf" && rm -f "${envf}.bak"
      fi
    else
      printf '%s=%s\n' "$k" "$v" >>"$envf"
    fi
  }
  upsert HEAD_IP "$head_ip"
  if [ -n "$worker_ip" ]; then
    upsert WORKER_IP "$worker_ip"
  else
    warn "WORKER_IP not written (peer unresolved)"
  fi
  [ -n "$hca_csv" ] && upsert NCCL_IB_HCA "$hca_csv"
  if [ -n "$socket_if" ]; then
    upsert NCCL_SOCKET_IFNAME "$socket_if"
    upsert GLOO_SOCKET_IFNAME "$socket_if"
    upsert TP_SOCKET_IFNAME "$socket_if"
  fi
  log "updated $envf"
  echo "Written:"
  grep -E '^(HEAD_IP|WORKER_IP|NCCL_IB_HCA|NCCL_SOCKET_IFNAME|GLOO_SOCKET_IFNAME|TP_SOCKET_IFNAME)=' "$envf" | sed 's/^/  /'
fi

[ "$confidence" != low ]
