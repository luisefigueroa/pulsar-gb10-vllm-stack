#!/usr/bin/env bash
# Contracts for the pulsar-model-onboarding skill package (ADR 0017 Stage 4).
# shellcheck disable=SC2016
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

skill="$REPO_DIR/skills/pulsar-model-onboarding/SKILL.md"
phases="$REPO_DIR/skills/pulsar-model-onboarding/references/workflow-phases.md"
handoff="$REPO_DIR/skills/pulsar-model-onboarding/references/handoff-template.md"
agent_yaml="$REPO_DIR/skills/pulsar-model-onboarding/agents/openai.yaml"
journal="$REPO_DIR/skills/pulsar-model-onboarding/scripts/onboarding_journal.py"

[ -f "$skill" ] || fail "missing $skill"
[ -f "$phases" ] || fail "missing $phases"
[ -f "$handoff" ] || fail "missing $handoff"
[ -f "$agent_yaml" ] || fail "missing $agent_yaml"
[ -f "$journal" ] || fail "missing $journal"
[ ! -e "$REPO_DIR/skills/pulsar-model-onboarding/README.md" ] \
  || fail "skill package must not add a README"

skill_lines=$(wc -l <"$skill")
[ "$skill_lines" -lt 500 ] \
  || fail "SKILL.md must stay under 500 lines (has $skill_lines)"

# --- package identity -------------------------------------------------------
grep -Fq 'name: pulsar-model-onboarding' "$skill" \
  || fail "skill frontmatter must name pulsar-model-onboarding"
grep -Eq 'new-model onboarding|onboard a brand-new' "$skill" \
  || fail "description must trigger on new-model onboarding"
grep -Fq 'interrupted onboarding' "$skill" \
  || fail "description must trigger on resume of interrupted onboarding"
grep -Fq 'ADR 0017 Stage 4' "$skill" \
  || fail "description must name the Stage 4 contract"
grep -Fq 'display_name: "Pulsar Model Onboarding"' "$agent_yaml" \
  || fail "agents/openai.yaml must keep the generated display name"
grep -Fq 'measured release spec' "$agent_yaml" \
  || fail "agent prompt must describe the measured-spec flow"
grep -Fq 'promotion pull request' "$agent_yaml" \
  || fail "agent prompt must end in the promotion pull request"

# --- a profile is a spec id; drafts are lab input only ---------------------
grep -Fq 'A profile is a released spec id' "$skill" \
  || fail "skill must define a profile as a released spec id"
grep -Fq '`models/*.conf` no' "$skill" \
  || fail "skill must state that conf profiles no longer exist"
grep -Fq 'home add --draft <draft.conf>' "$skill" \
  || fail "skill must acquire through home add --draft"
grep -Fq 'from-draft' "$skill" \
  || fail "skill must generate the measured spec with from-draft"
grep -Fq 'Nothing in the stack starts a draft' "$skill" \
  || fail "skill must state that no stack command starts a draft"
grep -Fq 'PULSAR_SPEC_FILE=<measured-spec' "$skill" \
  || fail "skill must start the measured spec through PULSAR_SPEC_FILE"
grep -Fq '.pulsar-overlay.json' "$skill" \
  || fail "skill must name the deployment overlay as the served-name source"
grep -Fq 'scripts/testdata/drafts/' "$skill" \
  || fail "skill must keep selftest drafts out of onboarding"
! grep -Fq 'STATUS="untested"' "$skill" \
  || fail "skill must not require the retired STATUS label"
! grep -Fq 'MODEL_SERVING_RELEASE_ID' "$skill" \
  || fail "skill must not reference the retired release id field"
! grep -Eq 'model-serving-release-(plan|attempt|capture|issue)' "$skill" \
  || fail "skill must not invoke the ADR 0004 tooling"
! grep -Fq 'model-serving-experiment-monitor' "$skill" \
  || fail "skill must not start the experiment monitor"

# --- hard stops ---------------------------------------------------------------
grep -Fq 'never set a' "$skill" && grep -Fq 'review.status' "$skill" \
  || fail "skill must not set a review status"
grep -Fq 'write under `releases/`' "$skill" \
  || fail "skill must not write under releases/"
grep -Fq 'Review status is display-only and never blocks serving' "$skill" \
  || fail "skill must keep review status display-only"
grep -Fq 'fail without fallback' "$skill" \
  || fail "skill must keep concrete operational failures fail without fallback"
grep -Fq 'Do not silently select another node' "$skill" \
  || fail "skill must forbid silent fallback"
grep -Fq 'Do not offer live NFS/RDMA serving' "$skill" \
  || fail "skill must refuse live NFS/RDMA as a runtime-access path"
grep -Fq 'Do not mutate `refs/main`' "$skill" \
  || fail "skill must name the mutable refs/main path"
grep -Fq 'never a mutable selector' "$skill" \
  || fail "skill must acquire the exact planned commit, never a selector"
grep -Fq 'Do not edit a measured spec by hand' "$skill" \
  || fail "skill must forbid hand-edited specs"
grep -Fq 'A failed gate is evidence' "$skill" \
  || fail "skill must keep a failed gate as evidence"
grep -Fq 'Do not rerun a failed gate' "$skill" \
  || fail "skill must not chase a pass"
grep -Fq 'Changing the recipe means a new draft' "$skill" && grep -Fq 'a new spec id, and a new run' "$skill" \
  || fail "skill must make a recipe change a new identity"

