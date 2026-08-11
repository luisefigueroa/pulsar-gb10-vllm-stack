#!/usr/bin/env python3
"""Plan and verify Pulsar's experimental single-copy weight fabric."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import resource
import shlex
import socket
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    from scripts.terminal_format import TerminalWriter
except ModuleNotFoundError:
    try:
        from terminal_format import TerminalWriter
    except ModuleNotFoundError:
        # Manifest commands are intentionally self-contained so the script can
        # be streamed over SSH without installing the repository remotely.
        TerminalWriter = None  # type: ignore[assignment,misc]


CONFIG_SCHEMA_VERSION = 2
LEGACY_CONFIG_SCHEMA_VERSION = 1
MODEL_MANIFEST_SCHEMA_VERSION = 1
SAFE_PROFILE = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_REVISION = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_MODEL = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"
    r"/[A-Za-z0-9][A-Za-z0-9._-]*$"
)
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".gguf")
PRIVATE_ARTIFACT_FIELDS = (
    "node_id",
    "owner_node_id",
    "hostname",
    "ssh_host",
    "cache_root",
    "export_path",
    "mount_root",
    "mount_path",
    "server_ip",
    "client_ip",
)
PRIVATE_ARTIFACT_FIELD_PATTERN = re.compile(
    r'"('
    + "|".join(re.escape(field) for field in PRIVATE_ARTIFACT_FIELDS)
    + r')"\s*:'
)


class WeightFabricError(ValueError):
    pass


def fail(message: str) -> None:
    raise WeightFabricError(message)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def load_json(path: str | pathlib.Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path}: expected a JSON object")
    return value


def clean_text(value: Any, field: str) -> str:
    text = str(value or "")
    if not text or any(char in text for char in ("\0", "\t", "\r", "\n")):
        fail(f"{field}: missing or contains control characters")
    return text


def absolute_path(value: Any, field: str) -> str:
    text = clean_text(value, field)
    path = pathlib.PurePosixPath(text)
    if (
        not path.is_absolute()
        or text == "/"
        or ".." in path.parts
        or '"' in text
        or "\\" in text
    ):
        fail(f"{field}: must be a bounded absolute path")
    return str(path)


def profile_name(value: Any) -> str:
    text = clean_text(value, "profile")
    if not SAFE_PROFILE.fullmatch(text):
        fail("profile: use letters, numbers, dot, underscore, or hyphen")
    return text


def model_name(value: Any) -> str:
    text = clean_text(value, "model")
    if len(text) > 255 or not SAFE_MODEL.fullmatch(text):
        fail("model: single-copy cache mode requires a Hugging Face repository ID")
    return text


def bounded_int(
    value: Any, field: str, minimum: int, maximum: int
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        fail(f"{field}: expected an integer")
    if result < minimum or result > maximum:
        fail(f"{field}: expected {minimum}..{maximum}")
    return result


def positive_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        fail(f"{field}: expected a number")
    if not 0 < result <= 1024 * 1024:
        fail(f"{field}: expected a positive value")
    return result


def load_topology(path: str) -> dict[str, Any]:
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


def resolve_owner(
    topology: dict[str, Any], selector: str
) -> dict[str, Any]:
    matches = []
    for node in topology["nodes"]:
        values = {
            str(node["rank"]),
            node["node_id"],
            node["hostname"],
            node["ssh_host"],
            node["control"]["ip"],
        }
        if selector in values:
            matches.append(node)
    if len(matches) != 1:
        fail(f"owner: selector {selector!r} matched {len(matches)} nodes")
    return matches[0]


def link_between(
    topology: dict[str, Any], first: int, second: int
) -> dict[str, Any]:
    wanted = [min(first, second), max(first, second)]
    for link in topology["links"]:
        if link["ranks"] == wanted:
            return link
    fail(f"topology: no fabric link for ranks {first}/{second}")


def selected_rail(
    topology: dict[str, Any],
    owner_rank: int,
    client_rank: int,
    rail_index: int,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    link = link_between(topology, owner_rank, client_rank)
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
            f"topology: ranks {owner_rank}/{client_rank} expose "
            f"{len(rails)} rails, not index {rail_index}"
        )
    rail = rails[rail_index]
    owner_side = "a" if owner_rank < client_rank else "b"
    client_side = "b" if owner_rank < client_rank else "a"
    return rail[owner_side], rail[client_side], rail["network"]


def configuration_identity(config: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in config.items()
        if key != "configuration_id"
    }


def build_configuration(
    topology: dict[str, Any],
    profile: str,
    model: str,
    nodes: int,
    storage_nodes: int | None,
    owner_selector: str,
    cache_root: str,
    mount_root: str,
    port: int,
    rail_index: int,
) -> dict[str, Any]:
    profile = profile_name(profile)
    model = model_name(model)
    node_count = bounded_int(nodes, "nodes", 2, len(topology["nodes"]))
    storage_node_count = bounded_int(
        storage_nodes if storage_nodes is not None else node_count,
        "storage_nodes",
        node_count,
        len(topology["nodes"]),
    )
    owner = resolve_owner(topology, clean_text(owner_selector, "owner"))
    if owner["rank"] >= node_count:
        fail("owner: must be one of the exact profile's serving nodes")
    cache_root = absolute_path(cache_root, "cache_root")
    mount_root = absolute_path(mount_root, "mount_root")
    if (
        pathlib.PurePosixPath(cache_root)
        == pathlib.PurePosixPath(mount_root)
    ):
        fail("cache_root and mount_root must differ")
    port = bounded_int(port, "port", 1, 65535)
    rail_index = bounded_int(rail_index, "rail_index", 0, 15)

    model_relative_path = str(
        pathlib.PurePosixPath("hub")
        / f"models--{model.replace('/', '--')}"
    )
    synthetic_cache_root = str(
        pathlib.PurePosixPath(mount_root)
        / f"{profile}-{topology['topology_id'][:12]}"
    )
    mount_path = str(
        pathlib.PurePosixPath(synthetic_cache_root) / model_relative_path
    )
    export_path = str(pathlib.PurePosixPath(cache_root) / model_relative_path)
    clients = []
    ranks = []
    for rank in range(storage_node_count):
        node = topology["nodes"][rank]
        if rank == owner["rank"]:
            ranks.append(
                {
                    "rank": rank,
                    "node_id": node["node_id"],
                    "hostname": node["hostname"],
                    "ssh_host": node["ssh_host"],
                    "role": "owner",
                    "cache_root": cache_root,
                }
            )
            continue
        server, client, network = selected_rail(
            topology, owner["rank"], rank, rail_index
        )
        client_record = {
            "rank": rank,
            "node_id": node["node_id"],
            "hostname": node["hostname"],
            "ssh_host": node["ssh_host"],
            "server_ip": server["ip"],
            "server_hca": server["hca"],
            "server_netdev": server["netdev"],
            "client_ip": client["ip"],
            "client_hca": client["hca"],
            "client_netdev": client["netdev"],
            "network": network,
            "mount_path": mount_path,
        }
        clients.append(client_record)
        ranks.append(
            {
                "rank": rank,
                "node_id": node["node_id"],
                "hostname": node["hostname"],
                "ssh_host": node["ssh_host"],
                "role": "client",
                "cache_root": synthetic_cache_root,
            }
        )

    config: dict[str, Any] = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "topology_id": topology["topology_id"],
        "profile": profile,
        "model": model,
        "nodes": node_count,
        "storage_nodes": storage_node_count,
        "owner": {
            "topology_rank": owner["rank"],
            "node_id": owner["node_id"],
            "hostname": owner["hostname"],
            "ssh_host": owner["ssh_host"],
            "cache_root": cache_root,
        },
        "transport": {
            "kind": "nfs-rdma",
            "port": port,
            "rail_index": rail_index,
            "export_scope": "model-repository",
            "export_path": export_path,
            "mount_root": mount_root,
            "mount_path": mount_path,
            "mount_options": [
                "ro",
                "vers=4.2",
                "proto=rdma",
                f"port={port}",
                "hard",
                "timeo=600",
                "retrans=2",
            ],
            "clients": clients,
        },
        "integrity": {
            "manifest_relative_path": (
                f"{model_relative_path}/.pulsar/manifests/"
                f"{profile}.manifest.json"
            )
        },
        "ranks": ranks,
        "fallback": "replicated",
        "promotion": "experimental",
    }
    config["configuration_id"] = digest(configuration_identity(config))
    return config


def build_legacy_configuration(
    topology: dict[str, Any],
    profile: str,
    model: str,
    nodes: int,
    storage_nodes: int | None,
    owner_selector: str,
    cache_root: str,
    mount_root: str,
    port: int,
    rail_index: int,
) -> dict[str, Any]:
    """Reconstruct schema 1 exactly, only so its resources can be removed."""
    config = build_configuration(
        topology=topology,
        profile=profile,
        model=model,
        nodes=nodes,
        storage_nodes=storage_nodes,
        owner_selector=owner_selector,
        cache_root=cache_root,
        mount_root=mount_root,
        port=port,
        rail_index=rail_index,
    )
    transport = config["transport"]
    legacy_mount_path = str(
        pathlib.PurePosixPath(transport["mount_root"])
        / f"{config['profile']}-{config['topology_id'][:12]}"
    )
    config["schema_version"] = LEGACY_CONFIG_SCHEMA_VERSION
    transport.pop("export_scope")
    transport["export_path"] = config["owner"]["cache_root"]
    transport["mount_path"] = legacy_mount_path
    for client in transport["clients"]:
        client["mount_path"] = legacy_mount_path
    config["integrity"]["manifest_relative_path"] = (
        f".pulsar/manifests/{config['profile']}.manifest.json"
    )
    config["configuration_id"] = digest(configuration_identity(config))
    return config


def validate_configuration(
    config: dict[str, Any],
    topology: dict[str, Any],
    expected_profile: str | None = None,
    expected_model: str | None = None,
    expected_nodes: int | None = None,
    allow_legacy_teardown: bool = False,
) -> None:
    schema_version = config.get("schema_version")
    if schema_version == LEGACY_CONFIG_SCHEMA_VERSION:
        if not allow_legacy_teardown:
            fail(
                "configuration: schema 1 exported the full cache and is "
                "teardown-only"
            )
    elif schema_version != CONFIG_SCHEMA_VERSION:
        fail("configuration: unsupported schema version")
    profile = profile_name(config.get("profile"))
    model = model_name(config.get("model"))
    nodes = bounded_int(
        config.get("nodes"), "configuration.nodes", 2, len(topology["nodes"])
    )
    storage_nodes = bounded_int(
        config.get("storage_nodes", nodes),
        "configuration.storage_nodes",
        nodes,
        len(topology["nodes"]),
    )
    if expected_profile is not None and profile != expected_profile:
        fail(
            f"configuration: profile is {profile}, expected {expected_profile}"
        )
    if expected_model is not None and model != expected_model:
        fail(f"configuration: model is {model}, expected {expected_model}")
    if expected_nodes is not None and nodes != expected_nodes:
        fail(f"configuration: nodes is {nodes}, expected {expected_nodes}")
    if config.get("topology_id") != topology["topology_id"]:
        fail("configuration: topology identity changed; reconfigure")

    owner = config.get("owner") or {}
    transport = config.get("transport") or {}
    builder = (
        build_legacy_configuration
        if schema_version == LEGACY_CONFIG_SCHEMA_VERSION
        else build_configuration
    )
    expected = builder(
        topology=topology,
        profile=profile,
        model=model,
        nodes=nodes,
        storage_nodes=storage_nodes,
        owner_selector=clean_text(owner.get("node_id"), "owner.node_id"),
        cache_root=absolute_path(owner.get("cache_root"), "owner.cache_root"),
        mount_root=absolute_path(
            transport.get("mount_root"), "transport.mount_root"
        ),
        port=bounded_int(transport.get("port"), "transport.port", 1, 65535),
        rail_index=bounded_int(
            transport.get("rail_index"),
            "transport.rail_index",
            0,
            15,
        ),
    )
    if config != expected:
        fail("configuration: content does not match the confirmed topology")
    if config.get("configuration_id") != digest(
        configuration_identity(config)
    ):
        fail("configuration: identity digest mismatch")


def atomic_write_json(
    value: dict[str, Any], destination: str, mode: int
) -> None:
    path = pathlib.Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def render_configuration(config: dict[str, Any]) -> None:
    if TerminalWriter is None:
        fail("rendering requires scripts/terminal_format.py")
    term = TerminalWriter()
    term.emit("SINGLE-COPY WEIGHT FABRIC")
    term.field("Profile", config["profile"])
    term.field("State", "experimental · explicit opt-in only")
    term.field(
        "Owner",
        f"{config['owner']['hostname']} · "
        f"{config['owner']['node_id'][:12]}",
    )
    term.field("Model", config["model"])
    term.field(
        "Scope",
        f"{config['nodes']} serving · "
        f"{config['storage_nodes']} storage-visible",
    )
    term.field(
        "Transport",
        f"NFSv4.2 over RDMA · port {config['transport']['port']}",
    )
    term.field("Topology", config["topology_id"][:12])
    term.field("Config", config["configuration_id"][:12])
    term.blank()
    term.emit("STORAGE-VISIBLE NODES")
    for rank in config["ranks"]:
        if rank["role"] == "owner":
            detail = f"{rank['hostname']} · authoritative local cache"
        else:
            client = next(
                item
                for item in config["transport"]["clients"]
                if item["rank"] == rank["rank"]
            )
            detail = (
                f"{rank['hostname']} · {client['server_ip']} via "
                f"{client['client_netdev']}"
            )
        term.field("Node", detail)
    term.blank()
    term.emit("SAFETY")
    term.field(
        "Export",
        "read-only · exact model repository · exact client rail addresses",
    )
    term.field("Fallback", "replicated caches require explicit selection")
    term.field("Launch", "fails if config, route, mount, or manifest differs")


def rows(config: dict[str, Any]) -> None:
    owner = config["owner"]
    transport = config["transport"]
    integrity = config["integrity"]
    print(
        "\t".join(
            [
                "META",
                config["configuration_id"],
                config["topology_id"],
                config["profile"],
                config["model"],
                str(config["nodes"]),
                str(owner["topology_rank"]),
                owner["node_id"],
                owner["hostname"],
                owner["ssh_host"],
                owner["cache_root"],
                transport["kind"],
                str(transport["port"]),
                transport["export_path"],
                transport["mount_path"],
                integrity["manifest_relative_path"],
                str(config["storage_nodes"]),
            ]
        )
    )
    clients = {
        item["rank"]: item for item in transport.get("clients") or []
    }
    for rank in config["ranks"]:
        fields = [
            "RANK",
            str(rank["rank"]),
            rank["node_id"],
            rank["hostname"],
            rank["ssh_host"],
            rank["role"],
            rank["cache_root"],
        ]
        client = clients.get(rank["rank"])
        if client:
            fields.extend(
                [
                    client["server_ip"],
                    client["client_ip"],
                    client["client_netdev"],
                    client["client_hca"],
                    client["network"],
                    client["server_netdev"],
                    client["server_hca"],
                ]
            )
        else:
            fields.extend(["", "", "", "", "", "", ""])
        print("\t".join(fields))


def public_provenance(
    config: dict[str, Any], topology: dict[str, Any]
) -> dict[str, Any]:
    nodes = {item["rank"]: item for item in topology["nodes"]}
    clients = []
    for item in config["transport"]["clients"]:
        clients.append(
            {
                "rank": item["rank"],
                "node_fingerprint": fingerprint(
                    nodes[item["rank"]]["node_id"]
                ),
                "server_netdev": item["server_netdev"],
                "server_hca": item["server_hca"],
                "client_netdev": item["client_netdev"],
                "client_hca": item["client_hca"],
                "rail_fingerprint": digest(
                    {
                        "network": item["network"],
                        "server_ip": item["server_ip"],
                        "client_ip": item["client_ip"],
                    }
                )[:16],
            }
        )
    return {
        "schema_version": 1,
        "kind": "weight-fabric-provenance",
        "topology": {
            "topology_id": topology["topology_id"],
            "node_count": len(topology["nodes"]),
            "class": topology["validation"]["class"],
            "connectivity_verified": topology["validation"][
                "connectivity_verified"
            ],
            "min_rails_per_pair": topology["validation"][
                "min_rails_per_pair"
            ],
            "nodes": [
                {
                    "rank": item["rank"],
                    "node_fingerprint": fingerprint(item["node_id"]),
                }
                for item in topology["nodes"]
            ],
            "links": [
                {
                    "ranks": item["ranks"],
                    "rail_count": len(item["rails"]),
                }
                for item in topology["links"]
            ],
        },
        "configuration": {
            "configuration_id": config["configuration_id"],
            "profile": config["profile"],
            "model": config["model"],
            "serving_nodes": config["nodes"],
            "storage_nodes": config["storage_nodes"],
            "owner": {
                "rank": config["owner"]["topology_rank"],
                "node_fingerprint": fingerprint(config["owner"]["node_id"]),
            },
            "transport": {
                "kind": config["transport"]["kind"],
                "export_scope": config["transport"]["export_scope"],
                "port": config["transport"]["port"],
                "rail_index": config["transport"]["rail_index"],
                "mount_options": config["transport"]["mount_options"],
                "clients": clients,
            },
            "fallback": config["fallback"],
            "promotion": config["promotion"],
        },
    }


def private_artifact_values(
    config: dict[str, Any], topology: dict[str, Any]
) -> dict[str, str]:
    """Return exact site values that must never enter a result bundle."""
    values: dict[str, str] = {}

    def remember(category: str, value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        if category in ("hostname", "SSH target") and value in {
            "local",
            "localhost",
        }:
            return
        values.setdefault(value, category)

    owner = config["owner"]
    for field, category in (
        ("node_id", "node identity"),
        ("hostname", "hostname"),
        ("ssh_host", "SSH target"),
        ("cache_root", "cache path"),
    ):
        remember(category, owner.get(field))
    transport = config["transport"]
    for field in ("export_path", "mount_root", "mount_path"):
        remember("storage path", transport.get(field))
    for rank in config["ranks"]:
        remember("node identity", rank.get("node_id"))
        remember("hostname", rank.get("hostname"))
        remember("SSH target", rank.get("ssh_host"))
        remember("cache path", rank.get("cache_root"))
    for client in transport.get("clients") or []:
        remember("node identity", client.get("node_id"))
        remember("hostname", client.get("hostname"))
        remember("SSH target", client.get("ssh_host"))
        remember("RoCE address", client.get("server_ip"))
        remember("RoCE address", client.get("client_ip"))
        remember("RoCE network", client.get("network"))
        remember("storage path", client.get("mount_path"))

    for node in topology["nodes"]:
        remember("node identity", node.get("node_id"))
        remember("hostname", node.get("hostname"))
        remember("SSH target", node.get("ssh_host"))
        remember("control-LAN address", (node.get("control") or {}).get("ip"))
        for adapter in node.get("rdma") or []:
            for cidr in adapter.get("cidrs") or []:
                remember("RoCE address", cidr)
                remember("RoCE address", str(cidr).partition("/")[0])
    for link in topology["links"]:
        remember("RoCE network", link.get("network"))
        for rail in link.get("rails") or []:
            remember("RoCE network", rail.get("network"))
            for side in ("a", "b"):
                remember("RoCE address", (rail.get(side) or {}).get("ip"))
    return values


def is_within(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def audit_public_artifact_directory(
    directory: str,
    config: dict[str, Any],
    topology: dict[str, Any],
    *,
    private_inputs: Iterable[str],
    output: str,
) -> dict[str, Any]:
    root = pathlib.Path(directory)
    if root.is_symlink() or not root.is_dir():
        fail("artifact audit: directory must be a real directory")
    root = root.resolve()

    for private_input in private_inputs:
        candidate = pathlib.Path(private_input).resolve()
        if is_within(candidate, root):
            fail("artifact audit: private inputs must be outside the bundle")

    output_path = pathlib.Path(output)
    output_resolved = output_path.parent.resolve() / output_path.name
    if not is_within(output_resolved, root):
        fail("artifact audit: output must be inside the bundle")
    if output_path.exists():
        fail("artifact audit: output already exists")

    private_values = private_artifact_values(config, topology)
    files_scanned = 0
    bytes_scanned = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            fail("artifact audit: bundle contains a symbolic link")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".private-") for part in relative.parts):
            fail("artifact audit: bundle contains a private staging file")
        try:
            raw = path.read_bytes()
            document = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            fail("artifact audit: bundle contains a non-UTF-8 file")
        files_scanned += 1
        bytes_scanned += len(raw)
        searchable = f"{relative}\n{document}"
        for private_value, category in sorted(
            private_values.items(), key=lambda item: len(item[0]), reverse=True
        ):
            if private_value in searchable:
                fail(
                    "artifact audit: bundle contains a private "
                    f"{category} value"
                )
        match = PRIVATE_ARTIFACT_FIELD_PATTERN.search(document)
        if match:
            fail(
                "artifact audit: bundle contains private JSON field "
                f"{match.group(1)!r}"
            )
    if files_scanned == 0:
        fail("artifact audit: bundle is empty")
    return {
        "schema_version": 1,
        "kind": "weight-fabric-artifact-audit",
        "state": "pass",
        "topology_id": topology["topology_id"],
        "configuration_id": config["configuration_id"],
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "checks": {
            "regular_utf8_files_only": True,
            "no_private_staging_files": True,
            "no_private_json_fields": True,
            "no_private_site_values": True,
        },
    }


def hf_model_root(cache_root: str, model: str) -> pathlib.Path:
    return pathlib.Path(cache_root) / "hub" / f"models--{model.replace('/', '--')}"


def identity_has_access(
    item: os.stat_result, uid: int, gid: int, required: int
) -> bool:
    mode = stat.S_IMODE(item.st_mode)
    if item.st_uid == uid:
        granted = (mode >> 6) & 0b111
    elif item.st_gid == gid:
        granted = (mode >> 3) & 0b111
    else:
        granted = mode & 0b111
    return granted & required == required


def validate_repository_access(repository_path: str) -> dict[str, Any]:
    """Prove the export's mapped non-root identity can read the repository."""
    repository = pathlib.Path(
        absolute_path(repository_path, "repository_path")
    )
    try:
        root_lstat = repository.lstat()
        resolved_root = repository.resolve(strict=True)
    except OSError as exc:
        fail(f"repository access: cannot inspect {repository}: {exc}")
    if stat.S_ISLNK(root_lstat.st_mode) or not stat.S_ISDIR(
        root_lstat.st_mode
    ):
        fail("repository access: export path must be a real directory")
    uid = root_lstat.st_uid
    gid = root_lstat.st_gid
    if uid == 0:
        fail("repository access: refusing a root-owned repository")

    checked_directories = 0
    checked_files = 0
    checked_symlinks = 0

    def walk_error(exc: OSError) -> None:
        fail(f"repository access: cannot walk repository: {exc}")

    for current, directories, files in os.walk(
        repository, topdown=True, followlinks=False, onerror=walk_error
    ):
        current_path = pathlib.Path(current)
        try:
            current_stat = current_path.stat()
        except OSError as exc:
            fail(f"repository access: cannot inspect {current_path}: {exc}")
        if not identity_has_access(current_stat, uid, gid, 0b101):
            fail(
                "repository access: mapped identity cannot list/traverse "
                f"{current_path}"
            )
        checked_directories += 1

        for name in tuple(directories) + tuple(files):
            path = current_path / name
            try:
                item = path.lstat()
            except OSError as exc:
                fail(f"repository access: cannot inspect {path}: {exc}")
            if stat.S_ISLNK(item.st_mode):
                if name in directories:
                    directories.remove(name)
                try:
                    target = path.resolve(strict=True)
                    target.relative_to(resolved_root)
                    target_stat = target.stat()
                except ValueError:
                    fail(f"repository access: link escapes repository: {path}")
                except OSError as exc:
                    fail(f"repository access: cannot resolve {path}: {exc}")
                if stat.S_ISDIR(target_stat.st_mode):
                    required = 0b101
                elif stat.S_ISREG(target_stat.st_mode):
                    required = 0b100
                else:
                    fail(
                        "repository access: link target is not a regular "
                        f"file or directory: {path}"
                    )
                if not identity_has_access(target_stat, uid, gid, required):
                    fail(
                        "repository access: mapped identity cannot access "
                        f"link target {path}"
                    )
                checked_symlinks += 1
            elif stat.S_ISDIR(item.st_mode):
                if not identity_has_access(item, uid, gid, 0b101):
                    fail(
                        "repository access: mapped identity cannot "
                        f"list/traverse {path}"
                    )
            elif stat.S_ISREG(item.st_mode):
                if not identity_has_access(item, uid, gid, 0b100):
                    fail(
                        "repository access: mapped identity cannot read "
                        f"{path}"
                    )
                checked_files += 1
            else:
                fail(f"repository access: unsupported special file: {path}")

    return {
        "state": "ok",
        "repository_path": str(repository),
        "uid": uid,
        "gid": gid,
        "directories_checked": checked_directories,
        "files_checked": checked_files,
        "symlinks_checked": checked_symlinks,
    }


