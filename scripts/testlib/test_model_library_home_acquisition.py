#!/usr/bin/env python3
"""Contracts for one-home reviewed model acquisition."""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402


class HomeAcquisitionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.profile = "sealed-fixture"
        self.model_id = "Fixture/Sealed-Model"
        self.revision = "a" * 40
        self.node_ids = ["node-a", "node-b"]
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
                    "ranks": [0, 1],
                    "rails": [
                        {
                            "network": "198.51.100.0/24",
                            "a": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": "198.51.100.10",
                            },
                            "b": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": "198.51.100.11",
                            },
                        }
                    ],
                }
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
        self.cache_roots = [self.root / f"cache-{rank}" for rank in range(2)]
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
        for rank in range(2):
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

    def test_home_must_participate_in_current_profile_geometry(self) -> None:
        plan = self._plan(serving_nodes=1)
        self.assertEqual(plan["serving_ranks"], [0])
        self.assertEqual(plan["target"]["rank"], 0)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "serving geometry"):
            self._plan(node_selector="1", serving_nodes=1)

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


if __name__ == "__main__":
    unittest.main()
