#!/usr/bin/env python3
"""Contracts for protected receipts and receipt-indexed model archives."""

from __future__ import annotations

import json
import pathlib
import stat
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity, model_library  # noqa: E402
from scripts import model_library_cold_archive as cold_archive  # noqa: E402
from scripts import model_library_receipt as source_attested  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402


class ColdArchiveContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.library_dir = self.root / "library"
        self.cold_root = self.root / "cold"
        self.cold_root.mkdir()
        self.model_id = "Fixture/Unbound-Model"
        self.node_id = "node-1"
        self.rank = 1

    def _receipt(self) -> dict[str, object]:
        source = fixture.build_source(model_id=self.model_id)
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source, profile="unbound-fixture"
        )
        observations = []
        for rank in (0, 1):
            cache = self.root / f"cache-{rank}"
            cache.mkdir(exist_ok=True)
            observations.append(
                {
                    "rank": rank,
                    "node_id": f"node-{rank}",
                    "eligible": True,
                    "hf_cli": "hf",
                    "available_bytes": 10**12,
                    "target_state": "absent",
                    "cache_root": str(cache),
                    "hub_root": str(cache / "hub"),
                    "target_hub": str(
                        cache / "hub" / model_library.model_id_to_hub_dirname(self.model_id)
                    ),
                    "required_free_bytes": 1,
                    "required_content_bytes": 1,
                    "model_id": self.model_id,
                    "revision": fixture.COMMIT,
                    "writable": True,
                    "detail": "ok",
                    "schema_version": 1,
                    "kind": "pulsar-model-library-home-acquisition-observation",
                }
            )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=source,
            identity=identity,
            observations=observations,
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        hub = self.root / "source-hub"
        fixture.write_snapshot_hub(hub)
        observed = model_library.inspect_snapshot_blob_identities(
            hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        return source_attested.build_source_attested_acquisition_receipt(
            source=source,
            identity=identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )

    def test_enqueue_unavailable_without_cold_root(self) -> None:
        receipt = self._receipt()
        job = cold_archive.enqueue_cold_archive_job(
            self.library_dir, receipt, cold_root=None
        )
        self.assertEqual(job["state"], "unavailable")

    def test_publish_and_complete(self) -> None:
        receipt = self._receipt()
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        hub = self.root / "source-hub"
        presence = cold_archive.publish_verified_recovery_set(
            self.cold_root, receipt, hub
        )
        self.assertEqual(presence["state"], "complete")
        replica_path = (
            self.cold_root
            / "pulsar-control"
            / "download-receipts"
            / f"{receipt['receipt_id']}.json"
        )
        self.assertTrue(replica_path.is_file())
        self.assertEqual(stat.S_IMODE(replica_path.stat().st_mode), 0o600)
        self.assertFalse(
            (
                self.cold_root
                / "pulsar-receipts"
                / str(receipt["receipt_id"])
                / "receipt.json"
            ).exists()
        )
        self.assertTrue(
            cold_archive.archive_is_complete(
                library_dir=self.library_dir,
                receipt_id=receipt["receipt_id"],
                cold_root=str(self.cold_root),
            )
        )
        loaded = cold_archive.verify_existing_archive(self.cold_root, receipt)
        self.assertEqual(loaded["receipt_id"], receipt["receipt_id"])
        verified = cold_archive.verify_receipt_replica(self.cold_root, receipt)
        self.assertEqual(verified["state"], "verified")

    def test_archive_layout_without_receipt_replica_is_incomplete(self) -> None:
        receipt = self._receipt()
        hub = self.root / "source-hub"
        cold_archive.publish_verified_archive(self.cold_root, receipt, hub)
        self.assertFalse(
            cold_archive.archive_is_complete(
                library_dir=self.library_dir,
                receipt_id=str(receipt["receipt_id"]),
                cold_root=str(self.cold_root),
            )
        )

    def test_receipt_recovery_is_explicit_and_canonical(self) -> None:
        receipt = self._receipt()
        cold_archive.publish_receipt_replica(self.cold_root, receipt)
        self.assertFalse((self.library_dir / "download-receipts").exists())
        result = cold_archive.recover_receipt_replica(
            self.cold_root,
            self.library_dir,
            str(receipt["receipt_id"]),
        )
        self.assertEqual(result["state"], "recovered")
        recovered = source_attested.load_source_attested_receipt(
            self.library_dir,
            str(receipt["receipt_id"]),
            require_canonical=True,
        )
        self.assertEqual(recovered, receipt)

    def test_noncanonical_or_public_receipt_replica_is_refused(self) -> None:
        receipt = self._receipt()
        cold_archive.publish_receipt_replica(self.cold_root, receipt)
        path = (
            cold_archive.cold_receipt_replica_store(self.cold_root)
            / f"{receipt['receipt_id']}.json"
        )
        path.write_text(
            json.dumps(receipt, sort_keys=True),
            encoding="utf-8",
        )
        path.chmod(0o600)
        with self.assertRaisesRegex(Exception, "canonical"):
            cold_archive.load_receipt_replica(
                self.cold_root, str(receipt["receipt_id"])
            )
        path.write_bytes(model_identity.pretty_json_bytes(receipt))
        path.chmod(0o644)
        with self.assertRaisesRegex(Exception, "private"):
            cold_archive.load_receipt_replica(
                self.cold_root, str(receipt["receipt_id"])
            )

    def test_nested_cold_root_is_not_a_distinct_replica(self) -> None:
        hub = self.root / "source-hub"
        nested = hub / "nested-cold"
        nested.mkdir(parents=True)
        ok, detail = cold_archive.cold_root_is_distinct_replica(nested, hub)
        self.assertFalse(ok)
        self.assertIn("nested", detail)

    def test_mismatch_refuses_publish(self) -> None:
        receipt = self._receipt()
        other = self.root / "other-hub"
        other.mkdir()
        (other / "dummy").write_text("no", encoding="utf-8")
        with self.assertRaises(Exception):
            cold_archive.publish_verified_archive(self.cold_root, receipt, other)


if __name__ == "__main__":
    unittest.main()
