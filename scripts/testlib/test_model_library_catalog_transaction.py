#!/usr/bin/env python3
"""Atomic catalog build plus receipt-occupancy classification contracts."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts import model_library_receipt as source_attested  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402


class CatalogTransactionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.library_dir = self.root / "library"
        self.cache_root = self.root / "cache"
        self.model_id = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
        self.profile = "nemotron-3-nano-30b-nvfp4"
        self.node_id = "node-0"
        self.hub = (
            self.cache_root
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )
        fixture.write_snapshot_hub(self.hub)
        self.receipt = self._write_receipt_and_attachment()
        self.homes_path = self.root / "homes.json"
        self.catalog_path = self.library_dir / "catalog.json"

    def _write_receipt_and_attachment(self) -> dict[str, object]:
        source = fixture.build_source(model_id=self.model_id)
        identity = source_attested.resolve_huggingface_v1_acquisition_identity(
            source=source,
            profile=self.profile,
        )
        observation = fixture.observation(
            self.cache_root,
            rank=0,
            node_id=self.node_id,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            content_bytes=int(source["content_bytes"]),
            available_bytes=10**12,
            target_state="absent",
        )
        plan, _handle = source_attested.plan_source_attested_acquisition(
            source=source,
            identity=identity,
            observations=[observation],
            serving_nodes=1,
            topology_generation="d" * 64,
        )
        observed = model_library.inspect_snapshot_blob_identities(
            self.hub,
            model_id=self.model_id,
            revision=fixture.COMMIT,
            allow_empty_files=True,
        )
        receipt = source_attested.build_source_attested_acquisition_receipt(
            source=source,
            identity=identity,
            approval=plan["approval"],
            observed_manifest=observed["manifest"],
        )
        source_attested.write_source_attested_receipt(self.library_dir, receipt)
        live = model_library.inspect_live_directory_identity(self.hub)
        source_attested.occupy_source_attested_home(
            self.library_dir,
            receipt=receipt,
            observed_manifest=observed["manifest"],
            node_id=self.node_id,
            durable_home_path=str(self.hub),
            directory_identity=live,
        )
        return receipt

    def _scan_homes(self) -> list[dict[str, object]]:
        return model_library.scan_hub_cache(
            self.cache_root,
            rank=0,
            node_id=self.node_id,
            hostname="fixture-0",
            ssh_host="local",
        )

    def _write_homes(self, homes: list[dict[str, object]] | None = None) -> None:
        self.homes_path.write_text(
            json.dumps(homes if homes is not None else self._scan_homes()),
            encoding="utf-8",
        )

    def _run_build(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "model_library.py"),
                "build",
                "--topology-id",
                "a" * 64,
                "--models-dir",
                str(REPO_ROOT / "models"),
                "--homes-json",
                str(self.homes_path),
                "--library-dir",
                str(self.library_dir),
                "--output",
                str(self.catalog_path),
                "--json",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            preexec_fn=lambda: os.umask(0o002),
        )

    def _model_entry(self, catalog: dict[str, object]) -> dict[str, object]:
        return next(
            entry
            for entry in catalog["models"]
            if entry["model_id"] == self.model_id and entry.get("homes")
        )

    def test_final_json_matches_private_catalog_and_primary_policy(self) -> None:
        extra_cache = self.root / "extra-cache"
        extra_hub = (
            extra_cache / "hub" / model_library.model_id_to_hub_dirname(self.model_id)
        )
        fixture.write_snapshot_hub(extra_hub)
        homes = self._scan_homes() + model_library.scan_hub_cache(
            extra_cache,
            rank=1,
            node_id="node-1",
            hostname="fixture-1",
            ssh_host="fixture-1",
        )
        self._write_homes(homes)
        result = self._run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        emitted = json.loads(result.stdout)
        stored = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.assertEqual(emitted, stored)
        self.assertEqual(stat.S_IMODE(self.catalog_path.stat().st_mode), 0o600)
        entry = self._model_entry(stored)
        occupancy = [home for home in entry["homes"] if home["occupancy"]]
        unbound = [
            home for home in entry["homes"] if home["home_class"] == "unbound-complete"
        ]
        self.assertEqual(len(occupancy), 1)
        self.assertEqual(len(unbound), 1)
        self.assertTrue(occupancy[0]["primary"])
        self.assertFalse(entry["duplicate"])

    def test_replaced_directory_at_same_path_is_not_occupancy(self) -> None:
        retired = self.root / "retired-home"
        os.rename(self.hub, retired)
        fixture.write_snapshot_hub(self.hub)
        self._write_homes()
        result = self._run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        stored = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        home = self._model_entry(stored)["homes"][0]
        self.assertFalse(home["occupancy"])
        self.assertEqual(home["home_class"], "unbound-complete")
        self.assertFalse(home["primary"])

    def test_corrupt_receipt_does_not_replace_prior_catalog(self) -> None:
        self._write_homes()
        first = self._run_build()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.catalog_path.read_bytes()
        receipt_path = (
            self.library_dir
            / "download-receipts"
            / f"{self.receipt['receipt_id']}.json"
        )
        receipt_path.write_text("{not-json", encoding="utf-8")
        failed = self._run_build()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("occupancy classification failed", failed.stderr)
        self.assertEqual(self.catalog_path.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(self.catalog_path.stat().st_mode), 0o600)

    def test_corrupt_attachment_does_not_replace_prior_catalog(self) -> None:
        self._write_homes()
        first = self._run_build()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.catalog_path.read_bytes()
        attachment_path = next((self.library_dir / "home-occupancy").glob("*.json"))
        attachment_path.write_text("[]\n", encoding="utf-8")
        failed = self._run_build()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("occupancy classification failed", failed.stderr)
        self.assertEqual(self.catalog_path.read_bytes(), before)

    def test_second_catalog_writer_command_is_removed(self) -> None:
        source = (
            REPO_ROOT / "scripts" / "model_library_receipt.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("classify-catalog-occupancy", source)
        self.assertNotIn("tmp_path.write_bytes(raw)", source)
        shell = (REPO_ROOT / "scripts" / "model-library.sh").read_text(
            encoding="utf-8"
        )
        refresh = shell.split("cmd_catalog_refresh()", 1)[1].split(
            "cmd_catalog_list()", 1
        )[0]
        self.assertIn('--library-dir "$LIBRARY_DIR"', refresh)
        self.assertNotIn("classify-catalog-occupancy", refresh)


if __name__ == "__main__":
    unittest.main()
