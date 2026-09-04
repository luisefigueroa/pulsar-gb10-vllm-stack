#!/usr/bin/env python3
"""Public catalog, health, and prepare contracts for unknown complete trees."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.testlib.release_spec_fixture_set import write_fixture_set  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402


class UnboundAdmissionPublicContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        fixture.write_cli_fixture(self.root)
        self.model_id = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
        ids = write_fixture_set(self.root / "spec-fixture")
        self.profile = ids["one_node"]["spec_id"]
        self.hub = (
            self.root
            / "cache"
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        fixture.write_snapshot_hub(self.hub)
        docker = self.root / "bin" / "docker-fixture"
        fixture.write_executable(
            docker,
            """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  info) exit 0 ;;
  ps) exit 0 ;;
  *) exit 2 ;;
esac
""",
        )
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.root / 'bin'}:{self.env.get('PATH', '')}",
                "CLUSTER_TOPOLOGY_FILE": str(self.root / "topology.json"),
                "HF_CACHE": str(self.root / "cache"),
                "PULSAR_RELEASES_ROOT": str(self.root / "spec-fixture" / "releases"),
                "MODEL_LIBRARY_DIR": str(self.root / "library"),
                "MODEL_LIBRARY_CATALOG": str(
                    self.root / "library" / "catalog.json"
                ),
                "MOCK_HF_LOG": str(self.root / "hf.log"),
                "PULSAR_HF_SOURCE_INVENTORY_PY": str(
                    self.root / "bin" / "hf-source-inventory.py"
                ),
                "PULSAR_COLD_ROOT": "",
                "PULSAR_COLD_ARCHIVE_AUTOSTART": "0",
                "PULSAR_DOCKER": str(docker),
                "PULSAR_HOT_ROOT": str(self.root / "hot"),
                "PULSAR_HOT_RESERVE_BYTES": "0",
                "PULSAR_HOT_BUDGET_BYTES": "1000000",
            }
        )

    def run_library(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(REPO_ROOT / "scripts" / "model-library.sh"), *args],
            cwd=REPO_ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_external_tree_stays_unbound_through_public_workflow(self) -> None:
        refreshed = self.run_library("catalog", "refresh", "--local-only")
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        catalog = json.loads(
            pathlib.Path(self.env["MODEL_LIBRARY_CATALOG"]).read_text(
                encoding="utf-8"
            )
        )
        entry = next(
            item for item in catalog["models"] if item["model_id"] == self.model_id
        )
        self.assertEqual(entry["validation"], "unbound-complete")
        self.assertEqual(model_library.policy_complete_homes(entry), [])
        self.assertEqual(entry["homes"][0]["unbound_reason"], "missing-receipt")
        self.assertFalse(entry["homes"][0]["primary"])

        health_result = self.run_library("health", "--json")
        self.assertIn(health_result.returncode, {0, 1}, health_result.stderr)
        health = json.loads(health_result.stdout)
        model = next(
            item for item in health["models"] if item["model_id"] == self.model_id
        )
        self.assertEqual(model["home_ranks"], [])
        self.assertIsNone(model["primary"]["rank"])
        issue = next(
            item
            for item in health["issues"]
            if item["code"] == "unbound-complete-no-receipt"
        )
        self.assertEqual(
            issue["remediation"]["command"],
            "scripts/model-library.sh cleanup-recommend",
        )

        cleanup_result = self.run_library("cleanup-recommend", "--json")
        self.assertEqual(cleanup_result.returncode, 0, cleanup_result.stderr)
        recommendations = json.loads(cleanup_result.stdout)["recommendations"]
        recommendation = next(
            item for item in recommendations if item["model_id"] == self.model_id
        )
        self.assertEqual(recommendation["select_commands"], [])
        self.assertIn("home check", recommendation["removal_commands"][0]["check"])
        self.assertIn("home remove", recommendation["removal_commands"][0]["remove"])
        self.assertIn(
            "--revision <selector> --plan --json",
            recommendation["reacquire_commands"][0],
        )

        prepare = self.run_library(
            "prepare", self.profile, "--transport", "ssh-control", "--yes"
        )
        self.assertNotEqual(prepare.returncode, 0)
        self.assertIn("complete tree has no download receipt", prepare.stderr)
        self.assertIn("cleanup-recommend", prepare.stderr)
        self.assertIn("home add --revision", prepare.stderr)

    def test_model_id_with_multiple_unbound_revisions_names_cleanup(self) -> None:
        homes = []
        for rank, revision in enumerate(("b" * 40, "c" * 40)):
            homes.append(
                {
                    "model_id": self.model_id,
                    "revision": revision,
                    "identity_key": f"{self.model_id}@{revision}",
                    "rank": rank,
                    "node_id": f"node-{rank}",
                    "hostname": f"fixture-{rank}",
                    "ssh_host": "local",
                    "cache_root": f"/cache/{rank}",
                    "hub_path": f"/cache/{rank}/hub/model",
                    "state": "complete",
                    "home_class": "unbound-complete",
                    "occupancy": False,
                    "unbound_reason": "missing-receipt",
                    "active": False,
                    "bytes": 1,
                }
            )
        catalog = model_library.build_catalog(
            topology_id="d" * 64,
            homes=homes,
            profiles=[],
        )

        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "multiple unbound complete revisions.*cleanup-recommend",
        ):
            model_library.resolve_entry(
                catalog,
                model_id=self.model_id,
                cold_root=None,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
