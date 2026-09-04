#!/usr/bin/env bash
# Run baseline-v1 against an ALREADY-RUNNING server and evaluate the spec.
#
#   validate/baseline-v1.sh <profile|spec_id> --spec FILE --out DIR --dataset FILE
#       [--node NODE_ID] [--tag LABEL] [--soak-concurrency N] [--lab-commit SHA]
#       [--policy FILE] [--producer-dir DIR] [--skip-weights-check] [--check-only]
#
# --check-only stops after the server-identity checks and the boot witness
# and writes nothing; use it to confirm a running server is the spec's
# before committing two hours to the gates.
# Order: producers present → lab commit (the tracked tree must equal it) →
# server identity (container label equals the profile's launch contract,
# image digest and speculative-decode state equal the spec's, the spec is
# the identity the catalog computes for the profile) → boot witness → the
# six closed measurements into --out → boot witness again →
# validate/baseline_v1.py → run.json. It never starts, stops, or restarts
# a server, never invents a document, and stops at the first failed gate
# while keeping every document already written. One-node profiles served
# on this node only. --out should live under results/baseline-v1/<spec_id>/.
set -euo pipefail
# Used by log/warn/die after sourcing lib.sh.
# shellcheck disable=SC2034
SCRIPT_NAME=baseline-v1
# shellcheck disable=SC1091
. "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/lib.sh"

PY="${PULSAR_LAB_PYTHON:-python3}"
usage() { sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; }
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  ""|--*) usage >&2; exit 2 ;;
esac
PROFILE="$1"
shift
SPEC="" OUT="" DATASET="" NODE_SELECTOR="" TAG="" SOAK_CONCURRENCY=8 LAB_COMMIT=""
POLICY="$REPO_DIR/policy/baseline-v1.json"
PRODUCER_DIR="$REPO_DIR/validate"
SKIP_W=0 CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --spec) [ $# -ge 2 ] || die "--spec requires a file" 2; SPEC="$2"; shift ;;
    --out) [ $# -ge 2 ] || die "--out requires a directory" 2; OUT="$2"; shift ;;
    --dataset) [ $# -ge 2 ] || die "--dataset requires a file" 2; DATASET="$2"; shift ;;
    --node) [ $# -ge 2 ] || die "--node requires a topology node id or hostname" 2; NODE_SELECTOR="$2"; shift ;;
    --tag) [ $# -ge 2 ] || die "--tag requires a value" 2; TAG="$2"; shift ;;
    --soak-concurrency) [ $# -ge 2 ] || die "--soak-concurrency requires a value" 2; SOAK_CONCURRENCY="$2"; shift ;;
    --lab-commit) [ $# -ge 2 ] || die "--lab-commit requires a 40-hex commit" 2; LAB_COMMIT="$2"; shift ;;
    --policy) [ $# -ge 2 ] || die "--policy requires a file" 2; POLICY="$2"; shift ;;
    --producer-dir) [ $# -ge 2 ] || die "--producer-dir requires a directory" 2; PRODUCER_DIR="$2"; shift ;;
    --skip-weights-check) SKIP_W=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown arg $1" 2 ;;
  esac
  shift
done
if [ -z "$SPEC" ] || [ -z "$OUT" ] || [ -z "$DATASET" ]; then
  die "usage: $0 <profile|spec_id> --spec FILE --out DIR --dataset FILE [options]" 2
fi
[ -f "$SPEC" ] || die "spec is not a file: $SPEC" 2
[ -f "$DATASET" ] || die "dataset is not a file: $DATASET" 2
[ -f "$POLICY" ] || die "policy is not a file: $POLICY" 2
case "$SOAK_CONCURRENCY" in *[!0-9]*|"") die "--soak-concurrency must be a positive integer" 2 ;; esac
[ "$SOAK_CONCURRENCY" -ge 1 ] || die "--soak-concurrency must be a positive integer" 2
[ -n "$TAG" ] || TAG="baseline-v1-$(date -u +%Y%m%dT%H%M%SZ)"
case "$TAG" in *[!A-Za-z0-9._-]*) die "invalid --tag: use only letters, numbers, dot, underscore, or hyphen" 2 ;; esac

