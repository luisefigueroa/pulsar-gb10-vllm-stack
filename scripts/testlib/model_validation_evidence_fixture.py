#!/usr/bin/env python3
"""Deterministic, privacy-safe fixtures for ADR-0004 evidence schemas."""

from __future__ import annotations

import copy
import hashlib
import pathlib
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_identity,
    model_serving_release,
    model_validation_evidence,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402


EXPECTED_FIRST_RUN_RECORD_ID = (
    "5b117631115cd3e35ea505c917cb6154db5dda59b29a499ac86bf22f0bf3d5cf"
)
EXPECTED_EVIDENCE_BUNDLE_ID = (
    "9929e849a41663ee37c9e295fd9365fa78478a823d35d269eaab3092c8f19010"
)
EXPECTED_VALIDATION_DECISION_ID = (
    "38339191a9eac005531e0180a48d19228a621142026f49d5d39dbbe886124364"
)


PASS_METRICS: dict[str, list[dict[str, str]]] = {
    "accuracy-gsm8k": [
        {"metric": "accuracy", "value": "0.75", "unit": "ratio"}
    ],
    "latency-ttft": [
        {"metric": "ttft_p95", "value": "1000", "unit": "milliseconds"}
    ],
    "physical-geometry-dgx": [
        {"metric": "geometry_verdict", "value": "pass", "unit": "verdict"}
    ],
    "serving-integration-smoke": [
        {
            "metric": "integration_verdict",
            "value": "pass",
            "unit": "verdict",
        }
    ],
    "stability-soak": [
        {"metric": "request_error_count", "value": "0", "unit": "count"}
    ],
    "strict-same-boot-captures": [
        {"metric": "exact_match_rate", "value": "1", "unit": "ratio"}
    ],
    "throughput-serving": [
        {
            "metric": "output_tokens_per_second",
            "value": "25",
            "unit": "tokens-per-second",
        }
    ],
}


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_release() -> dict[str, Any]:
    return release_fixture.build_release()


def build_contract(*, release: dict[str, Any] | None = None) -> dict[str, Any]:
    return release_fixture.build_contract(release=release or build_release())


def build_relative_contract(
    *,
    release: dict[str, Any] | None = None,
    predecessor_release_id: str | None = None,
    throughput_max_regression_percent: str = "5",
    latency_max_regression_percent: str = "10",
) -> dict[str, Any]:
    release = release or build_release()
    release_criteria = release_fixture.criteria()
    by_dimension = {item["dimension"]: item for item in release_criteria}
    relative = model_serving_release.build_relative_performance_requirement(
        release=release,
        predecessor_release_id=(
            predecessor_release_id or digest("predecessor-release")
        ),
        throughput_criterion=by_dimension["throughput"],
        latency_criterion=by_dimension["latency"],
        throughput_max_regression_percent=throughput_max_regression_percent,
        latency_max_regression_percent=latency_max_regression_percent,
    )
    return release_fixture.build_contract(
        release=release,
        release_criteria=release_criteria,
        relative_performance=relative,
    )


def build_artifact(
    label: str,
    qualification_scope: str,
    *,
    privacy_review: str = "passed",
    protected: bool = False,
) -> dict[str, Any]:
    content_digest = digest(f"content:{label}")
    return model_validation_evidence.build_evidence_artifact(
        location_kind=(
            "protected-content-addressed" if protected else "repository-relative"
        ),
        location_value=(
            f"sha256:{content_digest}"
            if protected
            else f"results/validation-fixture/{label}.json"
        ),
        content_sha256=content_digest,
        media_type="application/json",
        qualification_scope=qualification_scope,
        visibility="protected" if protected else "publishable",
        privacy_review=privacy_review,
    )


def build_artifacts(
    *,
    review_privacy: str = "passed",
) -> list[dict[str, Any]]:
    contract = build_contract()
    criteria = {
        item["criterion_id"]: item
        for item in contract["release_criteria"]["criteria"]
    }
    artifacts = [
        build_artifact(criterion_id, criterion["qualification_scope"])
        for criterion_id, criterion in sorted(criteria.items())
        if criterion["dimension"] != "provenance-security"
    ]
    artifacts.append(
        build_artifact(
            "provenance-security-review",
            "release-promotion",
            privacy_review=review_privacy,
            protected=True,
        )
    )
    return sorted(artifacts, key=lambda item: item["artifact_id"])


