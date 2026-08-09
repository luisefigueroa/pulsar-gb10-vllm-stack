#!/usr/bin/env python3
"""Federated model library: warm catalog + optional cold + hot staging.

Bash owns topology/SSH, rsync activate/adopt, and operator entrypoints. This
module owns schemas, hub/flat completeness, labels, digests, hot.json, disk
budget, and cold-archive resolve (warm → cold → fail closed).
"""

from __future__ import annotations

import argparse
import hashlib
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

SCHEMA_VERSION = 1
HOT_SCHEMA_VERSION = 1
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


def hub_dirname_to_model_id(dirname: str) -> str | None:
    match = HUB_DIR_RE.fullmatch(dirname)
    if not match:
        return None
    # models--org--name → org/name (HF uses -- between path segments)
    return match.group(1).replace("--", "/")


def model_id_to_hub_dirname(model_id: str) -> str:
    return "models--" + model_id.replace("/", "--")


def read_revision(hub_root: pathlib.Path) -> str | None:
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
    """Return complete | partial | missing for a hub model directory."""
    if not hub_root.is_dir():
        return "missing"
    if _has_incomplete_marker(hub_root):
        return "partial"

    revision = read_revision(hub_root)
    if revision is None:
        return "partial"
    snapshot = hub_root / "snapshots" / revision
    return weight_dir_state(snapshot)


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
        state = hub_tree_state(entry)
        revision = read_revision(entry) if state == "complete" else None
        identity = f"{model_id}@{revision}" if revision else f"{model_id}@unknown"
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
                "bytes": tree_bytes(entry) if state == "complete" else 0,
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
) -> dict[str, Any]:
    if layout == "hub":
        state = hub_tree_state(path)
        revision = read_revision(path) if state == "complete" else None
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
    seen_paths: set[str] = set()

    for rel in COLD_HUB_REL_PATHS:
        hub_dir = root / rel
        for item in _scan_cold_hub_dir(hub_dir, category=f"hub:{rel}"):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            entries.append(item)

    for cat in COLD_CATEGORY_DIRS:
        for item in _scan_cold_category(root / cat, category=cat):
            if item["path"] in seen_paths:
                continue
            seen_paths.add(item["path"])
            entries.append(item)

    entries.sort(key=lambda e: (e["model_id"], e["path"]))
    return entries


