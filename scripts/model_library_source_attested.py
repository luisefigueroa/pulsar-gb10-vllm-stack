#!/usr/bin/env python3
"""Source-attested Hugging Face v1 acquisition contracts.

This module owns the closed version-1 source inventory, identity
precedence, privacy-safe approval, public plan, immutable receipt, and
offline verification helpers. It parses Hub metadata JSON and manages
site-local receipts, but it does not call the Hub, accept a token, refresh the
catalog, prepare a runtime view, launch, assign status, or issue a Model
Serving Release decision.

The sealed HOME_ACQUISITION_SCHEMA_VERSION=1 plan/result contracts remain
owned by model_library.py. This module is intentionally not imported from
that file so remote inspection can still stream model_library.py alone.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import pathlib
import re
import secrets
import stat
import sys
from typing import Any, NoReturn

try:
    from scripts import model_identity, model_serving_release
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]


SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION = 1
HF_V1_ACQUISITION_SOURCE_KIND = (
    "pulsar-model-library-huggingface-v1-acquisition-source"
)
ACQUISITION_IDENTITY_KIND = "pulsar-model-library-acquisition-identity"
SOURCE_ATTESTED_ACQUISITION_APPROVAL_KIND = (
    "pulsar-model-library-source-attested-acquisition-approval"
)
SOURCE_ATTESTED_ACQUISITION_PLAN_KIND = (
    "pulsar-model-library-source-attested-acquisition-plan"
)
SOURCE_ATTESTED_ACQUISITION_RECEIPT_KIND = (
    "pulsar-model-library-source-attested-acquisition-receipt"
)
SOURCE_ATTESTED_ACQUISITION_RESULT_KIND = (
    "pulsar-model-library-source-attested-acquisition-result"
)
SOURCE_ATTESTED_HOME_VERIFY_KIND = (
    "pulsar-model-library-source-attested-home-verify-result"
)
UNSUPPORTED_SOURCE_FORM = "unsupported Hugging Face source object form"
ALL_ZERO_GIT_OID = "0" * 40
ALL_ZERO_SHA256 = "0" * 64
HF_V1_ADAPTER_KIND = "huggingface-v1"
HF_V1_ADAPTER_VERSION = 1
HF_V1_REQUIRED_CLI = "hf"
HF_V1_REVISION_KIND = "huggingface-commit"
HF_V1_BLOB_GIT = "git-blob"
HF_V1_BLOB_LFS = "lfs-object"
HF_V1_BLOB_KINDS = {HF_V1_BLOB_GIT, HF_V1_BLOB_LFS}

IDENTITY_CLASS_REVIEWED_RELEASE = "reviewed-model-serving-release"
IDENTITY_CLASS_LEGACY_SEAL = "legacy-expected-seal"
IDENTITY_CLASS_SOURCE_ATTESTED = "source-attested"
ACQUISITION_IDENTITY_CLASSES = {
    IDENTITY_CLASS_REVIEWED_RELEASE,
    IDENTITY_CLASS_LEGACY_SEAL,
    IDENTITY_CLASS_SOURCE_ATTESTED,
}

EXECUTION_CONTRACT_COMPLETE_MANIFEST = "complete-expected-manifest"
EXECUTION_CONTRACT_MANIFEST_ID = "expected-manifest-id"
EXECUTION_CONTRACT_SOURCE_ATTESTED = "source-attested-complete-hash"
ACQUISITION_EXECUTION_CONTRACTS = {
    EXECUTION_CONTRACT_COMPLETE_MANIFEST,
    EXECUTION_CONTRACT_MANIFEST_ID,
    EXECUTION_CONTRACT_SOURCE_ATTESTED,
}

SOURCE_ATTESTED_ACQUISITION_POLICY_VERSION = 1
SOURCE_ATTESTED_ACQUISITION_MIN_HEADROOM_BYTES = 5 * 1024**3
SOURCE_ATTESTED_ACQUISITION_SELECTION_POLICIES = {
    "most-free-space",
    "operator-override",
}
SOURCE_ATTESTED_ACQUISITION_POLICY_OPERATIONS = (
    "exact-commit-target-side-download",
    "no-mutable-selector-fallback",
    "no-controller-byte-ferry",
    "target-local-authentication",
    "complete-upstream-inventory-set-check",
    "hf-cache-verify-missing-extra",
    "complete-pulsar-sha256",
    "optional-reviewed-manifest-or-manifest-id-comparison",
    "all-rank-absence-recheck",
    "receipt-before-atomic-home-publication",
    "no-catalog-refresh",
    "no-prepare",
    "no-launch",
    "no-status",
    "no-promotion",
)

HF_V1_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_OID_RE = re.compile(r"^[0-9a-f]{40}$")
HF_V1_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._/-]+$")

HF_V1_SOURCE_FIELDS = {
    "schema_version",
    "kind",
    "adapter",
    "model_id",
    "selector",
    "revision_kind",
    "snapshot_revision",
    "inventory",
    "file_count",
    "content_bytes",
    "inventory_digest",
    "source_digest",
}
HF_V1_ADAPTER_FIELDS = {"kind", "version"}
HF_V1_GIT_ENTRY_FIELDS = {"path", "size", "blob_kind", "git_oid"}
HF_V1_LFS_ENTRY_FIELDS = {"path", "size", "blob_kind", "sha256"}
ACQUISITION_IDENTITY_FIELDS = {
    "schema_version",
    "kind",
    "identity_class",
    "profile",
    "model_id",
    "selector",
    "snapshot_revision",
    "source_digest",
    "inventory_digest",
    "file_count",
    "content_bytes",
    "model_serving_release_id",
    "seal_id",
    "validation_bundle_id",
    "expected_manifest_id",
    "execution_contract",
}
SOURCE_ATTESTED_PLAN_FIELDS = {
    "schema_version",
    "kind",
    "source",
    "identity",
    "approval",
    "plan_id",
}
SOURCE_ATTESTED_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "receipt_id",
    "source",
    "identity",
    "approval",
    "observed_manifest",
    "selected_rank",
    "serving_ranks",
    "model_id",
    "snapshot_revision",
}
SOURCE_ATTESTED_RESULT_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "receipt_id",
    "source_digest",
    "approval_id",
    "identity_class",
    "profile",
    "model_id",
    "snapshot_revision",
    "selected_rank",
    "serving_ranks",
    "file_count",
    "content_bytes",
    "bytes_hashed",
    "catalog_refreshed",
    "staging_cleanup",
}
SOURCE_ATTESTED_VERIFY_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "receipt_id",
    "identity_class",
    "model_id",
    "snapshot_revision",
    "file_count",
    "bytes_hashed",
}
SOURCE_ATTESTED_APPROVAL_FIELDS = {
    "schema_version",
    "kind",
    "adapter",
    "identity_class",
    "profile",
    "model_id",
    "selector",
    "snapshot_revision",
    "source_digest",
    "inventory_digest",
    "file_count",
    "content_bytes",
    "model_serving_release_id",
    "seal_id",
    "validation_bundle_id",
    "expected_manifest_id",
    "serving_ranks",
    "selected_rank",
    "selection",
    "required_content_bytes",
    "required_free_bytes",
    "policy",
    "approval_id",
}
APPROVAL_POLICY_FIELDS = {"version", "operations"}
PROHIBITED_PUBLIC_FIELD_NAMES = {
    "address",
    "available_bytes",
    "cache_root",
    "created_at",
    "credential",
    "hf_cli",
    "hf_token",
    "host",
    "hostname",
    "hub_root",
    "ip",
    "local_path",
    "node_id",
    "recommendation",
    "ssh_host",
    "staging_root",
    "status",
    "target_hub",
    "token",
    "topology_fingerprint",
    "topology_id",
}
PROHIBITED_APPROVAL_FIELD_NAMES = PROHIBITED_PUBLIC_FIELD_NAMES


class SourceAttestedAcquisitionError(ValueError):
    """Malformed or conflicting source-attested acquisition contract."""


def fail(message: str) -> NoReturn:
    raise SourceAttestedAcquisitionError(message)


def source_attested_required_free_bytes(content_bytes: int) -> int:
    """Return the same staging-plus-headroom budget as sealed home add."""
    if isinstance(content_bytes, bool) or not isinstance(content_bytes, int):
        fail("expected content size must be an integer")
    if content_bytes <= 0:
        fail("expected content size must be positive")
    headroom = max(
        SOURCE_ATTESTED_ACQUISITION_MIN_HEADROOM_BYTES,
        (content_bytes + 9) // 10,
    )
    return content_bytes + headroom


def _public_string(value: Any, *, label: str) -> str:
    try:
        return model_serving_release.validate_public_string_value(
            value, label=label
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))


def _public_json(value: Any, *, label: str) -> None:
    try:
        model_serving_release.validate_public_json_value(value, label=label)
    except model_serving_release.ModelServingReleaseError as exc:
        fail(str(exc))


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    return value


def _require_fields(
    value: Any, required: set[str], *, label: str
) -> dict[str, Any]:
    document = _require_object(value, label=label)
    if set(document) != required:
        missing = sorted(required - set(document))
        extra = sorted(set(document) - required)
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return document


def _require_positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        fail(f"{label} must be a positive integer")
    return value


def _require_non_negative_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{label} must be a non-negative integer")
    return value


def _require_rank(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail(f"{label} must be a non-negative rank number")
    return value


def _validate_hex_id(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(value) is None
    ):
        fail(f"{label} must be a SHA-256 hex digest")
    return value


def _validate_optional_hex_id(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _validate_hex_id(value, label=label)


def _validate_hf_model_id(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or model_identity.HF_MODEL_ID_RE.fullmatch(value) is None
    ):
        fail(f"{label} must be a public Hugging Face repository ID")
    _public_string(value, label=label)
    return value


def _validate_profile(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or model_identity.SAFE_REV.fullmatch(value) is None
    ):
        fail("acquisition profile is invalid")
    _public_string(value, label="acquisition profile")
    return value


def _validate_selector(value: Any) -> str:
    if not isinstance(value, str) or not value:
        fail("acquisition selector must be a non-empty string")
    if (
        pathlib.PurePosixPath(value).is_absolute()
        or ".." in pathlib.PurePosixPath(value).parts
        or "\\" in value
        or HF_V1_SELECTOR_RE.fullmatch(value) is None
    ):
        fail("acquisition selector is unsafe or not a public revision selector")
    _public_string(value, label="acquisition selector")
    return value


def _validate_commit(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or HF_V1_COMMIT_RE.fullmatch(value) is None:
        fail(f"{label} must be one exact 40-hex Hugging Face commit")
    return value


def _validate_inventory_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        fail(f"{label} must be a relative POSIX path")
    if "\\" in value or value.startswith("/") or value.endswith("/"):
        fail(f"{label} must be a relative POSIX path")
    pure = pathlib.PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        fail(f"{label} is unsafe")
    if str(pure) != value:
        fail(f"{label} is not a canonical relative POSIX path")
    _public_string(value, label=label)
    return value


def _validate_adapter(value: Any) -> dict[str, Any]:
    adapter = _require_fields(value, HF_V1_ADAPTER_FIELDS, label="source adapter")
    if adapter.get("kind") != HF_V1_ADAPTER_KIND:
        fail("source adapter kind must be huggingface-v1")
    if adapter.get("version") != HF_V1_ADAPTER_VERSION:
        fail("source adapter version is unsupported")
    return adapter


def normalize_huggingface_v1_inventory_entry(
    *,
    path: str,
    size: int,
    blob_kind: str,
    sha256: str | None = None,
    git_oid: str | None = None,
) -> dict[str, Any]:
    """Normalize one upstream inventory row without inventing a SHA-256."""
    relative = _validate_inventory_path(path, label="inventory path")
    byte_count = _require_non_negative_int(
        size, label=f"inventory size for {relative}"
    )
    if blob_kind == HF_V1_BLOB_GIT:
        if sha256 is not None:
            fail(
                f"inventory {relative}: a Git blob must not carry a content "
                "SHA-256; do not invent one"
            )
        if not isinstance(git_oid, str) or GIT_OID_RE.fullmatch(git_oid) is None:
            fail(f"inventory {relative}: Git blob git_oid must be a 40-hex object ID")
        if git_oid == ALL_ZERO_GIT_OID:
            fail(f"inventory {relative}: Git blob git_oid must not be all zeros")
        return {
            "path": relative,
            "size": byte_count,
            "blob_kind": HF_V1_BLOB_GIT,
            "git_oid": git_oid,
        }
    if blob_kind == HF_V1_BLOB_LFS:
        if git_oid is not None:
            fail(f"inventory {relative}: an LFS object must not carry a Git object ID")
        if (
            not isinstance(sha256, str)
            or model_identity.SHA256_HEX_RE.fullmatch(sha256) is None
        ):
            fail(
                f"inventory {relative}: an LFS object must carry its upstream "
                "SHA-256; do not invent one"
            )
        if sha256 == ALL_ZERO_SHA256:
            fail(f"inventory {relative}: LFS SHA-256 must not be all zeros")
        return {
            "path": relative,
            "size": byte_count,
            "blob_kind": HF_V1_BLOB_LFS,
            "sha256": sha256,
        }
    fail(f"inventory {relative}: blob_kind must be git-blob or lfs-object")


def validate_huggingface_v1_inventory_entry(
    value: Any, *, index: int
) -> dict[str, Any]:
    entry = _require_object(value, label=f"inventory[{index}]")
    blob_kind = entry.get("blob_kind")
    if blob_kind == HF_V1_BLOB_GIT:
        entry = _require_fields(
            entry, HF_V1_GIT_ENTRY_FIELDS, label=f"inventory[{index}]"
        )
        return normalize_huggingface_v1_inventory_entry(
            path=entry["path"],
            size=entry["size"],
            blob_kind=HF_V1_BLOB_GIT,
            git_oid=entry.get("git_oid"),
        )
    if blob_kind == HF_V1_BLOB_LFS:
        entry = _require_fields(
            entry, HF_V1_LFS_ENTRY_FIELDS, label=f"inventory[{index}]"
        )
        return normalize_huggingface_v1_inventory_entry(
            path=entry["path"],
            size=entry["size"],
            blob_kind=HF_V1_BLOB_LFS,
            sha256=entry.get("sha256"),
        )
    fail(f"inventory[{index}].blob_kind must be git-blob or lfs-object")


def validate_huggingface_v1_inventory(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail("source inventory must be a non-empty list")
    entries = [
        validate_huggingface_v1_inventory_entry(item, index=index)
        for index, item in enumerate(value)
    ]
    paths = [item["path"] for item in entries]
    if paths != sorted(paths):
        fail("source inventory paths must be uniquely sorted")
    if len(paths) != len(set(paths)):
        fail("source inventory paths must be unique")
    return entries


def huggingface_v1_inventory_digest(inventory: list[dict[str, Any]]) -> str:
    return model_identity.canonical_json_digest(
        validate_huggingface_v1_inventory(inventory)
    )


def huggingface_v1_source_identity(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in source.items() if key != "source_digest"
    }


def huggingface_v1_source_digest(source: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(huggingface_v1_source_identity(source))


def validate_huggingface_v1_acquisition_source(value: Any) -> dict[str, Any]:
    source = _require_fields(value, HF_V1_SOURCE_FIELDS, label="Hugging Face v1 source")
    if source.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("Hugging Face v1 source schema is unsupported")
    if source.get("kind") != HF_V1_ACQUISITION_SOURCE_KIND:
        fail("Hugging Face v1 source kind is invalid")
    _validate_adapter(source.get("adapter"))
    _validate_hf_model_id(source.get("model_id"), label="source model_id")
    selector = _validate_selector(source.get("selector"))
    if source.get("revision_kind") != HF_V1_REVISION_KIND:
        fail("source revision_kind must be huggingface-commit")
    revision = _validate_commit(
        source.get("snapshot_revision"), label="source snapshot_revision"
    )
    if HF_V1_COMMIT_RE.fullmatch(selector) is not None and selector != revision:
        fail("source selector commit differs from the exact snapshot revision")
    inventory = validate_huggingface_v1_inventory(source.get("inventory"))
    if source.get("file_count") != len(inventory):
        fail("source file_count does not match the inventory")
    total = sum(item["size"] for item in inventory)
    if source.get("content_bytes") != total:
        fail("source content_bytes does not match the inventory")
    _require_positive_int(source.get("file_count"), label="source file_count")
    _require_positive_int(source.get("content_bytes"), label="source content_bytes")
    expected_inventory_digest = huggingface_v1_inventory_digest(inventory)
    if source.get("inventory_digest") != expected_inventory_digest:
        fail("source inventory_digest mismatch")
    if source.get("source_digest") != huggingface_v1_source_digest(source):
        fail("source digest mismatch")
    _public_json(
        {
            key: value
            for key, value in source.items()
            if key not in {"inventory"}
        },
        label="Hugging Face v1 source",
    )
    return source


def build_huggingface_v1_acquisition_source(
    *,
    model_id: str,
    selector: str,
    snapshot_revision: str,
    inventory: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a canonical Hugging Face v1 source from unsorted inventory rows."""
    normalized = [
        validate_huggingface_v1_inventory_entry(item, index=index)
        for index, item in enumerate(inventory)
    ]
    normalized.sort(key=lambda item: item["path"])
    source: dict[str, Any] = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": HF_V1_ACQUISITION_SOURCE_KIND,
        "adapter": {
            "kind": HF_V1_ADAPTER_KIND,
            "version": HF_V1_ADAPTER_VERSION,
        },
        "model_id": model_id,
        "selector": selector,
        "revision_kind": HF_V1_REVISION_KIND,
        "snapshot_revision": snapshot_revision,
        "inventory": normalized,
        "file_count": len(normalized),
        "content_bytes": sum(item["size"] for item in normalized),
        "inventory_digest": huggingface_v1_inventory_digest(normalized),
    }
    source["source_digest"] = huggingface_v1_source_digest(source)
    return validate_huggingface_v1_acquisition_source(source)