# --- producers present ---------------------------------------------------
for producer in verify_snapshot_manifest.py serve_smoke.py gsm8k_eval.py soak.py; do
  [ -f "$PRODUCER_DIR/$producer" ] || die "missing producer $PRODUCER_DIR/$producer"
  "$PY" "$PRODUCER_DIR/$producer" --help >/dev/null 2>&1 \
    || die "producer cannot start: $PRODUCER_DIR/$producer (see docs/PREREQUISITES.md)"
done
[ -x "$PRODUCER_DIR/run-gates.sh" ] || die "missing producer $PRODUCER_DIR/run-gates.sh"
for tool in baseline_v1.py baseline_run.py; do
  [ -f "$REPO_DIR/validate/$tool" ] || die "missing $REPO_DIR/validate/$tool"
done
log "producers present"

# --- lab commit -----------------------------------------------------------
if [ -z "$LAB_COMMIT" ]; then
  LAB_COMMIT=$(git -C "$REPO_DIR" rev-parse HEAD 2>/dev/null) \
    || die "cannot read the lab commit; commit the checkout or pass --lab-commit"
fi
case "$LAB_COMMIT" in *[!0-9a-f]*|"") die "--lab-commit must be a 40-character lowercase hex commit" 2 ;; esac
[ "${#LAB_COMMIT}" -eq 40 ] || die "--lab-commit must be a 40-character lowercase hex commit" 2
if [ "${PULSAR_SELFTEST:-0}" = 1 ]; then
  warn "lab commit $LAB_COMMIT accepted without verification (PULSAR_SELFTEST=1)"
else
  git -C "$REPO_DIR" cat-file -e "${LAB_COMMIT}^{commit}" 2>/dev/null \
    || die "lab commit $LAB_COMMIT is not a commit of this checkout"
  if ! git -C "$REPO_DIR" diff --quiet "$LAB_COMMIT" -- . 2>/dev/null; then
    die "tracked files differ from commit $LAB_COMMIT; the producers that run must be the ones evidence names"
  fi
fi

# --- policy-derived arguments and dataset pin ------------------------------
policy_args=$("$PY" "$REPO_DIR/validate/baseline_run.py" run-args --policy "$POLICY") \
  || die "policy is unusable: $POLICY"
while IFS='=' read -r key value; do
  [ -n "$key" ] || continue
  case "$key" in
    GSM8K_*|SOAK_MINUTES|PERF_CONCURRENCIES) printf -v "$key" '%s' "$value" ;;
    *) die "unexpected policy argument $key" ;;
  esac
done <<<"$policy_args"
: "${GSM8K_DATASET_ID:?}" "${GSM8K_DATASET_REVISION:?}" "${GSM8K_DATASET_SHA256:?}" \
  "${GSM8K_SUBSET:?}" "${GSM8K_SPLIT:?}" "${GSM8K_SAMPLE_SIZE:?}" \
  "${GSM8K_MAX_COMPLETION_TOKENS:?}" "${GSM8K_REASONING_MODE:?}" "${SOAK_MINUTES:?}" \
  "${PERF_CONCURRENCIES:?}"
dataset_digest=$(sha256sum "$DATASET" | cut -d' ' -f1)
[ "$dataset_digest" = "$GSM8K_DATASET_SHA256" ] \
  || die "dataset digest $dataset_digest is not the policy pin $GSM8K_DATASET_SHA256 (policy/README.md)"
log "dataset matches the policy pin"

