#!/usr/bin/env python3
"""Contracts for lab-issued expected model seals and exact revision launch."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class ExpectedModelSealContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.models_dir = self.root / "models"
        self.seals_dir = self.models_dir / "seals"
        self.bundles_dir = self.models_dir / "validation-bundles"
        self.seals_dir.mkdir(parents=True)
        self.bundles_dir.mkdir(parents=True)
        self.profile = "sealed-fixture"
        self.model_id = "Fixture/Sealed-Model"
        self.revision = "a" * 40
        self.cache_root = self.root / "cache"
        self.hub = (
            self.cache_root
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        snapshot = self.hub / "snapshots" / self.revision
        snapshot.mkdir(parents=True)
        (self.hub / "refs").mkdir()
        (self.hub / "refs" / "main").write_text(
            self.revision + "\n", encoding="utf-8"
        )
        (snapshot / "config.json").write_text(
            "{\"architectures\":[\"Fixture\"]}\n", encoding="utf-8"
        )
        (snapshot / "model.safetensors").write_bytes(b"sealed-weights")
        self.manifest = model_library.build_snapshot_manifest(
            self.hub, model_id=self.model_id
        )
        evidence = self.root / "results" / "model-library" / "fixture.json"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("{}\n", encoding="utf-8")
        self.evidence = ["results/model-library/fixture.json"]
        self.issuer = "pulsar-lab-fixture"
        self.issued_at = "2026-08-10T12:00:00Z"
        self.image = "registry.invalid/fixture@sha256:" + ("9" * 64)
        self.profile_contract = model_library.build_profile_contract(
            model_id=self.model_id,
            served_name=self.profile,
            image=self.image,
            nodes=1,
            port=8000,
            gpu_mem_util="0.80",
            engine_args=[],
            container_env=[],
            spec_decode_args=[],
            recommended_spec=False,
            profile_purpose="serving",
            topology_class="single",
            min_rails_per_pair=0,
            weights_gib="1",
            weights_ram_gib="1",
            kv_gib="2",
            overhead_gib="3",
            mem_min_free_gib="4",
        )
        self.bundle = self._write_bundle()
        self.seal_path = self.seals_dir / f"{self.profile}.json"
        self._write_seal()
        self._write_profile()
        self.catalog_path = self.root / "catalog.json"
        self._write_catalog()

    def _seal(self, **changes: object) -> dict[str, object]:
        seal: dict[str, object] = {
            "schema_version": 1,
            "kind": "pulsar-expected-model-seal",
            "profile": self.profile,
            "model_id": self.model_id,
            "revision_kind": "huggingface-commit",
            "snapshot_revision": self.revision,
            "manifest": {
                "scheme": "sha256-snapshot-manifest-v1",
                "manifest_id": self.manifest["manifest_id"],
            },
            "provenance": {
                "validation_bundle_id": self.bundle["bundle_id"],
                "issuer": self.issuer,
                "issued_at": self.issued_at,
                "evidence": self.evidence,
            },
        }
        seal.update(changes)
        seal["seal_id"] = model_library.expected_model_seal_id(seal)
        return seal

    def _write_seal(self, **changes: object) -> dict[str, object]:
        seal = self._seal(**changes)
        self.seal_path.write_text(json.dumps(seal), encoding="utf-8")
        return seal

    def _bundle(self, **changes: object) -> dict[str, object]:
        bundle: dict[str, object] = {
            "schema_version": 1,
            "kind": "pulsar-validation-bundle",
            "profile": self.profile,
            "models": [
                {
                    "role": "primary",
                    "model_id": self.model_id,
                    "revision_kind": "huggingface-commit",
                    "snapshot_revision": self.revision,
                    "manifest": {
                        "scheme": "sha256-snapshot-manifest-v1",
                        "manifest_id": self.manifest["manifest_id"],
                    },
                }
            ],
            "external_artifacts": [],
            "profile_contract": self.profile_contract,
            "evidence": self.evidence,
            "provenance": {
                "issuer": self.issuer,
                "issued_at": self.issued_at,
            },
        }
        bundle.update(changes)
        bundle["bundle_id"] = model_library.validation_bundle_id(bundle)
        return bundle

    def _write_bundle(self, **changes: object) -> dict[str, object]:
        bundle = self._bundle(**changes)
        path = self.bundles_dir / f"{bundle['bundle_id']}.json"
        path.write_text(json.dumps(bundle), encoding="utf-8")
        self.bundle_path = path
        return bundle

    def _write_profile(self, *, status: str = "tested", sealed: bool = True) -> None:
        lines = [
            f"MODEL=\"{self.model_id}\"",
            f"SERVED_NAME=\"{self.profile}\"",
            f"IMAGE=\"{self.image}\"",
            f"STATUS=\"{status}\"",
            "NODES=1",
            "TOPOLOGY_CLASS=\"single\"",
            "MIN_RAILS_PER_PAIR=0",
            "PORT=8000",
            "GPU_MEM_UTIL=0.80",
            "PROFILE_PURPOSE=\"serving\"",
            "WEIGHTS_GIB=1",
            "WEIGHTS_RAM_GIB=1",
            "KV_GIB=2",
            "OVERHEAD_GIB=3",
            "MEM_MIN_FREE_GIB=4",
        ]
        if sealed:
            lines.append(f"EXPECTED_MODEL_SEAL=\"seals/{self.seal_path.name}\"")
        (self.models_dir / f"{self.profile}.conf").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def _homes(self) -> list[dict[str, object]]:
        return model_library.scan_hub_cache(
            self.cache_root,
            rank=0,
            node_id="node-a",
            hostname="rank-0",
        )

    def _write_catalog(self) -> dict[str, object]:
        catalog = model_library.build_catalog(
            topology_id="topology-sealed",
            homes=self._homes(),
            profiles=model_library.load_hf_profiles(self.models_dir),
        )
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        return catalog

    def _plan(self, *, allow_unvalidated: bool = False) -> dict[str, object]:
        return model_library.plan_activate(
            catalog_path=str(self.catalog_path),
            profile=self.profile,
            topology_id="topology-sealed",
            hot_root=str(self.root / "hot"),
            models_dir=self.models_dir,
            backend="copy",
            allow_unvalidated=allow_unvalidated,
            nodes=1,
        )

    def _materialize_hot(self) -> tuple[dict[str, object], pathlib.Path]:
        plan = self._plan()
        destination = pathlib.Path(str(plan["hub_dest"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.hub, destination, symlinks=True)
        instance = pathlib.Path(str(plan["instance_dir"]))
        model_library.write_hot_stamp(instance, plan["stamp"])
        return plan, instance

    def test_catalog_binds_profile_to_exact_expected_revision(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(catalog["schema_version"], 2)
        entry = catalog["models"][0]
        self.assertEqual(entry["revision"], self.revision)
        self.assertEqual(entry["validation"], "expected-unverified")
        expected = entry["profile_validation"][0]["expected_model_seal"]
        self.assertEqual(expected["manifest_id"], self.manifest["manifest_id"])
        bundle = entry["profile_validation"][0]["validation_bundle"]
        self.assertEqual(bundle["bundle_id"], self.bundle["bundle_id"])
        self.assertEqual(bundle["image_digest"], "sha256:" + ("9" * 64))
        self.assertTrue(model_library.catalog_entry_has_expected_identity(entry))

    def test_activation_full_hash_matches_lab_expected_seal(self) -> None:
        plan = self._plan()
        validation = plan["validation"]
        self.assertEqual(validation["identity_status"], "match")
        self.assertEqual(plan["stamp"]["schema_version"], 3)
        self.assertEqual(
            validation["expected_seal"]["manifest_id"],
            validation["observed_seal"]["manifest_id"],
        )

    def test_content_mismatch_cannot_be_overridden(self) -> None:
        weights = self.hub / "snapshots" / self.revision / "model.safetensors"
        weights.write_bytes(b"x" * len(b"sealed-weights"))
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "expected model seal mismatch: manifest_id"
        ):
            self._plan(allow_unvalidated=True)

    def test_mutable_main_drift_does_not_change_sealed_activation(self) -> None:
        drift_revision = "c" * 40
        shutil.copytree(
            self.hub / "snapshots" / self.revision,
            self.hub / "snapshots" / drift_revision,
        )
        (
            self.hub
            / "snapshots"
            / drift_revision
            / "model.safetensors"
        ).write_bytes(b"different-upstream-weights")
        (self.hub / "refs" / "main").write_text(
            drift_revision + "\n", encoding="utf-8"
        )
        catalog = self._write_catalog()
        bound = [
            entry
            for entry in catalog["models"]
            if self.profile in entry["profiles"]
        ]
        self.assertEqual([entry["revision"] for entry in bound], [self.revision])
        plan = self._plan()
        self.assertEqual(plan["revision"], self.revision)
        self.assertEqual(
            plan["integrity_manifest"]["snapshot_revision"],
            self.revision,
        )

    def test_commit_pinned_cache_does_not_require_refs_main(self) -> None:
        shutil.rmtree(self.hub / "refs")
        catalog = self._write_catalog()
        self.assertEqual(len(catalog["models"]), 1)
        self.assertEqual(catalog["models"][0]["revision"], self.revision)
        plan = self._plan()
        self.assertEqual(plan["validation"]["identity_status"], "match")
        self.assertEqual(plan["revision"], self.revision)

    def test_legacy_experiment_uses_unambiguous_main_revision(self) -> None:
        drift_revision = "d" * 40
        shutil.copytree(
            self.hub / "snapshots" / self.revision,
            self.hub / "snapshots" / drift_revision,
        )
        (self.hub / "refs" / "main").write_text(
            drift_revision + "\n", encoding="utf-8"
        )
        self._write_profile(sealed=False)
        catalog = self._write_catalog()
        bound = [
            entry
            for entry in catalog["models"]
            if self.profile in entry["profiles"]
        ]
        self.assertEqual([entry["revision"] for entry in bound], [drift_revision])
        plan = self._plan(allow_unvalidated=True)
        self.assertEqual(plan["revision"], drift_revision)
        self.assertEqual(
            plan["validation"]["identity_status"],
            "legacy-unsealed",
        )

    def test_cold_stage_selects_sealed_commit_without_refs_main(self) -> None:
        cold_root = self.root / "cold"
        cold_hub = (
            cold_root
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        cold_hub.parent.mkdir(parents=True)
        shutil.copytree(self.hub, cold_hub, symlinks=True)
        shutil.rmtree(cold_hub / "refs")
        plan = model_library.plan_cold_stage(
            cold_root=str(cold_root),
            profile=self.profile,
            topology_id="topology-sealed",
            hot_root=str(self.root / "cold-hot"),
            models_dir=self.models_dir,
            nodes=1,
        )
        self.assertEqual(plan["revision"], self.revision)
        self.assertEqual(plan["validation"]["identity_status"], "match")

    def test_absolute_path_profile_remains_explicit_legacy_experiment(self) -> None:
        cold_root = self.root / "absolute-cold"
        model_root = cold_root / "Official Models" / "AbsOrg" / "AbsModel"
        model_root.mkdir(parents=True)
        (model_root / "config.json").write_text("{}\n", encoding="utf-8")
        (model_root / "model.safetensors").write_bytes(b"absolute-weights")
        profile = "absolute-fixture"
        (self.models_dir / f"{profile}.conf").write_text(
            f'MODEL="{model_root}"\nSTATUS="tested"\nNODES=1\n',
            encoding="utf-8",
        )
        plan = model_library.plan_cold_stage(
            cold_root=str(cold_root),
            profile=profile,
            topology_id="topology-sealed",
            hot_root=str(self.root / "absolute-hot"),
            models_dir=self.models_dir,
            allow_unvalidated=True,
            nodes=1,
        )
        model_library.materialize_hub_tree(
            plan["source_path"],
            plan["hub_dest"],
            layout=plan["layout"],
            revision=plan["revision"],
        )
        model_library.write_hot_stamp(
            pathlib.Path(plan["instance_dir"]),
            plan["stamp"],
        )
        live = model_library.load_model_profile(self.models_dir, profile)
        self.assertEqual(live["model_id"], "AbsOrg/AbsModel")
        stamp = model_library.load_hot_stamp(plan["instance_dir"])
        validation = model_library.verify_hot_stamp_against_profile(stamp, live)
        self.assertEqual(validation["identity_status"], "legacy-unsealed")

    def test_legacy_tested_profile_requires_explicit_override(self) -> None:
        self._write_profile(sealed=False)
        self._write_catalog()
        with self.assertRaisesRegex(model_library.ModelLibraryError, "legacy-unsealed"):
            self._plan()
        plan = self._plan(allow_unvalidated=True)
        self.assertEqual(plan["validation"]["identity_status"], "legacy-unsealed")

    def test_seal_cannot_self_promote_experimental_profile(self) -> None:
        self._write_profile(status="experimental")
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "EXPECTED_MODEL_SEAL requires STATUS=tested",
        ):
            model_library.load_hf_profile(self.models_dir, self.profile)

    def test_hot_launch_resolves_exact_snapshot_path(self) -> None:
        _plan, instance = self._materialize_hot()
        verified = model_library.verify_hot_ready(
            instance,
            profile=self.profile,
            topology_id="topology-sealed",
        )
        self.assertTrue(verified["snapshot_path"].endswith(self.revision))
        args = types.SimpleNamespace(
            hot_root=str(self.root / "hot"),
            profile=self.profile,
            topology_id="topology-sealed",
            models_dir=str(self.models_dir),
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            model_library.cmd_find_hot(args)
        found = json.loads(output.getvalue())
        self.assertEqual(
            found["container_model_path"],
            "/root/.cache/huggingface/hub/"
            + model_library.model_id_to_hub_dirname(self.model_id)
            + f"/snapshots/{self.revision}",
        )

    def test_seal_change_creates_distinct_hot_identity(self) -> None:
        first = self._plan()
        self.issued_at = "2026-08-10T12:00:01Z"
        self.bundle = self._write_bundle()
        self._write_seal()
        self._write_catalog()
        second = self._plan()
        self.assertNotEqual(first["content_id"], second["content_id"])
        self.assertNotEqual(first["instance_dir"], second["instance_dir"])

    def test_missing_lab_evidence_fails_profile_load(self) -> None:
        changed = self._seal()
        changed["provenance"]["evidence"] = [
            "results/model-library/missing.json"
        ]
        changed["seal_id"] = model_library.expected_model_seal_id(changed)
        self.seal_path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "expected seal evidence is missing",
        ):
            model_library.load_hf_profile(self.models_dir, self.profile)

    def test_remote_verify_requires_controller_validation_identity(self) -> None:
        plan, instance = self._materialize_hot()
        wrong = dict(plan["validation"])
        wrong["identity_status"] = "legacy-unsealed"
        args = types.SimpleNamespace(
            instance_dir=str(instance),
            profile=self.profile,
            topology_id="topology-sealed",
            skip_digest=False,
            allow_verifying=False,
            workers=2,
            models_dir="",
            expected_validation_json=json.dumps(wrong),
        )
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "differs from controller expectation",
        ):
            model_library.cmd_verify_hot(args)

    def test_live_seal_change_invalidates_existing_hot_stamp(self) -> None:
        _plan, instance = self._materialize_hot()
        self.issued_at = "2026-08-10T12:00:01Z"
        self.bundle = self._write_bundle()
        self._write_seal()
        live_profile = model_library.load_hf_profile(self.models_dir, self.profile)
        stamp = model_library.load_hot_stamp(instance)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "provenance differs"
        ):
            model_library.verify_hot_stamp_against_profile(stamp, live_profile)

    def test_expected_seal_requires_its_content_addressed_bundle(self) -> None:
        self.bundle_path.unlink()
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "validation bundle is missing",
        ):
            model_library.load_hf_profile(self.models_dir, self.profile)

    def test_live_profile_contract_drift_fails_closed(self) -> None:
        changed = json.loads(json.dumps(self.profile_contract))
        changed["runtime"]["port"] = 9000
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "profile contract differs from live profile",
        ):
            model_library.verify_profile_validation_bundle(
                models_dir=self.models_dir,
                profile=self.profile,
                profile_contract=changed,
                expected_seal_ref=f"seals/{self.seal_path.name}",
            )

    def test_shell_load_conf_checks_sourced_profile_against_bundle(self) -> None:
        command = r'''
. "$1/scripts/lib.sh"
REPO_DIR="$2"
PULSAR_MODEL_LIBRARY_PY="$1/scripts/model_library.py"
load_conf sealed-fixture
printf '%s\n' "$PROFILE_VALIDATION_BUNDLE_JSON"
'''
        matched = subprocess.run(
            ["bash", "-c", command, "bundle-test", str(REPO_ROOT), str(self.root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(matched.returncode, 0, matched.stderr)
        self.assertEqual(json.loads(matched.stdout)["state"], "match")

        profile_path = self.models_dir / f"{self.profile}.conf"
        profile_path.write_text(
            profile_path.read_text(encoding="utf-8").replace(
                "PORT=8000",
                "PORT=9000",
            ),
            encoding="utf-8",
        )
        drifted = subprocess.run(
            ["bash", "-c", command, "bundle-test", str(REPO_ROOT), str(self.root)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("does not match the sourced profile", drifted.stderr)

    def test_external_artifact_identity_is_content_addressed(self) -> None:
        artifacts = [
            {
                "role": "adapter",
                "artifact_id": "Fixture/Adapter",
                "revision": "adapter-release-1",
                "digest": {"scheme": "sha256", "value": "8" * 64},
            }
        ]
        changed = self._bundle(external_artifacts=artifacts)
        model_library.validate_validation_bundle(changed, profile=self.profile)
        self.assertNotEqual(changed["bundle_id"], self.bundle["bundle_id"])

        malformed = json.loads(json.dumps(changed))
        malformed["external_artifacts"][0]["digest"]["value"] = "not-a-digest"
        malformed["bundle_id"] = model_library.validation_bundle_id(malformed)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "digest value is invalid",
        ):
            model_library.validate_validation_bundle(malformed)

    def test_profile_contract_requires_digest_pinned_image(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "image must be pinned",
        ):
            model_library.build_profile_contract(
                model_id=self.model_id,
                served_name=self.profile,
                image="registry.invalid/fixture:mutable",
                nodes=1,
                port=8000,
                gpu_mem_util="0.8",
                engine_args=[],
                container_env=[],
                spec_decode_args=[],
                recommended_spec=False,
                profile_purpose="serving",
                topology_class="single",
                min_rails_per_pair=0,
            )

    def test_bundle_model_or_provenance_cannot_diverge_from_seal(self) -> None:
        changed = json.loads(json.dumps(self.bundle))
        changed["provenance"]["issuer"] = "another-lab"
        changed["bundle_id"] = model_library.validation_bundle_id(changed)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "validation_bundle_id differs from bundle",
        ):
            model_library.validate_validation_bundle(
                changed,
                profile=self.profile,
                expected_seal=self._seal(),
            )

    def test_seal_identity_and_path_traversal_fail_closed(self) -> None:
        bad = self._seal()
        bad["seal_id"] = "0" * 64
        with self.assertRaisesRegex(model_library.ModelLibraryError, "identity mismatch"):
            model_library.validate_expected_model_seal(bad)
        malformed_model = self._seal(model_id="Fixture/")
        with self.assertRaisesRegex(model_library.ModelLibraryError, "repository ID"):
            model_library.validate_expected_model_seal(malformed_model)
        profile_path = self.models_dir / f"{self.profile}.conf"
        profile_path.write_text(
            f"MODEL=\"{self.model_id}\"\n"
            "STATUS=\"tested\"\n"
            "EXPECTED_MODEL_SEAL=\"../outside.json\"\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "relative to models"):
            model_library.load_hf_profile(self.models_dir, self.profile)


if __name__ == "__main__":
    unittest.main()