def parse_active_exports(document: str) -> dict[str, dict[str, set[str]]]:
    exports: dict[str, dict[str, set[str]]] = {}
    for line_number, raw_line in enumerate(document.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError as exc:
            fail(f"export scope: malformed active-export line {line_number}: {exc}")
        if len(fields) < 2:
            fail(f"export scope: incomplete active-export line {line_number}")
        export_path = absolute_path(
            fields[0], f"export scope line {line_number} path"
        )
        entries = exports.setdefault(export_path, {})
        for field in fields[1:]:
            match = re.fullmatch(r"([^()\s]+)\(([^()]*)\)", field)
            if match is None:
                fail(
                    f"export scope: malformed client on line {line_number}"
                )
            client, option_text = match.groups()
            if client in entries:
                fail(
                    f"export scope: duplicate {client} entry for {export_path}"
                )
            entries[client] = {
                option for option in option_text.split(",") if option
            }
    return exports


def path_is_strict_parent(parent: str, child: str) -> bool:
    parent_path = pathlib.PurePosixPath(parent)
    child_path = pathlib.PurePosixPath(child)
    if parent_path == child_path:
        return False
    try:
        child_path.relative_to(parent_path)
    except ValueError:
        return False
    return True


def validate_export_scope(
    active_document: str,
    pulsar_export_files: Iterable[str],
    *,
    expected_export_file: str,
    export_path: str,
    clients: Iterable[str],
    anonuid: int,
    anongid: int,
    require_active: bool,
    forbid_active: bool = False,
    require_export_file: bool = False,
    forbid_export_file: bool = False,
) -> dict[str, Any]:
    if require_active and forbid_active:
        fail("export scope: active export cannot be required and forbidden")
    if require_export_file and forbid_export_file:
        fail("export scope: export file cannot be required and forbidden")
    expected_export_file = absolute_path(
        expected_export_file, "expected_export_file"
    )
    export_path = absolute_path(export_path, "export_path")
    expected_clients = {clean_text(item, "client") for item in clients}
    if not expected_clients:
        fail("export scope: at least one exact client is required")
    listed_files = {
        absolute_path(item, "pulsar_export_file")
        for item in pulsar_export_files
        if item
    }
    other_files = sorted(
        item for item in listed_files if item != expected_export_file
    )
    if other_files:
        fail(f"export scope: another Pulsar export file exists: {other_files[0]}")
    if require_export_file and expected_export_file not in listed_files:
        fail("export scope: configuration export file is missing")
    if forbid_export_file and expected_export_file in listed_files:
        fail("export scope: configuration export file still exists")

    active = parse_active_exports(active_document)
    broader = sorted(
        path for path in active if path_is_strict_parent(path, export_path)
    )
    if broader:
        fail(f"export scope: broader active export exists: {broader[0]}")
    exact = active.get(export_path)
    if exact is None:
        if require_active:
            fail("export scope: exact repository export is not active")
        if expected_export_file in listed_files:
            fail(
                "export scope: configuration export file exists but is inactive"
            )
        return {"state": "ok", "active": False}
    if forbid_active:
        fail("export scope: exact repository export is still active")
    if expected_export_file not in listed_files:
        fail(
            "export scope: exact active export is not owned by this configuration"
        )
    if set(exact) != expected_clients:
        fail("export scope: active export clients differ from configuration")

    required_options = {
        "ro",
        "sync",
        "insecure",
        "root_squash",
        "no_subtree_check",
        f"anonuid={anonuid}",
        f"anongid={anongid}",
    }
    forbidden_options = {"rw", "secure", "no_root_squash"}
    for client, options in exact.items():
        missing = sorted(required_options - options)
        forbidden = sorted(forbidden_options & options)
        if missing or forbidden:
            fail(
                f"export scope: active policy differs for {client} "
                f"(missing={missing}, forbidden={forbidden})"
            )
    return {"state": "ok", "active": True}


def resolve_snapshot(
    cache_root: str, model: str
) -> tuple[pathlib.Path, pathlib.Path, str]:
    root = hf_model_root(cache_root, model)
    ref_path = root / "refs" / "main"
    try:
        revision = ref_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        fail(f"weights: cannot read {ref_path}: {exc}")
    if not SAFE_REVISION.fullmatch(revision):
        fail("weights: refs/main is empty or unsafe")
    snapshot = root / "snapshots" / revision
    if not snapshot.is_dir():
        fail(f"weights: snapshot {revision} is missing")
    return root, snapshot, revision


def resolved_inside(path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        fail(f"weights: cannot resolve {path}: {exc}")
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError:
        fail(f"weights: {path} resolves outside the model cache")
    if not resolved.is_file():
        fail(f"weights: {path} is not a regular file")
    return resolved


def iter_snapshot_files(
    model_root: pathlib.Path, snapshot: pathlib.Path
) -> Iterable[tuple[str, pathlib.Path]]:
    for path in sorted(snapshot.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(snapshot).as_posix()
        resolved = resolved_inside(path, model_root)
        yield relative, resolved


def sha256_file(path: pathlib.Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_weight_index(
    snapshot: pathlib.Path, logical_paths: set[str]
) -> None:
    for index_path in snapshot.rglob("*.index.json"):
        try:
            body = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"weights: invalid index {index_path}: {exc}")
        weight_map = body.get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            continue
        parent = index_path.parent.relative_to(snapshot)
        referenced = {
            (parent / str(value)).as_posix()
            for value in weight_map.values()
        }
        missing = sorted(referenced - logical_paths)
        if missing:
            fail(
                f"weights: index {index_path.name} references missing "
                f"{missing[0]}"
            )


def manifest_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in manifest.items() if key != "manifest_id"
    }


def build_model_manifest(
    cache_root: str, model: str, profile: str
) -> dict[str, Any]:
    cache_root = absolute_path(cache_root, "cache_root")
    model = model_name(model)
    profile = profile_name(profile)
    model_root, snapshot, revision = resolve_snapshot(cache_root, model)
    incomplete = next(model_root.rglob("*.incomplete"), None)
    if incomplete is not None:
        fail(f"weights: interrupted download marker present: {incomplete}")

    files = []
    logical_paths = set()
    for relative, resolved in iter_snapshot_files(model_root, snapshot):
        size = resolved.stat().st_size
        if size <= 0:
            fail(f"weights: empty file {relative}")
        logical_paths.add(relative)
        files.append(
            {
                "path": relative,
                "size": size,
                "sha256": sha256_file(resolved),
            }
        )
    if "config.json" not in logical_paths:
        fail("weights: snapshot root config.json is missing")
    if not any(path.endswith(WEIGHT_SUFFIXES) for path in logical_paths):
        fail("weights: snapshot has no recognized model weights")
    validate_weight_index(snapshot, logical_paths)

    manifest: dict[str, Any] = {
        "schema_version": MODEL_MANIFEST_SCHEMA_VERSION,
        "profile": profile,
        "model": model,
        "snapshot_revision": revision,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
    }
    manifest["manifest_id"] = digest(manifest_identity(manifest))
    return manifest


def load_model_manifest(path: str) -> dict[str, Any]:
    manifest = load_json(path)
    if manifest.get("schema_version") != MODEL_MANIFEST_SCHEMA_VERSION:
        fail("manifest: unsupported schema version")
    profile_name(manifest.get("profile"))
    model_name(manifest.get("model"))
    revision = clean_text(
        manifest.get("snapshot_revision"), "manifest.snapshot_revision"
    )
    if not SAFE_REVISION.fullmatch(revision):
        fail("manifest: unsafe snapshot revision")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        fail("manifest: files must be a non-empty list")
    observed = set()
    total = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            fail(f"manifest.files[{index}]: expected object")
        relative = clean_text(item.get("path"), f"manifest.files[{index}].path")
        pure = pathlib.PurePosixPath(relative)
        if pure.is_absolute() or ".." in pure.parts or relative in observed:
            fail(f"manifest.files[{index}]: unsafe or duplicate path")
        observed.add(relative)
        size = bounded_int(
            item.get("size"), f"manifest.files[{index}].size", 1, 2**63 - 1
        )
        checksum = clean_text(
            item.get("sha256"), f"manifest.files[{index}].sha256"
        )
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            fail(f"manifest.files[{index}]: invalid SHA-256")
        total += size
    if manifest.get("file_count") != len(files):
        fail("manifest: file_count mismatch")
    if manifest.get("total_bytes") != total:
        fail("manifest: total_bytes mismatch")
    if manifest.get("manifest_id") != digest(manifest_identity(manifest)):
        fail("manifest: identity digest mismatch")
    return manifest


def verify_model_manifest(
    cache_root: str,
    manifest: dict[str, Any],
    metadata_only: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    cache_root = absolute_path(cache_root, "cache_root")
    model_root, snapshot, revision = resolve_snapshot(
        cache_root, manifest["model"]
    )
    if revision != manifest["snapshot_revision"]:
        fail(
            f"manifest: snapshot is {revision}, expected "
            f"{manifest['snapshot_revision']}"
        )
    incomplete = next(model_root.rglob("*.incomplete"), None)
    if incomplete is not None:
        fail(f"weights: interrupted download marker present: {incomplete}")

    actual = {
        relative: resolved
        for relative, resolved in iter_snapshot_files(model_root, snapshot)
    }
    expected_paths = {item["path"] for item in manifest["files"]}
    if set(actual) != expected_paths:
        missing = sorted(expected_paths - set(actual))
        extra = sorted(set(actual) - expected_paths)
        detail = f"missing={missing[:1]} extra={extra[:1]}"
        fail(f"manifest: snapshot file set changed ({detail})")

    bytes_checked = 0
    for item in manifest["files"]:
        path = actual[item["path"]]
        size = path.stat().st_size
        if size != item["size"]:
            fail(
                f"manifest: size changed for {item['path']} "
                f"({size} != {item['size']})"
            )
        if not metadata_only:
            checksum = sha256_file(path)
            if checksum != item["sha256"]:
                fail(f"manifest: SHA-256 mismatch for {item['path']}")
            bytes_checked += size
    elapsed = max(time.monotonic() - started, 0.000001)
    return {
        "state": "ok",
        "mode": "metadata" if metadata_only else "full",
        "manifest_id": manifest["manifest_id"],
        "snapshot_revision": revision,
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "bytes_hashed": bytes_checked,
        "seconds": round(elapsed, 6),
        "throughput_gib_s": round(
            bytes_checked / (1024**3) / elapsed, 6
        )
        if bytes_checked
        else None,
    }


def read_meminfo() -> dict[str, int | None]:
    wanted = ("MemAvailable", "Cached", "SReclaimable")
    values: dict[str, int | None] = {key: None for key in wanted}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, separator, remainder = line.partition(":")
                if not separator or key not in values:
                    continue
                fields = remainder.split()
                if not fields:
                    continue
                values[key] = int(fields[0]) * 1024
    except (OSError, ValueError):
        pass
    return {
        "mem_available_bytes": values["MemAvailable"],
        "cached_bytes": values["Cached"],
        "sreclaimable_bytes": values["SReclaimable"],
    }


def delta_or_none(before: int | None, after: int | None) -> int | None:
    if before is None or after is None:
        return None
    return after - before


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def benchmark_model_io(
    cache_root: str,
    manifest: dict[str, Any],
    *,
    rank: int | None,
    role: str | None,
    source: str,
    label: str | None,
    node_id: str | None,
    verify_sha256: bool,
    chunk_mib: int,
    max_mib_s: float | None,
) -> dict[str, Any]:
    cache_root = absolute_path(cache_root, "cache_root")
    chunk_size = bounded_int(chunk_mib, "chunk_mib", 1, 256) * 1024 * 1024
    rate_limit = (
        positive_float(max_mib_s, "max_mib_s")
        if max_mib_s is not None
        else None
    )
    model_root, snapshot, revision = resolve_snapshot(
        cache_root, manifest["model"]
    )
    if revision != manifest["snapshot_revision"]:
        fail(
            f"manifest: snapshot is {revision}, expected "
            f"{manifest['snapshot_revision']}"
        )
    incomplete = next(model_root.rglob("*.incomplete"), None)
    if incomplete is not None:
        fail(f"weights: interrupted download marker present: {incomplete}")
    actual = {
        relative: resolved
        for relative, resolved in iter_snapshot_files(model_root, snapshot)
    }
    expected_paths = {item["path"] for item in manifest["files"]}
    if set(actual) != expected_paths:
        fail("benchmark: snapshot file set differs from the sealed manifest")
    for item in manifest["files"]:
        if actual[item["path"]].stat().st_size != item["size"]:
            fail(f"benchmark: size changed for {item['path']}")

    before_memory = read_meminfo()
    before_usage = resource.getrusage(resource.RUSAGE_SELF)
    started_at = utc_timestamp()
    started = time.monotonic()
    bytes_read = 0
    buffer = bytearray(chunk_size)
    for item in manifest["files"]:
        hasher = hashlib.sha256() if verify_sha256 else None
        with actual[item["path"]].open("rb", buffering=0) as handle:
            if hasattr(os, "posix_fadvise") and hasattr(
                os, "POSIX_FADV_SEQUENTIAL"
            ):
                try:
                    os.posix_fadvise(
                        handle.fileno(), 0, 0, os.POSIX_FADV_SEQUENTIAL
                    )
                except OSError:
                    pass
            while size := handle.readinto(buffer):
                bytes_read += size
                if hasher is not None:
                    hasher.update(memoryview(buffer)[:size])
                if rate_limit is not None:
                    target_seconds = bytes_read / (1024**2) / rate_limit
                    delay = target_seconds - (time.monotonic() - started)
                    if delay > 0:
                        time.sleep(delay)
        if hasher is not None and hasher.hexdigest() != item["sha256"]:
            fail(f"benchmark: SHA-256 mismatch for {item['path']}")
    elapsed = max(time.monotonic() - started, 0.000001)
    finished_at = utc_timestamp()
    after_usage = resource.getrusage(resource.RUSAGE_SELF)
    after_memory = read_meminfo()
    user_seconds = after_usage.ru_utime - before_usage.ru_utime
    system_seconds = after_usage.ru_stime - before_usage.ru_stime
    cpu_seconds = user_seconds + system_seconds
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "model-io",
        "state": "ok",
        "label": label,
        "node_fingerprint": fingerprint(node_id or socket.gethostname()),
        "rank": rank,
        "role": role,
        "source": source,
        "read_pattern": "sequential-buffered-full-snapshot",
        "sha256_verified": verify_sha256,
        "rate_limit_mib_s": rate_limit,
        "profile": manifest["profile"],
        "model": manifest["model"],
        "manifest_id": manifest["manifest_id"],
        "snapshot_revision": revision,
        "file_count": manifest["file_count"],
        "bytes_read": bytes_read,
        "started_at": started_at,
        "finished_at": finished_at,
        "seconds": round(elapsed, 6),
        "throughput_gib_s": round(bytes_read / (1024**3) / elapsed, 6),
        "cpu_user_seconds": round(user_seconds, 6),
        "cpu_system_seconds": round(system_seconds, 6),
        "cpu_seconds": round(cpu_seconds, 6),
        "cpu_utilization_percent": round(cpu_seconds / elapsed * 100, 3),
        "max_rss_bytes": after_usage.ru_maxrss * 1024,
        "memory_before": before_memory,
        "memory_after": after_memory,
        "memory_delta": {
            key: delta_or_none(before_memory[key], after_memory[key])
            for key in before_memory
        },
    }
    if bytes_read != manifest["total_bytes"]:
        fail(
            f"benchmark: read {bytes_read} bytes, expected "
            f"{manifest['total_bytes']}"
        )
    return result


def load_counter_snapshot(
    path: str,
) -> dict[tuple[int, str, str], dict[str, Any]]:
    counters: dict[tuple[int, str, str], dict[str, Any]] = {}
    try:
        handle = open(path, encoding="utf-8")
    except OSError as exc:
        fail(f"benchmark counters: {path}: {exc}")
    with handle:
        for line_number, line in enumerate(handle, 1):
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 6:
                fail(
                    f"benchmark counters: {path}:{line_number}: "
                    "expected six TSV fields"
                )
            rank_text, node_id, role, netdev, rx_text, tx_text = fields
            rank = bounded_int(rank_text, "counter.rank", 0, 255)
            role = clean_text(role, "counter.role")
            netdev = clean_text(netdev, "counter.netdev")
            node_id = clean_text(node_id, "counter.node_fingerprint")
            if not re.fullmatch(r"[0-9a-f]{16}", node_id):
                fail("benchmark counters: invalid node fingerprint")
            try:
                rx_bytes = int(rx_text)
                tx_bytes = int(tx_text)
            except ValueError:
                fail(
                    f"benchmark counters: {path}:{line_number}: "
                    "byte counters must be integers"
                )
            if rx_bytes < 0 or tx_bytes < 0:
                fail("benchmark counters: negative byte counter")
            key = (rank, role, netdev)
            if key in counters:
                fail(f"benchmark counters: duplicate {key}")
            counters[key] = {
                "rank": rank,
                "node_fingerprint": node_id,
                "role": role,
                "netdev": netdev,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
            }
    if not counters:
        fail(f"benchmark counters: {path}: empty snapshot")
    return counters


def build_benchmark_report(
    config: dict[str, Any],
    manifest: dict[str, Any],
    result_paths: list[str],
    before_path: str,
    after_path: str,
    *,
    tag: str,
    source: str,
    scope: str,
    cache_state: str,
) -> dict[str, Any]:
    tag = profile_name(tag)
    if source not in ("fabric", "replicated"):
        fail("benchmark report: invalid source")
    if scope not in ("serving", "all-configured"):
        fail("benchmark report: invalid scope")
    if cache_state not in ("cold", "warm"):
        fail("benchmark report: invalid cache state")
    if config.get("profile") != manifest["profile"]:
        fail("benchmark report: config/manifest profile mismatch")
    if config.get("model") != manifest["model"]:
        fail("benchmark report: config/manifest model mismatch")

    results = [load_json(path) for path in result_paths]
    expected_count = (
        config["nodes"]
        if scope == "serving"
        else config["storage_nodes"]
    )
    if len(results) != expected_count:
        fail(
            f"benchmark report: got {len(results)} rank results, "
            f"expected {expected_count}"
        )
    results.sort(key=lambda item: item.get("rank", -1))
    expected_ranks = list(range(expected_count))
    if [item.get("rank") for item in results] != expected_ranks:
        fail("benchmark report: rank result set is incomplete")
    rank_config = {item["rank"]: item for item in config["ranks"]}
    for item in results:
        rank = item["rank"]
        if (
            item.get("schema_version") != 1
            or item.get("kind") != "model-io"
            or item.get("state") != "ok"
            or item.get("source") != source
            or item.get("manifest_id") != manifest["manifest_id"]
            or item.get("profile") != manifest["profile"]
            or item.get("model") != manifest["model"]
            or item.get("role") != rank_config[rank]["role"]
            or item.get("node_fingerprint")
            != fingerprint(rank_config[rank]["node_id"])
            or item.get("bytes_read") != manifest["total_bytes"]
        ):
            fail(f"benchmark report: invalid or mismatched result for rank {rank}")
    raw_rate_limits = [
        item.get("rate_limit_mib_s") for item in results
    ]
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value <= 0
        )
        for value in raw_rate_limits
    ):
        fail("benchmark report: invalid fault-injection rate")
    if any(value != raw_rate_limits[0] for value in raw_rate_limits[1:]):
        fail("benchmark report: ranks used different pacing rates")
    rate_limit_mib_s = (
        float(raw_rate_limits[0])
        if raw_rate_limits[0] is not None
        else None
    )

    before = load_counter_snapshot(before_path)
    after = load_counter_snapshot(after_path)
    if set(before) != set(after):
        fail("benchmark report: network-counter interface set changed")
    if any(
        item["role"] not in ("control", "fabric-client", "fabric-owner")
        for item in before.values()
    ):
        fail("benchmark report: unexpected network-counter role")
    control_keys = {
        key for key, item in before.items() if item["role"] == "control"
    }
    if (
        len(control_keys) != expected_count
        or {key[0] for key in control_keys} != set(expected_ranks)
    ):
        fail("benchmark report: expected one control interface per rank")
    selected_clients = [
        item
        for item in config["transport"]["clients"]
        if item["rank"] < expected_count
    ]
    owner_rank = config["owner"]["topology_rank"]
    expected_fabric_keys = {
        (item["rank"], "fabric-client", item["client_netdev"])
        for item in selected_clients
    }
    expected_fabric_keys.update(
        {
            (owner_rank, "fabric-owner", item["server_netdev"])
            for item in selected_clients
        }
    )
    actual_fabric_keys = {
        key for key, item in before.items() if item["role"] != "control"
    }
    if actual_fabric_keys != expected_fabric_keys:
        fail(
            "benchmark report: counters do not match configured RoCE interfaces"
        )
    network = []
    for key in sorted(before):
        first = before[key]
        last = after[key]
        rank_record = rank_config.get(first["rank"])
        expected_fingerprint = (
            fingerprint(rank_record["node_id"]) if rank_record else None
        )
        if (
            first["node_fingerprint"] != expected_fingerprint
            or last["node_fingerprint"] != expected_fingerprint
        ):
            fail(
                "benchmark report: network counter node identity mismatch"
            )
        rx_delta = last["rx_bytes"] - first["rx_bytes"]
        tx_delta = last["tx_bytes"] - first["tx_bytes"]
        if rx_delta < 0 or tx_delta < 0:
            fail(f"benchmark report: network counter moved backwards for {key}")
        network.append(
            {
                "rank": first["rank"],
                "node_fingerprint": first["node_fingerprint"],
                "role": first["role"],
                "netdev": first["netdev"],
                "rx_bytes": rx_delta,
                "tx_bytes": tx_delta,
            }
        )

    remote_results = [item for item in results if item["role"] == "client"]
    remote_logical_bytes = sum(item["bytes_read"] for item in remote_results)
    client_fabric_rx = sum(
        item["rx_bytes"] for item in network if item["role"] == "fabric-client"
    )
    owner_fabric_tx = sum(
        item["tx_bytes"] for item in network if item["role"] == "fabric-owner"
    )
    control_bytes = sum(
        item["rx_bytes"] + item["tx_bytes"]
        for item in network
        if item["role"] == "control"
    )
    max_seconds = max(float(item["seconds"]) for item in results)
    logical_bytes = sum(int(item["bytes_read"]) for item in results)
    traffic_checks: list[dict[str, Any]] = []
    if (
        source == "fabric"
        and cache_state == "cold"
        and remote_logical_bytes
    ):
        for result in remote_results:
            observed = sum(
                item["rx_bytes"]
                for item in network
                if item["rank"] == result["rank"]
                and item["role"] == "fabric-client"
            )
            minimum = int(result["bytes_read"] * 0.8)
            traffic_checks.append(
                {
                    "name": f"rank-{result['rank']}-fabric-rx",
                    "observed_bytes": observed,
                    "minimum_bytes": minimum,
                    "pass": observed >= minimum,
                }
            )
        owner_minimum = int(remote_logical_bytes * 0.8)
        traffic_checks.append(
            {
                "name": "owner-fabric-tx",
                "observed_bytes": owner_fabric_tx,
                "minimum_bytes": owner_minimum,
                "pass": owner_fabric_tx >= owner_minimum,
            }
        )
        control_maximum = max(int(remote_logical_bytes * 0.2), 64 * 1024**2)
        traffic_checks.append(
            {
                "name": "control-lan-upper-bound",
                "observed_bytes": control_bytes,
                "maximum_bytes": control_maximum,
                "pass": control_bytes <= control_maximum,
            }
        )
        traffic_state = (
            "pass"
            if all(item["pass"] for item in traffic_checks)
            else "fail"
        )
    elif source == "replicated" and cache_state == "cold":
        fabric_bytes = client_fabric_rx + owner_fabric_tx
        fabric_maximum = max(int(logical_bytes * 0.05), 64 * 1024**2)
        control_maximum = max(int(logical_bytes * 0.05), 64 * 1024**2)
        traffic_checks.extend(
            [
                {
                    "name": "fabric-upper-bound",
                    "observed_bytes": fabric_bytes,
                    "maximum_bytes": fabric_maximum,
                    "pass": fabric_bytes <= fabric_maximum,
                },
                {
                    "name": "control-lan-upper-bound",
                    "observed_bytes": control_bytes,
                    "maximum_bytes": control_maximum,
                    "pass": control_bytes <= control_maximum,
                },
            ]
        )
        traffic_state = (
            "pass"
            if all(item["pass"] for item in traffic_checks)
            else "fail"
        )
    else:
        traffic_state = "not-applicable"

    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "weight-fabric-benchmark",
        "state": "ok",
        "recorded_at": utc_timestamp(),
        "tag": tag,
        "profile": manifest["profile"],
        "model": manifest["model"],
        "source": source,
        "transport": "nfs-rdma" if source == "fabric" else "local-replicated",
        "scope": scope,
        "cache_state": cache_state,
        "concurrent": True,
        "measurement_kind": (
            "fault-injection"
            if rate_limit_mib_s is not None
            else "performance"
        ),
        "rate_limit_mib_s": rate_limit_mib_s,
        "throughput_comparable": rate_limit_mib_s is None,
        "topology_id": config["topology_id"],
        "configuration_id": config["configuration_id"],
        "manifest_id": manifest["manifest_id"],
        "owner": {
            "rank": config["owner"]["topology_rank"],
            "node_fingerprint": fingerprint(config["owner"]["node_id"]),
        },
        "serving_nodes": config["nodes"],
        "storage_nodes": config["storage_nodes"],
        "aggregate": {
            "rank_count": len(results),
            "logical_bytes_read": logical_bytes,
            "max_rank_seconds": round(max_seconds, 6),
            "logical_throughput_gib_s": round(
                logical_bytes / (1024**3) / max_seconds, 6
            ),
            "cpu_seconds": round(
                sum(float(item["cpu_seconds"]) for item in results), 6
            ),
            "remote_logical_bytes": remote_logical_bytes,
            "client_fabric_rx_bytes": client_fabric_rx,
            "owner_fabric_tx_bytes": owner_fabric_tx,
            "control_lan_bytes": control_bytes,
        },
        "traffic_proof": {
            "state": traffic_state,
            "checks": traffic_checks,
        },
        "ranks": results,
        "network": network,
    }
    return report


