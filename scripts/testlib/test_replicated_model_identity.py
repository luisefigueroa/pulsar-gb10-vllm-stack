#!/usr/bin/env python3
"""Exact identity and serve-witness contracts for replicated HF caches."""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class ReplicatedModelIdentityContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "replicated-fixture"
        self.model_id = "Fixture/Replicated"
        self.revision = "a" * 40
        self.hub = (
            self.root
            / "cache"
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        self.snapshot = self.hub / "snapshots" / self.revision
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "config.json").write_bytes(b'{"fixture":true}\n')
        (self.snapshot / "model.safetensors").write_bytes(b"fixture-weights\n")
        manifest = model_library.build_snapshot_manifest(
            self.hub,
            model_id=self.model_id,
            revision=self.revision,
        )
        expected = {
            "seal_id": "b" * 64,
            "validation_bundle_id": "c" * 64,
            "model_id": self.model_id,
            "snapshot_revision": self.revision,
            "manifest_id": manifest["manifest_id"],
        }
        observed = model_library.observed_model_seal_projection(manifest)
        self.plan = {
            "schema_version": model_library.REPLICATED_PLAN_SCHEMA_VERSION,
            "kind": model_library.REPLICATED_PLAN_KIND,
            "weight_source": model_library.REPLICATED_WEIGHT_SOURCE,
            "profile": self.profile,
            "model_id": self.model_id,
            "snapshot_revision": self.revision,
            "manifest": manifest,
            "validation": {
                "identity_status": "match",
                "expected_seal": expected,
                "observed_seal": observed,
            },
        }
        self.plan["plan_id"] = model_library.canonical_json_digest(self.plan)
        model_library.validate_replicated_verification_plan(self.plan)
        self.witness = self.root / "state" / "witness.json"

    def verify(self, *, serve: bool) -> dict:
        return model_library.verify_replicated_cache(
            self.plan,
            hub_path=self.hub,
            witness_path=self.witness,
            serve_time_witness=serve,
            workers=2,
        )

    def test_reviewed_qwen_plan_resolves_exact_manifest(self) -> None:
        plan = model_library.replicated_verification_plan(
            REPO_ROOT / "models",
            "qwen3-1.7b",
        )
        self.assertEqual(
            plan["snapshot_revision"],
            "70d244cc86ccca08cf5af4e1e306ecf908b1ad5e",
        )
        self.assertEqual(
            plan["manifest"]["manifest_id"],
            "775e58d51419ccd0c3b28a151ec2d5fc28e14f3bbcb54a5ef1c1b1d17de995e1",
        )
        self.assertEqual(plan["validation"]["identity_status"], "match")

    def test_full_verify_then_zero_hash_fast_path(self) -> None:
        full = self.verify(serve=False)
        self.assertEqual(full["integrity"]["mode"], "full")
        self.assertGreater(full["integrity"]["bytes_hashed"], 0)
        self.assertEqual(full["witness"]["status"], "refreshed")

        fast = self.verify(serve=True)
        self.assertEqual(fast["integrity"]["mode"], "witness")
        self.assertEqual(fast["integrity"]["bytes_hashed"], 0)
        self.assertEqual(fast["witness"]["status"], "match")
        self.assertEqual(fast["snapshot_revision"], self.revision)

    def test_metadata_drift_runs_full_verify_and_refreshes(self) -> None:
        self.verify(serve=False)
        config = self.snapshot / "config.json"
        current = config.stat()
        os.utime(
            config,
            ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000),
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            refreshed = self.verify(serve=True)
        self.assertIn("running full SHA-256 verification", stderr.getvalue())
        self.assertEqual(refreshed["integrity"]["mode"], "full")
        self.assertGreater(refreshed["integrity"]["bytes_hashed"], 0)
        self.assertEqual(refreshed["witness"]["status"], "refreshed")
        self.assertEqual(self.verify(serve=True)["integrity"]["bytes_hashed"], 0)

    def test_same_size_corruption_fails_closed_without_refresh(self) -> None:
        self.verify(serve=False)
        before = self.witness.read_bytes()
        weights = self.snapshot / "model.safetensors"
        weights.write_bytes(b"X" * weights.stat().st_size)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "SHA-256 mismatch",
        ):
            self.verify(serve=True)
        self.assertEqual(self.witness.read_bytes(), before)

    def test_extra_snapshot_file_fails_before_launch(self) -> None:
        self.verify(serve=False)
        (self.snapshot / "unexpected.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "file set changed",
        ):
            self.verify(serve=True)

    def test_encoded_plan_is_digest_checked(self) -> None:
        encoded = model_library.encode_replicated_verification_plan(self.plan)
        decoded = model_library.decode_replicated_verification_plan(encoded)
        self.assertEqual(decoded, self.plan)
        changed = dict(self.plan)
        changed["snapshot_revision"] = "d" * 40
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "revision differs|digest mismatch",
        ):
            model_library.validate_replicated_verification_plan(changed)

    def test_witness_is_rank_local_and_not_part_of_model_tree(self) -> None:
        result = self.verify(serve=False)
        witness_path = pathlib.Path(result["witness"]["path"])
        self.assertTrue(witness_path.is_file())
        with self.assertRaises(ValueError):
            witness_path.relative_to(self.hub)
        document = json.loads(witness_path.read_text(encoding="utf-8"))
        self.assertEqual(document["weight_source"], "replicated")
        self.assertEqual(document["runtime_view"], "exact-snapshot")

    def test_witness_path_inside_repository_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "outside the model repository",
        ):
            model_library.verify_replicated_cache(
                self.plan,
                hub_path=self.hub,
                witness_path=self.hub / ".pulsar" / "witness.json",
                serve_time_witness=False,
                workers=2,
            )

    def test_streamed_remote_verifier_needs_no_checkout(self) -> None:
        encoded = model_library.encode_replicated_verification_plan(self.plan)
        streamed_witness = self.root / "remote-state" / "witness.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-",
                "verify-replicated",
                "--plan-b64",
                encoded,
                "--hub-path",
                str(self.hub),
                "--witness-path",
                str(streamed_witness),
                "--workers",
                "2",
            ],
            cwd=self.root,
            input=(REPO_ROOT / "scripts" / "model_library.py").read_text(
                encoding="utf-8"
            ),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["identity_status"], "match")
        self.assertEqual(result["integrity"]["mode"], "full")


if __name__ == "__main__":
    unittest.main()
