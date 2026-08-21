#!/usr/bin/env python3
"""Contracts for activating a catalog home owned by a remote rank."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class RemoteHomeActivationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.revision = "abc123def456"
        self.models_dir = self.root / "models"
        self.models_dir.mkdir()
        (self.models_dir / "qwen3-1.7b-2node.conf").write_text(
            'MODEL="Qwen/Qwen3-1.7B"\nSTATUS="tested"\nNODES=2\n',
            encoding="utf-8",
        )
        self.actual_hub = self.root / "rank-1-home"
        snapshot = self.actual_hub / "snapshots" / self.revision
        snapshot.mkdir(parents=True)
        (self.actual_hub / "refs").mkdir()
        (self.actual_hub / "refs" / "main").write_text(
            self.revision + "\n", encoding="utf-8"
        )
        (snapshot / "config.json").write_text(
            '{"architectures":["Fixture"]}\n', encoding="utf-8"
        )
        (snapshot / "model.safetensors").write_bytes(b"fixture-weights")

        self.remote_hub = (
            "/srv/remote-homes/hub/models--Qwen--Qwen3-1.7B"
        )
        self.inventory = model_library.inspect_hub_inventory(
            self.actual_hub,
            rank=1,
            node_id="node-b",
            model_id="Qwen/Qwen3-1.7B",
        )
        self.inventory["hub_path"] = self.remote_hub
        self.catalog_path = self.root / "catalog.json"
        catalog = {
            "schema_version": 2,
            "refreshed_at": "2026-08-10T00:00:00.000Z",
            "topology_id": "topology-test",
            "models": [
                {
                    "model_id": "Qwen/Qwen3-1.7B",
                    "revision": self.revision,
                    "identity_key": (
                        f"Qwen/Qwen3-1.7B@{self.revision}"
                    ),
                    "validation": "legacy-unsealed",
                    "profiles": ["qwen3-1.7b-2node"],
                    "profile_validation": [
                        {
                            "profile": "qwen3-1.7b-2node",
                            "profile_status": "tested",
                            "identity_status": "legacy-unsealed",
                            "expected_model_seal_ref": None,
                            "expected_model_seal": None,
                        }
                    ],
                    "homes": [
                        {
                            "rank": 1,
                            "node_id": "node-b",
                            "hostname": "rank-1",
                            "ssh_host": "rank-1.test",
                            "cache_root": "/srv/remote-homes",
                            "hub_path": self.remote_hub,
                            "state": "complete",
                            "bytes": self.inventory["bytes_logical"],
                            "primary": True,
                        }
                    ],
                    "duplicate": False,
                    "has_primary": True,
                    "on_disk": True,
                }
            ],
        }
        self.catalog_path.write_text(
            json.dumps(catalog), encoding="utf-8"
        )

    def plan(self, inventory: dict[str, object]) -> dict[str, object]:
        return model_library.plan_activate(
            catalog_path=str(self.catalog_path),
            profile="qwen3-1.7b-2node",
            topology_id="topology-test",
            hot_root=str(self.root / "hot"),
            models_dir=str(self.models_dir),
            backend="copy",
            allow_unvalidated=True,
            nodes=2,
            home_inventory=inventory,
        )

    def test_bound_remote_inventory_avoids_controller_path_probe(self) -> None:
        self.assertFalse(pathlib.Path(self.remote_hub).exists())
        plan = self.plan(self.inventory)

        self.assertEqual(plan["action"], "copy")
        self.assertEqual(plan["transport"], "ssh-roce")
        self.assertEqual(plan["stamp"]["transport"], "ssh-roce")
        self.assertEqual(plan["home"]["rank"], 1)
        self.assertEqual(plan["hub_source"], self.remote_hub)
        self.assertEqual(
            plan["content_digest"],
            self.inventory["content_digest"],
        )
        self.assertEqual(
            plan["bytes_logical"],
            self.inventory["bytes_logical"],
        )

    def test_inventory_identity_and_revision_mismatch_fail_closed(self) -> None:
        bad_node = dict(self.inventory)
        bad_node["node_id"] = "node-a"
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "node_id differs",
        ):
            self.plan(bad_node)

        bad_revision = dict(self.inventory)
        bad_revision["revision"] = "different"
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "revision differs",
        ):
            self.plan(bad_revision)


if __name__ == "__main__":
    unittest.main()
