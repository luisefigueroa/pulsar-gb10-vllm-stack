#!/usr/bin/env python3
"""Source-attested adapter, receipt, verify, and prepare contracts."""

from __future__ import annotations

import copy
import ctypes
import errno
import json
import pathlib
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
        found = source_attested.find_source_attested_receipt(
            self.library_dir,
            model_id=self.model_id,
            snapshot_revision=fixture.COMMIT,
        )
        self.assertIsNotNone(found)
        self.assertEqual(
            found["receipt_id"],
            min(receipt["receipt_id"], other["receipt_id"]),
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
        self.assertNotIn("node_id", blob)
        self.assertNotIn("cache_root", blob)
        self.assertNotIn("topology_id", blob)


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
