#!/usr/bin/env python3
"""Structured startup-evidence contracts for sealed model-library launches."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import startup_metric  # noqa: E402


class ModelLibraryStartupMetricContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = pathlib.Path(self.temporary.name) / "startup.json"
        self.arguments = {
            "output": str(self.output),
            "profile": "fixture-2node",
            "model": "Org/Fixture",
            "nodes": 2,
            "topology_id": "a" * 64,
            "owner_node_id": "fixture-node-a",
            "content_id": "b" * 12,
            "content_digest": "c" * 64,
            "transport": "ssh-roce",
            "integrity_scheme": "sha256-snapshot-manifest-v1",
            "model_revision": "d" * 40,
            "identity_status": "match",
            "model_seal_id": "e" * 64,
            "validation_bundle_id": "f" * 64,
            "runtime_model_path": (
                "/root/.cache/huggingface/hub/models--Org--Fixture/"
                + "snapshots/"
                + "d" * 40
            ),
            "tag": "schema2-roce8",
            "started_at": "2026-08-10T00:00:00.000Z",
            "first_healthy_at": "2026-08-10T00:05:00.000Z",
            "elapsed_seconds": 300.0,
        }

    def invoke(self, **overrides: object) -> dict[str, object]:
        arguments = dict(self.arguments)
        arguments.update(overrides)
        startup_metric.command_startup_metric(argparse.Namespace(**arguments))
        return json.loads(self.output.read_text(encoding="utf-8"))

    def test_metric_binds_sealed_content_transport_and_redacted_owner(self) -> None:
        metric = self.invoke()
        self.assertEqual(metric["schema_version"], 2)
        self.assertEqual(metric["weight_source"], "library-hot")
        self.assertEqual(metric["cache_state"], "sealed-hot")
        self.assertEqual(metric["content_id"], "b" * 12)
        self.assertEqual(metric["content_digest"], "c" * 64)
        self.assertEqual(metric["transport"], "ssh-roce")
        self.assertEqual(
            metric["integrity_scheme"], "sha256-snapshot-manifest-v1"
        )
        self.assertEqual(metric["model_revision"], "d" * 40)
        self.assertEqual(metric["identity_status"], "match")
        self.assertEqual(metric["model_seal_id"], "e" * 64)
        self.assertEqual(metric["validation_bundle_id"], "f" * 64)
        self.assertTrue(metric["runtime_model_path"].endswith("d" * 40))
        self.assertEqual(
            metric["owner_node_fingerprint"],
            hashlib.sha256(b"fixture-node-a").hexdigest()[:16],
        )
        self.assertNotIn(
            "fixture-node-a", self.output.read_text(encoding="utf-8")
        )

    def test_existing_evidence_is_never_overwritten(self) -> None:
        self.invoke()
        with self.assertRaisesRegex(
            startup_metric.StartupMetricError, "new bounded path"
        ):
            self.invoke()

    def test_cli_rejects_retired_mode_flags(self) -> None:
        parser = startup_metric.build_parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "startup-metric",
                    "--output",
                    str(self.output),
                    "--profile",
                    "fixture-2node",
                    "--model",
                    "Org/Fixture",
                    "--weight-source",
                    "library-hot",
                    "--nodes",
                    "2",
                    "--topology-id",
                    "a" * 64,
                    "--started-at",
                    "2026-08-10T00:00:00.000Z",
                    "--first-healthy-at",
                    "2026-08-10T00:05:00.000Z",
                    "--elapsed-seconds",
                    "1",
                ]
            )

    def test_incomplete_or_mislabeled_hot_evidence_fails_closed(self) -> None:
        for overrides, message in (
            ({"content_digest": None}, "content digest"),
            ({"content_id": "short"}, "content identity"),
            ({"transport": None}, "transport"),
            ({"model_revision": None}, "model revision"),
            ({"model_seal_id": None}, "model seal"),
            ({"runtime_model_path": "Org/Fixture"}, "exact revision"),
        ):
            with self.subTest(overrides=overrides):
                if self.output.exists():
                    self.output.unlink()
                with self.assertRaisesRegex(
                    startup_metric.StartupMetricError,
                    message,
                ):
                    self.invoke(**overrides)


if __name__ == "__main__":
    unittest.main()
