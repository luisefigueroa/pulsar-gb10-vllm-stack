#!/usr/bin/env python3
"""Adversarial contracts for the publishable privacy scanner."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "check_publishable_privacy.py"
SPEC = importlib.util.spec_from_file_location("check_publishable_privacy", MODULE_PATH)
assert SPEC and SPEC.loader
privacy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = privacy
SPEC.loader.exec_module(privacy)


class PublishablePrivacyTests(unittest.TestCase):
    def rules(self, path: str, value: object) -> set[str]:
        data = (
            json.dumps(value, sort_keys=True).encode("utf-8")
            if path.endswith(".json")
            else str(value).encode("utf-8")
        )
        return {finding.rule for finding in privacy.scan_bytes(path, data)}

    def test_allows_generic_roles_documentation_addresses_and_versions(self) -> None:
        value = {
            "rank": 0,
            "physical_system": "Node A",
            "api_base": "http://127.0.0.1:8000",
            "example": "192.0.2.42",
            "example_v6": "2001:db8::42",
            "privacy": {"node_and_topology_ids_omitted": True},
            "environment": "nvidia-curand==10.4.0.35",
        }
        self.assertEqual(self.rules("results/safe.json", value), set())

    def test_rejects_sensitive_structured_identity_fields(self) -> None:
        cases = {
            "topology_id": "topology-46c1",
            "node_id": "node-001",
            "hostname": "compute-a",
            "ssh_alias": "site-rank-a",
            "control_ip": "10.1.2.3",
            "host_key_fingerprint": "SHA256:abcdefghijklmnopqrstuvwx1234567890ABCDE",
            "interface_name": "rocep1s0f0",
            "filesystem_id": "fs-42",
            "mac_address": "00:11:22:33:44:55",
            "gpu_uuid": "GPU-12345678-abcd-1234-abcd-1234567890ab",
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                self.assertIn(
                    "sensitive-json-field",
                    self.rules("results/private.json", {key: value}),
                )

    def test_explicit_redaction_is_allowed(self) -> None:
        value = {
            "topology_id": "<redacted>",
            "node_id": None,
            "ssh_alias": "omitted",
            "control_ip": "private",
        }
        self.assertEqual(self.rules("results/redacted.json", value), set())

    def test_rejects_hostnames_addresses_and_ssh_material_in_text(self) -> None:
        cases = {
            "stable-hostname": "rank 0 ran on dgx-spark-7.local",
            "local-hostname": "ssh host: lab-node.internal",
            "network-address": "control IP: 10.23.4.5",
            "network-address-v6": "control IP: fd00::42",
            "mac-address": "adapter 00:11:22:33:44:55",
            "gpu-uuid": "GPU-12345678-abcd-1234-abcd-1234567890ab",
            "runtime-hostname": "Rank 0 on cluster-private-a device 0",
            "site-identity-value": "hostname: cluster-private-a",
            "ssh-public-key": "ssh-ed25519 " + ("A" * 48),
            "ssh-fingerprint": "host key SHA256:" + ("A" * 32),
            "private-key": "-----BEGIN OPENSSH PRIVATE KEY-----",
            "hashed-known-host": "|1|" + ("A" * 20) + "|" + ("B" * 20),
            "site-home-path": "/home/alice/private/results.json",
        }
        for label, text in cases.items():
            expected = "network-address" if label == "network-address-v6" else label
            with self.subTest(rule=label):
                self.assertIn(expected, self.rules("results/private.log", text))

    def test_does_not_treat_ordinary_host_prose_as_identity_assignment(self) -> None:
        text = "Not required on the host: vLLM, Ray, or host NCCL."
        self.assertEqual(self.rules("docs/safe.md", text), set())

    def test_rejects_bare_json_ip_values_without_sensitive_keys(self) -> None:
        self.assertIn(
            "network-address",
            self.rules("results/private.json", {"observation": "10.23.4.5"}),
        )
        self.assertIn(
            "network-address",
            self.rules("results/private.json", ["fd00::42"]),
        )

    def test_rejects_common_credential_shapes(self) -> None:
        cases = {
            "hugging-face-token": "hf_" + ("a" * 24),
            "github-token": "ghp_" + ("b" * 24),
            "openai-token": "sk-proj-" + ("c" * 24),
            "aws-access-key": "AKIA" + ("D" * 16),
            "google-api-key": "AIza" + ("e" * 32),
        }
        for expected, token in cases.items():
            with self.subTest(rule=expected):
                self.assertIn(expected, self.rules("results/token.txt", token))

    def test_rejects_sensitive_hostname_in_publishable_path(self) -> None:
        self.assertIn(
            "stable-hostname",
            self.rules("results/dgx-spark-9/report.json", {"ok": True}),
        )

    def test_staged_mode_reads_index_not_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Privacy Test"],
                check=True,
            )
            result = repo / "results" / "candidate.json"
            result.parent.mkdir()
            result.write_text('{"topology_id":"private-topology"}\n', encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "results/candidate.json"], check=True)
            result.write_text('{"status":"safe working tree"}\n', encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(repo),
                    "--staged",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("sensitive-json-field", proc.stderr)

            subprocess.run(["git", "-C", str(repo), "add", "results/candidate.json"], check=True)
            result.write_text('{"node_id":"private working tree"}\n', encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(repo),
                    "--staged",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_staged_mode_rejects_credentials_in_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            source = repo / "scripts" / "leak.sh"
            source.parent.mkdir()
            source.write_text("TOKEN=hf_abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "scripts/leak.sh"], check=True)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(repo),
                    "--staged",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("hugging-face-token", proc.stderr)

    def test_working_tree_scan_does_not_follow_publishable_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            link = repo / "docs" / "external.md"
            link.parent.mkdir()
            link.symlink_to("/home/alice/private/evidence.md")
            subprocess.run(["git", "-C", str(repo), "add", "docs/external.md"], check=True)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--repo-root",
                    str(repo),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("site-home-path", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
