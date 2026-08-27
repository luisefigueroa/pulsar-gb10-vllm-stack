#!/usr/bin/env python3
"""Profile-geometry contracts for receipt-backed home relocation."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIBRARY = REPO_ROOT / "scripts" / "model-library.sh"
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402


class RelocationGeometryContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        fixture.write_topology(self.root / "topology.json", ranks=3)
        (self.root / "cache").mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "CLUSTER_TOPOLOGY_FILE": str(self.root / "topology.json"),
                "HF_CACHE": str(self.root / "cache"),
                "MODEL_LIBRARY_DIR": str(self.root / "library"),
                "MODEL_LIBRARY_CATALOG": str(self.root / "library" / "catalog.json"),
                "PULSAR_COLD_ROOT": "",
            }
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(LIBRARY), *args],
            cwd=REPO_ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_two_rank_profile_refuses_idle_rank_before_catalog_access(self) -> None:
        result = self._run(
            "home",
            "relocate",
            "qwen3.8-27b-fp8-2node",
            "--node",
            "2",
            "--yes",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside qwen3.8-27b-fp8-2node serving ranks", result.stderr)
        self.assertIn("0 1", result.stderr)
        self.assertFalse((self.root / "library" / "catalog.json").exists())

    def test_raw_identity_requires_explicit_profile(self) -> None:
        result = self._run(
            "home",
            "relocate",
            "Qwen/Qwen3.8-27B-FP8@" + fixture.COMMIT,
            "--node",
            "0",
            "--yes",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("raw model_id queries require --profile", result.stderr)
        self.assertFalse((self.root / "library" / "catalog.json").exists())

    def test_raw_identity_refuses_mismatched_profile(self) -> None:
        result = self._run(
            "home",
            "relocate",
            "Qwen/Qwen3.8-27B-FP8@" + fixture.COMMIT,
            "--profile",
            "nemotron-3-nano-30b-nvfp4",
            "--node",
            "0",
            "--yes",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not describe Qwen/Qwen3.8-27B-FP8", result.stderr)
        self.assertFalse((self.root / "library" / "catalog.json").exists())

    def test_one_rank_profile_accepts_idle_confirmed_rank_geometry(self) -> None:
        result = self._run(
            "home",
            "relocate",
            "qwen3.8-27b-fp8",
            "--node",
            "2",
            "--yes",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no catalog", result.stderr)
        self.assertNotIn("outside", result.stderr)

    def test_geometry_check_precedes_catalog_copy_and_occupancy(self) -> None:
        source = LIBRARY.read_text(encoding="utf-8")
        body = source.split("cmd_home_relocate()", 1)[1].split(
            "start_cold_archive_after_receipt()", 1
        )[0]
        geometry_at = body.index("geometry-ranks")
        catalog_at = body.index("ensure_catalog")
        destination_at = body.index("inspect-home-acquisition-target")
        occupancy_at = body.index("occupy-current-home")
        self.assertLess(geometry_at, catalog_at)
        self.assertLess(catalog_at, destination_at)
        self.assertLess(destination_at, occupancy_at)

    def test_unbound_cleanup_guidance_never_guesses_profile(self) -> None:
        identity = "Qwen/Qwen3.8-27B-FP8@" + fixture.COMMIT
        catalog = {
            "models": [
                {
                    "model_id": "Qwen/Qwen3.8-27B-FP8",
                    "identity_key": identity,
                    "revision": fixture.COMMIT,
                    "profiles": ["qwen3.8-27b-fp8", "qwen3.8-27b-fp8-2node"],
                    "duplicate": False,
                    "homes": [
                        {
                            "rank": 2,
                            "node_id": "node-2",
                            "state": "complete",
                            "home_class": "unbound-complete",
                            "occupancy": False,
                        }
                    ],
                }
            ]
        }
        recommendation = model_library.cleanup_recommend(catalog)[0]
        self.assertEqual(len(recommendation["select_commands"]), 1)
        command = recommendation["select_commands"][0]
        self.assertIn("--profile PROFILE", command)
        self.assertIn(identity, command)


if __name__ == "__main__":
    unittest.main()
