#!/usr/bin/env bash
# Deterministic ADR 0017 measured spec from a current profile plus receipt.
# Maintainer/lab CLI. Not routed by ./pulsar.
set -euo pipefail
# Used by die/log after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=release-spec
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

PY_TOOL="${PULSAR_RELEASE_SPEC_GENERATE_PY:-$REPO_DIR/scripts/release_spec_generate.py}"

usage() {
  cat <<'EOF'
Emit a measured ADR 0017 release spec from a lab draft

Usage:
  scripts/release-spec.sh from-draft <draft.conf> --receipt FILE
      --stack-version STRING
      [--spec-decode] [--out FILE] [--gap-report FILE]
  scripts/release-spec.sh promote <measured-spec> --reviewer NAME
      --out releases/<spec_id>.json [--reviewed-at ISO-8601-Z]
      [--status stable|failed|withdrawn|validated]

  • from-draft reads a conf-format draft from an explicit path (a lab
    input, never a startable profile), an explicit download receipt, the
    pinned image digest, and the current platform; writes a measured spec
    (empty measurements) and a closed gap report named after the draft.
  • promote copies an evaluated measured spec with state=released and a
    review block; the status defaults to stable when every baseline-v1
    outcome passed and failed otherwise. Committing the file under
    releases/ is the reviewed promotion PR.
  • Neither writes the catalog or profile status.
  • --stack-version is required. It is never read from git.
EOF
}

case "${1:-}" in
  -h|--help|help|"")
    usage
    exit 0
    ;;
esac

command="$1"
shift
PROMOTE_PY="${PULSAR_RELEASE_SPEC_PROMOTE_PY:-$REPO_DIR/scripts/release_spec_promote.py}"
case "$command" in
  from-draft) ;;
  from-profile) die "from-profile is retired: profiles are released specs; pass a draft file with from-draft" 2 ;;
  promote)
    [ -f "$PROMOTE_PY" ] || die "missing $PROMOTE_PY"
    exec python3 "$PROMOTE_PY" "$@"
    ;;
  *) die "unknown release-spec command: $command" 2 ;;
esac

profile=""
receipt=""
stack_version=""
spec_decode=0
out=""
gap_report=""

while [ $# -gt 0 ]; do
  case "$1" in
    --receipt)
      [ $# -ge 2 ] || die "--receipt requires a value" 2
      receipt="$2"
      shift 2
      ;;
    --stack-version)
      [ $# -ge 2 ] || die "--stack-version requires a value" 2
      stack_version="$2"
      shift 2
      ;;
    --spec-decode)
      spec_decode=1
      shift
      ;;
    --out)
      [ $# -ge 2 ] || die "--out requires a value" 2
      out="$2"
      shift 2
      ;;
    --gap-report)
      [ $# -ge 2 ] || die "--gap-report requires a value" 2
      gap_report="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      die "unknown arg: $1" 2
      ;;
    *)
      if [ -z "$profile" ]; then
        profile="$1"
        shift
      else
        die "unexpected argument: $1" 2
      fi
      ;;
  esac
done

[ -n "$profile" ] || die "usage: release-spec.sh from-draft <draft.conf> --receipt FILE --stack-version STRING" 2
[ -n "$receipt" ] || die "usage: release-spec.sh from-draft <draft.conf> --receipt FILE --stack-version STRING" 2
[ -n "$stack_version" ] || die "usage: release-spec.sh from-draft <draft.conf> --receipt FILE --stack-version STRING" 2
[ -f "$PY_TOOL" ] || die "missing $PY_TOOL"
[ -n "${PULSAR_PLATFORM_ID:-}" ] || die "platform id is unavailable"

load_draft "$profile"

args=(
  --repo-root "$REPO_DIR"
  --profile "$CONF_NAME"
  --draft "$CONF_PATH"
  --receipt "$receipt"
  --stack-version "$stack_version"
  --platform-id "$PULSAR_PLATFORM_ID"
)
append_loaded_profile_contract_args args
if [ "$spec_decode" = 1 ]; then
  args+=(--spec-decode)
fi
if [ -n "$out" ]; then
  args+=(--out "$out")
fi
if [ -n "$gap_report" ]; then
  args+=(--gap-report "$gap_report")
fi
python3 "$PY_TOOL" "${args[@]}"
