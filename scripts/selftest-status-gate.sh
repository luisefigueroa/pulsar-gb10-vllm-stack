#!/usr/bin/env bash
# Self-test advisory STATUS policy (no Docker).
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
. "$REPO_DIR/scripts/lib.sh"

STATE=$(mktemp -d "${TMPDIR:-/tmp}/pulsar-status-projection.XXXXXX")
trap 'rm -rf "$STATE"' EXIT

pass=0
fail=0
check() {
  local status="$1" expect_tested="$2"
  STATUS="$status"
  if status_is_tested; then tested=1; else tested=0; fi
  if status_is_launchable; then launchable=1; else launchable=0; fi
  if status_requires_force; then requires_force=1; else requires_force=0; fi
  if [ "$tested" = "$expect_tested" ] \
      && [ "$launchable" = 1 ] && [ "$requires_force" = 0 ]; then
    echo "OK   STATUS=$status  tested=$tested advisory=1"
    pass=$((pass + 1))
  else
    echo "FAIL STATUS=$status tested=$tested expected_tested=$expect_tested launchable=$launchable requires_force=$requires_force" >&2
    fail=$((fail + 1))
  fi
}

# Legacy tested labels remain recommendation classifiers.
check tested 1
check tested+soaked 1
check "tested-experimental" 1

# Every other label is also launchable with respect to status.
check untested 0
check do-not-use 0
check blocked-upstream 0
check blocked 0
check "?" 0
check experimental 0
check "" 0

# conf files on disk
for pair in \
  "qwen3.6-27b-fp8-2node:0" \
  "qwen3.8-27b-fp8:0" \
  "nemotron-3-nano-30b-nvfp4:1" \
  "qwen3.6-27b-fp8:1" \
  "qwen3-1.7b-2node:0"
do
  name="${pair%%:*}"
  exp_tested="${pair##*:}"
  load_conf "$name"
  if status_is_tested; then tested=1; else tested=0; fi
  if status_is_launchable; then launchable=1; else launchable=0; fi
  if status_requires_force; then requires_force=1; else requires_force=0; fi
  if [ "$tested" = "$exp_tested" ] \
      && [ "$launchable" = 1 ] && [ "$requires_force" = 0 ]; then
    echo "OK   conf=$name STATUS=$STATUS tested=$tested advisory=1"
    pass=$((pass + 1))
  else
    echo "FAIL conf=$name STATUS=$STATUS tested=$tested expected_tested=$exp_tested launchable=$launchable requires_force=$requires_force" >&2
    fail=$((fail + 1))
  fi
done

STATUS=do-not-use
if status_is_blocked; then
  echo "OK   blocked labels remain descriptive classifiers"
  pass=$((pass + 1))
else
  echo "FAIL do-not-use lost its descriptive classifier" >&2
  fail=$((fail + 1))
fi

if ! grep -q 'status_requires_force' \
    "$REPO_DIR/scripts/up.sh" \
    "$REPO_DIR/serve.sh" \
    "$REPO_DIR/cluster/start-cluster.sh" \
    "$REPO_DIR/wizard.sh"; then
  echo "OK   serving entrypoints contain no status-derived refusal"
  pass=$((pass + 1))
else
  echo "FAIL a serving entrypoint still calls the legacy status gate" >&2
  fail=$((fail + 1))
fi

if grep -Fq 'list-models.sh" --serving --json' "$REPO_DIR/wizard.sh" \
    && ! grep -Fq 'list-models.sh" --validated --serving --json' "$REPO_DIR/wizard.sh"; then
  echo "OK   wizard catalog selection has no status filter"
  pass=$((pass + 1))
else
  echo "FAIL wizard catalog selection still filters by status" >&2
  fail=$((fail + 1))
fi

catalog_json=$("$REPO_DIR/scripts/list-models.sh" --serving --json)
if CATALOG_JSON="$catalog_json" python3 - <<'PY'
import json
import os