def render_verification(result: dict[str, Any]) -> None:
    if TerminalWriter is None:
        fail("rendering requires scripts/terminal_format.py")
    term = TerminalWriter()
    term.emit("WEIGHT INTEGRITY")
    term.field("Status", "PASS")
    term.field("Mode", result["mode"])
    term.field("Manifest", result["manifest_id"][:12])
    term.field("Files", str(result["file_count"]))
    term.field(
        "Size", f"{result['total_bytes'] / (1024**3):.2f} GiB"
    )
    if result["bytes_hashed"]:
        term.field(
            "Read",
            f"{result['seconds']:.2f} s · "
            f"{result['throughput_gib_s']:.2f} GiB/s",
        )


def command_configure(args: argparse.Namespace) -> None:
    topology = load_topology(args.topology)
    config = build_configuration(
        topology=topology,
        profile=args.profile,
        model=args.model,
        nodes=args.nodes,
        storage_nodes=args.storage_nodes,
        owner_selector=args.owner,
        cache_root=args.cache_root,
        mount_root=args.mount_root,
        port=args.port,
        rail_index=args.rail_index,
    )
    if args.output:
        atomic_write_json(config, args.output, 0o600)
    if args.json:
        print(json.dumps(config, indent=2, sort_keys=True))
    else:
        render_configuration(config)


