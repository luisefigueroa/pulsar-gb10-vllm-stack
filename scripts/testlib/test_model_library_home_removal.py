#!/usr/bin/env python3
"""Contracts for fail-closed durable-home removal."""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402


class HomeRemovalContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.model_id = "Qwen/Qwen3-1.7B"
        self.revision = "a" * 40
        self.node_id = "node-a"
        self.cache_root = self.root / "cache"
        self.hub = (
            self.cache_root
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        snapshot = self.hub / "snapshots" / self.revision
        snapshot.mkdir(parents=True)
        (self.hub / "refs").mkdir()
        (self.hub / "refs" / "main").write_text(
            self.revision + "\n",
            encoding="utf-8",
        )
        (snapshot / "config.json").write_text(
            '{"architectures":["Fixture"]}\n',
            encoding="utf-8",
        )
        (snapshot / "model.safetensors").write_bytes(b"fixture-weights")

        self.models_dir = self.root / "models"
        self.models_dir.mkdir()
        (self.models_dir / "qwen.conf").write_text(
            'MODEL="Qwen/Qwen3-1.7B"\nSTATUS="tested"\nNODES=1\n',
            encoding="utf-8",
        )
        self.topology_path = self.root / "topology.json"
        topology = {
            "schema_version": 1,
            "nodes": [
                {
                    "rank": 0,
                    "node_id": self.node_id,
                    "hostname": "fixture-node",
                    "ssh_host": "local",
                    "control": {"interface": "mgmt0", "ip": "192.0.2.10"},
                    "gpu": "NVIDIA GB10",
                    "rdma": [],
                }
            ],
            "links": [],
            "validation": {
                "class": "roce-full-mesh",
                "full_mesh": True,
                "connectivity_verified": True,
                "min_rails_per_pair": 0,
            },
        }
        topology["topology_id"] = topology_digest(topology)
        self.topology_id = topology["topology_id"]
        self.topology_path.write_text(json.dumps(topology), encoding="utf-8")

        self.catalog_path = self.root / "catalog.json"
        self._write_catalog()
        self.observations = self.root / "observations"
        self.observations.mkdir()
        self._write_observations()

    def _write_catalog(self, *, alternate: dict[str, object] | None = None) -> None:
        homes: list[dict[str, object]] = [
            {
                "rank": 0,
                "node_id": self.node_id,
                "hostname": "fixture-node",
                "ssh_host": "local",
                "cache_root": str(self.cache_root),
                "hub_path": str(self.hub),
                "state": "complete",
                "bytes": model_library.tree_bytes(self.hub),
                "active": True,
                "primary": True,
            }
        ]
        if alternate is not None:
            homes.append(alternate)
        catalog = {
            "schema_version": 2,
            "refreshed_at": "2026-08-11T00:00:00.000Z",
            "topology_id": self.topology_id,
            "primary_selections": [
                {
                    "identity_key": f"{self.model_id}@{self.revision}",
                    "node_id": self.node_id,
                    "selected_at": "2026-08-11T00:00:00.000Z",
                }
            ],
            "models": [
                {
                    "model_id": self.model_id,
                    "revision": self.revision,
                    "identity_key": f"{self.model_id}@{self.revision}",
                    "validation": "legacy-unsealed",
                    "profiles": ["qwen", "qwen3-1.7b"],
                    "profile_validation": [],
                    "homes": homes,
                    "duplicate": len(homes) > 1,
                    "has_primary": True,
                    "on_disk": True,
                }
            ],
        }
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    def _write_observations(
        self,
        *,
        hot_scan: dict[str, object] | None = None,
        containers: list[dict[str, object]] | None = None,
    ) -> None:
        if hot_scan is None:
            hot_scan = {
                "schema_version": 1,
                "kind": "pulsar-model-library-home-hot-reference-scan",
                "rank": 0,
                "node_id": self.node_id,
                "hot_root": str(self.root / "hot"),
                "status": "ok",
                "references": [],
                "errors": [],
            }
        (self.observations / "hot-0.json").write_text(
            json.dumps(hot_scan),
            encoding="utf-8",
        )
        rows = "".join(json.dumps(item) + "\n" for item in containers or [])
        (self.observations / "containers-0.jsonl").write_text(
            rows,
            encoding="utf-8",
        )

    def _inspection(self) -> dict[str, object]:
        return model_library.inspect_removable_home(
            self.hub,
            cache_root=self.cache_root,
            model_id=self.model_id,
            revision=self.revision,
            rank=0,
            node_id=self.node_id,
        )

    def _plan(
        self,
        *,
        allow_last_home: bool = True,
        node_selector: str = "",
    ) -> dict[str, object]:
        inspection_path = self.root / "inspection.json"
        inspection_path.write_text(
            json.dumps(self._inspection()),
            encoding="utf-8",
        )
        return model_library.plan_home_removal(
            catalog_path=self.catalog_path,
            query="qwen",
            topology_file=self.topology_path,
            topology_id=self.topology_id,
            models_dir=self.models_dir,
            inspection_path=inspection_path,
            observations_dir=self.observations,
            node_selector=node_selector,
            allow_last_home=allow_last_home,
        )

    @staticmethod
    def _kinds(plan: dict[str, object]) -> set[str]:
        return {item["kind"] for item in plan["blockers"]}  # type: ignore[index]

    def test_exact_single_revision_home_is_eligible_shape(self) -> None:
        inspection = self._inspection()
        self.assertEqual(inspection["state"], "eligible")
        self.assertEqual(inspection["snapshot_entries"], [self.revision])
        self.assertTrue(inspection["fingerprint"])

    def test_other_snapshot_or_ref_target_blocks_whole_repository_removal(self) -> None:
        other = self.hub / "snapshots" / ("b" * 40)
        other.mkdir()
        inspection = self._inspection()
        codes = {item["code"] for item in inspection["blockers"]}
        self.assertIn("multiple-snapshot-revisions", codes)

        other.rmdir()
        (self.hub / "refs" / "main").write_text("b" * 40, encoding="utf-8")
        inspection = self._inspection()
        codes = {item["code"] for item in inspection["blockers"]}
        self.assertIn("ref-target-differs", codes)

    def test_symlinked_repository_layout_directory_blocks_removal(self) -> None:
        snapshots = self.hub / "snapshots"
        external = self.root / "external-snapshots"
        snapshots.rename(external)
        snapshots.symlink_to(external, target_is_directory=True)

        inspection = self._inspection()
        codes = {item["code"] for item in inspection["blockers"]}
        self.assertIn("snapshots-is-symlink", codes)
        self.assertEqual(inspection["state"], "blocked")

    def test_last_home_requires_separate_acknowledgement(self) -> None:
        blocked = self._plan(allow_last_home=False)
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("last-durable-home", self._kinds(blocked))

        eligible = self._plan(allow_last_home=True)
        self.assertEqual(eligible["state"], "eligible")
        self.assertEqual(eligible["blockers"], [])

    def test_selected_primary_with_alternate_must_be_switched_before_removal(
        self,
    ) -> None:
        self._write_catalog(
            alternate={
                "rank": 1,
                "node_id": "node-b",
                "hostname": "fixture-alternate",
                "ssh_host": "fixture-alternate",
                "cache_root": "/alternate/cache",
                "hub_path": "/alternate/cache/hub/models--Qwen--Qwen3-1.7B",
                "state": "complete",
                "bytes": 1,
                "active": False,
                "primary": False,
            }
        )
        plan = self._plan()
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("selected-primary-home", self._kinds(plan))

    def test_duplicate_removal_requires_primary_even_with_node_selector(
        self,
    ) -> None:
        self._write_catalog(
            alternate={
                "rank": 1,
                "node_id": "node-b",
                "hostname": "fixture-alternate",
                "ssh_host": "fixture-alternate",
                "cache_root": "/alternate/cache",
                "hub_path": "/alternate/cache/hub/models--Qwen--Qwen3-1.7B",
                "state": "complete",
                "bytes": 1,
                "active": False,
                "primary": False,
            }
        )
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["primary_selections"] = []
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

        plan = self._plan(node_selector="0")
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("primary-selection-required", self._kinds(plan))

    def test_ready_and_pinned_hot_views_both_block(self) -> None:
        reference = {
            "rank": 0,
            "node_id": self.node_id,
            "instance_dir": str(self.root / "hot" / "qwen" / "content"),
            "schema_version": 3,
            "profile": "qwen",
            "model_id": self.model_id,
            "revision": self.revision,
            "home_node_id": self.node_id,
            "content_id": "content",
            "state": "ready",
            "pinned": False,
        }
        scan = {
            "schema_version": 1,
            "kind": "pulsar-model-library-home-hot-reference-scan",
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.root / "hot"),
            "status": "ok",
            "references": [reference],
            "errors": [],
        }
        self._write_observations(hot_scan=scan)
        ready = self._plan()
        self.assertIn("hot-reference", self._kinds(ready))
        self.assertIn("retained managed hot view", ready["blockers"][0]["detail"])

        reference["state"] = "pinned"
        reference["pinned"] = True
        self._write_observations(hot_scan=scan)
        pinned = self._plan()
        self.assertIn("pinned hot view", pinned["blockers"][0]["detail"])

    def test_managed_library_hot_and_replicated_containers_block(self) -> None:
        base_labels = {
            "io.pulsar.gb10.managed": "true",
            "io.pulsar.gb10.conf": "qwen",
            "io.pulsar.gb10.rank": "single",
        }
        library_labels = {
            **base_labels,
            "io.pulsar.gb10.weight-source": "library-hot",
            "io.pulsar.gb10.weight-owner": self.node_id,
            "io.pulsar.gb10.model-revision": self.revision,
        }
        self._write_observations(
            containers=[{"id": "1" * 64, "name": "/library", "labels": library_labels}]
        )
        library_plan = self._plan()
        self.assertIn("container-reference", self._kinds(library_plan))

        replicated_labels = {
            **base_labels,
            "io.pulsar.gb10.weight-source": "replicated",
        }
        self._write_observations(
            containers=[
                {"id": "2" * 64, "name": "/replicated", "labels": replicated_labels}
            ]
        )
        replicated_plan = self._plan()
        self.assertIn("container-reference", self._kinds(replicated_plan))

        unknown_profile_labels = {
            **base_labels,
            "io.pulsar.gb10.conf": "removed-profile",
            "io.pulsar.gb10.weight-source": "replicated",
        }
        self._write_observations(
            containers=[
                {
                    "id": "3" * 64, "name": "/unknown", "labels": unknown_profile_labels
                }
            ]
        )
        unknown_plan = self._plan()
        self.assertIn("container-reference", self._kinds(unknown_plan))

    def test_unobservable_hot_metadata_fails_closed(self) -> None:
        scan = {
            "schema_version": 1,
            "kind": "pulsar-model-library-home-hot-reference-scan",
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.root / "hot"),
            "status": "error",
            "references": [],
            "errors": [
                {"path": "/fixture/hot.json", "detail": "unsupported hot schema"}
            ],
        }
        self._write_observations(hot_scan=scan)
        plan = self._plan()
        self.assertIn("observability", self._kinds(plan))

    def test_hot_error_without_diagnostics_aborts_planning(self) -> None:
        scan = {
            "schema_version": 1,
            "kind": "pulsar-model-library-home-hot-reference-scan",
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.root / "hot"),
            "status": "error",
            "references": [],
            "errors": [],
        }
        self._write_observations(hot_scan=scan)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "lacks diagnostic"
        ):
            self._plan()

    def test_hot_scanner_does_not_follow_home_view_symlink(self) -> None:
        hot_root = self.root / "hot-scan"
        instance = hot_root / "qwen-topology" / "content"
        metadata = instance / ".pulsar"
        metadata.mkdir(parents=True)
        (instance / "hub").mkdir()
        (instance / "hub" / self.hub.name).symlink_to(self.hub)
        stamp = {
            "schema_version": 3,
            "profile": "qwen",
            "model_id": self.model_id,
            "revision": self.revision,
            "home_node_id": self.node_id,
            "content_id": "content",
            "state": "ready",
            "pinned": False,
        }
        (metadata / "hot.json").write_text(json.dumps(stamp), encoding="utf-8")
        scan = model_library.scan_home_hot_references(
            hot_root,
            rank=0,
            node_id=self.node_id,
        )
        self.assertEqual(scan["status"], "ok")
        self.assertEqual(len(scan["references"]), 1)
        self.assertEqual(scan["references"][0]["instance_dir"], str(instance))

    def test_hot_scanner_treats_broken_root_symlink_as_unobservable(self) -> None:
        hot_root = self.root / "broken-hot"
        hot_root.symlink_to(self.root / "missing-hot", target_is_directory=True)
        scan = model_library.scan_home_hot_references(
            hot_root,
            rank=0,
            node_id=self.node_id,
        )
        self.assertEqual(scan["status"], "error")
        self.assertEqual(scan["references"], [])
        self.assertIn("not a directory", scan["errors"][0]["detail"])

    def test_execution_revalidates_fingerprint_then_removes_exact_home(self) -> None:
        plan = self._plan()
        result = model_library.execute_home_removal_plan(
            plan,
            rank=0,
            node_id=self.node_id,
        )
        self.assertEqual(result["state"], "removed")
        self.assertFalse(self.hub.exists())
        self.assertTrue((self.cache_root / "hub").is_dir())

    def test_metadata_drift_preserves_home(self) -> None:
        plan = self._plan()
        (self.hub / "refs" / "main").write_text(
            self.revision + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "metadata changed",
        ):
            model_library.execute_home_removal_plan(
                plan,
                rank=0,
                node_id=self.node_id,
            )
        self.assertTrue(self.hub.is_dir())

    def _cli_environment(self) -> tuple[dict[str, str], pathlib.Path, pathlib.Path]:
        mock_dir = self.root / "mock-bin"
        mock_dir.mkdir(exist_ok=True)
        ids_file = self.root / "docker.ids"
        metadata_file = self.root / "docker.metadata.json"
        ids_file.write_text("", encoding="utf-8")
        metadata_file.write_text("", encoding="utf-8")
        docker = mock_dir / "docker"
        docker.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  info)
    [ "${MOCK_DOCKER_INFO:-ok}" = ok ]
    ;;
  ps)
    [ "${MOCK_DOCKER_INFO:-ok}" = ok ] || exit 1
    [ ! -s "$MOCK_DOCKER_IDS_FILE" ] || cat "$MOCK_DOCKER_IDS_FILE"
    ;;
  inspect)
    [ "${MOCK_DOCKER_INFO:-ok}" = ok ] || exit 1
    [ -s "$MOCK_DOCKER_METADATA_FILE" ] || exit 1
    cat "$MOCK_DOCKER_METADATA_FILE"
    ;;
  *) exit 1 ;;
