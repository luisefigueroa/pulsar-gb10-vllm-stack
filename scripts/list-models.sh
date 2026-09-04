#!/usr/bin/env bash
# List released specs (releases/) for humans and gum choose; the spec id is
# the first column and the profile name for scripts/up.sh <ID>.
#   scripts/list-models.sh [--serving|--diagnostic] [--json]
set -euo pipefail
SCRIPT_NAME=list-models
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JSON=0 SCOPE=all
while [ $# -gt 0 ]; do
  case "$1" in
    --legacy-tested) die "--legacy-tested is retired: profiles are released specs and carry review.status, not a legacy STATUS label" 2 ;;
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
      echo "usage: $0 [--serving|--diagnostic] [--json]"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
  shift
done

releases_root="${PULSAR_RELEASES_ROOT:-$REPO_DIR/releases}"
tmp=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models.XXXXXX")
records=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models-records.XXXXXX")
projections=$(mktemp "${TMPDIR:-/tmp}/pulsar-list-models-projections.XXXXXX")
trap 'rm -f "$tmp" "$records" "$projections"' EXIT
: >"$tmp"
: >"$records"

for spec_file in "$releases_root"/*.json; do
  [ -f "$spec_file" ] || continue
  name=$(basename "$spec_file" .json)
  [[ "$name" =~ ^[0-9a-f]{64}$ ]] || continue
  # One subshell per spec: a spec that cannot load as a profile (no overlay,
  # invalid file, foreign platform) is listed as unloadable, never hidden.
  (
    set -e
    # shellcheck disable=SC2034
    SCRIPT_NAME=list-models
    # shellcheck disable=SC1091
    . "$REPO_DIR/scripts/lib.sh"
    if ! load_conf "$name" 2>"$tmp.err"; then
      reason=$(tr -d '\r' <"$tmp.err" | tail -1 | sed 's/^\[[^]]*\] ERROR: //')
      rm -f "$tmp.err"
      python3 - "$name" "$spec_file" "$reason" <<'PY' >>"$tmp.unloadable"
import json, sys
name, path, reason = sys.argv[1:]
try:
    spec = json.load(open(path, encoding="utf-8"))
    model = spec["identity"]["model_id"]; nodes = int(spec["identity"]["geometry"]["nodes"])
except Exception:
    model, nodes = "?", 1
print("\t".join([name, model, str(nodes), reason]))
PY
      exit 0
    fi
    rm -f "$tmp.err"
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
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$name" "$MODEL" "$NODES" "$src" "$SERVED_NAME" "$spec" \
      "${FIRST_RUN_CANDIDATE:-0}" "$PROFILE_FAMILY" "$VARIANT_LABEL" \
      "$FAMILY_RECOMMENDED" "$TOPOLOGY_CLASS" "$MIN_RAILS_PER_PAIR" \
      "$PROFILE_PURPOSE" "${WEIGHTS_GIB:-}" "$reviewed_identity" \
      "$reviewed_model_id" "$reviewed_revision" "$reviewed_manifest" \
      "${MODEL_SERVING_RELEASE_ID:-}" \
      "$MODEL_SERVING_RELEASE_PROJECTION_STATE" \
      "$MODEL_SERVING_RELEASE_STATUS" "$MODEL_SERVING_RELEASE_STATUS_LABEL" \
      "$MODEL_SERVING_RELEASE_CONTRACT_ID" \
      "$MODEL_SERVING_RELEASE_DECISION_ID" \
      "${IMAGE#*@}"
  ) >>"$tmp" || true
done
[ -e "$tmp.unloadable" ] || : >"$tmp.unloadable"
trap 'rm -f "$tmp" "$tmp.unloadable" "$tmp.err" "$records" "$projections"' EXIT

# Display-only spec review for every listed spec in one process (ADR 0017).
# Any failure leaves every row unlabeled; a listing never fails on spec state.
: >"$projections"
if [ -s "$records" ]; then
  python3 "${PULSAR_RELEASE_CONSUMER_PY:-$REPO_DIR/scripts/release_consumer.py}" \
    project-batch --records "$records" >"$projections" 2>/dev/null || : >"$projections"
fi

python3 - "$tmp" "$tmp.unloadable" "$REPO_DIR" "$projections" "$JSON" "$SCOPE" <<'PY'
from pathlib import Path
import json
import sys

sys.path.insert(0, sys.argv[3])
from scripts.release_consumer import human_spec_review_values
from scripts.terminal_format import TerminalWriter

UNREADABLE = {"receipt": "unreadable", "identities": []}
FIELDS = 25
try:
    projections = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8")).get("projections", {})
except Exception:
    projections = {}
if not isinstance(projections, dict):
    projections = {}
want_json = sys.argv[5] == "1"
scope = sys.argv[6]


def review_of(release_spec: dict) -> tuple[str, str | None]:
    """(status label, reviewed_at) shown for this spec, or ('-', None)."""
    for identity in release_spec.get("identities") or []:
        if identity.get("released") and identity.get("comparison") == "equal":
            return str(identity.get("review_status") or "-"), identity.get("reviewed_at")
    return "-", None


rows = []
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if not line:
        continue
    p = line.split("\t")
    p.extend([""] * (FIELDS - len(p)))
    release_spec = projections.get(p[0])
    if not isinstance(release_spec, dict):
        release_spec = dict(UNREADABLE)
    status, reviewed_at = review_of(release_spec)
    rows.append({
        "id": p[0],
        "model_id": p[1],
        "status": status,
        "review_status": status,
        "reviewed_at": reviewed_at,
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
        "image_digest": p[24] or None,
        "release_spec": release_spec,
        "loadable": True,
    })
unloadable = []
for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
    if not line:
        continue
    name, model, nodes, reason = (line.split("\t") + ["", "", "", ""])[:4]
    unloadable.append({"id": name, "model_id": model, "nodes": int(nodes or 1), "reason": reason, "loadable": False})

ORDER = {"stable": 0, "validated": 0, "-": 1, "failed": 2, "withdrawn": 3}
rows.sort(key=lambda r: (ORDER.get(r["status"], 1), r["model_id"].casefold(), r["id"]))

if want_json:
    print(json.dumps({"models": rows, "unloadable": unloadable}, indent=2))
    raise SystemExit(0)

term = TerminalWriter()
if not rows and not unloadable:
    term.emit("no released specs under releases/ (scripts/release.sh list)")
for r in rows:
    term.emit(r["id"])
    term.field("Model", r["model_id"], indent=2, label_width=10)
    for value in human_spec_review_values(r["release_spec"]):
        term.field("Spec review", value, indent=2, label_width=10)
    term.field("Serves", r["served_name"], indent=2, label_width=10)
    term.field(
        "Recipe",
        f"{r['nodes']} node(s) · source={r['source']} · image={str(r['image_digest'] or '?')[7:19]} · spec={r['spec']}",
        indent=2,
        label_width=10,
    )
    term.blank()
for u in unloadable:
    term.emit(u["id"])
    term.field("Model", u["model_id"], indent=2, label_width=10)
    term.field("Unloadable", u["reason"], indent=2, label_width=10)
    term.blank()
term.emit("Columns:")
term.field("ID", "spec id; the profile name for scripts/up.sh <ID>, scripts/down.sh, scripts/status.sh", indent=2, label_width=16)
term.field(
    "Spec review",
    "display-only ADR 0017 review.status of the released spec, shown only "
    "when the live launch contract (argv, container env, image digest, "
    "geometry) matches; otherwise hidden (-). It does not grant or deny launch",
    indent=2,
    label_width=16,
)
term.field(
    "Recipe",
    "exact active rank count, source, image digest, and speculative-decode "
    "policy; multi-node needs confirmed topology",
    indent=2,
    label_width=16,
)
term.field("Serves", "OpenAI API model name from the site overlay (default: the model id)", indent=2, label_width=16)
term.field("Unloadable", "a released spec that cannot load as a profile on this node (missing overlay, foreign platform); fix the reason, it is never hidden", indent=2, label_width=16)
term.blank()
term.emit("Filters:")
term.field("--serving", "serving-purpose specs", indent=2, label_width=18)
term.field("--diagnostic", "diagnostic specs reserved for explicit diagnostics", indent=2, label_width=18)
PY
