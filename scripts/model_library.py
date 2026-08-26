#!/usr/bin/env python3
"""Model library: warm catalog + optional cold + working-copy staging.

Bash owns topology/SSH, model preparation/adoption, and operator entrypoints. This
module owns operational schemas, hub/flat completeness, labels, digests,
hot.json, disk budget, and cold-archive resolve (warm → cold → fail without fallback).
Trust-document schemas live in model_identity.py.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.util
import errno
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

try:
    from scripts import model_identity as _model_identity
except ModuleNotFoundError:
    try:
        import model_identity as _model_identity  # type: ignore[no-redef]
    except ModuleNotFoundError:
        # Remote inspection streams this file alone to nodes without a checkout.
        _model_identity = None  # type: ignore[assignment]

try:
    from scripts.terminal_format import TerminalWriter
except ModuleNotFoundError:
    try:
        from terminal_format import TerminalWriter
    except ModuleNotFoundError:
        # Remote schema/inspection commands stream this file without the repo.
        TerminalWriter = None  # type: ignore[assignment,misc]

SCHEMA_VERSION = 2
HOT_SCHEMA_VERSION = 3
HOT_WITNESS_SCHEMA_VERSION = 1
HOT_WITNESS_KIND = "pulsar-model-library-serve-witness"
HOT_WITNESS_SCHEME = "stat-witness-v1"
SNAPSHOT_MANIFEST_SCHEMA_VERSION = 1
SNAPSHOT_MANIFEST_KIND = "model-library-snapshot-manifest"
SNAPSHOT_INTEGRITY_SCHEME = "sha256-snapshot-manifest-v1"
HOME_REMOVAL_PLAN_SCHEMA_VERSION = 1
HOME_REMOVAL_PLAN_KIND = "pulsar-model-library-home-removal-plan"
HOME_REMOVAL_RESULT_KIND = "pulsar-model-library-home-removal-result"
HOME_ACQUISITION_SCHEMA_VERSION = 1
HOME_ACQUISITION_OBSERVATION_KIND = (
    "pulsar-model-library-home-acquisition-observation"
)
HOME_ACQUISITION_RECHECK_KIND = "pulsar-model-library-home-acquisition-recheck"
HOME_ACQUISITION_MIN_HEADROOM_BYTES = 5 * 1024**3
OWNED_HUB_STAGING_SCHEMA_VERSION = 1
OWNED_HUB_STAGING_KIND = "pulsar-model-library-owned-hub-staging"
LIVE_DIRECTORY_IDENTITY_SCHEMA_VERSION = 1
LIVE_DIRECTORY_IDENTITY_KIND = "pulsar-model-library-live-directory-identity"
RENAME_NOREPLACE = 1
# Hugging Face download (recorded file list) contracts live in
# model_library_receipt.py. They use a separate schema/kind and must
# not change this home-acquisition plan/result contract. This module does not
# import that planner; the Bash boundary composes these generic remote
# primitives with the separate schema owner.

HOT_BUDGET_SCHEMA_VERSION = 1
HOT_BUDGET_OBSERVATION_KIND = "pulsar-model-library-hot-budget-observation"
HOT_BUDGET_PLAN_KIND = "pulsar-model-library-hot-budget-plan"
HEALTH_SCHEMA_VERSION = 1
HEALTH_KIND = "pulsar-model-library-health"
HOT_HEALTH_SCAN_KIND = "pulsar-model-library-hot-health-scan"
LEGACY_HOT_SCHEMA_VERSIONS = {1, 2}
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
HF_MODEL_ID_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
# Exact Hugging Face snapshot commit. Download-receipt detach uses this form.
# Do not treat 41–64 hex as a bound home-removal identity.
HF_EXACT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
HUB_DIR_RE = re.compile(r"^models--(.+)$")
SAFE_REV = re.compile(r"^[A-Za-z0-9._-]+$")
UNBOUND_HOME_REVISIONS = frozenset({"", "unknown"})
COMPLETE_HOME_OCCUPANCY = "complete-home"
INCOMPLETE_HUB_OCCUPANCY = "incomplete-hub"
UNRECOGNIZED_HUB_OCCUPANCY = "unrecognized"
HF_HUB_LAYOUT_NAMES = frozenset({"refs", "snapshots", "blobs", ".no_exist", ".locks"})
SOURCE_ATTESTED_HOME_ATTACHMENT_STORE = "home-occupancy"
STATUS_TESTED = re.compile(r"^tested")
DEFAULT_HOT_ROOT = "/var/tmp/pulsar-hot"
DEFAULT_HOT_RESERVE_BYTES = 64 * 1024**3
DEFAULT_HOT_RESERVE_PERCENT = 5
DEFAULT_FABRIC_RAIL_INDEX = 0
ACTIVATE_TRANSPORT_BACKENDS = {
    "ssh-control": "copy",
    "ssh-roce": "copy",
}
# Site cold archive category dirs (org/name trees under each).
COLD_CATEGORY_DIRS = ("Official Models", "Community Models")
# Hub cache roots relative to cold root (in addition to cold_root/hub).
COLD_HUB_REL_PATHS = (
    "hub",
    ".cache/huggingface/hub",
    "huggingface/hub",
)


class ModelLibraryError(ValueError):
    """Operator-facing library error."""


def fail(message: str) -> None:
    raise ModelLibraryError(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def load_json(path: str | pathlib.Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")


def atomic_write_json(path: str | pathlib.Path, value: Any, mode: int = 0o600) -> None:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def canonical_json_digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _identity_module_required() -> Any:
    if _model_identity is None:
        fail(
            "validation identity commands require a repository checkout with "
            "scripts/model_identity.py"
        )
    return _model_identity


if _model_identity is None:
    def build_profile_contract(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return _identity_module_required().build_profile_contract(*args, **kwargs)


    def validate_profile_contract_document(value: Any) -> dict[str, Any]:
        return _identity_module_required().validate_profile_contract_document(value)
else:
    # Trust-document schemas have one owner. Remote physical inspection still
    # works when this file is streamed alone; trust commands require checkout.
    ModelLibraryError = _model_identity.ModelIdentityError
    fail = _model_identity.fail
    SNAPSHOT_MANIFEST_SCHEMA_VERSION = (
        _model_identity.SNAPSHOT_MANIFEST_SCHEMA_VERSION
    )
    SNAPSHOT_MANIFEST_KIND = _model_identity.SNAPSHOT_MANIFEST_KIND
    SNAPSHOT_INTEGRITY_SCHEME = _model_identity.SNAPSHOT_INTEGRITY_SCHEME
    SHA256_HEX_RE = _model_identity.SHA256_HEX_RE
    HF_COMMIT_RE = _model_identity.HF_COMMIT_RE
    HF_MODEL_ID_RE = _model_identity.HF_MODEL_ID_RE
    IMAGE_DIGEST_RE = _model_identity.IMAGE_DIGEST_RE
    SAFE_REV = _model_identity.SAFE_REV
    canonical_json_digest = _model_identity.canonical_json_digest
    build_profile_contract = _model_identity.build_profile_contract
    validate_profile_contract_document = (
        _model_identity.validate_profile_contract_document
    )


def _refuse_expected_model_seal(profile: str, reference: str | None) -> None:
    if reference:
        fail(
            f"{profile}: EXPECTED_MODEL_SEAL is retired (ADR 0012); "
            "expected-seal and schema-1 validation bundles are not a live product"
        )


def hub_dirname_to_model_id(dirname: str) -> str | None:
    match = HUB_DIR_RE.fullmatch(dirname)
    if not match:
        return None
    # models--org--name → org/name (HF uses -- between path segments)
    return match.group(1).replace("--", "/")


def model_id_to_hub_dirname(model_id: str) -> str:
    return "models--" + model_id.replace("/", "--")


def read_revision(hub_root: pathlib.Path) -> str | None:
    """Return mutable refs/main for legacy callers; never use it as file identity."""
    ref_path = hub_root / "refs" / "main"
    if not ref_path.is_file():
        return None
    try:
        rev = ref_path.read_text(encoding="utf-8").strip().replace("\r", "")
    except OSError:
        return None
    if not rev or not SAFE_REV.fullmatch(rev):
        return None
    if not (hub_root / "snapshots" / rev).is_dir():
        return None
    return rev


def hub_snapshot_state(hub_root: pathlib.Path, revision: str) -> str:
    """Return complete | partial | missing for one exact snapshot directory."""
    if not hub_root.is_dir():
        return "missing"
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        return "partial"
    snapshot = hub_root / "snapshots" / revision
    try:
        mode = snapshot.lstat().st_mode
    except OSError:
        return "missing"
    if not stat.S_ISDIR(mode) or _has_incomplete_marker(snapshot):
        return "partial"
    return weight_dir_state(snapshot)


def complete_snapshot_revisions(hub_root: pathlib.Path) -> list[str]:
    """List complete immutable snapshot directories without consulting refs/main."""
    snapshots = hub_root / "snapshots"
    try:
        children = sorted(snapshots.iterdir(), key=lambda item: item.name)
    except OSError:
        return []
    revisions: list[str] = []
    for child in children:
        if SAFE_REV.fullmatch(child.name) is None:
            continue
        if hub_snapshot_state(hub_root, child.name) == "complete":
            revisions.append(child.name)
    return revisions


def tree_bytes(path: pathlib.Path) -> int:
    """Sum regular-file sizes without following symlinks (HF blobs counted once)."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    st = (pathlib.Path(root) / name).lstat()
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode):
                    continue
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
    except OSError:
        return 0
    return total


def partition_blob_files(
    hub_path: str | pathlib.Path,
    *,
    streams: int,
) -> dict[str, Any]:
    """Greedily balance HF blob files across deterministic copy streams."""
    if streams < 1 or streams > 16:
        fail("copy streams must be between 1 and 16")
    hub = pathlib.Path(hub_path)
    blobs = hub / "blobs"
    if not blobs.is_dir():
        fail(f"hub blob directory is missing: {blobs}")

    files: list[tuple[int, str]] = []
    for path in sorted(blobs.rglob("*"), key=lambda item: item.as_posix()):
        try:
            st = path.lstat()
        except OSError as exc:
            fail(f"cannot inspect blob path {path}: {exc}")
        if stat.S_ISDIR(st.st_mode):
            continue
        if stat.S_ISLNK(st.st_mode):
            fail(f"blob tree contains a symlink: {path}")
        if not stat.S_ISREG(st.st_mode):
            fail(f"blob tree contains a non-regular file: {path}")
        relative = path.relative_to(hub).as_posix()
        if "\n" in relative or "\r" in relative:
            fail("blob path contains a line break")
        files.append((st.st_size, relative))
    if not files:
        fail(f"hub blob directory has no regular files: {blobs}")

    effective = min(streams, len(files))
    groups: list[dict[str, Any]] = [
        {"stream": index, "bytes": 0, "files": []}
        for index in range(effective)
    ]
    for size, relative in sorted(files, key=lambda item: (-item[0], item[1])):
        group = min(groups, key=lambda item: (item["bytes"], item["stream"]))
        group["files"].append(relative)
        group["bytes"] += size

    return {
        "schema_version": 1,
        "kind": "model-library-blob-stream-plan",
        "requested_streams": streams,
        "effective_streams": effective,
        "total_bytes": sum(size for size, _relative in files),
        "groups": groups,
    }


def _has_incomplete_marker(root: pathlib.Path) -> bool:
    try:
        for dirpath, _dirs, files in os.walk(root, followlinks=False):
            for name in files:
                if name.endswith(".incomplete"):
                    return True
    except OSError:
        return True
    return False


def weight_dir_state(weight_dir: pathlib.Path) -> str:
    """Return complete | partial for a directory holding config + weight files."""
    if not weight_dir.is_dir():
        return "partial"
    config = weight_dir / "config.json"
    if not config.is_file() or config.stat().st_size <= 0:
        found = False
        try:
            for path in weight_dir.rglob("config.json"):
                if path.is_file() and path.stat().st_size > 0:
                    found = True
                    weight_dir = path.parent
                    break
        except OSError:
            return "partial"
        if not found:
            return "partial"

    has_weight = False
    try:
        for path in weight_dir.iterdir():
            if not path.is_file():
                continue
            if path.suffix in {".safetensors", ".bin", ".gguf"} and path.stat().st_size > 0:
                has_weight = True
                break
    except OSError:
        return "partial"
    if not has_weight:
        return "partial"

    index_files = list(weight_dir.glob("*.index.json"))
    for index_path in index_files:
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            names = set((data.get("weight_map") or {}).values())
        except (OSError, ValueError, AttributeError, TypeError):
            return "partial"
        if not names:
            return "partial"
        for name in names:
            shard = index_path.parent / name
            if not shard.is_file() or shard.stat().st_size <= 0:
                return "partial"
    return "complete"


def hub_tree_state(hub_root: pathlib.Path) -> str:
    """Return legacy refs/main state; exact-commit paths use hub_snapshot_state()."""
    if not hub_root.is_dir():
        return "missing"
    revision = read_revision(hub_root)
    if revision is None:
        return "partial"
    return hub_snapshot_state(hub_root, revision)


def flat_tree_state(model_root: pathlib.Path) -> str:
    """Return complete | partial | missing for a flat (non-hub) model tree."""
    if not model_root.is_dir():
        return "missing"
    if _has_incomplete_marker(model_root):
        return "partial"
    return weight_dir_state(model_root)


def detect_model_layout(path: pathlib.Path) -> str | None:
    """Return 'hub' | 'flat' | None for a model directory."""
    if not path.is_dir():
        return None
    if (path / "refs").is_dir() and (path / "snapshots").is_dir():
        return "hub"
    if (path / "config.json").is_file():
        return "flat"
    try:
        for child in path.iterdir():
            if child.is_file() and child.name == "config.json":
                return "flat"
            if child.is_dir() and child.name == "snapshots":
                return "hub"
    except OSError:
        return None
    # Nested config under flat tree
    try:
        for cfg in path.rglob("config.json"):
            if cfg.is_file():
                # Prefer hub if snapshots exists at root
                if (path / "snapshots").is_dir():
                    return "hub"
                return "flat"
    except OSError:
        return None
    return None


def scan_hub_cache(
    cache_root: str | pathlib.Path,
    *,
    rank: int,
    node_id: str,
    hostname: str = "",
    ssh_host: str = "",
) -> list[dict[str, Any]]:
    """Scan HF_CACHE for hub/models--* trees on one node."""
    cache_root = pathlib.Path(cache_root).expanduser()
    hub = cache_root / "hub"
    homes: list[dict[str, Any]] = []
    if not hub.is_dir():
        return homes
    try:
        entries = sorted(hub.iterdir(), key=lambda p: p.name)
    except OSError:
        return homes
    for entry in entries:
        if not entry.is_dir():
            continue
        model_id = hub_dirname_to_model_id(entry.name)
        if model_id is None:
            continue
        active_revision = read_revision(entry)
        revisions = complete_snapshot_revisions(entry)
        if not revisions:
            revisions = [active_revision] if active_revision else [None]
        repository_bytes = tree_bytes(entry)
        for revision in revisions:
            state = (
                hub_snapshot_state(entry, revision)
                if revision is not None
                else "partial"
            )
            identity = (
                f"{model_id}@{revision}" if revision else f"{model_id}@unknown"
            )
            homes.append(
                {
                    "model_id": model_id,
                    "revision": revision,
                    "identity_key": identity,
                    "rank": rank,
                    "node_id": node_id,
                    "hostname": hostname,
                    "ssh_host": ssh_host,
                    "cache_root": str(cache_root),
                    "hub_path": str(entry),
                    "state": state,
                    "active": revision is not None and revision == active_revision,
                    "bytes": repository_bytes if state == "complete" else 0,
                }
            )
    return homes


# --- Optional cold storage archive ---


def configured_cold_root() -> str | None:
    """Return configured cold root, or None if cold tier is disabled.

    Precedence:
      1. PULSAR_COLD_ROOT — empty string disables cold explicitly
      2. MODELS_NFS — site default (often /mnt/Models)
    """
    if "PULSAR_COLD_ROOT" in os.environ:
        raw = os.environ["PULSAR_COLD_ROOT"].strip()
        if raw == "":
            return None
        return raw
    raw = os.environ.get("MODELS_NFS", "").strip()
    if raw == "":
        return None
    return raw


def resolve_cold_root(explicit: str | None = None) -> str | None:
    """Resolve cold root from explicit arg or environment."""
    if explicit is not None:
        value = explicit.strip()
        if value in {"", "-", "none", "None"}:
            return None
        return value
    return configured_cold_root()


def cold_root_status(root: str | pathlib.Path | None) -> dict[str, Any]:
    """Report whether cold is configured and readable."""
    if root is None or str(root).strip() == "":
        return {
            "configured": False,
            "available": False,
            "root": None,
            "reason": "cold tier disabled (PULSAR_COLD_ROOT empty or MODELS_NFS unset)",
        }
    path = pathlib.Path(root).expanduser()
    if not path.exists():
        return {
            "configured": True,
            "available": False,
            "root": str(path),
            "reason": f"cold root does not exist: {path}",
        }
    if not path.is_dir():
        return {
            "configured": True,
            "available": False,
            "root": str(path),
            "reason": f"cold root is not a directory: {path}",
        }
    try:
        next(path.iterdir(), None)
    except OSError as exc:
        return {
            "configured": True,
            "available": False,
            "root": str(path),
            "reason": f"cold root unreadable: {path}: {exc}",
        }
    return {
        "configured": True,
        "available": True,
        "root": str(path),
        "reason": None,
    }


def _cold_entry(
    *,
    model_id: str,
    path: pathlib.Path,
    layout: str,
    category: str = "",
    revision: str | None = None,
) -> dict[str, Any]:
    if layout == "hub":
        selected_revision = revision or read_revision(path)
        state = (
            hub_snapshot_state(path, selected_revision)
            if selected_revision is not None
            else hub_tree_state(path)
        )
        revision = selected_revision if state == "complete" else None
    else:
        state = flat_tree_state(path)
        revision = None
        if state == "complete":
            # Synthetic revision from inventory digest (stable for same bytes).
            revision = "cold-" + inventory_digest(path)[:12]
    identity = f"{model_id}@{revision}" if revision else f"{model_id}@unknown"
    return {
        "model_id": model_id,
        "revision": revision,
        "identity_key": identity,
        "path": str(path),
        "layout": layout,
        "category": category,
        "state": state,
        "bytes": tree_bytes(path) if state == "complete" else 0,
        "tier": "cold",
    }


