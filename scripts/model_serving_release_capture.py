#!/usr/bin/env python3
"""ADR 0004 evidence-capture candidate persistence.

Local, candidate-only workflow: validate supplied release and contract
objects, capture immutable run records and content-addressed evidence,
assemble compatible run records into an immutable evidence bundle, and
independently verify the resulting candidate.

This module is not an issuing or promotion authority. A successful
candidate is unreviewed, has privacy review pending, grants no serving
authorization, changes no catalog or profile status, and never writes the
tracked release registry. Schema ownership remains in the pure ADR 0004
modules.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import ctypes.util
import errno
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import (
        model_identity,
        model_serving_release,
        model_validation_evidence,
        terminal_format,
    )
except ModuleNotFoundError:
    import model_identity  # type: ignore[no-redef]
    import model_serving_release  # type: ignore[no-redef]
    import model_validation_evidence  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]


CAPTURE_SPEC_SCHEMA_VERSION = 1
CAPTURE_SPEC_KIND = "pulsar-model-serving-release-capture-spec"
CANDIDATE_SCHEMA_VERSION = 1
CANDIDATE_KIND = "pulsar-model-serving-release-capture-candidate"
OUTPUT_SCHEMA_VERSION = 1
DEFAULT_CAPTURE_ROOT = "experiments/model-serving-release-captures"
REGISTRY_RELATIVE = "models/model-serving-releases"

MANIFEST_NAME = "candidate.json"
RELEASE_NAME = "release.json"
CONTRACT_NAME = "contract.json"
BUNDLE_NAME = "evidence-bundle.json"
RUN_RECORDS_DIR = "run-records"
EVIDENCE_DIR = "evidence"

CANDIDATE_STATE = "unreviewed"
CANDIDATE_AUTHORITY = "none"
CANDIDATE_PRIVACY = "pending"

SPEC_TOP_FIELDS = {
    "schema_version",
    "kind",
    "release",
    "contract",
    "attempt",
    "preparation_provenance",
    "observed_environment",
    "commands",
    "criterion_observations",
    "evidence_sources",
    "review_source_keys",
}
RELEASE_SPEC_FIELDS = {
    "schema_version",
    "kind",
    "model_artifact_set",
    "serving_recipe",
    "runtime_image_identity",
    "supported_hardware_geometry",
}
RELEASE_SPEC_REQUIRED = {
    "model_artifact_set",
    "serving_recipe",
    "runtime_image_identity",
    "supported_hardware_geometry",
}
CONTRACT_SPEC_FIELDS = {
    "schema_version",
    "kind",
    "repository_invariants",
    "release_criteria",
}
CONTRACT_SPEC_REQUIRED = {"repository_invariants", "release_criteria"}
ATTEMPT_SPEC_FIELDS = {
    "attempt_id",
    "phase",
    "qualification_scope",
    "attempted_criterion_ids",
    "started_at",
    "ended_at",
    "completion",
}
PROVENANCE_SPEC_FIELDS = {
    "origin",
    "transfer",
    "subsystems",
    "runtime_sources",
    "verification",
    "qualification_barrier",
    "elapsed_seconds",
}
VERIFICATION_SPEC_FIELDS = {"status"}
OBSERVED_ENV_SPEC_FIELDS = {
    "image_digest",
    "server_boot_id",
    "launch_id",
    "cluster",
    "ranks",
}
COMMAND_SPEC_FIELDS = {
    "program",
    "arguments",
    "environment",
    "working_directory",
}
OBSERVATION_SPEC_FIELDS = {
    "criterion_id",
    "completion",
    "sample_size",
    "metrics",
    "evidence_source_keys",
    "contract_requirements",
    "reason",
}
CONTEXT_REQ_SPEC_FIELDS = {
    "completion",
    "minimum_tokens",
    "depths",
    "evidence_source_keys",
    "reason",
}
SOAK_REQ_SPEC_FIELDS = {
    "completion",
    "started_at",
    "ended_at",
    "duration_seconds",
    "concurrency",
    "request_errors",
    "evidence_source_keys",
    "reason",
}
EVIDENCE_SOURCE_COMMON_FIELDS = {
    "source_key",
    "class",
    "qualification_scope",
    "media_type",
}
EVIDENCE_SOURCE_PUBLISHABLE_FIELDS = EVIDENCE_SOURCE_COMMON_FIELDS | {
    "repository_path",
}
EVIDENCE_SOURCE_PROTECTED_FIELDS = EVIDENCE_SOURCE_COMMON_FIELDS | {
    "content_sha256",
}
EVIDENCE_CLASSES = {"publishable", "protected"}
MANIFEST_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "authority",
    "privacy_review",
    "promotion_authorized",
    "release_id",
    "contract_id",
    "run_record_ids",
    "bundle_id",
    "files",
    "candidate_id",
}
FORBIDDEN_SPEC_KEYS = {
    "authority",
    "base_status",
    "bundle_id",
    "candidate_id",
    "contract_id",
    "decision",
    "decision_id",
    "decisions",
    "effective_status",
    "exit_code",
    "exit_status",
    "model_artifact_set_id",
    "privacy_review",
    "program_version",
    "promotion_authorized",
    "release_id",
    "return_code",
    "returncode",
    "review",
    "review_reference",
    "reviewed_at",
    "reviewer",
    "run_record_id",
    "serving_authorization",
    "supported_hardware_geometry_id",
    "validation_status",
    "validator_output",
}
PROCESS_TRANSLATION_KEYS = {
    "adapter",
    "exit_code",
    "exit_status",
    "process_exit",
    "return_code",
    "returncode",
    "signal",
    "validator_output",
}
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_+.-])(/[^\s\"']+)")
SAFE_RELATIVE_FILE_RE = re.compile(
    r"^(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+$"
)

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
UNSAFE_PATH_COMPONENTS = {"", ".", ".."}
READ_STABILITY_HOOK = None
VERIFY_AFTER_SCAN_HOOK = None

PERSISTENCE_NOTES = (
    "This candidate is unreviewed and has no issuance authority.",
    "Privacy review is pending.",
    "This workflow does not launch a release.",
    "Capture does not write the tracked release registry.",
    "Capture does not change catalog or profile status.",
    "A successful candidate is not a reviewed decision.",
    "This workflow makes no physical DGX claim.",
)


class ModelServingReleaseCaptureError(ValueError):
    """A capture, assembly, or verification operation is unsafe or invalid."""


def fail(message: str) -> None:
    raise ModelServingReleaseCaptureError(message)


def _require_object(value: Any, fields: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    extra = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if extra or missing:
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def _require_closed(
    value: Any,
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    extra = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if extra or missing:
        fail(f"{label} fields differ (missing={missing}, extra={extra})")
    return value


def encode_json(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candidate_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "candidate_id"}


def candidate_id_for(manifest: dict[str, Any]) -> str:
    return model_identity.canonical_json_digest(candidate_identity(manifest))


def _require_safe_component(name: str, *, label: str) -> str:
    if name in UNSAFE_PATH_COMPONENTS or "/" in name or os.sep in name or "\x00" in name:
        fail(f"{label} has an unsafe path component")
    return name


def lexical_parts(path: Path, *, label: str) -> tuple[str, ...]:
    """Return non-root parts after rejecting empty, dot, and dot-dot names."""
    parts = path.parts[1:] if path.is_absolute() else path.parts
    return tuple(_require_safe_component(part, label=label) for part in parts)


def safe_absolute(path: Path, *, base: Path | None = None, label: str = "path") -> Path:
    """Return an absolute path without following or collapsing through '..'."""
    if not path.is_absolute():
        if base is None:
            fail(f"{label} must be absolute")
        path = base.joinpath(*lexical_parts(path, label=label))
    return Path("/").joinpath(*lexical_parts(path, label=label))


def path_is_under(path: Path, root: Path, *, allow_equal: bool = False) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return allow_equal or path != root


def default_capture_root(repo_root: Path) -> Path:
    return safe_absolute(
        Path("experiments") / "model-serving-release-captures",
        base=repo_root,
        label="capture root",
    )


def sanitize_error(message: str, *, repo_root: Path | None = None) -> str:
    """Keep human and JSON errors free of absolute and repository paths."""
    text = str(message)
    if repo_root is not None:
        raw = str(repo_root)
        if raw not in {"", ".", "/"}:
            text = text.replace(raw + os.sep, "")
            text = text.replace(raw, "<repository-root>")
    text = ABS_PATH_RE.sub("<path>", text)
    return text


def _reject_json_constant(value: str) -> None:
    fail(f"JSON contains non-standard constant {value}")


def _unique_object_pairs(pairs: list[tuple[Any, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def parse_strict_json(raw: bytes, *, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        fail(f"{label} is not valid UTF-8")
    try:
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_object_pairs,
            strict=True,
        )
    except json.JSONDecodeError:
        fail(f"{label} is malformed JSON")
    except ValueError as exc:
        fail(f"{label}: {exc}")


def _is_enoent(exc: OSError) -> bool:
    return exc.errno == errno.ENOENT


def _is_permission(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EPERM}


def _classify_os_error(exc: OSError, *, label: str) -> None:
    if _is_enoent(exc):
        fail(f"{label} is missing")
    if _is_permission(exc):
        fail(f"{label} is unreadable")
    if exc.errno in {errno.ELOOP, errno.EINVAL}:
        fail(f"{label} must not be a symlink")
    fail(f"{label} is unreadable")


def close_quietly(fd: int | None) -> None:
    if fd is None or fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def read_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def fsync_dir_fd(dir_fd: int) -> None:
    os.fsync(dir_fd)


def stat_fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _require_same_file(
    observed: os.stat_result,
    expected: os.stat_result,
    *,
    label: str,
) -> None:
    if stat_fingerprint(observed) != stat_fingerprint(expected):
        fail(f"{label} changed during read")


def _stat_at(dir_fd: int, name: str, *, label: str) -> os.stat_result:
    _require_safe_component(name, label=label)
    try:
        return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        _classify_os_error(exc, label=label)
        raise


def _open_at(
    dir_fd: int,
    name: str,
    *,
    flags: int,
    mode: int = 0o600,
    label: str,
) -> int:
    _require_safe_component(name, label=label)
    try:
        return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    except OSError as exc:
        _classify_os_error(exc, label=label)
        raise


def _file_read_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _dir_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _require_same_dir(
    opened: os.stat_result,
    preview: os.stat_result,
    *,
    label: str,
) -> None:
    if stat.S_ISLNK(opened.st_mode) or not stat.S_ISDIR(opened.st_mode):
        fail(f"{label} must be a directory")
    if _dir_identity(opened) != _dir_identity(preview):
        fail(f"{label} changed during open")


def _open_dir_at_matching(
    parent_fd: int,
    name: str,
    preview: os.stat_result,
    *,
    label: str,
) -> int:
    fd = _open_at(
        parent_fd,
        name,
        flags=os.O_RDONLY | os.O_DIRECTORY,
        label=label,
    )
    try:
        _require_same_dir(os.fstat(fd), preview, label=label)
    except Exception:
        close_quietly(fd)
        raise
    return fd


def _mode_of(fd: int) -> int:
    return stat.S_IMODE(os.fstat(fd).st_mode)


def require_fd_mode(fd: int, expected: int, *, label: str) -> None:
    observed = _mode_of(fd)
    if observed != expected:
        fail(f"{label} mode is not {expected:04o}")


def _stable_read_fd(
    fd: int,
    *,
    preview: os.stat_result,
    label: str,
    hook_key: str | None = None,
) -> bytes:
    opened = os.fstat(fd)
    if stat.S_ISLNK(opened.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(opened.st_mode):
        fail(f"{label} must be a regular file")
    _require_same_file(opened, preview, label=label)
    hook = READ_STABILITY_HOOK
    if hook is not None:
        hook(hook_key)
    data = read_fd(fd)
    after = os.fstat(fd)
    _require_same_file(after, preview, label=label)
    if after.st_size != len(data):
        fail(f"{label} changed during read")
    return data


def _reject_candidate_marker(dir_fd: int, *, label: str) -> None:
    try:
        info = os.stat(MANIFEST_NAME, dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        if _is_enoent(exc):
            return
        _classify_os_error(exc, label=f"{label} candidate marker")
        raise
    fail("refusing to write under an existing capture candidate")


def refuse_existing_candidate_ancestor(path: Path, *, label: str) -> None:
    """Fail if path or any existing prefix directory contains candidate.json."""
    absolute = safe_absolute(path, label=label)
    parts = lexical_parts(absolute, label=label)
    if not parts:
        return
    first = "/" + parts[0]
    try:
        preview = os.lstat(first)
    except OSError as exc:
        if _is_enoent(exc):
            return
        _classify_os_error(exc, label=label)
        raise
    if stat.S_ISLNK(preview.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISDIR(preview.st_mode):
        return
    try:
        current = os.open(first, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        _classify_os_error(exc, label=label)
        raise
    try:
        _require_same_dir(os.fstat(current), preview, label=label)
        _reject_candidate_marker(current, label=label)
        for part in parts[1:]:
            try:
                preview = os.stat(part, dir_fd=current, follow_symlinks=False)
            except OSError as exc:
                if _is_enoent(exc):
                    return
                _classify_os_error(exc, label=label)
                raise
            if stat.S_ISLNK(preview.st_mode):
                fail(f"{label} must not be a symlink")
            if not stat.S_ISDIR(preview.st_mode):
                return
            next_fd = _open_dir_at_matching(current, part, preview, label=label)
            close_quietly(current)
            current = next_fd
            _reject_candidate_marker(current, label=label)
    finally:
        close_quietly(current)


def open_directory_from_root(
    parts: tuple[str, ...],
    *,
    label: str,
    create: bool = False,
) -> int:
    """Walk absolute parts with no-follow opens. Optionally create missing directories."""
    if not parts:
        fail(f"{label} is too broad")
    first = "/" + parts[0]
    try:
        preview = os.lstat(first)
    except OSError as exc:
        if create and _is_enoent(exc):
            fail(f"{label} is missing")
        _classify_os_error(exc, label=label)
        raise
    if stat.S_ISLNK(preview.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISDIR(preview.st_mode):
        fail(f"{label} must be a directory")
    try:
        current = os.open(first, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        _classify_os_error(exc, label=label)
        raise
    try:
        _require_same_dir(os.fstat(current), preview, label=label)
        if create:
            _reject_candidate_marker(current, label=label)
        for part in parts[1:]:
            _require_safe_component(part, label=label)
            created = False
            try:
                preview = os.stat(part, dir_fd=current, follow_symlinks=False)
            except OSError as exc:
                if create and _is_enoent(exc):
                    try:
                        os.mkdir(part, 0o700, dir_fd=current)
                    except OSError as mkdir_exc:
                        _classify_os_error(mkdir_exc, label=label)
                    preview = os.stat(part, dir_fd=current, follow_symlinks=False)
                    created = True
                else:
                    _classify_os_error(exc, label=label)
                    raise
            if stat.S_ISLNK(preview.st_mode):
                fail(f"{label} must not be a symlink")
            if not stat.S_ISDIR(preview.st_mode):
                fail(f"{label} must be a directory")
            next_fd = _open_dir_at_matching(
                current, part, preview, label=label
            )
            close_quietly(current)
            current = next_fd
            if created:
                os.fchmod(current, 0o700)
            if create:
                _reject_candidate_marker(current, label=label)
        return current
    except Exception:
        close_quietly(current)
        raise


def open_file_from_root(
    parts: tuple[str, ...],
    *,
    label: str,
    hook_key: str | None = None,
) -> tuple[int, os.stat_result]:
    if not parts:
        fail(f"{label} must be a regular file")
    parent = open_directory_from_root(parts[:-1], label=label, create=False)
    try:
        preview = _stat_at(parent, parts[-1], label=label)
        if stat.S_ISLNK(preview.st_mode):
            fail(f"{label} must not be a symlink")
        if not stat.S_ISREG(preview.st_mode):
            fail(f"{label} must be a regular file")
        fd = _open_at(parent, parts[-1], flags=_file_read_flags(), label=label)
        try:
            opened = os.fstat(fd)
            _require_same_file(opened, preview, label=label)
        except Exception:
            close_quietly(fd)
            raise
        return fd, preview
    finally:
        close_quietly(parent)


def read_absolute_file(path: Path, *, label: str, hook_key: str | None = None) -> bytes:
    absolute = safe_absolute(path, label=label)
    fd, preview = open_file_from_root(
        lexical_parts(absolute, label=label),
        label=label,
        hook_key=hook_key,
    )
    try:
        return _stable_read_fd(fd, preview=preview, label=label, hook_key=hook_key)
    finally:
        close_quietly(fd)


def hash_regular_file(path: Path, *, label: str) -> tuple[str, bytes, os.stat_result]:
    absolute = safe_absolute(path, label=label)
    fd, preview = open_file_from_root(
        lexical_parts(absolute, label=label),
        label=label,
    )
    try:
        data = _stable_read_fd(fd, preview=preview, label=label, hook_key=str(absolute))
        after = os.fstat(fd)
    finally:
        close_quietly(fd)
    return sha256_bytes(data), data, after


def hash_repo_relative_file(repo_root: Path, relative: str, *, label: str) -> str:
    repo = safe_absolute(repo_root, label="repository root")
    rel = lexical_parts(PurePosixPath(relative), label=label)
    data = read_absolute_file(repo.joinpath(*rel), label=label, hook_key=relative)
    return sha256_bytes(data)


def _renameat2_noreplace(
    dir_fd: int,
    source_name: str,
    dest_name: str,
) -> None:
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        fail("exclusive publish is unavailable")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    if not hasattr(libc, "renameat2"):
        fail("exclusive publish requires renameat2")
    result = libc.renameat2(
        dir_fd,
        os.fsencode(source_name),
        dir_fd,
        os.fsencode(dest_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    err = ctypes.get_errno()
    if err in {errno.EEXIST, errno.ENOTEMPTY}:
        fail("destination already exists")
    fail("exclusive publish failed")


def _rmtree_at(parent_fd: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError:
            return
        return
    child = None
    try:
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        for entry in os.listdir(child):
            _rmtree_at(child, entry)
    except OSError:
        pass
    finally:
        close_quietly(child)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        pass


def _scan_forbidden_keys(value: Any, *, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_SPEC_KEYS or key in PROCESS_TRANSLATION_KEYS:
                fail(f"{label} contains forbidden field {key}")
            _scan_forbidden_keys(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _scan_forbidden_keys(item, label=f"{label}[{index}]")


def _relative_repo_path(value: Any, *, label: str) -> str:
    return model_validation_evidence._relative_repository_path(value, label=label)


def validate_publishable_repository_path(value: Any, *, label: str) -> str:
    relative = _relative_repo_path(value, label=label)
    parts = PurePosixPath(relative).parts
    if not parts or parts[0] != "results":
        fail(f"{label} must be a sanitized file under results/")
    if "raw" in parts:
        fail(f"{label} must not use a results raw subtree")
    return relative


def _safe_source_key(value: Any, *, label: str) -> str:
    return model_validation_evidence._safe_identifier(value, label=label)


def _screen_public(value: Any, *, label: str) -> None:
    model_serving_release._validate_public_json(value, label=label)


def _screen_capture_input(value: Any, *, label: str, context: tuple[str, ...] = ()) -> None:
    """Screen free-form capture values; keep secret-reference names and protected digests."""
    if isinstance(value, dict):
        kind = value.get("kind")
        for key, item in value.items():
            child_label = f"{label}.{key}"
            if key == "content_sha256":
                if not isinstance(item, str) or HEX64_RE.fullmatch(item) is None:
                    fail(f"{child_label} must be a SHA-256 digest")
                continue
            if (
                key == "name"
                and kind == "secret-reference"
                and "environment" in context
            ):
                if not isinstance(item, str) or not item:
                    fail(f"{child_label} is invalid")
                continue
            if (
                isinstance(key, str)
                and key.lower() in model_serving_release.PRIVATE_FIELD_NAMES
            ):
                fail(f"{label} contains private field {key!r}")
            if isinstance(key, str) and model_serving_release._is_credential_field_name(key):
                fail(f"{label} contains credential-bearing field {key!r}")
            _screen_capture_input(item, label=child_label, context=context + (key,))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _screen_capture_input(
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


def _load_review_source_keys(
    value: Any, *, source_keys: set[str]
) -> list[str]:
    if not isinstance(value, list):
        fail("spec.review_source_keys must be a list")
    keys: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in source_keys:
            fail(f"spec.review_source_keys[{index}] is unknown")
        keys.append(item)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail("spec.review_source_keys must be sorted and unique")
    return keys


def _observation_artifact_ids(observation: dict[str, Any]) -> set[str]:
    ids = set(observation.get("evidence_artifact_ids") or [])
    requirements = observation.get("contract_requirements") or {}
    for name in ("context", "soak"):
        block = requirements.get(name)
        if isinstance(block, dict):
            ids.update(block.get("evidence_artifact_ids") or [])
    return ids


def load_spec_file(path: Path) -> dict[str, Any]:
    data = read_absolute_file(path, label="capture spec", hook_key="capture-spec")
    payload = parse_strict_json(data, label="capture spec")
    if not isinstance(payload, dict):
        fail("capture spec must be a JSON object")
    return payload


def _load_release(value: Any) -> dict[str, Any]:
    release_spec = _require_closed(
        value,
        allowed=RELEASE_SPEC_FIELDS,
        required=RELEASE_SPEC_REQUIRED,
        label="spec.release",
    )
    if "schema_version" in release_spec:
        if release_spec["schema_version"] != (
            model_serving_release.MODEL_SERVING_RELEASE_SCHEMA_VERSION
        ):
            fail("spec.release schema_version is unsupported")
    if "kind" in release_spec:
        if release_spec["kind"] != model_serving_release.MODEL_SERVING_RELEASE_KIND:
            fail("spec.release kind is invalid")
    try:
        return model_serving_release.build_model_serving_release(
            model_artifact_set=release_spec["model_artifact_set"],
            serving_recipe=release_spec["serving_recipe"],
            runtime_image_identity=release_spec["runtime_image_identity"],
            supported_hardware_geometry=release_spec["supported_hardware_geometry"],
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(f"spec.release is invalid: {exc}")


def _load_contract(value: Any, *, release: dict[str, Any]) -> dict[str, Any]:
    contract_spec = _require_closed(
        value,
        allowed=CONTRACT_SPEC_FIELDS,
        required=CONTRACT_SPEC_REQUIRED,
        label="spec.contract",
    )
    if "schema_version" in contract_spec:
        if contract_spec["schema_version"] != (
            model_serving_release.VALIDATION_CONTRACT_SCHEMA_VERSION
        ):
            fail("spec.contract schema_version is unsupported")
    if "kind" in contract_spec:
        if contract_spec["kind"] != model_serving_release.VALIDATION_CONTRACT_KIND:
            fail("spec.contract kind is invalid")
    invariants = contract_spec["repository_invariants"]
    if invariants != model_serving_release.repository_validation_invariants():
        fail("spec.contract repository invariants differ from policy")
    criteria_block = _require_object(
        contract_spec["release_criteria"],
        {
            "criteria",
            "context_requirement",
            "soak_requirement",
            "relative_performance",
        },
        label="spec.contract.release_criteria",
    )
    try:
        return model_serving_release.build_validation_contract(
            release=release,
            criteria=criteria_block["criteria"],
            context_requirement=criteria_block["context_requirement"],
            soak_requirement=criteria_block["soak_requirement"],
            relative_performance=criteria_block["relative_performance"],
        )
    except model_serving_release.ModelServingReleaseError as exc:
        fail(f"spec.contract is invalid: {exc}")


def _load_evidence_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail("spec.evidence_sources must be a non-empty list")
    sources: list[dict[str, Any]] = []
    keys: list[str] = []
    publishable_paths: dict[str, str] = {}
    for index, item in enumerate(value):
        label = f"spec.evidence_sources[{index}]"
        if not isinstance(item, dict):
            fail(f"{label} must be an object")
        source_class = item.get("class")
        if source_class == "publishable":
            source = _require_object(
                item, EVIDENCE_SOURCE_PUBLISHABLE_FIELDS, label=label
            )
        elif source_class == "protected":
            source = _require_object(
                item, EVIDENCE_SOURCE_PROTECTED_FIELDS, label=label
            )
        else:
            fail(f"{label}.class is unsupported")
        source_key = _safe_source_key(
            source.get("source_key"), label=f"{label}.source_key"
        )
        _screen_public(source_key, label=f"{label}.source_key")
        if source.get("qualification_scope") not in (
            model_serving_release.QUALIFICATION_SCOPES
        ):
            fail(f"{label}.qualification_scope is unsupported")
        media_type = model_serving_release.validate_public_string_value(
            source.get("media_type"), label=f"{label}.media_type"
        )
        record = {
            "source_key": source_key,
            "class": source_class,
            "qualification_scope": source["qualification_scope"],
            "media_type": media_type,
        }
        if source_class == "publishable":
            repository_path = validate_publishable_repository_path(
                source.get("repository_path"),
                label=f"{label}.repository_path",
            )
            if repository_path in publishable_paths:
                fail(f"{label}.repository_path repeats an earlier evidence source")
            publishable_paths[repository_path] = source_key
            record["repository_path"] = repository_path
        else:
            digest = source.get("content_sha256")
            if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
                fail(f"{label}.content_sha256 must be a SHA-256 digest")
            record["content_sha256"] = digest
        keys.append(source_key)
        sources.append(record)
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        fail("spec.evidence_sources must be sorted by unique source_key")
    return sources


def _translate_requirement(
    value: Any,
    *,
    fields: set[str],
    label: str,
    source_ids: dict[str, str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    requirement = _require_object(value, fields, label=label)
    keys = requirement.get("evidence_source_keys")
    if not isinstance(keys, list) or not keys:
        fail(f"{label}.evidence_source_keys must be a non-empty list")
    artifact_ids: list[str] = []
    for index, key in enumerate(keys):
        if not isinstance(key, str) or key not in source_ids:
            fail(f"{label}.evidence_source_keys[{index}] is unknown")
        artifact_ids.append(source_ids[key])
    translated = {
        field: copy.deepcopy(requirement[field])
        for field in fields
        if field != "evidence_source_keys"
    }
    translated["evidence_artifact_ids"] = sorted(set(artifact_ids))
    return translated


def _translate_observation(
    value: Any,
    *,
    index: int,
    source_ids: dict[str, str],
    contract: dict[str, Any],
) -> dict[str, Any]:
    label = f"spec.criterion_observations[{index}]"
    observation = _require_object(value, OBSERVATION_SPEC_FIELDS, label=label)
    criterion_id = model_validation_evidence._safe_identifier(
        observation.get("criterion_id"), label=f"{label}.criterion_id"
    )
    criteria = {
        item["criterion_id"]: item
        for item in contract["release_criteria"]["criteria"]
    }
    criterion = criteria.get(criterion_id)
    if criterion is None:
        fail(f"{label}.criterion_id is unknown")
    if criterion["dimension"] == "provenance-security":
        fail("provenance/security criterion is review-derived, not captured")
    keys = observation.get("evidence_source_keys")
    if not isinstance(keys, list) or not keys:
        fail(f"{label}.evidence_source_keys must be a non-empty list")
    artifact_ids: list[str] = []
    for key_index, key in enumerate(keys):
        if not isinstance(key, str) or key not in source_ids:
            fail(f"{label}.evidence_source_keys[{key_index}] is unknown")
        artifact_ids.append(source_ids[key])
    requirements = _require_object(
        observation.get("contract_requirements"),
        {"context", "soak"},
        label=f"{label}.contract_requirements",
    )
    model_serving_release.validate_public_string_value(
        observation.get("reason"), label=f"{label}.reason"
    )
    translated = {
        "criterion_id": criterion_id,
        "benchmark_protocol_id": model_serving_release.benchmark_protocol_id(
            criterion
        ),
        "completion": observation["completion"],
        "sample_size": observation["sample_size"],
        "metrics": copy.deepcopy(observation["metrics"]),
        "evidence_artifact_ids": sorted(set(artifact_ids)),
        "contract_requirements": {
            "context": _translate_requirement(
                requirements.get("context"),
                fields=CONTEXT_REQ_SPEC_FIELDS,
                label=f"{label}.contract_requirements.context",
                source_ids=source_ids,
            ),
            "soak": _translate_requirement(
                requirements.get("soak"),
                fields=SOAK_REQ_SPEC_FIELDS,
                label=f"{label}.contract_requirements.soak",
                source_ids=source_ids,
            ),
        },
        "reason": observation["reason"],
    }
    return translated


def hash_allowlisted_program(repo_root: Path, program: str) -> str:
    if program not in model_validation_evidence.COMMAND_PROGRAM_OPERATIONS:
        fail("spec.commands program is not an allowed repository-owned executable")
    digest = hash_repo_relative_file(
        repo_root,
        program,
        label=f"allowlisted program {program}",
    )
    return "sha256:" + digest


def _bind_commands(value: Any, *, repo_root: Path) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        fail("spec.commands must be a non-empty list")
    commands: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        label = f"spec.commands[{index}]"
        command = _require_object(item, COMMAND_SPEC_FIELDS, label=label)
        if "version" in item:
            fail(f"{label} must not include a precomputed program version")
        program = command.get("program")
        if not isinstance(program, str):
            fail(f"{label}.program is invalid")
        bound = {
            "program": program,
            "version": hash_allowlisted_program(repo_root, program),
            "arguments": copy.deepcopy(command["arguments"]),
            "environment": copy.deepcopy(command["environment"]),
            "working_directory": command["working_directory"],
        }
        commands.append(bound)
    return commands


def _bind_provenance(
    value: Any, *, release: dict[str, Any]
) -> dict[str, Any]:
    provenance = _require_object(
        value, PROVENANCE_SPEC_FIELDS, label="spec.preparation_provenance"
    )
    verification = _require_object(
        provenance.get("verification"),
        VERIFICATION_SPEC_FIELDS,
        label="spec.preparation_provenance.verification",
    )
    artifact_set_id = model_identity.canonical_json_digest(
        release["model_artifact_set"]
    )
    bound = copy.deepcopy(provenance)
    bound["verification"] = {
        "status": verification["status"],
        "model_artifact_set_id": artifact_set_id,
    }
    return bound


def _bind_observed_environment(
    value: Any, *, release: dict[str, Any]
) -> dict[str, Any]:
    environment = _require_object(
        value, OBSERVED_ENV_SPEC_FIELDS, label="spec.observed_environment"
    )
    bound = copy.deepcopy(environment)
    bound["supported_hardware_geometry_id"] = (
        model_serving_release.supported_hardware_geometry_id(
            release["supported_hardware_geometry"]
        )
    )
    return bound


def _materialize_evidence(
    sources: list[dict[str, Any]],
    *,
    repo_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, bytes], dict[str, str]]:
    artifacts: list[dict[str, Any]] = []
    source_ids: dict[str, str] = {}
    publishable_bytes: dict[str, bytes] = {}
    location_digests: dict[str, str] = {}
    for source in sources:
        if source["class"] == "publishable":
            relative = source["repository_path"]
            data = read_absolute_file(
                safe_absolute(repo_root, label="repository root").joinpath(
                    *lexical_parts(PurePosixPath(relative), label="publishable evidence")
                ),
                label="publishable evidence source",
                hook_key=relative,
            )
            digest = sha256_bytes(data)
            if relative in location_digests and location_digests[relative] != digest:
                fail(
                    "publishable evidence location resolves to conflicting digests"
                )
            location_digests[relative] = digest
            location_kind = "repository-relative"
            location_value = relative
            publishable_bytes[digest] = data
        else:
            digest = source["content_sha256"]
            location_kind = "protected-content-addressed"
            location_value = "sha256:" + digest
        try:
            artifact = model_validation_evidence.build_evidence_artifact(
                location_kind=location_kind,
                location_value=location_value,
                content_sha256=digest,
                media_type=source["media_type"],
                qualification_scope=source["qualification_scope"],
                visibility=source["class"],
                privacy_review=CANDIDATE_PRIVACY,
            )
        except model_validation_evidence.ModelValidationEvidenceError as exc:
            fail(f"evidence source {source['source_key']} is invalid: {exc}")
        artifacts.append(artifact)
        source_ids[source["source_key"]] = artifact["artifact_id"]
    artifacts.sort(key=lambda item: item["artifact_id"])
    return artifacts, source_ids, publishable_bytes, location_digests


def _used_source_keys(observations: list[dict[str, Any]]) -> set[str]:
    used: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        keys = observation.get("evidence_source_keys")
        if isinstance(keys, list):
            used.update(key for key in keys if isinstance(key, str))
        requirements = observation.get("contract_requirements")
        if not isinstance(requirements, dict):
            continue
        for name in ("context", "soak"):
            block = requirements.get(name)
            if isinstance(block, dict):
                nested = block.get("evidence_source_keys")
                if isinstance(nested, list):
                    used.update(key for key in nested if isinstance(key, str))
    return used


def _walk_optional_registry_file(
    repo_root: Path,
    *,
    namespace: str,
    object_id: str,
    label: str,
) -> bytes | None:
    repo = safe_absolute(repo_root, label="repository root")
    parts = lexical_parts(repo, label="repository root") + tuple(
        lexical_parts(PurePosixPath(REGISTRY_RELATIVE) / namespace / f"{object_id}.json", label=label)
    )
    current = None
    if not parts:
        fail(f"tracked registry {label} is too broad")
    first = "/" + parts[0]
    try:
        preview = os.lstat(first)
    except OSError as exc:
        if _is_enoent(exc):
            return None
        _classify_os_error(exc, label=f"tracked registry {label}")
        raise
    if stat.S_ISLNK(preview.st_mode):
        fail(f"tracked registry {label} must not be a symlink")
    if not stat.S_ISDIR(preview.st_mode):
        fail(f"tracked registry {label} must be a directory")
    try:
        current = os.open(first, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        _require_same_dir(
            os.fstat(current), preview, label=f"tracked registry {label}"
        )
        remaining = parts[1:]
        for index, part in enumerate(remaining):
            is_last = index == len(remaining) - 1
            _require_safe_component(part, label=label)
            try:
                preview = os.stat(part, dir_fd=current, follow_symlinks=False)
            except OSError as exc:
                if _is_enoent(exc):
                    return None
                _classify_os_error(exc, label=f"tracked registry {label}")
                raise
            if stat.S_ISLNK(preview.st_mode):
                fail(f"tracked registry {label} must not be a symlink")
            if is_last:
                if not stat.S_ISREG(preview.st_mode):
                    fail(f"tracked registry {label} must be a regular file")
                fd = _open_at(
                    current,
                    part,
                    flags=_file_read_flags(),
                    label=f"tracked registry {label}",
                )
                try:
                    return _stable_read_fd(
                        fd,
                        preview=preview,
                        label=f"tracked registry {label}",
                    )
                finally:
                    close_quietly(fd)
            if not stat.S_ISDIR(preview.st_mode):
                fail(f"tracked registry {label} must be a directory")
            next_fd = _open_dir_at_matching(
                current,
                part,
                preview,
                label=f"tracked registry {label}",
            )
            close_quietly(current)
            current = next_fd
        return None
    except Exception:
        close_quietly(current)
        raise
    finally:
        close_quietly(current)


def check_registry_object(
    repo_root: Path,
    *,
    namespace: str,
    object_id: str,
    expected: dict[str, Any],
    label: str,
) -> None:
    raw = _walk_optional_registry_file(
        repo_root,
        namespace=namespace,
        object_id=object_id,
        label=label,
    )
    if raw is None:
        return
    loaded = parse_strict_json(raw, label=f"tracked registry {label}")
    if model_identity.canonical_json_digest(loaded) != (
        model_identity.canonical_json_digest(expected)
    ):
        fail(f"tracked registry {label} differs from the capture spec")


def check_registry_equality(
    repo_root: Path,
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
) -> None:
    check_registry_object(
        repo_root,
        namespace="descriptors",
        object_id=release["release_id"],
        expected=release,
        label="release",
    )
    check_registry_object(
        repo_root,
        namespace="contracts",
        object_id=contract["contract_id"],
        expected=contract,
        label="contract",
    )


def _protected_prefixes(repo_root: Path) -> tuple[Path, ...]:
    repo = safe_absolute(repo_root, label="repository root")
    return (
        repo / "models",
        repo / REGISTRY_RELATIVE,
        repo / ".git",
    )


def _refuse_protected_write(path: Path, *, repo_root: Path, output_root: Path | None = None) -> None:
    repo = safe_absolute(repo_root, label="repository root")
    dest = safe_absolute(path, label="output path")
    default_root = default_capture_root(repo)
    if dest in {Path("/"), repo}:
        fail("output directory is too broad")
    for forbidden in _protected_prefixes(repo):
        if dest == forbidden or path_is_under(dest, forbidden):
            fail("refusing to write under a protected repository directory")
    if dest == default_root:
        fail("refusing to write the output root itself")
    if output_root is not None and dest == output_root:
        fail("refusing to write the output root itself")
    if path_is_under(dest, repo, allow_equal=True) and not path_is_under(
        dest, default_root
    ):
        fail(
            "repository-local capture output must live under "
            f"{DEFAULT_CAPTURE_ROOT}/"
        )
    refuse_existing_candidate_ancestor(dest, label="output path")


def validate_output_root(path: Path, *, repo_root: Path) -> Path:
    repo = safe_absolute(repo_root, label="repository root")
    root = safe_absolute(path, base=repo if not path.is_absolute() else None, label="output directory")
    if root in {Path("/"), repo, * _protected_prefixes(repo)}:
        fail("output directory is too broad")
    if path_is_under(root, repo, allow_equal=True) and not (
        root == default_capture_root(repo) or path_is_under(root, default_capture_root(repo))
    ):
        fail(
            "repository-local capture output must live under "
            f"{DEFAULT_CAPTURE_ROOT}/"
        )
    refuse_existing_candidate_ancestor(root, label="output directory")
    return root


def validate_destination(dest: Path, *, repo_root: Path, output_root: Path) -> Path:
    repo = safe_absolute(repo_root, label="repository root")
    output = safe_absolute(output_root, label="output directory")
    normalized = safe_absolute(dest, label="destination")
    _refuse_protected_write(normalized, repo_root=repo, output_root=output)
    return normalized


def _write_tree_at(staging_fd: int, files: dict[str, bytes]) -> None:
    created_dirs: dict[str, int] = {"": staging_fd}
    try:
        for relative, data in sorted(files.items()):
            if (
                not SAFE_RELATIVE_FILE_RE.fullmatch(relative)
                or relative.startswith(".")
                or "/." in relative
            ):
                fail("candidate filename is unsafe")
            parts = lexical_parts(PurePosixPath(relative), label="candidate file")
            parent_key = ""
            for part in parts[:-1]:
                child_key = f"{parent_key}{part}/"
                if child_key not in created_dirs:
                    try:
                        os.mkdir(part, 0o700, dir_fd=created_dirs[parent_key])
                    except FileExistsError:
                        pass
                    child_fd = _open_at(
                        created_dirs[parent_key],
                        part,
                        flags=os.O_RDONLY | os.O_DIRECTORY,
                        label="staging subdirectory",
                    )
                    os.fchmod(child_fd, 0o700)
                    created_dirs[child_key] = child_fd
                parent_key = child_key
            name = parts[-1]
            file_fd = _open_at(
                created_dirs[parent_key],
                name,
                flags=os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode=0o600,
                label="staging file",
            )
            try:
                os.fchmod(file_fd, 0o600)
                write_fd(file_fd, data)
                os.fsync(file_fd)
            finally:
                close_quietly(file_fd)
        for key in sorted((item for item in created_dirs if item), reverse=True):
            fsync_dir_fd(created_dirs[key])
        fsync_dir_fd(staging_fd)
    finally:
        for key, handle in created_dirs.items():
            if key != "":
                close_quietly(handle)


def publish_candidate_tree(dest: Path, files: dict[str, bytes]) -> None:
    normalized = safe_absolute(dest, label="destination")
    dest_parts = lexical_parts(normalized, label="destination")
    if not dest_parts:
        fail("refusing to write the output root itself")
    refuse_existing_candidate_ancestor(normalized, label="destination")
    parent_fd = open_directory_from_root(
        dest_parts[:-1],
        label="destination parent",
        create=True,
    )
    dest_name = dest_parts[-1]
    staging_name = f".{dest_name}.staging.{os.getpid()}.{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
    try:
        try:
            _stat_at(parent_fd, dest_name, label="destination")
        except ModelServingReleaseCaptureError as exc:
            if "is missing" not in str(exc):
                raise
        else:
            fail("destination already exists")
        try:
            os.mkdir(staging_name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            fail("staging directory already exists")
        staging_fd = _open_at(
            parent_fd,
            staging_name,
            flags=os.O_RDONLY | os.O_DIRECTORY,
            label="staging directory",
        )
        try:
            os.fchmod(staging_fd, 0o700)
            _write_tree_at(staging_fd, files)
        finally:
            close_quietly(staging_fd)
        _renameat2_noreplace(parent_fd, staging_name, dest_name)
        fsync_dir_fd(parent_fd)
    except Exception:
        _rmtree_at(parent_fd, staging_name)
        raise
    finally:
        close_quietly(parent_fd)


def build_candidate_manifest(
    *,
    release_id: str,
    contract_id: str,
    run_record_ids: list[str],
    bundle_id: str,
    file_digests: dict[str, str],
) -> dict[str, Any]:
    files = {name: file_digests[name] for name in sorted(file_digests)}
    manifest = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "kind": CANDIDATE_KIND,
        "state": CANDIDATE_STATE,
        "authority": CANDIDATE_AUTHORITY,
        "privacy_review": CANDIDATE_PRIVACY,
        "promotion_authorized": False,
        "release_id": release_id,
        "contract_id": contract_id,
        "run_record_ids": list(run_record_ids),
        "bundle_id": bundle_id,
        "files": files,
    }
    manifest["candidate_id"] = candidate_id_for(manifest)
    return validate_candidate_manifest(manifest)


def validate_candidate_manifest(value: Any) -> dict[str, Any]:
    manifest = _require_object(value, MANIFEST_FIELDS, label="capture candidate")
    if manifest.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        fail("capture candidate schema_version is unsupported")
    if manifest.get("kind") != CANDIDATE_KIND:
        fail("capture candidate kind is invalid")
    if manifest.get("state") != CANDIDATE_STATE:
        fail("capture candidate state must be unreviewed")
    if manifest.get("authority") != CANDIDATE_AUTHORITY:
        fail("capture candidate cannot claim authority")
    if manifest.get("privacy_review") != CANDIDATE_PRIVACY:
        fail("capture candidate privacy review must remain pending")
    if manifest.get("promotion_authorized") is not False:
        fail("capture candidate promotion is not authorized")
    for field_name in ("release_id", "contract_id", "bundle_id", "candidate_id"):
        digest = manifest.get(field_name)
        if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
            fail(f"capture candidate {field_name} is invalid")
    run_ids = manifest.get("run_record_ids")
    if (
        not isinstance(run_ids, list)
        or not run_ids
        or run_ids != sorted(run_ids)
        or len(run_ids) != len(set(run_ids))
        or any(
            not isinstance(item, str) or HEX64_RE.fullmatch(item) is None
            for item in run_ids
        )
    ):
        fail("capture candidate run_record_ids must be sorted unique SHA-256 digests")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        fail("capture candidate files map is invalid")
    if list(files) != sorted(files):
        fail("capture candidate files map must be sorted")
    if MANIFEST_NAME in files:
        fail("capture candidate files map must not include candidate.json")
    for name, digest in files.items():
        if not isinstance(name, str) or not SAFE_RELATIVE_FILE_RE.fullmatch(name):
            fail("capture candidate file name is unsafe")
        if name.startswith(".") or "/." in name:
            fail("capture candidate cannot retain scratch files")
        if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
            fail("capture candidate file digest is invalid")
    required = {RELEASE_NAME, CONTRACT_NAME, BUNDLE_NAME}
    if not required.issubset(files):
        fail("capture candidate is missing required documents")
    expected_runs = {f"{RUN_RECORDS_DIR}/{item}.json" for item in run_ids}
    if not expected_runs.issubset(files):
        fail("capture candidate run-record file set is incomplete")
    if manifest["candidate_id"] != candidate_id_for(manifest):
        fail("capture candidate identity mismatch")
    return manifest


def candidate_payload_files(
    *,
    release: dict[str, Any],
    contract: dict[str, Any],
    run_records: list[dict[str, Any]],
    bundle: dict[str, Any],
    publishable_bytes: dict[str, bytes],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    files: dict[str, bytes] = {
        RELEASE_NAME: encode_json(release),
        CONTRACT_NAME: encode_json(contract),
        BUNDLE_NAME: encode_json(bundle),
    }
    for record in run_records:
        files[f"{RUN_RECORDS_DIR}/{record['run_record_id']}.json"] = encode_json(
            record
        )
    used_publishable: set[str] = set()
    for artifact in bundle["evidence_artifacts"]:
        if artifact["visibility"] != "publishable":
            continue
        digest = artifact["content"]["sha256"]
        if digest not in publishable_bytes:
            fail("publishable evidence bytes are missing from the candidate")
        files[f"{EVIDENCE_DIR}/{digest}"] = publishable_bytes[digest]
        used_publishable.add(digest)
    if set(publishable_bytes) != used_publishable:
        fail("candidate would retain unused publishable evidence bytes")
    digests = {name: sha256_bytes(data) for name, data in files.items()}
    manifest = build_candidate_manifest(
        release_id=release["release_id"],
        contract_id=contract["contract_id"],
        run_record_ids=[item["run_record_id"] for item in run_records],
        bundle_id=bundle["bundle_id"],
        file_digests=digests,
    )
    files[MANIFEST_NAME] = encode_json(manifest)
    return files, manifest


@dataclass
class BuiltCapture:
    release: dict[str, Any]
    contract: dict[str, Any]
    run_records: list[dict[str, Any]]
    bundle: dict[str, Any]
    manifest: dict[str, Any]
    files: dict[str, bytes]
    publishable_bytes: dict[str, bytes]
    layout: str
    model_artifact_set_id: str
    location_digests: dict[str, str] = field(default_factory=dict)


def build_capture_from_spec(
    spec: dict[str, Any],
    *,
    repo_root: Path,
) -> BuiltCapture:
    document = _require_object(spec, SPEC_TOP_FIELDS, label="capture spec")
    if document.get("schema_version") != CAPTURE_SPEC_SCHEMA_VERSION:
        fail("capture spec schema_version is unsupported")
    if document.get("kind") != CAPTURE_SPEC_KIND:
        fail("capture spec kind is invalid")
    _scan_forbidden_keys(document, label="capture spec")
    _screen_capture_input(document, label="capture spec")
    release = _load_release(document["release"])
    contract = _load_contract(document["contract"], release=release)
    check_registry_equality(repo_root, release=release, contract=contract)
    attempt = _require_object(
        document["attempt"], ATTEMPT_SPEC_FIELDS, label="spec.attempt"
    )
    sources = _load_evidence_sources(document["evidence_sources"])
    artifacts, source_ids, publishable_bytes, location_digests = _materialize_evidence(
        sources, repo_root=repo_root
    )
    raw_observations = document["criterion_observations"]
    if not isinstance(raw_observations, list):
        fail("spec.criterion_observations must be a list")
    observations = [
        _translate_observation(
            item,
            index=index,
            source_ids=source_ids,
            contract=contract,
        )
        for index, item in enumerate(raw_observations)
    ]
    used_keys = _used_source_keys(raw_observations)
    review_keys = _load_review_source_keys(
        document["review_source_keys"],
        source_keys={item["source_key"] for item in sources},
    )
    overlap = used_keys.intersection(review_keys)
    if overlap:
        fail("review_source_keys must not repeat run evidence sources")
    all_source_keys = {item["source_key"] for item in sources}
    if raw_observations:
        missing = all_source_keys - used_keys - set(review_keys)
        if missing:
            fail("every evidence source must be used by the run or listed in review_source_keys")
        extra_review = set(review_keys) - all_source_keys
        if extra_review:
            fail("spec.review_source_keys contains an unknown source")
        run_artifact_ids = sorted(
            {
                artifact_id
                for observation in observations
                for artifact_id in _observation_artifact_ids(observation)
            }
        )
    else:
        implicit_run = all_source_keys - set(review_keys)
        if not implicit_run:
            fail("pre-barrier capture requires at least one run evidence source")
        run_artifact_ids = sorted(source_ids[key] for key in implicit_run)
    review_ids: list[str] = []
    sources_by_key = {item["source_key"]: item for item in sources}
    for key in review_keys:
        source = sources_by_key[key]
        if source["qualification_scope"] != "release-promotion":
            fail("review evidence sources must use release-promotion scope")
        review_ids.append(source_ids[key])
    if not run_artifact_ids:
        fail("capture requires at least one evidence source")
    commands = _bind_commands(document["commands"], repo_root=repo_root)
    provenance = _bind_provenance(
        document["preparation_provenance"], release=release
    )
    environment = _bind_observed_environment(
        document["observed_environment"], release=release
    )
    try:
        record = model_validation_evidence.build_validation_run_record(
            release=release,
            contract=contract,
            attempt=copy.deepcopy(attempt),
            preparation_provenance=provenance,
            observed_environment=environment,
            commands=commands,
            criterion_observations=observations,
            evidence_artifacts=artifacts,
            evidence_artifact_ids=run_artifact_ids,
        )
        bundle_artifacts = [
            item
            for item in artifacts
            if item["artifact_id"] in set(run_artifact_ids) | set(review_ids)
        ]
        bundle = model_validation_evidence.build_validation_evidence_bundle(
            release=release,
            contract=contract,
            run_records=[record],
            evidence_artifacts=bundle_artifacts,
            review_evidence_artifact_ids=sorted(set(review_ids)),
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))
    files, manifest = candidate_payload_files(
        release=release,
        contract=contract,
        run_records=[record],
        bundle=bundle,
        publishable_bytes={
            digest: data
            for digest, data in publishable_bytes.items()
            if any(
                item["content"]["sha256"] == digest
                for item in bundle["evidence_artifacts"]
                if item["visibility"] == "publishable"
            )
        },
    )
    return BuiltCapture(
        release=release,
        contract=contract,
        run_records=[record],
        bundle=bundle,
        manifest=manifest,
        files=files,
        publishable_bytes=publishable_bytes,
        layout=(
            f"{release['release_id']}/runs/{record['run_record_id']}"
        ),
        model_artifact_set_id=record["preparation_provenance"]["verification"][
            "model_artifact_set_id"
        ],
        location_digests=location_digests,
    )


def output_root_from_args(args: argparse.Namespace, repo_root: Path) -> Path:
    repo = safe_absolute(repo_root, label="repository root")
    if args.output_dir:
        raw = Path(args.output_dir)
        return validate_output_root(raw, repo_root=repo)
    return validate_output_root(default_capture_root(repo), repo_root=repo)


def destination_for_layout(output_root: Path, layout: str, *, repo_root: Path) -> Path:
    dest = output_root.joinpath(*lexical_parts(PurePosixPath(layout), label="layout"))
    return validate_destination(dest, repo_root=repo_root, output_root=output_root)


def common_result(
    command: str,
    built: BuiltCapture,
    *,
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "ok": ok,
        "command": command,
        "kind": CANDIDATE_KIND,
        "state": CANDIDATE_STATE,
        "authority": CANDIDATE_AUTHORITY,
        "privacy_review": CANDIDATE_PRIVACY,
        "promotion_authorized": False,
        "release_id": built.release["release_id"],
        "contract_id": built.contract["contract_id"],
        "run_record_ids": [item["run_record_id"] for item in built.run_records],
        "bundle_id": built.bundle["bundle_id"],
        "candidate_id": built.manifest["candidate_id"],
        "model_artifact_set_id": built.model_artifact_set_id,
        "qualification_started": built.bundle["qualification_started"],
        "criterion_coverage": built.bundle["criterion_coverage"],
        "layout": built.layout,
        "file_count": len(built.files),
        "notes": list(PERSISTENCE_NOTES),
    }


def render_result(payload: dict[str, Any]) -> None:
    writer = terminal_format.TerminalWriter()
    writer.emit("ADR 0004 evidence-capture candidate")
    writer.blank()
    writer.field("Command", payload.get("command", ""))
    writer.field("State", payload.get("state", CANDIDATE_STATE))
    writer.field("Authority", payload.get("authority", CANDIDATE_AUTHORITY))
    writer.field("Privacy", payload.get("privacy_review", CANDIDATE_PRIVACY))
    writer.field(
        "Promotion",
        "not authorized"
        if not payload.get("promotion_authorized")
        else "authorized",
    )
    if payload.get("ok") is False:
        writer.field("Error", payload.get("error", "capture failed"))
        return
    writer.field("Release", payload.get("release_id", ""))
    writer.field("Contract", payload.get("contract_id", ""))
    run_ids = payload.get("run_record_ids") or []
    writer.field("Runs", ", ".join(run_ids) if run_ids else "none")
    writer.field("Bundle", payload.get("bundle_id", ""))
    writer.field("Candidate", payload.get("candidate_id", ""))
    if payload.get("layout"):
        writer.field("Layout", payload["layout"])
    if "qualification_started" in payload:
        writer.field(
            "Barrier",
            "passed" if payload["qualification_started"] else "not reached",
        )
    writer.blank()
    writer.emit("Notes")
    for note in payload.get("notes") or PERSISTENCE_NOTES:
        writer.emit(note, initial_indent="  ", subsequent_indent="  ")


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def error_payload(command: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "ok": False,
        "command": command,
        "error": message,
        "state": CANDIDATE_STATE,
        "authority": CANDIDATE_AUTHORITY,
        "privacy_review": CANDIDATE_PRIVACY,
        "promotion_authorized": False,
        "notes": list(PERSISTENCE_NOTES),
    }


def _read_child_file(
    dir_fd: int, name: str, *, label: str
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    preview = _stat_at(dir_fd, name, label=label)
    if stat.S_ISLNK(preview.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(preview.st_mode):
        fail(f"{label} must be a regular file")
    fd = _open_at(dir_fd, name, flags=_file_read_flags(), label=label)
    try:
        require_fd_mode(fd, 0o600, label=label)
        data = _stable_read_fd(fd, preview=preview, label=label, hook_key=name)
        after = os.fstat(fd)
        return data, stat_fingerprint(after)
    finally:
        close_quietly(fd)


@dataclass
class CandidateSnapshot:
    root_identity: tuple[int, int]
    root_mode: int
    root_names: tuple[str, ...]
    subdirs: dict[str, tuple[int, tuple[int, int], int, tuple[str, ...]]]
    files: dict[str, tuple[str, bytes, tuple[int, int, int, int, int, int]]]

    def close(self) -> None:
        for handle, _identity, _mode, _names in self.subdirs.values():
            close_quietly(handle)
        self.subdirs.clear()


def scan_candidate_tree(dest_fd: int) -> CandidateSnapshot:
    require_fd_mode(dest_fd, 0o700, label="candidate directory")
    root_stat = os.fstat(dest_fd)
    try:
        root_names = tuple(sorted(os.listdir(dest_fd)))
    except OSError:
        fail("candidate directory is unreadable")
    snapshot = CandidateSnapshot(
        root_identity=_dir_identity(root_stat),
        root_mode=stat.S_IMODE(root_stat.st_mode),
        root_names=root_names,
        subdirs={},
        files={},
    )
    try:
        for name in root_names:
            preview = _stat_at(dest_fd, name, label="candidate entry")
            if stat.S_ISLNK(preview.st_mode):
                fail("candidate must not contain a symlink")
            if name.startswith("."):
                fail("candidate cannot retain scratch files")
            if stat.S_ISDIR(preview.st_mode):
                if name not in {RUN_RECORDS_DIR, EVIDENCE_DIR}:
                    fail("candidate contains an unexpected directory")
                child_fd = _open_dir_at_matching(
                    dest_fd,
                    name,
                    preview,
                    label="candidate subdirectory",
                )
                require_fd_mode(child_fd, 0o700, label="candidate subdirectory")
                try:
                    child_names = tuple(sorted(os.listdir(child_fd)))
                except OSError:
                    fail("candidate directory is unreadable")
                if not child_names:
                    fail("candidate contains an unexpected directory")
                snapshot.subdirs[name] = (
                    child_fd,
                    _dir_identity(os.fstat(child_fd)),
                    0o700,
                    child_names,
                )
                for child_name in child_names:
                    if child_name.startswith("."):
                        fail("candidate cannot retain scratch files")
                    child_preview = _stat_at(
                        child_fd, child_name, label="candidate file"
                    )
                    if stat.S_ISLNK(child_preview.st_mode):
                        fail("candidate must not contain a symlink")
                    if stat.S_ISDIR(child_preview.st_mode):
                        fail("candidate contains an unexpected directory")
                    if not stat.S_ISREG(child_preview.st_mode):
                        fail("candidate file must be a regular file")
                    data, fingerprint = _read_child_file(
                        child_fd, child_name, label="candidate file"
                    )
                    snapshot.files[f"{name}/{child_name}"] = (
                        sha256_bytes(data),
                        data,
                        fingerprint,
                    )
                continue
            if not stat.S_ISREG(preview.st_mode):
                fail("candidate file must be a regular file")
            data, fingerprint = _read_child_file(
                dest_fd, name, label="candidate file"
            )
            snapshot.files[name] = (sha256_bytes(data), data, fingerprint)
    except Exception:
        snapshot.close()
        raise
    return snapshot


def _recheck_candidate_snapshot(
    dest_fd: int,
    snapshot: CandidateSnapshot,
    dest: Path,
) -> None:
    root_now = os.fstat(dest_fd)
    if _dir_identity(root_now) != snapshot.root_identity:
        fail("candidate directory identity changed")
    if stat.S_IMODE(root_now.st_mode) != 0o700:
        fail("candidate directory mode is not 0700")
    try:
        root_names = tuple(sorted(os.listdir(dest_fd)))
    except OSError:
        fail("candidate directory is unreadable")
    if root_names != snapshot.root_names:
        fail("candidate directory entries changed")
    for name, (handle, identity, _mode, names) in snapshot.subdirs.items():
        preview = _stat_at(dest_fd, name, label="candidate subdirectory")
        if stat.S_ISLNK(preview.st_mode):
            fail("candidate subdirectory must not be a symlink")
        if not stat.S_ISDIR(preview.st_mode):
            fail("candidate subdirectory must be a directory")
        if _dir_identity(preview) != identity:
            fail("candidate subdirectory identity changed")
        named_fd = _open_dir_at_matching(
            dest_fd,
            name,
            preview,
            label="candidate subdirectory",
        )
        try:
            if _dir_identity(os.fstat(named_fd)) != identity:
                fail("candidate subdirectory identity changed")
            now = os.fstat(handle)
            if _dir_identity(now) != identity:
                fail("candidate subdirectory identity changed")
            if stat.S_IMODE(now.st_mode) != 0o700:
                fail("candidate subdirectory mode is not 0700")
            try:
                current_names = tuple(sorted(os.listdir(named_fd)))
            except OSError:
                fail("candidate directory is unreadable")
            if current_names != names:
                fail("candidate directory entries changed")
        finally:
            close_quietly(named_fd)
    for relative, (_digest, _data, fingerprint) in snapshot.files.items():
        parts = lexical_parts(PurePosixPath(relative), label="candidate file")
        if len(parts) == 1:
            parent_fd = dest_fd
            name = parts[0]
        else:
            parent_fd = snapshot.subdirs[parts[0]][0]
            name = parts[1]
        preview = _stat_at(parent_fd, name, label="candidate file")
        if stat_fingerprint(preview) != fingerprint:
            fail("candidate file changed")
    fresh = open_directory_from_root(
        lexical_parts(dest, label="candidate directory"),
        label="candidate directory",
        create=False,
    )
    try:
        if _dir_identity(os.fstat(fresh)) != snapshot.root_identity:
            fail("candidate path no longer identifies the same directory")
    finally:
        close_quietly(fresh)


def _load_candidate_json_from_map(
    files: dict[str, tuple[Any, ...]], relative: str
) -> Any:
    if relative not in files:
        fail("candidate file is missing")
    return parse_strict_json(files[relative][1], label="candidate file")


def verify_program_versions(
    commands: list[dict[str, Any]], *, repo_root: Path
) -> None:
    for command in commands:
        program = command["program"]
        expected = command["version"]
        observed = hash_allowlisted_program(repo_root, program)
        if observed != expected:
            fail(f"allowlisted program {program} has drifted")


def verify_capture_review_policy(bundle: dict[str, Any]) -> None:
    artifacts = bundle.get("evidence_artifacts") or []
    for artifact in artifacts:
        if artifact.get("privacy_review") != CANDIDATE_PRIVACY:
            fail("capture candidate evidence privacy review must remain pending")
    by_id = {item["artifact_id"]: item for item in artifacts}
    for artifact_id in bundle.get("review_evidence_artifact_ids") or []:
        artifact = by_id.get(artifact_id)
        if artifact is None:
            fail("capture review evidence artifact is unavailable")
        if artifact.get("qualification_scope") != "release-promotion":
            fail("capture review evidence must use release-promotion scope")


def verify_capture_evidence_locations(artifacts: list[dict[str, Any]]) -> None:
    for artifact in artifacts:
        visibility = artifact.get("visibility")
        location = artifact.get("location") or {}
        if visibility == "publishable":
            validate_publishable_repository_path(
                location.get("value"),
                label="publishable evidence location",
            )
            if location.get("kind") != "repository-relative":
                fail("publishable evidence must use a repository-relative location")
            continue
        if visibility != "protected":
            fail("evidence artifact visibility is unsupported")
        if location.get("kind") != "protected-content-addressed":
            fail("protected evidence must use a content-addressed locator")
        expected = "sha256:" + artifact["content"]["sha256"]
        if location.get("value") != expected:
            fail("protected evidence locator differs from its content digest")


def verify_publishable_sources(
    artifacts: list[dict[str, Any]],
    *,
    repo_root: Path,
    copied: dict[str, str],
) -> None:
    verify_capture_evidence_locations(artifacts)
    for artifact in artifacts:
        if artifact["visibility"] != "publishable":
            continue
        location = artifact["location"]["value"]
        digest = artifact["content"]["sha256"]
        copied_name = f"{EVIDENCE_DIR}/{digest}"
        if copied.get(copied_name) != digest:
            fail("copied publishable evidence digest mismatch")
        checkout = hash_repo_relative_file(
            repo_root,
            location,
            label="publishable evidence source",
        )
        if checkout != digest:
            fail("publishable evidence source has drifted")


def load_verified_candidate(dest: Path, *, repo_root: Path) -> BuiltCapture:
    normalized = safe_absolute(dest, label="candidate directory")
    dest_fd = open_directory_from_root(
        lexical_parts(normalized, label="candidate directory"),
        label="candidate directory",
        create=False,
    )
    snapshot: CandidateSnapshot | None = None
    try:
        snapshot = scan_candidate_tree(dest_fd)
        hook = VERIFY_AFTER_SCAN_HOOK
        if hook is not None:
            hook()
        file_map = snapshot.files
        observed = {name: digest for name, (digest, _data, _fp) in file_map.items()}
        if MANIFEST_NAME not in observed:
            fail("candidate is missing candidate.json")
        manifest = validate_candidate_manifest(
            _load_candidate_json_from_map(file_map, MANIFEST_NAME)
        )
        expected = dict(manifest["files"])
        expected_with_manifest = dict(expected)
        expected_with_manifest[MANIFEST_NAME] = observed[MANIFEST_NAME]
        if set(observed) != set(expected_with_manifest):
            missing = sorted(set(expected_with_manifest) - set(observed))
            extra = sorted(set(observed) - set(expected_with_manifest))
            fail(
                "candidate file set differs "
                f"(missing={missing}, extra={extra})"
            )
        for name, digest in expected.items():
            if observed[name] != digest:
                fail("candidate file digest mismatch")
        release = model_serving_release.validate_model_serving_release(
            _load_candidate_json_from_map(file_map, RELEASE_NAME)
        )
        contract = model_serving_release.validate_validation_contract(
            _load_candidate_json_from_map(file_map, CONTRACT_NAME),
            expected_release=release,
        )
        if release["release_id"] != manifest["release_id"]:
            fail("candidate release cross-link mismatch")
        if contract["contract_id"] != manifest["contract_id"]:
            fail("candidate contract cross-link mismatch")
        check_registry_equality(repo_root, release=release, contract=contract)
        records: list[dict[str, Any]] = []
        for run_id in manifest["run_record_ids"]:
            records.append(
                _load_candidate_json_from_map(
                    file_map, f"{RUN_RECORDS_DIR}/{run_id}.json"
                )
            )
        bundle = _load_candidate_json_from_map(file_map, BUNDLE_NAME)
        try:
            records = [
                model_validation_evidence.validate_validation_run_record(
                    record,
                    release=release,
                    contract=contract,
                    evidence_artifacts=bundle.get("evidence_artifacts") or [],
                )
                for record in records
            ]
            bundle = model_validation_evidence.validate_validation_evidence_bundle(
                bundle,
                release=release,
                contract=contract,
                run_records=records,
            )
        except model_validation_evidence.ModelValidationEvidenceError as exc:
            fail(str(exc))
        verify_capture_review_policy(bundle)
        if bundle["bundle_id"] != manifest["bundle_id"]:
            fail("candidate bundle cross-link mismatch")
        if [item["run_record_id"] for item in records] != manifest["run_record_ids"]:
            fail("candidate run-record set mismatch")
        for record in records:
            verify_program_versions(record["commands"], repo_root=repo_root)
        verify_publishable_sources(
            bundle["evidence_artifacts"],
            repo_root=repo_root,
            copied=observed,
        )
        publishable_bytes: dict[str, bytes] = {}
        for artifact in bundle["evidence_artifacts"]:
            if artifact["visibility"] != "publishable":
                continue
            digest = artifact["content"]["sha256"]
            copied_name = f"{EVIDENCE_DIR}/{digest}"
            data = file_map[copied_name][1]
            if sha256_bytes(data) != digest:
                fail("copied publishable evidence digest mismatch")
            publishable_bytes[digest] = data
        rebuilt_files, rebuilt_manifest = candidate_payload_files(
            release=release,
            contract=contract,
            run_records=records,
            bundle=bundle,
            publishable_bytes=publishable_bytes,
        )
        if rebuilt_manifest["candidate_id"] != manifest["candidate_id"]:
            fail("candidate identity does not match reconstructed documents")
        if rebuilt_files.keys() != set(observed):
            fail("reconstructed candidate file set differs")
        _recheck_candidate_snapshot(dest_fd, snapshot, normalized)
        return BuiltCapture(
            release=release,
            contract=contract,
            run_records=records,
            bundle=bundle,
            manifest=manifest,
            files=rebuilt_files,
            publishable_bytes=publishable_bytes,
            layout="",
            model_artifact_set_id=records[0]["preparation_provenance"]["verification"][
                "model_artifact_set_id"
            ]
            if records
            else "",
        )
    finally:
        if snapshot is not None:
            snapshot.close()
        close_quietly(dest_fd)


def _merge_unique_objects(
    items: list[dict[str, Any]],
    *,
    id_field: str,
    label: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        object_id = item[id_field]
        existing = merged.get(object_id)
        if existing is None:
            merged[object_id] = item
            continue
        if model_identity.canonical_json_digest(existing) != (
            model_identity.canonical_json_digest(item)
        ):
            fail(f"{label} {object_id} is not byte/canonical equal across candidates")
    return [merged[key] for key in sorted(merged)]


def assemble_built_candidates(candidates: list[BuiltCapture]) -> BuiltCapture:
    if not candidates:
        fail("assemble-bundle requires at least one verified candidate")
    first = candidates[0]
    for item in candidates[1:]:
        if item.release["release_id"] != first.release["release_id"]:
            fail("assembled candidates must share one release")
        if item.contract["contract_id"] != first.contract["contract_id"]:
            fail("assembled candidates must share one contract")
        if model_identity.canonical_json_digest(item.release) != (
            model_identity.canonical_json_digest(first.release)
        ):
            fail("assembled release objects are not canonically equal")
        if model_identity.canonical_json_digest(item.contract) != (
            model_identity.canonical_json_digest(first.contract)
        ):
            fail("assembled contract objects are not canonically equal")
    records = _merge_unique_objects(
        [record for item in candidates for record in item.run_records],
        id_field="run_record_id",
        label="run record",
    )
    attempt_ids = [record["attempt"]["attempt_id"] for record in records]
    if len(attempt_ids) != len(set(attempt_ids)):
        fail("assembled candidates must have unique attempt IDs")
    artifacts = _merge_unique_objects(
        [
            artifact
            for item in candidates
            for artifact in item.bundle["evidence_artifacts"]
        ],
        id_field="artifact_id",
        label="evidence artifact",
    )
    location_digests: dict[str, str] = {}
    for artifact in artifacts:
        if artifact["visibility"] != "publishable":
            continue
        location = artifact["location"]["value"]
        digest = artifact["content"]["sha256"]
        previous = location_digests.get(location)
        if previous is not None and previous != digest:
            fail("publishable evidence location resolves to conflicting digests")
        location_digests[location] = digest
    review_ids = sorted(
        {
            artifact_id
            for item in candidates
            for artifact_id in item.bundle["review_evidence_artifact_ids"]
        }
    )
    try:
        bundle = model_validation_evidence.build_validation_evidence_bundle(
            release=first.release,
            contract=first.contract,
            run_records=records,
            evidence_artifacts=artifacts,
            review_evidence_artifact_ids=review_ids,
        )
    except model_validation_evidence.ModelValidationEvidenceError as exc:
        fail(str(exc))
    publishable_bytes: dict[str, bytes] = {}
    for item in candidates:
        for digest, data in item.publishable_bytes.items():
            previous = publishable_bytes.get(digest)
            if previous is not None and previous != data:
                fail("publishable evidence digest is not byte-equal across candidates")
            publishable_bytes[digest] = data
    files, manifest = candidate_payload_files(
        release=first.release,
        contract=first.contract,
        run_records=records,
        bundle=bundle,
        publishable_bytes={
            digest: data
            for digest, data in publishable_bytes.items()
            if any(
                artifact["content"]["sha256"] == digest
                for artifact in bundle["evidence_artifacts"]
                if artifact["visibility"] == "publishable"
            )
        },
    )
    return BuiltCapture(
        release=first.release,
        contract=first.contract,
        run_records=records,
        bundle=bundle,
        manifest=manifest,
        files=files,
        publishable_bytes=publishable_bytes,
        layout=f"{first.release['release_id']}/bundles/{bundle['bundle_id']}",
        model_artifact_set_id=first.model_artifact_set_id,
        location_digests=location_digests,
    )


def load_spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    repo = safe_absolute(Path(args.repo_root), label="repository root")
    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = safe_absolute(spec_path, base=repo, label="capture spec")
    else:
        spec_path = safe_absolute(spec_path, label="capture spec")
    return load_spec_file(spec_path)


HELP_LINES = (
    "Capture unreviewed ADR 0004 evidence-capture candidates",
    "",
    "Usage:",
    "scripts/model-serving-release-capture.sh plan --spec SPEC [--json]",
    "scripts/model-serving-release-capture.sh capture-run --spec SPEC [--output-dir DIR] [--json]",
    "scripts/model-serving-release-capture.sh assemble-bundle --candidate-dir DIR [--candidate-dir DIR ...] [--output-dir DIR] [--json]",
    "scripts/model-serving-release-capture.sh verify-candidate --candidate-dir DIR [--json]",
    "",
    "Candidate safety:",
    "Output is unreviewed and has no validation authority.",
    "Privacy review remains pending. This tool does not launch a model.",
    "Default output is gitignored experiments/model-serving-release-captures/.",
    "This tool never writes models/, the tracked release registry, a profile, or catalog status.",
    "Existing candidates are never overwritten.",
)


def cmd_help(_args: argparse.Namespace) -> int:
    writer = terminal_format.TerminalWriter()
    for line in HELP_LINES:
        if line == "":
            writer.blank()
        else:
            writer.emit(line)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root)
    built = build_capture_from_spec(load_spec_from_args(args), repo_root=repo_root)
    payload = common_result("plan", built)
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def cmd_capture_run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root)
    built = build_capture_from_spec(load_spec_from_args(args), repo_root=repo_root)
    output_root = output_root_from_args(args, repo_root)
    dest = destination_for_layout(output_root, built.layout, repo_root=repo_root)
    publish_candidate_tree(dest, built.files)
    verified = load_verified_candidate(dest, repo_root=repo_root)
    verified.layout = built.layout
    payload = common_result("capture-run", verified)
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def cmd_assemble_bundle(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root)
    if not args.candidate_dir:
        fail("assemble-bundle requires at least one --candidate-dir")
    loaded: list[BuiltCapture] = []
    repo = safe_absolute(repo_root, label="repository root")
    for raw in args.candidate_dir:
        path = Path(raw)
        if not path.is_absolute():
            path = safe_absolute(path, base=repo, label="candidate directory")
        else:
            path = safe_absolute(path, label="candidate directory")
        loaded.append(load_verified_candidate(path, repo_root=repo))
    built = assemble_built_candidates(loaded)
    output_root = output_root_from_args(args, repo_root)
    dest = destination_for_layout(output_root, built.layout, repo_root=repo_root)
    publish_candidate_tree(dest, built.files)
    verified = load_verified_candidate(dest, repo_root=repo_root)
    verified.layout = built.layout
    payload = common_result("assemble-bundle", verified)
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def cmd_verify_candidate(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root)
    repo = safe_absolute(repo_root, label="repository root")
    path = Path(args.candidate_dir)
    if not path.is_absolute():
        path = safe_absolute(path, base=repo, label="candidate directory")
    else:
        path = safe_absolute(path, label="candidate directory")
    built = load_verified_candidate(path, repo_root=repo)
    payload = common_result("verify-candidate", built)
    if args.json:
        emit_json(payload)
    else:
        render_result(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture and verify unreviewed ADR 0004 evidence-capture candidates"
        )
    )
    parser.add_argument("--repo-root", required=True, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    help_cmd = subparsers.add_parser("help", help="Show capture command help")
    help_cmd.set_defaults(func=cmd_help)

    plan = subparsers.add_parser("plan", help="Derive IDs without writing")
    plan.add_argument("--spec", required=True)
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(func=cmd_plan)

    capture = subparsers.add_parser(
        "capture-run", help="Capture one immutable run candidate"
    )
    capture.add_argument("--spec", required=True)
    capture.add_argument("--output-dir")
    capture.add_argument("--json", action="store_true")
    capture.set_defaults(func=cmd_capture_run)

    assemble = subparsers.add_parser(
        "assemble-bundle",
        help="Assemble independently verified candidates into one bundle",
    )
    assemble.add_argument("--candidate-dir", action="append", default=[])
    assemble.add_argument("--output-dir")
    assemble.add_argument("--json", action="store_true")
    assemble.set_defaults(func=cmd_assemble_bundle)

    verify = subparsers.add_parser(
        "verify-candidate", help="Independently verify one candidate"
    )
    verify.add_argument("--candidate-dir", required=True)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify_candidate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command
    repo_root = Path(args.repo_root)
    try:
        return int(args.func(args))
    except (
        ModelServingReleaseCaptureError,
        model_serving_release.ModelServingReleaseError,
        model_validation_evidence.ModelValidationEvidenceError,
        model_identity.ModelIdentityError,
        OSError,
    ) as exc:
        message = sanitize_error(str(exc), repo_root=repo_root)
        payload = error_payload(command, message)
        if getattr(args, "json", False):
            emit_json(payload)
        else:
            print(
                f"model-serving-release-capture: ERROR: {message}",
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