def validated_config_from_args(args: argparse.Namespace) -> dict[str, Any]:
    topology = load_topology(args.topology)
    config = load_json(args.config)
    validate_configuration(
        config,
        topology,
        expected_profile=args.profile,
        expected_model=args.model,
        expected_nodes=args.nodes,
        allow_legacy_teardown=getattr(args, "allow_legacy_teardown", False),
    )
    return config


def command_validate(args: argparse.Namespace) -> None:
    validated_config_from_args(args)


def command_render(args: argparse.Namespace) -> None:
    render_configuration(validated_config_from_args(args))


def command_rows(args: argparse.Namespace) -> None:
    rows(validated_config_from_args(args))


def command_json(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            validated_config_from_args(args),
            indent=2,
            sort_keys=True,
        )
    )


def command_provenance(args: argparse.Namespace) -> None:
    topology = load_topology(args.topology)
    config = load_json(args.config)
    validate_configuration(
        config,
        topology,
        expected_profile=args.profile,
        expected_model=args.model,
        expected_nodes=args.nodes,
    )
    value = public_provenance(config, topology)
    if args.output:
        atomic_write_json(value, args.output, 0o644)
    print(json.dumps(value, indent=2, sort_keys=True))


def command_artifact_audit(args: argparse.Namespace) -> None:
    topology = load_topology(args.topology)
    config = load_json(args.config)
    validate_configuration(config, topology)
    result = audit_public_artifact_directory(
        args.directory,
        config,
        topology,
        private_inputs=(args.config, args.topology),
        output=args.output,
    )
    atomic_write_json(result, args.output, 0o644)
    print(json.dumps(result, indent=2, sort_keys=True))


