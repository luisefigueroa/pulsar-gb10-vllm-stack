#!/usr/bin/env python3
"""Contracts for the baseline-v1 policy and evaluator."""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "validate"))

from release_spec import load_spec  # noqa: E402
from baseline_v1 import main as evaluate_main  # noqa: E402
from baseline_v1_policy import (  # noqa: E402
    BaselinePolicyError,
    load_policy,
)
import validator_measurement as measurement  # noqa: E402

POLICY = REPO_ROOT / "policy" / "baseline-v1.json"
FIXTURES = REPO_ROOT / "scripts" / "testdata" / "baseline-v1"
INPUT_SPEC = FIXTURES / "input-spec.json"
LAB_COMMIT = "d" * 40
EVIDENCE_PREFIX = "results/example/"
PINNED_POLICY_DIGEST = (
    "0bc35c33af7d89ad048768e63947ddf8eec6a798907c4eee084f0c4e6e867416"
)
GOLDEN_CASES = (
    "pass",
    "same-boot-mismatch",
    "soak-one-error",
    "missing-soak",
    "accuracy-under-floor",
    "accuracy-override",
)


class BaselineV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="pulsar-baseline-v1-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmpdir, ignore_errors=True))

    def stage_measurements(self, source: Path) -> Path:
        dest = self.tmpdir / source.parent.name / "measurements"
        shutil.copytree(source, dest)
        return dest

    def run_eval(
        self,
        *,
        measurements: Path,
        out: Path,
        spec: Path = INPUT_SPEC,
        policy: Path = POLICY,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = evaluate_main(
                [
                    "--spec",
                    str(spec),
                    "--policy",
                    str(policy),
                    "--measurements-dir",
                    str(measurements),
                    "--lab-commit",
                    LAB_COMMIT,
                    "--out",
                    str(out),
                    "--evidence-path-prefix",
                    EVIDENCE_PREFIX,
                ]
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_committed_policy_digest_is_pinned(self) -> None:
        policy, digest = load_policy(POLICY)
        self.assertEqual(digest, PINNED_POLICY_DIGEST)
        self.assertEqual(policy["accuracy_floor_overrides"], {})
        self.assertEqual(policy["suite"], "baseline-v1")

    def test_policy_noncanonical_fails_without_fallback(self) -> None:
        with self.assertRaisesRegex(BaselinePolicyError, "canonical encoding"):
            load_policy(FIXTURES / "policy-noncanonical.json")

    def test_goldens_verify_and_measurement_fixtures_validate(self) -> None:
        for name in GOLDEN_CASES:
            spec = load_spec(FIXTURES / name / "expected.json")
            self.assertEqual(spec["state"], "measured")
            self.assertEqual(spec["review"], {})
            directory = FIXTURES / name / "measurements"
            for path in sorted(directory.glob("*.json")):
                measurement.validate_measurement(
                    json.loads(path.read_text(encoding="utf-8"))
                )

    def test_pass_is_stable_and_byte_identical(self) -> None:
        measurements = self.stage_measurements(FIXTURES / "pass" / "measurements")
        first = self.tmpdir / "pass-a.json"
        second = self.tmpdir / "pass-b.json"
        code, stdout, stderr = self.run_eval(measurements=measurements, out=first)
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"policy_digest={PINNED_POLICY_DIGEST}", stdout)
        self.assertIn("identity-snapshot-manifest pass", stdout)
        self.assertIn("proposed_status=stable", stdout)
        self.assertNotIn("validated", stdout)
        self.assertEqual(
            first.read_bytes(),
            (FIXTURES / "pass" / "expected.json").read_bytes(),
        )
        code, _stdout, stderr = self.run_eval(measurements=measurements, out=second)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.atomic_write_bytes(first, first.read_bytes())

    def test_same_boot_mismatch_fails_compare_gate(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "same-boot-mismatch" / "measurements"
        )
        out = self.tmpdir / "mismatch.json"
        code, stdout, stderr = self.run_eval(measurements=measurements, out=out)
        self.assertEqual(code, 0, stderr)
        self.assertIn("strict-same-boot-captures fail", stdout)
        self.assertIn("proposed_status=failed", stdout)
        self.assertEqual(
            out.read_bytes(),
            (FIXTURES / "same-boot-mismatch" / "expected.json").read_bytes(),
        )

    def test_soak_one_error_fails_soak_gate(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "soak-one-error" / "measurements"
        )
        out = self.tmpdir / "soak.json"
        code, stdout, stderr = self.run_eval(measurements=measurements, out=out)
        self.assertEqual(code, 0, stderr)
        self.assertIn("soak-60 fail", stdout)
        self.assertIn("proposed_status=failed", stdout)
        self.assertEqual(
            out.read_bytes(),
            (FIXTURES / "soak-one-error" / "expected.json").read_bytes(),
        )

    def test_missing_soak_is_incomplete(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "missing-soak" / "measurements"
        )
        out = self.tmpdir / "missing.json"
        code, stdout, stderr = self.run_eval(measurements=measurements, out=out)
        self.assertEqual(code, 0, stderr)
        self.assertIn("soak-60 incomplete", stdout)
        self.assertIn("proposed_status=failed", stdout)
        spec = json.loads(out.read_text(encoding="utf-8"))
        soak = next(
            item for item in spec["measurements"] if item["criterion_id"] == "soak-60"
        )
        self.assertEqual(soak["outcome"], "incomplete")
        self.assertEqual(soak["evidence_ids"], [])
        self.assertFalse(
            any(item["id"] == "validate-soak" for item in spec["evidence"])
        )
        self.assertEqual(
            out.read_bytes(),
            (FIXTURES / "missing-soak" / "expected.json").read_bytes(),
        )

    def test_accuracy_under_floor_fails(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "accuracy-under-floor" / "measurements"
        )
        out = self.tmpdir / "under.json"
        code, stdout, stderr = self.run_eval(measurements=measurements, out=out)
        self.assertEqual(code, 0, stderr)
        self.assertIn("gsm8k-subset fail", stdout)
        self.assertIn("proposed_status=failed", stdout)
        self.assertEqual(
            out.read_bytes(),
            (FIXTURES / "accuracy-under-floor" / "expected.json").read_bytes(),
        )

    def test_accuracy_override_applies_model_floor(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "accuracy-override" / "measurements"
        )
        out = self.tmpdir / "override.json"
        code, stdout, stderr = self.run_eval(
            measurements=measurements,
            out=out,
            policy=FIXTURES / "accuracy-override" / "policy.json",
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("gsm8k-subset fail", stdout)
        spec = json.loads(out.read_text(encoding="utf-8"))
        gsm8k = next(
            item
            for item in spec["measurements"]
            if item["criterion_id"] == "gsm8k-subset"
        )
        accuracy = next(
            item for item in gsm8k["thresholds"] if item["metric"] == "accuracy"
        )
        self.assertEqual(accuracy["value"], "0.99")
        self.assertEqual(
            out.read_bytes(),
            (FIXTURES / "accuracy-override" / "expected.json").read_bytes(),
        )

    def test_malformed_gsm8k_exits_without_output(self) -> None:
        measurements = self.stage_measurements(
            FIXTURES / "malformed-gsm8k" / "measurements"
        )
        out = self.tmpdir / "malformed.json"
        code, _stdout, stderr = self.run_eval(measurements=measurements, out=out)
        self.assertEqual(code, 2)
        self.assertIn("error:", stderr)
        self.assertFalse(out.exists())

    def test_spec_already_filled_exits_without_output(self) -> None:
        measurements = self.stage_measurements(FIXTURES / "pass" / "measurements")
        out = self.tmpdir / "filled.json"
        code, _stdout, stderr = self.run_eval(
            measurements=measurements,
            out=out,
            spec=FIXTURES / "spec-already-filled.json",
        )
        self.assertEqual(code, 2)
        self.assertIn("measurements must be empty", stderr)
        self.assertFalse(out.exists())

    def test_input_spec_is_empty_measured(self) -> None:
        spec = load_spec(INPUT_SPEC)
        self.assertEqual(spec["state"], "measured")
        self.assertEqual(spec["measurements"], [])
        self.assertEqual(spec["evidence"], [])
        self.assertEqual(spec["review"], {})


if __name__ == "__main__":
    unittest.main()
