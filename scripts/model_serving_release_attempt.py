#!/usr/bin/env python3
"""Compose ADR 0004 attempt-only specs from validator measurements.

This maintainer service consumes a verified release-plan candidate, a closed
caller context, and closed validator measurement documents. It emits one
attempt-only spec for compare-captures and a separate spec for
benchmark-serving. It does not capture evidence, hash programs, issue a
decision, write the tracked registry, or grant serving permission.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import stat
import sys
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import (
        immutable_descriptor_dir,
        model_identity,
        model_serving_release,
        model_serving_release_capture,
        model_serving_release_plan,
        model_validation_evidence,
        terminal_format,
    )
except ModuleNotFoundError:
    import immutable_descriptor_dir  # type: ignore[no-redef]
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]
    import model_serving_release_capture  # type: ignore[no-redef]
    import model_serving_release_plan  # type: ignore[no-redef]
    import model_validation_evidence  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "validate") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "validate"))

from validator_measurement import (  # noqa: E402
    OPERATION_PROGRAMS,
    PROMPT_STYLES,
    ValidatorMeasurementError,
    ValidatorMeasurementMissing,
    atomic_write_json,
    canonical_decimal,
    load_measurement_bytes,
    parse_strict_json,
    read_stable_bytes,
    sha256_bytes,
)


ATTEMPT_CONTEXT_SCHEMA_VERSION = 1
ATTEMPT_CONTEXT_KIND = "pulsar-model-serving-release-attempt-context"
INVOCATION_PLAN_SCHEMA_VERSION = 1
INVOCATION_PLAN_KIND = "pulsar-model-serving-release-invocation-plan"
OUTPUT_SCHEMA_VERSION = 1
COMPARE_OPERATION = "compare-captures"
BENCHMARK_OPERATION = "benchmark-serving"
RESOURCE_OPERATION = "observe-resources"
STARTED_OPERATIONS = (COMPARE_OPERATION, BENCHMARK_OPERATION)
COMPARE_DIMENSIONS = {"strict-same-boot"}
BENCHMARK_DIMENSIONS = {"throughput", "latency"}
BENCHMARK_PROTOCOL_NAME = "pulsar-bench-serve"
COMPARE_PROTOCOL_NAME = "strict-same-boot"
COMPARE_PROTOCOL_PARAMETERS = {
    "comparison": "exact",
    "fp_equivalent_satisfies": False,
}
QUALIFICATION_PHASE = "model-qualification"
DEFAULT_ATTEMPT_ROOT = "experiments/model-serving-release-attempts"
DEFAULT_MEASUREMENT_ROOT = "results"

CONTEXT_FIELDS = {
    "schema_version",
    "kind",
    "preparation_provenance",
    "observed_environment",
    "attempts",
    "command_environment",
    "command_site_options",
    "evidence_sources",
    "resource_diagnostic_sources",
}
OPERATION_ATTEMPT_FIELDS = {"attempt_id", "started_at", "ended_at"}
INVOCATION_COMPARE_FIELDS = {"program", "operation", "sample_size"}
INVOCATION_BENCH_REQUIRED = {
    "program",
    "operation",
    "concurrency",
    "num_requests",
}
INVOCATION_BENCH_OPTIONAL = {"input_tokens", "output_tokens", "prompt_style"}
BENCH_PROTOCOL_KEYS = {
    "concurrency",
    "input_tokens",
    "output_tokens",
    "prompt_style",
}
BENCH_METRIC_FIELDS = {
    ("output_tokens_per_second", "tokens-per-second"): "aggregate_tps",
    ("ttft_p95", "milliseconds"): "ttft_p95_ms",
    ("ttft_p50", "milliseconds"): "ttft_p50_ms",
    ("decode_tps_p50", "tokens-per-second"): "decode_tps_p50",
}
ATTEMPT_SPEC_NAMES = {
    COMPARE_OPERATION: "compare-captures.attempt-spec.json",
    BENCHMARK_OPERATION: "benchmark-serving.attempt-spec.json",
}

FORBIDDEN_FIELDS = {
    "adapter",
    "authority",
    "base_status",
    "bundle_id",
    "candidate_id",
    "decision",
    "decision_id",
    "decisions",
    "disposition",
    "effective_status",
    "exit_code",
    "exit_status",
    "privacy_review",
    "program_version",
    "promotion_authorized",
    "return_code",
    "returncode",
    "review",
    "reviewer",
    "serving_authorization",
    "serving_permission",
    "validation_status",
    "validator_output",
}

PERSISTENCE_NOTES = (
    "These attempt-only specs have no issuance authority.",
    "Capture must independently re-read evidence and derive digests.",
    "A later evidence mutation is not visible in the attempt spec.",
    "This workflow does not write the tracked release registry.",
    "This workflow does not issue a decision or change status.",
    "This workflow makes no physical DGX claim.",
)


class ModelServingReleaseAttemptError(ValueError):
    """Attempt composition input is unsafe, incomplete, or invalid."""


def fail(message: str) -> None:
    raise ModelServingReleaseAttemptError(message)


def _require_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    extra = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if extra or missing:
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _scan_forbidden_keys(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_FIELDS:
                fail(f"{label} contains forbidden field {key}")
            _scan_forbidden_keys(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden_keys(item, label=f"{label}[{index}]")


def _screen_context(value: Any, *, label: str, context: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        kind = value.get("kind")
        for key, item in value.items():
            child = f"{label}.{key}"
            if (
                key == "name"
                and kind == "secret-reference"
                and "command_environment" in context
            ):
                if not isinstance(item, str) or not item:
                    fail(f"{child} is invalid")
                continue
            if key == "content_sha256":
                if (
                    not isinstance(item, str)
                    or model_identity.SHA256_HEX_RE.fullmatch(item) is None
                ):
                    fail(f"{child} must be a SHA-256 digest")
                continue
            if (
                isinstance(key, str)
                and key.lower() in model_serving_release.PRIVATE_FIELD_NAMES
            ):
                fail(f"{label} contains private field {key!r}")
            if isinstance(key, str) and model_serving_release._is_credential_field_name(
                key
            ):
                fail(f"{label} contains credential-bearing field {key!r}")
            _screen_context(item, label=child, context=context + (key,))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _screen_context(
                item, label=f"{label}[{index}]", context=context
            )
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        fail(f"{label} must encode decimals as canonical strings, not floats")
    if isinstance(value, str):
        model_serving_release.validate_public_string_value(value, label=label)
        return
    fail(f"{label} contains unsupported JSON value {type(value).__name__}")


def _safe_identifier(value: Any, *, label: str) -> str:
    try:
        return model_validation_evidence._safe_identifier(value, label=label)
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))


def _positive_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{label} must be a positive integer")
    return value


def _repo_root(path: Path) -> Path:
    try:
        return model_serving_release_capture.safe_absolute(
            path, label="repository root"
        )
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(str(exc))


def _lexical_absolute(path: Path, *, base: Path | None, label: str) -> Path:
    try:
        if path.is_absolute():
            return model_serving_release_capture.safe_absolute(path, label=label)
        if base is None:
            fail(f"{label} must be absolute")
        return model_serving_release_capture.safe_absolute(
            path, base=base, label=label
        )
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(str(exc))


def _protected_prefixes(repo: Path) -> tuple[Path, ...]:
    return (
        repo / "models",
        repo / "models" / "model-serving-releases",
        repo / ".git",
    )


def _path_is_under(path: Path, root: Path, *, allow_equal: bool = False) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return allow_equal or path != root


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _reject_symlink(path: Path, *, label: str) -> None:
    info = _lstat_or_none(path)
    if info is not None and stat.S_ISLNK(info.st_mode):
        fail(f"{label} must not be a symlink")


def default_attempt_root(repo_root: Path) -> Path:
    repo = _repo_root(repo_root)
    return repo / DEFAULT_ATTEMPT_ROOT


def default_measurement_root(repo_root: Path) -> Path:
    repo = _repo_root(repo_root)
    return repo / DEFAULT_MEASUREMENT_ROOT


def _validate_output_location(
    path: Path,
    *,
    repo_root: Path,
    in_repo_root: Path,
    label: str,
    allow_equal_to_in_repo_root: bool = False,
) -> Path:
    repo = _repo_root(repo_root)
    dest = _lexical_absolute(
        path, base=repo if not path.is_absolute() else None, label=label
    )
    _reject_symlink(dest, label=label)
    if dest in {Path("/"), repo}:
        fail(f"{label} is too broad")
    for forbidden in _protected_prefixes(repo):
        if dest == forbidden or _path_is_under(dest, forbidden, allow_equal=True):
            fail(f"{label} cannot be written under a protected repository path")
    if _path_is_under(dest, repo, allow_equal=True):
        if dest == in_repo_root and not allow_equal_to_in_repo_root:
            fail(f"{label} cannot be the output root itself")
        if dest != in_repo_root and not _path_is_under(
            dest, in_repo_root, allow_equal=True
        ):
            fail(f"{label} must live under {in_repo_root.relative_to(repo)}")
    return dest


def validate_attempt_output_dir(path: Path, *, repo_root: Path) -> Path:
    return _validate_output_location(
        path,
        repo_root=repo_root,
        in_repo_root=default_attempt_root(repo_root),
        label="attempt output directory",
    )


def validate_attempt_output_file(path: Path, *, repo_root: Path) -> Path:
    dest = _validate_output_location(
        path,
        repo_root=repo_root,
        in_repo_root=default_attempt_root(repo_root),
        label="attempt output file",
    )
    if dest == default_attempt_root(repo_root):
        fail("attempt output file cannot be the output root itself")
    return dest


def validate_measurement_dir(path: Path, *, repo_root: Path) -> Path:
    return _validate_output_location(
        path,
        repo_root=repo_root,
        in_repo_root=default_measurement_root(repo_root),
        label="measurement directory",
    )


def resolve_publishable_evidence(
    repository_path: str, *, repo_root: Path, label: str
) -> Path:
    repo = _repo_root(repo_root)
    try:
        relative = (
            model_serving_release_capture.validate_publishable_repository_path(
                repository_path, label=label
            )
        )
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(str(exc))
    dest = _lexical_absolute(Path(relative), base=repo, label=label)
    _reject_symlink(dest, label=label)
    if not _path_is_under(dest, repo / "results", allow_equal=False):
        fail(f"{label} must resolve under results/")
    return dest


def resolve_measurement_input(
    path: Path, *, repo_root: Path, label: str
) -> Path:
    dest = _lexical_absolute(
        path, base=_repo_root(repo_root) if not path.is_absolute() else None, label=label
    )
    _reject_symlink(dest, label=label)
    return dest


def require_same_regular_file(left: Path, right: Path, *, label: str) -> None:
    if left == right:
        _reject_symlink(left, label=label)
        return
    left_stat = _lstat_or_none(left)
    right_stat = _lstat_or_none(right)
    if left_stat is None or right_stat is None:
        fail(f"{label} does not match the bound evidence source")
    if stat.S_ISLNK(left_stat.st_mode) or stat.S_ISLNK(right_stat.st_mode):
        fail(f"{label} must not be a symlink")
    if (left_stat.st_dev, left_stat.st_ino) != (right_stat.st_dev, right_stat.st_ino):
        fail(f"{label} does not match the bound evidence source")


def load_verified_release_and_contract(
    release_plan_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        verified = model_serving_release_plan.load_verified_release_plan_candidate(
            release_plan_dir
        )
    except (
        model_serving_release_plan.ModelServingReleasePlanError,
        immutable_descriptor_dir.ImmutableDescriptorDirectoryError,
    ) as exc:
        fail(f"release-plan candidate is invalid: {exc}")
    try:
        release = model_serving_release.validate_model_serving_release(
            verified.release
        )
        contract = model_serving_release.validate_validation_contract(
            verified.contract,
            expected_release=release,
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))
    return release, contract


def _load_operation_attempt(value: Any, *, operation: str) -> dict[str, Any]:
    label = f"attempt context attempts.{operation}"
    document = _require_fields(value, OPERATION_ATTEMPT_FIELDS, label=label)
    return {
        "attempt_id": _safe_identifier(
            document.get("attempt_id"), label=f"{label}.attempt_id"
        ),
        "started_at": model_serving_release.validate_public_string_value(
            document.get("started_at"), label=f"{label}.started_at"
        ),
        "ended_at": model_serving_release.validate_public_string_value(
            document.get("ended_at"), label=f"{label}.ended_at"
        ),
    }


def _load_evidence_source(
    value: Any, *, operation: str, source_field: str = "evidence_sources"
) -> dict[str, Any]:
    label = f"attempt context {source_field}.{operation}"
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    source_class = value.get("class")
    if source_class == "publishable":
        source = _require_fields(
            value,
            model_serving_release_capture.EVIDENCE_SOURCE_PUBLISHABLE_FIELDS,
            label=label,
        )
        repository_path = (
            model_serving_release_capture.validate_publishable_repository_path(
                source.get("repository_path"),
                label=f"{label}.repository_path",
            )
        )
        record = {
            "source_key": _safe_identifier(
                source.get("source_key"), label=f"{label}.source_key"
            ),
            "class": "publishable",
            "qualification_scope": source["qualification_scope"],
            "media_type": model_serving_release.validate_public_string_value(
                source.get("media_type"), label=f"{label}.media_type"
            ),
            "repository_path": repository_path,
        }
    elif source_class == "protected":
        fail(
            f"{label}.class protected is unsupported in this slice; "
            "use a publishable results/ evidence file"
        )
    else:
        fail(f"{label}.class is unsupported")
    if record["qualification_scope"] != "model-qualification":
        fail(f"{label}.qualification_scope must be model-qualification")
    if record["media_type"] != "application/json":
        fail(f"{label}.media_type must be application/json")
    return record


def load_attempt_context(path: Path) -> dict[str, Any]:
    try:
        data = read_stable_bytes(path, label="attempt context")
    except (OSError, ValidatorMeasurementError):
        fail("attempt context cannot be read")
    payload = parse_strict_json(data, label="attempt context")
    if not isinstance(payload, dict):
        fail("attempt context must be an object")
    _scan_forbidden_keys(payload, label="attempt context")
    document = _require_fields(payload, CONTEXT_FIELDS, label="attempt context")
    if document.get("schema_version") != ATTEMPT_CONTEXT_SCHEMA_VERSION:
        fail("attempt context schema_version is unsupported")
    if document.get("kind") != ATTEMPT_CONTEXT_KIND:
        fail("attempt context kind is invalid")
    attempts = document.get("attempts")
    if not isinstance(attempts, dict) or set(attempts) != set(STARTED_OPERATIONS):
        fail("attempt context attempts must name compare-captures and benchmark-serving")
    evidence_sources = document.get("evidence_sources")
    if not isinstance(evidence_sources, dict) or set(evidence_sources) != set(
        STARTED_OPERATIONS
    ):
        fail(
            "attempt context evidence_sources must name compare-captures and "
            "benchmark-serving"
        )
    resource_sources = document.get("resource_diagnostic_sources")
    if not isinstance(resource_sources, dict) or set(resource_sources) != set(
        STARTED_OPERATIONS
    ):
        fail(
            "attempt context resource_diagnostic_sources must name "
            "compare-captures and benchmark-serving"
        )
    environment = document.get("command_environment")
    if not isinstance(environment, list):
        fail("attempt context command_environment must be a list")
    site_options = document.get("command_site_options")
    if not isinstance(site_options, list):
        fail("attempt context command_site_options must be a list")
    loaded = {
        "schema_version": ATTEMPT_CONTEXT_SCHEMA_VERSION,
        "kind": ATTEMPT_CONTEXT_KIND,
        "preparation_provenance": copy.deepcopy(
            document["preparation_provenance"]
        ),
        "observed_environment": copy.deepcopy(document["observed_environment"]),
        "attempts": {
            operation: _load_operation_attempt(
                attempts[operation], operation=operation
            )
            for operation in STARTED_OPERATIONS
        },
        "command_environment": copy.deepcopy(environment),
        "command_site_options": copy.deepcopy(site_options),
        "evidence_sources": {
            operation: _load_evidence_source(
                evidence_sources[operation], operation=operation
            )
            for operation in STARTED_OPERATIONS
        },
        "resource_diagnostic_sources": {
            operation: _load_evidence_source(
                resource_sources[operation],
                operation=operation,
                source_field="resource_diagnostic_sources",
            )
            for operation in STARTED_OPERATIONS
        },
    }
    _screen_context(loaded, label="attempt context")
    attempt_ids = [
        loaded["attempts"][operation]["attempt_id"]
        for operation in STARTED_OPERATIONS
    ]
    if len(set(attempt_ids)) != len(attempt_ids):
        fail("attempt context attempt_id values must be unique")
    source_keys = [
        source["source_key"]
        for source in loaded["evidence_sources"].values()
    ] + [
        source["source_key"]
        for source in loaded["resource_diagnostic_sources"].values()
    ]
    if len(source_keys) != len(set(source_keys)):
        fail("attempt context evidence source_key values must be unique")
    return loaded


def criteria_for_operation(
    contract: dict[str, Any], operation: str
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for criterion in contract["release_criteria"]["criteria"]:
        dimension = criterion["dimension"]
        protocol_name = criterion["protocol"]["name"]
        if operation == COMPARE_OPERATION and dimension in COMPARE_DIMENSIONS:
            selected.append(criterion)
        elif (
            operation == BENCHMARK_OPERATION
            and dimension in BENCHMARK_DIMENSIONS
            and protocol_name == BENCHMARK_PROTOCOL_NAME
        ):
            selected.append(criterion)
    selected.sort(key=lambda item: item["criterion_id"])
    return selected


def _exact_match_rate(payload: dict[str, Any]) -> str | None:
    sample_count = payload["sample_count"]
    if sample_count < 1:
        return None
    return canonical_decimal(
        Decimal(payload["identical_record_count"]) / Decimal(sample_count),
        label="exact_match_rate",
    )


def _sample_reason(measured: int, required: int) -> str:
    if measured < required:
        return "short-sample"
    return "protocol-mismatch"


def _compare_protocol_matches(criterion: dict[str, Any]) -> bool:
    protocol = criterion.get("protocol") or {}
    if protocol.get("name") != COMPARE_PROTOCOL_NAME:
        return False
    parameters = protocol.get("parameters")
    if not isinstance(parameters, dict):
        return False
    if set(parameters) != set(COMPARE_PROTOCOL_PARAMETERS):
        return False
    return (
        parameters.get("comparison") == "exact"
        and parameters.get("fp_equivalent_satisfies") is False
    )


def _map_compare_criterion(
    criterion: dict[str, Any],
    measurement: dict[str, Any] | None,
    *,
    source_state: str,
) -> dict[str, Any]:
    if not _compare_protocol_matches(criterion):
        return {
            "ok": False,
            "reason": "protocol-mismatch",
            "sample_size": 0,
            "metrics": [],
        }
    if measurement is None:
        return {
            "ok": False,
            "reason": source_state,
            "sample_size": 0,
            "metrics": [],
        }
    payload = measurement["compare-captures"]
    if measurement["completion"] != "complete":
        return {
            "ok": False,
            "reason": measurement["reason"],
            "sample_size": payload["sample_count"],
            "metrics": [],
        }
    if payload["sample_count"] != criterion["sample_size"]:
        return {
            "ok": False,
            "reason": _sample_reason(payload["sample_count"], criterion["sample_size"]),
            "sample_size": payload["sample_count"],
            "metrics": [],
        }
    required = {
        (item["metric"], item["unit"]) for item in criterion["thresholds"]
    }
    if required != {("exact_match_rate", "ratio")}:
        return {
            "ok": False,
            "reason": "unsupported-metric",
            "sample_size": payload["sample_count"],
            "metrics": [],
        }
    rate = _exact_match_rate(payload)
    if rate is None:
        return {
            "ok": False,
            "reason": "unusable-input",
            "sample_size": payload["sample_count"],
            "metrics": [],
        }
    return {
        "ok": True,
        "reason": "completed",
        "sample_size": payload["sample_count"],
        "metrics": [
            {
                "metric": "exact_match_rate",
                "value": rate,
                "unit": "ratio",
            }
        ],
    }


def _matching_bench_level(
    criterion: dict[str, Any], payload: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    wanted = criterion["protocol"]["parameters"].get("concurrency")
    if wanted is None:
        if len(payload["levels"]) == 1:
            return payload["levels"][0], None
        return None, "protocol-mismatch"
    for level in payload["levels"]:
        if level["concurrency"] == wanted:
            return level, None
    return None, "protocol-mismatch"


def _bench_protocol_matches(
    criterion: dict[str, Any], payload: dict[str, Any], level: dict[str, Any]
) -> bool:
    observed = {
        "concurrency": level["concurrency"],
        "input_tokens": payload["input_tokens"],
        "output_tokens": payload["output_tokens"],
        "prompt_style": payload["prompt_style"],
    }
    for key, value in criterion["protocol"]["parameters"].items():
        if key not in BENCH_PROTOCOL_KEYS or key not in observed:
            return False
        if observed[key] != value:
            return False
    return True


def _map_bench_criterion(
    criterion: dict[str, Any],
    measurement: dict[str, Any] | None,
    *,
    source_state: str,
) -> dict[str, Any]:
    if measurement is None or measurement.get("completion") != "complete":
        return {
            "ok": False,
            "reason": (
                source_state
                if measurement is None
                else measurement.get("reason") or source_state
            ),
            "sample_size": 0,
            "metrics": [],
        }
    payload = measurement["benchmark-serving"]
    level, mismatch = _matching_bench_level(criterion, payload)
    if level is None:
        return {
            "ok": False,
            "reason": mismatch or "protocol-mismatch",
            "sample_size": 0,
            "metrics": [],
        }
    if not _bench_protocol_matches(criterion, payload, level):
        return {
            "ok": False,
            "reason": "protocol-mismatch",
            "sample_size": level["measured_request_count"],
            "metrics": [],
        }
    if level["completion"] != "complete":
        return {
            "ok": False,
            "reason": level["reason"],
            "sample_size": level["measured_request_count"],
            "metrics": [],
        }
    if level["measured_request_count"] != criterion["sample_size"]:
        return {
            "ok": False,
            "reason": _sample_reason(
                level["measured_request_count"], criterion["sample_size"]
            ),
            "sample_size": level["measured_request_count"],
            "metrics": [],
        }
    metrics: list[dict[str, str]] = []
    for threshold in criterion["thresholds"]:
        key = (threshold["metric"], threshold["unit"])
        field = BENCH_METRIC_FIELDS.get(key)
        if field is None or level.get(field) is None:
            return {
                "ok": False,
                "reason": "unsupported-metric",
                "sample_size": level["measured_request_count"],
                "metrics": [],
            }
        metrics.append(
            {
                "metric": threshold["metric"],
                "value": level[field],
                "unit": threshold["unit"],
            }
        )
    metrics.sort(key=lambda item: (item["metric"], item["unit"]))
    return {
        "ok": True,
        "reason": "completed",
        "sample_size": level["measured_request_count"],
        "metrics": metrics,
    }


def _attempt_completion(source_state: str, mapped: list[dict[str, Any]]) -> str:
    if source_state == "interrupted" or any(
        item["reason"] == "interrupted" for item in mapped
    ):
        return "interrupted"
    if source_state in {"missing-measurement", "corrupt-measurement", "unusable-input"}:
        return "failed"
    if source_state != "completed" or any(not item["ok"] for item in mapped):
        return "inconclusive"
    return "completed"


def _observation(
    criterion: dict[str, Any],
    mapped: dict[str, Any],
    *,
    source_key: str,
    complete: bool,
) -> dict[str, Any]:
    return {
        "criterion_id": criterion["criterion_id"],
        "completion": "complete" if complete else "inconclusive",
        "sample_size": mapped["sample_size"],
        "metrics": copy.deepcopy(mapped["metrics"]) if complete else [],
        "evidence_source_keys": [source_key],
        "contract_requirements": {"context": None, "soak": None},
        "reason": "completed" if complete else mapped["reason"],
    }


def _commands(
    *,
    operation: str,
    criterion_ids: list[str],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    arguments: list[dict[str, Any]] = [
        {"kind": "operation", "value": operation}
    ]
    for criterion_id in sorted(criterion_ids):
        arguments.append(
            {"kind": "criterion-reference", "criterion_id": criterion_id}
        )
    arguments.extend(copy.deepcopy(context["command_site_options"]))
    environment = sorted(
        copy.deepcopy(context["command_environment"]),
        key=lambda item: str(item.get("name", "")),
    )
    commands = [
        {
            "program": OPERATION_PROGRAMS[operation],
            "arguments": arguments,
            "environment": environment,
            "working_directory": "repository-root",
        }
    ]
    commands.append(
        {
            "program": "scripts/model-serving-experiment-monitor.sh",
            "arguments": [{"kind": "operation", "value": RESOURCE_OPERATION}],
            "environment": copy.deepcopy(environment),
            "working_directory": "repository-root",
        }
    )
    return commands


def load_bound_measurement(
    path: Path | None,
    *,
    operation: str,
    context: dict[str, Any],
    repo_root: Path,
) -> tuple[dict[str, Any] | None, str, str]:
    source = context["evidence_sources"][operation]
    evidence_path = resolve_publishable_evidence(
        source["repository_path"],
        repo_root=repo_root,
        label=f"{operation} evidence source",
    )
    if path is not None:
        supplied = resolve_measurement_input(
            path, repo_root=repo_root, label=f"{operation} measurement"
        )
        require_same_regular_file(
            supplied,
            evidence_path,
            label=f"{operation} measurement",
        )
    try:
        data = read_stable_bytes(evidence_path, label=f"{operation} measurement")
    except ValidatorMeasurementMissing:
        fail(
            f"{operation} measurement is missing; supply a validator "
            "--result-json document (incomplete validator output is accepted)"
        )
    except ValidatorMeasurementError:
        fail(f"{operation} measurement cannot be read safely")
    try:
        document = load_measurement_bytes(data)
    except ValidatorMeasurementError:
        return None, "corrupt-measurement", sha256_bytes(data)
    if document["operation"] != operation:
        fail(f"{operation} measurement operation is {document['operation']}")
    return document, document["reason"], sha256_bytes(data)


def load_bound_resource_diagnostic(
    *, operation: str, context: dict[str, Any], repo_root: Path
) -> tuple[dict[str, Any], str]:
    source = context["resource_diagnostic_sources"][operation]
    evidence_path = resolve_publishable_evidence(
        source["repository_path"],
        repo_root=repo_root,
        label=f"{operation} resource diagnostic source",
    )
    try:
        data = read_stable_bytes(
            evidence_path, label=f"{operation} resource diagnostic"
        )
    except ValidatorMeasurementMissing:
        fail(
            f"{operation} resource diagnostic is missing; preserve a closed "
            "incomplete observe-resources measurement when collection is unavailable"
        )
    except ValidatorMeasurementError:
        fail(f"{operation} resource diagnostic cannot be read safely")
    try:
        document = load_measurement_bytes(data)
    except ValidatorMeasurementError:
        fail(f"{operation} resource diagnostic is corrupt")
    if document["operation"] != RESOURCE_OPERATION:
        fail(
            f"{operation} resource diagnostic operation is {document['operation']}"
        )
    payload = document[RESOURCE_OPERATION]
    attempt = context["attempts"][operation]
    if payload["started_at"] != attempt["started_at"]:
        fail(f"{operation} resource diagnostic started_at differs from attempt")
    if payload["ended_at"] != attempt["ended_at"]:
        fail(f"{operation} resource diagnostic ended_at differs from attempt")
    if payload["qualification_scope"] != source["qualification_scope"]:
        fail(f"{operation} resource diagnostic qualification scope differs")
    return document, sha256_bytes(data)


def compose_attempt_spec(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    context: dict[str, Any],
    operation: str,
    measurement: dict[str, Any] | None,
    source_state: str,
) -> dict[str, Any]:
    criteria = criteria_for_operation(contract, operation)
    if not criteria:
        fail(f"release contract has no {operation} criteria to declare")
    mapper = (
        _map_compare_criterion
        if operation == COMPARE_OPERATION
        else _map_bench_criterion
    )
    mapped = [
        mapper(criterion, measurement, source_state=source_state)
        for criterion in criteria
    ]
    completion = _attempt_completion(source_state, mapped)
    observations_complete = completion == "completed"
    if not observations_complete:
        for item in mapped:
            if item["ok"]:
                item["reason"] = "attempt-incomplete"
                item["ok"] = False
    attempt = context["attempts"][operation]
    source = context["evidence_sources"][operation]
    resource_source = context["resource_diagnostic_sources"][operation]
    criterion_ids = [item["criterion_id"] for item in criteria]
    observations = [
        _observation(
            criterion,
            mapped_item,
            source_key=source["source_key"],
            complete=observations_complete,
        )
        for criterion, mapped_item in zip(criteria, mapped)
    ]
    observations.sort(key=lambda item: item["criterion_id"])
    return {
        "schema_version": 1,
        "kind": model_serving_release_capture.CAPTURE_SPEC_KIND,
        "release_id": release["release_id"],
        "contract_id": contract["contract_id"],
        "attempt": {
            "attempt_id": attempt["attempt_id"],
            "phase": QUALIFICATION_PHASE,
            "qualification_scope": QUALIFICATION_PHASE,
            "attempted_criterion_ids": sorted(criterion_ids),
            "started_at": attempt["started_at"],
            "ended_at": attempt["ended_at"],
            "completion": completion,
        },
        "preparation_provenance": copy.deepcopy(context["preparation_provenance"]),
        "observed_environment": copy.deepcopy(context["observed_environment"]),
        "commands": _commands(
            operation=operation,
            criterion_ids=criterion_ids,
            context=context,
        ),
        "criterion_observations": observations,
        "evidence_sources": sorted(
            [copy.deepcopy(source), copy.deepcopy(resource_source)],
            key=lambda item: item["source_key"],
        ),
        "run_diagnostic_source_keys": [resource_source["source_key"]],
        "review_source_keys": [],
    }


def compose_attempt_specs(
    *,
    release_plan_dir: Path,
    context: dict[str, Any],
    compare_measurement_path: Path | None,
    benchmark_measurement_path: Path | None,
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    release, contract = load_verified_release_and_contract(release_plan_dir)
    compare_measurement, compare_state, compare_digest = load_bound_measurement(
        compare_measurement_path,
        operation=COMPARE_OPERATION,
        context=context,
        repo_root=repo_root,
    )
    bench_measurement, bench_state, bench_digest = load_bound_measurement(
        benchmark_measurement_path,
        operation=BENCHMARK_OPERATION,
        context=context,
        repo_root=repo_root,
    )
    _compare_resource, compare_resource_digest = load_bound_resource_diagnostic(
        operation=COMPARE_OPERATION, context=context, repo_root=repo_root
    )
    _bench_resource, bench_resource_digest = load_bound_resource_diagnostic(
        operation=BENCHMARK_OPERATION, context=context, repo_root=repo_root
    )
    specs = {
        COMPARE_OPERATION: compose_attempt_spec(
            release=release,
            contract=contract,
            context=context,
            operation=COMPARE_OPERATION,
            measurement=compare_measurement,
            source_state=compare_state,
        ),
        BENCHMARK_OPERATION: compose_attempt_spec(
            release=release,
            contract=contract,
            context=context,
            operation=BENCHMARK_OPERATION,
            measurement=bench_measurement,
            source_state=bench_state,
        ),
    }
    return specs, {
        COMPARE_OPERATION: {
            context["evidence_sources"][COMPARE_OPERATION]["source_key"]: compare_digest,
            context["resource_diagnostic_sources"][COMPARE_OPERATION][
                "source_key"
            ]: compare_resource_digest,
        },
        BENCHMARK_OPERATION: {
            context["evidence_sources"][BENCHMARK_OPERATION]["source_key"]: bench_digest,
            context["resource_diagnostic_sources"][BENCHMARK_OPERATION][
                "source_key"
            ]: bench_resource_digest,
        },
    }


def _unique_protocol_value(
    criteria: list[dict[str, Any]], key: str
) -> Any:
    values = []
    for criterion in criteria:
        if key in criterion["protocol"]["parameters"]:
            values.append(criterion["protocol"]["parameters"][key])
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    if len(unique) > 1:
        fail(f"benchmark criteria disagree on protocol parameter {key}")
    return unique[0] if unique else None


def plan_invocation(contract: dict[str, Any]) -> dict[str, Any]:
    compare = criteria_for_operation(contract, COMPARE_OPERATION)
    bench = criteria_for_operation(contract, BENCHMARK_OPERATION)
    document: dict[str, Any] = {
        "schema_version": INVOCATION_PLAN_SCHEMA_VERSION,
        "kind": INVOCATION_PLAN_KIND,
    }
    if compare:
        mismatched = [
            item["criterion_id"]
            for item in compare
            if not _compare_protocol_matches(item)
        ]
        if mismatched:
            fail(
                "strict-same-boot invocation requires protocol name "
                "strict-same-boot with exact closed parameters"
            )
        sample_sizes = {item["sample_size"] for item in compare}
        if len(sample_sizes) != 1:
            fail("strict same-boot criteria disagree on sample_size")
        document[COMPARE_OPERATION] = {
            "program": OPERATION_PROGRAMS[COMPARE_OPERATION],
            "operation": COMPARE_OPERATION,
            "sample_size": next(iter(sample_sizes)),
        }
    if bench:
        sample_sizes = {item["sample_size"] for item in bench}
        if len(sample_sizes) != 1:
            fail("benchmark criteria disagree on sample_size")
        concurrencies = sorted(
            {
                item["protocol"]["parameters"]["concurrency"]
                for item in bench
                if "concurrency" in item["protocol"]["parameters"]
            }
        )
        if not concurrencies:
            fail("benchmark criteria do not declare concurrency")
        sample_size = next(iter(sample_sizes))
        if sample_size < max(concurrencies):
            fail(
                "benchmark criterion sample_size must be at least the largest "
                "declared concurrency"
            )
        section: dict[str, Any] = {
            "program": OPERATION_PROGRAMS[BENCHMARK_OPERATION],
            "operation": BENCHMARK_OPERATION,
            "concurrency": concurrencies,
            "num_requests": sample_size,
        }
        for key in ("input_tokens", "output_tokens", "prompt_style"):
            value = _unique_protocol_value(bench, key)
            if value is not None:
                section[key] = value
        extra = set()
        for item in bench:
            extra.update(set(item["protocol"]["parameters"]) - BENCH_PROTOCOL_KEYS)
        if extra:
            fail(
                "benchmark criteria use unsupported protocol parameters: "
                + ", ".join(sorted(extra))
            )
        document[BENCHMARK_OPERATION] = section
    if COMPARE_OPERATION not in document and BENCHMARK_OPERATION not in document:
        fail("release contract has no strict-same-boot or bench criteria")
    _screen_context(document, label="invocation plan")
    return document


def _validate_invocation_compare(value: Any) -> dict[str, Any]:
    section = _require_fields(
        value, INVOCATION_COMPARE_FIELDS, label="invocation plan compare-captures"
    )
    if section.get("program") != OPERATION_PROGRAMS[COMPARE_OPERATION]:
        fail("invocation plan compare-captures.program is unsupported")
    if section.get("operation") != COMPARE_OPERATION:
        fail("invocation plan compare-captures.operation is unsupported")
    return {
        "program": OPERATION_PROGRAMS[COMPARE_OPERATION],
        "operation": COMPARE_OPERATION,
        "sample_size": _positive_int(
            section.get("sample_size"),
            label="invocation plan compare-captures.sample_size",
        ),
    }


def _validate_invocation_bench(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("invocation plan benchmark-serving must be an object")
    extra_fields = set(value) - (INVOCATION_BENCH_REQUIRED | INVOCATION_BENCH_OPTIONAL)
    missing = INVOCATION_BENCH_REQUIRED - set(value)
    if extra_fields or missing:
        fail(
            "invocation plan benchmark-serving fields differ "
            f"(missing={sorted(missing)}, extra={sorted(extra_fields)})"
        )
    if value.get("program") != OPERATION_PROGRAMS[BENCHMARK_OPERATION]:
        fail("invocation plan benchmark-serving.program is unsupported")
    if value.get("operation") != BENCHMARK_OPERATION:
        fail("invocation plan benchmark-serving.operation is unsupported")
    concurrency = value.get("concurrency")
    if not isinstance(concurrency, list) or not concurrency:
        fail("invocation plan concurrency must be a non-empty list")
    parsed = [
        _positive_int(
            item, label=f"invocation plan concurrency[{index}]"
        )
        for index, item in enumerate(concurrency)
    ]
    if len(parsed) != len(set(parsed)):
        fail("invocation plan concurrency values must be unique")
    num_requests = _positive_int(
        value.get("num_requests"),
        label="invocation plan num_requests",
    )
    if num_requests < max(parsed):
        fail(
            "invocation plan num_requests must be at least the largest "
            "concurrency value"
        )
    section: dict[str, Any] = {
        "program": OPERATION_PROGRAMS[BENCHMARK_OPERATION],
        "operation": BENCHMARK_OPERATION,
        "concurrency": parsed,
        "num_requests": num_requests,
    }
    if "input_tokens" in value:
        section["input_tokens"] = _positive_int(
            value.get("input_tokens"), label="invocation plan input_tokens"
        )
    if "output_tokens" in value:
        section["output_tokens"] = _positive_int(
            value.get("output_tokens"), label="invocation plan output_tokens"
        )
    if "prompt_style" in value:
        style = value.get("prompt_style")
        if style not in PROMPT_STYLES:
            fail("invocation plan prompt_style is unsupported")
        section["prompt_style"] = style
    return section


def load_invocation_plan(path: Path) -> dict[str, Any]:
    try:
        data = read_stable_bytes(path, label="invocation plan")
    except (OSError, ValidatorMeasurementError):
        fail("invocation plan cannot be read")
    payload = parse_strict_json(data, label="invocation plan")
    if not isinstance(payload, dict):
        fail("invocation plan must be an object")
    _scan_forbidden_keys(payload, label="invocation plan")
    if payload.get("schema_version") != INVOCATION_PLAN_SCHEMA_VERSION:
        fail("invocation plan schema_version is unsupported")
    if payload.get("kind") != INVOCATION_PLAN_KIND:
        fail("invocation plan kind is invalid")
    extra = set(payload) - {
        "schema_version",
        "kind",
        COMPARE_OPERATION,
        BENCHMARK_OPERATION,
    }
    if extra:
        fail(f"invocation plan has unknown fields: {sorted(extra)}")
    document: dict[str, Any] = {
        "schema_version": INVOCATION_PLAN_SCHEMA_VERSION,
        "kind": INVOCATION_PLAN_KIND,
    }
    if COMPARE_OPERATION in payload:
        document[COMPARE_OPERATION] = _validate_invocation_compare(
            payload[COMPARE_OPERATION]
        )
    if BENCHMARK_OPERATION in payload:
        document[BENCHMARK_OPERATION] = _validate_invocation_bench(
            payload[BENCHMARK_OPERATION]
        )
    if COMPARE_OPERATION not in document and BENCHMARK_OPERATION not in document:
        fail("invocation plan has no compare-captures or benchmark-serving section")
    _screen_context(document, label="invocation plan")
    return document


def bench_argv(plan: dict[str, Any]) -> list[str]:
    section = plan.get(BENCHMARK_OPERATION)
    if not isinstance(section, dict):
        fail("invocation plan does not include benchmark-serving")
    validated = _validate_invocation_bench(section)
    argv = ["--concurrency"]
    argv.extend(str(item) for item in validated["concurrency"])
    argv.extend(["--num-requests", str(validated["num_requests"])])
    if "input_tokens" in validated:
        argv.extend(["--input-tokens", str(validated["input_tokens"])])
    if "output_tokens" in validated:
        argv.extend(["--output-tokens", str(validated["output_tokens"])])
    if "prompt_style" in validated:
        argv.extend(["--prompt-style", str(validated["prompt_style"])])
    return argv


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def render_compose(payload: dict[str, Any]) -> None:
    writer = terminal_format.TerminalWriter()
    writer.emit("Model Serving Release attempt composition")
    writer.blank()
    writer.field("Command", payload.get("command", "compose"))
    writer.field("Authority", payload.get("authority", "none"))
    writer.field("Privacy", payload.get("privacy_review", "pending"))
    writer.field(
        "Promotion",
        "not authorized"
        if not payload.get("promotion_authorized")
        else "unauthorized-state",
    )
    writer.blank()
    for attempt in payload.get("attempts", []):
        writer.field("Attempt", attempt.get("attempt_id", ""))
        writer.field("Operation", attempt.get("operation", ""), indent=2)
        writer.field("Completion", attempt.get("completion", ""), indent=2)
        writer.field(
            "Criteria",
            ", ".join(attempt.get("attempted_criterion_ids") or ["none"]),
            indent=2,
        )
        writer.field("Output", attempt.get("output", ""), indent=2)
        writer.blank()
    for note in payload.get("notes", PERSISTENCE_NOTES):
        writer.emit(note)


def render_invocation(payload: dict[str, Any]) -> None:
    writer = terminal_format.TerminalWriter()
    writer.emit("Contract-driven validator invocation plan")
    writer.blank()
    writer.field("Kind", payload.get("kind", INVOCATION_PLAN_KIND))
    writer.field("Authority", "none")
    if COMPARE_OPERATION in payload:
        section = payload[COMPARE_OPERATION]
        writer.blank()
        writer.field("Compare", section.get("operation", COMPARE_OPERATION))
        writer.field("Samples", section.get("sample_size", ""), indent=2)
    if BENCHMARK_OPERATION in payload:
        section = payload[BENCHMARK_OPERATION]
        writer.blank()
        writer.field("Benchmark", section.get("operation", BENCHMARK_OPERATION))
        writer.field(
            "Concurrency",
            " ".join(str(item) for item in section.get("concurrency", [])),
            indent=2,
        )
        writer.field("Requests", section.get("num_requests", ""), indent=2)
        if "prompt_style" in section:
            writer.field("Prompts", section["prompt_style"], indent=2)
    writer.blank()
    writer.emit("This plan does not launch a server or change default run-gates.")


def _evidence_content_digests(
    spec: dict[str, Any], *, repo_root: Path
) -> dict[str, str]:
    digests: dict[str, str] = {}
    for source in spec["evidence_sources"]:
        path = resolve_publishable_evidence(
            source["repository_path"],
            repo_root=repo_root,
            label="evidence source",
        )
        data = read_stable_bytes(path, label="evidence source")
        digests[source["source_key"]] = sha256_bytes(data)
    return digests


def _validate_generated_spec(
    spec: dict[str, Any],
    *,
    release_plan_dir: Path,
    repo_root: Path,
) -> None:
    try:
        model_serving_release_capture.build_capture_from_plan(
            release_plan_dir=release_plan_dir,
            attempt_spec=spec,
            repo_root=repo_root,
        )
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(f"generated attempt spec is invalid: {exc}")


def cmd_compose(args: argparse.Namespace) -> int:
    if not args.release_plan or not args.context or not args.output_dir:
        fail("compose requires --release-plan DIR --context FILE --output-dir DIR")
    repo_root = _repo_root(Path(args.repo_root))
    context = load_attempt_context(Path(args.context))
    specs, measurement_digests = compose_attempt_specs(
        release_plan_dir=Path(args.release_plan),
        context=context,
        compare_measurement_path=(
            Path(args.compare_measurement) if args.compare_measurement else None
        ),
        benchmark_measurement_path=(
            Path(args.benchmark_measurement) if args.benchmark_measurement else None
        ),
        repo_root=repo_root,
    )
    output_dir = validate_attempt_output_dir(
        Path(args.output_dir), repo_root=repo_root
    )
    for spec in specs.values():
        _validate_generated_spec(
            spec,
            release_plan_dir=Path(args.release_plan),
            repo_root=repo_root,
        )
    for operation, spec in specs.items():
        if (
            _evidence_content_digests(spec, repo_root=repo_root)
            != measurement_digests[operation]
        ):
            fail(
                "evidence changed during composition; regenerate measurements "
                "and compose again"
            )
    files = {
        ATTEMPT_SPEC_NAMES[operation]: model_identity.pretty_json_bytes(spec)
        for operation, spec in specs.items()
    }
    try:
        model_serving_release_capture.publish_candidate_tree(output_dir, files)
    except model_serving_release_capture.ModelServingReleaseCaptureError as exc:
        fail(str(exc))
    written = [
        {
            "operation": operation,
            "attempt_id": spec["attempt"]["attempt_id"],
            "completion": spec["attempt"]["completion"],
            "attempted_criterion_ids": list(
                spec["attempt"]["attempted_criterion_ids"]
            ),
            "output": ATTEMPT_SPEC_NAMES[operation],
        }
        for operation, spec in specs.items()
    ]
    payload = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "ok": True,
        "command": "compose",
        "authority": "none",
        "privacy_review": "pending",
        "promotion_authorized": False,
        "release_id": specs[COMPARE_OPERATION]["release_id"],
        "contract_id": specs[COMPARE_OPERATION]["contract_id"],
        "attempts": written,
        "notes": list(PERSISTENCE_NOTES),
    }
    if args.json:
        emit_json(payload)
    else:
        render_compose(payload)
    return 0


def cmd_plan_invocation(args: argparse.Namespace) -> int:
    if not args.release_plan:
        fail("plan-invocation requires --release-plan DIR")
    repo_root = _repo_root(Path(args.repo_root))
    _release, contract = load_verified_release_and_contract(Path(args.release_plan))
    document = plan_invocation(contract)
    if args.output:
        dest = validate_attempt_output_file(Path(args.output), repo_root=repo_root)
        atomic_write_json(dest, document)
    if args.json or not args.output:
        if args.json:
            emit_json(document)
        else:
            render_invocation(document)
    else:
        render_invocation(document)
    return 0


def cmd_bench_argv(args: argparse.Namespace) -> int:
    if not args.invocation_plan:
        fail("bench-argv requires --invocation-plan FILE")
    plan = load_invocation_plan(Path(args.invocation_plan))
    tokens = bench_argv(plan)
    if not tokens:
        fail("invocation plan produced no bench arguments")
    for token in tokens:
        print(token)
    return 0


def cmd_check_measurement_dir(args: argparse.Namespace) -> int:
    if not args.path:
        fail("check-measurement-dir requires --path DIR")
    validate_measurement_dir(Path(args.path), repo_root=Path(args.repo_root))
    return 0


def cmd_help(_args: argparse.Namespace) -> int:
    writer = terminal_format.TerminalWriter()
    for line in (
        "Compose draft ADR 0004 attempt-only specs",
        "",
        "Usage:",
        "scripts/model-serving-release-attempt.sh compose --release-plan DIR --context FILE --output-dir DIR [--compare-measurement FILE] [--benchmark-measurement FILE] [--json]",
        "scripts/model-serving-release-attempt.sh plan-invocation --release-plan DIR [--output FILE] [--json]",
        "scripts/model-serving-release-attempt.sh bench-argv --invocation-plan FILE",
        "",
        "Repository-local outputs must live under",
        "experiments/model-serving-release-attempts/. Explicit paths outside",
        "the repository are allowed; models/, .git, and the repository root",
        "are refused. Evidence sources in this slice must be publishable",
        "results/ files naming the same stably read measurement file.",
        "Each physical attempt context must also bind one closed",
        "observe-resources summary with the exact attempt window and scope.",
        "It is run diagnostic evidence and never a criterion or review file.",
        "The attempt spec has no precomputed digest; later capture re-reads.",
        "Missing validator output is refused; this tool does not invent it.",
        "It emits attempt-only specs. It does not capture evidence, issue a",
        "decision, write the tracked registry, or launch a release.",
    ):
        if line == "":
            writer.blank()
        else:
            writer.emit(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compose ADR 0004 attempt-only specs from validator measurements"
    )
    parser.add_argument("--repo-root", default=str(_REPO_ROOT), help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    help_cmd = subparsers.add_parser("help", help="Show attempt-composition help")
    help_cmd.set_defaults(func=cmd_help)

    compose = subparsers.add_parser(
        "compose", help="Compose compare and benchmark attempt-only specs"
    )
    compose.add_argument("--release-plan")
    compose.add_argument("--context")
    compose.add_argument("--compare-measurement")
    compose.add_argument("--benchmark-measurement")
    compose.add_argument("--output-dir")
    compose.add_argument("--json", action="store_true")
    compose.set_defaults(func=cmd_compose)

    plan = subparsers.add_parser(
        "plan-invocation",
        help="Emit an explicit contract-driven validator invocation plan",
    )
    plan.add_argument("--release-plan")
    plan.add_argument("--output")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan_invocation)

    argv_cmd = subparsers.add_parser(
        "bench-argv",
        help="Print bench_serve argv tokens from an invocation plan",
    )
    argv_cmd.add_argument("--invocation-plan")
    argv_cmd.set_defaults(func=cmd_bench_argv)

    check_dir = subparsers.add_parser(
        "check-measurement-dir",
        help="Validate a run-gates measurement directory",
    )
    check_dir.add_argument("--path")
    check_dir.set_defaults(func=cmd_check_measurement_dir)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        ModelServingReleaseAttemptError,
        ValidatorMeasurementError,
        immutable_descriptor_dir.ImmutableDescriptorDirectoryError,
        model_serving_release_plan.ModelServingReleasePlanError,
        model_serving_release.ModelServingReleaseError,
        model_serving_release_capture.ModelServingReleaseCaptureError,
        model_validation_evidence.ModelValidationEvidenceError,
        model_identity.ModelIdentityError,
        OSError,
    ) as exc:
        message = model_serving_release_capture.sanitize_error(
            str(exc), repo_root=Path(args.repo_root)
        )
        print(f"model-serving-release-attempt: ERROR: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
