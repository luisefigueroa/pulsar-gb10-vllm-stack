#!/usr/bin/env python3
"""Source-attested adapter, receipt, verify, and prepare contracts."""

from __future__ import annotations

import copy
import ctypes
import errno
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts import model_library_source_attested as source_attested  # noqa: E402
from scripts.testlib import model_library_source_attested_fixture as fixture  # noqa: E402


class SourceAttestedExecutionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.model_id = "Fixture/Unbound-Model"
        self.profile = "unbound-fixture"
        self.source = fixture.build_source(model_id=self.model_id)
        self.identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=self.source, profile=self.profile
        )
        self.library_dir = self.root / "library"

    def _observations(self, *, occupied: bool = False) -> list[dict[str, object]]:
        rows = []
        for rank in (0, 1):
            cache = self.root / f"cache-{rank}"
            cache.mkdir(exist_ok=True)
            rows.append(
                fixture.observation(
                    cache,
                    rank=rank,
                    node_id=f"node-{rank}",
                    model_id=self.model_id,
                    revision=self.source["snapshot_revision"],
                    content_bytes=self.source["content_bytes"],
                    available_bytes=20 * 1024**3 if rank == 1 else 8 * 1024**3,
                    hf_cli="hf",
                    target_state="occupied" if occupied and rank == 0 else None,
                )
            )
        return rows

    def _plan(self, **kwargs):
        observations = kwargs.pop("observations", None) or self._observations()
        return source_attested.plan_source_attested_acquisition(
            source=self.source,
            identity=self.identity,
            observations=observations,
            serving_nodes=kwargs.pop("serving_nodes", 1),
            topology_generation=kwargs.pop("topology_generation", "d" * 64),
            node_selector=kwargs.pop("node_selector", ""),
            metadata_resolved_ranks=kwargs.pop("metadata_resolved_ranks", None),
        )

    def _receipt(self, hub: pathlib.Path | None = None) -> dict[str, object]:
        plan, _handle = self._plan()
        if hub is None:
            hub = self.root / "hub"
            fixture.write_snapshot_hub(
                hub, revision=self.source["snapshot_revision"]
            )
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=self.source["snapshot_revision"],
            allow_empty_files=True,
        )
        source_attested.compare_observed_files_to_inventory(
            self.source["inventory"], observed["files"]
        )
        return source_attested.build_source_attested_acquisition_receipt(
            source=self.source,
            identity=self.identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )

    def test_adapter_binds_mutable_selector_to_exact_commit(self) -> None:
        source = source_attested.build_huggingface_v1_source_from_adapter(
            model_id=self.model_id,
            selector="main",
            repo_info=fixture.repo_info_payload(self.source),
            repo_tree=fixture.repo_tree_payload(self.source),
        )
        self.assertEqual(source["selector"], "main")
        self.assertEqual(source["snapshot_revision"], fixture.COMMIT)
        self.assertEqual(source["source_digest"], self.source["source_digest"])

    def test_adapter_rejects_all_zero_and_unsupported_forms(self) -> None:
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "all zeros"
        ):
            source_attested.normalize_huggingface_v1_inventory_entry(
                path="config.json",
                size=1,
                blob_kind=source_attested.HF_V1_BLOB_GIT,
                git_oid="0" * 40,
            )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "all zeros"
        ):
            source_attested.normalize_huggingface_v1_inventory_entry(
                path="model.safetensors",
                size=1,
                blob_kind=source_attested.HF_V1_BLOB_LFS,
                sha256="0" * 64,
            )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "unsupported Hugging Face source object form",
        ):
            source_attested.parse_huggingface_v1_inventory_payload(
                [{"type": "xet", "path": "weights.xet", "size": 8}]
            )

    def test_plan_is_privacy_safe_and_selects_most_free_space(self) -> None:
        plan, handle = self._plan()
        self.assertEqual(plan["approval"]["selected_rank"], 1)
        self.assertEqual(plan["approval"]["selection"], "most-free-space")
        blob = json.dumps(plan)
        for banned in (
            "node-0",
            "node-1",
            "fixture-",
            "192.0.2.",
            "cache-0",
            "topology_id",
            str(self.root),
        ):
            self.assertNotIn(banned, blob)
        self.assertIn("node-1", json.dumps(handle))

    def test_plan_rejects_mismatched_embedded_identity(self) -> None:
        plan, _handle = self._plan()
        tampered = copy.deepcopy(plan)
        tampered["identity"]["profile"] = "different-profile"
        tampered["plan_id"] = source_attested.source_attested_plan_id(tampered)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "approval profile differs from its identity",
        ):
            source_attested.validate_source_attested_acquisition_plan(tampered)

    def test_metadata_failure_does_not_force_another_rank(self) -> None:
        plan, _handle = self._plan(metadata_resolved_ranks=[1])
        self.assertEqual(plan["approval"]["selected_rank"], 1)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "no eligible"
        ):
            self._plan(metadata_resolved_ranks=[])
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "source metadata is unavailable",
        ):
            self._plan(node_selector="0", metadata_resolved_ranks=[1])

    def test_unique_source_requires_matching_content(self) -> None:
        first = self.source
        second = fixture.build_source(selector="v1")
        self.assertEqual(
            source_attested.unique_source_attested_source([first, second]),
            first,
        )
        other = fixture.build_source(
            inventory=[
                source_attested.normalize_huggingface_v1_inventory_entry(
                    path="other.bin",
                    size=4,
                    blob_kind=source_attested.HF_V1_BLOB_GIT,
                    git_oid="c" * 40,
                )
            ]
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "disagreeing"
        ):
            source_attested.unique_source_attested_source([first, other])

    def test_geometry_ranks_follow_profile_serving_nodes(self) -> None:
        self.assertEqual(
            source_attested.source_attested_geometry_ranks(
                [0, 1, 2], serving_nodes=1
            ),
            [0, 1, 2],
        )
        self.assertEqual(
            source_attested.source_attested_geometry_ranks(
                [0, 1, 2], serving_nodes=2
            ),
            [0, 1],
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "serving geometry"
        ):
            source_attested.source_attested_geometry_ranks(
                [0, 1], serving_nodes=3
            )

    def test_occupied_or_huggingface_cli_is_ineligible(self) -> None:
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "already exists"
        ):
            self._plan(observations=self._observations(occupied=True))
        rows = self._observations()
        rows[0]["hf_cli"] = "huggingface-cli"
        rows[1]["hf_cli"] = "huggingface-cli"
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "no eligible"
        ):
            self._plan(observations=rows)

    def test_topology_generation_change_rejects_approval(self) -> None:
        plan, _handle = self._plan(topology_generation="d" * 64)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "approval identity"
        ):
            source_attested.verify_source_attested_acquisition_approval(
                plan["approval"],
                source=self.source,
                identity=self.identity,
                topology_generation="e" * 64,
            )

    def test_receipt_identity_is_canonical_and_privacy_safe(self) -> None:
        first = self._receipt()
        second = self._receipt()
        self.assertEqual(first["receipt_id"], second["receipt_id"])
        blob = json.dumps(first)
        for banned in ("cache-0", "node-0", "192.0.2.", "topology_id", str(self.root)):
            self.assertNotIn(banned, blob)
        self.assertEqual(first["model_id"], self.model_id)
        self.assertEqual(first["snapshot_revision"], fixture.COMMIT)

    def test_receipt_written_before_home_and_orphan_retry(self) -> None:
        receipt = self._receipt()
        written = source_attested.write_source_attested_receipt(
            self.library_dir, receipt
        )
        self.assertEqual(written["receipt_id"], receipt["receipt_id"])
        store = source_attested.source_attested_receipt_store(self.library_dir)
        self.assertTrue((store / f"{receipt['receipt_id']}.json").is_file())
        self.assertEqual(
            source_attested.write_source_attested_receipt(self.library_dir, receipt),
            written,
        )
        self.assertEqual(store.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (store / f"{receipt['receipt_id']}.json").stat().st_mode & 0o777,
            0o600,
        )

    def test_receipt_rejects_open_manifest_and_malformed_store(self) -> None:
        receipt = self._receipt()
        tampered = copy.deepcopy(receipt)
        tampered["observed_manifest"]["local_path"] = "/private/model"
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "closed snapshot-manifest schema",
        ):
            source_attested.validate_source_attested_acquisition_receipt(tampered)

        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        store = source_attested.source_attested_receipt_store(self.library_dir)
        (store / "unexpected.tmp").write_text("partial", encoding="utf-8")
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "unexpected entry"
        ):
            source_attested.find_source_attested_receipt(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )

    def test_receipt_store_symlink_is_refused(self) -> None:
        library = self.root / "symlink-library"
        library.mkdir()
        outside = self.root / "outside-receipts"
        outside.mkdir()
        source_attested.source_attested_receipt_store(library).symlink_to(
            outside, target_is_directory=True
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "not a regular directory",
        ):
            source_attested.find_source_attested_receipt(
                library,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )

    def test_publication_does_not_replace_an_existing_home(self) -> None:
        hub_root = self.root / "publish-hub"
        owner_id = "d" * 64
        staging_result = model_library.create_owned_hub_staging(
            hub_root,
            owner_id=owner_id,
            rank=0,
            node_id="node-0",
        )
        staging = pathlib.Path(staging_result["staging_root"])
        staged_hub = staging / model_library.model_id_to_hub_dirname(self.model_id)
        staged_hub.mkdir()
        target = hub_root / model_library.model_id_to_hub_dirname(self.model_id)
        target.mkdir()
        marker = target / "existing"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "appeared before publication"
        ):
            model_library.publish_owned_hub_staging(
                staging,
                owner_id=owner_id,
                rank=0,
                node_id="node-0",
                model_id=self.model_id,
                target_hub=target,
                hub_root=hub_root,
            )
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertTrue(staged_hub.is_dir())

    def test_compatible_receipts_are_preserved_and_lookup_is_stable(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        other_source = fixture.build_source(selector="v1")
        other_identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=other_source, profile=self.profile
        )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=other_source,
            identity=other_identity,
            observations=self._observations(),
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        hub = self.root / "hub-other"
        fixture.write_snapshot_hub(hub)
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        other = source_attested.build_source_attested_acquisition_receipt(
            source=other_source,
            identity=other_identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )
        self.assertNotEqual(receipt["receipt_id"], other["receipt_id"])
        written = source_attested.write_source_attested_receipt(
            self.library_dir, other
        )
        self.assertEqual(written["receipt_id"], other["receipt_id"])
        store = source_attested.source_attested_receipt_store(self.library_dir)
        self.assertTrue((store / f"{receipt['receipt_id']}.json").is_file())
        self.assertTrue((store / f"{other['receipt_id']}.json").is_file())
        listed = source_attested.list_source_attested_receipts_for_revision(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
        )
        self.assertEqual(
            {item["receipt_id"] for item in listed},
            {receipt["receipt_id"], other["receipt_id"]},
        )
        tampered = copy.deepcopy(observed["manifest"])
        tampered["files"][0]["sha256"] = "f" * 64
        tampered["manifest_id"] = model_library.snapshot_manifest_id(tampered)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "rehash"
        ):
            source_attested.verify_source_attested_home(
                receipt,
                tampered,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )

    def test_incompatible_receipt_is_refused_and_existing_files_remain(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        other_source = fixture.build_source(
            selector="v1",
            inventory=[
                source_attested.normalize_huggingface_v1_inventory_entry(
                    path="config.json",
                    size=12,
                    blob_kind=source_attested.HF_V1_BLOB_GIT,
                    git_oid="c" * 40,
                ),
                source_attested.normalize_huggingface_v1_inventory_entry(
                    path="model.safetensors",
                    size=8,
                    blob_kind=source_attested.HF_V1_BLOB_LFS,
                    sha256="1" * 64,
                ),
            ],
        )
        other_identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=other_source, profile=self.profile
        )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=other_source,
            identity=other_identity,
            observations=self._observations(),
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        observed_manifest = {
            "schema_version": 1,
            "kind": "model-library-snapshot-manifest",
            "model_id": self.model_id,
            "snapshot_revision": fixture.COMMIT,
            "files": [
                {"path": "config.json", "size": 12, "sha256": "2" * 64},
                {"path": "model.safetensors", "size": 8, "sha256": "1" * 64},
            ],
            "file_count": 2,
            "total_bytes": 20,
        }
        observed_manifest["manifest_id"] = model_library.snapshot_manifest_id(
            observed_manifest
        )
        other = source_attested.build_source_attested_acquisition_receipt(
            source=other_source,
            identity=other_identity,
            approval=plan["approval"],
            observed_manifest=observed_manifest,
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "incompatible"
        ):
            source_attested.write_source_attested_receipt(self.library_dir, other)
        store = source_attested.source_attested_receipt_store(self.library_dir)
        self.assertTrue((store / f"{receipt['receipt_id']}.json").is_file())
        self.assertFalse((store / f"{other['receipt_id']}.json").exists())

    def test_missing_extra_and_tamper_detected(self) -> None:
        observed = [
            {
                "path": "config.json",
                "size": self.source["inventory"][0]["size"],
                "git_oid": self.source["inventory"][0]["git_oid"],
                "sha256": "1" * 64,
            }
        ]
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "missing"
        ):
            source_attested.compare_observed_files_to_inventory(
                self.source["inventory"], observed
            )
        extra = list(self.source["inventory"])
        extra_obs = [
            {
                "path": item["path"],
                "size": item["size"],
                "git_oid": item.get("git_oid"),
                "sha256": item.get("sha256"),
            }
            for item in extra
        ]
        extra_obs.append(
            {"path": "bonus.bin", "size": 1, "git_oid": "1" * 40, "sha256": "2" * 64}
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "extra"
        ):
            source_attested.compare_observed_files_to_inventory(
                self.source["inventory"], extra_obs
            )
        extra_obs.pop()
        extra_obs[0]["git_oid"] = "c" * 40
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "Git object ID"
        ):
            source_attested.compare_observed_files_to_inventory(
                self.source["inventory"], extra_obs
            )

    def test_offline_home_verify_matches_receipt_only(self) -> None:
        receipt = self._receipt()
        hub = self.root / "hub"
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        result = source_attested.verify_source_attested_home(
            receipt,
            observed["manifest"],
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
        )
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["receipt_id"], receipt["receipt_id"])
        (hub / "snapshots" / fixture.COMMIT / "config.json").write_text(
            "changed\n", encoding="utf-8"
        )
        changed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "rehash"
        ):
            source_attested.verify_source_attested_home(
                receipt,
                changed["manifest"],
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )

    def test_prepare_enforces_exact_receipt_revision(self) -> None:
        receipt = self._receipt()
        catalog_path = self.root / "catalog.json"
        models_dir = self.root / "models"
        models_dir.mkdir()
        (models_dir / f"{self.profile}.conf").write_text(
            f'MODEL="{self.model_id}"\nSTATUS="untested"\nNODES=1\n',
            encoding="utf-8",
        )
        hub = self.root / "hub"
        home = {
            "rank": 0,
            "node_id": "node-0",
            "hub_path": str(hub),
        }
        inventory = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        home_inventory = {
            "schema_version": 2,
            "kind": "model-library-home-inventory",
            "rank": 0,
            "node_id": "node-0",
            "hub_path": str(hub),
            "model_id": self.model_id,
            "state": "complete",
            "revision": fixture.COMMIT,
            "content_digest": inventory["manifest"]["manifest_id"],
            "bytes_logical": inventory["manifest"]["total_bytes"],
            "integrity_manifest": inventory["manifest"],
        }
        catalog = {
            "schema_version": 2,
            "generated_at": "2026-08-17T00:00:00.000Z",
            "topology_id": "d" * 64,
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
                            "hub_path": str(hub),
                            "state": "complete",
                            "bytes": inventory["manifest"]["total_bytes"],
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
        model_library.atomic_write_json(catalog_path, catalog)
        with self.assertRaisesRegex(Exception, "exact 40-hex commit"):
            model_library.plan_activate(
                catalog_path=str(catalog_path),
                profile=self.profile,
                topology_id="d" * 64,
                hot_root=str(self.root / "hot"),
                models_dir=models_dir,
                nodes=1,
                home_inventory=home_inventory,
                require_exact_revision="main",
                expected_integrity_manifest=receipt["observed_manifest"],
            )
        plan = model_library.plan_activate(
            catalog_path=str(catalog_path),
            profile=self.profile,
            topology_id="d" * 64,
            hot_root=str(self.root / "hot"),
            models_dir=models_dir,
            nodes=1,
            home_inventory=home_inventory,
            require_exact_revision=fixture.COMMIT,
            expected_integrity_manifest=receipt["observed_manifest"],
        )
        self.assertEqual(plan["revision"], fixture.COMMIT)
        self.assertEqual(
            plan["identity_key"], f"{self.model_id}@{fixture.COMMIT}"
        )

    def test_public_result_omits_private_fields(self) -> None:
        receipt = self._receipt()
        result = source_attested.build_source_attested_acquisition_result(
            receipt=receipt, state="published", staging_cleanup="removed"
        )
        self.assertFalse(result["catalog_refreshed"])
        blob = json.dumps(result)
        for banned in (
            "node_id",
            "cache_root",
            "topology_id",
            "device",
            "inode",
            "ctime_ns",
            "durable_home_path",
            "source-attested-home-attachments",
        ):
            self.assertNotIn(banned, blob)

    def test_prepare_without_receipt_manifest_stays_unsealed(self) -> None:
        catalog_path = self.root / "catalog-unsealed.json"
        models_dir = self.root / "models-unsealed"
        models_dir.mkdir()
        (models_dir / f"{self.profile}.conf").write_text(
            f'MODEL="{self.model_id}"\nSTATUS="untested"\nNODES=1\n',
            encoding="utf-8",
        )
        hub = self.root / "hub-unsealed"
        fixture.write_snapshot_hub(hub)
        inventory = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        home_inventory = {
            "schema_version": 2,
            "kind": "model-library-home-inventory",
            "rank": 0,
            "node_id": "node-0",
            "hub_path": str(hub),
            "model_id": self.model_id,
            "state": "complete",
            "revision": fixture.COMMIT,
            "content_digest": inventory["manifest"]["manifest_id"],
            "bytes_logical": inventory["manifest"]["total_bytes"],
            "integrity_manifest": inventory["manifest"],
        }
        catalog = {
            "schema_version": 2,
            "generated_at": "2026-08-17T00:00:00.000Z",
            "topology_id": "d" * 64,
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
                            "hub_path": str(hub),
                            "state": "complete",
                            "bytes": inventory["manifest"]["total_bytes"],
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
        model_library.atomic_write_json(catalog_path, catalog)
        plan = model_library.plan_activate(
            catalog_path=str(catalog_path),
            profile=self.profile,
            topology_id="d" * 64,
            hot_root=str(self.root / "hot-unsealed"),
            models_dir=models_dir,
            nodes=1,
            home_inventory=home_inventory,
        )
        self.assertEqual(plan["revision"], fixture.COMMIT)
        self.assertNotIn("receipt_id", json.dumps(plan))

    def test_hub_inventory_can_preserve_empty_files_for_receipt_prepare(self) -> None:
        hub = self.root / "empty-file-hub"
        fixture.write_snapshot_hub(hub)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "empty"):
            model_library.inspect_hub_inventory(
                hub,
                rank=0,
                node_id="node-0",
                model_id=self.model_id,
                revision=fixture.COMMIT,
            )
        inventory = model_library.inspect_hub_inventory(
            hub,
            rank=0,
            node_id="node-0",
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        self.assertEqual(inventory["integrity_manifest"]["file_count"], 3)


def _writer_temp_name(final_stem: str, *, pid: int = 9, token: str = "0123456789abcdef") -> str:
    return f".{final_stem}.json.{pid}.{token}.tmp"


class SourceAttestedHomeAttachmentContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.model_id = "Fixture/Unbound-Model"
        self.profile = "unbound-fixture"
        self.source = fixture.build_source(model_id=self.model_id)
        self.identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=self.source, profile=self.profile
        )
        self.library_dir = self.root / "library"
        self.node_id = "node-1"
        self.rank = 1

    def _observations(self) -> list[dict[str, object]]:
        rows = []
        for rank in (0, 1):
            cache = self.root / f"cache-{rank}"
            cache.mkdir(exist_ok=True)
            rows.append(
                fixture.observation(
                    cache,
                    rank=rank,
                    node_id=f"node-{rank}",
                    model_id=self.model_id,
                    revision=self.source["snapshot_revision"],
                    content_bytes=self.source["content_bytes"],
                    available_bytes=20 * 1024**3 if rank == 1 else 8 * 1024**3,
                    hf_cli="hf",
                )
            )
        return rows

    def _receipt(self, *, selector: str = "main") -> dict[str, object]:
        source = (
            self.source
            if selector == self.source["selector"]
            else fixture.build_source(model_id=self.model_id, selector=selector)
        )
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile=self.profile
        )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=source,
            identity=identity,
            observations=self._observations(),
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        hub = self.root / f"hub-{selector}"
        fixture.write_snapshot_hub(hub)
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        return source_attested.build_source_attested_acquisition_receipt(
            source=source,
            identity=identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )

    def _publish(self, *, owner_id: str, hub_root: pathlib.Path) -> dict[str, object]:
        staging_result = model_library.create_owned_hub_staging(
            hub_root,
            owner_id=owner_id,
            rank=self.rank,
            node_id=self.node_id,
        )
        staging = pathlib.Path(staging_result["staging_root"])
        staged = staging / model_library.model_id_to_hub_dirname(self.model_id)
        fixture.write_snapshot_hub(staged)
        target = hub_root / model_library.model_id_to_hub_dirname(self.model_id)
        return model_library.publish_owned_hub_staging(
            staging,
            owner_id=owner_id,
            rank=self.rank,
            node_id=self.node_id,
            model_id=self.model_id,
            target_hub=target,
            hub_root=hub_root,
        )

    def _attach(self, receipt: dict[str, object], published: dict[str, object]) -> dict[str, object]:
        return source_attested.attach_source_attested_home_from_publication(
            self.library_dir,
            receipt=receipt,
            node_id=self.node_id,
            publish_result=published,
        )

    def _resolve(
        self,
        published: dict[str, object],
        *,
        rank: int | None = None,
        node_id: str | None = None,
        path: str | None = None,
        live: dict[str, object] | None = None,
    ) -> dict[str, object]:
        identity = live or published["directory_identity"]
        return source_attested.resolve_attached_source_attested_receipt(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank if rank is None else rank,
            node_id=self.node_id if node_id is None else node_id,
            durable_home_path=published["target_hub"] if path is None else path,
            live_identity=identity,
        )

    def test_publish_attach_and_live_identity_selects_receipt(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="a" * 64, hub_root=self.root / "hub-root")
        attachment = self._attach(receipt, published)
        self.assertEqual(attachment["receipt_id"], receipt["receipt_id"])
        live = model_library.inspect_live_directory_identity(published["target_hub"])
        self.assertEqual(live["device"], published["directory_identity"]["device"])
        self.assertEqual(live["inode"], published["directory_identity"]["inode"])
        self.assertEqual(live["ctime_ns"], published["directory_identity"]["ctime_ns"])
        authority = self._resolve(published, live=live)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_ATTACHED)
        self.assertEqual(authority["receipt"]["receipt_id"], receipt["receipt_id"])
        result = source_attested.build_source_attested_acquisition_result(
            receipt=receipt, state="published", staging_cleanup="removed"
        )
        public = json.dumps(result) + json.dumps(receipt)
        for banned in (
            self.node_id,
            published["target_hub"],
            "ctime_ns",
            '"device"',
            '"inode"',
            str(live["inode"]),
        ):
            self.assertNotIn(banned, public)

    def test_publication_does_not_depend_on_post_rename_path_probe(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        hub_root = self.root / "post-rename-probe"
        target = hub_root / model_library.model_id_to_hub_dirname(self.model_id)
        real_inspect = model_library.inspect_live_directory_identity

        def reject_target(path: str | pathlib.Path) -> dict[str, object]:
            if pathlib.Path(path) == target:
                raise model_library.ModelLibraryError(
                    "simulated post-rename probe failure"
                )
            return real_inspect(path)

        with mock.patch(
            "scripts.model_library.inspect_live_directory_identity",
            side_effect=reject_target,
        ):
            published = self._publish(owner_id="7" * 64, hub_root=hub_root)
        self.assertTrue(target.is_dir())
        self.assertEqual(published["directory_identity"]["path"], str(target))
        self._attach(receipt, published)
        authority = self._resolve(published)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_ATTACHED)

    def test_orphan_receipt_and_matching_external_tree_have_no_authority(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        external = self.root / "external-home"
        fixture.write_snapshot_hub(external)
        live = model_library.inspect_live_directory_identity(external)
        authority = source_attested.resolve_attached_source_attested_receipt(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank,
            node_id=self.node_id,
            durable_home_path=str(external),
            live_identity=live,
        )
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_NONE)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_MISSING_ATTACHMENT
        )
        self.assertIsNone(
            source_attested.load_source_attested_home_attachment(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )
        )

    def test_same_path_new_inode_or_ctime_has_no_authority(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="b" * 64, hub_root=self.root / "ctime-root")
        self._attach(receipt, published)
        target = pathlib.Path(published["target_hub"])
        os.chmod(target, 0o700)
        live = model_library.inspect_live_directory_identity(target)
        authority = self._resolve(published, live=live)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_NONE)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_STALE_ATTACHMENT
        )

        shutil.rmtree(target)
        fixture.write_snapshot_hub(target)
        replaced = model_library.inspect_live_directory_identity(target)
        authority = self._resolve(published, live=replaced)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_NONE)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_STALE_ATTACHMENT
        )

    def test_path_node_and_identity_mismatches_have_no_authority(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="c" * 64, hub_root=self.root / "mismatch-root")
        self._attach(receipt, published)
        live = published["directory_identity"]
        other_path = str(self.root / "other-home")
        pathlib.Path(other_path).mkdir()
        cases = [
            {"node_id": "node-other"},
            {"path": other_path},
            {
                "live": {
                    **live,
                    "device": live["device"] + 1,
                }
            },
            {
                "live": {
                    **live,
                    "inode": live["inode"] + 1,
                }
            },
            {
                "live": {
                    **live,
                    "ctime_ns": live["ctime_ns"] + 1,
                }
            },
        ]
        for kwargs in cases:
            authority = self._resolve(published, **kwargs)
            self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_NONE)
            self.assertEqual(
                authority["reason"], source_attested.HOME_AUTHORITY_STALE_ATTACHMENT
            )

    def test_live_rank_need_not_match_receipt_download_rank(self) -> None:
        receipt = self._receipt()
        self.assertEqual(receipt["selected_rank"], self.rank)
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="c" * 64, hub_root=self.root / "move-rank")
        self._attach(receipt, published)
        authority = self._resolve(published, rank=0)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_ATTACHED)
        self.assertEqual(authority["receipt"]["selected_rank"], self.rank)

    def test_attachment_selects_receipt_not_lexicographic_minimum(self) -> None:
        first = self._receipt(selector="main")
        second = self._receipt(selector="v1")
        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        source_attested.write_source_attested_receipt(self.library_dir, first)
        source_attested.write_source_attested_receipt(self.library_dir, second)
        published = self._publish(owner_id="d" * 64, hub_root=self.root / "multi-root")
        chosen = max((first, second), key=lambda item: item["receipt_id"])
        self._attach(chosen, published)
        authority = self._resolve(published)
        self.assertEqual(authority["receipt"]["receipt_id"], chosen["receipt_id"])
        self.assertNotEqual(
            authority["receipt"]["receipt_id"],
            min(first["receipt_id"], second["receipt_id"]),
        )

    def test_missing_or_incompatible_named_receipt_has_no_authority(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="5" * 64, hub_root=self.root / "named-receipt")
        attachment = self._attach(receipt, published)
        store = source_attested.source_attested_receipt_store(self.library_dir)
        receipt_path = store / f"{receipt['receipt_id']}.json"
        receipt_path.unlink()
        authority = self._resolve(published)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_MISSING_RECEIPT
        )

        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        attachment_path = (
            source_attested.source_attested_home_attachment_store(self.library_dir)
            / f"{attachment['attachment_key']}.json"
        )
        tampered = json.loads(attachment_path.read_text(encoding="utf-8"))
        tampered["inventory_digest"] = "f" * 64
        attachment_path.write_text(json.dumps(tampered, indent=2) + "\n", encoding="utf-8")
        authority = self._resolve(published)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_INCOMPATIBLE_RECEIPT
        )

    def test_missing_attachment_never_auto_attaches(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="e" * 64, hub_root=self.root / "no-attach")
        authority = self._resolve(published)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_MISSING_ATTACHMENT
        )
        self.assertIsNone(
            source_attested.load_source_attested_home_attachment(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )
        )
        store = source_attested.source_attested_home_attachment_store(self.library_dir)
        self.assertFalse(store.exists() and any(store.iterdir()))

    def test_supported_remove_detaches_before_mutation_and_keeps_receipts(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="f" * 64, hub_root=self.root / "remove-root")
        self._attach(receipt, published)
        detached = source_attested.detach_source_attested_home_attachment(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank,
            node_id=self.node_id,
            durable_home_path=published["target_hub"],
        )
        self.assertEqual(detached["state"], "detached")
        self.assertTrue(pathlib.Path(published["target_hub"]).is_dir())
        authority = self._resolve(published)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_MISSING_ATTACHMENT
        )
        store = source_attested.source_attested_receipt_store(self.library_dir)
        self.assertTrue((store / f"{receipt['receipt_id']}.json").is_file())

        again = source_attested.detach_source_attested_home_attachment(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank,
            node_id=self.node_id,
            durable_home_path=published["target_hub"],
        )
        self.assertEqual(again["state"], "absent")

    def test_detach_unlinks_via_store_fd_and_fsyncs_directory(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="6" * 64, hub_root=self.root / "fsync-root")
        attachment = self._attach(receipt, published)
        opened: list[tuple[int, int | None, int]] = []
        unlinked: list[tuple[str, int | None]] = []
        synced: list[int] = []
        real_open = os.open
        real_unlink = os.unlink
        real_fsync = os.fsync

        def fake_open(path, flags, *args, dir_fd=None, **kwargs):
            fd = real_open(path, flags, *args, dir_fd=dir_fd, **kwargs)
            opened.append((flags, dir_fd, fd))
            return fd

        def fake_unlink(name, *, dir_fd=None):
            unlinked.append((name, dir_fd))
            return real_unlink(name, dir_fd=dir_fd)

        def fake_fsync(fd):
            synced.append(fd)
            return real_fsync(fd)

        with (
            mock.patch(
                "scripts.model_library_source_attested.os.open",
                side_effect=fake_open,
            ),
            mock.patch(
                "scripts.model_library_source_attested.os.unlink",
                side_effect=fake_unlink,
            ),
            mock.patch(
                "scripts.model_library_source_attested.os.fsync",
                side_effect=fake_fsync,
            ),
        ):
            detached = source_attested.detach_source_attested_home_attachment(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
                selected_rank=self.rank,
                node_id=self.node_id,
                durable_home_path=published["target_hub"],
            )
        self.assertEqual(detached["state"], "detached")
        store_fds = [
            fd
            for flags, dir_fd, fd in opened
            if dir_fd is None and flags & os.O_DIRECTORY
        ]
        self.assertEqual(len(store_fds), 1)
        self.assertIn((f"{attachment['attachment_key']}.json", store_fds[0]), unlinked)
        self.assertIn(store_fds[0], synced)
        self.assertFalse(
            (
                source_attested.source_attested_home_attachment_store(self.library_dir)
                / f"{attachment['attachment_key']}.json"
            ).exists()
        )

    def test_selected_rank_mismatch_is_incompatible_receipt(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="7" * 64, hub_root=self.root / "rank-mismatch")
        attachment = self._attach(receipt, published)
        self.assertEqual(receipt["selected_rank"], self.rank)
        attachment_path = (
            source_attested.source_attested_home_attachment_store(self.library_dir)
            / f"{attachment['attachment_key']}.json"
        )
        tampered = json.loads(attachment_path.read_text(encoding="utf-8"))
        tampered["selected_rank"] = 0
        attachment_path.write_text(
            json.dumps(tampered, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        authority = self._resolve(published)
        self.assertEqual(
            authority["reason"], source_attested.HOME_AUTHORITY_INCOMPATIBLE_RECEIPT
        )

    def test_occupy_after_rehash_moves_occupancy_without_download_rank(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        dest = self.root / "occupy-dest"
        fixture.write_snapshot_hub(dest)
        observed = model_library.inspect_snapshot_blob_identities(
            dest,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        live = model_library.inspect_live_directory_identity(dest)
        attachment = source_attested.occupy_source_attested_home(
            self.library_dir,
            receipt=receipt,
            observed_manifest=observed["manifest"],
            node_id="node-0",
            durable_home_path=str(dest),
            directory_identity=live,
        )
        self.assertEqual(attachment["selected_rank"], receipt["selected_rank"])
        self.assertEqual(attachment["node_id"], "node-0")
        authority = source_attested.resolve_attached_source_attested_receipt(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=0,
            node_id="node-0",
            durable_home_path=str(dest),
            live_identity=live,
        )
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_ATTACHED)
        self.assertEqual(authority["receipt"]["receipt_id"], receipt["receipt_id"])

    def test_occupy_refuses_manifest_mismatch(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        dest = self.root / "occupy-bad"
        fixture.write_snapshot_hub(dest)
        live = model_library.inspect_live_directory_identity(dest)
        observed = model_library.inspect_snapshot_blob_identities(
            dest,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        bad = dict(observed["manifest"])
        bad["manifest_id"] = "f" * 64
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "rehash differs from the receipt|observed manifest is invalid",
        ):
            source_attested.occupy_source_attested_home(
                self.library_dir,
                receipt=receipt,
                observed_manifest=bad,
                node_id="node-0",
                durable_home_path=str(dest),
                directory_identity=live,
            )
        self.assertIsNone(
            source_attested.load_source_attested_home_attachment(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )
        )

    def test_catalog_marks_extra_complete_tree_unbound(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="9" * 64, hub_root=self.root / "occ-root")
        self._attach(receipt, published)
        extra = self.root / "extra-complete"
        extra.mkdir()
        catalog = {
            "schema_version": 2,
            "models": [
                {
                    "model_id": self.model_id,
                    "revision": fixture.COMMIT,
                    "homes": [
                        {
                            "rank": 1,
                            "node_id": self.node_id,
                            "hub_path": published["target_hub"],
                            "state": "complete",
                        },
                        {
                            "rank": 0,
                            "node_id": "node-0",
                            "hub_path": str(extra),
                            "state": "complete",
                        },
                    ],
                }
            ],
        }
        source_attested.classify_catalog_occupancy(catalog, self.library_dir)
        homes = catalog["models"][0]["homes"]
        self.assertEqual(homes[0]["home_class"], "occupancy")
        self.assertTrue(homes[0]["occupancy"])
        self.assertEqual(homes[1]["home_class"], "unbound-complete")
        self.assertFalse(homes[1]["occupancy"])

    def test_noncanonical_paths_are_rejected(self) -> None:
        canonical = "/var/tmp/pulsar-canonical-home"
        self.assertEqual(
            source_attested._require_durable_home_path(canonical), canonical
        )
        for bad in (
            "/var/tmp/pulsar-canonical-home/.",
            "/var/tmp/pulsar-canonical-home/./nested",
            "/var/tmp/foo/../pulsar-canonical-home",
            "/var/tmp/pulsar-canonical-home/",
            "//var/tmp/pulsar-canonical-home",
            "relative/home",
            "/var/tmp/pulsar-canonical-home/..",
        ):
            with self.assertRaisesRegex(
                source_attested.SourceAttestedAcquisitionError,
                "durable-home path is invalid",
            ):
                source_attested._require_durable_home_path(bad)

        hub = self.root / "canonical-live"
        hub.mkdir()
        live = model_library.inspect_live_directory_identity(hub)
        self.assertEqual(live["path"], str(hub))
        for bad in (
            f"{hub}/.",
            f"{hub}/",
            f"{hub}/../{hub.name}",
        ):
            with self.assertRaisesRegex(
                model_library.ModelLibraryError, "canonical|absolute"
            ):
                model_library.inspect_live_directory_identity(bad)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "durable-home path is invalid",
        ):
            source_attested.validate_live_directory_identity(
                {**live, "path": f"{hub}/."}
            )

    def test_remove_failure_after_detach_leaves_unbound_home(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="1" * 64, hub_root=self.root / "fail-remove")
        self._attach(receipt, published)
        source_attested.detach_source_attested_home_attachment(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank,
            node_id=self.node_id,
            durable_home_path=published["target_hub"],
        )
        self.assertTrue(pathlib.Path(published["target_hub"]).is_dir())
        authority = self._resolve(published)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_NONE)

    def test_reacquisition_writes_new_attachment_and_keeps_old_receipts(self) -> None:
        first = self._receipt(selector="main")
        source_attested.write_source_attested_receipt(self.library_dir, first)
        first_pub = self._publish(owner_id="2" * 64, hub_root=self.root / "readd-a")
        self._attach(first, first_pub)
        source_attested.detach_source_attested_home_attachment(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
            selected_rank=self.rank,
            node_id=self.node_id,
            durable_home_path=first_pub["target_hub"],
        )
        second = self._receipt(selector="v1")
        source_attested.write_source_attested_receipt(self.library_dir, second)
        second_pub = self._publish(owner_id="3" * 64, hub_root=self.root / "readd-b")
        self._attach(second, second_pub)
        authority = self._resolve(second_pub)
        self.assertEqual(authority["receipt"]["receipt_id"], second["receipt_id"])
        store = source_attested.source_attested_receipt_store(self.library_dir)
        self.assertTrue((store / f"{first['receipt_id']}.json").is_file())
        self.assertTrue((store / f"{second['receipt_id']}.json").is_file())

    def test_regular_writer_temp_is_ignored_and_temp_only_store_is_empty(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        store = source_attested.source_attested_receipt_store(self.library_dir)
        temp_name = _writer_temp_name(receipt["receipt_id"])
        temp_path = store / temp_name
        temp_path.write_text("{not-a-receipt", encoding="utf-8")
        os.chmod(temp_path, 0o600)
        found = source_attested.find_source_attested_receipt(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
        )
        self.assertEqual(found["receipt_id"], receipt["receipt_id"])

        empty_dir = self.root / "temp-only-library"
        empty_store = source_attested.source_attested_receipt_store(empty_dir)
        empty_store.mkdir(parents=True)
        os.chmod(empty_store, 0o700)
        leftover = empty_store / _writer_temp_name("a" * 64)
        leftover.write_text("{also-not-a-receipt", encoding="utf-8")
        self.assertEqual(
            source_attested.list_source_attested_receipts_for_revision(
                empty_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            ),
            [],
        )
        written = source_attested.write_source_attested_receipt(empty_dir, receipt)
        self.assertEqual(written["receipt_id"], receipt["receipt_id"])
        self.assertTrue((empty_store / f"{receipt['receipt_id']}.json").is_file())
        self.assertTrue(leftover.is_file())

    def test_temp_shaped_symlink_directory_and_unrelated_entries_fail(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        store = source_attested.source_attested_receipt_store(self.library_dir)

        symlink_name = _writer_temp_name("b" * 64)
        (store / symlink_name).symlink_to(store / f"{receipt['receipt_id']}.json")
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "non-regular writer temp"
        ):
            source_attested.find_source_attested_receipt(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )
        (store / symlink_name).unlink()

        dir_name = _writer_temp_name("c" * 64)
        (store / dir_name).mkdir()
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "non-regular writer temp"
        ):
            source_attested.find_source_attested_receipt(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )
        (store / dir_name).rmdir()

        (store / "unexpected.tmp").write_text("nope", encoding="utf-8")
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "unexpected entry"
        ):
            source_attested.find_source_attested_receipt(
                self.library_dir,
                model_id=self.model_id,
                snapshot_revision=fixture.COMMIT,
            )

    def test_attachment_store_ignores_writer_temp_and_rejects_malformed(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        published = self._publish(owner_id="4" * 64, hub_root=self.root / "attach-temp")
        attachment = self._attach(receipt, published)
        store = source_attested.source_attested_home_attachment_store(self.library_dir)
        leftover = store / _writer_temp_name(attachment["attachment_key"])
        leftover.write_text("{not-an-attachment", encoding="utf-8")
        authority = self._resolve(published)
        self.assertEqual(authority["state"], source_attested.HOME_AUTHORITY_ATTACHED)

        (store / "unexpected.tmp").write_text("nope", encoding="utf-8")
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "unexpected entry"
        ):
            self._resolve(published)

    def test_live_directory_inspect_refuses_symlink_and_file(self) -> None:
        path = self.root / "not-dir"
        path.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(model_library.ModelLibraryError, "not a directory"):
            model_library.inspect_live_directory_identity(path)
        linked = self.root / "linked-dir"
        real = self.root / "real-dir"
        real.mkdir()
        linked.symlink_to(real)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "symlink"):
            model_library.inspect_live_directory_identity(linked)


