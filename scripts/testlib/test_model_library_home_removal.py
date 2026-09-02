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
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402


class HomeRemovalFixture:
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
                "home_class": "occupancy",
                "occupancy": True,
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
                    "validation": "receipt-occupancy",
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
        allow_unarchived_last_home: bool = False,
        node_selector: str = "",
        query: str = "qwen",
        library_dir: pathlib.Path | None = None,
    ) -> dict[str, object]:
        inspection_path = self.root / "inspection.json"
        inspection_path.write_text(
            json.dumps(self._inspection()),
            encoding="utf-8",
        )
        if library_dir is None:
            library_dir = self.root / "library-dir"
            library_dir.mkdir(exist_ok=True)
        return model_library.plan_home_removal(
            catalog_path=self.catalog_path,
            query=query,
            topology_file=self.topology_path,
            topology_id=self.topology_id,
            models_dir=self.models_dir,
            inspection_path=inspection_path,
            observations_dir=self.observations,
            node_selector=node_selector,
            allow_last_home=allow_last_home,
            allow_unarchived_last_home=allow_unarchived_last_home,
            library_dir=library_dir,
        )

    @staticmethod
    def _kinds(plan: dict[str, object]) -> set[str]:
        return {item["kind"] for item in plan["blockers"]}  # type: ignore[index]

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


class HomeRemovalContracts(HomeRemovalFixture, unittest.TestCase):
    def test_exact_single_revision_home_is_eligible_shape(self) -> None:
        inspection = self._inspection()
        self.assertEqual(inspection["state"], "eligible")
        self.assertEqual(inspection["occupancy_class"], "complete-home")
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
                "home_class": "occupancy",
                "occupancy": True,
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
                "home_class": "occupancy",
                "occupancy": True,
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
            "io.pulsar.gb10.weight-source": "local-files",
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
                        "io.pulsar.gb10.weight-source": "local-files",
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

    def test_public_cli_removes_unreceipted_synthetic_revision(self) -> None:
        synthetic = "cold-123456789abc"
        snapshot = self.hub / "snapshots" / self.revision
        snapshot.rename(self.hub / "snapshots" / synthetic)
        (self.hub / "refs" / "main").write_text(
            synthetic + "\n",
            encoding="utf-8",
        )
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["primary_selections"] = []
        entry = catalog["models"][0]
        entry["revision"] = synthetic
        entry["identity_key"] = f"{self.model_id}@{synthetic}"
        entry["validation"] = "unbound-complete"
        entry["duplicate"] = False
        entry["has_primary"] = False
        home = entry["homes"][0]
        home["home_class"] = "unbound-complete"
        home["occupancy"] = False
        home["unbound_reason"] = "non-exact-revision"
        home["primary"] = False
        home["bytes"] = model_library.tree_bytes(self.hub)
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

        environment, _ids_file, _metadata_file = self._cli_environment()
        removed = self._run_library_cli(
            environment,
            "home", "remove", "qwen3-1.7b", "--yes",
        )

        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertIn("durable home removal  REMOVED", removed.stdout)
        self.assertFalse(self.hub.exists())

    def test_unbound_complete_attachment_blocks_removal(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["primary_selections"] = []
        entry = catalog["models"][0]
        entry["has_primary"] = False
        home = entry["homes"][0]
        home["home_class"] = "unbound-complete"
        home["occupancy"] = False
        home["unbound_reason"] = "missing-receipt"
        home["primary"] = False
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        library_dir = self.root / "attached-unbound-library"
        store = library_dir / "home-occupancy"
        store.mkdir(parents=True)
        (store / "fixture.json").write_text(
            json.dumps(
                {
                    "model_id": self.model_id,
                    "snapshot_revision": self.revision,
                    "durable_home_path": str(self.hub),
                }
            ),
            encoding="utf-8",
        )

        plan = self._plan(library_dir=library_dir)

        self.assertEqual(plan["state"], "blocked")
        self.assertIn("current-home-attached", self._kinds(plan))
        self.assertTrue(self.hub.is_dir())

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
        ):
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("acquire_model_library_lifecycle_lock shared", text)


