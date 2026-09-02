#!/usr/bin/env python3
"""Health inventory contracts, including untrusted schema-1/2 observation."""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402


class ModelLibraryHealthContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.hot_root = self.root / "hot"
        self.node_id = "private-node-a"
        self.topology_path = self.root / "topology.json"
        topology = {
            "schema_version": 1,
            "nodes": [{
                "rank": 0,
                "node_id": self.node_id,
                "hostname": "private-host-a",
                "ssh_host": "local",
                "control": {"interface": "mgmt0", "ip": "192.0.2.10"},
                "gpu": "NVIDIA GB10",
                "rdma": [],
            }],
            "links": [],
            "validation": {
                "class": "roce-full-mesh",
                "full_mesh": True,
                "connectivity_verified": True,
                "min_rails_per_pair": 0,
            },
        }
        topology["topology_id"] = topology_digest(topology)
        self.topology = topology
        self.topology_id = topology["topology_id"]
        self.topology_path.write_text(json.dumps(topology), encoding="utf-8")
        self.observations = self.root / "observations"
        self.observations.mkdir()
        (self.observations / "containers-0.jsonl").write_text("", encoding="utf-8")
        self.catalog_path = self.root / "catalog.json"

    def legacy_stamp(self, schema: int = 1, *, pinned: bool = False) -> dict[str, object]:
        stamp: dict[str, object] = {
            "schema_version": schema,
            "state": "pinned" if pinned else "ready",
            "profile": "tiny-profile",
            "model_id": "Fixture/Tiny",
            "revision": "a" * 40,
            "identity_key": f"Fixture/Tiny@{'a' * 40}",
            "home_node_id": self.node_id,
            "topology_id": self.topology_id,
            "content_id": "content",
            "content_digest": "b" * 64,
            "backend": "copy",
            "bytes_logical": 8,
            "activated_at": "2026-01-01T00:00:00.000Z",
            "pinned": pinned,
            "budget_bytes_accounted": 8,
        }
        if schema == 2:
            stamp["integrity"] = {
                "scheme": model_library.SNAPSHOT_INTEGRITY_SCHEME,
                "manifest": {"historical": True},
            }
        return stamp

    def write_legacy(self, schema: int = 1, *, pinned: bool = False) -> pathlib.Path:
        instance = self.hot_root / "tiny-profile-topology" / "content"
        metadata = instance / ".pulsar"
        metadata.mkdir(parents=True)
        (metadata / "hot.json").write_text(
            json.dumps(self.legacy_stamp(schema, pinned=pinned)), encoding="utf-8"
        )
        return instance

    def scan(self) -> dict[str, object]:
        return model_library.scan_hot_health(
            self.hot_root, rank=0, node_id=self.node_id
        )

    def write_scan(self, scan: dict[str, object] | None = None) -> None:
        (self.observations / "hot-0.json").write_text(
            json.dumps(scan or self.scan()), encoding="utf-8"
        )

    def write_catalog(
        self,
        *,
        duplicate: bool = False,
        stale_primary: bool = False,
        selected_primary: bool = False,
        unbound_reason: str | None = None,
    ) -> None:
        revision = "a" * 40
        homes = [{
            "rank": 0,
            "node_id": self.node_id,
            "hostname": "private-host-a",
            "ssh_host": "local",
            "cache_root": "/private/cache-a",
            "hub_path": "/private/cache-a/hub/models--Fixture--Tiny",
            "state": "complete",
            "home_class": (
                "unbound-complete" if unbound_reason else "occupancy"
            ),
            "occupancy": unbound_reason is None,
            "active": True,
            "bytes": 8,
            "primary": False,
        }]
        if duplicate:
            homes.append({
                "rank": 1,
                "node_id": "private-node-b",
                "hostname": "private-host-b",
                "ssh_host": "private-control-b",
                "cache_root": "/private/cache-b",
                "hub_path": "/private/cache-b/hub/models--Fixture--Tiny",
                "state": "complete",
                "home_class": "occupancy",
                "occupancy": True,
                "active": True,
                "bytes": 8,
                "primary": False,
            })
        if unbound_reason:
            homes[0]["unbound_reason"] = unbound_reason
        selections = []
        if selected_primary:
            selections = [{
                "identity_key": f"Fixture/Tiny@{revision}",
                "node_id": self.node_id,
                "selected_at": "2026-01-01T00:00:00.000Z",
            }]
        if stale_primary:
            selections = [{
                "identity_key": f"Fixture/Tiny@{revision}",
                "node_id": "missing-private-node",
                "selected_at": "2026-01-01T00:00:00.000Z",
            }]
        catalog = {
            "schema_version": 2,
            "refreshed_at": "2026-01-01T00:00:00.000Z",
            "topology_id": self.topology_id,
            "primary_selections": selections,
            "models": [{
                "model_id": "Fixture/Tiny",
                "revision": revision,
                "identity_key": f"Fixture/Tiny@{revision}",
                "homes": homes,
                "profiles": ["tiny-profile"],
                "profile_validation": [{
                    "profile": "tiny-profile",
                    "profile_status": "untested",
                    "identity_status": (
                        "unbound-complete"
                        if unbound_reason
                        else "receipt-occupancy"
                    ),
                }],
                "validation": (
                    "unbound-complete" if unbound_reason else "receipt-occupancy"
                ),
            }],
        }
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    def health(self) -> dict[str, object]:
        return model_library.build_health_report(
            catalog_path=self.catalog_path,
            topology_file=self.topology_path,
            topology_id=self.topology_id,
            observations_dir=self.observations,
        )

    def test_absent_catalog_is_not_configured_and_read_only(self) -> None:
        self.write_scan({
            "schema_version": 1,
            "kind": model_library.HOT_HEALTH_SCAN_KIND,
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.hot_root),
            "status": "ok",
            "instances": [],
            "errors": [],
        })
        before = set(self.root.rglob("*"))
        report = self.health()
        self.assertEqual(report["state"], "not-configured")
        self.assertEqual(report["catalog"]["status"], "absent")
        self.assertEqual(before, set(self.root.rglob("*")))

    def test_legacy_schema_one_and_two_are_recognized_but_untrusted(self) -> None:
        for schema in (1, 2):
            with self.subTest(schema=schema):
                if self.hot_root.exists():
                    import shutil
                    shutil.rmtree(self.hot_root)
                self.write_legacy(schema)
                item = self.scan()["instances"][0]
                self.assertEqual(item["metadata_status"], "legacy")
                self.assertEqual(item["identity_status"], "unknown")
                self.assertEqual(item["witness_status"], "not-applicable")
                self.assertFalse(item["repairable"])
                self.assertIsNone(item["repair_id"])

    def test_duplicate_and_stale_primary_are_structured_findings(self) -> None:
        self.write_scan({
            "schema_version": 1,
            "kind": model_library.HOT_HEALTH_SCAN_KIND,
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.hot_root),
            "status": "ok",
            "instances": [],
            "errors": [],
        })
        self.write_catalog(duplicate=True)
        report = self.health()
        self.assertEqual(report["models"][0]["duplicate_home"], "unresolved")
        self.assertIn("duplicate-home-unresolved", {x["code"] for x in report["issues"]})
        self.write_catalog(duplicate=True, selected_primary=True)
        report = self.health()
        self.assertEqual(report["models"][0]["duplicate_home"], "redundant")
        self.assertIn("duplicate-home-redundant", {x["code"] for x in report["issues"]})
        self.write_catalog(stale_primary=True)
        report = self.health()
        self.assertIn("primary-selection-stale", {x["code"] for x in report["issues"]})

    def test_unreceipted_complete_tree_is_visible_but_not_a_home(self) -> None:
        self.write_scan({
            "schema_version": 1,
            "kind": model_library.HOT_HEALTH_SCAN_KIND,
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.hot_root),
            "status": "ok",
            "instances": [],
            "errors": [],
        })
        self.write_catalog(unbound_reason="missing-receipt")
        report = self.health()
        model = report["models"][0]
        self.assertEqual(model["validation"], "unbound-complete")
        self.assertEqual(model["home_ranks"], [])
        self.assertIsNone(model["primary"]["rank"])
        issue = next(
            item
            for item in report["issues"]
            if item["code"] == "unbound-complete-no-receipt"
        )
        self.assertIn("no download receipt", issue["detail"])
        self.assertEqual(
            issue["remediation"]["command"],
            "scripts/model-library.sh cleanup-recommend",
        )

    def test_invalid_and_topology_stale_catalogs_are_visible(self) -> None:
        self.write_scan({
            "schema_version": 1,
            "kind": model_library.HOT_HEALTH_SCAN_KIND,
            "rank": 0,
            "node_id": self.node_id,
            "hot_root": str(self.hot_root),
            "status": "ok",
            "instances": [],
            "errors": [],
        })
        self.catalog_path.write_text("{not-json", encoding="utf-8")
        invalid = self.health()
        self.assertEqual(invalid["state"], "unavailable")
        self.assertNotIn(str(self.root), json.dumps(invalid))
        self.assertEqual(
            invalid["issues"][0]["detail"],
            "cached catalog is invalid or unreadable",
        )
        self.write_catalog()
        report = model_library.build_health_report(
            catalog_path=self.catalog_path,
            topology_file=self.topology_path,
            topology_id="different-topology",
            observations_dir=self.observations,
        )
        self.assertIn(
            "catalog-topology-stale",
            {item["code"] for item in report["issues"]},
        )

    def test_public_health_is_sanitized(self) -> None:
        self.write_legacy()
        self.write_scan()
        self.write_catalog()
        report = self.health()
        encoded = json.dumps(report)
        for private in (
            str(self.root), self.node_id, self.topology_id, "private-host-a",
            "192.0.2.10", "/private/cache-a",
        ):
            self.assertNotIn(private, encoded)
        self.assertIn("repair_id", encoded)
        self.assertFalse(report["hot_instances"][0]["repairable"])
        self.assertIsNone(report["hot_instances"][0]["repair_id"])
        self.assertIn("legacy-hot-metadata", {item["code"] for item in report["issues"]})
        self.assertFalse(any(
            (item.get("remediation") or {}).get("command")
            for item in report["issues"]
            if item.get("code") == "legacy-hot-metadata"
        ))
        self.assertEqual(
            report["catalog"]["refreshed_at"],
            "2026-01-01T00:00:00.000Z",
        )
        self.assertEqual(report["models"][0]["profiles"], ["tiny-profile"])

    def test_schema_three_witness_match_missing_and_drift_without_hash(self) -> None:
        model_id = "Fixture/Current"
        revision = "c" * 40
        instance = model_library.hot_instance_dir(
            self.hot_root, "current", self.topology_id, "current-content"
        )
        durable = self.root / "durable" / model_library.model_id_to_hub_dirname(model_id)
        snapshot = durable / "snapshots" / revision
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}\n", encoding="utf-8")
        (snapshot / "model.safetensors").write_bytes(b"tiny-weights")
        hub = model_library.hot_hub_path(instance, model_id)
        hub.parent.mkdir(parents=True)
        hub.symlink_to(durable, target_is_directory=True)
        manifest = model_library.build_snapshot_manifest(durable, model_id=model_id, revision=revision)
        validation = {
            "identity_status": "receipt-occupancy",
            "expected_seal": None,
            "observed_seal": model_library.observed_model_seal_projection(manifest),
        }
        stamp = model_library.build_hot_stamp(
            profile="current",
            model_id=model_id,
            identity_key=f"{model_id}@{revision}",
            revision=revision,
            topology_id=self.topology_id,
            home_node_id=self.node_id,
            content_id="current-content",
            content_digest=manifest["manifest_id"],
            integrity_manifest=manifest,
            validation=validation,
            backend="copy",
            bytes_logical=manifest["total_bytes"],
        )
        model_library.write_hot_stamp(instance, stamp)
        with mock.patch.object(model_library, "verify_snapshot_manifest", side_effect=AssertionError("hash path called")):
            missing = self.scan()["instances"][0]
            self.assertEqual(missing["witness_status"], "missing")
            observation = model_library.build_stable_hot_witness_observation(
                stamp, hub=hub, manifest=manifest, validation=validation
            )
            model_library.write_hot_witness(instance, model_library.finalize_hot_witness(observation))
            matched = self.scan()["instances"][0]
            self.assertEqual(matched["witness_status"], "match")
            (snapshot / "config.json").touch()
            drift = self.scan()["instances"][0]
            self.assertEqual(drift["witness_status"], "drift")
            hub.unlink()
            shutil.copytree(durable, hub)
            sealed = self.scan()["instances"][0]
            self.assertEqual(sealed["metadata_status"], "current")
            self.assertEqual(sealed["runtime_source"], "working-copy")
            model_library.hot_witness_path(instance).write_text(
                "{}\n", encoding="utf-8"
            )
            malformed = self.scan()["instances"][0]
            self.assertEqual(malformed["witness_status"], "malformed")

        stamp["unexpected"] = "not-schema-3"
        model_library.write_hot_stamp(instance, stamp)
        malformed_stamp = self.scan()["instances"][0]
        self.assertEqual(malformed_stamp["metadata_status"], "malformed")
        self.assertFalse(malformed_stamp["repairable"])
        self.assertIsNone(malformed_stamp["repair_id"])

    def test_malformed_legacy_and_symlink_layouts_are_never_repairable(self) -> None:
        instance = self.write_legacy()
        stamp_path = instance / ".pulsar" / "hot.json"
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        stamp["unexpected"] = True
        stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
        malformed = self.scan()["instances"][0]
        self.assertEqual(malformed["metadata_status"], "malformed")
        self.assertFalse(malformed["repairable"])
        self.assertIsNone(malformed["repair_id"])

        shutil.rmtree(self.hot_root)
        external_root = self.root / "external-hot"
        external_root.mkdir()
        self.hot_root.symlink_to(external_root, target_is_directory=True)
        root_scan = self.scan()
        self.assertEqual(root_scan["status"], "error")
        self.assertEqual(root_scan["instances"], [])

        self.hot_root.unlink()
        group = self.hot_root / "group"
        group.mkdir(parents=True)
        external_instance = self.root / "external-instance"
        external_instance.mkdir()
        (group / "content").symlink_to(
            external_instance, target_is_directory=True
        )
        instance_scan = self.scan()
        self.assertEqual(instance_scan["status"], "error")
        self.assertEqual(instance_scan["instances"], [])


if __name__ == "__main__":
    unittest.main()