class RenameDirectoryNoreplaceTests(unittest.TestCase):
    def test_second_open_failure_closes_source_fd(self) -> None:
        opened: list[pathlib.Path] = []
        closed: list[int] = []

        def fake_open(path, flags, *args, **kwargs):
            opened.append(pathlib.Path(path))
            if len(opened) == 1:
                return 11
            raise OSError(errno.EACCES, "denied")

        libc = mock.Mock()
        libc.renameat2 = mock.Mock()
        with (
            mock.patch("scripts.model_library.ctypes.util.find_library", return_value="libc.so.6"),
            mock.patch("scripts.model_library.ctypes.CDLL", return_value=libc),
            mock.patch("scripts.model_library.os.open", side_effect=fake_open),
            mock.patch("scripts.model_library.os.close", side_effect=closed.append),
        ):
            with self.assertRaises(OSError):
                model_library._rename_directory_noreplace(
                    pathlib.Path("/tmp/src/dir"),
                    pathlib.Path("/tmp/dst/dir"),
                )
        self.assertEqual(closed, [11])
        libc.renameat2.assert_not_called()

    def test_renameat2_signature_is_assigned_before_use(self) -> None:
        fds = iter((20, 21))
        libc = mock.Mock()
        renameat2 = mock.Mock(return_value=0)
        libc.renameat2 = renameat2
        with (
            mock.patch("scripts.model_library.ctypes.util.find_library", return_value="libc.so.6"),
            mock.patch("scripts.model_library.ctypes.CDLL", return_value=libc),
            mock.patch("scripts.model_library.os.open", side_effect=lambda *args, **kwargs: next(fds)),
            mock.patch("scripts.model_library.os.close"),
            mock.patch("scripts.model_library.os.fsync"),
        ):
            model_library._rename_directory_noreplace(
                pathlib.Path("/tmp/src/dir"),
                pathlib.Path("/tmp/dst/dir"),
            )
        self.assertEqual(
            renameat2.argtypes,
            [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ],
        )
        self.assertEqual(renameat2.restype, ctypes.c_int)
        renameat2.assert_called_once_with(
            20,
            b"dir",
            21,
            b"dir",
            model_library.RENAME_NOREPLACE,
        )


if __name__ == "__main__":
    unittest.main()
