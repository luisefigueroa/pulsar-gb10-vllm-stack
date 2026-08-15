#!/usr/bin/env python3
"""Canonical Model Serving Release and Validation Contract schemas.

This module implements the first machine-readable objects from ADR 0004.  It
is intentionally pure: it performs no profile sourcing, filesystem writes,
network access, evidence collection, status assignment, or trusted issuance.
Legacy schema-1 seals and validation bundles remain owned by model_identity.py.
"""

from __future__ import annotations

import copy
import re
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from scripts import model_identity
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]


MODEL_SERVING_RELEASE_SCHEMA_VERSION = 1
MODEL_SERVING_RELEASE_KIND = "pulsar-model-serving-release"
VALIDATION_CONTRACT_SCHEMA_VERSION = 1
VALIDATION_CONTRACT_KIND = "pulsar-validation-contract"

ARTIFACT_KINDS = {
    "huggingface-snapshot",
    "content-addressed-model",
    "digest-artifact",
}
PRIMARY_MODEL_ARTIFACT_KINDS = {
    "huggingface-snapshot",
    "content-addressed-model",
}
ARTIFACT_USES = {
    "primary-model",
    "draft-model",
    "tokenizer",
    "tokenizer-override",
    "adapter",
    "model-code",
    "supplemental-head",
    "other",
}
MODEL_ACCESS_CONTRACTS = {
    "local-verified-readonly",
    "live-remote-readonly",
}

CORE_VALIDATION_DIMENSIONS = (
    "stability",
    "accuracy",
    "throughput",
    "latency",
)
REQUIRED_VALIDATION_PREREQUISITES = (
    "strict-same-boot",
    "provenance-security",
    "serving-integration",
    "physical-geometry",
)
VALIDATION_DIMENSION_QUALIFICATION_SCOPES = {
    "stability": "model-qualification",
    "accuracy": "model-qualification",
    "throughput": "model-qualification",
    "latency": "model-qualification",
    "strict-same-boot": "model-qualification",
    "provenance-security": "release-promotion",
    "serving-integration": "serving-integration",
    "physical-geometry": "release-promotion",
}
VALIDATION_DIMENSIONS = set(VALIDATION_DIMENSION_QUALIFICATION_SCOPES)
QUALIFICATION_SCOPES = {
    "catalog-artifact",
    "serving-integration",
    "model-qualification",
    "release-promotion",
}
THRESHOLD_OPERATORS = {"eq", "gt", "gte", "lt", "lte"}
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NUMERIC_VERSION_PATTERN = r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*))*"
NUMERIC_VERSION_RE = re.compile(rf"^{NUMERIC_VERSION_PATTERN}$")
NUMERIC_VERSION_RANGE_RE = re.compile(
    rf"^>=({NUMERIC_VERSION_PATTERN}),<({NUMERIC_VERSION_PATTERN})$"
)
DEPLOYED_VERSION_RE = re.compile(
    r"^(?P<numeric>[0-9]+(?:\.[0-9]+)*)"
    r"(?:[-+~][A-Za-z0-9][A-Za-z0-9.+~_-]*)?$"
)
HIGH_RISK_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:bearer|basic)\s+\S+|"
    r"(?:^|[^A-Za-z0-9])(?:password|passwd|passphrase|secret|token|"
    r"credential|authorization|api[_-]?key|access[_-]?key|"
    r"private[_-]?key)\s*[:=]|"
    r"[:=]\s*[\"']?(?:password|passwd|passphrase|secret|token|"
    r"credential|authorization|api[_-]?key|access[_-]?key|"
    r"private[_-]?key)(?:$|[^A-Za-z0-9])|"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"hf_[A-Za-z0-9]{30,64}(?![A-Za-z0-9_-])|"
    r"(?:gh[opusr]_|github_pat_)[A-Za-z0-9_]{20,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"(?:^|[^A-Za-z0-9])eyJ[A-Za-z0-9_-]+\."
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:$|[^A-Za-z0-9])"
    r")"
)
ABSOLUTE_SITE_PATH_RE = re.compile(
    r"(?:^|[\s=,:;\"'(\[{])(?:~[/\\]|/(?!/)|[A-Za-z]:[/\\]|\\\\)"
)
DEPLOYMENT_REFERENCE_RE = re.compile(
    r"(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|"
    r"%[A-Za-z_][A-Za-z0-9_]*%|"
    r"(?:^|[\s=,:;\"'(\[{])\.\.[/\\])"
)
URI_ENDPOINT_RE = re.compile(r"(?i)\b[A-Za-z][A-Za-z0-9+.-]*://")
IPV4_VALUE_RE = re.compile(
    r"(?:^|[^0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?:$|[^0-9])"
)
IPV6_VALUE_RE = re.compile(
    r"(?i)(?:^|[\s=\[('\"])(?:[0-9a-f]{0,4}:){2,}"
    r"[0-9a-f]{0,4}(?:$|[\s\])'\",}])"
)
PRIVATE_ENDPOINT_VALUE_RE = re.compile(
    r"(?i)(?:^|[\s=,:;\"'(\[])"
    r"(?:localhost|(?:node|host|topology|site|lab)[-_.][A-Za-z0-9-]+|"
    r"[A-Za-z][A-Za-z0-9_-]*:[0-9]{1,5}|"
    r"[A-Za-z0-9-]+\.(?:local|lan|internal))"
    r"(?:$|[\s,;\"')\]}])"
)
PRIVATE_VALUE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:address|cache[_ -]?path|endpoint|"
    r"home[_ -]?node[_ -]?id|host(?:name|[_ -]?id)?|interface|"
    r"ip(?:[_ -]?address)?|mount[_ -]?path|node[_ -]?id|"
    r"serial(?:[_ -]?number)?|ssh[_ -]?(?:alias|host)|"
    r"topology[_ -]?id)\s*[:=]"
)
PRIVATE_ENV_PARTS = {
    "CREDENTIAL",
    "CREDENTIALS",
    "PASSWORD",
    "SECRET",
    "TOKEN",
}
PLACEMENT_ENV_NAMES = {
    "GLOO_SOCKET_IFNAME",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HOME",
    "MASTER_ADDR",
    "MASTER_PORT",
    "MODEL_PATH",
    "NCCL_SOCKET_IFNAME",
    "TMP",
    "TMPDIR",
    "TORCH_HOME",
    "TRANSFORMERS_CACHE",
    "VLLM_CACHE_ROOT",
    "XDG_CACHE_HOME",
}
STRUCTURED_ENGINE_FLAGS = {
    "--draft-tensor-parallel-size",
    "--gpu-memory-utilization",
    "--num-speculative-tokens",
    "--pipeline-parallel-size",
    "--speculative-config",
    "--speculative-model",
    "--tensor-parallel-size",
    "-pp",
    "-tp",
}

# These values belong only in protected run evidence.  Exact schema field sets
# already reject them at fixed levels; this denylist also protects extensible
# workload/protocol parameter maps.
PRIVATE_FIELD_NAMES = {
    "address",
    "addresses",
    "cache_path",
    "home_node_id",
    "host",
    "host_id",
    "hostname",
    "hostnames",
    "interface",
    "interfaces",
    "ip",
    "ip_address",
    "ip_addresses",
    "mount_path",
    "node_id",
    "node_ids",
    "path",
    "paths",
    "serial",
    "serial_number",
    "ssh_alias",
    "ssh_host",
    "topology_id",
    "topology_ids",
}
CREDENTIAL_FIELD_NAME_SUFFIXES = {
    "ACCESSKEY",
    "ACCESSTOKEN",
    "APIKEY",
    "AUTHORIZATION",
    "AUTHTOKEN",
    "BEARER",
    "BEARERTOKEN",
    "CLIENTSECRET",
    "CREDENTIAL",
    "CREDENTIALS",
    "GITHUBTOKEN",
    "HFTOKEN",
    "PASSWORD",
    "PASSPHRASE",
    "PASSWD",
    "PRIVATEKEY",
    "SECRET",
}