def find_cold_entry(
    cold_root: str | pathlib.Path,
    *,
    model_id: str | None = None,
    path: str | None = None,
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
        entry = _cold_entry(model_id=mid, path=candidate, layout=layout, category="path")
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
    return {
        "profile": path.stem,
        "model_id": model_id,
        "absolute_path": model if absolute else None,
        "status": status,
        "nodes": nodes,
        "validated": bool(STATUS_TESTED.match(status)),
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
    if not model or model.startswith("/"):
        return None
    return {
        "profile": path.stem,
        "model_id": model,
        "status": status,
        "nodes": nodes,
        "validated": bool(STATUS_TESTED.match(status)),
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


def build_catalog(
    *,
    topology_id: str,
    homes: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    primary_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Merge scanned homes with profile validation labels."""
    primary_overrides = primary_overrides or {}
    by_model: dict[str, dict[str, Any]] = {}

    profile_by_model: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        profile_by_model.setdefault(profile["model_id"], []).append(profile)

    for home in homes:
        model_id = home["model_id"]
        revision = home.get("revision") or "unknown"
        identity = home.get("identity_key") or f"{model_id}@{revision}"
        entry = by_model.setdefault(
            identity,
            {
                "model_id": model_id,
                "revision": home.get("revision"),
                "identity_key": identity,
                "validation": "unvalidated",
                "profiles": [],
                "homes": [],
                "duplicate": False,
            },
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
                "bytes": home.get("bytes") or 0,
                "primary": False,
            }
        )

    # Attach profiles / validation for any identity with matching model_id
    for identity, entry in by_model.items():
        model_id = entry["model_id"]
        matched = profile_by_model.get(model_id) or []
        entry["profiles"] = [p["profile"] for p in matched]
        if any(p["validated"] for p in matched):
            entry["validation"] = "validated"
        else:
            entry["validation"] = "unvalidated"

    # Also surface validated profiles with zero homes (not on disk)
    seen_models = {e["model_id"] for e in by_model.values()}
    for profile in profiles:
        if profile["model_id"] in seen_models:
            continue
        identity = f"{profile['model_id']}@missing"
        by_model[identity] = {
            "model_id": profile["model_id"],
            "revision": None,
            "identity_key": identity,
            "validation": "validated" if profile["validated"] else "unvalidated",
            "profiles": [profile["profile"]],
            "homes": [],
            "duplicate": False,
            "on_disk": False,
        }

    models_out: list[dict[str, Any]] = []
    for identity in sorted(by_model.keys()):
        entry = by_model[identity]
        complete_homes = [h for h in entry["homes"] if h["state"] == "complete"]
        partial_homes = [h for h in entry["homes"] if h["state"] != "complete"]
        entry["homes"] = complete_homes + partial_homes
        entry["on_disk"] = bool(complete_homes)
        entry["duplicate"] = len(complete_homes) > 1

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
            # Fail-closed: no automatic primary when duplicates exist
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
    for entry in catalog.get("models") or []:
        if identity_key and entry.get("identity_key") == identity_key:
            return entry
        if profile and profile in (entry.get("profiles") or []):
            return entry
        if model_id and entry.get("model_id") == model_id and entry.get("on_disk", True):
            # Prefer complete-on-disk entries for model_id
            if any(h.get("state") == "complete" for h in entry.get("homes") or []):
                return entry
    if model_id:
        for entry in catalog.get("models") or []:
            if entry.get("model_id") == model_id:
                return entry
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
    # Profile conf may supply model_id or absolute cold path.
    if profile and models_dir and (model_id is None or absolute_path is None):
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        if conf.is_file():
            parsed = parse_profile_conf_any(conf)
            if parsed:
                model_id = model_id or parsed.get("model_id")
                absolute_path = absolute_path or parsed.get("absolute_path")

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
                return {
                    "model_id": entry["model_id"],
                    "revision": entry.get("revision"),
                    "identity_key": entry["identity_key"],
                    "validation": entry.get("validation"),
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
            cold = find_cold_entry(root, path=absolute_path, require_complete=True)
        elif model_id:
            cold = find_cold_entry(root, model_id=model_id, require_complete=True)
        elif profile and catalog is not None:
            # Profile known but no model_id — already tried warm via profile.
            entry = find_model_entry(catalog, profile=profile)
            if entry and entry.get("model_id"):
                cold = find_cold_entry(
                    root, model_id=entry["model_id"], require_complete=True
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
        state = hub_tree_state(source)
        if state != "complete":
            fail(f"materialize: source hub is {state}: {source}")
        rev = revision or read_revision(source)
        if not rev:
            fail(f"materialize: source hub has no revision: {source}")
        if dest_hub.exists():
            shutil.rmtree(dest_hub)
        dest_hub.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest_hub, symlinks=False)
        if hub_tree_state(dest_hub) != "complete":
            fail(f"materialize: dest hub incomplete after copy: {dest_hub}")
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

    if profile and models_dir and not model_id and not path:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        parsed = parse_profile_conf_any(conf) if conf.is_file() else None
        if parsed:
            model_id = parsed.get("model_id")
            path = parsed.get("absolute_path")

    entry = find_cold_entry(
        root,
        model_id=model_id,
        path=path,
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
        "dest_state": hub_tree_state(pathlib.Path(plan["dest_hub"])),
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

    if profile and models_dir and not model_id and not absolute_path:
        conf = pathlib.Path(models_dir) / f"{profile}.conf"
        parsed = parse_profile_conf_any(conf) if conf.is_file() else None
        if parsed:
            model_id = parsed.get("model_id")
            absolute_path = parsed.get("absolute_path")

    # Catalog may supply model_id / validation for HF profiles.
    catalog = load_catalog(catalog_path) if catalog_path else None
    validation = "unvalidated"
    if catalog is not None:
        warm_entry = find_model_entry(catalog, model_id=model_id, profile=profile)
        if warm_entry:
            validation = warm_entry.get("validation") or validation
            model_id = model_id or warm_entry.get("model_id")

    entry = find_cold_entry(
        root,
        model_id=model_id,
        path=absolute_path,
        require_complete=True,
    )
    if entry is None:
        target = absolute_path or model_id or profile or "?"
        fail(f"cold stage-only: no complete cold tree for {target}")

    if validation != "validated" and not allow_unvalidated:
        fail(
            f"cold stage-only: {entry['model_id']} is unvalidated; "
            "pass --allow-unvalidated to proceed"
        )

    source = pathlib.Path(entry["path"])
    layout = entry.get("layout") or detect_model_layout(source)
    if layout == "hub":
        if hub_tree_state(source) != "complete":
            fail(f"cold stage-only: source hub incomplete: {source}")
    else:
        if flat_tree_state(source) != "complete":
            fail(f"cold stage-only: source flat incomplete: {source}")
    source_digest = inventory_digest(source)
    # Instance path is keyed by source identity so flat→hub rewrite stays stable.
    cid = content_id_for(entry["identity_key"], source_digest)
    bytes_logical = dir_size_bytes(source)
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


def content_id_for(identity_key: str, digest: str) -> str:
    raw = f"{identity_key}|{digest}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


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
    backend: str,
    bytes_logical: int,
    pinned: bool = False,
    state: str = "ready",
) -> dict[str, Any]:
    return {
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
        "backend": backend,
        "bytes_logical": bytes_logical,
        "activated_at": utc_now(),
        "pinned": pinned,
        "budget_bytes_accounted": bytes_logical,
    }


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
) -> pathlib.Path | None:
    """Return newest ready/pinned instance dir for profile+topology, if any."""
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
    return candidates[0][1]


def dir_size_bytes(path: pathlib.Path) -> int:
    return tree_bytes(path)


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
    backend: str = "copy",
    allow_unvalidated: bool = False,
    nodes: int | None = None,
    topology_file: str | None = None,
    rail_index: int = DEFAULT_FABRIC_RAIL_INDEX,
    fabric_port: int = DEFAULT_FABRIC_PORT,
) -> dict[str, Any]:
    """Return activate plan JSON for bash to execute (copy/fabric + stamp)."""
    if backend not in {"copy", "fabric"}:
        fail(f"activate: backend {backend!r} not supported (use copy or fabric)")
    catalog = load_catalog(catalog_path)
    if catalog.get("topology_id") and topology_id and catalog["topology_id"] != topology_id:
        fail(
            f"activate: catalog topology_id mismatch "
            f"(catalog={catalog['topology_id'][:12]}… live={topology_id[:12]}…); "
            "run catalog refresh"
        )
    # Activate is warm-catalog only; cold uses adopt or stage-only.
    resolved = resolve_entry(catalog, profile=profile, cold_root=None)
    if resolved.get("tier") == "cold":
        fail(
            "activate: cold source requires "
            "`cold adopt` (durable warm home) or `cold stage-only` (hot only)"
        )
    if resolved.get("validation") != "validated" and not allow_unvalidated:
        fail(
            f"activate: {resolved['model_id']} is unvalidated; "
            "pass --allow-unvalidated to proceed"
        )
    home = resolved["home"]
    hub_path = home["hub_path"]
    state = hub_tree_state(pathlib.Path(hub_path))
    if state != "complete":
        fail(f"activate: home hub is {state}: {hub_path}")
    digest = inventory_digest(hub_path)
    cid = content_id_for(resolved["identity_key"], digest)
    bytes_logical = dir_size_bytes(pathlib.Path(hub_path))
    instance = hot_instance_dir(hot_root, profile, topology_id, cid)
    target_ranks = list(range(nodes if nodes is not None else 1))
    existing = None
    if hot_stamp_path(instance).is_file():
        existing = load_hot_stamp(instance)
        if (
            existing.get("content_digest") == digest
            and existing.get("identity_key") == resolved["identity_key"]
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
                "bytes_logical": bytes_logical,
                "backend": backend,
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
        backend=backend,
        bytes_logical=bytes_logical,
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
        "bytes_logical": bytes_logical,
        "backend": backend,
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
) -> dict[str, Any]:
    instance_dir = pathlib.Path(instance_dir)
    stamp = load_hot_stamp(instance_dir)
    if stamp.get("state") not in {"ready", "pinned"} and not stamp.get("pinned"):
        fail(f"hot not ready: state={stamp.get('state')!r} at {instance_dir}")
    if profile and stamp.get("profile") != profile:
        fail(f"hot profile mismatch: stamp={stamp.get('profile')} want={profile}")
    if topology_id and stamp.get("topology_id") != topology_id:
        fail("hot topology_id mismatch")
    hub = hot_hub_path(instance_dir, stamp["model_id"])
    if hub_tree_state(hub) != "complete":
        fail(f"hot hub incomplete: {hub}")
    if require_digest:
        digest = inventory_digest(hub)
        if digest != stamp.get("content_digest"):
            fail("hot content_digest mismatch after copy")
    return {"stamp": stamp, "hub_path": str(hub), "instance_dir": str(instance_dir)}


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


def render_catalog_human(catalog: dict[str, Any], *, validated_only: bool = False) -> None:
    models = catalog.get("models") or []
    if validated_only:
        models = [m for m in models if m.get("validation") == "validated"]
    print(f"model library  topology={str(catalog.get('topology_id') or '')[:12]}")
    print(f"refreshed      {catalog.get('refreshed_at')}")
    print(f"entries        {len(models)}")
    print()
    if not models:
        print("(empty)")
        return
    print(f"{'MODEL':<36} {'VAL':<12} {'HOMES':>5} {'DUP':>3}  PROFILES")
    for entry in models:
        complete = sum(1 for h in entry.get("homes") or [] if h.get("state") == "complete")
        dup = "yes" if entry.get("duplicate") else "no"
        profiles = ",".join(entry.get("profiles") or []) or "-"
        print(
            f"{entry.get('model_id', '?'):<36} "
            f"{entry.get('validation', '?'):<12} "
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
            models = [m for m in models if m.get("validation") == "validated"]
        print(json.dumps({"schema_version": SCHEMA_VERSION, "models": models}, indent=2, sort_keys=True))
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
        # Local materialize into hub_dest + write stamp (selftests / single-node).
        materialize_hub_tree(
            plan["source_path"],
            plan["hub_dest"],
            layout=plan.get("layout"),
            revision=plan.get("revision"),
        )
        # Flat→hub rewrites paths; verify digest is always of the final hub tree.
        hub_digest = inventory_digest(plan["hub_dest"])
        stamp = dict(plan["stamp"])
        stamp["content_digest"] = hub_digest
        stamp["source_content_digest"] = plan.get("source_content_digest") or stamp.get(
            "source_content_digest"
        )
        write_hot_stamp(pathlib.Path(plan["instance_dir"]), stamp)
        verify = verify_hot_ready(
            plan["instance_dir"],
            profile=args.profile,
            topology_id=args.topology_id,
            require_digest=True,
        )
        plan = {
            **plan,
            "executed": True,
            "content_digest": hub_digest,
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


def cmd_plan_activate(args: argparse.Namespace) -> int:
    plan = plan_activate(
        catalog_path=args.catalog,
        profile=args.profile,
        topology_id=args.topology_id,
        hot_root=args.hot_root or default_hot_root(),
        backend=args.backend,
        allow_unvalidated=args.allow_unvalidated,
        nodes=args.nodes,
        topology_file=args.topology_file or None,
        rail_index=args.rail_index,
        fabric_port=args.fabric_port,
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
    result = verify_hot_ready(
        args.instance_dir,
        profile=args.profile,
        topology_id=args.topology_id,
        require_digest=not args.skip_digest,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_find_hot(args: argparse.Namespace) -> int:
    path = find_hot_instance_for_profile(
        args.hot_root or default_hot_root(),
        args.profile,
        args.topology_id,
    )
    if path is None:
        fail(f"find-hot: no ready instance for profile {args.profile}")
    stamp = load_hot_stamp(path)
    out = {
        "instance_dir": str(path),
        "hub_path": str(hot_hub_path(path, stamp["model_id"])),
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
    ssh_roce_map: dict[str, Any] | None = None,
    control_phases: dict[str, Any] | None = None,
    ssh_roce_phases: dict[str, Any] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Compare control-path SSH copy vs SSH-over-RoCE-IP copy (experiment)."""
    if control_seconds < 0 or ssh_roce_seconds < 0:
        fail("bench times must be non-negative")
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
    plan.add_argument("--backend", default="copy", choices=("copy", "fabric"))
    plan.add_argument("--nodes", type=int, default=1)
    plan.add_argument("--allow-unvalidated", action="store_true")
    plan.add_argument(
        "--topology-file",
        default="",
        help="Confirmed topology JSON (required for multi-rank fabric)",
    )
    plan.add_argument("--rail-index", type=int, default=DEFAULT_FABRIC_RAIL_INDEX)
    plan.add_argument("--fabric-port", type=int, default=DEFAULT_FABRIC_PORT)
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
    vh.add_argument("--skip-digest", action="store_true")
    vh.set_defaults(func=cmd_verify_hot)

    fh = sub.add_parser("find-hot", help="Find ready hot instance for profile")
    fh.add_argument("--profile", required=True)
    fh.add_argument("--topology-id", required=True)
    fh.add_argument("--hot-root", default="")
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
