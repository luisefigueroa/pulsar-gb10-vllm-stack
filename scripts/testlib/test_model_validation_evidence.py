#!/usr/bin/env python3
"""Contracts for immutable validation evidence and reviewed decisions."""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_identity,
    model_serving_release,
    model_validation_evidence as evidence,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402
from scripts.testlib import model_validation_evidence_fixture as fixture  # noqa: E402


class ModelValidationEvidenceSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = fixture.build_release()
        self.contract = fixture.build_contract(release=self.release)
        self.artifacts = fixture.build_artifacts()
        self.runs = fixture.build_passing_runs(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
        )
        self.bundle = fixture.build_bundle(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
        )
        self.decision = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
        )

    def _replace_run(
        self,
        criterion_id: str,
        replacement: dict[str, Any],
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        source_records = self.runs if records is None else records
        records = [
            record
            for record in source_records
            if not any(
                item["criterion_id"] == criterion_id
                for item in record["criterion_observations"]
            )
        ]
        records.append(replacement)
        return sorted(records, key=lambda item: item["run_record_id"])

    def _bundle_and_decision(
        self,
        records: list[dict[str, Any]],
        *,
        status: str,
        selections: dict[str, list[str]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        review: dict[str, Any] | None = None,
        contract: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        artifacts = artifacts or self.artifacts
        contract = contract or self.contract
        bundle = fixture.build_bundle(
            release=self.release,
            contract=contract,
            artifacts=artifacts,
            run_records=records,
        )
        decision = fixture.build_decision(
            release=self.release,
            contract=contract,
            artifacts=artifacts,
            run_records=records,
            bundle=bundle,
            selections=(
                selections
                if selections is not None
                else fixture.passing_selections(records)
            ),
            provenance_review=review,
            status=status,
        )
        return bundle, decision

    def test_fixture_has_frozen_ids_and_validated_status(self) -> None:
        self.assertEqual(len(self.runs), 7)
        self.assertEqual(
            self.runs[0]["run_record_id"], fixture.EXPECTED_FIRST_RUN_RECORD_ID
        )
        self.assertEqual(
            self.bundle["bundle_id"], fixture.EXPECTED_EVIDENCE_BUNDLE_ID
        )
        self.assertEqual(
            self.decision["decision_id"], fixture.EXPECTED_VALIDATION_DECISION_ID
        )
        self.assertEqual(self.decision["status"], "validated")
        self.assertEqual(
            evidence.validation_status_label(self.decision["status"]), "Validated"
        )

    def test_documents_survive_canonical_json_round_trip(self) -> None:
        artifacts = json.loads(
            json.dumps(self.artifacts, sort_keys=True, separators=(",", ":"))
        )
        runs = json.loads(
            json.dumps(self.runs, sort_keys=True, separators=(",", ":"))
        )
        bundle = json.loads(
            json.dumps(self.bundle, sort_keys=True, separators=(",", ":"))
        )
        decision = json.loads(
            json.dumps(self.decision, sort_keys=True, separators=(",", ":"))
        )
        for record in runs:
            self.assertEqual(
                evidence.validate_validation_run_record(
                    record,
                    release=self.release,
                    contract=self.contract,
                    evidence_artifacts=artifacts,
                ),
                record,
            )
        self.assertEqual(
            evidence.validate_validation_evidence_bundle(
                bundle,
                release=self.release,
                contract=self.contract,
                run_records=runs,
            ),
            bundle,
        )
        self.assertEqual(
            evidence.validate_validation_decision(
                decision,
                release=self.release,
                contract=self.contract,
                evidence_bundle=bundle,
                run_records=runs,
            ),
            decision,
        )

    def test_bundle_builder_normalizes_record_and_artifact_sets(self) -> None:
        rebuilt = evidence.build_validation_evidence_bundle(
            release=self.release,
            contract=self.contract,
            run_records=list(reversed(self.runs)),
            evidence_artifacts=list(reversed(self.artifacts)),
            review_evidence_artifact_ids=list(
                reversed(self.bundle["review_evidence_artifact_ids"])
            ),
        )
        self.assertEqual(rebuilt, self.bundle)

    def test_attempt_environment_or_command_change_changes_run_identity(self) -> None:
        baseline = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
        )
        changed_environment = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            launch_id=fixture.digest("another-launch"),
        )
        changed_command = copy.deepcopy(baseline)
        changed_command["commands"][0]["arguments"][-1] = "another-tag"
        changed_command["run_record_id"] = evidence.validation_run_record_id(
            changed_command
        )
        evidence.validate_validation_run_record(
            changed_command,
            release=self.release,
            contract=self.contract,
            evidence_artifacts=self.artifacts,
        )
        self.assertNotEqual(baseline["run_record_id"], changed_environment["run_record_id"])
        self.assertNotEqual(baseline["run_record_id"], changed_command["run_record_id"])

    def test_prequalification_distribution_failure_remains_untested(self) -> None:
        record, artifacts = fixture.build_prequalification_failure(
            release=self.release, contract=self.contract
        )
        review_artifact = fixture.build_artifact(
            "preparation-failure-review", "release-promotion", protected=True
        )
        artifacts.append(review_artifact)
        artifacts.sort(key=lambda item: item["artifact_id"])
        artifact_id = review_artifact["artifact_id"]
        bundle = evidence.build_validation_evidence_bundle(
            release=self.release,
            contract=self.contract,
            run_records=[record],
            evidence_artifacts=artifacts,
            review_evidence_artifact_ids=[artifact_id],
        )
        review = evidence.build_provenance_security_review(
            artifact_identity="pending",
            runtime_identity="pending",
            contract_frozen_before_testing="pass",
            evidence_privacy="pass",
            security="pending",
            evidence_artifact_ids=[artifact_id],
        )
        decision = evidence.build_validation_decision(
            release=self.release,
            contract=self.contract,
            evidence_bundle=bundle,
            run_records=[record],
            criterion_run_record_ids={},
            provenance_security_review=review,
            status="untested",
            reviewer="fixture-maintainer",
            reviewed_at="2026-08-14T13:00:00Z",
            review_reference="repository-review:preparation-failure",
        )
        self.assertFalse(bundle["qualification_started"])
        self.assertEqual(decision["status"], "untested")

    def test_experimental_distribution_does_not_cap_validated_status(self) -> None:
        self.assertTrue(
            all(
                record["preparation_provenance"]["subsystems"][0]["maturity"]
                == "experimental"
                for record in self.runs
            )
        )
        self.assertEqual(self.decision["status"], "validated")

    def test_conclusive_threshold_failure_derives_criteria_not_met(self) -> None:
        failed = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            metrics=[
                {
                    "metric": "output_tokens_per_second",
                    "value": "10",
                    "unit": "tokens-per-second",
                }
            ],
        )
        records = self._replace_run("throughput-serving", failed)
        _bundle, decision = self._bundle_and_decision(
            records, status="tested-criteria-not-met"
        )
        result = next(
            item
            for item in decision["criterion_results"]
            if item["criterion_id"] == "throughput-serving"
        )
        self.assertEqual(result["disposition"], "fail")

    def test_context_requirement_is_part_of_the_accuracy_verdict(self) -> None:
        missing = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            include_context_requirement=False,
        )
        records = self._replace_run("accuracy-gsm8k", missing)
        _bundle, incomplete = self._bundle_and_decision(
            records, status="testing-incomplete"
        )
        missing_result = next(
            item
            for item in incomplete["criterion_results"]
            if item["criterion_id"] == "accuracy-gsm8k"
        )
        self.assertEqual(missing_result["reason"], "context-evidence-missing")

        insufficient = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            context_minimum_tokens=1024,
            context_depths=["0.05", "0.5"],
        )
        records = self._replace_run("accuracy-gsm8k", insufficient)
        _bundle, failed = self._bundle_and_decision(
            records, status="tested-criteria-not-met"
        )
        failed_result = next(
            item
            for item in failed["criterion_results"]
            if item["criterion_id"] == "accuracy-gsm8k"
        )
        self.assertEqual(failed_result["reason"], "context-minimum-not-satisfied")

        shallow = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            context_depths=["0.05", "0.5"],
        )
        records = self._replace_run("accuracy-gsm8k", shallow)
        _bundle, failed = self._bundle_and_decision(
            records, status="tested-criteria-not-met"
        )
        failed_result = next(
            item
            for item in failed["criterion_results"]
            if item["criterion_id"] == "accuracy-gsm8k"
        )
        self.assertEqual(failed_result["reason"], "context-depths-not-satisfied")

    def test_soak_requirement_is_part_of_the_stability_verdict(self) -> None:
        missing = fixture.build_run_for_criterion(
            "stability-soak",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            include_soak_requirement=False,
        )
        records = self._replace_run("stability-soak", missing)
        _bundle, incomplete = self._bundle_and_decision(
            records, status="testing-incomplete"
        )
        missing_result = next(
            item
            for item in incomplete["criterion_results"]
            if item["criterion_id"] == "stability-soak"
        )
        self.assertEqual(missing_result["reason"], "soak-evidence-missing")

        short = fixture.build_run_for_criterion(
            "stability-soak",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            soak_duration_seconds="120",
        )
        records = self._replace_run("stability-soak", short)
        _bundle, failed = self._bundle_and_decision(
            records, status="tested-criteria-not-met"
        )
        failed_result = next(
            item
            for item in failed["criterion_results"]
            if item["criterion_id"] == "stability-soak"
        )
        self.assertEqual(failed_result["reason"], "soak-duration-not-satisfied")

        wrong_concurrency = fixture.build_run_for_criterion(
            "stability-soak",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            soak_concurrency=4,
        )
        records = self._replace_run("stability-soak", wrong_concurrency)
        _bundle, failed = self._bundle_and_decision(
            records, status="tested-criteria-not-met"
        )
        failed_result = next(
            item
            for item in failed["criterion_results"]
            if item["criterion_id"] == "stability-soak"
        )
        self.assertEqual(
            failed_result["reason"], "soak-concurrency-not-satisfied"
        )

    def test_inconclusive_context_or_soak_evidence_is_not_a_missing_gate(self) -> None:
        cases = (
            (
                "accuracy-gsm8k",
                {"context_completion": "inconclusive"},
                "context-evidence-inconclusive",
            ),
            (
                "stability-soak",
                {"soak_completion": "inconclusive"},
                "soak-evidence-inconclusive",
            ),
        )
        for criterion_id, overrides, expected_reason in cases:
            with self.subTest(criterion_id=criterion_id):
                replacement = fixture.build_run_for_criterion(
                    criterion_id,
                    release=self.release,
                    contract=self.contract,
                    artifacts=self.artifacts,
                    **overrides,
                )
                records = self._replace_run(criterion_id, replacement)
                _bundle, decision = self._bundle_and_decision(
                    records, status="tested-inconclusive"
                )
                result = next(
                    item
                    for item in decision["criterion_results"]
                    if item["criterion_id"] == criterion_id
                )
                self.assertEqual(result["reason"], expected_reason)

    def test_required_relative_budgets_gate_validated_status(self) -> None:
        contract = fixture.build_relative_contract(release=self.release)
        runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        _bundle, passing = self._bundle_and_decision(
            runs, status="validated", contract=contract
        )
        self.assertEqual(passing["status"], "validated")

        cases = (
            (
                "throughput-serving",
                "output_tokens_per_second",
                "23",
                "25",
                "tokens-per-second",
            ),
            ("latency-ttft", "ttft_p95", "1200", "1000", "milliseconds"),
        )
        for criterion_id, metric, current, predecessor, unit in cases:
            with self.subTest(criterion_id=criterion_id):
                regressed = fixture.build_run_for_criterion(
                    criterion_id,
                    release=self.release,
                    contract=contract,
                    artifacts=self.artifacts,
                    metrics=[{"metric": metric, "value": current, "unit": unit}],
                    relative_predecessor_metrics=[
                        {"metric": metric, "value": predecessor, "unit": unit}
                    ],
                )
                records = self._replace_run(
                    criterion_id, regressed, records=runs
                )
                _bundle, failed = self._bundle_and_decision(
                    records,
                    status="tested-criteria-not-met",
                    contract=contract,
                )
                result = next(
                    item
                    for item in failed["criterion_results"]
                    if item["criterion_id"] == criterion_id
                )
                self.assertEqual(
                    result["reason"], "relative-regression-budget-exceeded"
                )

    def test_missing_or_inconclusive_relative_evidence_cannot_validate(self) -> None:
        contract = fixture.build_relative_contract(release=self.release)
        passing_runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        cases = (
            (
                "testing-incomplete",
                {"include_relative_comparison": False},
                "relative-performance-evidence-missing",
            ),
            (
                "tested-inconclusive",
                {"relative_completion": "inconclusive"},
                "relative-performance-evidence-inconclusive",
            ),
        )
        for expected_status, overrides, expected_reason in cases:
            with self.subTest(status=expected_status):
                replacement = fixture.build_run_for_criterion(
                    "latency-ttft",
                    release=self.release,
                    contract=contract,
                    artifacts=self.artifacts,
                    **overrides,
                )
                records = self._replace_run(
                    "latency-ttft", replacement, records=passing_runs
                )
                _bundle, decision = self._bundle_and_decision(
                    records,
                    status=expected_status,
                    contract=contract,
                )
                result = next(
                    item
                    for item in decision["criterion_results"]
                    if item["criterion_id"] == "latency-ttft"
                )
                self.assertEqual(result["reason"], expected_reason)

    def test_relative_comparison_cross_links_fail_closed(self) -> None:
        contract = fixture.build_relative_contract(release=self.release)
        record = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        comparison = record["criterion_observations"][0][
            "contract_requirements"
        ]["relative_performance"]
        assert comparison is not None
        for field in (
            "predecessor_release_id",
            "supported_hardware_geometry_id",
            "benchmark_protocol_id",
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(record)
                tampered["criterion_observations"][0]["contract_requirements"][
                    "relative_performance"
                ][field] = "0" * 64
                tampered["run_record_id"] = evidence.validation_run_record_id(
                    tampered
                )
                with self.assertRaisesRegex(
                    evidence.ModelValidationEvidenceError,
                    "differs from the frozen contract",
                ):
                    evidence.validate_validation_run_record(
                        tampered,
                        release=self.release,
                        contract=contract,
                        evidence_artifacts=self.artifacts,
                    )

        irrelevant = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
        )
        irrelevant["criterion_observations"][0]["contract_requirements"][
            "relative_performance"
        ] = copy.deepcopy(comparison)
        irrelevant["run_record_id"] = evidence.validation_run_record_id(irrelevant)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "without a comparable predecessor",
        ):
            evidence.validate_validation_run_record(
                irrelevant,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_inconclusive_observation_derives_tested_inconclusive(self) -> None:
        inconclusive = fixture.build_run_for_criterion(
            "latency-ttft",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            metrics=[],
            observation_completion="inconclusive",
            sample_size=4,
            attempt_completion="inconclusive",
        )
        records = self._replace_run("latency-ttft", inconclusive)
        _bundle, decision = self._bundle_and_decision(
            records, status="tested-inconclusive"
        )
        self.assertEqual(decision["status"], "tested-inconclusive")

    def test_interrupted_attempt_cannot_contribute_a_passing_criterion(self) -> None:
        interrupted = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            attempt_completion="interrupted",
        )
        records = self._replace_run("throughput-serving", interrupted)
        _bundle, decision = self._bundle_and_decision(
            records, status="tested-inconclusive"
        )
        result = next(
            item
            for item in decision["criterion_results"]
            if item["criterion_id"] == "throughput-serving"
        )
        self.assertEqual(result["disposition"], "inconclusive")

    def test_missing_gate_derives_testing_incomplete(self) -> None:
        selections = fixture.passing_selections(self.runs)
        selections["latency-ttft"] = []
        _bundle, decision = self._bundle_and_decision(
            self.runs,
            status="testing-incomplete",
            selections=selections,
        )
        self.assertEqual(decision["status"], "testing-incomplete")

    def test_pending_provenance_review_derives_testing_incomplete(self) -> None:
        review = fixture.build_review(
            self.artifacts, component_overrides={"runtime_identity": "pending"}
        )
        _bundle, decision = self._bundle_and_decision(
            self.runs,
            status="testing-incomplete",
            review=review,
        )
        self.assertEqual(decision["status"], "testing-incomplete")

    def test_explicit_status_is_rejected_when_evidence_derives_another(self) -> None:
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "status disagrees with evidence",
        ):
            fixture.build_decision(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=self.runs,
                bundle=self.bundle,
                status="testing-incomplete",
            )

    def test_rehashed_status_tamper_still_fails_independent_derivation(self) -> None:
        tampered = copy.deepcopy(self.decision)
        tampered["status"] = "testing-incomplete"
        tampered["decision_id"] = evidence.validation_decision_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "status disagrees with evidence",
        ):
            evidence.validate_validation_decision(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
            )

    def test_rehashed_criterion_disposition_tamper_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.decision)
        tampered["criterion_results"][0]["disposition"] = "fail"
        tampered["criterion_results"][0]["reason"] = "threshold-not-satisfied"
        tampered["decision_id"] = evidence.validation_decision_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "criterion results disagree with evidence",
        ):
            evidence.validate_validation_decision(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
            )

    def test_protocol_mismatch_fails_even_after_run_id_is_rehashed(self) -> None:
        tampered = copy.deepcopy(self.runs[0])
        tampered["criterion_observations"][0]["benchmark_protocol_id"] = "0" * 64
        tampered["run_record_id"] = evidence.validation_run_record_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "benchmark protocol identity mismatch",
        ):
            evidence.validate_validation_run_record(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_complete_observation_cannot_undershoot_frozen_sample_size(self) -> None:
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "sample is smaller",
        ):
            fixture.build_run_for_criterion(
                "accuracy-gsm8k",
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                sample_size=1,
            )
        record = fixture.build_run_for_criterion(
            "accuracy-gsm8k",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            sample_size=1,
            metrics=[],
            observation_completion="inconclusive",
        )
        self.assertEqual(
            record["criterion_observations"][0]["completion"], "inconclusive"
        )

    def test_strict_pass_cannot_span_multiple_server_boots(self) -> None:
        first = fixture.build_run_for_criterion(
            "strict-same-boot-captures",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            server_boot_id=fixture.digest("boot-one"),
            launch_id=fixture.digest("launch-one"),
            attempt_id="attempt-strict-first",
        )
        second = fixture.build_run_for_criterion(
            "strict-same-boot-captures",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            server_boot_id=fixture.digest("boot-two"),
            launch_id=fixture.digest("launch-two"),
            attempt_id="attempt-strict-second",
        )
        records = self._replace_run("strict-same-boot-captures", first)
        records.append(second)
        records.sort(key=lambda item: item["run_record_id"])
        bundle = fixture.build_bundle(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=records,
        )
        selections = fixture.passing_selections(records)
        selections["strict-same-boot-captures"] = sorted(
            [first["run_record_id"], second["run_record_id"]]
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "spans more than one live server boot",
        ):
            fixture.build_decision(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=records,
                bundle=bundle,
                selections=selections,
            )

    def test_release_and_contract_cross_link_drift_fails_closed(self) -> None:
        changed_release = release_fixture.build_release(
            runtime=release_fixture.build_runtime(
                image_reference="registry.invalid/changed@sha256:" + ("1" * 64)
            )
        )
        changed_contract = release_fixture.build_contract(release=changed_release)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "release cross-link mismatch",
        ):
            evidence.validate_validation_run_record(
                self.runs[0],
                release=changed_release,
                contract=changed_contract,
                evidence_artifacts=self.artifacts,
            )

    def test_evidence_locations_and_commands_reject_private_values(self) -> None:
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "repository-relative path",
        ):
            evidence.build_evidence_artifact(
                location_kind="repository-relative",
                location_value="/private/workspace/result.json",
                content_sha256="1" * 64,
                media_type="application/json",
                qualification_scope="model-qualification",
                visibility="publishable",
                privacy_review="passed",
            )
        tampered = copy.deepcopy(self.runs[0])
        tampered["commands"][0]["arguments"] = ["--api-key", "private-value"]
        tampered["run_record_id"] = evidence.validation_run_record_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "must not contain credentials",
        ):
            evidence.validate_validation_run_record(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_observed_image_or_geometry_drift_fails_closed(self) -> None:
        for field, value in (
            ("image_digest", "sha256:" + ("1" * 64)),
            ("supported_hardware_geometry_id", "2" * 64),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(self.runs[0])
                tampered["observed_environment"][field] = value
                tampered["run_record_id"] = evidence.validation_run_record_id(
                    tampered
                )
                with self.assertRaises(evidence.ModelValidationEvidenceError):
                    evidence.validate_validation_run_record(
                        tampered,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

    def test_bundle_requires_exact_run_and_artifact_sets(self) -> None:
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "run record set mismatch",
        ):
            evidence.validate_validation_evidence_bundle(
                self.bundle,
                release=self.release,
                contract=self.contract,
                run_records=self.runs[:-1],
            )
        extra = fixture.build_artifact("unused", "model-qualification")
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "unreferenced evidence",
        ):
            evidence.build_validation_evidence_bundle(
                release=self.release,
                contract=self.contract,
                run_records=self.runs,
                evidence_artifacts=self.artifacts + [extra],
                review_evidence_artifact_ids=self.bundle[
                    "review_evidence_artifact_ids"
                ],
            )

    def test_bundle_rejects_reused_attempt_identity(self) -> None:
        duplicate_attempt = copy.deepcopy(self.runs[1])
        duplicate_attempt["attempt"]["attempt_id"] = self.runs[0]["attempt"][
            "attempt_id"
        ]
        duplicate_attempt["run_record_id"] = evidence.validation_run_record_id(
            duplicate_attempt
        )
        evidence.validate_validation_run_record(
            duplicate_attempt,
            release=self.release,
            contract=self.contract,
            evidence_artifacts=self.artifacts,
        )
        records = [duplicate_attempt, *self.runs[2:], self.runs[0]]
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "attempt IDs must be unique",
        ):
            fixture.build_bundle(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=records,
            )

    def test_artifact_privacy_state_constrains_review_and_status(self) -> None:
        artifacts = fixture.build_artifacts(review_privacy="pending")
        runs = fixture.build_passing_runs(
            release=self.release, contract=self.contract, artifacts=artifacts
        )
        bundle = fixture.build_bundle(
            release=self.release,
            contract=self.contract,
            artifacts=artifacts,
            run_records=runs,
        )
        review = fixture.build_review(
            artifacts, component_overrides={"evidence_privacy": "pending"}
        )
        decision = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=artifacts,
            run_records=runs,
            bundle=bundle,
            provenance_review=review,
            status="testing-incomplete",
        )
        self.assertEqual(decision["status"], "testing-incomplete")
        lying_review = fixture.build_review(artifacts)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "evidence_privacy disagrees",
        ):
            fixture.build_decision(
                release=self.release,
                contract=self.contract,
                artifacts=artifacts,
                run_records=runs,
                bundle=bundle,
                provenance_review=lying_review,
                status="validated",
            )

    def test_new_decision_supersedes_without_mutating_prior_outcome(self) -> None:
        original = copy.deepcopy(self.decision)
        later = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
            supersedes=[self.decision],
            reviewed_at="2026-08-14T14:00:00Z",
        )
        self.assertEqual(self.decision, original)
        self.assertEqual(
            evidence.effective_validation_status(
                self.decision, later_decisions=[later]
            ),
            "superseded",
        )
        self.assertEqual(
            evidence.effective_validation_status(later, later_decisions=[]),
            "validated",
        )
        self.assertEqual(
            evidence.validation_status_label("superseded"), "Superseded"
        )

    def test_supersession_cross_link_requires_the_prior_decision(self) -> None:
        later = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
            supersedes=[self.decision],
            reviewed_at="2026-08-14T14:00:00Z",
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "supersession cross-link mismatch",
        ):
            evidence.validate_validation_decision(
                later,
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
            )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "review must be later",
        ):
            fixture.build_decision(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=self.runs,
                bundle=self.bundle,
                supersedes=[self.decision],
            )

    def test_superseded_is_projected_not_stored_as_a_base_outcome(self) -> None:
        tampered = copy.deepcopy(self.decision)
        tampered["status"] = "superseded"
        tampered["decision_id"] = evidence.validation_decision_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "unsupported base status",
        ):
            evidence.validate_validation_decision(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
            )

    def test_invalid_time_or_reviewer_authority_fails_closed(self) -> None:
        tampered_run = copy.deepcopy(self.runs[0])
        tampered_run["attempt"]["ended_at"] = "2026-08-14T11:59:00Z"
        tampered_run["run_record_id"] = evidence.validation_run_record_id(
            tampered_run
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError, "precedes"
        ):
            evidence.validate_validation_run_record(
                tampered_run,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )
        tampered_decision = copy.deepcopy(self.decision)
        tampered_decision["review"]["authority"] = "local-self-review"
        tampered_decision["decision_id"] = evidence.validation_decision_id(
            tampered_decision
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "repository-maintainer-review",
        ):
            evidence.validate_validation_decision(
                tampered_decision,
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
            )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "review predates its evidence",
        ):
            evidence.build_validation_decision(
                release=self.release,
                contract=self.contract,
                evidence_bundle=self.bundle,
                run_records=self.runs,
                criterion_run_record_ids=fixture.passing_selections(self.runs),
                provenance_security_review=fixture.build_review(self.artifacts),
                status="validated",
                reviewer="fixture-maintainer",
                reviewed_at="2026-08-14T11:00:00Z",
                review_reference="repository-review:too-early",
            )

    def test_legacy_schema_one_artifacts_remain_unchanged(self) -> None:
        for path in sorted((REPO_ROOT / "models" / "validation-bundles").glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["kind"], model_identity.VALIDATION_BUNDLE_KIND)
            model_identity.validate_validation_bundle(document)
        for path in sorted((REPO_ROOT / "models" / "seals").glob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["kind"], model_identity.EXPECTED_MODEL_SEAL_KIND)
            model_identity.validate_expected_model_seal(document)


if __name__ == "__main__":
    unittest.main(verbosity=2)
