#!/usr/bin/env python3
"""Contracts for promoting a measured spec into a released document."""

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

from release_spec import load_spec  # noqa: E402
from scripts import release_spec_promote as promote  # noqa: E402

FIXTURES = REPO_ROOT / "scripts" / "testdata" / "baseline-v1"
PASS = FIXTURES / "pass" / "expected.json"
FAILED = FIXTURES / "soak-one-error" / "expected.json"


class PromoteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-promote-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def run_main(self, measured: pathlib.Path, *extra: str, out_name: str | None = None) -> tuple[int, pathlib.Path, str]:
        spec_id = json.loads(measured.read_text(encoding="utf-8"))["spec_id"]
        out = self.root / (out_name or f"{spec_id}.json")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = promote.main([str(measured), "--reviewer", "maintainer", "--reviewed-at", "2026-09-04T12:00:00Z", "--out", str(out), *extra])
        return code, out, err.getvalue()

    def test_passing_measured_spec_becomes_released_stable(self) -> None:
        code, out, err = self.run_main(PASS)
        self.assertEqual(code, 0, err)
        released = load_spec(out)
        measured = load_spec(PASS)
        self.assertEqual(released["state"], "released")
        self.assertEqual(released["review"], {"status": "stable", "reviewer": "maintainer", "reviewed_at": "2026-09-04T12:00:00Z"})
        differing = [k for k in released if released[k] != measured.get(k)]
        self.assertEqual(sorted(differing), ["review", "state"])

    def test_failed_outcome_defaults_to_failed_and_refuses_stable(self) -> None:
        code, out, _err = self.run_main(FAILED)
        self.assertEqual(code, 0)
        self.assertEqual(load_spec(out)["review"]["status"], "failed")
        out.unlink()
        code, _out, err = self.run_main(FAILED, "--status", "stable")
        self.assertEqual(code, 2)
        self.assertIn("requires every baseline-v1 outcome", err)

    def test_refuses_overwrite_wrong_name_and_released_input(self) -> None:
        code, out, _err = self.run_main(PASS)
        self.assertEqual(code, 0)
        code, _out, err = self.run_main(PASS)
        self.assertEqual(code, 2)
        self.assertIn("refusing to overwrite", err)
        code, _out, err = self.run_main(PASS, out_name="renamed.json")
        self.assertEqual(code, 2)
        self.assertIn("must be named", err)
        code, _out, err = self.run_main(out, out_name=f"again/{out.name}")
        self.assertEqual(code, 2)
        self.assertIn("only a measured spec", err)

    def test_reviewed_at_defaults_to_now_in_utc_z(self) -> None:
        spec_id = json.loads(PASS.read_text(encoding="utf-8"))["spec_id"]
        out = self.root / f"{spec_id}.json"
        with redirect_stdout(io.StringIO()):
            code = promote.main([str(PASS), "--reviewer", "maintainer", "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(load_spec(out)["review"]["reviewed_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
