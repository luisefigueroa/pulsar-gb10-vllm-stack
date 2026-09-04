#!/usr/bin/env python3
"""Contracts for the baseline-v1 run record and policy-derived run arguments."""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "validate"))

import baseline_run  # noqa: E402
from baseline_v1_policy import load_policy  # noqa: E402

POLICY = REPO_ROOT / "policy" / "baseline-v1.json"
HEX64 = "a" * 64


class BaselineRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-baseline-run-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_run_args_follow_the_committed_policy(self) -> None:
        policy, _digest = load_policy(POLICY)
        values = baseline_run.run_args(policy)
        gsm8k = next(g for g in policy["gates"] if g["criterion_id"] == "gsm8k-subset")
        self.assertEqual(values["GSM8K_DATASET_REVISION"], gsm8k["pins"]["dataset_revision"])
        self.assertEqual(values["GSM8K_DATASET_SHA256"], gsm8k["pins"]["dataset_file_sha256"])
        self.assertEqual(values["GSM8K_SAMPLE_SIZE"], "100")
        self.assertEqual(values["SOAK_MINUTES"], "60")
        self.assertEqual(values["PERF_CONCURRENCIES"], "1 2 4 8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = baseline_run.main(["run-args", "--policy", str(POLICY)])
        self.assertEqual(code, 0)
        self.assertIn("GSM8K_REASONING_MODE=enabled\n", out.getvalue())

    def _write(self, extra: list[str]) -> tuple[int, pathlib.Path, str]:
        out = self.root / "run.json"
        argv = [
            "write", "--out", str(out), "--spec-id", HEX64, "--policy-digest", "b" * 64,
            "--lab-commit", "c" * 40, "--image-digest", "sha256:" + "d" * 64,
            "--launch-contract-id", "e" * 64, "--witness-before", "abc123@2026-09-04T12:19:00.123Z",
            "--witness-after", "abc123@2026-09-04T12:19:00.123Z",
        ] + extra
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = baseline_run.main(argv)
        return code, out, err.getvalue()

    def test_record_carries_gates_and_same_boot(self) -> None:
        code, out, _err = self._write([
            "--gate", "verify-snapshot-manifest:2026-09-04T10:00:00Z:2026-09-04T10:00:03Z:0",
            "--gate", "serve-smoke:2026-09-04T10:00:03Z:2026-09-04T10:00:09Z:0",
            "--proposed-status", "stable",
        ])
        self.assertEqual(code, 0)
        record = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(record["kind"], "pulsar-baseline-run")
        self.assertTrue(record["boot_witness"]["same_boot"])
        self.assertEqual([g["name"] for g in record["gates"]], ["verify-snapshot-manifest", "serve-smoke"])
        self.assertEqual(record["proposed_status"], "stable")

    def test_changed_witness_is_recorded_as_a_different_boot(self) -> None:
        out = self.root / "run.json"
        record = baseline_run.build_run_record(
            spec_id=HEX64, policy_digest="b" * 64, lab_commit="c" * 40,
            image_digest="sha256:" + "d" * 64, launch_contract_id="e" * 64,
            witness_before="abc123@2026-09-04T12:19:00Z", witness_after="abc123@2026-09-04T13:00:00Z", gates=[], proposed_status=None,
        )
        self.assertFalse(record["boot_witness"]["same_boot"])
        self.assertIsNone(record["proposed_status"])
        self.assertFalse(out.exists())

    def test_rejects_out_of_order_duplicate_and_malformed_gates(self) -> None:
        code, _out, err = self._write([
            "--gate", "serve-smoke:2026-09-04T10:00:00Z:2026-09-04T10:00:01Z:0",
            "--gate", "verify-snapshot-manifest:2026-09-04T10:00:01Z:2026-09-04T10:00:02Z:0",
        ])
        self.assertEqual(code, 2)
        self.assertIn("policy order", err)
        code, _out, err = self._write([
            "--gate", "serve-smoke:2026-09-04T10:00:00Z:2026-09-04T10:00:01Z:0",
            "--gate", "serve-smoke:2026-09-04T10:00:01Z:2026-09-04T10:00:02Z:0",
        ])
        self.assertEqual(code, 2)
        self.assertIn("twice", err)
        code, _out, err = self._write(["--gate", "serve-smoke:2026-09-04 10:00:00Z:2026-09-04T10:00:01Z:0"])
        self.assertEqual(code, 2)
        self.assertIn("ISO-8601", err)
        code, _out, err = self._write(["--gate", "serve-smoke:now:later:0"])
        self.assertEqual(code, 2)
        self.assertIn("ISO-8601", err)
        code, _out, err = self._write(["--gate", "serve-smoke:2026-09-04T10:00:00Z:2026-09-04T10:00:01Z:0", "--proposed-status", "validated"])
        self.assertEqual(code, 2)
        self.assertIn("stable or failed", err)

    def test_rejects_private_strings_and_bad_digests(self) -> None:
        with self.assertRaises(baseline_run.BaselineRunError):
            baseline_run.build_run_record(
                spec_id=HEX64, policy_digest="b" * 64, lab_commit="c" * 40,
                image_digest="1c8e60a0", launch_contract_id="e" * 64,
                witness_before="w@1", witness_after="w@1", gates=[], proposed_status=None,
            )
        with self.assertRaises(baseline_run.BaselineRunError):
            baseline_run.build_run_record(
                spec_id=HEX64, policy_digest="b" * 64, lab_commit="not-a-commit",
                image_digest="sha256:" + "d" * 64, launch_contract_id="e" * 64,
                witness_before="w@1", witness_after="w@1", gates=[], proposed_status=None,
            )
        with self.assertRaises(baseline_run.BaselineRunError):
            baseline_run.build_run_record(
                spec_id=HEX64, policy_digest="b" * 64, lab_commit="c" * 40,
                image_digest="sha256:" + "d" * 64, launch_contract_id="e" * 64,
                witness_before="has space", witness_after="has space", gates=[], proposed_status=None,
            )


if __name__ == "__main__":
    unittest.main()
