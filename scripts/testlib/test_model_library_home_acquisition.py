#!/usr/bin/env python3
"""Contracts for sealed home add and source-attested planning helpers."""

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
    model_library_source_attested as source_attested,
)
from scripts.topology_manifest import topology_digest  # noqa: E402
from scripts.testlib import (  # noqa: E402
    model_serving_release_fixture as release_fixture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_registry_fixture as registry_fixture,
)


class HomeAcquisitionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "sealed-fixture"
        self.model_id = "Fixture/Sealed-Model"
        self.revision = "a" * 40
        self.node_ids = ["node-a", "node-b", "node-c"]
        self.source_hub = self.root / "source" / model_library.model_id_to_hub_dirname(
            self.model_id
        )
        self._write_hub(self.source_hub)
        self.manifest = model_library.build_snapshot_manifest(
            self.source_hub,
            model_id=self.model_id,
        )
        observed = model_library.observed_model_seal_projection(self.manifest)
        expected = {
            "seal_id": "b" * 64,
            "validation_bundle_id": "c" * 64,
            "model_id": self.model_id,
            "snapshot_revision": self.revision,
            "manifest_id": self.manifest["manifest_id"],
        }
        self.identity_plan = {
            "schema_version": model_library.REPLICATED_PLAN_SCHEMA_VERSION,
            "kind": model_library.REPLICATED_PLAN_KIND,
            "weight_source": model_library.REPLICATED_WEIGHT_SOURCE,
            "profile": self.profile,
            "model_id": self.model_id,
            "snapshot_revision": self.revision,
            "manifest": self.manifest,
            "validation": {
                "identity_status": "match",
                "expected_seal": expected,
                "observed_seal": observed,
            },
        }
        self.identity_plan["plan_id"] = model_library.canonical_json_digest(
            self.identity_plan
        )
        model_library.validate_replicated_verification_plan(self.identity_plan)

        topology = {
            "schema_version": 1,
            "nodes": [
                {
                    "rank": rank,
                    "node_id": node_id,
                    "hostname": f"fixture-{rank}",
                    "ssh_host": "local" if rank == 0 else f"fixture-{rank}",
                    "control": {
                        "interface": "mgmt0",
                        "ip": f"192.0.2.{10 + rank}",
                    },
                    "gpu": "NVIDIA GB10",
                    "rdma": [
                        {
                            "hca": "roce0",
                            "netdev": "fabric0",
                            "cidrs": [f"198.51.100.{10 + rank}/24"],
                        }
                    ],
                }
                for rank, node_id in enumerate(self.node_ids)
            ],
            "links": [
                {
                    "ranks": [left, right],
                    "rails": [
                        {
                            "network": "198.51.100.0/24",
                            "a": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": f"198.51.100.{10 + left}",
                            },
                            "b": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": f"198.51.100.{10 + right}",
                            },
                        }
                    ],
                }
                for left in range(len(self.node_ids))
                for right in range(left + 1, len(self.node_ids))
            ],
            "validation": {
                "class": "single" if len(self.node_ids) == 1 else "roce-full-mesh",
                "full_mesh": True,
                "connectivity_verified": True,
                "min_rails_per_pair": 1,
            },
        }
        topology["topology_id"] = topology_digest(topology)
        self.topology = topology
        self.topology_path = self.root / "topology.json"
        self.topology_path.write_text(json.dumps(topology), encoding="utf-8")
        self.observations = self.root / "observations"
        self.observations.mkdir()
        self.cache_roots = [
            self.root / f"cache-{rank}" for rank in range(len(self.node_ids))
        ]
        self._write_observations()

    def _write_hub(self, hub: pathlib.Path, *, weights: bytes = b"fixture-weights") -> None:
        snapshot = hub / "snapshots" / self.revision
        snapshot.mkdir(parents=True)
        (hub / "refs").mkdir()
        (hub / "refs" / "main").write_text(self.revision + "\n", encoding="utf-8")
        (snapshot / "config.json").write_text(
            '{"architectures":["Fixture"]}\n',
            encoding="utf-8",
        )
        (snapshot / "model.safetensors").write_bytes(weights)

    def _observation(self, rank: int) -> dict[str, object]:
        return model_library.inspect_home_acquisition_target(
            self.cache_roots[rank],
            model_id=self.model_id,
            revision=self.revision,
            required_content_bytes=self.manifest["total_bytes"],
            rank=rank,
            node_id=self.node_ids[rank],
            hf_cli="hf",
        )

    def _write_observations(self, *, most_free_rank: int = 1) -> None:
        for rank in range(len(self.node_ids)):
            observation = self._observation(rank)
            observation["available_bytes"] = 20 * 1024**3 + (
                1024**3 if rank == most_free_rank else 0
            )
            observation["eligible"] = True
            (self.observations / f"rank-{rank}.json").write_text(
                json.dumps(observation),
                encoding="utf-8",
            )

    def _plan(
        self,
        *,
        node_selector: str = "",
        serving_nodes: int = 2,
    ) -> dict[str, object]:
        return model_library.plan_home_acquisition(
            identity_plan=self.identity_plan,
            topology_file=self.topology_path,
            topology_id=self.topology["topology_id"],
            observations_dir=self.observations,
            serving_nodes=serving_nodes,
            node_selector=node_selector,
        )

    def _stage_download(self, plan: dict[str, object]) -> pathlib.Path:
        target = plan["target"]
        created = model_library.create_home_acquisition_staging(
            plan,
            rank=target["rank"],
            node_id=target["node_id"],
        )
        staging = pathlib.Path(created["staging_root"])
        self._write_hub(
            staging / model_library.model_id_to_hub_dirname(self.model_id)
        )
        return staging

    def test_default_selects_eligible_rank_with_most_free_space(self) -> None:
        plan = self._plan()
        self.assertEqual(plan["selection"], "most-free-space")
        self.assertEqual(plan["target"]["rank"], 1)

    def test_node_override_is_exact_and_visible(self) -> None:
        plan = self._plan(node_selector="0")
        self.assertEqual(plan["selection"], "operator-override")
        self.assertEqual(plan["target"]["node_id"], self.node_ids[0])
        with self.assertRaisesRegex(model_library.ModelLibraryError, "exactly one"):
            self._plan(node_selector="missing")

    def test_one_node_profile_automatically_selects_any_confirmed_rank(self) -> None:
        plan = self._plan(serving_nodes=1)
        self.assertEqual(plan["serving_ranks"], [1])
        self.assertEqual(plan["target"]["rank"], 1)

    def test_one_node_profile_accepts_explicit_remote_placement(self) -> None:
        plan = self._plan(node_selector="2", serving_nodes=1)
        self.assertEqual(plan["selection"], "operator-override")
        self.assertEqual(plan["serving_ranks"], [2])
        self.assertEqual(plan["target"]["node_id"], self.node_ids[2])

    def test_multi_node_home_must_remain_in_contiguous_serving_geometry(self) -> None:
        with self.assertRaisesRegex(model_library.ModelLibraryError, "serving geometry"):
            self._plan(node_selector="2", serving_nodes=2)

    def test_existing_repository_anywhere_blocks_duplicate_home(self) -> None:
        occupied = (
            self.cache_roots[0]
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        self._write_hub(occupied)
        self._write_observations()
        with self.assertRaisesRegex(model_library.ModelLibraryError, "already exists"):
            self._plan()

    def test_ineligible_override_fails_without_fallback(self) -> None:
        observation = self._observation(0)
        observation.update(
            {"eligible": False, "hf_cli": None, "detail": "CLI unavailable"}
        )
        (self.observations / "rank-0.json").write_text(
            json.dumps(observation), encoding="utf-8"
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "not eligible"):
            self._plan(node_selector="0")

    def test_managed_hf_cli_venv_is_an_eligible_target_capability(self) -> None:
        observation = model_library.inspect_home_acquisition_target(
            self.cache_roots[2],
            model_id=self.model_id,
            revision=self.revision,
            required_content_bytes=self.manifest["total_bytes"],
            rank=2,
            node_id=self.node_ids[2],
            hf_cli="/home/fixture/.hf-cli/venv/bin/hf",
        )
        observation["available_bytes"] = 20 * 1024**3
        observation["eligible"] = True
        (self.observations / "rank-2.json").write_text(
            json.dumps(observation), encoding="utf-8"
        )
        plan = self._plan(node_selector="2", serving_nodes=1)
        self.assertEqual(
            plan["target"]["hf_cli"],
            "/home/fixture/.hf-cli/venv/bin/hf",
        )

    def test_unmanaged_absolute_hf_cli_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "CLI observation"
        ):
            model_library.inspect_home_acquisition_target(
                self.cache_roots[2],
                model_id=self.model_id,
                revision=self.revision,
                required_content_bytes=self.manifest["total_bytes"],
                rank=2,
                node_id=self.node_ids[2],
                hf_cli="/tmp/hf",
            )

    def test_symlinked_managed_hub_root_is_ineligible(self) -> None:
        self.cache_roots[0].mkdir()
        external = self.root / "external-hub"
        external.mkdir()
        (self.cache_roots[0] / "hub").symlink_to(external, target_is_directory=True)
        observation = self._observation(0)
        self.assertEqual(observation["target_state"], "invalid")
        self.assertFalse(observation["eligible"])

    def test_stale_rank_identity_is_rejected(self) -> None:
        observation = json.loads(
            (self.observations / "rank-1.json").read_text(encoding="utf-8")
        )
        observation["node_id"] = "old-node"
        (self.observations / "rank-1.json").write_text(
            json.dumps(observation), encoding="utf-8"
        )
        with self.assertRaisesRegex(model_library.ModelLibraryError, "stale"):
            self._plan()

    def test_publication_recheck_blocks_home_that_appeared_during_download(self) -> None:
        plan = self._plan(node_selector="0")
        self._write_hub(
            self.cache_roots[1]
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        self._write_observations()
        with self.assertRaisesRegex(model_library.ModelLibraryError, "appeared"):
            model_library.recheck_home_acquisition_publication(
                plan,
                topology_file=self.topology_path,
                topology_id=self.topology["topology_id"],
                observations_dir=self.observations,
            )

    def test_full_verification_precedes_atomic_publication(self) -> None:
        plan = self._plan(node_selector="0")
        staging = self._stage_download(plan)
        result = model_library.execute_home_acquisition(
            plan,
            identity_plan=self.identity_plan,
            staging_root=staging,
            rank=0,
            node_id=self.node_ids[0],
            workers=1,
        )
        target = pathlib.Path(plan["target"]["target_hub"])
        self.assertEqual(result["state"], "published")
        self.assertEqual(result["bytes_hashed"], self.manifest["total_bytes"])
        self.assertTrue(target.is_dir())
        self.assertFalse(staging.exists())
        self.assertFalse(result["catalog_refreshed"])

    def test_manifest_mismatch_never_publishes_and_owned_staging_cleans(self) -> None:
        plan = self._plan(node_selector="0")
        staging = self._stage_download(plan)
        weights = (
            staging
            / model_library.model_id_to_hub_dirname(self.model_id)
            / "snapshots"
            / self.revision
            / "model.safetensors"
        )
        weights.write_bytes(b"changed-content")
        with self.assertRaises(model_library.ModelLibraryError):
            model_library.execute_home_acquisition(
                plan,
                identity_plan=self.identity_plan,
                staging_root=staging,
                rank=0,
                node_id=self.node_ids[0],
                workers=1,
            )
        self.assertFalse(pathlib.Path(plan["target"]["target_hub"]).exists())
        cleanup = model_library.cleanup_home_acquisition_staging(
            plan,
            staging_root=staging,
            rank=0,
            node_id=self.node_ids[0],
        )
        self.assertEqual(cleanup["state"], "staging-removed")

    def test_plan_tamper_and_foreign_staging_are_rejected(self) -> None:
        plan = self._plan(node_selector="0")
        tampered = dict(plan)
        tampered["revision"] = "b" * 40
        with self.assertRaisesRegex(model_library.ModelLibraryError, "identity mismatch"):
            model_library.validate_home_acquisition_plan(tampered)
        foreign = self.root / "foreign"
        foreign.mkdir()
        with self.assertRaisesRegex(model_library.ModelLibraryError, "outside"):
            model_library.cleanup_home_acquisition_staging(
                plan,
                staging_root=foreign,
                rank=0,
                node_id=self.node_ids[0],
            )

    def test_target_race_blocks_publication_without_overwrite(self) -> None:
        plan = self._plan(node_selector="0")
        staging = self._stage_download(plan)
        target = pathlib.Path(plan["target"]["target_hub"])
        target.mkdir(parents=True)
        sentinel = target / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        with self.assertRaisesRegex(model_library.ModelLibraryError, "appeared"):
            model_library.execute_home_acquisition(
                plan,
                identity_plan=self.identity_plan,
                staging_root=staging,
                rank=0,
                node_id=self.node_ids[0],
                workers=1,
            )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")


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

    def _seal(
        self,
        *,
        profile: str | None = None,
        model_id: str | None = None,
        snapshot_revision: str | None = None,
        manifest_id: str | None = None,
    ) -> dict[str, object]:
        seal: dict[str, object] = {
            "schema_version": 1,
            "kind": "pulsar-expected-model-seal",
            "profile": profile or self.profile,
            "model_id": model_id or self.model_id,
            "revision_kind": "huggingface-commit",
            "snapshot_revision": snapshot_revision or self.commit,
            "manifest": {
                "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
                "manifest_id": manifest_id or ("b" * 64),
            },
            "provenance": {
                "validation_bundle_id": "c" * 64,
                "issuer": "pulsar-lab-fixture",
                "issued_at": "2026-08-10T12:00:00Z",
                "evidence": ["results/model-library/fixture.json"],
            },
        }
        seal["seal_id"] = model_identity.expected_model_seal_id(seal)
        return model_identity.validate_expected_model_seal(
            seal, profile=seal["profile"], model_id=seal["model_id"]
        )

    def _registry_repo(self, release: dict[str, object]) -> pathlib.Path:
        repo = self.root / release["release_id"][:12]
        registry_root = repo / "models" / "model-serving-releases"
        registry_fixture.init_registry_root(registry_root)
        registry_fixture.write_release(registry_root, release)
        return repo

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
            first["kind"], model_library.HOME_ACQUISITION_PLAN_KIND
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
            source_attested.IDENTITY_CLASS_SOURCE_ATTESTED,
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

    def test_legacy_seal_keeps_complete_expected_manifest_contract(self) -> None:
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=self._source(),
            profile=self.profile,
            expected_seal=self._seal(),
        )
        self.assertEqual(
            identity["identity_class"],
            source_attested.IDENTITY_CLASS_LEGACY_SEAL,
        )
        self.assertEqual(
            identity["execution_contract"],
            source_attested.EXECUTION_CONTRACT_COMPLETE_MANIFEST,
        )
        self.assertEqual(identity["expected_manifest_id"], "b" * 64)
        self.assertIsNone(identity["model_serving_release_id"])

    def test_reviewed_release_precedes_seal_and_uses_manifest_id_only(
        self,
    ) -> None:
        release = release_fixture.build_release()
        repo = self._registry_repo(release)
        source = self._source(
            model_id="Fixture/Primary-Model",
            snapshot_revision="a" * 40,
        )
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source,
            profile=self.profile,
            model_serving_release_id=release["release_id"],
            repo_root=repo,
        )
        self.assertEqual(
            identity["identity_class"],
            source_attested.IDENTITY_CLASS_REVIEWED_RELEASE,
        )
        self.assertEqual(
            identity["execution_contract"],
            source_attested.EXECUTION_CONTRACT_MANIFEST_ID,
        )
        self.assertEqual(identity["expected_manifest_id"], "b" * 64)
        self.assertEqual(identity["model_serving_release_id"], release["release_id"])
        self.assertIsNone(identity["seal_id"])

    def test_release_and_seal_must_agree_and_then_keep_complete_manifest(
        self,
    ) -> None:
        release = release_fixture.build_release()
        repo = self._registry_repo(release)
        source = self._source(
            model_id="Fixture/Primary-Model",
            snapshot_revision="a" * 40,
        )
        seal = self._seal(
            model_id="Fixture/Primary-Model",
            snapshot_revision="a" * 40,
            manifest_id="b" * 64,
        )
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source,
            profile=self.profile,
            expected_seal=seal,
            model_serving_release_id=release["release_id"],
            repo_root=repo,
        )
        self.assertEqual(
            identity["identity_class"],
            source_attested.IDENTITY_CLASS_REVIEWED_RELEASE,
        )
        self.assertEqual(
            identity["execution_contract"],
            source_attested.EXECUTION_CONTRACT_COMPLETE_MANIFEST,
        )
        disagreeing = self._seal(
            model_id="Fixture/Primary-Model",
            snapshot_revision="a" * 40,
            manifest_id="d" * 64,
        )
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError, "disagree"
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=source,
                profile=self.profile,
                expected_seal=disagreeing,
                model_serving_release_id=release["release_id"],
                repo_root=repo,
            )

    def test_unavailable_or_unverified_binding_fails_without_fallback(
        self,
    ) -> None:
        empty = self.root / "empty-registry"
        registry_fixture.init_registry_root(
            empty / "models" / "model-serving-releases"
        )
        source = self._source()
        seal = self._seal()
        with self.assertRaisesRegex(
            source_attested.SourceAttestedAcquisitionError,
            "cannot be verified",
        ):
            source_attested.resolve_huggingface_v1_acquisition_identity(
                source=source,
                profile=self.profile,
                expected_seal=seal,
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
                expected_seal=seal,
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
