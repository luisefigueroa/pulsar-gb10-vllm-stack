#!/usr/bin/env python3
"""Deterministic, privacy-safe fixtures for ADR-0004 schema tests."""

from __future__ import annotations

import copy
import pathlib
import sys
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity, model_serving_release  # noqa: E402


EXPECTED_RELEASE_ID = (
    "cb8d2362aec0556dd8a270b279e01aff7bf1fa93e1585188bc9081a4d39f1766"
)
EXPECTED_CONTRACT_ID = (
    "2f40604a134c93943698c629c04bed5053dfe1f547430717bbcf4c8cba5f8490"
)


def model_artifacts() -> list[dict[str, Any]]:
    """Return intentionally unsorted inputs for builder-normalization coverage."""
    return [
        {
            "artifact_key": "draft",
            "kind": "huggingface-snapshot",
            "model_id": "Fixture/Draft-Model",
            "revision_kind": "huggingface-commit",
            "snapshot_revision": "c" * 40,
            "manifest": {
                "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
                "manifest_id": "d" * 64,
            },
        },
        {
            "artifact_key": "adapter",
            "kind": "digest-artifact",
            "artifact_id": "fixture/adapter",
            "revision": "v1",
            "digest": {"scheme": "sha256", "value": "e" * 64},
        },
        {
            "artifact_key": "primary",
            "kind": "huggingface-snapshot",
            "model_id": "Fixture/Primary-Model",
            "revision_kind": "huggingface-commit",
            "snapshot_revision": "a" * 40,
            "manifest": {
                "scheme": model_identity.SNAPSHOT_INTEGRITY_SCHEME,
                "manifest_id": "b" * 64,
            },
        },
    ]


def build_artifact_set() -> dict[str, Any]:
    return model_serving_release.build_model_artifact_set(model_artifacts())


def build_recipe(
    *,
    model_access_contract: str = "local-verified-readonly",
    engine_args: list[str] | None = None,
    tensor_parallel_size: int = 2,
    pipeline_parallel_size: int = 1,
) -> dict[str, Any]:
    return model_serving_release.build_serving_recipe(
        artifact_bindings=[
            {"artifact_key": "primary", "use": "primary-model"},
            {"artifact_key": "draft", "use": "draft-model"},
            {"artifact_key": "adapter", "use": "adapter"},
        ],
        engine_args=(
            engine_args
            if engine_args is not None
            else [
                "--max-model-len",
                "131072",
                "--distributed-executor-backend",
                "mp",
            ]
        ),
        container_env=["VLLM_BATCH_INVARIANT=1", "VLLM_USE_V1=1"],
        gpu_memory_utilization="0.80",
        spec_decode_args=[
            "--speculative-config",
            '{"model":"draft","num_speculative_tokens":5}',
        ],
        spec_decode_enabled_by_default=True,
        model_access_contract=model_access_contract,
        tensor_parallel_size=tensor_parallel_size,
        pipeline_parallel_size=pipeline_parallel_size,
        weights_ram_gib="40.0",
        kv_gib="20.00",
        overhead_gib="8",
        mem_min_free_gib="16.0",
    )


def build_runtime(
    *,
    image_reference: str | None = None,
    architecture: str = "aarch64",
    driver_abi_range: str = ">=580,<590",
    container_runtime_range: str = ">=28,<29",
    kernel_range: str = ">=6.11,<6.12",
) -> dict[str, Any]:
    return model_serving_release.build_runtime_image_identity(
        image_reference=image_reference
        or "registry.invalid/vllm@sha256:" + ("f" * 64),
        architecture=architecture,
        driver_abi_family="nvidia-open-kernel-module",
        driver_abi_range=driver_abi_range,
        container_runtime_family="docker",
        container_runtime_range=container_runtime_range,
        required_container_capabilities=["ipc-host", "nvidia-gpu"],
        kernel_range=kernel_range,
        required_kernel_features=["nfs-v4.2", "rdma"],
    )


def build_geometry(
    *,
    hardware_class: str = "nvidia-dgx-spark-gb10",
    architecture: str = "aarch64",
) -> dict[str, Any]:
    return model_serving_release.build_supported_hardware_geometry(
        hardware_class=hardware_class,
        architecture=architecture,
        node_count=2,
        accelerators_per_node=1,
        accelerator_count=2,
        tensor_parallel_size=2,
        pipeline_parallel_size=1,
        topology_class="roce-full-mesh",
        interconnect_class="roce-v2",
        minimum_rails_per_pair=2,
        minimum_unified_memory_gib_per_node="128.0",
    )