class IncompleteHubOccupancyRemoval(HomeRemovalFixture, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._convert_to_refs_only_stub()
        self._write_incomplete_catalog()
        self.sibling = (
            self.cache_root / "hub" / "models--Sibling--KeepMe"
        )
        self.sibling.mkdir()
        (self.sibling / "refs").mkdir()
        (self.sibling / "refs" / "main").write_text("sibling-ref\n", encoding="utf-8")
        (self.sibling / "keep.txt").write_text("sibling\n", encoding="utf-8")

    def _convert_to_refs_only_stub(self) -> None:
        snapshots = self.hub / "snapshots"
        if snapshots.exists():
            shutil.rmtree(snapshots)
        blobs = self.hub / "blobs"
        if blobs.exists():
            shutil.rmtree(blobs)
        refs = self.hub / "refs"
        refs.mkdir(exist_ok=True)
        (refs / "main").write_text(self.revision + "\n", encoding="utf-8")

    def _write_incomplete_catalog(self) -> None:
        catalog = {
            "schema_version": 2,
            "refreshed_at": "2026-08-11T00:00:00.000Z",
            "topology_id": self.topology_id,
            "primary_selections": [],
            "models": [
                {
                    "model_id": self.model_id,
                    "revision": None,
                    "identity_key": f"{self.model_id}@unknown",
                    "validation": "unvalidated",
                    "profiles": ["qwen", "qwen3-1.7b"],
                    "profile_validation": [],
                    "homes": [
                        {
                            "rank": 0,
                            "node_id": self.node_id,
                            "hostname": "fixture-node",
                            "ssh_host": "local",
                            "cache_root": str(self.cache_root),
                            "hub_path": str(self.hub),
                            "state": "partial",
                            "bytes": 0,
                            "active": False,
                            "primary": False,
                        }
                    ],
                    "duplicate": False,
                    "has_primary": False,
                    "on_disk": False,
                }
            ],
        }
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    def test_refs_only_stub_is_recognized_incomplete_occupancy(self) -> None:
        inspection = self._inspection()
        self.assertEqual(inspection["occupancy_class"], "incomplete-hub")
        self.assertEqual(inspection["occupancy_subtype"], "refs-only")
        self.assertEqual(inspection["state"], "eligible")
        self.assertEqual(inspection["bound_revision"], self.revision)
        self.assertEqual(inspection["snapshot_entries"], [])
        self.assertEqual(inspection["repository_bytes"], len(self.revision) + 1)

    def test_complete_or_multi_revision_trees_refuse_stub_classification(
        self,
    ) -> None:
        complete = HomeRemovalContracts()
        complete.setUp()
        self.addCleanup(complete.temporary.cleanup)
        inspection = complete._inspection()
        self.assertEqual(inspection["occupancy_class"], "complete-home")
        self.assertNotEqual(inspection["occupancy_class"], "incomplete-hub")

        other = self.hub / "snapshots" / ("b" * 40)
        other.mkdir(parents=True)
        (other / "config.json").write_text("{}\n", encoding="utf-8")
        blocked = model_library.inspect_removable_home(
            self.hub,
            cache_root=self.cache_root,
            model_id=self.model_id,
            revision=self.revision,
            rank=0,
            node_id=self.node_id,
        )
        codes = {item["code"] for item in blocked["blockers"]}
        self.assertIn("multiple-snapshot-revisions", codes)
        self.assertNotEqual(blocked["occupancy_class"], "incomplete-hub")
        self.assertEqual(blocked["state"], "blocked")

        shutil.rmtree(self.hub / "snapshots")
        (self.hub / "notes.txt").write_text("not a hub payload\n", encoding="utf-8")
        unknown = model_library.inspect_removable_home(
            self.hub,
            cache_root=self.cache_root,
            model_id=self.model_id,
            revision=self.revision,
            rank=0,
            node_id=self.node_id,
        )
        codes = {item["code"] for item in unknown["blockers"]}
        self.assertIn("unrecognized-hub-tree", codes)
        self.assertEqual(unknown["occupancy_class"], "unrecognized")
        self.assertEqual(unknown["state"], "blocked")

    def test_last_stub_occupancy_requires_allow_last_home(self) -> None:
        blocked = self._plan(allow_last_home=False)
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("last-durable-home", self._kinds(blocked))
        self.assertEqual(blocked["occupancy_class"], "incomplete-hub")
        self.assertIn("refs-only", blocked["action"]["will_delete"])
        self.assertIn("retire this incomplete/refs-only", blocked["action"]["summary"])
        self.assertIn("--yes", blocked["action"]["confirmation"])

        eligible = self._plan(allow_last_home=True)
        self.assertEqual(eligible["state"], "eligible")
        self.assertEqual(eligible["blockers"], [])
        self.assertEqual(eligible["target"]["revision"], self.revision)
        self.assertTrue(eligible["target"]["last_durable_home"])

    def test_current_home_attachment_blocks_stub_retirement(self) -> None:
        library_dir = self.root / "attached-library"
        store = library_dir / "home-occupancy"
        store.mkdir(parents=True)
        (store / "fixture.json").write_text(
            json.dumps(
                {
                    "model_id": self.model_id,
                    "snapshot_revision": self.revision,
                    "durable_home_path": str(self.hub),
                }
            ),
            encoding="utf-8",
        )
        plan = self._plan(allow_last_home=True, library_dir=library_dir)
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("current-home-attached", self._kinds(plan))

    def test_missing_library_dir_fails_closed_for_stub(self) -> None:
        inspection_path = self.root / "inspection.json"
        inspection_path.write_text(json.dumps(self._inspection()), encoding="utf-8")
        plan = model_library.plan_home_removal(
            catalog_path=self.catalog_path,
            query="qwen",
            topology_file=self.topology_path,
            topology_id=self.topology_id,
            models_dir=self.models_dir,
            inspection_path=inspection_path,
            observations_dir=self.observations,
            allow_last_home=True,
            library_dir=None,
        )
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("current-home-unobservable", self._kinds(plan))

    def test_unobservable_rank_fails_closed_for_stub(self) -> None:
        (self.observations / "hot-0.json").unlink()
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "reference probes are incomplete",
        ):
            self._plan(allow_last_home=True)

    def test_public_cli_stub_check_json_and_human_action(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        blocked = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--json",
        )
        self.assertEqual(blocked.returncode, 1, blocked.stderr)
        blocked_plan = json.loads(blocked.stdout)
        self.assertEqual(blocked_plan["state"], "blocked")
        self.assertIn("last-durable-home", self._kinds(blocked_plan))
        self.assertEqual(blocked_plan["occupancy_class"], "incomplete-hub")

        human = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home",
        )
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("incomplete hub occupancy  ELIGIBLE", human.stdout)
        self.assertIn("retire this incomplete/refs-only", human.stdout)
        self.assertIn("Will delete", human.stdout)
        self.assertIn("refs-only stub", human.stdout)
        self.assertIn("Will not delete", human.stdout)
        self.assertIn("home remove", human.stdout)
        self.assertIn("--yes", human.stdout)
        self.assertNotIn("192.0.2.10", human.stdout)

        eligible = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home", "--json",
        )
        self.assertEqual(eligible.returncode, 0, eligible.stderr)
        plan = json.loads(eligible.stdout)
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(plan["occupancy_class"], "incomplete-hub")
        self.assertEqual(plan["action"]["occupancy_subtype"], "refs-only")
        self.assertIn("home add --revision", plan["action"]["enables"])
        self.assertIn("--yes", plan["action"]["confirmation"])

    def test_public_cli_stub_remove_requires_yes_and_preserves_tree(self) -> None:
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
        self.assertTrue(self.hub.is_dir())
        self.assertTrue((self.hub / "refs" / "main").is_file())
        self.assertTrue(self.sibling.is_dir())

    def test_public_cli_stub_remove_yes_deletes_only_that_hub(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        removed = self._run_library_cli(
            environment,
            "home", "remove", "qwen3-1.7b", "--allow-last-home", "--yes",
        )
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(self.hub.exists())
        self.assertTrue(self.sibling.is_dir())
        self.assertTrue((self.sibling / "keep.txt").is_file())
        self.assertTrue((self.cache_root / "hub").is_dir())

    def test_public_cli_missing_topology_fails_closed(self) -> None:
        environment, _ids_file, _metadata_file = self._cli_environment()
        environment["CLUSTER_TOPOLOGY_FILE"] = str(self.root / "missing-topology.json")
        check = self._run_library_cli(
            environment,
            "home", "check", "qwen3-1.7b", "--allow-last-home",
        )
        self.assertNotEqual(check.returncode, 0)
        self.assertTrue(self.hub.is_dir())
        remove = self._run_library_cli(
            environment,
            "home", "remove", "qwen3-1.7b", "--allow-last-home", "--yes",
        )
        self.assertNotEqual(remove.returncode, 0)
        self.assertTrue(self.hub.is_dir())
        self.assertTrue(self.sibling.is_dir())

    def test_unknown_revision_without_bindable_ref_fails_closed(self) -> None:
        (self.hub / "refs" / "main").write_text("not-a-commit\n", encoding="utf-8")
        inspection = model_library.inspect_removable_home(
            self.hub,
            cache_root=self.cache_root,
            model_id=self.model_id,
            revision="unknown",
            rank=0,
            node_id=self.node_id,
        )
        codes = {item["code"] for item in inspection["blockers"]}
        self.assertIn("unknown-revision", codes)
        self.assertEqual(inspection["state"], "blocked")

    def test_non_40_hex_ref_stays_unbound_not_eligible(self) -> None:
        for value in ("a" * 41, "a" * 64):
            (self.hub / "refs" / "main").write_text(value + "\n", encoding="utf-8")
            inspection = model_library.inspect_removable_home(
                self.hub,
                cache_root=self.cache_root,
                model_id=self.model_id,
                revision="unknown",
                rank=0,
                node_id=self.node_id,
            )
            codes = {item["code"] for item in inspection["blockers"]}
            self.assertIn("unknown-revision", codes)
            self.assertIsNone(inspection["bound_revision"])
            self.assertEqual(inspection["state"], "blocked")

    def test_blob_payload_mutation_after_plan_refuses_execute(self) -> None:
        blobs = self.hub / "blobs"
        blobs.mkdir()
        blob = blobs / "payload"
        blob.write_bytes(b"old-bytes")
        plan = self._plan(allow_last_home=True)
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(plan["occupancy_class"], "incomplete-hub")
        blob.write_bytes(b"new-bytes")
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
        self.assertEqual(blob.read_bytes(), b"new-bytes")

    def test_stub_with_complete_survivor_skips_primary_and_last_home(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        catalog["models"].append(
            {
                "model_id": self.model_id,
                "revision": self.revision,
                "identity_key": f"{self.model_id}@{self.revision}",
                "validation": "unvalidated",
                "profiles": [],
                "profile_validation": [],
                "homes": [
                    {
                        "rank": 1,
                        "node_id": "node-b",
                        "hostname": "fixture-alternate",
                        "ssh_host": "fixture-alternate",
                        "cache_root": "/alternate/cache",
                        "hub_path": "/alternate/cache/hub/models--Qwen--Qwen3-1.7B",
                        "state": "complete",
                        "home_class": "occupancy",
                        "occupancy": True,
                        "bytes": 1,
                        "active": True,
                        "primary": True,
                    }
                ],
                "duplicate": False,
                "has_primary": True,
                "on_disk": True,
            }
        )
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        plan = self._plan(allow_last_home=False, query=f"{self.model_id}@unknown")
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(plan["occupancy_class"], "incomplete-hub")
        self.assertFalse(plan["target"]["last_durable_home"])
        self.assertNotIn("last-durable-home", self._kinds(plan))
        self.assertNotIn("primary-selection-required", self._kinds(plan))
        self.assertNotIn("selected-primary-home", self._kinds(plan))

    def test_select_unknown_catalog_row_is_inspectable(self) -> None:
        target = model_library.select_home_removal_target(
            self.catalog_path,
            "qwen",
        )
        self.assertEqual(target["occupancy_class"], "incomplete-hub")
        self.assertTrue(target["identity_unbound"])
        exact = model_library.select_home_removal_target(
            self.catalog_path,
            f"{self.model_id}@{self.revision}",
        )
        self.assertEqual(exact["home"]["hub_path"], str(self.hub))
        self.assertEqual(exact["revision"], self.revision)


class LastHomeArchiveGuard(HomeRemovalFixture, unittest.TestCase):
    def _receipt_for_catalog(self) -> dict[str, object]:
        from scripts import model_library_receipt as source_attested
        from scripts.testlib import model_library_receipt_fixture as fixture

        source = fixture.build_source(
            model_id=self.model_id, snapshot_revision=self.revision
        )
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile="qwen"
        )
        observations = []
        for rank in (0, 1):
            cache = self.root / f"acquire-cache-{rank}"
            cache.mkdir(exist_ok=True)
            observations.append(
                fixture.observation(
                    cache,
                    rank=rank,
                    node_id=f"node-{rank}" if rank else self.node_id,
                    model_id=self.model_id,
                    revision=self.revision,
                    content_bytes=source["content_bytes"],
                    available_bytes=10**12,
                    hf_cli="hf",
                )
            )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=source,
            identity=identity,
            observations=observations,
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        hub = self.root / "receipt-hub"
        fixture.write_snapshot_hub(hub, revision=self.revision)
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=self.revision,
            allow_empty_files=True,
        )
        return source_attested.build_source_attested_acquisition_receipt(
            source=source,
            identity=identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )

    def test_unbound_complete_does_not_clear_last_occupancy(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        home = catalog["models"][0]["homes"][0]
        home["home_class"] = "occupancy"
        home["occupancy"] = True
        catalog["models"][0]["homes"].append(
            {
                "rank": 1,
                "node_id": "node-b",
                "hostname": "fixture-unbound",
                "ssh_host": "fixture-unbound",
                "cache_root": "/unbound/cache",
                "hub_path": "/unbound/cache/hub/models--Qwen--Qwen3-1.7B",
                "state": "complete",
                "bytes": 1,
                "active": False,
                "primary": False,
                "home_class": "unbound-complete",
                "occupancy": False,
            }
        )
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        plan = self._plan(allow_last_home=False)
        self.assertTrue(plan["target"]["last_durable_home"])
        self.assertIn("last-durable-home", self._kinds(plan))
        self.assertNotIn("selected-primary-home", self._kinds(plan))
        self.assertNotIn("primary-selection-required", self._kinds(plan))

    def test_removing_unbound_does_not_require_last_home_or_primary(self) -> None:
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        home = catalog["models"][0]["homes"][0]
        home["home_class"] = "occupancy"
        home["occupancy"] = True
        catalog["models"][0]["homes"].append(
            {
                "rank": 1,
                "node_id": "node-b",
                "hostname": "fixture-unbound",
                "ssh_host": "fixture-unbound",
                "cache_root": "/unbound/cache",
                "hub_path": "/unbound/cache/hub/models--Qwen--Qwen3-1.7B",
                "state": "complete",
                "bytes": 1,
                "active": False,
                "primary": False,
                "home_class": "unbound-complete",
                "occupancy": False,
            }
        )
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        target = model_library.select_home_removal_target(
            self.catalog_path, "qwen", node_selector="1"
        )
        self.assertFalse(target["last_durable_home"])
        self.assertFalse(target["selected_is_occupancy"])
        self.assertEqual(
            [home["home_class"] for home in target["alternate_homes"]],
            ["occupancy"],
        )

    def test_corrupt_receipt_store_fails_closed(self) -> None:
        library_dir = self.root / "corrupt-library"
        store = library_dir / "download-receipts"
        store.mkdir(parents=True)
        (store / ("b" * 64 + ".json")).write_text("{not-json", encoding="utf-8")
        with self.assertRaises(Exception):
            self._plan(library_dir=library_dir)

    def test_truncated_archive_blocks_last_occupancy(self) -> None:
        from scripts import model_library_cold_archive as cold_archive
        from scripts import model_library_receipt as source_attested

        receipt = self._receipt_for_catalog()
        library_dir = self.root / "archive-library"
        source_attested.write_source_attested_receipt(library_dir, receipt)
        cold_root = self.root / "cold"
        cold_root.mkdir()
        hub = self.root / "receipt-hub"
        cold_archive.publish_verified_recovery_set(cold_root, receipt, hub)
        weight = (
            cold_root
            / "pulsar-receipts"
            / receipt["receipt_id"]
            / "home"
            / "snapshots"
            / self.revision
            / "model.safetensors"
        )
        weight.write_bytes(b"truncated")
        env = {"PULSAR_COLD_ROOT": str(cold_root)}
        with mock.patch.dict(os.environ, env, clear=False):
            plan = self._plan(library_dir=library_dir)
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("unarchived-last-home", self._kinds(plan))
        with mock.patch.dict(os.environ, env, clear=False):
            allowed = self._plan(
                library_dir=library_dir, allow_unarchived_last_home=True
            )
        self.assertEqual(allowed["state"], "eligible")

    def test_model_archive_without_receipt_replica_blocks_last_occupancy(self) -> None:
        from scripts import model_library_cold_archive as cold_archive
        from scripts import model_library_receipt as source_attested

        receipt = self._receipt_for_catalog()
        library_dir = self.root / "archive-library"
        source_attested.write_source_attested_receipt(library_dir, receipt)
        cold_root = self.root / "cold"
        cold_root.mkdir()
        hub = self.root / "receipt-hub"
        cold_archive.publish_verified_archive(cold_root, receipt, hub)
        env = {"PULSAR_COLD_ROOT": str(cold_root)}
        with mock.patch.dict(os.environ, env, clear=False):
            plan = self._plan(library_dir=library_dir)
        self.assertEqual(plan["state"], "blocked")
        self.assertIn("unarchived-last-home", self._kinds(plan))
        self.assertIn("receipt", plan["blockers"][0]["detail"])

    def test_operator_selected_same_device_recovery_set_is_eligible(self) -> None:
        from scripts import model_library_cold_archive as cold_archive
        from scripts import model_library_receipt as source_attested

        receipt = self._receipt_for_catalog()
        library_dir = self.root / "archive-library"
        source_attested.write_source_attested_receipt(library_dir, receipt)
        cold_root = self.root / "cold"
        cold_root.mkdir()
        hub = self.root / "receipt-hub"
        cold_archive.publish_verified_recovery_set(cold_root, receipt, hub)
        with mock.patch.dict(
            os.environ, {"PULSAR_COLD_ROOT": str(cold_root)}, clear=False
        ):
            plan = self._plan(library_dir=library_dir)
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(plan["blockers"], [])

    def test_execute_does_not_open_controller_receipt_store(self) -> None:
        plan = self._plan()
        self.assertEqual(plan["state"], "eligible")
        self.assertNotIn("library_dir", plan)
        result = model_library.execute_home_removal_plan(
            plan, rank=0, node_id=self.node_id
        )
        self.assertEqual(result["state"], "removed")
        self.assertFalse(self.hub.exists())

    def test_controller_reverify_catches_truncated_archive_before_detach(self) -> None:
        from scripts import model_library_cold_archive as cold_archive
        from scripts import model_library_receipt as source_attested

        receipt = self._receipt_for_catalog()
        library_dir = self.root / "archive-library"
        source_attested.write_source_attested_receipt(library_dir, receipt)
        cold_root = self.root / "cold"
        cold_root.mkdir()
        hub = self.root / "receipt-hub"
        cold_archive.publish_verified_recovery_set(cold_root, receipt, hub)
        env = {"PULSAR_COLD_ROOT": str(cold_root)}
        with mock.patch.dict(os.environ, env, clear=False):
            plan = self._plan(library_dir=library_dir)
        self.assertEqual(plan["state"], "eligible")
        self.assertEqual(plan["receipt_id"], receipt["receipt_id"])
        weight = (
            cold_root
            / "pulsar-receipts"
            / receipt["receipt_id"]
            / "home"
            / "snapshots"
            / self.revision
            / "model.safetensors"
        )
        weight.write_bytes(b"truncated-after-plan")
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(
                (model_library.ModelLibraryError, cold_archive.ColdArchiveError),
                "cold archive|rehash",
            ):
                cold_archive.reverify_last_home_archive(
                    plan, library_dir=library_dir
                )
        self.assertTrue(self.hub.is_dir())

    def test_reverify_fails_if_planned_receipt_disappears(self) -> None:
        from scripts import model_library_cold_archive as cold_archive
        from scripts import model_library_receipt as source_attested

        receipt = self._receipt_for_catalog()
        library_dir = self.root / "archive-library"
        source_attested.write_source_attested_receipt(library_dir, receipt)
        cold_root = self.root / "cold"
        cold_root.mkdir()
        hub = self.root / "receipt-hub"
        cold_archive.publish_verified_recovery_set(cold_root, receipt, hub)
        env = {"PULSAR_COLD_ROOT": str(cold_root)}
        with mock.patch.dict(os.environ, env, clear=False):
            plan = self._plan(library_dir=library_dir)
        receipt_path = (
            library_dir / "download-receipts" / f"{receipt['receipt_id']}.json"
        )
        receipt_path.unlink()
        with mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaisesRegex(
                Exception, "receipt is missing|receipt store"
            ):
                cold_archive.reverify_last_home_archive(
                    plan, library_dir=library_dir
                )
        self.assertTrue(self.hub.is_dir())

    def test_home_remove_cli_reverifies_before_detach(self) -> None:
        source = (REPO_ROOT / "scripts" / "model-library.sh").read_text(
            encoding="utf-8"
        )
        remove = source.split("cmd_home_remove()", 1)[1]
        reverify_at = remove.index("reverify-last-home-archive")
        detach_at = remove.index("detach-current-home")
        execute_at = remove.index('execute_home_removal_on_rank "$plan"')
        self.assertLess(reverify_at, detach_at)
        self.assertLess(detach_at, execute_at)


if __name__ == "__main__":
    unittest.main()
