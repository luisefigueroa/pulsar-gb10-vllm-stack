#!/usr/bin/env python3
"""Immutable validation evidence and decision schemas for ADR 0004.

This module implements the second machine-readable stage of Model Serving
Release validation.  It is deliberately pure: it performs no filesystem or
network I/O, captures no evidence itself, publishes no trusted artifact,
changes no profile, and launches nothing.  Repository review and
the separate read-only persistence/inspection layer remain distinct
authority boundaries.  Caller-supplied predecessor and decision registries
are validation input, not trusted persistence.  Review-metadata shape checks
cannot prove that review occurred.
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
    "working-copy",
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
REVIEW_REFERENCE_RE = re.compile(
    r"^(?:"
    r"pr:[1-9][0-9]{0,6}"
    r"|commit:[0-9a-f]{40}(?:[0-9a-f]{24})?"
    r"|repository-review:[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
    r")$"
)
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
COMMAND_ENVIRONMENT_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"(?i)hf_[A-Za-z0-9]{30,64}(?![A-Za-z0-9_])"),
    re.compile(
        r"(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
)
SENSITIVE_ENV_MARKERS = {
    "APIKEY",
    "AUTH",
    "AUTHORIZATION",
    "BEARER",
    "COOKIE",
    "CREDENTIAL",
    "CREDENTIALS",
    "HEADER",
    "PASSWORD",
    "PASSPHRASE",
    "SECRET",
    "TOKEN",
}
COMMAND_PROGRAM_OPERATIONS = {
    "bench/membw.py": {"measure-memory-bandwidth"},
    "scripts/model-library.sh": {"prepare-model-for-serving"},
    "validate/analyze_trace.py": {"analyze-trace"},
    "validate/bench_serve.py": {"benchmark-serving"},
    "validate/compare_captures.py": {"compare-captures"},
    "validate/greedy_capture.py": {"capture-determinism"},
    "validate/hf_reference.py": {"capture-reference"},
    "validate/needle.py": {"validate-context"},
    "validate/run-gates.sh": {"run-validation-gates"},
    "validate/soak.py": {"validate-soak"},
    "validate/warmup.py": {"warmup-serving"},
}
COMMAND_ARGUMENT_KINDS = {
    "operation",
    "criterion-reference",
    "repository-path",
    "site-option",
}
COMMAND_REPOSITORY_PATHS = {"validate/prompts.txt"}
COMMAND_SITE_OPTION_REFERENCE_KINDS = {
    "--host": {"protected-site-reference", "rank-reference"},
    "--rank": {"rank-reference"},
    "--url": {"protected-site-reference"},
}
COMMAND_ENVIRONMENT_KINDS = {
    "non-secret-reference",
    "secret-reference",
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


def _elapsed_seconds(started_at: datetime, ended_at: datetime) -> Decimal:
    """Return an exact decimal duration without float conversion."""
    delta = ended_at - started_at
    microseconds = (
        (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    )
    return Decimal(microseconds) / Decimal(1_000_000)


def _canonical_elapsed_seconds(started_at: datetime, ended_at: datetime) -> str:
    normalized = format(_elapsed_seconds(started_at, ended_at), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _sorted_unique_safe_identifiers(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    for item in value:
        _safe_identifier(item, label=f"{label} item")
    if value != sorted(value) or len(value) != len(set(value)):
        fail(f"{label} must be sorted and unique")
    return value


def _numeric_version_in_range(
    observed_version: Any,
    version_range: Any,
    *,
    label: str,
) -> bool:
    try:
        return model_serving_release.numeric_version_in_range(
            observed_version,
            version_range,
            label=label,
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))


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
        fail("full verification before qualification requires one runtime source for every rank")
    access_contract = release["serving_recipe"]["model_access_contract"]
    source_names = {item["source"] for item in value}
    if access_contract == "live-remote-readonly":
        fail(
            "live-remote-readonly is retired as a serving access contract "
            "(ADR 0005); use local-verified-readonly"
        )
    if access_contract == "local-verified-readonly" and "live-mount" in source_names:
        fail("local verified release cannot use a live-mount runtime source")
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
        fail("full verification before qualification requires full artifact verification")
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
    compatibility = release["runtime_image_identity"]["host_compatibility"]
    ranks: list[int] = []
    engine_versions: set[str] = set()
    minimum_memory = Decimal(
        geometry["capacity"]["minimum_unified_memory_gib_per_node"]
    )
    for index, item in enumerate(value):
        rank = _require_fields(
            item,
            {
                "rank",
                "hardware_class",
                "architecture",
                "accelerator_count",
                "unified_memory_gib",
                "driver_abi",
                "container_runtime",
                "kernel",
                "engine_version",
            },
            label=f"observed environment ranks[{index}]",
        )
        rank_number = _nonnegative_integer(
            rank.get("rank"), label=f"observed environment ranks[{index}].rank"
        )
        if rank.get("hardware_class") != geometry["hardware_class"]:
            fail("observed environment hardware class differs from release")
        if rank.get("architecture") != compatibility["architecture"]:
            fail("observed environment architecture differs from release")
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
        driver = _require_fields(
            rank.get("driver_abi"),
            {"family", "version"},
            label=f"observed environment ranks[{index}].driver_abi",
        )
        expected_driver = compatibility["driver_abi"]
        if driver.get("family") != expected_driver["family"]:
            fail("observed environment driver ABI family differs from release")
        if not _numeric_version_in_range(
            driver.get("version"),
            expected_driver["range"],
            label=f"observed environment ranks[{index}].driver_abi.version",
        ):
            fail("observed environment driver ABI version is outside release range")
        runtime = _require_fields(
            rank.get("container_runtime"),
            {"family", "version", "capabilities"},
            label=f"observed environment ranks[{index}].container_runtime",
        )
        expected_runtime = compatibility["container_runtime"]
        if runtime.get("family") != expected_runtime["family"]:
            fail("observed environment container runtime family differs from release")
        if not _numeric_version_in_range(
            runtime.get("version"),
            expected_runtime["range"],
            label=f"observed environment ranks[{index}].container_runtime.version",
        ):
            fail("observed environment container runtime version is outside release range")
        capabilities = _sorted_unique_safe_identifiers(
            runtime.get("capabilities"),
            label=f"observed environment ranks[{index}].container_runtime.capabilities",
        )
        if not set(expected_runtime["required_capabilities"]).issubset(capabilities):
            fail("observed environment lacks a required container capability")
        kernel = _require_fields(
            rank.get("kernel"),
            {"version", "features"},
            label=f"observed environment ranks[{index}].kernel",
        )
        expected_kernel = compatibility["kernel"]
        if not _numeric_version_in_range(
            kernel.get("version"),
            expected_kernel["range"],
            label=f"observed environment ranks[{index}].kernel.version",
        ):
            fail("observed environment kernel version is outside release range")
        features = _sorted_unique_safe_identifiers(
            kernel.get("features"),
            label=f"observed environment ranks[{index}].kernel.features",
        )
        if not set(expected_kernel["required_features"]).issubset(features):
            fail("observed environment lacks a required kernel feature")
        engine_version = _safe_identifier(
            rank.get("engine_version"),
            label=f"observed environment ranks[{index}].engine_version",
        )
        engine_versions.add(engine_version)
        ranks.append(rank_number)
    if ranks != list(range(geometry["node_count"])):
        fail("observed environment must contain each release rank exactly once")
    if len(engine_versions) != 1:
        fail("observed environment engine version differs across ranks")
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
            "cluster",
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
    geometry = release["supported_hardware_geometry"]
    cluster = _require_fields(
        environment.get("cluster"),
        {
            "node_count",
            "accelerator_count",
            "tensor_parallel_size",
            "pipeline_parallel_size",
            "topology_class",
            "interconnect_class",
            "rails_per_pair",
        },
        label="observed environment cluster",
    )
    for field in (
        "node_count",
        "accelerator_count",
        "tensor_parallel_size",
        "pipeline_parallel_size",
    ):
        _positive_integer(cluster.get(field), label=f"observed environment cluster.{field}")
        if cluster.get(field) != geometry[field]:
            fail(f"observed environment cluster {field} differs from release")
    for field in ("topology_class", "interconnect_class"):
        _safe_identifier(
            cluster.get(field), label=f"observed environment cluster.{field}"
        )
        if cluster.get(field) != geometry[field]:
            fail(f"observed environment cluster {field} differs from release")
    observed_rails = _nonnegative_integer(
        cluster.get("rails_per_pair"),
        label="observed environment cluster.rails_per_pair",
    )
    if observed_rails < geometry["minimum_rails_per_pair"]:
        fail("observed environment cluster rails are below release requirement")
    if geometry["node_count"] == 1 and observed_rails != 0:
        fail("single-node observed environment must report zero rails")
    for field in ("server_boot_id", "launch_id"):
        identifier = environment.get(field)
        if identifier is not None:
            _sha256(identifier, label=f"observed environment {field}")
        if barrier_state == "passed" and identifier is None:
            fail(f"passed full verification before qualification requires observed {field}")
    _validate_rank_observations(environment.get("ranks"), release=release)
    return environment


def _normalized_sensitive_marker(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _contains_sensitive_marker(value: str) -> bool:
    normalized = _normalized_sensitive_marker(value)
    return any(marker in normalized for marker in SENSITIVE_ENV_MARKERS)


def _contains_command_environment_credential_value(value: str) -> bool:
    return any(
        pattern.search(value) is not None
        for pattern in COMMAND_ENVIRONMENT_CREDENTIAL_VALUE_PATTERNS
    )


def _validate_command_site_reference(
    value: Any,
    *,
    label: str,
    allowed_kinds: set[str],
    node_count: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be a structured site reference")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in allowed_kinds:
        fail(f"{label}.kind is incompatible with its site option")
    if kind == "rank-reference":
        descriptor = _require_fields(value, {"kind", "rank"}, label=label)
        rank = _nonnegative_integer(
            descriptor.get("rank"), label=f"{label}.rank"
        )
        if rank >= node_count:
            fail(f"{label}.rank is outside the release geometry")
        return descriptor
    descriptor = _require_fields(value, {"kind", "digest"}, label=label)
    _sha256(descriptor.get("digest"), label=f"{label}.digest", prefix=True)
    return descriptor


def _validate_command_argument(
    value: Any,
    *,
    command_index: int,
    index: int,
    program: str,
    criteria: dict[str, dict[str, Any]],
    attempted_criterion_ids: set[str],
    node_count: int,
) -> dict[str, Any]:
    label = f"run commands[{command_index}].arguments[{index}]"
    if not isinstance(value, dict):
        fail(f"{label} must be a structured argument descriptor")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in COMMAND_ARGUMENT_KINDS:
        fail(f"{label}.kind is unsupported")
    if kind == "operation":
        descriptor = _require_fields(value, {"kind", "value"}, label=label)
        operation = descriptor.get("value")
        if (
            not isinstance(operation, str)
            or operation not in COMMAND_PROGRAM_OPERATIONS[program]
        ):
            fail(f"{label}.value is not allowed for the selected program")
        return descriptor
    if kind == "criterion-reference":
        descriptor = _require_fields(value, {"kind", "criterion_id"}, label=label)
        criterion_id = descriptor.get("criterion_id")
        if not isinstance(criterion_id, str) or criterion_id not in criteria:
            fail(f"{label}.criterion_id is unknown")
        if criterion_id not in attempted_criterion_ids:
            fail(f"{label}.criterion_id was not declared by the attempt")
        return descriptor
    if kind == "repository-path":
        descriptor = _require_fields(value, {"kind", "value"}, label=label)
        path = _relative_repository_path(descriptor.get("value"), label=f"{label}.value")
        if path not in COMMAND_REPOSITORY_PATHS:
            fail("run command repository-path is not an allowed repository resource")
        return descriptor
    descriptor = _require_fields(
        value,
        {"kind", "option", "reference"},
        label=label,
    )
    option = descriptor.get("option")
    allowed_reference_kinds = (
        COMMAND_SITE_OPTION_REFERENCE_KINDS.get(option)
        if isinstance(option, str)
        else None
    )
    if allowed_reference_kinds is None:
        fail(f"{label}.option is unsupported")
    _validate_command_site_reference(
        descriptor.get("reference"),
        label=f"{label}.reference",
        allowed_kinds=allowed_reference_kinds,
        node_count=node_count,
    )
    return descriptor


def _validate_command_environment(value: Any, *, command_index: int) -> list[dict[str, Any]]:
    label = f"run commands[{command_index}].environment"
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    keys: list[str] = []
    for index, item in enumerate(value):
        descriptor = _require_fields(
            item, {"kind", "name"}, label=f"{label}[{index}]"
        )
        kind = descriptor.get("kind")
        if not isinstance(kind, str) or kind not in COMMAND_ENVIRONMENT_KINDS:
            fail(f"{label}[{index}].kind is unsupported")
        name = descriptor.get("name")
        if not isinstance(name, str):
            fail(f"{label}[{index}].name is invalid")
        if _contains_command_environment_credential_value(name):
            fail(f"{label}[{index}].name contains a credential value")
        if ENV_NAME_RE.fullmatch(name) is None:
            fail(f"{label}[{index}].name is invalid")
        sensitive = _contains_sensitive_marker(name)
        if sensitive != (kind == "secret-reference"):
            fail(f"{label}[{index}] secret classification is invalid")
        keys.append(name)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail(f"{label} must be sorted by unique environment-variable name")
    return value


def _validate_command(
    value: Any,
    *,
    index: int,
    criteria: dict[str, dict[str, Any]],
    attempted_criterion_ids: set[str],
    node_count: int,
) -> dict[str, Any]:
    command = _require_fields(
        value,
        {
            "program",
            "version",
            "arguments",
            "environment",
            "working_directory",
        },
        label=f"run commands[{index}]",
    )
    program = command.get("program")
    if not isinstance(program, str) or program not in COMMAND_PROGRAM_OPERATIONS:
        fail("run command program is not an allowed repository-owned executable")
    _sha256(
        command.get("version"),
        label=f"run commands[{index}].version",
        prefix=True,
    )
    arguments = command.get("arguments")
    if not isinstance(arguments, list):
        fail(f"run commands[{index}].arguments must be a list")
    operations = 0
    for argument_index, argument in enumerate(arguments):
        _validate_command_argument(
            argument,
            command_index=index,
            index=argument_index,
            program=program,
            criteria=criteria,
            attempted_criterion_ids=attempted_criterion_ids,
            node_count=node_count,
        )
        if isinstance(argument, dict) and argument.get("kind") == "operation":
            operations += 1
    if operations != 1:
        fail(f"run commands[{index}] must declare exactly one allowed operation")
    _validate_command_environment(command.get("environment"), command_index=index)
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
    attempt_started_at: datetime,
    attempt_ended_at: datetime,
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
            "started_at",
            "ended_at",
            "duration_seconds",
            "concurrency",
            "request_errors",
            "evidence_artifact_ids",
            "reason",
        },
        label=label,
    )
    completion = _validate_requirement_completion(observation, label=label)
    soak_started_at = _parse_rfc3339_utc(
        observation.get("started_at"), label=f"{label}.started_at"
    )
    soak_ended_at = _parse_rfc3339_utc(
        observation.get("ended_at"), label=f"{label}.ended_at"
    )
    if soak_ended_at < soak_started_at:
        fail("run soak ended_at precedes started_at")
    if soak_started_at < attempt_started_at or soak_ended_at > attempt_ended_at:
        fail("run soak interval must be contained within the attempt")
    duration = _normalize_decimal(
        observation.get("duration_seconds"),
        label=f"{label}.duration_seconds",
        require_canonical=True,
    )
    assert duration is not None
    expected_duration = _canonical_elapsed_seconds(soak_started_at, soak_ended_at)
    if duration != expected_duration:
        fail(
            "run soak duration_seconds differs from its verified timestamp interval"
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


def _validate_contract_requirement_observations(
    value: Any,
    *,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    criterion_metrics: list[dict[str, Any]],
    run_artifact_ids: set[str],
    attempt_started_at: datetime,
    attempt_ended_at: datetime,
) -> dict[str, Any]:
    requirements = _require_fields(
        value,
        {"context", "soak"},
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
        attempt_started_at=attempt_started_at,
        attempt_ended_at=attempt_ended_at,
    )
    return requirements


def _validate_criterion_observation(
    value: Any,
    *,
    index: int,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    run_artifact_ids: set[str],
    attempt_started_at: datetime,
    attempt_ended_at: datetime,
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
        attempt_started_at=attempt_started_at,
        attempt_ended_at=attempt_ended_at,
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
        environment = command.get("environment")
        if isinstance(environment, list):
            environment.sort(
                key=lambda item: str(item.get("name", ""))
                if isinstance(item, dict)
                else ""
            )
    observations = [_canonicalize_observation(item) for item in criterion_observations]
    observations.sort(key=lambda item: str(item.get("criterion_id", "")))
    normalized_attempt = copy.deepcopy(attempt)
    attempted_criterion_ids = normalized_attempt.get("attempted_criterion_ids")
    if isinstance(attempted_criterion_ids, list):
        normalized_attempt["attempted_criterion_ids"] = sorted(
            attempted_criterion_ids
        )
    record: dict[str, Any] = {
        "schema_version": VALIDATION_RUN_RECORD_SCHEMA_VERSION,
        "kind": VALIDATION_RUN_RECORD_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "attempt": normalized_attempt,
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
            "attempted_criterion_ids",
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
    criteria = _criterion_map(contract)
    attempted_criterion_ids = _sorted_unique_safe_identifiers(
        attempt.get("attempted_criterion_ids"),
        label="validation run attempted_criterion_ids",
    )
    provenance = _validate_preparation_provenance(
        record.get("preparation_provenance"), release=release
    )
    barrier_state = provenance["qualification_barrier"]
    if barrier_state == "not-reached":
        if phase != "preparation":
            fail("pre-qualification failure must use the preparation phase")
        if attempt.get("completion") == "completed":
            fail("not-reached full verification before qualification cannot report completed")
        if attempted_criterion_ids:
            fail("pre-qualification preparation must declare no attempted criteria")
    elif phase != "preparation" and not attempted_criterion_ids:
        fail("post-barrier qualification attempt must declare an attempted criterion")
    for criterion_id in attempted_criterion_ids:
        criterion = criteria.get(criterion_id)
        if criterion is None:
            fail("validation run attempted_criterion_ids contains an unknown criterion")
        if criterion["dimension"] == "provenance-security":
            fail("provenance/security criterion is review-derived, not run-derived")
        if criterion["qualification_scope"] != attempt["qualification_scope"]:
            fail("validation run attempted criterion scope differs from attempt scope")
    _validate_observed_environment(
        record.get("observed_environment"),
        release=release,
        barrier_state=barrier_state,
    )
    commands = record.get("commands")
    if not isinstance(commands, list) or not commands:
        fail("validation run commands must be a non-empty list")
    for index, command in enumerate(commands):
        _validate_command(
            command,
            index=index,
            criteria=criteria,
            attempted_criterion_ids=set(attempted_criterion_ids),
            node_count=release["supported_hardware_geometry"]["node_count"],
        )
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
    observation_ids: list[str] = []
    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            fail("validation run criterion observation must be an object")
        criterion_id = observation.get("criterion_id")
        criterion = criteria.get(criterion_id)
        if criterion is None:
            fail("validation run references an unknown criterion")
        if criterion["dimension"] == "provenance-security":
            fail("provenance/security criterion is review-derived, not run-derived")
        if criterion["qualification_scope"] != attempt["qualification_scope"]:
            fail("validation run criterion scope differs from attempt scope")
        _validate_criterion_observation(
            observation,
            index=index,
            contract=contract,
            criterion=criterion,
            run_artifact_ids=set(run_artifact_ids),
            attempt_started_at=started_at,
            attempt_ended_at=ended_at,
        )
        if (
            attempt["completion"] != "completed"
            and observation.get("completion") != "inconclusive"
        ):
            fail("incomplete validation attempt requires inconclusive observations")
        observation_ids.append(criterion_id)
    if observation_ids != sorted(observation_ids) or len(observation_ids) != len(
        set(observation_ids)
    ):
        fail("validation run criterion_observations must be sorted and unique")
    if observation_ids != attempted_criterion_ids:
        fail(
            "validation run criterion observations must exactly cover "
            "attempted_criterion_ids"
        )
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
    leftover_ids = bundle["review_evidence_artifact_ids"]
    if artifact_ids != leftover_ids:
        fail("provenance/security review must cover every bundle review artifact")
    conclusive = any(
        review.get(component) in {"pass", "fail"}
        for component in PROVENANCE_REVIEW_COMPONENTS
    )
    if conclusive and not artifact_ids:
        fail(
            "provenance/security review must cite extra review files "
            "when any component is pass or fail"
        )
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
    started_at = _parse_rfc3339_utc(
        observation["started_at"], label="soak observation started_at"
    )
    ended_at = _parse_rfc3339_utc(
        observation["ended_at"], label="soak observation ended_at"
    )
    verified_duration = _elapsed_seconds(started_at, ended_at)
    if verified_duration < requirement["minimum_duration_seconds"]:
        return "fail", "soak-duration-not-satisfied"
    if observation["concurrency"] != requirement["concurrency"]:
        return "fail", "soak-concurrency-not-satisfied"
    if observation["request_errors"] > requirement["maximum_request_errors"]:
        return "fail", "soak-error-budget-not-satisfied"
    return "pass", "soak-requirement-satisfied"


def _evaluate_relative_performance(
    predecessor_metrics: dict[tuple[str, str], str] | None,
    requirement: dict[str, Any] | None,
    *,
    criterion: dict[str, Any],
    current_metrics: dict[tuple[str, str], str],
) -> tuple[str, str]:
    if requirement is None:
        return "pass", "relative-performance-not-required"
    if predecessor_metrics is None:
        return "not-evaluated", "relative-performance-evidence-missing"
    maximum_regression = Decimal(requirement["maximum_regression_percent"])
    for threshold in criterion["thresholds"]:
        key = (threshold["metric"], threshold["unit"])
        predecessor = Decimal(predecessor_metrics[key])
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
    *,
    predecessor_metrics: dict[tuple[str, str], str] | None = None,
) -> tuple[str, str]:
    """Derive a disposition and reason from every frozen requirement."""
    criterion_id = criterion["criterion_id"]
    requirements = observation["contract_requirements"]
    nested_requirement_outcomes = [
        _evaluate_context_requirement(
            requirements["context"],
            _context_requirement_for_criterion(contract, criterion_id),
        ),
        _evaluate_soak_requirement(
            requirements["soak"],
            _soak_requirement_for_criterion(contract, criterion_id),
        ),
    ]
    if observation["completion"] == "inconclusive":
        # Context and soak carry their own completion state.  A completed
        # failure there is conclusive even if the outer measurement is not.
        for disposition, reason in nested_requirement_outcomes:
            if disposition == "fail":
                return disposition, reason
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
    supplemental = [
        *nested_requirement_outcomes,
        _evaluate_relative_performance(
            predecessor_metrics,
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


def _canonicalize_criterion_exclusions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail("validation decision criterion_exclusions must be a list")
    exclusions = copy.deepcopy(value)
    for exclusion in exclusions:
        if isinstance(exclusion, dict):
            evidence_ids = exclusion.get("review_evidence_artifact_ids")
            if isinstance(evidence_ids, list):
                exclusion["review_evidence_artifact_ids"] = sorted(evidence_ids)
    exclusions.sort(
        key=lambda item: (
            str(item.get("criterion_id", "")) if isinstance(item, dict) else "",
            str(item.get("run_record_id", "")) if isinstance(item, dict) else "",
        )
    )
    return exclusions


def _validate_criterion_exclusions(
    value: Any,
    *,
    contract: dict[str, Any],
    bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, list):
        fail("validation decision criterion_exclusions must be a list")
    criteria = _criterion_map(contract)
    records = {item["run_record_id"]: item for item in run_records}
    artifacts = {item["artifact_id"]: item for item in bundle["evidence_artifacts"]}
    review_ids = set(bundle["review_evidence_artifact_ids"])
    keys: list[tuple[str, str]] = []
    by_criterion: dict[str, list[dict[str, Any]]] = {}
    for index, exclusion_value in enumerate(value):
        label = f"validation decision criterion_exclusions[{index}]"
        exclusion = _require_fields(
            exclusion_value,
            {
                "criterion_id",
                "run_record_id",
                "reason",
                "review_evidence_artifact_ids",
            },
            label=label,
        )
        criterion_id = _safe_identifier(
            exclusion.get("criterion_id"), label=f"{label}.criterion_id"
        )
        criterion = criteria.get(criterion_id)
        if criterion is None:
            fail("validation decision exclusion references an unknown criterion")
        if criterion["dimension"] == "provenance-security":
            fail("provenance/security disposition cannot exclude run evidence")
        run_id = _sha256(
            exclusion.get("run_record_id"), label=f"{label}.run_record_id"
        )
        record = records.get(run_id)
        if record is None:
            fail("validation decision exclusion references a run outside the bundle")
        if not any(
            item["criterion_id"] == criterion_id
            for item in record["criterion_observations"]
        ):
            fail("validation decision exclusion does not name an observed criterion")
        _nonempty_string(exclusion.get("reason"), label=f"{label}.reason")
        evidence_ids = _sorted_unique_sha256(
            exclusion.get("review_evidence_artifact_ids"),
            label=f"{label}.review_evidence_artifact_ids",
        )
        if not evidence_ids or not set(evidence_ids).issubset(review_ids):
            fail("validation decision exclusion lacks bundle review evidence")
        if any(
            artifacts[artifact_id]["qualification_scope"] != "release-promotion"
            for artifact_id in evidence_ids
        ):
            fail(
                "validation decision exclusion evidence must be "
                "release-promotion review evidence"
            )
        keys.append((criterion_id, run_id))
        by_criterion.setdefault(criterion_id, []).append(exclusion)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail("validation decision criterion_exclusions must be sorted and unique")
    return by_criterion


def _aggregate_observation_outcomes(
    outcomes: list[tuple[str, str]],
) -> tuple[str, str]:
    dispositions = {item[0] for item in outcomes}
    if "pass" in dispositions and "fail" in dispositions:
        return "inconclusive", "conflicting-pass-fail-evidence"
    if "pass" in dispositions and "inconclusive" in dispositions:
        return "inconclusive", "pass-with-inconclusive-evidence"
    if "fail" in dispositions:
        return "fail", "conclusive-failure-evidence"
    if "inconclusive" in dispositions:
        return "inconclusive", "inconclusive-evidence"
    if "not-evaluated" in dispositions:
        return "not-evaluated", "included-evidence-not-evaluated"
    if dispositions == {"pass"}:
        return "pass", "all-included-evidence-passed"
    fail("validation decision has an unsupported observation outcome set")


def _criterion_result(
    *,
    contract: dict[str, Any],
    criterion: dict[str, Any],
    records: dict[str, dict[str, Any]],
    exclusions: list[dict[str, Any]],
    provenance_review: dict[str, Any],
    predecessor_metrics: dict[tuple[str, str], str] | None,
) -> dict[str, Any]:
    criterion_id = criterion["criterion_id"]
    if criterion["dimension"] == "provenance-security":
        if exclusions:
            fail("provenance/security criterion cannot carry exclusions")
        disposition = _provenance_disposition(provenance_review)
        reason = {
            "pass": "review-passed",
            "fail": "review-failed",
            "not-evaluated": "review-pending",
        }[disposition]
        return {
            "criterion_id": criterion_id,
            "disposition": disposition,
            "included_run_record_ids": [],
            "excluded_run_records": [],
            "reason": reason,
        }
    observed_run_ids = sorted(
        record["run_record_id"]
        for record in records.values()
        if any(
            observation["criterion_id"] == criterion_id
            for observation in record["criterion_observations"]
        )
    )
    excluded_ids = {item["run_record_id"] for item in exclusions}
    included_run_ids = [
        run_id for run_id in observed_run_ids if run_id not in excluded_ids
    ]
    if not included_run_ids:
        return {
            "criterion_id": criterion_id,
            "disposition": "not-evaluated",
            "included_run_record_ids": [],
            "excluded_run_records": exclusions,
            "reason": "no-included-evidence",
        }
    outcomes: list[tuple[str, str]] = []
    included_records: list[dict[str, Any]] = []
    for run_id in included_run_ids:
        record = records[run_id]
        included_records.append(record)
        outcome, reason = evaluate_criterion_observation(
            _observation_for(record, criterion_id),
            criterion,
            contract,
            predecessor_metrics=predecessor_metrics,
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
            for record in included_records
        }
        launch_ids = {
            record["observed_environment"]["launch_id"]
            for record in included_records
        }
        if len(boot_ids) != 1 or len(launch_ids) != 1:
            fail("strict same-boot evidence spans more than one live server boot")
    disposition, reason = _aggregate_observation_outcomes(outcomes)
    if len(outcomes) == 1:
        disposition, reason = outcomes[0]
    return {
        "criterion_id": criterion_id,
        "disposition": disposition,
        "included_run_record_ids": included_run_ids,
        "excluded_run_records": exclusions,
        "reason": reason,
    }


def _derive_criterion_results(
    *,
    contract: dict[str, Any],
    bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    criterion_exclusions: list[dict[str, Any]],
    provenance_review: dict[str, Any],
    predecessor_baselines: dict[str, dict[tuple[str, str], str]],
) -> list[dict[str, Any]]:
    criteria = _criterion_map(contract)
    records = {item["run_record_id"]: item for item in run_records}
    exclusions = _validate_criterion_exclusions(
        criterion_exclusions,
        contract=contract,
        bundle=bundle,
        run_records=run_records,
    )
    results: list[dict[str, Any]] = []
    for criterion_id, criterion in sorted(criteria.items()):
        results.append(
            _criterion_result(
                contract=contract,
                criterion=criterion,
                records=records,
                exclusions=exclusions.get(criterion_id, []),
                provenance_review=provenance_review,
                predecessor_metrics=predecessor_baselines.get(criterion_id),
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


def _validate_decision_reviewer(value: Any) -> str:
    """Accept only a privacy-safe reviewer identifier.

    Shape validation cannot prove that the named reviewer performed a
    repository review.
    """
    reviewer = _safe_identifier(value, label="validation decision reviewer")
    try:
        model_serving_release.validate_public_string_value(
            reviewer, label="validation decision reviewer"
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))
    return reviewer


def _validate_decision_review_reference(value: Any) -> str:
    """Accept only the closed repository-review grammar.

    Allowed forms are ``pr:<positive-integer>``, ``commit:<40-or-64 hex>``,
    and ``repository-review:<privacy-safe-identifier>``.  Arbitrary strings,
    credentials, paths, addresses, and deployment-only identifiers are
    rejected.  A matching reference does not prove that the review occurred.
    """
    reference = _nonempty_string(
        value, label="validation decision review_reference"
    )
    if REVIEW_REFERENCE_RE.fullmatch(reference) is None:
        fail(
            "validation decision review_reference must use the closed "
            "repository-review grammar (pr:<id>, commit:<hex>, or "
            "repository-review:<identifier>)"
        )
    if reference.startswith("repository-review:"):
        identifier = reference.split(":", 1)[1]
        try:
            model_serving_release.validate_public_string_value(
                identifier, label="validation decision review_reference"
            )
        except model_serving_release.ModelServingReleaseError as exc:
            fail(str(exc))
    return reference


def _validate_decision_review(value: Any) -> dict[str, Any]:
    review = _require_fields(
        value,
        {"authority", "reviewer", "reviewed_at", "review_reference"},
        label="validation decision review",
    )
    if review.get("authority") != "repository-maintainer-review":
        fail("validation decision authority must be repository-maintainer-review")
    _validate_decision_reviewer(review.get("reviewer"))
    _parse_rfc3339_utc(
        review.get("reviewed_at"), label="validation decision reviewed_at"
    )
    _validate_decision_review_reference(review.get("review_reference"))
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
            {
                "criterion_id",
                "disposition",
                "included_run_record_ids",
                "excluded_run_records",
                "reason",
            },
            label=f"validation decision criterion_results[{index}]",
        )
        criterion_id = _safe_identifier(
            result.get("criterion_id"),
            label=f"validation decision criterion_results[{index}].criterion_id",
        )
        if result.get("disposition") not in CRITERION_DISPOSITIONS:
            fail("validation decision criterion disposition is unsupported")
        included_ids = _sorted_unique_sha256(
            result.get("included_run_record_ids"),
            label=(
                f"validation decision criterion_results[{index}]."
                "included_run_record_ids"
            ),
        )
        excluded = result.get("excluded_run_records")
        if not isinstance(excluded, list):
            fail("validation decision excluded_run_records must be a list")
        excluded_keys: list[tuple[str, str]] = []
        for exclusion_index, exclusion_value in enumerate(excluded):
            exclusion = _require_fields(
                exclusion_value,
                {
                    "criterion_id",
                    "run_record_id",
                    "reason",
                    "review_evidence_artifact_ids",
                },
                label=(
                    f"validation decision criterion_results[{index}]."
                    f"excluded_run_records[{exclusion_index}]"
                ),
            )
            if exclusion.get("criterion_id") != criterion_id:
                fail("validation decision excluded record criterion mismatch")
            excluded_run_id = _sha256(
                exclusion.get("run_record_id"),
                label="validation decision excluded run_record_id",
            )
            _nonempty_string(
                exclusion.get("reason"),
                label="validation decision excluded record reason",
            )
            _sorted_unique_sha256(
                exclusion.get("review_evidence_artifact_ids"),
                label="validation decision exclusion review evidence",
            )
            excluded_keys.append((criterion_id, excluded_run_id))
        if excluded_keys != sorted(excluded_keys) or len(excluded_keys) != len(
            set(excluded_keys)
        ):
            fail("validation decision excluded_run_records must be sorted and unique")
        if set(included_ids).intersection(item[1] for item in excluded_keys):
            fail("validation decision cannot include and exclude the same run")
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


def _source_set_core_fields(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    allowed = {
        "release",
        "contract",
        "evidence_bundle",
        "run_records",
        "decision",
        "prior_decision_sources",
    }
    required = allowed - {"prior_decision_sources"}
    missing = sorted(required - set(value))
    extra = sorted(set(value) - allowed)
    if missing or extra:
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _flatten_prior_decisions(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}

    def walk(source: dict[str, Any]) -> None:
        decision = _validate_decision_shape_and_identity(source.get("decision"))
        decision_id = decision["decision_id"]
        if decision_id in by_id and by_id[decision_id] != decision:
            fail("prior-decision evidence lineage contains conflicting objects")
        by_id[decision_id] = decision
        for child in source.get("prior_decision_sources") or []:
            walk(child)

    for source in sources:
        walk(source)
    return [by_id[item] for item in sorted(by_id)]


def prior_decisions_from_source_set(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the flattened prior-decision objects from one source set."""
    return _flatten_prior_decisions(source.get("prior_decision_sources") or [])


