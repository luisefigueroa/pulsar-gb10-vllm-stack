#!/usr/bin/env python3
"""Immutable validation evidence and decision schemas for ADR 0004.

This module implements the second machine-readable stage of Model Serving
Release validation.  It is deliberately pure: it performs no filesystem or
network I/O, captures no evidence itself, publishes no trusted artifact,
changes no profile, and grants no serving eligibility.  Repository review and
the eventual trusted persistence/status-projection layer remain separate
authority boundaries.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import Any

try:
    from scripts import model_identity, model_serving_release
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]


EVIDENCE_ARTIFACT_SCHEMA_VERSION = 1
EVIDENCE_ARTIFACT_KIND = "pulsar-validation-evidence-artifact"
VALIDATION_RUN_RECORD_SCHEMA_VERSION = 1
VALIDATION_RUN_RECORD_KIND = "pulsar-validation-run-record"
VALIDATION_EVIDENCE_BUNDLE_SCHEMA_VERSION = 1
VALIDATION_EVIDENCE_BUNDLE_KIND = "pulsar-model-serving-validation-bundle"
VALIDATION_DECISION_SCHEMA_VERSION = 1
VALIDATION_DECISION_KIND = "pulsar-validation-decision"

RUN_PHASE_SCOPES = {
    "preparation": "catalog-artifact",
    "serving-integration": "serving-integration",
    "model-qualification": "model-qualification",
    "release-review": "release-promotion",
}
ATTEMPT_COMPLETIONS = {"completed", "failed", "interrupted", "inconclusive"}
OBSERVATION_COMPLETIONS = {"complete", "inconclusive"}
QUALIFICATION_BARRIER_STATES = {"not-reached", "passed"}

ORIGINS = {"huggingface", "cold-catalog", "managed-home", "preexisting-local"}
TRANSFERS = {
    "preexisting",
    "replicated-pull",
    "ssh-control",
    "ssh-roce",
    "nfs-rdma",
}
SUBSYSTEM_MATURITY = {"promoted", "experimental"}
RUNTIME_SOURCES = {
    "replicated-cache",
    "absolute-path",
    "durable-home",
    "sealed-hot",
    "live-mount",
}
RETENTION_POLICIES = {"durable", "ephemeral", "pinned", "external"}
VERIFICATION_RESULTS = {"passed", "failed", "not-run"}

ARTIFACT_LOCATION_KINDS = {"repository-relative", "protected-content-addressed"}
ARTIFACT_VISIBILITY = {"publishable", "protected"}
PRIVACY_REVIEW_RESULTS = {"passed", "pending", "failed"}

CRITERION_DISPOSITIONS = {"pass", "fail", "inconclusive", "not-evaluated"}
REVIEW_COMPONENT_RESULTS = {"pass", "fail", "pending"}
PROVENANCE_REVIEW_COMPONENTS = (
    "artifact_identity",
    "runtime_identity",
    "contract_frozen_before_testing",
    "evidence_privacy",
    "security",
)

BASE_VALIDATION_STATUSES = {
    "untested",
    "testing-incomplete",
    "tested-criteria-not-met",
    "tested-inconclusive",
    "validated",
}
EFFECTIVE_VALIDATION_STATUSES = BASE_VALIDATION_STATUSES | {"superseded"}
STATUS_LABELS = {
    "untested": "Untested",
    "testing-incomplete": "Testing incomplete",
    "tested-criteria-not-met": "Tested—criteria not met",
    "tested-inconclusive": "Tested—inconclusive",
    "validated": "Validated",
    "superseded": "Superseded",
}

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|authorization)\s*[:=]"
)
SECRET_FLAGS = {
    "--api-key",
    "--authorization",
    "--password",
    "--secret",
    "--token",
}


class ModelValidationEvidenceError(ValueError):
    """A run record, evidence bundle, or validation decision is invalid."""


def fail(message: str) -> None:
    raise ModelValidationEvidenceError(message)


def _require_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        missing = sorted(fields - set(value)) if isinstance(value, dict) else []
        extra = sorted(set(value) - fields) if isinstance(value, dict) else []
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _nonempty_string(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        fail(f"{label} must be a non-empty single-line string")
    return value


def _safe_identifier(value: Any, *, label: str) -> str:
    value = _nonempty_string(value, label=label)
    if SAFE_ID_RE.fullmatch(value) is None:
        fail(f"{label} is invalid")
    return value


def _sha256(value: Any, *, label: str, prefix: bool = False) -> str:
    if not isinstance(value, str):
        fail(f"{label} must be a SHA-256 digest")
    digest = value.removeprefix("sha256:") if prefix else value
    if model_identity.SHA256_HEX_RE.fullmatch(digest) is None:
        fail(f"{label} must be a SHA-256 digest")
    if prefix and value != "sha256:" + digest:
        fail(f"{label} must use the sha256: prefix")
    return value


def _positive_integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        fail(f"{label} must be a non-negative integer")
    return value


def _normalize_decimal(
    value: Any,
    *,
    label: str,
    allow_none: bool = False,
    positive: bool = False,
    signed: bool = False,
    require_canonical: bool = True,
) -> str | None:
    if allow_none and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        fail(f"{label} must be a canonical decimal string or integer")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        fail(f"{label} must be numeric")
    if not parsed.is_finite():
        fail(f"{label} must be finite")
    if not signed and parsed < 0:
        fail(f"{label} must be non-negative")
    if positive and parsed <= 0:
        fail(f"{label} must be positive")
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        normalized = "0"
    if require_canonical and value != normalized:
        fail(f"{label} is not canonical (expected {normalized!r})")
    return normalized


def _parse_rfc3339_utc(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail(f"{label} must be an RFC3339 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        fail(f"{label} must include UTC")
    return parsed


def _sorted_unique_sha256(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    for item in value:
        _sha256(item, label=f"{label} item")
    if value != sorted(value) or len(value) != len(set(value)):
        fail(f"{label} must be sorted and unique")
    return value


def _relative_repository_path(value: Any, *, label: str) -> str:
    value = _nonempty_string(value, label=label)
    if "\\" in value or value.startswith("/") or "//" in value:
        fail(f"{label} must be a normalized repository-relative path")
    path = PurePosixPath(value)
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        fail(f"{label} must be a normalized repository-relative path")
    return value


def evidence_artifact_identity(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "artifact_id"}


def evidence_artifact_id(artifact: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(evidence_artifact_identity(artifact))


def build_evidence_artifact(
    *,
    location_kind: str,
    location_value: str,
    content_sha256: str,
    media_type: str,
    qualification_scope: str,
    visibility: str,
    privacy_review: str,
) -> dict[str, Any]:
    """Build one content-addressed evidence reference without reading it."""
    artifact: dict[str, Any] = {
        "schema_version": EVIDENCE_ARTIFACT_SCHEMA_VERSION,
        "kind": EVIDENCE_ARTIFACT_KIND,
        "location": {"kind": location_kind, "value": location_value},
        "content": {
            "sha256": content_sha256,
            "media_type": media_type,
        },
        "qualification_scope": qualification_scope,
        "visibility": visibility,
        "privacy_review": privacy_review,
    }
    artifact["artifact_id"] = evidence_artifact_id(artifact)
    return validate_evidence_artifact(artifact)


def validate_evidence_artifact(value: Any) -> dict[str, Any]:
    artifact = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "location",
            "content",
            "qualification_scope",
            "visibility",
            "privacy_review",
            "artifact_id",
        },
        label="evidence artifact",
    )
    if artifact.get("schema_version") != EVIDENCE_ARTIFACT_SCHEMA_VERSION:
        fail("evidence artifact schema_version is unsupported")
    if artifact.get("kind") != EVIDENCE_ARTIFACT_KIND:
        fail("evidence artifact kind is invalid")
    location = _require_fields(
        artifact.get("location"),
        {"kind", "value"},
        label="evidence artifact location",
    )
    location_kind = location.get("kind")
    if location_kind not in ARTIFACT_LOCATION_KINDS:
        fail("evidence artifact location kind is unsupported")
    if location_kind == "repository-relative":
        _relative_repository_path(
            location.get("value"), label="evidence artifact location value"
        )
    else:
        _sha256(
            location.get("value"),
            label="evidence artifact protected locator",
            prefix=True,
        )
    content = _require_fields(
        artifact.get("content"),
        {"sha256", "media_type"},
        label="evidence artifact content",
    )
    _sha256(content.get("sha256"), label="evidence artifact content sha256")
    _nonempty_string(content.get("media_type"), label="evidence artifact media_type")
    if artifact.get("qualification_scope") not in model_serving_release.QUALIFICATION_SCOPES:
        fail("evidence artifact qualification_scope is unsupported")
    if artifact.get("visibility") not in ARTIFACT_VISIBILITY:
        fail("evidence artifact visibility is unsupported")
    if artifact.get("privacy_review") not in PRIVACY_REVIEW_RESULTS:
        fail("evidence artifact privacy_review is unsupported")
    if (
        artifact.get("visibility") == "publishable"
        and location_kind != "repository-relative"
    ):
        fail("publishable evidence must use a repository-relative location")
    if (
        artifact.get("visibility") == "protected"
        and location_kind != "protected-content-addressed"
    ):
        fail("protected evidence must use a content-addressed locator")
    if (
        location_kind == "protected-content-addressed"
        and location.get("value") != "sha256:" + content["sha256"]
    ):
        fail("protected evidence locator differs from its content digest")
    artifact_id_value = artifact.get("artifact_id")
    if (
        not isinstance(artifact_id_value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(artifact_id_value) is None
        or artifact_id_value != evidence_artifact_id(artifact)
    ):
        fail("evidence artifact identity mismatch")
    return artifact


def _artifact_registry(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        fail("evidence artifacts must be a list")
    artifacts = [validate_evidence_artifact(item) for item in value]
    ids = [item["artifact_id"] for item in artifacts]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        fail("evidence artifacts must be sorted by unique artifact_id")
    return {item["artifact_id"]: item for item in artifacts}


def _model_artifact_set_id(release: dict[str, Any]) -> str:
    release = model_serving_release.validate_model_serving_release(release)
    return model_identity.canonical_json_digest(release["model_artifact_set"])


def _validate_subsystems(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail("preparation provenance subsystems must be a non-empty list")
    keys: list[tuple[str, str, str]] = []
    for index, item in enumerate(value):
        subsystem = _require_fields(
            item,
            {"name", "version", "maturity"},
            label=f"preparation provenance subsystems[{index}]",
        )
        name = _safe_identifier(
            subsystem.get("name"),
            label=f"preparation provenance subsystems[{index}].name",
        )
        version = _nonempty_string(
            subsystem.get("version"),
            label=f"preparation provenance subsystems[{index}].version",
        )
        maturity = subsystem.get("maturity")
        if maturity not in SUBSYSTEM_MATURITY:
            fail("preparation provenance subsystem maturity is unsupported")
        keys.append((name, version, maturity))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail("preparation provenance subsystems must be sorted and unique")
    return value


def _validate_runtime_sources(
    value: Any,
    *,
    release: dict[str, Any],
    barrier_state: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail("preparation provenance runtime_sources must be a list")
    geometry = release["supported_hardware_geometry"]
    node_count = geometry["node_count"]
    ranks: list[int] = []
    for index, item in enumerate(value):
        source = _require_fields(
            item,
            {"rank", "source", "retention"},
            label=f"preparation provenance runtime_sources[{index}]",
        )
        rank = _nonnegative_integer(
            source.get("rank"),
            label=f"preparation provenance runtime_sources[{index}].rank",
        )
        if rank >= node_count:
            fail("preparation provenance runtime source rank is outside geometry")
        if source.get("source") not in RUNTIME_SOURCES:
            fail("preparation provenance runtime source is unsupported")
        if source.get("retention") not in RETENTION_POLICIES:
            fail("preparation provenance retention is unsupported")
        ranks.append(rank)
    if ranks != sorted(ranks) or len(ranks) != len(set(ranks)):
        fail("preparation provenance runtime_sources must be sorted by unique rank")
    if barrier_state == "passed" and ranks != list(range(node_count)):
        fail("qualification barrier requires one runtime source for every rank")
    access_contract = release["serving_recipe"]["model_access_contract"]
    source_names = {item["source"] for item in value}
    if access_contract == "local-verified-readonly" and "live-mount" in source_names:
        fail("local verified release cannot use a live-mount runtime source")
    if access_contract == "live-remote-readonly" and barrier_state == "passed":
        if source_names != {"live-mount"}:
            fail("live remote release requires live-mount on every serving rank")
    return value


def _validate_preparation_provenance(
    value: Any,
    *,
    release: dict[str, Any],
) -> dict[str, Any]:
    provenance = _require_fields(
        value,
        {
            "origin",
            "transfer",
            "subsystems",
            "runtime_sources",
            "verification",
            "qualification_barrier",
            "elapsed_seconds",
        },
        label="preparation provenance",
    )
    if provenance.get("origin") not in ORIGINS:
        fail("preparation provenance origin is unsupported")
    if provenance.get("transfer") not in TRANSFERS:
        fail("preparation provenance transfer is unsupported")
    _validate_subsystems(provenance.get("subsystems"))
    barrier_state = provenance.get("qualification_barrier")
    if barrier_state not in QUALIFICATION_BARRIER_STATES:
        fail("preparation provenance qualification_barrier is unsupported")
    verification = _require_fields(
        provenance.get("verification"),
        {"status", "model_artifact_set_id"},
        label="preparation provenance verification",
    )
    if verification.get("status") not in VERIFICATION_RESULTS:
        fail("preparation provenance verification status is unsupported")
    expected_artifact_set_id = _model_artifact_set_id(release)
    if verification.get("model_artifact_set_id") != expected_artifact_set_id:
        fail("preparation provenance Model Artifact Set identity mismatch")
    if barrier_state == "passed" and verification.get("status") != "passed":
        fail("qualification barrier requires full artifact verification")
    _validate_runtime_sources(
        provenance.get("runtime_sources"),
        release=release,
        barrier_state=barrier_state,
    )
    _normalize_decimal(
        provenance.get("elapsed_seconds"),
        label="preparation provenance elapsed_seconds",
        allow_none=True,
        require_canonical=True,
    )
    return provenance


def _validate_rank_observations(
    value: Any,
    *,
    release: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail("observed environment ranks must be a list")
    geometry = release["supported_hardware_geometry"]
    ranks: list[int] = []
    minimum_memory = Decimal(
        geometry["capacity"]["minimum_unified_memory_gib_per_node"]
    )
    for index, item in enumerate(value):
        rank = _require_fields(
            item,
            {
                "rank",
                "hardware_class",
                "accelerator_count",
                "unified_memory_gib",
                "driver_version",
                "kernel_release",
                "container_runtime_version",
                "engine_version",
            },
            label=f"observed environment ranks[{index}]",
        )
        rank_number = _nonnegative_integer(
            rank.get("rank"), label=f"observed environment ranks[{index}].rank"
        )
        if rank.get("hardware_class") != geometry["hardware_class"]:
            fail("observed environment hardware class differs from release")
        if rank.get("accelerator_count") != geometry["accelerators_per_node"]:
            fail("observed environment accelerator count differs from release")
        memory = _normalize_decimal(
            rank.get("unified_memory_gib"),
            label=f"observed environment ranks[{index}].unified_memory_gib",
            positive=True,
        )
        assert memory is not None
        if Decimal(memory) < minimum_memory:
            fail("observed environment memory is below release requirement")
        for field in (
            "driver_version",
            "kernel_release",
            "container_runtime_version",
            "engine_version",
        ):
            _nonempty_string(
                rank.get(field), label=f"observed environment ranks[{index}].{field}"
            )
        ranks.append(rank_number)
    if ranks != list(range(geometry["node_count"])):
        fail("observed environment must contain each release rank exactly once")
    return value


def _validate_observed_environment(
    value: Any,
    *,
    release: dict[str, Any],
    barrier_state: str,
) -> dict[str, Any]:
    environment = _require_fields(
        value,
        {
            "image_digest",
            "supported_hardware_geometry_id",
            "server_boot_id",
            "launch_id",
            "ranks",
        },
        label="observed environment",
    )
    expected_image = release["runtime_image_identity"]["image"]["digest"]
    if environment.get("image_digest") != expected_image:
        fail("observed environment image digest differs from release")
    expected_geometry = model_serving_release.supported_hardware_geometry_id(
        release["supported_hardware_geometry"]
    )
    if environment.get("supported_hardware_geometry_id") != expected_geometry:
        fail("observed environment geometry differs from release")
    for field in ("server_boot_id", "launch_id"):
        identifier = environment.get(field)
        if identifier is not None:
            _sha256(identifier, label=f"observed environment {field}")
        if barrier_state == "passed" and identifier is None:
            fail(f"passed qualification barrier requires observed {field}")
    _validate_rank_observations(environment.get("ranks"), release=release)
    return environment


def _validate_command(value: Any, *, index: int) -> dict[str, Any]:
    command = _require_fields(
        value,
        {
            "program",
            "version",
            "arguments",
            "environment_variable_names",
            "working_directory",
        },
        label=f"run commands[{index}]",
    )
    program = _relative_repository_path(
        command.get("program"), label=f"run commands[{index}].program"
    )
    if program.startswith("results/"):
        fail("run command program cannot be an evidence artifact")
    _nonempty_string(command.get("version"), label=f"run commands[{index}].version")
    arguments = command.get("arguments")
    if not isinstance(arguments, list):
        fail(f"run commands[{index}].arguments must be a list")
    for argument_index, argument in enumerate(arguments):
        argument = _nonempty_string(
            argument,
            label=f"run commands[{index}].arguments[{argument_index}]",
        )
        if argument.startswith("/"):
            fail("run command arguments must not expose absolute paths")
        if SECRET_ASSIGNMENT_RE.search(argument) or argument.lower() in SECRET_FLAGS:
            fail("run command arguments must not contain credentials")
    environment_names = command.get("environment_variable_names")
    if not isinstance(environment_names, list):
        fail(f"run commands[{index}].environment_variable_names must be a list")
    for name in environment_names:
        if not isinstance(name, str) or ENV_NAME_RE.fullmatch(name) is None:
            fail("run command environment variable name is invalid")
    if environment_names != sorted(environment_names) or len(environment_names) != len(
        set(environment_names)
    ):
        fail("run command environment variable names must be sorted and unique")
    if command.get("working_directory") != "repository-root":
        fail("run command working_directory must be repository-root")
    return command


def _canonical_metric_value(value: Any, *, label: str) -> str:
    value = _nonempty_string(value, label=label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return value
    if not parsed.is_finite():
        fail(f"{label} must be finite")
    normalized = _normalize_decimal(
        value,
        label=label,
        signed=True,
        require_canonical=False,
    )
    if value != normalized:
        fail(f"{label} is not canonical (expected {normalized!r})")
    return value


def _validate_metrics(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    keys: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        metric = _require_fields(
            item,
            {"metric", "value", "unit"},
            label=f"{label}[{index}]",
        )
        name = _nonempty_string(
            metric.get("metric"), label=f"{label}[{index}].metric"
        )
        unit = _nonempty_string(metric.get("unit"), label=f"{label}[{index}].unit")
        _canonical_metric_value(metric.get("value"), label=f"{label}[{index}].value")
        keys.append((name, unit))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail(f"{label} must be sorted by unique metric/unit")
    return value


def _criterion_map(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["criterion_id"]: item
        for item in contract["release_criteria"]["criteria"]
    }


def _context_requirement_for_criterion(
    contract: dict[str, Any], criterion_id: str
) -> dict[str, Any] | None:
    requirement = contract["release_criteria"]["context_requirement"]
    if (
        requirement["status"] == "required"
        and criterion_id in requirement["criterion_ids"]
    ):
        return requirement
    return None


def _soak_requirement_for_criterion(
    contract: dict[str, Any], criterion_id: str
) -> dict[str, Any] | None:
    requirement = contract["release_criteria"]["soak_requirement"]
    if (
        requirement["status"] == "required"
        and criterion_id == requirement["criterion_id"]
    ):
        return requirement
    return None


def _relative_requirement_for_criterion(
    contract: dict[str, Any], criterion: dict[str, Any]
) -> dict[str, Any] | None:
    relative = contract["release_criteria"]["relative_performance"]
    dimension = criterion["dimension"]
    if relative["status"] != "required" or dimension not in {
        "throughput",
        "latency",
    }:
        return None
    dimension_requirement = relative[dimension]
    if dimension_requirement["criterion_id"] != criterion["criterion_id"]:
        return None
    return {
        "predecessor_release_id": relative["predecessor_release_id"],
        "supported_hardware_geometry_id": relative[
            "supported_hardware_geometry_id"
        ],
        **dimension_requirement,
    }


def _validate_requirement_artifact_ids(
    value: Any,
    *,
    label: str,
    run_artifact_ids: set[str],
) -> list[str]:
    artifact_ids = _sorted_unique_sha256(value, label=label)
    if not artifact_ids or not set(artifact_ids).issubset(run_artifact_ids):
        fail(f"{label} are missing from the run")
    return artifact_ids


def _validate_requirement_completion(
    value: dict[str, Any], *, label: str
) -> str:
    completion = value.get("completion")
    if completion not in OBSERVATION_COMPLETIONS:
        fail(f"{label}.completion is unsupported")
    reason = _nonempty_string(value.get("reason"), label=f"{label}.reason")
    if completion == "complete" and reason != "completed":
        fail(f"{label} complete evidence reason must be completed")
    if completion == "inconclusive" and reason == "completed":
        fail(f"{label} inconclusive evidence requires an explanatory reason")
    return completion


def _validate_context_requirement_observation(
    value: Any,
    *,
    requirement: dict[str, Any] | None,
    run_artifact_ids: set[str],
) -> dict[str, Any] | None:
    label = "run context requirement observation"
    if requirement is None:
        if value is not None:
            fail("run records context evidence for a criterion without that requirement")
        return None
    if value is None:
        return None
    observation = _require_fields(
        value,
        {
            "completion",
            "minimum_tokens",
            "depths",
            "evidence_artifact_ids",
            "reason",
        },
        label=label,
    )
    _validate_requirement_completion(observation, label=label)
    _nonnegative_integer(
        observation.get("minimum_tokens"), label=f"{label}.minimum_tokens"
    )
    depths = observation.get("depths")
    if not isinstance(depths, list):
        fail(f"{label}.depths must be a list")
    normalized_depths: list[str] = []
    for index, depth in enumerate(depths):
        normalized = _normalize_decimal(
            depth,
            label=f"{label}.depths[{index}]",
            require_canonical=True,
        )
        assert normalized is not None
        if not Decimal("0") <= Decimal(normalized) <= Decimal("1"):
            fail(f"{label}.depths must be between zero and one")
        normalized_depths.append(normalized)
    if normalized_depths != sorted(normalized_depths, key=Decimal) or len(
        normalized_depths
    ) != len(set(normalized_depths)):
        fail(f"{label}.depths must be sorted and unique")
    _validate_requirement_artifact_ids(
        observation.get("evidence_artifact_ids"),
        label=f"{label}.evidence_artifact_ids",
        run_artifact_ids=run_artifact_ids,
    )
    return observation


def _validate_soak_requirement_observation(
    value: Any,
    *,
    requirement: dict[str, Any] | None,
    criterion_metrics: list[dict[str, Any]],
    run_artifact_ids: set[str],
) -> dict[str, Any] | None:
    label = "run soak requirement observation"
    if requirement is None:
        if value is not None:
            fail("run records soak evidence for a criterion without that requirement")
        return None
    if value is None:
        return None
    observation = _require_fields(
        value,
        {
            "completion",
            "duration_seconds",
            "concurrency",
            "request_errors",
            "evidence_artifact_ids",
            "reason",
        },
        label=label,
    )
    completion = _validate_requirement_completion(observation, label=label)
    _normalize_decimal(
        observation.get("duration_seconds"),
        label=f"{label}.duration_seconds",
        require_canonical=True,
    )
    _nonnegative_integer(
        observation.get("concurrency"), label=f"{label}.concurrency"
    )
    request_errors = _nonnegative_integer(
        observation.get("request_errors"), label=f"{label}.request_errors"
    )
    _validate_requirement_artifact_ids(
        observation.get("evidence_artifact_ids"),
        label=f"{label}.evidence_artifact_ids",
        run_artifact_ids=run_artifact_ids,
    )
    if completion == "complete":
        metrics = {
            (item["metric"], item["unit"]): item["value"]
            for item in criterion_metrics
        }
        error_metric = metrics.get(("request_error_count", "count"))
        if error_metric is not None:
            observed_errors = _numeric_metric(
                error_metric,
                label="run soak request_error_count criterion metric",
            )
            if observed_errors != Decimal(request_errors):
                fail("run soak request_errors disagrees with the criterion metric")
    return observation


def _numeric_metric(
    value: str, *, label: str, positive: bool = False
) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail(f"{label} must be numeric")
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        qualifier = "positive" if positive else "non-negative"
        fail(f"{label} must be a {qualifier} finite number")
    return parsed


def _validate_relative_performance_comparison(
    value: Any,
    *,
    requirement: dict[str, Any] | None,
    criterion: dict[str, Any],
    criterion_metrics: list[dict[str, Any]],
    run_artifact_ids: set[str],
) -> dict[str, Any] | None:
    label = "run relative performance comparison"
    if requirement is None:
        if value is not None:
            fail(
                "run records relative evidence without a comparable predecessor"
            )
        return None
    if value is None:
        return None
    comparison = _require_fields(
        value,
        {
            "predecessor_release_id",
            "supported_hardware_geometry_id",
            "benchmark_protocol_id",
            "completion",
            "sample_size",
            "metrics",
            "evidence_artifact_ids",
            "reason",
        },
        label=label,
    )
    for field in (
        "predecessor_release_id",
        "supported_hardware_geometry_id",
        "benchmark_protocol_id",
    ):
        if comparison.get(field) != requirement[field]:
            fail(f"{label} {field} differs from the frozen contract")
    completion = _validate_requirement_completion(comparison, label=label)
    sample_size = _nonnegative_integer(
        comparison.get("sample_size"), label=f"{label}.sample_size"
    )
    metrics = _validate_metrics(comparison.get("metrics"), label=f"{label}.metrics")
    _validate_requirement_artifact_ids(
        comparison.get("evidence_artifact_ids"),
        label=f"{label}.evidence_artifact_ids",
        run_artifact_ids=run_artifact_ids,
    )
    if completion == "complete":
        if sample_size < criterion["sample_size"]:
            fail(f"{label} complete sample is smaller than the frozen contract")
        required_keys = {
            (threshold["metric"], threshold["unit"])
            for threshold in criterion["thresholds"]
        }
        predecessor_metrics = {
            (item["metric"], item["unit"]): item["value"] for item in metrics
        }
        current_metrics = {
            (item["metric"], item["unit"]): item["value"]
            for item in criterion_metrics
        }
        if set(predecessor_metrics) != required_keys:
            fail(f"{label} must contain exactly the frozen threshold metrics")
        for key in sorted(required_keys):
            _numeric_metric(
                predecessor_metrics[key],
                label=f"{label} predecessor metric {key[0]}",
                positive=True,
            )
            _numeric_metric(
                current_metrics[key],
                label=f"{label} current metric {key[0]}",
            )
    return comparison


def _validate_contract_requirement_observations(
    value: Any,
    *,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    criterion_metrics: list[dict[str, Any]],
    run_artifact_ids: set[str],
) -> dict[str, Any]:
    requirements = _require_fields(
        value,
        {"context", "soak", "relative_performance"},
        label="run contract requirement observations",
    )
    criterion_id = criterion["criterion_id"]
    _validate_context_requirement_observation(
        requirements.get("context"),
        requirement=_context_requirement_for_criterion(contract, criterion_id),
        run_artifact_ids=run_artifact_ids,
    )
    _validate_soak_requirement_observation(
        requirements.get("soak"),
        requirement=_soak_requirement_for_criterion(contract, criterion_id),
        criterion_metrics=criterion_metrics,
        run_artifact_ids=run_artifact_ids,
    )
    _validate_relative_performance_comparison(
        requirements.get("relative_performance"),
        requirement=_relative_requirement_for_criterion(contract, criterion),
        criterion=criterion,
        criterion_metrics=criterion_metrics,
        run_artifact_ids=run_artifact_ids,
    )
    return requirements


def _validate_criterion_observation(
    value: Any,
    *,
    index: int,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    run_artifact_ids: set[str],
) -> dict[str, Any]:
    label = f"run criterion_observations[{index}]"
    observation = _require_fields(
        value,
        {
            "criterion_id",
            "benchmark_protocol_id",
            "completion",
            "sample_size",
            "metrics",
            "evidence_artifact_ids",
            "contract_requirements",
            "reason",
        },
        label=label,
    )
    if observation.get("criterion_id") != criterion["criterion_id"]:
        fail(f"{label} criterion identity mismatch")
    expected_protocol_id = model_serving_release.benchmark_protocol_id(criterion)
    if observation.get("benchmark_protocol_id") != expected_protocol_id:
        fail(f"{label} benchmark protocol identity mismatch")
    completion = observation.get("completion")
    if completion not in OBSERVATION_COMPLETIONS:
        fail(f"{label} completion is unsupported")
    sample_size = _nonnegative_integer(
        observation.get("sample_size"), label=f"{label}.sample_size"
    )
    metrics = _validate_metrics(observation.get("metrics"), label=f"{label}.metrics")
    artifact_ids = _sorted_unique_sha256(
        observation.get("evidence_artifact_ids"),
        label=f"{label}.evidence_artifact_ids",
    )
    if not artifact_ids or not set(artifact_ids).issubset(run_artifact_ids):
        fail(f"{label} evidence artifacts are missing from the run")
    reason = _nonempty_string(observation.get("reason"), label=f"{label}.reason")
    if completion == "complete":
        if sample_size < criterion["sample_size"]:
            fail(f"{label} complete sample is smaller than the frozen contract")
        if reason != "completed":
            fail(f"{label} complete observation reason must be completed")
        metric_keys = {(item["metric"], item["unit"]) for item in metrics}
        required_keys = {
            (threshold["metric"], threshold["unit"])
            for threshold in criterion["thresholds"]
        }
        if not required_keys.issubset(metric_keys):
            fail(f"{label} lacks a metric required by the frozen thresholds")
    elif reason == "completed":
        fail(f"{label} inconclusive observation requires an explanatory reason")
    _validate_contract_requirement_observations(
        observation.get("contract_requirements"),
        contract=contract,
        criterion=criterion,
        criterion_metrics=metrics,
        run_artifact_ids=run_artifact_ids,
    )
    return observation


def _canonicalize_observation(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    metrics = result.get("metrics")
    if isinstance(metrics, list):
        metrics.sort(key=lambda item: (str(item.get("metric", "")), str(item.get("unit", ""))))
    artifact_ids = result.get("evidence_artifact_ids")
    if isinstance(artifact_ids, list):
        result["evidence_artifact_ids"] = sorted(artifact_ids)
    requirements = result.get("contract_requirements")
    if isinstance(requirements, dict):
        context = requirements.get("context")
        if isinstance(context, dict):
            depths = context.get("depths")
            if isinstance(depths, list):
                context["depths"] = sorted(
                    depths, key=lambda item: Decimal(str(item))
                )
            context_artifacts = context.get("evidence_artifact_ids")
            if isinstance(context_artifacts, list):
                context["evidence_artifact_ids"] = sorted(context_artifacts)
        soak = requirements.get("soak")
        if isinstance(soak, dict):
            soak_artifacts = soak.get("evidence_artifact_ids")
            if isinstance(soak_artifacts, list):
                soak["evidence_artifact_ids"] = sorted(soak_artifacts)
        relative = requirements.get("relative_performance")
        if isinstance(relative, dict):
            relative_metrics = relative.get("metrics")
            if isinstance(relative_metrics, list):
                relative_metrics.sort(
                    key=lambda item: (
                        str(item.get("metric", "")),
                        str(item.get("unit", "")),
                    )
                )
            relative_artifacts = relative.get("evidence_artifact_ids")
            if isinstance(relative_artifacts, list):
                relative["evidence_artifact_ids"] = sorted(relative_artifacts)
    return result


def validation_run_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "run_record_id"}


def validation_run_record_id(record: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(validation_run_record_identity(record))


def build_validation_run_record(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    attempt: dict[str, Any],
    preparation_provenance: dict[str, Any],
    observed_environment: dict[str, Any],
    commands: list[dict[str, Any]],
    criterion_observations: list[dict[str, Any]],
    evidence_artifacts: list[dict[str, Any]],
    evidence_artifact_ids: list[str],
) -> dict[str, Any]:
    """Build one immutable attempt record; every invocation needs a new ID."""
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    artifacts = sorted(
        (copy.deepcopy(item) for item in evidence_artifacts),
        key=lambda item: str(item.get("artifact_id", "")),
    )
    _artifact_registry(artifacts)
    provenance = copy.deepcopy(preparation_provenance)
    subsystems = provenance.get("subsystems")
    if isinstance(subsystems, list):
        subsystems.sort(
            key=lambda item: (
                str(item.get("name", "")),
                str(item.get("version", "")),
                str(item.get("maturity", "")),
            )
        )
    runtime_sources = provenance.get("runtime_sources")
    if isinstance(runtime_sources, list):
        runtime_sources.sort(key=lambda item: int(item.get("rank", -1)))
    normalized_commands = copy.deepcopy(commands)
    for command in normalized_commands:
        names = command.get("environment_variable_names")
        if isinstance(names, list):
            command["environment_variable_names"] = sorted(names)
    observations = [_canonicalize_observation(item) for item in criterion_observations]
    observations.sort(key=lambda item: str(item.get("criterion_id", "")))
    record: dict[str, Any] = {
        "schema_version": VALIDATION_RUN_RECORD_SCHEMA_VERSION,
        "kind": VALIDATION_RUN_RECORD_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "attempt": copy.deepcopy(attempt),
        "preparation_provenance": provenance,
        "observed_environment": copy.deepcopy(observed_environment),
        "commands": normalized_commands,
        "criterion_observations": observations,
        "evidence_artifact_ids": sorted(evidence_artifact_ids),
    }
    record["run_record_id"] = validation_run_record_id(record)
    return validate_validation_run_record(
        record,
        release=release,
        contract=contract,
        evidence_artifacts=artifacts,
    )


def validate_validation_run_record(
    value: Any,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    evidence_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    artifacts = _artifact_registry(evidence_artifacts)
    record = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "release_id",
            "contract_id",
            "attempt",
            "preparation_provenance",
            "observed_environment",
            "commands",
            "criterion_observations",
            "evidence_artifact_ids",
            "run_record_id",
        },
        label="validation run record",
    )
    if record.get("schema_version") != VALIDATION_RUN_RECORD_SCHEMA_VERSION:
        fail("validation run record schema_version is unsupported")
    if record.get("kind") != VALIDATION_RUN_RECORD_KIND:
        fail("validation run record kind is invalid")
    if record.get("release_id") != release["release_id"]:
        fail("validation run record release cross-link mismatch")
    if record.get("contract_id") != contract["contract_id"]:
        fail("validation run record contract cross-link mismatch")
    attempt = _require_fields(
        record.get("attempt"),
        {
            "attempt_id",
            "phase",
            "qualification_scope",
            "started_at",
            "ended_at",
            "completion",
        },
        label="validation run attempt",
    )
    _safe_identifier(attempt.get("attempt_id"), label="validation run attempt_id")
    phase = attempt.get("phase")
    if phase not in RUN_PHASE_SCOPES:
        fail("validation run phase is unsupported")
    if attempt.get("qualification_scope") != RUN_PHASE_SCOPES[phase]:
        fail("validation run phase and qualification_scope disagree")
    started_at = _parse_rfc3339_utc(
        attempt.get("started_at"), label="validation run started_at"
    )
    ended_at = _parse_rfc3339_utc(
        attempt.get("ended_at"), label="validation run ended_at"
    )
    if ended_at < started_at:
        fail("validation run ended_at precedes started_at")
    if attempt.get("completion") not in ATTEMPT_COMPLETIONS:
        fail("validation run completion is unsupported")
    provenance = _validate_preparation_provenance(
        record.get("preparation_provenance"), release=release
    )
    barrier_state = provenance["qualification_barrier"]
    if barrier_state == "not-reached":
        if phase != "preparation":
            fail("pre-qualification failure must use the preparation phase")
        if attempt.get("completion") == "completed":
            fail("not-reached qualification barrier cannot report completed")
    _validate_observed_environment(
        record.get("observed_environment"),
        release=release,
        barrier_state=barrier_state,
    )
    commands = record.get("commands")
    if not isinstance(commands, list) or not commands:
        fail("validation run commands must be a non-empty list")
    for index, command in enumerate(commands):
        _validate_command(command, index=index)
    run_artifact_ids = _sorted_unique_sha256(
        record.get("evidence_artifact_ids"), label="validation run evidence_artifact_ids"
    )
    if not run_artifact_ids or not set(run_artifact_ids).issubset(artifacts):
        fail("validation run references unavailable evidence artifacts")
    for artifact_id_value in run_artifact_ids:
        if (
            artifacts[artifact_id_value]["qualification_scope"]
            != attempt["qualification_scope"]
        ):
            fail("validation run evidence artifact scope differs from attempt scope")
    observations = record.get("criterion_observations")
    if not isinstance(observations, list):
        fail("validation run criterion_observations must be a list")
    if barrier_state == "not-reached" and observations:
        fail("pre-qualification failure cannot contain qualification observations")
    criteria = _criterion_map(contract)
    observation_ids: list[str] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            fail("validation run criterion observation must be an object")
        criterion_id = observation.get("criterion_id")
        criterion = criteria.get(criterion_id)
        if criterion is None:
            fail("validation run references an unknown criterion")
        if criterion["qualification_scope"] != attempt["qualification_scope"]:
            fail("validation run criterion scope differs from attempt scope")
        _validate_criterion_observation(
            observation,
            index=index,
            contract=contract,
            criterion=criterion,
            run_artifact_ids=set(run_artifact_ids),
        )
        observation_ids.append(criterion_id)
    if observation_ids != sorted(observation_ids) or len(observation_ids) != len(
        set(observation_ids)
    ):
        fail("validation run criterion_observations must be sorted and unique")
    run_record_id_value = record.get("run_record_id")
    if (
        not isinstance(run_record_id_value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(run_record_id_value) is None
        or run_record_id_value != validation_run_record_id(record)
    ):
        fail("validation run record identity mismatch")
    return record


def _criterion_coverage(
    contract: dict[str, Any], run_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    coverage: dict[str, list[str]] = {
        criterion_id: [] for criterion_id in sorted(_criterion_map(contract))
    }
    for record in run_records:
        for observation in record["criterion_observations"]:
            coverage[observation["criterion_id"]].append(record["run_record_id"])
    return [
        {"criterion_id": criterion_id, "run_record_ids": sorted(run_ids)}
        for criterion_id, run_ids in sorted(coverage.items())
    ]


def validation_evidence_bundle_identity(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bundle.items() if key != "bundle_id"}


def validation_evidence_bundle_id(bundle: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(
        validation_evidence_bundle_identity(bundle)
    )


def build_validation_evidence_bundle(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    run_records: list[dict[str, Any]],
    evidence_artifacts: list[dict[str, Any]],
    review_evidence_artifact_ids: list[str],
) -> dict[str, Any]:
    """Bind immutable attempt IDs and artifact descriptors for review."""
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    artifacts = sorted(
        (copy.deepcopy(item) for item in evidence_artifacts),
        key=lambda item: str(item.get("artifact_id", "")),
    )
    records = sorted(
        (copy.deepcopy(item) for item in run_records),
        key=lambda item: str(item.get("run_record_id", "")),
    )
    bundle: dict[str, Any] = {
        "schema_version": VALIDATION_EVIDENCE_BUNDLE_SCHEMA_VERSION,
        "kind": VALIDATION_EVIDENCE_BUNDLE_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "run_record_ids": [item["run_record_id"] for item in records],
        "evidence_artifacts": artifacts,
        "review_evidence_artifact_ids": sorted(review_evidence_artifact_ids),
        "qualification_started": any(
            item["preparation_provenance"]["qualification_barrier"] == "passed"
            for item in records
        ),
        "criterion_coverage": _criterion_coverage(contract, records),
    }
    bundle["bundle_id"] = validation_evidence_bundle_id(bundle)
    return validate_validation_evidence_bundle(
        bundle,
        release=release,
        contract=contract,
        run_records=records,
    )


def validate_validation_evidence_bundle(
    value: Any,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    run_records: list[dict[str, Any]],
) -> dict[str, Any]:
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    bundle = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "release_id",
            "contract_id",
            "run_record_ids",
            "evidence_artifacts",
            "review_evidence_artifact_ids",
            "qualification_started",
            "criterion_coverage",
            "bundle_id",
        },
        label="validation evidence bundle",
    )
    if bundle.get("schema_version") != VALIDATION_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        fail("validation evidence bundle schema_version is unsupported")
    if bundle.get("kind") != VALIDATION_EVIDENCE_BUNDLE_KIND:
        fail("validation evidence bundle kind is invalid")
    if bundle.get("release_id") != release["release_id"]:
        fail("validation evidence bundle release cross-link mismatch")
    if bundle.get("contract_id") != contract["contract_id"]:
        fail("validation evidence bundle contract cross-link mismatch")
    artifacts = _artifact_registry(bundle.get("evidence_artifacts"))
    record_ids = _sorted_unique_sha256(
        bundle.get("run_record_ids"), label="validation evidence bundle run_record_ids"
    )
    if not record_ids:
        fail("validation evidence bundle must reference at least one run record")
    if not isinstance(run_records, list):
        fail("validation evidence bundle run records must be supplied")
    supplied_records = sorted(run_records, key=lambda item: str(item.get("run_record_id", "")))
    supplied_ids = [item.get("run_record_id") for item in supplied_records]
    if supplied_ids != record_ids:
        fail("validation evidence bundle run record set mismatch")
    used_artifact_ids: set[str] = set()
    attempt_ids: list[str] = []
    for record in supplied_records:
        validate_validation_run_record(
            record,
            release=release,
            contract=contract,
            evidence_artifacts=list(artifacts.values()),
        )
        attempt_ids.append(record["attempt"]["attempt_id"])
        used_artifact_ids.update(record["evidence_artifact_ids"])
    if len(attempt_ids) != len(set(attempt_ids)):
        fail("validation evidence bundle attempt IDs must be unique")
    review_artifact_ids = _sorted_unique_sha256(
        bundle.get("review_evidence_artifact_ids"),
        label="validation evidence bundle review_evidence_artifact_ids",
    )
    if not set(review_artifact_ids).issubset(artifacts):
        fail("validation evidence bundle review artifact is unavailable")
    used_artifact_ids.update(review_artifact_ids)
    if used_artifact_ids != set(artifacts):
        fail("validation evidence bundle contains unreferenced evidence artifacts")
    qualification_started = any(
        item["preparation_provenance"]["qualification_barrier"] == "passed"
        for item in supplied_records
    )
    if bundle.get("qualification_started") is not qualification_started:
        fail("validation evidence bundle qualification_started mismatch")
    expected_coverage = _criterion_coverage(contract, supplied_records)
    if bundle.get("criterion_coverage") != expected_coverage:
        fail("validation evidence bundle criterion coverage mismatch")
    bundle_id_value = bundle.get("bundle_id")
    if (
        not isinstance(bundle_id_value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(bundle_id_value) is None
        or bundle_id_value != validation_evidence_bundle_id(bundle)
    ):
        fail("validation evidence bundle identity mismatch")
    return bundle


def build_provenance_security_review(
    *,
    artifact_identity: str,
    runtime_identity: str,
    contract_frozen_before_testing: str,
    evidence_privacy: str,
    security: str,
    evidence_artifact_ids: list[str],
) -> dict[str, Any]:
    review = {
        "artifact_identity": artifact_identity,
        "runtime_identity": runtime_identity,
        "contract_frozen_before_testing": contract_frozen_before_testing,
        "evidence_privacy": evidence_privacy,
        "security": security,
        "evidence_artifact_ids": sorted(evidence_artifact_ids),
    }
    return review


def _privacy_disposition(artifacts: dict[str, dict[str, Any]]) -> str:
    values = {item["privacy_review"] for item in artifacts.values()}
    if "failed" in values:
        return "fail"
    if values and values == {"passed"}:
        return "pass"
    return "pending"


def _validate_provenance_security_review(
    value: Any,
    *,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    review = _require_fields(
        value,
        set(PROVENANCE_REVIEW_COMPONENTS) | {"evidence_artifact_ids"},
        label="provenance/security review",
    )
    for component in PROVENANCE_REVIEW_COMPONENTS:
        if review.get(component) not in REVIEW_COMPONENT_RESULTS:
            fail(f"provenance/security review {component} is unsupported")
    artifacts = {item["artifact_id"]: item for item in bundle["evidence_artifacts"]}
    if review.get("evidence_privacy") != _privacy_disposition(artifacts):
        fail("provenance/security evidence_privacy disagrees with artifact reviews")
    artifact_ids = _sorted_unique_sha256(
        review.get("evidence_artifact_ids"),
        label="provenance/security review evidence_artifact_ids",
    )
    if not artifact_ids:
        fail("provenance/security review must cite evidence")
    if artifact_ids != bundle["review_evidence_artifact_ids"]:
        fail("provenance/security review must cover every bundle review artifact")
    if any(
        artifacts[artifact_id]["qualification_scope"] != "release-promotion"
        for artifact_id in artifact_ids
    ):
        fail("provenance/security review evidence must use release-promotion scope")
    return review


def _provenance_disposition(review: dict[str, Any]) -> str:
    values = [review[component] for component in PROVENANCE_REVIEW_COMPONENTS]
    if "fail" in values:
        return "fail"
    if values and all(value == "pass" for value in values):
        return "pass"
    return "not-evaluated"


def _threshold_passes(observed: str, threshold: dict[str, Any]) -> bool:
    expected = threshold["value"]
    operator = threshold["operator"]
    try:
        observed_decimal = Decimal(observed)
        expected_decimal = Decimal(expected)
        numeric = observed_decimal.is_finite() and expected_decimal.is_finite()
    except InvalidOperation:
        numeric = False
    if operator == "eq":
        if numeric:
            return observed_decimal == expected_decimal
        return observed == expected
    if not numeric:
        fail("ordered validation threshold requires numeric observed and expected values")
    if operator == "gt":
        return observed_decimal > expected_decimal
    if operator == "gte":
        return observed_decimal >= expected_decimal
    if operator == "lt":
        return observed_decimal < expected_decimal
    if operator == "lte":
        return observed_decimal <= expected_decimal
    fail("validation threshold operator is unsupported")


def _evaluate_context_requirement(
    observation: dict[str, Any] | None,
    requirement: dict[str, Any] | None,
) -> tuple[str, str]:
    if requirement is None:
        return "pass", "context-not-required"
    if observation is None:
        return "not-evaluated", "context-evidence-missing"
    if observation["completion"] == "inconclusive":
        return "inconclusive", "context-evidence-inconclusive"
    if observation["minimum_tokens"] < requirement["minimum_tokens"]:
        return "fail", "context-minimum-not-satisfied"
    if not set(requirement["depths"]).issubset(observation["depths"]):
        return "fail", "context-depths-not-satisfied"
    return "pass", "context-requirement-satisfied"


def _evaluate_soak_requirement(
    observation: dict[str, Any] | None,
    requirement: dict[str, Any] | None,
) -> tuple[str, str]:
    if requirement is None:
        return "pass", "soak-not-required"
    if observation is None:
        return "not-evaluated", "soak-evidence-missing"
    if observation["completion"] == "inconclusive":
        return "inconclusive", "soak-evidence-inconclusive"
    if Decimal(observation["duration_seconds"]) < requirement[
        "minimum_duration_seconds"
    ]:
        return "fail", "soak-duration-not-satisfied"
    if observation["concurrency"] != requirement["concurrency"]:
        return "fail", "soak-concurrency-not-satisfied"
    if observation["request_errors"] > requirement["maximum_request_errors"]:
        return "fail", "soak-error-budget-not-satisfied"
    return "pass", "soak-requirement-satisfied"


def _evaluate_relative_performance(
    observation: dict[str, Any] | None,
    requirement: dict[str, Any] | None,
    *,
    criterion: dict[str, Any],
    current_metrics: dict[tuple[str, str], str],
) -> tuple[str, str]:
    if requirement is None:
        return "pass", "relative-performance-not-required"
    if observation is None:
        return "not-evaluated", "relative-performance-evidence-missing"
    if observation["completion"] == "inconclusive":
        return "inconclusive", "relative-performance-evidence-inconclusive"
    predecessor_metrics = {
        (item["metric"], item["unit"]): Decimal(item["value"])
        for item in observation["metrics"]
    }
    maximum_regression = Decimal(requirement["maximum_regression_percent"])
    for threshold in criterion["thresholds"]:
        key = (threshold["metric"], threshold["unit"])
        predecessor = predecessor_metrics[key]
        current = Decimal(current_metrics[key])
        if criterion["dimension"] == "throughput":
            regression = (predecessor - current) * Decimal("100") / predecessor
        else:
            regression = (current - predecessor) * Decimal("100") / predecessor
        if regression > maximum_regression:
            return "fail", "relative-regression-budget-exceeded"
    return "pass", "relative-regression-budget-satisfied"


def evaluate_criterion_observation(
    observation: dict[str, Any],
    criterion: dict[str, Any],
    contract: dict[str, Any],
) -> tuple[str, str]:
    """Derive a disposition and reason from every frozen requirement."""
    if observation["completion"] == "inconclusive":
        return "inconclusive", "criterion-evidence-inconclusive"
    metrics = {
        (item["metric"], item["unit"]): item["value"]
        for item in observation["metrics"]
    }
    passes = all(
        _threshold_passes(
            metrics[(threshold["metric"], threshold["unit"])], threshold
        )
        for threshold in criterion["thresholds"]
    )
    if not passes:
        return "fail", "threshold-not-satisfied"
    criterion_id = criterion["criterion_id"]
    requirements = observation["contract_requirements"]
    supplemental = [
        _evaluate_context_requirement(
            requirements["context"],
            _context_requirement_for_criterion(contract, criterion_id),
        ),
        _evaluate_soak_requirement(
            requirements["soak"],
            _soak_requirement_for_criterion(contract, criterion_id),
        ),
        _evaluate_relative_performance(
            requirements["relative_performance"],
            _relative_requirement_for_criterion(contract, criterion),
            criterion=criterion,
            current_metrics=metrics,
        ),
    ]
    for disposition in ("fail", "inconclusive", "not-evaluated"):
        for observed_disposition, reason in supplemental:
            if observed_disposition == disposition:
                return disposition, reason
    return "pass", "thresholds-and-contract-requirements-satisfied"


def _observation_for(record: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    for observation in record["criterion_observations"]:
        if observation["criterion_id"] == criterion_id:
            return observation
    fail("validation decision selects a run without the named criterion")


def _criterion_result(
    *,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    selected_run_ids: list[str],
    records: dict[str, dict[str, Any]],
    provenance_review: dict[str, Any],
) -> dict[str, Any]:
    criterion_id = criterion["criterion_id"]
    if criterion["dimension"] == "provenance-security":
        if selected_run_ids:
            fail("provenance/security criterion is decided by reviewed disposition")
        disposition = _provenance_disposition(provenance_review)
        reason = {
            "pass": "review-passed",
            "fail": "review-failed",
            "not-evaluated": "review-pending",
        }[disposition]
        return {
            "criterion_id": criterion_id,
            "disposition": disposition,
            "run_record_ids": [],
            "reason": reason,
        }
    if not selected_run_ids:
        return {
            "criterion_id": criterion_id,
            "disposition": "not-evaluated",
            "run_record_ids": [],
            "reason": "no-selected-evidence",
        }
    outcomes: list[tuple[str, str]] = []
    selected_records: list[dict[str, Any]] = []
    for run_id in selected_run_ids:
        record = records.get(run_id)
        if record is None:
            fail("validation decision selects a run outside the evidence bundle")
        selected_records.append(record)
        outcome, reason = evaluate_criterion_observation(
            _observation_for(record, criterion_id), criterion, contract
        )
        if outcome == "pass" and record["attempt"]["completion"] != "completed":
            outcome = "inconclusive"
            reason = "attempt-not-completed"
        outcomes.append((outcome, reason))
    dispositions = [item[0] for item in outcomes]
    if criterion["dimension"] == "strict-same-boot" and set(dispositions) == {
        "pass"
    }:
        boot_ids = {
            record["observed_environment"]["server_boot_id"]
            for record in selected_records
        }
        launch_ids = {
            record["observed_environment"]["launch_id"]
            for record in selected_records
        }
        if len(boot_ids) != 1 or len(launch_ids) != 1:
            fail("strict same-boot evidence spans more than one live server boot")
    for disposition in ("fail", "inconclusive", "not-evaluated", "pass"):
        matching_reasons = [
            observed_reason
            for observed_disposition, observed_reason in outcomes
            if observed_disposition == disposition
        ]
        if matching_reasons:
            reason = matching_reasons[0]
            break
    return {
        "criterion_id": criterion_id,
        "disposition": disposition,
        "run_record_ids": selected_run_ids,
        "reason": reason,
    }


def _derive_criterion_results(
    *,
    contract: dict[str, Any],
    bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    criterion_run_record_ids: dict[str, list[str]],
    provenance_review: dict[str, Any],
) -> list[dict[str, Any]]:
    criteria = _criterion_map(contract)
    unknown = set(criterion_run_record_ids) - set(criteria)
    if unknown:
        fail("validation decision selects an unknown criterion")
    bundle_ids = set(bundle["run_record_ids"])
    records = {item["run_record_id"]: item for item in run_records}
    results: list[dict[str, Any]] = []
    for criterion_id, criterion in sorted(criteria.items()):
        selected = sorted(criterion_run_record_ids.get(criterion_id, []))
        if len(selected) != len(set(selected)):
            fail("validation decision run selections must be unique")
        for run_id in selected:
            _sha256(run_id, label="validation decision selected run ID")
        if not set(selected).issubset(bundle_ids):
            fail("validation decision selects a run outside the evidence bundle")
        results.append(
            _criterion_result(
                contract=contract,
                criterion=criterion,
                selected_run_ids=selected,
                records=records,
                provenance_review=provenance_review,
            )
        )
    return results


def derive_validation_status(
    *,
    qualification_started: bool,
    criterion_results: list[dict[str, Any]],
) -> str:
    """Derive the only permissible base status from reviewed evidence."""
    if not qualification_started:
        return "untested"
    dispositions = {item["disposition"] for item in criterion_results}
    if "fail" in dispositions:
        return "tested-criteria-not-met"
    if "inconclusive" in dispositions:
        return "tested-inconclusive"
    if dispositions == {"pass"}:
        return "validated"
    return "testing-incomplete"


def _validate_decision_review(value: Any) -> dict[str, Any]:
    review = _require_fields(
        value,
        {"authority", "reviewer", "reviewed_at", "review_reference"},
        label="validation decision review",
    )
    if review.get("authority") != "repository-maintainer-review":
        fail("validation decision authority must be repository-maintainer-review")
    _nonempty_string(review.get("reviewer"), label="validation decision reviewer")
    _parse_rfc3339_utc(
        review.get("reviewed_at"), label="validation decision reviewed_at"
    )
    _nonempty_string(
        review.get("review_reference"), label="validation decision review_reference"
    )
    return review


def validation_decision_identity(decision: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in decision.items() if key != "decision_id"}


def validation_decision_id(decision: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(validation_decision_identity(decision))


def _validate_decision_shape_and_identity(value: Any) -> dict[str, Any]:
    decision = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "release_id",
            "contract_id",
            "evidence_bundle_id",
            "status",
            "criterion_results",
            "provenance_security_review",
            "review",
            "supersedes_decision_ids",
            "decision_id",
        },
        label="validation decision",
    )
    if decision.get("schema_version") != VALIDATION_DECISION_SCHEMA_VERSION:
        fail("validation decision schema_version is unsupported")
    if decision.get("kind") != VALIDATION_DECISION_KIND:
        fail("validation decision kind is invalid")
    for field in ("release_id", "contract_id", "evidence_bundle_id"):
        _sha256(decision.get(field), label=f"validation decision {field}")
    if decision.get("status") not in BASE_VALIDATION_STATUSES:
        fail("validation decision stores an unsupported base status")
    results = decision.get("criterion_results")
    if not isinstance(results, list) or not results:
        fail("validation decision criterion_results must be non-empty")
    ids: list[str] = []
    for index, value_item in enumerate(results):
        result = _require_fields(
            value_item,
            {"criterion_id", "disposition", "run_record_ids", "reason"},
            label=f"validation decision criterion_results[{index}]",
        )
        criterion_id = _safe_identifier(
            result.get("criterion_id"),
            label=f"validation decision criterion_results[{index}].criterion_id",
        )
        if result.get("disposition") not in CRITERION_DISPOSITIONS:
            fail("validation decision criterion disposition is unsupported")
        _sorted_unique_sha256(
            result.get("run_record_ids"),
            label=f"validation decision criterion_results[{index}].run_record_ids",
        )
        _nonempty_string(
            result.get("reason"),
            label=f"validation decision criterion_results[{index}].reason",
        )
        ids.append(criterion_id)
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        fail("validation decision criterion_results must be sorted and unique")
    review = _require_fields(
        decision.get("provenance_security_review"),
        set(PROVENANCE_REVIEW_COMPONENTS) | {"evidence_artifact_ids"},
        label="provenance/security review",
    )
    for component in PROVENANCE_REVIEW_COMPONENTS:
        if review.get(component) not in REVIEW_COMPONENT_RESULTS:
            fail(f"provenance/security review {component} is unsupported")
    _sorted_unique_sha256(
        review.get("evidence_artifact_ids"),
        label="provenance/security review evidence_artifact_ids",
    )
    _validate_decision_review(decision.get("review"))
    supersedes = _sorted_unique_sha256(
        decision.get("supersedes_decision_ids"),
        label="validation decision supersedes_decision_ids",
    )
    decision_id_value = decision.get("decision_id")
    if (
        not isinstance(decision_id_value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(decision_id_value) is None
        or decision_id_value != validation_decision_id(decision)
    ):
        fail("validation decision identity mismatch")
    if decision_id_value in supersedes:
        fail("validation decision cannot supersede itself")
    return decision


def build_validation_decision(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    evidence_bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    criterion_run_record_ids: dict[str, list[str]],
    provenance_security_review: dict[str, Any],
    status: str,
    reviewer: str,
    reviewed_at: str,
    review_reference: str,
    supersedes_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a decision candidate whose explicit status must match evidence."""
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    bundle = validate_validation_evidence_bundle(
        evidence_bundle,
        release=release,
        contract=contract,
        run_records=run_records,
    )
    provenance = copy.deepcopy(provenance_security_review)
    _validate_provenance_security_review(provenance, bundle=bundle)
    results = _derive_criterion_results(
        contract=contract,
        bundle=bundle,
        run_records=run_records,
        criterion_run_record_ids=criterion_run_record_ids,
        provenance_review=provenance,
    )
    derived_status = derive_validation_status(
        qualification_started=bundle["qualification_started"],
        criterion_results=results,
    )
    if status != derived_status:
        fail(
            "validation decision status disagrees with evidence "
            f"(expected {derived_status})"
        )
    prior_decisions = supersedes_decisions or []
    prior_ids: list[str] = []
    for prior in prior_decisions:
        prior_ids.append(_validate_decision_shape_and_identity(prior)["decision_id"])
    if len(prior_ids) != len(set(prior_ids)):
        fail("validation decision supersession inputs must be unique")
    decision: dict[str, Any] = {
        "schema_version": VALIDATION_DECISION_SCHEMA_VERSION,
        "kind": VALIDATION_DECISION_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "evidence_bundle_id": bundle["bundle_id"],
        "status": status,
        "criterion_results": results,
        "provenance_security_review": provenance,
        "review": {
            "authority": "repository-maintainer-review",
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "review_reference": review_reference,
        },
        "supersedes_decision_ids": sorted(prior_ids),
    }
    decision["decision_id"] = validation_decision_id(decision)
    return validate_validation_decision(
        decision,
        release=release,
        contract=contract,
        evidence_bundle=bundle,
        run_records=run_records,
        prior_decisions=prior_decisions,
    )


