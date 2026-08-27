#!/usr/bin/env python3
"""Hugging Face download (recorded file list) acquisition contracts.

This module owns the closed source inventory, identity
precedence, privacy-safe approval, public plan, immutable receipt,
site-local current-home attachment, and offline verification helpers. It
parses Hub metadata JSON and manages site-local receipts and current-home
attachments, but it does not call the Hub, accept a token, refresh the
catalog, prepare a runtime view, launch, assign status, or issue a Model
Serving Release decision.

Lab expected-identity HOME_ACQUISITION plan/result contracts are retired
(ADR 0012). This module is intentionally not imported from model_library.py
so remote inspection can still stream that file alone.
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
    "pulsar-model-library-download-approval"
)
SOURCE_ATTESTED_ACQUISITION_PLAN_KIND = (
    "pulsar-model-library-download-plan"
)
SOURCE_ATTESTED_ACQUISITION_RECEIPT_KIND = (
    "pulsar-model-library-download-receipt"
)
SOURCE_ATTESTED_ACQUISITION_RESULT_KIND = (
    "pulsar-model-library-download-result"
)
SOURCE_ATTESTED_HOME_VERIFY_KIND = (
    "pulsar-model-library-home-verify-result"
)
SOURCE_ATTESTED_HOME_ATTACHMENT_KIND = (
    "pulsar-model-library-home-occupancy"
)
SOURCE_ATTESTED_HOME_ATTACHMENT_KEY_KIND = (
    "pulsar-model-library-home-occupancy-key"
)
SOURCE_ATTESTED_HOME_ATTACHMENT_RESULT_KIND = (
    "pulsar-model-library-home-occupancy-result"
)
SOURCE_ATTESTED_HOME_AUTHORITY_KIND = (
    "pulsar-model-library-home-occupancy-authority"
)
LIVE_DIRECTORY_IDENTITY_KIND = "pulsar-model-library-live-directory-identity"
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
IDENTITY_CLASS_DOWNLOAD_RECEIPT = "download-receipt"
ACQUISITION_IDENTITY_CLASSES = {
    IDENTITY_CLASS_REVIEWED_RELEASE,
    IDENTITY_CLASS_DOWNLOAD_RECEIPT,
}

EXECUTION_CONTRACT_COMPLETE_MANIFEST = "complete-expected-manifest"
EXECUTION_CONTRACT_MANIFEST_ID = "expected-manifest-id"
EXECUTION_CONTRACT_SOURCE_ATTESTED = "download-receipt-complete-hash"
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
SOURCE_ATTESTED_HOME_ATTACHMENT_FIELDS = {
    "schema_version",
    "kind",
    "attachment_key",
    "receipt_id",
    "model_id",
    "snapshot_revision",
    "inventory_digest",
    "observed_manifest_id",
    "selected_rank",
    "node_id",
    "durable_home_path",
    "directory_identity",
}
SOURCE_ATTESTED_DIRECTORY_IDENTITY_FIELDS = {"device", "inode", "ctime_ns"}
LIVE_DIRECTORY_IDENTITY_FIELDS = {
    "schema_version",
    "kind",
    "path",
    "device",
    "inode",
    "ctime_ns",
}
SOURCE_ATTESTED_HOME_ATTACHMENT_RESULT_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "receipt_id",
    "model_id",
    "snapshot_revision",
}
HOME_AUTHORITY_ATTACHED = "attached"
HOME_AUTHORITY_NONE = "no-authority"
HOME_AUTHORITY_MISSING_ATTACHMENT = "missing-attachment"
HOME_AUTHORITY_STALE_ATTACHMENT = "stale-attachment"
HOME_AUTHORITY_MISSING_RECEIPT = "missing-receipt"
HOME_AUTHORITY_INCOMPATIBLE_RECEIPT = "incompatible-receipt"
HOME_AUTHORITY_REASONS = {
    HOME_AUTHORITY_MISSING_ATTACHMENT,
    HOME_AUTHORITY_STALE_ATTACHMENT,
    HOME_AUTHORITY_MISSING_RECEIPT,
    HOME_AUTHORITY_INCOMPATIBLE_RECEIPT,
}
STORE_FINAL_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
STORE_WRITER_TEMP_RE = re.compile(
    r"^\.[0-9a-f]{64}\.json\.[0-9]+\.[0-9a-f]{16}\.tmp$"
)
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
    """Malformed or conflicting download-receipt acquisition contract."""


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
    if identity_class == IDENTITY_CLASS_DOWNLOAD_RECEIPT:
        if identity.get("model_serving_release_id") is not None:
            fail("download-receipt identity must not carry a release binding")
        if identity.get("seal_id") is not None:
            fail("download-receipt identity must not carry a reviewed seal")
        if identity.get("validation_bundle_id") is not None:
            fail("download-receipt identity must not carry a validation bundle")
        if identity.get("expected_manifest_id") is not None:
            fail("download-receipt identity must not carry an expected manifest")
        if execution_contract != EXECUTION_CONTRACT_SOURCE_ATTESTED:
            fail(
                "download-receipt identity execution_contract must be "
                "download-receipt-complete-hash"
            )
    else:
        if identity.get("model_serving_release_id") is None:
            fail("reviewed release identity requires model_serving_release_id")
        if identity.get("expected_manifest_id") is None:
            fail("reviewed release identity requires expected_manifest_id")
        if identity.get("seal_id") is not None:
            fail("reviewed release identity must not carry a retired expected seal")
        if identity.get("validation_bundle_id") is not None:
            fail("reviewed release identity must not carry a schema-1 validation bundle")
        if execution_contract != EXECUTION_CONTRACT_MANIFEST_ID:
            fail(
                "release-only identity execution_contract must be "
                "expected-manifest-id"
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
    """Resolve a verified Model Serving Release, else unbound source identity.

    A bound release that cannot be verified fails without falling back.
    Expected-seal identity is retired (ADR 0012).
    """
    source = validate_huggingface_v1_acquisition_source(source)
    profile = _validate_profile(profile)
    if expected_seal is not None:
        fail(
            "expected-seal acquisition identity is retired (ADR 0012); "
            "use home add --revision or a bound Model Serving Release"
        )
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
        return _identity_document(
            source=source,
            profile=profile,
            identity_class=IDENTITY_CLASS_REVIEWED_RELEASE,
            execution_contract=EXECUTION_CONTRACT_MANIFEST_ID,
            model_serving_release_id=release["release_id"],
            seal_id=None,
            validation_bundle_id=None,
            expected_manifest_id=expected["manifest_id"],
        )

    return _identity_document(
        source=source,
        profile=profile,
        identity_class=IDENTITY_CLASS_DOWNLOAD_RECEIPT,
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
        value, SOURCE_ATTESTED_APPROVAL_FIELDS, label="download-receipt approval"
    )
    _reject_prohibited_approval_fields(approval)
    if approval.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("download-receipt approval schema is unsupported")
    if approval.get("kind") != SOURCE_ATTESTED_ACQUISITION_APPROVAL_KIND:
        fail("download-receipt approval kind is invalid")
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
    if identity_class == IDENTITY_CLASS_DOWNLOAD_RECEIPT:
        if any(
            item is not None
            for item in (release_id, seal_id, bundle_id, manifest_id)
        ):
            fail("download-receipt approval must not carry reviewed identity")
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
    _public_json(approval, label="download-receipt approval")
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
    plan = _require_fields(value, SOURCE_ATTESTED_PLAN_FIELDS, label="download-receipt plan")
    _reject_prohibited_public_fields(plan, label="download-receipt plan")
    if plan.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("download-receipt plan schema is unsupported")
    if plan.get("kind") != SOURCE_ATTESTED_ACQUISITION_PLAN_KIND:
        fail("download-receipt plan kind is invalid")
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
            fail(f"download-receipt plan identity {field} differs from its source")
        if approval[field] != source[field]:
            fail(f"download-receipt plan approval {field} differs from its source")
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
            fail(f"download-receipt plan approval {field} differs from its identity")
    if plan.get("plan_id") != source_attested_plan_id(plan):
        fail("download-receipt plan identity mismatch")
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
        label="download-receipt plan summary",
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


def source_attested_geometry_ranks(
    confirmed_ranks: list[int],
    *,
    serving_nodes: int,
) -> list[int]:
    """Return the serving-geometry ranks that may host a durable home."""
    if not isinstance(confirmed_ranks, list) or not confirmed_ranks:
        fail("home add: not every confirmed rank was observed")
    ranks = [_require_rank(item, label="confirmed rank") for item in confirmed_ranks]
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
    if serving_nodes == 1:
        return list(ranks)
    return list(range(serving_nodes))


def huggingface_v1_source_content_identity(source: dict[str, Any]) -> dict[str, Any]:
    """Return the selector-neutral source bytes used for candidate agreement."""
    source = validate_huggingface_v1_acquisition_source(source)
    return {
        "model_id": source["model_id"],
        "snapshot_revision": source["snapshot_revision"],
        "inventory": source["inventory"],
        "inventory_digest": source["inventory_digest"],
        "file_count": source["file_count"],
        "content_bytes": source["content_bytes"],
    }


def unique_source_attested_source(sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Require every successful candidate to report the same source content."""
    if not isinstance(sources, list) or not sources:
        fail("home add: no candidate rank resolved Hugging Face source metadata")
    normalized = [
        validate_huggingface_v1_acquisition_source(item) for item in sources
    ]
    first = huggingface_v1_source_content_identity(normalized[0])
    for item in normalized[1:]:
        if huggingface_v1_source_content_identity(item) != first:
            fail(
                "home add: candidate ranks resolved disagreeing Hugging Face "
                "source metadata"
            )
    return normalized[0]


