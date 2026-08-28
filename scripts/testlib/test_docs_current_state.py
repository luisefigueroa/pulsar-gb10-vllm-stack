#!/usr/bin/env python3
"""Current docs, profiles, evidence, and registry must describe one live state."""

from __future__ import annotations

import pathlib
import re
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

LIVE_PROFILE_ROW = re.compile(r"^\| `([a-z0-9][a-z0-9._-]*)`")
FORBIDDEN_CLAIMS = (
    "fall back to reflink",
    "current working tree contains ten profiles",
    "physical gate below remains pending",
    "guided replicated path",
    "scripts/model-library.sh cold stage-only <profile> --yes",
    "docs/model_catalog_distribution_loading_spec.md",
    "<sealed-profile>",
    "reviewed qwen3.8 lineage",
    "qwen3.8 lineage is the first reviewed",
    "docs/archive/schema-1-expected-seal/",
    "deepseek-v4-flash",
    "huggingface-cli",
    "weights in hf cache or models_nfs path",
)
FORBIDDEN_ACTIVE_PATTERNS = (
    re.compile(r"`qwen3-1[.]7b`"),
)
RESET_RECIPE_IDS = {
    "qwen3-1.7b-2node",
    "qwen3.8-27b-fp8",
    "qwen3.8-27b-fp8-2node",
}
RETIRED_EVIDENCE_PATTERNS = (
    re.compile(r"deepseek-v4-flash|dsv4", re.IGNORECASE),
    re.compile(r"qwen1[.]7b|qwen3-1[.]7b|qwen17", re.IGNORECASE),
    re.compile(r"qwen3[.]8", re.IGNORECASE),
)
ACTIVE_CURRENT_STATE = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "OPERATIONS.md",
    REPO_ROOT / "docs" / "REVALIDATE.md",
    REPO_ROOT / "docs" / "VALIDATION.md",
    REPO_ROOT / "docs" / "MODELS.md",
    REPO_ROOT / "docs" / "PREREQUISITES.md",
    REPO_ROOT / "docs" / "MULTINODE.md",
    REPO_ROOT / "docs" / "RECIPES.md",
    REPO_ROOT / "docs" / "MODEL_LIBRARY_DESIGN.md",
)


def _skill_markdown() -> list[pathlib.Path]:
    roots = (REPO_ROOT / "skills",)
    return sorted(
        path
        for root in roots
        if root.is_dir()
        for path in root.rglob("*.md")
        if path.is_file()
    )


def _active_markdown() -> list[pathlib.Path]:
    docs = [
        path
        for path in (REPO_ROOT / "docs").rglob("*.md")
        if "archive" not in path.relative_to(REPO_ROOT / "docs").parts
    ]
    return sorted(
        {
            REPO_ROOT / "README.md",
            REPO_ROOT / "AGENTS.md",
            REPO_ROOT / "models" / "model-serving-releases" / "README.md",
            *ACTIVE_CURRENT_STATE,
            *_skill_markdown(),
            *docs,
        }
    )


def _live_models_md_ids() -> set[str]:
    ids: set[str] = set()
    for line in (REPO_ROOT / "docs" / "MODELS.md").read_text(encoding="utf-8").splitlines():
        if "profile removed" in line:
            continue
        match = LIVE_PROFILE_ROW.match(line)
        if match:
            ids.add(match.group(1))
    return ids


def _conf_ids() -> set[str]:
    return {path.stem for path in (REPO_ROOT / "models").glob("*.conf")}


class CurrentStateDocsTests(unittest.TestCase):
    def test_single_node_onboarding_establishes_required_state(self) -> None:
        prerequisites = (REPO_ROOT / "docs" / "PREREQUISITES.md").read_text(
            encoding="utf-8"
        )
        single_node = prerequisites.split("## 2. ", 1)[1].split("## 3. ", 1)[0]
        required_steps = (
            "scripts/detect-fabric.sh --write-topology",
            "--revision <selector> --plan --json",
            "--revision <exact-commit-from-plan>",
            "scripts/model-library.sh catalog refresh",
            "scripts/model-library.sh prepare <profile> --yes",
            "scripts/up.sh <profile> --dry-run",
            "./pulsar start <profile>",
        )
        positions = [single_node.index(step) for step in required_steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("modern `hf`", single_node)
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("modern `hf`", readme)

    def test_models_md_live_rows_match_conf_files(self) -> None:
        self.assertTrue(_conf_ids(), "expected models/*.conf")
        self.assertEqual(_live_models_md_ids(), _conf_ids())

    def test_durable_home_materialize_fails_closed_without_reflink_fallback(self) -> None:
        source = (REPO_ROOT / "scripts" / "model-library-materialize.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("durable-home view requires an exact symlink", source)
        self.assertNotIn("reflink", source)

    def test_source_attested_gate14_is_recorded_as_bounded_physical_pass(self) -> None:
        ledger = (REPO_ROOT / "docs" / "VALIDATION.md").read_text(encoding="utf-8")
        self.assertIn("BOUNDED PHYSICAL GATE", ledger)
        self.assertIn("Gate 14", ledger)
        revalidate = (REPO_ROOT / "docs" / "REVALIDATE.md").read_text(encoding="utf-8")
        self.assertIn("Gate 14", revalidate)
        self.assertNotIn("physical gate below remains pending", revalidate)

    def test_reset_recipe_shells_are_unbound_and_untested(self) -> None:
        for profile_id in RESET_RECIPE_IDS:
            with self.subTest(profile=profile_id):
                text = (REPO_ROOT / "models" / f"{profile_id}.conf").read_text(
                    encoding="utf-8"
                )
                self.assertRegex(text, r'(?m)^STATUS="untested"$')
                self.assertNotRegex(text, r"(?m)^MODEL_SERVING_RELEASE_ID=")

    def test_reviewed_registry_is_empty_and_profiles_are_unbound(self) -> None:
        registry = REPO_ROOT / "models" / "model-serving-releases"
        self.assertFalse(
            list(registry.rglob("*.json")),
            "reset registry must contain no reviewed objects",
        )
        for profile in (REPO_ROOT / "models").glob("*.conf"):
            with self.subTest(profile=profile.name):
                self.assertNotRegex(
                    profile.read_text(encoding="utf-8"),
                    r"(?m)^MODEL_SERVING_RELEASE_ID=",
                )

    def test_retired_model_specific_evidence_is_not_retained(self) -> None:
        retired_archive = REPO_ROOT / "docs" / "archive" / "schema-1-expected-seal"
        self.assertFalse(retired_archive.exists())
        for path in (REPO_ROOT / "results").rglob("*"):
            if not path.is_file() or path.name == "README.md":
                continue
            relative = path.relative_to(REPO_ROOT).as_posix()
            with self.subTest(path=relative):
                for pattern in RETIRED_EVIDENCE_PATTERNS:
                    self.assertIsNone(pattern.search(relative))

    def test_active_docs_match_the_reset_current_state(self) -> None:
        spec = REPO_ROOT / "docs" / "MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md"
        self.assertFalse(
            spec.exists(),
            "stale current-system spec must not remain an active document",
        )
        for path in _active_markdown():
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8").lower()
                for claim in FORBIDDEN_CLAIMS:
                    self.assertNotIn(claim, text)
                for pattern in FORBIDDEN_ACTIVE_PATTERNS:
                    self.assertIsNone(pattern.search(text))


if __name__ == "__main__":
    unittest.main()
