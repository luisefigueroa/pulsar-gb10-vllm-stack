#!/usr/bin/env python3
"""Contracts for the closed snapshot-manifest verification producer."""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "validate"))

import verify_snapshot_manifest  # noqa: E402
from release_spec.identity import identity_block, spec_id_for  # noqa: E402
from release_spec.normalize import build_snapshot_manifest, pretty_json_bytes  # noqa: E402
from release_spec.verify import verify_spec  # noqa: E402

GOLDEN = REPO_ROOT / "release_spec" / "tests" / "fixtures" / "golden_measured.json"
MODEL_ID = "example-org/example-model"
REVISION = "a" * 40
FILES = {
    "config.json": b'{"model_type": "example"}\n',
    "model.safetensors": b"weights!",
}


def _entry(name: str, data: bytes) -> dict[str, Any]:
    return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class VerifySnapshotManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-manifest-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.hub = self.root / "hub"
        self.snapshot = self.hub / "snapshots" / REVISION
        self.snapshot.mkdir(parents=True)
        for name, data in FILES.items():
            (self.snapshot / name).write_bytes(data)
        self.result = self.root / "verify-snapshot-manifest.json"

    def write_spec(self, extra_entries: list[dict[str, Any]] | None = None) -> pathlib.Path:
        entries = [_entry(name, data) for name, data in FILES.items()]
        entries.extend(extra_entries or [])
        manifest = build_snapshot_manifest(
            model_id=MODEL_ID, snapshot_revision=REVISION, files=entries
        )
        spec = json.loads(GOLDEN.read_text(encoding="utf-8"))
        spec["identity"]["snapshot_manifest"] = manifest
        spec["spec_id"] = spec_id_for(identity_block(spec))
        spec = verify_spec(spec)
        path = self.root / "spec.json"
        path.write_bytes(pretty_json_bytes(spec))
        return path

    def run_main(self, spec: pathlib.Path, hub: pathlib.Path | None = None) -> int:
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = verify_snapshot_manifest.main(
                [
                    "--spec",
                    str(spec),
                    "--hub",
                    str(hub or self.hub),
                    "--workers",
                    "2",
                    "--result-json",
                    str(self.result),
                ]
            )
        return code

    def document(self) -> dict[str, Any]:
        return json.loads(self.result.read_text(encoding="utf-8"))

    def test_matching_tree_is_complete_and_names_no_path(self) -> None:
        spec_path = self.write_spec()
        self.assertEqual(self.run_main(spec_path), 0)
        document = self.document()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        self.assertEqual(document["operation"], "verify-snapshot-manifest")
        self.assertEqual(document["program"], "validate/verify_snapshot_manifest.py")
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(document["reason"], "completed")
        payload = document["verify-snapshot-manifest"]
        self.assertEqual(payload["spec_id"], spec["spec_id"])
        self.assertEqual(
            payload["manifest_id"], spec["identity"]["snapshot_manifest"]["manifest_id"]
        )
        self.assertEqual(payload["expected_file_count"], 2)
        self.assertEqual(payload["matched_file_count"], 2)
        self.assertEqual(payload["mismatched_file_count"], 0)
        self.assertEqual(payload["missing_file_count"], 0)
        self.assertEqual(payload["extra_file_count"], 0)
        self.assertNotIn(str(self.root), json.dumps(document))
        self.assertNotIn("config.json", json.dumps(document))

    def test_changed_bytes_of_equal_size_count_as_mismatch(self) -> None:
        spec_path = self.write_spec()
        (self.snapshot / "model.safetensors").write_bytes(b"weightZ!")
        self.assertEqual(self.run_main(spec_path), 1)
        document = self.document()
        self.assertEqual(document["completion"], "complete")
        payload = document["verify-snapshot-manifest"]
        self.assertEqual(payload["matched_file_count"], 1)
        self.assertEqual(payload["mismatched_file_count"], 1)

    def test_size_change_counts_as_mismatch_without_hashing(self) -> None:
        spec_path = self.write_spec()
        (self.snapshot / "model.safetensors").write_bytes(b"weights!!")
        self.assertEqual(self.run_main(spec_path), 1)
        payload = self.document()["verify-snapshot-manifest"]
        self.assertEqual(payload["mismatched_file_count"], 1)
        self.assertEqual(payload["matched_file_count"], 1)

    def test_extra_and_missing_files_are_counted(self) -> None:
        spec_path = self.write_spec(
            extra_entries=[_entry("missing.bin", b"never materialized")]
        )
        (self.snapshot / "extra.txt").write_bytes(b"unexpected")
        self.assertEqual(self.run_main(spec_path), 1)
        document = self.document()
        self.assertEqual(document["completion"], "complete")
        payload = document["verify-snapshot-manifest"]
        self.assertEqual(payload["expected_file_count"], 3)
        self.assertEqual(payload["matched_file_count"], 2)
        self.assertEqual(payload["missing_file_count"], 1)
        self.assertEqual(payload["extra_file_count"], 1)

    def test_unwalkable_tree_writes_closed_incomplete_measurement(self) -> None:
        spec_path = self.write_spec()
        self.assertEqual(self.run_main(spec_path, hub=self.root / "absent"), 2)
        document = self.document()
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "mismatch")
        payload = document["verify-snapshot-manifest"]
        self.assertEqual(payload["expected_file_count"], 2)
        self.assertEqual(payload["matched_file_count"], 0)
        self.assertEqual(payload["missing_file_count"], 0)

    def test_partial_snapshot_is_incomplete(self) -> None:
        spec_path = self.write_spec()
        (self.snapshot / "model.safetensors.incomplete").write_bytes(b"")
        self.assertEqual(self.run_main(spec_path), 2)
        self.assertEqual(self.document()["completion"], "incomplete")

    def test_unusable_spec_writes_nothing(self) -> None:
        spec_path = self.root / "bad.json"
        spec_path.write_text("{\"kind\": \"nope\"}", encoding="utf-8")
        self.assertEqual(self.run_main(spec_path), 2)
        self.assertFalse(self.result.exists())

    def test_unmatched_helper_sums_every_difference(self) -> None:
        counts = verify_snapshot_manifest.zero_counts()
        counts["mismatched_file_count"] = 1
        counts["missing_file_count"] = 2
        counts["extra_file_count"] = 3
        self.assertEqual(verify_snapshot_manifest.unmatched(counts), 6)


if __name__ == "__main__":
    unittest.main()
