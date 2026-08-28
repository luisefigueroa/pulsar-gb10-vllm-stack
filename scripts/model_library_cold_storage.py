#!/usr/bin/env python3
"""Explicit-only cold recovery storage configuration (ADR 0015).

This module owns preferred-key ``.env`` parse/write, persisted/effective
state, configuration plans, archive-job projection, and closed JSON
schemas. It is not a receipt, occupancy, or archive authority. Live
recovery configuration is ``PULSAR_COLD_ROOT`` only: process, then
persisted repository ``.env``, then ``not-configured``. There is no
``MODELS_NFS`` alias and no implicit ``/mnt/Models`` fallback.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import pathlib
import re
import secrets
import stat
import sys
from typing import Any, Callable, Mapping

try:
    from scripts import immutable_descriptor_dir as descriptor_dir
    from scripts import model_identity
    from scripts import model_library
    from scripts import model_library_cold_archive as cold_archive
    from scripts import model_library_receipt as source_attested
    from scripts import terminal_format
except ModuleNotFoundError:
    import immutable_descriptor_dir as descriptor_dir  # type: ignore[no-redef]
    import model_identity  # type: ignore[no-redef]
    import model_library  # type: ignore[no-redef]
    import model_library_cold_archive as cold_archive  # type: ignore[no-redef]
    import model_library_receipt as source_attested  # type: ignore[no-redef]
    import terminal_format  # type: ignore[no-redef]


SCHEMA_VERSION = 1
STATUS_KIND = "pulsar-model-library-cold-storage-status"
PLAN_KIND = "pulsar-model-library-cold-storage-plan"
JOBS_KIND = "pulsar-model-library-cold-storage-archive-jobs"
RETRY_KIND = "pulsar-model-library-cold-storage-retry-eligibility"
MUTATION_KIND = "pulsar-model-library-cold-storage-mutation-result"

PREFERRED_KEY = "PULSAR_COLD_ROOT"
TEST_DOTENV_ENV = "PULSAR_COLD_STORAGE_TEST_DOTENV"
SELFTEST_ENV = "PULSAR_SELFTEST"
AUTHORITY_ASSERTION = (
    "Pulsar can verify path safety and recovery-set integrity. You assert "
    "that this storage location meets your recovery and failure-domain policy."
)

STATES = (
    "not-configured",
    "disabled",
    "configured-available",
    "configured-unavailable",
    "environment-override",
)
ASSIGNMENT_STATES = ("absent", "empty", "path")
SOURCES = ("absent", "process", "dotenv")
PLAN_ACTIONS = ("set-new", "keep", "change-blocked")
RETRYABLE_JOB_STATES = frozenset({"pending", "failed", "unavailable"})
RECEIPT_ID_RE = re.compile(r"^[0-9a-f]{64}$")
JOB_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
JOB_TEMP_RE = re.compile(r"^\.[0-9a-f]{64}\.json\.")
REPLICA_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
ARCHIVE_DIR_RE = re.compile(r"^[0-9a-f]{64}$")
REPLICA_TEMP_RE = re.compile(
    r"^\.[0-9a-f]{64}\.json\.[0-9]+\.[0-9a-f]{16}\.tmp$"
)
ARCHIVE_STAGING_RE = re.compile(r"^\.[0-9a-f]{64}\.staging$")
UNSAFE_PATH_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
STATUS_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "persisted",
    "effective",
    "path",
    "path_health",
    "recovery",
    "archive_jobs",
    "findings",
    "authority_assertion",
    "exit_code",
}
PLAN_FIELDS = {
    "schema_version",
    "kind",
    "action",
    "requested",
    "persisted",
    "effective",
    "path_health",
    "affected",
    "findings",
    "authority_assertion",
    "plan_id",
    "exit_code",
}
ASSIGNMENT_FIELDS = {"state", "source", "value"}
MUTATION_FIELDS = {
    "schema_version",
    "kind",
    "action",
    "plan",
    "status",
    "exit_code",
}
WRITE_LOCK_HOOK: Callable[[pathlib.Path], None] | None = None


class ColdStorageError(ValueError):
    """Configuration contract or mutation failure."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def fail(message: str, *, exit_code: int = 2) -> None:
    raise ColdStorageError(message, exit_code=exit_code)


def repo_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


def library_dir(explicit: str | pathlib.Path | None = None) -> pathlib.Path:
    if explicit is not None and str(explicit).strip():
        return pathlib.Path(explicit)
    env = os.environ.get("MODEL_LIBRARY_DIR", "").strip()
    if env:
        return pathlib.Path(env)
    return repo_dir() / ".model-library"


def dotenv_path(*, repo: pathlib.Path | None = None) -> pathlib.Path:
    root = repo if repo is not None else repo_dir()
    override = os.environ.get(TEST_DOTENV_ENV)
    selftest = os.environ.get(SELFTEST_ENV) == "1"
    if override is not None:
        if not selftest:
            fail(
                f"{TEST_DOTENV_ENV} is only valid when {SELFTEST_ENV}=1",
                exit_code=2,
            )
        if override == "":
            fail(f"{TEST_DOTENV_ENV} must be an absolute file path", exit_code=2)
        path = pathlib.Path(override)
        if not path.is_absolute() or _has_dot_dot(path):
            fail(f"{TEST_DOTENV_ENV} must be an absolute file path", exit_code=2)
        return path
    return root / ".env"


def _has_dot_dot(path: pathlib.Path) -> bool:
    return any(part == ".." for part in path.parts)


def process_assignment() -> dict[str, Any]:
    if PREFERRED_KEY not in os.environ:
        return {"state": "absent", "source": "absent", "value": None}
    raw = os.environ[PREFERRED_KEY]
    if raw == "":
        return {"state": "empty", "source": "process", "value": ""}
    return {"state": "path", "source": "process", "value": raw}


def _lock_path(path: pathlib.Path) -> pathlib.Path:
    prefix = path.name if path.name.startswith(".") else f".{path.name}"
    return path.parent / f"{prefix}.pulsar-cold-storage.lock"


def _open_nofollow(
    path: pathlib.Path,
    flags: int,
    *,
    mode: int = 0o600,
    label: str,
) -> int:
    try:
        return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EINVAL}:
            fail(f"{label} must not be a symlink", exit_code=2)
        if exc.errno == errno.ENOENT:
            fail(f"{label} is missing", exit_code=2)
        fail(f"{label} is unavailable: {exc}", exit_code=2)
        raise


def _read_regular_nofollow(path: pathlib.Path, *, label: str) -> bytes:
    fd = _open_nofollow(path, os.O_RDONLY, label=label)
    try:
        before = os.fstat(fd)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            fail(f"{label} is not a regular file", exit_code=2)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            fail(f"{label} changed during read", exit_code=2)
        data = b"".join(chunks)
        if after.st_size != len(data):
            fail(f"{label} changed during read", exit_code=2)
        return data
    finally:
        os.close(fd)


