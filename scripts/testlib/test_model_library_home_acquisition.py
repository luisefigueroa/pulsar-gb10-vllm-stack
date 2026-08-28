#!/usr/bin/env python3
"""Contracts for download-receipt planning helpers."""

from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_identity,
    model_library,
    model_library_receipt as source_attested,
)
from scripts.topology_manifest import topology_digest  # noqa: E402
from scripts.testlib import (  # noqa: E402
    model_serving_release_fixture as release_fixture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_registry_fixture as registry_fixture,
)


class SourceAttestedAcquisitionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "unbound-fixture"
        self.model_id = "Fixture/Unbound-Model"
        self.commit = "a" * 40
        self.selector = "main"

    def _git_entry(
        self,
        path: str = "config.json",
        *,
        size: int = 21,
        git_oid: str | None = None,
    ) -> dict[str, object]:
        return {
            "path": path,
            "size": size,
            "blob_kind": source_attested.HF_V1_BLOB_GIT,
            "git_oid": git_oid or ("1" * 40),
        }

    def _lfs_entry(
        self,
        path: str = "model.safetensors",
        *,
        size: int = 64,
        sha256: str | None = None,
    ) -> dict[str, object]:
        return {
            "path": path,
            "size": size,
            "blob_kind": source_attested.HF_V1_BLOB_LFS,
            "sha256": sha256 or ("2" * 64),
        }

    def _source(
        self,
        *,
        model_id: str | None = None,
        selector: str | None = None,
        snapshot_revision: str | None = None,
        inventory: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        return source_attested.build_huggingface_v1_acquisition_source(
            model_id=model_id or self.model_id,
            selector=selector if selector is not None else self.selector,
            snapshot_revision=snapshot_revision or self.commit,
            inventory=inventory or [self._lfs_entry(), self._git_entry()],
        )

    def _registry_repo(self, release: dict[str, object]) -> pathlib.Path:
        repo = self.root / release["release_id"][:12]
        registry_root = repo / "models" / "model-serving-releases"
        registry_fixture.init_registry_root(registry_root)
        registry_fixture.write_release(registry_root, release)
        return repo

    def test_home_acquisition_accepts_only_modern_hf_cli(self) -> None:
        self.assertTrue(model_library.valid_home_acquisition_hf_cli("hf"))
        self.assertTrue(
            model_library.valid_home_acquisition_hf_cli(
                "/srv/operator/.hf-cli/venv/bin/hf"
            )
        )
        self.assertTrue(model_library.valid_home_acquisition_hf_cli(""))
        self.assertFalse(
            model_library.valid_home_acquisition_hf_cli("huggingface-cli")
        )
        self.assertFalse(
            model_library.valid_home_acquisition_hf_cli("/usr/local/bin/hf")
        )

    def test_source_inventory_and_digests_are_canonical(self) -> None:
        first = self._source(inventory=[self._lfs_entry(), self._git_entry()])
        second = self._source(inventory=[self._git_entry(), self._lfs_entry()])
        self.assertEqual(first, second)
        self.assertEqual(
            [item["path"] for item in first["inventory"]],
            ["config.json", "model.safetensors"],
        )
        self.assertEqual(first["file_count"], 2)
        self.assertEqual(first["content_bytes"], 85)
        self.assertEqual(
            first["inventory_digest"],
            source_attested.huggingface_v1_inventory_digest(first["inventory"]),
        )
        self.assertEqual(
            first["source_digest"],
            source_attested.huggingface_v1_source_digest(first),
        )
        self.assertEqual(first["schema_version"], 1)
        self.assertNotEqual(
            first["kind"], "pulsar-model-library-home-acquisition-plan"
        )

    def test_git_blob_has_no_invented_content_sha256(self) -> None:
        source = self._source()
        git_entry = next(
            item
            for item in source["inventory"]
            if item["blob_kind"] == source_attested.HF_V1_BLOB_GIT
        )
        lfs_entry = next(
            item
            for item in source["inventory"]
            if item["blob_kind"] == source_attested.HF_V1_BLOB_LFS
        )
        self.assertEqual(git_entry["git_oid"], "1" * 40)
        self.assertNotIn("sha256", git_entry)
        self.assertEqual(lfs_entry["sha256"], "2" * 64)
        self.assertNotIn("git_oid", lfs_entry)

    def test_zero_byte_inventory_entry_allowed_with_positive_aggregate(self) -> None:
        source = self._source(
            inventory=[self._git_entry(size=0), self._lfs_entry()]
        )
        self.assertEqual(source["file_count"], 2)
        self.assertEqual(source["content_bytes"], 64)
        self.assertEqual(source["inventory"][0]["size"], 0)

    def test_negative_and_zero_total_inventory_rejected(self) -> None:
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "non-negative"
        ):
            self._source(
                inventory=[self._git_entry(size=-1), self._lfs_entry()]
            )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "positive"
        ):
            self._source(inventory=[self._git_entry(size=0)])

    def test_malformed_duplicate_and_unsafe_inventory_rejected(self) -> None:
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "unique"
        ):
            source_attested.build_huggingface_v1_acquisition_source(
                model_id=self.model_id,
                selector=self.selector,
                snapshot_revision=self.commit,
                inventory=[self._git_entry(), self._git_entry()],
            )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "unsafe"
        ):
            self._source(inventory=[self._git_entry("../escape.json")])
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "relative POSIX"
        ):
            self._source(inventory=[self._git_entry("/abs/config.json")])
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "invent"
        ):
            source_attested.normalize_huggingface_v1_inventory_entry(
                path="config.json",
                size=21,
                blob_kind=source_attested.HF_V1_BLOB_GIT,
                sha256="2" * 64,
                git_oid="1" * 40,
            )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "fields differ"
        ):
            source_attested.validate_huggingface_v1_acquisition_source(
                {
                    **self._source(),
                    "local_path": "/tmp/huggingface/hub",
                }
            )

    def test_source_rejects_non_hex_commit_and_credential_selector(self) -> None:
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "40-hex"
        ):
            self._source(snapshot_revision="not-a-commit")
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "selector|private|secret",
        ):
            self._source(selector="hf_abcdefghijklmnopqrstuvwxyz0123456789ABCD")

    def test_unbound_identity_is_source_attested_without_status_claims(
        self,
    ) -> None:
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=self._source(),
            profile=self.profile,
        )
        self.assertEqual(
            identity["identity_class"],
            source_attested.IDENTITY_CLASS_DOWNLOAD_RECEIPT,
        )
        self.assertEqual(
            identity["execution_contract"],
            source_attested.EXECUTION_CONTRACT_SOURCE_ATTESTED,
        )
        self.assertIsNone(identity["model_serving_release_id"])
        self.assertIsNone(identity["seal_id"])
        self.assertIsNone(identity["expected_manifest_id"])
        rendered = json.dumps(identity)
        self.assertNotRegex(rendered, r"(?i)\bvalidated\b")
        self.assertNotRegex(rendered, r"(?i)\breviewed\b")
        self.assertNotRegex(rendered, r"(?i)\bsealed\b")
        self.assertNotIn('"match"', rendered)
        self.assertNotIn("recommendation", rendered)
        self.assertNotIn("serving_authorization", rendered)

    def test_unavailable_or_unverified_binding_fails_without_fallback(
        self,
    ) -> None:
        empty = self.root / "empty-registry"
        registry_fixture.init_registry_root(
            empty / "models" / "model-serving-releases"
        )
        source = self._source()
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "cannot be verified",
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=source,
                profile=self.profile,
                model_serving_release_id="f" * 64,
                repo_root=empty,
            )
        corrupt = self.root / "corrupt-registry"
        registry_root = corrupt / "models" / "model-serving-releases"
        registry_fixture.init_registry_root(registry_root)
        (registry_root / "descriptors" / f"{'e' * 64}.json").write_text(
            '{"kind":"not-a-verified-release"}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "cannot be verified",
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=source,
                profile=self.profile,
                model_serving_release_id="e" * 64,
                repo_root=corrupt,
            )

    def test_conflicting_revision_and_content_addressed_primary_are_refused(
        self,
    ) -> None:
        release = release_fixture.build_release()
        repo = self._registry_repo(release)
        conflicting = self._source(
            model_id="Fixture/Primary-Model",
            selector="b" * 40,
            snapshot_revision="b" * 40,
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "commit differs"
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=conflicting,
                profile=self.profile,
                model_serving_release_id=release["release_id"],
                repo_root=repo,
            )
        addressed = release_fixture.build_content_addressed_release()
        addressed_repo = self._registry_repo(addressed)
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "content-addressed-model",
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=self._source(
                    model_id="fixture/catalog-primary-model",
                    snapshot_revision="a" * 40,
                ),
                profile=self.profile,
                model_serving_release_id=addressed["release_id"],
                repo_root=addressed_repo,
            )

    def test_approval_id_is_stable_and_sensitive_to_bound_inputs(self) -> None:
        source = self._source()
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile=self.profile
        )
        topology = "d" * 64
        first = source_attested.build_source_attested_acquisition_approval(
            source=source,
            identity=identity,
            serving_ranks=[0, 1],
            selected_rank=1,
            selection="most-free-space",
            topology_generation=topology,
        )
        reordered = source_attested.build_source_attested_acquisition_approval(
            source=self._source(
                inventory=[self._git_entry(), self._lfs_entry()]
            ),
            identity=identity,
            serving_ranks=[0, 1],
            selected_rank=1,
            selection="most-free-space",
            topology_generation=topology,
        )
        self.assertEqual(first["approval_id"], reordered["approval_id"])
        changed_commit = self._source(snapshot_revision="b" * 40)
        changed_identity = (
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=changed_commit, profile=self.profile
            )
        )
        variants = [
            source_attested.build_source_attested_acquisition_approval(
                source=changed_commit,
                identity=changed_identity,
                serving_ranks=[0, 1],
                selected_rank=1,
                selection="most-free-space",
                topology_generation=topology,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=self._source(
                    inventory=[
                        self._git_entry(),
                        self._lfs_entry(sha256="3" * 64),
                    ]
                ),
                identity=source_attested.resolve_huggingface_v1_acquisition_identity(
                    source=self._source(
                        inventory=[
                            self._git_entry(),
                            self._lfs_entry(sha256="3" * 64),
                        ]
                    ),
                    profile=self.profile,
                ),
                serving_ranks=[0, 1],
                selected_rank=1,
                selection="most-free-space",
                topology_generation=topology,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=source,
                identity=identity,
                serving_ranks=[0, 1],
                selected_rank=0,
                selection="most-free-space",
                topology_generation=topology,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=source,
                identity=identity,
                serving_ranks=[0],
                selected_rank=0,
                selection="most-free-space",
                topology_generation=topology,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=source,
                identity=identity,
                serving_ranks=[0, 1],
                selected_rank=1,
                selection="most-free-space",
                topology_generation="e" * 64,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=source,
                identity=identity,
                serving_ranks=[0, 1],
                selected_rank=1,
                selection="operator-override",
                topology_generation=topology,
            ),
            source_attested.build_source_attested_acquisition_approval(
                source=self._source(
                    inventory=[
                        self._git_entry(),
                        self._lfs_entry(size=128),
                    ]
                ),
                identity=source_attested.resolve_huggingface_v1_acquisition_identity(
                    source=self._source(
                        inventory=[
                            self._git_entry(),
                            self._lfs_entry(size=128),
                        ]
                    ),
                    profile=self.profile,
                ),
                serving_ranks=[0, 1],
                selected_rank=1,
                selection="most-free-space",
                topology_generation=topology,
            ),
        ]
        ids = {first["approval_id"], *(item["approval_id"] for item in variants)}
        self.assertEqual(len(ids), 1 + len(variants))
        mutated_policy = dict(first)
        mutated_policy["policy"] = {
            "version": 2,
            "operations": list(first["policy"]["operations"]),
        }
        self.assertNotEqual(
            first["approval_id"],
            source_attested.source_attested_acquisition_approval_id(
                mutated_policy,
                source=source,
                topology_generation=topology,
            ),
        )

    def test_approval_omits_private_topology_paths_and_live_capacity(self) -> None:
        source = self._source()
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile=self.profile
        )
        topology = "d" * 64
        approval = source_attested.build_source_attested_acquisition_approval(
            source=source,
            identity=identity,
            serving_ranks=[0, 1],
            selected_rank=1,
            selection="most-free-space",
            topology_generation=topology,
        )
        self.assertEqual(approval["selected_rank"], 1)
        self.assertEqual(approval["serving_ranks"], [0, 1])
        self.assertEqual(
            approval["required_free_bytes"],
            model_library.home_acquisition_required_bytes(source["content_bytes"]),
        )
        self.assertEqual(
            approval["required_free_bytes"],
            source_attested.source_attested_required_free_bytes(
                source["content_bytes"]
            ),
        )
        rendered = json.dumps(approval)
        for banned in source_attested.PROHIBITED_APPROVAL_FIELD_NAMES:
            self.assertNotIn(banned, approval)
            self.assertNotRegex(
                rendered,
                rf'"{re.escape(banned)}"',
            )
        self.assertNotIn(topology, rendered)
        self.assertNotIn("192.0.2.", rendered)
        self.assertNotIn("/tmp/", rendered)
        self.assertNotIn("~/.cache", rendered)
        self.assertEqual(
            approval["policy"]["operations"],
            list(source_attested.SOURCE_ATTESTED_ACQUISITION_POLICY_OPERATIONS),
        )
        self.assertEqual(approval["adapter"]["kind"], "huggingface-v1")
        self.assertEqual(
            source_attested.HF_V1_REQUIRED_CLI,
            "hf",
        )

    def test_approval_rejects_reviewed_fields_for_source_identity(self) -> None:
        source = self._source()
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile=self.profile
        )
        topology = "d" * 64
        approval = source_attested.build_source_attested_acquisition_approval(
            source=source,
            identity=identity,
            serving_ranks=[0, 1],
            selected_rank=1,
            selection="most-free-space",
            topology_generation=topology,
        )
        forged = dict(approval)
        forged["seal_id"] = "e" * 64
        forged["approval_id"] = (
            source_attested.source_attested_acquisition_approval_id(
                forged,
                source=source,
                topology_generation=topology,
            )
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "must not carry reviewed identity",
        ):
            source_attested.verify_source_attested_acquisition_approval(
                forged,
                source=source,
                identity=identity,
                topology_generation=topology,
            )

    def test_approval_verification_checks_complete_live_identity(self) -> None:
        source = self._source()
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile=self.profile
        )
        topology = "d" * 64
        approval = source_attested.build_source_attested_acquisition_approval(
            source=source,
            identity=identity,
            serving_ranks=[0, 1],
            selected_rank=1,
            selection="most-free-space",
            topology_generation=topology,
        )
        other_identity = dict(identity)
        other_identity["profile"] = "different-profile"
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "profile differs",
        ):
            source_attested.verify_source_attested_acquisition_approval(
                approval,
                source=source,
                identity=other_identity,
                topology_generation=topology,
            )

    def test_sealed_capacity_formula_remains_shared(self) -> None:
        self.assertEqual(
            source_attested.SOURCE_ATTESTED_ACQUISITION_MIN_HEADROOM_BYTES,
            model_library.HOME_ACQUISITION_MIN_HEADROOM_BYTES,
        )
        for size in (1, 100, 5 * 1024**3, 20 * 1024**3):
            self.assertEqual(
                source_attested.source_attested_required_free_bytes(size),
                model_library.home_acquisition_required_bytes(size),
            )


if __name__ == "__main__":
    unittest.main()
