#!/usr/bin/env python3
"""Filesystem-backed, all-rank hot-storage admission contracts."""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402

GIB = 1024**3
TIB = 1024**4


class HotBudgetContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.hot_root = self.root / "hot"
        self.hot_root.mkdir()

    def _admission(
        self,
        required: int,
        *,
        total: int = 4 * TIB,
        available: int = 2 * TIB,
        hard_cap: int | None = None,
        reserve: int | None = None,
        rank: int = 0,
        node_id: str = "node-zero",
        runtime_source: str = "sealed-hot",
        replacing_path: pathlib.Path | None = None,
    ) -> dict[str, object]:
        with mock.patch.dict(
            os.environ,
            {
                "PULSAR_HOT_BUDGET_BYTES": "",
                "PULSAR_HOT_RESERVE_BYTES": "",
            },
        ):
            return model_library.hot_budget_admission(
                self.hot_root,
                required,
                budget_bytes=hard_cap,
                reserve_bytes=reserve,
                replacing_path=replacing_path,
                runtime_source=runtime_source,
                rank=rank,
                node_id=node_id,
                hostname=f"fixture-{rank}",
                filesystem_total_bytes=total,
                filesystem_available_bytes=available,
            )

    def test_default_reserve_admits_flagship_without_arbitrary_cap(self) -> None:
        report = self._admission(167 * GIB)
        self.assertEqual(report["state"], "eligible")
        self.assertIsNone(report["policy"]["hard_cap_bytes"])
        self.assertEqual(report["policy"]["reserve_bytes"], (4 * TIB * 5 + 99) // 100)
        self.assertEqual(
            report["policy"]["reserve_source"],
            "default-max-64gib-or-5-percent",
        )

    def test_filesystem_reserve_and_explicit_hard_cap_block_independently(self) -> None:
        reserve_blocked = self._admission(
            50 * GIB,
            total=TIB,
            available=100 * GIB,
        )
        self.assertEqual(reserve_blocked["state"], "blocked")
        self.assertEqual(
            [item["code"] for item in reserve_blocked["blockers"]],
            ["filesystem-reserve"],
        )

        cap_blocked = self._admission(
            167 * GIB,
            hard_cap=100 * GIB,
            reserve=0,
        )
        self.assertEqual(cap_blocked["state"], "blocked")
        self.assertEqual(
            [item["code"] for item in cap_blocked["blockers"]],
            ["hard-cap-exceeded"],
        )

    def test_durable_home_view_owns_zero_hot_model_bytes(self) -> None:
        report = self._admission(0, runtime_source="durable-home")
        self.assertEqual(report["state"], "eligible")
        self.assertEqual(report["required_owned_bytes"], 0)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "durable-home views must require zero",
        ):
            self._admission(GIB, runtime_source="durable-home")

    def test_replacement_credits_quota_but_not_prewrite_free_space(self) -> None:
        instance = self.hot_root / "profile-topology" / "content"
        instance.mkdir(parents=True)
        (instance / "old.bin").write_bytes(b"x" * 80)
        report = self._admission(
            50,
            total=1000,
            available=45,
            hard_cap=100,
            reserve=0,
            replacing_path=instance,
        )
        self.assertEqual(report["replacing_owned_bytes"], 80)
        self.assertEqual(report["projected_owned_hot_bytes"], 50)
        self.assertEqual(
            [item["code"] for item in report["blockers"]],
            ["filesystem-reserve"],
        )

    def test_inventory_counts_untracked_bytes_without_following_symlinks(self) -> None:
        payload = b"untracked-payload"
        (self.hot_root / "untracked.bin").write_bytes(payload)
        outside = self.root / "durable"
        outside.mkdir()
        (outside / "large.bin").write_bytes(b"z" * 4096)
        (self.hot_root / "home-view").symlink_to(outside, target_is_directory=True)
        report = model_library.budget_report(
            self.hot_root,
            reserve_bytes=0,
            filesystem_total_bytes=TIB,
            filesystem_available_bytes=TIB,
        )
        self.assertEqual(report["used_bytes"], len(payload))
        self.assertEqual(report["untracked_bytes"], len(payload))

    def test_malformed_managed_metadata_is_visible_and_still_accounted(self) -> None:
        instance = self.hot_root / "profile-topology" / "content"
        metadata = instance / ".pulsar"
        metadata.mkdir(parents=True)
        (instance / "weights.bin").write_bytes(b"weights")
        (metadata / "hot.json").write_text("{broken", encoding="utf-8")
        report = model_library.budget_report(
            self.hot_root,
            reserve_bytes=0,
            filesystem_total_bytes=TIB,
            filesystem_available_bytes=TIB,
        )
        self.assertGreaterEqual(report["used_bytes"], len(b"weights"))
        self.assertEqual(len(report["scan_errors"]), 1)
        self.assertEqual(report["instances"][0]["state"], "invalid")

    def test_storage_requirements_charge_only_non_home_ranks(self) -> None:
        instance = self.hot_root / "profile-topology" / "content"
        warm = model_library.build_hot_storage_requirements(
            target_ranks=[0, 1, 2],
            bytes_logical=167 * GIB,
            instance_dir=instance,
            home_rank=1,
        )
        self.assertEqual(
            [(item["runtime_source"], item["required_owned_bytes"]) for item in warm],
            [
                ("sealed-hot", 167 * GIB),
                ("durable-home", 0),
                ("sealed-hot", 167 * GIB),
            ],
        )
        cold = model_library.build_hot_storage_requirements(
            target_ranks=[0, 1],
            bytes_logical=167 * GIB,
            instance_dir=instance,
            home_rank=None,
        )
        self.assertTrue(
            all(item["required_owned_bytes"] == 167 * GIB for item in cold)
        )

    def test_all_rank_merge_is_exact_and_propagates_blockers(self) -> None:
        first = self._admission(0, reserve=0)
        second = self._admission(
            200,
            total=1000,
            available=100,
            reserve=0,
            rank=1,
            node_id="node-one",
        )
        plan = model_library.merge_hot_budget_observations(
            [first, second],
            expected_ranks=[0, 1],
            topology_id="topology-fixture",
            mode="activate",
        )
        self.assertEqual(plan["state"], "blocked")
        self.assertEqual(plan["blockers"][0]["rank"], 1)
        with self.assertRaisesRegex(model_library.ModelLibraryError, "differ"):
            model_library.merge_hot_budget_observations(
                [first],
                expected_ranks=[0, 1],
                topology_id="topology-fixture",
                mode="activate",
            )
        duplicate_node = {**second, "node_id": "node-zero"}
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "duplicate node identity",
        ):
            model_library.merge_hot_budget_observations(
                [first, duplicate_node],
                expected_ranks=[0, 1],
                topology_id="topology-fixture",
                mode="activate",
            )

    def test_human_plan_respects_narrow_terminal(self) -> None:
        first = self._admission(0, reserve=0)
        plan = model_library.merge_hot_budget_observations(
            [first],
            expected_ranks=[0],
            topology_id="topology-fixture",
            mode="inventory",
        )
        output = io.StringIO()
        with mock.patch.dict(os.environ, {"COLUMNS": "48"}), contextlib.redirect_stdout(output):
            model_library.render_hot_budget_plan(plan)
        self.assertIn("hot storage admission  ELIGIBLE", output.getvalue())
        self.assertLessEqual(
            max(len(line) for line in output.getvalue().splitlines()),
            48,
        )

    def test_public_budget_cli_observes_every_confirmed_rank(self) -> None:
        topology = {
            "schema_version": 1,
            "nodes": [
                {
                    "rank": 0,
                    "node_id": "node-zero",
                    "hostname": "fixture-zero",
                    "ssh_host": "local",
                    "control": {"interface": "lan0", "ip": "192.0.2.10"},
                    "gpu": "NVIDIA GB10",
                    "rdma": [
                        {
                            "hca": "roce0",
                            "netdev": "fabric0",
                            "cidrs": ["198.51.100.10/24"],
                        }
                    ],
                },
                {
                    "rank": 1,
                    "node_id": "node-one",
                    "hostname": "fixture-one",
                    "ssh_host": "fixture-one.local",
                    "control": {"interface": "lan0", "ip": "192.0.2.11"},
                    "gpu": "NVIDIA GB10",
                    "rdma": [
                        {
                            "hca": "roce0",
                            "netdev": "fabric0",
                            "cidrs": ["198.51.100.11/24"],
                        }
                    ],
                },
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
                "class": "roce-full-mesh",
                "full_mesh": True,
                "connectivity_verified": True,
                "min_rails_per_pair": 1,
            },
        }
        topology["topology_id"] = topology_digest(topology)
        topology_path = self.root / "topology.json"
        topology_path.write_text(json.dumps(topology), encoding="utf-8")
        fake_ssh = self.root / "fake-ssh"
        fake_ssh.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
