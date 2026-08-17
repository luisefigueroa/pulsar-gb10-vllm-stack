#!/usr/bin/env python3
"""Pure model-release identity schemas, builders, and validators.

This module owns the content-addressed trust documents shared by model-library
enforcement and maintainer release tooling. It deliberately performs no
profile sourcing, repository writes, network access, or trust-root promotion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import pathlib
import re
from typing import Any


SNAPSHOT_MANIFEST_SCHEMA_VERSION = 1
SNAPSHOT_MANIFEST_KIND = "model-library-snapshot-manifest"
SNAPSHOT_INTEGRITY_SCHEME = "sha256-snapshot-manifest-v1"
EXPECTED_MODEL_SEAL_SCHEMA_VERSION = 1
EXPECTED_MODEL_SEAL_KIND = "pulsar-expected-model-seal"
VALIDATION_BUNDLE_SCHEMA_VERSION = 1
VALIDATION_BUNDLE_KIND = "pulsar-validation-bundle"

SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
HF_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
HF_MODEL_ID_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
IMAGE_DIGEST_RE = re.compile(r"@sha256:([0-9a-f]{64})$")
SAFE_REV = re.compile(r"^[A-Za-z0-9._-]+$")


class ModelIdentityError(ValueError):
    """A malformed or mismatched model identity contract."""


def fail(message: str) -> None:
    raise ModelIdentityError(message)


def pretty_json_bytes(value: Any) -> bytes:
    """Return deterministic pretty JSON bytes for published candidate files.

    Identity digests stay on compact ``canonical_json_digest``. This encoder is
    the shared publication form used by unreviewed planner and capture
    candidates and by staged issuance proposals.
    """
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def canonical_json_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def expected_model_seal_identity(seal: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in seal.items() if key != "seal_id"}


def expected_model_seal_id(seal: dict[str, Any]) -> str:
    return canonical_json_digest(expected_model_seal_identity(seal))


def validation_bundle_identity(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bundle.items() if key != "bundle_id"}


def validation_bundle_id(bundle: dict[str, Any]) -> str:
    return canonical_json_digest(validation_bundle_identity(bundle))


def _validate_rfc3339_utc(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail(f"{label} must be an RFC3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail(f"{label} must be an RFC3339 UTC timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        fail(f"{label} must include UTC")
    return value


def _normalize_decimal(
    value: Any,
    *,
    label: str,
    allow_empty: bool = False,
) -> str | None:
    if allow_empty and (value is None or value == ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        fail(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        fail(f"{label} must be numeric")
    if not parsed.is_finite() or parsed < 0:
        fail(f"{label} must be a non-negative finite number")
    normalized = format(parsed, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    if normalized in {"", "-0"}:
        normalized = "0"
    return normalized


def _validate_string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or "\x00" in item for item in value
    ):
        fail(f"{label} must be a list of strings")
    return list(value)


def _engine_arg_value(args: list[str], flag: str, default: str) -> str:
    for index, item in enumerate(args):
        if item == flag:
            if index + 1 >= len(args):
                fail(f"profile contract {flag} requires a value")
            return args[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return default


def build_profile_contract(
    *,
    model_id: str,
    served_name: str,
    image: str,
    nodes: int,
    port: int,
    gpu_mem_util: str,
    engine_args: list[str],
    container_env: list[str],
    spec_decode_args: list[str],
    recommended_spec: bool,
    profile_purpose: str,
    topology_class: str,
    min_rails_per_pair: int,
    weights_gib: str | None = None,
    weights_ram_gib: str | None = None,
    kv_gib: str | None = None,
    overhead_gib: str | None = None,
    mem_min_free_gib: str | None = None,
) -> dict[str, Any]:
    """Build the canonical behavior/safety contract for one sourced profile."""
    if HF_MODEL_ID_RE.fullmatch(model_id or "") is None:
        fail("profile contract model_id must be an exact Hugging Face repository ID")
    if not isinstance(served_name, str) or not served_name:
        fail("profile contract served_name is invalid")
    image_match = IMAGE_DIGEST_RE.search(image or "")
    if image_match is None:
        fail("profile contract image must be pinned by @sha256 digest")
    if not isinstance(nodes, int) or isinstance(nodes, bool) or nodes < 1:
        fail("profile contract nodes must be positive")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        fail("profile contract port is invalid")
    engine_args = _validate_string_list(
        engine_args, label="profile contract engine_args"
    )
    container_env = _validate_string_list(
        container_env, label="profile contract container_env"
    )
    spec_decode_args = _validate_string_list(
        spec_decode_args, label="profile contract spec_decode_args"
    )
    if not isinstance(recommended_spec, bool):
        fail("profile contract recommended_spec must be boolean")
    if recommended_spec and not spec_decode_args:
        fail("profile contract recommended_spec requires spec_decode_args")
    if profile_purpose not in {"serving", "diagnostic"}:
        fail("profile contract profile_purpose is invalid")

    try:
        tp = int(_engine_arg_value(engine_args, "--tensor-parallel-size", "1"))
        pp = int(_engine_arg_value(engine_args, "--pipeline-parallel-size", "1"))
    except ValueError:
        fail("profile contract tensor/pipeline parallel size must be integer")
    if tp < 1 or pp < 1 or tp * pp != nodes:
        fail("profile contract TP x PP must equal nodes")
    if nodes == 1:
        if topology_class != "single" or min_rails_per_pair != 0:
            fail("single-node profile contract requires single topology and zero rails")
    else:
        if topology_class != "roce-full-mesh" or min_rails_per_pair < 1:
            fail(
                "multi-node profile contract requires roce-full-mesh and positive rails"
            )
        if _engine_arg_value(
            engine_args, "--distributed-executor-backend", ""
        ) != "mp":
            fail("multi-node profile contract requires distributed backend mp")

    normalized_gpu = _normalize_decimal(
        gpu_mem_util, label="profile contract gpu_mem_util"
    )
    assert normalized_gpu is not None
    if Decimal(normalized_gpu) <= 0 or Decimal(normalized_gpu) > 1:
        fail("profile contract gpu_mem_util must be greater than zero and at most one")
    memory_values = {
        "weights_gib": weights_gib,
        "weights_ram_gib": weights_ram_gib,
        "kv_gib": kv_gib,
        "overhead_gib": overhead_gib,
        "mem_min_free_gib": mem_min_free_gib,
    }
    memory_policy = {
        key: _normalize_decimal(
            value,
            label=f"profile contract {key}",
            allow_empty=True,
        )
        for key, value in memory_values.items()
    }
    return {
        "model_id": model_id,
        "served_name": served_name,
        "image": {
            "reference": image,
            "digest": "sha256:" + image_match.group(1),
        },
        "runtime": {
            "port": port,
            "gpu_mem_util": normalized_gpu,
            "engine_args": engine_args,
            "container_env": container_env,
            "spec_decode_args": spec_decode_args,
            "recommended_spec": recommended_spec,
        },
        "geometry": {
            "nodes": nodes,
            "tensor_parallel_size": tp,
            "pipeline_parallel_size": pp,
            "topology_class": topology_class,
            "min_rails_per_pair": min_rails_per_pair,
        },
        "profile_purpose": profile_purpose,
        "memory_policy": memory_policy,
    }


def validate_profile_contract_document(contract: Any) -> dict[str, Any]:
    if not isinstance(contract, dict) or set(contract) != {
        "model_id",
        "served_name",
        "image",
        "runtime",
        "geometry",
        "profile_purpose",
        "memory_policy",
    }:
        fail("validation bundle profile_contract fields are invalid")
    image = contract.get("image")
    runtime = contract.get("runtime")
    geometry = contract.get("geometry")
    memory = contract.get("memory_policy")
    if not isinstance(image, dict) or set(image) != {"reference", "digest"}:
        fail("validation bundle profile_contract image is invalid")
    if not isinstance(runtime, dict) or set(runtime) != {
        "port",
        "gpu_mem_util",
        "engine_args",
        "container_env",
        "spec_decode_args",
        "recommended_spec",
    }:
        fail("validation bundle profile_contract runtime is invalid")
    if not isinstance(geometry, dict) or set(geometry) != {
        "nodes",
        "tensor_parallel_size",
        "pipeline_parallel_size",
        "topology_class",
        "min_rails_per_pair",
    }:
        fail("validation bundle profile_contract geometry is invalid")
    if not isinstance(memory, dict) or set(memory) != {
        "weights_gib",
        "weights_ram_gib",
        "kv_gib",
        "overhead_gib",
        "mem_min_free_gib",
    }:
        fail("validation bundle profile_contract memory_policy is invalid")
    rebuilt = build_profile_contract(
        model_id=contract.get("model_id"),
        served_name=contract.get("served_name"),
        image=image.get("reference"),
        nodes=geometry.get("nodes"),
        port=runtime.get("port"),
        gpu_mem_util=runtime.get("gpu_mem_util"),
        engine_args=runtime.get("engine_args"),
        container_env=runtime.get("container_env"),
        spec_decode_args=runtime.get("spec_decode_args"),
        recommended_spec=runtime.get("recommended_spec"),
        profile_purpose=contract.get("profile_purpose"),
        topology_class=geometry.get("topology_class"),
        min_rails_per_pair=geometry.get("min_rails_per_pair"),
        weights_gib=memory.get("weights_gib"),
        weights_ram_gib=memory.get("weights_ram_gib"),
        kv_gib=memory.get("kv_gib"),
        overhead_gib=memory.get("overhead_gib"),
        mem_min_free_gib=memory.get("mem_min_free_gib"),
    )
    if rebuilt != contract:
        fail("validation bundle profile_contract is not canonical")
    if image.get("digest") != rebuilt["image"]["digest"]:
        fail("validation bundle profile_contract image digest differs from reference")
    return contract


def _validate_evidence_path(
    value: Any,
    *,
    label: str = "expected seal evidence",
) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} path must be a non-empty string")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        fail(f"{label} path must be repository-relative: {value!r}")
    return value


def _validate_validation_bundle_model(item: Any, *, index: int) -> dict[str, Any]:
    required = {
        "role",
        "model_id",
        "revision_kind",
        "snapshot_revision",
        "manifest",
    }
    if not isinstance(item, dict) or set(item) != required:
        fail(f"validation bundle models[{index}] fields are invalid")
    role = item.get("role")
    if not isinstance(role, str) or SAFE_REV.fullmatch(role) is None:
        fail(f"validation bundle models[{index}].role is invalid")
    model_id = item.get("model_id")
    if not isinstance(model_id, str) or HF_MODEL_ID_RE.fullmatch(model_id) is None:
        fail(f"validation bundle models[{index}].model_id is invalid")
    if item.get("revision_kind") != "huggingface-commit":
        fail(
            f"validation bundle models[{index}].revision_kind "
            "must be huggingface-commit"
        )
    revision = item.get("snapshot_revision")
    if not isinstance(revision, str) or HF_COMMIT_RE.fullmatch(revision) is None:
        fail(f"validation bundle models[{index}].snapshot_revision is invalid")
    manifest = item.get("manifest")
    if not isinstance(manifest, dict) or set(manifest) != {"scheme", "manifest_id"}:
        fail(f"validation bundle models[{index}].manifest is invalid")
    if manifest.get("scheme") != SNAPSHOT_INTEGRITY_SCHEME:
        fail(f"validation bundle models[{index}].manifest scheme is unsupported")
    digest = manifest.get("manifest_id")
    if not isinstance(digest, str) or SHA256_HEX_RE.fullmatch(digest) is None:
        fail(f"validation bundle models[{index}].manifest_id is invalid")
    return item


def _validate_external_artifact(item: Any, *, index: int) -> dict[str, Any]:
    required = {"role", "artifact_id", "revision", "digest"}
    if not isinstance(item, dict) or set(item) != required:
        fail(f"validation bundle external_artifacts[{index}] fields are invalid")
    role = item.get("role")
    if role not in {"tokenizer", "draft-model", "adapter", "model-code", "other"}:
        fail(f"validation bundle external_artifacts[{index}].role is invalid")
    for field in ("artifact_id", "revision"):
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            fail(f"validation bundle external_artifacts[{index}].{field} is invalid")
    digest = item.get("digest")
    if not isinstance(digest, dict) or set(digest) != {"scheme", "value"}:
        fail(f"validation bundle external_artifacts[{index}].digest is invalid")
    if digest.get("scheme") != "sha256":
        fail(
            f"validation bundle external_artifacts[{index}].digest scheme "
            "is unsupported"
        )
    value = digest.get("value")
    if not isinstance(value, str) or SHA256_HEX_RE.fullmatch(value) is None:
        fail(f"validation bundle external_artifacts[{index}].digest value is invalid")
    return item


def validate_validation_bundle(
    bundle: Any,
    *,
    profile: str | None = None,
    expected_seal: dict[str, Any] | None = None,
    expected_profile_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one repository-reviewed immutable validation bundle."""
    required = {
        "schema_version",
        "kind",
        "profile",
        "models",
        "external_artifacts",
        "profile_contract",
        "evidence",
        "provenance",
        "bundle_id",
    }
    if not isinstance(bundle, dict) or set(bundle) != required:
        missing = sorted(required - set(bundle)) if isinstance(bundle, dict) else []
        extra = sorted(set(bundle) - required) if isinstance(bundle, dict) else []
        fail(f"validation bundle fields differ (missing={missing}, extra={extra})")
    if bundle.get("schema_version") != VALIDATION_BUNDLE_SCHEMA_VERSION:
        fail("validation bundle schema_version is unsupported")
    if bundle.get("kind") != VALIDATION_BUNDLE_KIND:
        fail("validation bundle kind is invalid")
    bundle_profile = bundle.get("profile")
    if (
        not isinstance(bundle_profile, str)
        or SAFE_REV.fullmatch(bundle_profile) is None
    ):
        fail("validation bundle profile is invalid")
    if profile and bundle_profile != profile:
        fail(
            f"validation bundle profile differs: "
            f"bundle={bundle_profile} profile={profile}"
        )

    models = bundle.get("models")
    if not isinstance(models, list) or not models:
        fail("validation bundle models must be a non-empty list")
    roles: set[str] = set()
    model_identities: set[tuple[str, str]] = set()
    for index, item in enumerate(models):
        _validate_validation_bundle_model(item, index=index)
        role = item["role"]
        identity = (item["model_id"], item["snapshot_revision"])
        if role in roles:
            fail(f"validation bundle model role is duplicated: {role}")
        if identity in model_identities:
            fail("validation bundle model identity is duplicated")
        roles.add(role)
        model_identities.add(identity)
    primary_models = [item for item in models if item["role"] == "primary"]
    if len(primary_models) != 1:
        fail("validation bundle must contain exactly one primary model")

    external = bundle.get("external_artifacts")
    if not isinstance(external, list):
        fail("validation bundle external_artifacts must be a list")
    external_keys: set[tuple[str, str, str]] = set()
    for index, item in enumerate(external):
        _validate_external_artifact(item, index=index)
        key = (item["role"], item["artifact_id"], item["revision"])
        if key in external_keys:
            fail("validation bundle external artifact is duplicated")
        external_keys.add(key)

    contract = validate_profile_contract_document(bundle.get("profile_contract"))
    primary = primary_models[0]
    if primary["model_id"] != contract["model_id"]:
        fail("validation bundle primary model differs from profile_contract")

    evidence = bundle.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        fail("validation bundle must include evidence")
    checked_evidence = [
        _validate_evidence_path(item, label="validation bundle evidence")
        for item in evidence
    ]
    if len(set(checked_evidence)) != len(checked_evidence):
        fail("validation bundle evidence contains duplicates")

    provenance = bundle.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "issuer",
        "issued_at",
    }:
        fail("validation bundle provenance fields are invalid")
    issuer = provenance.get("issuer")
    if not isinstance(issuer, str) or not issuer.strip():
        fail("validation bundle issuer is invalid")
    _validate_rfc3339_utc(
        provenance.get("issued_at"), label="validation bundle issued_at"
    )

    bundle_digest = bundle.get("bundle_id")
    if (
        not isinstance(bundle_digest, str)
        or SHA256_HEX_RE.fullmatch(bundle_digest) is None
        or bundle_digest != validation_bundle_id(bundle)
    ):
        fail("validation bundle identity mismatch")

    if expected_profile_contract is not None:
        expected_profile_contract = validate_profile_contract_document(
            expected_profile_contract
        )
        if contract != expected_profile_contract:
            fail("validation bundle profile contract differs from live profile")

    if expected_seal is not None:
        expected_seal = validate_expected_model_seal(
            expected_seal,
            profile=bundle_profile,
            model_id=primary["model_id"],
        )
        seal_model = {
            "model_id": expected_seal["model_id"],
            "revision_kind": expected_seal["revision_kind"],
            "snapshot_revision": expected_seal["snapshot_revision"],
            "manifest": expected_seal["manifest"],
        }
        bundle_model = {key: value for key, value in primary.items() if key != "role"}
        if seal_model != bundle_model:
            fail("validation bundle primary model differs from expected seal")
        seal_provenance = expected_seal["provenance"]
        if seal_provenance["validation_bundle_id"] != bundle_digest:
            fail("expected seal validation_bundle_id differs from bundle")
        if seal_provenance["issuer"] != provenance["issuer"]:
            fail("expected seal issuer differs from validation bundle")
        if seal_provenance["issued_at"] != provenance["issued_at"]:
            fail("expected seal issued_at differs from validation bundle")
        if seal_provenance["evidence"] != evidence:
            fail("expected seal evidence differs from validation bundle")
    return bundle


