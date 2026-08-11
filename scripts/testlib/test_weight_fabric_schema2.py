#!/usr/bin/env python3
"""Focused contracts for weight-fabric schema 2 and its safety checks."""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import weight_fabric  # noqa: E402


def topology_fixture() -> dict[str, object]:
    return {
        "topology_id": "a" * 64,
        "nodes": [
            {
                "rank": 0,
                "node_id": "node-0",
                "hostname": "owner",
                "ssh_host": "local",
                "control": {"ip": "192.0.2.10"},
            },
            {
                "rank": 1,
                "node_id": "node-1",
                "hostname": "client",
                "ssh_host": "client.test",
                "control": {"ip": "192.0.2.11"},
            },
        ],
        "links": [
            {
                "ranks": [0, 1],
                "rails": [
                    {
                        "network": "10.10.1.0/24",
                        "a": {
                            "ip": "10.10.1.1",
                            "hca": "owner-hca",
                            "netdev": "owner-data",
                        },
                        "b": {
                            "ip": "10.10.1.2",
                            "hca": "client-hca",
                            "netdev": "client-data",
                        },
                    }
                ],
            }
        ],
    }


class ConfigurationContracts(unittest.TestCase):
    def test_schema2_is_exact_and_schema1_is_teardown_only(self) -> None:
        topology = topology_fixture()
        arguments = {
            "topology": topology,
            "profile": "small-2node",
            "model": "Example/Small",
            "nodes": 2,
            "storage_nodes": 2,
            "owner_selector": "node-0",
            "cache_root": "/srv/huggingface",
            "mount_root": "/mnt/pulsar-weight-fabric",
            "port": 20049,
            "rail_index": 0,
        }
        config = weight_fabric.build_configuration(**arguments)
        repository = "hub/models--Example--Small"
        synthetic = "/mnt/pulsar-weight-fabric/small-2node-aaaaaaaaaaaa"

        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(
            config["transport"]["export_scope"], "model-repository"
        )
        self.assertEqual(
            config["transport"]["export_path"],
            f"/srv/huggingface/{repository}",
        )
        self.assertEqual(
            config["transport"]["mount_path"],
            f"{synthetic}/{repository}",
        )
        self.assertEqual(config["ranks"][0]["cache_root"], "/srv/huggingface")
        self.assertEqual(config["ranks"][1]["cache_root"], synthetic)
        self.assertEqual(
            config["integrity"]["manifest_relative_path"],
            f"{repository}/.pulsar/manifests/small-2node.manifest.json",
        )
        weight_fabric.validate_configuration(config, topology)

        legacy = weight_fabric.build_legacy_configuration(**arguments)
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "teardown-only"
        ):
            weight_fabric.validate_configuration(legacy, topology)
        weight_fabric.validate_configuration(
            legacy, topology, allow_legacy_teardown=True
        )


class RepositoryAccessContracts(unittest.TestCase):
    @unittest.skipIf(
        os.getuid() == 0,
        "a root-owned fixture must be rejected by the production contract",
    )
    def test_access_readability_and_link_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = pathlib.Path(temporary)
            repository = base / "repository"
            blobs = repository / "blobs"
            snapshots = repository / "snapshots" / "revision"
            snapshots.mkdir(parents=True)
            blobs.mkdir()
            (blobs / "weight").write_bytes(b"weight")
            (snapshots / "weight").symlink_to("../../blobs/weight")

            result = weight_fabric.validate_repository_access(str(repository))
            self.assertEqual(result["state"], "ok")
            self.assertEqual(result["symlinks_checked"], 1)

            outside = base / "outside"
            outside.write_bytes(b"outside")
            escape = snapshots / "escape"
            escape.symlink_to(outside)
            with self.assertRaisesRegex(
                weight_fabric.WeightFabricError, "link escapes repository"
            ):
                weight_fabric.validate_repository_access(str(repository))
            escape.unlink()

            (blobs / "weight").chmod(0)
            with self.assertRaisesRegex(
                weight_fabric.WeightFabricError, "cannot read"
            ):
                weight_fabric.validate_repository_access(str(repository))


class ExportScopeContracts(unittest.TestCase):
    export_path = "/srv/huggingface/hub/models--Example--Small"
    export_file = "/etc/exports.d/pulsar-weight-fabric-aaaaaaaaaaaa.exports"
    client = "10.10.1.2"
    options = (
        "ro,sync,insecure,root_squash,anonuid=1000,anongid=1000,"
        "no_subtree_check"
    )

    def validate(
        self,
        document: str,
        files: list[str] | None = None,
        **overrides: object,
    ) -> dict[str, object]:
        arguments = {
            "expected_export_file": self.export_file,
            "export_path": self.export_path,
            "clients": [self.client],
            "anonuid": 1000,
            "anongid": 1000,
            "require_active": True,
            "require_export_file": True,
        }
        arguments.update(overrides)
        return weight_fabric.validate_export_scope(
            document,
            files if files is not None else [self.export_file],
            **arguments,
        )

    def test_exact_owned_policy_is_accepted(self) -> None:
        result = self.validate(
            f'"{self.export_path}" {self.client}({self.options})\n'
        )
        self.assertEqual(result, {"state": "ok", "active": True})

    def test_kernel_active_export_view_is_accepted(self) -> None:
        kernel_options = (
            f"{self.options},wdelay,nocrossmnt,acl,no_pnfs,"
            "uuid=00000000:11111111:22222222:33333333,sec=1"
        )
        document = (
            "# Version 1.1\n"
            "# Path Client(Flags) # IPs\n"
            f"{self.export_path}\t{self.client}({kernel_options})\n"
        )
        self.assertEqual(
            self.validate(document), {"state": "ok", "active": True}
        )

    def test_broader_other_and_unowned_exports_fail_closed(self) -> None:
        broader = f'/srv/huggingface {self.client}({self.options})\n'
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "broader active export"
        ):
            self.validate(broader)

        exact = f'{self.export_path} {self.client}({self.options})\n'
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "another Pulsar export file"
        ):
            self.validate(
                exact,
                files=[self.export_file, "/etc/exports.d/pulsar-other.exports"],
            )
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "not owned"
        ):
            self.validate(
                exact,
                files=[],
                require_active=False,
                require_export_file=False,
            )
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "exists but is inactive"
        ):
            self.validate(
                "",
                files=[self.export_file],
                require_active=False,
                require_export_file=False,
            )

    def test_wrong_mapping_and_incomplete_teardown_fail_closed(self) -> None:
        wrong = f'{self.export_path} {self.client}({self.options})\n'
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "active policy differs"
        ):
            self.validate(wrong, anonuid=1001)
        with self.assertRaisesRegex(
            weight_fabric.WeightFabricError, "still active"
        ):
            self.validate(
                wrong,
                files=[],
                require_active=False,
                require_export_file=False,
                forbid_active=True,
                forbid_export_file=True,
            )
        result = self.validate(
            "",
            files=[],
            require_active=False,
            require_export_file=False,
            forbid_active=True,
            forbid_export_file=True,
        )
        self.assertEqual(result, {"state": "ok", "active": False})


if __name__ == "__main__":
    unittest.main()
