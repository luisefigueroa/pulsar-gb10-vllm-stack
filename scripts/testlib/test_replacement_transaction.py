#!/usr/bin/env python3
"""Contracts for the short-lived serving replacement transaction."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import replacement_transaction as tx  # noqa: E402


PROFILE = "deepseek-v4-flash"
CONTRACT = "1" * 64
TOPOLOGY = "2" * 64
REVISION = "3" * 40
SEAL = "4" * 64
BUNDLE = "5" * 64
CONTENT = "6" * 12
MANIFEST = "7" * 64


def inventory(*, source: str = "library-hot") -> dict[str, object]:
    common = {
        "io.pulsar.gb10.managed": "true",
        "io.pulsar.gb10.conf": PROFILE,
        "io.pulsar.gb10.topology": TOPOLOGY,
        "io.pulsar.gb10.weight-source": source,
        "io.pulsar.gb10.launch-contract": CONTRACT,
        "io.pulsar.gb10.spec-decode": "on",
    }
    ranks = []
    for index in range(2):
        labels = {
            **common,
            "io.pulsar.gb10.rank": str(index),
            "io.pulsar.gb10.node-id": f"node-{index}",
        }
        if source == "library-hot":
            labels.update({
                "io.pulsar.gb10.weight-owner": "node-1",
                "io.pulsar.gb10.weight-config": CONTENT,
                "io.pulsar.gb10.model-revision": REVISION,
                "io.pulsar.gb10.model-seal": SEAL,
                "io.pulsar.gb10.validation-bundle": BUNDLE,
                "io.pulsar.gb10.model-identity-status": "match",
            })
        ranks.append({
            "rank": str(index),
            "node": "head" if index == 0 else "worker",
            "running": True,
            "labels": labels,
        })
    service = {
        "conf": PROFILE,
        "state": "running",
        "ownership": "managed",
        "safe_to_stop": True,
        "complete": True,
        "observability": "complete",
        "expected_nodes": 2,
        "expected_ranks": ["0", "1"],
        "ranks": ranks,
        "weight_source": source,
        "launch_contract_id": CONTRACT,
        "spec_decode": "on",
        "model_revision": REVISION if source == "library-hot" else None,
        "model_seal_id": SEAL if source == "library-hot" else None,
        "validation_bundle_id": BUNDLE if source == "library-hot" else None,
        "model_identity_status": "match" if source == "library-hot" else None,
        "weight_owner_node_id": "node-1" if source == "library-hot" else None,
        "weight_configuration_id": CONTENT if source == "library-hot" else None,
    }
    return {
        "schema_version": 1,
        "topology_id": TOPOLOGY,
        "nodes": {
            "head": {"topology_index": 0, "node_id": "node-0"},
            "worker": {"topology_index": 1, "node_id": "node-1"},
        },
        "services": [service],
    }


def health(*, retention: str = "ephemeral", active: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-health",
        "state": "healthy",
        "catalog": {"topology_compatible": True},
        "models": [{
            "profiles": [PROFILE],
            "revision": REVISION,
            "expected_manifest": MANIFEST,
        }],
        "hot_instances": [
            {
                "rank": 0,
                "profile": PROFILE,
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "sealed-hot",
                "retention": retention,
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": active,
            },
            {
                "rank": 1,
                "profile": PROFILE,
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "durable-home",
                "retention": retention,
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": active,
            },
        ],
    }


class ReplacementTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, name: str, value: object) -> pathlib.Path:
        path = self.root / name
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
        return path

    def capture(self, *, source: str = "library-hot", report=None) -> pathlib.Path:
        inv = self.write("inventory.json", inventory(source=source))
        report_path = self.write("health.json", report) if report is not None else None
        output = self.root / "transaction.json"
        args = argparse.Namespace(
            inventory=str(inv),
            library_health=str(report_path) if report_path else None,
            profile=PROFILE,
            launch_contract_id=CONTRACT,
            output=str(output),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_capture(args)
        return output

    def test_capture_binds_geometry_spec_and_source(self) -> None:
        path = self.capture(report=health())
        saved = tx.validate_transaction(tx.load_json(path))
        service = saved["previous_service"]
        self.assertEqual(service["launch_contract_id"], CONTRACT)
        self.assertEqual(service["spec_decode"], "on")
        self.assertEqual(service["weight"]["source"], "library-hot")
        self.assertEqual(service["placement"]["mode"], "exact-topology")
        self.assertEqual(
            service["placement"]["ranks"],
            [{"rank": 0, "node_id": "node-0"}, {"rank": 1, "node_id": "node-1"}],
        )
        self.assertNotIn("hostname", path.read_text())
        self.assertNotIn("192.0.2", path.read_text())

    def test_legacy_replicated_service_is_refused_with_migration_advice(self) -> None:
        with self.assertRaisesRegex(tx.TransactionError, "library-only decision"):
            self.capture(source="replicated")

    def test_old_service_without_contract_label_is_refused(self) -> None:
        inv_value = inventory()
        inv_value["services"][0]["launch_contract_id"] = None
        inv = self.write("inventory.json", inv_value)
        args = argparse.Namespace(
            inventory=str(inv), library_health=None, profile=PROFILE,
            launch_contract_id=CONTRACT, output=str(self.root / "transaction.json"),
        )
        with self.assertRaisesRegex(tx.TransactionError, "launch-contract label"):
            tx.cmd_capture(args)
        self.assertFalse((self.root / "transaction.json").exists())

    def test_library_capture_requires_active_exact_views_and_records_retention(self) -> None:
        path = self.capture(source="library-hot", report=health())
        saved = tx.load_json(path)
        weight = saved["previous_service"]["weight"]
        self.assertEqual(weight["revision"], REVISION)
        self.assertEqual(weight["manifest_id"], MANIFEST)
        self.assertEqual(weight["original_retention"], "ephemeral")
        self.assertTrue(saved["temporary_retention"]["required"])
        bad = health(active=False)
        path.unlink()
        with self.assertRaisesRegex(tx.TransactionError, "not bound"):
            self.capture(source="library-hot", report=bad)

    def test_phase_is_monotonic_and_file_is_exclusive(self) -> None:
        path = self.capture(report=health())
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_phase(argparse.Namespace(path=str(path), to="retained"))
            tx.cmd_phase(argparse.Namespace(path=str(path), to="stopped"))
        self.assertEqual(tx.load_json(path)["phase"], "stopped")
        with self.assertRaisesRegex(tx.TransactionError, "invalid transaction phase"):
            tx.cmd_phase(argparse.Namespace(path=str(path), to="retained"))
        with self.assertRaisesRegex(tx.TransactionError, "already exists"):
            self.capture(report=health())

    def test_rollback_rejects_profile_topology_and_retention_drift(self) -> None:
        path = self.capture(source="library-hot", report=health())
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_phase(argparse.Namespace(path=str(path), to="retained"))
            tx.cmd_phase(argparse.Namespace(path=str(path), to="stopped"))
        inv_path = self.write("rollback-inventory.json", inventory(source="library-hot"))
        pinned_path = self.write("pinned.json", health(retention="pinned", active=False))
        args = argparse.Namespace(
            path=str(path), launch_contract_id=CONTRACT,
            inventory=str(inv_path), library_health=str(pinned_path),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_verify_rollback(args)
        args.launch_contract_id = "8" * 64
        with self.assertRaisesRegex(tx.TransactionError, "profile changed"):
            tx.cmd_verify_rollback(args)
        args.launch_contract_id = CONTRACT
        drifted = inventory(source="library-hot")
        drifted["topology_id"] = "9" * 64
        args.inventory = str(self.write("drifted-inventory.json", drifted))
        with self.assertRaisesRegex(tx.TransactionError, "topology changed"):
            tx.cmd_verify_rollback(args)
        args.inventory = str(inv_path)
        args.library_health = str(self.write("ephemeral.json", health(active=False)))
        with self.assertRaisesRegex(tx.TransactionError, "no longer pinned"):
            tx.cmd_verify_rollback(args)

    def test_transaction_tamper_and_library_drift_fail_closed(self) -> None:
        path = self.capture(source="library-hot", report=health())
        saved = tx.load_json(path)
        saved["previous_service"]["weight"]["runtime_views"] = []
        with self.assertRaisesRegex(tx.TransactionError, "runtime views are incomplete"):
            tx.validate_transaction(saved)

        saved = tx.load_json(path)
        saved["previous_service"]["weight"]["content_id"] = "6" * 64
        with self.assertRaisesRegex(tx.TransactionError, "saved content identity"):
            tx.validate_transaction(saved)

        path.unlink()
        path = self.capture(source="library-hot", report=health())
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_phase(argparse.Namespace(path=str(path), to="retained"))
            tx.cmd_phase(argparse.Namespace(path=str(path), to="stopped"))
        inv_path = self.write("exact-inventory.json", inventory(source="library-hot"))
        report = health(retention="pinned", active=False)
        report["models"][0]["expected_manifest"] = "9" * 64
        args = argparse.Namespace(
            path=str(path), launch_contract_id=CONTRACT,
            inventory=str(inv_path),
            library_health=str(self.write("manifest-drift.json", report)),
        )
        with self.assertRaisesRegex(tx.TransactionError, "catalog model identity"):
            tx.cmd_verify_rollback(args)
        args.library_health = str(self.write("active.json", health(retention="pinned")))
        with self.assertRaisesRegex(tx.TransactionError, "active reference"):
            tx.cmd_verify_rollback(args)

    def test_recovery_distinguishes_previous_running_stopped_and_ambiguous(self) -> None:
        path = self.capture(report=health())
        running_path = self.write("running.json", inventory())
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            tx.cmd_recovery_state(argparse.Namespace(path=str(path), inventory=str(running_path)))
        self.assertEqual(json.loads(stream.getvalue())["state"], "previous-running")
        moved = inventory()
        moved["services"][0]["ranks"][0]["labels"]["io.pulsar.gb10.node-id"] = "moved-node"
        moved_path = self.write("moved.json", moved)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            rc = tx.cmd_recovery_state(
                argparse.Namespace(path=str(path), inventory=str(moved_path))
            )
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(stream.getvalue())["state"], "ambiguous")
        with contextlib.redirect_stdout(io.StringIO()):
            tx.cmd_phase(argparse.Namespace(path=str(path), to="retained"))
            tx.cmd_phase(argparse.Namespace(path=str(path), to="stopped"))
        stopped = inventory()
        stopped["services"] = []
        stopped_path = self.write("stopped.json", stopped)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            tx.cmd_recovery_state(argparse.Namespace(path=str(path), inventory=str(stopped_path)))
        self.assertEqual(json.loads(stream.getvalue())["state"], "stopped")
        other = inventory()
        other["services"][0]["conf"] = "another-profile"
        other_path = self.write("other.json", other)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            rc = tx.cmd_recovery_state(
                argparse.Namespace(path=str(path), inventory=str(other_path))
            )
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(stream.getvalue())["state"], "ambiguous")
        partial = inventory()
        partial["services"][0]["state"] = "degraded"
        partial["services"][0]["complete"] = False
        partial_path = self.write("partial.json", partial)
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            rc = tx.cmd_recovery_state(
                argparse.Namespace(path=str(path), inventory=str(partial_path))
            )
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(stream.getvalue())["state"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
