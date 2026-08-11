#!/usr/bin/env python3
"""Rank-local serve-witness contracts for the model-library hot path."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class ModelLibraryWitnessContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "witness-fixture"
        self.model_id = "Fixture/Witness"
        self.revision = "a" * 40
        self.topology_id = "b" * 64
        self.instance = model_library.hot_instance_dir(
            self.root / "hot",
            self.profile,
            self.topology_id,
            "content",
        )
        self.durable_hub = (
            self.root
            / "durable"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        self.snapshot = self.durable_hub / "snapshots" / self.revision
        self.snapshot.mkdir(parents=True)
        (self.durable_hub / "refs").mkdir()
        (self.durable_hub / "refs" / "main").write_text(
            self.revision + "\n",
            encoding="utf-8",
        )
        self.config = self.snapshot / "config.json"
        self.weights = self.snapshot / "model.safetensors"
        self.config.write_text(
            '{"architectures":["WitnessFixture"]}\n',
            encoding="utf-8",
        )
        self.weight_bytes = b"rank-local-witness-weights"
        self.weights.write_bytes(self.weight_bytes)

        self.hub = model_library.hot_hub_path(self.instance, self.model_id)
        self.hub.parent.mkdir(parents=True)
        self.hub.symlink_to(self.durable_hub, target_is_directory=True)
        self.manifest = model_library.build_snapshot_manifest(
            self.durable_hub,
            model_id=self.model_id,
            revision=self.revision,
        )
        self.validation = {
            "identity_status": "legacy-unsealed",
            "expected_seal": None,
            "observed_seal": model_library.observed_model_seal_projection(
                self.manifest
            ),
        }
        self.stamp = model_library.build_hot_stamp(
            profile=self.profile,
            model_id=self.model_id,
            identity_key=f"{self.model_id}@{self.revision}",
            revision=self.revision,
            topology_id=self.topology_id,
            home_node_id="home-node",
            content_id="content",
            content_digest=self.manifest["manifest_id"],
            integrity_manifest=self.manifest,
            validation=self.validation,
            backend="copy",
            bytes_logical=self.manifest["total_bytes"],
            transport="ssh-roce",
        )
        model_library.write_hot_stamp(self.instance, self.stamp)

    @property
    def witness_path(self) -> pathlib.Path:
        return model_library.hot_witness_path(self.instance)

    def refresh(self) -> dict[str, object]:
        return model_library.verify_hot_ready(
            self.instance,
            profile=self.profile,
            topology_id=self.topology_id,
            workers=2,
            refresh_witness=True,
        )

    def serve(self) -> dict[str, object]:
        return model_library.verify_hot_ready(
            self.instance,
            profile=self.profile,
            topology_id=self.topology_id,
            workers=2,
            serve_time_witness=True,
        )

    def test_activation_full_verify_creates_fast_serve_witness(self) -> None:
        refreshed = self.refresh()
        self.assertEqual(refreshed["integrity"]["mode"], "full")
        self.assertEqual(refreshed["witness"]["status"], "refreshed")
        witness = model_library.load_hot_witness(self.instance)
        self.assertEqual(witness["schema_version"], 1)
        self.assertEqual(
            witness["view"]["hub"]["canonical_path"],
            str(self.durable_hub.resolve()),
        )
        self.assertEqual(
            [item["path"] for item in witness["files"]],
            ["config.json", "model.safetensors"],
        )

        served = self.serve()
        self.assertEqual(served["integrity"]["mode"], "witness")
        self.assertEqual(served["integrity"]["bytes_hashed"], 0)
        self.assertEqual(served["witness"]["status"], "match")

    def test_missing_witness_visibly_full_verifies_and_refreshes(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            served = self.serve()
        self.assertIn("running full SHA-256 verification", stderr.getvalue())
        self.assertIn("serve witness refreshed", stderr.getvalue())
        self.assertEqual(served["integrity"]["mode"], "full")
        self.assertEqual(served["witness"]["status"], "refreshed")
        self.assertTrue(self.witness_path.is_file())
        self.assertEqual(self.serve()["integrity"]["mode"], "witness")

    def test_unchanged_content_with_metadata_drift_rehashes_and_refreshes(self) -> None:
        first = self.refresh()
        old_id = first["witness"]["witness_id"]
        metadata = self.weights.stat()
        os.utime(
            self.weights,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000),
        )
        served = self.serve()
        self.assertEqual(served["integrity"]["mode"], "full")
        self.assertEqual(served["witness"]["status"], "refreshed")
        self.assertNotEqual(served["witness"]["witness_id"], old_id)
        self.assertEqual(self.serve()["integrity"]["mode"], "witness")

    def test_same_size_corruption_fails_without_refreshing_witness(self) -> None:
        self.refresh()
        original_witness = self.witness_path.read_bytes()
        corrupted = bytes([self.weight_bytes[0] ^ 1]) + self.weight_bytes[1:]
        self.assertEqual(len(corrupted), len(self.weight_bytes))
        self.weights.write_bytes(corrupted)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "SHA-256 mismatch",
        ):
            self.serve()
        self.assertEqual(self.witness_path.read_bytes(), original_witness)

    def test_home_symlink_retarget_runs_full_verify_before_accepting(self) -> None:
        first = self.refresh()
        replacement = (
            self.root
            / "replacement"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        replacement.parent.mkdir(parents=True)
        shutil.copytree(self.durable_hub, replacement, symlinks=True)
        self.hub.unlink()
        self.hub.symlink_to(replacement, target_is_directory=True)

        served = self.serve()
        self.assertEqual(served["integrity"]["mode"], "full")
        self.assertEqual(served["witness"]["status"], "refreshed")
        self.assertNotEqual(
            served["witness"]["witness_id"],
            first["witness"]["witness_id"],
        )
        current = model_library.load_hot_witness(self.instance)
        self.assertEqual(
            current["view"]["hub"]["canonical_path"],
            str(replacement.resolve()),
        )

    def test_malformed_witness_is_only_an_accelerator_failure(self) -> None:
        self.refresh()
        malformed = json.loads(self.witness_path.read_text(encoding="utf-8"))
        malformed["witness_id"] = "0" * 64
        self.witness_path.write_text(json.dumps(malformed), encoding="utf-8")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            served = self.serve()
        self.assertIn("identity mismatch", stderr.getvalue())
        self.assertEqual(served["integrity"]["mode"], "full")
        model_library.load_hot_witness(self.instance)

    def test_witness_from_another_rank_cannot_fast_pass(self) -> None:
        self.refresh()
        other_instance = model_library.hot_instance_dir(
            self.root / "rank-1-hot",
            self.profile,
            self.topology_id,
            "content",
        )
        other_hub = model_library.hot_hub_path(other_instance, self.model_id)
        other_hub.parent.mkdir(parents=True)
        shutil.copytree(self.durable_hub, other_hub, symlinks=True)
        model_library.write_hot_stamp(other_instance, self.stamp)
        other_witness_path = model_library.hot_witness_path(other_instance)
        other_witness_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.witness_path, other_witness_path)

        served = model_library.verify_hot_ready(
            other_instance,
            profile=self.profile,
            topology_id=self.topology_id,
            workers=2,
            serve_time_witness=True,
        )
        self.assertEqual(served["integrity"]["mode"], "full")
        self.assertEqual(served["witness"]["status"], "refreshed")

    def test_controller_validation_mismatch_fails_before_hash_or_refresh(self) -> None:
        wrong = dict(self.validation)
        wrong["identity_status"] = "unvalidated"
        args = types.SimpleNamespace(
            instance_dir=str(self.instance),
            profile=self.profile,
            topology_id=self.topology_id,
            skip_digest=False,
            serve_time_witness=True,
            refresh_witness=False,
            allow_verifying=False,
            workers=2,
            models_dir="",
            expected_validation_json=json.dumps(wrong),
        )
        with mock.patch.object(
            model_library,
            "verify_snapshot_manifest",
            side_effect=AssertionError("hash must not run"),
        ) as verifier:
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "differs from controller expectation",
            ):
                model_library.cmd_verify_hot(args)
        verifier.assert_not_called()
        self.assertFalse(self.witness_path.exists())

    def test_mutation_during_full_verify_never_creates_witness(self) -> None:
        original_verify = model_library.verify_snapshot_manifest

        def mutate_after_hash(*args: object, **kwargs: object) -> dict[str, object]:
            result = original_verify(*args, **kwargs)
            self.weights.write_bytes(self.weight_bytes)
            return result

        with mock.patch.object(
            model_library,
            "verify_snapshot_manifest",
            side_effect=mutate_after_hash,
        ):
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "metadata changed during full verification",
            ):
                self.refresh()
        self.assertFalse(self.witness_path.exists())


if __name__ == "__main__":
    unittest.main()