# --- confirmations --------------------------------------------------------------
grep -Fq '**large acquisition**' "$skill" \
  || fail "skill must require a separate large-acquisition confirmation"
grep -Fq '**launch**' "$skill" \
  || fail "skill must require a separate launch confirmation"
grep -Fq '**destructive cleanup**' "$skill" \
  || fail "skill must require a separate destructive-cleanup confirmation"
grep -Fq 'Distribution/source choice must be explicit' "$skill" \
  || fail "skill must make distribution/source choice explicit"

# --- workflow order ---------------------------------------------------------------
grep -Fq '### 1. Draft, then stop' "$skill" \
  || fail "skill must stop after the draft"
grep -Fq -- '--revision <selector> --plan --json' "$skill" \
  || fail "skill must compose the read-only Hugging Face acquisition plan"
grep -Fq -- '--revision <exact-commit-from-plan>' "$skill" \
  || fail "skill must acquire the exact planned commit after confirmation"
grep -Fq -- '--node <selected-rank-from-plan>' "$skill" \
  || fail "skill must bind execution to the reviewed rank"
grep -Fq 'home verify <model_id@revision> --json' "$skill" \
  || fail "skill must reuse an exact home only after receipt-backed verification"
grep -Fq 'An older tree without a receipt fails without' "$skill" \
  || fail "skill must fail closed without a receipt"
grep -Fq 'python3 -m release_spec id <measured-spec.json>' "$skill" \
  || fail "skill must derive the spec id from the measured spec"
grep -Fq 'scripts/model-library.sh prepare <spec_id> --yes' "$skill" \
  || fail "skill must prepare by spec id"
grep -Fq 'scripts/check-weights.sh <spec_id>' "$skill" \
  || fail "skill must verify every rank before launch"
grep -Fq './pulsar start <spec_id>' "$skill" \
  || fail "skill must start by spec id"
grep -Fq -- '--check-only' "$skill" \
  || fail "skill must prove the server is the spec before measuring"
grep -Fq 'validate/baseline-v1.sh <spec_id> --spec <measured-spec.json>' "$skill" \
  || fail "skill must run baseline-v1 by spec id"
grep -Fq 'results/baseline-v1/<spec_id>' "$skill" \
  || fail "skill must write evidence under results/baseline-v1/<spec_id>"
grep -Fq 'scripts/release-spec.sh promote' "$skill" \
  || fail "skill must promote through the promote command"
grep -Fq 'one reviewed pull request per spec' "$skill" \
  || fail "skill must open one promotion pull request per spec"
grep -Fq 'scripts/release.sh list --markdown' "$skill" \
  || fail "skill must regenerate the MODELS.md block"
grep -Fq 'scripts/check_publishable_privacy.py' "$skill" \
  || fail "skill must require the privacy scan before the pull request"
grep -Fq './pulsar stop <spec_id>' "$skill" \
  || fail "skill must use the normal stop path"
grep -Fq 'Refuse when a managed service still uses the resource' "$skill" \
  || fail "skill must refuse cleanup while a managed service still uses the resource"

# --- journal ------------------------------------------------------------------------
grep -Fq 'experiments/model-onboarding/workflows/<workflow-id>/' "$skill" \
  || fail "skill must isolate journal state below the workflows namespace"
grep -Fq -- '--id spec_id=<id>' "$skill" \
  || fail "skill must journal the spec id"
grep -Fq -- '--id receipt_id=<digest>' "$skill" \
  || fail "skill must journal the immutable receipt identity"
grep -Fq 'Reject credentials, raw' "$skill" \
  || fail "skill must keep site identifiers out of the journal"

# --- references -----------------------------------------------------------------
grep -Fq 'home add --draft <draft.conf> --revision <selector> --plan --json' "$phases" \
  || fail "phase checklist must plan through home add --draft"
grep -Fq 'PULSAR_SPEC_FILE=<measured-spec.json>' "$phases" \
  || fail "phase checklist must start the measured spec by id"
grep -Fq 'validate/baseline-v1.sh <spec_id>' "$phases" \
  || fail "phase checklist must run baseline-v1 by spec id"
grep -Fq 'scripts/release-spec.sh promote' "$phases" \
  || fail "phase checklist must end in promotion"
! grep -Eq 'models/<profile>\.conf|STATUS="untested"|capture-run' "$phases" \
  || fail "phase checklist must not carry conf-era steps"
grep -Fq 'assigns no status' "$handoff" \
  || fail "handoff template must deny reviewed authority"
grep -Fq 'Spec id:' "$handoff" && grep -Fq 'Receipt id:' "$handoff" \
  || fail "handoff must list the spec and receipt ids"
grep -Fq 'results/baseline-v1/<spec_id>/' "$handoff" \
  || fail "handoff must name the evidence directory"
grep -Fq 'Promotion pull request:' "$handoff" \
  || fail "handoff must name the promotion pull request"

# Experiment monitoring never reaches catalog serving entrypoints.
for entrypoint in pulsar wizard.sh scripts/up.sh serve.sh cluster/start-cluster.sh \
  scripts/status.sh scripts/inventory.sh; do
  if grep -Fq 'model-serving-experiment-monitor' "$REPO_DIR/$entrypoint"; then
    fail "catalog-serving entrypoint must not invoke experiment monitoring: $entrypoint"
  fi
done

python3 -m py_compile "$journal" || fail "journal helper must compile"

echo "model onboarding skill selftest: PASS"