class ModelServingReleaseError(ValueError):
    """A release descriptor or frozen Validation Contract is invalid."""


def fail(message: str) -> None:
    raise ModelServingReleaseError(message)


def _require_fields(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        missing = sorted(fields - set(value)) if isinstance(value, dict) else []
        extra = sorted(set(value) - fields) if isinstance(value, dict) else []
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        fail(f"{label} must be a non-empty string")
    return _validate_public_string_value(value, label=label)


def _safe_identifier(value: Any, *, label: str) -> str:
    value = _nonempty_string(value, label=label)
    if model_identity.SAFE_REV.fullmatch(value) is None:
        fail(f"{label} is invalid")
    return value


def _content_id(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(value) is None
    ):
        fail(f"{label} must be a sha256 content ID")
    return value


def parse_numeric_version(
    value: Any,
    *,
    label: str = "numeric version",
) -> tuple[int, ...]:
    """Parse a canonical dependency-free dotted numeric version."""
    if not isinstance(value, str) or NUMERIC_VERSION_RE.fullmatch(value) is None:
        fail(f"{label} must be a canonical dotted numeric version")
    return tuple(int(component) for component in value.split("."))


def parse_deployed_version(
    value: Any,
    *,
    label: str = "deployed version",
) -> tuple[int, ...]:
    """Parse an observed vendor version while preserving its raw evidence value."""
    if not isinstance(value, str):
        fail(f"{label} must be a dotted numeric version with optional vendor suffix")
    match = DEPLOYED_VERSION_RE.fullmatch(value)
    if match is None:
        fail(f"{label} must be a dotted numeric version with optional vendor suffix")
    return tuple(int(component) for component in match.group("numeric").split("."))


def _compare_numeric_versions(
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def parse_numeric_version_range(
    value: Any,
    *,
    label: str = "numeric version range",
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Parse the canonical inclusive-low/exclusive-high range grammar."""
    if not isinstance(value, str):
        fail(f"{label} must use canonical >=LOW,<HIGH numeric grammar")
    match = NUMERIC_VERSION_RANGE_RE.fullmatch(value)
    if match is None:
        fail(f"{label} must use canonical >=LOW,<HIGH numeric grammar")
    lower = parse_numeric_version(match.group(1), label=f"{label} lower endpoint")
    upper = parse_numeric_version(match.group(2), label=f"{label} upper endpoint")
    if _compare_numeric_versions(lower, upper) >= 0:
        fail(f"{label} lower endpoint must be less than upper endpoint")
    return lower, upper


def numeric_version_in_range(
    observed_version: Any,
    version_range: Any,
    *,
    label: str = "numeric version",
) -> bool:
    """Return whether an observed dotted numeric version is in a frozen range."""
    observed = parse_deployed_version(observed_version, label=label)
    lower, upper = parse_numeric_version_range(
        version_range,
        label=f"{label} range",
    )
    return (
        _compare_numeric_versions(observed, lower) >= 0
        and _compare_numeric_versions(observed, upper) < 0
    )


def _positive_integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{label} must be a positive integer")
    return value


def _nonnegative_integer(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        fail(f"{label} must be a non-negative integer")
    return value


def _canonical_decimal(
    value: Any,
    *,
    label: str,
    allow_none: bool = False,
    positive: bool = False,
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
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        qualifier = "positive" if positive else "non-negative"
        fail(f"{label} must be a {qualifier} finite number")
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        normalized = "0"
    if require_canonical and value != normalized:
        fail(f"{label} is not canonical (expected {normalized!r})")
    return normalized


def _string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list):
        fail(f"{label} must be a list")
    return [_nonempty_string(item, label=f"{label} item") for item in value]


def _sorted_unique_identifiers(
    value: Any,
    *,
    label: str,
    allow_empty: bool = True,
) -> list[str]:
    values = _string_list(value, label=label)
    for item in values:
        _safe_identifier(item, label=f"{label} item")
    if not allow_empty and not values:
        fail(f"{label} must not be empty")
    if values != sorted(values) or len(values) != len(set(values)):
        fail(f"{label} must be sorted and unique")
    return values


def _validate_container_env(value: Any) -> list[str]:
    values = _string_list(value, label="serving recipe container_env")
    names: list[str] = []
    for item in values:
        name, separator, env_value = item.partition("=")
        if not separator or ENV_NAME_RE.fullmatch(name) is None:
            fail("serving recipe container_env items must be NAME=value")
        upper_name = name.upper()
        name_parts = upper_name.split("_")
        has_private_part = bool(PRIVATE_ENV_PARTS.intersection(name_parts))
        has_api_key = any(
            name_parts[index : index + 2] == ["API", "KEY"]
            for index in range(len(name_parts) - 1)
        )
        if (
            upper_name in PLACEMENT_ENV_NAMES
            or has_private_part
            or has_api_key
        ):
            fail(
                "serving recipe container_env contains a credential or "
                f"deployment-only name: {name}"
            )
        _validate_public_string_value(
            env_value,
            label=f"serving recipe container_env value for {name}",
        )
        names.append(name)
    if values != sorted(values) or len(values) != len(set(values)):
        fail("serving recipe container_env must be sorted and unique")
    if len(names) != len(set(names)):
        fail("serving recipe container_env assigns a name more than once")
    return values


def _validate_remaining_engine_args(value: Any) -> list[str]:
    values = _string_list(value, label="serving recipe engine_args")
    for item in values:
        _validate_public_string_value(
            item,
            label="serving recipe engine_args item",
        )
        for flag in STRUCTURED_ENGINE_FLAGS:
            if item == flag or item.startswith(flag + "="):
                fail(
                    f"serving recipe engine_args repeats structured field {flag}"
                )
    return values


def validate_public_string_value(value: Any, *, label: str) -> str:
    """Reject recognized private, secret, or deployment-only string data.

    This structural screen is the public schema helper for free-form public
    strings.  It does not prove that unknown private identifiers are absent.
    """
    if not isinstance(value, str):
        fail(f"{label} must be a string")
    if "\x00" in value:
        fail(f"{label} contains a NUL byte")
    if (
        HIGH_RISK_SECRET_VALUE_RE.search(value)
        or ABSOLUTE_SITE_PATH_RE.search(value)
        or DEPLOYMENT_REFERENCE_RE.search(value)
        or URI_ENDPOINT_RE.search(value)
        or IPV4_VALUE_RE.search(value)
        or IPV6_VALUE_RE.search(value)
        or PRIVATE_ENDPOINT_VALUE_RE.search(value)
        or PRIVATE_VALUE_ASSIGNMENT_RE.search(value)
    ):
        fail(f"{label} contains private, secret, or deployment-only data")
    return value


def _validate_public_string_value(value: str, *, label: str) -> str:
    return validate_public_string_value(value, label=label)


def _is_credential_field_name(value: str) -> bool:
    normalized = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    return any(
        normalized == marker or normalized.endswith(marker)
        for marker in CREDENTIAL_FIELD_NAME_SUFFIXES
    )


def _validate_public_json(value: Any, *, label: str) -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _validate_public_string_value(value, label=label)
        return
    if isinstance(value, float):
        fail(f"{label} must encode decimals as canonical strings, not floats")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_public_json(item, label=f"{label}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                fail(f"{label} has an invalid object key")
            _validate_public_string_value(key, label=f"{label} object key")
            if _is_credential_field_name(key):
                fail(f"{label} contains credential-bearing field {key!r}")
            if key.lower() in PRIVATE_FIELD_NAMES:
                fail(f"{label} contains private field {key!r}")
            _validate_public_json(item, label=f"{label}.{key}")
        return
    fail(f"{label} contains unsupported JSON value {type(value).__name__}")


def _validate_artifact(artifact: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        fail(f"model artifact set artifacts[{index}] must be an object")
    kind = artifact.get("kind")
    if kind not in ARTIFACT_KINDS:
        fail(f"model artifact set artifacts[{index}].kind is unsupported")
    _safe_identifier(
        artifact.get("artifact_key"),
        label=f"model artifact set artifacts[{index}].artifact_key",
    )
    if kind == "huggingface-snapshot":
        _require_fields(
            artifact,
            {
                "artifact_key",
                "kind",
                "model_id",
                "revision_kind",
                "snapshot_revision",
                "manifest",
            },
            label=f"model artifact set artifacts[{index}]",
        )
        model_id = artifact.get("model_id")
        if (
            not isinstance(model_id, str)
            or model_identity.HF_MODEL_ID_RE.fullmatch(model_id) is None
        ):
            fail(f"model artifact set artifacts[{index}].model_id is invalid")
        if artifact.get("revision_kind") != "huggingface-commit":
            fail(
                f"model artifact set artifacts[{index}].revision_kind "
                "must be huggingface-commit"
            )
        revision = artifact.get("snapshot_revision")
        if (
            not isinstance(revision, str)
            or model_identity.HF_COMMIT_RE.fullmatch(revision) is None
        ):
            fail(
                f"model artifact set artifacts[{index}].snapshot_revision "
                "is invalid"
            )
        manifest = _require_fields(
            artifact.get("manifest"),
            {"scheme", "manifest_id"},
            label=f"model artifact set artifacts[{index}].manifest",
        )
        if manifest.get("scheme") != model_identity.SNAPSHOT_INTEGRITY_SCHEME:
            fail(
                f"model artifact set artifacts[{index}].manifest scheme "
                "is unsupported"
            )
        manifest_id = manifest.get("manifest_id")
        if (
            not isinstance(manifest_id, str)
            or model_identity.SHA256_HEX_RE.fullmatch(manifest_id) is None
        ):
            fail(f"model artifact set artifacts[{index}].manifest_id is invalid")
        return artifact

    if kind == "content-addressed-model":
        _require_fields(
            artifact,
            {
                "artifact_key",
                "kind",
                "artifact_id",
                "revision",
                "manifest",
            },
            label=f"model artifact set artifacts[{index}]",
        )
        artifact_id = _nonempty_string(
            artifact.get("artifact_id"),
            label=f"model artifact set artifacts[{index}].artifact_id",
        )
        revision = _nonempty_string(
            artifact.get("revision"),
            label=f"model artifact set artifacts[{index}].revision",
        )
        _validate_public_string_value(
            artifact_id,
            label=f"model artifact set artifacts[{index}].artifact_id",
        )
        _validate_public_string_value(
            revision,
            label=f"model artifact set artifacts[{index}].revision",
        )
        manifest = _require_fields(
            artifact.get("manifest"),
            {"scheme", "manifest_id"},
            label=f"model artifact set artifacts[{index}].manifest",
        )
        if manifest.get("scheme") != model_identity.SNAPSHOT_INTEGRITY_SCHEME:
            fail(
                f"model artifact set artifacts[{index}].manifest scheme "
                "is unsupported"
            )
        manifest_id = manifest.get("manifest_id")
        if (
            not isinstance(manifest_id, str)
            or model_identity.SHA256_HEX_RE.fullmatch(manifest_id) is None
        ):
            fail(f"model artifact set artifacts[{index}].manifest_id is invalid")
        return artifact

    _require_fields(
        artifact,
        {"artifact_key", "kind", "artifact_id", "revision", "digest"},
        label=f"model artifact set artifacts[{index}]",
    )
    _nonempty_string(
        artifact.get("artifact_id"),
        label=f"model artifact set artifacts[{index}].artifact_id",
    )
    _nonempty_string(
        artifact.get("revision"),
        label=f"model artifact set artifacts[{index}].revision",
    )
    digest = _require_fields(
        artifact.get("digest"),
        {"scheme", "value"},
        label=f"model artifact set artifacts[{index}].digest",
    )
    if digest.get("scheme") != "sha256":
        fail(f"model artifact set artifacts[{index}].digest scheme is unsupported")
    digest_value = digest.get("value")
    if (
        not isinstance(digest_value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(digest_value) is None
    ):
        fail(f"model artifact set artifacts[{index}].digest value is invalid")
    return artifact


def build_model_artifact_set(artifacts: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a canonical, order-independent Model Artifact Set."""
    if not isinstance(artifacts, list):
        fail("model artifact set artifacts must be a list")
    if any(not isinstance(item, dict) for item in artifacts):
        fail("model artifact set artifacts must contain only objects")
    normalized = copy.deepcopy(artifacts)
    normalized.sort(key=lambda item: str(item.get("artifact_key", "")))
    result = {"artifacts": normalized}
    return validate_model_artifact_set(result)


def validate_model_artifact_set(value: Any) -> dict[str, Any]:
    artifact_set = _require_fields(
        value,
        {"artifacts"},
        label="model artifact set",
    )
    artifacts = artifact_set.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        fail("model artifact set artifacts must be a non-empty list")
    keys: list[str] = []
    for index, artifact in enumerate(artifacts):
        _validate_artifact(artifact, index=index)
        keys.append(artifact["artifact_key"])
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail("model artifact set artifacts must be sorted by unique artifact_key")
    return artifact_set


def build_serving_recipe(
    *,
    artifact_bindings: list[dict[str, str]],
    engine_args: list[str],
    container_env: list[str],
    gpu_memory_utilization: str,
    spec_decode_args: list[str],
    spec_decode_enabled_by_default: bool,
    model_access_contract: str,
    tensor_parallel_size: int,
    pipeline_parallel_size: int,
    weights_ram_gib: str | None,
    kv_gib: str | None,
    overhead_gib: str | None,
    mem_min_free_gib: str | None,
    engine: str = "vllm-openai",
) -> dict[str, Any]:
    """Build a normalized serving recipe without deployment-only metadata."""
    if not isinstance(artifact_bindings, list) or any(
        not isinstance(item, dict) for item in artifact_bindings
    ):
        fail("serving recipe artifact_bindings must be a list of objects")
    for label, values in (
        ("engine_args", engine_args),
        ("container_env", container_env),
        ("spec_decode_args", spec_decode_args),
    ):
        if not isinstance(values, list) or any(
            not isinstance(item, str) for item in values
        ):
            fail(f"serving recipe {label} must be a list of strings")
    bindings = copy.deepcopy(artifact_bindings)
    bindings.sort(
        key=lambda item: (
            str(item.get("artifact_key", "")),
            str(item.get("use", "")),
        )
    )
    memory_policy = {
        "weights_ram_gib": _canonical_decimal(
            weights_ram_gib,
            label="serving recipe memory_policy.weights_ram_gib",
            allow_none=True,
            require_canonical=False,
        ),
        "kv_gib": _canonical_decimal(
            kv_gib,
            label="serving recipe memory_policy.kv_gib",
            allow_none=True,
            require_canonical=False,
        ),
        "overhead_gib": _canonical_decimal(
            overhead_gib,
            label="serving recipe memory_policy.overhead_gib",
            allow_none=True,
            require_canonical=False,
        ),
        "mem_min_free_gib": _canonical_decimal(
            mem_min_free_gib,
            label="serving recipe memory_policy.mem_min_free_gib",
            allow_none=True,
            require_canonical=False,
        ),
    }
    result = {
        "engine": engine,
        "engine_args": copy.deepcopy(engine_args),
        "container_env": sorted(copy.deepcopy(container_env)),
        "parallelism": {
            "tensor_parallel_size": tensor_parallel_size,
            "pipeline_parallel_size": pipeline_parallel_size,
        },
        "gpu_memory_utilization": _canonical_decimal(
            gpu_memory_utilization,
            label="serving recipe gpu_memory_utilization",
            positive=True,
            require_canonical=False,
        ),
        "speculative_decoding": {
            "arguments": copy.deepcopy(spec_decode_args),
            "enabled_by_default": spec_decode_enabled_by_default,
        },
        "memory_policy": memory_policy,
        "model_access_contract": model_access_contract,
        "artifact_bindings": bindings,
    }
    return validate_serving_recipe(result)


def _validate_artifact_bindings(
    value: Any,
    *,
    artifact_set: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail("serving recipe artifact_bindings must be a non-empty list")
    keys: list[str] = []
    pairs: list[tuple[str, str]] = []
    primary_keys: list[str] = []
    for index, binding in enumerate(value):
        binding = _require_fields(
            binding,
            {"artifact_key", "use"},
            label=f"serving recipe artifact_bindings[{index}]",
        )
        key = _safe_identifier(
            binding.get("artifact_key"),
            label=f"serving recipe artifact_bindings[{index}].artifact_key",
        )
        use = binding.get("use")
        if use not in ARTIFACT_USES:
            fail(f"serving recipe artifact_bindings[{index}].use is unsupported")
        keys.append(key)
        pairs.append((key, use))
        if use == "primary-model":
            primary_keys.append(key)
    if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
        fail("serving recipe artifact_bindings must be sorted and unique")
    if len(keys) != len(set(keys)):
        fail("serving recipe binds an artifact_key more than once")
    if len(primary_keys) != 1:
        fail("serving recipe requires exactly one primary-model binding")
    if artifact_set is not None:
        artifact_set = validate_model_artifact_set(artifact_set)
        artifacts = {item["artifact_key"]: item for item in artifact_set["artifacts"]}
        if set(keys) != set(artifacts):
            fail("serving recipe artifact_bindings differ from Model Artifact Set")
        if artifacts[primary_keys[0]]["kind"] not in PRIMARY_MODEL_ARTIFACT_KINDS:
            fail(
                "serving recipe primary-model must bind a complete "
                "content-addressed model"
            )
    return value


def validate_serving_recipe(
    value: Any,
    *,
    artifact_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recipe = _require_fields(
        value,
        {
            "engine",
            "engine_args",
            "container_env",
            "parallelism",
            "gpu_memory_utilization",
            "speculative_decoding",
            "memory_policy",
            "model_access_contract",
            "artifact_bindings",
        },
        label="serving recipe",
    )
    if recipe.get("engine") != "vllm-openai":
        fail("serving recipe engine is unsupported")
    _validate_remaining_engine_args(recipe.get("engine_args"))
    _validate_container_env(recipe.get("container_env"))
    parallelism = _require_fields(
        recipe.get("parallelism"),
        {"tensor_parallel_size", "pipeline_parallel_size"},
        label="serving recipe parallelism",
    )
    _positive_integer(
        parallelism.get("tensor_parallel_size"),
        label="serving recipe parallelism.tensor_parallel_size",
    )
    _positive_integer(
        parallelism.get("pipeline_parallel_size"),
        label="serving recipe parallelism.pipeline_parallel_size",
    )
    gpu = _canonical_decimal(
        recipe.get("gpu_memory_utilization"),
        label="serving recipe gpu_memory_utilization",
        positive=True,
    )
    assert gpu is not None
    if Decimal(gpu) > 1:
        fail("serving recipe gpu_memory_utilization must be at most one")
    speculative = _require_fields(
        recipe.get("speculative_decoding"),
        {"arguments", "enabled_by_default"},
        label="serving recipe speculative_decoding",
    )
    arguments = _string_list(
        speculative.get("arguments"),
        label="serving recipe speculative_decoding.arguments",
    )
    for argument in arguments:
        _validate_public_string_value(
            argument,
            label="serving recipe speculative_decoding.arguments item",
        )
    enabled = speculative.get("enabled_by_default")
    if not isinstance(enabled, bool):
        fail("serving recipe speculative_decoding.enabled_by_default must be boolean")
    if enabled and not arguments:
        fail("serving recipe enabled speculative decoding requires arguments")
    memory = _require_fields(
        recipe.get("memory_policy"),
        {"weights_ram_gib", "kv_gib", "overhead_gib", "mem_min_free_gib"},
        label="serving recipe memory_policy",
    )
    for field in sorted(memory):
        _canonical_decimal(
            memory.get(field),
            label=f"serving recipe memory_policy.{field}",
            allow_none=True,
        )
    if recipe.get("model_access_contract") not in MODEL_ACCESS_CONTRACTS:
        fail("serving recipe model_access_contract is unsupported")
    _validate_artifact_bindings(
        recipe.get("artifact_bindings"),
        artifact_set=artifact_set,
    )
    return recipe


def build_runtime_image_identity(
    *,
    image_reference: str,
    architecture: str,
    driver_abi_family: str,
    driver_abi_range: str,
    container_runtime_family: str,
    container_runtime_range: str,
    required_container_capabilities: list[str],
    kernel_range: str,
    required_kernel_features: list[str],
) -> dict[str, Any]:
    """Build an image digest plus reusable host-compatibility envelope."""
    if not isinstance(image_reference, str):
        fail("runtime/image identity image_reference must be a string")
    if not isinstance(required_container_capabilities, list) or any(
        not isinstance(item, str) for item in required_container_capabilities
    ):
        fail("runtime/image identity required_container_capabilities must be strings")
    if not isinstance(required_kernel_features, list) or any(
        not isinstance(item, str) for item in required_kernel_features
    ):
        fail("runtime/image identity required_kernel_features must be strings")
    match = model_identity.IMAGE_DIGEST_RE.search(image_reference)
    if match is None:
        fail("runtime/image identity image_reference must be digest-pinned")
    result = {
        "image": {"digest": "sha256:" + match.group(1)},
        "host_compatibility": {
            "architecture": architecture,
            "driver_abi": {
                "family": driver_abi_family,
                "range": driver_abi_range,
            },
            "container_runtime": {
                "family": container_runtime_family,
                "range": container_runtime_range,
                "required_capabilities": sorted(required_container_capabilities),
            },
            "kernel": {
                "range": kernel_range,
                "required_features": sorted(required_kernel_features),
            },
        },
    }
    return validate_runtime_image_identity(result)


def validate_runtime_image_identity(value: Any) -> dict[str, Any]:
    identity = _require_fields(
        value,
        {"image", "host_compatibility"},
        label="runtime/image identity",
    )
    image = _require_fields(
        identity.get("image"),
        {"digest"},
        label="runtime/image identity image",
    )
    digest = image.get("digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or model_identity.SHA256_HEX_RE.fullmatch(digest[7:]) is None
    ):
        fail("runtime/image identity image digest is invalid")
    compatibility = _require_fields(
        identity.get("host_compatibility"),
        {"architecture", "driver_abi", "container_runtime", "kernel"},
        label="runtime/image identity host_compatibility",
    )
    _safe_identifier(
        compatibility.get("architecture"),
        label="runtime/image identity host_compatibility.architecture",
    )
    driver = _require_fields(
        compatibility.get("driver_abi"),
        {"family", "range"},
        label="runtime/image identity host_compatibility.driver_abi",
    )
    _safe_identifier(
        driver.get("family"),
        label="runtime/image identity host_compatibility.driver_abi.family",
    )
    parse_numeric_version_range(
        driver.get("range"),
        label="runtime/image identity host_compatibility.driver_abi.range",
    )
    runtime = _require_fields(
        compatibility.get("container_runtime"),
        {"family", "range", "required_capabilities"},
        label="runtime/image identity host_compatibility.container_runtime",
    )
    _safe_identifier(
        runtime.get("family"),
        label="runtime/image identity host_compatibility.container_runtime.family",
    )
    parse_numeric_version_range(
        runtime.get("range"),
        label="runtime/image identity host_compatibility.container_runtime.range",
    )
    _sorted_unique_identifiers(
        runtime.get("required_capabilities"),
        label=(
            "runtime/image identity host_compatibility.container_runtime."
            "required_capabilities"
        ),
        allow_empty=False,
    )
    kernel = _require_fields(
        compatibility.get("kernel"),
        {"range", "required_features"},
        label="runtime/image identity host_compatibility.kernel",
    )
    parse_numeric_version_range(
        kernel.get("range"),
        label="runtime/image identity host_compatibility.kernel.range",
    )
    _sorted_unique_identifiers(
        kernel.get("required_features"),
        label="runtime/image identity host_compatibility.kernel.required_features",
    )
    return identity


def build_supported_hardware_geometry(
    *,
    hardware_class: str,
    architecture: str,
    node_count: int,
    accelerators_per_node: int,
    accelerator_count: int,
    tensor_parallel_size: int,
    pipeline_parallel_size: int,
    topology_class: str,
    interconnect_class: str,
    minimum_rails_per_pair: int,
    minimum_unified_memory_gib_per_node: str,
) -> dict[str, Any]:
    result = {
        "hardware_class": hardware_class,
        "architecture": architecture,
        "node_count": node_count,
        "accelerators_per_node": accelerators_per_node,
        "accelerator_count": accelerator_count,
        "tensor_parallel_size": tensor_parallel_size,
        "pipeline_parallel_size": pipeline_parallel_size,
        "topology_class": topology_class,
        "interconnect_class": interconnect_class,
        "minimum_rails_per_pair": minimum_rails_per_pair,
        "capacity": {
            "minimum_unified_memory_gib_per_node": _canonical_decimal(
                minimum_unified_memory_gib_per_node,
                label=(
                    "supported hardware geometry capacity."
                    "minimum_unified_memory_gib_per_node"
                ),
                positive=True,
                require_canonical=False,
            )
        },
    }
    return validate_supported_hardware_geometry(result)


def validate_supported_hardware_geometry(value: Any) -> dict[str, Any]:
    geometry = _require_fields(
        value,
        {
            "hardware_class",
            "architecture",
            "node_count",
            "accelerators_per_node",
            "accelerator_count",
            "tensor_parallel_size",
            "pipeline_parallel_size",
            "topology_class",
            "interconnect_class",
            "minimum_rails_per_pair",
            "capacity",
        },
        label="supported hardware geometry",
    )
    for field in (
        "hardware_class",
        "architecture",
        "topology_class",
        "interconnect_class",
    ):
        _safe_identifier(
            geometry.get(field),
            label=f"supported hardware geometry {field}",
        )
    node_count = _positive_integer(
        geometry.get("node_count"),
        label="supported hardware geometry node_count",
    )
    accelerators_per_node = _positive_integer(
        geometry.get("accelerators_per_node"),
        label="supported hardware geometry accelerators_per_node",
    )
    accelerator_count = _positive_integer(
        geometry.get("accelerator_count"),
        label="supported hardware geometry accelerator_count",
    )
    tp = _positive_integer(
        geometry.get("tensor_parallel_size"),
        label="supported hardware geometry tensor_parallel_size",
    )
    pp = _positive_integer(
        geometry.get("pipeline_parallel_size"),
        label="supported hardware geometry pipeline_parallel_size",
    )
    rails = _nonnegative_integer(
        geometry.get("minimum_rails_per_pair"),
        label="supported hardware geometry minimum_rails_per_pair",
    )
    if accelerator_count != node_count * accelerators_per_node:
        fail("supported hardware geometry accelerator count differs from nodes")
    if tp * pp != accelerator_count:
        fail("supported hardware geometry TP x PP must equal accelerator_count")
    if node_count == 1:
        if (
            geometry.get("topology_class") != "single"
            or geometry.get("interconnect_class") != "local"
            or rails != 0
        ):
            fail("single-node hardware geometry requires single/local/zero rails")
    elif (
        geometry.get("topology_class") == "single"
        or geometry.get("interconnect_class") == "local"
        or rails < 1
    ):
        fail("multi-node hardware geometry requires an interconnect and rails")
    capacity = _require_fields(
        geometry.get("capacity"),
        {"minimum_unified_memory_gib_per_node"},
        label="supported hardware geometry capacity",
    )
    _canonical_decimal(
        capacity.get("minimum_unified_memory_gib_per_node"),
        label=(
            "supported hardware geometry capacity."
            "minimum_unified_memory_gib_per_node"
        ),
        positive=True,
    )
    return geometry


def model_serving_release_identity(release: dict[str, Any]) -> dict[str, Any]:
    """Return exactly the four ADR-0004 release-ID inputs."""
    return {
        "model_artifact_set": release["model_artifact_set"],
        "serving_recipe": release["serving_recipe"],
        "runtime_image_identity": release["runtime_image_identity"],
        "supported_hardware_geometry": release["supported_hardware_geometry"],
    }


def model_serving_release_id(release: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(model_serving_release_identity(release))


def supported_hardware_geometry_id(geometry: dict[str, Any]) -> str:
    geometry = validate_supported_hardware_geometry(geometry)
    return model_identity.canonical_json_digest(geometry)


def _validate_recipe_geometry(
    recipe: dict[str, Any],
    geometry: dict[str, Any],
) -> None:
    if recipe["parallelism"]["tensor_parallel_size"] != geometry[
        "tensor_parallel_size"
    ]:
        fail("serving recipe tensor parallelism differs from hardware geometry")
    if recipe["parallelism"]["pipeline_parallel_size"] != geometry[
        "pipeline_parallel_size"
    ]:
        fail("serving recipe pipeline parallelism differs from hardware geometry")


def _validate_runtime_geometry(
    runtime: dict[str, Any],
    geometry: dict[str, Any],
) -> None:
    runtime_architecture = runtime["host_compatibility"]["architecture"]
    if runtime_architecture != geometry["architecture"]:
        fail("runtime host architecture differs from hardware geometry")


def build_model_serving_release(
    *,
    model_artifact_set: dict[str, Any],
    serving_recipe: dict[str, Any],
    runtime_image_identity: dict[str, Any],
    supported_hardware_geometry: dict[str, Any],
) -> dict[str, Any]:
    artifact_set = validate_model_artifact_set(model_artifact_set)
    recipe = validate_serving_recipe(
        serving_recipe,
        artifact_set=artifact_set,
    )
    runtime = validate_runtime_image_identity(runtime_image_identity)
    geometry = validate_supported_hardware_geometry(supported_hardware_geometry)
    _validate_recipe_geometry(recipe, geometry)
    _validate_runtime_geometry(runtime, geometry)
    release: dict[str, Any] = {
        "schema_version": MODEL_SERVING_RELEASE_SCHEMA_VERSION,
        "kind": MODEL_SERVING_RELEASE_KIND,
        "model_artifact_set": copy.deepcopy(artifact_set),
        "serving_recipe": copy.deepcopy(recipe),
        "runtime_image_identity": copy.deepcopy(runtime),
        "supported_hardware_geometry": copy.deepcopy(geometry),
    }
    release["release_id"] = model_serving_release_id(release)
    return validate_model_serving_release(release)


def validate_model_serving_release(value: Any) -> dict[str, Any]:
    release = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "model_artifact_set",
            "serving_recipe",
            "runtime_image_identity",
            "supported_hardware_geometry",
            "release_id",
        },
        label="Model Serving Release",
    )
    if release.get("schema_version") != MODEL_SERVING_RELEASE_SCHEMA_VERSION:
        fail("Model Serving Release schema_version is unsupported")
    if release.get("kind") != MODEL_SERVING_RELEASE_KIND:
        fail("Model Serving Release kind is invalid")
    artifact_set = validate_model_artifact_set(release.get("model_artifact_set"))
    recipe = validate_serving_recipe(
        release.get("serving_recipe"),
        artifact_set=artifact_set,
    )
    runtime = validate_runtime_image_identity(release.get("runtime_image_identity"))
    geometry = validate_supported_hardware_geometry(
        release.get("supported_hardware_geometry")
    )
    _validate_recipe_geometry(recipe, geometry)
    _validate_runtime_geometry(runtime, geometry)
    release_id = release.get("release_id")
    if (
        not isinstance(release_id, str)
        or model_identity.SHA256_HEX_RE.fullmatch(release_id) is None
        or release_id != model_serving_release_id(release)
    ):
        fail("Model Serving Release identity mismatch")
    return release


def repository_validation_invariants() -> dict[str, Any]:
    """Return the immutable repository-wide validation policy for schema 1."""
    return {
        "policy": "pulsar-model-serving-validation-v1",
        "core_dimensions": list(CORE_VALIDATION_DIMENSIONS),
        "priority_order": list(CORE_VALIDATION_DIMENSIONS),
        "required_prerequisites": list(REQUIRED_VALIDATION_PREREQUISITES),
        "dimension_qualification_scopes": dict(
            VALIDATION_DIMENSION_QUALIFICATION_SCOPES
        ),
        "strict_same_boot": {
            "required": True,
            "comparison": "exact",
            "fp_equivalent_satisfies": False,
        },
        "provenance_security_review": {"required": True},
        "evidence": {
            "immutable_attempt_records": True,
            "preserve_failed": True,
            "preserve_interrupted": True,
            "preserve_inconclusive": True,
        },
        "fail_closed": {
            "missing_required_evidence": True,
            "prequalification_distribution_failure_status": "Untested",
        },
        "relative_performance": {
            "only_when_comparable_predecessor": True,
            "identical_protocol_required": True,
            "identical_geometry_required": True,
            "without_predecessor": "N/A",
        },
    }


def _validate_workload_or_protocol(value: Any, *, label: str) -> dict[str, Any]:
    document = _require_fields(
        value,
        {"name", "version", "parameters"},
        label=label,
    )
    _nonempty_string(document.get("name"), label=f"{label}.name")
    _nonempty_string(document.get("version"), label=f"{label}.version")
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        fail(f"{label}.parameters must be an object")
    _validate_public_json(parameters, label=f"{label}.parameters")
    return document


def _validate_threshold(value: Any, *, label: str) -> dict[str, Any]:
    threshold = _require_fields(
        value,
        {"metric", "operator", "value", "unit"},
        label=label,
    )
    _nonempty_string(threshold.get("metric"), label=f"{label}.metric")
    if threshold.get("operator") not in THRESHOLD_OPERATORS:
        fail(f"{label}.operator is unsupported")
    _nonempty_string(threshold.get("value"), label=f"{label}.value")
    _nonempty_string(threshold.get("unit"), label=f"{label}.unit")
    return threshold


def _threshold_sort_key(value: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("metric", "")),
        str(value.get("operator", "")),
        str(value.get("value", "")),
        str(value.get("unit", "")),
    )


def _canonicalize_criterion(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    thresholds = result.get("thresholds")
    if isinstance(thresholds, list):
        thresholds.sort(key=_threshold_sort_key)
    return result


def validate_validation_criterion(value: Any, *, index: int = 0) -> dict[str, Any]:
    label = f"Validation Contract criteria[{index}]"
    criterion = _require_fields(
        value,
        {
            "criterion_id",
            "dimension",
            "qualification_scope",
            "workload",
            "protocol",
            "sample_size",
            "thresholds",
        },
        label=label,
    )
    _safe_identifier(criterion.get("criterion_id"), label=f"{label}.criterion_id")
    dimension = criterion.get("dimension")
    if dimension not in VALIDATION_DIMENSIONS:
        fail(f"{label}.dimension is unsupported")
    qualification_scope = criterion.get("qualification_scope")
    if qualification_scope not in QUALIFICATION_SCOPES:
        fail(f"{label}.qualification_scope is unsupported")
    expected_scope = VALIDATION_DIMENSION_QUALIFICATION_SCOPES[dimension]
    if qualification_scope != expected_scope:
        fail(
            f"{label}.qualification_scope must be {expected_scope} "
            f"for dimension {dimension}"
        )
    _validate_workload_or_protocol(criterion.get("workload"), label=f"{label}.workload")
    _validate_workload_or_protocol(criterion.get("protocol"), label=f"{label}.protocol")
    _positive_integer(criterion.get("sample_size"), label=f"{label}.sample_size")
    thresholds = criterion.get("thresholds")
    if not isinstance(thresholds, list) or not thresholds:
        fail(f"{label}.thresholds must be a non-empty list")
    keys: list[tuple[str, str, str, str]] = []
    for threshold_index, threshold in enumerate(thresholds):
        _validate_threshold(
            threshold,
            label=f"{label}.thresholds[{threshold_index}]",
        )
        keys.append(_threshold_sort_key(threshold))
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail(f"{label}.thresholds must be sorted and unique")
    return criterion


def _validate_strict_same_boot_criterion(criterion: dict[str, Any]) -> None:
    parameters = criterion["protocol"]["parameters"]
    if (
        parameters.get("comparison") != "exact"
        or parameters.get("fp_equivalent_satisfies") is not False
    ):
        fail(
            "strict-same-boot criterion must require exact comparison and "
            "reject FP-equivalent output as a pass"
        )
    required_threshold = {
        "metric": "exact_match_rate",
        "operator": "eq",
        "value": "1",
        "unit": "ratio",
    }
    if required_threshold not in criterion["thresholds"]:
        fail("strict-same-boot criterion must require exact_match_rate == 1")


def provenance_security_criterion_template() -> dict[str, Any]:
    """Return the only review-derived provenance/security criterion shape."""
    return {
        "criterion_id": "provenance-security-review",
        "dimension": "provenance-security",
        "qualification_scope": "release-promotion",
        "workload": {
            "name": "bound-release-inputs",
            "version": "1",
            "parameters": {},
        },
        "protocol": {
            "name": "reviewed-provenance-security",
            "version": "1",
            "parameters": {"reviewed_issuance_required": True},
        },
        "sample_size": 1,
        "thresholds": [
            {
                "metric": "review_verdict",
                "operator": "eq",
                "value": "pass",
                "unit": "verdict",
            }
        ],
    }


def _validate_provenance_security_criterion(criterion: dict[str, Any]) -> None:
    if criterion != provenance_security_criterion_template():
        fail(
            "provenance-security criterion must match the canonical "
            "review-derived template"
        )


def benchmark_protocol_id(criterion: dict[str, Any]) -> str:
    criterion = validate_validation_criterion(criterion)
    return model_identity.canonical_json_digest(
        {
            "dimension": criterion["dimension"],
            "workload": criterion["workload"],
            "protocol": criterion["protocol"],
            "sample_size": criterion["sample_size"],
        }
    )


def no_comparable_predecessor() -> dict[str, str]:
    return {
        "status": "not-applicable",
        "reason": "no-comparable-predecessor",
    }


def build_relative_performance_requirement(
    *,
    release: dict[str, Any],
    predecessor_release_id: str,
    predecessor_contract_id: str,
    predecessor_bundle_id: str,
    predecessor_decision_id: str,
    throughput_criterion: dict[str, Any],
    throughput_predecessor_criterion_id: str,
    throughput_predecessor_run_record_id: str,
    latency_criterion: dict[str, Any],
    latency_predecessor_criterion_id: str,
    latency_predecessor_run_record_id: str,
    throughput_max_regression_percent: str,
    latency_max_regression_percent: str,
) -> dict[str, Any]:
    release = validate_model_serving_release(release)
    throughput_criterion = validate_validation_criterion(throughput_criterion)
    latency_criterion = validate_validation_criterion(latency_criterion)
    if throughput_criterion["dimension"] != "throughput":
        fail("relative performance throughput criterion has the wrong dimension")
    if latency_criterion["dimension"] != "latency":
        fail("relative performance latency criterion has the wrong dimension")
    result = {
        "status": "required",
        "predecessor_release_id": predecessor_release_id,
        "predecessor_contract_id": predecessor_contract_id,
        "predecessor_bundle_id": predecessor_bundle_id,
        "predecessor_decision_id": predecessor_decision_id,
        "supported_hardware_geometry_id": supported_hardware_geometry_id(
            release["supported_hardware_geometry"]
        ),
        "throughput": {
            "criterion_id": throughput_criterion["criterion_id"],
            "predecessor_criterion_id": throughput_predecessor_criterion_id,
            "predecessor_run_record_id": throughput_predecessor_run_record_id,
            "benchmark_protocol_id": benchmark_protocol_id(throughput_criterion),
            "maximum_regression_percent": _canonical_decimal(
                throughput_max_regression_percent,
                label="relative performance throughput maximum_regression_percent",
                require_canonical=False,
            ),
        },
        "latency": {
            "criterion_id": latency_criterion["criterion_id"],
            "predecessor_criterion_id": latency_predecessor_criterion_id,
            "predecessor_run_record_id": latency_predecessor_run_record_id,
            "benchmark_protocol_id": benchmark_protocol_id(latency_criterion),
            "maximum_regression_percent": _canonical_decimal(
                latency_max_regression_percent,
                label="relative performance latency maximum_regression_percent",
                require_canonical=False,
            ),
        },
    }
    _validate_relative_performance(
        result,
        release_id=release["release_id"],
        geometry_id=supported_hardware_geometry_id(
            release["supported_hardware_geometry"]
        ),
        criteria={
            throughput_criterion["criterion_id"]: throughput_criterion,
            latency_criterion["criterion_id"]: latency_criterion,
        },
    )
    return result


def _canonicalize_context_requirement(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    if result.get("status") == "required":
        criterion_ids = result.get("criterion_ids")
        if isinstance(criterion_ids, list):
            result["criterion_ids"] = sorted(criterion_ids)
        depths = result.get("depths")
        if isinstance(depths, list):
            normalized = [
                _canonical_decimal(
                    depth,
                    label="Validation Contract context depth",
                    require_canonical=False,
                )
                for depth in depths
            ]
            result["depths"] = sorted(normalized, key=lambda item: Decimal(item))
    return result


def _validate_context_requirement(
    value: Any,
    *,
    criteria: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("Validation Contract context_requirement must be an object")
    if value.get("status") == "not-applicable":
        _require_fields(
            value,
            {"status", "reason"},
            label="Validation Contract context_requirement",
        )
        _nonempty_string(
            value.get("reason"),
            label="Validation Contract context_requirement.reason",
        )
        return value
    _require_fields(
        value,
        {"status", "criterion_ids", "minimum_tokens", "depths"},
        label="Validation Contract context_requirement",
    )
    if value.get("status") != "required":
        fail("Validation Contract context_requirement status is unsupported")
    criterion_ids = _sorted_unique_identifiers(
        value.get("criterion_ids"),
        label="Validation Contract context_requirement.criterion_ids",
        allow_empty=False,
    )
    for criterion_id in criterion_ids:
        if criterion_id not in criteria:
            fail("Validation Contract context_requirement references unknown criterion")
    _positive_integer(
        value.get("minimum_tokens"),
        label="Validation Contract context_requirement.minimum_tokens",
    )
    depths = value.get("depths")
    if not isinstance(depths, list) or not depths:
        fail("Validation Contract context_requirement.depths must be non-empty")
    normalized: list[str] = []
    for depth in depths:
        item = _canonical_decimal(
            depth,
            label="Validation Contract context_requirement depth",
        )
        assert item is not None
        if not Decimal("0") <= Decimal(item) <= Decimal("1"):
            fail("Validation Contract context depth must be between zero and one")
        normalized.append(item)
    if normalized != sorted(normalized, key=Decimal) or len(normalized) != len(
        set(normalized)
    ):
        fail("Validation Contract context depths must be sorted and unique")
    return value


def _validate_soak_requirement(
    value: Any,
    *,
    criteria: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("Validation Contract soak_requirement must be an object")
    if value.get("status") == "not-applicable":
        _require_fields(
            value,
            {"status", "reason"},
            label="Validation Contract soak_requirement",
        )
        _nonempty_string(
            value.get("reason"),
            label="Validation Contract soak_requirement.reason",
        )
        return value
    _require_fields(
        value,
        {
            "status",
            "criterion_id",
            "minimum_duration_seconds",
            "concurrency",
            "maximum_request_errors",
        },
        label="Validation Contract soak_requirement",
    )
    if value.get("status") != "required":
        fail("Validation Contract soak_requirement status is unsupported")
    criterion_id = _safe_identifier(
        value.get("criterion_id"),
        label="Validation Contract soak_requirement.criterion_id",
    )
    if criterion_id not in criteria:
        fail("Validation Contract soak_requirement references unknown criterion")
    if criteria[criterion_id]["dimension"] != "stability":
        fail("Validation Contract soak_requirement must reference stability")
    _positive_integer(
        value.get("minimum_duration_seconds"),
        label="Validation Contract soak_requirement.minimum_duration_seconds",
    )
    _positive_integer(
        value.get("concurrency"),
        label="Validation Contract soak_requirement.concurrency",
    )
    _nonnegative_integer(
        value.get("maximum_request_errors"),
        label="Validation Contract soak_requirement.maximum_request_errors",
    )
    return value


def _validate_relative_dimension(
    value: Any,
    *,
    dimension: str,
    criteria: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    item = _require_fields(
        value,
        {
            "criterion_id",
            "predecessor_criterion_id",
            "predecessor_run_record_id",
            "benchmark_protocol_id",
            "maximum_regression_percent",
        },
        label=f"Validation Contract relative_performance.{dimension}",
    )
    criterion_id = _safe_identifier(
        item.get("criterion_id"),
        label=f"Validation Contract relative_performance.{dimension}.criterion_id",
    )
    criterion = criteria.get(criterion_id)
    if criterion is None or criterion["dimension"] != dimension:
        fail(
            f"Validation Contract relative_performance.{dimension} references "
            "the wrong criterion"
        )
    _safe_identifier(
        item.get("predecessor_criterion_id"),
        label=(
            f"Validation Contract relative_performance.{dimension}."
            "predecessor_criterion_id"
        ),
    )
    _content_id(
        item.get("predecessor_run_record_id"),
        label=(
            f"Validation Contract relative_performance.{dimension}."
            "predecessor_run_record_id"
        ),
    )
    protocol_id = item.get("benchmark_protocol_id")
    if (
        not isinstance(protocol_id, str)
        or model_identity.SHA256_HEX_RE.fullmatch(protocol_id) is None
        or protocol_id != benchmark_protocol_id(criterion)
    ):
        fail(
            f"Validation Contract relative_performance.{dimension} protocol "
            "identity mismatch"
        )
    _canonical_decimal(
        item.get("maximum_regression_percent"),
        label=(
            f"Validation Contract relative_performance.{dimension}."
            "maximum_regression_percent"
        ),
    )
    return item


def _validate_relative_performance(
    value: Any,
    *,
    release_id: str,
    geometry_id: str | None,
    criteria: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail("Validation Contract relative_performance must be an object")
    if value.get("status") == "not-applicable":
        _require_fields(
            value,
            {"status", "reason"},
            label="Validation Contract relative_performance",
        )
        if value.get("reason") != "no-comparable-predecessor":
            fail("relative performance N/A requires no-comparable-predecessor")
        return value
    relative = _require_fields(
        value,
        {
            "status",
            "predecessor_release_id",
            "predecessor_contract_id",
            "predecessor_bundle_id",
            "predecessor_decision_id",
            "supported_hardware_geometry_id",
            "throughput",
            "latency",
        },
        label="Validation Contract relative_performance",
    )
    if relative.get("status") != "required":
        fail("Validation Contract relative_performance status is unsupported")
    predecessor = _content_id(
        relative.get("predecessor_release_id"),
        label="Validation Contract predecessor_release_id",
    )
    if predecessor == release_id:
        fail("Validation Contract predecessor_release_id is invalid")
    for field in (
        "predecessor_contract_id",
        "predecessor_bundle_id",
        "predecessor_decision_id",
    ):
        _content_id(
            relative.get(field),
            label=f"Validation Contract {field}",
        )
    observed_geometry_id = relative.get("supported_hardware_geometry_id")
    if (
        not isinstance(observed_geometry_id, str)
        or model_identity.SHA256_HEX_RE.fullmatch(observed_geometry_id) is None
        or (geometry_id is not None and observed_geometry_id != geometry_id)
    ):
        fail("Validation Contract relative performance geometry mismatch")
    _validate_relative_dimension(
        relative.get("throughput"),
        dimension="throughput",
        criteria=criteria,
    )
    _validate_relative_dimension(
        relative.get("latency"),
        dimension="latency",
        criteria=criteria,
    )
    return relative


def validation_contract_identity(contract: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in contract.items() if key != "contract_id"}


def validation_contract_id(contract: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(validation_contract_identity(contract))


def _validate_release_criteria(
    value: Any,
    *,
    release_id: str,
    geometry_id: str | None,
) -> dict[str, Any]:
    release_criteria = _require_fields(
        value,
        {
            "criteria",
            "context_requirement",
            "soak_requirement",
            "relative_performance",
        },
        label="Validation Contract release_criteria",
    )
    criteria = release_criteria.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        fail("Validation Contract criteria must be a non-empty list")
    ids: list[str] = []
    dimensions: set[str] = set()
    criteria_by_id: dict[str, dict[str, Any]] = {}
    for index, criterion in enumerate(criteria):
        validate_validation_criterion(criterion, index=index)
        criterion_id = criterion["criterion_id"]
        ids.append(criterion_id)
        dimensions.add(criterion["dimension"])
        criteria_by_id[criterion_id] = criterion
        if criterion["dimension"] == "strict-same-boot":
            _validate_strict_same_boot_criterion(criterion)
        if criterion["dimension"] == "provenance-security":
            _validate_provenance_security_criterion(criterion)
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        fail("Validation Contract criteria must be sorted by unique criterion_id")
    missing_dimensions = VALIDATION_DIMENSIONS - dimensions
    if missing_dimensions:
        fail(
            "Validation Contract is missing required dimensions: "
            + ", ".join(sorted(missing_dimensions))
        )
    _validate_context_requirement(
        release_criteria.get("context_requirement"),
        criteria=criteria_by_id,
    )
    _validate_soak_requirement(
        release_criteria.get("soak_requirement"),
        criteria=criteria_by_id,
    )
    _validate_relative_performance(
        release_criteria.get("relative_performance"),
        release_id=release_id,
        geometry_id=geometry_id,
        criteria=criteria_by_id,
    )
    return release_criteria


def build_validation_contract(
    *,
    release: dict[str, Any],
    criteria: list[dict[str, Any]],
    context_requirement: dict[str, Any],
    soak_requirement: dict[str, Any],
    relative_performance: dict[str, Any],
) -> dict[str, Any]:
    """Freeze repository invariants and release-specific pass criteria."""
    release = validate_model_serving_release(release)
    if not isinstance(criteria, list) or any(
        not isinstance(item, dict) for item in criteria
    ):
        fail("Validation Contract criteria must be a list of objects")
    if not isinstance(context_requirement, dict):
        fail("Validation Contract context_requirement must be an object")
    if not isinstance(soak_requirement, dict):
        fail("Validation Contract soak_requirement must be an object")
    if not isinstance(relative_performance, dict):
        fail("Validation Contract relative_performance must be an object")
    normalized_criteria = [_canonicalize_criterion(item) for item in criteria]
    normalized_criteria.sort(key=lambda item: str(item.get("criterion_id", "")))
    contract: dict[str, Any] = {
        "schema_version": VALIDATION_CONTRACT_SCHEMA_VERSION,
        "kind": VALIDATION_CONTRACT_KIND,
        "release_id": release["release_id"],
        "repository_invariants": repository_validation_invariants(),
        "release_criteria": {
            "criteria": normalized_criteria,
            "context_requirement": _canonicalize_context_requirement(
                context_requirement
            ),
            "soak_requirement": copy.deepcopy(soak_requirement),
            "relative_performance": copy.deepcopy(relative_performance),
        },
    }
    _validate_release_criteria(
        contract["release_criteria"],
        release_id=release["release_id"],
        geometry_id=supported_hardware_geometry_id(
            release["supported_hardware_geometry"]
        ),
    )
    contract["contract_id"] = validation_contract_id(contract)
    return validate_validation_contract(contract, expected_release=release)


def validate_validation_contract(
    value: Any,
    *,
    expected_release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    contract = _require_fields(
        value,
        {
            "schema_version",
            "kind",
            "release_id",
            "repository_invariants",
            "release_criteria",
            "contract_id",
        },
        label="Validation Contract",
    )
    if contract.get("schema_version") != VALIDATION_CONTRACT_SCHEMA_VERSION:
        fail("Validation Contract schema_version is unsupported")
    if contract.get("kind") != VALIDATION_CONTRACT_KIND:
        fail("Validation Contract kind is invalid")
    release_id = contract.get("release_id")
    if (
        not isinstance(release_id, str)
        or model_identity.SHA256_HEX_RE.fullmatch(release_id) is None
    ):
        fail("Validation Contract release_id is invalid")
    geometry_id: str | None = None
    if expected_release is not None:
        release = validate_model_serving_release(expected_release)
        if release_id != release["release_id"]:
            fail("Validation Contract release_id differs from expected release")
        geometry_id = supported_hardware_geometry_id(
            release["supported_hardware_geometry"]
        )
    if contract.get("repository_invariants") != repository_validation_invariants():
        fail("Validation Contract repository invariants differ from policy")
    _validate_release_criteria(
        contract.get("release_criteria"),
        release_id=release_id,
        geometry_id=geometry_id,
    )
    contract_id = contract.get("contract_id")
    if (
        not isinstance(contract_id, str)
        or model_identity.SHA256_HEX_RE.fullmatch(contract_id) is None
        or contract_id != validation_contract_id(contract)
    ):
        fail("Validation Contract identity mismatch")
    return contract
