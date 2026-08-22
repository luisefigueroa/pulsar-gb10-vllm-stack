#!/usr/bin/env python3
"""AUD-03: active current-state docs must match the live catalog and fail-closed home views."""

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
    skills = REPO_ROOT / "skills"
    if not skills.is_dir():
        return []
    return sorted(path for path in skills.rglob("*.md") if path.is_file())


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

    def test_active_docs_do_not_repeat_aud03_false_current_claims(self) -> None:
        spec = REPO_ROOT / "docs" / "MODEL_CATALOG_DISTRIBUTION_LOADING_SPEC.md"
        self.assertFalse(
            spec.exists(),
            "stale current-system spec must not remain an active document",
        )
        files = list(ACTIVE_CURRENT_STATE) + _skill_markdown()
        for path in files:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8").lower()
                for claim in FORBIDDEN_CLAIMS:
                    self.assertNotIn(claim, text)


if __name__ == "__main__":
    unittest.main()