# --- spec, profile, placement ------------------------------------------------
spec_id=$("$PY" -m release_spec id "$SPEC" 2>/dev/null) || die "spec does not verify: $SPEC"
spec_image_digest=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["identity"]["image"]["digest"])' "$SPEC")
load_conf "$PROFILE"
NODE_SELECTOR=$(spec_overlay_node_selector "$NODE_SELECTOR")
if [ "$NODES" -eq 1 ]; then
  resolve_single_node_placement "$NODE_SELECTOR" \
    || die "cannot resolve physical node placement '$NODE_SELECTOR'"
  [ "${SINGLE_NODE_REMOTE:-0}" = 0 ] \
    || die "baseline-v1 runs on the serving node; placement on another node is not supported yet"
  URL=$(single_node_api_base_url "$PORT")
else
  die "multi-node baseline runs are not supported by this runner yet: every serving rank's container must be verified, which lands with the two-node milestone" 2
fi
case "$PROFILE" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f])
    [ "$PROFILE" = "$spec_id" ] || die "profile is spec $PROFILE but --spec is $spec_id"
    label="spec-${spec_id:0:12}"
    expected_spec_decode=off
    ;;
  *)
    catalog=$("$REPO_DIR/scripts/list-models.sh" --serving --json 2>/dev/null) \
      || die "cannot read the catalog projection for $PROFILE"
    identity_line=$(CATALOG_JSON="$catalog" "$PY" - "$CONF_NAME" "$spec_id" <<'PY'
import json, os, sys
conf, spec_id = sys.argv[1:]
for model in json.loads(os.environ["CATALOG_JSON"])["models"]:
    if model["id"] != conf:
        continue
    for identity in model["release_spec"]["identities"]:
        if identity["spec_id"] != spec_id:
            continue
        if identity["released"] and identity["comparison"] != "equal":
            print(f"released-differs {identity['comparison']}")
            raise SystemExit(0)
        print("ok " + ("on" if identity["spec_decode"] else "off"))
        raise SystemExit(0)
print("absent")
PY
    ) || die "cannot inspect the catalog projection for $PROFILE"
    case "$identity_line" in
      "ok "*) expected_spec_decode="${identity_line#ok }" ;;
      released-differs*) die "released spec $spec_id and profile $PROFILE compute different launch contracts (${identity_line#released-differs })" ;;
      *) die "spec $spec_id is not an identity the catalog computes for $PROFILE; regenerate it with scripts/release-spec.sh from-profile" ;;
    esac
    label="$CONF_NAME"
    ;;
esac
if [ "$SKIP_W" = 0 ]; then
  "$REPO_DIR/scripts/check-weights.sh" "$PROFILE" ${NODE_SELECTOR:+--node "$NODE_SELECTOR"} >/dev/null \
    || die "model files are not ready on every rank — run scripts/check-weights.sh $PROFILE"
fi
resolve_library_hot_for_profile "$CONF_NAME"
[ -d "$LIBRARY_VIEW_HUB_PATH" ] || die "served hub is not a directory on this node: $LIBRARY_VIEW_HUB_PATH"

# --- server identity ----------------------------------------------------------
cname=$(container_name_for "$CONF_NAME" "$NODES")
expected_contract=$(loaded_launch_contract_id)
meta=$("$PULSAR_DOCKER" inspect --format \
  '{"running":{{json .State.Running}},"labels":{{json .Config.Labels}},"image":{{json .Image}}}' \
  "$cname" 2>/dev/null) || die "container $cname is not present; start the profile first"
image_id=$(printf '%s' "$meta" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["image"])')
repo_digests=$("$PULSAR_DOCKER" image inspect --format '{{json .RepoDigests}}' "$image_id" 2>/dev/null) \
  || die "cannot inspect the served image"