models = json.loads(os.environ["CATALOG_JSON"])["models"]
assert models
order = [model["id"] for model in models]
for model in models:
    assert model["legacy_status"] == model["status"]
    release = model["model_serving_release"]
    assert release == {
        "release_id": None,
        "state": "legacy-unbound",
        "effective_status": None,
        "effective_status_label": "No release binding",
        "contract_id": None,
        "decision_id": None,
        "advisory": True,
    }
    spec = model["release_spec"]
    assert spec["receipt"] in {"found", "missing", "unreadable"}
    assert isinstance(spec["identities"], list)
    if spec["receipt"] != "found":
        assert spec["identities"] == []
    for identity in spec["identities"]:
        assert set(identity) >= {
            "spec_decode",
            "default",
            "spec_id",
            "released",
            "comparison",
            "differs_fields",
            "review_status",
            "reviewed_at",
            "release_file",
        }
        assert identity["released"] is False
        assert identity["review_status"] is None
assert order == [model["id"] for model in models]
PY
then
  echo "OK   catalog separates reviewed release projection from legacy status"
  pass=$((pass + 1))
else
  echo "FAIL catalog release projection contract" >&2
  fail=$((fail + 1))
fi

narrow_catalog=$(COLUMNS=48 "$REPO_DIR/scripts/list-models.sh" --serving)
if NARROW_CATALOG="$narrow_catalog" python3 - <<'PY'
import os

lines = os.environ["NARROW_CATALOG"].splitlines()
assert lines
assert max(map(len, lines)) <= 48
assert any("Release" in line and "No release binding" in line for line in lines)
assert not any("Testing incomplete" in line for line in lines)
assert any("Legacy" in line for line in lines)
assert any("Spec review" in line for line in lines)
PY
then
  echo "OK   human catalog projection honors narrow terminal width"
  pass=$((pass + 1))
else
  echo "FAIL narrow catalog release projection" >&2
  fail=$((fail + 1))
fi

if grep -Fq 'spec-review=' "$REPO_DIR/scripts/up.sh"; then
  echo "OK   up.sh prints display-only spec-review"
  pass=$((pass + 1))
else
  echo "FAIL up.sh missing spec-review line" >&2
  fail=$((fail + 1))
fi

MODEL_SERVING_RELEASE_ID=not-a-content-id
load_model_serving_release_projection local-verified-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = projection-unavailable ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS_LABEL" = "Release status unavailable" ] \
    && status_is_launchable; then
  echo "OK   unavailable release projection remains advisory"
  pass=$((pass + 1))
else
  echo "FAIL unavailable release projection affected status policy" >&2
  fail=$((fail + 1))
fi

python3 - "$STATE/repo" "$STATE/neutral-repo" "$STATE/ambiguous-repo" <<'PY'
from pathlib import Path
import sys

from scripts.testlib import model_serving_release_registry_fixture as fixture

repo = Path(sys.argv[1])
repo.mkdir()
source = fixture.populate_happy_registry(
    repo / "models" / "model-serving-releases", repo
)
(repo / "release-id").write_text(
    source["release"]["release_id"] + "\n", encoding="utf-8"
)
neutral_repo = Path(sys.argv[2])
neutral_registry = fixture.init_registry_root(
    neutral_repo / "models" / "model-serving-releases"
)
fixture.write_release(neutral_registry, source["release"])
(neutral_repo / "release-id").write_text(
    source["release"]["release_id"] + "\n", encoding="utf-8"
)
ambiguous_repo = Path(sys.argv[3])
ambiguous_source = fixture.populate_happy_registry(
    ambiguous_repo / "models" / "model-serving-releases", ambiguous_repo
)
fixture.write_contract(
    ambiguous_repo / "models" / "model-serving-releases",
    fixture.build_alternate_contract(ambiguous_source["release"]),
)
(ambiguous_repo / "release-id").write_text(
    ambiguous_source["release"]["release_id"] + "\n", encoding="utf-8"
)
PY
original_repo_dir="$REPO_DIR"
MODEL_SERVING_RELEASE_ID=$(<"$STATE/repo/release-id")
PULSAR_MODEL_SERVING_RELEASE_REGISTRY_PY="$original_repo_dir/scripts/model_serving_release_registry.py"
REPO_DIR="$STATE/repo"
load_model_serving_release_projection local-verified-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = unique-reviewed-decision ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS" = validated ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS_LABEL" = Validated ]; then
  echo "OK   exact bound release projects its reviewed status"
  pass=$((pass + 1))
