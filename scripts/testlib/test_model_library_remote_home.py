#!/usr/bin/env python3
"""Contracts for activating a catalog home owned by a remote rank."""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.testlib.release_spec_fixture_set import write_fixture_set  # noqa: E402


class RemoteHomeActivationContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.revision = "a" * 40
        # A profile is a released spec id (ADR 0017 Stage 4): the diagnostic
        # two-node fixture spec names Qwen/Qwen3-1.7B with NODES=2.
        ids = write_fixture_set(self.root / "spec-fixture")
        self.profile = ids["diagnostic_two_node"]["spec_id"]
        assert ids["diagnostic_two_node"]["model_id"] == "Qwen/Qwen3-1.7B"
        os.environ["PULSAR_RELEASES_ROOT"] = str(self.root / "spec-fixture" / "releases")
        self.addCleanup(os.environ.pop, "PULSAR_RELEASES_ROOT", None)
        self.models_dir = self.root / "models"
        self.models_dir.mkdir()
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
                    "validation": "receipt-occupancy",
                    "profiles": [self.profile],
                    "profile_validation": [
                        {
                            "profile": self.profile,
                            "profile_status": "tested",
                            "identity_status": "receipt-occupancy",
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
                            "home_class": "occupancy",
                            "occupancy": True,
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
        return model_library.plan_prepare(
            catalog_path=str(self.catalog_path),
            profile=self.profile,
            topology_id="topology-test",
            hot_root=str(self.root / "hot"),
            models_dir=str(self.models_dir),
            backend="copy",
            allow_unvalidated=True,
            nodes=2,
            home_inventory=inventory,
            require_exact_revision=self.revision,
            expected_integrity_manifest=inventory["integrity_manifest"],
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

    def test_multi_rank_home_outside_serving_ranks_fails_before_inventory(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        home = catalog["models"][0]["homes"][0]
        home["rank"] = 2
        home["node_id"] = "node-c"
        home["hostname"] = "rank-2"
        home["ssh_host"] = "rank-2.test"
        home["primary"] = True
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            rf"durable home rank 2 is outside {re.escape(self.profile)} serving ranks "
            rf"\(0 1\).*home relocate {re.escape(self.profile)} --node 0 --yes.*"
            r"catalog refresh",
        ):
            self.plan({"integrity_manifest": {}})


if __name__ == "__main__":
    unittest.main()
