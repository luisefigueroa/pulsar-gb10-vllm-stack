#!/usr/bin/env python3
"""Contracts for maintainer-only model release candidates."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity, model_library, model_release  # noqa: E402


class ModelReleaseContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "release-fixture"
        self.model_id = "Fixture/Release-Model"
        self.revision = "a" * 40
        self.image = "registry.invalid/vllm@sha256:" + ("9" * 64)
        self.hub = self.root / "hub" / model_library.model_id_to_hub_dirname(
            self.model_id
        )
        snapshot = self.hub / "snapshots" / self.revision
        snapshot.mkdir(parents=True)
        (self.hub / "refs").mkdir()
        (self.hub / "refs" / "main").write_text(
            ("b" * 40) + "\n",
            encoding="utf-8",
        )
        (snapshot / "config.json").write_text(
            '{"architectures":["Fixture"]}\n',
            encoding="utf-8",
        )
        (snapshot / "model.safetensors").write_bytes(b"release-weights")
        self.evidence_path = self.root / "results" / "fixture.json"
        self.evidence_path.parent.mkdir(parents=True)
        self.evidence_path.write_text("{}\n", encoding="utf-8")
        self.evidence = "results/fixture.json"

    def common_args(self, **changes: object) -> list[str]:
        values: dict[str, object] = {
            "model_id": self.model_id,
            "served_name": self.profile,
            "image": self.image,
            "nodes": 1,
            "port": 8000,
            "gpu_mem_util": "0.80",
            "recommended_spec": 0,
            "profile_purpose": "serving",
            "topology_class": "single",
            "min_rails_per_pair": 0,
            "weights_gib": "1.00",
            "weights_ram_gib": "1",
            "kv_gib": "2.0",
            "overhead_gib": "3",
            "mem_min_free_gib": "4",
        }
        values.update(changes)
        return [
            "--repo-root",
            str(self.root),
            "--profile",
            self.profile,
            "--model-id",
            str(values["model_id"]),
            "--served-name",
            str(values["served_name"]),
            "--image",
            str(values["image"]),
            "--nodes",
            str(values["nodes"]),
            "--port",
            str(values["port"]),
            "--gpu-mem-util",
            str(values["gpu_mem_util"]),
            "--recommended-spec",
            str(values["recommended_spec"]),
            "--profile-purpose",
            str(values["profile_purpose"]),
            "--topology-class",
            str(values["topology_class"]),
            "--min-rails-per-pair",
            str(values["min_rails_per_pair"]),
            f"--weights-gib={values['weights_gib']}",
            f"--weights-ram-gib={values['weights_ram_gib']}",
            f"--kv-gib={values['kv_gib']}",
            f"--overhead-gib={values['overhead_gib']}",
            f"--mem-min-free-gib={values['mem_min_free_gib']}",
        ]

    def run_tool(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = model_release.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def make_manifest(self, *, name: str = "observed") -> pathlib.Path:
        output = self.root / "experiments" / "release-candidates" / name
        result, stdout, stderr = self.run_tool(
            [
                "manifest",
                *self.common_args(),
                "--hub-path",
                str(self.hub),
                "--revision",
                self.revision,
                "--output-dir",
                str(output),
                "--json",
            ]
        )
        self.assertEqual(result, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["state"], "observed-unreviewed")
        self.assertEqual(report["verification_mode"], "full")
        self.assertGreater(report["bytes_hashed"], 0)
        return output / "snapshot-manifest.json"

    def assemble(
        self,
        manifest: pathlib.Path,
        *,
        name: str = "candidate",
        common_args: list[str] | None = None,
    ) -> pathlib.Path:
        output = self.root / "experiments" / "release-candidates" / name
        result, stdout, stderr = self.run_tool(
            [
                "assemble",
                *(common_args or self.common_args()),
                "--manifest",
                str(manifest),
                "--issuer",
                "pulsar-lab-fixture",
                "--issued-at",
                "2026-08-11T12:00:00Z",
                "--evidence",
                self.evidence,
                "--output-dir",
                str(output),
                "--json",
            ]
        )
        self.assertEqual(result, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["state"], "candidate-match")
        self.assertEqual(report["authority"], "none")
        self.assertEqual(report["promotion"], "not-authorized")
        return output

    def test_shared_identity_module_is_live_model_library_owner(self) -> None:
        self.assertIs(
            model_library.build_profile_contract,
            model_identity.build_profile_contract,
        )
        self.assertIs(
            model_library.validate_validation_bundle,
            model_identity.validate_validation_bundle,
        )
        self.assertIs(
            model_library.validate_expected_model_seal,
            model_identity.validate_expected_model_seal,
        )

    def test_plan_normalizes_profile_and_has_no_authority(self) -> None:
        result, stdout, stderr = self.run_tool(
            ["plan", *self.common_args(gpu_mem_util="0.800"), "--json"]
        )
        self.assertEqual(result, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["state"], "candidate-only")
        self.assertEqual(plan["authority"], "none")
        self.assertFalse(plan["trusted_write_enabled"])
        self.assertEqual(plan["profile_contract"]["runtime"]["gpu_mem_util"], "0.8")
        self.assertEqual(plan["profile_contract"]["memory_policy"]["weights_gib"], "1")

    def test_plan_rejects_mutable_image_without_writing(self) -> None:
        result, _stdout, stderr = self.run_tool(
            [
                "plan",
                *self.common_args(image="registry.invalid/vllm:mutable"),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("image must be pinned", stderr)
        self.assertFalse((self.root / "experiments").exists())

    def test_candidate_is_deterministic_and_verifies_against_profile(self) -> None:
        manifest = self.make_manifest()
        first = self.assemble(manifest, name="candidate-one")
        second = self.assemble(manifest, name="candidate-two")
        for name in (
            "snapshot-manifest.json",
            "validation-bundle.json",
            "expected-model-seal.json",
            "candidate.json",
        ):
            self.assertEqual(
                (first / name).read_bytes(),
                (second / name).read_bytes(),
                name,
            )

        result, stdout, stderr = self.run_tool(
            [
                "verify-candidate",
                *self.common_args(gpu_mem_util="0.800"),
                "--candidate-dir",
                str(first),
                "--json",
            ]
        )
        self.assertEqual(result, 0, stderr)
        report = json.loads(stdout)
        self.assertTrue(report["ok"])
        self.assertEqual(report["state"], "candidate-match")
        self.assertEqual(report["privacy_review"], "pending")

    def test_exact_revision_ignores_mutable_main_and_rejects_alias(self) -> None:
        manifest_path = self.make_manifest()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["snapshot_revision"], self.revision)
        self.assertNotEqual(
            manifest["snapshot_revision"],
            (self.hub / "refs" / "main").read_text(encoding="utf-8").strip(),
        )
        output = self.root / "experiments" / "release-candidates" / "mutable"
        result, _stdout, stderr = self.run_tool(
            [
                "manifest",
                *self.common_args(),
                "--hub-path",
                str(self.hub),
                "--revision",
                "main",
                "--output-dir",
                str(output),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("exact 40-64 hex HF commit", stderr)
        self.assertFalse(output.exists())

    def test_manifest_full_hash_rejects_corrupt_lfs_blob(self) -> None:
        revision = "d" * 40
        hub = self.root / "corrupt" / model_library.model_id_to_hub_dirname(
            self.model_id
        )
        snapshot = hub / "snapshots" / revision
        blobs = hub / "blobs"
        snapshot.mkdir(parents=True)
        blobs.mkdir()
        expected_digest = hashlib.sha256(b"expected-weights").hexdigest()
        (blobs / expected_digest).write_bytes(b"tampered-weights")
        (snapshot / "config.json").write_text("{}\n", encoding="utf-8")
        (snapshot / "model.safetensors").symlink_to(
            pathlib.Path("../..") / "blobs" / expected_digest
        )
        output = self.root / "experiments" / "release-candidates" / "corrupt"
        result, _stdout, stderr = self.run_tool(
            [
                "manifest",
                *self.common_args(),
                "--hub-path",
                str(hub),
                "--revision",
                revision,
                "--output-dir",
                str(output),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("SHA-256 mismatch", stderr)
        self.assertFalse(output.exists())

    def test_candidate_tampering_and_profile_drift_fail_closed(self) -> None:
        manifest = self.make_manifest()
        candidate = self.assemble(manifest)
        bundle_path = candidate / "validation-bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["provenance"]["issuer"] = "tampered"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        result, _stdout, stderr = self.run_tool(
            [
                "verify-candidate",
                *self.common_args(),
                "--candidate-dir",
                str(candidate),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("identity mismatch", stderr)

        clean = self.assemble(manifest, name="candidate-drift")
        result, _stdout, stderr = self.run_tool(
            [
                "verify-candidate",
                *self.common_args(port=9000),
                "--candidate-dir",
                str(clean),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("differs from live profile", stderr)

    def test_candidate_cannot_claim_reviewed_authority(self) -> None:
        manifest = self.make_manifest()
        candidate_dir = self.assemble(manifest)
        descriptor_path = candidate_dir / "candidate.json"
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        descriptor["authority"] = "lab-reviewed"
        descriptor["candidate_id"] = model_release.candidate_id(descriptor)
        descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
        result, _stdout, stderr = self.run_tool(
            [
                "verify-candidate",
                *self.common_args(),
                "--candidate-dir",
                str(candidate_dir),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("cannot claim reviewed authority", stderr)

    def test_output_refuses_trusted_roots_and_overwrite(self) -> None:
        trusted = self.root / "models" / "seals" / "candidate"
        result, _stdout, stderr = self.run_tool(
            [
                "manifest",
                *self.common_args(),
                "--hub-path",
                str(self.hub),
                "--revision",
                self.revision,
                "--output-dir",
                str(trusted),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("cannot be written under models", stderr)
        self.assertFalse(trusted.exists())

        manifest = self.make_manifest(name="no-overwrite")
        result, _stdout, stderr = self.run_tool(
            [
                "manifest",
                *self.common_args(),
                "--hub-path",
                str(self.hub),
                "--revision",
                self.revision,
                "--output-dir",
                str(manifest.parent),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("refusing overwrite", stderr)

    def test_missing_evidence_fails_before_candidate_write(self) -> None:
        manifest = self.make_manifest()
        output = self.root / "experiments" / "release-candidates" / "no-evidence"
        result, _stdout, stderr = self.run_tool(
            [
                "assemble",
                *self.common_args(),
                "--manifest",
                str(manifest),
                "--issuer",
                "pulsar-lab-fixture",
                "--issued-at",
                "2026-08-11T12:00:00Z",
                "--output-dir",
                str(output),
            ]
        )
        self.assertEqual(result, 1)
        self.assertIn("requires at least one evidence", stderr)
        self.assertFalse(output.exists())

    def test_streamed_model_library_keeps_standalone_manifest_behavior(self) -> None:
        source = (REPO_ROOT / "scripts" / "model_library.py").read_text(
            encoding="utf-8"
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-",
                "inspect-hub",
                "--hub-path",
                str(self.hub),
                "--rank",
                "0",
                "--node-id",
                "fixture-node",
                "--model-id",
                self.model_id,
                "--revision",
                self.revision,
            ],
            input=source,
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        remote = json.loads(completed.stdout)
        local = model_library.inspect_hub_inventory(
            self.hub,
            rank=0,
            node_id="fixture-node",
            model_id=self.model_id,
            revision=self.revision,
        )
        self.assertEqual(remote["content_digest"], local["content_digest"])
        self.assertEqual(
            remote["integrity_manifest"],
            local["integrity_manifest"],
        )

    def test_shell_wrapper_manifest_uses_catalog_profile_identity(self) -> None:
        model_id = "Qwen/Qwen3-1.7B"
        revision = "c" * 40
        hub = self.root / "wrapper" / model_library.model_id_to_hub_dirname(model_id)
        snapshot = hub / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}\n", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"wrapper-weights")
        output = self.root / "wrapper-candidate"
        completed = subprocess.run(
            [
                str(REPO_ROOT / "scripts" / "model-release.sh"),
                "manifest",
                "qwen3-1.7b",
                "--hub-path",
                str(hub),
                "--revision",
                revision,
                "--output-dir",
                str(output),
                "--json",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = json.loads(
            (output / "snapshot-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["model_id"], model_id)
        self.assertEqual(manifest["snapshot_revision"], revision)
        self.assertNotIn(
            "model-release",
            (REPO_ROOT / "pulsar").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
