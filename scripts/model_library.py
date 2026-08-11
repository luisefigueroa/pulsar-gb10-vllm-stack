#!/usr/bin/env python3
"""Federated model library: warm catalog + optional cold + hot staging.

Bash owns topology/SSH, rsync activate/adopt, and operator entrypoints. This
module owns schemas, hub/flat completeness, labels, digests, hot.json, disk
budget, and cold-archive resolve (warm → cold → fail closed).
"""

from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 2
HOT_SCHEMA_VERSION = 3
HOT_WITNESS_SCHEMA_VERSION = 1
HOT_WITNESS_KIND = "pulsar-model-library-serve-witness"
HOT_WITNESS_SCHEME = "stat-witness-v1"
SNAPSHOT_MANIFEST_SCHEMA_VERSION = 1
SNAPSHOT_MANIFEST_KIND = "model-library-snapshot-manifest"
SNAPSHOT_INTEGRITY_SCHEME = "sha256-snapshot-manifest-v1"
EXPECTED_MODEL_SEAL_SCHEMA_VERSION = 1
EXPECTED_MODEL_SEAL_KIND = "pulsar-expected-model-seal"
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
HF_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
HF_MODEL_ID_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
HUB_DIR_RE = re.compile(r"^models--(.+)$")
SAFE_REV = re.compile(r"^[A-Za-z0-9._-]+$")
STATUS_TESTED = re.compile(r"^tested")
DEFAULT_HOT_ROOT = "/var/tmp/pulsar-hot"
DEFAULT_HOT_BUDGET_BYTES = 100 * 1024**3  # 100 GiB
DEFAULT_FABRIC_PORT = 20049
DEFAULT_FABRIC_RAIL_INDEX = 0
TRANSFER_MOUNT_OPTIONS = (
    "ro,vers=4.2,proto=rdma,port=20049,hard,timeo=600,retrans=2"
)
ACTIVATE_TRANSPORT_BACKENDS = {
    "ssh-control": "copy",
    "ssh-roce": "copy",
    "nfs-rdma": "fabric",
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


def expected_model_seal_identity(seal: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in seal.items() if key != "seal_id"}


def expected_model_seal_id(seal: dict[str, Any]) -> str:
    return canonical_json_digest(expected_model_seal_identity(seal))


def _validate_evidence_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        fail("expected seal evidence path must be a non-empty string")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        fail(f"expected seal evidence path must be repository-relative: {value!r}")
    return value


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
            f"expected model seal profile differs: seal={seal_profile} profile={profile}"
        )
    seal_model = seal.get("model_id")
    if (
        not isinstance(seal_model, str)
        or HF_MODEL_ID_RE.fullmatch(seal_model) is None
    ):
        fail("expected model seal model_id must be an exact Hugging Face repository ID")
    if model_id and seal_model != model_id:
        fail(
            f"expected model seal model_id differs: seal={seal_model} profile={model_id}"
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
    issued_at = provenance.get("issued_at")
    if not isinstance(issued_at, str) or not issued_at.endswith("Z"):
        fail("expected model seal issued_at must be an RFC3339 UTC timestamp")
    try:
        parsed_issued_at = datetime.fromisoformat(issued_at[:-1] + "+00:00")
    except ValueError:
        fail("expected model seal issued_at must be an RFC3339 UTC timestamp")
    if parsed_issued_at.tzinfo is None:
        fail("expected model seal issued_at must include UTC")
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


def load_profile_expected_model_seal(
    profile_path: pathlib.Path,
    reference: str | None,
    *,
    profile: str,
    model_id: str,
) -> dict[str, Any] | None:
    if not reference:
        return None
    relative = pathlib.PurePosixPath(reference)
    if relative.is_absolute() or ".." in relative.parts or "\\" in reference:
        fail(f"{profile}: EXPECTED_MODEL_SEAL must be relative to models/")
    seal_root = (profile_path.parent / "seals").resolve()
    candidate = (profile_path.parent / pathlib.Path(reference)).resolve()
    try:
        candidate.relative_to(seal_root)
    except ValueError:
        fail(f"{profile}: EXPECTED_MODEL_SEAL must live under models/seals/")
    if not candidate.is_file():
        fail(f"{profile}: expected model seal is missing: {candidate}")
    seal = validate_expected_model_seal(
        load_json(candidate),
        profile=profile,
        model_id=model_id,
    )
    repository_root = profile_path.parent.parent.resolve()
    for evidence_ref in seal["provenance"]["evidence"]:
        evidence_path = (repository_root / evidence_ref).resolve()
        try:
            evidence_path.relative_to(repository_root)
        except ValueError:
            fail(f"{profile}: expected seal evidence escapes the repository")
        if not evidence_path.is_file():
            fail(
                f"{profile}: expected seal evidence is missing: {evidence_ref}"
            )
    return seal


def hub_dirname_to_model_id(dirname: str) -> str | None:
    match = HUB_DIR_RE.fullmatch(dirname)
    if not match:
        return None
    # models--org--name → org/name (HF uses -- between path segments)
    return match.group(1).replace("--", "/")


def model_id_to_hub_dirname(model_id: str) -> str:
    return "models--" + model_id.replace("/", "--")


def read_revision(hub_root: pathlib.Path) -> str | None:
    """Return mutable refs/main for legacy callers; never use it as sealed identity."""
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
    """Return legacy refs/main state; sealed paths use hub_snapshot_state()."""
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
    expected_seal = load_profile_expected_model_seal(
        path,
        expected_seal_ref,
        profile=path.stem,
        model_id=model_id,
    )
    tested = bool(STATUS_TESTED.match(status))
    if expected_seal is not None and not tested:
        fail(f"{path.stem}: EXPECTED_MODEL_SEAL requires STATUS=tested*")
    return {
        "profile": path.stem,
        "model_id": model_id,
        "absolute_path": model if absolute else None,
        "status": status,
        "nodes": nodes,
        "validated": tested,
        "expected_model_seal_ref": expected_seal_ref,
        "expected_model_seal": expected_seal,
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
    expected_seal = load_profile_expected_model_seal(
        path,
        expected_seal_ref,
        profile=path.stem,
        model_id=model,
    )
    tested = bool(STATUS_TESTED.match(status))
    if expected_seal is not None and not tested:
        fail(f"{path.stem}: EXPECTED_MODEL_SEAL requires STATUS=tested*")
    return {
        "profile": path.stem,
        "model_id": model,
        "status": status,
        "nodes": nodes,
        "validated": tested,
        "expected_model_seal_ref": expected_seal_ref,
        "expected_model_seal": expected_seal,
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
    """Compare observed bytes with a profile's lab-issued trust root."""
    observed = observed_model_seal_projection(manifest)
    expected = profile.get("expected_model_seal")
    if expected is None:
        return {
            "identity_status": (
                "legacy-unsealed" if profile.get("validated") else "unvalidated"
            ),
            "expected_seal": None,
            "observed_seal": observed,
        }

    expected_projection = expected_model_seal_projection(expected)
    for field in ("model_id", "snapshot_revision", "manifest_id"):
        if observed[field] != expected_projection[field]:
            fail(
                f"expected model seal mismatch: {field} "
                f"observed={observed[field]} expected={expected_projection[field]}"
            )
    return {
        "identity_status": "match" if profile.get("validated") else "unvalidated",
        "expected_seal": expected_projection,
        "observed_seal": observed,
    }


def require_activation_identity(
    profile: dict[str, Any],
    manifest: dict[str, Any],
    *,
    allow_unvalidated: bool,
) -> dict[str, Any]:
    validation = compare_profile_expected_identity(profile, manifest)
    status = validation["identity_status"]
    if status != "match" and not allow_unvalidated:
        if status == "legacy-unsealed":
            fail(
                f"activate: {profile['profile']} is legacy-unsealed; add a reviewed "
                "EXPECTED_MODEL_SEAL or pass --allow-unvalidated for an explicit experiment"
            )
        fail(
            f"activate: {profile['profile']} identity status is {status}; "
            "pass --allow-unvalidated for an explicit experiment"
        )
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
    if not profile.get("validated"):
        return "unvalidated"
    if profile.get("expected_model_seal") is None:
        return "legacy-unsealed"
    return "expected-unverified"


def build_catalog(
    *,
    topology_id: str,
    homes: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    primary_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge scanned homes with exact profile/seal identity expectations."""
    primary_overrides = primary_overrides or {}
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
        expected = profile.get("expected_model_seal")
        expected_revision = expected.get("snapshot_revision") if expected else None
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
                "expected_model_seal_ref": profile.get("expected_model_seal_ref"),
                "expected_model_seal": (
                    expected_model_seal_projection(expected) if expected else None
                ),
            }
            entry["profiles"].append(profile["profile"])
            entry["profile_validation"].append(profile_state)

    precedence = (
        "expected-unverified",
        "legacy-unsealed",
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
        entry["duplicate"] = len(complete_homes) > 1
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
        primary_set = False
        if override:
            for home in complete_homes:
                if home["node_id"] == override or str(home["rank"]) == str(override):
                    home["primary"] = True
                    primary_set = True
                    break
        if not primary_set and len(complete_homes) == 1:
            complete_homes[0]["primary"] = True
            primary_set = True
        if not primary_set and len(complete_homes) > 1:
            for home in complete_homes:
                home["primary"] = False
        entry["has_primary"] = any(h.get("primary") for h in complete_homes)
        models_out.append(entry)

    return {
        "schema_version": SCHEMA_VERSION,
        "refreshed_at": utc_now(),
        "topology_id": topology_id,
        "models": models_out,
    }


def load_catalog(path: str | pathlib.Path) -> dict[str, Any]:
    data = load_json(path)
    if not isinstance(data, dict):
        fail(f"{path}: expected object")
    if data.get("schema_version") != SCHEMA_VERSION:
        fail(f"{path}: unsupported schema_version {data.get('schema_version')!r}")
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
                "add an EXPECTED_MODEL_SEAL with an exact commit"
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
                "select an exact identity or profile seal"
            )
        if complete:
            return complete[0]
        missing = [entry for entry in entries if entry.get("model_id") == model_id]
        if len(missing) == 1:
            return missing[0]
    return None


def resolve_entry(
    catalog: dict[str, Any] | None,
    *,
    model_id: str | None = None,
    profile: str | None = None,
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
                expected_seal = parsed.get("expected_model_seal")
                if expected_seal:
                    profile_expected_revision = expected_seal["snapshot_revision"]

    warm_error: str | None = None
    if catalog is not None and not absolute_path:
        entry = find_model_entry(catalog, model_id=model_id, profile=profile)
        if entry is None:
            target = profile or model_id or "?"
            warm_error = f"resolve: {target}: not found in warm catalog"
        else:
            complete = [h for h in entry.get("homes") or [] if h.get("state") == "complete"]
            if not complete:
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

    # Synthetic "home" so activate/stage paths can treat cold like a source.
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
        if not entry.get("duplicate"):
            continue
        complete = [h for h in entry.get("homes") or [] if h.get("state") == "complete"]
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
                "action": (
                    "Choose one primary home (node_id or rank) and remove or ignore "
                    "the other complete copies. Activate refuses until a primary is set."
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
            expected_seal = parsed.get("expected_model_seal")
            if expected_seal:
                expected_revision = expected_seal["snapshot_revision"]

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
        validation = {
            "identity_status": "unvalidated",
            "expected_seal": None,
            "observed_seal": observed_model_seal_projection(integrity_manifest),
        }
        if not allow_unvalidated:
            fail(
                "cold stage-only: profile identity is unvalidated; "
                "pass --allow-unvalidated for an explicit experiment"
            )
    else:
        validation = require_activation_identity(
            profile_data,
            integrity_manifest,
            allow_unvalidated=allow_unvalidated,
        )
    source_digest = integrity_manifest["manifest_id"]
    # Instance path is keyed by the exact sealed snapshot identity.
    cid = hot_content_id(entry["identity_key"], source_digest, validation)
    bytes_logical = integrity_manifest["total_bytes"]
    instance = hot_instance_dir(hot_root, profile, topology_id, cid)
    hub_dest = hot_hub_path(instance, entry["model_id"])
    target_ranks = list(range(nodes if nodes is not None else 1))

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
                "stamp": existing,
            }

    ensure_budget_for_add(hot_root, bytes_logical)
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
        "stamp": stamp,
        "note": (
            "Stages cold → hot only; no durable warm home. "
            "Unpinned restart needs cold again unless you pin hot."
        ),
    }


# --- Hot staging (working set; not durable library) ---


def default_hot_root() -> str:
    return os.environ.get("PULSAR_HOT_ROOT") or DEFAULT_HOT_ROOT


def default_hot_budget_bytes() -> int:
    raw = os.environ.get("PULSAR_HOT_BUDGET_BYTES")
    if raw is None or raw == "":
        return DEFAULT_HOT_BUDGET_BYTES
    try:
        value = int(raw)
    except ValueError:
        fail(f"PULSAR_HOT_BUDGET_BYTES must be an integer (got {raw!r})")
    if value < 1:
        fail("PULSAR_HOT_BUDGET_BYTES must be positive")
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
        if size <= 0:
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
) -> dict[str, Any]:
    hub = pathlib.Path(hub_path)
    revision, files = iter_snapshot_files(hub, revision=revision)
    return _build_manifest_from_files(
        model_id=model_id,
        revision=revision,
        files=files,
        lfs_blob_root=hub / "blobs",
    )


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
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
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
    expected = validation.get("expected_seal") or {}
    validation_key = expected.get("seal_id") or validation.get("identity_status")
    if not isinstance(validation_key, str) or not validation_key:
        fail("hot content identity lacks validation provenance")
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
) -> tuple[str, str]:
    """Resolve the compatibility backend and explicit transfer transport."""
    backend = backend or ""
    transport = transport or ""
    if backend and backend not in {"copy", "fabric"}:
        fail(f"activate: backend {backend!r} not supported (use copy or fabric)")
    if transport:
        expected_backend = ACTIVATE_TRANSPORT_BACKENDS.get(transport)
        if expected_backend is None:
            choices = ", ".join(ACTIVATE_TRANSPORT_BACKENDS)
            fail(f"activate: transport {transport!r} not supported (use {choices})")
        if backend and backend != expected_backend:
            fail(
                f"activate: transport {transport} requires backend "
                f"{expected_backend}, not {backend}"
            )
        return expected_backend, transport
    resolved_backend = backend or "copy"
    resolved_transport = (
        "nfs-rdma" if resolved_backend == "fabric" else "ssh-control"
    )
    return resolved_backend, resolved_transport


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
    if status not in {"match", "legacy-unsealed", "unvalidated"}:
        fail(f"hot identity status is not launchable: {status!r}")
    observed = validation.get("observed_seal")
    expected_observed = observed_model_seal_projection(manifest)
    if observed != expected_observed:
        fail("hot observed seal differs from integrity manifest")
    expected = validation.get("expected_seal")
    if status == "match":
        required = {
            "seal_id",
            "validation_bundle_id",
            "model_id",
            "snapshot_revision",
            "manifest_id",
        }
        if not isinstance(expected, dict) or set(expected) != required:
            fail("hot expected seal projection is invalid")
        for digest_field in ("seal_id", "validation_bundle_id", "manifest_id"):
            value = expected.get(digest_field)
            if not isinstance(value, str) or SHA256_HEX_RE.fullmatch(value) is None:
                fail(f"hot expected seal {digest_field} is invalid")
        for field in ("model_id", "snapshot_revision", "manifest_id"):
            if expected.get(field) != observed.get(field):
                fail(f"hot expected and observed {field} differ")
    elif expected is not None:
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
        if item["size"] < 1:
            fail(f"witness: invalid size for {relative}")
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
        fail("activate: home inventory schema_version must be 2")
    if inventory.get("kind") != "model-library-home-inventory":
        fail("activate: home inventory kind is invalid")
    try:
        expected_rank = int(home["rank"])
    except (KeyError, TypeError, ValueError):
        fail("activate: catalog home rank is invalid")
    actual_rank = inventory.get("rank")
    if isinstance(actual_rank, bool) or actual_rank != expected_rank:
        fail("activate: home inventory rank differs from catalog home")
    expected_node_id = str(home.get("node_id") or "")
    if inventory.get("node_id") != expected_node_id:
        fail("activate: home inventory node_id differs from catalog home")
    expected_path = str(pathlib.Path(str(home.get("hub_path") or "")))
    if not pathlib.Path(expected_path).is_absolute():
        fail("activate: catalog home hub_path must be absolute")
    if inventory.get("hub_path") != expected_path:
        fail("activate: home inventory path differs from catalog home")
    if inventory.get("model_id") != model_id:
        fail("activate: home inventory model_id differs from catalog")
    state = inventory.get("state")
    if state != "complete":
        fail(f"activate: home hub is {state or 'invalid'}: {expected_path}")
    revision = inventory.get("revision")
    if catalog_revision and revision != catalog_revision:
        fail("activate: home revision differs from catalog; run catalog refresh")
    manifest = validate_snapshot_manifest(inventory.get("integrity_manifest"))
    if manifest.get("model_id") != model_id:
        fail("activate: home manifest model_id differs from catalog")
    if manifest.get("snapshot_revision") != revision:
        fail("activate: home manifest revision differs from inventory")
    digest = inventory.get("content_digest")
    if digest != manifest.get("manifest_id"):
        fail("activate: home inventory content_digest differs from manifest")
    bytes_logical = inventory.get("bytes_logical")
    if (
        isinstance(bytes_logical, bool)
        or not isinstance(bytes_logical, int)
        or bytes_logical < 1
        or bytes_logical != manifest.get("total_bytes")
    ):
        fail("activate: home inventory bytes_logical differs from manifest")
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


def budget_report(
    hot_root: str | pathlib.Path,
    budget_bytes: int | None = None,
) -> dict[str, Any]:
    hot_root = pathlib.Path(hot_root)
    budget = budget_bytes if budget_bytes is not None else default_hot_budget_bytes()
    used = 0
    instances: list[dict[str, Any]] = []
    if hot_root.is_dir():
        for stamp_path in hot_root.rglob("hot.json"):
            if stamp_path.parent.name != ".pulsar":
                continue
            instance = stamp_path.parent.parent
            try:
                stamp = load_json(stamp_path)
            except ModelLibraryError:
                continue
            size = dir_size_bytes(instance)
            used += size
            instances.append(
                {
                    "path": str(instance),
                    "profile": stamp.get("profile") if isinstance(stamp, dict) else None,
                    "state": stamp.get("state") if isinstance(stamp, dict) else None,
                    "pinned": bool(stamp.get("pinned")) if isinstance(stamp, dict) else False,
                    "bytes": size,
                }
            )
    remaining = budget - used
    return {
        "hot_root": str(hot_root),
        "budget_bytes": budget,
        "used_bytes": used,
        "remaining_bytes": remaining,
        "over_budget": remaining < 0,
        "instances": sorted(instances, key=lambda i: i["path"]),
    }


def ensure_budget_for_add(
    hot_root: str | pathlib.Path,
    add_bytes: int,
    *,
    budget_bytes: int | None = None,
    replacing_path: str | pathlib.Path | None = None,
) -> dict[str, Any]:
    report = budget_report(hot_root, budget_bytes=budget_bytes)
    used = report["used_bytes"]
    if replacing_path is not None:
        rep = pathlib.Path(replacing_path)
        if rep.is_dir():
            used = max(0, used - dir_size_bytes(rep))
    budget = report["budget_bytes"]
    if used + add_bytes > budget:
        fail(
            f"hot budget exceeded: need {add_bytes} more bytes, "
            f"used={used}, budget={budget}, hot_root={report['hot_root']}"
        )
    return report


def load_topology_for_plan(topology_file: str | pathlib.Path | None) -> dict[str, Any]:
    """Load confirmed topology for fabric rail selection."""
    if not topology_file:
        fail("fabric activate requires --topology-file (confirmed cluster topology)")
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


def selected_rail_between(
    topology: dict[str, Any],
    home_rank: int,
    client_rank: int,
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    try:
        try:
            from scripts.weight_fabric import selected_rail
        except ModuleNotFoundError:
            from weight_fabric import selected_rail
    except Exception as exc:
        fail(f"cannot import rail selection: {exc}")
    try:
        return selected_rail(topology, home_rank, client_rank, rail_index)
    except Exception as exc:
        fail(f"rail selection ranks {home_rank}/{client_rank}: {exc}")


def transfer_mount_path(
    hot_root: str | pathlib.Path,
    profile: str,
    topology_id: str,
    content_id: str,
) -> pathlib.Path:
    """Ephemeral NFS mount point — not the final hot hub path."""
    topo12 = (topology_id or "notopology")[:12]
    return (
        pathlib.Path(hot_root)
        / ".transfer"
        / f"{profile}-{topo12}"
        / content_id
    )


def build_fabric_transfer(
    *,
    topology: dict[str, Any],
    home: dict[str, Any],
    hub_path: str,
    profile: str,
    topology_id: str,
    content_id: str,
    target_ranks: list[int],
    hot_root: str,
    port: int = DEFAULT_FABRIC_PORT,
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
) -> dict[str, Any]:
    """Build ephemeral NFS/RDMA transfer plan from catalog home to clients."""
    home_rank = int(home["rank"])
    export_path = str(pathlib.Path(hub_path))
    mount_base = transfer_mount_path(hot_root, profile, topology_id, content_id)
    clients: list[dict[str, Any]] = []
    for rank in target_ranks:
        if rank == home_rank:
            continue
        server, client, network = selected_rail_between(
            topology, home_rank, rank, rail_index
        )
        clients.append(
            {
                "rank": rank,
                "server_ip": server["ip"],
                "server_hca": server.get("hca") or "",
                "server_netdev": server.get("netdev") or "",
                "client_ip": client["ip"],
                "client_hca": client.get("hca") or "",
                "client_netdev": client.get("netdev") or "",
                "network": network,
                "mount_path": str(mount_base / f"rank-{rank}"),
                "mount_options": TRANSFER_MOUNT_OPTIONS,
            }
        )
    export_file = f"/etc/exports.d/pulsar-library-activate-{profile}.exports"
    return {
        "kind": "nfs-rdma",
        "port": port,
        "rail_index": rail_index,
        "export_path": export_path,
        "export_file": export_file,
        "export_scope": "model-repository",
        "home_rank": home_rank,
        "home_node_id": home["node_id"],
        "mount_options": TRANSFER_MOUNT_OPTIONS,
        "clients": clients,
        # Per-rank source after transfer plane is up
        "sources": {
            str(home_rank): export_path,
            **{str(c["rank"]): c["mount_path"] for c in clients},
        },
    }


def plan_activate(
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
    topology_file: str | None = None,
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
    fabric_port: int = DEFAULT_FABRIC_PORT,
    home_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return activate plan JSON for bash to execute (copy/fabric + stamp)."""
    backend, transport = resolve_activate_transport(backend, transport)
    catalog = load_catalog(catalog_path)
    if catalog.get("topology_id") and topology_id and catalog["topology_id"] != topology_id:
        fail(
            f"activate: catalog topology_id mismatch "
            f"(catalog={catalog['topology_id'][:12]}… live={topology_id[:12]}…); "
            "run catalog refresh"
        )
    # Activate is warm-catalog only; cold uses adopt or stage-only.
    profile_data = load_hf_profile(models_dir, profile)
    resolved = resolve_entry(
        catalog,
        profile=profile,
        cold_root=None,
        models_dir=models_dir,
    )
    if resolved.get("tier") == "cold":
        fail(
            "activate: cold source requires "
            "`cold adopt` (durable warm home) or `cold stage-only` (hot only)"
        )
    if resolved.get("model_id") != profile_data.get("model_id"):
        fail("activate: catalog model differs from the live profile")
    home = resolved["home"]
    hub_path = home["hub_path"]
    digest, bytes_logical, integrity_manifest = activation_home_inventory(
        home,
        resolved.get("revision"),
        home_inventory,
        model_id=resolved["model_id"],
    )
    validation = require_activation_identity(
        profile_data,
        integrity_manifest,
        allow_unvalidated=allow_unvalidated,
    )
    cid = hot_content_id(resolved["identity_key"], digest, validation)
    instance = hot_instance_dir(hot_root, profile, topology_id, cid)
    target_ranks = list(range(nodes if nodes is not None else 1))
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
                "stamp": existing,
                "transfer": None,
            }

    ensure_budget_for_add(
        hot_root,
        bytes_logical,
        replacing_path=instance if instance.is_dir() else None,
    )
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
    if backend == "fabric":
        # Single-rank / home-only: no NFS needed — local read on home.
        needs_transfer = any(r != int(home["rank"]) for r in target_ranks)
        if needs_transfer:
            topology = load_topology_for_plan(topology_file)
            if topology.get("topology_id") and topology["topology_id"] != topology_id:
                fail(
                    "fabric activate: live topology_id does not match plan topology_id"
                )
            transfer = build_fabric_transfer(
                topology=topology,
                home=home,
                hub_path=hub_path,
                profile=profile,
                topology_id=topology_id,
                content_id=cid,
                target_ranks=target_ranks,
                hot_root=hot_root,
                port=fabric_port,
                rail_index=rail_index,
            )
            action = "fabric-copy"
            # hub_source for home remains durable path; clients use mount after apply
            hub_source = hub_path
        else:
            # Degenerate fabric on single node: local copy, still stamp backend=fabric
            action = "copy"
            transfer = {
                "kind": "local-only",
                "port": fabric_port,
                "rail_index": rail_index,
                "export_path": hub_path,
                "clients": [],
                "home_rank": int(home["rank"]),
                "home_node_id": home["node_id"],
                "sources": {str(home["rank"]): hub_path},
                "note": "single-rank fabric activate uses local home path (no NFS)",
            }

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
            "reactivate from the current expected identity"
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


def catalog_entry_has_expected_identity(entry: dict[str, Any]) -> bool:
    """Return whether a catalog entry carries a reviewed lab expectation."""
    return any(
        item.get("expected_model_seal") is not None
        and item.get("identity_status") in {"expected-unverified", "match"}
        for item in entry.get("profile_validation") or []
    )


def render_catalog_human(catalog: dict[str, Any], *, validated_only: bool = False) -> None:
    models = catalog.get("models") or []
    if validated_only:
        models = [m for m in models if catalog_entry_has_expected_identity(m)]
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
        if args.validated:
            models = [m for m in models if catalog_entry_has_expected_identity(m)]
        print(
            json.dumps(
                {"schema_version": SCHEMA_VERSION, "models": models},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        render_catalog_human(catalog, validated_only=args.validated)
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
    absolute_path = None
    if args.query:
        if args.query.startswith("/"):
            absolute_path = args.query
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
        allow_unvalidated=args.allow_unvalidated,
        nodes=args.nodes,
    )
    if args.execute and plan.get("action") == "stage-only":
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
        print(f"  → {rec['action']}\n")
    return 0


def cmd_inspect_hub(args: argparse.Namespace) -> int:
    result = inspect_hub_inventory(
        args.hub_path,
        rank=args.rank,
        node_id=args.node_id,
        model_id=args.model_id or None,
        revision=args.revision or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0



def cmd_plan_activate(args: argparse.Namespace) -> int:
    home_inventory = None
    if args.home_inventory_json:
        try:
            home_inventory = json.loads(args.home_inventory_json)
        except json.JSONDecodeError as exc:
            fail(f"home-inventory-json: {exc}")
        if not isinstance(home_inventory, dict):
            fail("home-inventory-json must be an object")
    plan = plan_activate(
        catalog_path=args.catalog,
        profile=args.profile,
        topology_id=args.topology_id,
        hot_root=args.hot_root or default_hot_root(),
        models_dir=args.models_dir,
        backend=args.backend or None,
        transport=args.transport or None,
        allow_unvalidated=args.allow_unvalidated,
        nodes=args.nodes,
        topology_file=args.topology_file or None,
        rail_index=args.rail_index,
        fabric_port=args.fabric_port,
        home_inventory=home_inventory,
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
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"hot_root   {report['hot_root']}")
        print(f"budget     {report['budget_bytes']} bytes")
        print(f"used       {report['used_bytes']} bytes")
        print(f"remaining  {report['remaining_bytes']} bytes")
        print(f"instances  {len(report['instances'])}")
        for item in report["instances"]:
            pin = " pinned" if item.get("pinned") else ""
            print(f"  - {item['profile']} {item['bytes']}B{pin}")
            print(f"    {item['path']}")
    return 0


def cmd_inventory_digest(args: argparse.Namespace) -> int:
    print(inventory_digest(args.hub_path))
    return 0


def cmd_partition_blobs(args: argparse.Namespace) -> int:
    report = partition_blob_files(args.hub_path, streams=args.streams)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def compare_activate_bench(
    *,
    profile: str,
    topology_id: str,
    model_id: str,
    bytes_logical: int,
    copy_seconds: float,
    fabric_seconds: float,
    tag: str,
    nodes: int,
    copy_phases: dict[str, Any] | None = None,
    fabric_phases: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Compare copy vs fabric wall times. Fabric wins only if strictly faster."""
    if copy_seconds < 0 or fabric_seconds < 0:
        fail("bench times must be non-negative")
    if copy_seconds == 0:
        # Avoid div-by-zero; treat as inconclusive rather than fabric win
        ratio = None
        verdict = "inconclusive"
        reason = "copy_seconds is zero; cannot compute speedup"
    else:
        ratio = fabric_seconds / copy_seconds
        if fabric_seconds < copy_seconds:
            verdict = "fabric_faster"
            reason = "fabric wall time is strictly less than copy"
        elif fabric_seconds == copy_seconds:
            verdict = "tie"
            reason = "fabric and copy wall times are equal"
        else:
            verdict = "copy_faster"
            reason = "fabric wall time is not less than copy"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "model-library-activate-bench",
        "tag": tag,
        "profile": profile,
        "model_id": model_id,
        "topology_id": topology_id,
        "nodes": nodes,
        "bytes_logical": bytes_logical,
        "copy_seconds": copy_seconds,
        "fabric_seconds": fabric_seconds,
        "fabric_over_copy_ratio": ratio,
        "verdict": verdict,
        "reason": reason,
        "fabric_claims_fast_path": verdict == "fabric_faster",
        "recorded_at": utc_now(),
    }
    if copy_phases:
        report["copy_phases"] = copy_phases
    if fabric_phases:
        report["fabric_phases"] = fabric_phases
    if notes:
        report["notes"] = notes
    return report


def build_ssh_roce_map(
    topology: dict[str, Any],
    *,
    home_rank: int,
    target_ranks: list[int],
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
) -> dict[str, Any]:
    """Map ranks → control SSH host and RoCE IP for experimental SSH-over-RoCE copy.

    Uses the same selected_rail() as fabric NFS. Does not change product activate
    defaults — experiment-only addressing for rsync -e ssh over fabric IPs.
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


def cmd_compare_bench(args: argparse.Namespace) -> int:
    copy_phases = None
    fabric_phases = None
    if getattr(args, "copy_phases_json", None):
        try:
            copy_phases = json.loads(args.copy_phases_json)
        except json.JSONDecodeError as exc:
            fail(f"copy-phases-json: {exc}")
    if getattr(args, "fabric_phases_json", None):
        try:
            fabric_phases = json.loads(args.fabric_phases_json)
        except json.JSONDecodeError as exc:
            fail(f"fabric-phases-json: {exc}")
    report = compare_activate_bench(
        profile=args.profile,
        topology_id=args.topology_id,
        model_id=args.model_id,
        bytes_logical=args.bytes_logical,
        copy_seconds=args.copy_seconds,
        fabric_seconds=args.fabric_seconds,
        tag=args.tag,
        nodes=args.nodes,
        copy_phases=copy_phases if isinstance(copy_phases, dict) else None,
        fabric_phases=fabric_phases if isinstance(fabric_phases, dict) else None,
        notes=getattr(args, "notes", None) or None,
    )
    if args.output:
        atomic_write_json(args.output, report, mode=0o644)
    if args.json or args.output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"profile   {report['profile']}")
        print(f"copy_s    {report['copy_seconds']}")
        print(f"fabric_s  {report['fabric_seconds']}")
        print(f"verdict   {report['verdict']}")
        print(f"fast_path {report['fabric_claims_fast_path']}")
        if args.output:
            print(f"wrote     {args.output}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pulsar federated model library")
    sub = parser.add_subparsers(dest="command", required=True)

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
    inspect.set_defaults(func=cmd_inspect_hub)

    build = sub.add_parser("build", help="Build catalog from homes JSON + profiles")
    build.add_argument("--topology-id", required=True)
    build.add_argument("--models-dir", required=True)
    build.add_argument("--homes-json", help="JSON array of scanned homes")
    build.add_argument("--output", help="Write catalog.json here")
    build.add_argument("--primary", action="append", default=[], help="identity_or_model=node_id")
    build.add_argument("--json", action="store_true")
    build.set_defaults(func=cmd_build)

    list_p = sub.add_parser("list", help="List catalog entries")
    list_p.add_argument("--catalog", required=True)
    list_p.add_argument("--validated", action="store_true")
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
    stage.add_argument("--allow-unvalidated", action="store_true")
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

    plan = sub.add_parser(
        "plan-activate", help="Plan copy/fabric activate into hot staging"
    )
    plan.add_argument("--catalog", required=True)
    plan.add_argument("--profile", required=True)
    plan.add_argument("--topology-id", required=True)
    plan.add_argument("--hot-root", default="")
    plan.add_argument("--models-dir", required=True)
    plan.add_argument("--backend", default="", choices=("copy", "fabric"))
    plan.add_argument(
        "--transport",
        default="",
        choices=tuple(ACTIVATE_TRANSPORT_BACKENDS),
        help="Transfer path: ssh-control, ssh-roce, or nfs-rdma",
    )
    plan.add_argument("--nodes", type=int, default=1)
    plan.add_argument("--allow-unvalidated", action="store_true")
    plan.add_argument(
        "--topology-file",
        default="",
        help="Confirmed topology JSON (required for multi-rank fabric)",
    )
    plan.add_argument("--rail-index", type=int, default=DEFAULT_FABRIC_RAIL_INDEX)
    plan.add_argument("--fabric-port", type=int, default=DEFAULT_FABRIC_PORT)
    plan.add_argument(
        "--home-inventory-json",
        default="",
        help=argparse.SUPPRESS,
    )
    plan.set_defaults(func=cmd_plan_activate)

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
    bud.add_argument("--json", action="store_true")
    bud.set_defaults(func=cmd_budget)

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

    bench = sub.add_parser(
        "compare-bench",
        help="Compare copy vs fabric activate wall times (B gate)",
    )
    bench.add_argument("--profile", required=True)
    bench.add_argument("--topology-id", required=True)
    bench.add_argument("--model-id", required=True)
    bench.add_argument("--bytes-logical", type=int, required=True)
    bench.add_argument("--copy-seconds", type=float, required=True)
    bench.add_argument("--fabric-seconds", type=float, required=True)
    bench.add_argument("--tag", required=True)
    bench.add_argument("--nodes", type=int, required=True)
    bench.add_argument("--copy-phases-json", default="")
    bench.add_argument("--fabric-phases-json", default="")
    bench.add_argument("--notes", default="")
    bench.add_argument("--output", default="")
    bench.add_argument("--json", action="store_true")
    bench.set_defaults(func=cmd_compare_bench)

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
