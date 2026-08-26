#!/usr/bin/env bash
# Contracts for the pulsar-model-serving-release-issuance skill package.
# shellcheck disable=SC2016
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

skill="$REPO_DIR/skills/pulsar-model-serving-release-issuance/SKILL.md"
phases="$REPO_DIR/skills/pulsar-model-serving-release-issuance/references/workflow-phases.md"
notes="$REPO_DIR/skills/pulsar-model-serving-release-issuance/references/review-declaration-notes.md"
agent_yaml="$REPO_DIR/skills/pulsar-model-serving-release-issuance/agents/openai.yaml"

[ -f "$skill" ] || fail "missing $skill"
[ -f "$phases" ] || fail "missing $phases"
[ -f "$notes" ] || fail "missing $notes"
[ -f "$agent_yaml" ] || fail "missing $agent_yaml"
[ ! -e "$REPO_DIR/skills/pulsar-model-serving-release-issuance/README.md" ] \
  || fail "skill package must not add a README"
[ ! -e "$REPO_DIR/skills/pulsar-model-serving-release-issuance/scripts" ] \
  || fail "issuance skill must not add an orchestration journal helper"

skill_lines=$(wc -l <"$skill")
[ "$skill_lines" -lt 500 ] \
  || fail "SKILL.md must stay under 500 lines (has $skill_lines)"

grep -Fq 'name: pulsar-model-serving-release-issuance' "$skill" \
  || fail "skill frontmatter must name pulsar-model-serving-release-issuance"
grep -Fq 'Model Serving Release issuance' "$skill" \
  || fail "description must trigger on Model Serving Release issuance"
grep -Fq 'issue.sh' "$skill" \
  || fail "description must trigger on issue.sh"
grep -Fq 'MODEL_SERVING_RELEASE_ID' "$skill" \
  || fail "description must trigger on MODEL_SERVING_RELEASE_ID"
grep -Fq 'capture candidate' "$skill" \
  || fail "description must trigger on capture candidate issuance"
grep -Fq 'display_name: "Pulsar Model Serving Release Issuance"' "$agent_yaml" \
  || fail "agents/openai.yaml must keep the generated display name"

grep -Fq 'This skill has no authority' "$skill" \
  || fail "skill must deny issuance authority"
grep -Fq 'does not establish trust' "$skill" \
  || fail "skill must say local plan or stage does not establish trust"
grep -Fq 'Repository review and merge remain the trust event' "$skill" \
  || fail "skill must keep repository review and merge as the trust event"
grep -Fq 'Status is advisory' "$skill" \
  || fail "skill must keep status advisory"
grep -Fq 'never blocks serving' "$skill" \
  || fail "skill must not treat status as a serving gate"
grep -Fq 'Never mutate the capture candidate' "$skill" \
  || fail "skill must not mutate the capture candidate"
grep -Fq 'Never auto-pass' "$skill" \
  || fail "skill must not auto-pass provenance or privacy"
grep -Fq 'ADR 0002' "$skill" \
  || fail "skill must refuse treating smoke as model qualification"
grep -Fq 'FAMILY_RECOMMENDED' "$skill" \
  || fail "skill must not promote FAMILY_RECOMMENDED on first issuance"
grep -Fq 'expected_status' "$skill" \
  || fail "skill must treat expected_status as an assertion"
grep -Fq 'testing-incomplete' "$skill" \
  || fail "skill must name testing-incomplete as an honest derived status"
grep -Fq 'There is no orchestration journal' "$skill" \
  || fail "skill must not add an orchestration journal"
grep -Fq 'Never recapture a maintainer essay' "$skill" \
  || fail "skill must not recapture a dummy review document"
grep -Fq 'Empty `review_evidence_artifact_ids`' "$skill" \
  || fail "skill must treat empty leftover review IDs as expected"
grep -Fq 'Do not invent review evidence' "$skill" \
  || fail "skill must not invent review evidence to make plan succeed"

grep -Fq 'scripts/model-serving-release-capture.sh verify-candidate' "$skill" \
  || fail "skill must re-verify the capture candidate"
grep -Fq 'scripts/model-serving-release-issue.sh plan' "$skill" \
  || fail "skill must run issue.sh plan"
grep -Fq 'scripts/model-serving-release-issue.sh stage' "$skill" \
  || fail "skill must run issue.sh stage"
grep -Fq 'clean non-default branch' "$skill" \
  || fail "skill must require a clean non-default branch"
grep -Fq 'issue.sh` does not edit a profile' "$skill" \
  || fail "skill must say issue.sh does not edit a profile"
grep -Fq 'Same publication only' "$skill" \
  || fail "skill must keep profile bind on the same publication"
grep -Fq 'Do not merge' "$skill" \
  || fail "skill must not merge the issuance PR"
grep -Fq 'scripts/model-serving-release-registry.sh verify' "$skill" \
  || fail "skill must verify the ADR 0004 registry after reported merge"
if grep -Fq 'scripts/model-library.sh validation-bundle verify' "$skill"; then
  fail "skill must not run schema-1 validation-bundle verify as the ADR 0004 check"
fi
grep -Fq 'all five' "$skill" \
  || fail "skill must keep all five provenance components pending for empty leftovers"
grep -Fq 'Park them' "$notes" \
  || fail "review notes must park extra untracked files before stage"
grep -Fq 'docs/MODEL_SERVING_RELEASE_ISSUANCE.md' "$skill" \
  || fail "skill must defer the review schema to the issuance runbook"

grep -Fq 'plan' "$phases" \
  || fail "phase checklist must include plan"
grep -Fq 'stage' "$phases" \
  || fail "phase checklist must include stage"
grep -Fq 'Derived status must equal' "$phases" \
  || fail "phase checklist must compare derived and asserted status"

grep -Fq 'MODEL_SERVING_RELEASE_ISSUANCE.md' "$notes" \
  || fail "review notes must point at the live issuance schema"
grep -Fq 'Do not fork it here' "$notes" \
  || fail "review notes must not fork the review schema"
grep -Fq 'evidence_artifacts' "$notes" \
  || fail "review notes must say how to extract artifact IDs"
grep -Fq 'Extra measurements' "$notes" \
  || fail "review notes must keep extra measurements out of issuance inputs"

echo "OK   pulsar-model-serving-release-issuance skill package contracts"
