#!/usr/bin/env python3
"""plan-prepare identity path and spec/receipt/home manifest compare (WP1.4d)."""

from __future__ import annotations

import json
import os
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
        self.assertNotIn("reuse_candidates", plan)
        self.assertNotIn("copy_ranks", plan)
        self.assertEqual(plan["profile"], SPEC_ID)
        self.assertEqual(plan["stamp"]["profile"], SPEC_ID)
        self.assertEqual(
            plan["identity_key"], f"{self.model_id}@{fixture.COMMIT}"
        )
        self.assertIn(SPEC_ID, plan["instance_dir"])

    def test_identity_classification_names_the_exact_commit(self) -> None:
        empty = self.root / "empty-catalog.json"
        model_library.atomic_write_json(
            empty,
            {
                "schema_version": 2,
                "generated_at": "2026-09-02T00:00:00.000Z",
                "topology_id": TOPOLOGY_ID,
                "models": [],
                "primary_selections": [],
            },
        )
        gap = model_library.classify_library_readiness(
            profile=SPEC_ID,
            catalog_path=str(empty),
            topology_id=TOPOLOGY_ID,
            models_dir=None,
            identity_key=f"{self.model_id}@{fixture.COMMIT}",
        )
        self.assertEqual(gap["reason"], "no-home")
        self.assertIn(f"--revision {fixture.COMMIT}", gap["remediation"])
        self.assertNotIn("<selector>", gap["remediation"])
        self.assertNotIn("<exact-commit>", gap["remediation"])
        self.assertNotIn("--node", gap["remediation"])
        placed = model_library.classify_library_readiness(
            profile=SPEC_ID,
            catalog_path=str(empty),
            topology_id=TOPOLOGY_ID,
            models_dir=None,
            identity_key=f"{self.model_id}@{fixture.COMMIT}",
            selected_rank=1,
            selected_node_id="node-1",
        )
        # Both acquisition steps carry the selected placement.
        self.assertEqual(placed["remediation"].count("--node node-1"), 2)
        # A one-rank spec placed by its overlay on a rank other than its
        # durable home: the remediation names the two real choices, never a
        # re-check on the home rank that the next start would undo.
        mismatch = model_library.classify_library_readiness(
            profile=SPEC_ID,
            catalog_path=str(self.catalog_path),
            topology_id=TOPOLOGY_ID,
            models_dir=None,
            identity_key=f"{self.model_id}@{fixture.COMMIT}",
            selected_rank=1,
            selected_node_id="node-1",
        )
        self.assertEqual(mismatch["reason"], "wrong-placement")
        self.assertIn(
            f"home relocate {self.model_id}@{fixture.COMMIT} --profile {SPEC_ID} --node node-1 --yes",
            mismatch["remediation"],
        )
        self.assertIn("catalog refresh", mismatch["remediation"])
        self.assertIn("placement.node_id to node-0", mismatch["remediation"])
        self.assertNotIn("check-weights", mismatch["remediation"])

    def test_controller_stamp_never_skips_a_remote_placement(self) -> None:
        # Prepared on the controller once; the exact spec-named instance has
        # a matching stamp there.
        plan = model_library.plan_prepare(**self._identity_kwargs())
        instance = pathlib.Path(plan["instance_dir"])
        instance.mkdir(parents=True)
        model_library.write_hot_stamp(instance, plan["stamp"])
        self.assertEqual(model_library.plan_prepare(**self._identity_kwargs())["action"], "skip")
        # Occupancy moved to rank 1: the placement excludes the controller,
        # so the controller-local stamp is a stale leftover, not a skip.
        catalog = model_library.load_json(self.catalog_path)
        home = catalog["models"][0]["homes"][0]
        home["rank"] = 1
        home["node_id"] = "node-1"
        catalog["models"][0]["primary_selection"]["node_id"] = "node-1"
        remote_catalog = self.root / "catalog-rank1.json"
        model_library.atomic_write_json(remote_catalog, catalog)
        remote_inventory = dict(self.home_inventory, rank=1, node_id="node-1")
        moved = model_library.plan_prepare(
            **self._identity_kwargs(
                catalog_path=str(remote_catalog),
                home_inventory=remote_inventory,
                target_rank=1,
            )
        )
        self.assertEqual(moved["action"], "copy")
        self.assertEqual(moved["target_ranks"], [1])
        self.assertEqual(moved["instance_dir"], str(instance))

    def test_cleanup_lookup_tells_absence_from_uninspectable_or_foreign_entries(self) -> None:
        plan = model_library.plan_prepare(**self._identity_kwargs())
        instance = pathlib.Path(plan["instance_dir"])
        parent = instance.parent
        # Missing entry: absence in both modes.
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(self.hot, SPEC_ID, TOPOLOGY_ID)
        )
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(
                self.hot, SPEC_ID, TOPOLOGY_ID, include_incomplete=True
            )
        )
        # A stamp under the spec's directory that names another owner is
        # not this spec's view: launch skips it, cleanup refuses.
        instance.mkdir(parents=True)
        foreign = dict(plan["stamp"])
        foreign["profile"] = "someone-else"
        model_library.write_hot_stamp(instance, foreign)
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(self.hot, SPEC_ID, TOPOLOGY_ID)
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "names owner 'someone-else'"):
            model_library.find_hot_instance_for_profile(
                self.hot, SPEC_ID, TOPOLOGY_ID, include_incomplete=True
            )
        # A stamp of another schema is not inspectable by this tool: launch
        # sees no view, cleanup refuses rather than reading absence.
        unsupported = dict(plan["stamp"])
        unsupported["schema_version"] = 99
        model_library.write_hot_stamp(instance, unsupported)
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(self.hot, SPEC_ID, TOPOLOGY_ID)
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "unsupported hot stamp schema"):
            model_library.find_hot_instance_for_profile(
                self.hot, SPEC_ID, TOPOLOGY_ID, include_incomplete=True
            )
        model_library.write_hot_stamp(instance, plan["stamp"])
        self.assertEqual(
            model_library.find_hot_instance_for_profile(
                self.hot, SPEC_ID, TOPOLOGY_ID, include_incomplete=True
            ),
            instance,
        )
        # An entry that exists but cannot be listed is an inspection failure
        # for cleanup and no view for launch (not meaningful as root).
        if os.geteuid() != 0:
            parent.chmod(0)
            try:
                self.assertIsNone(
                    model_library.find_hot_instance_for_profile(self.hot, SPEC_ID, TOPOLOGY_ID)
                )
                with self.assertRaisesRegex(model_library.ModelLibraryError, "cannot list hot entry"):
                    model_library.find_hot_instance_for_profile(
                        self.hot, SPEC_ID, TOPOLOGY_ID, include_incomplete=True
                    )
            finally:
                parent.chmod(0o755)

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
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(self.hot, "linked", TOPOLOGY_ID)
        )
        # Cleanup never reads a symlinked entry as absence.
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlinked hot entry"):
            model_library.find_hot_instance_for_profile(
                self.hot, "linked", TOPOLOGY_ID, include_incomplete=True
            )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlink"):
            model_library.purge_hot_instance(link_parent / conf_plan["content_id"])
        self.assertTrue(real_instance.is_dir())
        link_parent.unlink()
        real_parent = self.hot / f"real-{TOPOLOGY_ID[:12]}"
        real_parent.mkdir()
        (real_parent / conf_plan["content_id"]).symlink_to(real_instance, target_is_directory=True)
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(self.hot, "real", TOPOLOGY_ID)
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlinked hot instance"):
            model_library.find_hot_instance_for_profile(
                self.hot, "real", TOPOLOGY_ID, include_incomplete=True
            )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlink"):
            model_library.purge_hot_instance(real_parent / conf_plan["content_id"])
        self.assertTrue(real_instance.is_dir())

    def test_purge_requires_canonical_containment_and_a_truthful_stamp(self) -> None:
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
        # A hot root that is itself a symlink is the operator's configured
        # root: an instance inside its real target is contained.
        linked_root = self.root / "hot-link"
        linked_root.symlink_to(self.hot, target_is_directory=True)
        model_library.require_hot_instance_within_root(instance, linked_root)
        # An instance outside the root, or one level deeper, is refused.
        outside = self.root / "elsewhere" / "x-topo" / conf_plan["content_id"]
        outside.mkdir(parents=True)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "not inside the hot root"):
            model_library.purge_hot_instance(outside, hot_root=self.hot)
        self.assertTrue(outside.is_dir())
        deeper = instance / "nested"
        deeper.mkdir()
        with self.assertRaisesRegex(model_library.ModelLibraryError, "not a <name>-<topology>"):
            model_library.purge_hot_instance(deeper, hot_root=self.hot)
        # A stamp whose content_id disagrees with the directory name is
        # damaged and must not be trusted, so the purge is refused.
        damaged = dict(conf_plan["stamp"])
        damaged["content_id"] = "e" * 12
        model_library.write_hot_stamp(instance, damaged)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "differs from the instance name"):
            model_library.purge_hot_instance(instance, hot_root=self.hot)
        self.assertTrue(instance.is_dir())
        model_library.write_hot_stamp(instance, conf_plan["stamp"])
        model_library.purge_hot_instance(instance, hot_root=self.hot)
        self.assertFalse(instance.exists())

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
