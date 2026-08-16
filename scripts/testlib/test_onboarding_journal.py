#!/usr/bin/env python3
"""Contracts for the pulsar-model-onboarding journal helper."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNAL_PATH = (
    REPO_ROOT / "skills" / "pulsar-model-onboarding" / "scripts" / "onboarding_journal.py"
)
COMMIT = "a" * 40
ALT_COMMIT = "b" * 40
REVISION = "c" * 40
DIGEST = "d" * 64
IDENTITY = {
    "workflow_id": "example-new-model",
    "profile": "example-new-model",
    "public_model_id": "org/Example-New-Model",
    "repository_base_commit": COMMIT,
    "profile_base_commit": COMMIT,
}


def load_journal_module():
    spec = importlib.util.spec_from_file_location(
        "pulsar_onboarding_journal", JOURNAL_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


journal = load_journal_module()


class OnboardingJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="pulsar-onboard-journal-"))
        self.repo = self.tmpdir / "repo"
        self.repo.mkdir()
        self.journal_dir = (
            self.repo
            / "experiments"
            / "model-onboarding"
            / "workflows"
            / "example-new-model"
        )
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = journal.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def identity_args(self) -> list[str]:
        return [
            "--workflow-id",
            IDENTITY["workflow_id"],
            "--profile",
            IDENTITY["profile"],
            "--public-model-id",
            IDENTITY["public_model_id"],
            "--repository-base-commit",
            IDENTITY["repository_base_commit"],
            "--profile-base-commit",
            IDENTITY["profile_base_commit"],
        ]

    def initialize(self, extra: list[str] | None = None) -> dict[str, object]:
        code, out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--repo-root",
                str(self.repo),
                "--journal-dir",
                str(self.journal_dir),
                *self.identity_args(),
                *(extra or []),
            ]
        )
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def append(
        self,
        *,
        phase: str = "criteria",
        outcome: str = "frozen",
        extra: list[str] | None = None,
    ) -> tuple[int, str, str]:
        return self.run_main(
            [
                "append",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                "--phase",
                phase,
                "--outcome",
                outcome,
                *(extra or []),
            ]
        )

    def test_exclusive_initialize_creates_private_files(self) -> None:
        report = self.initialize()
        self.assertEqual(report["authority"], "none")
        self.assertFalse(report["evidence"])
        self.assertEqual(report["event_count"], 0)
        self.assertEqual(stat.S_IMODE(self.journal_dir.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.journal_dir / "header.json").stat().st_mode), 0o600
        )
        self.assertEqual(
            stat.S_IMODE((self.journal_dir / "events.jsonl").stat().st_mode), 0o600
        )
        code, _out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                *self.identity_args(),
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_append_resume_and_verify_identity(self) -> None:
        self.initialize()
        code, out, err = self.append(
            extra=[
                "--choice",
                "relative_performance=n/a",
                "--id",
                f"exact_revision={REVISION}",
                "--id",
                f"release_id={DIGEST}",
                "--reference",
                "experiments/model-onboarding/example/release.json",
            ]
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["appended_seq"], 1)
        code, _out, err = self.run_main(
            [
                "verify",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                *self.identity_args(),
                "--id",
                f"exact_revision={REVISION}",
                "--id",
                f"release_id={DIGEST}",
            ]
        )
        self.assertEqual(code, 0, err)
        code, _out, err = self.run_main(
            [
                "verify",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                "--workflow-id",
                "other-workflow",
                "--profile",
                IDENTITY["profile"],
                "--public-model-id",
                IDENTITY["public_model_id"],
                "--repository-base-commit",
                COMMIT,
                "--profile-base-commit",
                COMMIT,
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("resume identity mismatch", err)

        code, _out, err = self.run_main(
            [
                "verify",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                "--id",
                f"exact_revision={'e' * 40}",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("resume identity mismatch for exact_revision", err)

        code, _out, err = self.append(
            extra=["--id", f"exact_revision={'e' * 40}"]
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot rebind exact_revision", err)

        code, _out, err = self.run_main(
            [
                "verify",
                "--json",
                "--journal-dir",
                str(self.journal_dir),
                "--id",
                f"contract_id={DIGEST}",
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("contract_id is not yet bound", err)

    def test_tamper_truncation_hash_and_sequence_fail_closed(self) -> None:
        self.initialize()
        self.assertEqual(
            self.append(extra=["--id", f"exact_revision={REVISION}"])[0],
            0,
        )
        events = self.journal_dir / "events.jsonl"
        original = events.read_bytes()
        events.write_bytes(original[:-2])
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("truncated", err)
        events.write_bytes(original[:-2] + b"0\n")
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertRegex(err, r"hash|JSON|broken|does not match")
        events.write_bytes(original)
        line = json.loads(original.decode("utf-8"))
        line["seq"] = 9
        events.write_text(json.dumps(line) + "\n", encoding="utf-8")
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("sequence", err)

        events.write_bytes(original)
        self.assertEqual(
            self.append(extra=["--id", f"exact_revision={REVISION}"])[0],
            0,
        )
        lines = [json.loads(item) for item in events.read_text().splitlines()]
        lines[1]["ids"]["exact_revision"] = "e" * 40
        lines[1]["event_hash"] = journal.digest_object(
            journal.event_payload(lines[1])
        )
        events.write_text(
            "".join(json.dumps(item) + "\n" for item in lines),
            encoding="utf-8",
        )
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("rebinds exact_revision", err)

    def test_forbidden_privacy_values_and_embedded_objects(self) -> None:
        self.initialize()
        cases = [
            ["--choice", "note=token=sekrit"],
            ["--choice", "HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz0123456789"],
            ["--choice", "home=/var/data/models"],
            ["--choice", "endpoint=node-a.local"],
            ["--choice", "RAW=VALUE"],
            ["--choice", "hostname=spark-host"],
            ["--choice", "note=line one\nline two"],
            ["--id", "compare_attempt_id=contains spaces"],
            ["--reference", "models/model-serving-releases/release.json"],
            ["--choice", 'payload={"release_id":"opaque"}'],
            [
                "--reference",
                "experiments/model-onboarding/example",
                "--choice",
                'payload={"kind":"pulsar-model-serving-release"}',
            ],
        ]
        for extra in cases:
            code, _out, err = self.append(extra=extra)
            self.assertEqual(code, 2, extra)
            self.assertTrue(err, extra)

    def test_unsafe_paths_and_symlinks(self) -> None:
        self.initialize()
        planner_default = (
            self.repo
            / "experiments"
            / "model-onboarding"
            / IDENTITY["profile"]
            / DIGEST
        )
        planner_default.mkdir(parents=True)
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 0, err)

        colliding_legacy_dir = (
            self.repo
            / "experiments"
            / "model-onboarding"
            / "legacy-collision"
        )
        code, _out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--repo-root",
                str(self.repo),
                "--journal-dir",
                str(colliding_legacy_dir),
                "--workflow-id",
                "legacy-collision",
                "--profile",
                "legacy-collision",
                "--public-model-id",
                IDENTITY["public_model_id"],
                "--repository-base-commit",
                COMMIT,
                "--profile-base-commit",
                COMMIT,
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("experiments/model-onboarding/workflows/", err)

        nested_workflow_dir = (
            self.repo
            / "experiments"
            / "model-onboarding"
            / "workflows"
            / "profile"
            / DIGEST
        )
        code, _out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--repo-root",
                str(self.repo),
                "--journal-dir",
                str(nested_workflow_dir),
                "--workflow-id",
                "nested-collision",
                "--profile",
                IDENTITY["profile"],
                "--public-model-id",
                IDENTITY["public_model_id"],
                "--repository-base-commit",
                COMMIT,
                "--profile-base-commit",
                COMMIT,
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("directly under", err)

        models_dir = self.repo / "models" / "shadow"
        code, _out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--repo-root",
                str(self.repo),
                "--journal-dir",
                str(models_dir),
                "--workflow-id",
                "shadow-journal",
                "--profile",
                IDENTITY["profile"],
                "--public-model-id",
                IDENTITY["public_model_id"],
                "--repository-base-commit",
                COMMIT,
                "--profile-base-commit",
                COMMIT,
            ]
        )
        self.assertEqual(code, 2)
        self.assertIn("permitted", err)
        outside = self.tmpdir / "outside-journal"
        code, _out, err = self.run_main(
            [
                "initialize",
                "--json",
                "--repo-root",
                str(self.repo),
                "--journal-dir",
                str(outside),
                *self.identity_args(),
            ]
        )
        self.assertEqual(code, 0, err)
        alias = self.tmpdir / "journal-link"
        alias.symlink_to(self.journal_dir)
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(alias)]
        )
        self.assertEqual(code, 2)
        self.assertRegex(err, r"symlink")

    def test_unexpected_fields_and_nonregular_files(self) -> None:
        self.initialize()
        header_path = self.journal_dir / "header.json"
        header = json.loads(header_path.read_text(encoding="utf-8"))
        header["extra"] = "nope"
        header_path.write_text(json.dumps(header), encoding="utf-8")
        os.chmod(header_path, 0o600)
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("unexpected", err)
        import shutil

        shutil.rmtree(self.journal_dir)
        self.initialize()
        extra = self.journal_dir / "notes.txt"
        extra.write_text("nope\n", encoding="utf-8")
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertIn("unexpected files", err)
        extra.unlink()
        events = self.journal_dir / "events.jsonl"
        events.unlink()
        events.mkdir()
        code, _out, err = self.run_main(
            ["verify", "--json", "--journal-dir", str(self.journal_dir)]
        )
        self.assertEqual(code, 2)
        self.assertRegex(err, r"regular file|symlink")

    def test_scan_friendly_human_output(self) -> None:
        self.initialize()
        self.assertEqual(
            self.append(
                extra=[
                    "--choice",
                    "relative_performance=n/a",
                    "--id",
                    f"release_id={DIGEST}",
                ]
            )[0],
            0,
        )
        env_width = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "48"
        try:
            code, out, err = self.run_main(
                ["show", "--journal-dir", str(self.journal_dir)]
            )
        finally:
            if env_width is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = env_width
        self.assertEqual(code, 0, err)
        self.assertIn("Onboarding journal", out)
        self.assertIn("Workflow", out)
        self.assertIn("example-new-model", out)
        self.assertIn("criteria", out)
        self.assertIn("relative_performance", out)
        self.assertLessEqual(max(len(line) for line in out.splitlines() if line), 48)
        self.assertNotRegex(out, r"token=|HF_TOKEN|/home/")


if __name__ == "__main__":
    unittest.main()