def artifact_for_label(
    artifacts: list[dict[str, Any]], label: str
) -> dict[str, Any]:
    expected_path = f"results/validation-fixture/{label}.json"
    for artifact in artifacts:
        if artifact["location"].get("value") == expected_path:
            return artifact
    raise AssertionError(f"fixture artifact not found: {label}")


def review_artifact(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    for artifact in artifacts:
        if artifact["visibility"] == "protected":
            return artifact
    raise AssertionError("fixture review artifact not found")


def build_preparation_provenance(
    *,
    release: dict[str, Any] | None = None,
    maturity: str = "experimental",
    barrier: str = "passed",
    verification_status: str = "passed",
) -> dict[str, Any]:
    release = release or build_release()
    runtime_sources = [
        {"rank": 0, "source": "sealed-hot", "retention": "ephemeral"},
        {"rank": 1, "source": "durable-home", "retention": "durable"},
    ]
    if barrier == "not-reached":
        runtime_sources = [runtime_sources[0]]
    return {
        "origin": "managed-home",
        "transfer": "ssh-roce",
        "subsystems": [
            {
                "name": "pulsar-model-library",
                "version": "fixture-v1",
                "maturity": maturity,
            }
        ],
        "runtime_sources": runtime_sources,
        "verification": {
            "status": verification_status,
            "model_artifact_set_id": model_identity.canonical_json_digest(
                release["model_artifact_set"]
            ),
        },
        "qualification_barrier": barrier,
        "elapsed_seconds": "120" if barrier == "passed" else "12",
    }


def build_observed_environment(
    *,
    release: dict[str, Any] | None = None,
    server_boot_id: str | None = None,
    launch_id: str | None = None,
    launched: bool = True,
) -> dict[str, Any]:
    release = release or build_release()
    geometry = release["supported_hardware_geometry"]
    return {
        "image_digest": release["runtime_image_identity"]["image"]["digest"],
        "supported_hardware_geometry_id": (
            model_serving_release.supported_hardware_geometry_id(geometry)
        ),
        "server_boot_id": (
            server_boot_id
            if server_boot_id is not None
            else (digest("server-boot") if launched else None)
        ),
        "launch_id": (
            launch_id
            if launch_id is not None
            else (digest("launch") if launched else None)
        ),
        "ranks": [
            {
                "rank": rank,
                "hardware_class": geometry["hardware_class"],
                "accelerator_count": geometry["accelerators_per_node"],
                "unified_memory_gib": "128",
                "driver_version": "fixture-driver-580",
                "kernel_release": "fixture-kernel-6.11",
                "container_runtime_version": "fixture-runtime-28",
                "engine_version": "fixture-vllm-0.26",
            }
            for rank in range(geometry["node_count"])
        ],
    }


def phase_for_scope(scope: str) -> str:
    return {
        "catalog-artifact": "preparation",
        "serving-integration": "serving-integration",
        "model-qualification": "model-qualification",
        "release-promotion": "release-review",
    }[scope]


def build_run_for_criterion(
    criterion_id: str,
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, str]] | None = None,
    observation_completion: str = "complete",
    observation_reason: str | None = None,
    sample_size: int | None = None,
    attempt_completion: str = "completed",
    server_boot_id: str | None = None,
    launch_id: str | None = None,
    maturity: str = "experimental",
    attempt_id: str | None = None,
    include_context_requirement: bool = True,
    context_completion: str = "complete",
    context_minimum_tokens: int | None = None,
    context_depths: list[str] | None = None,
    include_soak_requirement: bool = True,
    soak_completion: str = "complete",
    soak_duration_seconds: str | None = None,
    soak_concurrency: int | None = None,
    soak_request_errors: int | None = None,
    include_relative_comparison: bool = True,
    relative_completion: str = "complete",
    relative_sample_size: int | None = None,
    relative_predecessor_metrics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    release = release or build_release()
    contract = contract or build_contract(release=release)
    artifacts = artifacts or build_artifacts()
    criterion = next(
        item
        for item in contract["release_criteria"]["criteria"]
        if item["criterion_id"] == criterion_id
    )
    artifact = artifact_for_label(artifacts, criterion_id)
    observed_metrics = copy.deepcopy(
        PASS_METRICS[criterion_id] if metrics is None else metrics
    )
    release_requirements = contract["release_criteria"]
    context_requirement = release_requirements["context_requirement"]
    context_observation = None
    if (
        include_context_requirement
        and context_requirement["status"] == "required"
        and criterion_id in context_requirement["criterion_ids"]
    ):
        context_observation = {
            "completion": context_completion,
            "minimum_tokens": (
                context_requirement["minimum_tokens"]
                if context_minimum_tokens is None
                else context_minimum_tokens
            ),
            "depths": sorted(
                copy.deepcopy(
                    context_requirement["depths"]
                    if context_depths is None
                    else context_depths
                ),
                key=lambda item: float(item),
            ),
            "evidence_artifact_ids": [artifact["artifact_id"]],
            "reason": (
                "completed"
                if context_completion == "complete"
                else "insufficient-context-samples"
            ),
        }
    soak_requirement = release_requirements["soak_requirement"]
    soak_observation = None
    if (
        include_soak_requirement
        and soak_requirement["status"] == "required"
        and criterion_id == soak_requirement["criterion_id"]
    ):
        soak_observation = {
            "completion": soak_completion,
            "duration_seconds": (
                str(soak_requirement["minimum_duration_seconds"])
                if soak_duration_seconds is None
                else soak_duration_seconds
            ),
            "concurrency": (
                soak_requirement["concurrency"]
                if soak_concurrency is None
                else soak_concurrency
            ),
            "request_errors": (
                soak_requirement["maximum_request_errors"]
                if soak_request_errors is None
                else soak_request_errors
            ),
            "evidence_artifact_ids": [artifact["artifact_id"]],
            "reason": (
                "completed"
                if soak_completion == "complete"
                else "soak-interrupted"
            ),
        }
    relative_requirement = release_requirements["relative_performance"]
    relative_observation = None
    if (
        include_relative_comparison
        and relative_requirement["status"] == "required"
        and criterion["dimension"] in {"throughput", "latency"}
        and relative_requirement[criterion["dimension"]]["criterion_id"]
        == criterion_id
    ):
        dimension_requirement = relative_requirement[criterion["dimension"]]
        relative_observation = {
            "predecessor_release_id": relative_requirement[
                "predecessor_release_id"
            ],
            "supported_hardware_geometry_id": relative_requirement[
                "supported_hardware_geometry_id"
            ],
            "benchmark_protocol_id": dimension_requirement[
                "benchmark_protocol_id"
            ],
            "completion": relative_completion,
            "sample_size": (
                criterion["sample_size"]
                if relative_sample_size is None
                else relative_sample_size
            ),
            "metrics": copy.deepcopy(
                PASS_METRICS[criterion_id]
                if relative_predecessor_metrics is None
                else relative_predecessor_metrics
            ),
            "evidence_artifact_ids": [artifact["artifact_id"]],
            "reason": (
                "completed"
                if relative_completion == "complete"
                else "predecessor-samples-inconclusive"
            ),
        }
    observation = {
        "criterion_id": criterion_id,
        "benchmark_protocol_id": model_serving_release.benchmark_protocol_id(
            criterion
        ),
        "completion": observation_completion,
        "sample_size": (
            criterion["sample_size"] if sample_size is None else sample_size
        ),
        "metrics": observed_metrics,
        "evidence_artifact_ids": [artifact["artifact_id"]],
        "contract_requirements": {
            "context": context_observation,
            "soak": soak_observation,
            "relative_performance": relative_observation,
        },
        "reason": (
            observation_reason
            if observation_reason is not None
            else (
                "completed"
                if observation_completion == "complete"
                else "insufficient-stable-samples"
            )
        ),
    }
    return model_validation_evidence.build_validation_run_record(
        release=release,
        contract=contract,
        attempt={
            "attempt_id": attempt_id or f"attempt-{criterion_id}",
            "phase": phase_for_scope(criterion["qualification_scope"]),
            "qualification_scope": criterion["qualification_scope"],
            "started_at": "2026-08-14T12:00:00Z",
            "ended_at": "2026-08-14T12:05:00Z",
            "completion": attempt_completion,
        },
        preparation_provenance=build_preparation_provenance(
            release=release, maturity=maturity
        ),
        observed_environment=build_observed_environment(
            release=release,
            server_boot_id=server_boot_id,
            launch_id=launch_id,
        ),
        commands=[
            {
                "program": "validate/run-gates.sh",
                "version": "fixture-v1",
                "arguments": [criterion_id, "--tag", "fixture"],
                "environment_variable_names": ["VLLM_API_KEY"],
                "working_directory": "repository-root",
            }
        ],
        criterion_observations=[observation],
        evidence_artifacts=artifacts,
        evidence_artifact_ids=[artifact["artifact_id"]],
    )


