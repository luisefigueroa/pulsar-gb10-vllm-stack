#!/usr/bin/env python3
"""Deterministic, privacy-safe fixtures for ADR-0004 evidence schemas."""

from __future__ import annotations

import copy
import hashlib
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
    "09c407fd2c7a8004ced8033d9b1e7e035aab94c40b2b10e8bf20d906a376709d"
)
EXPECTED_EVIDENCE_BUNDLE_ID = (
    "e8a98f17d14837c5a2405910095b2cbfb1bfbe3803abf4f5e06765a8c5a7fc07"
)
EXPECTED_VALIDATION_DECISION_ID = (
    "b87146549936390c3738ad4661dd4ed5ecb89e50dbfb43b140dd02abf6d37bb6"
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
    predecessor_source: dict[str, Any] | None = None,
    predecessor_release_id: str | None = None,
    throughput_max_regression_percent: str = "5",
    latency_max_regression_percent: str = "10",
) -> dict[str, Any]:
    release = release or build_release()
    predecessor_source = predecessor_source or build_predecessor_source()
    predecessor_contract = predecessor_source["contract"]
    predecessor_runs = predecessor_source["run_records"]
    release_criteria = release_fixture.criteria()
    by_dimension = {item["dimension"]: item for item in release_criteria}
    predecessor_criteria = {
        item["dimension"]: item
        for item in predecessor_contract["release_criteria"]["criteria"]
    }
    predecessor_run_by_criterion = {
        observation["criterion_id"]: record["run_record_id"]
        for record in predecessor_runs
        for observation in record["criterion_observations"]
    }
    relative = model_serving_release.build_relative_performance_requirement(
        release=release,
        predecessor_release_id=(
            predecessor_release_id or predecessor_source["release"]["release_id"]
        ),
        predecessor_contract_id=predecessor_contract["contract_id"],
        predecessor_bundle_id=predecessor_source["evidence_bundle"]["bundle_id"],
        predecessor_decision_id=predecessor_source["decision"]["decision_id"],
        throughput_criterion=by_dimension["throughput"],
        throughput_predecessor_criterion_id=predecessor_criteria["throughput"][
            "criterion_id"
        ],
        throughput_predecessor_run_record_id=predecessor_run_by_criterion[
            predecessor_criteria["throughput"]["criterion_id"]
        ],
        latency_criterion=by_dimension["latency"],
        latency_predecessor_criterion_id=predecessor_criteria["latency"][
            "criterion_id"
        ],
        latency_predecessor_run_record_id=predecessor_run_by_criterion[
            predecessor_criteria["latency"]["criterion_id"]
        ],
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
    compatibility = release["runtime_image_identity"]["host_compatibility"]
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
        "cluster": {
            "node_count": geometry["node_count"],
            "accelerator_count": geometry["accelerator_count"],
            "tensor_parallel_size": geometry["tensor_parallel_size"],
            "pipeline_parallel_size": geometry["pipeline_parallel_size"],
            "topology_class": geometry["topology_class"],
            "interconnect_class": geometry["interconnect_class"],
            "rails_per_pair": geometry["minimum_rails_per_pair"],
        },
        "ranks": [
            {
                "rank": rank,
                "hardware_class": geometry["hardware_class"],
                "architecture": geometry["architecture"],
                "accelerator_count": geometry["accelerators_per_node"],
                "unified_memory_gib": "128",
                "driver_abi": {
                    "family": compatibility["driver_abi"]["family"],
                    "version": "580.1",
                },
                "container_runtime": {
                    "family": compatibility["container_runtime"]["family"],
                    "version": "28.0",
                    "capabilities": compatibility["container_runtime"][
                        "required_capabilities"
                    ],
                },
                "kernel": {
                    "version": "6.11.1",
                    "features": compatibility["kernel"]["required_features"],
                },
                "engine_version": "0.26.0",
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


def timestamp_after(value: str, seconds: str) -> str:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    elapsed = Decimal(seconds)
    result = parsed + timedelta(microseconds=int(elapsed * Decimal(1_000_000)))
    return result.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_run_for_criterion(
    criterion_id: str,
    *,
    release: dict[str, Any] | None = None,
    contract: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, str]] | None = None,
    observation_completion: str | None = None,
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
    soak_started_at: str | None = None,
    soak_ended_at: str | None = None,
    attempt_started_at: str = "2026-08-14T12:00:00Z",
    attempt_ended_at: str | None = None,
    attempted_criterion_ids: list[str] | None = None,
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
    observed_observation_completion = observation_completion or (
        "complete" if attempt_completion == "completed" else "inconclusive"
    )
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
        observed_soak_duration = (
            str(soak_requirement["minimum_duration_seconds"])
            if soak_duration_seconds is None
            else soak_duration_seconds
        )
        observed_soak_started_at = soak_started_at or attempt_started_at
        observed_soak_ended_at = soak_ended_at or timestamp_after(
            observed_soak_started_at, observed_soak_duration
        )
        soak_observation = {
            "completion": soak_completion,
            "started_at": observed_soak_started_at,
            "ended_at": observed_soak_ended_at,
            "duration_seconds": observed_soak_duration,
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
    observation = {
        "criterion_id": criterion_id,
        "benchmark_protocol_id": model_serving_release.benchmark_protocol_id(
            criterion
        ),
        "completion": observed_observation_completion,
        "sample_size": (
            criterion["sample_size"] if sample_size is None else sample_size
        ),
        "metrics": observed_metrics,
        "evidence_artifact_ids": [artifact["artifact_id"]],
        "contract_requirements": {
            "context": context_observation,
            "soak": soak_observation,
        },
        "reason": (
            observation_reason
            if observation_reason is not None
            else (
                "completed"
                if observed_observation_completion == "complete"
                else "insufficient-stable-samples"
            )
        ),
    }
    observed_attempt_ended_at = attempt_ended_at or (
        soak_observation["ended_at"]
        if soak_observation is not None
        else "2026-08-14T12:05:00Z"
    )
    return model_validation_evidence.build_validation_run_record(
        release=release,
        contract=contract,
        attempt={
            "attempt_id": attempt_id or f"attempt-{criterion_id}",
            "phase": phase_for_scope(criterion["qualification_scope"]),
            "qualification_scope": criterion["qualification_scope"],
            "attempted_criterion_ids": sorted(
                copy.deepcopy(
                    [criterion_id]
                    if attempted_criterion_ids is None
                    else attempted_criterion_ids
                )
            ),
            "started_at": attempt_started_at,
            "ended_at": observed_attempt_ended_at,
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
                "version": "sha256:" + digest("validate/run-gates.sh:fixture-v1"),
                "arguments": [
                    {"kind": "operation", "value": "run-validation-gates"},
                    {
                        "kind": "criterion-reference",
                        "criterion_id": criterion_id,
                    },
                ],
                "environment": [
                    {"kind": "secret-reference", "name": "VLLM_API_KEY"}
                ],
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
            "attempted_criterion_ids": [],
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
                "version": "sha256:" + digest("scripts/model-library.sh:fixture-v1"),
                "arguments": [
                    {
                        "kind": "operation",
                        "value": "prepare-model-for-serving",
                    },
                ],
                "environment": [],
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


def build_exclusion(
    criterion_id: str,
    run_record: dict[str, Any],
    artifacts: list[dict[str, Any]],
    *,
    reason: str = "reviewed-protocol-deviation",
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "run_record_id": run_record["run_record_id"],
        "reason": reason,
        "review_evidence_artifact_ids": [
            review_artifact(artifacts)["artifact_id"]
        ],
    }


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
    exclusions: list[dict[str, Any]] | None = None,
    predecessor_registry: list[dict[str, Any]] | None = None,
    provenance_review: dict[str, Any] | None = None,
    status: str = "validated",
    supersedes: list[dict[str, Any]] | None = None,
    supersession_lineage: list[dict[str, Any]] | None = None,
    reviewed_at: str = "2026-08-14T15:00:00Z",
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
        criterion_exclusions=exclusions or [],
        predecessor_evidence_registry=predecessor_registry or [],
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
        supersession_lineage=supersession_lineage or [],
    )


def build_predecessor_release() -> dict[str, Any]:
    artifacts = release_fixture.model_artifacts()
    primary = next(item for item in artifacts if item["artifact_key"] == "primary")
    primary["snapshot_revision"] = "9" * 40
    primary["manifest"]["manifest_id"] = "8" * 64
    return release_fixture.build_release(
        artifact_set=model_serving_release.build_model_artifact_set(artifacts)
    )


def build_predecessor_source() -> dict[str, Any]:
    release = build_predecessor_release()
    contract = build_contract(release=release)
    artifacts = build_artifacts()
    runs = build_passing_runs(
        release=release,
        contract=contract,
        artifacts=artifacts,
    )
    bundle = build_bundle(
        release=release,
        contract=contract,
        artifacts=artifacts,
        run_records=runs,
    )
    decision = build_decision(
        release=release,
        contract=contract,
        artifacts=artifacts,
        run_records=runs,
        bundle=bundle,
    )
    return {
        "release": release,
        "contract": contract,
        "evidence_bundle": bundle,
        "run_records": runs,
        "decision": decision,
    }


def evidence_source(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "release": release,
        "contract": contract,
        "evidence_bundle": bundle,
        "run_records": run_records,
        "decision": decision,
    }
