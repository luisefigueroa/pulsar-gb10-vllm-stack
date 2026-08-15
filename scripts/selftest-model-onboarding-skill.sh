#!/usr/bin/env bash
# Contracts for the pulsar-model-onboarding skill package.
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

grep -Fq 'name: pulsar-model-onboarding' "$skill" \
  || fail "skill frontmatter must name pulsar-model-onboarding"
grep -Eq 'new-model onboarding|onboard a brand-new' "$skill" \
  || fail "description must trigger on new-model onboarding"
grep -Fq 'qualification planning' "$skill" \
  || fail "description must trigger on qualification planning"
grep -Fq 'Model Serving Release evidence' "$skill" \
  || fail "description must trigger on Model Serving Release evidence"
grep -Fq 'interrupted onboarding' "$skill" \
  || fail "description must trigger on resume of interrupted onboarding"
grep -Fq 'display_name: "Pulsar Model Onboarding"' "$agent_yaml" \
  || fail "agents/openai.yaml must keep the generated display name"

grep -Fq 'STATUS="untested"' "$skill" \
  || fail "skill must require STATUS=\"untested\""
grep -Fq 'FIRST_RUN_CANDIDATE=0' "$skill" \
  || fail "skill must require FIRST_RUN_CANDIDATE=0"
grep -Fq 'no `EXPECTED_MODEL_SEAL`' "$skill" \
  || fail "skill must forbid EXPECTED_MODEL_SEAL on the draft profile"
grep -Fq 'no `MODEL_SERVING_RELEASE_ID`' "$skill" \
  || fail "skill must forbid MODEL_SERVING_RELEASE_ID on the draft profile"
grep -Fq 'ready-for-review PR' "$skill" \
  || fail "skill must publish the draft profile as its own ready-for-review PR"
grep -Fq 'Do not begin the onboarding journal until' "$skill" \
  || fail "skill must stop before the journal exists"
grep -Fq 'user reports that PR merged' "$skill" \
  || fail "skill must wait for the user to report the profile PR merged"
grep -Fq 'syncs local main' "$skill" \
  || fail "skill must sync local main after the profile PR merges"
grep -Fq 'new feature branch' "$skill" \
  || fail "skill must create a new feature branch after merge"

grep -Fq 'must not be represented as an exact' "$skill" \
  || fail "skill must refuse unsealed replicated qualification"
grep -Fq 'ADR 0004 qualification attempt' "$skill" \
  || fail "skill must name the refused qualification claim"
grep -Fq 'refs/main' "$skill" \
  || fail "skill must name the mutable refs/main unsealed path"
grep -Fq 'writable HF home' "$skill" \
  || fail "skill must name the writable HF home on the unsealed path"

grep -Fq 'no silent fallback' "$skill" \
  || fail "skill must forbid silent fallback"
grep -Fq 'no automatic fallback' "$skill" \
  || fail "skill must forbid automatic fallback"
grep -Fq 'library-hot' "$skill" \
  || fail "skill must offer library-hot as a qualifying path"
grep -Fq 'local-verified-readonly' "$skill" \
  || fail "skill must bind library-hot to local-verified-readonly"
grep -Fq 'live-remote-readonly' "$skill" \
  || fail "skill must bind live fabric to live-remote-readonly"
grep -Fq 'implementation gap' "$skill" \
  || fail "skill must stop on an uncomposable acquisition target"
grep -Fq 'observe every confirmed serving rank' "$skill" \
  || fail "skill must check for durable homes before acquisition"
grep -Fq 'scripts/model-serving-release-plan.sh build' "$skill" \
  || fail "skill must build the release plan after exact inputs exist"
grep -Fq 'scripts/model-serving-release-plan.sh verify' "$skill" \
  || fail "skill must verify the release plan before testing"
grep -Fq 'do not claim that a Validation' "$skill" \
  || fail "skill must not freeze a contract before planner inputs exist"

grep -Fq 'large acquisition' "$skill" \
  || fail "skill must require a separate large-acquisition confirmation"
grep -Fq '**launch**' "$skill" \
  || fail "skill must require a separate launch confirmation"
grep -Fq 'destructive cleanup' "$skill" \
  || fail "skill must require a separate destructive-cleanup confirmation"
