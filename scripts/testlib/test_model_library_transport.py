#!/usr/bin/env python3
"""Contracts for promotion-candidate model-library transfer identities and routes."""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.testlib.release_spec_fixture_set import write_fixture_set  # noqa: E402


class ActivateTransportContracts(unittest.TestCase):
    def test_backend_compatibility_mapping_is_deterministic(self) -> None:
        cases = (
            (None, None, 1, ("copy", "ssh-control")),
            ("copy", None, 1, ("copy", "ssh-control")),
            (None, None, 2, ("copy", "ssh-roce")),
            ("copy", None, 2, ("copy", "ssh-roce")),
            (None, "ssh-control", 2, ("copy", "ssh-control")),
            (None, "ssh-roce", 2, ("copy", "ssh-roce")),
            ("copy", "ssh-roce", 2, ("copy", "ssh-roce")),
        )
        for backend, transport, nodes, expected in cases:
            with self.subTest(backend=backend, transport=transport, nodes=nodes):
                self.assertEqual(
                    model_library.resolve_activate_transport(
                        backend,
                        transport,
                        nodes=nodes,
                    ),
                    expected,
                )

    def test_one_rank_rejects_ssh_roce(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "no non-home transfer",
        ):
            model_library.resolve_activate_transport(
                "copy",
                "ssh-roce",
                nodes=1,
            )

    def test_missing_catalog_classifies_as_refresh_not_prepare(self) -> None:
        report = model_library.classify_library_readiness(
            profile="deepseek-v4-flash",
            catalog_path=pathlib.Path("/no/such/catalog.json"),
            topology_id="a" * 64,
            models_dir=REPO_ROOT / "models",
        )
        self.assertEqual(report["reason"], "catalog-missing")
        self.assertIn("catalog refresh", report["remediation"])
        self.assertNotIn("prepare", report["remediation"])

    def test_retired_fabric_modes_fail_closed(self) -> None:
        for backend, transport in (
            ("fabric", None),
            ("fabric", "ssh-roce"),
            (None, "nfs-rdma"),
            ("copy", "nfs-rdma"),
        ):
            with self.subTest(backend=backend, transport=transport):
                with self.assertRaisesRegex(
                    model_library.ModelLibraryError,
                    "not supported",
                ):
                    model_library.resolve_activate_transport(
                        backend,
                        transport,
                        nodes=2,
                    )

        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "not supported",
        ):
            model_library.resolve_activate_transport(None, "automatic", nodes=2)

    def test_hot_stamp_records_transfer_provenance(self) -> None:
        manifest = {
            "schema_version": 1,
            "kind": model_library.SNAPSHOT_MANIFEST_KIND,
            "model_id": "Org/Fixture",
            "snapshot_revision": "revision",
            "files": [
                {
                    "path": "model.safetensors",
                    "size": 123,
                    "sha256": "a" * 64,
                }
            ],
            "file_count": 1,
            "total_bytes": 123,
        }
        manifest["manifest_id"] = model_library.snapshot_manifest_id(manifest)
        stamp = model_library.build_hot_stamp(
            profile="fixture-2node",
            model_id="Org/Fixture",
            identity_key="Org/Fixture@revision",
            revision="revision",
            topology_id="topology",
            home_node_id="node-a",
            content_id="content",
            content_digest=manifest["manifest_id"],
            integrity_manifest=manifest,
            validation={
                "identity_status": "receipt-occupancy",
                "expected_seal": None,
                "observed_seal": model_library.observed_model_seal_projection(
                    manifest
                ),
            },
            backend="copy",
            bytes_logical=123,
            transport="ssh-roce",
        )
        self.assertEqual(stamp["schema_version"], 3)
        self.assertEqual(stamp["backend"], "copy")
        self.assertEqual(stamp["transport"], "ssh-roce")
        self.assertEqual(
            stamp["integrity"]["manifest"]["manifest_id"],
            manifest["manifest_id"],
        )


class LibraryReadinessClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.catalog_path = self.root / "catalog.json"
        self.topology_id = "a" * 64
        # Profiles are released specs: a fixture release set stands in for the
        # site's releases/, and models_dir (drafts) stays empty.
        ids = write_fixture_set(self.root / "spec-fixture")
        previous = os.environ.get("PULSAR_RELEASES_ROOT")
        os.environ["PULSAR_RELEASES_ROOT"] = str(self.root / "spec-fixture" / "releases")
        self.addCleanup(lambda: os.environ.update({"PULSAR_RELEASES_ROOT": previous}) if previous is not None else os.environ.pop("PULSAR_RELEASES_ROOT", None))
        self.NEMOTRON = ids["one_node"]["spec_id"]
        self.QWEN = ids["one_node_qwen"]["spec_id"]
        self.models_dir = self.root / "drafts"
        self.models_dir.mkdir()

    def _write(self, catalog: dict[str, object]) -> None:
        self.catalog_path.write_text(
            json.dumps(catalog, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _classify(
        self,
        profile: str,
        *,
        selected_rank: int | None = None,
        selected_node_id: str | None = None,
    ) -> dict[str, object]:
        return model_library.classify_library_readiness(
            profile=profile,
            catalog_path=self.catalog_path,
            topology_id=self.topology_id,
            models_dir=self.models_dir,
            selected_rank=selected_rank,
            selected_node_id=selected_node_id,
        )

    def _home(self, profile_info: dict[str, object], *, rank: int) -> dict[str, object]:
        model_id = str(profile_info["model_id"])
        revision = "b" * 40
        return {
            "rank": rank,
            "node_id": f"node-{rank}",
            "hostname": f"fixture-{rank}",
            "ssh_host": f"fixture-{rank}",
            "cache_root": f"/cache/{rank}",
            "hub_path": f"/cache/{rank}/hub/models--Example--Model",
            "model_id": model_id,
            "revision": revision,
            "identity_key": f"{model_id}@{revision}",
            "state": "complete",
            "home_class": "occupancy",
            "occupancy": True,
            "active": True,
            "bytes": 1024,
        }

    def test_unsealed_missing_home_names_plan_then_yes(self) -> None:
        self._write(
            model_library.build_catalog(
                topology_id=self.topology_id,
                homes=[],
                profiles=[],
            )
        )
        report = self._classify(self.NEMOTRON)
        self.assertEqual(report["reason"], "no-home")
        self.assertIn(f"home add {self.NEMOTRON} --revision <selector> --plan", report["remediation"])
        self.assertIn("--yes", report["remediation"])
        self.assertNotEqual(
            report["remediation"],
            f"scripts/model-library.sh home add {self.NEMOTRON} --yes",
        )

    def test_sealed_missing_home_names_home_add_yes(self) -> None:
        self._write(
            model_library.build_catalog(
                topology_id=self.topology_id,
                homes=[],
                profiles=[],
            )
        )
        report = self._classify(self.QWEN)
        self.assertEqual(report["reason"], "no-home")
        self.assertIn(
            f"home add {self.QWEN} --revision <selector> --plan",
            report["remediation"],
        )
        self.assertIn("--yes", report["remediation"])
        self.assertNotEqual(
            report["remediation"],
            f"scripts/model-library.sh home add {self.QWEN} --yes",
        )

    def test_unreceipted_tree_names_cleanup_instead_of_refresh(self) -> None:
        profile_info = model_library.load_hf_profile(self.models_dir, self.NEMOTRON)
        home = self._home(profile_info, rank=0)
        home["home_class"] = "unbound-complete"
        home["occupancy"] = False
        home["unbound_reason"] = "missing-receipt"
        self._write(
            model_library.build_catalog(
                topology_id=self.topology_id,
                homes=[home],
                profiles=[profile_info],
            )
        )

        report = self._classify(self.NEMOTRON)
        self.assertEqual(report["reason"], "unreceipted-tree")
        self.assertEqual(
            report["remediation"],
            "scripts/model-library.sh cleanup-recommend",
        )
        self.assertNotIn("catalog refresh", report["remediation"])

    def test_duplicate_homes_name_cleanup_recommend(self) -> None:
        profile_info = model_library.load_hf_profile(self.models_dir, self.NEMOTRON)
        self._write(
            model_library.build_catalog(
                topology_id=self.topology_id,
                homes=[
                    self._home(profile_info, rank=0),
                    self._home(profile_info, rank=1),
                ],
                profiles=[profile_info],
            )
        )
        report = self._classify(self.NEMOTRON)
        self.assertEqual(report["reason"], "duplicate-home")
        self.assertEqual(
            report["remediation"],
            "scripts/model-library.sh cleanup-recommend",
        )
        self.assertNotIn("catalog refresh", report["remediation"])

    def test_stale_primary_names_refresh_then_select(self) -> None:
        profile_info = model_library.load_hf_profile(self.models_dir, self.NEMOTRON)
        home = self._home(profile_info, rank=1)
        catalog = model_library.build_catalog(
            topology_id=self.topology_id,
            homes=[home],
            profiles=[profile_info],
            primary_selections=[
                {
                    "identity_key": str(home["identity_key"]),
                    "node_id": "node-missing",
                    "selected_at": "2026-08-12T00:00:00.000Z",
                }
            ],
        )
        self._write(catalog)
        report = self._classify(self.NEMOTRON)
        self.assertEqual(report["reason"], "catalog-stale")
        self.assertIn("catalog refresh", report["remediation"])
        self.assertIn(f"catalog primary set {self.NEMOTRON} --node RANK", report["remediation"])

    def test_ready_home_without_views_names_prepare(self) -> None:
        profile_info = model_library.load_hf_profile(self.models_dir, self.NEMOTRON)
        self._write(
            model_library.build_catalog(
                topology_id=self.topology_id,
                homes=[self._home(profile_info, rank=0)],
                profiles=[profile_info],
            )
        )
        report = self._classify(self.NEMOTRON)
        self.assertEqual(report["reason"], "views-missing")
        self.assertEqual(
            report["remediation"],
            f"scripts/model-library.sh prepare {self.NEMOTRON} --yes",
        )
        home_ok = self._classify(self.NEMOTRON, selected_rank=0, selected_node_id="node-0")
        self.assertEqual(home_ok["reason"], "views-missing")
        misplaced = self._classify(
            self.NEMOTRON, selected_rank=1, selected_node_id="node-1"
        )
        self.assertEqual(misplaced["reason"], "wrong-placement")
        self.assertIn("--node node-0", misplaced["remediation"])
        self.assertNotIn("prepare", misplaced["remediation"])
        self.assertIn("Do not prepare onto a non-home rank", misplaced["detail"])


class SshRoceRouteContracts(unittest.TestCase):
    def test_confirmed_route_is_accepted(self) -> None:
        report = model_library.validate_ssh_roce_route(
            [
                {
                    "dst": "10.0.0.2",
                    "dev": "enp1s0f0",
                    "prefsrc": "10.0.0.1",
                }
            ],
            remote_ip="10.0.0.2",
            expected_netdev="enp1s0f0",
            expected_source_ip="10.0.0.1",
        )
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["netdev"], "enp1s0f0")
        self.assertEqual(report["source_ip"], "10.0.0.1")

    def test_wrong_interface_or_source_fails_closed(self) -> None:
        for route, message in (
            (
                [{"dev": "mgmt0", "prefsrc": "192.168.1.10"}],
                "expected confirmed enp1s0f0",
            ),
            (
                [{"dev": "enp1s0f0", "prefsrc": "10.0.0.99"}],
                "expected confirmed 10.0.0.1",
            ),
            ([], "returned no routes"),
            ({}, "returned no routes"),
        ):
            with self.subTest(route=route):
                with self.assertRaisesRegex(
                    model_library.ModelLibraryError,
                    message,
                ):
                    model_library.validate_ssh_roce_route(
                        route,
                        remote_ip="10.0.0.2",
                        expected_netdev="enp1s0f0",
                        expected_source_ip="10.0.0.1",
                    )

    def test_route_src_field_is_supported(self) -> None:
        report = model_library.validate_ssh_roce_route(
            [{"dev": "enp1s0f0", "src": "10.0.0.1"}],
            remote_ip="10.0.0.2",
            expected_netdev="enp1s0f0",
            expected_source_ip="10.0.0.1",
        )
        self.assertEqual(report["state"], "ready")


if __name__ == "__main__":
    unittest.main()