else
  echo "FAIL exact bound release projection" >&2
  fail=$((fail + 1))
fi
load_model_serving_release_projection live-remote-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = recipe-mismatch ] \
    && [ -z "$MODEL_SERVING_RELEASE_STATUS" ] \
    && status_is_launchable; then
  echo "OK   different runtime-access recipe does not inherit status"
  pass=$((pass + 1))
else
  echo "FAIL runtime-access recipe mismatch projection" >&2
  fail=$((fail + 1))
fi
MODEL_SERVING_RELEASE_ID=$(<"$STATE/neutral-repo/release-id")
REPO_DIR="$STATE/neutral-repo"
load_model_serving_release_projection local-verified-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = no-reviewed-decision ] \
    && [ -z "$MODEL_SERVING_RELEASE_STATUS" ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS_LABEL" = "No reviewed decision" ]; then
  echo "OK   absent reviewed decision remains neutral rather than Untested"
  pass=$((pass + 1))
else
  echo "FAIL neutral no-reviewed-decision projection" >&2
  fail=$((fail + 1))
fi
MODEL_SERVING_RELEASE_ID=$(<"$STATE/ambiguous-repo/release-id")
REPO_DIR="$STATE/ambiguous-repo"
load_model_serving_release_projection local-verified-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = ambiguous ] \
    && [ -z "$MODEL_SERVING_RELEASE_STATUS" ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS_LABEL" = "Ambiguous reviewed decisions" ]; then
  echo "OK   ambiguous reviewed decisions remain a precise advisory state"
  pass=$((pass + 1))
else
  echo "FAIL ambiguous release projection" >&2
  fail=$((fail + 1))
fi
load_model_serving_release_projection live-remote-readonly
if [ "$MODEL_SERVING_RELEASE_PROJECTION_STATE" = recipe-mismatch ] \
    && [ -z "$MODEL_SERVING_RELEASE_STATUS" ] \
    && [ "$MODEL_SERVING_RELEASE_STATUS_LABEL" = "No decision for selected recipe" ]; then
  echo "OK   recipe mismatch remains precise when the release is ambiguous"
  pass=$((pass + 1))
else
  echo "FAIL ambiguous recipe-mismatch projection" >&2
  fail=$((fail + 1))
fi
REPO_DIR="$original_repo_dir"
unset PULSAR_MODEL_SERVING_RELEASE_REGISTRY_PY MODEL_SERVING_RELEASE_ID

python3 - "$REPO_DIR" "$STATE/spec-fixture" <<'PY'
import json
import pathlib
import sys

sys.path.insert(0, sys.argv[1])
from release_spec import pretty_json_bytes
from scripts.testlib.test_release_consumer import (
    NANO,
    PINNED_IMAGE,
    released_nano_spec,
    write_fixture_library,
)
from scripts.testlib.test_release_spec_generate import nano_kwargs

root = pathlib.Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
library, catalog, _receipt = write_fixture_library(
    root, model_id=nano_kwargs()["model_id"], profile=NANO
)
releases = root / "releases"
releases.mkdir(exist_ok=True)
spec = released_nano_spec()
(releases / f"{spec['spec_id']}.json").write_bytes(pretty_json_bytes(spec))
(root / "paths.json").write_text(
    json.dumps(
        {
            "library": str(library),
            "catalog": str(catalog),
            "releases": str(releases),
            "image": PINNED_IMAGE,
        }
    )
    + "\n",
    encoding="utf-8",
)
PY

library_dir=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["library"])' "$STATE/spec-fixture/paths.json")
catalog_file=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["catalog"])' "$STATE/spec-fixture/paths.json")
releases_dir=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["releases"])' "$STATE/spec-fixture/paths.json")
pinned_image=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["image"])' "$STATE/spec-fixture/paths.json")