def command_startup_metric(args: argparse.Namespace) -> None:
    topology_id = clean_text(args.topology_id, "topology_id")
    if not re.fullmatch(r"[0-9a-f]{64}", topology_id):
        fail("startup metric: invalid topology identity")
    configuration_id = args.configuration_id or None
    if configuration_id is not None and not re.fullmatch(
        r"[0-9a-f]{64}", configuration_id
    ):
        fail("startup metric: invalid configuration identity")
    content_id = args.content_id or None
    content_digest = args.content_digest or None
    transport = args.transport or None
    integrity_scheme = args.integrity_scheme or None
    owner_node_id = args.owner_node_id or None
    if owner_node_id is not None:
        owner_node_id = clean_text(owner_node_id, "owner_node_id")
    if args.weight_source == "fabric" and (
        configuration_id is None or owner_node_id is None
    ):
        fail("startup metric: fabric evidence requires config and owner")
    if args.weight_source == "fabric" and any(
        value is not None
        for value in (content_id, content_digest, transport, integrity_scheme)
    ):
        fail("startup metric: fabric evidence cannot claim hot content")
    if args.weight_source == "replicated":
        if any(
            value is not None
            for value in (
                configuration_id,
                owner_node_id,
                content_id,
                content_digest,
                transport,
                integrity_scheme,
            )
        ):
            fail(
                "startup metric: replicated evidence cannot claim an owner "
                "or shared content"
            )
    if args.weight_source == "library-hot":
        if configuration_id is not None:
            fail("startup metric: library-hot content is not a fabric config")
        if owner_node_id is None:
            fail("startup metric: library-hot evidence requires a home owner")
        if content_id is None or not re.fullmatch(r"[0-9a-f]{12}", content_id):
            fail("startup metric: invalid library-hot content identity")
        if content_digest is None or not re.fullmatch(
            r"[0-9a-f]{64}", content_digest
        ):
            fail("startup metric: invalid library-hot content digest")
        if transport not in ("ssh-control", "ssh-roce", "nfs-rdma"):
            fail("startup metric: invalid library-hot transport")
        if integrity_scheme != "sha256-snapshot-manifest-v1":
            fail("startup metric: invalid library-hot integrity scheme")
        if args.cache_state != "sealed-hot":
            fail("startup metric: library-hot cache state must be sealed-hot")
    elif args.cache_state == "sealed-hot":
        fail("startup metric: sealed-hot cache state requires library-hot")
    destination = pathlib.Path(args.output)
    if destination == pathlib.Path("/") or destination.exists():
        fail("startup metric: output must be a new bounded path")
    tag = profile_name(args.tag) if args.tag else None
    record = {
        "schema_version": 2,
        "kind": "container-launch-to-first-health",
        "profile": profile_name(args.profile),
        "model": clean_text(args.model, "model"),
        "weight_source": args.weight_source,
        "nodes": bounded_int(args.nodes, "nodes", 2, 255),
        "topology_id": topology_id,
        "configuration_id": configuration_id,
        "content_id": content_id,
        "content_digest": content_digest,
        "transport": transport,
        "integrity_scheme": integrity_scheme,
        "owner_node_fingerprint": (
            fingerprint(owner_node_id) if owner_node_id else None
        ),
        "tag": tag,
        "cache_state": args.cache_state,
        "started_at": clean_text(args.started_at, "started_at"),
        "first_healthy_at": clean_text(
            args.first_healthy_at, "first_healthy_at"
        ),
        "time_to_first_healthy_seconds": positive_float(
            args.elapsed_seconds, "elapsed_seconds"
        ),
    }
    atomic_write_json(record, args.output, 0o644)


