#!/usr/bin/env bash
# List models/*.conf for humans and gum choose (conf id is first column).
#   scripts/list-models.sh [--legacy-tested] [--serving|--diagnostic] [--json]
set -euo pipefail
SCRIPT_NAME=list-models
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

LEGACY_TESTED=0 JSON=0 SCOPE=all
while [ $# -gt 0 ]; do
  case "$1" in
    --legacy-tested) LEGACY_TESTED=1 ;;
    --validated)
      # Backward-compatible alias. This filters legacy profile STATUS text; it
      # does not assert an ADR 0004 Validated Model Serving Release.
      LEGACY_TESTED=1
      ;;
    --serving)
      [ "$SCOPE" = all ] || die "--serving and --diagnostic are mutually exclusive"
      SCOPE=serving
      ;;
    --diagnostic)
      [ "$SCOPE" = all ] || die "--serving and --diagnostic are mutually exclusive"
      SCOPE=diagnostic
      ;;
    --json) JSON=1 ;;
    -h|--help)
      echo "usage: $0 [--legacy-tested] [--serving|--diagnostic] [--json]"
      echo "       --validated is a deprecated alias for --legacy-tested"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

tmp=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models.XXXXXX")
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
    if [ "$LEGACY_TESTED" = 1 ] && ! status_is_tested; then
      exit 0
    fi
    case "$SCOPE" in
      serving) [ "$PROFILE_PURPOSE" = serving ] || exit 0 ;;
      diagnostic) [ "$PROFILE_PURPOSE" = diagnostic ] || exit 0 ;;
    esac
    src=$(model_source_kind)
    spec="none"
    if has_spec_args; then
      if [ "${RECOMMENDED_SPEC}" = "1" ]; then
        spec="recommended"
      else
        spec="optional"
      fi
    fi
    reviewed_identity=0 reviewed_model_id="" reviewed_revision=""
    reviewed_manifest=""
    if [ -n "${EXPECTED_MODEL_SEAL:-}" ]; then
      reviewed_identity=1
      identity_fields=$(printf '%s' "$PROFILE_VALIDATION_BUNDLE_JSON" | \
        python3 -c 'import json,sys; s=json.load(sys.stdin)["expected_model_seal"]; print("\t".join((s["model_id"],s["snapshot_revision"],s["manifest_id"])))')
      IFS=$'\t' read -r reviewed_model_id reviewed_revision reviewed_manifest \
        <<<"$identity_fields"
    fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$STATUS" "$NODES" "$src" "$SERVED_NAME" "$spec" \
      "${FIRST_RUN_CANDIDATE:-0}" "$PROFILE_FAMILY" "$VARIANT_LABEL" \
      "$FAMILY_RECOMMENDED" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
      "$PROFILE_PURPOSE" "${WEIGHTS_GIB:-}" "$reviewed_identity" \
      "$reviewed_model_id" "$reviewed_revision" "$reviewed_manifest"
  ) >>"$tmp" || true
done

# Legacy tested/recommended profiles first, then first-run candidates, one-node,
# and name. This is recommendation order only; no status is hidden by default.
python3 - "$tmp" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]

def key(line: str):
    fields = line.split("\t")
    status = fields[1] if len(fields) > 1 else ""
    nodes = int(fields[2] or 1) if len(fields) > 2 else 1
    first_run = fields[6] == "1" if len(fields) > 6 else False
    return (not status.startswith("tested"), not first_run, nodes, fields[0].casefold())

path.write_text("".join(f"{line}\n" for line in sorted(rows, key=key)), encoding="utf-8")
PY

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
        while len(p) < 18:
            p.append("")
        rows.append({
            "id": p[0],
            "status": p[1],
            "nodes": int(p[2] or 1),
            "source": p[3],
            "served_name": p[4],
            "spec": p[5],
            "spec_default_enabled": p[5] == "recommended",
            "first_run_candidate": p[6] == "1",
            "family": p[7],
            "variant": p[8],
            "family_recommended": p[9] == "1",
            "topology_class": p[10],
            "min_rails_per_pair": int(p[11] or 0),
            "purpose": p[12] or "serving",
            "weights_gib": float(p[13]) if p[13] else None,
            "reviewed_identity": p[14] == "1",
            "reviewed_model_id": p[15] or None,
            "reviewed_revision": p[16] or None,
            "reviewed_manifest": p[17] or None,
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
while IFS=$'\t' read -r id st nodes src served spec _fr _family _variant \
    _recommended _topology _rails _purpose; do
  printf '%-32s %-14s %5s %-4s %-22s %s\n' \
    "$id" "$st" "$nodes" "$src" "$served" "$spec"
done <"$tmp"

cat <<'EOF'

Columns:
  ID           conf name for scripts/up.sh <ID>
  STATUS       advisory evidence label; never launch permission
  NODES        exact active rank count; multi-node needs confirmed topology
  SRC          hf = Hugging Face id; nfs = path under /mnt/Models (no auto-download)
  SERVED_NAME  OpenAI API "model" field (may differ from ID)
  SPEC_DECODE  none | optional | recommended
                 none        = no reviewed speculative-decode configuration
                 optional    = reviewed; off by default; --spec-decode enables
                 recommended = reviewed and on by default
                               (--no-spec-decode is the rollback)

Filters:
  --serving     serving-purpose profiles (all statuses)
  --diagnostic  canary profiles reserved for explicit diagnostics
  --legacy-tested  filter to legacy STATUS=tested* recommendation labels
EOF