def _registry_module() -> Any:
    try:
        from scripts import model_serving_release_registry
    except ModuleNotFoundError:
        try:
            import model_serving_release_registry  # type: ignore[no-redef]
        except ModuleNotFoundError as exc:
            fail(
                "bound Model Serving Release cannot be verified: registry "
                f"loader is unavailable ({exc})"
            )
        return model_serving_release_registry
    return model_serving_release_registry


def load_verified_bound_release(
    *,
    repo_root: str | pathlib.Path,
    release_id: str,
) -> dict[str, Any]:
    """Load one reviewed release from the verified read-only registry.

    An unverified object is never treated as authority. A binding that cannot
    be verified fails; this function does not fall back to a seal or source.
    """
    release_id = _validate_hex_id(release_id, label="MODEL_SERVING_RELEASE_ID")
    registry = _registry_module()
    try:
        graph = registry.load_registry(pathlib.Path(repo_root))
    except Exception as exc:
        if isinstance(exc, SourceAttestedAcquisitionError):
            raise
        fail(f"bound Model Serving Release cannot be verified: {exc}")
    try:
        release = graph.descriptors[release_id]
    except KeyError:
        fail(
            "bound Model Serving Release cannot be verified: the reviewed "
            "binding is not stored in the verified registry"
        )
    return copy.deepcopy(release)


def _primary_model_artifact(release: dict[str, Any]) -> dict[str, Any]:
    bindings = release["serving_recipe"]["artifact_bindings"]
    primary_keys = [
        item["artifact_key"] for item in bindings if item["use"] == "primary-model"
    ]
    if len(primary_keys) != 1:
        fail("reviewed release does not bind exactly one primary-model artifact")
    artifacts = {
        item["artifact_key"]: item
        for item in release["model_artifact_set"]["artifacts"]
    }
    try:
        primary = artifacts[primary_keys[0]]
    except KeyError:
        fail("reviewed release primary-model artifact is missing")
    kind = primary.get("kind")
    if kind == "content-addressed-model":
        fail(
            "Hugging Face v1 acquisition refuses a content-addressed-model "
            "primary artifact"
        )
    if kind != "huggingface-snapshot":
        fail("Hugging Face v1 acquisition requires a huggingface-snapshot primary")
    return primary


def _release_expected_identity(release: dict[str, Any]) -> dict[str, str]:
    primary = _primary_model_artifact(release)
    model_id = _validate_hf_model_id(
        primary.get("model_id"), label="reviewed release primary model_id"
    )
    revision = _validate_commit(
        primary.get("snapshot_revision"),
        label="reviewed release primary snapshot_revision",
    )
    manifest = _require_object(
        primary.get("manifest"), label="reviewed release primary manifest"
    )
    manifest_id = _validate_hex_id(
        manifest.get("manifest_id"),
        label="reviewed release primary manifest_id",
    )
    return {
        "model_id": model_id,
        "snapshot_revision": revision,
        "manifest_id": manifest_id,
    }