def _validate_evidence_source_set(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    """Validate one caller-supplied source set without granting it authority.

    ``prior_decision_sources`` is the explicit registry-contract extension
    that supplies complete prior-decision evidence lineage when a predecessor
    decision itself has supersession links.  It is caller-supplied validation
    input, not a persisted schema-version change.
    """
    source = _source_set_core_fields(value, label=label)
    release = model_serving_release.validate_model_serving_release(
        source.get("release")
    )
    contract = model_serving_release.validate_validation_contract(
        source.get("contract"), expected_release=release
    )
    run_records = source.get("run_records")
    if not isinstance(run_records, list):
        fail(f"{label}.run_records must be a list")
    bundle = validate_validation_evidence_bundle(
        source.get("evidence_bundle"),
        release=release,
        contract=contract,
        run_records=run_records,
    )
    decision = _validate_decision_shape_and_identity(source.get("decision"))
    if decision["release_id"] != release["release_id"]:
        fail(f"{label} decision release cross-link mismatch")
    if decision["contract_id"] != contract["contract_id"]:
        fail(f"{label} decision contract cross-link mismatch")
    if decision["evidence_bundle_id"] != bundle["bundle_id"]:
        fail(f"{label} decision bundle cross-link mismatch")
    if "prior_decision_sources" in source:
        prior_sources = source.get("prior_decision_sources")
        if not isinstance(prior_sources, list):
            fail(f"{label}.prior_decision_sources must be a list")
        validated_priors = [
            _validate_evidence_source_set(
                item, label=f"{label}.prior_decision_sources[{index}]"
            )
            for index, item in enumerate(prior_sources)
        ]
        prior_ids = [item["decision"]["decision_id"] for item in validated_priors]
        if prior_ids != sorted(prior_ids) or len(prior_ids) != len(set(prior_ids)):
            fail(
                f"{label}.prior_decision_sources must be sorted by unique "
                "decision_id"
            )
        for prior in validated_priors:
            if (
                prior["decision"]["release_id"] != decision["release_id"]
                or prior["decision"]["contract_id"] != decision["contract_id"]
            ):
                fail(
                    f"{label}.prior_decision_sources must retain the same "
                    "release and contract"
                )
        if prior_ids and not decision["supersedes_decision_ids"]:
            fail(
                f"{label}.prior_decision_sources must be empty when the "
                "decision has no supersession links"
            )
    return source


def _validate_evidence_source_registry(
    value: Any,
    *,
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        fail(f"{label} must be a list of exact evidence source sets")
    sources = [
        _validate_evidence_source_set(item, label=f"{label}[{index}]")
        for index, item in enumerate(value)
    ]
    decision_ids = [item["decision"]["decision_id"] for item in sources]
    if decision_ids != sorted(decision_ids) or len(decision_ids) != len(
        set(decision_ids)
    ):
        fail(f"{label} must be sorted by unique decision_id")
    return sources


def _validate_prior_decision_sources(
    sources: list[dict[str, Any]],
    *,
    predecessor_evidence_registry: list[dict[str, Any]],
    validation_stack: set[str],
) -> None:
    """Fully validate nested prior-decision source sets, not shape alone."""
    for source in sources:
        decision = source["decision"]
        decision_id = decision["decision_id"]
        if decision_id in validation_stack:
            fail("comparable predecessor evidence contains a decision cycle")
        nested = source.get("prior_decision_sources") or []
        if decision["supersedes_decision_ids"] and not nested:
            fail(
                "comparable predecessor decision has supersession lineage "
                "without a fully supplied prior-decision evidence registry"
            )
        next_stack = validation_stack | {decision_id}
        _validate_prior_decision_sources(
            nested,
            predecessor_evidence_registry=predecessor_evidence_registry,
            validation_stack=next_stack,
        )
        validate_validation_decision(
            decision,
            release=source["release"],
            contract=source["contract"],
            evidence_bundle=source["evidence_bundle"],
            run_records=source["run_records"],
            predecessor_evidence_registry=predecessor_evidence_registry,
            prior_decisions=_flatten_prior_decisions(nested),
            _predecessor_validation_stack=next_stack,
        )


def _resolve_predecessor_baselines(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    predecessor_evidence_registry: list[dict[str, Any]],
    validation_stack: set[str],
) -> dict[str, dict[tuple[str, str], str]]:
    """Resolve frozen predecessor IDs to reviewed, internally valid evidence.

    The registry is external validation input and is never persisted into the
    current decision.  Validating it proves schema and cross-link consistency,
    not repository review, trusted publication, or physical measurement.
    A predecessor decision with supersession links must carry complete
    ``prior_decision_sources`` so chronology, same-release/contract
    constraints, acyclicity, exact bundle/runs, and recursive predecessor
    requirements can be checked.  Shape-only prior decisions are rejected.
    """
    sources = _validate_evidence_source_registry(
        predecessor_evidence_registry,
        label="predecessor evidence registry",
    )
    relative = contract["release_criteria"]["relative_performance"]
    if relative["status"] == "not-applicable":
        return {}
    expected_ids = {
        "release_id": relative["predecessor_release_id"],
        "contract_id": relative["predecessor_contract_id"],
        "bundle_id": relative["predecessor_bundle_id"],
        "decision_id": relative["predecessor_decision_id"],
    }
    matching: list[dict[str, Any]] = []
    for source in sources:
        observed_ids = {
            "release_id": source["release"]["release_id"],
            "contract_id": source["contract"]["contract_id"],
            "bundle_id": source["evidence_bundle"]["bundle_id"],
            "decision_id": source["decision"]["decision_id"],
        }
        shared = {
            field for field, observed in observed_ids.items() if observed == expected_ids[field]
        }
        if shared and observed_ids != expected_ids:
            fail("predecessor evidence registry contains conflicting identity cross-links")
        if observed_ids == expected_ids:
            matching.append(source)
    if len(matching) != 1:
        fail("relative performance requires exactly one matching predecessor source set")
    source = matching[0]
    predecessor_release = source["release"]
    predecessor_contract = source["contract"]
    predecessor_bundle = source["evidence_bundle"]
    predecessor_runs = source["run_records"]
    predecessor_decision = source["decision"]
    if (
        predecessor_release["supported_hardware_geometry"]
        != release["supported_hardware_geometry"]
    ):
        fail("comparable predecessor hardware geometry differs from current release")
    predecessor_decision_id = predecessor_decision["decision_id"]
    if predecessor_decision_id in validation_stack:
        fail("comparable predecessor evidence contains a decision cycle")
    remaining_sources = [
        item
        for item in sources
        if item["decision"]["decision_id"] != predecessor_decision_id
    ]
    prior_sources = source.get("prior_decision_sources") or []
    if predecessor_decision["supersedes_decision_ids"] and not prior_sources:
        fail(
            "comparable predecessor decision has supersession lineage without "
            "a fully supplied prior-decision evidence registry"
        )
    next_stack = validation_stack | {predecessor_decision_id}
    _validate_prior_decision_sources(
        prior_sources,
        predecessor_evidence_registry=remaining_sources,
        validation_stack=next_stack,
    )
    prior_decisions = _flatten_prior_decisions(prior_sources)
    validate_validation_decision(
        predecessor_decision,
        release=predecessor_release,
        contract=predecessor_contract,
        evidence_bundle=predecessor_bundle,
        run_records=predecessor_runs,
        predecessor_evidence_registry=remaining_sources,
        prior_decisions=prior_decisions,
        _predecessor_validation_stack=next_stack,
    )
    predecessor_criteria = _criterion_map(predecessor_contract)
    predecessor_records = {
        item["run_record_id"]: item for item in predecessor_runs
    }
    predecessor_results = {
        item["criterion_id"]: item
        for item in predecessor_decision["criterion_results"]
    }
    current_criteria = _criterion_map(contract)
    baselines: dict[str, dict[tuple[str, str], str]] = {}
    for dimension in ("throughput", "latency"):
        requirement = relative[dimension]
        current_criterion = current_criteria[requirement["criterion_id"]]
        predecessor_criterion_id = requirement["predecessor_criterion_id"]
        predecessor_criterion = predecessor_criteria.get(predecessor_criterion_id)
        if predecessor_criterion is None or predecessor_criterion["dimension"] != dimension:
            fail("relative performance predecessor criterion has the wrong dimension")
        if (
            model_serving_release.benchmark_protocol_id(predecessor_criterion)
            != requirement["benchmark_protocol_id"]
        ):
            fail("relative performance predecessor protocol differs from current protocol")
        result = predecessor_results.get(predecessor_criterion_id)
        if result is None or result["disposition"] != "pass":
            fail("relative performance predecessor criterion is not a reviewed pass")
        run_id = requirement["predecessor_run_record_id"]
        if run_id not in result["included_run_record_ids"]:
            fail("relative performance predecessor run is not included in the passing result")
        if run_id not in predecessor_bundle["run_record_ids"]:
            fail("relative performance predecessor run is outside the predecessor bundle")
        record = predecessor_records.get(run_id)
        if record is None or record["attempt"]["completion"] != "completed":
            fail("relative performance predecessor run is not a completed attempt")
        observation = _observation_for(record, predecessor_criterion_id)
        if observation["benchmark_protocol_id"] != requirement["benchmark_protocol_id"]:
            fail("relative performance predecessor run protocol mismatch")
        observed_metrics = {
            (item["metric"], item["unit"]): item["value"]
            for item in observation["metrics"]
        }
        required_keys = {
            (threshold["metric"], threshold["unit"])
            for threshold in current_criterion["thresholds"]
        }
        if not required_keys.issubset(observed_metrics):
            fail("relative performance predecessor run lacks a required baseline metric")
        baseline: dict[tuple[str, str], str] = {}
        for key in sorted(required_keys):
            _numeric_metric(
                observed_metrics[key],
                label=f"relative performance predecessor metric {key[0]}",
                positive=True,
            )
            baseline[key] = observed_metrics[key]
        baselines[current_criterion["criterion_id"]] = baseline
    return baselines


def validate_predecessor_evidence_registry(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    predecessor_evidence_registry: list[dict[str, Any]],
) -> dict[str, dict[tuple[str, str], str]]:
    """Resolve and semantically validate frozen predecessor source sets.

    This public entrypoint does not require a current decision.  It is a
    pure check over caller-supplied objects: the registry is validation
    input, not trusted persistence, and does not prove repository review
    or physical measurement.
    """
    release = model_serving_release.validate_model_serving_release(release)
    contract = model_serving_release.validate_validation_contract(
        contract, expected_release=release
    )
    return _resolve_predecessor_baselines(
        release=release,
        contract=contract,
        predecessor_evidence_registry=predecessor_evidence_registry,
        validation_stack=set(),
    )


def _supersession_lineage_closure(
    decision: dict[str, Any],
    registry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    closure: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    def visit(child: dict[str, Any]) -> None:
        child_id = child["decision_id"]
        if child_id in visiting:
            fail("validation decision supersession lineage contains a cycle")
        visiting.add(child_id)
        child_reviewed_at = _parse_rfc3339_utc(
            child["review"]["reviewed_at"],
            label="validation decision reviewed_at",
        )
        for parent_id in child["supersedes_decision_ids"]:
            if parent_id in visiting:
                fail("validation decision supersession lineage contains a cycle")
            parent = registry.get(parent_id)
            if parent is None:
                fail("validation decision supersession lineage is incomplete")
            if (
                child["release_id"] != parent["release_id"]
                or child["contract_id"] != parent["contract_id"]
            ):
                fail(
                    "validation decision supersession must retain release and contract"
                )
            parent_reviewed_at = _parse_rfc3339_utc(
                parent["review"]["reviewed_at"],
                label="prior validation decision reviewed_at",
            )
            if child_reviewed_at <= parent_reviewed_at:
                fail("superseding validation decision review must be strictly later")
            if parent_id not in closure:
                closure[parent_id] = parent
                visit(parent)
        visiting.remove(child_id)

    visit(decision)
    return [closure[item] for item in sorted(closure)]


def _validate_supersession_lineage(
    decision: dict[str, Any],
    prior_decisions: list[dict[str, Any]],
) -> None:
    validated = [
        _validate_decision_shape_and_identity(item) for item in prior_decisions
    ]
    ids = [item["decision_id"] for item in validated]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        fail("validation decision prior lineage must be sorted and unique")
    registry = {item["decision_id"]: item for item in validated}
    closure = _supersession_lineage_closure(decision, registry)
    if [item["decision_id"] for item in closure] != ids:
        fail("validation decision prior lineage contains unrelated decisions")


def build_validation_decision(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    evidence_bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    criterion_exclusions: list[dict[str, Any]],
    predecessor_evidence_registry: list[dict[str, Any]],
    provenance_security_review: dict[str, Any],
    status: str,
    reviewer: str,
    reviewed_at: str,
    review_reference: str,
    supersedes_decisions: list[dict[str, Any]] | None = None,
    supersession_lineage: list[dict[str, Any]] | None = None,
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
    normalized_exclusions = _canonicalize_criterion_exclusions(
        criterion_exclusions
    )
    predecessor_baselines = _resolve_predecessor_baselines(
        release=release,
        contract=contract,
        predecessor_evidence_registry=predecessor_evidence_registry,
        validation_stack=set(),
    )
    results = _derive_criterion_results(
        contract=contract,
        bundle=bundle,
        run_records=run_records,
        criterion_exclusions=normalized_exclusions,
        provenance_review=provenance,
        predecessor_baselines=predecessor_baselines,
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
    direct_prior_decisions = supersedes_decisions or []
    prior_ids: list[str] = []
    for prior in direct_prior_decisions:
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
    prior_decisions_by_id: dict[str, dict[str, Any]] = {}
    for prior in [*direct_prior_decisions, *(supersession_lineage or [])]:
        validated_prior = _validate_decision_shape_and_identity(prior)
        prior_id = validated_prior["decision_id"]
        if prior_id in prior_decisions_by_id:
            fail("validation decision supersession inputs must be unique")
        prior_decisions_by_id[prior_id] = validated_prior
    prior_decisions = [prior_decisions_by_id[item] for item in sorted(prior_decisions_by_id)]
    return validate_validation_decision(
        decision,
        release=release,
        contract=contract,
        evidence_bundle=bundle,
        run_records=run_records,
        predecessor_evidence_registry=predecessor_evidence_registry,
        prior_decisions=prior_decisions,
    )


def validate_validation_decision(
    value: Any,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    evidence_bundle: dict[str, Any],
    run_records: list[dict[str, Any]],
    predecessor_evidence_registry: list[dict[str, Any]],
    prior_decisions: list[dict[str, Any]] | None = None,
    _predecessor_validation_stack: set[str] | None = None,
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
    exclusions = [
        exclusion
        for item in decision["criterion_results"]
        for exclusion in item["excluded_run_records"]
    ]
    stack = set(_predecessor_validation_stack or {decision["decision_id"]})
    predecessor_baselines = _resolve_predecessor_baselines(
        release=release,
        contract=contract,
        predecessor_evidence_registry=predecessor_evidence_registry,
        validation_stack=stack,
    )
    expected_results = _derive_criterion_results(
        contract=contract,
        bundle=bundle,
        run_records=run_records,
        criterion_exclusions=exclusions,
        provenance_review=provenance,
        predecessor_baselines=predecessor_baselines,
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
    _validate_supersession_lineage(decision, supplied_prior)
    return decision


def effective_validation_status(
    decision: dict[str, Any],
    *,
    decision_evidence_registry: list[dict[str, Any]],
    predecessor_evidence_registry: list[dict[str, Any]],
) -> str:
    """Project Superseded from fully supplied, internally valid source sets.

    This rejects shape-only or backdated superseders.  It is still a pure
    consistency check over caller-supplied objects.  A caller-supplied
    decision or predecessor registry is not trusted persistence and cannot
    prove repository review, issuance, or current authority.
    """
    current = _validate_decision_shape_and_identity(decision)
    sources = _validate_evidence_source_registry(
        decision_evidence_registry,
        label="decision evidence registry",
    )
    source_by_id = {
        item["decision"]["decision_id"]: item for item in sources
    }
    current_source = source_by_id.get(current["decision_id"])
    if current_source is None or current_source["decision"] != current:
        fail("decision evidence registry does not contain the exact current decision")
    decisions = {
        decision_id: source["decision"]
        for decision_id, source in source_by_id.items()
    }
    for decision_id, source in source_by_id.items():
        source_decision = source["decision"]
        lineage = _supersession_lineage_closure(source_decision, decisions)
        validate_validation_decision(
            source_decision,
            release=source["release"],
            contract=source["contract"],
            evidence_bundle=source["evidence_bundle"],
            run_records=source["run_records"],
            predecessor_evidence_registry=predecessor_evidence_registry,
            prior_decisions=lineage,
        )
    superseders = [
        source["decision"]
        for source in sources
        if current["decision_id"] in source["decision"]["supersedes_decision_ids"]
    ]
    if len(superseders) > 1:
        fail("more than one later decision directly supersedes the same decision")
    return "superseded" if superseders else current["status"]


def validation_status_label(status: str) -> str:
    if status not in EFFECTIVE_VALIDATION_STATUSES:
        fail("validation status is unsupported")
    return STATUS_LABELS[status]