def build_release(
    *,
    artifact_set: dict[str, Any] | None = None,
    recipe: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    geometry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return model_serving_release.build_model_serving_release(
        model_artifact_set=(
            artifact_set if artifact_set is not None else build_artifact_set()
        ),
        serving_recipe=recipe if recipe is not None else build_recipe(),
        runtime_image_identity=runtime if runtime is not None else build_runtime(),
        supported_hardware_geometry=(
            geometry if geometry is not None else build_geometry()
        ),
    )


def _criterion(
    *,
    criterion_id: str,
    dimension: str,
    qualification_scope: str,
    workload_name: str,
    protocol_name: str,
    sample_size: int,
    metric: str,
    operator: str,
    value: str,
    unit: str,
    workload_parameters: dict[str, Any] | None = None,
    protocol_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "criterion_id": criterion_id,
        "dimension": dimension,
        "qualification_scope": qualification_scope,
        "workload": {
            "name": workload_name,
            "version": "1",
            "parameters": copy.deepcopy(workload_parameters or {}),
        },
        "protocol": {
            "name": protocol_name,
            "version": "1",
            "parameters": copy.deepcopy(protocol_parameters or {}),
        },
        "sample_size": sample_size,
        "thresholds": [
            {
                "metric": metric,
                "operator": operator,
                "value": value,
                "unit": unit,
            }
        ],
    }


def criteria() -> list[dict[str, Any]]:
    """Return every mandatory core dimension and release prerequisite."""
    return [
        _criterion(
            criterion_id="throughput-serving",
            dimension="throughput",
            qualification_scope="model-qualification",
            workload_name="openai-completions-throughput",
            protocol_name="pulsar-bench-serve",
            sample_size=100,
            metric="output_tokens_per_second",
            operator="gte",
            value="20",
            unit="tokens-per-second",
            protocol_parameters={"concurrency": 8},
        ),
        _criterion(
            criterion_id="accuracy-gsm8k",
            dimension="accuracy",
            qualification_scope="model-qualification",
            workload_name="gsm8k",
            protocol_name="exact-answer-evaluation",
            sample_size=100,
            metric="accuracy",
            operator="gte",
            value="0.7",
            unit="ratio",
            workload_parameters={"dataset_revision": "fixture-v1"},
        ),
        _criterion(
            criterion_id="stability-soak",
            dimension="stability",
            qualification_scope="model-qualification",
            workload_name="openai-completions-soak",
            protocol_name="pulsar-soak",
            sample_size=500,
            metric="request_error_count",
            operator="lte",
            value="0",
            unit="count",
            protocol_parameters={"concurrency": 5},
        ),
        _criterion(
            criterion_id="latency-ttft",
            dimension="latency",
            qualification_scope="model-qualification",
            workload_name="openai-completions-latency",
            protocol_name="pulsar-bench-serve",
            sample_size=100,
            metric="ttft_p95",
            operator="lte",
            value="1500",
            unit="milliseconds",
            protocol_parameters={"concurrency": 8},
        ),
        _criterion(
            criterion_id="strict-same-boot-captures",
            dimension="strict-same-boot",
            qualification_scope="model-qualification",
            workload_name="deterministic-capture",
            protocol_name="strict-same-boot",
            sample_size=30,
            metric="exact_match_rate",
            operator="eq",
            value="1",
            unit="ratio",
            protocol_parameters={
                "comparison": "exact",
                "fp_equivalent_satisfies": False,
            },
        ),
        model_serving_release.provenance_security_criterion_template(),
        _criterion(
            criterion_id="serving-integration-smoke",
            dimension="serving-integration",
            qualification_scope="serving-integration",
            workload_name="openai-compatible-smoke",
            protocol_name="health-warmup-completion",
            sample_size=3,
            metric="integration_verdict",
            operator="eq",
            value="pass",
            unit="verdict",
        ),
        _criterion(
            criterion_id="physical-geometry-dgx",
            dimension="physical-geometry",
            qualification_scope="release-promotion",
            workload_name="declared-geometry-execution",
            protocol_name="physical-dgx-qualification",
            sample_size=1,
            metric="geometry_verdict",
            operator="eq",
            value="pass",
            unit="verdict",
        ),
    ]


def build_contract(
    *,
    release: dict[str, Any] | None = None,
    release_criteria: list[dict[str, Any]] | None = None,
    relative_performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return model_serving_release.build_validation_contract(
        release=release or build_release(),
        criteria=(release_criteria if release_criteria is not None else criteria()),
        context_requirement={
            "status": "required",
            "criterion_ids": ["accuracy-gsm8k"],
            "minimum_tokens": 32768,
            "depths": ["0.95", "0.05", "0.50"],
        },
        soak_requirement={
            "status": "required",
            "criterion_id": "stability-soak",
            "minimum_duration_seconds": 9000,
            "concurrency": 5,
            "maximum_request_errors": 0,
        },
        relative_performance=(
            relative_performance
            if relative_performance is not None
            else model_serving_release.no_comparable_predecessor()
        ),
    )
