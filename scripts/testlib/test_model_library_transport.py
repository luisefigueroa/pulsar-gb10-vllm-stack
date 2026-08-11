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
            (None, None, ("copy", "ssh-control")),
            ("copy", None, ("copy", "ssh-control")),
            ("fabric", None, ("fabric", "nfs-rdma")),
            (None, "ssh-control", ("copy", "ssh-control")),
            (None, "ssh-roce", ("copy", "ssh-roce")),
            (None, "nfs-rdma", ("fabric", "nfs-rdma")),
            ("copy", "ssh-roce", ("copy", "ssh-roce")),
            ("fabric", "nfs-rdma", ("fabric", "nfs-rdma")),
        )
        for backend, transport, expected in cases:
            with self.subTest(backend=backend, transport=transport):
                self.assertEqual(
                    model_library.resolve_activate_transport(
                        backend,
                        transport,
                    ),
                    expected,
                )

    def test_transport_backend_conflicts_fail_closed(self) -> None:
        for backend, transport in (
            ("fabric", "ssh-control"),
            ("fabric", "ssh-roce"),
            ("copy", "nfs-rdma"),
        ):
            with self.subTest(backend=backend, transport=transport):
                with self.assertRaisesRegex(
                    model_library.ModelLibraryError,
                    "requires backend",
                ):
                    model_library.resolve_activate_transport(
                        backend,
                        transport,
                    )

        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "not supported",
        ):
            model_library.resolve_activate_transport(None, "automatic")

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
