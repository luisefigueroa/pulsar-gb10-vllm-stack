#!/usr/bin/env python3
"""Federated model library: warm catalog + hot staging stamps (Phase 0–1 brain).

Bash owns topology/SSH, rsync activate, and operator entrypoints. This module
owns schemas, hub completeness, labels, digests, hot.json, and disk budget.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
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
    total = 0
    try:
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    total += (pathlib.Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def hub_tree_state(hub_root: pathlib.Path) -> str:
    """Return complete | partial | missing for a hub model directory."""
    if not hub_root.is_dir():
        return "missing"
    try:
        for root, _dirs, files in os.walk(hub_root, followlinks=False):
            for name in files:
                if name.endswith(".incomplete"):
                    return "partial"
    except OSError:
        return "partial"

    revision = read_revision(hub_root)
    if revision is None:
        return "partial"
    snapshot = hub_root / "snapshots" / revision
    config = snapshot / "config.json"
    if not config.is_file() or config.stat().st_size <= 0:
        # Nested configs: accept any config.json under snapshot
        found = False
        try:
            for path in snapshot.rglob("config.json"):
                if path.is_file() and path.stat().st_size > 0:
                    found = True
                    config = path
                    break
        except OSError:
            return "partial"
        if not found:
            return "partial"

    weight_dir = config.parent
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


def resolve_entry(catalog: dict[str, Any], *, model_id: str | None = None, profile: str | None = None) -> dict[str, Any]:
    entry = find_model_entry(catalog, model_id=model_id, profile=profile)
    if entry is None:
        target = profile or model_id or "?"
        fail(f"resolve: {target}: not found in warm catalog")
    complete = [h for h in entry.get("homes") or [] if h.get("state") == "complete"]
    if not complete:
        fail(
            f"resolve: {entry['model_id']}: no complete warm home "
            "(download/place weights, then catalog refresh)"
        )
    if entry.get("duplicate") and not entry.get("has_primary"):
        fail(
            f"resolve: {entry['model_id']}: duplicate complete homes without primary; "
            "run: scripts/model-library.sh cleanup-recommend"
        )
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
    resolved = resolve_entry(catalog, profile=profile)
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
    catalog = load_catalog(args.catalog)
    model_id = args.model
    profile = args.profile
    if args.query:
        if "/" in args.query:
            model_id = args.query
        else:
            profile = args.query
    result = resolve_entry(catalog, model_id=model_id, profile=profile)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        home = result["home"]
        print(f"model     {result['model_id']}")
        print(f"revision  {result.get('revision') or '-'}")
        print(f"validation {result.get('validation')}")
        print(f"home rank {home['rank']}  node_id={home['node_id']}")
        print(f"hub_path  {home['hub_path']}")
        if result.get("duplicate"):
            print("note      duplicates present; using selected primary")
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

    resolve = sub.add_parser("resolve", help="Resolve profile/model to primary home")
    resolve.add_argument("--catalog", required=True)
    resolve.add_argument("--profile")
    resolve.add_argument("--model")
    resolve.add_argument("query", nargs="?")
    resolve.add_argument("--json", action="store_true")
    resolve.set_defaults(func=cmd_resolve)

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
