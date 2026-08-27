#!/usr/bin/env python3
"""Fail-closed integrity contracts for model-library hot schema v3."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class ModelLibraryIntegrityContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "fixture-2node"
        self.model_id = "Org/Fixture"
        self.revision = "revision-1"
        self.topology_id = "a" * 64
        self.instance = model_library.hot_instance_dir(
            self.root / "hot",
            self.profile,
            self.topology_id,
            "content",
        )
        self.hub = model_library.hot_hub_path(self.instance, self.model_id)
        blobs = self.hub / "blobs"
        snapshot = self.hub / "snapshots" / self.revision
        refs = self.hub / "refs"
        blobs.mkdir(parents=True)
        snapshot.mkdir(parents=True)
        refs.mkdir(parents=True)
        (refs / "main").write_text(self.revision + "\n", encoding="utf-8")

        self.weight_bytes = b"fixture-weights-for-integrity"
        self.weight_digest = hashlib.sha256(self.weight_bytes).hexdigest()
        self.weight_blob = blobs / self.weight_digest
        self.weight_blob.write_bytes(self.weight_bytes)
        (snapshot / "model.safetensors").symlink_to(
            pathlib.Path("../../blobs") / self.weight_digest
        )
        (snapshot / "config.json").write_text(
            '{"architectures":["Fixture"]}\n', encoding="utf-8"
        )

        self.manifest = model_library.build_snapshot_manifest(
            self.hub,
            model_id=self.model_id,
        )
        self.stamp = model_library.build_hot_stamp(
            profile=self.profile,
            model_id=self.model_id,
            identity_key=f"{self.model_id}@{self.revision}",
            revision=self.revision,
            topology_id=self.topology_id,
            home_node_id="node-a",
            content_id="content",
            content_digest=self.manifest["manifest_id"],
            integrity_manifest=self.manifest,
            validation={
                "identity_status": "receipt-occupancy",
                "expected_seal": None,
                "observed_seal": model_library.observed_model_seal_projection(
                    self.manifest
                ),
            },
            backend="copy",
            bytes_logical=self.manifest["total_bytes"],
            transport="ssh-roce",
        )
        model_library.write_hot_stamp(self.instance, self.stamp)

    def verify(self, **kwargs: object) -> dict[str, object]:
        return model_library.verify_hot_ready(
            self.instance,
            profile=self.profile,
            topology_id=self.topology_id,
            workers=2,
            **kwargs,
        )

    def test_full_manifest_verifies_and_is_discoverable(self) -> None:
        result = self.verify()
        integrity = result["integrity"]
        self.assertEqual(integrity["mode"], "full")
        self.assertEqual(integrity["manifest_id"], self.manifest["manifest_id"])
        self.assertEqual(integrity["bytes_hashed"], self.manifest["total_bytes"])
        self.assertEqual(
            model_library.find_hot_instance_for_profile(
                self.root / "hot",
                self.profile,
                self.topology_id,
            ),
            self.instance,
        )

    def test_retired_cold_stage_state_is_cleanup_only(self) -> None:
        retired = copy.deepcopy(self.stamp)
        retired.update(
            {
                "home_node_id": "cold",
                "tier": "cold",
                "mode": "stage-only",
                "source_path": "/protected/cold/source",
                "layout": "hub",
                "source_content_digest": self.manifest["manifest_id"],
            }
        )
        model_library.write_hot_stamp(self.instance, retired)

        stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            rc = model_library.main(
                [
                    "find-hot",
                    "--profile",
                    self.profile,
                    "--topology-id",
                    self.topology_id,
                    "--hot-root",
                    str(self.root / "hot"),
                    "--for-launch",
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("retired cold stage-only", stderr.getvalue())

        verify_stderr = io.StringIO()
        with (
            contextlib.redirect_stderr(verify_stderr),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            verify_rc = model_library.main(
                [
                    "verify-hot",
                    "--instance-dir",
                    str(self.instance),
                    "--profile",
                    self.profile,
                    "--topology-id",
                    self.topology_id,
                    "--for-launch",
                ]
            )
        self.assertEqual(verify_rc, 1)
        self.assertIn("retired cold stage-only", verify_stderr.getvalue())

        with contextlib.redirect_stdout(io.StringIO()):
            cleanup_rc = model_library.main(
                [
                    "find-hot",
                    "--profile",
                    self.profile,
                    "--topology-id",
                    self.topology_id,
                    "--hot-root",
                    str(self.root / "hot"),
                ]
            )
        self.assertEqual(cleanup_rc, 0)
        model_library.purge_hot_instance(self.instance, force_unpin=True)
        self.assertFalse(self.instance.exists())

    def test_same_size_byte_corruption_fails_full_verify(self) -> None:
        original = self.weight_blob.read_bytes()
        corrupted = bytes([original[0] ^ 1]) + original[1:]
        self.assertEqual(len(corrupted), len(original))
        self.weight_blob.write_bytes(corrupted)
        try:
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "SHA-256 mismatch",
            ):
                self.verify()
        finally:
            self.weight_blob.write_bytes(original)
        self.verify()

    def test_verifying_state_is_never_publicly_ready(self) -> None:
        provisional = dict(self.stamp)
        provisional["state"] = "verifying"
        model_library.write_hot_stamp(self.instance, provisional)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "not ready"):
            self.verify()
        self.verify(allow_verifying=True)
        self.assertIsNone(
            model_library.find_hot_instance_for_profile(
                self.root / "hot",
                self.profile,
                self.topology_id,
            )
        )

    def test_manifest_identity_and_file_set_fail_closed(self) -> None:
        altered = copy.deepcopy(self.manifest)
        checksum = altered["files"][0]["sha256"]
        altered["files"][0]["sha256"] = (
            ("0" if checksum[0] != "0" else "1") + checksum[1:]
        )
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "identity mismatch",
        ):
            model_library.validate_snapshot_manifest(altered)

        snapshot = self.hub / "snapshots" / self.revision
        extra = snapshot / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        try:
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "file set changed",
            ):
                self.verify()
        finally:
            extra.unlink()


if __name__ == "__main__":
    unittest.main()