def validation_bundle_projection(bundle: dict[str, Any]) -> dict[str, Any]:
    bundle = validate_validation_bundle(bundle)
    contract = bundle["profile_contract"]
    return {
        "bundle_id": bundle["bundle_id"],
        "profile": bundle["profile"],
        "image_digest": contract["image"]["digest"],
        "nodes": contract["geometry"]["nodes"],
        "topology_class": contract["geometry"]["topology_class"],
    }


def validate_expected_model_seal(
    seal: Any,
    *,
    profile: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Validate a repository-reviewed lab-issued expected model seal."""
    if not isinstance(seal, dict):
        fail("expected model seal must be an object")
    required = {
        "schema_version",
        "kind",
        "profile",
        "model_id",
        "revision_kind",
        "snapshot_revision",
        "manifest",
        "provenance",
        "seal_id",
    }
    if set(seal) != required:
        missing = sorted(required - set(seal))
        extra = sorted(set(seal) - required)
        fail(f"expected model seal fields differ (missing={missing}, extra={extra})")
    if seal.get("schema_version") != EXPECTED_MODEL_SEAL_SCHEMA_VERSION:
        fail("expected model seal schema_version is unsupported")
    if seal.get("kind") != EXPECTED_MODEL_SEAL_KIND:
        fail("expected model seal kind is invalid")
    seal_profile = seal.get("profile")
    if not isinstance(seal_profile, str) or not seal_profile:
        fail("expected model seal profile is invalid")
    if profile and seal_profile != profile:
        fail(
            f"expected model seal profile differs: "
            f"seal={seal_profile} profile={profile}"
        )
    seal_model = seal.get("model_id")
    if not isinstance(seal_model, str) or HF_MODEL_ID_RE.fullmatch(seal_model) is None:
        fail("expected model seal model_id must be an exact Hugging Face repository ID")
    if model_id and seal_model != model_id:
        fail(
            f"expected model seal model_id differs: "
            f"seal={seal_model} profile={model_id}"
        )
    if seal.get("revision_kind") != "huggingface-commit":
        fail("expected model seal revision_kind must be huggingface-commit")
    revision = seal.get("snapshot_revision")
    if not isinstance(revision, str) or HF_COMMIT_RE.fullmatch(revision) is None:
        fail("expected model seal snapshot_revision must be an immutable HF commit")

    manifest = seal.get("manifest")
    if not isinstance(manifest, dict) or set(manifest) != {"scheme", "manifest_id"}:
        fail("expected model seal manifest fields are invalid")
    if manifest.get("scheme") != SNAPSHOT_INTEGRITY_SCHEME:
        fail("expected model seal manifest scheme is unsupported")
    if not isinstance(manifest.get("manifest_id"), str) or SHA256_HEX_RE.fullmatch(
        manifest["manifest_id"]
    ) is None:
        fail("expected model seal manifest_id is invalid")

    provenance = seal.get("provenance")
    provenance_fields = {
        "validation_bundle_id",
        "issuer",
        "issued_at",
        "evidence",
    }
    if not isinstance(provenance, dict) or set(provenance) != provenance_fields:
        fail("expected model seal provenance fields are invalid")
    bundle_id = provenance.get("validation_bundle_id")
    if not isinstance(bundle_id, str) or SHA256_HEX_RE.fullmatch(bundle_id) is None:
        fail("expected model seal validation_bundle_id is invalid")
    issuer = provenance.get("issuer")
    if not isinstance(issuer, str) or not issuer.strip():
        fail("expected model seal issuer is invalid")
    _validate_rfc3339_utc(
        provenance.get("issued_at"), label="expected model seal issued_at"
    )
    evidence = provenance.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        fail("expected model seal provenance must include evidence")
    for item in evidence:
        _validate_evidence_path(item)

    seal_id = seal.get("seal_id")
    if not isinstance(seal_id, str) or SHA256_HEX_RE.fullmatch(seal_id) is None:
        fail("expected model seal seal_id is invalid")
    if seal_id != expected_model_seal_id(seal):
        fail("expected model seal identity mismatch")
    return seal


def expected_model_seal_projection(seal: dict[str, Any]) -> dict[str, Any]:
    seal = validate_expected_model_seal(seal)
    return {
        "seal_id": seal["seal_id"],
        "validation_bundle_id": seal["provenance"]["validation_bundle_id"],
        "model_id": seal["model_id"],
        "snapshot_revision": seal["snapshot_revision"],
        "manifest_id": seal["manifest"]["manifest_id"],
    }


def build_validation_bundle(
    *,
    profile: str,
    models: list[dict[str, Any]],
    external_artifacts: list[dict[str, Any]],
    profile_contract: dict[str, Any],
    evidence: list[str],
    issuer: str,
    issued_at: str,
) -> dict[str, Any]:
    """Build and validate a content-addressed validation bundle candidate."""
    bundle: dict[str, Any] = {
        "schema_version": VALIDATION_BUNDLE_SCHEMA_VERSION,
        "kind": VALIDATION_BUNDLE_KIND,
        "profile": profile,
        "models": models,
        "external_artifacts": external_artifacts,
        "profile_contract": profile_contract,
        "evidence": evidence,
        "provenance": {"issuer": issuer, "issued_at": issued_at},
    }
    bundle["bundle_id"] = validation_bundle_id(bundle)
    return validate_validation_bundle(bundle, profile=profile)


def build_expected_model_seal(
    *,
    profile: str,
    model_id: str,
    snapshot_revision: str,
    manifest_id: str,
    validation_bundle: dict[str, Any],
) -> dict[str, Any]:
    """Build and cross-check an expected-seal candidate from its bundle."""
    bundle = validate_validation_bundle(validation_bundle, profile=profile)
    provenance = bundle["provenance"]
    seal: dict[str, Any] = {
        "schema_version": EXPECTED_MODEL_SEAL_SCHEMA_VERSION,
        "kind": EXPECTED_MODEL_SEAL_KIND,
        "profile": profile,
        "model_id": model_id,
        "revision_kind": "huggingface-commit",
        "snapshot_revision": snapshot_revision,
        "manifest": {
            "scheme": SNAPSHOT_INTEGRITY_SCHEME,
            "manifest_id": manifest_id,
        },
        "provenance": {
            "validation_bundle_id": bundle["bundle_id"],
            "issuer": provenance["issuer"],
            "issued_at": provenance["issued_at"],
            "evidence": bundle["evidence"],
        },
    }
    seal["seal_id"] = expected_model_seal_id(seal)
    validate_validation_bundle(bundle, expected_seal=seal)
    return validate_expected_model_seal(
        seal,
        profile=profile,
        model_id=model_id,
    )
