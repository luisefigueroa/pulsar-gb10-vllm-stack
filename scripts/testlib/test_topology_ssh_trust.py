#!/usr/bin/env python3
"""Deterministic contracts for topology-bound SSH identity."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import topology_manifest as manifest  # noqa: E402
import topology_ssh_trust as trust  # noqa: E402


def public_key(algorithm: str, seed: str) -> str:
    algorithm_bytes = algorithm.encode("ascii")
    blob = len(algorithm_bytes).to_bytes(4, "big") + algorithm_bytes
    blob += seed.encode("ascii")
    return base64.b64encode(blob).decode("ascii")


def key(seed: str, algorithm: str = "ssh-ed25519") -> dict[str, str]:
    value = public_key(algorithm, seed)
    return {
        "algorithm": algorithm,
        "fingerprint": manifest.host_key_fingerprint(value),
        "public_key": value,
    }


def schema1_topology() -> dict:
    nodes = [
        {
            "rank": 0,
            "node_id": "node-zero-identity",
            "hostname": "fixture-zero",
            "ssh_host": "local",
            "control": {"interface": "lan0", "ip": "192.0.2.10"},
            "gpu": "NVIDIA GB10",
            "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.10/24"]}],
        },
        {
            "rank": 1,
            "node_id": "node-one-identity",
            "hostname": "fixture-one",
            "ssh_host": "fixture-one.local",
            "control": {"interface": "lan0", "ip": "192.0.2.11"},
            "gpu": "NVIDIA GB10",
            "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.11/24"]}],
        },
    ]
    topology = {
        "schema_version": 1,
        "generated_at": "2026-08-10T00:00:00+00:00",
        "nodes": nodes,
        "links": [{
            "ranks": [0, 1],
            "rails": [{
                "network": "198.51.100.0/24",
                "a": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.10"},
                "b": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.11"},
            }],
        }],
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": True,
            "connectivity_verified": True,
            "min_rails_per_pair": 1,
        },
    }
    topology["topology_id"] = manifest.topology_digest(topology)
    manifest.validate_manifest(topology, require_verified=True)
    return topology


def probe_for(node: dict, host_key: dict) -> dict:
    return {
        "probe_schema_version": 2,
        "local": node["rank"] == 0,
        "ssh_host": "local" if node["rank"] == 0 else node["ssh_host"],
        "node_id": node["node_id"],
        "hostname": node["hostname"],
        "arch": "aarch64",
        "gpu": "NVIDIA GB10",
        "docker_ok": True,
        "docker_nvidia": True,
        "control": node["control"],
        "rdma": node["rdma"],
        "qualified": True,
        "reject_reasons": [],
        "ssh_host_keys": [host_key],
    }


def enroll(topology: dict | None = None) -> tuple[dict, list[dict]]:
    topology = topology or schema1_topology()
    probes = [
        probe_for(topology["nodes"][0], key("zero")),
        probe_for(topology["nodes"][1], key("one")),
    ]
    return manifest.enroll_ssh_trust(topology, probes), probes


class ManifestContracts(unittest.TestCase):
    def test_explicit_enrollment_is_only_schema2_path(self) -> None:
        topology = schema1_topology()
        probes = [
            probe_for(topology["nodes"][0], key("zero")),
            probe_for(topology["nodes"][1], key("one")),
        ]
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index, probe in enumerate(probes):
                path = pathlib.Path(directory) / f"probe-{index}.json"
                path.write_text(json.dumps(probe), encoding="utf-8")
                paths.append(str(path))
            discovery = manifest.assemble(paths, None)
        self.assertEqual(discovery["topology"]["schema_version"], 1)
        self.assertNotIn("ssh_host_keys", discovery["topology"]["nodes"][0])
        enrolled, _ = enroll(topology)
        self.assertEqual(enrolled["schema_version"], 2)
        self.assertTrue(enrolled["validation"]["ssh_identity_enrolled"])
        self.assertNotEqual(enrolled["topology_id"], topology["topology_id"])
        manifest.validate_manifest(enrolled, require_verified=True, require_ssh_trust=True)

    def test_shared_key_across_nodes_is_rejected(self) -> None:
        topology = schema1_topology()
        shared = key("shared")
        probes = [probe_for(node, shared) for node in topology["nodes"]]
        with self.assertRaisesRegex(manifest.TopologyError, "shared by multiple nodes"):
            manifest.enroll_ssh_trust(topology, probes)

    def test_rotation_requires_explicit_acceptance(self) -> None:
        enrolled, probes = enroll()
        rotated = json.loads(json.dumps(probes))
        rotated[1]["ssh_host_keys"] = [key("one-rotated")]
        with self.assertRaisesRegex(manifest.TopologyError, "keys changed"):
            manifest.enroll_ssh_trust(enrolled, rotated)
        accepted = manifest.enroll_ssh_trust(enrolled, rotated, allow_key_change=True)
        self.assertNotEqual(accepted["topology_id"], enrolled["topology_id"])

    def test_alias_control_mapping_and_config_staleness(self) -> None:
        enrolled, _ = enroll()
        with tempfile.TemporaryDirectory() as directory:
            topology_path = pathlib.Path(directory) / "topology.json"
            config_path = pathlib.Path(directory) / "ssh-config"
            manifest.write_trust_bundle(enrolled, str(topology_path), str(config_path))
            config = config_path.read_text(encoding="utf-8")
            self.assertIn("Host fixture-one.local", config)
            self.assertIn("HostName 192.0.2.11", config)
            self.assertIn("HostKeyAlias fixture-one.local", config)
            self.assertIn("StrictHostKeyChecking yes", config)
            self.assertIn("KnownHostsCommand", config)
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            manifest.validate_ssh_config_file(enrolled, str(config_path), topology_path=str(topology_path))
            config_path.write_text(config + "# stale\n", encoding="utf-8")
            with self.assertRaisesRegex(manifest.TopologyError, "stale"):
                manifest.validate_ssh_config_file(enrolled, str(config_path), topology_path=str(topology_path))

    def test_known_hosts_uses_alias_not_transport_ip(self) -> None:
        enrolled, _ = enroll()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            manifest.known_hosts(enrolled, "fixture-one.local")
        line = output.getvalue().strip()
        self.assertTrue(line.startswith("fixture-one.local ssh-ed25519 "))
        self.assertNotIn("192.0.2.11", line)


class DiagnosticContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.enrolled, _ = enroll()
        self.expected_zero = {item["fingerprint"] for item in self.enrolled["nodes"][0]["ssh_host_keys"]}
        self.expected_one = {item["fingerprint"] for item in self.enrolled["nodes"][1]["ssh_host_keys"]}
        self.owners = {
            **{item: 0 for item in self.expected_zero},
            **{item: 1 for item in self.expected_one},
        }

    def test_wrong_node_address_collision_classification(self) -> None:
        state, detail = trust.classify_failed_connection(
            expected=self.expected_one,
            observed=self.expected_zero,
            owners=self.owners,
            rank=1,
            stderr="Host key verification failed.",
            endpoint_kind="roce",
            endpoint="198.51.100.11",
        )
        self.assertEqual(state, "wrong-node-address-collision")
        self.assertIn("rank(s) 0", detail)

    def test_unenrolled_key_is_rotation_or_replacement(self) -> None:
        state, _ = trust.classify_failed_connection(
            expected=self.expected_one,
            observed={key("unknown")["fingerprint"]},
            owners=self.owners,
            rank=1,
            stderr="Host key verification failed.",
            endpoint_kind="roce",
            endpoint="198.51.100.11",
        )
        self.assertEqual(state, "host-key-changed")

    def test_stale_control_endpoint_resolution(self) -> None:
        state, detail = trust.classify_failed_connection(
            expected=self.expected_one,
            observed=set(),
            owners=self.owners,
            rank=1,
            stderr="connect: No route to host",
            endpoint_kind="control",
            endpoint="192.0.2.11",
            resolved=["192.0.2.99"],
        )
        self.assertEqual(state, "stale-control-endpoint")
        self.assertIn("192.0.2.99", detail)

    def test_endpoint_plan_covers_control_and_roce(self) -> None:
        plan = trust.endpoint_plan(self.enrolled, self.enrolled["nodes"][1])
        self.assertEqual(
            {(item["kind"], item["endpoint"]) for item in plan},
            {("control", "192.0.2.11"), ("roce", "198.51.100.11")},
        )
        self.assertTrue(all(item["source_rank"] == 0 for item in plan))

    def test_remote_probe_keeps_alias_while_overriding_transport(self) -> None:
        node = self.enrolled["nodes"][1]
        wrong_node = self.enrolled["nodes"][0]
        response = {"node_id": wrong_node["node_id"], "ssh_host_keys": wrong_node["ssh_host_keys"]}
        fingerprint = wrong_node["ssh_host_keys"][0]["fingerprint"]
        with tempfile.TemporaryDirectory() as directory:
            directory_path = pathlib.Path(directory)
            log_path = directory_path / "argv.json"
            mock_path = directory_path / "ssh-mock.py"
            mock_source = f"""#!/usr/bin/env python3