def _seal_expected_identity(
    expected_seal: dict[str, Any], *, profile: str, model_id: str
) -> dict[str, str]:
    seal = model_identity.validate_expected_model_seal(
        expected_seal, profile=profile, model_id=model_id
    )
    projection = model_identity.expected_model_seal_projection(seal)
    return {
        "model_id": projection["model_id"],
        "snapshot_revision": projection["snapshot_revision"],
        "manifest_id": projection["manifest_id"],
        "seal_id": projection["seal_id"],
        "validation_bundle_id": projection["validation_bundle_id"],
    }


def _identity_document(
    *,
    source: dict[str, Any],
    profile: str,
    identity_class: str,
    execution_contract: str,
    model_serving_release_id: str | None,
    seal_id: str | None,
    validation_bundle_id: str | None,
    expected_manifest_id: str | None,
) -> dict[str, Any]:
    document = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": ACQUISITION_IDENTITY_KIND,
        "identity_class": identity_class,
        "profile": profile,
        "model_id": source["model_id"],
        "selector": source["selector"],
        "snapshot_revision": source["snapshot_revision"],
        "source_digest": source["source_digest"],
        "inventory_digest": source["inventory_digest"],
        "file_count": source["file_count"],
        "content_bytes": source["content_bytes"],
        "model_serving_release_id": model_serving_release_id,
        "seal_id": seal_id,
        "validation_bundle_id": validation_bundle_id,
        "expected_manifest_id": expected_manifest_id,
        "execution_contract": execution_contract,
    }
    return validate_acquisition_identity(document)


def validate_acquisition_identity(value: Any) -> dict[str, Any]:
    identity = _require_fields(
        value, ACQUISITION_IDENTITY_FIELDS, label="acquisition identity"
    )
    if identity.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("acquisition identity schema is unsupported")
    if identity.get("kind") != ACQUISITION_IDENTITY_KIND:
        fail("acquisition identity kind is invalid")
    identity_class = identity.get("identity_class")
    if identity_class not in ACQUISITION_IDENTITY_CLASSES:
        fail("acquisition identity_class is unsupported")
    _validate_profile(identity.get("profile"))
    _validate_hf_model_id(identity.get("model_id"), label="identity model_id")
    _validate_selector(identity.get("selector"))
    _validate_commit(
        identity.get("snapshot_revision"), label="identity snapshot_revision"
    )
    _validate_hex_id(identity.get("source_digest"), label="identity source_digest")
    _validate_hex_id(
        identity.get("inventory_digest"), label="identity inventory_digest"
    )
    _require_positive_int(identity.get("file_count"), label="identity file_count")
    _require_positive_int(
        identity.get("content_bytes"), label="identity content_bytes"
    )
    _validate_optional_hex_id(
        identity.get("model_serving_release_id"),
        label="identity model_serving_release_id",
    )
    _validate_optional_hex_id(identity.get("seal_id"), label="identity seal_id")
    _validate_optional_hex_id(
        identity.get("validation_bundle_id"),
        label="identity validation_bundle_id",
    )
    _validate_optional_hex_id(
        identity.get("expected_manifest_id"),
        label="identity expected_manifest_id",
    )
    execution_contract = identity.get("execution_contract")
    if execution_contract not in ACQUISITION_EXECUTION_CONTRACTS:
        fail("acquisition execution_contract is unsupported")
    if identity_class == IDENTITY_CLASS_SOURCE_ATTESTED:
        if identity.get("model_serving_release_id") is not None:
            fail("source-attested identity must not carry a release binding")
        if identity.get("seal_id") is not None:
            fail("source-attested identity must not carry a reviewed seal")
        if identity.get("validation_bundle_id") is not None:
            fail("source-attested identity must not carry a validation bundle")
        if identity.get("expected_manifest_id") is not None:
            fail("source-attested identity must not carry an expected manifest")
        if execution_contract != EXECUTION_CONTRACT_SOURCE_ATTESTED:
            fail(
                "source-attested identity execution_contract must be "
                "source-attested-complete-hash"
            )
    elif identity_class == IDENTITY_CLASS_LEGACY_SEAL:
        if identity.get("model_serving_release_id") is not None:
            fail("legacy seal identity must not carry a release binding")
        if identity.get("seal_id") is None:
            fail("legacy seal identity requires seal_id")
        if identity.get("validation_bundle_id") is None:
            fail("legacy seal identity requires validation_bundle_id")
        if identity.get("expected_manifest_id") is None:
            fail("legacy seal identity requires expected_manifest_id")
        if execution_contract != EXECUTION_CONTRACT_COMPLETE_MANIFEST:
            fail(
                "legacy seal identity execution_contract must be "
                "complete-expected-manifest"
            )
    else:
        if identity.get("model_serving_release_id") is None:
            fail("reviewed release identity requires model_serving_release_id")
        if identity.get("expected_manifest_id") is None:
            fail("reviewed release identity requires expected_manifest_id")
        if identity.get("seal_id") is None:
            if execution_contract != EXECUTION_CONTRACT_MANIFEST_ID:
                fail(
                    "release-only identity execution_contract must be "
                    "expected-manifest-id"
                )
            if identity.get("validation_bundle_id") is not None:
                fail("release-only identity must not invent a validation bundle")
        else:
            if identity.get("validation_bundle_id") is None:
                fail("release-and-seal identity requires validation_bundle_id")
            if execution_contract != EXECUTION_CONTRACT_COMPLETE_MANIFEST:
                fail(
                    "release-and-seal identity execution_contract must be "
                    "complete-expected-manifest"
                )
    _public_json(identity, label="acquisition identity")
    return identity


