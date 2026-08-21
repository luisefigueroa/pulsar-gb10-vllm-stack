#!/usr/bin/env python3
"""Contracts for promotion-candidate model-library transfer identities and routes."""

from __future__ import annotations

import pathlib
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


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
                "identity_status": "legacy-unsealed",
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
