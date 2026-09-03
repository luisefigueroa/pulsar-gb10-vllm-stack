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
    --validated) refuse_removed_list_validated_flag ;;
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
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

tmp=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models.XXXXXX")
records=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models-records.XXXXXX")
projections=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models-projections.XXXXXX")
trap 'rm -f "$tmp" "$records" "$projections"' EXIT
: >"$tmp"
: >"$records"

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
    load_model_serving_release_projection local-verified-readonly
    # One spec-review record per profile; projected in one batch below.
    { print_release_spec_projection_args; printf '\n'; } >>"$records"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$STATUS" "$NODES" "$src" "$SERVED_NAME" "$spec" \
      "${FIRST_RUN_CANDIDATE:-0}" "$PROFILE_FAMILY" "$VARIANT_LABEL" \
      "$FAMILY_RECOMMENDED" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
      "$PROFILE_PURPOSE" "${WEIGHTS_GIB:-}" "$reviewed_identity" \
      "$reviewed_model_id" "$reviewed_revision" "$reviewed_manifest" \
      "${MODEL_SERVING_RELEASE_ID:-}" \
      "$MODEL_SERVING_RELEASE_PROJECTION_STATE" \
      "$MODEL_SERVING_RELEASE_STATUS" "$MODEL_SERVING_RELEASE_STATUS_LABEL" \
      "$MODEL_SERVING_RELEASE_CONTRACT_ID" \
      "$MODEL_SERVING_RELEASE_DECISION_ID"
  ) >>"$tmp" || true
done

# Display-only spec review for every listed profile in one process (ADR 0017).
# Any failure leaves every row unlabeled; a listing never fails on spec state.
: >"$projections"
if [ -s "$records" ]; then
  python3 "${PULSAR_RELEASE_CONSUMER_PY:-$REPO_DIR/scripts/release_consumer.py}" \
    project-batch --records "$records" >"$projections" 2>/dev/null || : >"$projections"
fi

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
  python3 - "$projections" <<PY
import json
import sys
UNREADABLE = {"receipt": "unreadable", "identities": []}
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        projections = json.load(f).get("projections", {})
except Exception:
    projections = {}
if not isinstance(projections, dict):
    projections = {}
rows = []
with open("$tmp") as f:
    for line in f:
        line = line.rstrip("\\n")
        if not line:
            continue
        p = line.split("\\t")
        while len(p) < 24:
            p.append("")
        release_spec = projections.get(p[0])
        if not isinstance(release_spec, dict):
            release_spec = dict(UNREADABLE)
        rows.append({
            "id": p[0],
            "status": p[1],
            "legacy_status": p[1],
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
            "model_serving_release": {
                "release_id": p[18] or None,
                "state": p[19],
                "effective_status": p[20] or None,
                "effective_status_label": p[21],
                "contract_id": p[22] or None,
                "decision_id": p[23] or None,
                "advisory": True,
            },
            "release_spec": release_spec,
        })
print(json.dumps({"models": rows}, indent=2))
PY
  exit 0
fi

if [ ! -s "$tmp" ]; then
  warn "no models matched"
  exit 0
fi

python3 - "$tmp" "$REPO_DIR" "$projections" <<'PY'
from pathlib import Path
import json
import sys

sys.path.insert(0, sys.argv[2])
from scripts.release_consumer import human_spec_review_values
from scripts.terminal_format import TerminalWriter

UNREADABLE = {"receipt": "unreadable", "identities": []}
try:
    projections = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")).get("projections", {})
except Exception:
    projections = {}
if not isinstance(projections, dict):
    projections = {}
term = TerminalWriter()
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    fields = line.split("\t")
    fields.extend([""] * (24 - len(fields)))
    release_spec = projections.get(fields[0])
    if not isinstance(release_spec, dict):
        release_spec = dict(UNREADABLE)
    term.emit(fields[0])
    term.field("Release", fields[21], indent=2, label_width=10)
    term.field("Legacy", fields[1], indent=2, label_width=10)
    for value in human_spec_review_values(release_spec):
        term.field("Spec review", value, indent=2, label_width=10)
    term.field("Serves", fields[4], indent=2, label_width=10)
    term.field(
        "Recipe",
        f"{fields[2]} node(s) · source={fields[3]} · spec={fields[5]}",
        indent=2,
        label_width=10,
    )
    term.blank()
term.emit("Columns:")
term.field("ID", "conf name for scripts/up.sh <ID>", indent=2, label_width=16)
term.field(
    "Release status",
    "reviewed ADR 0004 decision for the exact bound release; "
    "No release binding is neutral and is not Untested",
    indent=2,
    label_width=16,
)
term.field(
    "Legacy status",
    "historical profile evidence/recommendation label; both status fields "
    "are display-only and neither grants nor denies launch",
    indent=2,
    label_width=16,
)
term.field(
    "Spec review",
    "display-only ADR 0017 review.status for the spec computed from this "
    "profile plus its download receipt. Shown only when the live launch "
    "contract (argv, container env, image digest, geometry) matches; "
    "otherwise hidden. No receipt or no released spec is unlabeled (-) "
    "and is not Untested. This field does not grant or deny launch",
    indent=2,
    label_width=16,
)
term.field(
    "Recipe",
    "exact active rank count, source, and speculative-decode policy; "
    "multi-node needs confirmed topology; hf = Hugging Face ID; nfs = "
    "mounted path (no auto-download); spec = none, optional, or recommended",
    indent=2,
    label_width=16,
)
term.field("Serves", "OpenAI API model name", indent=2, label_width=16)
term.blank()
term.emit("Filters:")
term.field("--serving", "serving-purpose profiles (all statuses)", indent=2, label_width=18)
term.field("--diagnostic", "canary profiles reserved for explicit diagnostics", indent=2, label_width=18)
term.field("--legacy-tested", "filter to legacy STATUS=tested* recommendation labels", indent=2, label_width=18)
PY