grep -Fq 'Distribution/source choice' "$skill" \
  || fail "skill must make distribution/source choice explicit"

grep -Fq 'Do not use `validate/run-gates.sh` as the ADR attempt wrapper' "$skill" \
  || fail "skill must not wrap ADR attempts in run-gates.sh"
grep -Fq 'greedy_capture.py' "$skill" \
  || fail "skill must invoke greedy captures sequentially"
grep -Fq 'compare_captures.py' "$skill" \
  || fail "skill must invoke compare_captures.py"
grep -Fq 'bench_serve.py' "$skill" \
  || fail "skill must invoke bench_serve.py"
grep -Fq -- '--result-json' "$skill" \
  || fail "skill must persist closed --result-json measurements"
grep -Fq -- '--require-identical' "$skill" \
  || fail "strict same-boot comparison must reject FP-equivalent output"
grep -Fq 'without `eval`' "$skill" \
  || fail "skill must load frozen benchmark argv without shell evaluation"
grep -Fq 'failed evidence and must be preserved' "$skill" \
  || fail "skill must preserve complete conclusive failures"
grep -Fq 'repository-relative' "$skill" \
  || fail "skill must keep composer measurements on publishable result paths"
grep -Fq 'wall-clock UTC' "$skill" \
  || fail "skill must record per-operation wall-clock UTC timestamps"
grep -Fq 'Never share one enclosing timestamp' "$skill" \
  || fail "skill must not share one enclosing timestamp"
grep -Fq 'Never fabricate a missing validator measurement' "$skill" \
  || fail "skill must not invent missing validator output"

grep -Fq 'Never use the workflow-journal' "$skill" \
  || fail "skill must name the workflow-journal ID as forbidden identity input"
grep -Fq 'before correctness' "$skill" \
  || fail "skill must reobserve identities before correctness"
grep -Fq 'after correctness' "$skill" \
  || fail "skill must reobserve identities after correctness"
grep -Fq 'after benchmarking' "$skill" \
  || fail "skill must reobserve identities after benchmarking"
grep -Fq 'Never combine measurements when either identity changes' "$skill" \
  || fail "skill must refuse to combine measurements across identity changes"
grep -Fq 'domain-separated canonical hashes' "$skill" \
  || fail "skill must derive distinct launch and boot identities"

grep -Fq 'never issue a seal' "$skill" \
  || fail "skill must not issue a seal"
grep -Fq 'validation decision' "$skill" \
  || fail "skill must not issue a validation decision"
grep -Fq 'assign a status' "$skill" \
  || fail "skill must not assign a status"
grep -Fq 'bind a profile to a' "$skill" \
  || fail "skill must not bind a profile to a release"
grep -Fq 'trusted registry' "$skill" \
  || fail "skill must not publish into the trusted registry"
grep -Fq 'promote a path' "$skill" \
  || fail "skill must not promote a path"
grep -Fq 'claim physical behavior' "$skill" \
  || fail "skill must not claim physical behavior"
grep -Fq 'Status is advisory' "$skill" \
  || fail "skill must keep status advisory"
grep -Fq 'never blocks serving' "$skill" \
  || fail "skill must not treat status as a serving gate"
grep -Fq 'fail closed' "$skill" \
  || fail "skill must keep concrete operational failures fail-closed"

grep -Fq 'ownership-safe' "$skill" \
  || fail "skill must require ownership-safe cleanup"
grep -Fq 'scripts/down.sh' "$skill" \
  || fail "skill must use the normal stop path"
grep -Fq 'Refuse' "$skill" \
  || fail "skill must refuse cleanup while a managed service still uses the resource"

grep -Fq 'not a sixth ADR object' "$skill" \
  || fail "skill must not treat the journal as an ADR object"
grep -Fq 'experiments/model-onboarding/' "$skill" \
  || fail "skill must keep journal state under experiments/model-onboarding/"

grep -Fq 'STATUS="untested"' "$phases" \
  || fail "phase checklist must repeat the draft-profile STATUS contract"
grep -Fq 'Do not use `validate/run-gates.sh` as the ADR attempt wrapper' "$phases" \
  || fail "phase checklist must keep sequential measurement rules"
grep -Fq 'No seal was issued' "$handoff" \
  || fail "handoff template must deny reviewed authority"

echo "OK   pulsar-model-onboarding skill package contracts"