def parse_metadata_resolved_ranks(value: str | None) -> list[int] | None:
    if value is None or value == "":
        return None
    ranks: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            fail("metadata-resolved ranks contain an empty entry")
        if not token.isdigit():
            fail("metadata-resolved ranks must be non-negative rank numbers")
        ranks.append(_require_rank(int(token), label="metadata-resolved rank"))
    if ranks != sorted(set(ranks)):
        fail("metadata-resolved ranks must be uniquely sorted")
    return ranks


def select_source_attested_target(
    observations: list[dict[str, Any]],
    *,
    serving_nodes: int,
    required_free_bytes: int,
    node_selector: str = "",
    metadata_resolved_ranks: list[int] | None = None,
) -> tuple[dict[str, Any], str, list[int]]:
    """Select one eligible rank from already-collected observations."""
    if not isinstance(observations, list) or not observations:
        fail("home add: not every confirmed rank was observed")
    ranks = [_observation_rank(item) for item in observations]
    candidate_ranks = source_attested_geometry_ranks(
        ranks, serving_nodes=serving_nodes
    )
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
    metadata_set = (
        None
        if metadata_resolved_ranks is None
        else set(metadata_resolved_ranks)
    )

    def eligible(item: dict[str, Any]) -> bool:
        rank = _observation_rank(item)
        return bool(
            item.get("target_state") == "absent"
            and item.get("writable")
            and valid_source_attested_hf_cli(item.get("hf_cli"))
            and isinstance(item.get("available_bytes"), int)
            and not isinstance(item.get("available_bytes"), bool)
            and item["available_bytes"] >= required_free_bytes
            and (metadata_set is None or rank in metadata_set)
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
            elif (
                metadata_set is not None
                and _observation_rank(selected) not in metadata_set
            ):
                detail = "Hugging Face source metadata is unavailable on this rank"
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
    metadata_resolved_ranks: list[int] | None = None,
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
        metadata_resolved_ranks=metadata_resolved_ranks,
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
        value, SOURCE_ATTESTED_RECEIPT_FIELDS, label="download-receipt receipt"
    )
    _reject_prohibited_public_fields(receipt, label="download-receipt receipt")
    if receipt.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("download-receipt receipt schema is unsupported")
    if receipt.get("kind") != SOURCE_ATTESTED_ACQUISITION_RECEIPT_KIND:
        fail("download-receipt receipt kind is invalid")
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
        fail("download-receipt receipt identity mismatch")
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
        label="download-receipt receipt summary",
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
    return pathlib.Path(library_dir) / "download-receipts"


def source_attested_home_attachment_store(
    library_dir: str | pathlib.Path,
) -> pathlib.Path:
    return pathlib.Path(library_dir) / "home-occupancy"