def build_passing_runs(
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    release = release or build_release()
    contract = contract or build_contract(release=release)
    artifacts = artifacts or build_artifacts()
    return sorted(
        [
            build_run_for_criterion(
                criterion_id,
                release=release,
                contract=contract,
                artifacts=artifacts,
            )
            for criterion_id in PASS_METRICS
        ],
        key=lambda item: item["run_record_id"],
    )


def build_prequalification_failure(
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    release = release or build_release()
    contract = contract or build_contract(release=release)
    artifact = artifact or build_artifact(
        "preparation-failure", "catalog-artifact"
    )
    artifacts = [artifact]
    record = model_validation_evidence.build_validation_run_record(
        release=release,
        contract=contract,
        attempt={
            "attempt_id": "attempt-preparation-failure",
            "phase": "preparation",
            "qualification_scope": "catalog-artifact",
            "started_at": "2026-08-14T11:00:00Z",
            "ended_at": "2026-08-14T11:01:00Z",
            "completion": "failed",
        },
        preparation_provenance=build_preparation_provenance(
            release=release,
            barrier="not-reached",
            verification_status="failed",
        ),
        observed_environment=build_observed_environment(
            release=release, launched=False
        ),
        commands=[
            {
                "program": "scripts/model-library.sh",
                "version": "fixture-v1",
                "arguments": ["prepare", "fixture-profile", "--yes"],
                "environment_variable_names": [],
                "working_directory": "repository-root",
            }
        ],
        criterion_observations=[],
        evidence_artifacts=artifacts,
        evidence_artifact_ids=[artifact["artifact_id"]],
    )
    return record, artifacts


def build_bundle(
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    run_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    release = release or build_release()
    contract = contract or build_contract(release=release)
    artifacts = artifacts or build_artifacts()
    run_records = run_records or build_passing_runs(
        release=release, contract=contract, artifacts=artifacts
    )
    review = review_artifact(artifacts)
    return model_validation_evidence.build_validation_evidence_bundle(
        release=release,
        contract=contract,
        run_records=run_records,
        evidence_artifacts=artifacts,
        review_evidence_artifact_ids=[review["artifact_id"]],
    )


def passing_selections(
    run_records: list[dict[str, Any]],
) -> dict[str, list[str]]:
    selections: dict[str, list[str]] = {}
    for record in run_records:
        for observation in record["criterion_observations"]:
            selections[observation["criterion_id"]] = [record["run_record_id"]]
    selections["provenance-security-review"] = []
    return selections


def build_review(
    artifacts: list[dict[str, Any]],
    *,
    component_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    values = {
        "artifact_identity": "pass",
        "runtime_identity": "pass",
        "contract_frozen_before_testing": "pass",
        "evidence_privacy": "pass",
        "security": "pass",
    }
    values.update(component_overrides or {})
    return model_validation_evidence.build_provenance_security_review(
        **values,
        evidence_artifact_ids=[review_artifact(artifacts)["artifact_id"]],
    )


def build_decision(
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    run_records: list[dict[str, Any]] | None = None,
    bundle: dict[str, Any] | None = None,
    selections: dict[str, list[str]] | None = None,
    provenance_review: dict[str, Any] | None = None,
    status: str = "validated",
    supersedes: list[dict[str, Any]] | None = None,
    reviewed_at: str = "2026-08-14T13:00:00Z",
) -> dict[str, Any]:
    release = release or build_release()
    contract = contract or build_contract(release=release)
    artifacts = artifacts or build_artifacts()
    run_records = run_records or build_passing_runs(
        release=release, contract=contract, artifacts=artifacts
    )
    bundle = bundle or build_bundle(
        release=release,
        contract=contract,
        artifacts=artifacts,
        run_records=run_records,
    )
    return model_validation_evidence.build_validation_decision(
        release=release,
        contract=contract,
        evidence_bundle=bundle,
        run_records=run_records,
        criterion_run_record_ids=(
            selections if selections is not None else passing_selections(run_records)
        ),
        provenance_security_review=(
            provenance_review
            if provenance_review is not None
            else build_review(artifacts)
        ),
        status=status,
        reviewer="fixture-maintainer",
        reviewed_at=reviewed_at,
        review_reference="repository-review:fixture",
        supersedes_decisions=supersedes or [],
    )
