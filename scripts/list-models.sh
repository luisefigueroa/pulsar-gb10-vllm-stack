#!/usr/bin/env bash
# List models/*.conf for humans and gum choose (conf id is first column).
#   scripts/list-models.sh [--validated] [--json]
set -euo pipefail
SCRIPT_NAME=list-models
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

VALIDATED=0 JSON=0
while [ $# -gt 0 ]; do
  case "$1" in
    --validated) VALIDATED=1 ;;
    --json) JSON=1 ;;
    -h|--help) echo "usage: $0 [--validated] [--json]"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

tmp="${TMPDIR:-/tmp}/pulsar-list-models.$$"
trap 'rm -f "$tmp"' EXIT
: >"$tmp"

for conf in "$REPO_DIR"/models/*.conf; do
  [ -f "$conf" ] || continue
  name=$(basename "$conf" .conf)
  # shellcheck disable=SC1090
  (
    set -e
    SCRIPT_NAME=list-models
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/lib.sh"
    load_conf "$name"
    if [ "$VALIDATED" = 1 ] && ! status_is_tested; then
      exit 0
    fi
    src=$(model_source_kind)
    spec="none"
    if has_spec_args; then
      if [ "${RECOMMENDED_SPEC}" = "1" ]; then
        spec="recommended"
      else
        spec="optional"
      fi
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$STATUS" "$NODES" "$src" "$SERVED_NAME" "$spec" "${FIRST_RUN_CANDIDATE:-0}"
  ) >>"$tmp" || true
done

# first-run candidates first, then 1-node, then name
sort -t$'\t' -k7,7r -k3,3n -k1,1 "$tmp" -o "$tmp"

if [ "$JSON" = 1 ]; then
  python3 - <<PY
import json
rows = []
with open("$tmp") as f:
    for line in f:
        line = line.rstrip("\\n")
        if not line:
            continue
        p = line.split("\\t")
        while len(p) < 7:
            p.append("")
        rows.append({
            "id": p[0], "status": p[1], "nodes": int(p[2] or 1),
            "source": p[3], "served_name": p[4], "spec": p[5],
            "first_run_candidate": p[6] == "1",
        })
print(json.dumps({"models": rows}, indent=2))
PY
  exit 0
fi

if [ ! -s "$tmp" ]; then
  warn "no models matched"
  exit 0
fi

printf '%-32s %-14s %5s %-4s %-22s %s\n' "ID" "STATUS" "NODES" "SRC" "SERVED_NAME" "SPEC_DECODE"
printf '%-32s %-14s %5s %-4s %-22s %s\n' "--------------------------------" "--------------" "-----" "----" "----------------------" "-----------"
while IFS=$'\t' read -r id st nodes src served spec fr; do
  printf '%-32s %-14s %5s %-4s %-22s %s\n' "$id" "$st" "$nodes" "$src" "$served" "$spec"
done <"$tmp"

cat <<'EOF'

Columns:
  ID           conf name for scripts/up.sh <ID>
  STATUS       validation ledger status
  NODES        1 = single-node serve; 2 = cluster (needs .env HEAD_IP/WORKER_IP)
  SRC          hf = Hugging Face id; nfs = path under /mnt/Models (no auto-download)
  SERVED_NAME  OpenAI API "model" field (may differ from ID)
  SPEC_DECODE  none | optional | recommended
                 none        = no validated speculative-decode config
                 optional    = has --spec-decode config; off by default
                 recommended = use: scripts/up.sh <ID> --spec-decode
EOF