esac
""",
            encoding="utf-8",
        )
        docker.chmod(0o700)
        state_dir = self.root / "library-state"
        state_dir.mkdir()
        environment = os.environ.copy()
        environment.update(
            {
                "CLUSTER_TOPOLOGY_FILE": str(self.topology_path),
                "HF_CACHE": str(self.cache_root),
                "MODEL_LIBRARY_CATALOG": str(self.catalog_path),
                "MODEL_LIBRARY_DIR": str(state_dir),
                "PULSAR_DOCKER": str(docker),
                "PULSAR_HOT_ROOT": str(self.root / "cli-hot"),
                "PULSAR_MODEL_LIBRARY_LOCK_TIMEOUT_SECONDS": "0",
                "MOCK_DOCKER_IDS_FILE": str(ids_file),
                "MOCK_DOCKER_METADATA_FILE": str(metadata_file),
            }
        )
        return environment, ids_file, metadata_file

    def _run_library_cli(
        self,
        environment: dict[str, str],
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(REPO_ROOT / "scripts" / "model-library.sh"), *arguments],
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_public_cli_blocks_hot_container_and_unobservable_docker(self) -> None:
        environment, ids_file, metadata_file = self._cli_environment()
        eligible = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home", "--json",
        )
        self.assertEqual(eligible.returncode, 0, eligible.stderr)
        self.assertEqual(json.loads(eligible.stdout)["state"], "eligible")

        hot_root = pathlib.Path(environment["PULSAR_HOT_ROOT"])
        metadata_dir = hot_root / "qwen-topology" / "content" / ".pulsar"
        metadata_dir.mkdir(parents=True)
        stamp = {
            "schema_version": 3,
            "profile": "qwen3-1.7b",
            "model_id": self.model_id,
            "revision": self.revision,
            "home_node_id": self.node_id,
            "content_id": "content",
            "state": "ready",
            "pinned": False,
        }
        (metadata_dir / "hot.json").write_text(json.dumps(stamp), encoding="utf-8")
        hot_blocked = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home", "--json",
        )
        self.assertEqual(hot_blocked.returncode, 1)
        self.assertIn("hot-reference", self._kinds(json.loads(hot_blocked.stdout)))

        shutil.rmtree(hot_root)
        ids_file.write_text("1" * 64 + "\n", encoding="utf-8")
        metadata_file.write_text(
            json.dumps(
                {
                    "id": "1" * 64,
                    "name": "/fixture-qwen",
                    "labels": {
                        "io.pulsar.gb10.managed": "true",
                        "io.pulsar.gb10.conf": "qwen3-1.7b",
                        "io.pulsar.gb10.rank": "single",
                        "io.pulsar.gb10.weight-source": "library-hot",
                        "io.pulsar.gb10.weight-owner": self.node_id,
                        "io.pulsar.gb10.model-revision": self.revision,
                    },
                }
            ),
            encoding="utf-8",
        )
        container_blocked = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home", "--json",
        )
        self.assertEqual(container_blocked.returncode, 1)
        self.assertIn("container-reference", self._kinds(json.loads(container_blocked.stdout)))

        environment["MOCK_DOCKER_INFO"] = "error"
        unobservable = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home", "--json",
        )
        self.assertNotEqual(unobservable.returncode, 0)
        self.assertIn("cannot enumerate managed containers", unobservable.stderr)

    def test_public_cli_requires_yes_and_preserves_home(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        refused = self._run_library_cli(
            environment,
            "home", "remove", "qwen3-1.7b", "--allow-last-home",
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn(
            "home removal requires --yes after reviewing the eligible plan",
            refused.stderr,
        )
        self.assertNotIn("eligible plan 3", refused.stderr)
        self.assertTrue(self.hub.is_dir())

    def test_public_cli_removes_only_temp_home_and_refreshes_catalog(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        environment["COLUMNS"] = "48"
        removed = self._run_library_cli(
            environment,
            "home", "remove", "qwen3-1.7b", "--allow-last-home", "--yes",
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("durable home removal  REMOVED", removed.stdout)
        self.assertLessEqual(max(len(line) for line in removed.stdout.splitlines()), 48)
        self.assertFalse(self.hub.exists())
        refreshed = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entry = next(
            item for item in refreshed["models"] if item["model_id"] == self.model_id
        )
        self.assertFalse(entry["on_disk"])

    def test_exclusive_removal_lock_blocks_supported_readers_and_launchers(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        lock_path = pathlib.Path(environment["MODEL_LIBRARY_DIR"]) / "lifecycle.lock"
        with open(lock_path, "w", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = subprocess.run(
                [str(REPO_ROOT / "scripts" / "check-weights.sh"), "qwen3-1.7b"],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("removal is in progress", blocked.stderr)
        for relative in (
            "scripts/up.sh",
            "serve.sh",
            "cluster/start-cluster.sh",
            "scripts/pull-weights.sh",
            "scripts/weight-fabric.sh",
        ):
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("acquire_model_library_lifecycle_lock shared", text)


if __name__ == "__main__":
    unittest.main()