def _ensure_private_store(
    library_dir: str | pathlib.Path,
    store_name: str,
    *,
    label: str,
) -> pathlib.Path:
    library = pathlib.Path(library_dir)
    library.mkdir(parents=True, exist_ok=True)
    try:
        library_info = library.lstat()
    except OSError as exc:
        fail(f"download-receipt library directory is unavailable: {exc}")
    if stat.S_ISLNK(library_info.st_mode) or not stat.S_ISDIR(library_info.st_mode):
        fail("download-receipt library directory is not a regular directory")
    store = library / store_name
    try:
        os.mkdir(store, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        fail(f"{label} cannot be created: {exc}")
    try:
        info = store.lstat()
    except OSError as exc:
        fail(f"{label} is unavailable: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{label} is not a regular directory")
    try:
        os.chmod(store, 0o700, follow_symlinks=False)
    except OSError as exc:
        fail(f"{label} permissions cannot be set: {exc}")
    return store


def _ensure_receipt_store(library_dir: str | pathlib.Path) -> pathlib.Path:
    return _ensure_private_store(
        library_dir,
        "download-receipts",
        label="download-receipt receipt store",
    )


def _ensure_home_attachment_store(library_dir: str | pathlib.Path) -> pathlib.Path:
    return _ensure_private_store(
        library_dir,
        "home-occupancy",
        label="home-occupancy store",
    )


def _iter_private_store_final_paths(
    store: pathlib.Path,
    *,
    label: str,
) -> list[pathlib.Path]:
    try:
        store_info = store.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        fail(f"{label} is unavailable: {exc}")
    if stat.S_ISLNK(store_info.st_mode) or not stat.S_ISDIR(store_info.st_mode):
        fail(f"{label} is not a regular directory")
    try:
        names = sorted(os.listdir(store))
    except OSError as exc:
        fail(f"{label} is unreadable: {exc}")
    finals: list[pathlib.Path] = []
    for name in names:
        path = store / name
        try:
            info = path.lstat()
        except OSError:
            fail(f"{label} entry is unreadable")
        if STORE_FINAL_NAME_RE.fullmatch(name) is not None:
            if not stat.S_ISREG(info.st_mode):
                fail(f"{label} contains a non-regular final document")
            finals.append(path)
            continue
        if STORE_WRITER_TEMP_RE.fullmatch(name) is not None:
            if not stat.S_ISREG(info.st_mode):
                fail(f"{label} contains a non-regular writer temp")
            continue
        fail(f"{label} contains an unexpected entry: {name}")
    return finals


def _load_receipt_path(
    path: pathlib.Path,
    *,
    require_canonical: bool = False,
) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        fail(f"download-receipt receipt is unreadable: {exc}")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            fail("download-receipt receipt is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            fail("download-receipt receipt changed during read")
    finally:
        os.close(fd)
    receipt = validate_source_attested_acquisition_receipt(
        _load_json_value(raw, label="download-receipt receipt")
    )
    if path.name != f"{receipt['receipt_id']}.json":
        fail("download-receipt receipt filename does not match its identity")
    if require_canonical and raw != model_identity.pretty_json_bytes(receipt):
        fail("download-receipt receipt does not use canonical JSON encoding")
    return receipt


def _write_receipt_exclusive(
    path: pathlib.Path,
    receipt: dict[str, Any],
    *,
    operation: str,
) -> None:
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
            fail(f"{operation}: a download-receipt receipt already exists")
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
    *,
    operation: str = "home add",
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    store = _ensure_receipt_store(library_dir)
    path = store / f"{receipt['receipt_id']}.json"
    for existing in list_source_attested_receipts_for_revision(
        library_dir,
        model_id=receipt["model_id"],
        snapshot_revision=receipt["snapshot_revision"],
    ):
        if not source_attested_receipts_are_content_compatible(existing, receipt):
            fail(
                f"{operation}: an incompatible download-receipt receipt already exists"
            )
    if path.exists() or path.is_symlink():
        existing = load_source_attested_receipt(library_dir, receipt["receipt_id"])
        if existing != receipt:
            fail(f"{operation}: a different download-receipt receipt already exists")
        return existing
    try:
        _write_receipt_exclusive(path, receipt, operation=operation)
    except SourceAttestedAcquisitionError as exc:
        if "already exists" not in str(exc):
            raise
        existing = load_source_attested_receipt(library_dir, receipt["receipt_id"])
        if existing != receipt:
            fail(f"{operation}: a different download-receipt receipt already exists")
        return existing
    return receipt


def load_source_attested_receipt(
    library_dir: str | pathlib.Path,
    receipt_id: str,
    *,
    require_canonical: bool = False,
) -> dict[str, Any]:
    receipt_id = _validate_hex_id(receipt_id, label="receipt_id")
    path = source_attested_receipt_store(library_dir) / f"{receipt_id}.json"
    return _load_receipt_path(path, require_canonical=require_canonical)


def source_attested_receipts_are_content_compatible(
    left: dict[str, Any],
    right: dict[str, Any],
) -> bool:
    """Compare byte identity, ignoring selector and placement context."""
    left = validate_source_attested_acquisition_receipt(left)
    right = validate_source_attested_acquisition_receipt(right)
    return (
        left["model_id"] == right["model_id"]
        and left["snapshot_revision"] == right["snapshot_revision"]
        and left["source"]["inventory_digest"] == right["source"]["inventory_digest"]
        and left["source"]["inventory"] == right["source"]["inventory"]
        and left["observed_manifest"] == right["observed_manifest"]
    )


def _listed_source_attested_receipts(
    library_dir: str | pathlib.Path,
) -> list[dict[str, Any]]:
    store = source_attested_receipt_store(library_dir)
    return [
        _load_receipt_path(path)
        for path in _iter_private_store_final_paths(
            store, label="download-receipt receipt store"
        )
    ]


def list_source_attested_receipts_for_revision(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
) -> list[dict[str, Any]]:
    _validate_hf_model_id(model_id, label="receipt model_id")
    _validate_commit(snapshot_revision, label="receipt snapshot_revision")
    matches = [
        item
        for item in _listed_source_attested_receipts(library_dir)
        if item["model_id"] == model_id
        and item["snapshot_revision"] == snapshot_revision
    ]
    if not matches:
        return []
    first = matches[0]
    for item in matches[1:]:
        if not source_attested_receipts_are_content_compatible(first, item):
            fail("home add: incompatible download-receipt receipts for this revision")
    return matches


def find_source_attested_receipt(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
) -> dict[str, Any] | None:
    """Return one stored receipt for the revision, if any.

    This is historical store lookup, not live-home authority. Multiple
    compatible receipts may exist; the current-home attachment selects the
    publication that owns the live directory.
    """
    matches = list_source_attested_receipts_for_revision(
        library_dir,
        model_id=model_id,
        snapshot_revision=snapshot_revision,
    )
    if not matches:
        return None
    return min(matches, key=lambda item: item["receipt_id"])


def source_attested_home_attachment_key(
    *,
    model_id: str,
    snapshot_revision: str,
) -> str:
    _validate_hf_model_id(model_id, label="attachment model_id")
    _validate_commit(snapshot_revision, label="attachment snapshot_revision")
    return model_identity.canonical_json_digest(
        {
            "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
            "kind": SOURCE_ATTESTED_HOME_ATTACHMENT_KEY_KIND,
            "model_id": model_id,
            "snapshot_revision": snapshot_revision,
        }
    )


def _require_private_node_id(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        fail("current-home attachment node identity is invalid")
    if any(character in value for character in ("\x00", "\n", "\r")):
        fail("current-home attachment node identity is invalid")
    return value


def _require_durable_home_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        fail("current-home attachment durable-home path is invalid")
    if any(character in value for character in ("\x00", "\n", "\r")):
        fail("current-home attachment durable-home path is invalid")
    try:
        from scripts import immutable_descriptor_dir as descriptor_dir
    except ModuleNotFoundError:
        import immutable_descriptor_dir as descriptor_dir  # type: ignore[no-redef]
    try:
        normalized = descriptor_dir.safe_absolute(
            pathlib.Path(value), label="durable-home path"
        )
    except descriptor_dir.ImmutableDescriptorDirectoryError:
        fail("current-home attachment durable-home path is invalid")
    if str(normalized) != value:
        fail("current-home attachment durable-home path is invalid")
    return value


def _require_identity_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        fail(f"{label} is invalid")
    return value


def _validate_directory_identity(value: Any) -> dict[str, Any]:
    identity = _require_fields(
        value,
        SOURCE_ATTESTED_DIRECTORY_IDENTITY_FIELDS,
        label="current-home directory identity",
    )
    return {
        "device": _require_identity_int(
            identity["device"], label="current-home directory device"
        ),
        "inode": _require_identity_int(
            identity["inode"], label="current-home directory inode", minimum=1
        ),
        "ctime_ns": _require_identity_int(
            identity["ctime_ns"], label="current-home directory ctime"
        ),
    }


def validate_live_directory_identity(value: Any) -> dict[str, Any]:
    identity = _require_fields(
        value, LIVE_DIRECTORY_IDENTITY_FIELDS, label="live directory identity"
    )
    if identity.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("live directory identity schema is unsupported")
    if identity.get("kind") != LIVE_DIRECTORY_IDENTITY_KIND:
        fail("live directory identity kind is invalid")
    path = _require_durable_home_path(identity.get("path"))
    fields = _validate_directory_identity(
        {
            "device": identity.get("device"),
            "inode": identity.get("inode"),
            "ctime_ns": identity.get("ctime_ns"),
        }
    )
    return {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": LIVE_DIRECTORY_IDENTITY_KIND,
        "path": path,
        **fields,
    }


def validate_source_attested_home_attachment(value: Any) -> dict[str, Any]:
    attachment = _require_fields(
        value,
        SOURCE_ATTESTED_HOME_ATTACHMENT_FIELDS,
        label="current-home attachment",
    )
    if attachment.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("current-home attachment schema is unsupported")
    if attachment.get("kind") != SOURCE_ATTESTED_HOME_ATTACHMENT_KIND:
        fail("current-home attachment kind is invalid")
    model_id = _validate_hf_model_id(
        attachment.get("model_id"), label="attachment model_id"
    )
    snapshot_revision = _validate_commit(
        attachment.get("snapshot_revision"), label="attachment snapshot_revision"
    )
    attachment_key = _validate_hex_id(
        attachment.get("attachment_key"), label="attachment_key"
    )
    expected_key = source_attested_home_attachment_key(
        model_id=model_id, snapshot_revision=snapshot_revision
    )
    if attachment_key != expected_key:
        fail("current-home attachment key does not match its model revision")
    _validate_hex_id(attachment.get("receipt_id"), label="attachment receipt_id")
    _validate_hex_id(
        attachment.get("inventory_digest"), label="attachment inventory_digest"
    )
    _validate_hex_id(
        attachment.get("observed_manifest_id"),
        label="attachment observed_manifest_id",
    )
    _require_rank(attachment.get("selected_rank"), label="attachment selected_rank")
    _require_private_node_id(attachment.get("node_id"))
    _require_durable_home_path(attachment.get("durable_home_path"))
    attachment["directory_identity"] = _validate_directory_identity(
        attachment.get("directory_identity")
    )
    return attachment


def build_source_attested_home_attachment(
    *,
    receipt: dict[str, Any],
    node_id: str,
    durable_home_path: str,
    directory_identity: dict[str, Any],
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    attachment = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_ATTACHMENT_KIND,
        "attachment_key": source_attested_home_attachment_key(
            model_id=receipt["model_id"],
            snapshot_revision=receipt["snapshot_revision"],
        ),
        "receipt_id": receipt["receipt_id"],
        "model_id": receipt["model_id"],
        "snapshot_revision": receipt["snapshot_revision"],
        "inventory_digest": receipt["source"]["inventory_digest"],
        "observed_manifest_id": receipt["observed_manifest"]["manifest_id"],
        "selected_rank": receipt["selected_rank"],
        "node_id": _require_private_node_id(node_id),
        "durable_home_path": _require_durable_home_path(durable_home_path),
        "directory_identity": _validate_directory_identity(directory_identity),
    }
    return validate_source_attested_home_attachment(attachment)


def _load_attachment_path(path: pathlib.Path) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        fail(f"current-home attachment is unreadable: {exc}")
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            fail("current-home attachment is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read()
        after = os.fstat(fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable):
            fail("current-home attachment changed during read")
    finally:
        os.close(fd)
    attachment = validate_source_attested_home_attachment(
        _load_json_value(raw, label="current-home attachment")
    )
    if path.name != f"{attachment['attachment_key']}.json":
        fail("current-home attachment filename does not match its key")
    return attachment


def _write_attachment_replace(path: pathlib.Path, attachment: dict[str, Any]) -> None:
    raw = model_identity.pretty_json_bytes(attachment)
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
        os.rename(temp_name, path.name, src_dir_fd=store_fd, dst_dir_fd=store_fd)
        temp_created = False
        os.fsync(store_fd)
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=store_fd)
            except OSError:
                pass
        os.close(store_fd)


def write_source_attested_home_attachment(
    library_dir: str | pathlib.Path,
    *,
    receipt: dict[str, Any],
    node_id: str,
    durable_home_path: str,
    directory_identity: dict[str, Any],
) -> dict[str, Any]:
    attachment = build_source_attested_home_attachment(
        receipt=receipt,
        node_id=node_id,
        durable_home_path=durable_home_path,
        directory_identity=directory_identity,
    )
    store = _ensure_home_attachment_store(library_dir)
    _iter_private_store_final_paths(
        store, label="home-occupancy store"
    )
    path = store / f"{attachment['attachment_key']}.json"
    _write_attachment_replace(path, attachment)
    return attachment


def _attachment_matches_target(
    attachment: dict[str, Any],
    *,
    model_id: str,
    snapshot_revision: str,
    node_id: str,
    durable_home_path: str,
) -> bool:
    """Match occupancy, not Hub-download provenance.

    ``selected_rank`` on the attachment is frozen download provenance copied
    from the receipt. Occupancy is node, path, and live directory identity.
    """
    return (
        attachment["model_id"] == model_id
        and attachment["snapshot_revision"] == snapshot_revision
        and attachment["node_id"] == node_id
        and attachment["durable_home_path"] == durable_home_path
    )


def _listed_source_attested_home_attachments(
    library_dir: str | pathlib.Path,
) -> list[dict[str, Any]]:
    store = source_attested_home_attachment_store(library_dir)
    return [
        _load_attachment_path(path)
        for path in _iter_private_store_final_paths(
            store, label="home-occupancy store"
        )
    ]


def load_source_attested_home_attachment(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
) -> dict[str, Any] | None:
    key = source_attested_home_attachment_key(
        model_id=model_id, snapshot_revision=snapshot_revision
    )
    attachments = _listed_source_attested_home_attachments(library_dir)
    matches = [item for item in attachments if item["attachment_key"] == key]
    if not matches:
        return None
    if len(matches) != 1:
        fail("current-home attachment store has conflicting documents")
    return matches[0]


def attach_source_attested_home_from_publication(
    library_dir: str | pathlib.Path,
    *,
    receipt: dict[str, Any],
    node_id: str,
    publish_result: dict[str, Any],
) -> dict[str, Any]:
    receipt = validate_source_attested_acquisition_receipt(receipt)
    if not isinstance(publish_result, dict):
        fail("publication result is not an object")
    if publish_result.get("state") != "published":
        fail("publication result is not a published home")
    identity = validate_live_directory_identity(
        publish_result.get("directory_identity")
    )
    target_hub = publish_result.get("target_hub")
    if target_hub != identity["path"]:
        fail("publication target differs from the published directory")
    return write_source_attested_home_attachment(
        library_dir,
        receipt=receipt,
        node_id=node_id,
        durable_home_path=identity["path"],
        directory_identity={
            "device": identity["device"],
            "inode": identity["inode"],
            "ctime_ns": identity["ctime_ns"],
        },
    )


def _home_authority_result(
    *,
    state: str,
    reason: str | None = None,
    receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if state == HOME_AUTHORITY_ATTACHED:
        if receipt is None or reason is not None:
            fail("attached home authority result is incomplete")
        return {
            "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
            "kind": SOURCE_ATTESTED_HOME_AUTHORITY_KIND,
            "state": HOME_AUTHORITY_ATTACHED,
            "reason": None,
            "receipt": validate_source_attested_acquisition_receipt(receipt),
        }
    if state != HOME_AUTHORITY_NONE or reason not in HOME_AUTHORITY_REASONS:
        fail("home authority result is invalid")
    if receipt is not None:
        fail("no-authority result must not include a receipt")
    return {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_AUTHORITY_KIND,
        "state": HOME_AUTHORITY_NONE,
        "reason": reason,
        "receipt": None,
    }


def resolve_attached_source_attested_receipt(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
    selected_rank: int,
    node_id: str,
    durable_home_path: str,
    live_identity: dict[str, Any],
) -> dict[str, Any]:
    """Resolve occupancy authority for a live directory.

    ``selected_rank`` is a caller topology hint, not a match against receipt
    download provenance. Occupancy matches node, path, and directory identity.
    """
    _validate_hf_model_id(model_id, label="authority model_id")
    _validate_commit(snapshot_revision, label="authority snapshot_revision")
    selected_rank = _require_rank(selected_rank, label="authority selected_rank")
    node_id = _require_private_node_id(node_id)
    durable_home_path = _require_durable_home_path(durable_home_path)
    live = validate_live_directory_identity(live_identity)
    attachment = load_source_attested_home_attachment(
        library_dir,
        model_id=model_id,
        snapshot_revision=snapshot_revision,
    )
    if attachment is None:
        return _home_authority_result(
            state=HOME_AUTHORITY_NONE, reason=HOME_AUTHORITY_MISSING_ATTACHMENT
        )
    live_fields = {
        "device": live["device"],
        "inode": live["inode"],
        "ctime_ns": live["ctime_ns"],
    }
    if (
        not _attachment_matches_target(
            attachment,
            model_id=model_id,
            snapshot_revision=snapshot_revision,
            node_id=node_id,
            durable_home_path=durable_home_path,
        )
        or live["path"] != attachment["durable_home_path"]
        or live_fields != attachment["directory_identity"]
    ):
        return _home_authority_result(
            state=HOME_AUTHORITY_NONE, reason=HOME_AUTHORITY_STALE_ATTACHMENT
        )
    receipt_path = (
        source_attested_receipt_store(library_dir) / f"{attachment['receipt_id']}.json"
    )
    try:
        receipt_info = receipt_path.lstat()
    except FileNotFoundError:
        return _home_authority_result(
            state=HOME_AUTHORITY_NONE, reason=HOME_AUTHORITY_MISSING_RECEIPT
        )
    except OSError as exc:
        fail(f"download-receipt receipt is unreadable: {exc}")
    if not stat.S_ISREG(receipt_info.st_mode):
        fail("download-receipt receipt is not a regular file")
    receipt = load_source_attested_receipt(library_dir, attachment["receipt_id"])
    if (
        receipt["model_id"] != attachment["model_id"]
        or receipt["snapshot_revision"] != attachment["snapshot_revision"]
        or receipt["selected_rank"] != attachment["selected_rank"]
        or receipt["source"]["inventory_digest"] != attachment["inventory_digest"]
        or receipt["observed_manifest"]["manifest_id"]
        != attachment["observed_manifest_id"]
    ):
        return _home_authority_result(
            state=HOME_AUTHORITY_NONE, reason=HOME_AUTHORITY_INCOMPATIBLE_RECEIPT
        )
    return _home_authority_result(state=HOME_AUTHORITY_ATTACHED, receipt=receipt)


def _unlink_store_document(
    store: pathlib.Path,
    name: str,
    *,
    label: str,
) -> bool:
    """Unlink one final store document through the directory fd and fsync it.

    Returns True when the name was removed. Missing is False. A non-regular
    or unreadable store fails instead of following a symlink.
    """
    if STORE_FINAL_NAME_RE.fullmatch(name) is None:
        fail(f"{label} detach name is invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        store_fd = os.open(store, flags)
    except OSError as exc:
        fail(f"{label} is unavailable: {exc}")
    try:
        try:
            info = os.stat(name, dir_fd=store_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            fail(f"{label} entry is unreadable: {exc}")
        if not stat.S_ISREG(info.st_mode):
            fail(f"{label} contains a non-regular final document")
        os.unlink(name, dir_fd=store_fd)
        os.fsync(store_fd)
        return True
    finally:
        os.close(store_fd)


def detach_source_attested_home_attachment(
    library_dir: str | pathlib.Path,
    *,
    model_id: str,
    snapshot_revision: str,
    selected_rank: int,
    node_id: str,
    durable_home_path: str,
) -> dict[str, Any]:
    _validate_hf_model_id(model_id, label="detach model_id")
    _validate_commit(snapshot_revision, label="detach snapshot_revision")
    selected_rank = _require_rank(selected_rank, label="detach selected_rank")
    node_id = _require_private_node_id(node_id)
    durable_home_path = _require_durable_home_path(durable_home_path)
    attachment = load_source_attested_home_attachment(
        library_dir,
        model_id=model_id,
        snapshot_revision=snapshot_revision,
    )
    result = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_ATTACHMENT_RESULT_KIND,
        "state": "absent",
        "receipt_id": None,
        "model_id": model_id,
        "snapshot_revision": snapshot_revision,
    }
    if attachment is None:
        return result
    if not _attachment_matches_target(
        attachment,
        model_id=model_id,
        snapshot_revision=snapshot_revision,
        node_id=node_id,
        durable_home_path=durable_home_path,
    ):
        return result
    store = source_attested_home_attachment_store(library_dir)
    name = f"{attachment['attachment_key']}.json"
    if _unlink_store_document(
        store, name, label="home-occupancy store"
    ):
        result["state"] = "detached"
        result["receipt_id"] = attachment["receipt_id"]
    return result


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


def occupy_source_attested_home(
    library_dir: str | pathlib.Path,
    *,
    receipt: dict[str, Any],
    observed_manifest: dict[str, Any],
    node_id: str,
    durable_home_path: str,
    directory_identity: dict[str, Any],
) -> dict[str, Any]:
    """Grant occupancy after a live full rehash against the immutable receipt.

    ``selected_rank`` on the receipt stays Hub-download provenance and is not
    a destination predicate. Silent reconstruct-from-bytes is refused because
    this function always rehashes first.
    """
    receipt = validate_source_attested_acquisition_receipt(receipt)
    verify_source_attested_home(
        receipt,
        observed_manifest,
        model_id=receipt["model_id"],
        snapshot_revision=receipt["snapshot_revision"],
    )
    durable_home_path = _require_durable_home_path(durable_home_path)
    if directory_identity.get("kind") == LIVE_DIRECTORY_IDENTITY_KIND:
        identity = validate_live_directory_identity(directory_identity)
    else:
        identity = validate_live_directory_identity(
            {
                "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
                "kind": LIVE_DIRECTORY_IDENTITY_KIND,
                "path": durable_home_path,
                "device": directory_identity.get("device"),
                "inode": directory_identity.get("inode"),
                "ctime_ns": directory_identity.get("ctime_ns"),
            }
        )
    if identity["path"] != durable_home_path:
        fail("occupy: live directory path differs from the durable home")
    return write_source_attested_home_attachment(
        library_dir,
        receipt=receipt,
        node_id=node_id,
        durable_home_path=identity["path"],
        directory_identity={
            "device": identity["device"],
            "inode": identity["inode"],
            "ctime_ns": identity["ctime_ns"],
        },
    )


def classify_catalog_occupancy(
    catalog: dict[str, Any],
    library_dir: str | pathlib.Path,
) -> dict[str, Any]:
    """Mark occupancy vs unbound-complete on scanned hub trees.

    Sealed/legacy identities with no receipt or attachment are unchanged.
    Download-receipt identities count only a matching occupancy attachment as
    a resolve-home. Extra complete hub trees are unbound-complete.
    """
    if not isinstance(catalog, dict) or not isinstance(catalog.get("models"), list):
        fail("catalog occupancy classification requires a catalog object")
    for entry in catalog["models"]:
        if not isinstance(entry, dict):
            fail("catalog occupancy classification requires model objects")
        _classify_entry_occupancy(entry, library_dir)
    return catalog


def _classify_entry_occupancy(
    entry: dict[str, Any],
    library_dir: str | pathlib.Path,
) -> None:
    model_id = entry.get("model_id")
    revision = entry.get("revision")
    homes = entry.get("homes")
    if not isinstance(homes, list):
        return
    if not isinstance(model_id, str) or not isinstance(revision, str):
        return
    if not revision or revision in {"missing", "unknown"}:
        return
    try:
        _validate_hf_model_id(model_id, label="catalog occupancy model_id")
        _validate_commit(revision, label="catalog occupancy revision")
    except SourceAttestedAcquisitionError:
        return
    try:
        receipts = list_source_attested_receipts_for_revision(
            library_dir, model_id=model_id, snapshot_revision=revision
        )
    except SourceAttestedAcquisitionError:
        return
    attachment = load_source_attested_home_attachment(
        library_dir, model_id=model_id, snapshot_revision=revision
    )
    if not receipts and attachment is None:
        return
    occupancy_count = 0
    for home in homes:
        if not isinstance(home, dict):
            fail("catalog occupancy classification requires home objects")
        if home.get("state") != "complete":
            home["occupancy"] = False
            home["home_class"] = str(home.get("state") or "partial")
            continue
        matched = bool(
            attachment is not None
            and home.get("node_id") == attachment["node_id"]
            and home.get("hub_path") == attachment["durable_home_path"]
        )
        home["occupancy"] = matched
        home["home_class"] = "occupancy" if matched else "unbound-complete"
        if matched:
            occupancy_count += 1
    if occupancy_count > 1:
        fail("catalog: multiple occupancy attachments resolved for one revision")


def validate_source_attested_home_verify_result(value: Any) -> dict[str, Any]:
    result = _require_fields(
        value, SOURCE_ATTESTED_VERIFY_FIELDS, label="download-receipt verify result"
    )
    _reject_prohibited_public_fields(result, label="download-receipt verify result")
    if result.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("download-receipt verify schema is unsupported")
    if result.get("kind") != SOURCE_ATTESTED_HOME_VERIFY_KIND:
        fail("download-receipt verify kind is invalid")
    if result.get("state") != "verified":
        fail("download-receipt verify state is invalid")
    if result.get("identity_class") not in ACQUISITION_IDENTITY_CLASSES:
        fail("download-receipt verify identity_class is unsupported")
    _validate_hex_id(result.get("receipt_id"), label="verify receipt_id")
    _validate_hf_model_id(result.get("model_id"), label="verify model_id")
    _validate_commit(result.get("snapshot_revision"), label="verify snapshot_revision")
    _require_positive_int(result.get("file_count"), label="verify file_count")
    _require_positive_int(result.get("bytes_hashed"), label="verify bytes_hashed")
    _public_json(result, label="download-receipt verify result")
    return result


def validate_source_attested_acquisition_result(value: Any) -> dict[str, Any]:
    result = _require_fields(
        value, SOURCE_ATTESTED_RESULT_FIELDS, label="download-receipt result"
    )
    _reject_prohibited_public_fields(result, label="download-receipt result")
    if result.get("schema_version") != SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION:
        fail("download-receipt result schema is unsupported")
    if result.get("kind") != SOURCE_ATTESTED_ACQUISITION_RESULT_KIND:
        fail("download-receipt result kind is invalid")
    if result.get("state") != "published":
        fail("download-receipt result state is invalid")
    if result.get("identity_class") not in ACQUISITION_IDENTITY_CLASSES:
        fail("download-receipt result identity_class is unsupported")
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
        fail("download-receipt result must not claim a catalog refresh")
    if result.get("staging_cleanup") not in {"removed", "incomplete"}:
        fail("download-receipt result staging_cleanup is invalid")
    _public_json(result, label="download-receipt result")
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
            fail("download-receipt rendering requires scripts/terminal_format.py")
    return TerminalWriter()


def render_source_attested_acquisition_plan(plan: dict[str, Any]) -> None:
    plan = validate_source_attested_acquisition_plan(plan)
    source = plan["source"]
    identity = plan["identity"]
    approval = plan["approval"]
    term = _terminal_writer()
    term.emit("Hugging Face download  PLAN")
    term.field("model", source["model_id"])
    term.field("profile", identity["profile"])
    term.field("selector", source["selector"])
    term.field("revision", source["snapshot_revision"])
    term.field("identity", "download receipt")
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
    term.emit(f"durable home  {label}")
    term.field("model", result["model_id"])
    term.field("revision", result["snapshot_revision"])
    term.field("home", f"rank {result['selected_rank']}")
    term.field("identity", "download receipt")
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
    term.emit("durable home  VERIFIED")
    term.field("model", result["model_id"])
    term.field("revision", result["snapshot_revision"])
    term.field("receipt", result["receipt_id"][:12])
    term.field("identity", "download receipt")
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


def cmd_geometry_ranks(args: argparse.Namespace) -> int:
    ranks = source_attested_geometry_ranks(
        list(range(args.confirmed_count)),
        serving_nodes=args.serving_nodes,
    )
    sys.stdout.write(" ".join(str(rank) for rank in ranks) + "\n")
    return 0


def cmd_unique_source(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.sources_dir)
    sources: list[dict[str, Any]] = []
    name = re.compile(r"^rank-[0-9]+\.json$")
    for path in sorted(root.iterdir()):
        if name.fullmatch(path.name) is None:
            continue
        item = _load_json_path(path, label=f"source {path.name}")
        if not isinstance(item, dict):
            fail(f"{path.name} is not a source object")
        sources.append(item)
    return _write_json(unique_source_attested_source(sources))


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
    if args.expected_seal:
        fail(
            "expected-seal acquisition identity is retired (ADR 0012); "
            "use home add --revision or a bound Model Serving Release"
        )
    identity = resolve_huggingface_v1_acquisition_identity(
        source=source,
        profile=args.profile,
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
        metadata_resolved_ranks=parse_metadata_resolved_ranks(
            args.metadata_resolved_ranks
        ),
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


def _receipt_from_authority(authority: dict[str, Any]) -> dict[str, Any] | None:
    if authority.get("state") == HOME_AUTHORITY_ATTACHED:
        return authority.get("receipt")
    return None


def cmd_attach_current_home(args: argparse.Namespace) -> int:
    receipt = validate_source_attested_acquisition_receipt(
        _read_arg_json(args.receipt, label="receipt")
    )
    publish_result = _read_arg_json(args.publish_result, label="publication result")
    attachment = attach_source_attested_home_from_publication(
        args.library_dir,
        receipt=receipt,
        node_id=args.node_id,
        publish_result=publish_result,
    )
    result = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_ATTACHMENT_RESULT_KIND,
        "state": "attached",
        "receipt_id": attachment["receipt_id"],
        "model_id": attachment["model_id"],
        "snapshot_revision": attachment["snapshot_revision"],
    }
    _public_json(result, label="current-home attachment result")
    return _write_json(result)


def cmd_occupy_current_home(args: argparse.Namespace) -> int:
    receipt = validate_source_attested_acquisition_receipt(
        _read_arg_json(args.receipt, label="receipt")
    )
    attachment = occupy_source_attested_home(
        args.library_dir,
        receipt=receipt,
        observed_manifest=_read_arg_json(args.manifest, label="observed manifest"),
        node_id=args.node_id,
        durable_home_path=args.durable_home_path,
        directory_identity=_read_arg_json(
            args.live_identity, label="live directory identity"
        ),
    )
    result = {
        "schema_version": SOURCE_ATTESTED_ACQUISITION_SCHEMA_VERSION,
        "kind": SOURCE_ATTESTED_HOME_ATTACHMENT_RESULT_KIND,
        "state": "attached",
        "receipt_id": attachment["receipt_id"],
        "model_id": attachment["model_id"],
        "snapshot_revision": attachment["snapshot_revision"],
    }
    _public_json(result, label="current-home occupancy result")
    return _write_json(result)


def cmd_classify_catalog_occupancy(args: argparse.Namespace) -> int:
    catalog_path = pathlib.Path(args.catalog)
    catalog = _load_json_value(catalog_path.read_bytes(), label="catalog")
    before = copy.deepcopy(catalog)
    classify_catalog_occupancy(catalog, args.library_dir)
    if catalog == before:
        if args.json:
            return _write_json(catalog)
        return 0
    raw = model_identity.pretty_json_bytes(catalog)
    tmp_path = catalog_path.with_name(
        f".{catalog_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        tmp_path.write_bytes(raw)
        os.replace(tmp_path, catalog_path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    if args.json:
        return _write_json(catalog)
    return 0


def cmd_resolve_attached_receipt(args: argparse.Namespace) -> int:
    authority = resolve_attached_source_attested_receipt(
        args.library_dir,
        model_id=args.model_id,
        snapshot_revision=args.revision,
        selected_rank=args.rank,
        node_id=args.node_id,
        durable_home_path=args.durable_home_path,
        live_identity=_read_arg_json(args.live_identity, label="live directory identity"),
    )
    receipt = _receipt_from_authority(authority)
    if receipt is None and not args.allow_missing:
        fail("download-receipt receipt has no current-home authority")
    return _write_json(receipt)


def cmd_has_current_home_attachment(args: argparse.Namespace) -> int:
    attachment = load_source_attested_home_attachment(
        args.library_dir,
        model_id=args.model_id,
        snapshot_revision=args.revision,
    )
    return _write_json(attachment is not None)


def cmd_show_current_home_attachment(args: argparse.Namespace) -> int:
    attachment = load_source_attested_home_attachment(
        args.library_dir,
        model_id=args.model_id,
        snapshot_revision=args.revision,
    )
    if attachment is None and not args.allow_missing:
        fail("current-home attachment not found")
    return _write_json(attachment)


def cmd_detach_current_home(args: argparse.Namespace) -> int:
    result = detach_source_attested_home_attachment(
        args.library_dir,
        model_id=args.model_id,
        snapshot_revision=args.revision,
        selected_rank=args.rank,
        node_id=args.node_id,
        durable_home_path=args.durable_home_path,
    )
    public = {
        "schema_version": result["schema_version"],
        "kind": result["kind"],
        "state": result["state"],
        "model_id": result["model_id"],
        "snapshot_revision": result["snapshot_revision"],
    }
    if result.get("receipt_id"):
        public["receipt_id"] = result["receipt_id"]
    _public_json(public, label="current-home detach result")
    return _write_json(public)


def cmd_find_receipt(args: argparse.Namespace) -> int:
    if args.receipt_id:
        receipt_path = source_attested_receipt_store(args.library_dir) / (
            f"{_validate_hex_id(args.receipt_id, label='receipt_id')}.json"
        )
        if not receipt_path.exists() and not receipt_path.is_symlink():
            if not args.allow_missing:
                fail("download-receipt receipt not found")
            receipt = None
        else:
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
            fail("download-receipt receipt not found")
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
        description="Download-receipt acquisition planning and receipt helpers"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    geometry = sub.add_parser("geometry-ranks")
    geometry.add_argument("--confirmed-count", type=int, required=True)
    geometry.add_argument("--serving-nodes", type=int, required=True)
    geometry.set_defaults(func=cmd_geometry_ranks)

    unique_source = sub.add_parser("unique-source")
    unique_source.add_argument("--sources-dir", required=True)
    unique_source.set_defaults(func=cmd_unique_source)

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
    plan.add_argument("--metadata-resolved-ranks", default="")
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

    attach_home = sub.add_parser("attach-current-home")
    attach_home.add_argument("--library-dir", required=True)
    attach_home.add_argument("--receipt", required=True)
    attach_home.add_argument("--publish-result", required=True)
    attach_home.add_argument("--node-id", required=True)
    attach_home.set_defaults(func=cmd_attach_current_home)

    occupy_home = sub.add_parser("occupy-current-home")
    occupy_home.add_argument("--library-dir", required=True)
    occupy_home.add_argument("--receipt", required=True)
    occupy_home.add_argument("--manifest", required=True)
    occupy_home.add_argument("--node-id", required=True)
    occupy_home.add_argument("--durable-home-path", required=True)
    occupy_home.add_argument("--live-identity", required=True)
    occupy_home.set_defaults(func=cmd_occupy_current_home)

    classify_occupancy = sub.add_parser("classify-catalog-occupancy")
    classify_occupancy.add_argument("--library-dir", required=True)
    classify_occupancy.add_argument("--catalog", required=True)
    classify_occupancy.add_argument("--json", action="store_true")
    classify_occupancy.set_defaults(func=cmd_classify_catalog_occupancy)

    resolve_attached = sub.add_parser("resolve-attached-receipt")
    resolve_attached.add_argument("--library-dir", required=True)
    resolve_attached.add_argument("--model-id", required=True)
    resolve_attached.add_argument("--revision", required=True)
    resolve_attached.add_argument("--rank", type=int, required=True)
    resolve_attached.add_argument("--node-id", required=True)
    resolve_attached.add_argument("--durable-home-path", required=True)
    resolve_attached.add_argument("--live-identity", required=True)
    resolve_attached.add_argument("--allow-missing", action="store_true")
    resolve_attached.set_defaults(func=cmd_resolve_attached_receipt)

    attachment_probe = sub.add_parser(
        "has-current-home-attachment",
        help="Check whether an exact model revision has a current-home attachment",
    )
    attachment_probe.add_argument("--library-dir", required=True)
    attachment_probe.add_argument("--model-id", required=True)
    attachment_probe.add_argument("--revision", required=True)
    attachment_probe.set_defaults(func=cmd_has_current_home_attachment)

    show_attachment = sub.add_parser("show-current-home-attachment")
    show_attachment.add_argument("--library-dir", required=True)
    show_attachment.add_argument("--model-id", required=True)
    show_attachment.add_argument("--revision", required=True)
    show_attachment.add_argument("--allow-missing", action="store_true")
    show_attachment.set_defaults(func=cmd_show_current_home_attachment)

    detach_home = sub.add_parser("detach-current-home")
    detach_home.add_argument("--library-dir", required=True)
    detach_home.add_argument("--model-id", required=True)
    detach_home.add_argument("--revision", required=True)
    detach_home.add_argument("--rank", type=int, required=True)
    detach_home.add_argument("--node-id", required=True)
    detach_home.add_argument("--durable-home-path", required=True)
    detach_home.set_defaults(func=cmd_detach_current_home)

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
        print(f"download-receipt: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
