#!/usr/bin/env python3
"""Stubbed contracts for public Hugging Face inventory resolution."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import hf_source_inventory  # noqa: E402


class FakeRepoFile:
    def __init__(self, path: str, size: int, blob_id: str) -> None:
        self.path = path
        self.size = size
        self.blob_id = blob_id
        self.lfs = None


class FakeInfo:
    def __init__(self, **fields: object) -> None:
        for key, value in fields.items():
            setattr(self, key, value)


class FakeApi:
    def __init__(self, info: FakeInfo, tree: list[FakeRepoFile]) -> None:
        self.info = info
        self.tree = tree
        self.expand: list[str] | None = None
        self.listed = False

    def model_info(self, model_id: str, revision: str, expand: list[str]) -> FakeInfo:
        self.expand = list(expand)
        return self.info

    def list_repo_tree(self, *args, **kwargs) -> list[FakeRepoFile]:
        self.listed = True
        return self.tree


class HfSourceInventoryPrivacyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tree = [FakeRepoFile("config.json", 8, "b" * 40)]
        self.hub = types.ModuleType("huggingface_hub")
        self.api_mod = types.ModuleType("huggingface_hub.hf_api")
        self.api_mod.RepoFile = FakeRepoFile

    def _fetch(self, api: FakeApi) -> dict:
        self.hub.HfApi = lambda: api
        with mock.patch.dict(
            sys.modules,
            {
                "huggingface_hub": self.hub,
                "huggingface_hub.hf_api": self.api_mod,
            },
        ):
            return hf_source_inventory.fetch_inventory("Org/Public-Model", "main")

    def test_public_repository_succeeds(self) -> None:
        api = FakeApi(
            FakeInfo(id="Org/Public-Model", sha="a" * 40, private=False),
            self.tree,
        )
        result = self._fetch(api)
        self.assertEqual(result["sha"], "a" * 40)
        self.assertNotIn("private", result)
        self.assertTrue(api.listed)
        self.assertEqual(api.expand, ["sha", "private"])

    def test_public_gated_repository_succeeds(self) -> None:
        api = FakeApi(
            FakeInfo(
                id="Org/Gated-Model",
                sha="a" * 40,
                private=False,
                gated=True,
            ),
            self.tree,
        )
        result = self._fetch(api)
        self.assertEqual(result["sha"], "a" * 40)
        self.assertNotIn("private", result)
        self.assertNotIn("gated", result)
        self.assertTrue(api.listed)

    def test_private_true_fails_before_tree_listing(self) -> None:
        api = FakeApi(
            FakeInfo(id="Org/Private-Model", sha="a" * 40, private=True),
            self.tree,
        )
        with self.assertRaisesRegex(ValueError, "not public"):
            self._fetch(api)
        self.assertFalse(api.listed)

    def test_missing_or_none_private_fails_before_tree_listing(self) -> None:
        for info in (
            FakeInfo(id="Org/Public-Model", sha="a" * 40),
            FakeInfo(id="Org/Public-Model", sha="a" * 40, private=None),
        ):
            with self.subTest(info=info):
                api = FakeApi(info, self.tree)
                with self.assertRaisesRegex(ValueError, "not public"):
                    self._fetch(api)
                self.assertFalse(api.listed)


if __name__ == "__main__":
    unittest.main()