def _scan_cold_hub_dir(hub_dir: pathlib.Path, *, category: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not hub_dir.is_dir():
        return out
    try:
        entries = sorted(hub_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for entry in entries:
        if not entry.is_dir():
            continue
        model_id = hub_dirname_to_model_id(entry.name)
        if model_id is None:
            continue
        revisions = complete_snapshot_revisions(entry)
        if revisions:
            for revision in revisions:
                out.append(
                    _cold_entry(
                        model_id=model_id,
                        path=entry,
                        layout="hub",
                        category=category,
                        revision=revision,
                    )
                )
        else:
            out.append(
                _cold_entry(
                    model_id=model_id,
                    path=entry,
                    layout="hub",
                    category=category,
                )
            )
    return out


def _scan_cold_category(category_dir: pathlib.Path, *, category: str) -> list[dict[str, Any]]:
    """Scan Official Models/org/name (or Community Models) flat trees."""
    out: list[dict[str, Any]] = []
    if not category_dir.is_dir():
        return out
    try:
        orgs = sorted(category_dir.iterdir(), key=lambda p: p.name)
    except OSError:
        return out
    for org_dir in orgs:
        if not org_dir.is_dir() or org_dir.name.startswith("."):
            continue
        try:
            models = sorted(org_dir.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for model_dir in models:
            if not model_dir.is_dir() or model_dir.name.startswith("."):
                continue
            layout = detect_model_layout(model_dir)
            if layout is None:
                continue
            model_id = f"{org_dir.name}/{model_dir.name}"
            out.append(
                _cold_entry(
                    model_id=model_id,
                    path=model_dir,
                    layout=layout,
                    category=category,
                )
            )
    return out


def scan_cold_archive(cold_root: str | pathlib.Path) -> list[dict[str, Any]]:
    """Scan optional cold archive for hub and Official/Community Models trees."""
    status = cold_root_status(cold_root)
    if not status["available"]:
        fail(status["reason"] or f"cold root unavailable: {cold_root}")
    root = pathlib.Path(status["root"])
    entries: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str | None]] = set()

    for rel in COLD_HUB_REL_PATHS:
        hub_dir = root / rel
        for item in _scan_cold_hub_dir(hub_dir, category=f"hub:{rel}"):
            key = (item["path"], item.get("revision"))
            if key in seen_identities:
                continue
            seen_identities.add(key)
            entries.append(item)

    for cat in COLD_CATEGORY_DIRS:
        for item in _scan_cold_category(root / cat, category=cat):
            key = (item["path"], item.get("revision"))
            if key in seen_identities:
                continue
            seen_identities.add(key)
            entries.append(item)

    entries.sort(key=lambda e: (e["model_id"], e["path"]))
    return entries


def find_cold_entry(
    cold_root: str | pathlib.Path,
    *,
    model_id: str | None = None,
    path: str | None = None,
    revision: str | None = None,
    require_complete: bool = True,
) -> dict[str, Any] | None:
    """Find one cold entry by absolute path or model_id (org/name)."""
    status = cold_root_status(cold_root)
    if not status["available"]:
        fail(status["reason"] or f"cold root unavailable: {cold_root}")
    root = pathlib.Path(status["root"]).resolve()

    if path:
        candidate = pathlib.Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = root / path
        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        # Allow path under cold root only (fail-closed boundary).
        try:
            candidate.relative_to(root)
        except ValueError:
            fail(f"cold path outside cold root: {candidate} (root={root})")
        layout = detect_model_layout(candidate)
        if layout is None:
            return None
        # Infer model_id from Official/Community layout or hub dirname.
        mid = model_id
        if mid is None:
            mid = _infer_model_id_from_cold_path(root, candidate, layout)
        if mid is None:
            fail(f"cold path: cannot infer model_id for {candidate}")
        entry = _cold_entry(
            model_id=mid,
            path=candidate,
            layout=layout,
            category="path",
            revision=revision,
        )
        if require_complete and entry["state"] != "complete":
            return None
        return entry

    if not model_id:
        fail("find_cold_entry requires model_id or path")

    # Prefer exact path matches under known layouts before full scan.
    candidates: list[pathlib.Path] = []
    hub_name = model_id_to_hub_dirname(model_id)
    for rel in COLD_HUB_REL_PATHS:
        candidates.append(root / rel / hub_name)
    if "/" in model_id:
        org, name = model_id.split("/", 1)
        for cat in COLD_CATEGORY_DIRS:
            candidates.append(root / cat / org / name)
        # Also try case-insensitive org/name under categories if exact miss.
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        layout = detect_model_layout(candidate)
        if layout is None:
            continue
        entry = _cold_entry(
            model_id=model_id,
            path=candidate,
            layout=layout,
            category="lookup",
            revision=revision,
        )
        if require_complete and entry["state"] != "complete":
            continue
        return entry

    # Fallback: scan and match model_id (case-sensitive first, then ci).
    scanned = scan_cold_archive(root)
    exact = [e for e in scanned if e["model_id"] == model_id]
    if not exact:
        lower = model_id.lower()
        exact = [e for e in scanned if e["model_id"].lower() == lower]
    if revision is not None:
        exact = [e for e in exact if e.get("revision") == revision]
    if require_complete:
        exact = [e for e in exact if e["state"] == "complete"]
    if not exact:
        return None
    # Prefer hub layout, then Official Models, then others.
    def rank(entry: dict[str, Any]) -> tuple[int, str]:
        layout_rank = 0 if entry.get("layout") == "hub" else 1
        cat = entry.get("category") or ""
        cat_rank = 0 if cat.startswith("Official") else 1
        return (layout_rank, f"{cat_rank}-{entry['path']}")

    exact.sort(key=rank)
    return exact[0]


def _infer_model_id_from_cold_path(
    root: pathlib.Path, path: pathlib.Path, layout: str
) -> str | None:
    if layout == "hub":
        return hub_dirname_to_model_id(path.name)
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    parts = rel.parts
    # Official Models/org/name or Community Models/org/name
    if len(parts) >= 3 and parts[0] in COLD_CATEGORY_DIRS:
        return f"{parts[1]}/{parts[2]}"
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


def model_id_from_profile_or_path(
    *,
    profile: str | None = None,
    model_id: str | None = None,
    models_dir: str | pathlib.Path | None = None,
    query: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (model_id, profile, absolute_path) from resolve inputs."""
    abs_path: str | None = None
    if query:
        if query.startswith("/"):
            abs_path = query
        elif "/" in query:
            model_id = model_id or query
        else:
            profile = profile or query
    if profile and models_dir:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        if conf.is_file():
            parsed = parse_profile_conf_any(conf)
            if parsed:
                if parsed.get("absolute_path"):
                    abs_path = parsed["absolute_path"]
                    model_id = model_id or parsed.get("model_id")
                else:
                    model_id = model_id or parsed.get("model_id")
    if model_id and model_id.startswith("/"):
        abs_path = model_id
        model_id = None
    return model_id, profile, abs_path


def parse_profile_conf_any(path: pathlib.Path) -> dict[str, Any] | None:
    """Parse conf for MODEL/STATUS/NODES; includes absolute-path models."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    model = None
    status = "?"
    nodes = 1
    expected_seal_ref = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("MODEL="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            model = value
        elif line.startswith("STATUS="):
            status = line.split("=", 1)[1].strip().strip("\"'")
        elif line.startswith("NODES="):
            try:
                nodes = int(line.split("=", 1)[1].strip())
            except ValueError:
                nodes = 1
        elif line.startswith("EXPECTED_MODEL_SEAL="):
            expected_seal_ref = line.split("=", 1)[1].strip().strip("\"'") or None
    if not model:
        return None
    absolute = model.startswith("/")
    model_id = None
    if absolute:
        # Infer org/name from Official Models/... when possible.
        parts = pathlib.Path(model).parts
        if "Official Models" in parts:
            idx = parts.index("Official Models")
            if len(parts) >= idx + 3:
                model_id = f"{parts[idx + 1]}/{parts[idx + 2]}"
        elif "Community Models" in parts:
            idx = parts.index("Community Models")
            if len(parts) >= idx + 3:
                model_id = f"{parts[idx + 1]}/{parts[idx + 2]}"
        if model_id is None and len(parts) >= 2:
            model_id = f"{parts[-2]}/{parts[-1]}"
    else:
        model_id = model
    _refuse_expected_model_seal(path.stem, expected_seal_ref)
    tested = bool(STATUS_TESTED.match(status))
    return {
        "profile": path.stem,
        "model_id": model_id,
        "absolute_path": model if absolute else None,
        "status": status,
        "nodes": nodes,
        "validated": tested,
        "expected_model_seal_ref": None,
        "expected_model_seal": None,
        "validation_bundle": None,
    }


def parse_profile_conf(path: pathlib.Path) -> dict[str, Any] | None:
    """Minimal shell conf parse for MODEL / STATUS / NODES (HF profiles only)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    model = None
    status = "?"
    nodes = 1
    expected_seal_ref = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("MODEL="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            model = value
        elif line.startswith("STATUS="):
            status = line.split("=", 1)[1].strip().strip("\"'")
        elif line.startswith("NODES="):
            try:
                nodes = int(line.split("=", 1)[1].strip())
            except ValueError:
                nodes = 1
        elif line.startswith("EXPECTED_MODEL_SEAL="):
            expected_seal_ref = line.split("=", 1)[1].strip().strip("\"'") or None
    if not model or model.startswith("/"):
        return None
    _refuse_expected_model_seal(path.stem, expected_seal_ref)
    tested = bool(STATUS_TESTED.match(status))
    return {
        "profile": path.stem,
        "model_id": model,
        "status": status,
        "nodes": nodes,
        "validated": tested,
        "expected_model_seal_ref": None,
        "expected_model_seal": None,
        "validation_bundle": None,
    }


def load_hf_profiles(models_dir: str | pathlib.Path) -> list[dict[str, Any]]:
    models_dir = pathlib.Path(models_dir)
    profiles: list[dict[str, Any]] = []
    if not models_dir.is_dir():
        return profiles
    for path in sorted(models_dir.glob("*.conf")):
        parsed = parse_profile_conf(path)
        if parsed is not None:
            profiles.append(parsed)
    return profiles


def load_hf_profile(
    models_dir: str | pathlib.Path,
    profile: str,
) -> dict[str, Any]:
    path = pathlib.Path(models_dir) / f"{profile}.conf"
    parsed = parse_profile_conf(path)
    if parsed is None:
        fail(f"{profile}: expected a Hugging Face model profile")
    return parsed


def load_model_profile(
    models_dir: str | pathlib.Path,
    profile: str,
) -> dict[str, Any]:
    """Load an HF or absolute-path profile for hot-state revalidation."""
    path = pathlib.Path(models_dir) / f"{profile}.conf"
    parsed = parse_profile_conf_any(path)
    if parsed is None:
        fail(f"{profile}: model profile is missing or invalid")
    return parsed


def observed_model_seal_projection(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = validate_snapshot_manifest(manifest)
    return {
        "model_id": manifest["model_id"],
        "snapshot_revision": manifest["snapshot_revision"],
        "manifest_id": manifest["manifest_id"],
    }


def compare_profile_expected_identity(
    profile: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Label observed bytes as receipt/occupancy identity (ADR 0012)."""
    if profile.get("expected_model_seal") is not None:
        fail(
            f"{profile.get('profile')}: EXPECTED_MODEL_SEAL is retired (ADR 0012)"
        )
    observed = observed_model_seal_projection(manifest)
    return {
        "identity_status": "receipt-occupancy",
        "expected_seal": None,
        "observed_seal": observed,
    }


def require_activation_identity(
    profile: dict[str, Any],
    manifest: dict[str, Any],
    *,
    allow_unvalidated: bool,
) -> dict[str, Any]:
    # Public --allow-unvalidated is refused (ADR 0008). This leftover keyword
    # cannot bypass identity checks; validation status is display-only.
    _ = allow_unvalidated
    validation = compare_profile_expected_identity(profile, manifest)
    return validation


def _catalog_entry(
    model_id: str,
    revision: str | None,
    identity_key: str,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "revision": revision,
        "identity_key": identity_key,
        "validation": "unvalidated",
        "profiles": [],
        "profile_validation": [],
        "homes": [],
        "duplicate": False,
    }


def _profile_catalog_status(
    profile: dict[str, Any],
    *,
    present: bool,
) -> str:
    if not present:
        return "missing"
    return "receipt-occupancy"


def normalize_primary_selections(value: Any) -> list[dict[str, str]]:
    """Validate persistent, exact-identity primary selections."""
    if value is None:
        return []
    if not isinstance(value, list):
        fail("catalog: primary_selections must be an array")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            fail(f"catalog: primary_selections[{index}] must be an object")
        unknown = sorted(
            set(item).difference({"identity_key", "node_id", "selected_at"})
        )
        if unknown:
            fail(
                f"catalog: primary_selections[{index}] has unsupported fields "
                f"{unknown}"
            )
        identity = item.get("identity_key")
        node_id = item.get("node_id")
        selected_at = item.get("selected_at")
        if not isinstance(identity, str) or "@" not in identity:
            fail(
                f"catalog: primary_selections[{index}] needs an exact identity_key"
            )
        model_id, revision = identity.rsplit("@", 1)
        if (
            HF_MODEL_ID_RE.fullmatch(model_id) is None
            or SAFE_REV.fullmatch(revision) is None
            or revision in {"missing", "unknown"}
        ):
            fail(
                f"catalog: primary_selections[{index}] identity is not an exact "
                "model revision"
            )
        if not isinstance(node_id, str) or not node_id:
            fail(f"catalog: primary_selections[{index}] needs node_id")
        if (
            not isinstance(selected_at, str)
            or UTC_TIMESTAMP_RE.fullmatch(selected_at) is None
        ):
            fail(
                f"catalog: primary_selections[{index}] selected_at must be a "
                "millisecond UTC timestamp"
            )
        if identity in seen:
            fail(f"catalog: duplicate primary selection for {identity}")
        seen.add(identity)
        normalized.append(
            {
                "identity_key": identity,
                "node_id": node_id,
                "selected_at": selected_at,
            }
        )
    return sorted(normalized, key=lambda item: item["identity_key"])


def policy_complete_homes(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Homes that count for resolve/primary.

    Download-receipt occupancy classification marks complete hub trees as
    ``occupancy`` or ``unbound-complete``. Only occupancy counts as a durable
    home. Unclassified entries keep the legacy complete-tree rule.
    """
    homes = [home for home in (entry.get("homes") or []) if isinstance(home, dict)]
    classified = [
        home
        for home in homes
        if home.get("home_class") in {"occupancy", "unbound-complete"}
    ]
    if classified:
        return [
            home
            for home in classified
            if home.get("home_class") == "occupancy" and home.get("state") == "complete"
        ]
    return [home for home in homes if home.get("state") == "complete"]


def unbound_complete_homes(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        home
        for home in (entry.get("homes") or [])
        if isinstance(home, dict) and home.get("home_class") == "unbound-complete"
    ]


def catalog_homes_are_classified(homes: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(home, dict)
        and home.get("home_class") in {"occupancy", "unbound-complete"}
        for home in homes
    )


def catalog_home_is_occupancy(
    home: dict[str, Any], *, classified: bool
) -> bool:
    """Occupancy-class complete homes, or legacy complete trees when unclassified."""
    if not isinstance(home, dict) or home.get("state") != "complete":
        return False
    if classified:
        return home.get("home_class") == "occupancy"
    return True


def _catalog_home_identity(home: dict[str, Any]) -> tuple[int, str, str]:
    try:
        rank = int(home.get("rank", -1))
    except (TypeError, ValueError):
        rank = -1
    return (rank, str(home.get("node_id") or ""), str(home.get("hub_path") or ""))


def _apply_entry_primary_policy(
    entry: dict[str, Any],
    selection: dict[str, str] | None,
) -> None:
    complete = policy_complete_homes(entry)
    for home in entry.get("homes") or []:
        home["primary"] = False

    if selection is not None:
        matches = [h for h in complete if h.get("node_id") == selection["node_id"]]
        if len(matches) > 1:
            fail(
                f"catalog: primary node {selection['node_id']} matches multiple homes "
                f"for {entry['identity_key']}"
            )
        if matches:
            matches[0]["primary"] = True
            status = "match"
        else:
            status = "stale"
        entry["primary_selection"] = {
            "mode": "explicit",
            "status": status,
            "node_id": selection["node_id"],
            "selected_at": selection["selected_at"],
        }
    elif len(complete) == 1:
        complete[0]["primary"] = True
        entry["primary_selection"] = {
            "mode": "automatic-single-home",
            "status": "match",
            "node_id": complete[0]["node_id"],
        }
    elif len(complete) > 1:
        entry["primary_selection"] = {
            "mode": "operator-required",
            "status": "missing",
        }
    else:
        entry["primary_selection"] = {
            "mode": "unavailable",
            "status": "missing",
        }
    entry["duplicate"] = len(complete) > 1
    entry["has_primary"] = any(h.get("primary") for h in complete)


def _catalog_selection_map(catalog: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        item["identity_key"]: item
        for item in normalize_primary_selections(catalog.get("primary_selections"))
    }


def _apply_catalog_primary_policies(catalog: dict[str, Any]) -> None:
    selections = _catalog_selection_map(catalog)
    for entry in catalog.get("models") or []:
        _apply_entry_primary_policy(entry, selections.get(entry.get("identity_key")))


def build_catalog(
    *,
    topology_id: str,
    homes: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    primary_overrides: dict[str, str] | None = None,
    primary_selections: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Merge scanned homes with exact profile/seal identity expectations."""
    primary_overrides = primary_overrides or {}
    selections = {
        item["identity_key"]: item
        for item in normalize_primary_selections(primary_selections)
    }
    refreshed_at = utc_now()
    by_identity: dict[str, dict[str, Any]] = {}

    for home in homes:
        model_id = home["model_id"]
        revision = home.get("revision") or "unknown"
        identity = home.get("identity_key") or f"{model_id}@{revision}"
        entry = by_identity.setdefault(
            identity,
            _catalog_entry(model_id, home.get("revision"), identity),
        )
        entry["homes"].append(
            {
                "rank": home["rank"],
                "node_id": home["node_id"],
                "hostname": home.get("hostname") or "",
                "ssh_host": home.get("ssh_host") or "",
                "cache_root": home["cache_root"],
                "hub_path": home["hub_path"],
                "state": home["state"],
                "active": bool(home.get("active")),
                "bytes": home.get("bytes") or 0,
                "primary": False,
            }
        )

    for profile in profiles:
        model_id = profile["model_id"]
        expected_revision = None
        model_targets = [
            entry for entry in by_identity.values() if entry["model_id"] == model_id
        ]
        if expected_revision is not None:
            targets = [
                entry
                for entry in model_targets
                if entry.get("revision") == expected_revision
            ]
        else:
            # Preserve the legacy experimental interpretation of refs/main when
            # it is unambiguous. Sealed profiles never enter this branch.
            targets = [
                entry
                for entry in model_targets
                if any(
                    home.get("active") and home.get("state") == "complete"
                    for home in entry.get("homes") or []
                )
            ]
            if not targets:
                complete_targets = [
                    entry
                    for entry in model_targets
                    if any(
                        home.get("state") == "complete"
                        for home in entry.get("homes") or []
                    )
                ]
                targets = (
                    complete_targets
                    if len(complete_targets) == 1
                    else model_targets
                )
        if not targets:
            revision = expected_revision
            suffix = revision or "missing"
            identity = f"{model_id}@{suffix}"
            entry = by_identity.setdefault(
                identity,
                _catalog_entry(model_id, revision, identity),
            )
            targets = [entry]

        for entry in targets:
            complete_present = any(
                home.get("state") == "complete" for home in entry.get("homes") or []
            )
            status = _profile_catalog_status(profile, present=complete_present)
            profile_state: dict[str, Any] = {
                "profile": profile["profile"],
                "profile_status": profile["status"],
                "identity_status": status,
                "expected_model_seal_ref": None,
                "expected_model_seal": None,
                "validation_bundle": None,
            }
            entry["profiles"].append(profile["profile"])
            entry["profile_validation"].append(profile_state)

    precedence = (
        "receipt-occupancy",
        "missing",
        "unvalidated",
    )
    models_out: list[dict[str, Any]] = []
    for identity in sorted(by_identity):
        entry = by_identity[identity]
        complete_homes = [h for h in entry["homes"] if h["state"] == "complete"]
        partial_homes = [h for h in entry["homes"] if h["state"] != "complete"]
        entry["homes"] = complete_homes + partial_homes
        entry["on_disk"] = bool(complete_homes)
        entry["duplicate"] = len(policy_complete_homes(entry)) > 1
        statuses = {
            item.get("identity_status") for item in entry["profile_validation"]
        }
        entry["validation"] = next(
            (candidate for candidate in precedence if candidate in statuses),
            "unvalidated",
        )
        entry["profiles"] = sorted(set(entry["profiles"]))
        entry["profile_validation"].sort(key=lambda item: item["profile"])

        override = primary_overrides.get(entry["identity_key"]) or primary_overrides.get(
            entry["model_id"]
        )
        if override:
            matches = [
                home
                for home in complete_homes
                if home["node_id"] == override or str(home["rank"]) == str(override)
            ]
            if len(matches) != 1:
                fail(
                    f"build: primary override {override!r} must match exactly one "
                    f"complete home for {entry['identity_key']}"
                )
            selections[entry["identity_key"]] = {
                "identity_key": entry["identity_key"],
                "node_id": matches[0]["node_id"],
                "selected_at": refreshed_at,
            }
        _apply_entry_primary_policy(entry, selections.get(entry["identity_key"]))
        models_out.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "refreshed_at": refreshed_at,
        "topology_id": topology_id,
        "primary_selections": sorted(
            selections.values(), key=lambda item: item["identity_key"]
        ),
        "models": models_out,
    }


def load_catalog(path: str | pathlib.Path) -> dict[str, Any]:
    data = load_json(path)
    if not isinstance(data, dict):
        fail(f"{path}: expected object")
    if data.get("schema_version") != SCHEMA_VERSION:
        fail(f"{path}: unsupported schema_version {data.get('schema_version')!r}")
    data["primary_selections"] = normalize_primary_selections(
        data.get("primary_selections")
    )
    if not isinstance(data.get("models"), list):
        fail(f"{path}: models must be an array")
    _apply_catalog_primary_policies(data)
    return data


def find_model_entry(
    catalog: dict[str, Any],
    *,
    model_id: str | None = None,
    profile: str | None = None,
    identity_key: str | None = None,
) -> dict[str, Any] | None:
    entries = catalog.get("models") or []
    if identity_key:
        return next(
            (entry for entry in entries if entry.get("identity_key") == identity_key),
            None,
        )
    if profile:
        matches = [entry for entry in entries if profile in (entry.get("profiles") or [])]
        if len(matches) > 1:
            fail(
                f"resolve: profile {profile} matches multiple revisions; "
                "select an exact model_id@commit"
            )
        return matches[0] if matches else None
    if model_id:
        complete = [
            entry
            for entry in entries
            if entry.get("model_id") == model_id
            and any(h.get("state") == "complete" for h in entry.get("homes") or [])
        ]
        if len(complete) > 1:
            fail(
                f"resolve: model {model_id} has multiple revisions; "
                "select an exact model_id@commit"
            )
        if complete:
            return complete[0]
        missing = [entry for entry in entries if entry.get("model_id") == model_id]
        if len(missing) == 1:
            return missing[0]
    return None


def find_catalog_query_entry(
    catalog: dict[str, Any],
    query: str,
    *,
    operation: str,
) -> dict[str, Any]:
    if "@" in query:
        entry = find_model_entry(catalog, identity_key=query)
    elif "/" in query:
        entry = find_model_entry(catalog, model_id=query)
    else:
        entry = find_model_entry(catalog, profile=query)
    if entry is None:
        fail(f"{operation}: no catalog entry matching {query!r}")
    return entry


def catalog_primary_records(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    present: set[str] = set()
    for entry in catalog.get("models") or []:
        identity = str(entry.get("identity_key") or "")
        present.add(identity)
        primary = next(
            (
                home
                for home in entry.get("homes") or []
                if home.get("state") == "complete" and home.get("primary")
            ),
            None,
        )
        records.append(
            {
                "identity_key": identity,
                "model_id": entry.get("model_id"),
                "duplicate": bool(entry.get("duplicate")),
                "complete_homes": sum(
                    1
                    for home in entry.get("homes") or []
                    if home.get("state") == "complete"
                ),
                "selection": entry.get("primary_selection") or {},
                "primary_home": (
                    {
                        "rank": primary.get("rank"),
                        "node_id": primary.get("node_id"),
                        "hostname": primary.get("hostname") or "",
                    }
                    if primary
                    else None
                ),
            }
        )
    for selection in normalize_primary_selections(catalog.get("primary_selections")):
        if selection["identity_key"] in present:
            continue
        records.append(
            {
                "identity_key": selection["identity_key"],
                "model_id": selection["identity_key"].rsplit("@", 1)[0],
                "duplicate": False,
                "complete_homes": 0,
                "selection": {
                    "mode": "explicit",
                    "status": "stale",
                    "node_id": selection["node_id"],
                    "selected_at": selection["selected_at"],
                },
                "primary_home": None,
            }
        )
    return sorted(records, key=lambda item: item["identity_key"])


def set_catalog_primary(
    catalog_path: str | pathlib.Path,
    query: str,
    node_selector: str,
    *,
    topology_id: str = "",
    topology_file: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    if topology_id and catalog.get("topology_id") != topology_id:
        fail("catalog primary: catalog topology is stale; run catalog refresh")
    entry = find_catalog_query_entry(catalog, query, operation="catalog primary")
    revision = entry.get("revision")
    if (
        not isinstance(revision, str)
        or SAFE_REV.fullmatch(revision) is None
        or revision in {"missing", "unknown"}
    ):
        fail("catalog primary: entry lacks an exact snapshot revision")
    complete = [
        home
        for home in entry.get("homes") or []
        if home.get("state") == "complete"
    ]
    matches = [
        home
        for home in complete
        if str(home.get("rank")) == node_selector
        or str(home.get("node_id") or "") == node_selector
    ]
    if len(matches) != 1:
        fail(
            "catalog primary: --node must match exactly one complete home "
            f"by rank or node ID (selector={node_selector!r})"
        )
    selected = matches[0]
    if topology_file:
        topology = load_topology_for_plan(topology_file)
        if topology.get("topology_id") != topology_id:
            fail("catalog primary: confirmed topology differs from controller topology")
        try:
            selected_rank = int(selected["rank"])
        except (KeyError, TypeError, ValueError):
            fail("catalog primary: selected catalog home has an invalid rank")
        if not isinstance(selected.get("node_id"), str) or not selected["node_id"]:
            fail("catalog primary: selected catalog home has an invalid node ID")
        topology_matches = [
            node
            for node in topology.get("nodes") or []
            if int(node.get("rank", -1)) == selected_rank
            and node.get("node_id") == selected["node_id"]
        ]
        if len(topology_matches) != 1:
            fail(
                "catalog primary: selected catalog home differs from confirmed "
                "rank/node identity; run catalog refresh"
            )
    selections = _catalog_selection_map(catalog)
    previous = selections.get(entry["identity_key"])
    changed = previous is None or previous["node_id"] != selected["node_id"]
    selected_at = utc_now() if changed else previous["selected_at"]
    selections[entry["identity_key"]] = {
        "identity_key": entry["identity_key"],
        "node_id": selected["node_id"],
        "selected_at": selected_at,
    }
    if changed:
        catalog["primary_selections"] = sorted(
            selections.values(), key=lambda item: item["identity_key"]
        )
        _apply_catalog_primary_policies(catalog)
        atomic_write_json(catalog_path, catalog)
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-primary-selection-result",
        "action": "set",
        "changed": changed,
        "identity_key": entry["identity_key"],
        "previous_node_id": previous["node_id"] if previous else None,
        "selection": selections[entry["identity_key"]],
        "home": {
            "rank": selected["rank"],
            "node_id": selected["node_id"],
            "hostname": selected.get("hostname") or "",
        },
    }


def clear_catalog_primary(
    catalog_path: str | pathlib.Path,
    query: str,
    *,
    topology_id: str = "",
) -> dict[str, Any]:
    catalog = load_catalog(catalog_path)
    if topology_id and catalog.get("topology_id") != topology_id:
        fail("catalog primary: catalog topology is stale; run catalog refresh")
    selections = _catalog_selection_map(catalog)
    entry: dict[str, Any] | None = None
    if "@" in query and query in selections:
        identity = query
        entry = find_model_entry(catalog, identity_key=query)
    else:
        entry = find_catalog_query_entry(catalog, query, operation="catalog primary")
        identity = entry["identity_key"]
    previous = selections.pop(identity, None)
    if previous is not None:
        catalog["primary_selections"] = sorted(
            selections.values(), key=lambda item: item["identity_key"]
        )
        _apply_catalog_primary_policies(catalog)
        atomic_write_json(catalog_path, catalog)
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-primary-selection-result",
        "action": "clear",
        "changed": previous is not None,
        "identity_key": identity,
        "previous_node_id": previous["node_id"] if previous else None,
        "selection": (
            entry.get("primary_selection")
            if entry is not None
            else {"mode": "unavailable", "status": "missing"}
        ),
    }


def resolve_entry(
    catalog: dict[str, Any] | None,
    *,
    model_id: str | None = None,
    profile: str | None = None,
    identity_key: str | None = None,
    absolute_path: str | None = None,
    cold_root: str | None | object = ...,
    models_dir: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    """Resolve warm primary home, then optional cold archive.

    cold_root:
      Ellipsis (default) — use configured env (PULSAR_COLD_ROOT / MODELS_NFS)
      None / "" / "none" — disable cold fall-through
      str — explicit cold root
    """
    # Profile conf supplies model identity and, when reviewed, the exact commit.
    profile_expected_revision = None
    if profile and models_dir:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        if conf.is_file():
            parsed = parse_profile_conf_any(conf)
            if parsed:
                model_id = model_id or parsed.get("model_id")
                absolute_path = absolute_path or parsed.get("absolute_path")
                _refuse_expected_model_seal(
                    parsed.get("profile") or profile,
                    parsed.get("expected_model_seal_ref"),
                )

    warm_error: str | None = None
    if catalog is not None and not absolute_path:
        entry = find_model_entry(
            catalog,
            model_id=model_id,
            profile=profile,
            identity_key=identity_key,
        )
        if entry is None:
            target = identity_key or profile or model_id or "?"
            warm_error = f"resolve: {target}: not found in warm catalog"
        else:
            complete = policy_complete_homes(entry)
            if (entry.get("primary_selection") or {}).get("status") == "stale":
                selection = entry["primary_selection"]
                fail(
                    f"resolve: {entry['model_id']}: selected primary "
                    f"{selection.get('node_id')} is stale; run catalog refresh, "
                    "then explicitly select a complete home"
                )
            if not complete:
                if unbound_complete_homes(entry):
                    fail(
                        f"resolve: {entry['model_id']}: complete tree is unbound; "
                        "occupy it with scripts/model-library.sh home relocate "
                        "--node RANK --yes after a live receipt rehash"
                    )
                warm_error = (
                    f"resolve: {entry['model_id']}: no complete warm home "
                    "(download/place weights, then catalog refresh)"
                )
            elif entry.get("duplicate") and not entry.get("has_primary"):
                # Fail closed — do not fall through to cold on ambiguous warm dups.
                fail(
                    f"resolve: {entry['model_id']}: duplicate complete homes without primary; "
                    "run: scripts/model-library.sh cleanup-recommend"
                )
            else:
                primary = next((h for h in complete if h.get("primary")), None)
                if primary is None:
                    fail(f"resolve: {entry['model_id']}: no primary home selected")
                profile_validation = next(
                    (
                        item
                        for item in entry.get("profile_validation") or []
                        if item.get("profile") == profile
                    ),
                    None,
                )
                return {
                    "model_id": entry["model_id"],
                    "revision": entry.get("revision"),
                    "identity_key": entry["identity_key"],
                    "validation": entry.get("validation"),
                    "identity_status": (
                        profile_validation.get("identity_status")
                        if profile_validation
                        else entry.get("validation")
                    ),
                    "profile_validation": profile_validation,
                    "profiles": entry.get("profiles") or [],
                    "home": primary,
                    "duplicate": bool(entry.get("duplicate")),
                    "tier": "warm",
                    "source_path": primary.get("hub_path"),
                    "layout": "hub",
                }
    elif catalog is None and not absolute_path and model_id is None and profile is None:
        fail("resolve: need catalog, model_id, profile, or absolute_path")

    # --- cold fall-through ---
    if cold_root is ...:
        root = configured_cold_root()
    else:
        root = resolve_cold_root(None if cold_root is None else str(cold_root))

    target = profile or model_id or absolute_path or "?"
    if root is None:
        if warm_error:
            fail(warm_error + "; cold tier not configured")
        if absolute_path:
            fail(
                f"resolve: absolute path {absolute_path} needs cold root "
                "(set PULSAR_COLD_ROOT or MODELS_NFS)"
            )
        fail(f"resolve: {target}: not in warm catalog; cold tier not configured")

    status = cold_root_status(root)
    if not status["available"]:
        # Fail only when cold is needed (warm miss or absolute path).
        fail(
            f"resolve: {target}: warm miss and cold unavailable "
            f"({status.get('reason') or root})"
        )

    cold: dict[str, Any] | None = None
    try:
        if absolute_path:
            cold = find_cold_entry(
                root,
                path=absolute_path,
                revision=profile_expected_revision,
                require_complete=True,
            )
        elif model_id:
            cold = find_cold_entry(
                root,
                model_id=model_id,
                revision=profile_expected_revision,
                require_complete=True,
            )
        elif profile and catalog is not None:
            # Profile known but no model_id — already tried warm via profile.
            entry = find_model_entry(catalog, profile=profile)
            if entry and entry.get("model_id"):
                cold = find_cold_entry(
                    root,
                    model_id=entry["model_id"],
                    revision=profile_expected_revision,
                    require_complete=True,
                )
    except ModelLibraryError:
        raise

    if cold is None:
        if warm_error:
            fail(warm_error + f"; not found complete in cold archive ({root})")
        fail(f"resolve: {target}: not found complete in cold archive ({root})")

    # Synthetic "home" so preparation/stage paths can treat cold like a source.
    cold_home = {
        "rank": -1,
        "node_id": "cold",
        "hostname": "",
        "ssh_host": "cold",
        "cache_root": str(root),
        "hub_path": cold["path"],
        "state": cold["state"],
        "bytes": cold.get("bytes") or 0,
        "primary": True,
        "tier": "cold",
        "layout": cold["layout"],
    }
    validation = "unvalidated"
    profiles: list[str] = []
    if catalog is not None:
        warm_entry = find_model_entry(
            catalog, model_id=cold["model_id"], profile=profile
        )
        if warm_entry:
            validation = warm_entry.get("validation") or validation
            profiles = warm_entry.get("profiles") or []
    if profile and profile not in profiles:
        profiles = list(profiles) + [profile]

    return {
        "model_id": cold["model_id"],
        "revision": cold.get("revision"),
        "identity_key": cold["identity_key"],
        "validation": validation,
        "profiles": profiles,
        "home": cold_home,
        "duplicate": False,
        "tier": "cold",
        "source_path": cold["path"],
        "layout": cold["layout"],
        "cold": cold,
    }


def cleanup_recommend(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for entry in catalog.get("models") or []:
        unbound = unbound_complete_homes(entry)
        if unbound and not entry.get("duplicate"):
            identity_arg = shlex.quote(entry["identity_key"])
            recommendations.append(
                {
                    "model_id": entry["model_id"],
                    "identity_key": entry["identity_key"],
                    "homes": [
                        {
                            "rank": home["rank"],
                            "node_id": home["node_id"],
                            "hostname": home.get("hostname") or "",
                            "bytes": home.get("bytes") or 0,
                            "hub_path": home.get("hub_path") or "",
                            "primary": False,
                            "home_class": "unbound-complete",
                        }
                        for home in unbound
                    ],
                    "selection_status": (
                        entry.get("primary_selection") or {}
                    ).get("status"),
                    "select_commands": [
                        (
                            "scripts/model-library.sh home relocate "
                            f"{identity_arg} --node {shlex.quote(str(home['rank']))} --yes"
                        )
                        for home in unbound
                    ],
                    "removal_commands": [
                        {
                            "rank": home["rank"],
                            "check": (
                                "scripts/model-library.sh home check "
                                f"{identity_arg} --node {shlex.quote(str(home['rank']))}"
                            ),
                            "remove": (
                                "scripts/model-library.sh home remove "
                                f"{identity_arg} --node {shlex.quote(str(home['rank']))} --yes"
                            ),
                        }
                        for home in unbound
                    ],
                    "action": (
                        "Occupy one complete tree with home relocate after a live "
                        "receipt rehash, or remove unbound complete trees. They "
                        "are not durable homes."
                    ),
                }
            )
            continue
        if not entry.get("duplicate"):
            continue
        complete = policy_complete_homes(entry)
        identity_arg = shlex.quote(entry["identity_key"])
        primary = next((h for h in complete if h.get("primary")), None)
        select_commands = []
        if primary is None:
            select_commands = [
                (
                    "scripts/model-library.sh catalog primary set "
                    f"{identity_arg} --node {shlex.quote(str(home['rank']))}"
                )
                for home in complete
            ]
        removal_commands = (
            [
                {
                    "rank": home["rank"],
                    "check": (
                        "scripts/model-library.sh home check "
                        f"{identity_arg} --node {shlex.quote(str(home['rank']))}"
                    ),
                    "remove": (
                        "scripts/model-library.sh home remove "
                        f"{identity_arg} --node {shlex.quote(str(home['rank']))} --yes"
                    ),
                }
                for home in complete
                if not home.get("primary")
            ]
            if primary is not None
            else []
        )
        recommendations.append(
            {
                "model_id": entry["model_id"],
                "identity_key": entry["identity_key"],
                "homes": [
                    {
                        "rank": h["rank"],
                        "node_id": h["node_id"],
                        "hostname": h.get("hostname") or "",
                        "bytes": h.get("bytes") or 0,
                        "hub_path": h.get("hub_path") or "",
                        "primary": bool(h.get("primary")),
                    }
                    for h in complete
                ],
                "selection_status": (
                    entry.get("primary_selection") or {}
                ).get("status"),
                "select_commands": select_commands,
                "removal_commands": removal_commands,
                "action": (
                    "Explicitly select one primary home. Inspect each non-primary "
                    "home, then remove it only with the separate confirmed command."
                ),
            }
        )
    return recommendations


def materialize_hub_tree(
    source: str | pathlib.Path,
    dest_hub: str | pathlib.Path,
    *,
    layout: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Copy cold/flat or hub source into a complete HF hub tree at dest_hub."""
    source = pathlib.Path(source)
    dest_hub = pathlib.Path(dest_hub)
    if not source.is_dir():
        fail(f"materialize: source missing: {source}")
    layout = layout or detect_model_layout(source)
    if layout is None:
        fail(f"materialize: cannot detect layout at {source}")

    if layout == "hub":
        rev = revision or read_revision(source)
        if not rev:
            fail(f"materialize: source hub has no revision: {source}")
        state = hub_snapshot_state(source, rev)
        if state != "complete":
            fail(f"materialize: source snapshot {rev} is {state}: {source}")
        if dest_hub.exists():
            shutil.rmtree(dest_hub)
        dest_hub.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest_hub, symlinks=False)
        if hub_snapshot_state(dest_hub, rev) != "complete":
            fail(f"materialize: dest snapshot {rev} incomplete after copy: {dest_hub}")
        return {
            "layout": "hub",
            "source": str(source),
            "dest_hub": str(dest_hub),
            "revision": rev,
            "bytes": tree_bytes(dest_hub),
        }

    # flat → hub snapshots/<rev>/
    state = flat_tree_state(source)
    if state != "complete":
        fail(f"materialize: source flat tree is {state}: {source}")
    rev = revision or ("cold-" + inventory_digest(source)[:12])
    if not SAFE_REV.fullmatch(rev):
        fail(f"materialize: invalid revision {rev!r}")
    if dest_hub.exists():
        shutil.rmtree(dest_hub)
    snap = dest_hub / "snapshots" / rev
    snap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, snap, symlinks=False)
    refs = dest_hub / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(rev + "\n", encoding="utf-8")
    if hub_tree_state(dest_hub) != "complete":
        fail(f"materialize: dest hub incomplete after flat import: {dest_hub}")
    return {
        "layout": "flat",
        "source": str(source),
        "dest_hub": str(dest_hub),
        "revision": rev,
        "bytes": tree_bytes(dest_hub),
    }


def plan_cold_adopt(
    *,
    cold_root: str | None = None,
    model_id: str | None = None,
    path: str | None = None,
    profile: str | None = None,
    models_dir: str | pathlib.Path | None = None,
    cache_root: str | pathlib.Path,
    catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Plan adopt: cold complete tree → durable warm HF hub home."""
    root = resolve_cold_root(cold_root) if cold_root else configured_cold_root()
    if root is None:
        fail("cold adopt: cold root not configured (PULSAR_COLD_ROOT or MODELS_NFS)")

    expected_revision = None
    if profile and models_dir:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        parsed = parse_profile_conf_any(conf) if conf.is_file() else None
        if parsed:
            model_id = model_id or parsed.get("model_id")
            path = path or parsed.get("absolute_path")
            _refuse_expected_model_seal(
                parsed.get("profile") or profile,
                parsed.get("expected_model_seal_ref"),
            )

    entry = find_cold_entry(
        root,
        model_id=model_id,
        path=path,
        revision=expected_revision,
        require_complete=True,
    )
    if entry is None:
        target = path or model_id or profile or "?"
        fail(f"cold adopt: no complete cold tree for {target}")

    cache_root = pathlib.Path(cache_root).expanduser()
    dest_hub = cache_root / "hub" / model_id_to_hub_dirname(entry["model_id"])
    existing_state = hub_tree_state(dest_hub) if dest_hub.exists() else "missing"
    return {
        "action": "adopt",
        "tier": "cold",
        "model_id": entry["model_id"],
        "identity_key": entry["identity_key"],
        "revision": entry.get("revision"),
        "layout": entry["layout"],
        "source_path": entry["path"],
        "cache_root": str(cache_root),
        "dest_hub": str(dest_hub),
        "bytes": entry.get("bytes") or 0,
        "existing_dest_state": existing_state,
        "note": (
            "Copies cold archive into a durable warm HF hub home. "
            "Run catalog refresh afterward to register the home."
        ),
    }


def execute_cold_adopt(plan: dict[str, Any]) -> dict[str, Any]:
    """Execute adopt plan on local filesystem (selftests / single-node)."""
    if plan.get("action") != "adopt":
        fail(f"execute_cold_adopt: unexpected action {plan.get('action')!r}")
    result = materialize_hub_tree(
        plan["source_path"],
        plan["dest_hub"],
        layout=plan.get("layout"),
        revision=plan.get("revision"),
    )
    return {
        **plan,
        "executed": True,
        "revision": result["revision"],
        "dest_bytes": result["bytes"],
        "dest_state": hub_snapshot_state(
            pathlib.Path(plan["dest_hub"]),
            result["revision"],
        ),
    }


def plan_cold_stage(
    *,
    cold_root: str | None = None,
    profile: str,
    topology_id: str,
    hot_root: str,
    model_id: str | None = None,
    absolute_path: str | None = None,
    catalog_path: str | None = None,
    models_dir: str | pathlib.Path | None = None,
    allow_unvalidated: bool = False,
    nodes: int | None = None,
) -> dict[str, Any]:
    """Plan stage-only: cold → hot (no durable warm home)."""
    root = resolve_cold_root(cold_root) if cold_root else configured_cold_root()
    if root is None:
        fail("cold stage-only: cold root not configured (PULSAR_COLD_ROOT or MODELS_NFS)")

    profile_data = None
    if profile and models_dir:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        profile_data = parse_profile_conf_any(conf) if conf.is_file() else None
        if profile_data:
            model_id = model_id or profile_data.get("model_id")
            absolute_path = absolute_path or profile_data.get("absolute_path")

    # Catalog may supply model_id for HF profiles, but never supplies trust.
    catalog = load_catalog(catalog_path) if catalog_path else None
    if catalog is not None:
        warm_entry = find_model_entry(catalog, model_id=model_id, profile=profile)
        if warm_entry:
            model_id = model_id or warm_entry.get("model_id")

    expected_revision = None
    if profile_data and profile_data.get("expected_model_seal"):
        expected_revision = profile_data["expected_model_seal"]["snapshot_revision"]
    entry = find_cold_entry(
        root,
        model_id=model_id,
        path=absolute_path,
        revision=expected_revision,
        require_complete=True,
    )
    if entry is None:
        target = absolute_path or model_id or profile or "?"
        fail(f"cold stage-only: no complete cold tree for {target}")

    source = pathlib.Path(entry["path"])
    layout = entry.get("layout") or detect_model_layout(source)
    if layout == "hub":
        revision = entry.get("revision")
        if not revision or hub_snapshot_state(source, revision) != "complete":
            fail(f"cold stage-only: selected source snapshot is incomplete: {source}")
    else:
        if flat_tree_state(source) != "complete":
            fail(f"cold stage-only: source flat incomplete: {source}")
    if layout == "hub":
        integrity_manifest = build_snapshot_manifest(
            source,
            model_id=entry["model_id"],
            revision=entry.get("revision"),
        )
    else:
        revision = entry.get("revision")
        if not revision:
            fail("cold stage-only: flat source has no stable revision")
        integrity_manifest = build_flat_snapshot_manifest(
            source,
            model_id=entry["model_id"],
            revision=revision,
        )
    if profile_data is None:
        fail("cold stage-only: model profile is required")
    validation = require_activation_identity(
        profile_data,
        integrity_manifest,
        allow_unvalidated=allow_unvalidated,
    )
    source_digest = integrity_manifest["manifest_id"]
    # Instance path is keyed by the exact snapshot identity.
    cid = hot_content_id(entry["identity_key"], source_digest, validation)
    bytes_logical = integrity_manifest["total_bytes"]
    instance = hot_instance_dir(hot_root, profile, topology_id, cid)
    hub_dest = hot_hub_path(instance, entry["model_id"])
    target_ranks = list(range(nodes if nodes is not None else 1))
    hot_storage_requirements = build_hot_storage_requirements(
        target_ranks=target_ranks,
        bytes_logical=bytes_logical,
        instance_dir=instance,
        home_rank=None,
    )

    if hot_stamp_path(instance).is_file():
        existing = load_hot_stamp(instance)
        same_source = (
            existing.get("source_content_digest") == source_digest
            or (
                layout == "hub"
                and existing.get("content_digest") == source_digest
            )
        )
        if (
            same_source
            and existing.get("identity_key") == entry["identity_key"]
            and existing.get("validation") == validation
            and existing.get("state") in {"ready", "pinned"}
        ):
            return {
                "action": "skip",
                "reason": "hot already ready with matching digest",
                "mode": "stage-only",
                "tier": "cold",
                "profile": profile,
                "model_id": entry["model_id"],
                "identity_key": entry["identity_key"],
                "revision": entry.get("revision"),
                "layout": layout,
                "source_path": str(source),
                "hot_root": hot_root,
                "instance_dir": str(instance),
                "hub_dest": str(hub_dest),
                "content_id": cid,
                "content_digest": existing.get("content_digest") or source_digest,
                "source_content_digest": source_digest,
                "integrity_manifest": integrity_manifest,
                "validation": validation,
                "bytes_logical": bytes_logical,
                "backend": "copy",
                "hot_storage_requirements": hot_storage_requirements,
                "stamp": existing,
            }

    # content_digest is finalized after materialize for flat→hub (paths change).
    # Hub-layout cold sources keep source_digest as the verify digest.
    provisional_digest = source_digest if layout == "hub" else source_digest
    stamp = build_hot_stamp(
        profile=profile,
        model_id=entry["model_id"],
        identity_key=entry["identity_key"],
        revision=entry.get("revision"),
        topology_id=topology_id,
        home_node_id="cold",
        content_id=cid,
        content_digest=provisional_digest,
        integrity_manifest=integrity_manifest,
        validation=validation,
        backend="copy",
        bytes_logical=bytes_logical,
    )
    stamp["tier"] = "cold"
    stamp["mode"] = "stage-only"
    stamp["source_path"] = str(source)
    stamp["layout"] = layout
    stamp["source_content_digest"] = source_digest
    return {
        "action": "stage-only",
        "mode": "stage-only",
        "tier": "cold",
        "profile": profile,
        "model_id": entry["model_id"],
        "identity_key": entry["identity_key"],
        "revision": entry.get("revision"),
        "layout": layout,
        "source_path": str(source),
        "home": {
            "rank": -1,
            "node_id": "cold",
            "hub_path": str(source),
            "state": entry["state"],
            "primary": True,
            "tier": "cold",
            "layout": layout,
        },
        "hot_root": hot_root,
        "instance_dir": str(instance),
        "hub_dest": str(hub_dest),
        "content_id": cid,
        "content_digest": provisional_digest,
        "source_content_digest": source_digest,
        "integrity_manifest": integrity_manifest,
        "validation": validation,
        "bytes_logical": bytes_logical,
        "backend": "copy",
        "target_ranks": target_ranks,
        "hot_storage_requirements": hot_storage_requirements,
        "stamp": stamp,
        "note": (
            "Stages cold → hot only; no durable warm home. "
            "Unpinned restart needs cold again unless you pin hot."
        ),
    }


# --- Hot staging (working set; not durable library) ---


def default_hot_root() -> str:
    return os.environ.get("PULSAR_HOT_ROOT") or DEFAULT_HOT_ROOT


def configured_hot_budget_bytes() -> int | None:
    raw = os.environ.get("PULSAR_HOT_BUDGET_BYTES")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        fail(f"PULSAR_HOT_BUDGET_BYTES must be an integer (got {raw!r})")
    if value < 1:
        fail("PULSAR_HOT_BUDGET_BYTES must be positive")
    return value


def configured_hot_reserve_bytes() -> int | None:
    raw = os.environ.get("PULSAR_HOT_RESERVE_BYTES")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        fail(f"PULSAR_HOT_RESERVE_BYTES must be an integer (got {raw!r})")
    if value < 0:
        fail("PULSAR_HOT_RESERVE_BYTES must be non-negative")
    return value


def inventory_digest(hub_path: str | pathlib.Path) -> str:
    """Content identity from relative paths + sizes (not full file SHA-256)."""
    root = pathlib.Path(hub_path)
    if not root.is_dir():
        fail(f"inventory_digest: not a directory: {root}")
    lines: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = pathlib.Path(dirpath).relative_to(root).as_posix()
        for name in sorted(filenames):
            path = pathlib.Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = name if rel_dir == "." else f"{rel_dir}/{name}"
            lines.append(f"{rel}\t{size}")
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()



def sha256_file(
    path: pathlib.Path,
    *,
    expected_size: int | None = None,
) -> str:
    """Hash one stable regular file and fail if it changes during the read."""
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if expected_size is not None and before.st_size != expected_size:
                fail(
                    f"integrity: size changed for {path} "
                    f"({before.st_size} != {expected_size})"
                )
            while chunk := handle.read(8 * 1024 * 1024):
                hasher.update(chunk)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        fail(f"integrity: cannot hash {path}: {exc}")
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in stable_fields):
        fail(f"integrity: file changed while hashing: {path}")
    return hasher.hexdigest()


def snapshot_manifest_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in manifest.items() if key != "manifest_id"}


def snapshot_manifest_id(manifest: dict[str, Any]) -> str:
    payload = json.dumps(
        snapshot_manifest_identity(manifest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolved_inside_file(path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        fail(f"integrity: path escapes or is unavailable: {path}: {exc}")
    if not resolved.is_file():
        fail(f"integrity: snapshot entry is not a regular file: {path}")
    return resolved


def iter_snapshot_files(
    hub_path: str | pathlib.Path,
    *,
    revision: str | None = None,
) -> tuple[str, list[tuple[str, pathlib.Path]]]:
    """Return one exact snapshot revision and its logical file set."""
    hub = pathlib.Path(hub_path)
    revision = revision or read_revision(hub)
    if revision is None:
        fail(f"integrity: hub has no selected snapshot revision: {hub}")
    state = hub_snapshot_state(hub, revision)
    if state != "complete":
        fail(f"integrity: snapshot {revision} is {state}: {hub}")
    snapshot = hub / "snapshots" / revision
    files: list[tuple[str, pathlib.Path]] = []
    try:
        candidates = sorted(snapshot.rglob("*"))
    except OSError as exc:
        fail(f"integrity: cannot walk snapshot {snapshot}: {exc}")
    for candidate in candidates:
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            fail(f"integrity: cannot inspect {candidate}: {exc}")
        if stat.S_ISDIR(mode):
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            fail(f"integrity: unsupported snapshot entry type: {candidate}")
        relative = candidate.relative_to(snapshot).as_posix()
        files.append((relative, _resolved_inside_file(candidate, hub)))
    if not files:
        fail(f"integrity: active snapshot has no files: {snapshot}")
    return revision, files


def _build_manifest_from_files(
    *,
    model_id: str,
    revision: str,
    files: list[tuple[str, pathlib.Path]],
    lfs_blob_root: pathlib.Path | None,
    allow_empty_files: bool = False,
) -> dict[str, Any]:
    if not model_id:
        fail("integrity: model_id is required")
    if not revision or SAFE_REV.fullmatch(revision) is None:
        fail(f"integrity: unsafe snapshot revision {revision!r}")
    resolved_lfs_root = (
        lfs_blob_root.resolve(strict=True)
        if lfs_blob_root is not None and lfs_blob_root.is_dir()
        else None
    )
    hash_cache: dict[pathlib.Path, str] = {}
    entries: list[dict[str, Any]] = []
    for relative, resolved in files:
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            fail(f"integrity: cannot stat {resolved}: {exc}")
        if size < 0 or (size == 0 and not allow_empty_files):
            fail(f"integrity: empty snapshot file: {relative}")
        if (
            resolved_lfs_root is not None
            and resolved.parent == resolved_lfs_root
            and SHA256_HEX_RE.fullmatch(resolved.name) is not None
        ):
            checksum = resolved.name
        else:
            checksum = hash_cache.get(resolved, "")
            if not checksum:
                checksum = sha256_file(resolved, expected_size=size)
                hash_cache[resolved] = checksum
        entries.append({"path": relative, "size": size, "sha256": checksum})
    manifest: dict[str, Any] = {
        "schema_version": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "kind": SNAPSHOT_MANIFEST_KIND,
        "model_id": model_id,
        "snapshot_revision": revision,
        "files": entries,
        "file_count": len(entries),
        "total_bytes": sum(item["size"] for item in entries),
    }
    manifest["manifest_id"] = snapshot_manifest_id(manifest)
    return manifest


def build_snapshot_manifest(
    hub_path: str | pathlib.Path,
    *,
    model_id: str,
    revision: str | None = None,
    allow_empty_files: bool = False,
) -> dict[str, Any]:
    hub = pathlib.Path(hub_path)
    revision, files = iter_snapshot_files(hub, revision=revision)
    return _build_manifest_from_files(
        model_id=model_id,
        revision=revision,
        files=files,
        lfs_blob_root=hub / "blobs",
        allow_empty_files=allow_empty_files,
    )


def inspect_snapshot_blob_identities(
    hub_path: str | pathlib.Path,
    *,
    model_id: str,
    revision: str | None = None,
    allow_empty_files: bool = True,
) -> dict[str, Any]:
    """Return SHA-256, Git blob IDs, and the snapshot manifest for one hub."""
    hub = pathlib.Path(hub_path)
    revision, files = iter_snapshot_files(hub, revision=revision)
    observed: list[dict[str, Any]] = []
    for relative, resolved in files:
        try:
            before = resolved.stat()
            sha256 = hashlib.sha256()
            git_oid = hashlib.sha1(
                f"blob {before.st_size}\0".encode("ascii")
            )
            with resolved.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    sha256.update(chunk)
                    git_oid.update(chunk)
                after = os.fstat(handle.fileno())
        except OSError as exc:
            fail(f"integrity: cannot read {resolved}: {exc}")
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
            fail(f"integrity: file changed while hashing: {resolved}")
        size = before.st_size
        if size < 0 or (size == 0 and not allow_empty_files):
            fail(f"integrity: empty snapshot file: {relative}")
        observed.append(
            {
                "path": relative,
                "size": size,
                "sha256": sha256.hexdigest(),
                "git_oid": git_oid.hexdigest(),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": SNAPSHOT_MANIFEST_SCHEMA_VERSION,
        "kind": SNAPSHOT_MANIFEST_KIND,
        "model_id": model_id,
        "snapshot_revision": revision,
        "files": [
            {key: item[key] for key in ("path", "size", "sha256")}
            for item in observed
        ],
        "file_count": len(observed),
        "total_bytes": sum(item["size"] for item in observed),
    }
    manifest["manifest_id"] = snapshot_manifest_id(manifest)
    validate_snapshot_manifest(manifest)
    return {
        "model_id": model_id,
        "snapshot_revision": revision,
        "files": observed,
        "manifest": manifest,
    }


def build_flat_snapshot_manifest(
    source_path: str | pathlib.Path,
    *,
    model_id: str,
    revision: str,
) -> dict[str, Any]:
    source = pathlib.Path(source_path)
    if not source.is_dir():
        fail(f"integrity: flat source is not a directory: {source}")
    files: list[tuple[str, pathlib.Path]] = []
    try:
        candidates = sorted(source.rglob("*"))
    except OSError as exc:
        fail(f"integrity: cannot walk flat source {source}: {exc}")
    for candidate in candidates:
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            fail(f"integrity: cannot inspect {candidate}: {exc}")
        if stat.S_ISDIR(mode):
            continue
        if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
            fail(f"integrity: unsupported flat entry type: {candidate}")
        relative = candidate.relative_to(source).as_posix()
        files.append((relative, _resolved_inside_file(candidate, source)))
    return _build_manifest_from_files(
        model_id=model_id,
        revision=revision,
        files=files,
        lfs_blob_root=None,
    )


def validate_snapshot_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        fail("integrity: snapshot manifest must be an object")
    if manifest.get("schema_version") != SNAPSHOT_MANIFEST_SCHEMA_VERSION:
        fail("integrity: unsupported snapshot manifest schema")
    if manifest.get("kind") != SNAPSHOT_MANIFEST_KIND:
        fail("integrity: snapshot manifest kind is invalid")
    model_id = manifest.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        fail("integrity: snapshot manifest model_id is invalid")
    revision = manifest.get("snapshot_revision")
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        fail("integrity: snapshot manifest revision is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        fail("integrity: snapshot manifest files must be non-empty")
    observed: set[str] = set()
    total = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            fail(f"integrity: manifest.files[{index}] must be an object")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            fail(f"integrity: manifest.files[{index}].path is invalid")
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in observed:
            fail(f"integrity: unsafe or duplicate manifest path: {relative}")
        observed.add(relative)
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            fail(f"integrity: invalid manifest size for {relative}")
        checksum = item.get("sha256")
        if not isinstance(checksum, str) or SHA256_HEX_RE.fullmatch(checksum) is None:
            fail(f"integrity: invalid SHA-256 for {relative}")
        total += size
    if manifest.get("file_count") != len(files):
        fail("integrity: snapshot manifest file_count mismatch")
    if manifest.get("total_bytes") != total:
        fail("integrity: snapshot manifest total_bytes mismatch")
    if manifest.get("manifest_id") != snapshot_manifest_id(manifest):
        fail("integrity: snapshot manifest identity mismatch")
    return manifest


def default_integrity_workers() -> int:
    raw = os.environ.get("PULSAR_INTEGRITY_WORKERS", "8")
    try:
        workers = int(raw)
    except ValueError:
        fail(f"PULSAR_INTEGRITY_WORKERS must be an integer (got {raw!r})")
    if workers < 1 or workers > 16:
        fail("PULSAR_INTEGRITY_WORKERS must be between 1 and 16")
    return workers


def verify_snapshot_manifest(
    hub_path: str | pathlib.Path,
    manifest: dict[str, Any],
    *,
    metadata_only: bool = False,
    workers: int | None = None,
) -> dict[str, Any]:
    manifest = validate_snapshot_manifest(manifest)
    hub = pathlib.Path(hub_path)
    revision, actual_files = iter_snapshot_files(
        hub,
        revision=manifest["snapshot_revision"],
    )
    if revision != manifest["snapshot_revision"]:
        fail(
            f"integrity: snapshot is {revision}, expected "
            f"{manifest['snapshot_revision']}"
        )
    actual = {relative: resolved for relative, resolved in actual_files}
    expected = {item["path"]: item for item in manifest["files"]}
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        fail(
            "integrity: snapshot file set changed "
            f"(missing={missing[:1]} extra={extra[:1]})"
        )
    unique_paths: dict[pathlib.Path, int] = {}
    for relative, item in expected.items():
        resolved = actual[relative]
        try:
            size = resolved.stat().st_size
        except OSError as exc:
            fail(f"integrity: cannot stat {relative}: {exc}")
        if size != item["size"]:
            fail(
                f"integrity: size changed for {relative} "
                f"({size} != {item['size']})"
            )
        unique_paths.setdefault(resolved, size)
    bytes_hashed = 0
    worker_count = 0
    if not metadata_only:
        worker_count = default_integrity_workers() if workers is None else workers
        if isinstance(worker_count, bool) or worker_count < 1 or worker_count > 16:
            fail("integrity workers must be between 1 and 16")
        items = sorted(unique_paths.items(), key=lambda item: str(item[0]))

        def hash_one(item: tuple[pathlib.Path, int]) -> tuple[pathlib.Path, str]:
            candidate, size = item
            return candidate, sha256_file(candidate, expected_size=size)

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            checksums = dict(pool.map(hash_one, items))
        for relative, item in expected.items():
            if checksums[actual[relative]] != item["sha256"]:
                fail(f"integrity: SHA-256 mismatch for {relative}")
        bytes_hashed = sum(unique_paths.values())
    return {
        "state": "ok",
        "mode": "metadata" if metadata_only else "full",
        "scheme": SNAPSHOT_INTEGRITY_SCHEME,
        "manifest_id": manifest["manifest_id"],
        "snapshot_revision": revision,
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "bytes_hashed": bytes_hashed,
        "workers": worker_count,
    }


def content_id_for(identity_key: str, digest: str) -> str:
    raw = f"{identity_key}|{digest}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def hot_content_id(
    identity_key: str,
    digest: str,
    validation: dict[str, Any],
) -> str:
    validation_key = validation.get("identity_status")
    if validation_key != "receipt-occupancy":
        fail("hot content identity lacks receipt/occupancy provenance")
    return content_id_for(f"{identity_key}|validation:{validation_key}", digest)


def hot_instance_dir(
    hot_root: str | pathlib.Path,
    profile: str,
    topology_id: str,
    content_id: str,
) -> pathlib.Path:
    topo12 = (topology_id or "notopology")[:12]
    return pathlib.Path(hot_root) / f"{profile}-{topo12}" / content_id


def hot_hub_path(instance_dir: pathlib.Path, model_id: str) -> pathlib.Path:
    return instance_dir / "hub" / model_id_to_hub_dirname(model_id)


def hot_stamp_path(instance_dir: pathlib.Path) -> pathlib.Path:
    return instance_dir / ".pulsar" / "hot.json"


def hot_witness_path(instance_dir: pathlib.Path) -> pathlib.Path:
    return instance_dir / ".pulsar" / "witness.json"


def resolve_activate_transport(
    backend: str | None,
    transport: str | None,
    *,
    nodes: int | None = None,
) -> tuple[str, str]:
    """Resolve copy backend and transfer transport.

    Multi-rank defaults to topology-bound ssh-roce (ADR 0003/0006). One-rank
    has no non-home copy and uses ssh-control. An explicit transport always
    wins, except ssh-roce on a one-rank profile which fails closed.
    """
    backend = backend or ""
    transport = transport or ""
    if backend and backend != "copy":
        fail(f"prepare: backend {backend!r} not supported (use copy)")
    if transport:
        if transport not in ACTIVATE_TRANSPORT_BACKENDS:
            choices = ", ".join(ACTIVATE_TRANSPORT_BACKENDS)
            fail(f"prepare: transport {transport!r} not supported (use {choices})")
        if nodes == 1 and transport == "ssh-roce":
            fail(
                "prepare: one-rank profiles have no non-home transfer; "
                "use ssh-control"
            )
        return "copy", transport
    if nodes is not None and nodes > 1:
        return "copy", "ssh-roce"
    return "copy", "ssh-control"


def classify_library_readiness(
    *,
    profile: str,
    catalog_path: str | pathlib.Path | None,
    topology_id: str,
    models_dir: str | pathlib.Path,
    selected_rank: int | None = None,
    selected_node_id: str | None = None,
) -> dict[str, Any]:
    """Classify why library views are not ready, without restaging."""
    refresh = "scripts/model-library.sh catalog refresh"
    prepare = f"scripts/model-library.sh prepare {profile} --yes"
    unsealed_add = (
        f"scripts/model-library.sh home add {profile} --revision <selector> --plan && "
        f"scripts/model-library.sh home add {profile} --revision <exact-commit> --yes"
    )
    cleanup = "scripts/model-library.sh cleanup-recommend"
    primary_set = (
        f"scripts/model-library.sh catalog primary set {profile} --node RANK"
    )
    catalog_file = pathlib.Path(catalog_path) if catalog_path else None
    if catalog_file is None or not catalog_file.is_file():
        return {
            "reason": "catalog-missing",
            "remediation": refresh,
            "detail": "no distributed catalog is present",
        }
    try:
        catalog = load_catalog(catalog_file)
    except ModelLibraryError as exc:
        return {
            "reason": "catalog-missing",
            "remediation": refresh,
            "detail": str(exc),
        }
    catalog_topology = catalog.get("topology_id") or ""
    if catalog_topology and topology_id and catalog_topology != topology_id:
        return {
            "reason": "catalog-stale",
            "remediation": refresh,
            "detail": "catalog topology does not match the confirmed topology",
        }
    try:
        resolved = resolve_entry(
            catalog,
            profile=profile,
            cold_root=None,
            models_dir=models_dir,
        )
    except ModelLibraryError as exc:
        text = str(exc)
        if "not found in warm catalog" in text or "no complete warm home" in text:
            return {
                "reason": "no-home",
                "remediation": unsealed_add,
                "detail": text,
            }
        if "duplicate complete homes without primary" in text:
            return {
                "reason": "duplicate-home",
                "remediation": cleanup,
                "detail": text,
            }
        if "complete tree is unbound" in text:
            return {
                "reason": "occupancy-missing",
                "remediation": f"scripts/model-library.sh home relocate {profile} --node RANK --yes",
                "detail": text,
            }
        if "no primary home selected" in text:
            return {
                "reason": "primary-unset",
                "remediation": primary_set,
                "detail": text,
            }
        if "stale" in text:
            return {
                "reason": "catalog-stale",
                "remediation": f"{refresh} && {primary_set}",
                "detail": text,
            }
        return {
            "reason": "catalog-unresolved",
            "remediation": refresh,
            "detail": text,
        }
    home = resolved.get("home") or {}
    home_rank = home.get("rank")
    home_node_id = home.get("node_id") or ""
    selected_node = (selected_node_id or "").strip()
    placement_mismatch = False
    if (
        selected_rank is not None
        and isinstance(home_rank, int)
        and not isinstance(home_rank, bool)
        and selected_rank != home_rank
    ):
        placement_mismatch = True
    elif selected_node and home_node_id and selected_node != home_node_id:
        placement_mismatch = True
    if placement_mismatch:
        target = home_node_id or (
            str(home_rank) if home_rank is not None else "HOME"
        )
        return {
            "reason": "wrong-placement",
            "remediation": f"scripts/check-weights.sh {profile} --node {target}",
            "detail": (
                "one-node serving must use the durable-home node "
                f"(rank {home_rank}, {home_node_id or 'unknown'}); "
                f"selected rank {selected_rank} is not that home. "
                "Do not prepare onto a non-home rank"
            ),
        }
    return {
        "reason": "views-missing",
        "remediation": prepare,
        "detail": "durable home is present; prepared runtime views are not ready",
    }


def cmd_classify_library_readiness(args: argparse.Namespace) -> int:
    report = classify_library_readiness(
        profile=args.profile,
        catalog_path=args.catalog,
        topology_id=args.topology_id,
        models_dir=args.models_dir,
        selected_rank=args.selected_rank,
        selected_node_id=args.selected_node_id or None,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def validate_hot_validation(
    validation: Any,
    *,
    profile: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(validation, dict) or set(validation) != {
        "identity_status",
        "expected_seal",
        "observed_seal",
    }:
        fail("hot validation provenance is invalid")
    if not isinstance(profile, str) or not profile:
        fail("hot validation profile is invalid")
    status = validation.get("identity_status")
    if status != "receipt-occupancy":
        fail(f"hot identity status is unsupported: {status!r}")
    observed = validation.get("observed_seal")
    expected_observed = observed_model_seal_projection(manifest)
    if observed != expected_observed:
        fail("hot observed identity differs from integrity manifest")
    if validation.get("expected_seal") is not None:
        fail(f"hot {status} state must not carry an expected seal")
    return validation


def hot_witness_observation(witness: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in witness.items()
        if key not in {"verified_at", "witness_id"}
    }


def hot_witness_id(witness: dict[str, Any]) -> str:
    return canonical_json_digest(
        {key: value for key, value in witness.items() if key != "witness_id"}
    )


def _directory_filesystem_identity(
    path: pathlib.Path,
    *,
    label: str,
) -> dict[str, int]:
    try:
        metadata = path.stat()
    except OSError as exc:
        fail(f"witness: cannot stat {label} {path}: {exc}")
    if not stat.S_ISDIR(metadata.st_mode):
        fail(f"witness: {label} is not a directory: {path}")
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def build_hot_witness_observation(
    stamp: dict[str, Any],
    *,
    hub: pathlib.Path,
    manifest: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    # Capture rank-local metadata that may accelerate a later launch.
    manifest = validate_snapshot_manifest(manifest)
    validation = validate_hot_validation(
        validation,
        profile=str(stamp.get("profile") or ""),
        manifest=manifest,
    )
    revision, actual_files = iter_snapshot_files(
        hub,
        revision=manifest["snapshot_revision"],
    )
    expected = {item["path"]: item for item in manifest["files"]}
    actual = {relative: resolved for relative, resolved in actual_files}
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        fail(
            "witness: snapshot file set changed "
            f"(missing={missing[:1]} extra={extra[:1]})"
        )

    try:
        canonical_hub = hub.resolve(strict=True)
        logical_snapshot = hub / "snapshots" / revision
        canonical_snapshot = logical_snapshot.resolve(strict=True)
        canonical_snapshot.relative_to(canonical_hub)
    except (OSError, ValueError) as exc:
        fail(f"witness: canonical runtime view is unavailable or escapes: {exc}")

    files: list[dict[str, Any]] = []
    for relative in sorted(expected):
        resolved = actual[relative]
        try:
            metadata = resolved.stat()
        except OSError as exc:
            fail(f"witness: cannot stat {relative}: {exc}")
        if not stat.S_ISREG(metadata.st_mode):
            fail(f"witness: snapshot entry is not a regular file: {relative}")
        if metadata.st_size != expected[relative]["size"]:
            fail(
                f"witness: size changed for {relative} "
                f"({metadata.st_size} != {expected[relative]['size']})"
            )
        files.append(
            {
                "path": relative,
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "size": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
                "ctime_ns": metadata.st_ctime_ns,
            }
        )

    observation: dict[str, Any] = {
        "schema_version": HOT_WITNESS_SCHEMA_VERSION,
        "kind": HOT_WITNESS_KIND,
        "scheme": HOT_WITNESS_SCHEME,
        "profile": stamp["profile"],
        "model_id": stamp["model_id"],
        "snapshot_revision": revision,
        "topology_id": stamp["topology_id"],
        "home_node_id": stamp["home_node_id"],
        "content_id": stamp["content_id"],
        "manifest_id": manifest["manifest_id"],
        "validation": validation,
        "view": {
            "hub": {
                "logical_path": str(hub.absolute()),
                "canonical_path": str(canonical_hub),
                **_directory_filesystem_identity(
                    canonical_hub,
                    label="canonical hub",
                ),
            },
            "snapshot": {
                "logical_path": str(logical_snapshot.absolute()),
                "canonical_path": str(canonical_snapshot),
                **_directory_filesystem_identity(
                    canonical_snapshot,
                    label="canonical snapshot",
                ),
            },
        },
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
    }
    if observation["file_count"] != manifest["file_count"]:
        fail("witness: file_count differs from sealed manifest")
    if observation["total_bytes"] != manifest["total_bytes"]:
        fail("witness: total_bytes differs from sealed manifest")
    return observation


def build_stable_hot_witness_observation(
    stamp: dict[str, Any],
    *,
    hub: pathlib.Path,
    manifest: dict[str, Any],
    validation: dict[str, Any],
) -> dict[str, Any]:
    first = build_hot_witness_observation(
        stamp,
        hub=hub,
        manifest=manifest,
        validation=validation,
    )
    second = build_hot_witness_observation(
        stamp,
        hub=hub,
        manifest=manifest,
        validation=validation,
    )
    if first != second:
        fail("witness: runtime metadata changed during observation")
    return second


def finalize_hot_witness(observation: dict[str, Any]) -> dict[str, Any]:
    witness = {
        **observation,
        "verified_at": utc_now(),
    }
    witness["witness_id"] = hot_witness_id(witness)
    return witness


def validate_hot_witness(witness: Any) -> dict[str, Any]:
    if not isinstance(witness, dict):
        fail("witness: document must be an object")
    required = {
        "schema_version",
        "kind",
        "scheme",
        "profile",
        "model_id",
        "snapshot_revision",
        "topology_id",
        "home_node_id",
        "content_id",
        "manifest_id",
        "validation",
        "view",
        "files",
        "file_count",
        "total_bytes",
        "verified_at",
        "witness_id",
    }
    if set(witness) != required:
        missing = sorted(required - set(witness))
        extra = sorted(set(witness) - required)
        fail(f"witness: fields differ (missing={missing}, extra={extra})")
    if witness.get("schema_version") != HOT_WITNESS_SCHEMA_VERSION:
        fail("witness: unsupported schema_version")
    if witness.get("kind") != HOT_WITNESS_KIND:
        fail("witness: kind is invalid")
    if witness.get("scheme") != HOT_WITNESS_SCHEME:
        fail("witness: scheme is unsupported")
    for field in (
        "profile",
        "model_id",
        "snapshot_revision",
        "topology_id",
        "home_node_id",
        "content_id",
    ):
        if not isinstance(witness.get(field), str) or not witness[field]:
            fail(f"witness: {field} is invalid")
    if SAFE_REV.fullmatch(witness["snapshot_revision"]) is None:
        fail("witness: snapshot_revision is unsafe")
    if (
        not isinstance(witness.get("manifest_id"), str)
        or SHA256_HEX_RE.fullmatch(witness["manifest_id"]) is None
    ):
        fail("witness: manifest_id is invalid")
    validation = witness.get("validation")
    if not isinstance(validation, dict) or set(validation) != {
        "identity_status",
        "expected_seal",
        "observed_seal",
    }:
        fail("witness: validation provenance is invalid")

    view = witness.get("view")
    if not isinstance(view, dict) or set(view) != {"hub", "snapshot"}:
        fail("witness: view is invalid")
    view_fields = {"logical_path", "canonical_path", "device", "inode"}
    for label in ("hub", "snapshot"):
        item = view.get(label)
        if not isinstance(item, dict) or set(item) != view_fields:
            fail(f"witness: {label} view is invalid")
        for path_field in ("logical_path", "canonical_path"):
            value = item.get(path_field)
            if not isinstance(value, str) or not pathlib.Path(value).is_absolute():
                fail(f"witness: {label} {path_field} is invalid")
        for number_field in ("device", "inode"):
            value = item.get(number_field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                fail(f"witness: {label} {number_field} is invalid")

    files = witness.get("files")
    if not isinstance(files, list) or not files:
        fail("witness: files must be a non-empty list")
    file_fields = {"path", "device", "inode", "size", "mtime_ns", "ctime_ns"}
    observed_paths: list[str] = []
    total_bytes = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict) or set(item) != file_fields:
            fail(f"witness: files[{index}] is invalid")
        relative = item.get("path")
        if not isinstance(relative, str) or not relative:
            fail(f"witness: files[{index}].path is invalid")
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in observed_paths:
            fail(f"witness: unsafe or duplicate file path: {relative}")
        observed_paths.append(relative)
        for number_field in ("device", "inode", "size", "mtime_ns", "ctime_ns"):
            value = item.get(number_field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                fail(f"witness: invalid {number_field} for {relative}")
        # A complete snapshot may legitimately contain tracked zero-byte files;
        # the manifest and full hash still bind those entries exactly.
        total_bytes += item["size"]
    if observed_paths != sorted(observed_paths):
        fail("witness: files are not sorted")
    if witness.get("file_count") != len(files):
        fail("witness: file_count mismatch")
    if witness.get("total_bytes") != total_bytes:
        fail("witness: total_bytes mismatch")

    verified_at = witness.get("verified_at")
    if not isinstance(verified_at, str) or not verified_at.endswith("Z"):
        fail("witness: verified_at must be an RFC3339 UTC timestamp")
    try:
        parsed_verified_at = datetime.fromisoformat(
            verified_at[:-1] + "+00:00"
        )
    except ValueError:
        fail("witness: verified_at must be an RFC3339 UTC timestamp")
    if parsed_verified_at.tzinfo is None:
        fail("witness: verified_at must include UTC")
    witness_id = witness.get("witness_id")
    if (
        not isinstance(witness_id, str)
        or SHA256_HEX_RE.fullmatch(witness_id) is None
        or witness_id != hot_witness_id(witness)
    ):
        fail("witness: identity mismatch")
    return witness


def write_hot_witness(
    instance_dir: str | pathlib.Path,
    witness: dict[str, Any],
) -> pathlib.Path:
    witness = validate_hot_witness(witness)
    path = hot_witness_path(pathlib.Path(instance_dir))
    atomic_write_json(path, witness)
    return path


def load_hot_witness(instance_dir: str | pathlib.Path) -> dict[str, Any]:
    path = hot_witness_path(pathlib.Path(instance_dir))
    if not path.is_file():
        fail(f"witness: missing: {path}")
    return validate_hot_witness(load_json(path))


def _witness_result(
    witness: dict[str, Any],
    *,
    status: str,
    reason: str,
    path: pathlib.Path,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "scheme": HOT_WITNESS_SCHEME,
        "path": str(path),
        "witness_id": witness["witness_id"],
        "verified_at": witness["verified_at"],
    }


def _witness_integrity_result(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "ok",
        "mode": "witness",
        "scheme": SNAPSHOT_INTEGRITY_SCHEME,
        "manifest_id": manifest["manifest_id"],
        "snapshot_revision": manifest["snapshot_revision"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "bytes_hashed": 0,
        "workers": 0,
    }


def full_verify_and_refresh_hot_witness(
    instance_dir: str | pathlib.Path,
    stamp: dict[str, Any],
    *,
    hub: pathlib.Path,
    manifest: dict[str, Any],
    validation: dict[str, Any],
    workers: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    before = build_stable_hot_witness_observation(
        stamp,
        hub=hub,
        manifest=manifest,
        validation=validation,
    )
    verification = verify_snapshot_manifest(
        hub,
        manifest,
        metadata_only=False,
        workers=workers,
    )
    after = build_stable_hot_witness_observation(
        stamp,
        hub=hub,
        manifest=manifest,
        validation=validation,
    )
    if before != after:
        fail("witness: runtime metadata changed during full verification")
    witness = finalize_hot_witness(after)
    write_hot_witness(instance_dir, witness)
    return verification, witness


def build_hot_stamp(
    *,
    profile: str,
    model_id: str,
    identity_key: str,
    revision: str | None,
    topology_id: str,
    home_node_id: str,
    content_id: str,
    content_digest: str,
    integrity_manifest: dict[str, Any],
    validation: dict[str, Any],
    backend: str,
    bytes_logical: int,
    transport: str | None = None,
    pinned: bool = False,
    state: str = "ready",
) -> dict[str, Any]:
    manifest = validate_snapshot_manifest(integrity_manifest)
    if manifest.get("manifest_id") != content_digest:
        fail("hot stamp content_digest differs from integrity manifest")
    if manifest.get("model_id") != model_id:
        fail("hot stamp model_id differs from integrity manifest")
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        fail("hot stamp revision is invalid")
    if manifest.get("snapshot_revision") != revision:
        fail("hot stamp revision differs from integrity manifest")
    if manifest.get("total_bytes") != bytes_logical:
        fail("hot stamp bytes_logical differs from integrity manifest")
    validation = validate_hot_validation(
        validation,
        profile=profile,
        manifest=manifest,
    )
    stamp = {
        "schema_version": HOT_SCHEMA_VERSION,
        "state": state,
        "profile": profile,
        "model_id": model_id,
        "revision": revision,
        "identity_key": identity_key,
        "home_node_id": home_node_id,
        "topology_id": topology_id,
        "content_id": content_id,
        "content_digest": content_digest,
        "integrity": {
            "scheme": SNAPSHOT_INTEGRITY_SCHEME,
            "manifest": manifest,
        },
        "validation": validation,
        "backend": backend,
        "bytes_logical": bytes_logical,
        "activated_at": utc_now(),
        "pinned": pinned,
        "budget_bytes_accounted": bytes_logical,
    }
    if transport:
        stamp["transport"] = transport
    return stamp


def write_hot_stamp(instance_dir: pathlib.Path, stamp: dict[str, Any]) -> pathlib.Path:
    path = hot_stamp_path(instance_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, stamp)
    return path


def load_hot_stamp(instance_dir: str | pathlib.Path) -> dict[str, Any]:
    path = hot_stamp_path(pathlib.Path(instance_dir))
    if not path.is_file():
        fail(f"hot stamp missing: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        fail(f"{path}: expected object")
    if data.get("schema_version") != HOT_SCHEMA_VERSION:
        fail(f"{path}: unsupported hot schema")
    return data


def find_hot_instance_for_profile(
    hot_root: str | pathlib.Path,
    profile: str,
    topology_id: str,
    *,
    profile_data: dict[str, Any] | None = None,
) -> pathlib.Path | None:
    """Return the newest ready instance matching the live expected identity."""
    topo12 = (topology_id or "notopology")[:12]
    parent = pathlib.Path(hot_root) / f"{profile}-{topo12}"
    if not parent.is_dir():
        return None
    candidates: list[tuple[str, pathlib.Path]] = []
    try:
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            stamp_path = hot_stamp_path(child)
            if not stamp_path.is_file():
                continue
            try:
                stamp = load_json(stamp_path)
            except ModelLibraryError:
                continue
            if not isinstance(stamp, dict):
                continue
            if stamp.get("schema_version") != HOT_SCHEMA_VERSION:
                continue
            integrity = stamp.get("integrity")
            if not isinstance(integrity, dict) or (
                integrity.get("scheme") != SNAPSHOT_INTEGRITY_SCHEME
            ):
                continue
            if stamp.get("state") not in {"ready", "pinned"} and not stamp.get("pinned"):
                if stamp.get("state") != "ready":
                    continue
            activated = str(stamp.get("activated_at") or "")
            candidates.append((activated, child))
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    for _activated, candidate in candidates:
        if profile_data is not None:
            try:
                verify_hot_stamp_against_profile(
                    load_hot_stamp(candidate),
                    profile_data,
                )
            except ModelLibraryError:
                continue
        return candidate
    return None


def dir_size_bytes(path: pathlib.Path) -> int:
    return tree_bytes(path)


def inspect_hub_inventory(
    hub_path: str | pathlib.Path,
    *,
    rank: int,
    node_id: str,
    model_id: str | None = None,
    revision: str | None = None,
    allow_empty_files: bool = False,
) -> dict[str, Any]:
    """Inspect and seal one catalog home on the node that owns its path."""
    path = pathlib.Path(hub_path)
    if not path.is_absolute():
        fail(f"inspect-hub: hub path must be absolute: {path}")
    if rank < 0:
        fail("inspect-hub: rank must be non-negative")
    node_id = str(node_id).strip()
    if not node_id:
        fail("inspect-hub: node_id is required")
    resolved_model_id = model_id or hub_dirname_to_model_id(path.name)
    if not resolved_model_id:
        fail("inspect-hub: model_id is required for a non-standard hub path")
    selected_revision = revision or read_revision(path)
    state = (
        hub_snapshot_state(path, selected_revision)
        if selected_revision is not None
        else hub_tree_state(path)
    )
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "model-library-home-inventory",
        "rank": rank,
        "node_id": node_id,
        "hub_path": str(path),
        "model_id": resolved_model_id,
        "state": state,
        "revision": selected_revision,
        "content_digest": None,
        "bytes_logical": 0,
        "integrity_manifest": None,
    }
    if state == "complete":
        manifest = build_snapshot_manifest(
            path,
            model_id=resolved_model_id,
            revision=selected_revision,
            allow_empty_files=allow_empty_files,
        )
        result["content_digest"] = manifest["manifest_id"]
        result["bytes_logical"] = manifest["total_bytes"]
        result["integrity_manifest"] = manifest
    return result


def validate_activation_home_inventory(
    home: dict[str, Any],
    catalog_revision: str | None,
    inventory: dict[str, Any],
    *,
    model_id: str,
) -> tuple[str, int, dict[str, Any]]:
    """Bind a sealed remote-home manifest to the resolved catalog identity."""
    if inventory.get("schema_version") != 2:
        fail("prepare: home inventory schema_version must be 2")
    if inventory.get("kind") != "model-library-home-inventory":
        fail("prepare: home inventory kind is invalid")
    try:
        expected_rank = int(home["rank"])
    except (KeyError, TypeError, ValueError):
        fail("prepare: catalog home rank is invalid")
    actual_rank = inventory.get("rank")
    if isinstance(actual_rank, bool) or actual_rank != expected_rank:
        fail("prepare: home inventory rank differs from catalog home")
    expected_node_id = str(home.get("node_id") or "")
    if inventory.get("node_id") != expected_node_id:
        fail("prepare: home inventory node_id differs from catalog home")
    expected_path = str(pathlib.Path(str(home.get("hub_path") or "")))
    if not pathlib.Path(expected_path).is_absolute():
        fail("prepare: catalog home hub_path must be absolute")
    if inventory.get("hub_path") != expected_path:
        fail("prepare: home inventory path differs from catalog home")
    if inventory.get("model_id") != model_id:
        fail("prepare: home inventory model_id differs from catalog")
    state = inventory.get("state")
    if state != "complete":
        fail(f"prepare: home hub is {state or 'invalid'}: {expected_path}")
    revision = inventory.get("revision")
    if catalog_revision and revision != catalog_revision:
        fail("prepare: home revision differs from catalog; run catalog refresh")
    manifest = validate_snapshot_manifest(inventory.get("integrity_manifest"))
    if manifest.get("model_id") != model_id:
        fail("prepare: home manifest model_id differs from catalog")
    if manifest.get("snapshot_revision") != revision:
        fail("prepare: home manifest revision differs from inventory")
    digest = inventory.get("content_digest")
    if digest != manifest.get("manifest_id"):
        fail("prepare: home inventory content_digest differs from manifest")
    bytes_logical = inventory.get("bytes_logical")
    if (
        isinstance(bytes_logical, bool)
        or not isinstance(bytes_logical, int)
        or bytes_logical < 1
        or bytes_logical != manifest.get("total_bytes")
    ):
        fail("prepare: home inventory bytes_logical differs from manifest")
    return digest, bytes_logical, manifest


def activation_home_inventory(
    home: dict[str, Any],
    catalog_revision: str | None,
    supplied: dict[str, Any] | None,
    *,
    model_id: str,
) -> tuple[str, int, dict[str, Any]]:
    inventory = supplied
    if inventory is None:
        inventory = inspect_hub_inventory(
            home["hub_path"],
            rank=int(home["rank"]),
            node_id=str(home["node_id"]),
            model_id=model_id,
            revision=catalog_revision,
        )
    return validate_activation_home_inventory(
        home,
        catalog_revision,
        inventory,
        model_id=model_id,
    )


def build_hot_storage_requirements(
    *,
    target_ranks: list[int],
    bytes_logical: int,
    instance_dir: str | pathlib.Path,
    home_rank: int | None,
) -> list[dict[str, Any]]:
    if (
        isinstance(bytes_logical, bool)
        or not isinstance(bytes_logical, int)
        or bytes_logical < 1
    ):
        fail("hot storage requirements: model bytes must be positive")
    if len(set(target_ranks)) != len(target_ranks) or any(
        isinstance(rank, bool) or not isinstance(rank, int) or rank < 0
        for rank in target_ranks
    ):
        fail("hot storage requirements: target ranks are invalid")
    instance = pathlib.Path(instance_dir)
    if not instance.is_absolute():
        fail("hot storage requirements: instance path must be absolute")
    requirements: list[dict[str, Any]] = []
    for rank in sorted(target_ranks):
        durable_view = home_rank is not None and rank == home_rank
        requirements.append(
            {
                "rank": rank,
                "runtime_source": "durable-home" if durable_view else "working-copy",
                "required_owned_bytes": 0 if durable_view else bytes_logical,
                "replacing_path": str(instance),
            }
        )
    return requirements


def budget_report(
    hot_root: str | pathlib.Path,
    budget_bytes: int | None = None,
    *,
    reserve_bytes: int | None = None,
    filesystem_total_bytes: int | None = None,
    filesystem_available_bytes: int | None = None,
) -> dict[str, Any]:
    hot_root = pathlib.Path(hot_root)
    if not hot_root.is_absolute():
        fail(f"hot budget: hot root must be absolute: {hot_root}")
    if hot_root.exists():
        try:
            root_mode = hot_root.lstat().st_mode
        except OSError as exc:
            fail(f"hot budget: cannot inspect hot root {hot_root}: {exc}")
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            fail(f"hot budget: hot root must be a non-symlinked directory: {hot_root}")

    if (filesystem_total_bytes is None) != (filesystem_available_bytes is None):
        fail("hot budget: filesystem total and available bytes must be supplied together")
    if filesystem_total_bytes is None:
        probe = hot_root
        while not probe.exists():
            if probe.parent == probe:
                fail(f"hot budget: no existing filesystem parent for {hot_root}")
            probe = probe.parent
        try:
            filesystem = os.statvfs(probe)
        except OSError as exc:
            fail(f"hot budget: cannot inspect filesystem for {hot_root}: {exc}")
        block_size = filesystem.f_frsize or filesystem.f_bsize
        filesystem_total_bytes = int(filesystem.f_blocks) * int(block_size)
        filesystem_available_bytes = int(filesystem.f_bavail) * int(block_size)
        filesystem_probe = str(probe)
    else:
        if filesystem_total_bytes < 1 or filesystem_available_bytes < 0:
            fail("hot budget: injected filesystem capacity is invalid")
        if filesystem_available_bytes > filesystem_total_bytes:
            fail("hot budget: filesystem available bytes exceed total bytes")
        filesystem_probe = str(hot_root)

    hard_cap = (
        budget_bytes if budget_bytes is not None else configured_hot_budget_bytes()
    )
    if hard_cap is not None and hard_cap < 1:
        fail("hot budget: hard cap must be positive")
    reserve_override = (
        reserve_bytes
        if reserve_bytes is not None
        else configured_hot_reserve_bytes()
    )
    if reserve_override is not None and reserve_override < 0:
        fail("hot budget: reserve must be non-negative")
    default_reserve = max(
        DEFAULT_HOT_RESERVE_BYTES,
        (
            filesystem_total_bytes * DEFAULT_HOT_RESERVE_PERCENT
            + 99
        )
        // 100,
    )
    reserve = reserve_override if reserve_override is not None else default_reserve

    used = tree_bytes(hot_root) if hot_root.is_dir() else 0
    instances: list[dict[str, Any]] = []
    scan_errors: list[dict[str, str]] = []
    accounted_instances: set[pathlib.Path] = set()
    accounted_instance_bytes = 0
    pinned_bytes = 0
    reclaimable_bytes = 0
    if hot_root.is_dir():
        for dirpath, dirnames, filenames in os.walk(hot_root, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if not (pathlib.Path(dirpath) / name).is_symlink()
            ]
            if pathlib.Path(dirpath).name != ".pulsar" or "hot.json" not in filenames:
                continue
            stamp_path = pathlib.Path(dirpath) / "hot.json"
            instance = stamp_path.parent.parent
            if instance in accounted_instances:
                continue
            accounted_instances.add(instance)
            size = dir_size_bytes(instance)
            accounted_instance_bytes += size
            try:
                stamp = load_json(stamp_path)
            except ModelLibraryError as exc:
                scan_errors.append({"path": str(stamp_path), "detail": str(exc)})
                instances.append(
                    {
                        "path": str(instance),
                        "profile": None,
                        "state": "invalid",
                        "pinned": False,
                        "runtime_source": "unknown",
                        "bytes": size,
                    }
                )
                continue
            if not isinstance(stamp, dict):
                scan_errors.append(
                    {
                        "path": str(stamp_path),
                        "detail": "hot metadata is not an object",
                    }
                )
                continue
            pinned = bool(stamp.get("pinned"))
            model_id = stamp.get("model_id")
            runtime_source = "unknown"
            if isinstance(model_id, str) and model_id:
                hub_path = hot_hub_path(instance, model_id)
                runtime_source = (
                    "durable-home" if hub_path.is_symlink() else "working-copy"
                )
            if pinned:
                pinned_bytes += size
            else:
                reclaimable_bytes += size
            instances.append(
                {
                    "path": str(instance),
                    "profile": stamp.get("profile"),
                    "state": stamp.get("state"),
                    "pinned": pinned,
                    "runtime_source": runtime_source,
                    "bytes": size,
                }
            )
    untracked_bytes = max(0, used - accounted_instance_bytes)
    physical_remaining = max(0, filesystem_available_bytes - reserve)
    if hard_cap is None:
        quota_remaining = physical_remaining
        effective_budget = used + physical_remaining
    else:
        quota_remaining = max(0, hard_cap - used)
        effective_budget = hard_cap
    remaining = min(physical_remaining, quota_remaining)
    blockers: list[dict[str, Any]] = []
    if filesystem_available_bytes < reserve:
        blockers.append(
            {
                "code": "filesystem-reserve-breached",
                "detail": (
                    f"filesystem available={filesystem_available_bytes} is below "
                    f"the required reserve={reserve}"
                ),
            }
        )
    if hard_cap is not None and used > hard_cap:
        blockers.append(
            {
                "code": "hard-cap-exceeded",
                "detail": f"owned hot bytes={used} exceed hard cap={hard_cap}",
            }
        )
    return {
        "schema_version": HOT_BUDGET_SCHEMA_VERSION,
        "kind": HOT_BUDGET_OBSERVATION_KIND,
        "hot_root": str(hot_root),
        "policy": {
            "mode": "filesystem-reserve",
            "hard_cap_bytes": hard_cap,
            "hard_cap_source": "environment-or-argument" if hard_cap is not None else "none",
            "reserve_bytes": reserve,
            "reserve_source": (
                "environment-or-argument"
                if reserve_override is not None
                else "default-max-64gib-or-5-percent"
            ),
            "default_reserve_bytes": DEFAULT_HOT_RESERVE_BYTES,
            "default_reserve_percent": DEFAULT_HOT_RESERVE_PERCENT,
            "automatic_eviction": False,
        },
        "filesystem": {
            "probe_path": filesystem_probe,
            "total_bytes": filesystem_total_bytes,
            "available_bytes": filesystem_available_bytes,
            "projected_available_bytes": filesystem_available_bytes,
        },
        "budget_bytes": effective_budget,
        "used_bytes": used,
        "owned_hot_bytes": used,
        "remaining_bytes": remaining,
        "physical_remaining_bytes": physical_remaining,
        "quota_remaining_bytes": quota_remaining,
        "pinned_bytes": pinned_bytes,
        "reclaimable_bytes": reclaimable_bytes,
        "untracked_bytes": untracked_bytes,
        "over_budget": bool(blockers),
        "blockers": blockers,
        "scan_errors": scan_errors,
        "instances": sorted(instances, key=lambda i: i["path"]),
    }


def hot_budget_admission(
    hot_root: str | pathlib.Path,
    required_owned_bytes: int,
    *,
    budget_bytes: int | None = None,
    reserve_bytes: int | None = None,
    replacing_path: str | pathlib.Path | None = None,
    runtime_source: str = "working-copy",
    rank: int = 0,
    node_id: str = "",
    hostname: str = "",
    filesystem_total_bytes: int | None = None,
    filesystem_available_bytes: int | None = None,
) -> dict[str, Any]:
    if (
        isinstance(required_owned_bytes, bool)
        or not isinstance(required_owned_bytes, int)
        or required_owned_bytes < 0
    ):
        fail("hot admission: required owned bytes must be a non-negative integer")
    if runtime_source not in {"durable-home", "working-copy", "inventory", "pin"}:
        fail(f"hot admission: unsupported runtime source {runtime_source!r}")
    if runtime_source == "durable-home" and required_owned_bytes != 0:
        fail("hot admission: durable-home views must require zero owned model bytes")
    if rank < 0:
        fail("hot admission: rank must be non-negative")
    report = budget_report(
        hot_root,
        budget_bytes=budget_bytes,
        reserve_bytes=reserve_bytes,
        filesystem_total_bytes=filesystem_total_bytes,
        filesystem_available_bytes=filesystem_available_bytes,
    )
    used = report["used_bytes"]
    replacing_owned = 0
    if replacing_path is not None:
        rep = pathlib.Path(replacing_path)
        if not rep.is_absolute():
            fail(f"hot admission: replacing path must be absolute: {rep}")
        try:
            rep.resolve(strict=False).relative_to(
                pathlib.Path(hot_root).resolve(strict=False)
            )
        except ValueError:
            fail(f"hot admission: replacing path escapes hot root: {rep}")
        if rep.exists():
            if rep.is_symlink() or not rep.is_dir():
                fail(f"hot admission: replacing path is not a managed directory: {rep}")
            replacing_owned = dir_size_bytes(rep)

    projected_used = max(0, used - replacing_owned) + required_owned_bytes
    filesystem = report["filesystem"]
    available = int(filesystem["available_bytes"])
    # Do not credit replacement bytes before deletion. This deliberately
    # prefers an explicit purge/recheck over optimistic ENOSPC-prone recovery.
    projected_available = available - required_owned_bytes
    policy = report["policy"]
    reserve = int(policy["reserve_bytes"])
    hard_cap = policy.get("hard_cap_bytes")
    blockers: list[dict[str, Any]] = []
    if hard_cap is not None and projected_used > hard_cap:
        blockers.append(
            {
                "code": "hard-cap-exceeded",
                "detail": (
                    f"projected owned hot bytes={projected_used} exceed "
                    f"hard cap={hard_cap}"
                ),
            }
        )
    if projected_available < reserve:
        blockers.append(
            {
                "code": "filesystem-reserve",
                "detail": (
                    f"writing {required_owned_bytes} bytes would leave "
                    f"{max(0, projected_available)} available; reserve={reserve}"
                ),
            }
        )
    result = {
        **report,
        "rank": rank,
        "node_id": node_id,
        "hostname": hostname,
        "runtime_source": runtime_source,
        "required_owned_bytes": required_owned_bytes,
        "replacing_owned_bytes": replacing_owned,
        "projected_owned_hot_bytes": projected_used,
        "projected_available_bytes": max(0, projected_available),
        "state": "eligible" if not blockers else "blocked",
        "ok": not blockers,
        "blockers": blockers,
    }
    result["filesystem"] = {
        **filesystem,
        "projected_available_bytes": max(0, projected_available),
    }
    return result


def ensure_budget_for_add(
    hot_root: str | pathlib.Path,
    add_bytes: int,
    *,
    budget_bytes: int | None = None,
    reserve_bytes: int | None = None,
    replacing_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    admission = hot_budget_admission(
        hot_root,
        add_bytes,
        budget_bytes=budget_bytes,
        reserve_bytes=reserve_bytes,
        replacing_path=replacing_path,
    )
    if not admission["ok"]:
        detail = "; ".join(
            str(item.get("detail") or item.get("code"))
            for item in admission["blockers"]
        )
        fail(f"hot budget exceeded: {detail}")
    return admission


def merge_hot_budget_observations(
    observations: list[dict[str, Any]],
    *,
    expected_ranks: list[int],
    topology_id: str,
    mode: str,
    profile: str = "",
    model_id: str = "",
    bytes_logical: int = 0,
) -> dict[str, Any]:
    if not topology_id:
        fail("hot budget plan: topology_id is required")
    if not mode:
        fail("hot budget plan: mode is required")
    if isinstance(bytes_logical, bool) or bytes_logical < 0:
        fail("hot budget plan: model bytes must be non-negative")
    expected = sorted(set(expected_ranks))
    if not expected:
        fail("hot budget plan: at least one rank is required")
    if len(expected) != len(expected_ranks) or any(
        isinstance(rank, bool) or not isinstance(rank, int) or rank < 0
        for rank in expected
    ):
        fail("hot budget plan: expected ranks must be unique and non-negative")
    by_rank: dict[int, dict[str, Any]] = {}
    observed_node_ids: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict):
            fail("hot budget plan: observation is not an object")
        if observation.get("schema_version") != HOT_BUDGET_SCHEMA_VERSION:
            fail("hot budget plan: unsupported observation schema")
        if observation.get("kind") != HOT_BUDGET_OBSERVATION_KIND:
            fail("hot budget plan: invalid observation kind")
        rank = observation.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
            fail("hot budget plan: observation rank is invalid")
        if rank in by_rank:
            fail(f"hot budget plan: duplicate observation for rank {rank}")
        if not isinstance(observation.get("node_id"), str) or not observation["node_id"]:
            fail(f"hot budget plan: rank {rank} node identity is missing")
        if observation["node_id"] in observed_node_ids:
            fail(
                "hot budget plan: duplicate node identity "
                f"{observation['node_id']!r}"
            )
        observed_node_ids.add(observation["node_id"])
        observation_blockers = observation.get("blockers") or []
        expected_state = "blocked" if observation_blockers else "eligible"
        if observation.get("state") != expected_state:
            fail(f"hot budget plan: rank {rank} state/blocker mismatch")
        if observation.get("ok") is not (expected_state == "eligible"):
            fail(f"hot budget plan: rank {rank} ok/state mismatch")
        by_rank[rank] = observation
    if sorted(by_rank) != expected:
        fail(
            f"hot budget plan: observed ranks {sorted(by_rank)} "
            f"differ from expected {expected}"
        )

    blockers: list[dict[str, Any]] = []
    for rank in expected:
        observation = by_rank[rank]
        for blocker in observation.get("blockers") or []:
            if not isinstance(blocker, dict):
                fail(f"hot budget plan: rank {rank} blocker is invalid")
            blockers.append(
                {
                    **blocker,
                    "rank": rank,
                    "node_id": observation["node_id"],
                    "hostname": observation.get("hostname") or "",
                    "runtime_source": observation.get("runtime_source"),
                }
            )
    return {
        "schema_version": HOT_BUDGET_SCHEMA_VERSION,
        "kind": HOT_BUDGET_PLAN_KIND,
        "topology_id": topology_id,
        "mode": mode,
        "profile": profile or None,
        "model_id": model_id or None,
        "bytes_logical": bytes_logical,
        "state": "eligible" if not blockers else "blocked",
        "ok": not blockers,
        "observed_nodes": [by_rank[rank] for rank in expected],
        "blockers": blockers,
    }


def _human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(max(0, value))
    unit = units[0]
    for candidate in units:
        unit = candidate
        if amount < 1024 or candidate == units[-1]:
            break
        amount /= 1024
    return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"


def render_hot_budget_plan(plan: dict[str, Any]) -> None:
    if TerminalWriter is None:
        fail("hot budget rendering requires scripts/terminal_format.py")
    if plan.get("kind") != HOT_BUDGET_PLAN_KIND:
        fail("hot budget rendering: invalid plan kind")
    term = TerminalWriter()
    term.emit(f"hot storage admission  {str(plan.get('state')).upper()}")
    if plan.get("profile"):
        term.field("profile", plan["profile"])
    if plan.get("model_id"):
        term.field("model", plan["model_id"])
    if plan.get("bytes_logical"):
        term.field("model bytes", _human_bytes(int(plan["bytes_logical"])))
    term.field("nodes", len(plan.get("observed_nodes") or []))
    term.field("automatic eviction", "disabled")
    term.blank()
    for observation in plan.get("observed_nodes") or []:
        rank = observation["rank"]
        source = observation.get("runtime_source") or "inventory"
        term.emit(
            f"rank {rank} · {source} · {str(observation.get('state')).upper()}",
            initial_indent="  ",
            subsequent_indent="    ",
        )
        policy = observation["policy"]
        hard_cap = policy.get("hard_cap_bytes")
        cap_text = _human_bytes(int(hard_cap)) if hard_cap is not None else "none"
        term.emit(
            (
                f"need {_human_bytes(int(observation.get('required_owned_bytes') or 0))}"
                f" · used {_human_bytes(int(observation.get('used_bytes') or 0))}"
                f" · free {_human_bytes(int(observation['filesystem']['available_bytes']))}"
            ),
            initial_indent="    ",
            subsequent_indent="    ",
        )
        term.emit(
            (
                f"reserve {_human_bytes(int(policy['reserve_bytes']))}"
                f" · hard cap {cap_text}"
            ),
            initial_indent="    ",
            subsequent_indent="    ",
        )
        if observation.get("reclaimable_bytes"):
            term.emit(
                f"reclaimable {_human_bytes(int(observation['reclaimable_bytes']))}",
                initial_indent="    ",
                subsequent_indent="    ",
            )
    blockers = plan.get("blockers") or []
    if blockers:
        term.blank()
        term.emit(f"Blocked by {len(blockers)} condition(s):")
        for blocker in blockers:
            term.emit(
                f"rank {blocker['rank']} · {blocker.get('code') or 'policy'}",
                initial_indent="  ",
                subsequent_indent="    ",
            )
            term.emit(
                blocker.get("detail") or "hot admission failed",
                initial_indent="    ",
                subsequent_indent="    ",
            )
        term.blank()
        term.emit("No bytes were changed. Purge an unpinned hot instance or free disk, then recheck.")
    else:
        term.blank()
        term.emit("Every selected rank preserves its configured filesystem reserve.")


def load_topology_for_plan(topology_file: str | pathlib.Path | None) -> dict[str, Any]:
    """Load the confirmed topology for RoCE rail selection."""
    if not topology_file:
        fail("preparation requires --topology-file (confirmed cluster topology)")
    path = pathlib.Path(topology_file)
    if not path.is_file():
        fail(f"topology file missing: {path}")
    try:
        try:
            from scripts.topology_manifest import (
                extract_topology,
                validate_manifest,
            )
        except ModuleNotFoundError:
            from topology_manifest import extract_topology, validate_manifest

        topology = extract_topology(load_json(path))
        validate_manifest(topology, require_verified=True)
        return topology
    except ModelLibraryError:
        raise
    except Exception as exc:
        fail(f"topology: {exc}")


def link_between(
    topology: dict[str, Any], first: int, second: int
) -> dict[str, Any]:
    wanted = [min(first, second), max(first, second)]
    for link in topology["links"]:
        if link["ranks"] == wanted:
            return link
    fail(f"topology: no fabric link for ranks {first}/{second}")


def selected_rail_between(
    topology: dict[str, Any],
    home_rank: int,
    client_rank: int,
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    link = link_between(topology, home_rank, client_rank)
    rails = sorted(
        link["rails"],
        key=lambda item: (
            item["network"],
            item["a"]["ip"],
            item["b"]["ip"],
        ),
    )
    if rail_index >= len(rails):
        fail(
            f"topology: ranks {home_rank}/{client_rank} expose "
            f"{len(rails)} rails, not index {rail_index}"
        )
    rail = rails[rail_index]
    home_side = "a" if home_rank < client_rank else "b"
    client_side = "b" if home_rank < client_rank else "a"
    return rail[home_side], rail[client_side], rail["network"]


def plan_prepare(
    *,
    catalog_path: str,
    profile: str,
    topology_id: str,
    hot_root: str,
    models_dir: str | pathlib.Path,
    backend: str | None = None,
    transport: str | None = None,
    allow_unvalidated: bool = False,
    nodes: int | None = None,
    target_rank: int | None = None,
    topology_file: str | None = None,
    home_inventory: dict[str, Any] | None = None,
    require_exact_revision: str | None = None,
    expected_integrity_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a model-preparation plan JSON for bash to execute (copy + stamp).

    Occupancy lookup lives in the Bash wrapper. This planner still requires
    the download-receipt commit and file list; unknown trees without a receipt
    fail without fallback and must not use a self-observed manifest as identity.
    """
    backend, transport = resolve_activate_transport(
        backend, transport, nodes=nodes
    )
    catalog = load_catalog(catalog_path)
    if catalog.get("topology_id") and topology_id and catalog["topology_id"] != topology_id:
        fail(
            f"prepare: catalog topology_id mismatch "
            f"(catalog={catalog['topology_id'][:12]}… live={topology_id[:12]}…); "
            "run catalog refresh"
        )
    # Preparation is warm-catalog only; cold uses adopt or stage-only.
    profile_data = load_hf_profile(models_dir, profile)
    resolved = resolve_entry(
        catalog,
        profile=profile,
        cold_root=None,
        models_dir=models_dir,
    )
    if resolved.get("tier") == "cold":
        fail(
            "prepare: cold source requires "
            "`cold adopt` (durable warm home) or `cold stage-only` (hot only)"
        )
    if resolved.get("model_id") != profile_data.get("model_id"):
        fail("prepare: catalog model differs from the live profile")
    home = resolved["home"]
    hub_path = home["hub_path"]
    digest, bytes_logical, integrity_manifest = activation_home_inventory(
        home,
        resolved.get("revision"),
        home_inventory,
        model_id=resolved["model_id"],
    )
    if require_exact_revision is None or expected_integrity_manifest is None:
        fail(
            "prepare: download receipt revision and file list are required; "
            "unknown trees without a receipt fail without fallback"
        )
    if re.fullmatch(r"[0-9a-f]{40}", require_exact_revision) is None:
        fail("prepare: download receipt identity requires one exact 40-hex commit")
    if resolved.get("revision") != require_exact_revision:
        fail("prepare: catalog revision is not the exact download-receipt commit")
    if resolved.get("identity_key") != f"{resolved['model_id']}@{require_exact_revision}":
        fail("prepare: catalog identity is not the exact model_id@commit")
    if integrity_manifest.get("snapshot_revision") != require_exact_revision:
        fail("prepare: home manifest revision is not the exact commit")
    expected = validate_snapshot_manifest(expected_integrity_manifest)
    if integrity_manifest.get("manifest_id") != expected.get("manifest_id"):
        fail("prepare: receipt-backed home rehash differs from the receipt")
    if integrity_manifest.get("files") != expected.get("files"):
        fail("prepare: receipt-backed home file set differs from the receipt")
    validation = require_activation_identity(
        profile_data,
        integrity_manifest,
        allow_unvalidated=allow_unvalidated,
    )
    cid = hot_content_id(resolved["identity_key"], digest, validation)
    instance = hot_instance_dir(hot_root, profile, topology_id, cid)
    node_count = nodes if nodes is not None else 1
    if node_count < 1:
        fail("prepare: nodes must be a positive integer")
    if target_rank is not None:
        if node_count != 1:
            fail("prepare: an explicit target rank is valid only for one-node profiles")
        if target_rank < 0:
            fail("prepare: target rank must be non-negative")
        if target_rank != int(home["rank"]):
            fail(
                "prepare: a one-node local-files service must run on its "
                "durable-home rank"
            )
        target_ranks = [target_rank]
    elif node_count == 1:
        # A one-node model-library service consumes the durable-home view. The
        # caller may make that placement explicit, but omission must not
        # silently turn rank 0 into a non-home working copy.
        target_ranks = [int(home["rank"])]
    else:
        target_ranks = list(range(node_count))
    hot_storage_requirements = build_hot_storage_requirements(
        target_ranks=target_ranks,
        bytes_logical=bytes_logical,
        instance_dir=instance,
        home_rank=int(home["rank"]),
    )
    existing = None
    if hot_stamp_path(instance).is_file():
        existing = load_hot_stamp(instance)
        if (
            existing.get("content_digest") == digest
            and existing.get("identity_key") == resolved["identity_key"]
            and existing.get("validation") == validation
            and existing.get("state") in {"ready", "pinned"}
        ):
            return {
                "action": "skip",
                "reason": "hot already ready with matching digest",
                "profile": profile,
                "model_id": resolved["model_id"],
                "identity_key": resolved["identity_key"],
                "revision": resolved.get("revision"),
                "home": home,
                "hot_root": hot_root,
                "instance_dir": str(instance),
                "hub_dest": str(hot_hub_path(instance, resolved["model_id"])),
                "content_id": cid,
                "content_digest": digest,
                "integrity_manifest": integrity_manifest,
                "validation": validation,
                "bytes_logical": bytes_logical,
                "backend": backend,
                "transport": transport,
                "topology_id": topology_id,
                "target_ranks": target_ranks,
                "hot_storage_requirements": hot_storage_requirements,
                "stamp": existing,
                "transfer": None,
            }

    stamp = build_hot_stamp(
        profile=profile,
        model_id=resolved["model_id"],
        identity_key=resolved["identity_key"],
        revision=resolved.get("revision"),
        topology_id=topology_id,
        home_node_id=home["node_id"],
        content_id=cid,
        content_digest=digest,
        integrity_manifest=integrity_manifest,
        validation=validation,
        backend=backend,
        bytes_logical=bytes_logical,
        transport=transport,
        pinned=False,
        state="ready",
    )

    transfer = None
    action = "copy"
    hub_source = hub_path

    return {
        "action": action,
        "profile": profile,
        "model_id": resolved["model_id"],
        "identity_key": resolved["identity_key"],
        "revision": resolved.get("revision"),
        "home": home,
        "hot_root": hot_root,
        "instance_dir": str(instance),
        "hub_source": hub_source,
        "hub_dest": str(hot_hub_path(instance, resolved["model_id"])),
        "content_id": cid,
        "content_digest": digest,
        "integrity_manifest": integrity_manifest,
        "validation": validation,
        "bytes_logical": bytes_logical,
        "backend": backend,
        "transport": transport,
        "topology_id": topology_id,
        "target_ranks": target_ranks,
        "hot_storage_requirements": hot_storage_requirements,
        "stamp": stamp,
        "transfer": transfer,
        "rank_sources": (
            transfer["sources"]
            if transfer and transfer.get("sources")
            else {str(r): hub_path for r in target_ranks}
        ),
    }


def verify_hot_ready(
    instance_dir: str | pathlib.Path,
    *,
    profile: str | None = None,
    topology_id: str | None = None,
    require_digest: bool = True,
    allow_verifying: bool = False,
    workers: int | None = None,
    serve_time_witness: bool = False,
    refresh_witness: bool = False,
) -> dict[str, Any]:
    if serve_time_witness and refresh_witness:
        fail("hot verify cannot both consume and force-refresh a witness")
    instance_dir = pathlib.Path(instance_dir)
    stamp = load_hot_stamp(instance_dir)
    allowed_states = {"ready", "pinned"}
    if allow_verifying:
        allowed_states.add("verifying")
    if stamp.get("state") not in allowed_states and not stamp.get("pinned"):
        fail(f"hot not ready: state={stamp.get('state')!r} at {instance_dir}")
    if profile and stamp.get("profile") != profile:
        fail(f"hot profile mismatch: stamp={stamp.get('profile')} want={profile}")
    if topology_id and stamp.get("topology_id") != topology_id:
        fail("hot topology_id mismatch")
    hub = hot_hub_path(instance_dir, stamp["model_id"])
    integrity = stamp.get("integrity")
    if not isinstance(integrity, dict):
        fail("hot integrity seal missing")
    if integrity.get("scheme") != SNAPSHOT_INTEGRITY_SCHEME:
        fail("hot integrity scheme is unsupported")
    manifest = validate_snapshot_manifest(integrity.get("manifest"))
    stamp_revision = stamp.get("revision")
    if (
        not isinstance(stamp_revision, str)
        or SAFE_REV.fullmatch(stamp_revision) is None
        or stamp_revision != manifest["snapshot_revision"]
    ):
        fail("hot revision differs from sealed manifest")
    snapshot_state = hub_snapshot_state(hub, manifest["snapshot_revision"])
    if snapshot_state != "complete":
        fail(
            f"hot snapshot {manifest['snapshot_revision']} is {snapshot_state}: {hub}"
        )
    if manifest.get("manifest_id") != stamp.get("content_digest"):
        fail("hot content_digest differs from sealed manifest")
    if manifest.get("model_id") != stamp.get("model_id"):
        fail("hot model_id differs from sealed manifest")
    validation = validate_hot_validation(
        stamp.get("validation"),
        profile=str(stamp.get("profile") or ""),
        manifest=manifest,
    )

    witness_path = hot_witness_path(instance_dir)
    witness_result: dict[str, Any] = {
        "status": "not-checked",
        "reason": "verification mode did not use the serve witness",
        "scheme": HOT_WITNESS_SCHEME,
        "path": str(witness_path),
    }
    if serve_time_witness:
        observation = build_stable_hot_witness_observation(
            stamp,
            hub=hub,
            manifest=manifest,
            validation=validation,
        )
        witness = None
        fallback_reason = ""
        try:
            witness = load_hot_witness(instance_dir)
        except ModelLibraryError as exc:
            fallback_reason = str(exc)
        if witness is not None and hot_witness_observation(witness) == observation:
            verification = _witness_integrity_result(manifest)
            witness_result = _witness_result(
                witness,
                status="match",
                reason="rank-local metadata is unchanged",
                path=witness_path,
            )
        else:
            if witness is not None:
                fallback_reason = "rank-local metadata differs from the witness"
            print(
                "model-library: serve witness drift "
                f"({fallback_reason}); running full SHA-256 verification",
                file=sys.stderr,
            )
            verification, witness = full_verify_and_refresh_hot_witness(
                instance_dir,
                stamp,
                hub=hub,
                manifest=manifest,
                validation=validation,
                workers=workers,
            )
            witness_result = _witness_result(
                witness,
                status="refreshed",
                reason=fallback_reason,
                path=witness_path,
            )
            print(
                "model-library: full SHA-256 verification passed; "
                f"serve witness refreshed at {witness_path}",
                file=sys.stderr,
            )
    elif refresh_witness:
        verification, witness = full_verify_and_refresh_hot_witness(
            instance_dir,
            stamp,
            hub=hub,
            manifest=manifest,
            validation=validation,
            workers=workers,
        )
        witness_result = _witness_result(
            witness,
            status="refreshed",
            reason="full verification trust boundary",
            path=witness_path,
        )
    else:
        verification = verify_snapshot_manifest(
            hub,
            manifest,
            metadata_only=not require_digest,
            workers=workers,
        )

    snapshot_path = hub / "snapshots" / manifest["snapshot_revision"]
    return {
        "stamp": stamp,
        "hub_path": str(hub),
        "snapshot_path": str(snapshot_path),
        "runtime_model_relative": f"snapshots/{manifest['snapshot_revision']}",
        "instance_dir": str(instance_dir),
        "integrity": verification,
        "validation": validation,
        "witness": witness_result,
    }


def verify_hot_stamp_against_profile(
    stamp: dict[str, Any],
    profile_data: dict[str, Any],
) -> dict[str, Any]:
    if stamp.get("profile") != profile_data.get("profile"):
        fail("hot profile differs from live profile")
    integrity = stamp.get("integrity")
    if not isinstance(integrity, dict):
        fail("hot integrity seal missing")
    manifest = validate_snapshot_manifest(integrity.get("manifest"))
    live_validation = compare_profile_expected_identity(profile_data, manifest)
    stored_validation = validate_hot_validation(
        stamp.get("validation"),
        profile=profile_data["profile"],
        manifest=manifest,
    )
    if stored_validation != live_validation:
        fail(
            "hot validation provenance differs from the live profile/seal; "
            "prepare again from the current expected identity"
        )
    return live_validation


def set_hot_pinned(instance_dir: str | pathlib.Path, pinned: bool) -> dict[str, Any]:
    stamp = load_hot_stamp(instance_dir)
    stamp["pinned"] = pinned
    stamp["state"] = "pinned" if pinned else "ready"
    write_hot_stamp(pathlib.Path(instance_dir), stamp)
    return stamp


def purge_hot_instance(
    instance_dir: str | pathlib.Path,
    *,
    force_unpin: bool = False,
) -> None:
    instance_dir = pathlib.Path(instance_dir)
    if not instance_dir.is_dir():
        fail(f"purge: not a directory: {instance_dir}")
    stamp_path = hot_stamp_path(instance_dir)
    if stamp_path.is_file():
        stamp = load_json(stamp_path)
        if isinstance(stamp, dict) and stamp.get("pinned") and not force_unpin:
            fail("purge: instance is pinned; pass --force-unpin to remove")
    # Safety: only delete under hot root pattern
    shutil.rmtree(instance_dir)


def _require_canonical_absolute_path(path: str | pathlib.Path, *, label: str) -> str:
    """Require a normalized absolute path with no dot, dot-dot, or aliasing."""
    raw = str(path) if isinstance(path, pathlib.Path) else path
    if not isinstance(raw, str) or not raw:
        fail(f"{label}: path must be absolute")
    if any(character in raw for character in ("\x00", "\n", "\r")):
        fail(f"{label}: path is not canonical")
    candidate = pathlib.Path(raw)
    if not candidate.is_absolute():
        fail(f"{label}: path must be absolute")
    parts = candidate.parts[1:]
    if any(part in {"", ".", ".."} for part in parts):
        fail(f"{label}: path is not canonical")
    normalized = str(pathlib.Path("/").joinpath(*parts)) if parts else "/"
    if normalized != raw:
        fail(f"{label}: path is not canonical")
    return raw


def inspect_live_directory_identity(path: str | pathlib.Path) -> dict[str, Any]:
    """Return a private no-follow identity for one live directory.

    This is a rank-local inspection helper. It must not be persisted in
    catalog.json or the model tree, and public CLI results must not echo it.
    """
    target = pathlib.Path(_require_canonical_absolute_path(path, label="live directory"))
    try:
        before = target.lstat()
    except FileNotFoundError:
        fail("live directory: path is missing")
    except OSError:
        fail("live directory: path is unavailable")
    if stat.S_ISLNK(before.st_mode):
        fail("live directory: path must not be a symlink")
    if not stat.S_ISDIR(before.st_mode):
        fail("live directory: path is not a directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        fail("live directory: path is missing")
    except NotADirectoryError:
        fail("live directory: path is not a directory")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EINVAL}:
            fail("live directory: path must not be a symlink")
        fail("live directory: path is unavailable")
    try:
        info = os.fstat(fd)
    finally:
        os.close(fd)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)
    ):
        fail("live directory: path is not a regular directory")
    return {
        "schema_version": LIVE_DIRECTORY_IDENTITY_SCHEMA_VERSION,
        "kind": LIVE_DIRECTORY_IDENTITY_KIND,
        "path": str(target),
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "ctime_ns": int(info.st_ctime_ns),
    }


def _lstat_kind(path: pathlib.Path) -> tuple[str, os.stat_result | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    if stat.S_ISLNK(metadata.st_mode):
        return "symlink", metadata
    if stat.S_ISDIR(metadata.st_mode):
        return "directory", metadata
    if stat.S_ISREG(metadata.st_mode):
        return "file", metadata
    return "other", metadata


def _legacy_stamp_owner(stamp: Any) -> tuple[bool, str]:
    """Validate historical ownership fields without conferring trust."""
    if not isinstance(stamp, dict):
        return False, "metadata is not an object"
    if stamp.get("schema_version") not in LEGACY_HOT_SCHEMA_VERSIONS:
        return False, "metadata is not a recognized legacy schema"
    common_fields = {
        "schema_version",
        "state",
        "profile",
        "model_id",
        "revision",
        "identity_key",
        "home_node_id",
        "topology_id",
        "content_id",
        "content_digest",
        "backend",
        "bytes_logical",
        "activated_at",
        "pinned",
        "budget_bytes_accounted",
    }
    allowed_fields = set(common_fields)
    if stamp["schema_version"] == 2:
        allowed_fields.update({"integrity", "transport"})
        if "integrity" not in stamp:
            return False, "schema-2 integrity ownership field is missing"
    extra = sorted(set(stamp) - allowed_fields)
    missing_contract = sorted(common_fields - set(stamp))
    if extra or missing_contract:
        return False, (
            "metadata fields differ from the recognized legacy contract "
            f"(missing={missing_contract}, extra={extra})"
        )
    required = (
        "profile", "model_id", "revision", "home_node_id", "topology_id",
        "content_id", "state", "pinned",
    )
    missing = [field for field in required if stamp.get(field) in (None, "")]
    if missing:
        return False, f"ownership fields missing: {missing}"
    if not isinstance(stamp.get("pinned"), bool):
        return False, "pinned ownership field is not boolean"
    revision = stamp.get("revision")
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        return False, "revision ownership field is unsafe"
    return True, "recognized legacy ownership metadata"


def _legacy_layout_state(instance: pathlib.Path, stamp: dict[str, Any]) -> str:
    content_id = str(stamp.get("content_id") or "")
    if instance.name == content_id:
        return "managed"
    if instance.name.startswith(f".{content_id}.pulsar-removing-"):
        return "retiring"
    return "invalid"


def _validate_schema3_hot_metadata(
    instance: pathlib.Path, stamp: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    required = {
        "schema_version", "state", "profile", "model_id", "revision",
        "identity_key", "home_node_id", "topology_id", "content_id",
        "content_digest", "integrity", "validation", "backend",
        "bytes_logical", "activated_at", "pinned", "budget_bytes_accounted",
    }
    allowed = required | {"transport"}
    if set(stamp) < required or not set(stamp) <= allowed:
        missing = sorted(required - set(stamp))
        extra = sorted(set(stamp) - allowed)
        fail(f"schema-3 metadata fields differ (missing={missing}, extra={extra})")
    for field in (
        "profile", "model_id", "identity_key", "home_node_id", "topology_id",
        "content_id", "backend", "activated_at",
    ):
        if not isinstance(stamp.get(field), str) or not stamp[field]:
            fail(f"schema-3 {field} is invalid")
    revision = stamp.get("revision")
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        fail("schema-3 revision is unsafe")
    if stamp["identity_key"] != f"{stamp['model_id']}@{revision}":
        fail("schema-3 identity_key differs from model/revision")
    if instance.name != stamp["content_id"]:
        fail("schema-3 content_id differs from managed instance layout")
    digest = stamp.get("content_digest")
    if not isinstance(digest, str) or SHA256_HEX_RE.fullmatch(digest) is None:
        fail("schema-3 content_digest is invalid")
    if stamp.get("state") not in {"ready", "pinned", "verifying"}:
        fail("schema-3 state is invalid")
    if not isinstance(stamp.get("pinned"), bool):
        fail("schema-3 pinned is not boolean")
    if (stamp["state"] == "pinned") != stamp["pinned"]:
        fail("schema-3 state and pinned fields differ")
    for field in ("bytes_logical", "budget_bytes_accounted"):
        value = stamp.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            fail(f"schema-3 {field} is invalid")
    if stamp["bytes_logical"] < 1:
        fail("schema-3 bytes_logical is invalid")
    if stamp["budget_bytes_accounted"] != stamp["bytes_logical"]:
        fail("schema-3 budget accounting differs from logical bytes")
    if "transport" in stamp and (
        not isinstance(stamp["transport"], str) or not stamp["transport"]
    ):
        fail("schema-3 transport is invalid")
    integrity = stamp.get("integrity")
    if (
        not isinstance(integrity, dict)
        or set(integrity) != {"scheme", "manifest"}
        or integrity.get("scheme") != SNAPSHOT_INTEGRITY_SCHEME
    ):
        fail("schema-3 integrity metadata is invalid")
    manifest = validate_snapshot_manifest(integrity.get("manifest"))
    validation = validate_hot_validation(
        stamp.get("validation"), profile=stamp["profile"], manifest=manifest
    )
    if manifest["model_id"] != stamp["model_id"]:
        fail("schema-3 model_id differs from manifest")
    if manifest["snapshot_revision"] != revision:
        fail("schema-3 revision differs from manifest")
    if manifest["manifest_id"] != digest:
        fail("schema-3 content_digest differs from manifest")
    if manifest["total_bytes"] != stamp["bytes_logical"]:
        fail("schema-3 logical bytes differ from manifest")
    return manifest, validation


def _schema3_hot_health(instance: pathlib.Path, stamp: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "metadata_valid": False,
        "runtime_source": "unknown",
        "identity_status": "unknown",
        "witness_status": "malformed",
        "expected_manifest": None,
        "detail": None,
    }
    model_id = stamp.get("model_id")
    if not isinstance(model_id, str) or not model_id:
        result["detail"] = "schema-3 model identity is invalid"
        return result
    try:
        manifest, validation = _validate_schema3_hot_metadata(instance, stamp)
    except ModelLibraryError as exc:
        result["detail"] = str(exc)
        return result
    result["metadata_valid"] = True
    hub = hot_hub_path(instance, model_id)
    hub_kind, _ = _lstat_kind(hub)
    if hub_kind == "symlink":
        result["runtime_source"] = "durable-home"
    elif hub_kind == "directory":
        result["runtime_source"] = "working-copy"
    else:
        result["detail"] = f"runtime view is {hub_kind}"
        return result
    result["expected_manifest"] = manifest["manifest_id"]
    result["identity_status"] = validation["identity_status"]
    try:
        observation = build_stable_hot_witness_observation(
            stamp, hub=hub, manifest=manifest, validation=validation
        )
        try:
            witness = load_hot_witness(instance)
        except ModelLibraryError as exc:
            result["witness_status"] = (
                "missing" if "missing:" in str(exc) else "malformed"
            )
            result["detail"] = str(exc)
        else:
            if hot_witness_observation(witness) == observation:
                result["witness_status"] = "match"
            else:
                result["witness_status"] = "drift"
                result["detail"] = "rank-local metadata differs from the witness"
    except ModelLibraryError as exc:
        result["detail"] = str(exc)
    return result

def scan_hot_health(
    hot_root: str | pathlib.Path, *, rank: int, node_id: str
) -> dict[str, Any]:
    """Shallow, no-follow scan of only the managed two-level hot layout."""
    root = pathlib.Path(hot_root).expanduser().absolute()
    instances: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    root_kind, _ = _lstat_kind(root)
    if root_kind == "missing":
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "kind": HOT_HEALTH_SCAN_KIND,
            "rank": rank,
            "node_id": node_id,
            "hot_root": str(root),
            "status": "ok",
            "instances": [],
            "errors": [],
        }
    if root_kind != "directory":
        errors.append({"path": str(root), "detail": f"hot root is {root_kind}"})
    else:
        try:
            groups = sorted(root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            groups = []
            errors.append({"path": str(root), "detail": f"cannot list hot root: {exc}"})
        for group in groups:
            group_kind, _ = _lstat_kind(group)
            if group.name.startswith("."):
                continue
            if group_kind == "symlink":
                errors.append({"path": str(group), "detail": "managed group is a symlink"})
                continue
            if group_kind != "directory":
                continue
            try:
                children = sorted(group.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                errors.append({"path": str(group), "detail": f"cannot list group: {exc}"})
                continue
            for instance in children:
                instance_kind, instance_meta = _lstat_kind(instance)
                if instance_kind == "symlink":
                    errors.append({"path": str(instance), "detail": "managed instance is a symlink"})
                    continue
                if instance_kind != "directory" or instance_meta is None:
                    continue
                metadata_dir = instance / ".pulsar"
                stamp_path = metadata_dir / "hot.json"
                metadata_kind, _ = _lstat_kind(metadata_dir)
                stamp_kind, _ = _lstat_kind(stamp_path)
                base: dict[str, Any] = {
                    "rank": rank,
                    "node_id": node_id,
                    "instance_dir": str(instance),
                    "runtime_source": "unknown",
                    "retention": "ephemeral",
                    "identity_status": "unknown",
                    "witness_status": "not-applicable",
                    "active_reference": False,
                    "repairable": False,
                    "repair_id": None,
                    "layout_state": "invalid",
                }
                if metadata_kind == "missing" and stamp_kind == "missing":
                    base.update({
                        "metadata_schema": None,
                        "metadata_status": "untracked",
                        "detail": "managed-layout directory has no hot metadata",
                    })
                    instances.append(base)
                    continue
                if metadata_kind != "directory" or stamp_kind != "file":
                    base.update({
                        "metadata_schema": None,
                        "metadata_status": "malformed",
                        "detail": "metadata directory or hot.json has an unsafe type",
                    })
                    instances.append(base)
                    continue
                try:
                    stamp = load_json(stamp_path)
                except ModelLibraryError as exc:
                    base.update({"metadata_schema": None, "metadata_status": "malformed", "detail": str(exc)})
                    instances.append(base)
                    continue
                schema = stamp.get("schema_version") if isinstance(stamp, dict) else None
                base.update({
                    "metadata_schema": schema,
                    "profile": stamp.get("profile") if isinstance(stamp, dict) else None,
                    "model_id": stamp.get("model_id") if isinstance(stamp, dict) else None,
                    "revision": stamp.get("revision") if isinstance(stamp, dict) else None,
                    "home_node_id": stamp.get("home_node_id") if isinstance(stamp, dict) else None,
                    "topology_id": stamp.get("topology_id") if isinstance(stamp, dict) else None,
                    "state": stamp.get("state") if isinstance(stamp, dict) else None,
                    "retention": "pinned" if isinstance(stamp, dict) and stamp.get("pinned") is True else "ephemeral",
                })
                if schema in LEGACY_HOT_SCHEMA_VERSIONS:
                    valid_owner, detail = _legacy_stamp_owner(stamp)
                    base["metadata_status"] = "legacy" if valid_owner else "malformed"
                    base["detail"] = detail
                    if valid_owner:
                        base["layout_state"] = _legacy_layout_state(instance, stamp)
                    instances.append(base)
                    continue
                if schema != HOT_SCHEMA_VERSION or not isinstance(stamp, dict):
                    base.update({"metadata_status": "unsupported", "detail": f"unsupported hot schema {schema!r}"})
                    instances.append(base)
                    continue
                base["metadata_status"] = "current"
                base["layout_state"] = "managed"
                schema3 = _schema3_hot_health(instance, stamp)
                base.update(schema3)
                if not schema3["metadata_valid"]:
                    base["metadata_status"] = "malformed"
                instances.append(base)
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "kind": HOT_HEALTH_SCAN_KIND,
        "rank": rank,
        "node_id": node_id,
        "hot_root": str(root),
        "status": "ok" if not errors else "error",
        "instances": instances,
        "errors": errors,
    }


def _load_container_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                value = json.loads(raw)
                if not isinstance(value, dict):
                    fail(f"container observation line {line_number} is not an object")
                values.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read container observations: {exc}")
    return values


def _managed_hot_reference(containers: list[dict[str, Any]], profile: Any) -> bool:
    for metadata in containers:
        labels = metadata.get("labels") or {}
        if not isinstance(labels, dict):
            return True
        if str(labels.get("io.pulsar.gb10.managed") or "") != "true":
            continue
        source = str(labels.get("io.pulsar.gb10.weight-source") or "")
        if source not in {"local-files", "fabric"}:
            continue
        observed_profile = str(labels.get("io.pulsar.gb10.conf") or "")
        if not observed_profile or not profile or observed_profile == profile:
            return True
    return False


def _load_health_observations(
    observations_dir: str | pathlib.Path,
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    directory = pathlib.Path(observations_dir)
    observed: list[dict[str, Any]] = []
    nodes = sorted(topology.get("nodes") or [], key=lambda item: int(item["rank"]))
    for node in nodes:
        rank = int(node["rank"])
        node_id = str(node.get("node_id") or "")
        scan_path = directory / f"hot-{rank}.json"
        container_path = directory / f"containers-{rank}.jsonl"
        if not scan_path.is_file() or not container_path.is_file():
            fail(f"rank {rank} observations are incomplete")
        scan = load_json(scan_path)
        if not isinstance(scan, dict) or scan.get("kind") != HOT_HEALTH_SCAN_KIND:
            fail(f"rank {rank} hot-health scan is invalid")
        if scan.get("rank") != rank or scan.get("node_id") != node_id:
            fail(f"rank {rank} observation identity differs from topology")
        containers = _load_container_jsonl(container_path)
        for item in scan.get("instances") or []:
            if isinstance(item, dict):
                item["active_reference"] = _managed_hot_reference(containers, item.get("profile"))
        observed.append({"rank": rank, "node_id": node_id, "scan": scan, "containers": containers})
    return observed



def _health_issue(
    code: str,
    detail: str,
    *,
    rank: int | None = None,
    repair_id: str | None = None,
    command: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {"code": code, "detail": detail}
    if rank is not None:
        issue["rank"] = rank
    if repair_id:
        issue["repair_id"] = repair_id
    if command:
        issue["remediation"] = {"command": command}
    return issue


def _append_cold_archive_health_issues(
    issues: list[dict[str, Any]],
    *,
    library_dir: str | pathlib.Path | None,
) -> None:
    if not library_dir:
        return
    try:
        from scripts import model_library_cold_archive as cold_archive
    except ModuleNotFoundError:
        try:
            import model_library_cold_archive as cold_archive  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return
    store = cold_archive.cold_archive_job_store(library_dir)
    if not store.is_dir():
        return
    for path in sorted(store.glob("*.json")):
        try:
            job = cold_archive.validate_cold_archive_job(load_json(path))
        except Exception:
            continue
        if job["state"] in {"pending", "running"}:
            issues.append(_health_issue(
                "cold-archive-pending",
                "receipt-indexed cold archive is pending (not a serving gate)",
                command="scripts/model-library.sh home archive run --receipt <id> --yes",
            ))
        elif job["state"] == "failed":
            issues.append(_health_issue(
                "cold-archive-failed",
                job.get("detail") or "receipt-indexed cold archive failed",
                command="scripts/model-library.sh home archive run --receipt <id> --yes",
            ))
        elif job["state"] == "unavailable":
            issues.append(_health_issue(
                "cold-archive-unavailable",
                "cold root is not configured; last-home remove needs --allow-unarchived-last-home",
            ))


def build_health_report(
    *,
    catalog_path: str | pathlib.Path,
    topology_file: str | pathlib.Path,
    topology_id: str,
    observations_dir: str | pathlib.Path,
    library_dir: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    try:
        topology = load_topology_for_plan(topology_file)
        observations = _load_health_observations(observations_dir, topology)
    except ModelLibraryError:
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "kind": HEALTH_KIND,
            "state": "unavailable",
            "catalog": {"status": "unavailable", "topology_compatible": None},
            "models": [],
            "hot_instances": [],
            "issues": [_health_issue(
                "observation-unavailable",
                "confirmed topology or rank observations are invalid",
            )],
        }
    issues: list[dict[str, Any]] = []
    observation_unavailable = False
    public_instances: list[dict[str, Any]] = []
    for observation in observations:
        rank = observation["rank"]
        scan = observation["scan"]
        if scan.get("status") != "ok":
            observation_unavailable = True
            issues.append(_health_issue(
                "rank-hot-unavailable",
                "managed hot layout could not be observed completely",
                rank=rank,
            ))
        for raw in scan.get("instances") or []:
            repair_id = raw.get("repair_id") if raw.get("repairable") else None
            public_instances.append({
                "rank": rank,
                "profile": raw.get("profile"),
                "model_id": raw.get("model_id"),
                "revision": raw.get("revision"),
                "metadata_schema": raw.get("metadata_schema"),
                "metadata_status": raw.get("metadata_status"),
                "runtime_source": raw.get("runtime_source") or "unknown",
                "retention": raw.get("retention") or "ephemeral",
                "identity_status": raw.get("identity_status") or "unknown",
                "witness_status": raw.get("witness_status") or "not-applicable",
                "active_reference": bool(raw.get("active_reference")),
                "repairable": bool(raw.get("repairable")),
                "repair_id": repair_id,
            })
            metadata_status = raw.get("metadata_status")
            if metadata_status == "legacy":
                issues.append(_health_issue(
                    "legacy-hot-metadata",
                    "schema-1/2 hot metadata is obsolete and never launchable",
                    rank=rank,
                ))
            elif metadata_status != "current":
                issues.append(_health_issue(
                    "hot-metadata-untrusted",
                    "hot metadata is malformed, unsupported, or untracked",
                    rank=rank,
                ))
            if metadata_status == "current":
                runtime = raw.get("runtime_source")
                on_home = observation["node_id"] == raw.get("home_node_id")
                if runtime == "unknown":
                    issues.append(_health_issue(
                        "runtime-view-unavailable",
                        "rank-local runtime view is missing or unsafe",
                        rank=rank,
                    ))
                if on_home and runtime == "working-copy":
                    issues.append(_health_issue("home-rank-materialized", "home rank has a prohibited hot copy", rank=rank))
                if not on_home and runtime == "durable-home":
                    issues.append(_health_issue("non-home-symlink", "non-home rank has a prohibited durable-home view", rank=rank))
                if raw.get("witness_status") != "match":
                    issues.append(_health_issue("witness-not-current", "serve witness is missing, malformed, or drifted", rank=rank))
    catalog_file = pathlib.Path(catalog_path)
    if not catalog_file.is_file():
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "kind": HEALTH_KIND,
            "state": (
                "unavailable" if observation_unavailable
                else "attention" if issues
                else "not-configured"
            ),
            "catalog": {"status": "absent", "topology_compatible": None},
            "models": [],
            "hot_instances": public_instances,
            "issues": issues,
        }
    try:
        catalog = load_catalog(catalog_file)
    except ModelLibraryError:
        issues.append(_health_issue(
            "catalog-invalid",
            "cached catalog is invalid or unreadable",
            command="scripts/model-library.sh catalog refresh",
        ))
        return {
            "schema_version": HEALTH_SCHEMA_VERSION,
            "kind": HEALTH_KIND,
            "state": "unavailable",
            "catalog": {"status": "invalid", "topology_compatible": None},
            "models": [],
            "hot_instances": public_instances,
            "issues": issues,
        }
    compatible = bool(topology_id and catalog.get("topology_id") == topology_id)
    if not compatible:
        issues.append(_health_issue("catalog-topology-stale", "cached catalog differs from confirmed topology", command="scripts/model-library.sh catalog refresh"))
    public_models: list[dict[str, Any]] = []
    for entry in catalog.get("models") or []:
        complete = policy_complete_homes(entry)
        unbound = unbound_complete_homes(entry)
        primary = entry.get("primary_selection") or {}
        duplicate = "none"
        if len(complete) > 1:
            duplicate = "redundant" if primary.get("status") == "match" else "unresolved"
            code = "duplicate-home-redundant" if duplicate == "redundant" else "duplicate-home-unresolved"
            command = None if duplicate == "redundant" else "scripts/model-library.sh catalog primary set <model> --node <rank>"
            issues.append(_health_issue(code, "exact revision has multiple durable homes", command=command))
        elif unbound:
            issues.append(_health_issue(
                "unbound-complete",
                "complete tree has no occupancy; relocate after a live receipt rehash",
                command="scripts/model-library.sh home relocate <model> --node <rank> --yes",
            ))
        if primary.get("status") == "stale":
            issues.append(_health_issue("primary-selection-stale", "selected primary is no longer a complete home", command="scripts/model-library.sh catalog primary clear <model>"))
        expected_manifest = None
        public_models.append({
            "model_id": entry.get("model_id"),
            "revision": entry.get("revision"),
            "profiles": sorted(
                str(profile)
                for profile in (entry.get("profiles") or [])
                if profile
            ),
            "expected_manifest": expected_manifest,
            "validation": entry.get("validation"),
            "home_ranks": sorted(int(home["rank"]) for home in complete),
            "primary": {
                "mode": primary.get("mode"),
                "status": primary.get("status"),
                "rank": next((int(home["rank"]) for home in complete if home.get("primary")), None),
            },
            "duplicate_home": duplicate,
        })
    _append_cold_archive_health_issues(issues, library_dir=library_dir)
    return {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "kind": HEALTH_KIND,
        "state": (
            "unavailable" if observation_unavailable
            else "attention" if issues
            else "healthy"
        ),
        "catalog": {
            "status": "cached",
            "topology_compatible": compatible,
            "refreshed_at": catalog.get("refreshed_at"),
        },
        "models": public_models,
        "hot_instances": public_instances,
        "issues": issues,
    }

def _nearest_existing_path(path: pathlib.Path) -> pathlib.Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return candidate


def home_acquisition_required_bytes(content_bytes: int) -> int:
    if isinstance(content_bytes, bool) or not isinstance(content_bytes, int):
        fail("home add: expected content size must be an integer")
    if content_bytes <= 0:
        fail("home add: expected content size must be positive")
    headroom = max(
        HOME_ACQUISITION_MIN_HEADROOM_BYTES,
        (content_bytes + 9) // 10,
    )
    return content_bytes + headroom


def valid_home_acquisition_hf_cli(value: Any) -> bool:
    if value in {"hf", "huggingface-cli", ""}:
        return True
    if (
        not isinstance(value, str)
        or not pathlib.PurePosixPath(value).is_absolute()
    ):
        return False
    return pathlib.PurePosixPath(value).parts[-4:] == (".hf-cli", "venv", "bin", "hf")


def inspect_home_acquisition_target(
    cache_root: str | pathlib.Path,
    *,
    model_id: str,
    revision: str,
    required_content_bytes: int,
    rank: int,
    node_id: str,
    hf_cli: str,
) -> dict[str, Any]:
    """Inspect one rank without creating a repository or staging tree."""
    if HF_MODEL_ID_RE.fullmatch(model_id) is None:
        fail("home add: model_id is invalid")
    if SAFE_REV.fullmatch(revision) is None:
        fail("home add: revision is invalid")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        fail("home add: rank is invalid")
    if not isinstance(node_id, str) or not node_id:
        fail("home add: node identity is invalid")
    if not valid_home_acquisition_hf_cli(hf_cli):
        fail("home add: Hugging Face CLI observation is invalid")

    cache = pathlib.Path(cache_root).expanduser()
    if not cache.is_absolute():
        fail("home add: cache root must be absolute")
    hub_root = cache / "hub"
    target = hub_root / model_id_to_hub_dirname(model_id)
    hub_kind, _ = _lstat_kind(hub_root)
    target_kind, _ = _lstat_kind(target)
    if hub_kind not in {"missing", "directory"}:
        target_state = "invalid"
        detail = f"managed hub root is an unsupported {hub_kind}"
    elif target_kind == "missing":
        target_state = "absent"
        detail = "exact repository path is available"
    elif target_kind == "directory":
        target_state = "occupied"
        detail = (
            "repository path already exists; refresh the catalog or reconcile "
            "the existing tree before adding another home"
        )
    else:
        target_state = "invalid"
        detail = f"repository path is an unsupported {target_kind}"

    probe = _nearest_existing_path(hub_root)
    try:
        available = shutil.disk_usage(probe).free
    except OSError as exc:
        fail(f"home add: cannot inspect cache filesystem: {exc}")
    writable = probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)
    required = home_acquisition_required_bytes(required_content_bytes)
    eligible = (
        target_state == "absent"
        and writable
        and bool(hf_cli)
        and available >= required
    )
    if target_state == "absent" and not hf_cli:
        detail = "Hugging Face CLI is not installed on this rank"
    elif target_state == "absent" and not writable:
        detail = "Hugging Face cache filesystem is not writable"
    elif target_state == "absent" and available < required:
        detail = "insufficient free space for verified same-filesystem staging"

    return {
        "schema_version": HOME_ACQUISITION_SCHEMA_VERSION,
        "kind": HOME_ACQUISITION_OBSERVATION_KIND,
        "rank": rank,
        "node_id": node_id,
        "cache_root": str(cache),
        "hub_root": str(hub_root),
        "target_hub": str(target),
        "model_id": model_id,
        "revision": revision,
        "required_content_bytes": required_content_bytes,
        "required_free_bytes": required,
        "available_bytes": available,
        "writable": writable,
        "hf_cli": hf_cli or None,
        "target_state": target_state,
        "eligible": eligible,
        "detail": detail,
    }


def _load_home_acquisition_observations(
    observations_dir: str | pathlib.Path,
    topology: dict[str, Any],
    *,
    model_id: str,
    revision: str,
    required_content_bytes: int,
) -> list[dict[str, Any]]:
    root = pathlib.Path(observations_dir)
    nodes = sorted(topology.get("nodes") or [], key=lambda item: int(item["rank"]))
    observations: list[dict[str, Any]] = []
    for node in nodes:
        rank = int(node["rank"])
        observation = load_json(root / f"rank-{rank}.json")
        if not isinstance(observation, dict):
            fail(f"home add: rank {rank} observation is not an object")
        expected = {
            "schema_version": HOME_ACQUISITION_SCHEMA_VERSION,
            "kind": HOME_ACQUISITION_OBSERVATION_KIND,
            "rank": rank,
            "node_id": node.get("node_id"),
            "model_id": model_id,
            "revision": revision,
            "required_content_bytes": required_content_bytes,
        }
        for field, value in expected.items():
            if observation.get(field) != value:
                fail(f"home add: rank {rank} observation {field} is stale")
        observations.append(observation)
    if len(observations) != len(nodes):
        fail("home add: not every confirmed rank was observed")
    return observations


def create_owned_hub_staging(
    hub_root: str | pathlib.Path,
    *,
    owner_id: str,
    rank: int,
    node_id: str,
) -> dict[str, Any]:
    """Create same-filesystem private staging owned by an approval/receipt id."""
    if SHA256_HEX_RE.fullmatch(str(owner_id) or "") is None:
        fail("home add: staging owner identity is invalid")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
        fail("home add: rank is invalid")
    if not isinstance(node_id, str) or not node_id:
        fail("home add: node identity is invalid")
    root = pathlib.Path(hub_root)
    if not root.is_absolute():
        fail("home add: hub root must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    if _lstat_kind(root)[0] != "directory":
        fail("home add: managed hub root is not an exact directory")
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".pulsar-acquire-", dir=str(root)))
    marker = {
        "schema_version": OWNED_HUB_STAGING_SCHEMA_VERSION,
        "kind": OWNED_HUB_STAGING_KIND,
        "owner_id": owner_id,
        "rank": rank,
        "node_id": node_id,
        "created_at": utc_now(),
    }
    atomic_write_json(staging / ".pulsar-home-acquisition.json", marker)
    return {
        "schema_version": OWNED_HUB_STAGING_SCHEMA_VERSION,
        "kind": OWNED_HUB_STAGING_KIND,
        "owner_id": owner_id,
        "rank": rank,
        "node_id": node_id,
        "staging_root": str(staging),
    }


def _validate_owned_hub_staging(
    staging_root: str | pathlib.Path,
    *,
    owner_id: str,
    rank: int,
    node_id: str,
    hub_root: str | pathlib.Path | None = None,
) -> pathlib.Path:
    staging = pathlib.Path(staging_root)
    if not staging.is_absolute() or not staging.name.startswith(".pulsar-acquire-"):
        fail("home add: staging root is outside the managed hub root")
    if hub_root is not None and staging.parent != pathlib.Path(hub_root):
        fail("home add: staging root is outside the managed hub root")
    if _lstat_kind(staging)[0] != "directory":
        fail("home add: staging root is not an exact directory")
    marker_path = staging / ".pulsar-home-acquisition.json"
    if _lstat_kind(marker_path)[0] != "file":
        fail("home add: staging ownership marker is not a regular file")
    marker = load_json(marker_path)
    expected = {
        "schema_version": OWNED_HUB_STAGING_SCHEMA_VERSION,
        "kind": OWNED_HUB_STAGING_KIND,
        "owner_id": owner_id,
        "rank": rank,
        "node_id": node_id,
    }
    if not isinstance(marker, dict) or any(
        marker.get(field) != value for field, value in expected.items()
    ):
        fail("home add: staging ownership marker does not match the approval")
    return staging


def cleanup_owned_hub_staging(
    staging_root: str | pathlib.Path,
    *,
    owner_id: str,
    rank: int,
    node_id: str,
    hub_root: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    staging = _validate_owned_hub_staging(
        staging_root,
        owner_id=owner_id,
        rank=rank,
        node_id=node_id,
        hub_root=hub_root,
    )
    shutil.rmtree(staging)
    parent_fd = os.open(staging.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    return {
        "schema_version": OWNED_HUB_STAGING_SCHEMA_VERSION,
        "kind": OWNED_HUB_STAGING_KIND,
        "state": "staging-removed",
        "owner_id": owner_id,
        "rank": rank,
    }


def _rename_directory_noreplace(source: pathlib.Path, target: pathlib.Path) -> None:
    """Atomically move one directory without replacing an existing target."""
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        fail("home add: exclusive durable-home publication is unavailable")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    if not hasattr(libc, "renameat2"):
        fail("home add: exclusive durable-home publication requires renameat2")
    renameat2 = libc.renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    source_fd = os.open(
        source.parent,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        target_fd = os.open(
            target.parent,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            result = renameat2(
                source_fd,
                os.fsencode(source.name),
                target_fd,
                os.fsencode(target.name),
                RENAME_NOREPLACE,
            )
            if result != 0:
                error = ctypes.get_errno()
                if error in {errno.EEXIST, errno.ENOTEMPTY}:
                    fail("home add: durable repository appeared before publication")
                fail("home add: exclusive durable-home publication failed")
            os.fsync(source_fd)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


def publish_owned_hub_staging(
    staging_root: str | pathlib.Path,
    *,
    owner_id: str,
    rank: int,
    node_id: str,
    model_id: str,
    target_hub: str | pathlib.Path,
    hub_root: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    staging = _validate_owned_hub_staging(
        staging_root,
        owner_id=owner_id,
        rank=rank,
        node_id=node_id,
        hub_root=hub_root,
    )
    staged_hub = staging / model_id_to_hub_dirname(model_id)
    if _lstat_kind(staged_hub)[0] != "directory":
        fail("home add: Hugging Face download did not create the expected repository")
    target = pathlib.Path(target_hub)
    expected_root = pathlib.Path(hub_root) if hub_root is not None else staging.parent
    expected_target = expected_root / model_id_to_hub_dirname(model_id)
    if (
        not target.is_absolute()
        or not expected_root.is_absolute()
        or target != expected_target
        or staging.parent != expected_root
    ):
        fail("home add: durable repository target differs from the managed hub root")
    if _lstat_kind(target)[0] != "missing":
        fail("home add: durable repository appeared before publication")
    target_path = _require_canonical_absolute_path(target, label="published directory")
    directory_fd = -1
    try:
        try:
            directory_fd = os.open(
                staged_hub,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
            )
            before = os.fstat(directory_fd)
            if not stat.S_ISDIR(before.st_mode):
                fail("home add: staged repository is not a directory")
            _rename_directory_noreplace(staged_hub, target)
            after = os.fstat(directory_fd)
            if not stat.S_ISDIR(after.st_mode):
                fail("home add: published repository is not a directory")
            directory_identity = {
                "schema_version": LIVE_DIRECTORY_IDENTITY_SCHEMA_VERSION,
                "kind": LIVE_DIRECTORY_IDENTITY_KIND,
                "path": target_path,
                "device": int(after.st_dev),
                "inode": int(after.st_ino),
                "ctime_ns": int(after.st_ctime_ns),
            }
        except OSError as exc:
            fail(f"home add: atomic durable-home publication failed: {exc}")
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)
    cleanup_state = "removed"
    try:
        shutil.rmtree(staging)
        parent_fd = os.open(staging.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError:
        cleanup_state = "incomplete"
    return {
        "schema_version": OWNED_HUB_STAGING_SCHEMA_VERSION,
        "kind": OWNED_HUB_STAGING_KIND,
        "state": "published",
        "owner_id": owner_id,
        "rank": rank,
        "target_hub": str(target),
        "staging_cleanup": cleanup_state,
        "directory_identity": directory_identity,
    }


def recheck_home_acquisition_absence(
    *,
    topology_file: str | pathlib.Path,
    topology_id: str,
    observations_dir: str | pathlib.Path,
    model_id: str,
    revision: str,
    required_content_bytes: int,
    selected_rank: int,
    selected_node_id: str,
    selected_target_hub: str,
) -> dict[str, Any]:
    topology = load_topology_for_plan(topology_file)
    if topology.get("topology_id") != topology_id:
        fail("home add: confirmed topology changed before publication")
    observations = _load_home_acquisition_observations(
        observations_dir,
        topology,
        model_id=model_id,
        revision=revision,
        required_content_bytes=required_content_bytes,
    )
    occupied = [item for item in observations if item.get("target_state") != "absent"]
    if occupied:
        ranks = ", ".join(str(item["rank"]) for item in occupied)
        fail(
            "home add: repository path appeared on confirmed rank(s) "
            f"{ranks} during download; refusing duplicate-home publication"
        )
    selected = [
        item
        for item in observations
        if item["rank"] == selected_rank
        and item["node_id"] == selected_node_id
        and item["target_hub"] == selected_target_hub
    ]
    if len(selected) != 1:
        fail("home add: selected durable-home target changed before publication")
    return {
        "schema_version": HOME_ACQUISITION_SCHEMA_VERSION,
        "kind": HOME_ACQUISITION_RECHECK_KIND,
        "state": "publication-clear",
        "observed_ranks": len(observations),
    }


def _home_revision_is_unbound(revision: Any) -> bool:
    if revision is None:
        return True
    if not isinstance(revision, str):
        return True
    return revision.strip().lower() in UNBOUND_HOME_REVISIONS


def _catalog_home_revision(entry: dict[str, Any]) -> str | None:
    revision = entry.get("revision")
    if _home_revision_is_unbound(revision):
        return None
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        return None
    return revision


def _home_occupancy_usable(home: dict[str, Any]) -> bool:
    required = ("rank", "node_id", "cache_root", "hub_path")
    return all(home.get(name) not in (None, "") for name in required)


def _bind_home_removal_revision(
    requested: str | None,
    ref_targets: list[dict[str, Any]],
) -> str | None:
    commits: list[str] = []
    for item in ref_targets:
        target = item.get("revision")
        if isinstance(target, str) and HF_EXACT_COMMIT_RE.fullmatch(target):
            commits.append(target)
    unique = sorted(set(commits))
    if not _home_revision_is_unbound(requested):
        assert requested is not None
        if SAFE_REV.fullmatch(requested) is None:
            return None
        if unique and any(commit != requested for commit in unique):
            return None
        return requested
    if len(unique) == 1:
        return unique[0]
    return None


def _hub_has_regular_payload(path: pathlib.Path) -> bool:
    if not path.is_dir() or path.is_symlink():
        return False
    try:
        for root, dirnames, filenames in os.walk(path, followlinks=False):
            current = pathlib.Path(root)
            dirnames[:] = [
                name
                for name in dirnames
                if not (current / name).is_symlink()
            ]
            for name in filenames:
                candidate = current / name
                try:
                    meta = candidate.lstat()
                except OSError:
                    return True
                if stat.S_ISREG(meta.st_mode) and meta.st_size > 0:
                    return True
    except OSError:
        return True
    return False


def _recognized_incomplete_hub_occupancy(
    hub: pathlib.Path,
    *,
    bound_revision: str | None,
    snapshot_entries: list[str],
    ref_targets: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (subtype, rejection) for a recognized incomplete HF hub tree."""
    if complete_snapshot_revisions(hub):
        return None, "complete-snapshot-present"
    try:
        children = list(hub.iterdir())
    except OSError as exc:
        return None, f"unreadable:{exc}"
    for child in children:
        try:
            meta = child.lstat()
        except OSError as exc:
            return None, f"unreadable:{exc}"
        if stat.S_ISLNK(meta.st_mode):
            return None, "top-level-symlink"
        if child.name not in HF_HUB_LAYOUT_NAMES:
            return None, f"unrecognized-top-level:{child.name}"
    if snapshot_entries and (
        not bound_revision
        or any(name != bound_revision for name in snapshot_entries)
    ):
        return None, "foreign-or-unbound-snapshot"
    has_snapshot = False
    if bound_revision:
        snapshot = hub / "snapshots" / bound_revision
        try:
            snap_meta = snapshot.lstat()
        except FileNotFoundError:
            snap_meta = None
        except OSError as exc:
            return None, f"unreadable:{exc}"
        else:
            if stat.S_ISLNK(snap_meta.st_mode):
                return None, "snapshot-symlink"
            has_snapshot = True
            if hub_snapshot_state(hub, bound_revision) == "complete":
                return None, "complete-snapshot-present"
    has_blob_payload = _hub_has_regular_payload(hub / "blobs")
    has_refs = bool(ref_targets) or (hub / "refs" / "main").is_file()
    if not has_refs and not has_snapshot:
        return None, "no-hf-hub-identity"
    if not has_snapshot and not has_blob_payload:
        return "refs-only", None
    return "incomplete-snapshot", None


def _incomplete_home_attachment_blockers(
    library_dir: str | pathlib.Path | None,
    *,
    hub_path: str,
    model_id: str,
    revision: str,
    rank: int,
    node_id: str,
) -> list[dict[str, Any]]:
    """Fail closed unless the live directory has no current-home attachment."""
    if library_dir in (None, ""):
        return [
            {
                "kind": "current-home-unobservable",
                "rank": rank,
                "node_id": node_id,
                "detail": (
                    "cannot prove this incomplete occupancy has no current-home "
                    "attachment; pass the site library directory to the planner"
                ),
            }
        ]
    store = pathlib.Path(library_dir) / SOURCE_ATTESTED_HOME_ATTACHMENT_STORE
    try:
        info = store.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        fail(f"home removal: current-home attachment store is unusable: {exc}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail("home removal: current-home attachment store is not a regular directory")
    try:
        entries = list(store.iterdir())
    except OSError as exc:
        fail(f"home removal: cannot enumerate current-home attachments: {exc}")
    blockers: list[dict[str, Any]] = []
    for path in entries:
        name = path.name
        if name.startswith(".") or not name.endswith(".json"):
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            document = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            fail(f"home removal: current-home attachment store is unusable: {exc}")
        if not isinstance(document, dict):
            fail("home removal: current-home attachment is not an object")
        attached_path = document.get("durable_home_path")
        same_path = attached_path == hub_path
        same_identity = (
            document.get("model_id") == model_id
            and document.get("snapshot_revision") == revision
            and same_path
        )
        if same_path or same_identity:
            blockers.append(
                {
                    "kind": "current-home-attached",
                    "rank": rank,
                    "node_id": node_id,
                    "detail": (
                        "a current-home attachment still names this live "
                        "directory; incomplete occupancy retirement refuses "
                        "attached or receipt-bound homes"
                    ),
                }
            )
    return blockers


def _home_removal_action(
    *,
    occupancy_class: str,
    occupancy_subtype: str | None,
    model_id: str,
    revision: str,
    last_durable_home: bool,
) -> dict[str, Any]:
    identity = f"{model_id}@{revision}" if not _home_revision_is_unbound(revision) else model_id
    last_flag = " --allow-last-home" if last_durable_home else ""
    confirmation = (
        f"after reviewing an eligible plan, run scripts/model-library.sh "
        f"home remove '{identity}'{last_flag} --yes; home check is read-only "
        "and never mutates"
    )
    if occupancy_class == INCOMPLETE_HUB_OCCUPANCY:
        stub = occupancy_subtype == "refs-only"
        return {
            "summary": (
                "retire this incomplete/refs-only Hugging Face hub occupancy "
                "so the exact repository path becomes absent"
            ),
            "enables": "later home add --revision of the same repository",
            "eligibility": [
                "incomplete/partial hub tree",
                "not a complete snapshot",
                "no receipt or current-home attachment",
                "no managed or hot dependents",
                "exclusive exact-repository tree",
            ],
            "will_delete": (
                "the exact hub repository directory, including this refs-only stub"
                if stub
                else "the exact hub repository directory, including this incomplete hub tree"
            ),
            "will_not_delete": [
                "sibling models",
                "other revisions",
                "hot trees",
                "receipts history",
                "running or unrelated containers",
            ],
            "confirmation": confirmation,
            "occupancy_class": occupancy_class,
            "occupancy_subtype": occupancy_subtype,
        }
    return {
        "summary": "retire this exact complete durable home repository",
        "enables": None,
        "eligibility": [
            "exact single-revision complete snapshot",
            "no managed or hot dependents",
            "exclusive exact-repository tree",
        ],
        "will_delete": "the exact hub repository directory for this revision",
        "will_not_delete": [
            "sibling models",
            "other revisions",
            "hot trees",
            "receipts history",
            "running or unrelated containers",
        ],
        "confirmation": confirmation,
        "occupancy_class": occupancy_class,
        "occupancy_subtype": occupancy_subtype or "complete-snapshot",
    }


def select_home_removal_target(
    catalog_path: str | pathlib.Path,
    query: str,
    *,
    node_selector: str = "",
) -> dict[str, Any]:
    """Resolve one exact durable hub occupancy for a destructive operation."""
    catalog = load_catalog(catalog_path)
    requested_revision: str | None = None
    entry: dict[str, Any] | None = None
    if "@" in query:
        entry = find_model_entry(catalog, identity_key=query)
        model_id, _sep, query_revision = query.partition("@")
        if not _home_revision_is_unbound(query_revision) and SAFE_REV.fullmatch(
            query_revision
        ):
            requested_revision = query_revision
        if entry is None and requested_revision:
            unknown = find_model_entry(
                catalog, identity_key=f"{model_id}@unknown"
            )
            if unknown is not None:
                entry = unknown
    elif "/" in query:
        entry = find_model_entry(catalog, model_id=query)
    else:
        entry = find_model_entry(catalog, profile=query)
    if entry is None:
        fail(f"home removal: no catalog entry matching {query!r}")

    catalog_revision = _catalog_home_revision(entry)
    homes = [dict(home) for home in entry.get("homes") or []]
    complete = [
        home
        for home in homes
        if home.get("state") == "complete" and _home_occupancy_usable(home)
    ]
    incomplete = [
        home
        for home in homes
        if home.get("state") != "complete" and _home_occupancy_usable(home)
    ]
    if not complete and not incomplete:
        fail("home removal: catalog entry has no inspectable durable occupancy")

    selected: dict[str, Any] | None = None
    occupancy_class = COMPLETE_HOME_OCCUPANCY
    if node_selector:
        matches_complete = [
            home
            for home in complete
            if str(home.get("rank")) == node_selector
            or str(home.get("node_id") or "") == node_selector
        ]
        matches_incomplete = [
            home
            for home in incomplete
            if str(home.get("rank")) == node_selector
            or str(home.get("node_id") or "") == node_selector
        ]
        matches = matches_complete + matches_incomplete
        if len(matches) != 1:
            fail(
                "home removal: --node must match exactly one durable occupancy "
                f"by rank or node ID (selector={node_selector!r})"
            )
        selected = matches[0]
        occupancy_class = (
            COMPLETE_HOME_OCCUPANCY
            if matches_complete
            else INCOMPLETE_HUB_OCCUPANCY
        )
    elif len(complete) == 1:
        selected = complete[0]
        occupancy_class = COMPLETE_HOME_OCCUPANCY
    elif len(complete) > 1:
        primaries = [home for home in complete if home.get("primary")]
        if len(primaries) == 1:
            selected = primaries[0]
            occupancy_class = COMPLETE_HOME_OCCUPANCY
        else:
            fail(
                "home removal: duplicate homes have no unique primary; "
                "pass --node with the exact rank or node ID"
            )
    elif len(incomplete) == 1:
        selected = incomplete[0]
        occupancy_class = INCOMPLETE_HUB_OCCUPANCY
    else:
        fail(
            "home removal: multiple incomplete occupancies; "
            "pass --node with the exact rank or node ID"
        )

    assert selected is not None
    required = ("rank", "node_id", "cache_root", "hub_path")
    missing = [name for name in required if selected.get(name) in (None, "")]
    if missing:
        fail(f"home removal: catalog home is incomplete (missing={missing})")
    try:
        rank = int(selected["rank"])
    except (TypeError, ValueError):
        fail("home removal: catalog home rank is invalid")
    selected["rank"] = rank

    if occupancy_class == COMPLETE_HOME_OCCUPANCY and catalog_revision is None:
        fail("home removal: catalog entry lacks an exact snapshot revision")

    revision = catalog_revision or requested_revision or "unknown"
    if occupancy_class == COMPLETE_HOME_OCCUPANCY:
        if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
            fail("home removal: catalog entry lacks an exact snapshot revision")
    identity_unbound = catalog_revision is None
    if identity_unbound:
        identity_key = str(entry.get("identity_key") or f"{entry['model_id']}@unknown")
    else:
        identity_key = str(entry.get("identity_key") or f"{entry['model_id']}@{revision}")

    classified = catalog_homes_are_classified(homes)
    selected_identity = _catalog_home_identity(selected)
    occupancy_alternates = [
        home
        for home in complete
        if catalog_home_is_occupancy(home, classified=classified)
        and _catalog_home_identity(home) != selected_identity
    ]
    other_incomplete = [
        home
        for home in incomplete
        if _catalog_home_identity(home) != selected_identity
    ]
    selected_is_occupancy = catalog_home_is_occupancy(
        selected, classified=classified
    )
    if occupancy_class == COMPLETE_HOME_OCCUPANCY:
        last_durable_home = selected_is_occupancy and not occupancy_alternates
    else:
        last_durable_home = not bool(occupancy_alternates or other_incomplete)
    return {
        "topology_id": catalog.get("topology_id") or "",
        "model_id": entry["model_id"],
        "revision": revision,
        "catalog_revision": catalog_revision,
        "identity_key": identity_key,
        "identity_unbound": identity_unbound,
        "occupancy_class": occupancy_class,
        "profiles": sorted(entry.get("profiles") or []),
        "primary_selection": entry.get("primary_selection") or {},
        "home": selected,
        "alternate_homes": occupancy_alternates,
        "last_durable_home": last_durable_home,
        "selected_is_occupancy": selected_is_occupancy,
    }


def _path_metadata(path: pathlib.Path, *, relative_to: pathlib.Path) -> dict[str, Any]:
    metadata = path.lstat()
    if stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
    elif stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    return {
        "path": path.relative_to(relative_to).as_posix() or ".",
        "kind": kind,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


def _collect_tree_fingerprint_paths(
    root: pathlib.Path,
) -> tuple[list[pathlib.Path], list[str]]:
    """List a no-follow tree for fingerprinting. Errors are fail-closed."""
    paths = [root]
    errors: list[str] = []

    def on_walk_error(exc: OSError) -> None:
        errors.append(str(exc))

    try:
        root.lstat()
    except OSError as exc:
        return [], [f"cannot fingerprint {root}: {exc}"]
    for dirpath, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=on_walk_error,
    ):
        current = pathlib.Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            try:
                child.lstat()
            except OSError as exc:
                errors.append(f"cannot fingerprint {child}: {exc}")
                continue
            paths.append(child)
            if child.is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            child = current / name
            try:
                child.lstat()
            except OSError as exc:
                errors.append(f"cannot fingerprint {child}: {exc}")
                continue
            paths.append(child)
    return paths, errors


def inspect_removable_home(
    hub_path: str | pathlib.Path,
    *,
    cache_root: str | pathlib.Path,
    model_id: str,
    revision: str,
    rank: int,
    node_id: str,
) -> dict[str, Any]:
    """Validate that an exact catalog home is a safely removable HF repo tree."""
    if HF_MODEL_ID_RE.fullmatch(model_id) is None:
        fail("home removal: model_id is invalid")
    requested_revision = revision
    unbound = _home_revision_is_unbound(revision)
    if not unbound and SAFE_REV.fullmatch(revision) is None:
        fail("home removal: revision is invalid")

    hub = pathlib.Path(hub_path).expanduser()
    cache = pathlib.Path(cache_root).expanduser()
    expected = cache / "hub" / model_id_to_hub_dirname(model_id)
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "pulsar-model-library-removable-home-inspection",
        "rank": rank,
        "node_id": node_id,
        "model_id": model_id,
        "revision": revision,
        "requested_revision": requested_revision,
        "bound_revision": None,
        "occupancy_class": UNRECOGNIZED_HUB_OCCUPANCY,
        "occupancy_subtype": None,
        "cache_root": str(cache),
        "hub_path": str(hub),
        "canonical_hub_path": None,
        "repository_bytes": 0,
        "snapshot_entries": [],
        "ref_targets": [],
        "fingerprint": None,
        "occupancy_device": None,
        "blockers": [],
    }
    blockers: list[dict[str, str]] = result["blockers"]

    def block(code: str, detail: str) -> None:
        blockers.append({"code": code, "detail": detail})

    if not hub.is_absolute() or not cache.is_absolute():
        block("path-not-absolute", "cache_root and hub_path must be absolute")
    if pathlib.Path(os.path.abspath(hub)) != pathlib.Path(os.path.abspath(expected)):
        block("path-not-exact-hub-child", f"hub_path must equal {expected}")

    try:
        hub_meta = hub.lstat()
    except OSError as exc:
        block("home-unavailable", f"cannot inspect durable home: {exc}")
        result["state"] = "blocked"
        return result
    result["occupancy_device"] = int(hub_meta.st_dev)
    if stat.S_ISLNK(hub_meta.st_mode):
        block("home-is-symlink", "durable home repository root must not be a symlink")
    elif not stat.S_ISDIR(hub_meta.st_mode):
        block("home-not-directory", "durable home repository root is not a directory")

    try:
        canonical_hub = hub.resolve(strict=True)
        canonical_hub_parent = (cache / "hub").resolve(strict=True)
        if canonical_hub.parent != canonical_hub_parent:
            block(
                "canonical-path-escape",
                "canonical durable home is not an exact child of the cache hub root",
            )
        result["canonical_hub_path"] = str(canonical_hub)
    except OSError as exc:
        block("canonical-path-unavailable", f"cannot resolve durable home: {exc}")

    snapshots = hub / "snapshots"
    snapshots_present = False
    snapshots_usable = False
    try:
        snapshots_meta = snapshots.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        block("snapshots-unavailable", f"cannot inspect snapshots directory: {exc}")
    else:
        snapshots_present = True
        if stat.S_ISLNK(snapshots_meta.st_mode):
            block("snapshots-is-symlink", "snapshots directory must not be a symlink")
        elif not stat.S_ISDIR(snapshots_meta.st_mode):
            block("snapshots-not-directory", "snapshots path is not a directory")
        else:
            snapshots_usable = True
    snapshot_children: list[pathlib.Path] = []
    if snapshots_usable:
        try:
            snapshot_children = sorted(snapshots.iterdir(), key=lambda item: item.name)
            result["snapshot_entries"] = [item.name for item in snapshot_children]
        except OSError as exc:
            block("snapshots-unavailable", f"cannot enumerate snapshots: {exc}")

    refs = hub / "refs"
    ref_paths: list[pathlib.Path] = []
    refs_present = False
    refs_usable = False
    try:
        refs_meta = refs.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        block("refs-unreadable", f"cannot inspect refs directory: {exc}")
    else:
        refs_present = True
        if stat.S_ISLNK(refs_meta.st_mode):
            block("refs-is-symlink", "refs directory must not be a symlink")
        elif not stat.S_ISDIR(refs_meta.st_mode):
            block("refs-not-directory", "refs path is not a directory")
        else:
            refs_usable = True
    if refs_usable:
        walk_errors: list[str] = []

        def on_walk_error(exc: OSError) -> None:
            walk_errors.append(str(exc))

        for dirpath, dirnames, filenames in os.walk(
            refs,
            followlinks=False,
            onerror=on_walk_error,
        ):
            root = pathlib.Path(dirpath)
            kept: list[str] = []
            for name in dirnames:
                candidate = root / name
                try:
                    if candidate.is_symlink():
                        block("ref-symlink", f"ref directory is a symlink: {candidate}")
                    else:
                        kept.append(name)
                except OSError as exc:
                    block("ref-unreadable", f"cannot inspect ref directory: {exc}")
            dirnames[:] = kept
            for name in filenames:
                ref_paths.append(root / name)
        for detail in walk_errors:
            block("refs-unreadable", detail)

    ref_targets: list[dict[str, str]] = []
    for ref_path in sorted(ref_paths, key=lambda item: item.as_posix()):
        try:
            ref_meta = ref_path.lstat()
            if not stat.S_ISREG(ref_meta.st_mode):
                block("ref-not-regular", f"ref is not a regular file: {ref_path}")
                continue
            target = ref_path.read_text(encoding="utf-8").strip().replace("\r", "")
        except (OSError, UnicodeError) as exc:
            block("ref-unreadable", f"cannot read ref {ref_path}: {exc}")
            continue
        relative = ref_path.relative_to(hub).as_posix()
        ref_targets.append({"path": relative, "revision": target})
    result["ref_targets"] = ref_targets

    bound = _bind_home_removal_revision(
        None if unbound else requested_revision,
        ref_targets,
    )
    if bound is None and not unbound:
        bound = requested_revision if SAFE_REV.fullmatch(requested_revision) else None
    result["bound_revision"] = bound
    if bound:
        result["revision"] = bound
    if bound is None:
        block(
            "unknown-revision",
            "live inspection cannot bind one exact snapshot revision "
            "without inventing a seal",
        )
    else:
        unexpected = [
            item.name for item in snapshot_children if item.name != bound
        ]
        if unexpected:
            block(
                "multiple-snapshot-revisions",
                "repository contains other snapshot entries: " + ", ".join(unexpected),
            )
        for item in ref_targets:
            if item["revision"] != bound:
                block(
                    "ref-target-differs",
                    f"{item['path']} points to {item['revision']!r}, not {bound!r}",
                )

    complete_revisions = complete_snapshot_revisions(hub)
    occupancy_class = UNRECOGNIZED_HUB_OCCUPANCY
    occupancy_subtype: str | None = None
    if bound and bound in complete_revisions:
        occupancy_class = COMPLETE_HOME_OCCUPANCY
        occupancy_subtype = "complete-snapshot"
        snapshot = snapshots / bound
        if snapshots_usable:
            try:
                snapshot_meta = snapshot.lstat()
                if not stat.S_ISDIR(snapshot_meta.st_mode):
                    block("snapshot-not-directory", "exact snapshot is not a directory")
            except OSError as exc:
                block("snapshot-unavailable", f"cannot inspect exact snapshot: {exc}")
            if hub_snapshot_state(hub, bound) != "complete":
                block("snapshot-incomplete", "exact snapshot is not complete")
        else:
            block("snapshots-unavailable", "cannot inspect snapshots directory")
        if _has_incomplete_marker(hub):
            block("incomplete-download", "repository contains an .incomplete marker")
    elif complete_revisions:
        occupancy_class = COMPLETE_HOME_OCCUPANCY
        occupancy_subtype = "complete-snapshot"
        block(
            "complete-snapshot-present",
            "a complete snapshot occupies this repository; "
            "complete homes keep the complete-home removal contract",
        )
    else:
        subtype, rejection = _recognized_incomplete_hub_occupancy(
            hub,
            bound_revision=bound,
            snapshot_entries=result["snapshot_entries"],
            ref_targets=ref_targets,
        )
        if subtype is not None:
            occupancy_class = INCOMPLETE_HUB_OCCUPANCY
            occupancy_subtype = subtype
        else:
            occupancy_class = UNRECOGNIZED_HUB_OCCUPANCY
            occupancy_subtype = None
            if rejection != "complete-snapshot-present":
                block(
                    "unrecognized-hub-tree",
                    "hub tree is not a complete snapshot and not a recognized "
                    f"incomplete occupancy ({rejection or 'unknown-shape'})",
                )
    result["occupancy_class"] = occupancy_class
    result["occupancy_subtype"] = occupancy_subtype
    if occupancy_class == INCOMPLETE_HUB_OCCUPANCY and (
        not isinstance(bound, str) or HF_EXACT_COMMIT_RE.fullmatch(bound) is None
    ):
        block(
            "unknown-revision",
            "live inspection cannot bind one exact 40-hex snapshot revision "
            "without inventing a seal",
        )
        result["bound_revision"] = None
    result["repository_bytes"] = tree_bytes(hub)

    snapshot = snapshots / (bound or requested_revision or "unknown")
    fingerprint_paths = [hub]
    if snapshots_present:
        fingerprint_paths.append(snapshots)
    try:
        snapshot.lstat()
        fingerprint_paths.append(snapshot)
    except OSError:
        pass
    if refs_present:
        fingerprint_paths.append(refs)
    fingerprint_paths.extend(ref_paths)
    if occupancy_class == INCOMPLETE_HUB_OCCUPANCY:
        payload_roots = [hub / "blobs"]
        if snapshots_present:
            payload_roots.append(snapshot)
        for payload_root in payload_roots:
            try:
                payload_root.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                block(
                    "metadata-unavailable",
                    f"cannot fingerprint {payload_root}: {exc}",
                )
                continue
            extra, walk_errors = _collect_tree_fingerprint_paths(payload_root)
            fingerprint_paths.extend(extra)
            for detail in walk_errors:
                block("metadata-unavailable", detail)
    unique_fingerprint_paths: list[pathlib.Path] = []
    seen_fingerprint_paths: set[str] = set()
    for path in fingerprint_paths:
        key = str(path)
        if key in seen_fingerprint_paths:
            continue
        seen_fingerprint_paths.add(key)
        unique_fingerprint_paths.append(path)
    fingerprint_records: list[dict[str, Any]] = []
    for path in unique_fingerprint_paths:
        try:
            fingerprint_records.append(_path_metadata(path, relative_to=hub))
        except (OSError, ValueError) as exc:
            block("metadata-unavailable", f"cannot fingerprint {path}: {exc}")
    fingerprint_records.sort(key=lambda item: str(item.get("path") or ""))
    fingerprint_payload = {
        "model_id": model_id,
        "revision": result["revision"],
        "occupancy_class": occupancy_class,
        "occupancy_subtype": occupancy_subtype,
        "canonical_hub_path": result["canonical_hub_path"],
        "snapshot_entries": result["snapshot_entries"],
        "ref_targets": result["ref_targets"],
        "repository_bytes": result["repository_bytes"],
        "paths": fingerprint_records,
    }
    result["fingerprint"] = canonical_json_digest(fingerprint_payload)
    result["state"] = "eligible" if not blockers else "blocked"
    return result


def scan_home_hot_references(
    hot_root: str | pathlib.Path,
    *,
    rank: int,
    node_id: str,
) -> dict[str, Any]:
    """Scan managed hot stamps without following durable-home view symlinks."""
    root = pathlib.Path(hot_root).expanduser()
    references: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        root.lstat()
    except FileNotFoundError:
        return {
            "schema_version": 1,
            "kind": "pulsar-model-library-home-hot-reference-scan",
            "rank": rank,
            "node_id": node_id,
            "hot_root": str(root),
            "status": "ok",
            "references": references,
            "errors": errors,
        }
    except OSError as exc:
        errors.append({"path": str(root), "detail": f"cannot inspect hot root: {exc}"})
    if not errors and not root.is_dir():
        errors.append({"path": str(root), "detail": "hot root is not a directory"})
    if not errors:
        walk_errors: list[OSError] = []
        for dirpath, dirnames, filenames in os.walk(
            root,
            followlinks=False,
            onerror=walk_errors.append,
        ):
            current = pathlib.Path(dirpath)
            kept: list[str] = []
            for name in dirnames:
                child = current / name
                if child.is_symlink():
                    if name == ".pulsar":
                        errors.append(
                            {
                                "path": str(child),
                                "detail": "managed metadata directory is a symlink",
                            }
                        )
                    continue
                kept.append(name)
            dirnames[:] = kept
            if current.name != ".pulsar" or "hot.json" not in filenames:
                continue
            stamp_path = current / "hot.json"
            try:
                stamp = load_json(stamp_path)
            except ModelLibraryError as exc:
                errors.append({"path": str(stamp_path), "detail": str(exc)})
                continue
            if not isinstance(stamp, dict):
                errors.append({"path": str(stamp_path), "detail": "hot stamp is not an object"})
                continue
            missing = [
                field
                for field in ("profile", "model_id", "revision", "home_node_id", "state")
                if stamp.get(field) in (None, "")
            ]
            if stamp.get("schema_version") != HOT_SCHEMA_VERSION:
                errors.append(
                    {
                        "path": str(stamp_path),
                        "detail": f"unsupported hot schema {stamp.get('schema_version')!r}",
                    }
                )
            if missing:
                errors.append(
                    {
                        "path": str(stamp_path),
                        "detail": f"hot stamp fields missing: {missing}",
                    }
                )
            references.append(
                {
                    "rank": rank,
                    "node_id": node_id,
                    "instance_dir": str(current.parent),
                    "schema_version": stamp.get("schema_version"),
                    "profile": stamp.get("profile"),
                    "model_id": stamp.get("model_id"),
                    "revision": stamp.get("revision"),
                    "home_node_id": stamp.get("home_node_id"),
                    "content_id": stamp.get("content_id"),
                    "state": stamp.get("state"),
                    "pinned": bool(stamp.get("pinned")),
                }
            )
        errors.extend({"path": str(root), "detail": str(exc)} for exc in walk_errors)
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-home-hot-reference-scan",
        "rank": rank,
        "node_id": node_id,
        "hot_root": str(root),
        "status": "ok" if not errors else "error",
        "references": references,
        "errors": errors,
    }


def _load_home_reference_observations(
    observations_dir: str | pathlib.Path,
    topology: dict[str, Any],
) -> list[dict[str, Any]]:
    directory = pathlib.Path(observations_dir)
    observations: list[dict[str, Any]] = []
    for node in sorted(topology.get("nodes") or [], key=lambda item: int(item["rank"])):
        rank = int(node["rank"])
        node_id = str(node.get("node_id") or "")
        hot_path = directory / f"hot-{rank}.json"
        containers_path = directory / f"containers-{rank}.jsonl"
        if not hot_path.is_file() or not containers_path.is_file():
            fail(f"home removal: rank {rank} reference probes are incomplete")
        hot_scan = load_json(hot_path)
        if not isinstance(hot_scan, dict):
            fail(f"home removal: rank {rank} hot scan is not an object")
        if hot_scan.get("rank") != rank or hot_scan.get("node_id") != node_id:
            fail(f"home removal: rank {rank} hot scan identity differs from topology")
        if (
            hot_scan.get("schema_version") != 1
            or hot_scan.get("kind") != "pulsar-model-library-home-hot-reference-scan"
        ):
            fail(f"home removal: rank {rank} hot scan contract is unsupported")
        status = hot_scan.get("status")
        errors = hot_scan.get("errors")
        references = hot_scan.get("references")
        if status not in {"ok", "error"}:
            fail(f"home removal: rank {rank} hot scan status is invalid")
        if not isinstance(errors, list) or any(
            not isinstance(item, dict) for item in errors
        ):
            fail(f"home removal: rank {rank} hot scan errors are invalid")
        if not isinstance(references, list) or any(
            not isinstance(item, dict) for item in references
        ):
            fail(f"home removal: rank {rank} hot scan references are invalid")
        if status == "error" and not errors:
            fail(f"home removal: rank {rank} hot error lacks diagnostic details")
        if status == "ok" and errors:
            fail(f"home removal: rank {rank} hot scan status contradicts its errors")
        containers: list[dict[str, Any]] = []
        try:
            with open(containers_path, encoding="utf-8") as handle:
                for line_number, raw in enumerate(handle, start=1):
                    if not raw.strip():
                        continue
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        fail(
                            "home removal: container observation must be an object "
                            f"({containers_path}:{line_number})"
                        )
                    containers.append(value)
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"home removal: cannot read container observations: {exc}")
        observations.append(
            {
                "rank": rank,
                "node_id": node_id,
                "hostname": str(node.get("hostname") or ""),
                "hot_scan": hot_scan,
                "containers": containers,
            }
        )
    return observations


def _profile_model_map(models_dir: str | pathlib.Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    root = pathlib.Path(models_dir)
    for path in sorted(root.glob("*.conf")):
        parsed = parse_profile_conf_any(path)
        if parsed and parsed.get("model_id"):
            mapping[path.stem] = str(parsed["model_id"])
    return mapping


def _container_home_blocker(
    metadata: dict[str, Any],
    observation: dict[str, Any],
    target: dict[str, Any],
    profile_models: dict[str, str],
) -> dict[str, Any] | None:
    labels = metadata.get("labels") or {}
    if not isinstance(labels, dict):
        fail("home removal: container labels are not an object")
    managed = str(labels.get("io.pulsar.gb10.managed", "") or "")
    if managed != "true":
        return None
    source = str(labels.get("io.pulsar.gb10.weight-source", "") or "")
    profile = str(labels.get("io.pulsar.gb10.conf", "") or "")
    owner = str(labels.get("io.pulsar.gb10.weight-owner", "") or "")
    revision = str(labels.get("io.pulsar.gb10.model-revision", "") or "")
    profile_model = profile_models.get(profile)
    profile_matches = (
        profile in set(target.get("profiles") or [])
        or profile_model == target["model_id"]
    )
    owner_matches = owner == target["home"]["node_id"]
    on_home_node = observation["node_id"] == target["home"]["node_id"]

    if source == "replicated":
        depends = on_home_node and (profile_matches or profile_model is None)
    elif source in {"local-files", "fabric"}:
        depends = owner_matches and (
            profile_matches
            or revision == target["revision"]
            or profile_model is None
        )
    else:
        depends = on_home_node and (profile_matches or profile_model is None)
    if not depends:
        return None
    return {
        "kind": "container-reference",
        "rank": observation["rank"],
        "node_id": observation["node_id"],
        "name": str(metadata.get("name") or "").lstrip("/"),
        "container_id": str(metadata.get("id") or "")[:12],
        "profile": profile or None,
        "weight_source": source or "unknown",
        "owner_node_id": owner or None,
        "revision": revision or None,
        "detail": "managed container still references this durable repository",
    }


def home_removal_plan_id(plan: dict[str, Any]) -> str:
    return canonical_json_digest(
        {
            key: value
            for key, value in plan.items()
            if key not in {"plan_id", "created_at"}
        }
    )


def plan_home_removal(
    *,
    catalog_path: str | pathlib.Path,
    query: str,
    topology_file: str | pathlib.Path,
    topology_id: str,
    models_dir: str | pathlib.Path,
    inspection_path: str | pathlib.Path,
    observations_dir: str | pathlib.Path,
    node_selector: str = "",
    allow_last_home: bool = False,
    allow_unarchived_last_home: bool = False,
    library_dir: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    topology = load_topology_for_plan(topology_file)
    live_topology_id = str(topology.get("topology_id") or "")
    if live_topology_id != topology_id:
        fail("home removal: loaded topology differs from controller topology")
    target = select_home_removal_target(
        catalog_path,
        query,
        node_selector=node_selector,
    )
    if target["topology_id"] != topology_id:
        fail("home removal: catalog topology is stale; run catalog refresh")
    ranks = {int(node["rank"]): node for node in topology.get("nodes") or []}
    home = target["home"]
    rank = int(home["rank"])
    if rank not in ranks or ranks[rank].get("node_id") != home.get("node_id"):
        fail("home removal: catalog home identity differs from confirmed topology")

    inspection = load_json(inspection_path)
    if not isinstance(inspection, dict):
        fail("home removal: home inspection is not an object")
    for field, expected in (
        ("rank", rank),
        ("node_id", home["node_id"]),
        ("model_id", target["model_id"]),
        ("cache_root", home["cache_root"]),
        ("hub_path", home["hub_path"]),
    ):
        if inspection.get(field) != expected:
            fail(f"home removal: inspection {field} differs from catalog target")
    if not _home_revision_is_unbound(target["revision"]):
        inspected_revision = inspection.get("bound_revision") or inspection.get(
            "revision"
        )
        if inspected_revision != target["revision"]:
            fail("home removal: inspection revision differs from catalog target")
    occupancy_class = (
        inspection.get("occupancy_class")
        or target.get("occupancy_class")
        or COMPLETE_HOME_OCCUPANCY
    )
    occupancy_subtype = inspection.get("occupancy_subtype")
    bound = inspection.get("bound_revision") or inspection.get("revision")
    if occupancy_class == INCOMPLETE_HUB_OCCUPANCY:
        if not isinstance(bound, str) or HF_EXACT_COMMIT_RE.fullmatch(bound) is None:
            inspection.setdefault("blockers", []).append(
                {
                    "code": "unknown-revision",
                    "detail": (
                        "incomplete occupancy retirement requires live inspection "
                        "to bind one exact 40-hex commit without inventing a seal"
                    ),
                }
            )
        else:
            target["revision"] = bound
            target["identity_key"] = f"{target['model_id']}@{bound}"
            target["identity_unbound"] = False
            catalog = load_catalog(catalog_path)
            bound_entry = find_model_entry(
                catalog, identity_key=f"{target['model_id']}@{bound}"
            )
            bound_complete = [
                candidate
                for candidate in (bound_entry or {}).get("homes") or []
                if candidate.get("state") == "complete"
                and not (
                    int(candidate.get("rank", -1)) == rank
                    and candidate.get("node_id") == home.get("node_id")
                    and candidate.get("hub_path") == home.get("hub_path")
                )
            ]
            if bound_complete:
                # A leftover stub is not last occupancy when a complete
                # survivor already exists. Do not reuse complete-home
                # duplicate/primary policy for this incomplete target.
                target["last_durable_home"] = False
    elif occupancy_class == COMPLETE_HOME_OCCUPANCY:
        target["occupancy_class"] = COMPLETE_HOME_OCCUPANCY
    target["occupancy_class"] = occupancy_class
    target["occupancy_subtype"] = occupancy_subtype

    observations = _load_home_reference_observations(observations_dir, topology)
    profile_models = _profile_model_map(models_dir)
    blockers: list[dict[str, Any]] = []
    for item in inspection.get("blockers") or []:
        blockers.append(
            {
                "kind": "home-shape",
                "rank": rank,
                "node_id": home["node_id"],
                "code": item.get("code"),
                "detail": item.get("detail"),
            }
        )
    if occupancy_class == INCOMPLETE_HUB_OCCUPANCY:
        blockers.extend(
            _incomplete_home_attachment_blockers(
                library_dir,
                hub_path=str(home["hub_path"]),
                model_id=str(target["model_id"]),
                revision=str(target["revision"]),
                rank=rank,
                node_id=str(home["node_id"]),
            )
        )
    observed_nodes: list[dict[str, Any]] = []
    for observation in observations:
        hot_scan = observation["hot_scan"]
        for error in hot_scan.get("errors") or []:
            blockers.append(
                {
                    "kind": "observability",
                    "rank": observation["rank"],
                    "node_id": observation["node_id"],
                    "path": error.get("path"),
                    "detail": error.get("detail") or "hot state is unobservable",
                }
            )
        hot_matches = 0
        for reference in hot_scan.get("references") or []:
            if (
                reference.get("home_node_id") == home["node_id"]
                and reference.get("model_id") == target["model_id"]
            ):
                hot_matches += 1
                blockers.append(
                    {
                        "kind": "hot-reference",
                        "rank": observation["rank"],
                        "node_id": observation["node_id"],
                        "profile": reference.get("profile"),
                        "revision": reference.get("revision"),
                        "state": reference.get("state"),
                        "pinned": bool(reference.get("pinned")),
                        "content_id": reference.get("content_id"),
                        "instance_dir": reference.get("instance_dir"),
                        "detail": (
                            "pinned hot view depends on this home"
                            if reference.get("pinned")
                            else "retained managed hot view depends on this home"
                        ),
                    }
                )
        container_matches = 0
        for metadata in observation["containers"]:
            blocker = _container_home_blocker(
                metadata,
                observation,
                target,
                profile_models,
            )
            if blocker is not None:
                container_matches += 1
                blockers.append(blocker)
        observed_nodes.append(
            {
                "rank": observation["rank"],
                "node_id": observation["node_id"],
                "hostname": observation["hostname"],
                "hot_probe": hot_scan.get("status"),
                "hot_references": len(hot_scan.get("references") or []),
                "matching_hot_references": hot_matches,
                "managed_containers": len(observation["containers"]),
                "matching_containers": container_matches,
            }
        )

    if target["last_durable_home"] and not allow_last_home:
        if occupancy_class == INCOMPLETE_HUB_OCCUPANCY:
            last_home_detail = (
                "this is the last occupancy of this identity; "
                "pass --allow-last-home to acknowledge retiring the incomplete "
                "hub path"
            )
        else:
            last_home_detail = (
                "this is the last complete durable home for the exact revision; "
                "pass --allow-last-home to acknowledge model unavailability"
            )
        blockers.append(
            {
                "kind": "last-durable-home",
                "rank": rank,
                "node_id": home["node_id"],
                "detail": last_home_detail,
            }
        )
    if (
        target["last_durable_home"]
        and occupancy_class != INCOMPLETE_HUB_OCCUPANCY
        and not _home_revision_is_unbound(target["revision"])
    ):
        if not library_dir:
            fail(
                "home removal: library dir is required to inspect receipts "
                "before last occupancy remove"
            )
        try:
            from scripts import model_library_cold_archive as cold_archive
        except ModuleNotFoundError:
            import model_library_cold_archive as cold_archive  # type: ignore[no-redef]
        receipt_id = cold_archive.resolve_last_occupancy_receipt_id(
            library_dir,
            model_id=target["model_id"],
            snapshot_revision=target["revision"],
        )
        if receipt_id:
            target["receipt_id"] = receipt_id
        archive_detail = cold_archive.last_occupancy_cold_archive_blocker(
            library_dir=library_dir,
            model_id=target["model_id"],
            snapshot_revision=target["revision"],
            occupancy_hub_path=str(home["hub_path"]),
            allow_unarchived=allow_unarchived_last_home,
            occupancy_device=inspection.get("occupancy_device"),
            occupancy_rank=rank,
            expected_receipt_id=receipt_id,
        )
        if archive_detail:
            blockers.append(
                {
                    "kind": "unarchived-last-home",
                    "rank": rank,
                    "node_id": home["node_id"],
                    "detail": archive_detail,
                }
            )
    selected_is_occupancy = bool(target.get("selected_is_occupancy"))
    if (
        occupancy_class != INCOMPLETE_HUB_OCCUPANCY
        and selected_is_occupancy
        and target["alternate_homes"]
        and (
            target["primary_selection"].get("status") != "match"
            or not any(
                candidate.get("primary")
                for candidate in [home, *target["alternate_homes"]]
            )
        )
    ):
        blockers.append(
            {
                "kind": "primary-selection-required",
                "rank": rank,
                "node_id": home["node_id"],
                "detail": (
                    "duplicate removal requires an explicit primary selection; "
                    "select the intended survivor before removing any home"
                ),
            }
        )
    if (
        occupancy_class != INCOMPLETE_HUB_OCCUPANCY
        and selected_is_occupancy
        and home.get("primary")
        and target["alternate_homes"]
    ):
        blockers.append(
            {
                "kind": "selected-primary-home",
                "rank": rank,
                "node_id": home["node_id"],
                "detail": (
                    "this is the selected primary while another complete home exists; "
                    "select the intended survivor before removing this home"
                ),
            }
        )
    blockers.sort(
        key=lambda item: (
            int(item.get("rank", -1)),
            str(item.get("kind") or ""),
            str(item.get("profile") or item.get("path") or ""),
        )
    )
    action = _home_removal_action(
        occupancy_class=occupancy_class,
        occupancy_subtype=occupancy_subtype if isinstance(occupancy_subtype, str) else None,
        model_id=str(target["model_id"]),
        revision=str(target["revision"]),
        last_durable_home=bool(target["last_durable_home"]),
    )
    plan: dict[str, Any] = {
        "schema_version": HOME_REMOVAL_PLAN_SCHEMA_VERSION,
        "kind": HOME_REMOVAL_PLAN_KIND,
        "created_at": utc_now(),
        "state": "eligible" if not blockers else "blocked",
        "ok": not blockers,
        "topology_id": topology_id,
        "target": target,
        "inspection": inspection,
        "allow_last_home": allow_last_home,
        "allow_unarchived_last_home": allow_unarchived_last_home,
        "occupancy_class": occupancy_class,
        "receipt_id": target.get("receipt_id") or "",
        "action": action,
        "observed_nodes": observed_nodes,
        "blockers": blockers,
    }
    plan["plan_id"] = home_removal_plan_id(plan)
    return plan


def render_home_removal_plan(plan: dict[str, Any]) -> None:
    if TerminalWriter is None:
        fail("home removal rendering requires scripts/terminal_format.py")
    term = TerminalWriter()
    target = plan["target"]
    home = target["home"]
    occupancy = plan.get("occupancy_class") or target.get("occupancy_class")
    action = plan.get("action") or {}
    if occupancy == INCOMPLETE_HUB_OCCUPANCY:
        term.emit(f"incomplete hub occupancy  {str(plan['state']).upper()}")
        term.blank()
        term.emit("Action")
        term.emit(
            action.get("summary")
            or (
                "retire this incomplete/refs-only Hugging Face hub occupancy "
                "so the exact repository path becomes absent"
            ),
            initial_indent="  ",
            subsequent_indent="  ",
        )
        if action.get("enables"):
            term.emit(
                f"That enables a {action['enables']}.",
                initial_indent="  ",
                subsequent_indent="  ",
            )
        term.blank()
        term.emit("Target")
        term.field("model", target["model_id"], indent=2)
        term.field("revision", target["revision"], indent=2)
        term.field("rank", home["rank"], indent=2)
        term.field(
            "class",
            target.get("occupancy_subtype") or action.get("occupancy_subtype") or occupancy,
            indent=2,
        )
        term.blank()
        term.emit("Why eligible")
        for reason in action.get("eligibility") or []:
            term.emit(reason, initial_indent="  ", subsequent_indent="  ")
        term.blank()
        term.emit("Will delete")
        term.emit(
            action.get("will_delete") or "the exact hub repository directory",
            initial_indent="  ",
            subsequent_indent="  ",
        )
        term.blank()
        term.emit("Will not delete")
        for item in action.get("will_not_delete") or []:
            term.emit(item, initial_indent="  ", subsequent_indent="  ")
        term.blank()
        term.emit("Last confirmation")
        term.emit(
            action.get("confirmation")
            or "home remove requires --yes after reviewing this eligible plan",
            initial_indent="  ",
            subsequent_indent="  ",
        )
        term.field("last occupancy", "yes" if target["last_durable_home"] else "no")
        term.field("probes", f"{len(plan.get('observed_nodes') or [])} confirmed ranks")
    else:
        term.emit(f"durable home removal  {str(plan['state']).upper()}")
        term.field("model", target["model_id"])
        term.field("revision", target["revision"])
        term.field("home", f"rank {home['rank']} · node {str(home['node_id'])[:12]}")
        term.field("path", home["hub_path"])
        term.field("bytes", plan["inspection"].get("repository_bytes") or 0)
        term.field("last home", "yes" if target["last_durable_home"] else "no")
        term.field("probes", f"{len(plan.get('observed_nodes') or [])} confirmed nodes")
    blockers = plan.get("blockers") or []
    if blockers:
        term.blank()
        term.emit(f"Blocked by {len(blockers)} condition(s):")
        for blocker in blockers:
            rank = blocker.get("rank")
            label = blocker.get("kind") or "unknown"
            profile = blocker.get("profile")
            suffix = f" · {profile}" if profile else ""
            term.emit(
                f"rank {rank} · {label}{suffix}",
                initial_indent="  ",
                subsequent_indent="    ",
            )
            term.emit(
                blocker.get("detail") or "dependency remains",
                initial_indent="    ",
                subsequent_indent="    ",
            )
            if blocker.get("instance_dir"):
                term.emit(
                    blocker["instance_dir"],
                    initial_indent="    ",
                    subsequent_indent="    ",
                )
        term.blank()
        term.emit("Stop/remove dependent managed containers and purge their hot views.")
        term.emit("Then rerun the check; unobservable nodes or metadata fail without fallback.")
    else:
        term.blank()
        term.emit(
            "No managed container, retained hot view, or topology probe blocks removal."
        )
        term.emit("Mutation requires home remove with the separate --yes confirmation.")


def execute_home_removal_plan(
    plan: dict[str, Any],
    *,
    rank: int,
    node_id: str,
) -> dict[str, Any]:
    if plan.get("schema_version") != HOME_REMOVAL_PLAN_SCHEMA_VERSION:
        fail("home removal: unsupported plan schema")
    if plan.get("kind") != HOME_REMOVAL_PLAN_KIND:
        fail("home removal: invalid plan kind")
    if plan.get("plan_id") != home_removal_plan_id(plan):
        fail("home removal: plan identity mismatch")
    if plan.get("state") != "eligible" or plan.get("blockers"):
        fail("home removal: plan is blocked")
    target = plan.get("target") or {}
    home = target.get("home") or {}
    if int(home.get("rank", -1)) != rank or home.get("node_id") != node_id:
        fail("home removal: execution node differs from the plan")
    if target.get("last_durable_home") and not plan.get("allow_last_home"):
        fail("home removal: last-home acknowledgement is missing")

    current = inspect_removable_home(
        home["hub_path"],
        cache_root=home["cache_root"],
        model_id=target["model_id"],
        revision=target["revision"],
        rank=rank,
        node_id=node_id,
    )
    if current.get("state") != "eligible":
        fail("home removal: durable-home shape changed before execution")
    planned_occupancy = (
        plan.get("occupancy_class")
        or target.get("occupancy_class")
        or COMPLETE_HOME_OCCUPANCY
    )
    current_occupancy = current.get("occupancy_class") or COMPLETE_HOME_OCCUPANCY
    if planned_occupancy != current_occupancy:
        fail("home removal: durable-home occupancy class changed before execution")
    if planned_occupancy == COMPLETE_HOME_OCCUPANCY and current_occupancy != COMPLETE_HOME_OCCUPANCY:
        fail("home removal: complete homes cannot use the incomplete-occupancy path")
    if planned_occupancy == INCOMPLETE_HUB_OCCUPANCY and current_occupancy != INCOMPLETE_HUB_OCCUPANCY:
        fail("home removal: complete or unrecognized trees cannot use the stub path")
    expected_fingerprint = (plan.get("inspection") or {}).get("fingerprint")
    if not expected_fingerprint or current.get("fingerprint") != expected_fingerprint:
        fail("home removal: durable-home metadata changed after the guard check")

    hub = pathlib.Path(home["hub_path"])
    tombstone = hub.parent / (
        f".{hub.name}.pulsar-removing-{str(plan['plan_id'])[:12]}"
    )
    if tombstone.exists() or tombstone.is_symlink():
        fail(f"home removal: tombstone path already exists: {tombstone}")
    try:
        os.rename(hub, tombstone)
        parent_fd = os.open(hub.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        fail(f"home removal: atomic retirement failed before deletion: {exc}")
    try:
        shutil.rmtree(tombstone)
        parent_fd = os.open(hub.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        fail(
            "home removal: repository left at the retirement path after a "
            f"deletion failure: {tombstone}: {exc}"
        )
    return {
        "schema_version": 1,
        "kind": HOME_REMOVAL_RESULT_KIND,
        "state": "removed",
        "removed_at": utc_now(),
        "plan_id": plan["plan_id"],
        "model_id": target["model_id"],
        "revision": target["revision"],
        "rank": rank,
        "node_id": node_id,
        "hub_path": str(hub),
        "repository_bytes": current.get("repository_bytes") or 0,
    }


def render_catalog_human(catalog: dict[str, Any]) -> None:
    models = catalog.get("models") or []
    print(f"model library  topology={str(catalog.get('topology_id') or '')[:12]}")
    print(f"refreshed      {catalog.get('refreshed_at')}")
    print(f"entries        {len(models)}")
    print()
    if not models:
        print("(empty)")
        return
    print(f"{'MODEL':<36} {'VAL':<20} {'HOMES':>5} {'DUP':>3}  PROFILES")
    for entry in models:
        complete = sum(1 for h in entry.get("homes") or [] if h.get("state") == "complete")
        dup = "yes" if entry.get("duplicate") else "no"
        profiles = ",".join(entry.get("profiles") or []) or "-"
        print(
            f"{entry.get('model_id', '?'):<36} "
            f"{entry.get('validation', '?'):<20} "
            f"{complete:>5} {dup:>3}  {profiles}"
        )


def _health_exit(report: dict[str, Any]) -> int:
    return 0 if report.get("state") in {"healthy", "not-configured"} else 1


def render_health_report(report: dict[str, Any]) -> None:
    if report.get("kind") != HEALTH_KIND:
        fail("health report contract is invalid")
    if TerminalWriter is None:
        fail("health rendering requires scripts/terminal_format.py")
    term = TerminalWriter()
    state = str(report.get("state") or "unavailable")
    term.emit(f"model library  {state.upper()}")
    catalog = report.get("catalog") or {}
    term.field("catalog", str(catalog.get("status") or "unknown"))
    term.field("models", len(report.get("models") or []))
    term.field("hot views", len(report.get("hot_instances") or []))
    issues = report.get("issues") or []
    if not issues:
        term.blank()
        term.emit("No model-library findings.")
        return
    term.blank()
    term.emit("Findings")
    for issue in issues:
        rank = f" · rank {issue['rank']}" if issue.get("rank") is not None else ""
        term.emit(
            f"{issue.get('code', 'unknown')}{rank}",
            initial_indent="  ",
            subsequent_indent="    ",
        )
        term.emit(
            str(issue.get("detail") or "attention required"),
            initial_indent="    ",
            subsequent_indent="    ",
        )
        command = (issue.get("remediation") or {}).get("command")
        if command:
            term.emit(
                f"Next: {command}",
                initial_indent="    ",
                subsequent_indent="      ",
            )


def cmd_scan_hot_health(args: argparse.Namespace) -> int:
    result = scan_hot_health(
        args.hot_root or default_hot_root(), rank=args.rank, node_id=args.node_id
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def cmd_build_health(args: argparse.Namespace) -> int:
    report = build_health_report(
        catalog_path=args.catalog,
        topology_file=args.topology_file,
        topology_id=args.topology_id,
        observations_dir=args.observations_dir,
        library_dir=getattr(args, "library_dir", None) or None,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return _health_exit(report)


def cmd_render_health(args: argparse.Namespace) -> int:
    report = load_json(args.report_file)
    if not isinstance(report, dict):
        fail("health report must be an object")
    if args.doctor_rows:
        state = str(report.get("state") or "unavailable")
        if state == "not-configured":
            print("ok\tmodel_library\tmodel library not configured; replicated weights remain available")
        elif state == "healthy":
            print("ok\tmodel_library\tmodel library catalog and runtime views are healthy")
        else:
            for index, issue in enumerate(report.get("issues") or [], start=1):
                message = str(issue.get("detail") or issue.get("code") or "attention required")
                command = (issue.get("remediation") or {}).get("command")
                if command:
                    message = f"{message} · Next: {command}"
                print(f"warn\tmodel_library_{index}\t{message}")
            if not report.get("issues"):
                print("warn\tmodel_library\tmodel-library health is unavailable")
    elif args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        render_health_report(report)
    return _health_exit(report)


def cmd_resolve_home_removal_target(args: argparse.Namespace) -> int:
    target = select_home_removal_target(
        args.catalog,
        args.query,
        node_selector=args.node,
    )
    print(json.dumps(target, indent=2, sort_keys=True))
    return 0


def _decode_json_document_b64(value: str, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(base64.urlsafe_b64decode(value.encode("ascii")))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{label}: encoded JSON is invalid: {exc}")
    if not isinstance(document, dict):
        fail(f"{label}: document must be an object")
    return document


def cmd_inspect_home_acquisition_target(args: argparse.Namespace) -> int:
    result = inspect_home_acquisition_target(
        args.cache_root,
        model_id=args.model_id,
        revision=args.revision,
        required_content_bytes=args.required_content_bytes,
        rank=args.rank,
        node_id=args.node_id,
        hf_cli=args.hf_cli,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_create_owned_hub_staging(args: argparse.Namespace) -> int:
    result = create_owned_hub_staging(
        args.hub_root,
        owner_id=args.owner_id,
        rank=args.rank,
        node_id=args.node_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_cleanup_owned_hub_staging(args: argparse.Namespace) -> int:
    result = cleanup_owned_hub_staging(
        args.staging_root,
        owner_id=args.owner_id,
        rank=args.rank,
        node_id=args.node_id,
        hub_root=args.hub_root or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_publish_owned_hub_staging(args: argparse.Namespace) -> int:
    result = publish_owned_hub_staging(
        args.staging_root,
        owner_id=args.owner_id,
        rank=args.rank,
        node_id=args.node_id,
        model_id=args.model_id,
        target_hub=args.target_hub,
        hub_root=args.hub_root or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_inspect_live_directory_identity(args: argparse.Namespace) -> int:
    result = inspect_live_directory_identity(args.path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_inspect_snapshot_blob_identities(args: argparse.Namespace) -> int:
    result = inspect_snapshot_blob_identities(
        args.hub_path,
        model_id=args.model_id,
        revision=args.revision,
        allow_empty_files=args.allow_empty_files,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_recheck_home_acquisition_absence(args: argparse.Namespace) -> int:
    result = recheck_home_acquisition_absence(
        topology_file=args.topology_file,
        topology_id=args.topology_id,
        observations_dir=args.observations_dir,
        model_id=args.model_id,
        revision=args.revision,
        required_content_bytes=args.required_content_bytes,
        selected_rank=args.selected_rank,
        selected_node_id=args.selected_node_id,
        selected_target_hub=args.selected_target_hub,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_inspect_removable_home(args: argparse.Namespace) -> int:
    result = inspect_removable_home(
        args.hub_path,
        cache_root=args.cache_root,
        model_id=args.model_id,
        revision=args.revision,
        rank=args.rank,
        node_id=args.node_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_scan_home_hot_references(args: argparse.Namespace) -> int:
    result = scan_home_hot_references(
        args.hot_root or default_hot_root(),
        rank=args.rank,
        node_id=args.node_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_plan_home_removal(args: argparse.Namespace) -> int:
    plan = plan_home_removal(
        catalog_path=args.catalog,
        query=args.query,
        topology_file=args.topology_file,
        topology_id=args.topology_id,
        models_dir=args.models_dir,
        inspection_path=args.inspection_file,
        observations_dir=args.observations_dir,
        node_selector=args.node,
        allow_last_home=args.allow_last_home,
        allow_unarchived_last_home=getattr(
            args, "allow_unarchived_last_home", False
        ),
        library_dir=args.library_dir or None,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def _load_home_removal_plan_arg(args: argparse.Namespace) -> dict[str, Any]:
    if args.plan_json:
        try:
            plan = json.loads(args.plan_json)
        except json.JSONDecodeError as exc:
            fail(f"home removal plan JSON: {exc}")
    elif args.plan_file:
        plan = load_json(args.plan_file)
    else:
        fail("home removal: --plan-json or --plan-file is required")
    if not isinstance(plan, dict):
        fail("home removal: plan must be an object")
    return plan


def cmd_render_home_removal_plan(args: argparse.Namespace) -> int:
    render_home_removal_plan(_load_home_removal_plan_arg(args))
    return 0


def cmd_execute_home_removal(args: argparse.Namespace) -> int:
    result = execute_home_removal_plan(
        _load_home_removal_plan_arg(args),
        rank=args.rank,
        node_id=args.node_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_reverify_last_home_archive(args: argparse.Namespace) -> int:
    try:
        from scripts import model_library_cold_archive as cold_archive
    except ModuleNotFoundError:
        import model_library_cold_archive as cold_archive  # type: ignore[no-redef]
    try:
        cold_archive.reverify_last_home_archive(
            _load_home_removal_plan_arg(args),
            library_dir=args.library_dir,
        )
    except cold_archive.ColdArchiveError as exc:
        fail(str(exc))
    return 0


def cmd_render_home_removal_result(args: argparse.Namespace) -> int:
    result = load_json(args.result_file)
    if not isinstance(result, dict) or result.get("kind") != HOME_REMOVAL_RESULT_KIND:
        fail("home removal: result document is invalid")
    if TerminalWriter is None:
        fail("home removal rendering requires scripts/terminal_format.py")
    term = TerminalWriter()
    term.emit("durable home removal  REMOVED")
    term.field("model", result["model_id"])
    term.field("revision", result["revision"])
    term.field("home", f"rank {result['rank']} · node {str(result['node_id'])[:12]}")
    term.field("path", result["hub_path"])
    term.field("bytes", result.get("repository_bytes") or 0)
    term.field("catalog", "refreshed from confirmed topology")
    return 0


def cmd_scan_hub(args: argparse.Namespace) -> int:
    homes = scan_hub_cache(
        args.cache_root,
        rank=args.rank,
        node_id=args.node_id,
        hostname=args.hostname or "",
        ssh_host=args.ssh_host or "",
    )
    print(json.dumps(homes, indent=2, sort_keys=True))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    homes: list[dict[str, Any]] = []
    if args.homes_json:
        raw = load_json(args.homes_json)
        if not isinstance(raw, list):
            fail("--homes-json must be a JSON array")
        homes = raw
    profiles = load_hf_profiles(args.models_dir)
    persistent_selections: list[dict[str, str]] = []
    if args.preserve_primary_from:
        source = pathlib.Path(args.preserve_primary_from)
        if source.is_file():
            persistent_selections = normalize_primary_selections(
                load_catalog(source).get("primary_selections")
            )
    primary: dict[str, str] = {}
    if args.primary:
        for item in args.primary:
            if "=" not in item:
                fail(f"--primary expects identity_or_model=node_id (got {item!r})")
            key, value = item.split("=", 1)
            primary[key] = value
    catalog = build_catalog(
        topology_id=args.topology_id,
        homes=homes,
        profiles=profiles,
        primary_overrides=primary,
        primary_selections=persistent_selections,
    )
    if args.output:
        atomic_write_json(args.output, catalog)
    if args.json:
        print(json.dumps(catalog, indent=2, sort_keys=True))
    else:
        render_catalog_human(catalog)
        if args.output:
            print(f"wrote {args.output}", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    if args.json:
        models = catalog.get("models") or []
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "models": models},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        render_catalog_human(catalog)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    entry = find_model_entry(
        catalog,
        model_id=args.query if "/" in args.query else None,
        profile=args.query if "/" not in args.query else None,
        identity_key=args.query if "@" in args.query else None,
    )
    if entry is None and "/" not in args.query and "@" not in args.query:
        entry = find_model_entry(catalog, profile=args.query)
    if entry is None:
        fail(f"show: no entry matching {args.query!r}")
    if args.json:
        print(json.dumps(entry, indent=2, sort_keys=True))
    else:
        print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    catalog = None
    if args.catalog:
        if pathlib.Path(args.catalog).is_file():
            catalog = load_catalog(args.catalog)
        elif not getattr(args, "allow_missing_catalog", False):
            fail(f"resolve: catalog missing: {args.catalog}")
    model_id = args.model
    profile = args.profile
    identity_key = None
    absolute_path = None
    if args.query:
        if args.query.startswith("/"):
            absolute_path = args.query
        elif "@" in args.query:
            identity_key = args.query
        elif "/" in args.query:
            model_id = args.query
        else:
            profile = args.query
    cold_root: Any
    if getattr(args, "no_cold", False):
        cold_root = None
    elif getattr(args, "cold_root", None):
        cold_root = args.cold_root
    else:
        cold_root = ...
    result = resolve_entry(
        catalog,
        model_id=model_id,
        profile=profile,
        identity_key=identity_key,
        absolute_path=absolute_path,
        cold_root=cold_root,
        models_dir=getattr(args, "models_dir", None) or None,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        home = result["home"]
        print(f"model     {result['model_id']}")
        print(f"revision  {result.get('revision') or '-'}")
        print(f"validation {result.get('validation')}")
        print(f"tier      {result.get('tier') or 'warm'}")
        print(f"layout    {result.get('layout') or 'hub'}")
        print(f"home rank {home['rank']}  node_id={home['node_id']}")
        print(f"hub_path  {home['hub_path']}")
        if result.get("source_path") and result.get("source_path") != home.get("hub_path"):
            print(f"source    {result['source_path']}")
        if result.get("duplicate"):
            print("note      duplicates present; using selected primary")
        if result.get("tier") == "cold":
            print("note      resolved from cold archive (not a warm home)")
    return 0


def cmd_scan_cold(args: argparse.Namespace) -> int:
    root = args.cold_root or configured_cold_root()
    if root is None:
        fail("scan-cold: cold root not configured (pass --cold-root or set MODELS_NFS)")
    entries = scan_cold_archive(root)
    if args.complete_only:
        entries = [e for e in entries if e.get("state") == "complete"]
    out = {
        "cold_root": str(pathlib.Path(root).expanduser()),
        "count": len(entries),
        "entries": entries,
    }
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"cold root  {out['cold_root']}")
        print(f"entries    {out['count']}")
        print()
        print(f"{'MODEL':<40} {'STATE':<10} {'LAYOUT':<6}  PATH")
        for e in entries:
            print(
                f"{e['model_id']:<40} {e['state']:<10} {e['layout']:<6}  {e['path']}"
            )
    return 0


def cmd_find_cold(args: argparse.Namespace) -> int:
    root = args.cold_root or configured_cold_root()
    if root is None:
        fail("find-cold: cold root not configured")
    path = args.path
    model_id = args.model
    if args.query:
        if args.query.startswith("/"):
            path = args.query
        else:
            model_id = args.query
    entry = find_cold_entry(
        root,
        model_id=model_id,
        path=path,
        require_complete=not args.allow_partial,
    )
    if entry is None:
        fail(f"find-cold: no entry for {args.query or model_id or path!r}")
    print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


def cmd_plan_cold_adopt(args: argparse.Namespace) -> int:
    plan = plan_cold_adopt(
        cold_root=args.cold_root or None,
        model_id=args.model,
        path=args.path,
        profile=args.profile,
        models_dir=args.models_dir or None,
        cache_root=args.cache_root,
    )
    if args.execute:
        result = execute_cold_adopt(plan)
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_plan_cold_stage(args: argparse.Namespace) -> int:
    plan = plan_cold_stage(
        cold_root=args.cold_root or None,
        profile=args.profile,
        topology_id=args.topology_id,
        hot_root=args.hot_root or default_hot_root(),
        model_id=args.model,
        absolute_path=args.path,
        catalog_path=args.catalog or None,
        models_dir=args.models_dir or None,
        nodes=args.nodes,
    )
    if args.execute and plan.get("action") == "stage-only":
        admission = hot_budget_admission(
            plan["hot_root"],
            int(plan["bytes_logical"]),
            replacing_path=plan["instance_dir"],
            runtime_source="working-copy",
            rank=0,
            node_id="local-direct-execution",
        )
        if not admission["ok"]:
            detail = "; ".join(
                str(item.get("detail") or item.get("code"))
                for item in admission["blockers"]
            )
            fail(f"cold stage-only: hot admission blocked: {detail}")
        # Publish ready only after a stable full verify creates the local witness.
        materialize_hub_tree(
            plan["source_path"],
            plan["hub_dest"],
            layout=plan.get("layout"),
            revision=plan.get("revision"),
        )
        stamp = dict(plan["stamp"])
        stamp["source_content_digest"] = plan.get("source_content_digest") or stamp.get(
            "source_content_digest"
        )
        provisional = dict(stamp)
        provisional["state"] = "verifying"
        write_hot_stamp(pathlib.Path(plan["instance_dir"]), provisional)
        verify = verify_hot_ready(
            plan["instance_dir"],
            profile=args.profile,
            topology_id=args.topology_id,
            allow_verifying=True,
            refresh_witness=True,
        )
        write_hot_stamp(pathlib.Path(plan["instance_dir"]), stamp)
        verify["stamp"] = stamp
        plan = {
            **plan,
            "executed": True,
            "stamp": stamp,
            "verify": verify,
        }
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_cleanup_recommend(args: argparse.Namespace) -> int:
    catalog = load_catalog(args.catalog)
    recs = cleanup_recommend(catalog)
    if args.json:
        print(json.dumps({"recommendations": recs}, indent=2, sort_keys=True))
        return 0
    if not recs:
        print("No duplicate complete homes found.")
        return 0
    print(f"Found {len(recs)} model(s) with duplicate complete homes:\n")
    for rec in recs:
        print(f"## {rec['model_id']}  ({rec['identity_key']})")
        for home in rec["homes"]:
            primary = " PRIMARY" if home.get("primary") else ""
            print(
                f"  - rank={home['rank']} node_id={home['node_id']} "
                f"host={home.get('hostname') or '-'} bytes={home.get('bytes')}{primary}"
            )
            print(f"    {home.get('hub_path')}")
        if rec["select_commands"]:
            print("  Select one primary (choose exactly one):")
            for command in rec["select_commands"]:
                print(f"    {command}")
        if rec["removal_commands"]:
            print("  Inspect and explicitly remove each unwanted non-primary home:")
            for commands in rec["removal_commands"]:
                print(f"    {commands['check']}")
                print(f"    {commands['remove']}")
        print(f"  → {rec['action']}\n")
    return 0


def render_catalog_primary_records(
    records: list[dict[str, Any]],
    *,
    width: int | None = None,
) -> None:
    if TerminalWriter is None:
        fail("catalog primary rendering requires scripts/terminal_format.py")
    term = TerminalWriter(width=width)
    term.emit("model library primary selections")
    term.blank()
    if not records:
        term.emit("(empty)")
        return
    for index, record in enumerate(records):
        selection = record.get("selection") or {}
        primary = record.get("primary_home")
        home = (
            f"rank {primary['rank']} · node {str(primary['node_id'])[:12]}"
            if primary
            else "-"
        )
        if index:
            term.blank()
        term.field("identity", record["identity_key"])
        term.field("mode", selection.get("mode") or "-")
        term.field("status", selection.get("status") or "-")
        term.field("home", home)


def render_catalog_primary_result(
    result: dict[str, Any],
    *,
    width: int | None = None,
) -> None:
    if TerminalWriter is None:
        fail("catalog primary rendering requires scripts/terminal_format.py")
    term = TerminalWriter(width=width)
    action = "selected" if result["action"] == "set" else "cleared"
    suffix = " (unchanged)" if not result["changed"] else ""
    term.emit(f"catalog primary {action}{suffix}")
    term.field("identity", result["identity_key"])
    selection = result.get("selection") or {}
    term.field("status", selection.get("status") or "-")
    if result.get("home"):
        term.field(
            "home",
            f"rank {result['home']['rank']} · "
            f"node {str(result['home']['node_id'])[:12]}",
        )


def cmd_catalog_primary(args: argparse.Namespace) -> int:
    if args.primary_action == "list":
        catalog = load_catalog(args.catalog)
        records = catalog_primary_records(catalog)
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "pulsar-model-library-primary-records",
                        "primary_records": records,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            render_catalog_primary_records(records)
        return 0
    if args.primary_action == "set":
        result = set_catalog_primary(
            args.catalog,
            args.query,
            args.node,
            topology_id=args.topology_id,
            topology_file=args.topology_file,
        )
    else:
        result = clear_catalog_primary(
            args.catalog,
            args.query,
            topology_id=args.topology_id,
        )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        render_catalog_primary_result(result)
    return 0


def cmd_inspect_hub(args: argparse.Namespace) -> int:
    result = inspect_hub_inventory(
        args.hub_path,
        rank=args.rank,
        node_id=args.node_id,
        model_id=args.model_id or None,
        revision=args.revision or None,
        allow_empty_files=args.allow_empty_files,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def cmd_plan_prepare(args: argparse.Namespace) -> int:
    home_inventory = None
    if args.home_inventory_json:
        try:
            home_inventory = json.loads(args.home_inventory_json)
        except json.JSONDecodeError as exc:
            fail(f"home-inventory-json: {exc}")
        if not isinstance(home_inventory, dict):
            fail("home-inventory-json must be an object")
    expected_manifest = None
    if getattr(args, "expected_integrity_manifest_json", ""):
        try:
            expected_manifest = json.loads(args.expected_integrity_manifest_json)
        except json.JSONDecodeError as exc:
            fail(f"expected-integrity-manifest-json: {exc}")
        if not isinstance(expected_manifest, dict):
            fail("expected-integrity-manifest-json must be an object")
    plan = plan_prepare(
        catalog_path=args.catalog,
        profile=args.profile,
        topology_id=args.topology_id,
        hot_root=args.hot_root or default_hot_root(),
        models_dir=args.models_dir,
        backend=args.backend or None,
        transport=args.transport or None,
        nodes=args.nodes,
        target_rank=args.target_rank,
        topology_file=args.topology_file or None,
        home_inventory=home_inventory,
        require_exact_revision=getattr(args, "require_exact_revision", None) or None,
        expected_integrity_manifest=expected_manifest,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_write_hot_stamp(args: argparse.Namespace) -> int:
    if args.stamp_json:
        try:
            stamp = json.loads(args.stamp_json)
        except json.JSONDecodeError as exc:
            fail(f"stamp-json: {exc}")
    elif args.stamp_file:
        stamp = load_json(args.stamp_file)
    else:
        fail("write-hot-stamp requires --stamp-file or --stamp-json")
    if not isinstance(stamp, dict):
        fail("stamp must be a JSON object")
    path = write_hot_stamp(pathlib.Path(args.instance_dir), stamp)
    if args.json:
        print(json.dumps({"path": str(path), "stamp": stamp}, indent=2, sort_keys=True))
    else:
        print(path)
    return 0


def cmd_verify_hot(args: argparse.Namespace) -> int:
    stamp = load_hot_stamp(args.instance_dir)
    allowed_states = {"ready", "pinned"}
    if args.allow_verifying:
        allowed_states.add("verifying")
    if stamp.get("state") not in allowed_states and not stamp.get("pinned"):
        fail(
            f"hot not ready: state={stamp.get('state')!r} "
            f"at {args.instance_dir}"
        )
    if args.profile and stamp.get("profile") != args.profile:
        fail(
            f"hot profile mismatch: stamp={stamp.get('profile')} "
            f"want={args.profile}"
        )
    if args.topology_id and stamp.get("topology_id") != args.topology_id:
        fail("hot topology_id mismatch")

    integrity = stamp.get("integrity")
    if not isinstance(integrity, dict):
        fail("hot integrity seal missing")
    manifest = validate_snapshot_manifest(integrity.get("manifest"))
    checked_validation = validate_hot_validation(
        stamp.get("validation"),
        profile=str(stamp.get("profile") or ""),
        manifest=manifest,
    )
    if args.models_dir:
        if not args.profile:
            fail("verify-hot: --profile is required with --models-dir")
        profile_data = load_model_profile(args.models_dir, args.profile)
        checked_validation = verify_hot_stamp_against_profile(
            stamp,
            profile_data,
        )
    if args.expected_validation_json:
        try:
            expected_validation = json.loads(args.expected_validation_json)
        except json.JSONDecodeError as exc:
            fail(f"verify-hot: expected-validation-json: {exc}")
        if checked_validation != expected_validation:
            fail("hot validation provenance differs from controller expectation")

    result = verify_hot_ready(
        args.instance_dir,
        profile=args.profile,
        topology_id=args.topology_id,
        require_digest=not args.skip_digest,
        allow_verifying=args.allow_verifying,
        workers=args.workers,
        serve_time_witness=getattr(args, "serve_time_witness", False),
        refresh_witness=getattr(args, "refresh_witness", False),
    )
    result["validation"] = checked_validation
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_find_hot(args: argparse.Namespace) -> int:
    profile_data = (
        load_model_profile(args.models_dir, args.profile) if args.models_dir else None
    )
    path = find_hot_instance_for_profile(
        args.hot_root or default_hot_root(),
        args.profile,
        args.topology_id,
        profile_data=profile_data,
    )
    if path is None:
        fail(f"find-hot: no ready instance for profile {args.profile}")
    stamp = load_hot_stamp(path)
    if profile_data is not None:
        verify_hot_stamp_against_profile(stamp, profile_data)
    hub = hot_hub_path(path, stamp["model_id"])
    revision = stamp.get("revision")
    if not isinstance(revision, str) or SAFE_REV.fullmatch(revision) is None:
        fail("find-hot: sealed revision is invalid")
    container_hub = (
        "/root/.cache/huggingface/hub/"
        + model_id_to_hub_dirname(stamp["model_id"])
    )
    out = {
        "instance_dir": str(path),
        "hub_path": str(hub),
        "snapshot_path": str(hub / "snapshots" / revision),
        "runtime_model_relative": f"snapshots/{revision}",
        "container_model_path": f"{container_hub}/snapshots/{revision}",
        "stamp": stamp,
    }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


def cmd_validate_hot_stamp(args: argparse.Namespace) -> int:
    if args.stamp_json:
        try:
            stamp = json.loads(args.stamp_json)
        except json.JSONDecodeError as exc:
            fail(f"validate-hot-stamp: {exc}")
    else:
        stamp = load_json(args.stamp_file)
    if not isinstance(stamp, dict):
        fail("validate-hot-stamp: stamp must be an object")
    profile_data = load_model_profile(args.models_dir, args.profile)
    validation = verify_hot_stamp_against_profile(stamp, profile_data)
    print(json.dumps(validation, indent=2, sort_keys=True))
    return 0


def cmd_set_pinned(args: argparse.Namespace) -> int:
    stamp = set_hot_pinned(args.instance_dir, pinned=args.pinned)
    print(json.dumps(stamp, indent=2, sort_keys=True))
    return 0


def cmd_purge_hot(args: argparse.Namespace) -> int:
    purge_hot_instance(args.instance_dir, force_unpin=args.force_unpin)
    if args.json:
        print(json.dumps({"purged": args.instance_dir}, indent=2, sort_keys=True))
    else:
        print(f"purged {args.instance_dir}")
    return 0


def cmd_budget(args: argparse.Namespace) -> int:
    report = budget_report(
        args.hot_root or default_hot_root(),
        budget_bytes=args.budget_bytes,
        reserve_bytes=args.reserve_bytes,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"hot_root   {report['hot_root']}")
        hard_cap = report["policy"].get("hard_cap_bytes")
        print(f"hard_cap   {hard_cap if hard_cap is not None else 'none'}")
        print(f"reserve    {report['policy']['reserve_bytes']} bytes")
        print(f"available  {report['filesystem']['available_bytes']} bytes")
        print(f"used       {report['used_bytes']} bytes")
        print(f"remaining  {report['remaining_bytes']} bytes")
        print(f"instances  {len(report['instances'])}")
        for item in report["instances"]:
            pin = " pinned" if item.get("pinned") else ""
            print(f"  - {item['profile']} {item['bytes']}B{pin}")
            print(f"    {item['path']}")
    return 0


def cmd_budget_admission(args: argparse.Namespace) -> int:
    observation = hot_budget_admission(
        args.hot_root or default_hot_root(),
        args.required_owned_bytes,
        budget_bytes=args.hard_cap_bytes,
        reserve_bytes=args.reserve_bytes,
        replacing_path=args.replacing_path or None,
        runtime_source=args.runtime_source,
        rank=args.rank,
        node_id=args.node_id,
        hostname=args.hostname or "",
    )
    if args.compact:
        print(json.dumps(observation, sort_keys=True, separators=(",", ":")))
    else:
        print(json.dumps(observation, indent=2, sort_keys=True))
    return 0


def cmd_merge_budget_admissions(args: argparse.Namespace) -> int:
    observations: list[dict[str, Any]] = []
    try:
        with open(args.observations_file, encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    fail(
                        f"hot budget plan: observation line {line_number}: {exc}"
                    )
                if not isinstance(value, dict):
                    fail(
                        f"hot budget plan: observation line {line_number} "
                        "is not an object"
                    )
                observations.append(value)
    except OSError as exc:
        fail(f"hot budget plan: cannot read observations: {exc}")
    expected_ranks: list[int] = []
    for raw in args.expected_ranks.split(","):
        try:
            expected_ranks.append(int(raw))
        except ValueError:
            fail(f"hot budget plan: invalid expected rank {raw!r}")
    plan = merge_hot_budget_observations(
        observations,
        expected_ranks=expected_ranks,
        topology_id=args.topology_id,
        mode=args.mode,
        profile=args.profile or "",
        model_id=args.model_id or "",
        bytes_logical=args.bytes_logical,
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_render_budget_plan(args: argparse.Namespace) -> int:
    if args.plan_json:
        try:
            plan = json.loads(args.plan_json)
        except json.JSONDecodeError as exc:
            fail(f"hot budget rendering: {exc}")
    else:
        plan = load_json(args.plan_file)
    if not isinstance(plan, dict):
        fail("hot budget rendering: plan is not an object")
    render_hot_budget_plan(plan)
    return 0


def cmd_inventory_digest(args: argparse.Namespace) -> int:
    print(inventory_digest(args.hub_path))
    return 0


def cmd_partition_blobs(args: argparse.Namespace) -> int:
    report = partition_blob_files(args.hub_path, streams=args.streams)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def build_ssh_roce_map(
    topology: dict[str, Any],
    *,
    home_rank: int,
    target_ranks: list[int],
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
) -> dict[str, Any]:
    """Map ranks → control SSH host and RoCE IP for experimental SSH-over-RoCE copy.

    Selects the confirmed RoCE rail between each pair of ranks for rsync -e ssh
    addressing over the fabric IPs.
    """
    nodes = topology.get("nodes") or []
    by_rank: dict[int, dict[str, Any]] = {}
    for node in nodes:
        try:
            r = int(node["rank"])
        except (KeyError, TypeError, ValueError):
            continue
        by_rank[r] = node

    if home_rank not in by_rank:
        fail(f"ssh-roce-map: home_rank {home_rank} not in topology")

    ranks = sorted(set(int(r) for r in target_ranks))
    if home_rank not in ranks:
        ranks = sorted(ranks + [home_rank])

    peers = [r for r in ranks if r != home_rank]
    if not peers:
        fail(
            "ssh-roce-map: need at least one non-home target rank "
            "(single-rank is not a RoCE path experiment)"
        )

    rank_entries: dict[str, Any] = {}
    home_ip: str | None = None
    network: str | None = None
    for peer in peers:
        server, client, net = selected_rail_between(
            topology, home_rank, peer, rail_index
        )
        if home_ip is None:
            home_ip = str(server["ip"])
            network = str(net)
        elif str(server["ip"]) != home_ip:
            fail(
                f"ssh-roce-map: home rank {home_rank} has inconsistent RoCE IPs "
                f"across peers ({home_ip} vs {server['ip']})"
            )
        peer_node = by_rank[peer]
        rank_entries[str(peer)] = {
            "rank": peer,
            "role": "client",
            "control_ssh_host": peer_node.get("ssh_host") or "",
            "hostname": peer_node.get("hostname") or "",
            "node_id": peer_node.get("node_id") or "",
            "roce_ip": str(client["ip"]),
            "roce_hca": client.get("hca") or "",
            "roce_netdev": client.get("netdev") or "",
            "network": str(net),
            "rail_index": rail_index,
        }

    home_node = by_rank[home_rank]
    # Home endpoint from first peer rail (server side).
    server0, _client0, net0 = selected_rail_between(
        topology, home_rank, peers[0], rail_index
    )
    rank_entries[str(home_rank)] = {
        "rank": home_rank,
        "role": "home",
        "control_ssh_host": home_node.get("ssh_host") or "",
        "hostname": home_node.get("hostname") or "",
        "node_id": home_node.get("node_id") or "",
        "roce_ip": str(server0["ip"]),
        "roce_hca": server0.get("hca") or "",
        "roce_netdev": server0.get("netdev") or "",
        "network": str(net0),
        "rail_index": rail_index,
    }

    return {
        "schema_version": 1,
        "kind": "model-library-ssh-roce-map",
        "topology_id": topology.get("topology_id") or "",
        "home_rank": home_rank,
        "rail_index": rail_index,
        "network": network or net0,
        "target_ranks": ranks,
        "ranks": rank_entries,
        "notes": (
            "Experiment only: rsync -e ssh to roce_ip uses the RoCE NIC as TCP/IP "
            "(not NFS/RDMA). Requires sshd reachable on fabric IPs."
        ),
    }


def validate_ssh_roce_route(
    route_data: Any,
    *,
    remote_ip: str,
    expected_netdev: str,
    expected_source_ip: str,
) -> dict[str, Any]:
    """Fail closed unless Linux routes a RoCE peer over the confirmed rail."""
    if not remote_ip or not expected_netdev or not expected_source_ip:
        fail("ssh-roce-route: remote IP, netdev, and source IP are required")
    if not isinstance(route_data, list) or not route_data:
        fail("ssh-roce-route: `ip -j route get` returned no routes")
    route = route_data[0]
    if not isinstance(route, dict):
        fail("ssh-roce-route: first route is not an object")
    actual_netdev = str(route.get("dev") or "")
    actual_source_ip = str(route.get("prefsrc") or route.get("src") or "")
    if actual_netdev != expected_netdev:
        fail(
            f"ssh-roce-route: {remote_ip} uses dev "
            f"{actual_netdev or '<none>'}; expected confirmed "
            f"{expected_netdev}"
        )
    if actual_source_ip != expected_source_ip:
        fail(
            f"ssh-roce-route: {remote_ip} uses source "
            f"{actual_source_ip or '<none>'}; expected confirmed "
            f"{expected_source_ip}"
        )
    return {
        "schema_version": 1,
        "kind": "model-library-ssh-roce-route",
        "state": "ready",
        "remote_ip": remote_ip,
        "netdev": actual_netdev,
        "source_ip": actual_source_ip,
    }


def compare_ssh_roce_bench(
    *,
    profile: str,
    topology_id: str,
    model_id: str,
    bytes_logical: int,
    control_seconds: float,
    ssh_roce_seconds: float,
    tag: str,
    nodes: int,
    home_rank: int,
    run_order: str = "control-first",
    copy_streams: int = 1,
    ssh_roce_map: dict[str, Any] | None = None,
    control_phases: dict[str, Any] | None = None,
    ssh_roce_phases: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Compare control-path SSH copy vs SSH-over-RoCE-IP copy (experiment)."""
    if control_seconds < 0 or ssh_roce_seconds < 0:
        fail("bench times must be non-negative")
    if run_order not in {"control-first", "roce-first"}:
        fail("run_order must be control-first or roce-first")
    if copy_streams < 1 or copy_streams > 16:
        fail("copy_streams must be between 1 and 16")
    if control_seconds == 0:
        ratio = None
        verdict = "inconclusive"
        reason = "control_seconds is zero; cannot compute speedup"
    else:
        ratio = ssh_roce_seconds / control_seconds
        if ssh_roce_seconds < control_seconds:
            verdict = "ssh_roce_faster"
            reason = "SSH-over-RoCE wall time is strictly less than control SSH"
        elif ssh_roce_seconds == control_seconds:
            verdict = "tie"
            reason = "SSH-over-RoCE and control SSH wall times are equal"
        else:
            verdict = "control_faster"
            reason = "SSH-over-RoCE wall time is not less than control SSH"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "model-library-ssh-roce-bench",
        "tag": tag,
        "profile": profile,
        "model_id": model_id,
        "topology_id": topology_id,
        "nodes": nodes,
        "home_rank": home_rank,
        "run_order": run_order,
        "copy_streams": copy_streams,
        "bytes_logical": bytes_logical,
        "control_seconds": control_seconds,
        "ssh_roce_seconds": ssh_roce_seconds,
        "ssh_roce_over_control_ratio": ratio,
        "verdict": verdict,
        "reason": reason,
        "ssh_roce_claims_faster": verdict == "ssh_roce_faster",
        "product_default_unchanged": True,
        "recorded_at": utc_now(),
    }
    if ssh_roce_map:
        report["ssh_roce_map"] = ssh_roce_map
    if control_phases:
        report["control_phases"] = control_phases
    if ssh_roce_phases:
        report["ssh_roce_phases"] = ssh_roce_phases
    if notes:
        report["notes"] = notes
    return report


def cmd_ssh_roce_map(args: argparse.Namespace) -> int:
    topology = load_topology_for_plan(args.topology_file)
    ranks = [int(x) for x in args.ranks.split(",") if str(x).strip() != ""]
    if not ranks:
        ranks = list(range(int(args.nodes)))
    report = build_ssh_roce_map(
        topology,
        home_rank=int(args.home_rank),
        target_ranks=ranks,
        rail_index=int(args.rail_index),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_validate_ssh_roce_route(args: argparse.Namespace) -> int:
    try:
        route_data = json.loads(args.route_json)
    except json.JSONDecodeError as exc:
        fail(f"route-json: {exc}")
    report = validate_ssh_roce_route(
        route_data,
        remote_ip=args.remote_ip,
        expected_netdev=args.expected_netdev,
        expected_source_ip=args.expected_source_ip,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def cmd_compare_ssh_roce_bench(args: argparse.Namespace) -> int:
    control_phases = None
    ssh_roce_phases = None
    ssh_map = None
    if getattr(args, "control_phases_json", None):
        try:
            control_phases = json.loads(args.control_phases_json)
        except json.JSONDecodeError as exc:
            fail(f"control-phases-json: {exc}")
    if getattr(args, "ssh_roce_phases_json", None):
        try:
            ssh_roce_phases = json.loads(args.ssh_roce_phases_json)
        except json.JSONDecodeError as exc:
            fail(f"ssh-roce-phases-json: {exc}")
    if getattr(args, "ssh_roce_map_json", None):
        try:
            ssh_map = json.loads(args.ssh_roce_map_json)
        except json.JSONDecodeError as exc:
            fail(f"ssh-roce-map-json: {exc}")
    report = compare_ssh_roce_bench(
        profile=args.profile,
        topology_id=args.topology_id,
        model_id=args.model_id,
        bytes_logical=args.bytes_logical,
        control_seconds=args.control_seconds,
        ssh_roce_seconds=args.ssh_roce_seconds,
        tag=args.tag,
        nodes=args.nodes,
        home_rank=args.home_rank,
        run_order=args.run_order,
        copy_streams=args.copy_streams,
        ssh_roce_map=ssh_map if isinstance(ssh_map, dict) else None,
        control_phases=control_phases if isinstance(control_phases, dict) else None,
        ssh_roce_phases=ssh_roce_phases if isinstance(ssh_roce_phases, dict) else None,
        notes=getattr(args, "notes", None) or None,
    )
    if args.output:
        atomic_write_json(args.output, report, mode=0o644)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


REMOVED_ALLOW_UNVALIDATED_MESSAGE = (
    "--allow-unvalidated was removed (ADR 0008): Drop the flag. "
    "Lab expected-identity files are not a live product (ADR 0012)."
)
REMOVED_CATALOG_VALIDATED_MESSAGE = (
    "--validated was removed (ADR 0008): drop the flag. "
    "--reviewed-identity is retired (ADR 0012). "
    "It does not mean ADR 0004 Validated."
)
REMOVED_REVIEWED_IDENTITY_MESSAGE = (
    "--reviewed-identity is retired (ADR 0012): lab expected-identity catalog "
    "filter is not a live product"
)


class _RefuseRemovedFlag(argparse.Action):
    def __init__(self, option_strings, dest, message: str, **kwargs):
        kwargs["nargs"] = 0
        kwargs["default"] = False
        super().__init__(option_strings, dest, **kwargs)
        self.message = message

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        parser.exit(status=2, message=self.message + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pulsar federated model library")
    sub = parser.add_subparsers(dest="command", required=True)

    health_scan = sub.add_parser("scan-hot-health", help="Scan one managed hot root")
    health_scan.add_argument("--hot-root", default="")
    health_scan.add_argument("--rank", type=int, required=True)
    health_scan.add_argument("--node-id", required=True)
    health_scan.set_defaults(func=cmd_scan_hot_health)

    health_build = sub.add_parser("build-health", help="Merge rank health observations")
    health_build.add_argument("--catalog", required=True)
    health_build.add_argument("--topology-file", required=True)
    health_build.add_argument("--topology-id", required=True)
    health_build.add_argument("--observations-dir", required=True)
    health_build.add_argument("--library-dir", default="")
    health_build.set_defaults(func=cmd_build_health)

    health_render = sub.add_parser("render-health", help="Render a health report")
    health_render.add_argument("--report-file", required=True)
    health_render.add_argument("--json", action="store_true")
    health_render.add_argument("--doctor-rows", action="store_true")
    health_render.set_defaults(func=cmd_render_health)

    acquisition_inspect = sub.add_parser(
        "inspect-home-acquisition-target",
        help="Inspect one rank for a Hugging Face home add --revision target",
    )
    acquisition_inspect.add_argument("--cache-root", required=True)
    acquisition_inspect.add_argument("--model-id", required=True)
    acquisition_inspect.add_argument("--revision", required=True)
    acquisition_inspect.add_argument("--required-content-bytes", type=int, required=True)
    acquisition_inspect.add_argument("--rank", type=int, required=True)
    acquisition_inspect.add_argument("--node-id", required=True)
    acquisition_inspect.add_argument("--hf-cli", default="")
    acquisition_inspect.set_defaults(func=cmd_inspect_home_acquisition_target)

    owned_create = sub.add_parser(
        "create-owned-hub-staging",
        help="Create approval-owned same-filesystem acquisition staging",
    )
    owned_create.add_argument("--hub-root", required=True)
    owned_create.add_argument("--owner-id", required=True)
    owned_create.add_argument("--rank", type=int, required=True)
    owned_create.add_argument("--node-id", required=True)
    owned_create.set_defaults(func=cmd_create_owned_hub_staging)

    owned_cleanup = sub.add_parser(
        "cleanup-owned-hub-staging",
        help="Remove one approval-owned incomplete acquisition staging tree",
    )
    owned_cleanup.add_argument("--staging-root", required=True)
    owned_cleanup.add_argument("--owner-id", required=True)
    owned_cleanup.add_argument("--rank", type=int, required=True)
    owned_cleanup.add_argument("--node-id", required=True)
    owned_cleanup.add_argument("--hub-root", default="")
    owned_cleanup.set_defaults(func=cmd_cleanup_owned_hub_staging)

    owned_publish = sub.add_parser(
        "publish-owned-hub-staging",
        help="Atomically publish one approval-owned staged hub",
    )
    owned_publish.add_argument("--staging-root", required=True)
    owned_publish.add_argument("--owner-id", required=True)
    owned_publish.add_argument("--rank", type=int, required=True)
    owned_publish.add_argument("--node-id", required=True)
    owned_publish.add_argument("--model-id", required=True)
    owned_publish.add_argument("--target-hub", required=True)
    owned_publish.add_argument("--hub-root", default="")
    owned_publish.set_defaults(func=cmd_publish_owned_hub_staging)

    live_identity = sub.add_parser(
        "inspect-live-directory-identity",
        help="Inspect one live directory with no-follow device/inode identity",
    )
    live_identity.add_argument("--path", required=True)
    live_identity.set_defaults(func=cmd_inspect_live_directory_identity)

    inspect_identities = sub.add_parser(
        "inspect-snapshot-blob-identities",
        help="Hash one staged hub snapshot and report Git/LFS identities",
    )
    inspect_identities.add_argument("--hub-path", required=True)
    inspect_identities.add_argument("--model-id", required=True)
    inspect_identities.add_argument("--revision", required=True)
    inspect_identities.add_argument("--allow-empty-files", action="store_true")
    inspect_identities.set_defaults(func=cmd_inspect_snapshot_blob_identities)

    absence_recheck = sub.add_parser(
        "recheck-home-acquisition-absence",
        help="Recheck every confirmed rank before receipted home publication",
    )
    absence_recheck.add_argument("--topology-file", required=True)
    absence_recheck.add_argument("--topology-id", required=True)
    absence_recheck.add_argument("--observations-dir", required=True)
    absence_recheck.add_argument("--model-id", required=True)
    absence_recheck.add_argument("--revision", required=True)
    absence_recheck.add_argument("--required-content-bytes", type=int, required=True)
    absence_recheck.add_argument("--selected-rank", type=int, required=True)
    absence_recheck.add_argument("--selected-node-id", required=True)
    absence_recheck.add_argument("--selected-target-hub", required=True)
    absence_recheck.set_defaults(func=cmd_recheck_home_acquisition_absence)

    home_target = sub.add_parser(
        "resolve-home-removal-target",
        help="Resolve one exact durable home for guarded removal",
    )
    home_target.add_argument("--catalog", required=True)
    home_target.add_argument("--node", default="")
    home_target.add_argument("query")
    home_target.set_defaults(func=cmd_resolve_home_removal_target)

    home_inspect = sub.add_parser(
        "inspect-removable-home",
        help="Inspect one exact HF repository before guarded removal",
    )
    home_inspect.add_argument("--hub-path", required=True)
    home_inspect.add_argument("--cache-root", required=True)
    home_inspect.add_argument("--model-id", required=True)
    home_inspect.add_argument("--revision", required=True)
    home_inspect.add_argument("--rank", type=int, required=True)
    home_inspect.add_argument("--node-id", required=True)
    home_inspect.set_defaults(func=cmd_inspect_removable_home)

    hot_refs = sub.add_parser(
        "scan-home-hot-references",
        help="Scan one rank for managed hot references to durable homes",
    )
    hot_refs.add_argument("--hot-root", default="")
    hot_refs.add_argument("--rank", type=int, required=True)
    hot_refs.add_argument("--node-id", required=True)
    hot_refs.set_defaults(func=cmd_scan_home_hot_references)

    home_plan = sub.add_parser(
        "plan-home-removal",
        help="Build a fail-closed all-node durable-home removal plan",
    )
    home_plan.add_argument("--catalog", required=True)
    home_plan.add_argument("--topology-file", required=True)
    home_plan.add_argument("--topology-id", required=True)
    home_plan.add_argument("--models-dir", required=True)
    home_plan.add_argument("--inspection-file", required=True)
    home_plan.add_argument("--observations-dir", required=True)
    home_plan.add_argument("--node", default="")
    home_plan.add_argument("--allow-last-home", action="store_true")
    home_plan.add_argument("--allow-unarchived-last-home", action="store_true")
    home_plan.add_argument("--library-dir", default="")
    home_plan.add_argument("query")
    home_plan.set_defaults(func=cmd_plan_home_removal)

    home_render = sub.add_parser(
        "render-home-removal-plan",
        help="Render a guarded durable-home removal plan",
    )
    home_render.add_argument("--plan-file", default="")
    home_render.add_argument("--plan-json", default="")
    home_render.set_defaults(func=cmd_render_home_removal_plan)

    home_execute = sub.add_parser(
        "execute-home-removal",
        help="Execute an eligible guarded durable-home removal plan",
    )
    home_execute.add_argument("--plan-file", default="")
    home_execute.add_argument("--plan-json", default="")
    home_execute.add_argument("--rank", type=int, required=True)
    home_execute.add_argument("--node-id", required=True)
    home_execute.set_defaults(func=cmd_execute_home_removal)

    home_reverify = sub.add_parser(
        "reverify-last-home-archive",
        help="Controller-only last-occupancy cold-archive re-verify before detach",
    )
    home_reverify.add_argument("--plan-file", default="")
    home_reverify.add_argument("--plan-json", default="")
    home_reverify.add_argument("--library-dir", required=True)
    home_reverify.set_defaults(func=cmd_reverify_last_home_archive)

    home_result = sub.add_parser(
        "render-home-removal-result",
        help="Render a completed durable-home removal result",
    )
    home_result.add_argument("--result-file", required=True)
    home_result.set_defaults(func=cmd_render_home_removal_result)

    scan = sub.add_parser("scan-hub", help="Scan one HF cache root for hub models")
    scan.add_argument("--cache-root", required=True)
    scan.add_argument("--rank", type=int, required=True)
    scan.add_argument("--node-id", required=True)
    scan.add_argument("--hostname", default="")
    scan.add_argument("--ssh-host", default="")
    scan.set_defaults(func=cmd_scan_hub)
    inspect = sub.add_parser("inspect-hub", help="Inspect one catalog hub home")
    inspect.add_argument("--hub-path", required=True)
    inspect.add_argument("--rank", type=int, required=True)
    inspect.add_argument("--node-id", required=True)
    inspect.add_argument("--model-id", default="")
    inspect.add_argument(
        "--revision",
        default="",
        help="Exact snapshot revision to inspect (never inferred from mutable main)",
    )
    inspect.add_argument(
        "--allow-empty-files",
        action="store_true",
        help="Permit tracked zero-byte files in the complete snapshot manifest",
    )
    inspect.set_defaults(func=cmd_inspect_hub)

    build = sub.add_parser("build", help="Build catalog from homes JSON + profiles")
    build.add_argument("--topology-id", required=True)
    build.add_argument("--models-dir", required=True)
    build.add_argument("--homes-json", help="JSON array of scanned homes")
    build.add_argument("--output", help="Write catalog.json here")
    build.add_argument(
        "--preserve-primary-from",
        default="",
        help="Carry exact primary selections forward from an existing catalog",
    )
    build.add_argument("--primary", action="append", default=[], help="identity_or_model=node_id")
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_build)

    list_p = sub.add_parser("list", help="List catalog entries")
    list_p.add_argument("--catalog", required=True)
    list_p.add_argument(
        "--reviewed-identity",
        dest="_removed_reviewed_identity",
        action=_RefuseRemovedFlag,
        message=REMOVED_REVIEWED_IDENTITY_MESSAGE,
        help=argparse.SUPPRESS,
    )
    list_p.add_argument(
        "--validated",
        dest="_removed_validated",
        action=_RefuseRemovedFlag,
        message=REMOVED_CATALOG_VALIDATED_MESSAGE,
        help=argparse.SUPPRESS,
    )
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show one catalog entry")
    show.add_argument("--catalog", required=True)
    show.add_argument("query")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)

    resolve = sub.add_parser(
        "resolve", help="Resolve profile/model: warm home, then optional cold"
    )
    resolve.add_argument("--catalog", default="")
    resolve.add_argument("--profile")
    resolve.add_argument("--model")
    resolve.add_argument("query", nargs="?")
    resolve.add_argument("--json", action="store_true")
    resolve.add_argument(
        "--cold-root",
        default="",
        help="Cold archive root (default: PULSAR_COLD_ROOT or MODELS_NFS)",
    )
    resolve.add_argument(
        "--no-cold",
        action="store_true",
        help="Disable cold fall-through (warm catalog only)",
    )
    resolve.add_argument(
        "--models-dir",
        default="",
        help="models/*.conf dir for profile → MODEL mapping",
    )
    resolve.add_argument(
        "--allow-missing-catalog",
        action="store_true",
        help="Allow resolve with only cold when catalog file is absent",
    )
    resolve.set_defaults(func=cmd_resolve)

    scan_cold = sub.add_parser("scan-cold", help="Scan optional cold archive")
    scan_cold.add_argument("--cold-root", default="")
    scan_cold.add_argument("--complete-only", action="store_true")
    scan_cold.add_argument("--json", action="store_true")
    scan_cold.set_defaults(func=cmd_scan_cold)

    find_cold = sub.add_parser("find-cold", help="Find one model in cold archive")
    find_cold.add_argument("--cold-root", default="")
    find_cold.add_argument("--model")
    find_cold.add_argument("--path")
    find_cold.add_argument("query", nargs="?")
    find_cold.add_argument(
        "--allow-partial",
        action="store_true",
        help="Return partial trees (default: complete only)",
    )
    find_cold.set_defaults(func=cmd_find_cold)

    adopt = sub.add_parser(
        "plan-cold-adopt",
        help="Plan (or execute) cold → durable warm HF hub home",
    )
    adopt.add_argument("--cold-root", default="")
    adopt.add_argument("--model")
    adopt.add_argument("--path")
    adopt.add_argument("--profile")
    adopt.add_argument("--models-dir", default="")
    adopt.add_argument("--cache-root", required=True, help="Warm HF cache root")
    adopt.add_argument(
        "--execute",
        action="store_true",
        help="Copy into cache-root (local filesystem)",
    )
    adopt.set_defaults(func=cmd_plan_cold_adopt)

    stage = sub.add_parser(
        "plan-cold-stage",
        help="Plan (or execute) cold → hot stage-only (no warm home)",
    )
    stage.add_argument("--cold-root", default="")
    stage.add_argument("--profile", required=True)
    stage.add_argument("--topology-id", required=True)
    stage.add_argument("--hot-root", default="")
    stage.add_argument("--model")
    stage.add_argument("--path")
    stage.add_argument("--catalog", default="")
    stage.add_argument("--models-dir", default="")
    stage.add_argument("--nodes", type=int, default=1)
    stage.add_argument(
        "--allow-unvalidated",
        dest="_removed_allow_unvalidated",
        action=_RefuseRemovedFlag,
        message=REMOVED_ALLOW_UNVALIDATED_MESSAGE,
        help=argparse.SUPPRESS,
    )
    stage.add_argument(
        "--execute",
        action="store_true",
        help="Materialize hub into hot + write stamp (local)",
    )
    stage.set_defaults(func=cmd_plan_cold_stage)

    cleanup = sub.add_parser("cleanup-recommend", help="Recommend cleanup for duplicates")
    cleanup.add_argument("--catalog", required=True)
    cleanup.add_argument("--json", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup_recommend)

    catalog_primary = sub.add_parser(
        "catalog-primary",
        help="List, set, or clear persistent exact-revision primary selections",
    )
    primary_actions = catalog_primary.add_subparsers(
        dest="primary_action",
        required=True,
    )
    primary_list = primary_actions.add_parser("list", help="List primary state")
    primary_list.add_argument("--catalog", required=True)
    primary_list.add_argument("--json", action="store_true")
    primary_list.set_defaults(func=cmd_catalog_primary)
    primary_set = primary_actions.add_parser(
        "set",
        help="Persist one complete home as primary for an exact revision",
    )
    primary_set.add_argument("--catalog", required=True)
    primary_set.add_argument("--topology-id", required=True)
    primary_set.add_argument("--topology-file", required=True)
    primary_set.add_argument("--node", required=True)
    primary_set.add_argument("query")
    primary_set.add_argument("--json", action="store_true")
    primary_set.set_defaults(func=cmd_catalog_primary)
    primary_clear = primary_actions.add_parser(
        "clear",
        help="Clear an explicit primary selection",
    )
    primary_clear.add_argument("--catalog", required=True)
    primary_clear.add_argument("--topology-id", required=True)
    primary_clear.add_argument("query")
    primary_clear.add_argument("--json", action="store_true")
    primary_clear.set_defaults(func=cmd_catalog_primary)

    plan = sub.add_parser(
        "plan-prepare", help="Plan copy preparation into hot staging"
    )
    plan.add_argument("--catalog", required=True)
    plan.add_argument("--profile", required=True)
    plan.add_argument("--topology-id", required=True)
    plan.add_argument("--hot-root", default="")
    plan.add_argument("--models-dir", required=True)
    plan.add_argument("--backend", default="", choices=("copy",))
    plan.add_argument(
        "--transport",
        default="",
        choices=tuple(ACTIVATE_TRANSPORT_BACKENDS),
        help="Transfer path: ssh-control or ssh-roce",
    )
    plan.add_argument("--nodes", type=int, default=1)
    plan.add_argument(
        "--target-rank",
        type=int,
        default=None,
        help="Exact serving rank for a one-node profile",
    )
    plan.add_argument(
        "--allow-unvalidated",
        dest="_removed_allow_unvalidated",
        action=_RefuseRemovedFlag,
        message=REMOVED_ALLOW_UNVALIDATED_MESSAGE,
        help=argparse.SUPPRESS,
    )
    plan.add_argument(
        "--topology-file",
        default="",
        help="Confirmed cluster topology JSON",
    )
    plan.add_argument(
        "--home-inventory-json",
        default="",
        help=argparse.SUPPRESS,
    )
    plan.add_argument(
        "--require-exact-revision",
        default="",
        help=argparse.SUPPRESS,
    )
    plan.add_argument(
        "--expected-integrity-manifest-json",
        default="",
        help=argparse.SUPPRESS,
    )
    plan.set_defaults(func=cmd_plan_prepare)

    classify = sub.add_parser(
        "classify-library-readiness",
        help="Classify why prepared views are missing without restaging",
    )
    classify.add_argument("--profile", required=True)
    classify.add_argument("--catalog", default="")
    classify.add_argument("--topology-id", default="")
    classify.add_argument("--models-dir", required=True)
    classify.add_argument("--selected-rank", type=int, default=None)
    classify.add_argument("--selected-node-id", default="")
    classify.set_defaults(func=cmd_classify_library_readiness)

    whs = sub.add_parser("write-hot-stamp", help="Write hot.json for an instance dir")
    whs.add_argument("--instance-dir", required=True)
    whs.add_argument("--stamp-file")
    whs.add_argument("--stamp-json")
    whs.add_argument("--json", action="store_true")
    whs.set_defaults(func=cmd_write_hot_stamp)

    vh = sub.add_parser("verify-hot", help="Verify hot instance is ready")
    vh.add_argument("--instance-dir", required=True)
    vh.add_argument("--profile")
    vh.add_argument("--topology-id")
    vh.add_argument("--models-dir", default="")
    vh.add_argument("--expected-validation-json", default="", help=argparse.SUPPRESS)
    verify_mode = vh.add_mutually_exclusive_group()
    verify_mode.add_argument("--skip-digest", action="store_true")
    verify_mode.add_argument(
        "--serve-time-witness",
        action="store_true",
        help="Use the rank-local metadata witness, with visible full-verify fallback",
    )
    verify_mode.add_argument(
        "--refresh-witness",
        action="store_true",
        help="Full-verify and atomically refresh the rank-local witness",
    )
    vh.add_argument("--workers", type=int)
    vh.add_argument(
        "--allow-verifying", action="store_true", help=argparse.SUPPRESS
    )
    vh.set_defaults(func=cmd_verify_hot)

    fh = sub.add_parser("find-hot", help="Find ready hot instance for profile")
    fh.add_argument("--profile", required=True)
    fh.add_argument("--topology-id", required=True)
    fh.add_argument("--hot-root", default="")
    fh.add_argument("--models-dir", default="")
    fh.set_defaults(func=cmd_find_hot)

    vhs = sub.add_parser(
        "validate-hot-stamp",
        help="Validate a hot ownership document against a trusted profile",
    )
    vhs.add_argument("--profile", required=True)
    vhs.add_argument("--models-dir", required=True)
    vhs_source = vhs.add_mutually_exclusive_group(required=True)
    vhs_source.add_argument("--stamp-json")
    vhs_source.add_argument("--stamp-file")
    vhs.set_defaults(func=cmd_validate_hot_stamp)

    sp = sub.add_parser("set-pinned", help="Set pinned flag on hot stamp")
    sp.add_argument("--instance-dir", required=True)
    sp.add_argument("--pinned", action=argparse.BooleanOptionalAction, default=True)
    sp.set_defaults(func=cmd_set_pinned)

    ph = sub.add_parser("purge-hot", help="Delete a hot instance directory")
    ph.add_argument("--instance-dir", required=True)
    ph.add_argument("--force-unpin", action="store_true")
    ph.add_argument("--json", action="store_true")
    ph.set_defaults(func=cmd_purge_hot)

    bud = sub.add_parser("budget", help="Report hot staging disk budget")
    bud.add_argument("--hot-root", default="")
    bud.add_argument("--budget-bytes", type=int, default=None)
    bud.add_argument("--reserve-bytes", type=int, default=None)
    bud.add_argument("--json", action="store_true")
    bud.set_defaults(func=cmd_budget)

    badmit = sub.add_parser(
        "budget-admission",
        help="Observe one rank's filesystem-backed hot admission policy",
    )
    badmit.add_argument("--hot-root", default="")
    badmit.add_argument("--rank", type=int, required=True)
    badmit.add_argument("--node-id", required=True)
    badmit.add_argument("--hostname", default="")
    badmit.add_argument(
        "--runtime-source",
        required=True,
        choices=("durable-home", "working-copy", "inventory", "pin"),
    )
    badmit.add_argument("--required-owned-bytes", type=int, required=True)
    badmit.add_argument("--replacing-path", default="")
    badmit.add_argument("--hard-cap-bytes", type=int, default=None)
    badmit.add_argument("--reserve-bytes", type=int, default=None)
    badmit.add_argument("--compact", action="store_true")
    badmit.set_defaults(func=cmd_budget_admission)

    bmerge = sub.add_parser(
        "merge-budget-admissions",
        help="Merge exact all-rank hot budget observations",
    )
    bmerge.add_argument("--observations-file", required=True)
    bmerge.add_argument("--expected-ranks", required=True)
    bmerge.add_argument("--topology-id", required=True)
    bmerge.add_argument("--mode", required=True)
    bmerge.add_argument("--profile", default="")
    bmerge.add_argument("--model-id", default="")
    bmerge.add_argument("--bytes-logical", type=int, default=0)
    bmerge.set_defaults(func=cmd_merge_budget_admissions)

    brender = sub.add_parser(
        "render-budget-plan",
        help="Render an all-rank hot admission plan",
    )
    brender_source = brender.add_mutually_exclusive_group(required=True)
    brender_source.add_argument("--plan-json")
    brender_source.add_argument("--plan-file")
    brender.set_defaults(func=cmd_render_budget_plan)

    inv = sub.add_parser("inventory-digest", help="Digest a hub tree")
    inv.add_argument("--hub-path", required=True)
    inv.set_defaults(func=cmd_inventory_digest)

    partition = sub.add_parser(
        "partition-blobs", help="Balance HF blobs across parallel copy streams"
    )
    partition.add_argument("--hub-path", required=True)
    partition.add_argument("--streams", type=int, required=True)
    partition.set_defaults(func=cmd_partition_blobs)

    srm = sub.add_parser(
        "ssh-roce-map",
        help="Map ranks to RoCE IPs for experimental SSH-over-RoCE rsync",
    )
    srm.add_argument("--topology-file", required=True)
    srm.add_argument("--home-rank", type=int, required=True)
    srm.add_argument(
        "--nodes",
        type=int,
        default=2,
        help="If --ranks omitted, use ranks 0..nodes-1",
    )
    srm.add_argument(
        "--ranks",
        default="",
        help="Comma-separated ranks (default: 0..nodes-1)",
    )
    srm.add_argument("--rail-index", type=int, default=DEFAULT_FABRIC_RAIL_INDEX)
    srm.set_defaults(func=cmd_ssh_roce_map)

    route = sub.add_parser(
        "validate-ssh-roce-route",
        help="Validate a live route against the confirmed RoCE endpoint",
    )
    route.add_argument("--route-json", required=True)
    route.add_argument("--remote-ip", required=True)
    route.add_argument("--expected-netdev", required=True)
    route.add_argument("--expected-source-ip", required=True)
    route.set_defaults(func=cmd_validate_ssh_roce_route)

    csrb = sub.add_parser(
        "compare-ssh-roce-bench",
        help="Compare control SSH copy vs SSH-over-RoCE copy (experiment)",
    )
    csrb.add_argument("--profile", required=True)
    csrb.add_argument("--topology-id", required=True)
    csrb.add_argument("--model-id", required=True)
    csrb.add_argument("--bytes-logical", type=int, required=True)
    csrb.add_argument("--control-seconds", type=float, required=True)
    csrb.add_argument("--ssh-roce-seconds", type=float, required=True)
    csrb.add_argument("--tag", required=True)
    csrb.add_argument("--nodes", type=int, required=True)
    csrb.add_argument("--home-rank", type=int, required=True)
    csrb.add_argument("--copy-streams", type=int, default=1)
    csrb.add_argument(
        "--run-order",
        choices=("control-first", "roce-first"),
        default="control-first",
    )
    csrb.add_argument("--control-phases-json", default="")
    csrb.add_argument("--ssh-roce-phases-json", default="")
    csrb.add_argument("--ssh-roce-map-json", default="")
    csrb.add_argument("--notes", default="")
    csrb.add_argument("--output", default="")
    csrb.set_defaults(func=cmd_compare_ssh_roce_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ModelLibraryError as exc:
        print(f"model-library: ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
