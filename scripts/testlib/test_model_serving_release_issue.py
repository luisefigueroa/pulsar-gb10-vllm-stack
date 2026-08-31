#!/usr/bin/env python3
"""Contracts for maintainer ADR 0004 issuance staging."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity  # noqa: E402
from scripts import model_serving_release_capture as capture  # noqa: E402
from scripts import model_serving_release_issue as issue  # noqa: E402
from scripts import model_serving_release_registry as registry  # noqa: E402
from scripts import model_validation_evidence  # noqa: E402
from scripts.testlib import (  # noqa: E402
    model_serving_release_capture_fixture as capture_fixture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_issue_fixture as fixture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_registry_fixture as registry_fixture,
)
from scripts.testlib import model_validation_evidence_fixture as evidence_fixture  # noqa: E402


CLI = REPO_ROOT / "scripts" / "model-serving-release-issue.sh"
PRODUCTION_REGISTRY = REPO_ROOT / "models" / "model-serving-releases"


class ModelServingReleaseIssueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-msri-"))
        self.repo = self.tmpdir / "repo"
        self.production_registry_before = self.production_registry_snapshot()
        self.production_profiles_before = self.production_profile_snapshot()
        self._env_overrides = fixture.git_env(self.tmpdir)
        self._saved_env = {
            key: os.environ.get(key) for key in self._env_overrides
        }
        os.environ.update(self._env_overrides)
        fixture.seed_issue_repo(self.repo)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = issue.main(["--repo-root", str(self.repo), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(CLI), *args, "--repo-root", str(self.repo)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

    def commit_current(self, message: str) -> None:
        fixture.commit_all(self.repo, message)

    def assert_safe_text(self, text: str) -> None:
        self.assertNotIn(str(self.repo), text)
        self.assertNotIn(str(self.tmpdir), text)

    def assert_trust_caveats(self, payload: dict[str, object], text: str = "") -> None:
        self.assertEqual(payload.get("authority"), "none")
        self.assertFalse(payload.get("promotion_authorized", True))
        self.assertFalse(payload.get("physical_claim", True))
        self.assertEqual(
            payload.get("trust"),
            "untrusted-until-repository-review-and-merge",
        )
        notes = " ".join(str(item) for item in payload.get("notes") or [])
        self.assertIn("not trusted until repository review and merge", notes)
        self.assertIn("advisory", notes.lower())
        self.assertIn("physical DGX", notes)
        combined = f"{text}\n{notes}"
        self.assertIn("not trusted until repository review and merge", combined)

    @staticmethod
    def production_registry_snapshot() -> dict[str, bytes]:
        return {
            str(path.relative_to(PRODUCTION_REGISTRY)): path.read_bytes()
            for path in PRODUCTION_REGISTRY.rglob("*")
            if path.is_file() and not path.is_symlink()
        }

    @staticmethod
    def production_profile_snapshot() -> dict[str, bytes]:
        models = REPO_ROOT / "models"
        return {
            path.name: path.read_bytes()
            for path in models.glob("*.conf")
            if path.is_file() and not path.is_symlink()
        }

    def assert_production_unchanged(self) -> None:
        self.assertEqual(
            self.production_registry_snapshot(), self.production_registry_before
        )
        self.assertEqual(
            self.production_profile_snapshot(), self.production_profiles_before
        )

    def assert_candidate_unchanged(
        self, dest: pathlib.Path, before: dict[str, bytes]
    ) -> None:
        after = fixture.candidate_bytes(dest)
        self.assertEqual(after, before)

    def assert_no_path_leak(
        self, payload: dict[str, object], dest: pathlib.Path
    ) -> None:
        encoded = json.dumps(payload, sort_keys=True)
        self.assertNotIn(str(dest), encoded)
        self.assertNotIn(str(self.repo), encoded)
        registry_root = self.repo / "models" / "model-serving-releases"
        for path in registry_root.rglob("*.json"):
            if path.name == "README.md":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(str(dest), text)
            self.assertNotIn(str(self.repo), text)
            self.assertNotIn(str(self.tmpdir), text)

    def plan_or_stage(
        self,
        command: str,
        dest: pathlib.Path,
        review_path: pathlib.Path,
        *,
        use_json: bool = True,
    ) -> tuple[int, dict[str, object] | None, str, str]:
        args = [
            command,
            "--candidate-dir",
            str(dest),
            "--review-file",
            str(review_path),
        ]
        if use_json:
            args.append("--json")
        code, stdout, stderr = self.run_main(args)
        payload = None
        if stdout.strip():
            payload = json.loads(stdout)
        return code, payload, stdout, stderr

    def test_help_lists_trust_boundary(self) -> None:
        code, stdout, stderr = self.run_main(["help"])
        self.assertEqual(code, 0, stderr)
        self.assertIn("not trusted until repository review and merge", stdout)
        self.assertIn("plan", stdout)
        self.assertIn("stage", stdout)
        self.assertIn("repository-review:", stdout)
        self.assert_safe_text(stdout)

    def test_cli_help_and_unknown_command(self) -> None:
        help_proc = self.run_cli("help")
        self.assertEqual(help_proc.returncode, 0, help_proc.stderr)
        self.assertIn("not trusted until repository review and merge", help_proc.stdout)
        unknown = self.run_cli("publish")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("unknown model-serving-release-issue command", unknown.stderr)

    def test_run_diagnostic_artifact_survives_review_rematerialization(self) -> None:
        inputs = capture_fixture.passing_criterion_spec(
            "throughput-serving", repo_root=self.repo
        )
        diagnostic_path = "results/capture-fixture/throughput-resources.json"
        capture_fixture.write_publishable_file(
            self.repo,
            diagnostic_path,
            {"kind": "pulsar-validator-measurement", "operation": "observe-resources"},
        )
        inputs.attempt["evidence_sources"].append(
            {
                "source_key": "resource-throughput",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": diagnostic_path,
            }
        )
        inputs.attempt["evidence_sources"].sort(
            key=lambda item: item["source_key"]
        )
        inputs.attempt["run_diagnostic_source_keys"] = ["resource-throughput"]
        dest, candidate = fixture.persist_capture(inputs, self.repo)
        review = fixture.review_declaration(
            candidate,
            expected_status="testing-incomplete",
            privacy="pending",
            provenance_overrides={
                "artifact_identity": "pending",
                "runtime_identity": "pending",
                "contract_frozen_before_testing": "pending",
                "evidence_privacy": "pending",
                "security": "pending",
            },
        )
        plan = issue.build_issue_plan(
            repo_root=self.repo,
            candidate_dir=dest,
            review=issue.validate_review_declaration(review),
        )
        self.assertEqual(len(candidate.run_records[0]["evidence_artifact_ids"]), 2)
        self.assertEqual(len(plan.run_records[0]["evidence_artifact_ids"]), 2)
        self.assertEqual(
            len(plan.run_records[0]["criterion_observations"][0]["evidence_artifact_ids"]),
            1,
        )
        self.assertEqual(plan.bundle["review_evidence_artifact_ids"], [])

    def test_public_cli_plans_and_stages_json(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        planned = self.run_cli(
            "plan",
            "--candidate-dir",
            str(dest),
            "--review-file",
            str(review_path),
            "--json",
        )
        self.assertEqual(planned.returncode, 0, planned.stderr)
        plan_payload = json.loads(planned.stdout)
        self.assertEqual(plan_payload["command"], "plan")
        self.assertEqual(plan_payload["status"], "untested")

        self.commit_current("ready for public CLI stage")
        staged = self.run_cli(
            "stage",
            "--candidate-dir",
            str(dest),
            "--review-file",
            str(review_path),
            "--json",
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)
        stage_payload = json.loads(staged.stdout)
        self.assertEqual(stage_payload["state"], "staged-proposal")
        self.assertIn(
            stage_payload["decision_id"], registry.load_registry(self.repo).decisions
        )

    def _status_case(
        self,
        *,
        builder,
        expected_status: str,
        privacy: str = "passed",
        provenance_overrides: dict[str, str] | None = None,
        metrics: dict[str, object] | None = None,
    ) -> dict[str, object]:
        dest, candidate = builder()
        review_path, _review = fixture.write_review(
            self.repo,
            candidate,
            expected_status=expected_status,
            privacy=privacy,
            provenance_overrides=provenance_overrides,
        )
        del metrics
        code, payload, stdout, stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 0, stderr or stdout)
        assert payload is not None
        self.assertEqual(payload["status"], expected_status)
        self.assertEqual(
            payload["status_label"],
            model_validation_evidence.validation_status_label(expected_status),
        )
        self.assert_trust_caveats(payload, stdout)
        return payload

    def test_derived_untested_status(self) -> None:
        self._status_case(
            builder=lambda: fixture.capture_prebarrier(self.repo),
            expected_status="untested",
        )

    def test_derived_testing_incomplete_status(self) -> None:
        self._status_case(
            builder=lambda: fixture.capture_criterion(
                self.repo, "accuracy-gsm8k", with_review=True
            ),
            expected_status="testing-incomplete",
        )

    def test_all_pending_empty_leftovers_derives_testing_incomplete(self) -> None:
        dest, candidate = fixture.capture_criterion(self.repo, "accuracy-gsm8k")
        self.assertEqual(candidate.bundle["review_evidence_artifact_ids"], [])
        review_path, review = fixture.write_review(
            self.repo,
            candidate,
            expected_status="testing-incomplete",
            privacy="pending",
            provenance_overrides={
                "artifact_identity": "pending",
                "runtime_identity": "pending",
                "contract_frozen_before_testing": "pending",
                "security": "pending",
            },
        )
        code, payload, stdout, stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 0, stderr or stdout)
        assert payload is not None
        self.assertEqual(payload["status"], "testing-incomplete")
        self.assert_trust_caveats(payload, stdout)
        plan = issue.build_issue_plan(
            repo_root=self.repo, candidate_dir=dest, review=review
        )
        self.assertEqual(plan.bundle["review_evidence_artifact_ids"], [])
        self.assertEqual(
            plan.decision["provenance_security_review"]["evidence_artifact_ids"],
            [],
        )

    def test_conclusive_provenance_without_leftovers_fails(self) -> None:
        dest, candidate = fixture.capture_criterion(self.repo, "accuracy-gsm8k")
        self.assertEqual(candidate.bundle["review_evidence_artifact_ids"], [])
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="testing-incomplete"
        )
        code, payload, stdout, stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1, stdout)
        assert payload is not None
        self.assertFalse(payload["ok"])
        self.assertIn("extra review files", payload["error"])
        self.assert_trust_caveats(payload, stderr)

    def test_derived_tested_criteria_not_met_from_measurement(self) -> None:
        def builder():
            inputs = capture_fixture.failing_measurement_spec(self.repo)
            inputs.attempt["review_source_keys"] = ["provenance-review"]
            inputs.attempt["evidence_sources"].append(
                capture_fixture.review_protected_source()
            )
            inputs.attempt["evidence_sources"] = sorted(
                inputs.attempt["evidence_sources"],
                key=lambda item: item["source_key"],
            )
            capture_fixture.write_json(inputs.attempt_path, inputs.attempt)
            return fixture.persist_capture(inputs, self.repo)

        self._status_case(builder=builder, expected_status="tested-criteria-not-met")

    def test_derived_tested_inconclusive_status(self) -> None:
        def builder():
            inputs = capture_fixture.incomplete_attempt_spec(
                self.repo, completion="interrupted", reason="capture-interrupted"
            )
            inputs.attempt["review_source_keys"] = ["provenance-review"]
            inputs.attempt["evidence_sources"].append(
                capture_fixture.review_protected_source()
            )
            inputs.attempt["evidence_sources"] = sorted(
                inputs.attempt["evidence_sources"],
                key=lambda item: item["source_key"],
            )
            capture_fixture.write_json(inputs.attempt_path, inputs.attempt)
            return fixture.persist_capture(inputs, self.repo)

        self._status_case(builder=builder, expected_status="tested-inconclusive")

    def test_derived_validated_status(self) -> None:
        self._status_case(
            builder=lambda: fixture.complete_passing_candidate(self.repo),
            expected_status="validated",
        )

    def test_failed_privacy_derives_tested_criteria_not_met(self) -> None:
        self._status_case(
            builder=lambda: fixture.complete_passing_candidate(self.repo),
            expected_status="tested-criteria-not-met",
            privacy="failed",
        )

    def test_pending_privacy_derives_testing_incomplete(self) -> None:
        self._status_case(
            builder=lambda: fixture.complete_passing_candidate(self.repo),
            expected_status="testing-incomplete",
            privacy="pending",
        )

    def test_failed_provenance_component_derives_tested_criteria_not_met(self) -> None:
        self._status_case(
            builder=lambda: fixture.complete_passing_candidate(self.repo),
            expected_status="tested-criteria-not-met",
            provenance_overrides={"security": "fail"},
        )

    def test_expected_status_mismatch_fails(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        code, payload, stdout, stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1, stdout)
        assert payload is not None
        self.assertFalse(payload["ok"])
        self.assertIn("disagrees with evidence", payload["error"])
        self.assert_trust_caveats(payload, stderr)

    def test_review_must_cover_exact_artifact_set(self) -> None:
        dest, candidate = fixture.capture_criterion(
            self.repo, "latency-ttft", with_review=True
        )
        review = fixture.review_declaration(
            candidate, expected_status="testing-incomplete"
        )
        review["artifacts"] = review["artifacts"][1:]
        path = self.repo / "reviews" / "missing-artifact.json"
        fixture.write_json(path, review)
        code, payload, _stdout, _stderr = self.plan_or_stage("plan", dest, path)
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("cover the candidate artifact set exactly", payload["error"])

        extra = copy.deepcopy(
            fixture.review_declaration(candidate, expected_status="testing-incomplete")
        )
        extra["artifacts"].append(
            {"artifact_id": "a" * 64, "privacy_review": "passed"}
        )
        extra["artifacts"] = sorted(
            extra["artifacts"], key=lambda item: item["artifact_id"]
        )
        extra_path = self.repo / "reviews" / "extra-artifact.json"
        fixture.write_json(extra_path, extra)
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, extra_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("cover the candidate artifact set exactly", payload["error"])

    def test_unknown_review_field_rejected(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review = fixture.review_declaration(candidate, expected_status="untested")
        review["decision_id"] = "b" * 64
        path = self.repo / "reviews" / "unknown-field.json"
        fixture.write_json(path, review)
        code, payload, _stdout, _stderr = self.plan_or_stage("plan", dest, path)
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("fields differ", payload["error"])

    def test_external_input_paths_are_redacted_from_json_errors(self) -> None:
        candidate_dir = self.tmpdir / "external-candidate"
        review_path = self.tmpdir / "external-review.json"
        code, stdout, stderr = self.run_main(
            [
                "plan",
                "--candidate-dir",
                str(candidate_dir),
                "--review-file",
                str(review_path),
                "--json",
            ]
        )
        self.assertEqual(code, 1, stderr)
        self.assertNotIn(str(self.tmpdir), stdout)
        payload = json.loads(stdout)
        self.assertIn("missing", payload["error"])

    def test_deterministic_old_to_new_ids_and_nested_remap(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        exclusion = fixture.exclusion_for(candidate, "accuracy-gsm8k")
        review_path, _review = fixture.write_review(
            self.repo,
            candidate,
            expected_status="testing-incomplete",
            exclusions=[exclusion],
        )
        first = self.plan_or_stage("plan", dest, review_path)[1]
        second = self.plan_or_stage("plan", dest, review_path)[1]
        assert first is not None and second is not None
        self.assertEqual(first["artifact_id_map"], second["artifact_id_map"])
        self.assertEqual(first["run_record_id_map"], second["run_record_id_map"])
        self.assertEqual(first["bundle_id_map"], second["bundle_id_map"])
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertNotEqual(
            first["artifact_id_map"],
            {key: key for key in first["artifact_id_map"]},
        )
        old_review = fixture.first_review_artifact_id(candidate)
        new_review = first["artifact_id_map"][old_review]
        plan = issue.build_issue_plan(
            repo_root=self.repo,
            candidate_dir=dest,
            review=fixture.review_declaration(
                candidate,
                expected_status="testing-incomplete",
                exclusions=[exclusion],
            ),
        )
        rebuilt = {item["run_record_id"]: item for item in plan.run_records}
        self.assertTrue(rebuilt)
        for original in candidate.run_records:
            record = rebuilt[plan.run_record_id_map[original["run_record_id"]]]
            self.assertEqual(
                record["attempt"]["attempt_id"], original["attempt"]["attempt_id"]
            )
            self.assertEqual(
                record["attempt"]["started_at"], original["attempt"]["started_at"]
            )
            self.assertEqual(
                record["attempt"]["ended_at"], original["attempt"]["ended_at"]
            )
            self.assertEqual(record["commands"], original["commands"])
            self.assertEqual(
                record["observed_environment"], original["observed_environment"]
            )
            for observation, source in zip(
                record["criterion_observations"],
                original["criterion_observations"],
            ):
                self.assertEqual(observation["metrics"], source["metrics"])
                self.assertEqual(
                    observation["evidence_artifact_ids"],
                    sorted(
                        first["artifact_id_map"][item]
                        for item in source["evidence_artifact_ids"]
                    ),
                )
                requirements = observation["contract_requirements"]
                source_req = source["contract_requirements"]
                for name in ("context", "soak"):
                    block = requirements.get(name)
                    source_block = source_req.get(name)
                    if source_block is None:
                        self.assertIsNone(block)
                        continue
                    self.assertEqual(
                        block["evidence_artifact_ids"],
                        sorted(
                            first["artifact_id_map"][item]
                            for item in source_block["evidence_artifact_ids"]
                        ),
                    )
        self.assertEqual(
            plan.bundle["review_evidence_artifact_ids"], [new_review]
        )
        excluded = plan.decision["criterion_results"]
        accuracy = next(
            item for item in excluded if item["criterion_id"] == "accuracy-gsm8k"
        )
        self.assertEqual(accuracy["included_run_record_ids"], [])
        self.assertEqual(
            accuracy["excluded_run_records"][0]["review_evidence_artifact_ids"],
            [new_review],
        )

    def test_protected_conversion_and_publishable_copy_policy(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        publishable = [
            item
            for item in candidate.bundle["evidence_artifacts"]
            if item["visibility"] == "publishable"
        ]
        self.assertTrue(publishable)
        failed_path, _review = fixture.write_review(
            self.repo,
            candidate,
            name="failed-privacy",
            expected_status="tested-criteria-not-met",
            privacy="failed",
        )
        code, payload, _stdout, stderr = self.plan_or_stage(
            "plan", dest, failed_path
        )
        self.assertEqual(code, 0, stderr)
        assert payload is not None
        planned = {item["path"] for item in payload["files"]}
        for artifact in publishable:
            self.assertNotIn(artifact["location"]["value"], planned)
        plan = issue.build_issue_plan(
            repo_root=self.repo,
            candidate_dir=dest,
            review=fixture.review_declaration(
                candidate,
                expected_status="tested-criteria-not-met",
                privacy="failed",
            ),
        )
        for artifact in plan.artifacts:
            self.assertEqual(artifact["visibility"], "protected")
            self.assertEqual(
                artifact["location"]["kind"], "protected-content-addressed"
            )
            self.assertEqual(
                artifact["location"]["value"],
                "sha256:" + artifact["content"]["sha256"],
            )
        passed_path, _passed = fixture.write_review(
            self.repo,
            candidate,
            name="passed-privacy",
            expected_status="validated",
        )
        code, payload, _stdout, stderr = self.plan_or_stage(
            "plan", dest, passed_path
        )
        self.assertEqual(code, 0, stderr)
        assert payload is not None
        planned = {item["path"] for item in payload["files"]}
        for artifact in publishable:
            relative = artifact["location"]["value"]
            self.assertIn(relative, planned)
            entry = next(item for item in payload["files"] if item["path"] == relative)
            self.assertEqual(entry["action"], "reuse")
            self.assertEqual(entry["sha256"], artifact["content"]["sha256"])

    def test_candidate_immutability_and_no_source_path_leak(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        before = fixture.candidate_bytes(dest)
        self.commit_current("candidate and review")
        code, payload, stdout, stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 0, stderr or stdout)
        assert payload is not None
        self.assert_candidate_unchanged(dest, before)
        self.assert_no_path_leak(payload, dest)
        self.assertNotIn(str(dest), stdout)
        self.assert_production_unchanged()

    def test_source_and_program_drift_rejected(self) -> None:
        dest, candidate = fixture.capture_criterion(
            self.repo, "throughput-serving", with_review=True
        )
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="testing-incomplete"
        )
        source = next(
            item
            for item in candidate.bundle["evidence_artifacts"]
            if item["visibility"] == "publishable"
        )
        source_path = self.repo.joinpath(
            *pathlib.PurePosixPath(source["location"]["value"]).parts
        )
        source_path.write_bytes(b"drifted-bytes")
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("drifted", payload["error"])

        dest, candidate = fixture.capture_criterion(
            self.repo, "latency-ttft", with_review=True
        )
        review_path, _review = fixture.write_review(
            self.repo,
            candidate,
            name="program-drift",
            expected_status="testing-incomplete",
        )
        program = self.repo / "validate" / "run-gates.sh"
        program.write_bytes(program.read_bytes() + b"\n# drift\n")
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("drifted", payload["error"])

    def test_changed_candidate_rejected(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        manifest = json.loads((dest / "candidate.json").read_text(encoding="utf-8"))
        manifest["candidate_id"] = "c" * 64
        (dest / "candidate.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertTrue(
            "candidate" in payload["error"] or "identity" in payload["error"]
        )

    def test_stage_rechecks_candidate_after_planning_before_writes(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        self.commit_current("ready for candidate race")

        def change_candidate(_plan: issue.IssuePlan) -> None:
            manifest_path = dest / "candidate.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["candidate_id"] = "d" * 64
            fixture.write_json(manifest_path, manifest)

        previous = issue.AFTER_PLAN_HOOK
        issue.AFTER_PLAN_HOOK = change_candidate
        try:
            code, payload, _stdout, _stderr = self.plan_or_stage(
                "stage", dest, review_path
            )
        finally:
            issue.AFTER_PLAN_HOOK = previous
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertTrue(
            "candidate" in payload["error"] or "identity" in payload["error"]
        )
        registry_json = list(
            (self.repo / "models" / "model-serving-releases").rglob("*.json")
        )
        self.assertEqual(registry_json, [])

    def test_predecessor_resolution_from_tracked_registry(self) -> None:
        predecessor = fixture.write_predecessor_registry(self.repo)
        release = evidence_fixture.build_release()
        contract = evidence_fixture.build_relative_contract(
            release=release, predecessor_source=predecessor
        )
        dests = []
        built_runs = []
        for index, criterion_id in enumerate(sorted(evidence_fixture.PASS_METRICS)):
            extra = (
                capture_fixture.review_protected_source() if index == 0 else None
            )
            inputs = capture_fixture.passing_criterion_spec(
                criterion_id,
                repo_root=self.repo,
                release=release,
                contract=contract,
                extra_protected=extra,
            )
            _path, built = fixture.persist_capture(inputs, self.repo)
            dests.append(_path)
            built_runs.append(built)
        dest, candidate = fixture.assemble_persisted(built_runs, self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        code, payload, _stdout, stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 0, stderr)
        assert payload is not None
        self.assertEqual(payload["status"], "validated")
        self.assertEqual(
            payload["release_id"], candidate.release["release_id"]
        )

    def test_supersession_chronology_and_unrelated_lineage(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        self.commit_current("first candidate")
        code, first, _stdout, stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 0, stderr)
        assert first is not None
        later_path, _later = fixture.write_review(
            self.repo,
            candidate,
            name="later",
            expected_status="validated",
            reviewed_at="2026-08-16T01:00:00Z",
            review_reference="repository-review:fixture-later",
            supersedes_decision_ids=[str(first["decision_id"])],
        )
        code, later, _stdout, stderr = self.plan_or_stage(
            "plan", dest, later_path
        )
        self.assertEqual(code, 0, stderr)
        assert later is not None
        self.assertNotEqual(later["decision_id"], first["decision_id"])

        early_path, _early = fixture.write_review(
            self.repo,
            candidate,
            name="early",
            expected_status="validated",
            reviewed_at="2026-08-14T00:00:00Z",
            review_reference="repository-review:fixture-early",
            supersedes_decision_ids=[str(first["decision_id"])],
        )
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, early_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertTrue(
            "later" in payload["error"] or "predates" in payload["error"]
        )

        predecessor = fixture.write_predecessor_registry(self.repo)
        unrelated_path, _unrelated = fixture.write_review(
            self.repo,
            candidate,
            name="unrelated",
            expected_status="validated",
            reviewed_at="2026-08-16T02:00:00Z",
            review_reference="repository-review:fixture-unrelated",
            supersedes_decision_ids=[predecessor["decision"]["decision_id"]],
        )
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, unrelated_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("release and contract", payload["error"])

    def test_ambiguous_heads_and_equal_reuse(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        first_path, _first = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        self.commit_current("ready to stage")
        code, first, stdout, stderr = self.plan_or_stage(
            "stage", dest, first_path
        )
        self.assertEqual(code, 0, f"{stderr}\n{stdout}")
        assert first is not None
        code, retry_payload, _stdout, stderr = self.plan_or_stage(
            "stage", dest, first_path
        )
        self.assertEqual(code, 0, stderr)
        assert retry_payload is not None
        self.assertTrue(
            all(item["action"] == "reuse" for item in retry_payload["files"])
        )
        self.assertEqual(
            {item["path"] for item in retry_payload["files"]},
            {item["path"] for item in first["files"]},
        )
        second_path, _second = fixture.write_review(
            self.repo,
            candidate,
            name="second-head",
            expected_status="validated",
            reviewed_at="2026-08-16T03:00:00Z",
            review_reference="repository-review:fixture-second-head",
        )
        code, second, _stdout, stderr = self.plan_or_stage(
            "plan", dest, second_path
        )
        self.assertEqual(code, 0, stderr)
        assert second is not None
        self.assertEqual(second["projection"]["state"], registry.INSPECTION_AMBIGUOUS)

    def test_unequal_collision_symlink_and_directory(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        plan = issue.build_issue_plan(
            repo_root=self.repo,
            candidate_dir=dest,
            review=issue.load_review_declaration(review_path),
        )
        target = next(
            relative
            for relative in plan.files
            if relative.startswith("models/model-serving-releases/descriptors/")
        )
        dest_path = self.repo / "results" / "issuance-collision.json"
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"not-the-object\n")
        with self.assertRaisesRegex(issue.ModelServingReleaseIssueError, "different bytes"):
            issue.planned_file_action(
                self.repo, "results/issuance-collision.json", b"expected-bytes\n"
            )
        dest_path.unlink()
        dest_path.symlink_to("/tmp/issuance-symlink-target")
        with self.assertRaisesRegex(issue.ModelServingReleaseIssueError, "symlink"):
            issue.inspect_destination(self.repo, "results/issuance-collision.json")
        dest_path.unlink()
        dest_path.mkdir()
        with self.assertRaisesRegex(issue.ModelServingReleaseIssueError, "regular file"):
            issue.inspect_destination(self.repo, "results/issuance-collision.json")
        del target
        del review_path

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(issue.ModelServingReleaseIssueError):
            issue._safe_relative("../secrets.txt", label="destination")
        with self.assertRaises(issue.ModelServingReleaseIssueError):
            issue._safe_relative(".git/config", label="destination")
        with self.assertRaises(issue.ModelServingReleaseIssueError):
            issue._safe_relative("/etc/passwd", label="destination")

    def test_stage_refuses_default_detached_and_dirty(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        self.commit_current("clean candidate")
        fixture.run_git(self.repo, "checkout", "main")
        fixture.run_git(self.repo, "merge", "--ff-only", "issue/fixture")
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("default branch", payload["error"])
        fixture.run_git(self.repo, "checkout", "issue/fixture")
        fixture.run_git(self.repo, "checkout", "--detach")
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("detached", payload["error"])
        fixture.run_git(self.repo, "checkout", "issue/fixture")
        (self.repo / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("clean worktree", payload["error"])

    def test_interruption_and_idempotent_retry(self) -> None:
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        self.commit_current("ready for interrupted stage")
        created: list[str] = []

        def hook(relative: str) -> None:
            created.append(relative)
            if "/run-records/" in relative:
                raise issue.ModelServingReleaseIssueError("forced interruption")

        previous = issue.AFTER_FILE_WRITE_HOOK
        issue.AFTER_FILE_WRITE_HOOK = hook
        try:
            code, payload, _stdout, _stderr = self.plan_or_stage(
                "stage", dest, review_path
            )
        finally:
            issue.AFTER_FILE_WRITE_HOOK = previous
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("forced interruption", payload["error"])
        self.assertTrue(any("/run-records/" in item for item in created))
        self.assertTrue(
            (self.repo / pathlib.PurePosixPath(created[-1])).is_file()
        )
        with self.assertRaises(registry.ModelServingReleaseRegistryError):
            registry.load_registry(self.repo)
        code, payload, stdout, stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 0, stderr or stdout)
        assert payload is not None
        graph = registry.load_registry(self.repo)
        self.assertIn(payload["decision_id"], graph.decisions)
        inspected = registry.inspect_release(graph, str(payload["release_id"]))
        self.assertEqual(inspected["inspection"]["state"], registry.INSPECTION_UNIQUE)
        self.assertEqual(inspected["inspection"]["effective_status"], "validated")
        reused = [item for item in payload["files"] if item["action"] == "reuse"]
        created_files = [
            item for item in payload["files"] if item["action"] == "create"
        ]
        self.assertTrue(reused)
        self.assertTrue(created_files)

    def test_unrelated_orphan_run_still_fails_prospective_graph(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        orphan = evidence_fixture.build_run_for_criterion("accuracy-gsm8k")
        registry_fixture.write_run(
            self.repo / "models" / "model-serving-releases", orphan
        )
        with self.assertRaises(registry.ModelServingReleaseRegistryError):
            registry.load_registry(self.repo)
        code, payload, _stdout, _stderr = self.plan_or_stage(
            "plan", dest, review_path
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("not referenced by a stored evidence bundle", payload["error"])

    def test_plan_is_readonly_and_json_is_stable(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        before = fixture.candidate_bytes(dest)
        registry_before = [
            path.relative_to(self.repo)
            for path in (self.repo / "models" / "model-serving-releases").rglob("*")
            if path.is_file()
        ]
        first = self.plan_or_stage("plan", dest, review_path)[1]
        second = self.plan_or_stage("plan", dest, review_path)[1]
        assert first is not None and second is not None
        self.assertEqual(first, second)
        self.assertEqual(fixture.candidate_bytes(dest), before)
        after = [
            path.relative_to(self.repo)
            for path in (self.repo / "models" / "model-serving-releases").rglob("*")
            if path.is_file()
        ]
        self.assertEqual(after, registry_before)
        self.assertNotIn("serving_authorization", first)

    def test_narrow_terminal_output(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="untested"
        )
        plan = issue.build_issue_plan(
            repo_root=self.repo,
            candidate_dir=dest,
            review=issue.load_review_declaration(review_path),
        )
        payload = issue.plan_payload("plan", plan)
        buffer = io.StringIO()
        writer_holder = []

        class Narrow(issue.terminal_format.TerminalWriter):
            def __init__(self) -> None:
                super().__init__(width=40, stream=buffer)

        original = issue.terminal_format.TerminalWriter

        def factory(*_args: object, **_kwargs: object) -> Narrow:
            item = Narrow()
            writer_holder.append(item)
            return item

        issue.terminal_format.TerminalWriter = factory  # type: ignore[assignment]
        try:
            issue.render_result(payload)
        finally:
            issue.terminal_format.TerminalWriter = original  # type: ignore[assignment]
        text = buffer.getvalue()
        self.assertTrue(text)
        self.assertTrue(all(len(line) <= 40 for line in text.splitlines()))
        collapsed = " ".join(text.split())
        self.assertIn("not trusted until repository review and merge", collapsed)

    def test_review_reference_grammar(self) -> None:
        dest, candidate = fixture.capture_prebarrier(self.repo)
        for reference, name in (
            ("pr:42", "pr-42"),
            ("commit:" + ("a" * 40), "commit-ref"),
            ("repository-review:pre-pr-staging", "repo-review-ref"),
        ):
            path, _review = fixture.write_review(
                self.repo,
                candidate,
                name=name,
                expected_status="untested",
                review_reference=reference,
            )
            code, payload, stdout, stderr = self.plan_or_stage("plan", dest, path)
            self.assertEqual(code, 0, f"{reference}: {stderr}\n{stdout}")
            assert payload is not None
            self.assertTrue(payload["ok"])
        bad = fixture.review_declaration(
            candidate,
            expected_status="untested",
            review_reference="github:not-a-closed-form",
        )
        bad_path = self.repo / "reviews" / "bad-ref.json"
        fixture.write_json(bad_path, bad)
        code, payload, _stdout, _stderr = self.plan_or_stage("plan", dest, bad_path)
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertIn("review_reference", payload["error"])

    def test_validate_registry_graph_rejects_cycle(self) -> None:
        source = registry_fixture.build_happy_source()
        graph = registry.RegistryGraph(
            repo_root=self.repo,
            registry_root=self.repo / "models" / "model-serving-releases",
            descriptors={source["release"]["release_id"]: source["release"]},
            contracts={source["contract"]["contract_id"]: source["contract"]},
            run_records={
                item["run_record_id"]: item for item in source["run_records"]
            },
            evidence_bundles={
                source["evidence_bundle"]["bundle_id"]: source["evidence_bundle"]
            },
            decisions={source["decision"]["decision_id"]: source["decision"]},
        )
        later = evidence_fixture.build_decision(
            release=source["release"],
            contract=source["contract"],
            artifacts=source["evidence_bundle"]["evidence_artifacts"],
            run_records=source["run_records"],
            bundle=source["evidence_bundle"],
            supersedes=[source["decision"]],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        cyclic = copy.deepcopy(source["decision"])
        cyclic["supersedes_decision_ids"] = [later["decision_id"]]
        cyclic["decision_id"] = model_validation_evidence.validation_decision_id(
            cyclic
        )
        later_cycle = copy.deepcopy(later)
        later_cycle["supersedes_decision_ids"] = [cyclic["decision_id"]]
        later_cycle["decision_id"] = model_validation_evidence.validation_decision_id(
            later_cycle
        )
        graph.decisions = {
            cyclic["decision_id"]: cyclic,
            later_cycle["decision_id"]: later_cycle,
        }
        registry_fixture.write_publishable_artifacts(
            self.repo, source["evidence_bundle"]["evidence_artifacts"]
        )
        with self.assertRaises(registry.ModelServingReleaseRegistryError):
            registry.validate_registry_graph(graph)

    def test_production_registry_and_profiles_untouched(self) -> None:
        self.assert_production_unchanged()
        dest, candidate = fixture.complete_passing_candidate(self.repo)
        review_path, _review = fixture.write_review(
            self.repo, candidate, expected_status="validated"
        )
        self.commit_current("stage proposal in temp repo")
        code, payload, _stdout, stderr = self.plan_or_stage(
            "stage", dest, review_path
        )
        self.assertEqual(code, 0, stderr)
        assert payload is not None
        self.assertTrue(
            (self.repo / "models" / "model-serving-releases" / "decisions" /
             f"{payload['decision_id']}.json").is_file()
        )
        self.assert_production_unchanged()
        self.assertFalse(
            any(
                path.is_symlink()
                for path in (self.repo / "models" / "model-serving-releases").rglob("*")
            )
        )


if __name__ == "__main__":
    unittest.main()
