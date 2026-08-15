#!/usr/bin/env python3
"""Contracts for ADR 0004 evidence-capture candidate persistence."""

from __future__ import annotations

import copy
import errno
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_serving_release_capture as capture  # noqa: E402
from scripts.testlib import (  # noqa: E402
    model_serving_release_capture_fixture as fixture,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402
from scripts.testlib import (  # noqa: E402
    model_serving_release_registry_fixture as registry_fixture,
)


CLI = REPO_ROOT / "scripts" / "model-serving-release-capture.sh"


class ModelServingReleaseCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-msrc-"))
        self.repo = self.tmpdir / "repo"
        fixture.seed_capture_repo(self.repo)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        import contextlib
        import io

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = capture.main(["--repo-root", str(self.repo), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def run_cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [str(CLI), *args, "--repo-root", str(self.repo)],
            cwd=str(REPO_ROOT),
            env=merged,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_safe_text(self, text: str) -> None:
        self.assertNotIn(str(self.repo), text)
        self.assertNotIn(str(self.tmpdir), text)
        self.assertNotIn("/tmp/", text)

    def assert_no_authority(self, payload: dict[str, object], dest: pathlib.Path | None = None) -> None:
        self.assertEqual(payload["schema_version"], 1)
        self.assertFalse(payload["serving_authorization"])
        self.assertEqual(payload.get("state"), "unreviewed")
        self.assertEqual(payload.get("authority"), "none")
        self.assertEqual(payload.get("privacy_review"), "pending")
        self.assertFalse(payload.get("promotion_authorized", False))
        if dest is not None:
            names = {path.name for path in dest.rglob("*") if path.is_file()}
            self.assertNotIn("decision.json", names)
            self.assertFalse(any("decision" in name for name in names))

    def assert_repo_unmutated(self) -> None:
        registry = self.repo / "models" / "model-serving-releases"
        leftover = [
            path
            for path in registry.rglob("*")
            if path.is_file() and path.name != "README.md"
        ]
        self.assertEqual(leftover, [])
        self.assertFalse((self.repo / "models" / "qwen3-1.7b.conf").exists())

    def capture_criterion(self, criterion_id: str, **kwargs: object) -> tuple[dict[str, object], pathlib.Path]:
        _spec, spec_path = fixture.passing_criterion_spec(
            criterion_id,
            repo_root=self.repo,
            **kwargs,
        )
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        return payload, dest

    def test_plan_and_capture_json_and_human(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        del spec
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assert_no_authority(plan)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["command"], "plan")
        self.assertIn("/runs/", plan["layout"])
        self.assert_safe_text(stdout)

        human = self.run_main(["plan", "--spec", str(spec_path)])
        self.assertEqual(human[0], 0, human[2])
        self.assertIn("unreviewed", human[1])
        self.assertIn("no", human[1])
        self.assert_safe_text(human[1])

        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        captured = json.loads(stdout)
        self.assert_no_authority(captured)
        self.assertEqual(captured["release_id"], plan["release_id"])
        self.assertEqual(captured["run_record_ids"], plan["run_record_ids"])
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(captured["layout"])).parts,
        )
        self.assertTrue((dest / "candidate.json").is_file())
        self.assertEqual((dest / "candidate.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(dest.stat().st_mode & 0o777, 0o700)
        self.assert_no_authority(captured, dest)
        self.assert_repo_unmutated()

        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])
        verified = json.loads(verify[1])
        self.assertEqual(verified["candidate_id"], captured["candidate_id"])
        self.assertFalse(verified["serving_authorization"])

    def test_cli_human_and_json_modes(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "latency-ttft", repo_root=self.repo
        )
        planned = self.run_cli("plan", "--spec", str(spec_path), "--json")
        self.assertEqual(planned.returncode, 0, planned.stderr)
        payload = json.loads(planned.stdout)
        self.assertFalse(payload["serving_authorization"])
        human = self.run_cli("plan", "--spec", str(spec_path), env={"COLUMNS": "40"})
        self.assertEqual(human.returncode, 0, human.stderr)
        for line in human.stdout.splitlines():
            self.assertLessEqual(len(line), 40, line)
        captured = self.run_cli(
            "capture-run", "--spec", str(spec_path), "--json"
        )
        self.assertEqual(captured.returncode, 0, captured.stderr)
        dest_layout = json.loads(captured.stdout)["layout"]
        dest = self.repo / "experiments" / "model-serving-release-captures"
        dest = dest.joinpath(*pathlib.PurePosixPath(dest_layout).parts)
        verified = self.run_cli(
            "verify-candidate", "--candidate-dir", str(dest), "--json"
        )
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertFalse(json.loads(verified.stdout)["serving_authorization"])

    def test_completed_failing_measurement_and_incomplete_attempts(self) -> None:
        _spec, fail_path = fixture.failing_measurement_spec(self.repo)
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(fail_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        record = json.loads(
            (dest / "run-records" / f"{payload['run_record_ids'][0]}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["attempt"]["completion"], "completed")
        self.assertEqual(record["criterion_observations"][0]["completion"], "complete")
        self.assertEqual(
            record["criterion_observations"][0]["metrics"][0]["value"], "0.1"
        )

        for completion, reason in (
            ("failed", "unusable-output"),
            ("interrupted", "interrupted"),
            ("inconclusive", "missing-output"),
        ):
            _spec, spec_path = fixture.incomplete_attempt_spec(
                self.repo, completion=completion, reason=reason
            )
            code, stdout, stderr = self.run_main(
                ["capture-run", "--spec", str(spec_path), "--json"]
            )
            self.assertEqual(code, 0, stderr)
            captured = json.loads(stdout)
            dest = self.repo.joinpath(
                "experiments",
                "model-serving-release-captures",
                *pathlib.PurePosixPath(str(captured["layout"])).parts,
            )
            record = json.loads(
                (
                    dest
                    / "run-records"
                    / f"{captured['run_record_ids'][0]}.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(record["attempt"]["completion"], completion)
            self.assertEqual(
                record["criterion_observations"][0]["completion"], "inconclusive"
            )

    def test_prebarrier_and_coverage_constraints(self) -> None:
        _spec, spec_path = fixture.prebarrier_spec(self.repo)
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertFalse(payload["qualification_started"])

        spec, bad_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        spec["criterion_observations"] = []
        fixture.write_spec(self.repo, "missing-observation", spec)
        code, stdout, stderr = self.run_main(
            [
                "plan",
                "--spec",
                str(self.repo / "capture-specs" / "missing-observation.json"),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        del bad_path

    def test_missing_evidence_is_not_a_pass(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        evidence = self.repo / "results" / "capture-fixture" / "throughput-serving.json"
        evidence.unlink()
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(spec_path), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        del spec

    def test_program_hash_and_drift(self) -> None:
        payload, dest = self.capture_criterion("throughput-serving")
        record = json.loads(
            (dest / "run-records" / f"{payload['run_record_ids'][0]}.json").read_text(
                encoding="utf-8"
            )
        )
        program = self.repo / "validate" / "run-gates.sh"
        expected = "sha256:" + capture.sha256_bytes(program.read_bytes())
        self.assertEqual(record["commands"][0]["version"], expected)
        program.write_bytes(program.read_bytes() + b"\n# drift\n")
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("drift", (stdout + stderr).lower())

    def test_evidence_and_file_set_mutations(self) -> None:
        payload, dest = self.capture_criterion("throughput-serving")
        evidence_files = list((dest / "evidence").iterdir())
        self.assertEqual(len(evidence_files), 1)
        evidence_files[0].write_bytes(b"mutated-evidence\n")
        code, _stdout, _stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)

        payload, dest = self.capture_criterion("latency-ttft")
        extra = dest / "scratch.tmp"
        extra.write_text("nope\n", encoding="utf-8")
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        extra.unlink()
        leftover = dest / "notes"
        leftover.mkdir()
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        leftover.rmdir()
        (dest / "release.json").unlink()
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)

        payload, dest = self.capture_criterion("physical-geometry-dgx")
        (dest / "renamed.json").write_bytes((dest / "contract.json").read_bytes())
        (dest / "contract.json").unlink()
        code, _stdout, _stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)

    def test_malformed_cross_links(self) -> None:
        payload, dest = self.capture_criterion("serving-integration-smoke")
        release = json.loads((dest / "release.json").read_text(encoding="utf-8"))
        release["release_id"] = "a" * 64
        (dest / "release.json").write_text(
            json.dumps(release, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        del payload

    def test_spec_rejects_forbidden_and_malformed_json(self) -> None:
        spec, _path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        cases = [
            ("duplicate-keys", '{"schema_version": 1, "schema_version": 2}\n'),
            ("nan", '{"schema_version": NaN}\n'),
            ("infinity", '{"schema_version": Infinity}\n'),
        ]
        for name, raw in cases:
            path = self.repo / "capture-specs" / f"{name}.json"
            path.write_text(raw, encoding="utf-8")
            code, stdout, stderr = self.run_main(
                ["plan", "--spec", str(path), "--json"]
            )
            self.assertNotEqual(code, 0, name)
            self.assert_safe_text(stdout + stderr)

        invalid_utf = self.repo / "capture-specs" / "invalid-utf.json"
        invalid_utf.write_bytes(b'{"schema_version": 1, "kind": "\xff"}')
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(invalid_utf), "--json"]
        )
        self.assertNotEqual(code, 0)

        forbidden = copy.deepcopy(spec)
        forbidden["release"]["release_id"] = "a" * 64
        path = fixture.write_spec(self.repo, "precomputed-id", forbidden)
        code, stdout, stderr = self.run_main(["plan", "--spec", str(path), "--json"])
        self.assertNotEqual(code, 0)

        for field, value in (
            ("serving_authorization", True),
            ("privacy_review", "passed"),
            ("decision", {"status": "validated"}),
            ("exit_code", 0),
            ("authority", "reviewed"),
        ):
            mutated = copy.deepcopy(spec)
            mutated[field] = value
            path = fixture.write_spec(self.repo, f"forbidden-{field}", mutated)
            code, stdout, stderr = self.run_main(
                ["plan", "--spec", str(path), "--json"]
            )
            self.assertNotEqual(code, 0, field)

        secret = copy.deepcopy(spec)
        secret["criterion_observations"][0]["reason"] = "hf_" + ("a" * 32)
        path = fixture.write_spec(self.repo, "credential", secret)
        code, stdout, stderr = self.run_main(["plan", "--spec", str(path), "--json"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("hf_", stdout + stderr)

    def test_publishable_and_protected_evidence_rules(self) -> None:
        spec, _path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        spec["evidence_sources"][0]["repository_path"] = (
            "results/capture-fixture/raw/secret.json"
        )
        raw_dir = self.repo / "results" / "capture-fixture" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "secret.json").write_text("{}\n", encoding="utf-8")
        path = fixture.write_spec(self.repo, "raw-path", spec)
        code, stdout, stderr = self.run_main(["plan", "--spec", str(path), "--json"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("secret.json", stdout + stderr)
        self.assert_safe_text(stdout + stderr)

        protected = copy.deepcopy(spec)
        protected["evidence_sources"] = [
            {
                "class": "protected",
                "content_sha256": "b" * 64,
                "media_type": "application/json",
                "qualification_scope": "model-qualification",
                "source_key": "protected-trace",
            }
        ]
        protected["criterion_observations"][0]["evidence_source_keys"] = [
            "protected-trace"
        ]
        protected["criterion_observations"][0]["contract_requirements"] = {
            "context": None,
            "soak": None,
        }
        path = fixture.write_spec(self.repo, "protected-only", protected)
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        self.assertFalse((dest / "evidence").exists())
        bundle = json.loads((dest / "evidence-bundle.json").read_text(encoding="utf-8"))
        artifact = bundle["evidence_artifacts"][0]
        self.assertEqual(artifact["visibility"], "protected")
        self.assertEqual(artifact["privacy_review"], "pending")
        self.assertNotIn("path", json.dumps(artifact))
        listing = " ".join(path.name for path in dest.rglob("*"))
        self.assertNotIn("secret", listing)

    def test_registry_exact_match_and_mismatch(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        release = release_fixture.build_release()
        contract = release_fixture.build_contract(release=release)
        registry_root = self.repo / "models" / "model-serving-releases"
        registry_fixture.write_release(registry_root, release)
        registry_fixture.write_contract(registry_root, contract)
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        mismatched = copy.deepcopy(release)
        mismatched["serving_recipe"]["engine_args"] = ["--max-model-len", "1"]
        # Keep the filename colliding with the spec-derived ID.
        registry_fixture.write_json(
            registry_root / "descriptors" / f"{release['release_id']}.json",
            {"kind": "not-a-release", "payload": "mismatch"},
        )
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        del spec

    def test_symlink_and_nonregular_inputs(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        evidence = self.repo / "results" / "capture-fixture" / "throughput-serving.json"
        real = self.repo / "results" / "capture-fixture" / "real.json"
        real.write_bytes(evidence.read_bytes())
        evidence.unlink()
        evidence.symlink_to(real)
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)

        evidence.unlink()
        os.mkfifo(evidence)
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertNotEqual(code, 0)
        os.unlink(evidence)
        del spec

    def test_unsafe_and_existing_destinations(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        for target in (
            str(self.repo),
            str(self.repo / "models"),
            str(self.repo / "models" / "model-serving-releases"),
            "/",
        ):
            code, stdout, stderr = self.run_main(
                [
                    "capture-run",
                    "--spec",
                    str(spec_path),
                    "--output-dir",
                    target,
                    "--json",
                ]
            )
            self.assertNotEqual(code, 0, target)
            self.assert_safe_text(stdout + stderr)

        payload, dest = self.capture_criterion("throughput-serving")
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(spec_path), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assertTrue((dest / "candidate.json").is_file())
        del payload

    def test_concurrent_publish_is_exclusive(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        del spec_path
        built = capture.build_capture_from_spec(spec, repo_root=self.repo)
        output_root = capture.default_capture_root(self.repo)
        dest = output_root.joinpath(*pathlib.PurePosixPath(built.layout).parts)
        results: list[str] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            try:
                capture.publish_candidate_tree(dest, built.files)
                results.append("ok")
            except capture.ModelServingReleaseCaptureError:
                results.append("exists")

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results), ["exists", "ok"])
        self.assertTrue((dest / "candidate.json").is_file())
        verified = capture.load_verified_candidate(dest, repo_root=self.repo)
        self.assertEqual(verified.manifest["candidate_id"], built.manifest["candidate_id"])

    def test_assemble_bundle_multi_run_and_conflicts(self) -> None:
        first, first_dest = self.capture_criterion("throughput-serving")
        second, second_dest = self.capture_criterion("latency-ttft")
        code, stdout, stderr = self.run_main(
            [
                "assemble-bundle",
                "--candidate-dir",
                str(first_dest),
                "--candidate-dir",
                str(second_dest),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        assembled = json.loads(stdout)
        self.assertEqual(len(assembled["run_record_ids"]), 2)
        self.assert_no_authority(assembled)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(assembled["layout"])).parts,
        )
        self.assertIn("/bundles/", assembled["layout"])
        self.assertTrue((dest / "candidate.json").is_file())
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])
        self.assertEqual(
            json.loads(verify[1])["candidate_id"], assembled["candidate_id"]
        )
        del first, second

        other_release = release_fixture.build_release(
            recipe=release_fixture.build_recipe(engine_args=["--max-model-len", "8"])
        )
        _spec, other_path = fixture.passing_criterion_spec(
            "throughput-serving",
            repo_root=self.repo,
            release=other_release,
            contract=release_fixture.build_contract(release=other_release),
        )
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(other_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        other = json.loads(stdout)
        other_dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(other["layout"])).parts,
        )
        code, stdout, stderr = self.run_main(
            [
                "assemble-bundle",
                "--candidate-dir",
                str(first_dest),
                "--candidate-dir",
                str(other_dest),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)

    def test_conflicting_location_digests_fail_assembly(self) -> None:
        _first, first_dest = self.capture_criterion("throughput-serving")
        evidence = (
            self.repo / "results" / "capture-fixture" / "throughput-serving.json"
        )
        evidence.write_text('{"mutated": true}\n', encoding="utf-8")
        spec, spec_path = fixture.passing_criterion_spec(
            "latency-ttft", repo_root=self.repo
        )
        spec["evidence_sources"][0]["repository_path"] = (
            "results/capture-fixture/throughput-serving.json"
        )
        spec["evidence_sources"][0]["source_key"] = "shared-location"
        spec["criterion_observations"][0]["evidence_source_keys"] = [
            "shared-location"
        ]
        path = fixture.write_spec(self.repo, "shared-location", spec)
        code, stdout, stderr = self.run_main(
            ["capture-run", "--spec", str(path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        second = json.loads(stdout)
        second_dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(second["layout"])).parts,
        )
        code, stdout, stderr = self.run_main(
            [
                "assemble-bundle",
                "--candidate-dir",
                str(first_dest),
                "--candidate-dir",
                str(second_dest),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        del spec_path

    def test_explicit_external_output_dir(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        external = self.tmpdir / "external-captures"
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(spec_path),
                "--output-dir",
                str(external),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = external.joinpath(*pathlib.PurePosixPath(str(payload["layout"])).parts)
        self.assertTrue((dest / "candidate.json").is_file())
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])
        self.assert_safe_text(stdout + verify[1])

    def test_modes_and_file_map_are_immutable(self) -> None:
        _payload, dest = self.capture_criterion("throughput-serving")
        manifest = json.loads((dest / "candidate.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], capture.CANDIDATE_KIND)
        self.assertNotIn("candidate.json", manifest["files"])
        for relative, digest in manifest["files"].items():
            path = dest.joinpath(*pathlib.PurePosixPath(relative).parts)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(capture.sha256_bytes(path.read_bytes()), digest)
            self.assertFalse(path.is_symlink())
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)

    def _publish_hostile_candidate(
        self,
        dest: pathlib.Path,
        *,
        release: dict,
        contract: dict,
        record: dict,
        artifacts: list[dict],
        run_artifact_ids: list[str],
        review_artifact_ids: list[str],
        publishable_bytes: dict[str, bytes],
        leaf: str,
    ) -> pathlib.Path:
        from scripts import model_validation_evidence as evidence

        rebuilt_record = evidence.build_validation_run_record(
            release=release,
            contract=contract,
            attempt=record["attempt"],
            preparation_provenance=record["preparation_provenance"],
            observed_environment=record["observed_environment"],
            commands=record["commands"],
            criterion_observations=record["criterion_observations"],
            evidence_artifacts=artifacts,
            evidence_artifact_ids=run_artifact_ids,
        )
        rebuilt_bundle = evidence.build_validation_evidence_bundle(
            release=release,
            contract=contract,
            run_records=[rebuilt_record],
            evidence_artifacts=artifacts,
            review_evidence_artifact_ids=review_artifact_ids,
        )
        files, _manifest = capture.candidate_payload_files(
            release=release,
            contract=contract,
            run_records=[rebuilt_record],
            bundle=rebuilt_bundle,
            publishable_bytes=publishable_bytes,
        )
        hostile_dest = dest.parent / leaf
        capture.publish_candidate_tree(hostile_dest, files)
        return hostile_dest

    def test_dotdot_output_cannot_write_models(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        traversal = (
            "experiments/model-serving-release-captures/../../models/hostile"
        )
        models_before = {
            path.relative_to(self.repo / "models")
            for path in (self.repo / "models").rglob("*")
        }
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(spec_path),
                "--output-dir",
                traversal,
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        models_after = {
            path.relative_to(self.repo / "models")
            for path in (self.repo / "models").rglob("*")
        }
        self.assertEqual(models_before, models_after)
        self.assertFalse((self.repo / "models" / "hostile").exists())

    def test_parent_symlink_output_is_rejected(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        outside = self.tmpdir / "outside-captures"
        outside.mkdir()
        link = (
            self.repo
            / "experiments"
            / "model-serving-release-captures"
            / "linked-root"
        )
        link.symlink_to(outside)
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(spec_path),
                "--output-dir",
                str(link / "nested"),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)
        self.assertEqual(list(outside.iterdir()), [])

    def test_same_size_mutation_during_read_fails(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        evidence = (
            self.repo / "results" / "capture-fixture" / "throughput-serving.json"
        )
        original = evidence.read_bytes()
        mutated = (b"x" * len(original)) if original else b"x"

        def hook(key: str | None) -> None:
            if key == "results/capture-fixture/throughput-serving.json":
                evidence.write_bytes(mutated)

        capture.READ_STABILITY_HOOK = hook
        try:
            with self.assertRaises(capture.ModelServingReleaseCaptureError):
                capture.build_capture_from_spec(spec, repo_root=self.repo)
        finally:
            capture.READ_STABILITY_HOOK = None
        del spec_path

    def test_nested_context_source_is_in_run_artifacts(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "accuracy-gsm8k", repo_root=self.repo
        )
        context_path = "results/capture-fixture/accuracy-context.json"
        fixture.write_publishable_file(
            self.repo, context_path, {"kind": "context-only"}
        )
        spec["evidence_sources"].append(
            {
                "source_key": "accuracy-context",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": context_path,
            }
        )
        spec["evidence_sources"] = sorted(
            spec["evidence_sources"], key=lambda item: item["source_key"]
        )
        context = spec["criterion_observations"][0]["contract_requirements"]["context"]
        self.assertIsNotNone(context)
        context["evidence_source_keys"] = ["accuracy-context"]
        fixture.write_spec(self.repo, "nested-context", spec)
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(self.repo / "capture-specs" / "nested-context.json"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        record = json.loads(
            (
                dest
                / "run-records"
                / f"{payload['run_record_ids'][0]}.json"
            ).read_text(encoding="utf-8")
        )
        observation = record["criterion_observations"][0]
        nested_ids = observation["contract_requirements"]["context"][
            "evidence_artifact_ids"
        ]
        top_ids = observation["evidence_artifact_ids"]
        self.assertNotEqual(nested_ids, top_ids)
        self.assertTrue(set(nested_ids).issubset(record["evidence_artifact_ids"]))
        self.assertTrue(set(top_ids).issubset(record["evidence_artifact_ids"]))
        del spec_path

    def test_unused_source_requires_explicit_review_list(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        spec["evidence_sources"].append(
            {
                "source_key": "review-note",
                "class": "protected",
                "qualification_scope": "release-promotion",
                "media_type": "application/json",
                "content_sha256": "ab" * 32,
            }
        )
        spec["evidence_sources"] = sorted(
            spec["evidence_sources"], key=lambda item: item["source_key"]
        )
        fixture.write_spec(self.repo, "unused-protected", spec)
        code, stdout, stderr = self.run_main(
            [
                "plan",
                "--spec",
                str(self.repo / "capture-specs" / "unused-protected.json"),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        spec["review_source_keys"] = ["review-note"]
        fixture.write_spec(self.repo, "explicit-review", spec)
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(self.repo / "capture-specs" / "explicit-review.json"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        bundle = json.loads((dest / "evidence-bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(len(bundle["review_evidence_artifact_ids"]), 1)
        review = next(
            item
            for item in bundle["evidence_artifacts"]
            if item["artifact_id"] == bundle["review_evidence_artifact_ids"][0]
        )
        self.assertEqual(review["qualification_scope"], "release-promotion")
        self.assertEqual(review["privacy_review"], "pending")
        del spec_path

    def test_hostile_raw_location_fails_independent_verify(self) -> None:
        from scripts import model_validation_evidence as evidence

        payload, dest = self.capture_criterion("throughput-serving")
        bundle = json.loads((dest / "evidence-bundle.json").read_text(encoding="utf-8"))
        release = json.loads((dest / "release.json").read_text(encoding="utf-8"))
        contract = json.loads((dest / "contract.json").read_text(encoding="utf-8"))
        record = json.loads(
            (
                dest
                / "run-records"
                / f"{payload['run_record_ids'][0]}.json"
            ).read_text(encoding="utf-8")
        )
        original = bundle["evidence_artifacts"][0]
        hostile = evidence.build_evidence_artifact(
            location_kind="repository-relative",
            location_value="results/capture-fixture/raw/hostile.json",
            content_sha256=original["content"]["sha256"],
            media_type=original["content"]["media_type"],
            qualification_scope=original["qualification_scope"],
            visibility="publishable",
            privacy_review="pending",
        )
        mapping = {original["artifact_id"]: hostile["artifact_id"]}
        record["evidence_artifact_ids"] = [
            mapping.get(item, item) for item in record["evidence_artifact_ids"]
        ]
        for observation in record["criterion_observations"]:
            observation["evidence_artifact_ids"] = [
                mapping.get(item, item)
                for item in observation["evidence_artifact_ids"]
            ]
        record.pop("run_record_id")
        rebuilt_record = evidence.build_validation_run_record(
            release=release,
            contract=contract,
            attempt=record["attempt"],
            preparation_provenance=record["preparation_provenance"],
            observed_environment=record["observed_environment"],
            commands=record["commands"],
            criterion_observations=record["criterion_observations"],
            evidence_artifacts=[hostile],
            evidence_artifact_ids=record["evidence_artifact_ids"],
        )
        rebuilt_bundle = evidence.build_validation_evidence_bundle(
            release=release,
            contract=contract,
            run_records=[rebuilt_record],
            evidence_artifacts=[hostile],
            review_evidence_artifact_ids=[],
        )
        files, _manifest = capture.candidate_payload_files(
            release=release,
            contract=contract,
            run_records=[rebuilt_record],
            bundle=rebuilt_bundle,
            publishable_bytes={
                original["content"]["sha256"]: (
                    dest / "evidence" / original["content"]["sha256"]
                ).read_bytes()
            },
        )
        hostile_dest = dest.parent / ("hostile-" + dest.name)
        capture.publish_candidate_tree(hostile_dest, files)
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(hostile_dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        combined = stdout + stderr
        self.assertIn("raw", combined.lower())
        self.assert_safe_text(combined)

    def test_registry_permission_error_is_not_enoent(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        release = release_fixture.build_release()
        registry_root = self.repo / "models" / "model-serving-releases"
        path = (
            registry_root / "descriptors" / f"{release['release_id']}.json"
        )
        registry_fixture.write_release(registry_root, release)

        original_open = os.open

        def deny_registry_file(
            name: str | bytes,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if name == path.name and dir_fd is not None:
                raise PermissionError(errno.EACCES, "permission denied")
            return original_open(name, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(capture.os, "open", side_effect=deny_registry_file):
            code, stdout, stderr = self.run_main(
                ["plan", "--spec", str(spec_path), "--json"]
            )
            self.assertNotEqual(code, 0)
            combined = stdout + stderr
            self.assertIn("unreadable", combined)
            self.assertNotIn("missing", combined)
            self.assert_safe_text(combined)

        path.unlink()
        code, stdout, stderr = self.run_main(
            ["plan", "--spec", str(spec_path), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        del spec

    def test_private_freeform_value_is_rejected(self) -> None:
        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        spec["criterion_observations"][0]["reason"] = "home_node_id=lab-orion"
        fixture.write_spec(self.repo, "private-reason", spec)
        code, stdout, stderr = self.run_main(
            [
                "plan",
                "--spec",
                str(self.repo / "capture-specs" / "private-reason.json"),
                "--json",
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertNotIn("lab-orion", stdout + stderr)
        del spec_path

    def test_altered_modes_fail_verify(self) -> None:
        _payload, dest = self.capture_criterion("throughput-serving")
        os.chmod(dest, 0o755)
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        os.chmod(dest, 0o700)
        os.chmod(dest / "release.json", 0o644)
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assert_safe_text(stdout + stderr)

    def test_help_wraps_at_narrow_width(self) -> None:
        result = self.run_cli("help", env={"COLUMNS": "40"})
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertLessEqual(len(line), 40, line)
        self.assertIn("unreviewed", result.stdout)

    def test_hostile_privacy_passed_fails_independent_verify(self) -> None:
        from scripts import model_validation_evidence as evidence

        payload, dest = self.capture_criterion("throughput-serving")
        bundle = json.loads((dest / "evidence-bundle.json").read_text(encoding="utf-8"))
        release = json.loads((dest / "release.json").read_text(encoding="utf-8"))
        contract = json.loads((dest / "contract.json").read_text(encoding="utf-8"))
        record = json.loads(
            (
                dest
                / "run-records"
                / f"{payload['run_record_ids'][0]}.json"
            ).read_text(encoding="utf-8")
        )
        original = bundle["evidence_artifacts"][0]
        hostile = evidence.build_evidence_artifact(
            location_kind=original["location"]["kind"],
            location_value=original["location"]["value"],
            content_sha256=original["content"]["sha256"],
            media_type=original["content"]["media_type"],
            qualification_scope=original["qualification_scope"],
            visibility=original["visibility"],
            privacy_review="passed",
        )
        record["evidence_artifact_ids"] = [hostile["artifact_id"]]
        record["criterion_observations"][0]["evidence_artifact_ids"] = [
            hostile["artifact_id"]
        ]
        record.pop("run_record_id", None)
        hostile_dest = self._publish_hostile_candidate(
            dest,
            release=release,
            contract=contract,
            record=record,
            artifacts=[hostile],
            run_artifact_ids=[hostile["artifact_id"]],
            review_artifact_ids=[],
            publishable_bytes={
                original["content"]["sha256"]: (
                    dest / "evidence" / original["content"]["sha256"]
                ).read_bytes()
            },
            leaf="hostile-privacy-" + dest.name,
        )
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(hostile_dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("pending", (stdout + stderr).lower())
        self.assertFalse(json.loads(stdout).get("serving_authorization", True))
        self.assert_safe_text(stdout + stderr)

    def test_hostile_review_scope_fails_independent_verify(self) -> None:
        from scripts import model_validation_evidence as evidence

        spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        spec["evidence_sources"].append(
            {
                "source_key": "review-note",
                "class": "protected",
                "qualification_scope": "release-promotion",
                "media_type": "application/json",
                "content_sha256": "cd" * 32,
            }
        )
        spec["evidence_sources"] = sorted(
            spec["evidence_sources"], key=lambda item: item["source_key"]
        )
        spec["review_source_keys"] = ["review-note"]
        fixture.write_spec(self.repo, "review-scope-source", spec)
        code, stdout, stderr = self.run_main(
            [
                "capture-run",
                "--spec",
                str(self.repo / "capture-specs" / "review-scope-source.json"),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        dest = self.repo.joinpath(
            "experiments",
            "model-serving-release-captures",
            *pathlib.PurePosixPath(str(payload["layout"])).parts,
        )
        bundle = json.loads((dest / "evidence-bundle.json").read_text(encoding="utf-8"))
        release = json.loads((dest / "release.json").read_text(encoding="utf-8"))
        contract = json.loads((dest / "contract.json").read_text(encoding="utf-8"))
        record = json.loads(
            (
                dest
                / "run-records"
                / f"{payload['run_record_ids'][0]}.json"
            ).read_text(encoding="utf-8")
        )
        review = next(
            item
            for item in bundle["evidence_artifacts"]
            if item["artifact_id"] in bundle["review_evidence_artifact_ids"]
        )
        run_artifact = next(
            item
            for item in bundle["evidence_artifacts"]
            if item["artifact_id"] not in bundle["review_evidence_artifact_ids"]
        )
        hostile_review = evidence.build_evidence_artifact(
            location_kind=review["location"]["kind"],
            location_value=review["location"]["value"],
            content_sha256=review["content"]["sha256"],
            media_type=review["content"]["media_type"],
            qualification_scope="model-qualification",
            visibility=review["visibility"],
            privacy_review="pending",
        )
        record.pop("run_record_id", None)
        hostile_dest = self._publish_hostile_candidate(
            dest,
            release=release,
            contract=contract,
            record=record,
            artifacts=[run_artifact, hostile_review],
            run_artifact_ids=list(record["evidence_artifact_ids"]),
            review_artifact_ids=[hostile_review["artifact_id"]],
            publishable_bytes={
                run_artifact["content"]["sha256"]: (
                    dest / "evidence" / run_artifact["content"]["sha256"]
                ).read_bytes()
            },
            leaf="hostile-scope-" + dest.name,
        )
        code, stdout, stderr = self.run_main(
            ["verify-candidate", "--candidate-dir", str(hostile_dest), "--json"]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("release-promotion", stdout + stderr)
        self.assertFalse(json.loads(stdout).get("serving_authorization", True))
        self.assert_safe_text(stdout + stderr)
        del spec_path

    def test_extra_file_after_scan_fails_verify(self) -> None:
        _payload, dest = self.capture_criterion("throughput-serving")

        def hook() -> None:
            extra = dest / "extra-after-scan.json"
            extra.write_text("{}\n", encoding="utf-8")
            os.chmod(extra, 0o600)

        capture.VERIFY_AFTER_SCAN_HOOK = hook
        try:
            with self.assertRaises(capture.ModelServingReleaseCaptureError):
                capture.load_verified_candidate(dest, repo_root=self.repo)
        finally:
            capture.VERIFY_AFTER_SCAN_HOOK = None
        extra = dest / "extra-after-scan.json"
        if extra.exists():
            extra.unlink()
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])

    def test_candidate_path_replacement_fails_verify(self) -> None:
        _payload, dest = self.capture_criterion("latency-ttft")
        moved = dest.with_name(dest.name + ".original")

        def hook() -> None:
            dest.rename(moved)
            dest.mkdir()
            os.chmod(dest, 0o700)

        capture.VERIFY_AFTER_SCAN_HOOK = hook
        try:
            with self.assertRaises(capture.ModelServingReleaseCaptureError):
                capture.load_verified_candidate(dest, repo_root=self.repo)
        finally:
            capture.VERIFY_AFTER_SCAN_HOOK = None
            if dest.exists() and dest.is_dir() and not (dest / "candidate.json").exists():
                dest.rmdir()
            if moved.exists() and not dest.exists():
                moved.rename(dest)
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])

    def test_output_dir_cannot_be_existing_candidate_or_subdir(self) -> None:
        payload, dest = self.capture_criterion("throughput-serving")
        _spec, spec_path = fixture.passing_criterion_spec(
            "latency-ttft", repo_root=self.repo
        )
        for output in (dest, dest / "evidence", dest / "run-records"):
            before = {path.relative_to(dest) for path in dest.rglob("*")}
            code, stdout, stderr = self.run_main(
                [
                    "capture-run",
                    "--spec",
                    str(spec_path),
                    "--output-dir",
                    str(output),
                    "--json",
                ]
            )
            self.assertNotEqual(code, 0, output)
            self.assert_safe_text(stdout + stderr)
            after = {path.relative_to(dest) for path in dest.rglob("*")}
            self.assertEqual(before, after)
            nested = [
                path
                for path in output.iterdir()
                if path.is_dir() and len(path.name) == 64
            ]
            self.assertEqual(nested, [])
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])
        self.assertEqual(json.loads(verify[1])["candidate_id"], payload["candidate_id"])

    def test_subdirectory_name_swap_fails_verify(self) -> None:
        _payload, dest = self.capture_criterion("throughput-serving")
        original = dest / "run-records"
        moved = dest.parent / (dest.name + "-run-records-original")

        def hook() -> None:
            original.rename(moved)
            replacement = dest / "run-records"
            replacement.mkdir()
            os.chmod(replacement, 0o700)
            for item in moved.iterdir():
                target = replacement / item.name
                if item.is_file():
                    target.write_bytes(item.read_bytes())
                    os.chmod(target, 0o600)

        capture.VERIFY_AFTER_SCAN_HOOK = hook
        try:
            with self.assertRaises(capture.ModelServingReleaseCaptureError):
                capture.load_verified_candidate(dest, repo_root=self.repo)
        finally:
            capture.VERIFY_AFTER_SCAN_HOOK = None
            replacement = dest / "run-records"
            if replacement.exists() and moved.exists():
                for item in replacement.iterdir():
                    item.unlink()
                replacement.rmdir()
                moved.rename(original)
        verify = self.run_main(
            ["verify-candidate", "--candidate-dir", str(dest), "--json"]
        )
        self.assertEqual(verify[0], 0, verify[2])

    def test_nonregular_candidate_marker_is_rejected(self) -> None:
        _spec, spec_path = fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        for kind in ("directory", "fifo"):
            home = (
                self.repo
                / "experiments"
                / "model-serving-release-captures"
                / f"marker-{kind}"
            )
            home.mkdir()
            marker = home / "candidate.json"
            if kind == "directory":
                marker.mkdir()
            else:
                os.mkfifo(marker)
            code, stdout, stderr = self.run_main(
                [
                    "capture-run",
                    "--spec",
                    str(spec_path),
                    "--output-dir",
                    str(home),
                    "--json",
                ]
            )
            self.assertNotEqual(code, 0, kind)
            self.assert_safe_text(stdout + stderr)
            nested = [
                path
                for path in home.iterdir()
                if path.is_dir() and path.name != "candidate.json"
            ]
            self.assertEqual(nested, [])


if __name__ == "__main__":
    unittest.main()