def command_repository_access(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            validate_repository_access(args.repository),
            indent=2,
            sort_keys=True,
        )
    )


def command_export_scope(args: argparse.Namespace) -> None:
    try:
        active_document = pathlib.Path(args.active_exports).read_text(
            encoding="utf-8"
        )
        pulsar_export_files = pathlib.Path(args.pulsar_export_files).read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as exc:
        fail(f"export scope: cannot read captured owner state: {exc}")
    result = validate_export_scope(
        active_document,
        pulsar_export_files,
        expected_export_file=args.expected_export_file,
        export_path=args.export_path,
        clients=args.client,
        anonuid=bounded_int(
            args.anonuid, "anonuid", 1, 2**32 - 2
        ),
        anongid=bounded_int(
            args.anongid, "anongid", 0, 2**32 - 2
        ),
        require_active=args.require_active,
        forbid_active=args.forbid_active,
        require_export_file=args.require_export_file,
        forbid_export_file=args.forbid_export_file,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def command_manifest_create(args: argparse.Namespace) -> None:
    manifest = build_model_manifest(
        args.cache_root, args.model, args.profile
    )
    atomic_write_json(manifest, args.output, 0o644)
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(
            f"manifest {manifest['manifest_id'][:12]} · "
            f"{manifest['file_count']} files · "
            f"{manifest['total_bytes'] / (1024**3):.2f} GiB"
        )


def command_manifest_verify(args: argparse.Namespace) -> None:
    manifest = load_model_manifest(args.manifest)
    if args.profile and manifest["profile"] != args.profile:
        fail(
            f"manifest: profile is {manifest['profile']}, "
            f"expected {args.profile}"
        )
    if args.model and manifest["model"] != args.model:
        fail(
            f"manifest: model is {manifest['model']}, expected {args.model}"
        )
    result = verify_model_manifest(
        args.cache_root, manifest, args.metadata_only
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        render_verification(result)


def command_io_benchmark(args: argparse.Namespace) -> None:
    manifest = load_model_manifest(args.manifest)
    if args.profile and manifest["profile"] != args.profile:
        fail(
            f"manifest: profile is {manifest['profile']}, "
            f"expected {args.profile}"
        )
    if args.model and manifest["model"] != args.model:
        fail(
            f"manifest: model is {manifest['model']}, expected {args.model}"
        )
    result = benchmark_model_io(
        args.cache_root,
        manifest,
        rank=args.rank,
        role=args.role,
        source=args.source,
        label=args.label,
        node_id=args.node_id,
        verify_sha256=args.verify_sha256,
        chunk_mib=args.chunk_mib,
        max_mib_s=args.max_mib_s,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


def command_benchmark_report(args: argparse.Namespace) -> None:
    config = load_json(args.config)
    manifest = load_model_manifest(args.manifest)
    report = build_benchmark_report(
        config,
        manifest,
        args.result,
        args.network_before,
        args.network_after,
        tag=args.tag,
        source=args.source,
        scope=args.scope,
        cache_state=args.cache_state,
    )
    atomic_write_json(report, args.output, 0o644)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["traffic_proof"]["state"] == "fail":
        raise SystemExit(1)


def add_config_validation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("config")
    parser.add_argument("topology")
    parser.add_argument("--profile")
    parser.add_argument("--model")
    parser.add_argument("--nodes", type=int)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser(
        "configure", help="build a topology-bound fabric configuration"
    )
    configure.add_argument("topology")
    configure.add_argument("--profile", required=True)
    configure.add_argument("--model", required=True)
    configure.add_argument("--nodes", required=True, type=int)
    configure.add_argument(
        "--storage-nodes",
        type=int,
        help="confirmed nodes that can read the owner copy (default: --nodes)",
    )
    configure.add_argument("--owner", required=True)
    configure.add_argument("--cache-root", required=True)
    configure.add_argument(
        "--mount-root", default="/mnt/pulsar-weight-fabric"
    )
    configure.add_argument("--port", type=int, default=20049)
    configure.add_argument("--rail-index", type=int, default=0)
    configure.add_argument("--output")
    configure.add_argument("--json", action="store_true")
    configure.set_defaults(func=command_configure)

    validate = subparsers.add_parser("validate")
    add_config_validation_args(validate)
    validate.set_defaults(func=command_validate)

    render = subparsers.add_parser("render")
    add_config_validation_args(render)
    render.set_defaults(func=command_render)

    row_parser = subparsers.add_parser("rows")
    add_config_validation_args(row_parser)
    row_parser.add_argument(
        "--allow-legacy-teardown",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    row_parser.set_defaults(func=command_rows)

    json_parser = subparsers.add_parser("json")
    add_config_validation_args(json_parser)
    json_parser.set_defaults(func=command_json)

    provenance = subparsers.add_parser(
        "provenance",
        help="render commit-safe topology/config evidence without site addresses",
    )
    add_config_validation_args(provenance)
    provenance.add_argument("--output")
    provenance.set_defaults(func=command_provenance)

    artifact_audit = subparsers.add_parser(
        "artifact-audit",
        help="reject private topology details before publishing a bundle",
    )
    artifact_audit.add_argument("--directory", required=True)
    artifact_audit.add_argument("--config", required=True)
    artifact_audit.add_argument("--topology", required=True)
    artifact_audit.add_argument("--output", required=True)
    artifact_audit.set_defaults(func=command_artifact_audit)

    startup_metric = subparsers.add_parser(
        "startup-metric",
        help="write no-overwrite launch-to-first-health evidence",
    )
    startup_metric.add_argument("--output", required=True)
    startup_metric.add_argument("--profile", required=True)
    startup_metric.add_argument("--model", required=True)
    startup_metric.add_argument(
        "--weight-source",
        choices=("fabric", "replicated", "library-hot"),
        required=True,
    )
    startup_metric.add_argument("--nodes", type=int, required=True)
    startup_metric.add_argument("--topology-id", required=True)
    startup_metric.add_argument("--configuration-id")
    startup_metric.add_argument("--owner-node-id")
    startup_metric.add_argument("--content-id")
    startup_metric.add_argument("--content-digest")
    startup_metric.add_argument(
        "--transport",
        choices=("ssh-control", "ssh-roce", "nfs-rdma"),
    )
    startup_metric.add_argument("--integrity-scheme")
    startup_metric.add_argument("--tag")
    startup_metric.add_argument(
        "--cache-state",
        choices=("cold", "warm", "sealed-hot", "unspecified"),
        default="unspecified",
    )
    startup_metric.add_argument("--started-at", required=True)
    startup_metric.add_argument("--first-healthy-at", required=True)
    startup_metric.add_argument(
        "--elapsed-seconds", type=float, required=True
    )
    startup_metric.set_defaults(func=command_startup_metric)

    repository_access = subparsers.add_parser(
        "repository-access",
        help="verify the mapped export identity can read one repository",
    )
    repository_access.add_argument("--repository", required=True)
    repository_access.set_defaults(func=command_repository_access)

    export_scope = subparsers.add_parser(
        "export-scope",
        help="verify exact repository export scope and policy",
    )
    export_scope.add_argument("--active-exports", required=True)
    export_scope.add_argument("--pulsar-export-files", required=True)
    export_scope.add_argument("--expected-export-file", required=True)
    export_scope.add_argument("--export-path", required=True)
    export_scope.add_argument(
        "--client",
        action="append",
        required=True,
        help="exact configured client RoCE address",
    )
    export_scope.add_argument("--anonuid", type=int, required=True)
    export_scope.add_argument("--anongid", type=int, required=True)
    export_scope.add_argument("--require-active", action="store_true")
    export_scope.add_argument("--forbid-active", action="store_true")
    export_scope.add_argument("--require-export-file", action="store_true")
    export_scope.add_argument("--forbid-export-file", action="store_true")
    export_scope.set_defaults(func=command_export_scope)

    create = subparsers.add_parser(
        "manifest-create", help="hash an authoritative HF snapshot"
    )
    create.add_argument("--cache-root", required=True)
    create.add_argument("--model", required=True)
    create.add_argument("--profile", required=True)
    create.add_argument("--output", required=True)
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=command_manifest_create)

    verify = subparsers.add_parser(
        "manifest-verify", help="verify an authoritative HF snapshot"
    )
    verify.add_argument("--cache-root", required=True)
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--profile")
    verify.add_argument("--model")
    verify.add_argument("--metadata-only", action="store_true")
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=command_manifest_verify)

    io_benchmark = subparsers.add_parser(
        "io-benchmark",
        help="measure full-snapshot buffered reads and process resources",
    )
    io_benchmark.add_argument("--cache-root", required=True)
    io_benchmark.add_argument("--manifest", required=True)
    io_benchmark.add_argument("--profile")
    io_benchmark.add_argument("--model")
    io_benchmark.add_argument("--rank", type=int)
    io_benchmark.add_argument("--role")
    io_benchmark.add_argument(
        "--source",
        choices=("fabric", "replicated"),
        required=True,
    )
    io_benchmark.add_argument("--label")
    io_benchmark.add_argument(
        "--node-id",
        help="private topology identity; only its fingerprint is emitted",
    )
    io_benchmark.add_argument("--verify-sha256", action="store_true")
    io_benchmark.add_argument("--chunk-mib", type=int, default=8)
    io_benchmark.add_argument(
        "--max-mib-s",
        type=float,
        help="pace a fault-injection read; omit for performance measurement",
    )
    io_benchmark.set_defaults(func=command_io_benchmark)

    report = subparsers.add_parser(
        "benchmark-report",
        help="aggregate rank metrics and prove cold traffic stayed on RoCE",
    )
    report.add_argument("--config", required=True)
    report.add_argument("--manifest", required=True)
    report.add_argument("--result", action="append", required=True)
    report.add_argument("--network-before", required=True)
    report.add_argument("--network-after", required=True)
    report.add_argument("--tag", required=True)
    report.add_argument(
        "--source", choices=("fabric", "replicated"), required=True
    )
    report.add_argument(
        "--scope", choices=("serving", "all-configured"), required=True
    )
    report.add_argument(
        "--cache-state", choices=("cold", "warm"), required=True
    )
    report.add_argument("--output", required=True)
    report.set_defaults(func=command_benchmark_report)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except WeightFabricError as exc:
        print(f"weight-fabric: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except BrokenPipeError:
        raise SystemExit(0)


if __name__ == "__main__":
    main()