def validate_validation_decision(
    value: Any,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    evidence_bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    prior_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    bundle = validate_validation_evidence_bundle(
        evidence_bundle,
        release=release,
        contract=contract,
        run_records=run_records,
    )
    decision = _validate_decision_shape_and_identity(value)
    if decision["release_id"] != release["release_id"]:
        fail("validation decision release cross-link mismatch")
    if decision["contract_id"] != contract["contract_id"]:
        fail("validation decision contract cross-link mismatch")
    if decision["evidence_bundle_id"] != bundle["bundle_id"]:
        fail("validation decision evidence bundle cross-link mismatch")
    provenance = _validate_provenance_security_review(
        decision["provenance_security_review"], bundle=bundle
    )
    selections = {
        item["criterion_id"]: item["run_record_ids"]
        for item in decision["criterion_results"]
    }
    expected_results = _derive_criterion_results(
        contract=contract,
        bundle=bundle,
        run_records=run_records,
        criterion_run_record_ids=selections,
        provenance_review=provenance,
    )
    if decision["criterion_results"] != expected_results:
        fail("validation decision criterion results disagree with evidence")
    expected_status = derive_validation_status(
        qualification_started=bundle["qualification_started"],
        criterion_results=expected_results,
    )
    if decision["status"] != expected_status:
        fail("validation decision status disagrees with evidence")
    reviewed_at = _parse_rfc3339_utc(
        decision["review"]["reviewed_at"],
        label="validation decision reviewed_at",
    )
    latest_run_end = max(
        _parse_rfc3339_utc(
            record["attempt"]["ended_at"], label="validation run ended_at"
        )
        for record in run_records
    )
    if reviewed_at < latest_run_end:
        fail("validation decision review predates its evidence")
    supplied_prior = prior_decisions or []
    prior_ids = sorted(
        _validate_decision_shape_and_identity(item)["decision_id"]
        for item in supplied_prior
    )
    if prior_ids != decision["supersedes_decision_ids"]:
        fail("validation decision supersession cross-link mismatch")
    if supplied_prior:
        latest_prior_review = max(
            _parse_rfc3339_utc(
                prior["review"]["reviewed_at"],
                label="prior validation decision reviewed_at",
            )
            for prior in supplied_prior
        )
        if reviewed_at <= latest_prior_review:
            fail("superseding validation decision review must be later")
    return decision


def effective_validation_status(
    decision: dict[str, Any], *, later_decisions: list[dict[str, Any]]
) -> str:
    """Project Superseded without mutating the earlier reviewed outcome."""
    current = _validate_decision_shape_and_identity(decision)
    superseders = [
        _validate_decision_shape_and_identity(item)
        for item in later_decisions
        if current["decision_id"]
        in _validate_decision_shape_and_identity(item)["supersedes_decision_ids"]
    ]
    if len(superseders) > 1:
        fail("more than one later decision directly supersedes the same decision")
    return "superseded" if superseders else current["status"]


def validation_status_label(status: str) -> str:
    if status not in EFFECTIVE_VALIDATION_STATUSES:
        fail("validation status is unsupported")
    return STATUS_LABELS[status]