fixture_json=$(
  MODEL_LIBRARY_DIR="$library_dir" \
  MODEL_LIBRARY_CATALOG="$catalog_file" \
  PULSAR_RELEASES_ROOT="$releases_dir" \
  VLLM_IMAGE_MAINLINE="$pinned_image" \
  "$REPO_DIR/scripts/list-models.sh" --serving --json
)
if FIXTURE_JSON="$fixture_json" python3 - <<'PY'
import json
import os

models = {item["id"]: item for item in json.loads(os.environ["FIXTURE_JSON"])["models"]}
nano = models["nemotron-3-nano-30b-nvfp4"]
assert nano["model_serving_release"]["effective_status_label"] == "No release binding"
row = nano["release_spec"]["identities"][0]
assert nano["release_spec"]["receipt"] == "found"
assert row["comparison"] == "equal"
assert row["review_status"] == "stable"
PY
then
  echo "OK   fixture catalog shows equal stable spec review"
  pass=$((pass + 1))
else
  echo "FAIL fixture catalog stable spec review" >&2
  fail=$((fail + 1))
fi

fixture_human=$(
  MODEL_LIBRARY_DIR="$library_dir" \
  MODEL_LIBRARY_CATALOG="$catalog_file" \
  PULSAR_RELEASES_ROOT="$releases_dir" \
  VLLM_IMAGE_MAINLINE="$pinned_image" \
  COLUMNS=48 \
  "$REPO_DIR/scripts/list-models.sh" --serving
)
if FIXTURE_HUMAN="$fixture_human" python3 - <<'PY'
import os

lines = os.environ["FIXTURE_HUMAN"].splitlines()
assert lines
assert max(map(len, lines)) <= 48
assert any("stable since" in line for line in lines)
PY
then
  echo "OK   fixture human catalog shows stable since"
  pass=$((pass + 1))
else
  echo "FAIL fixture human stable since" >&2
  fail=$((fail + 1))
fi

hidden_human=$(
  MODEL_LIBRARY_DIR="$library_dir" \
  MODEL_LIBRARY_CATALOG="$catalog_file" \
  PULSAR_RELEASES_ROOT="$releases_dir" \
  VLLM_IMAGE_MAINLINE="$pinned_image" \
  VLLM_EXTRA_ARGS="--enforce-eager" \
  COLUMNS=48 \
  "$REPO_DIR/scripts/list-models.sh" --serving
)
if HIDDEN_HUMAN="$hidden_human" python3 - <<'PY'
import os

lines = os.environ["HIDDEN_HUMAN"].splitlines()
assert lines
assert max(map(len, lines)) <= 48
blob = " ".join(line.strip() for line in lines)
assert "hidden (launch contract differs: argv)" in blob
assert "stable since" not in blob
PY
then
  echo "OK   extra args hide spec review.status"
  pass=$((pass + 1))
else
  echo "FAIL extra args did not hide spec review" >&2
  fail=$((fail + 1))
fi

export VLLM_IMAGE_MAINLINE="$pinned_image"
export PULSAR_MODEL_LIBRARY_DIR="$library_dir"
export PULSAR_MODEL_LIBRARY_CATALOG="$catalog_file"
export PULSAR_RELEASES_ROOT="$releases_dir"
load_conf nemotron-3-nano-30b-nvfp4
resolve_spec_decode auto
load_release_spec_projection
spec_cell=$(release_spec_enabled_cell "${SPEC_DECODE_ENABLED:-0}")
if [ "$spec_cell" = "stable since 2026-09-02T00:00:00Z" ]; then
  echo "OK   up.sh spec-review cell matches the resolved identity"
  pass=$((pass + 1))
else
  echo "FAIL up.sh spec-review cell='$spec_cell'" >&2
  fail=$((fail + 1))
fi

echo "---"
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