def _lstat(path: pathlib.Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        fail(f"{path} is unavailable: {exc}", exit_code=1)


def quote_env_value(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def _decode_env_value(raw: str) -> str:
    if raw == "":
        return ""
    out: list[str] = []
    state = "unquoted"
    index = 0
    while index < len(raw):
        char = raw[index]
        if state == "single":
            if char == "'":
                state = "unquoted"
            else:
                out.append(char)
            index += 1
            continue
        if state == "double":
            if char == '"':
                state = "unquoted"
                index += 1
                continue
            if char in {"$", "`"}:
                fail("preferred PULSAR_COLD_ROOT assignment is dynamic", exit_code=2)
            if char == "\\":
                index += 1
                if index >= len(raw):
                    fail("preferred PULSAR_COLD_ROOT assignment is ambiguous", exit_code=2)
                escaped = raw[index]
                if escaped in {'"', "\\", "$", "`"}:
                    out.append(escaped)
                else:
                    # POSIX shell preserves the backslash before other characters
                    # inside double quotes.
                    out.extend(("\\", escaped))
                index += 1
                continue
            out.append(char)
            index += 1
            continue
        if char == "'":
            state = "single"
        elif char == '"':
            state = "double"
        elif char == "\\":
            index += 1
            if index >= len(raw):
                fail("preferred PULSAR_COLD_ROOT assignment is ambiguous", exit_code=2)
            out.append(raw[index])
        elif char in {"$", "`"}:
            fail("preferred PULSAR_COLD_ROOT assignment is dynamic", exit_code=2)
        elif char.isspace():
            fail(
                "preferred PULSAR_COLD_ROOT assignment has a trailing token",
                exit_code=2,
            )
        elif char in {";", "&", "|", "<", ">", "(", ")", "{", "}"}:
            fail("preferred PULSAR_COLD_ROOT assignment is ambiguous", exit_code=2)
        else:
            out.append(char)
        index += 1
    if state != "unquoted":
        fail("preferred PULSAR_COLD_ROOT assignment is malformed", exit_code=2)
    return "".join(out)


def _preferred_line_kind(line: bytes) -> str | None:
    body = line.split(b"\n", 1)[0].split(b"\r", 1)[0]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        if body.lstrip(b" \t").startswith(PREFERRED_KEY.encode("ascii")):
            return "malformed"
        return None
    stripped = text.lstrip(" \t")
    if stripped.startswith("#"):
        return None
    if stripped.startswith(("export ", "export\t", "declare ", "declare\t")):
        parts = stripped.split(None, 1)
        rest = parts[1] if len(parts) > 1 else ""
        if rest.startswith(PREFERRED_KEY):
            return "export"
        return None
    if text.startswith(" ") or text.startswith("\t"):
        if stripped.startswith(PREFERRED_KEY):
            return "ambiguous"
        return None
    if not text.startswith(PREFERRED_KEY):
        return None
    if text == PREFERRED_KEY or text.startswith(PREFERRED_KEY + "="):
        return "assignment"
    if text[len(PREFERRED_KEY) : len(PREFERRED_KEY) + 1].isspace():
        return "ambiguous"
    return None


def parse_dotenv_bytes(data: bytes) -> dict[str, Any]:
    lines = data.splitlines(keepends=True) if data else []
    matches: list[int] = []
    kinds: list[str] = []
    for index, line in enumerate(lines):
        kind = _preferred_line_kind(line)
        if kind is None:
            continue
        matches.append(index)
        kinds.append(kind)
    if not matches:
        return {
            "state": "absent",
            "source": "absent",
            "value": None,
            "line_index": None,
            "newline": b"\n" if b"\r\n" not in data else b"\r\n",
            "raw": data,
            "lines": lines,
        }
    if len(matches) != 1:
        fail("preferred PULSAR_COLD_ROOT assignment is duplicate", exit_code=2)
    kind = kinds[0]
    if kind in {"export", "ambiguous", "malformed"}:
        fail(
            "preferred PULSAR_COLD_ROOT assignment is "
            + (
                "exported or declared"
                if kind == "export"
                else "malformed"
                if kind == "malformed"
                else "ambiguous"
            ),
            exit_code=2,
        )
    line = lines[matches[0]]
    body = line.split(b"\n", 1)[0].split(b"\r", 1)[0]
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        fail("preferred PULSAR_COLD_ROOT assignment is malformed", exit_code=2)
    if not text.startswith(f"{PREFERRED_KEY}="):
        fail("preferred PULSAR_COLD_ROOT assignment is ambiguous", exit_code=2)
    raw_value = text[len(PREFERRED_KEY) + 1 :]
    if raw_value.endswith("\\"):
        fail("preferred PULSAR_COLD_ROOT assignment is ambiguous", exit_code=2)
    value = _decode_env_value(raw_value)
    if "\x00" in value or "\n" in value or "\r" in value:
        fail("preferred PULSAR_COLD_ROOT assignment is malformed", exit_code=2)
    newline = b"\r\n" if line.endswith(b"\r\n") else b"\n"
    if value == "":
        state = "empty"
        stored: str | None = ""
    else:
        state = "path"
        stored = value
    return {
        "state": state,
        "source": "dotenv",
        "value": stored,
        "line_index": matches[0],
        "newline": newline,
        "raw": data,
        "lines": lines,
    }


def read_dotenv(path: pathlib.Path | None = None) -> dict[str, Any]:
    target = path if path is not None else dotenv_path()
    info = _lstat(target)
    if info is None:
        return parse_dotenv_bytes(b"")
    if stat.S_ISLNK(info.st_mode):
        fail("configuration file must not be a symlink", exit_code=2)
    if not stat.S_ISREG(info.st_mode):
        fail("configuration file is not a regular file", exit_code=2)
    data = _read_regular_nofollow(target, label="configuration file")
    parsed = parse_dotenv_bytes(data)
    parsed["path"] = str(target)
    parsed["mode"] = stat.S_IMODE(info.st_mode)
    return parsed


def persisted_assignment(path: pathlib.Path | None = None) -> dict[str, Any]:
    parsed = read_dotenv(path)
    return {
        "state": parsed["state"],
        "source": "absent" if parsed["state"] == "absent" else "dotenv",
        "value": parsed["value"],
    }


def _assignment_state_label(assignment: Mapping[str, Any]) -> str:
    state = assignment.get("state")
    if state == "absent":
        return "not-configured"
    if state == "empty":
        return "disabled"
    if state == "path":
        return "configured"
    fail("configuration assignment state is invalid", exit_code=2)
    raise AssertionError


def _values_differ(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (left.get("state"), left.get("value")) != (
        right.get("state"),
        right.get("value"),
    )


def effective_assignment(
    *,
    process: Mapping[str, Any] | None = None,
    persisted: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    process_value = process if process is not None else process_assignment()
    persisted_value = (
        persisted if persisted is not None else persisted_assignment()
    )
    if process_value["state"] != "absent":
        return {
            "state": process_value["state"],
            "source": "process",
            "value": process_value["value"],
        }
    if persisted_value["state"] != "absent":
        return {
            "state": persisted_value["state"],
            "source": "dotenv",
            "value": persisted_value["value"],
        }
    return {"state": "absent", "source": "absent", "value": None}


def lexical_absolute(path: str, *, label: str = "path") -> str:
    if not isinstance(path, str) or path == "":
        fail(f"{label} must be an absolute directory", exit_code=2)
    if path != path.strip() or path.startswith("~"):
        fail(f"{label} must be an absolute directory", exit_code=2)
    if UNSAFE_PATH_CONTROLS.search(path) or "\x00" in path:
        fail(f"{label} contains a disallowed character", exit_code=2)
    if "$(" in path or "`" in path:
        fail(f"{label} contains a disallowed character", exit_code=2)
    candidate = pathlib.Path(path)
    if not candidate.is_absolute() or _has_dot_dot(candidate):
        fail(f"{label} must be an absolute directory", exit_code=2)
    try:
        normalized = descriptor_dir.safe_absolute(candidate, label=label)
    except descriptor_dir.ImmutableDescriptorDirectoryError as exc:
        fail(str(exc), exit_code=2)
    return str(normalized)


def _path_exists_dir_access(path: pathlib.Path) -> dict[str, Any]:
    info = _lstat(path)
    result = {
        "exists": info is not None,
        "directory": False,
        "readable": False,
        "writable": False,
        "searchable": False,
        "final_symlink": bool(info is not None and stat.S_ISLNK(info.st_mode)),
        "physical_path": None,
    }
    if info is None:
        return result
    if stat.S_ISLNK(info.st_mode):
        return result
    if not stat.S_ISDIR(info.st_mode):
        return result
    result["directory"] = True
    result["readable"] = os.access(path, os.R_OK)
    result["writable"] = os.access(path, os.W_OK)
    result["searchable"] = os.access(path, os.X_OK)
    try:
        result["physical_path"] = str(pathlib.Path(os.path.realpath(path)))
    except OSError:
        result["physical_path"] = None
    return result


def _parts(path: str) -> tuple[str, ...]:
    return pathlib.Path(path).parts


def paths_nested_or_equal(left: str, right: str) -> bool:
    lp, rp = _parts(left), _parts(right)
    if lp == rp:
        return True
    if len(lp) <= len(rp):
        return rp[: len(lp)] == lp
    return lp[: len(rp)] == rp


def _managed_roots(*, lib_dir: pathlib.Path) -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = []
    roots.append(("model-library", str(lib_dir)))
    hot = os.environ.get("PULSAR_HOT_ROOT") or model_library.DEFAULT_HOT_ROOT
    roots.append(("hot-root", hot))
    hf_cache = os.environ.get("HF_CACHE") or str(
        pathlib.Path.home() / ".cache" / "huggingface"
    )
    roots.append(("hf-cache", hf_cache))
    return roots


def inspect_path_health(
    path: str | None,
    *,
    lib_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    health: dict[str, Any] = {
        "lexical_path": None,
        "exists": False,
        "directory": False,
        "readable": False,
        "writable": False,
        "searchable": False,
        "final_symlink": False,
        "physical_path": None,
        "unsafe": False,
        "unsafe_detail": None,
        "usable": False,
    }
    if path in (None, ""):
        return health
    try:
        lexical = lexical_absolute(path, label="cold recovery path")
    except ColdStorageError as exc:
        health["unsafe"] = True
        health["unsafe_detail"] = str(exc)
        return health
    health["lexical_path"] = lexical
    observed = _path_exists_dir_access(pathlib.Path(lexical))
    health.update(observed)
    if health["final_symlink"]:
        health["unsafe"] = True
        health["unsafe_detail"] = "cold recovery path must not be a symlink"
        return health
    library = lib_dir if lib_dir is not None else library_dir()
    for label, root in _managed_roots(lib_dir=library):
        try:
            managed = lexical_absolute(root, label=label)
        except ColdStorageError:
            continue
        if paths_nested_or_equal(lexical, managed):
            health["unsafe"] = True
            health["unsafe_detail"] = (
                f"cold recovery path is nested with the {label} directory"
            )
            return health
        physical = health.get("physical_path")
        if physical:
            try:
                managed_physical = os.path.realpath(managed)
            except OSError:
                managed_physical = managed
            if paths_nested_or_equal(physical, managed_physical):
                health["unsafe"] = True
                health["unsafe_detail"] = (
                    f"cold recovery path is nested with the {label} directory"
                )
                return health
    occupancy_detail = _occupancy_nest_detail(lexical, library)
    if occupancy_detail:
        health["unsafe"] = True
        health["unsafe_detail"] = occupancy_detail
        return health
    health["usable"] = bool(
        health["exists"]
        and health["directory"]
        and health["readable"]
        and health["searchable"]
        and not health["final_symlink"]
        and not health["unsafe"]
    )
    return health


def _store_listing(
    store: pathlib.Path,
    *,
    label: str,
    name_re: re.Pattern[str],
    directories: bool = False,
    temp_re: re.Pattern[str] | None = None,
) -> dict[str, Any]:
    info = _lstat(store)
    if info is None:
        return {"ok": True, "ids": [], "detail": None}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return {
            "ok": False,
            "ids": [],
            "detail": f"{label} is not a regular directory",
        }
    try:
        names = sorted(os.listdir(store))
    except OSError as exc:
        return {"ok": False, "ids": [], "detail": f"{label} is unreadable: {exc}"}
    ids: list[str] = []
    for name in names:
        path = store / name
        try:
            entry = path.lstat()
        except OSError:
            return {"ok": False, "ids": [], "detail": f"{label} entry is unreadable"}
        if directories:
            if name_re.fullmatch(name) is None:
                if temp_re is not None and temp_re.fullmatch(name) is not None:
                    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                        return {
                            "ok": False,
                            "ids": [],
                            "detail": f"{label} contains a non-directory writer temp",
                        }
                    continue
                return {
                    "ok": False,
                    "ids": [],
                    "detail": f"{label} contains an unexpected entry",
                }
            if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                return {
                    "ok": False,
                    "ids": [],
                    "detail": f"{label} contains a non-directory recovery object",
                }
            ids.append(name)
            continue
        if name_re.fullmatch(name) is None:
            if temp_re is not None and temp_re.fullmatch(name) is not None:
                if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
                    return {
                        "ok": False,
                        "ids": [],
                        "detail": f"{label} contains a non-regular writer temp",
                    }
                continue
            return {
                "ok": False,
                "ids": [],
                "detail": f"{label} contains an unexpected entry",
            }
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            return {
                "ok": False,
                "ids": [],
                "detail": f"{label} contains a non-regular recovery object",
            }
        ids.append(name[: -len(".json")] if name.endswith(".json") else name)
    return {"ok": True, "ids": ids, "detail": None}


def _local_topology_node_id() -> str | None:
    """Return the controller node id when a stable topology names rank 0."""
    path = repo_dir() / ".cluster-topology.json"
    info = _lstat(path)
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    try:
        value = json.loads(_read_regular_nofollow(path, label="topology file"))
    except (ColdStorageError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    nodes = value.get("nodes") if isinstance(value, dict) else None
    if not isinstance(nodes, list) or not nodes or not isinstance(nodes[0], dict):
        return None
    if nodes[0].get("rank") != 0:
        return None
    node_id = nodes[0].get("node_id")
    return node_id if isinstance(node_id, str) and node_id else None


def _occupancy_paths(lib_dir: pathlib.Path) -> dict[str, Any]:
    store = source_attested.source_attested_home_attachment_store(lib_dir)
    info = _lstat(store)
    if info is None:
        return {"ok": True, "attachments": [], "detail": None}
    try:
        attachments = source_attested._listed_source_attested_home_attachments(
            lib_dir
        )
    except source_attested.SourceAttestedAcquisitionError as exc:
        return {"ok": False, "attachments": [], "detail": str(exc)}
    except OSError as exc:
        return {"ok": False, "attachments": [], "detail": str(exc)}
    return {"ok": True, "attachments": attachments, "detail": None}


def _occupancy_nest_detail(lexical: str, lib_dir: pathlib.Path) -> str | None:
    view = _occupancy_paths(lib_dir)
    if not view["ok"]:
        return view["detail"]
    local_node_id = _local_topology_node_id()
    if local_node_id is None:
        return None
    for attachment in view["attachments"]:
        if attachment.get("node_id") != local_node_id:
            continue
        home = attachment.get("durable_home_path")
        if not isinstance(home, str) or home == "":
            continue
        if paths_nested_or_equal(lexical, home):
            return "cold recovery path is nested with a known occupancy tree"
        physical = None
        try:
            if pathlib.Path(home).exists():
                physical = os.path.realpath(home)
        except OSError:
            physical = None
        if physical and paths_nested_or_equal(
            os.path.realpath(lexical)
            if pathlib.Path(lexical).exists()
            else lexical,
            physical,
        ):
            return "cold recovery path is nested with a known occupancy tree"
    return None


def inspect_recovery(
    root: str | None,
    *,
    lib_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    library = lib_dir if lib_dir is not None else library_dir()
    jobs_view = _job_view(library)
    recovery: dict[str, Any] = {
        "ok": jobs_view["ok"],
        "detail": jobs_view.get("detail"),
        "receipt_replica_ids": [],
        "model_archive_ids": [],
        "archive_job_ids": list(jobs_view.get("ids") or []),
        "jobs": list(jobs_view.get("jobs") or []),
        "job_counts": dict(jobs_view.get("counts") or {}),
        "occupancy_ok": True,
    }
    occupancy = _occupancy_paths(library)
    if not occupancy["ok"]:
        recovery["ok"] = False
        recovery["occupancy_ok"] = False
        recovery["detail"] = occupancy["detail"]
    receipts_ok, receipts_detail = _receipt_store_ok(library)
    if not receipts_ok:
        recovery["ok"] = False
        recovery["detail"] = receipts_detail
    if not root:
        return recovery
    try:
        lexical = lexical_absolute(root, label="cold recovery path")
    except ColdStorageError as exc:
        recovery["ok"] = False
        recovery["detail"] = str(exc)
        return recovery
    control = pathlib.Path(lexical) / "pulsar-control"
    replicas = _store_listing(
        control / "download-receipts",
        label="cold receipt replica store",
        name_re=REPLICA_NAME_RE,
        temp_re=REPLICA_TEMP_RE,
    )
    control_info = _lstat(control)
    if control_info is not None and (
        stat.S_ISLNK(control_info.st_mode) or not stat.S_ISDIR(control_info.st_mode)
    ):
        recovery["ok"] = False
        recovery["detail"] = "cold control namespace is not a regular directory"
        return recovery
    if control_info is not None:
        nested = _store_listing(
            control,
            label="cold control namespace",
            name_re=re.compile(r"^(download-receipts)$"),
            directories=True,
        )
        if not nested["ok"]:
            recovery["ok"] = False
            recovery["detail"] = nested["detail"]
            return recovery
    if not replicas["ok"]:
        recovery["ok"] = False
        recovery["detail"] = replicas["detail"]
        return recovery
    archives = _store_listing(
        pathlib.Path(lexical) / "pulsar-receipts",
        label="cold model-archive namespace",
        name_re=ARCHIVE_DIR_RE,
        directories=True,
        temp_re=ARCHIVE_STAGING_RE,
    )
    receipts_root = pathlib.Path(lexical) / "pulsar-receipts"
    receipts_info = _lstat(receipts_root)
    if receipts_info is not None and (
        stat.S_ISLNK(receipts_info.st_mode)
        or not stat.S_ISDIR(receipts_info.st_mode)
    ):
        recovery["ok"] = False
        recovery["detail"] = "cold model-archive namespace is not a regular directory"
        return recovery
    if not archives["ok"]:
        recovery["ok"] = False
        recovery["detail"] = archives["detail"]
        return recovery
    recovery["receipt_replica_ids"] = list(replicas["ids"])
    recovery["model_archive_ids"] = list(archives["ids"])
    for receipt_id in replicas["ids"]:
        try:
            cold_archive.load_receipt_replica(lexical, receipt_id)
        except (
            cold_archive.ColdArchiveError,
            source_attested.SourceAttestedAcquisitionError,
            OSError,
        ) as exc:
            recovery["ok"] = False
            recovery["detail"] = f"cold receipt replica is invalid: {exc}"
            return recovery
    for receipt_id in archives["ids"]:
        try:
            presence = cold_archive.load_cold_archive_presence(lexical, receipt_id)
            hub = cold_archive.archived_hub_path(lexical, receipt_id)
            hub_info = hub.lstat()
        except (cold_archive.ColdArchiveError, OSError) as exc:
            recovery["ok"] = False
            recovery["detail"] = f"cold model archive is invalid: {exc}"
            return recovery
        if (
            presence is None
            or presence.get("receipt_id") != receipt_id
            or stat.S_ISLNK(hub_info.st_mode)
            or not stat.S_ISDIR(hub_info.st_mode)
        ):
            recovery["ok"] = False
            recovery["detail"] = "cold model archive is incomplete or invalid"
            return recovery
    return recovery


def _receipt_store_ok(lib_dir: pathlib.Path) -> tuple[bool, str | None]:
    store = source_attested.source_attested_receipt_store(lib_dir)
    info = _lstat(store)
    if info is None:
        return True, None
    try:
        source_attested._listed_source_attested_receipts(lib_dir)
    except source_attested.SourceAttestedAcquisitionError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)
    return True, None


def _job_view(lib_dir: pathlib.Path) -> dict[str, Any]:
    counts = {state: 0 for state in cold_archive.COLD_ARCHIVE_JOB_STATES}
    try:
        jobs = cold_archive.list_cold_archive_jobs(lib_dir)
    except cold_archive.ColdArchiveError as exc:
        return {
            "ok": False,
            "ids": [],
            "jobs": [],
            "counts": counts,
            "detail": str(exc),
        }
    except OSError as exc:
        return {
            "ok": False,
            "ids": [],
            "jobs": [],
            "counts": counts,
            "detail": str(exc),
        }
    for job in jobs:
        state = job["state"]
        if state in counts:
            counts[state] += 1
    return {
        "ok": True,
        "ids": [job["receipt_id"] for job in jobs],
        "jobs": jobs,
        "counts": counts,
        "detail": None,
    }


def configured_state_for_path(
    assignment: Mapping[str, Any],
    health: Mapping[str, Any],
    recovery: Mapping[str, Any],
) -> str:
    label = _assignment_state_label(assignment)
    if label != "configured":
        return label
    if (
        health.get("usable")
        and recovery.get("ok")
        and not health.get("unsafe")
    ):
        return "configured-available"
    return "configured-unavailable"


def snapshot_pair(
    *,
    lib_dir: pathlib.Path | None = None,
    dotenv: pathlib.Path | None = None,
) -> dict[str, Any]:
    library = lib_dir if lib_dir is not None else library_dir()
    persisted = persisted_assignment(dotenv)
    process = process_assignment()
    effective = effective_assignment(process=process, persisted=persisted)
    persisted_health = inspect_path_health(persisted.get("value"), lib_dir=library)
    effective_health = inspect_path_health(effective.get("value"), lib_dir=library)
    persisted_recovery = inspect_recovery(persisted.get("value"), lib_dir=library)
    effective_recovery = inspect_recovery(effective.get("value"), lib_dir=library)
    persisted_state = configured_state_for_path(
        persisted, persisted_health, persisted_recovery
    )
    effective_state = configured_state_for_path(
        effective, effective_health, effective_recovery
    )
    override = process["state"] != "absent" and _values_differ(process, persisted)
    top = "environment-override" if override else effective_state
    return {
        "persisted": {
            "state": persisted_state,
            "source": persisted["source"],
            "value": persisted["value"],
            "assignment": persisted["state"],
        },
        "effective": {
            "state": effective_state,
            "source": effective["source"],
            "value": effective["value"],
            "assignment": effective["state"],
        },
        "process": process,
        "top_state": top,
        "path_health": effective_health
        if effective["state"] == "path"
        else persisted_health,
        "recovery": effective_recovery
        if effective["state"] == "path"
        else persisted_recovery,
        "persisted_recovery": persisted_recovery,
        "effective_recovery": effective_recovery,
        "library_dir": str(library),
    }


def _id_prefix(value: str) -> str:
    return value[:12] if len(value) >= 12 else value


def _findings(
    *,
    top_state: str,
    health: Mapping[str, Any],
    recovery: Mapping[str, Any],
    action: str | None = None,
) -> list[str]:
    findings: list[str] = []
    if top_state == "not-configured":
        findings.append(
            "No explicit PULSAR_COLD_ROOT choice is persisted or set in the process."
        )
        findings.append(
            "Receipt acquisition records cold archive unavailable until explicit configuration."
        )
    elif top_state == "disabled":
        findings.append("Cold recovery storage is explicitly disabled.")
    elif top_state == "environment-override":
        findings.append(
            "The process PULSAR_COLD_ROOT value differs from the persisted .env assignment."
        )
    if health.get("final_symlink"):
        findings.append("The selected path is a symlink.")
    if health.get("unsafe_detail"):
        findings.append(str(health["unsafe_detail"]))
    if health.get("lexical_path") and not health.get("exists"):
        findings.append("The selected directory does not exist.")
    elif health.get("exists") and not health.get("directory"):
        findings.append("The selected path is not a directory.")
    if not recovery.get("ok") and recovery.get("detail"):
        findings.append(str(recovery["detail"]))
    if action == "change-blocked":
        findings.append(
            "The requested configuration change is blocked; resolve the path "
            "or recovery-state findings above."
        )
    findings.append(AUTHORITY_ASSERTION)
    return findings


def show_status(
    *,
    lib_dir: pathlib.Path | None = None,
    dotenv: pathlib.Path | None = None,
) -> dict[str, Any]:
    snap = snapshot_pair(lib_dir=lib_dir, dotenv=dotenv)
    recovery = snap["recovery"]
    health = snap["path_health"]
    path = None
    if snap["effective"]["assignment"] == "path":
        path = health.get("lexical_path") or snap["effective"]["value"]
    elif snap["persisted"]["assignment"] == "path":
        path = health.get("lexical_path") or snap["persisted"]["value"]
    status = {
        "schema_version": SCHEMA_VERSION,
        "kind": STATUS_KIND,
        "state": snap["top_state"],
        "persisted": {
            "state": snap["persisted"]["state"],
            "source": snap["persisted"]["source"],
            "value": snap["persisted"]["value"],
        },
        "effective": {
            "state": snap["effective"]["state"],
            "source": snap["effective"]["source"],
            "value": snap["effective"]["value"],
        },
        "path": path,
        "path_health": {
            "lexical_path": health.get("lexical_path"),
            "exists": bool(health.get("exists")),
            "directory": bool(health.get("directory")),
            "readable": bool(health.get("readable")),
            "writable": bool(health.get("writable")),
            "searchable": bool(health.get("searchable")),
            "final_symlink": bool(health.get("final_symlink")),
            "physical_path": health.get("physical_path"),
            "unsafe": bool(health.get("unsafe")),
            "unsafe_detail": health.get("unsafe_detail"),
            "usable": bool(health.get("usable")),
        },
        "recovery": {
            "ok": bool(recovery.get("ok")),
            "detail": recovery.get("detail"),
            "receipt_replica_count": len(recovery.get("receipt_replica_ids") or []),
            "model_archive_count": len(recovery.get("model_archive_ids") or []),
            "archive_job_count": len(recovery.get("archive_job_ids") or []),
            "job_counts": recovery.get("job_counts") or {},
        },
        "archive_jobs": project_archive_jobs(
            recovery.get("jobs") or [],
            lib_dir=pathlib.Path(snap["library_dir"]),
        )["jobs"],
        "findings": _findings(
            top_state=snap["top_state"],
            health=health,
            recovery=recovery,
        ),
        "authority_assertion": AUTHORITY_ASSERTION,
        "exit_code": _status_exit(snap["top_state"], snap["effective"]["state"]),
    }
    unknown = set(status) - STATUS_FIELDS
    if unknown:
        fail(f"cold-storage status has unsupported fields {sorted(unknown)}")
    return status


def _status_exit(top_state: str, effective_state: str) -> int:
    if top_state == "configured-unavailable":
        return 1
    if top_state == "environment-override" and effective_state == "configured-unavailable":
        return 1
    return 0


def _affected_from_recovery(recovery: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "archive_job_ids": list(recovery.get("archive_job_ids") or []),
        "receipt_replica_ids": list(recovery.get("receipt_replica_ids") or []),
        "model_archive_ids": list(recovery.get("model_archive_ids") or []),
    }


def _merge_recovery(*views: Mapping[str, Any]) -> dict[str, Any]:
    job_by_id: dict[str, dict[str, Any]] = {}
    merged = {
        "ok": True,
        "detail": None,
        "receipt_replica_ids": [],
        "model_archive_ids": [],
        "archive_job_ids": [],
        "jobs": [],
        "job_counts": {state: 0 for state in cold_archive.COLD_ARCHIVE_JOB_STATES},
        "occupancy_ok": True,
    }
    replica_ids: set[str] = set()
    archive_ids: set[str] = set()
    for view in views:
        if not view.get("ok"):
            merged["ok"] = False
            if merged["detail"] is None:
                merged["detail"] = view.get("detail")
        if not view.get("occupancy_ok", True):
            merged["occupancy_ok"] = False
        replica_ids.update(view.get("receipt_replica_ids") or [])
        archive_ids.update(view.get("model_archive_ids") or [])
        for job in view.get("jobs") or []:
            receipt_id = job.get("receipt_id")
            if isinstance(receipt_id, str):
                job_by_id[receipt_id] = job
    jobs = [job_by_id[key] for key in sorted(job_by_id)]
    counts = {state: 0 for state in cold_archive.COLD_ARCHIVE_JOB_STATES}
    for job in jobs:
        state = job.get("state")
        if state in counts:
            counts[state] += 1
    merged["receipt_replica_ids"] = sorted(replica_ids)
    merged["model_archive_ids"] = sorted(archive_ids)
    merged["archive_job_ids"] = sorted(job_by_id)
    merged["jobs"] = jobs
    merged["job_counts"] = counts
    return merged


def _mutation_blocked(recovery: Mapping[str, Any], *, changing: bool) -> bool:
    if not changing:
        return False
    if not recovery.get("ok"):
        return True
    if recovery.get("archive_job_ids"):
        return True
    if recovery.get("receipt_replica_ids"):
        return True
    if recovery.get("model_archive_ids"):
        return True
    return False


def build_plan(
    *,
    requested: str | None,
    disable: bool,
    lib_dir: pathlib.Path | None = None,
    dotenv: pathlib.Path | None = None,
) -> dict[str, Any]:
    snap = snapshot_pair(lib_dir=lib_dir, dotenv=dotenv)
    requested_value: str | None
    if disable:
        requested_value = ""
        requested_state = "empty"
        health = inspect_path_health(None, lib_dir=lib_dir)
    else:
        if requested is None:
            fail("plan --path is required unless disabling", exit_code=2)
        lexical = lexical_absolute(requested, label="cold recovery path")
        requested_value = lexical
        requested_state = "path"
        health = inspect_path_health(lexical, lib_dir=pathlib.Path(snap["library_dir"]))
    persisted_value = snap["persisted"]["value"]
    same = (requested_state == snap["persisted"]["assignment"]) and (
        requested_value == persisted_value
        or (requested_state == "empty" and persisted_value == "")
    )
    changing = not same
    abandoned_views: list[Mapping[str, Any]] = []
    if snap["persisted"]["assignment"] == "path" and (
        disable or snap["persisted"]["value"] != requested_value
    ):
        abandoned_views.append(snap["persisted_recovery"])
    if snap["effective"]["assignment"] == "path" and (
        disable or snap["effective"]["value"] != requested_value
    ):
        abandoned_views.append(snap["effective_recovery"])
    # Disable is always a recovery-state transition, even when no root is
    # currently active; an existing job must not be silently abandoned.
    guard_recovery = _merge_recovery(
        *(abandoned_views or [snap["persisted_recovery"], snap["effective_recovery"]])
    )
    abandoning = bool(abandoned_views) or (disable and changing)
    strand = _mutation_blocked(
        guard_recovery,
        changing=abandoning,
    )
    if same:
        action = "keep"
    elif (
        not guard_recovery.get("ok")
        or strand
        or (requested_state == "path" and not health.get("usable"))
    ):
        action = "change-blocked"
    else:
        action = "set-new"
    findings = _findings(
        top_state=snap["top_state"],
        health=health if requested_state == "path" else snap["path_health"],
        recovery=guard_recovery,
        action=action,
    )
    body = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "action": action,
        "requested": {
            "state": requested_state,
            "value": requested_value,
        },
        "persisted": {
            "state": snap["persisted"]["state"],
            "source": snap["persisted"]["source"],
            "value": snap["persisted"]["value"],
        },
        "effective": {
            "state": snap["effective"]["state"],
            "source": snap["effective"]["source"],
            "value": snap["effective"]["value"],
        },
        "path_health": {
            "lexical_path": health.get("lexical_path"),
            "exists": bool(health.get("exists")),
            "directory": bool(health.get("directory")),
            "readable": bool(health.get("readable")),
            "writable": bool(health.get("writable")),
            "searchable": bool(health.get("searchable")),
            "final_symlink": bool(health.get("final_symlink")),
            "physical_path": health.get("physical_path"),
            "unsafe": bool(health.get("unsafe")),
            "unsafe_detail": health.get("unsafe_detail"),
            "usable": bool(health.get("usable")),
        },
        "affected": _affected_from_recovery(guard_recovery),
        "findings": findings,
        "authority_assertion": AUTHORITY_ASSERTION,
    }
    plan_id = model_identity.canonical_json_digest(body)
    exit_code = 0 if action in {"set-new", "keep"} else 1
    plan = {**body, "plan_id": plan_id, "exit_code": exit_code}
    unknown = set(plan) - PLAN_FIELDS
    if unknown:
        fail(f"cold-storage plan has unsupported fields {sorted(unknown)}")
    return plan


def _assignment_line(value: str, newline: bytes) -> bytes:
    rendered = f"{PREFERRED_KEY}={quote_env_value(value)}"
    return rendered.encode("utf-8") + newline


def _private_mode(mode: int) -> bool:
    return stat.S_IMODE(mode) & 0o077 == 0


def write_dotenv_assignment(
    value: str,
    *,
    path: pathlib.Path | None = None,
    expected_plan_id: str | None = None,
    disable: bool = False,
    lib_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    target = path if path is not None else dotenv_path()
    if os.environ.get(SELFTEST_ENV) == "1" and os.environ.get(TEST_DOTENV_ENV) in (
        None,
        "",
    ):
        fail(
            f"{SELFTEST_ENV}=1 requires {TEST_DOTENV_ENV} for configuration writes",
            exit_code=2,
        )
    parent = target.parent
    parent_info = _lstat(parent)
    if parent_info is None:
        fail("configuration directory is missing", exit_code=2)
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        fail("configuration directory is not a regular directory", exit_code=2)
    lock = _lock_path(target)
    tmp_path: pathlib.Path | None = None
    replaced = False
    try:
        lock_fd = os.open(
            lock,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EINVAL}:
            fail("configuration lock must not be a symlink", exit_code=2)
        fail(f"configuration lock is unavailable: {exc}", exit_code=2)
    try:
        lock_info = os.fstat(lock_fd)
        if stat.S_ISLNK(lock_info.st_mode) or not stat.S_ISREG(lock_info.st_mode):
            fail("configuration lock is not a regular file", exit_code=2)
        try:
            os.fchmod(lock_fd, 0o600)
        except OSError as exc:
            fail(f"configuration lock permissions cannot be set: {exc}", exit_code=2)
        lock_info = os.fstat(lock_fd)
        if not _private_mode(lock_info.st_mode):
            fail("configuration lock permissions are not private", exit_code=2)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if WRITE_LOCK_HOOK is not None:
            WRITE_LOCK_HOOK(target)
        plan = build_plan(
            requested=None if disable else value,
            disable=disable,
            lib_dir=lib_dir,
            dotenv=target,
        )
        if expected_plan_id is not None and expected_plan_id != plan["plan_id"]:
            fail(
                "cold recovery storage changed after the preview; re-run the plan",
                exit_code=1,
            )
        if plan["action"] == "change-blocked":
            fail(
                "changing or disabling cold recovery storage is blocked",
                exit_code=1,
            )
        if plan["action"] == "keep":
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": MUTATION_KIND,
                "action": "keep",
                "plan_id": plan["plan_id"],
                "plan": plan,
            }
        stored_value = "" if disable else plan["requested"]["value"]
        if stored_value is None:
            fail("planned configuration value is missing", exit_code=2)
        existing = _lstat(target)
        mode = 0o600
        parsed = parse_dotenv_bytes(b"")
        if existing is not None:
            if stat.S_ISLNK(existing.st_mode):
                fail("configuration file must not be a symlink", exit_code=2)
            if not stat.S_ISREG(existing.st_mode):
                fail("configuration file is not a regular file", exit_code=2)
            if not _private_mode(existing.st_mode):
                fail(
                    "configuration file permissions are not private",
                    exit_code=2,
                )
            mode = stat.S_IMODE(existing.st_mode)
            parsed = parse_dotenv_bytes(
                _read_regular_nofollow(target, label="configuration file")
            )
        newline = parsed.get("newline") or b"\n"
        assignment = _assignment_line(stored_value, newline)
        lines = list(parsed.get("lines") or [])
        index = parsed.get("line_index")
        if index is None:
            if lines and not lines[-1].endswith((b"\n", b"\r")):
                lines[-1] = lines[-1] + newline
            lines.append(assignment)
        else:
            lines[index] = assignment
        payload = b"".join(lines)
        tmp_name = f".{target.name}.pulsar-cold-storage.{secrets.token_hex(8)}"
        tmp_path = parent / tmp_name
        tmp_fd = os.open(
            tmp_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(tmp_fd, view)
                view = view[written:]
            os.fsync(tmp_fd)
            os.fchmod(tmp_fd, mode)
        finally:
            os.close(tmp_fd)
        os.replace(tmp_path, target)
        replaced = True
        dir_fd = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": MUTATION_KIND,
            "action": "set-new",
            "plan_id": plan["plan_id"],
            "plan": plan,
        }
    except Exception:
        if tmp_path is not None and not replaced:
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def project_archive_jobs(
    jobs: list[dict[str, Any]],
    *,
    lib_dir: pathlib.Path,
) -> dict[str, Any]:
    projected: list[dict[str, Any]] = []
    for job in jobs:
        receipt_id = job["receipt_id"]
        eligible, reason = retry_eligibility(lib_dir, job)
        projected.append(
            {
                "receipt_id": receipt_id,
                "receipt_id_prefix": _id_prefix(receipt_id),
                "model_id": job.get("model_id"),
                "snapshot_revision": job.get("snapshot_revision"),
                "state": job.get("state"),
                "detail": job.get("detail") or "",
                "retry_eligible": eligible,
                "reason": reason,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": JOBS_KIND,
        "jobs": projected,
        "count": len(projected),
    }


def list_archive_jobs_document(
    *,
    lib_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    library = lib_dir if lib_dir is not None else library_dir()
    view = _job_view(library)
    if not view["ok"]:
        fail(view["detail"] or "archive job store is unavailable", exit_code=1)
    document = project_archive_jobs(view["jobs"], lib_dir=library)
    document["exit_code"] = 0
    return document


def retry_eligibility(
    lib_dir: pathlib.Path,
    job: Mapping[str, Any],
) -> tuple[bool, str]:
    state = job.get("state")
    if state not in RETRYABLE_JOB_STATES:
        return False, f"job state {state} cannot be retried"
    receipt_id = job.get("receipt_id")
    if not isinstance(receipt_id, str) or RECEIPT_ID_RE.fullmatch(receipt_id) is None:
        return False, "job receipt id is invalid"
    try:
        receipt = source_attested.load_source_attested_receipt(lib_dir, receipt_id)
    except (source_attested.SourceAttestedAcquisitionError, OSError, FileNotFoundError):
        return False, "controller receipt does not resolve"
    try:
        attachment = source_attested.load_source_attested_home_attachment(
            lib_dir,
            model_id=receipt["model_id"],
            snapshot_revision=receipt["snapshot_revision"],
        )
    except source_attested.SourceAttestedAcquisitionError:
        return False, "occupancy store is unreadable"
    if attachment is None:
        return False, "current occupancy does not resolve"
    return True, job.get("detail") or "job can be retried"


def retry_plan(receipt_id: str, *, lib_dir: pathlib.Path | None = None) -> dict[str, Any]:
    if RECEIPT_ID_RE.fullmatch(receipt_id) is None:
        fail("receipt id is invalid", exit_code=2)
    library = lib_dir if lib_dir is not None else library_dir()
    job = cold_archive.load_cold_archive_job(library, receipt_id)
    if job is None:
        fail("archive job was not found", exit_code=1)
    eligible, reason = retry_eligibility(library, job)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RETRY_KIND,
        "receipt_id": receipt_id,
        "model_id": job.get("model_id"),
        "snapshot_revision": job.get("snapshot_revision"),
        "state": job.get("state"),
        "eligible": eligible,
        "reason": reason,
        "command": [
            "scripts/model-library.sh",
            "home",
            "archive",
            "run",
            "--receipt",
            receipt_id,
            "--yes",
        ],
        "exit_code": 0 if eligible else 1,
    }


def render_status(status: Mapping[str, Any], *, width: int | None = None) -> None:
    writer = terminal_format.TerminalWriter(width=width)
    writer.emit("Cold recovery storage")
    writer.field("State", status["state"], label_width=12)
    writer.field("Persisted", status["persisted"]["state"], label_width=12)
    writer.field("  source", status["persisted"]["source"], label_width=12)
    writer.field(
        "  value",
        _display_value(status["persisted"]["value"]),
        label_width=12,
    )
    writer.field("Effective", status["effective"]["state"], label_width=12)
    writer.field("  source", status["effective"]["source"], label_width=12)
    writer.field(
        "  value",
        _display_value(status["effective"]["value"]),
        label_width=12,
    )
    health = status["path_health"]
    if status.get("path"):
        writer.field("Path", status["path"], label_width=12)
    writer.field(
        "Health",
        _health_summary(health),
        label_width=12,
    )
    writer.field(
        "Writable", "yes" if health.get("writable") else "no", label_width=12
    )
    recovery = status["recovery"]
    writer.field(
        "Replicas",
        str(recovery.get("receipt_replica_count") or 0),
        label_width=12,
    )
    writer.field(
        "Archives",
        str(recovery.get("model_archive_count") or 0),
        label_width=12,
    )
    writer.field(
        "Jobs",
        _job_count_summary(recovery.get("job_counts") or {}),
        label_width=12,
    )
    writer.blank()
    for finding in status.get("findings") or []:
        writer.emit(finding, initial_indent="", subsequent_indent="")


def render_plan(plan: Mapping[str, Any], *, width: int | None = None) -> None:
    writer = terminal_format.TerminalWriter(width=width)
    writer.emit("Cold recovery storage plan")
    writer.field("Action", plan["action"], label_width=12)
    writer.field(
        "Requested",
        _display_value(plan["requested"]["value"])
        if plan["requested"]["state"] == "path"
        else "disable",
        label_width=12,
    )
    writer.field("Persisted", plan["persisted"]["state"], label_width=12)
    writer.field("Effective", plan["effective"]["state"], label_width=12)
    writer.field("Health", _health_summary(plan["path_health"]), label_width=12)
    writer.field(
        "Writable",
        "yes" if plan["path_health"].get("writable") else "no",
        label_width=12,
    )
    affected = plan.get("affected") or {}
    writer.field(
        "Jobs",
        _prefix_list(affected.get("archive_job_ids") or []),
        label_width=12,
    )
    writer.field(
        "Replicas",
        _prefix_list(affected.get("receipt_replica_ids") or []),
        label_width=12,
    )
    writer.field(
        "Archives",
        _prefix_list(affected.get("model_archive_ids") or []),
        label_width=12,
    )
    writer.blank()
    for finding in plan.get("findings") or []:
        writer.emit(finding)


def render_jobs(document: Mapping[str, Any], *, width: int | None = None) -> None:
    writer = terminal_format.TerminalWriter(width=width)
    jobs = document.get("jobs") or []
    if not jobs:
        writer.emit("No archive jobs.")
        return
    writer.emit("Archive jobs")
    for job in jobs:
        writer.blank()
        writer.field("Receipt", job["receipt_id_prefix"], label_width=12)
        writer.field("Model", job.get("model_id") or "-", label_width=12)
        writer.field(
            "Revision",
            _id_prefix(str(job.get("snapshot_revision") or "-")),
            label_width=12,
        )
        writer.field("State", job.get("state") or "-", label_width=12)
        writer.field("Reason", job.get("reason") or job.get("detail") or "-", label_width=12)


def render_retry(document: Mapping[str, Any], *, width: int | None = None) -> None:
    writer = terminal_format.TerminalWriter(width=width)
    writer.emit("Retry one archive job")
    writer.field("Receipt", document["receipt_id"], label_width=12)
    writer.field("Model", document.get("model_id") or "-", label_width=12)
    writer.field("State", document.get("state") or "-", label_width=12)
    writer.field("Eligible", "yes" if document.get("eligible") else "no", label_width=12)
    writer.field("Reason", document.get("reason") or "-", label_width=12)
    writer.emit("Delegates to scripts/model-library.sh home archive run --receipt ID --yes")


def _mutation_document(
    result: Mapping[str, Any], status: Mapping[str, Any]
) -> dict[str, Any]:
    plan = result.get("plan")
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND:
        fail("cold-storage mutation result is missing its bound plan", exit_code=2)
    status_payload = {key: value for key, value in status.items() if key != "exit_code"}
    document = {
        "schema_version": SCHEMA_VERSION,
        "kind": MUTATION_KIND,
        "action": result.get("action"),
        "plan": {key: value for key, value in plan.items() if key != "exit_code"},
        "status": status_payload,
        "exit_code": int(status.get("exit_code") or 0),
    }
    unknown = set(document) - MUTATION_FIELDS
    if unknown:
        fail(f"cold-storage mutation has unsupported fields {sorted(unknown)}")
    return document


def render_mutation(document: Mapping[str, Any], *, width: int | None = None) -> None:
    render_status(document["status"], width=width)


def _display_value(value: Any) -> str:
    if value in (None,):
        return "(absent)"
    if value == "":
        return "(empty)"
    return str(value)


def _health_summary(health: Mapping[str, Any]) -> str:
    if health.get("unsafe"):
        return health.get("unsafe_detail") or "unsafe"
    if not health.get("lexical_path"):
        return "no path"
    if not health.get("exists"):
        return "missing"
    if health.get("final_symlink"):
        return "symlink"
    if not health.get("directory"):
        return "not a directory"
    if not health.get("usable"):
        return "unavailable"
    return "usable"


def _job_count_summary(counts: Mapping[str, Any]) -> str:
    parts = [
        f"{name} {counts.get(name) or 0}"
        for name in ("pending", "running", "complete", "failed", "unavailable")
    ]
    return ", ".join(parts)


def _prefix_list(values: list[str]) -> str:
    if not values:
        return "none"
    return ", ".join(_id_prefix(item) for item in values)


def _print_json(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(model_identity.pretty_json_bytes(dict(value)))


def _emit(
    document: Mapping[str, Any],
    *,
    json_mode: bool,
    renderer: Callable[..., None],
) -> int:
    if json_mode:
        payload = {key: value for key, value in document.items() if key != "exit_code"}
        _print_json(payload)
    else:
        renderer(document)
    return int(document.get("exit_code") or 0)


def cmd_show(args: argparse.Namespace) -> int:
    status = show_status(lib_dir=_optional_path(args.library_dir))
    return _emit(status, json_mode=args.json, renderer=render_status)


def cmd_plan(args: argparse.Namespace) -> int:
    if args.disable and args.path:
        fail("plan cannot take --path and --disable together", exit_code=2)
    if not args.disable and not args.path:
        fail("plan --path is required", exit_code=2)
    plan = build_plan(
        requested=args.path or None,
        disable=bool(args.disable),
        lib_dir=_optional_path(args.library_dir),
    )
    return _emit(plan, json_mode=args.json, renderer=render_plan)


def cmd_render_plan(_args: argparse.Namespace) -> int:
    try:
        plan = json.load(sys.stdin)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"plan document is malformed: {exc}", exit_code=2)
    serialized_fields = PLAN_FIELDS - {"exit_code"}
    if not isinstance(plan, dict) or set(plan) != serialized_fields:
        fail("plan document fields are invalid", exit_code=2)
    identity_body = {
        key: value for key, value in plan.items() if key not in {"plan_id", "exit_code"}
    }
    if model_identity.canonical_json_digest(identity_body) != plan.get("plan_id"):
        fail("plan document identity is invalid", exit_code=2)
    render_plan(plan)
    return 0 if plan.get("action") in {"set-new", "keep"} else 1


def cmd_set(args: argparse.Namespace) -> int:
    if not args.yes:
        fail("set requires --yes", exit_code=2)
    if not args.path:
        fail("set requires --path", exit_code=2)
    result = write_dotenv_assignment(
        args.path,
        expected_plan_id=args.plan_id or None,
        lib_dir=_optional_path(args.library_dir),
    )
    status = show_status(lib_dir=_optional_path(args.library_dir))
    document = _mutation_document(result, status)
    return _emit(document, json_mode=args.json, renderer=render_mutation)


def cmd_disable(args: argparse.Namespace) -> int:
    if not args.yes:
        fail("disable requires --yes", exit_code=2)
    result = write_dotenv_assignment(
        "",
        disable=True,
        expected_plan_id=args.plan_id or None,
        lib_dir=_optional_path(args.library_dir),
    )
    status = show_status(lib_dir=_optional_path(args.library_dir))
    document = _mutation_document(result, status)
    return _emit(document, json_mode=args.json, renderer=render_mutation)


def cmd_archive_jobs(args: argparse.Namespace) -> int:
    document = list_archive_jobs_document(lib_dir=_optional_path(args.library_dir))
    return _emit(document, json_mode=args.json, renderer=render_jobs)


def cmd_retry_plan(args: argparse.Namespace) -> int:
    document = retry_plan(
        args.receipt,
        lib_dir=_optional_path(args.library_dir),
    )
    return _emit(document, json_mode=args.json, renderer=render_retry)


def cmd_persisted_state(_args: argparse.Namespace) -> int:
    persisted = persisted_assignment()
    sys.stdout.write(_assignment_state_label(persisted) + "\n")
    return 0


def cmd_effective_path(_args: argparse.Namespace) -> int:
    assignment = process_assignment()
    if assignment["state"] in {"absent", "empty"}:
        sys.stdout.write("\n")
        return 0
    sys.stdout.write(lexical_absolute(str(assignment["value"]), label=PREFERRED_KEY) + "\n")
    return 0


def _optional_path(value: str | None) -> pathlib.Path | None:
    if value:
        return pathlib.Path(value)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Configure explicit cold recovery storage"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show")
    show.add_argument("--json", action="store_true")
    show.add_argument("--library-dir", default="")
    show.set_defaults(func=cmd_show)
    plan = sub.add_parser("plan")
    plan.add_argument("--path", default="")
    plan.add_argument("--disable", action="store_true")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--library-dir", default="")
    plan.set_defaults(func=cmd_plan)
    render_plan_parser = sub.add_parser("render-plan")
    render_plan_parser.set_defaults(func=cmd_render_plan)
    set_p = sub.add_parser("set")
    set_p.add_argument("--path", required=True)
    set_p.add_argument("--yes", action="store_true")
    set_p.add_argument("--plan-id", default="")
    set_p.add_argument("--json", action="store_true")
    set_p.add_argument("--library-dir", default="")
    set_p.set_defaults(func=cmd_set)
    disable = sub.add_parser("disable")
    disable.add_argument("--yes", action="store_true")
    disable.add_argument("--plan-id", default="")
    disable.add_argument("--json", action="store_true")
    disable.add_argument("--library-dir", default="")
    disable.set_defaults(func=cmd_disable)
    jobs = sub.add_parser("archive-jobs")
    jobs.add_argument("--json", action="store_true")
    jobs.add_argument("--library-dir", default="")
    jobs.set_defaults(func=cmd_archive_jobs)
    retry = sub.add_parser("retry-plan")
    retry.add_argument("--receipt", required=True)
    retry.add_argument("--json", action="store_true")
    retry.add_argument("--library-dir", default="")
    retry.set_defaults(func=cmd_retry_plan)
    persisted = sub.add_parser("persisted-state")
    persisted.set_defaults(func=cmd_persisted_state)
    effective_path = sub.add_parser("effective-path")
    effective_path.set_defaults(func=cmd_effective_path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ColdStorageError as exc:
        print(f"cold-storage: {exc}", file=sys.stderr)
        return int(exc.exit_code)
    except cold_archive.ColdArchiveError as exc:
        print(f"cold-storage: {exc}", file=sys.stderr)
        return 1
    except source_attested.SourceAttestedAcquisitionError as exc:
        print(f"cold-storage: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