META_JSON="$meta" REPO_DIGESTS="$repo_digests" "$PY" - "$CONF_NAME" "$expected_contract" \
  "$spec_image_digest" "$expected_spec_decode" "$PULSAR_MANAGED_LABEL" "$PULSAR_CONF_LABEL" \
  "$PULSAR_LAUNCH_CONTRACT_LABEL" "$PULSAR_SPEC_DECODE_LABEL" <<'PY' \
  || die "container $cname does not serve profile $PROFILE as written for spec $spec_id (see the lines above)"
import json, os, sys
conf, contract, digest, spec_decode, managed_key, conf_key, contract_key, spec_decode_key = sys.argv[1:]
meta = json.loads(os.environ["META_JSON"])
labels = meta.get("labels") or {}
problems = []
if not meta.get("running"):
    problems.append("container is not running")
if str(labels.get(managed_key, "")).lower() != "true":
    problems.append("container is not stack-managed")
if labels.get(conf_key) != conf:
    problems.append(f"container conf label is {labels.get(conf_key)!r}, expected {conf!r}")
if labels.get(contract_key) != contract:
    problems.append("container launch contract differs from the profile as written now")
if str(labels.get(spec_decode_key, "off")).lower() != spec_decode:
    problems.append(f"container speculative decoding is {labels.get(spec_decode_key, 'off')!r}; this spec expects {spec_decode!r}")
repo_digests = json.loads(os.environ["REPO_DIGESTS"]) or []
if not any(item.split("@", 1)[-1] == digest for item in repo_digests):
    problems.append("served image digest is not the spec's image digest")
for problem in problems:
    print(f"[baseline-v1] {problem}", file=sys.stderr)
raise SystemExit(1 if problems else 0)
PY
log "container $cname serves $PROFILE as written; image matches spec $spec_id"

api_auth_args=()
api_auth_curl_args api_auth_args
boot_witness() {
  curl -fsS --max-time 10 "${api_auth_args[@]}" "$URL/v1/models" \
    | "$PY" -c 'import json,sys; served=sys.argv[1]; data=json.load(sys.stdin).get("data") or []
rows=[m for m in data if m.get("id")==served]
raise SystemExit(print(int(rows[0]["created"])) or 0) if rows else SystemExit(1)' "$SERVED_NAME"
}
witness_before=$(boot_witness) || die "served model $SERVED_NAME is not listed at $URL/v1/models"
log "boot witness $witness_before"
if [ "$CHECK_ONLY" = 1 ]; then
  log "check-only: server matches spec $spec_id; no gate was run and nothing was written"
  exit 0
fi

# --- output directory -------------------------------------------------------------
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
for existing in verify-snapshot-manifest serve-smoke compare-captures evaluate-gsm8k validate-soak benchmark-serving spec run; do
  [ ! -e "$OUT/$existing.json" ] || die "refusing to overwrite $OUT/$existing.json (choose a fresh --out)"
done
if compgen -G "$REPO_DIR/results/${label}-${TAG}-*" >/dev/null; then
  die "raw artifacts results/${label}-${TAG}-* already exist; choose a unique --tag"