command=${args[$((${#args[@]} - 1))]}
exec bash -c "$command"
""",
            encoding="utf-8",
        )
        fake_ssh.chmod(0o700)
        state = self.root / "state"
        state.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "CLUSTER_TOPOLOGY_FILE": str(topology_path),
                "MODEL_LIBRARY_DIR": str(state),
                "PULSAR_HOT_ROOT": str(self.hot_root),
                "PULSAR_HOT_RESERVE_BYTES": "0",
                "PULSAR_SSH": str(fake_ssh),
            }
        )
        result = subprocess.run(
            [str(REPO_ROOT / "scripts" / "model-library.sh"), "budget", "--json"],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(
            [(item["rank"], item["node_id"]) for item in plan["observed_nodes"]],
            [(0, "node-zero"), (1, "node-one")],
        )

    def test_hot_writer_excludes_supported_readers(self) -> None:
        state = self.root / "lock-state"
        state.mkdir()
        lock_path = state / "hot.lock"
        with lock_path.open("w", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            environment = os.environ.copy()
            environment.update(
                {
                    "MODEL_LIBRARY_DIR": str(state),
                    "PULSAR_MODEL_LIBRARY_HOT_LOCK_TIMEOUT_SECONDS": "0",
                }
            )
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    ". scripts/lib.sh; acquire_model_library_hot_lock shared",
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hot read lock timed out", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