def resolve_huggingface_v1_acquisition_identity(
    *,
    source: dict[str, Any],
    profile: str,
    expected_seal: dict[str, Any] | None = None,
    model_serving_release_id: str | None = None,
    repo_root: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Resolve reviewed release, legacy seal, then unbound source identity.

    Precedence is (1) a verified MODEL_SERVING_RELEASE_ID binding, (2) a
    legacy expected seal, (3) explicit source-attested identity. A bound
    release that cannot be verified fails without falling back.
    """
    source = validate_huggingface_v1_acquisition_source(source)
    profile = _validate_profile(profile)
    if model_serving_release_id:
        if repo_root is None:
            fail(
                "bound Model Serving Release cannot be verified: repository "
                "root is required"
            )
        release = load_verified_bound_release(
            repo_root=repo_root, release_id=model_serving_release_id
        )
        expected = _release_expected_identity(release)
        if expected["model_id"] != source["model_id"]:
            fail(
                "reviewed binding model_id differs from the selected Hugging "
                "Face source"
            )
        if expected["snapshot_revision"] != source["snapshot_revision"]:
            fail(
                "reviewed binding commit differs from the selected revision"
            )
        if (
            HF_V1_COMMIT_RE.fullmatch(source["selector"]) is not None
            and source["selector"] != expected["snapshot_revision"]
        ):
            fail("reviewed binding conflicts with the operator commit selector")
        seal_id = None
        validation_bundle_id = None
        execution_contract = EXECUTION_CONTRACT_MANIFEST_ID
        if expected_seal is not None:
            seal = _seal_expected_identity(
                expected_seal, profile=profile, model_id=source["model_id"]
            )
            for field in ("model_id", "snapshot_revision", "manifest_id"):
                if seal[field] != expected[field]:
                    fail(
                        "reviewed release and expected seal disagree on "
                        f"{field}"
                    )
            seal_id = seal["seal_id"]
            validation_bundle_id = seal["validation_bundle_id"]
            execution_contract = EXECUTION_CONTRACT_COMPLETE_MANIFEST
        return _identity_document(
            source=source,
            profile=profile,
            identity_class=IDENTITY_CLASS_REVIEWED_RELEASE,
            execution_contract=execution_contract,
            model_serving_release_id=release["release_id"],
            seal_id=seal_id,
            validation_bundle_id=validation_bundle_id,
            expected_manifest_id=expected["manifest_id"],
        )

    if expected_seal is not None:
        seal = _seal_expected_identity(
            expected_seal, profile=profile, model_id=source["model_id"]
        )
        if seal["snapshot_revision"] != source["snapshot_revision"]:
            fail("expected seal commit differs from the selected revision")
        if (
            HF_V1_COMMIT_RE.fullmatch(source["selector"]) is not None
            and source["selector"] != seal["snapshot_revision"]
        ):
            fail("expected seal conflicts with the operator commit selector")
        return _identity_document(
            source=source,
            profile=profile,
            identity_class=IDENTITY_CLASS_LEGACY_SEAL,
            execution_contract=EXECUTION_CONTRACT_COMPLETE_MANIFEST,
            model_serving_release_id=None,
            seal_id=seal["seal_id"],
            validation_bundle_id=seal["validation_bundle_id"],
            expected_manifest_id=seal["manifest_id"],
        )

    return _identity_document(
        source=source,
        profile=profile,
        identity_class=IDENTITY_CLASS_SOURCE_ATTESTED,
        execution_contract=EXECUTION_CONTRACT_SOURCE_ATTESTED,
        model_serving_release_id=None,
        seal_id=None,
        validation_bundle_id=None,
        expected_manifest_id=None,
    )


def _frozen_policy() -> dict[str, Any]:
    return {
        "version": SOURCE_ATTESTED_ACQUISITION_POLICY_VERSION,
        "operations": list(SOURCE_ATTESTED_ACQUISITION_POLICY_OPERATIONS),
    }


def _validate_policy(value: Any) -> dict[str, Any]:
    policy = _require_fields(value, APPROVAL_POLICY_FIELDS, label="approval policy")
    if policy.get("version") != SOURCE_ATTESTED_ACQUISITION_POLICY_VERSION:
        fail("approval policy version is unsupported")
    operations = policy.get("operations")
    if operations != list(SOURCE_ATTESTED_ACQUISITION_POLICY_OPERATIONS):
        fail("approval policy operations differ from the frozen execution policy")
    return policy


def _validate_serving_ranks(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        fail("approval serving_ranks must be a non-empty list")
    ranks = [_require_rank(item, label="approval serving rank") for item in value]
    if ranks != sorted(set(ranks)):
        fail("approval serving_ranks must be uniquely sorted rank numbers")
    return ranks


def _validate_topology_generation(value: Any) -> str:
    if (
        not isinstance(value, str)
        or model_identity.SHA256_HEX_RE.fullmatch(value) is None
    ):
        fail("internal topology generation is invalid")
    return value


def source_attested_acquisition_approval_id(
    approval: dict[str, Any],
    *,
    source: dict[str, Any],
    topology_generation: str,
) -> str:
    """Hash the canonical source, public approval, and topology generation.

    The raw topology identifier is an internal bind input only. It is not
    copied into the public approval and is not emitted as a reusable
    topology fingerprint.
    """
    source = validate_huggingface_v1_acquisition_source(source)
    generation = _validate_topology_generation(topology_generation)
    public = {
        key: approval[key]
        for key in sorted(approval)
        if key != "approval_id"
    }
    return model_identity.canonical_json_digest(
        {
            "approval": public,
            "source": source,
            "topology_generation": generation,
        }
    )


def _reject_prohibited_public_fields(document: dict[str, Any], *, label: str) -> None:
    lowered = {key.lower() for key in document}
    blocked = sorted(name for name in PROHIBITED_PUBLIC_FIELD_NAMES if name in lowered)
    if blocked:
        fail(f"{label} contains prohibited field(s): {blocked}")


def _reject_prohibited_approval_fields(approval: dict[str, Any]) -> None:
    _reject_prohibited_public_fields(approval, label="approval")


def validate_source_attested_acquisition_approval(value: Any) -> dict[str, Any]:
    approval = _require_fields(
        value, SOURCE_ATTESTED_APPROVAL_FIELDS, label="source-attested approval"
    )
    _reject_prohibited_approval_fields(approval)
    if approval.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("source-attested approval schema is unsupported")
    if approval.get("kind") != SOURCE_ATTESTED_ACQUISITION_APPROVAL_KIND:
        fail("source-attested approval kind is invalid")
    _validate_adapter(approval.get("adapter"))
    identity_class = approval.get("identity_class")
    if identity_class not in ACQUISITION_IDENTITY_CLASSES:
        fail("approval identity_class is unsupported")
    _validate_profile(approval.get("profile"))
    _validate_hf_model_id(approval.get("model_id"), label="approval model_id")
    _validate_selector(approval.get("selector"))
    _validate_commit(
        approval.get("snapshot_revision"), label="approval snapshot_revision"
    )
    _validate_hex_id(approval.get("source_digest"), label="approval source_digest")
    _validate_hex_id(
        approval.get("inventory_digest"), label="approval inventory_digest"
    )
    _require_positive_int(approval.get("file_count"), label="approval file_count")
    content_bytes = _require_positive_int(
        approval.get("content_bytes"), label="approval content_bytes"
    )
    _validate_optional_hex_id(
        approval.get("model_serving_release_id"),
        label="approval model_serving_release_id",
    )
    _validate_optional_hex_id(approval.get("seal_id"), label="approval seal_id")
    _validate_optional_hex_id(
        approval.get("validation_bundle_id"),
        label="approval validation_bundle_id",
    )
    _validate_optional_hex_id(
        approval.get("expected_manifest_id"),
        label="approval expected_manifest_id",
    )
    release_id = approval.get("model_serving_release_id")
    seal_id = approval.get("seal_id")
    bundle_id = approval.get("validation_bundle_id")
    manifest_id = approval.get("expected_manifest_id")
    if identity_class == IDENTITY_CLASS_SOURCE_ATTESTED:
        if any(
            item is not None
            for item in (release_id, seal_id, bundle_id, manifest_id)
        ):
            fail("source-attested approval must not carry reviewed identity")
    elif identity_class == IDENTITY_CLASS_LEGACY_SEAL:
        if release_id is not None:
            fail("legacy seal approval must not carry a release binding")
        if seal_id is None or bundle_id is None or manifest_id is None:
            fail("legacy seal approval requires complete reviewed references")
    else:
        if release_id is None or manifest_id is None:
            fail("reviewed release approval requires release and manifest IDs")
        if (seal_id is None) != (bundle_id is None):
            fail("reviewed release approval seal references are incomplete")
    serving_ranks = _validate_serving_ranks(approval.get("serving_ranks"))
    selected_rank = _require_rank(
        approval.get("selected_rank"), label="approval selected_rank"
    )
    if selected_rank not in serving_ranks:
        fail("approval selected_rank is outside the serving geometry")
    if approval.get("selection") not in SOURCE_ATTESTED_ACQUISITION_SELECTION_POLICIES:
        fail("approval selection policy is invalid")
    required_content = _require_positive_int(
        approval.get("required_content_bytes"),
        label="approval required_content_bytes",
    )
    if required_content != content_bytes:
        fail("approval required_content_bytes must equal source content_bytes")
    required_free = _require_positive_int(
        approval.get("required_free_bytes"),
        label="approval required_free_bytes",
    )
    if required_free != source_attested_required_free_bytes(content_bytes):
        fail("approval required_free_bytes does not match the frozen capacity policy")
    _validate_policy(approval.get("policy"))
    _validate_hex_id(approval.get("approval_id"), label="approval_id")
    _public_json(approval, label="source-attested approval")
    return approval


def verify_source_attested_acquisition_approval(
    approval: dict[str, Any],
    *,
    source: dict[str, Any],
    identity: dict[str, Any],
    topology_generation: str,
) -> dict[str, Any]:
    """Rebuild the approval identifier from live source and topology facts."""
    approval = validate_source_attested_acquisition_approval(approval)
    source = validate_huggingface_v1_acquisition_source(source)
    identity = validate_acquisition_identity(identity)
    source_fields = {
        "model_id": "model_id",
        "selector": "selector",
        "snapshot_revision": "snapshot_revision",
        "source_digest": "source_digest",
        "inventory_digest": "inventory_digest",
        "file_count": "file_count",
        "content_bytes": "content_bytes",
    }
    for approval_field, source_field in source_fields.items():
        if approval[approval_field] != source[source_field]:
            fail(f"approval {approval_field} differs from the live source")
    identity_fields = (
        "identity_class",
        "profile",
        "model_id",
        "selector",
        "snapshot_revision",
        "source_digest",
        "inventory_digest",
        "file_count",
        "content_bytes",
        "model_serving_release_id",
        "seal_id",
        "validation_bundle_id",
        "expected_manifest_id",
    )
    for field in identity_fields:
        if approval[field] != identity[field]:
            fail(f"approval {field} differs from the resolved identity")
    expected_id = source_attested_acquisition_approval_id(
        approval, source=source, topology_generation=topology_generation
    )
    if approval["approval_id"] != expected_id:
        fail("approval identity mismatch")
    return approval


def build_source_attested_acquisition_approval(
    *,
    source: dict[str, Any],
    identity: dict[str, Any],
    serving_ranks: list[int],
    selected_rank: int,
    selection: str,
    topology_generation: str,
) -> dict[str, Any]:
    """Build the public approval summary from internal bind inputs.

    Internal topology generation, created_at, available_bytes, hostnames,
    node IDs, SSH endpoints, local paths, and credentials stay out of the
    public object. Execution later rebuilds this identifier from live
    source and topology facts instead of trusting a client-supplied plan.
    """
    source = validate_huggingface_v1_acquisition_source(source)
    identity = validate_acquisition_identity(identity)
    if identity["source_digest"] != source["source_digest"]:
        fail("resolved identity does not match the supplied source")
    if identity["model_id"] != source["model_id"]:
        fail("resolved identity model_id differs from the source")
    if identity["snapshot_revision"] != source["snapshot_revision"]:
        fail("resolved identity commit differs from the source")
    ranks = _validate_serving_ranks(serving_ranks)
    rank = _require_rank(selected_rank, label="selected_rank")
    if rank not in ranks:
        fail("selected_rank is outside the serving geometry")
    if selection not in SOURCE_ATTESTED_ACQUISITION_SELECTION_POLICIES:
        fail("selection policy is invalid")
    generation = _validate_topology_generation(topology_generation)
    approval: dict[str, Any] = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_ACQUISITION_APPROVAL_KIND,
        "adapter": copy.deepcopy(source["adapter"]),
        "identity_class": identity["identity_class"],
        "profile": identity["profile"],
        "model_id": source["model_id"],
        "selector": source["selector"],
        "snapshot_revision": source["snapshot_revision"],
        "source_digest": source["source_digest"],
        "inventory_digest": source["inventory_digest"],
        "file_count": source["file_count"],
        "content_bytes": source["content_bytes"],
        "model_serving_release_id": identity["model_serving_release_id"],
        "seal_id": identity["seal_id"],
        "validation_bundle_id": identity["validation_bundle_id"],
        "expected_manifest_id": identity["expected_manifest_id"],
        "serving_ranks": ranks,
        "selected_rank": rank,
        "selection": selection,
        "required_content_bytes": source["content_bytes"],
        "required_free_bytes": source_attested_required_free_bytes(
            source["content_bytes"]
        ),
        "policy": _frozen_policy(),
    }
    approval["approval_id"] = source_attested_acquisition_approval_id(
        approval, source=source, topology_generation=generation
    )
    return verify_source_attested_acquisition_approval(
        approval,
        source=source,
        identity=identity,
        topology_generation=generation,
    )


def _pretty_json(value: Any) -> str:
    return model_identity.pretty_json_bytes(value).decode("utf-8")


def _load_json_value(value: Any, *, label: str) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return json.loads(bytes(value).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            fail(f"{label} is not valid JSON: {exc}")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            fail(f"{label} is not valid JSON: {exc}")
    fail(f"{label} must be JSON")


def _load_json_path(path: str | pathlib.Path, *, label: str) -> Any:
    try:
        raw = pathlib.Path(path).read_bytes()
    except OSError as exc:
        fail(f"{label} is unreadable: {exc}")
    return _load_json_value(raw, label=label)


def valid_source_attested_hf_cli(value: Any) -> bool:
    """Return True only for modern ``hf``, never huggingface-cli."""
    if value == HF_V1_REQUIRED_CLI:
        return True
    if not isinstance(value, str) or not value:
        return False
    pure = pathlib.PurePosixPath(value)
    return pure.is_absolute() and pure.parts[-4:] == (".hf-cli", "venv", "bin", "hf")


def parse_huggingface_v1_resolved_revision(payload: Any) -> str:
    document = _load_json_value(payload, label="Hugging Face repo info")
    if not isinstance(document, dict):
        fail(f"{UNSUPPORTED_SOURCE_FORM}: repo info must be an object")
    sha = document.get("sha") or document.get("oid") or document.get("commit")
    if not isinstance(sha, str) or HF_V1_COMMIT_RE.fullmatch(sha) is None:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: repo info does not name one 40-hex commit")
    return sha


def _lfs_sha256(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("sha256", "oid"):
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if raw.startswith("sha256:"):
            raw = raw[7:]
        if model_identity.SHA256_HEX_RE.fullmatch(raw) is not None:
            return raw
    return None


def _entry_path(entry: dict[str, Any]) -> str | None:
    for key in ("path", "rfilename", "filename"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _entry_git_oid(entry: dict[str, Any]) -> str | None:
    for key in ("git_oid", "blob_id", "oid"):
        value = entry.get(key)
        if isinstance(value, str) and GIT_OID_RE.fullmatch(value) is not None:
            return value
    return None


def adapt_huggingface_v1_tree_entry(
    entry: Any, *, index: int
) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        fail(f"{UNSUPPORTED_SOURCE_FORM}: tree[{index}] is not an object")
    kind = entry.get("type") or entry.get("kind")
    if kind in {"directory", "tree", "folder"}:
        return None
    path = _entry_path(entry)
    if path is None:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: tree[{index}] has no file path")
    if kind not in {None, "file", "blob"}:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: {path} uses unsupported object type {kind!r}")
    size = entry.get("size")
    if isinstance(entry.get("lfs"), dict) and isinstance(entry["lfs"].get("size"), int):
        size = entry["lfs"].get("size") if size is None else size
    if isinstance(size, bool) or not isinstance(size, int):
        fail(f"{UNSUPPORTED_SOURCE_FORM}: {path} has no integer size")
    lfs = entry.get("lfs")
    if lfs is not None:
        sha256 = _lfs_sha256(lfs)
        if sha256 is None:
            fail(f"{UNSUPPORTED_SOURCE_FORM}: {path} LFS object has no upstream SHA-256")
        return normalize_huggingface_v1_inventory_entry(
            path=path,
            size=size,
            blob_kind=HF_V1_BLOB_LFS,
            sha256=sha256,
        )
    git_oid = _entry_git_oid(entry)
    if git_oid is None:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: {path} is not a Git blob or LFS object")
    return normalize_huggingface_v1_inventory_entry(
        path=path,
        size=size,
        blob_kind=HF_V1_BLOB_GIT,
        git_oid=git_oid,
    )


def parse_huggingface_v1_inventory_payload(payload: Any) -> list[dict[str, Any]]:
    document = _load_json_value(payload, label="Hugging Face repo tree")
    if isinstance(document, dict):
        if isinstance(document.get("siblings"), list):
            rows = document["siblings"]
        elif isinstance(document.get("tree"), list):
            rows = document["tree"]
        elif isinstance(document.get("files"), list):
            rows = document["files"]
        else:
            fail(f"{UNSUPPORTED_SOURCE_FORM}: repo tree is not a file list")
    elif isinstance(document, list):
        rows = document
    else:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: repo tree is not a file list")
    inventory: list[dict[str, Any]] = []
    for index, item in enumerate(rows):
        adapted = adapt_huggingface_v1_tree_entry(item, index=index)
        if adapted is not None:
            inventory.append(adapted)
    if not inventory:
        fail(f"{UNSUPPORTED_SOURCE_FORM}: inventory is empty")
    return inventory


def build_huggingface_v1_source_from_adapter(
    *,
    model_id: str,
    selector: str,
    repo_info: Any,
    repo_tree: Any | None = None,
) -> dict[str, Any]:
    """Build a v1 source from Hub metadata. Mutable selectors stay inputs only."""
    revision = parse_huggingface_v1_resolved_revision(repo_info)
    info = _load_json_value(repo_info, label="Hugging Face repo info")
    reported_id = None
    if isinstance(info, dict):
        reported_id = info.get("id") or info.get("modelId")
    if isinstance(reported_id, str) and reported_id and reported_id != model_id:
        fail("Hugging Face repo info model_id differs from the selected model")
    tree_payload = repo_tree if repo_tree is not None else repo_info
    inventory = parse_huggingface_v1_inventory_payload(tree_payload)
    return build_huggingface_v1_acquisition_source(
        model_id=model_id,
        selector=selector,
        snapshot_revision=revision,
        inventory=inventory,
    )


def source_attested_plan_id(plan: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(
        {key: value for key, value in plan.items() if key != "plan_id"}
    )


def validate_source_attested_acquisition_plan(value: Any) -> dict[str, Any]:
    plan = _require_fields(value, SOURCE_ATTESTED_PLAN_FIELDS, label="source-attested plan")
    _reject_prohibited_public_fields(plan, label="source-attested plan")
    if plan.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("source-attested plan schema is unsupported")
    if plan.get("kind") != SOURCE_ATTESTED_ACQUISITION_PLAN_KIND:
        fail("source-attested plan kind is invalid")
    source = validate_huggingface_v1_acquisition_source(plan.get("source"))
    identity = validate_acquisition_identity(plan.get("identity"))
    approval = validate_source_attested_acquisition_approval(plan.get("approval"))
    source_links = (
        "model_id",
        "selector",
        "snapshot_revision",
        "source_digest",
        "inventory_digest",
        "file_count",
        "content_bytes",
    )
    for field in source_links:
        if identity[field] != source[field]:
            fail(f"source-attested plan identity {field} differs from its source")
        if approval[field] != source[field]:
            fail(f"source-attested plan approval {field} differs from its source")
    identity_links = (
        "identity_class",
        "profile",
        "model_id",
        "selector",
        "snapshot_revision",
        "source_digest",
        "inventory_digest",
        "file_count",
        "content_bytes",
        "model_serving_release_id",
        "seal_id",
        "validation_bundle_id",
        "expected_manifest_id",
    )
    for field in identity_links:
        if approval[field] != identity[field]:
            fail(f"source-attested plan approval {field} differs from its identity")
    if plan.get("plan_id") != source_attested_plan_id(plan):
        fail("source-attested plan identity mismatch")
    _public_json(
        {
            "schema_version": plan["schema_version"],
            "kind": plan["kind"],
            "plan_id": plan["plan_id"],
            "identity_class": identity["identity_class"],
            "profile": identity["profile"],
            "model_id": source["model_id"],
            "selector": source["selector"],
            "snapshot_revision": source["snapshot_revision"],
            "approval_id": approval["approval_id"],
        },
        label="source-attested plan summary",
    )
    return plan


def build_source_attested_acquisition_plan(
    *,
    source: dict[str, Any],
    identity: dict[str, Any],
    approval: dict[str, Any],
) -> dict[str, Any]:
    source = validate_huggingface_v1_acquisition_source(source)
    identity = validate_acquisition_identity(identity)
    approval = validate_source_attested_acquisition_approval(approval)
    plan = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_ACQUISITION_PLAN_KIND,
        "source": source,
        "identity": identity,
        "approval": approval,
    }
    plan["plan_id"] = source_attested_plan_id(plan)
    return validate_source_attested_acquisition_plan(plan)


def _observation_rank(observation: dict[str, Any]) -> int:
    return _require_rank(observation.get("rank"), label="observation rank")


def select_source_attested_target(
    observations: list[dict[str, Any]],
    *,
    serving_nodes: int,
    required_free_bytes: int,
    node_selector: str = "",
) -> tuple[dict[str, Any], str, list[int]]:
    """Select one eligible rank from already-collected observations."""
    if not isinstance(observations, list) or not observations:
        fail("home add: not every confirmed rank was observed")
    ranks = [_observation_rank(item) for item in observations]
    if ranks != sorted(set(ranks)):
        fail("home add: rank observations must be uniquely sorted")
    if (
        isinstance(serving_nodes, bool)
        or not isinstance(serving_nodes, int)
        or serving_nodes < 1
        or serving_nodes > len(ranks)
        or (serving_nodes > 1 and ranks[:serving_nodes] != list(range(serving_nodes)))
    ):
        fail("home add: profile serving geometry exceeds confirmed contiguous ranks")
    occupied = [
        item
        for item in observations
        if item.get("target_state") != "absent"
    ]
    if occupied:
        found = ", ".join(str(_observation_rank(item)) for item in occupied)
        fail(
            "home add: repository path already exists on confirmed rank(s) "
            f"{found}; run catalog refresh and reconcile existing content"
        )
    candidate_ranks = ranks if serving_nodes == 1 else list(range(serving_nodes))

    def eligible(item: dict[str, Any]) -> bool:
        return bool(
            item.get("target_state") == "absent"
            and item.get("writable")
            and valid_source_attested_hf_cli(item.get("hf_cli"))
            and isinstance(item.get("available_bytes"), int)
            and not isinstance(item.get("available_bytes"), bool)
            and item["available_bytes"] >= required_free_bytes
        )

    if node_selector:
        matches = [
            item
            for item in observations
            if str(item.get("rank")) == node_selector
            or item.get("node_id") == node_selector
        ]
        if len(matches) != 1:
            fail("home add: --node must match exactly one confirmed rank or node ID")
        selected = matches[0]
        if _observation_rank(selected) not in candidate_ranks:
            fail("home add: selected rank is outside the profile serving geometry")
        if not eligible(selected):
            detail = selected.get("detail") or "target check failed"
            if not valid_source_attested_hf_cli(selected.get("hf_cli")):
                detail = "modern hf CLI is not installed on this rank"
            fail(
                f"home add: selected rank {_observation_rank(selected)} is not "
                f"eligible: {detail}"
            )
        selection = "operator-override"
    else:
        eligible_rows = [
            item
            for item in observations
            if eligible(item) and _observation_rank(item) in candidate_ranks
        ]
        if not eligible_rows:
            details = "; ".join(
                f"rank {_observation_rank(item)}: "
                f"{item.get('detail') or 'not eligible'}"
                for item in observations
            )
            fail(f"home add: no eligible durable-home rank ({details})")
        selected = sorted(
            eligible_rows,
            key=lambda item: (
                -int(item["available_bytes"]),
                _observation_rank(item),
            ),
        )[0]
        selection = "most-free-space"
    serving_ranks = (
        [_observation_rank(selected)]
        if serving_nodes == 1
        else candidate_ranks
    )
    return selected, selection, serving_ranks


def plan_source_attested_acquisition(
    *,
    source: dict[str, Any],
    identity: dict[str, Any],
    observations: list[dict[str, Any]],
    serving_nodes: int,
    topology_generation: str,
    node_selector: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the public plan and a private execution handle."""
    source = validate_huggingface_v1_acquisition_source(source)
    identity = validate_acquisition_identity(identity)
    required_free = source_attested_required_free_bytes(source["content_bytes"])
    selected, selection, serving_ranks = select_source_attested_target(
        observations,
        serving_nodes=serving_nodes,
        required_free_bytes=required_free,
        node_selector=node_selector,
    )
    approval = build_source_attested_acquisition_approval(
        source=source,
        identity=identity,
        serving_ranks=serving_ranks,
        selected_rank=_observation_rank(selected),
        selection=selection,
        topology_generation=topology_generation,
    )
    plan = build_source_attested_acquisition_plan(
        source=source, identity=identity, approval=approval
    )
    handle = {
        "approval_id": approval["approval_id"],
        "selected_rank": approval["selected_rank"],
        "node_id": selected.get("node_id"),
        "cache_root": selected.get("cache_root"),
        "hub_root": selected.get("hub_root"),
        "target_hub": selected.get("target_hub"),
        "hf_cli": selected.get("hf_cli"),
        "available_bytes": selected.get("available_bytes"),
        "required_free_bytes": required_free,
    }
    return plan, handle


def git_blob_oid(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode("ascii") + data).hexdigest()


def compare_observed_files_to_inventory(
    inventory: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> None:
    inventory = validate_huggingface_v1_inventory(inventory)
    if not isinstance(observed, list) or not observed:
        fail("home add: staged snapshot has no files")
    expected = {item["path"]: item for item in inventory}
    actual: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(observed):
        row = _require_object(item, label=f"observed[{index}]")
        path = _validate_inventory_path(row.get("path"), label=f"observed[{index}].path")
        if path in actual:
            fail(f"home add: staged snapshot repeats path {path}")
        actual[path] = row
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        fail(f"home add: staged snapshot is missing {missing}")
    if extra:
        fail(f"home add: staged snapshot has extra files {extra}")
    for path, want in expected.items():
        got = actual[path]
        size = got.get("size")
        if size != want["size"]:
            fail(f"home add: staged {path} size differs from the upstream inventory")
        if want["blob_kind"] == HF_V1_BLOB_GIT:
            if got.get("git_oid") != want["git_oid"]:
                fail(
                    f"home add: staged {path} Git object ID differs from the "
                    "upstream inventory"
                )
        else:
            if got.get("sha256") != want["sha256"]:
                fail(
                    f"home add: staged {path} SHA-256 differs from the upstream "
                    "LFS object"
                )


def compare_observed_manifest_to_expected(
    observed: dict[str, Any],
    *,
    expected_manifest_id: str | None = None,
    expected_manifest: dict[str, Any] | None = None,
) -> None:
    observed = _validate_snapshot_manifest(observed, label="observed manifest")
    if expected_manifest is not None:
        expected_manifest = _validate_snapshot_manifest(
            expected_manifest, label="expected manifest"
        )
        if observed.get("manifest_id") != expected_manifest.get("manifest_id"):
            fail("home add: observed SHA-256 manifest differs from the reviewed manifest")
        return
    if expected_manifest_id is None:
        return
    if observed.get("manifest_id") != expected_manifest_id:
        fail("home add: observed SHA-256 manifest differs from the reviewed manifest")


def source_attested_receipt_id(receipt: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(
        {key: value for key, value in receipt.items() if key != "receipt_id"}
    )


def _validate_snapshot_manifest(value: Any, *, label: str) -> dict[str, Any]:
    """Validate the closed snapshot-manifest form owned by model_library.py."""
    manifest_fields = {
        "schema_version",
        "kind",
        "model_id",
        "snapshot_revision",
        "files",
        "file_count",
        "total_bytes",
        "manifest_id",
    }
    if not isinstance(value, dict) or set(value) != manifest_fields:
        fail(f"{label} fields differ from the closed snapshot-manifest schema")
    files = value.get("files")
    if not isinstance(files, list):
        fail(f"{label}.files must be a list")
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            fail(f"{label}.files[{index}] fields are invalid")
    try:
        try:
            from scripts import model_library
        except ModuleNotFoundError:
            import model_library  # type: ignore[no-redef]
        manifest = model_library.validate_snapshot_manifest(value)
    except Exception as exc:
        if isinstance(exc, SourceAttestedAcquisitionError):
            raise
        fail(f"{label} is invalid: {exc}")
    for index, item in enumerate(manifest["files"]):
        _validate_inventory_path(item["path"], label=f"{label}.files[{index}].path")
    _public_string(manifest["model_id"], label=f"{label}.model_id")
    _public_string(
        manifest["snapshot_revision"], label=f"{label}.snapshot_revision"
    )
    return manifest


def validate_source_attested_acquisition_receipt(value: Any) -> dict[str, Any]:
    receipt = _require_fields(
        value, SOURCE_ATTESTED_RECEIPT_FIELDS, label="source-attested receipt"
    )
    _reject_prohibited_public_fields(receipt, label="source-attested receipt")
    if receipt.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("source-attested receipt schema is unsupported")
    if receipt.get("kind") != SOURCE_ATTESTED_ACQUISITION_RECEIPT_KIND:
        fail("source-attested receipt kind is invalid")
    source = validate_huggingface_v1_acquisition_source(receipt.get("source"))
    identity = validate_acquisition_identity(receipt.get("identity"))
    approval = validate_source_attested_acquisition_approval(receipt.get("approval"))
    manifest = _validate_snapshot_manifest(
        receipt.get("observed_manifest"), label="receipt observed_manifest"
    )
    if manifest.get("model_id") != source["model_id"]:
        fail("receipt observed manifest model_id differs from the source")
    if manifest.get("snapshot_revision") != source["snapshot_revision"]:
        fail("receipt observed manifest revision differs from the source")
    expected_files = {
        item["path"]: item["size"] for item in source["inventory"]
    }
    observed_files = {
        item["path"]: item["size"] for item in manifest["files"]
    }
    if observed_files != expected_files:
        fail("receipt observed manifest file set differs from the source inventory")
    if receipt.get("model_id") != source["model_id"]:
        fail("receipt model_id differs from the source")
    if receipt.get("snapshot_revision") != source["snapshot_revision"]:
        fail("receipt revision differs from the source")
    if receipt.get("selected_rank") != approval["selected_rank"]:
        fail("receipt selected_rank differs from the approval")
    if receipt.get("serving_ranks") != approval["serving_ranks"]:
        fail("receipt serving_ranks differ from the approval")
    if identity["source_digest"] != source["source_digest"]:
        fail("receipt identity does not match its source")
    source_links = (
        "model_id",
        "selector",
        "snapshot_revision",
        "source_digest",
        "inventory_digest",
        "file_count",
        "content_bytes",
    )
    for field in source_links:
        if approval[field] != source[field]:
            fail(f"receipt approval {field} does not match its source")
    identity_links = (
        "identity_class",
        "profile",
        "model_id",
        "selector",
        "snapshot_revision",
        "source_digest",
        "inventory_digest",
        "file_count",
        "content_bytes",
        "model_serving_release_id",
        "seal_id",
        "validation_bundle_id",
        "expected_manifest_id",
    )
    for field in identity_links:
        if approval[field] != identity[field]:
            fail(f"receipt approval {field} does not match its identity")
    if receipt.get("receipt_id") != source_attested_receipt_id(receipt):
        fail("source-attested receipt identity mismatch")
    _public_json(
        {
            key: receipt[key]
            for key in (
                "schema_version",
                "kind",
                "receipt_id",
                "model_id",
                "snapshot_revision",
                "selected_rank",
                "serving_ranks",
            )
        },
        label="source-attested receipt summary",
    )
    return receipt


def build_source_attested_acquisition_receipt(
    *,
    source: dict[str, Any],
    identity: dict[str, Any],
    approval: dict[str, Any],
    observed_manifest: dict[str, Any],
) -> dict[str, Any]:
    source = validate_huggingface_v1_acquisition_source(source)
    identity = validate_acquisition_identity(identity)
    approval = validate_source_attested_acquisition_approval(approval)
    observed_manifest = _validate_snapshot_manifest(
        observed_manifest, label="receipt observed_manifest"
    )
    compare_observed_manifest_to_expected(
        observed_manifest,
        expected_manifest_id=identity.get("expected_manifest_id"),
    )
    receipt = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_ACQUISITION_RECEIPT_KIND,
        "source": source,
        "identity": identity,
        "approval": approval,
        "observed_manifest": observed_manifest,
        "selected_rank": approval["selected_rank"],
        "serving_ranks": list(approval["serving_ranks"]),
        "model_id": source["model_id"],
        "snapshot_revision": source["snapshot_revision"],
    }
    receipt["receipt_id"] = source_attested_receipt_id(receipt)
    return validate_source_attested_acquisition_receipt(receipt)


def source_attested_receipt_store(library_dir: str | pathlib.Path) -> pathlib.Path:
    return pathlib.Path(library_dir) / "source-attested-receipts"


def _ensure_receipt_store(library_dir: str | pathlib.Path) -> pathlib.Path:
    library = pathlib.Path(library_dir)
    library.mkdir(parents=True, exist_ok=True)
    try:
        library_info = library.lstat()
    except OSError as exc:
        fail(f"source-attested library directory is unavailable: {exc}")
    if stat.S_ISLNK(library_info.st_mode) or not stat.S_ISDIR(library_info.st_mode):
        fail("source-attested library directory is not a regular directory")
    store = source_attested_receipt_store(library)
    try:
        os.mkdir(store, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        fail(f"source-attested receipt store cannot be created: {exc}")
    try:
        info = store.lstat()
    except OSError as exc:
        fail(f"source-attested receipt store is unavailable: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail("source-attested receipt store is not a regular directory")
    try:
        os.chmod(store, 0o700, follow_symlinks=False)
    except OSError as exc:
        fail(f"source-attested receipt store permissions cannot be set: {exc}")
    return store


def _load_receipt_path(path: pathlib.Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        fail(f"source-attested receipt is unreadable: {exc}")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            fail("source-attested receipt is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            fail("source-attested receipt changed during read")
    finally:
        os.close(fd)
    receipt = validate_source_attested_acquisition_receipt(
        _load_json_value(raw, label="source-attested receipt")
    )
    if path.name != f"{receipt['receipt_id']}.json":
        fail("source-attested receipt filename does not match its identity")
    return receipt


def _write_receipt_exclusive(path: pathlib.Path, receipt: dict[str, Any]) -> None:
    raw = model_identity.pretty_json_bytes(receipt)
    store_fd = os.open(
        path.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    temp_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temp_created = False
    try:
        fd = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=store_fd,
        )
        temp_created = True
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(
                temp_name,
                path.name,
                src_dir_fd=store_fd,
                dst_dir_fd=store_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            fail("home add: a source-attested receipt already exists")
        os.unlink(temp_name, dir_fd=store_fd)
        temp_created = False
        os.fsync(store_fd)
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=store_fd)
            except OSError:
                pass
        os.close(store_fd)


def write_source_attested_receipt(
    library_dir: str | pathlib.Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    store = _ensure_receipt_store(library_dir)
    path = store / f"{receipt['receipt_id']}.json"
    existing_identity = find_source_attested_receipt(
        library_dir,
        model_id=receipt["model_id"],
        snapshot_revision=receipt["snapshot_revision"],
    )
    if existing_identity is not None and existing_identity["receipt_id"] != receipt["receipt_id"]:
        fail("home add: a different source-attested receipt already exists")
    if path.exists() or path.is_symlink():
        existing = load_source_attested_receipt(library_dir, receipt["receipt_id"])
        if existing != receipt:
            fail("home add: a different source-attested receipt already exists")
        return existing
    try:
        _write_receipt_exclusive(path, receipt)
    except SourceAttestedAcquisitionError as exc:
        if "already exists" not in str(exc):
            raise
        existing = load_source_attested_receipt(library_dir, receipt["receipt_id"])
        if existing != receipt:
            fail("home add: a different source-attested receipt already exists")
        return existing
    return receipt


def load_source_attested_receipt(
    library_dir: str | pathlib.Path,
    receipt_id: str,
) -> dict[str, Any]:
    receipt_id = _validate_hex_id(receipt_id, label="receipt_id")
    path = source_attested_receipt_store(library_dir) / f"{receipt_id}.json"
    return _load_receipt_path(path)


def find_source_attested_receipt(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
) -> dict[str, Any] | None:
    _validate_hf_model_id(model_id, label="receipt model_id")
    _validate_commit(snapshot_revision, label="receipt snapshot_revision")
    store = source_attested_receipt_store(library_dir)
    try:
        store_info = store.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        fail(f"source-attested receipt store is unavailable: {exc}")
    if stat.S_ISLNK(store_info.st_mode) or not stat.S_ISDIR(store_info.st_mode):
        fail("source-attested receipt store is not a regular directory")
    matches: list[dict[str, Any]] = []
    receipt_name = re.compile(r"^[0-9a-f]{64}\.json$")
    for path in sorted(store.iterdir()):
        if receipt_name.fullmatch(path.name) is None:
            fail(f"source-attested receipt store contains an unexpected entry: {path.name}")
        candidate = _load_receipt_path(path)
        if (
            candidate["model_id"] == model_id
            and candidate["snapshot_revision"] == snapshot_revision
        ):
            matches.append(candidate)
    if not matches:
        return None
    receipt_ids = {item["receipt_id"] for item in matches}
    if len(receipt_ids) != 1:
        fail("home add: conflicting source-attested receipts for this revision")
    return matches[0]


def verify_source_attested_home(
    receipt: dict[str, Any],
    observed_manifest: dict[str, Any],
    *,
    model_id: str,
    snapshot_revision: str,
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    _validate_hf_model_id(model_id, label="verify model_id")
    _validate_commit(snapshot_revision, label="verify snapshot_revision")
    if receipt["model_id"] != model_id:
        fail("home verify: receipt model_id differs from the selected home")
    if receipt["snapshot_revision"] != snapshot_revision:
        fail("home verify: receipt revision differs from the selected home")
    observed_manifest = _validate_snapshot_manifest(
        observed_manifest, label="home verify observed manifest"
    )
    expected = receipt["observed_manifest"]
    if observed_manifest.get("manifest_id") != expected.get("manifest_id"):
        fail("home verify: offline SHA-256 rehash differs from the receipt")
    if observed_manifest.get("files") != expected.get("files"):
        fail("home verify: observed file set differs from the receipt")
    result = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_VERIFY_KIND,
        "state": "verified",
        "receipt_id": receipt["receipt_id"],
        "identity_class": receipt["identity"]["identity_class"],
        "model_id": receipt["model_id"],
        "snapshot_revision": receipt["snapshot_revision"],
        "file_count": expected.get("file_count"),
        "bytes_hashed": expected.get("total_bytes"),
    }
    return validate_source_attested_home_verify_result(result)


def validate_source_attested_home_verify_result(value: Any) -> dict[str, Any]:
    result = _require_fields(
        value, SOURCE_ATTESTED_VERIFY_FIELDS, label="source-attested verify result"
    )
    _reject_prohibited_public_fields(result, label="source-attested verify result")
    if result.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("source-attested verify schema is unsupported")
    if result.get("kind") != SOURCE_ATTESTED_HOME_VERIFY_KIND:
        fail("source-attested verify kind is invalid")
    if result.get("state") != "verified":
        fail("source-attested verify state is invalid")
    if result.get("identity_class") not in ACQUISITION_IDENTITY_CLASSES:
        fail("source-attested verify identity_class is unsupported")
    _validate_hex_id(result.get("receipt_id"), label="verify receipt_id")
    _validate_hf_model_id(result.get("model_id"), label="verify model_id")
    _validate_commit(result.get("snapshot_revision"), label="verify snapshot_revision")
    _require_positive_int(result.get("file_count"), label="verify file_count")
    _require_positive_int(result.get("bytes_hashed"), label="verify bytes_hashed")
    _public_json(result, label="source-attested verify result")
    return result


def validate_source_attested_acquisition_result(value: Any) -> dict[str, Any]:
    result = _require_fields(
        value, SOURCE_ATTESTED_RESULT_FIELDS, label="source-attested result"
    )
    _reject_prohibited_public_fields(result, label="source-attested result")
    if result.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("source-attested result schema is unsupported")
    if result.get("kind") != SOURCE_ATTESTED_ACQUISITION_RESULT_KIND:
        fail("source-attested result kind is invalid")
    if result.get("state") != "published":
        fail("source-attested result state is invalid")
    if result.get("identity_class") not in ACQUISITION_IDENTITY_CLASSES:
        fail("source-attested result identity_class is unsupported")
    _validate_hex_id(result.get("receipt_id"), label="result receipt_id")
    _validate_hex_id(result.get("source_digest"), label="result source_digest")
    _validate_hex_id(result.get("approval_id"), label="result approval_id")
    _validate_profile(result.get("profile"))
    _validate_hf_model_id(result.get("model_id"), label="result model_id")
    _validate_commit(result.get("snapshot_revision"), label="result snapshot_revision")
    _require_rank(result.get("selected_rank"), label="result selected_rank")
    _validate_serving_ranks(result.get("serving_ranks"))
    _require_positive_int(result.get("file_count"), label="result file_count")
    _require_positive_int(result.get("content_bytes"), label="result content_bytes")
    _require_positive_int(result.get("bytes_hashed"), label="result bytes_hashed")
    if result.get("catalog_refreshed") is not False:
        fail("source-attested result must not claim a catalog refresh")
    if result.get("staging_cleanup") not in {"removed", "incomplete"}:
        fail("source-attested result staging_cleanup is invalid")
    _public_json(result, label="source-attested result")
    return result


def build_source_attested_acquisition_result(
    *,
    receipt: dict[str, Any],
    state: str,
    staging_cleanup: str,
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    manifest = receipt["observed_manifest"]
    result = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_ACQUISITION_RESULT_KIND,
        "state": state,
        "receipt_id": receipt["receipt_id"],
        "source_digest": receipt["source"]["source_digest"],
        "approval_id": receipt["approval"]["approval_id"],
        "identity_class": receipt["identity"]["identity_class"],
        "profile": receipt["identity"]["profile"],
        "model_id": receipt["model_id"],
        "snapshot_revision": receipt["snapshot_revision"],
        "selected_rank": receipt["selected_rank"],
        "serving_ranks": list(receipt["serving_ranks"]),
        "file_count": manifest.get("file_count"),
        "content_bytes": receipt["source"]["content_bytes"],
        "bytes_hashed": manifest.get("total_bytes"),
        "catalog_refreshed": False,
        "staging_cleanup": staging_cleanup,
    }
    return validate_source_attested_acquisition_result(result)


def _human_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    current = float(value)
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        current /= 1024.0
        if current < 1024.0 or unit == "TiB":
            if current >= 10:
                return f"{current:.0f} {unit}"
            return f"{current:.1f} {unit}"
    return f"{value} B"


def _terminal_writer() -> Any:
    try:
        from scripts.terminal_format import TerminalWriter
    except ModuleNotFoundError:
        try:
            from terminal_format import TerminalWriter
        except ModuleNotFoundError:
            fail("source-attested rendering requires scripts/terminal_format.py")
    return TerminalWriter()


def render_source_attested_acquisition_plan(plan: dict[str, Any]) -> None:
    plan = validate_source_attested_acquisition_plan(plan)
    source = plan["source"]
    identity = plan["identity"]
    approval = plan["approval"]
    term = _terminal_writer()
    term.emit("source-attested acquisition  PLAN")
    term.field("model", source["model_id"])
    term.field("profile", identity["profile"])
    term.field("selector", source["selector"])
    term.field("revision", source["snapshot_revision"])
    term.field("identity", identity["identity_class"].replace("-", " "))
    term.field("files", source["file_count"])
    term.field("content", _human_bytes(source["content_bytes"]))
    term.field("home", f"rank {approval['selected_rank']}")
    term.field("serving", ", ".join(str(rank) for rank in approval["serving_ranks"]))
    term.field("placement", str(approval["selection"]).replace("-", " "))
    term.field("space", f"{_human_bytes(approval['required_free_bytes'])} required")
    term.field("approval", approval["approval_id"][:12])
    term.blank()
    term.emit("The selected rank downloads the exact commit into private staging.")
    term.emit("A receipt is written before the durable home is published.")
    term.emit("This creates observed and source identity only. It is not a seal.")


def render_source_attested_acquisition_result(result: dict[str, Any]) -> None:
    result = validate_source_attested_acquisition_result(result)
    term = _terminal_writer()
    label = "READY" if result["state"] == "published" else "UNCHANGED"
    term.emit(f"source-attested home  {label}")
    term.field("model", result["model_id"])
    term.field("revision", result["snapshot_revision"])
    term.field("home", f"rank {result['selected_rank']}")
    term.field("identity", result["identity_class"].replace("-", " "))
    term.field("receipt", result["receipt_id"][:12])
    term.field("verified", f"{_human_bytes(result['bytes_hashed'])} SHA-256")
    term.field("catalog", "unchanged · explicit refresh required")
    if result.get("staging_cleanup") != "removed":
        term.field("warning", "private staging cleanup is incomplete")
    term.blank()
    term.emit("Next: scripts/model-library.sh catalog refresh")
    term.emit("This is catalog/artifact evidence only. No status was assigned.")


def render_source_attested_home_verify(result: dict[str, Any]) -> None:
    result = validate_source_attested_home_verify_result(result)
    term = _terminal_writer()
    term.emit("source-attested home  VERIFIED")
    term.field("model", result["model_id"])
    term.field("revision", result["snapshot_revision"])
    term.field("receipt", result["receipt_id"][:12])
    term.field("identity", result["identity_class"].replace("-", " "))
    term.field("rehash", f"{_human_bytes(result['bytes_hashed'])} SHA-256")
    term.blank()
    term.emit("Receipt-backed offline rehash matched. No status was assigned.")


def _read_arg_json(path: str, *, label: str) -> Any:
    if path == "-":
        return _load_json_value(sys.stdin.read(), label=label)
    return _load_json_path(path, label=label)


def _write_json(value: Any) -> int:
    sys.stdout.write(_pretty_json(value))
    return 0


def cmd_parse_source(args: argparse.Namespace) -> int:
    source = build_huggingface_v1_source_from_adapter(
        model_id=args.model_id,
        selector=args.selector,
        repo_info=_read_arg_json(args.repo_info, label="repo info"),
        repo_tree=(
            None
            if not args.repo_tree
            else _read_arg_json(args.repo_tree, label="repo tree")
        ),
    )
    return _write_json(source)


def cmd_resolve_identity(args: argparse.Namespace) -> int:
    source = _read_arg_json(args.source, label="source")
    expected_seal = None
    if args.expected_seal:
        expected_seal = _read_arg_json(args.expected_seal, label="expected seal")
    identity = resolve_huggingface_v1_acquisition_identity(
        source=source,
        profile=args.profile,
        expected_seal=expected_seal,
        model_serving_release_id=args.release_id or None,
        repo_root=args.repo_root or None,
    )
    return _write_json(identity)


def cmd_plan(args: argparse.Namespace) -> int:
    source = _read_arg_json(args.source, label="source")
    identity = _read_arg_json(args.identity, label="identity")
    observations: list[dict[str, Any]] = []
    root = pathlib.Path(args.observations_dir)
    for path in sorted(root.glob("rank-*.json")):
        item = _load_json_path(path, label=f"observation {path.name}")
        if not isinstance(item, dict):
            fail(f"{path.name} is not an observation object")
        observations.append(item)
    observations.sort(key=_observation_rank)
    plan, handle = plan_source_attested_acquisition(
        source=source,
        identity=identity,
        observations=observations,
        serving_nodes=args.serving_nodes,
        topology_generation=args.topology_generation,
        node_selector=args.node,
    )
    if args.handle_file:
        pathlib.Path(args.handle_file).write_text(_pretty_json(handle), encoding="utf-8")
        try:
            os.chmod(args.handle_file, 0o600)
        except OSError:
            pass
    if args.json:
        return _write_json(plan)
    render_source_attested_acquisition_plan(plan)
    return 0


def cmd_verify_approval(args: argparse.Namespace) -> int:
    plan = validate_source_attested_acquisition_plan(
        _read_arg_json(args.plan, label="plan")
    )
    source = validate_huggingface_v1_acquisition_source(
        _read_arg_json(args.source, label="source")
    )
    identity = validate_acquisition_identity(
        _read_arg_json(args.identity, label="identity")
    )
    approval = verify_source_attested_acquisition_approval(
        plan["approval"],
        source=source,
        identity=identity,
        topology_generation=args.topology_generation,
    )
    if approval["approval_id"] != plan["approval"]["approval_id"]:
        fail("approval identity mismatch")
    return _write_json(plan)


def cmd_compare_inventory(args: argparse.Namespace) -> int:
    inventory = _read_arg_json(args.inventory, label="inventory")
    observed = _read_arg_json(args.observed, label="observed files")
    compare_observed_files_to_inventory(inventory, observed)
    return _write_json({"state": "matched"})


def cmd_build_receipt(args: argparse.Namespace) -> int:
    receipt = build_source_attested_acquisition_receipt(
        source=_read_arg_json(args.source, label="source"),
        identity=_read_arg_json(args.identity, label="identity"),
        approval=_read_arg_json(args.approval, label="approval"),
        observed_manifest=_read_arg_json(args.manifest, label="observed manifest"),
    )
    if args.library_dir:
        receipt = write_source_attested_receipt(args.library_dir, receipt)
    return _write_json(receipt)


def cmd_find_receipt(args: argparse.Namespace) -> int:
    if args.receipt_id:
        receipt = load_source_attested_receipt(args.library_dir, args.receipt_id)
    elif not args.model_id or not args.revision:
        fail("find-receipt requires --receipt-id or --model-id and --revision")
    else:
        receipt = find_source_attested_receipt(
            args.library_dir,
            model_id=args.model_id,
            snapshot_revision=args.revision,
        )
        if receipt is None and not args.allow_missing:
            fail("source-attested receipt not found")
    return _write_json(receipt)


def cmd_verify_home(args: argparse.Namespace) -> int:
    receipt = validate_source_attested_acquisition_receipt(
        _read_arg_json(args.receipt, label="receipt")
    )
    observed = _read_arg_json(args.manifest, label="observed manifest")
    result = verify_source_attested_home(
        receipt,
        observed,
        model_id=args.model_id,
        snapshot_revision=args.revision,
    )
    if args.json:
        return _write_json(result)
    render_source_attested_home_verify(result)
    return 0


def cmd_render_plan(args: argparse.Namespace) -> int:
    plan = validate_source_attested_acquisition_plan(
        _read_arg_json(args.plan, label="plan")
    )
    if args.json:
        return _write_json(plan)
    render_source_attested_acquisition_plan(plan)
    return 0


def cmd_render_result(args: argparse.Namespace) -> int:
    result = validate_source_attested_acquisition_result(
        _read_arg_json(args.result, label="result")
    )
    if args.json:
        return _write_json(result)
    render_source_attested_acquisition_result(result)
    return 0


def cmd_build_result(args: argparse.Namespace) -> int:
    receipt = validate_source_attested_acquisition_receipt(
        _read_arg_json(args.receipt, label="receipt")
    )
    result = build_source_attested_acquisition_result(
        receipt=receipt,
        state=args.state,
        staging_cleanup=args.staging_cleanup,
    )
    return _write_json(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Source-attested acquisition planning and receipt helpers"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    parse_source = sub.add_parser("parse-source")
    parse_source.add_argument("--model-id", required=True)
    parse_source.add_argument("--selector", required=True)
    parse_source.add_argument("--repo-info", required=True)
    parse_source.add_argument("--repo-tree")
    parse_source.set_defaults(func=cmd_parse_source)

    resolve_identity = sub.add_parser("resolve-identity")
    resolve_identity.add_argument("--source", required=True)
    resolve_identity.add_argument("--profile", required=True)
    resolve_identity.add_argument("--expected-seal")
    resolve_identity.add_argument("--release-id", default="")
    resolve_identity.add_argument("--repo-root", default="")
    resolve_identity.set_defaults(func=cmd_resolve_identity)

    plan = sub.add_parser("plan")
    plan.add_argument("--source", required=True)
    plan.add_argument("--identity", required=True)
    plan.add_argument("--observations-dir", required=True)
    plan.add_argument("--topology-generation", required=True)
    plan.add_argument("--serving-nodes", type=int, required=True)
    plan.add_argument("--node", default="")
    plan.add_argument("--handle-file")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    verify_approval = sub.add_parser("verify-approval")
    verify_approval.add_argument("--plan", required=True)
    verify_approval.add_argument("--source", required=True)
    verify_approval.add_argument("--identity", required=True)
    verify_approval.add_argument("--topology-generation", required=True)
    verify_approval.set_defaults(func=cmd_verify_approval)

    compare = sub.add_parser("compare-inventory")
    compare.add_argument("--inventory", required=True)
    compare.add_argument("--observed", required=True)
    compare.set_defaults(func=cmd_compare_inventory)

    build_receipt = sub.add_parser("build-receipt")
    build_receipt.add_argument("--source", required=True)
    build_receipt.add_argument("--identity", required=True)
    build_receipt.add_argument("--approval", required=True)
    build_receipt.add_argument("--manifest", required=True)
    build_receipt.add_argument("--library-dir")
    build_receipt.set_defaults(func=cmd_build_receipt)

    find_receipt = sub.add_parser("find-receipt")
    find_receipt.add_argument("--library-dir", required=True)
    find_receipt.add_argument("--receipt-id")
    find_receipt.add_argument("--model-id")
    find_receipt.add_argument("--revision")
    find_receipt.add_argument("--allow-missing", action="store_true")
    find_receipt.set_defaults(func=cmd_find_receipt)

    verify_home = sub.add_parser("verify-home")
    verify_home.add_argument("--receipt", required=True)
    verify_home.add_argument("--manifest", required=True)
    verify_home.add_argument("--model-id", required=True)
    verify_home.add_argument("--revision", required=True)
    verify_home.add_argument("--json", action="store_true")
    verify_home.set_defaults(func=cmd_verify_home)

    render_plan = sub.add_parser("render-plan")
    render_plan.add_argument("--plan", required=True)
    render_plan.add_argument("--json", action="store_true")
    render_plan.set_defaults(func=cmd_render_plan)

    render_result = sub.add_parser("render-result")
    render_result.add_argument("--result", required=True)
    render_result.add_argument("--json", action="store_true")
    render_result.set_defaults(func=cmd_render_result)

    build_result = sub.add_parser("build-result")
    build_result.add_argument("--receipt", required=True)
    build_result.add_argument("--state", default="published", choices=("published",))
    build_result.add_argument("--staging-cleanup", default="removed")
    build_result.set_defaults(func=cmd_build_result)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SourceAttestedAcquisitionError as exc:
        print(f"source-attested: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
