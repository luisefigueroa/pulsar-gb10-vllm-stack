#!/usr/bin/env python3
"""Adversarial regressions for the repaired ADR-0004 Stage 2 schemas."""

from __future__ import annotations

import copy
import pathlib
import sys
import unittest
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_serving_release,
    model_validation_evidence as evidence,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402
from scripts.testlib import model_validation_evidence_fixture as fixture  # noqa: E402


class ModelValidationEvidenceAdversarialTests(unittest.TestCase):
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

    def _record_for(
        self,
        criterion_id: str,
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        for record in records or self.runs:
            if any(
                item["criterion_id"] == criterion_id
                for item in record["criterion_observations"]
            ):
                return record
        raise AssertionError(f"missing fixture record: {criterion_id}")

    def _replace_criterion_records(
        self,
        criterion_id: str,
        replacements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        records = [
            record
            for record in self.runs
            if not any(
                item["criterion_id"] == criterion_id
                for item in record["criterion_observations"]
            )
        ]
        return sorted([*records, *replacements], key=lambda item: item["run_record_id"])

    def _validate_rehashed_run(self, record: dict[str, Any]) -> None:
        record["run_record_id"] = evidence.validation_run_record_id(record)
        evidence.validate_validation_run_record(
            record,
            release=self.release,
            contract=self.contract,
            evidence_artifacts=self.artifacts,
        )

    def _decision_for_records(
        self,
        records: list[dict[str, Any]],
        *,
        status: str,
        exclusions: list[dict[str, Any]] | None = None,
        release: dict[str, Any] | None = None,
        contract: dict[str, Any] | None = None,
        predecessor_registry: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        release = release or self.release
        contract = contract or self.contract
        bundle = fixture.build_bundle(
            release=release,
            contract=contract,
            artifacts=self.artifacts,
            run_records=records,
        )
        return fixture.build_decision(
            release=release,
            contract=contract,
            artifacts=self.artifacts,
            run_records=records,
            bundle=bundle,
            exclusions=exclusions,
            predecessor_registry=predecessor_registry,
            status=status,
        )

    def _build_predecessor_source(
        self,
        *,
        failing_criterion_id: str | None = None,
        criteria: list[dict[str, Any]] | None = None,
        release: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        predecessor_release = release or fixture.build_predecessor_release()
        predecessor_contract = release_fixture.build_contract(
            release=predecessor_release,
            release_criteria=criteria,
        )
        artifacts = fixture.build_artifacts()
        runs = fixture.build_passing_runs(
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=artifacts,
        )
        status = "validated"
        if failing_criterion_id is not None:
            metric = {
                "accuracy-gsm8k": {
                    "metric": "accuracy",
                    "value": "0",
                    "unit": "ratio",
                },
                "throughput-serving": {
                    "metric": "output_tokens_per_second",
                    "value": "1",
                    "unit": "tokens-per-second",
                },
            }[failing_criterion_id]
            failed = fixture.build_run_for_criterion(
                failing_criterion_id,
                release=predecessor_release,
                contract=predecessor_contract,
                artifacts=artifacts,
                metrics=[metric],
                attempt_id=f"attempt-predecessor-failed-{failing_criterion_id}",
            )
            runs = [
                record
                for record in runs
                if not any(
                    item["criterion_id"] == failing_criterion_id
                    for item in record["criterion_observations"]
                )
            ]
            runs = sorted([*runs, failed], key=lambda item: item["run_record_id"])
            status = "tested-criteria-not-met"
        bundle = fixture.build_bundle(
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=artifacts,
            run_records=runs,
        )
        decision = fixture.build_decision(
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=artifacts,
            run_records=runs,
            bundle=bundle,
            status=status,
        )
        return fixture.evidence_source(
            release=predecessor_release,
            contract=predecessor_contract,
            bundle=bundle,
            run_records=runs,
            decision=decision,
        )

    def test_soak_duration_cannot_exceed_its_timestamp_interval(self) -> None:
        tampered = copy.deepcopy(self._record_for("stability-soak"))
        soak = tampered["criterion_observations"][0]["contract_requirements"]["soak"]
        soak["ended_at"] = "2026-08-14T12:05:00Z"
        tampered["run_record_id"] = evidence.validation_run_record_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "differs from its verified timestamp interval",
        ):
            evidence.validate_validation_run_record(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_soak_interval_must_be_contained_by_attempt(self) -> None:
        tampered = copy.deepcopy(self._record_for("stability-soak"))
        soak = tampered["criterion_observations"][0]["contract_requirements"]["soak"]
        soak["ended_at"] = "2026-08-14T14:31:00Z"
        soak["duration_seconds"] = "9060"
        tampered["run_record_id"] = evidence.validation_run_record_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "contained within the attempt",
        ):
            evidence.validate_validation_run_record(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_self_reported_geometry_id_cannot_mask_structural_drift(self) -> None:
        cases: list[tuple[str, Any]] = [
            (
                "cluster TP",
                lambda record: record["observed_environment"]["cluster"].__setitem__(
                    "tensor_parallel_size", 1
                ),
            ),
            (
                "cluster topology",
                lambda record: record["observed_environment"]["cluster"].__setitem__(
                    "topology_class", "ethernet-tree"
                ),
            ),
            (
                "cluster rails",
                lambda record: record["observed_environment"]["cluster"].__setitem__(
                    "rails_per_pair", 1
                ),
            ),
            (
                "rank architecture",
                lambda record: record["observed_environment"]["ranks"][0].__setitem__(
                    "architecture", "x86-64"
                ),
            ),
            (
                "driver family",
                lambda record: record["observed_environment"]["ranks"][0][
                    "driver_abi"
                ].__setitem__("family", "unrelated-driver"),
            ),
            (
                "driver range",
                lambda record: record["observed_environment"]["ranks"][0][
                    "driver_abi"
                ].__setitem__("version", "590.0"),
            ),
            (
                "runtime family",
                lambda record: record["observed_environment"]["ranks"][0][
                    "container_runtime"
                ].__setitem__("family", "containerd"),
            ),
            (
                "runtime range",
                lambda record: record["observed_environment"]["ranks"][0][
                    "container_runtime"
                ].__setitem__("version", "29.0"),
            ),
            (
                "runtime capability",
                lambda record: record["observed_environment"]["ranks"][0][
                    "container_runtime"
                ].__setitem__("capabilities", ["ipc-host"]),
            ),
            (
                "kernel range",
                lambda record: record["observed_environment"]["ranks"][0][
                    "kernel"
                ].__setitem__("version", "6.12.0"),
            ),
            (
                "kernel feature",
                lambda record: record["observed_environment"]["ranks"][0][
                    "kernel"
                ].__setitem__("features", ["nfs-v4.2"]),
            ),
            (
                "engine consistency",
                lambda record: record["observed_environment"]["ranks"][1].__setitem__(
                    "engine_version", "0.27.0"
                ),
            ),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                tampered = copy.deepcopy(self.runs[0])
                original_geometry_id = tampered["observed_environment"][
                    "supported_hardware_geometry_id"
                ]
                mutate(tampered)
                self.assertEqual(
                    tampered["observed_environment"][
                        "supported_hardware_geometry_id"
                    ],
                    original_geometry_id,
                )
                tampered["run_record_id"] = evidence.validation_run_record_id(
                    tampered
                )
                with self.assertRaises(
                    (
                        evidence.ModelValidationEvidenceError,
                        model_serving_release.ModelServingReleaseError,
                    )
                ):
                    evidence.validate_validation_run_record(
                        tampered,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

    def test_command_program_and_version_reject_token_leaks(self) -> None:
        cases = (
            ("program", "validate/hf_private_token.py", "allowed repository-owned"),
            ("version", "hf_private_token", "SHA-256 digest"),
        )
        for field, leaked_value, message in cases:
            with self.subTest(field=field):
                record = copy.deepcopy(self.runs[0])
                record["commands"][0][field] = leaked_value
                record["run_record_id"] = evidence.validation_run_record_id(record)
                with self.assertRaisesRegex(
                    evidence.ModelValidationEvidenceError,
                    message,
                ):
                    evidence.validate_validation_run_record(
                        record,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

    def test_arbitrary_hostname_literal_is_not_a_command_argument(self) -> None:
        record = copy.deepcopy(self.runs[0])
        record["commands"][0]["arguments"].append(
            {"kind": "literal", "value": "build-canary-west"}
        )
        record["run_record_id"] = evidence.validation_run_record_id(record)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "kind is unsupported",
        ):
            evidence.validate_validation_run_record(
                record,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_site_options_require_typed_reference_pairing(self) -> None:
        protected = {
            "kind": "protected-site-reference",
            "digest": "sha256:" + fixture.digest("protected-site"),
        }
        rank = {"kind": "rank-reference", "rank": 1}
        cases: tuple[tuple[str, dict[str, Any]], ...] = (
            (
                "raw host",
                {"kind": "site-option", "option": "--host", "reference": "node-a"},
            ),
            (
                "rank with protected value",
                {
                    "kind": "site-option",
                    "option": "--rank",
                    "reference": protected,
                },
            ),
            (
                "url with rank value",
                {"kind": "site-option", "option": "--url", "reference": rank},
            ),
            (
                "untyped node option",
                {"kind": "site-option", "option": "--node", "reference": rank},
            ),
        )
        for label, argument in cases:
            with self.subTest(label=label):
                record = copy.deepcopy(self.runs[0])
                record["commands"][0]["arguments"].append(argument)
                record["run_record_id"] = evidence.validation_run_record_id(record)
                with self.assertRaises(evidence.ModelValidationEvidenceError):
                    evidence.validate_validation_run_record(
                        record,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

    def test_site_options_accept_typed_protected_and_rank_references(self) -> None:
        record = copy.deepcopy(self.runs[0])
        record["commands"][0]["arguments"].extend(
            [
                {
                    "kind": "site-option",
                    "option": "--host",
                    "reference": {
                        "kind": "protected-site-reference",
                        "digest": "sha256:" + fixture.digest("protected-host"),
                    },
                },
                {
                    "kind": "site-option",
                    "option": "--rank",
                    "reference": {"kind": "rank-reference", "rank": 1},
                },
                {
                    "kind": "site-option",
                    "option": "--url",
                    "reference": {
                        "kind": "protected-site-reference",
                        "digest": "sha256:" + fixture.digest("protected-url"),
                    },
                },
            ]
        )
        self._validate_rehashed_run(record)

    def test_secret_environment_names_cannot_be_mislabeled(self) -> None:
        record = copy.deepcopy(self.runs[0])
        record["commands"][0]["environment"] = [
            {"kind": "non-secret-reference", "name": "VLLM_API_KEY"}
        ]
        record["run_record_id"] = evidence.validation_run_record_id(record)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "secret classification",
        ):
            evidence.validate_validation_run_record(
                record,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_environment_names_reject_embedded_credential_values(self) -> None:
        realistic_hugging_face_token = "hf_" + ("A1b2C3d4" * 4) + "Z9"
        cases = (
            ("fake-shaped", "hf_FAKE00000000", "non-secret-reference"),
            (
                "realistic-shaped",
                realistic_hugging_face_token,
                "secret-reference",
            ),
            (
                "delimiter-prepended",
                "CAPTURE_" + realistic_hugging_face_token,
                "secret-reference",
            ),
            (
                "alphanumeric-prepended",
                "CAPTURE" + realistic_hugging_face_token,
                "secret-reference",
            ),
        )
        for label, name, kind in cases:
            with self.subTest(label=label):
                record = copy.deepcopy(self.runs[0])
                record["commands"][0]["environment"] = [
                    {"kind": kind, "name": name}
                ]
                record["run_record_id"] = evidence.validation_run_record_id(record)
                with self.assertRaisesRegex(
                    evidence.ModelValidationEvidenceError,
                    "name contains a credential value",
                ):
                    evidence.validate_validation_run_record(
                        record,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

    def test_legitimate_environment_references_remain_valid(self) -> None:
        cases = (
            ("VLLM_API_KEY", "secret-reference"),
            ("HF_TOKEN", "secret-reference"),
            ("PYTHONHASHSEED", "non-secret-reference"),
        )
        for name, kind in cases:
            with self.subTest(name=name):
                record = copy.deepcopy(self.runs[0])
                record["commands"][0]["environment"] = [
                    {"kind": kind, "name": name}
                ]
                self._validate_rehashed_run(record)

    def test_attempted_criterion_declaration_validates_identity_scope_and_order(
        self,
    ) -> None:
        cases = (
            ("unknown", ["unknown-criterion"], "unknown criterion"),
            (
                "wrong scope",
                ["physical-geometry-dgx"],
                "scope differs from attempt scope",
            ),
            (
                "duplicate",
                ["accuracy-gsm8k", "accuracy-gsm8k"],
                "sorted and unique",
            ),
            (
                "unsorted",
                ["throughput-serving", "accuracy-gsm8k"],
                "sorted and unique",
            ),
        )
        baseline = self._record_for("accuracy-gsm8k")
        for label, attempted_ids, message in cases:
            with self.subTest(label=label):
                record = copy.deepcopy(baseline)
                record["attempt"]["attempted_criterion_ids"] = attempted_ids
                record["run_record_id"] = evidence.validation_run_record_id(record)
                with self.assertRaisesRegex(
                    evidence.ModelValidationEvidenceError,
                    message,
                ):
                    evidence.validate_validation_run_record(
                        record,
                        release=self.release,
                        contract=self.contract,
                        evidence_artifacts=self.artifacts,
                    )

        prequalification, preparation_artifacts = (
            fixture.build_prequalification_failure(
                release=self.release,
                contract=self.contract,
            )
        )
        prequalification["attempt"]["attempted_criterion_ids"] = [
            "accuracy-gsm8k"
        ]
        prequalification["run_record_id"] = evidence.validation_run_record_id(
            prequalification
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "pre-qualification preparation must declare no attempted criteria",
        ):
            evidence.validate_validation_run_record(
                prequalification,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=preparation_artifacts,
            )

    def test_failed_post_barrier_attempt_cannot_hide_an_empty_observation_set(
        self,
    ) -> None:
        failed = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            attempt_completion="failed",
            attempt_id="attempt-throughput-failed-after-barrier",
        )
        self.assertEqual(
            failed["criterion_observations"][0]["completion"],
            "inconclusive",
        )
        records_with_failed_attempt = sorted(
            [*self.runs, failed], key=lambda item: item["run_record_id"]
        )
        decision = self._decision_for_records(
            records_with_failed_attempt,
            status="tested-inconclusive",
        )
        throughput = next(
            result
            for result in decision["criterion_results"]
            if result["criterion_id"] == "throughput-serving"
        )
        self.assertEqual(throughput["disposition"], "inconclusive")
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "status disagrees with evidence",
        ):
            self._decision_for_records(
                records_with_failed_attempt,
                status="validated",
            )

        missing_observation = copy.deepcopy(failed)
        missing_observation["criterion_observations"] = []
        missing_observation["run_record_id"] = evidence.validation_run_record_id(
            missing_observation
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "must exactly cover attempted_criterion_ids",
        ):
            fixture.build_bundle(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=[*self.runs, missing_observation],
            )

        hidden_attempt = copy.deepcopy(missing_observation)
        hidden_attempt["attempt"]["attempted_criterion_ids"] = []
        hidden_attempt["run_record_id"] = evidence.validation_run_record_id(
            hidden_attempt
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "post-barrier qualification attempt must declare an attempted criterion",
        ):
            fixture.build_bundle(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=[*self.runs, hidden_attempt],
            )

        conclusive_observation = copy.deepcopy(failed)
        conclusive_observation["criterion_observations"][0]["completion"] = (
            "complete"
        )
        conclusive_observation["criterion_observations"][0]["reason"] = "completed"
        conclusive_observation["run_record_id"] = (
            evidence.validation_run_record_id(conclusive_observation)
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "incomplete validation attempt requires inconclusive observations",
        ):
            evidence.validate_validation_run_record(
                conclusive_observation,
                release=self.release,
                contract=self.contract,
                evidence_artifacts=self.artifacts,
            )

    def test_every_observation_is_automatically_adjudicated(self) -> None:
        passing = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            attempt_id="attempt-throughput-pass-extra",
        )
        failing = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            metrics=[
                {
                    "metric": "output_tokens_per_second",
                    "value": "1",
                    "unit": "tokens-per-second",
                }
            ],
            attempt_id="attempt-throughput-fail-extra",
        )
        inconclusive = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            metrics=[],
            observation_completion="inconclusive",
            attempt_completion="inconclusive",
            attempt_id="attempt-throughput-inconclusive-extra",
        )
        cases = (
            ([passing, failing], "tested-inconclusive", "inconclusive"),
            ([passing, inconclusive], "tested-inconclusive", "inconclusive"),
            ([failing, inconclusive], "tested-criteria-not-met", "fail"),
            (
                [passing, failing, inconclusive],
                "tested-inconclusive",
                "inconclusive",
            ),
            ([passing], "validated", "pass"),
        )
        for replacements, status, expected_disposition in cases:
            with self.subTest(status=status, count=len(replacements)):
                records = self._replace_criterion_records(
                    "throughput-serving", replacements
                )
                decision = self._decision_for_records(records, status=status)
                result = next(
                    item
                    for item in decision["criterion_results"]
                    if item["criterion_id"] == "throughput-serving"
                )
                self.assertEqual(result["disposition"], expected_disposition)
                self.assertEqual(
                    result["included_run_record_ids"],
                    sorted(item["run_record_id"] for item in replacements),
                )
                self.assertEqual(result["excluded_run_records"], [])

    def test_exclusions_require_review_evidence_and_are_hashed(self) -> None:
        passing = self._record_for("throughput-serving")
        failing = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            metrics=[
                {
                    "metric": "output_tokens_per_second",
                    "value": "1",
                    "unit": "tokens-per-second",
                }
            ],
            attempt_id="attempt-throughput-reviewed-exclusion",
        )
        records = self._replace_criterion_records(
            "throughput-serving", [passing, failing]
        )
        exclusion = fixture.build_exclusion(
            "throughput-serving", failing, self.artifacts
        )
        decision = self._decision_for_records(
            records, status="validated", exclusions=[exclusion]
        )
        result = next(
            item
            for item in decision["criterion_results"]
            if item["criterion_id"] == "throughput-serving"
        )
        self.assertEqual(result["excluded_run_records"], [exclusion])
        without_exclusion = copy.deepcopy(decision)
        without_exclusion["criterion_results"] = copy.deepcopy(
            self.decision["criterion_results"]
        )
        self.assertNotEqual(
            decision["decision_id"], evidence.validation_decision_id(without_exclusion)
        )

        for label, mutate in (
            ("empty reason", lambda item: item.__setitem__("reason", "")),
            (
                "non-review evidence",
                lambda item: item.__setitem__(
                    "review_evidence_artifact_ids",
                    [
                        fixture.artifact_for_label(
                            self.artifacts, "throughput-serving"
                        )["artifact_id"]
                    ],
                ),
            ),
        ):
            with self.subTest(label=label):
                invalid = copy.deepcopy(exclusion)
                mutate(invalid)
                with self.assertRaises(evidence.ModelValidationEvidenceError):
                    self._decision_for_records(
                        records, status="validated", exclusions=[invalid]
                    )

    def test_rehashed_decision_cannot_silently_drop_an_observation(self) -> None:
        passing = self._record_for("throughput-serving")
        second = fixture.build_run_for_criterion(
            "throughput-serving",
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            attempt_id="attempt-throughput-second-pass",
        )
        records = self._replace_criterion_records(
            "throughput-serving", [passing, second]
        )
        bundle = fixture.build_bundle(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=records,
        )
        decision = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=records,
            bundle=bundle,
        )
        tampered = copy.deepcopy(decision)
        result = next(
            item
            for item in tampered["criterion_results"]
            if item["criterion_id"] == "throughput-serving"
        )
        result["included_run_record_ids"] = result["included_run_record_ids"][:1]
        tampered["decision_id"] = evidence.validation_decision_id(tampered)
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "criterion results disagree with evidence",
        ):
            evidence.validate_validation_decision(
                tampered,
                release=self.release,
                contract=self.contract,
                evidence_bundle=bundle,
                run_records=records,
                predecessor_evidence_registry=[],
            )

    def test_predecessor_overall_status_need_not_be_validated(self) -> None:
        predecessor = self._build_predecessor_source(
            failing_criterion_id="accuracy-gsm8k"
        )
        self.assertEqual(
            predecessor["decision"]["status"], "tested-criteria-not-met"
        )
        contract = fixture.build_relative_contract(
            release=self.release, predecessor_source=predecessor
        )
        runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        decision = self._decision_for_records(
            runs,
            status="validated",
            contract=contract,
            predecessor_registry=[predecessor],
        )
        self.assertEqual(decision["status"], "validated")

    def test_predecessor_relevant_criterion_must_be_a_reviewed_pass(self) -> None:
        predecessor = self._build_predecessor_source(
            failing_criterion_id="throughput-serving"
        )
        contract = fixture.build_relative_contract(
            release=self.release, predecessor_source=predecessor
        )
        runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "criterion is not a reviewed pass",
        ):
            self._decision_for_records(
                runs,
                status="validated",
                contract=contract,
                predecessor_registry=[predecessor],
            )

    def test_predecessor_protocol_and_geometry_must_match(self) -> None:
        changed_criteria = release_fixture.criteria()
        throughput = next(
            item for item in changed_criteria if item["dimension"] == "throughput"
        )
        throughput["protocol"]["version"] = "2"
        protocol_source = self._build_predecessor_source(criteria=changed_criteria)

        changed_geometry = copy.deepcopy(release_fixture.build_geometry())
        changed_geometry["topology_class"] = "roce-ring"
        geometry_release = release_fixture.build_release(geometry=changed_geometry)
        geometry_source = self._build_predecessor_source(release=geometry_release)

        for label, source, expected in (
            ("protocol", protocol_source, "protocol differs"),
            ("geometry", geometry_source, "geometry differs"),
        ):
            with self.subTest(label=label):
                contract = fixture.build_relative_contract(
                    release=self.release, predecessor_source=source
                )
                runs = fixture.build_passing_runs(
                    release=self.release,
                    contract=contract,
                    artifacts=self.artifacts,
                )
                with self.assertRaisesRegex(
                    evidence.ModelValidationEvidenceError, expected
                ):
                    self._decision_for_records(
                        runs,
                        status="validated",
                        contract=contract,
                        predecessor_registry=[source],
                    )

    def test_predecessor_run_must_be_included_in_reviewed_result(self) -> None:
        predecessor = fixture.build_predecessor_source()
        predecessor_release = predecessor["release"]
        predecessor_contract = predecessor["contract"]
        predecessor_artifacts = predecessor["evidence_bundle"]["evidence_artifacts"]
        excluded_run = fixture.build_run_for_criterion(
            "throughput-serving",
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=predecessor_artifacts,
            metrics=[
                {
                    "metric": "output_tokens_per_second",
                    "value": "1",
                    "unit": "tokens-per-second",
                }
            ],
            attempt_id="attempt-predecessor-excluded-throughput",
        )
        predecessor_runs = sorted(
            [*predecessor["run_records"], excluded_run],
            key=lambda item: item["run_record_id"],
        )
        predecessor_bundle = fixture.build_bundle(
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=predecessor_artifacts,
            run_records=predecessor_runs,
        )
        exclusion = fixture.build_exclusion(
            "throughput-serving", excluded_run, predecessor_artifacts
        )
        predecessor_decision = fixture.build_decision(
            release=predecessor_release,
            contract=predecessor_contract,
            artifacts=predecessor_artifacts,
            run_records=predecessor_runs,
            bundle=predecessor_bundle,
            exclusions=[exclusion],
        )
        predecessor = fixture.evidence_source(
            release=predecessor_release,
            contract=predecessor_contract,
            bundle=predecessor_bundle,
            run_records=predecessor_runs,
            decision=predecessor_decision,
        )
        contract = fixture.build_relative_contract(
            release=self.release, predecessor_source=predecessor
        )
        contract = copy.deepcopy(contract)
        contract["release_criteria"]["relative_performance"]["throughput"][
            "predecessor_run_record_id"
        ] = excluded_run["run_record_id"]
        contract["contract_id"] = model_serving_release.validation_contract_id(
            contract
        )
        runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "not included in the passing result",
        ):
            self._decision_for_records(
                runs,
                status="validated",
                contract=contract,
                predecessor_registry=[predecessor],
            )

    def test_predecessor_identity_cross_links_cannot_be_partially_forged(self) -> None:
        predecessor = fixture.build_predecessor_source()
        contract = fixture.build_relative_contract(
            release=self.release, predecessor_source=predecessor
        )
        contract = copy.deepcopy(contract)
        contract["release_criteria"]["relative_performance"][
            "predecessor_bundle_id"
        ] = "0" * 64
        contract["contract_id"] = model_serving_release.validation_contract_id(
            contract
        )
        runs = fixture.build_passing_runs(
            release=self.release,
            contract=contract,
            artifacts=self.artifacts,
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "conflicting identity cross-links",
        ):
            self._decision_for_records(
                runs,
                status="validated",
                contract=contract,
                predecessor_registry=[predecessor],
            )

    def test_supersession_projection_rejects_shape_only_superseder(self) -> None:
        later = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
            supersedes=[self.decision],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        fake_later = copy.deepcopy(later)
        fake_later["criterion_results"][0]["disposition"] = "fail"
        fake_later["criterion_results"][0]["reason"] = "forged-review-result"
        fake_later["decision_id"] = evidence.validation_decision_id(fake_later)
        sources = sorted(
            [
                fixture.evidence_source(
                    release=self.release,
                    contract=self.contract,
                    bundle=self.bundle,
                    run_records=self.runs,
                    decision=item,
                )
                for item in (self.decision, fake_later)
            ],
            key=lambda item: item["decision"]["decision_id"],
        )
        with self.assertRaises(evidence.ModelValidationEvidenceError):
            evidence.effective_validation_status(
                self.decision,
                decision_evidence_registry=sources,
                predecessor_evidence_registry=[],
            )

    def test_supersession_rejects_backdating_and_different_subjects(self) -> None:
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "strictly later",
        ):
            fixture.build_decision(
                release=self.release,
                contract=self.contract,
                artifacts=self.artifacts,
                run_records=self.runs,
                bundle=self.bundle,
                supersedes=[self.decision],
                reviewed_at="2026-08-14T14:45:00Z",
            )

        other_release = release_fixture.build_release(
            runtime=release_fixture.build_runtime(
                image_reference="registry.invalid/other@sha256:" + ("1" * 64)
            )
        )
        other_contract = fixture.build_contract(release=other_release)
        other_artifacts = fixture.build_artifacts()
        other_runs = fixture.build_passing_runs(
            release=other_release,
            contract=other_contract,
            artifacts=other_artifacts,
        )
        other_bundle = fixture.build_bundle(
            release=other_release,
            contract=other_contract,
            artifacts=other_artifacts,
            run_records=other_runs,
        )
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError,
            "retain release and contract",
        ):
            fixture.build_decision(
                release=other_release,
                contract=other_contract,
                artifacts=other_artifacts,
                run_records=other_runs,
                bundle=other_bundle,
                supersedes=[self.decision],
                reviewed_at="2026-08-14T16:00:00Z",
            )

    def test_supersession_lineage_is_acyclic_and_transitively_validated(self) -> None:
        second = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
            supersedes=[self.decision],
            reviewed_at="2026-08-14T16:00:00Z",
        )
        third = fixture.build_decision(
            release=self.release,
            contract=self.contract,
            artifacts=self.artifacts,
            run_records=self.runs,
            bundle=self.bundle,
            supersedes=[second],
            supersession_lineage=[self.decision],
            reviewed_at="2026-08-14T17:00:00Z",
        )
        sources = sorted(
            [
                fixture.evidence_source(
                    release=self.release,
                    contract=self.contract,
                    bundle=self.bundle,
                    run_records=self.runs,
                    decision=item,
                )
                for item in (self.decision, second, third)
            ],
            key=lambda item: item["decision"]["decision_id"],
        )
        self.assertEqual(
            evidence.effective_validation_status(
                second,
                decision_evidence_registry=sources,
                predecessor_evidence_registry=[],
            ),
            "superseded",
        )
        cycle_a = {
            "decision_id": "cycle-a",
            "release_id": "release",
            "contract_id": "contract",
            "review": {"reviewed_at": "2026-08-14T16:00:00Z"},
            "supersedes_decision_ids": ["cycle-b"],
        }
        cycle_b = {
            "decision_id": "cycle-b",
            "release_id": "release",
            "contract_id": "contract",
            "review": {"reviewed_at": "2026-08-14T15:00:00Z"},
            "supersedes_decision_ids": ["cycle-a"],
        }
        with self.assertRaisesRegex(
            evidence.ModelValidationEvidenceError, "contains a cycle"
        ):
            evidence._supersession_lineage_closure(  # noqa: SLF001
                cycle_a, {"cycle-a": cycle_a, "cycle-b": cycle_b}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