import json, pathlib, sys
pathlib.Path({str(log_path)!r}).write_text(json.dumps(sys.argv[1:]))
sys.stderr.write({('debug1: Server host key: ssh-ed25519 ' + fingerprint + chr(10))!r})
print({json.dumps(json.dumps(response))})
"""
            mock_path.write_text(mock_source, encoding="utf-8")
            mock_path.chmod(0o755)
            result = trust.check_remote_endpoint(
                node=node,
                endpoint={"kind": "roce", "interface": "fabric0", "endpoint": "198.51.100.11"},
                config_path=directory_path / "unused-config",
                probe_path=SCRIPTS / "probe-node.py",
                ssh_bin=str(mock_path),
                timeout=2,
                owners=self.owners,
                node_id_owners={item["node_id"]: item["rank"] for item in self.enrolled["nodes"]},
                source_node={"rank": 2, "ssh_host": "fixture-jump.local"},
            )
            argv = json.loads(log_path.read_text(encoding="utf-8"))
        self.assertEqual(result["state"], "wrong-node-address-collision")
        self.assertIn("HostName=198.51.100.11", argv)
        self.assertIn("HostKeyAlias=fixture-one.local", argv)
        self.assertIn("fixture-one.local", argv)
        self.assertIn("-J", argv)
        self.assertIn("fixture-jump.local", argv)
        self.assertNotIn("198.51.100.11", [argv[-2], argv[-1]])


class ShellBoundaryContracts(unittest.TestCase):
    def test_enrollment_loop_isolates_ssh_stdin_and_cleanup_scope(self) -> None:
        source = (SCRIPTS / "topology-ssh-trust.sh").read_text(encoding="utf-8")
        self.assertIn(
            '-- "$alias" "$remote_query" </dev/null 2>/dev/null',
            source,
        )
        self.assertIn('TRUST_TMPDIR=""', source)
        self.assertIn("trap cleanup_trust_tmpdir EXIT", source)
        self.assertNotIn("trap 'rm -rf", source)

    def test_schema2_loader_installs_generated_config(self) -> None:
        enrolled, _ = enroll()
        with tempfile.TemporaryDirectory() as directory:
            topology_path = pathlib.Path(directory) / "topology.json"
            config_path = pathlib.Path(directory) / "ssh-config"
            manifest.write_trust_bundle(enrolled, str(topology_path), str(config_path))
            env = os.environ.copy()
            env.update(CLUSTER_TOPOLOGY_FILE=str(topology_path), CLUSTER_SSH_CONFIG_FILE=str(config_path))
            command = r"""
set -euo pipefail
. scripts/lib.sh
load_cluster_topology
printf '%s\n' "$CLUSTER_TOPOLOGY_SCHEMA|$CLUSTER_TOPOLOGY_SSH_TRUSTED"
printf '%s\n' "${PULSAR_SSH_OPTS[@]}"
"""
            proc = subprocess.run(
                ["bash", "-c", command],
                cwd=REPO,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("2|1", proc.stdout.splitlines())
        self.assertIn(str(config_path), proc.stdout.splitlines())
        self.assertIn("StrictHostKeyChecking=yes", proc.stdout.splitlines())


if __name__ == "__main__":
    unittest.main(verbosity=2)