fi
case "$OUT" in
  "$REPO_DIR"/results/*) evidence_prefix="${OUT#"$REPO_DIR"/}/" ;;
  *)
    evidence_prefix="results/baseline-v1/$spec_id/"
    warn "--out is outside results/; evidence paths assume the files move to $evidence_prefix"
    ;;
esac

# --- gates ------------------------------------------------------------------------
declare -a GATES=()
STOPPED=""
run_gate() {
  local name="$1" started ended rc=0
  shift
  if [ -n "$STOPPED" ]; then
    return 0
  fi
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  log "== $name"
  "$@" || rc=$?
  ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  GATES+=("$name:$started:$ended:$rc")
  if [ "$rc" -ne 0 ]; then
    STOPPED="$name"
    warn "$name exited $rc; later gates are not attempted, documents written so far are kept"
  fi
}
run_gate verify-snapshot-manifest \
  "$PY" "$PRODUCER_DIR/verify_snapshot_manifest.py" --spec "$SPEC" --hub "$LIBRARY_VIEW_HUB_PATH" \
    --result-json "$OUT/verify-snapshot-manifest.json"
run_gate serve-smoke \
  "$PY" "$PRODUCER_DIR/serve_smoke.py" --url "$URL" --model "$SERVED_NAME" \
    --result-json "$OUT/serve-smoke.json"
# shellcheck disable=SC2086  # PERF_CONCURRENCIES is a policy-derived list of integers
run_gate run-gates \
  "$PRODUCER_DIR/run-gates.sh" "$label" --model "$SERVED_NAME" --url "$URL" --tag "$TAG" \
    --concurrency $PERF_CONCURRENCIES --measurement-dir "$OUT"
run_gate evaluate-gsm8k \
  "$PY" "$PRODUCER_DIR/gsm8k_eval.py" --url "$URL" --model "$SERVED_NAME" --dataset "$DATASET" \
    --dataset-id "$GSM8K_DATASET_ID" --dataset-revision "$GSM8K_DATASET_REVISION" \
    --subset "$GSM8K_SUBSET" --split "$GSM8K_SPLIT" --sample-size "$GSM8K_SAMPLE_SIZE" \
    --max-completion-tokens "$GSM8K_MAX_COMPLETION_TOKENS" --reasoning-mode "$GSM8K_REASONING_MODE" \
    --result-json "$OUT/evaluate-gsm8k.json"
run_gate validate-soak \
  "$PY" "$PRODUCER_DIR/soak.py" --url "$URL" --model "$SERVED_NAME" --minutes "$SOAK_MINUTES" \
    --concurrency "$SOAK_CONCURRENCY" --out "$REPO_DIR/results/${label}-${TAG}-soak.json" \
    --result-json "$OUT/validate-soak.json"

witness_after=$(boot_witness) || witness_after=0
policy_digest=""
proposed=""
if [ "$witness_before" != "$witness_after" ]; then
  warn "boot witness changed ($witness_before -> $witness_after): the server was restarted during the run; no evaluation"
else
  evaluation=$("$PY" "$REPO_DIR/validate/baseline_v1.py" --spec "$SPEC" --policy "$POLICY" \
    --measurements-dir "$OUT" --lab-commit "$LAB_COMMIT" \
    --evidence-path-prefix "$evidence_prefix" --out "$OUT/spec.json") \
    || die "evaluator failed; documents under $OUT are kept"
  printf '%s\n' "$evaluation"
  policy_digest=$(printf '%s\n' "$evaluation" | sed -n 's/^policy_digest=//p')
  proposed=$(printf '%s\n' "$evaluation" | sed -n 's/^proposed_status=//p')
fi
[ -n "$policy_digest" ] || policy_digest=$("$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from baseline_v1_policy import load_policy; print(load_policy(sys.argv[2])[1])' "$REPO_DIR/validate" "$POLICY")
record_args=(write --out "$OUT/run.json" --spec-id "$spec_id" --policy-digest "$policy_digest"
  --lab-commit "$LAB_COMMIT" --image-digest "$spec_image_digest" --launch-contract-id "$expected_contract"
  --witness-before "$witness_before" --witness-after "$witness_after")
for gate in ${GATES[@]+"${GATES[@]}"}; do record_args+=(--gate "$gate"); done
[ -z "$proposed" ] || record_args+=(--proposed-status "$proposed")
"$PY" "$REPO_DIR/validate/baseline_run.py" "${record_args[@]}"

if [ -n "$STOPPED" ]; then
  die "baseline-v1 stopped at $STOPPED for $PROFILE; documents under $OUT are kept"
fi
if [ "$witness_before" != "$witness_after" ]; then
  die "baseline-v1 documents for $PROFILE do not share one boot; rerun in one boot"
fi
log "baseline-v1 complete for $PROFILE: proposed_status=$proposed (spec $OUT/spec.json)"
