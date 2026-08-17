#!/usr/bin/env python3
"""Source-attested Hugging Face v1 acquisition planning contracts.

These helpers own the PR 1 catalog/artifact planning schemas: a versioned
Hugging Face v1 source inventory, identity precedence against a reviewed
Model Serving Release or legacy expected seal, and a privacy-safe approval
summary. They do not download bytes, write receipts, expose a public
unsealed CLI, refresh the catalog, prepare a runtime view, launch, assign
status, or issue a Model Serving Release decision.

The sealed HOME_ACQUISITION_SCHEMA_VERSION=1 plan/result contracts remain
owned by model_library.py. This module is intentionally not imported from
that file so remote inspection can still stream model_library.py alone.
"""

from __future__ import annotations

import copy
import pathlib
import re
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
PROHIBITED_APPROVAL_FIELD_NAMES = {
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


def _reject_prohibited_approval_fields(approval: dict[str, Any]) -> None:
    lowered = {key.lower() for key in approval}
    blocked = sorted(name for name in PROHIBITED_APPROVAL_FIELD_NAMES if name in lowered)
    if blocked:
        fail(f"approval contains prohibited field(s): {blocked}")


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
