#!/usr/bin/env python3
"""plan-prepare identity path and spec/receipt/home manifest compare (WP1.4d)."""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402


SPEC_ID = "c" * 64
TOPOLOGY_ID = "d" * 64


class SpecPreparePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.model_id = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
        self.profile = "nemotron-3-nano-30b-nvfp4"
        self.hot = self.root / "hot"
        self.models_dir = self.root / "models"
        self.models_dir.mkdir()
        (self.models_dir / f"{self.profile}.conf").write_text(
            f'MODEL="{self.model_id}"\nSTATUS="untested"\nNODES=1\n',
            encoding="utf-8",
        )
        self.hub = self.root / "hub"
        fixture.write_snapshot_hub(self.hub)
        inventory = model_library.inspect_snapshot_blob_identities(
            self.hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        self.manifest = inventory["manifest"]
        self.home_inventory = {
            "schema_version": 2,
            "kind": "model-library-home-inventory",
            "rank": 0,
            "node_id": "node-0",
            "hub_path": str(self.hub),
            "model_id": self.model_id,
            "state": "complete",
            "revision": fixture.COMMIT,
            "content_digest": self.manifest["manifest_id"],
            "bytes_logical": self.manifest["total_bytes"],
            "integrity_manifest": self.manifest,
        }
        self.catalog_path = self.root / "catalog.json"
        catalog = {
            "schema_version": 2,
            "generated_at": "2026-09-02T00:00:00.000Z",
            "topology_id": TOPOLOGY_ID,
            "models": [
                {
                    "model_id": self.model_id,
                    "revision": fixture.COMMIT,
                    "identity_key": f"{self.model_id}@{fixture.COMMIT}",
                    "validation": "unvalidated",
                    "profiles": [self.profile],
                    "profile_validation": [],
                    "homes": [
                        {
                            "rank": 0,
                            "node_id": "node-0",
                            "hostname": "fixture-0",
                            "ssh_host": "local",
                            "cache_root": str(self.root / "cache-0"),
                            "hub_path": str(self.hub),
                            "state": "complete",
                            "home_class": "occupancy",
                            "occupancy": True,
                            "bytes": self.manifest["total_bytes"],
                            "primary": True,
                        }
                    ],
                    "duplicate": False,
                    "has_primary": True,
                    "primary_selection": {
                        "status": "selected",
                        "node_id": "node-0",
                        "mode": "automatic-single-home",
                    },
                }
            ],
            "primary_selections": [],
        }
        model_library.atomic_write_json(self.catalog_path, catalog)

    def _identity_kwargs(self, **overrides):
        values = dict(
            catalog_path=str(self.catalog_path),
            profile=SPEC_ID,
            topology_id=TOPOLOGY_ID,
            hot_root=str(self.hot),
            models_dir=None,
            nodes=1,
            home_inventory=self.home_inventory,
            require_exact_revision=fixture.COMMIT,
            expected_integrity_manifest=self.manifest,
            identity_key=f"{self.model_id}@{fixture.COMMIT}",
            spec_manifest=self.manifest,
        )
        values.update(overrides)
        return values

    def test_identity_plan_stamps_spec_id(self) -> None:
        plan = model_library.plan_prepare(**self._identity_kwargs())
        self.assertEqual(plan["action"], "copy")
        self.assertEqual(plan["profile"], SPEC_ID)
        self.assertEqual(plan["stamp"]["profile"], SPEC_ID)
        self.assertEqual(
            plan["identity_key"], f"{self.model_id}@{fixture.COMMIT}"
        )
        self.assertIn(SPEC_ID, plan["instance_dir"])

    def test_identity_reuses_conf_named_ready_view(self) -> None:
        conf_plan = model_library.plan_prepare(
            catalog_path=str(self.catalog_path),
            profile=self.profile,
            topology_id=TOPOLOGY_ID,
            hot_root=str(self.hot),
            models_dir=self.models_dir,
            nodes=1,
            home_inventory=self.home_inventory,
            require_exact_revision=fixture.COMMIT,
            expected_integrity_manifest=self.manifest,
        )
        instance = pathlib.Path(conf_plan["instance_dir"])
        instance.mkdir(parents=True)
        model_library.write_hot_stamp(instance, conf_plan["stamp"])
        plan = model_library.plan_prepare(**self._identity_kwargs())
        self.assertEqual(plan["action"], "skip")
        self.assertIn("reuse", plan["reason"])
        self.assertEqual(plan["instance_dir"], str(instance))
        self.assertEqual(plan["stamp"]["profile"], self.profile)
        self.assertNotIn(SPEC_ID, plan["instance_dir"])

    def test_identity_reuses_candidate_from_the_selected_rank(self) -> None:
        # A one-node spec placed on a remote home rank: the wrapper discovers
        # the candidate there and hands it in; the controller hot root is
        # not scanned when rank 0 is not a target.
        conf_plan = model_library.plan_prepare(
            catalog_path=str(self.catalog_path),
            profile=self.profile,
            topology_id=TOPOLOGY_ID,
            hot_root=str(self.hot),
            models_dir=self.models_dir,
            nodes=1,
            home_inventory=self.home_inventory,
            require_exact_revision=fixture.COMMIT,
            expected_integrity_manifest=self.manifest,
        )
        remote_dir = "/remote/hot/nemotron-3-nano-30b-nvfp4-topo/" + conf_plan["content_id"]
        plan = model_library.plan_prepare(
            **self._identity_kwargs(),
            reuse_instance_dir=remote_dir,
            reuse_stamp=conf_plan["stamp"],
        )
        self.assertEqual(plan["action"], "skip")
        self.assertIn("selected rank", plan["reason"])
        self.assertEqual(plan["instance_dir"], remote_dir)
        foreign = dict(conf_plan["stamp"])
        foreign["integrity"] = {
            "scheme": foreign["integrity"]["scheme"],
            "manifest": {**foreign["integrity"]["manifest"], "manifest_id": "f" * 64},
        }
        copied = model_library.plan_prepare(
            **self._identity_kwargs(),
            reuse_instance_dir=remote_dir,
            reuse_stamp=foreign,
        )
        self.assertNotEqual(copied["action"], "skip")

    def test_symlinked_hot_entries_are_never_discovered_or_purged(self) -> None:
        conf_plan = model_library.plan_prepare(
            catalog_path=str(self.catalog_path),
            profile=self.profile,
            topology_id=TOPOLOGY_ID,
            hot_root=str(self.hot),
            models_dir=self.models_dir,
            nodes=1,
            home_inventory=self.home_inventory,
            require_exact_revision=fixture.COMMIT,
            expected_integrity_manifest=self.manifest,
        )
        outside = self.root / "outside"
        real_instance = outside / "victim" / conf_plan["content_id"]
        real_instance.mkdir(parents=True)
        model_library.write_hot_stamp(real_instance, conf_plan["stamp"])
        self.hot.mkdir(parents=True, exist_ok=True)
        link_parent = self.hot / f"linked-{TOPOLOGY_ID[:12]}"
        link_parent.symlink_to(outside / "victim", target_is_directory=True)
        identity_key = conf_plan["identity_key"]
        self.assertIsNone(
            model_library.find_hot_instance_for_identity(
                self.hot, identity_key, TOPOLOGY_ID
            )
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlink"):
            model_library.purge_hot_instance(link_parent / conf_plan["content_id"])
        self.assertTrue(real_instance.is_dir())
        link_parent.unlink()
        real_parent = self.hot / f"real-{TOPOLOGY_ID[:12]}"
        real_parent.mkdir()
        (real_parent / conf_plan["content_id"]).symlink_to(real_instance, target_is_directory=True)
        self.assertIsNone(
            model_library.find_hot_instance_for_identity(
                self.hot, identity_key, TOPOLOGY_ID
            )
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlink"):
            model_library.purge_hot_instance(real_parent / conf_plan["content_id"])
        self.assertTrue(real_instance.is_dir())

    def test_spec_receipt_mismatch_names_both_ids(self) -> None:
        other = json.loads(json.dumps(self.manifest))
        other["files"][0]["sha256"] = "f" * 64
        other["manifest_id"] = model_library.snapshot_manifest_id(other)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            r"spec manifest .* differs from receipt",
        ) as raised:
            model_library.plan_prepare(
                **self._identity_kwargs(spec_manifest=other)
            )
        text = str(raised.exception)
        self.assertIn(other["manifest_id"], text)
        self.assertIn(self.manifest["manifest_id"], text)
        self.assertIn("first differing path: config.json", text)

    def test_compare_helper_names_first_path(self) -> None:
        other = json.loads(json.dumps(self.manifest))
        other["files"][1]["sha256"] = "a" * 64
        other["manifest_id"] = model_library.snapshot_manifest_id(other)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "empty.txt",
        ):
            model_library.compare_spec_receipt_and_home_manifests(
                other, self.manifest, self.manifest
            )


if __name__ == "__main__":
    unittest.main()
