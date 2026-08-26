#!/usr/bin/env python3
"""Short-lived serving replacement transaction state (fails without fallback).

The record is a recovery aid, not service history. It contains no secrets,
container argv, hostnames, IP addresses, or filesystem paths.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile
import uuid
from typing import Any


SCHEMA_VERSION = 1
KIND = "pulsar-serving-replacement-transaction"
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
CONTENT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
SAFE_PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]+$")
TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PHASES = ("captured", "retained", "stopped")
RECOVERED_DIRNAME = "recovered"
ARCHIVE_STAMP_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")


class TransactionError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise TransactionError(message)


def load_json(path: str | pathlib.Path) -> Any:
    try:
        with pathlib.Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read JSON {path}: {exc}")


def require_digest(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        fail(f"{label} is missing or invalid")
    return value


def require_content_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or CONTENT_ID_RE.fullmatch(value) is None:
        fail(f"{label} is missing or invalid")
    return value


def require_profile(value: Any) -> str:
    if not isinstance(value, str) or SAFE_PROFILE_RE.fullmatch(value) is None:
        fail("profile is missing or invalid")
    return value


def require_revision(value: Any, label: str, *, optional: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or REVISION_RE.fullmatch(value) is None:
        fail(f"{label} is missing or invalid")
    return value


def rank_index(rank: dict[str, Any], inventory: dict[str, Any]) -> int:
    value = rank.get("rank")
    if value != "single":
        try:
            index = int(value)
        except (TypeError, ValueError):
            fail("live service has an invalid rank")
        if index < 0:
            fail("live service has an invalid rank")
        return index
    node = (inventory.get("nodes") or {}).get(rank.get("node") or "") or {}
    index = node.get("topology_index")
    if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
        return index
    if rank.get("node") == "head" and not inventory.get("topology_id"):
        return 0
    fail("single-node placement has no stable topology index")


def find_live_service(inventory: dict[str, Any], profile: str) -> dict[str, Any]:
    if inventory.get("schema_version") != 1:
        fail("inventory schema is unsupported")
    matches = [
        item
        for item in inventory.get("services") or []
        if item.get("conf") == profile and item.get("state") == "running"
    ]
    if len(matches) != 1:
        fail(f"expected one running {profile} service, observed {len(matches)}")
    service = matches[0]
    if service.get("ownership") != "managed":
        fail("previous service is not consistently stack-managed")
    if service.get("safe_to_stop") is not True:
        fail("previous service is not safe to stop")
    if service.get("complete") is not True or service.get("observability") != "complete":
        fail("previous service placement is incomplete or unobservable")
    ranks = service.get("ranks") or []
    expected = service.get("expected_ranks") or []
    if len(ranks) != len(expected) or not ranks:
        fail("previous service rank inventory is incomplete")
    if not all(rank.get("running") is True for rank in ranks):
        fail("previous service has a non-running rank")
    return service


def capture_placement(
    inventory: dict[str, Any], service: dict[str, Any]
) -> dict[str, Any]:
    ranks = service["ranks"]
    nodes = int(service.get("expected_nodes") or 0)
    if nodes < 1 or nodes != len(ranks):
        fail("previous service geometry is invalid")
    topology_id = inventory.get("topology_id")
    placements = []
    seen = set()
    for rank in ranks:
        index = rank_index(rank, inventory)
        labels = rank.get("labels") or {}
        node_id = labels.get("io.pulsar.gb10.node-id") or ""
        if index in seen:
            fail("previous service has duplicate physical rank placement")
        seen.add(index)
        if nodes > 1 and not node_id:
            fail("multi-node service rank has no stable node identity")
        placements.append({"rank": index, "node_id": node_id})
    placements.sort(key=lambda item: item["rank"])
    if nodes == 1:
        item = placements[0]
        if item["node_id"]:
            if not isinstance(topology_id, str) or not topology_id:
                fail("confirmed one-node placement has no topology identity")
            mode = "confirmed-node"
        else:
            only = ranks[0]
            if only.get("node") != "head" or topology_id:
                fail("one-node placement cannot be reproduced exactly")
            mode = "standalone-local"
        return {
            "mode": mode,
            "nodes": 1,
            "topology_id": topology_id,
            "ranks": placements,
        }
    if not isinstance(topology_id, str) or not topology_id:
        fail("multi-node service has no topology identity")
    if [item["rank"] for item in placements] != list(range(nodes)):
        fail("multi-node service does not occupy the exact profile ranks")
    return {
        "mode": "exact-topology",
        "nodes": nodes,
        "topology_id": topology_id,
        "ranks": placements,
    }


def library_contract(
    health: dict[str, Any],
    *,
    profile: str,
    service: dict[str, Any],
    placement: dict[str, Any],
) -> dict[str, Any]:
    if health.get("schema_version") != 1 or health.get("kind") != "pulsar-model-library-health":
        fail("model-library health schema is unsupported")
    catalog = health.get("catalog") or {}
    if catalog.get("topology_compatible") is not True:
        fail("model-library catalog is stale for the confirmed topology")
    revision = require_revision(service.get("model_revision"), "model revision")
    identity = service.get("model_identity_status")
    if identity not in {"legacy-unsealed", "unvalidated"}:
        fail("library-backed service identity is not a live unsealed status")
    if service.get("model_seal_id") is not None or service.get("validation_bundle_id") is not None:
        fail("library-backed service cannot claim retired seal provenance")
    content = require_content_id(
        service.get("weight_configuration_id"), "weight configuration"
    )
    owner = service.get("weight_owner_node_id")
    if not isinstance(owner, str) or not owner:
        fail("library-backed service has no durable-home identity")

    wanted_ranks = {item["rank"] for item in placement["ranks"]}
    views = [
        item
        for item in health.get("hot_instances") or []
        if item.get("profile") == profile
        and item.get("revision") == revision
        and item.get("rank") in wanted_ranks
    ]
    if len(views) != len(wanted_ranks) or {item.get("rank") for item in views} != wanted_ranks:
        fail("exact prepared runtime views are incomplete")
    retentions = {item.get("retention") for item in views}
    if len(retentions) != 1 or next(iter(retentions)) not in {"ephemeral", "pinned"}:
        fail("prepared runtime-view retention is incomplete or inconsistent")
    for view in views:
        if view.get("metadata_schema") != 3 or view.get("metadata_status") != "current":
            fail("prepared runtime-view metadata is not current schema 3")
        if view.get("runtime_source") not in {"durable-home", "sealed-hot"}:
            fail("prepared runtime source is unknown")
        if view.get("identity_status") not in {"legacy-unsealed", "unvalidated"} \
                or view.get("witness_status") != "match":
            fail("prepared runtime-view identity or witness does not match")
        if view.get("active_reference") is not True:
            fail("prepared runtime view is not bound to the running service")
    home_views = [item for item in views if item.get("runtime_source") == "durable-home"]
    if len(home_views) != 1:
        fail("prepared runtime views do not identify exactly one durable home")
    placement_by_rank = {item["rank"]: item["node_id"] for item in placement["ranks"]}
    if placement_by_rank.get(home_views[0]["rank"]) != owner:
        fail("durable-home runtime view does not match the service owner identity")

    models = [
        item
        for item in health.get("models") or []
        if profile in (item.get("profiles") or []) and item.get("revision") == revision
    ]
    if len(models) != 1:
        fail("catalog identity for the running library service is ambiguous")
    return {
        "source": "library-hot",
        "revision": revision,
        "manifest_id": None,
        "content_id": content,
        "home_node_id": owner,
        "original_retention": next(iter(retentions)),
        "runtime_views": [
            {"rank": item["rank"], "source": item["runtime_source"]}
            for item in sorted(views, key=lambda item: item["rank"])
        ],
    }


def validate_transaction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("replacement transaction schema is unsupported")
    if value.get("kind") != KIND or value.get("phase") not in PHASES:
        fail("replacement transaction metadata is invalid")
    transaction_id = value.get("transaction_id")
    if not isinstance(transaction_id, str) or TRANSACTION_ID_RE.fullmatch(transaction_id) is None:
        fail("replacement transaction identity is invalid")
    if not isinstance(value.get("created_at"), str) or not value["created_at"]:
        fail("replacement transaction timestamp is invalid")

    service = value.get("previous_service")
    if not isinstance(service, dict):
        fail("saved service contract is invalid")
    require_profile(service.get("profile"))
    require_digest(service.get("launch_contract_id"), "saved launch contract")
    if service.get("spec_decode") not in {"on", "off"}:
        fail("saved speculative-decode state is invalid")

    placement = service.get("placement")
    if not isinstance(placement, dict):
        fail("saved placement is invalid")
    mode = placement.get("mode")
    if mode not in {"standalone-local", "confirmed-node", "exact-topology"}:
        fail("saved placement mode is invalid")
    nodes = placement.get("nodes")
    ranks = placement.get("ranks")
    if not isinstance(nodes, int) or isinstance(nodes, bool) or nodes < 1:
        fail("saved placement geometry is invalid")
    if not isinstance(ranks, list) or len(ranks) != nodes:
        fail("saved placement ranks are invalid")
    normalized_ranks = []
    for item in ranks:
        if not isinstance(item, dict):
            fail("saved placement rank is invalid")
        rank = item.get("rank")
        node_id = item.get("node_id")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 0:
            fail("saved placement rank is invalid")
        if not isinstance(node_id, str):
            fail("saved physical node identity is invalid")
        normalized_ranks.append(rank)
    if len(set(normalized_ranks)) != nodes:
        fail("saved placement ranks are ambiguous")
    if mode == "standalone-local":
        if nodes != 1 or ranks[0]["node_id"] or placement.get("topology_id") not in (None, ""):
            fail("saved standalone placement is inconsistent")
    else:
        require_digest(placement.get("topology_id"), "saved topology")
        if any(not item["node_id"] for item in ranks):
            fail("saved physical node identity is missing")
        if mode == "confirmed-node" and nodes != 1:
            fail("saved one-node placement geometry is inconsistent")
        if mode == "exact-topology" and normalized_ranks != list(range(nodes)):
            fail("saved multi-node placement is not exact")

    weight = service.get("weight")
    if not isinstance(weight, dict) or weight.get("source") != "library-hot":
        if isinstance(weight, dict) and weight.get("source") == "replicated":
            fail(
                "saved transaction was captured under the removed replicated "
                "mechanism (ADR 0006); exact rollback is impossible"
            )
        fail("saved weight source is unsupported")
    require_revision(weight.get("revision"), "saved model revision")
    for key, label in (
        ("model_seal_id", "saved model seal"),
        ("validation_bundle_id", "saved validation bundle"),
        ("manifest_id", "saved manifest"),
    ):
        if weight.get(key) is not None:
            fail(f"{label} is retired (ADR 0012)")
    require_content_id(weight.get("content_id"), "saved content identity")
    if not isinstance(weight.get("home_node_id"), str) or not weight["home_node_id"]:
        fail("saved durable-home identity is invalid")
    if weight.get("original_retention") not in {"ephemeral", "pinned"}:
        fail("saved runtime-view retention is invalid")
    views = weight.get("runtime_views")
    if not isinstance(views, list) or len(views) != nodes:
        fail("saved runtime views are incomplete")
    view_ranks = []
    home_count = 0
    for item in views:
        if not isinstance(item, dict) or item.get("source") not in {
            "durable-home",
            "sealed-hot",
        }:
            fail("saved runtime view is invalid")
        rank = item.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool):
            fail("saved runtime-view rank is invalid")
        view_ranks.append(rank)
        home_count += item["source"] == "durable-home"
    if sorted(view_ranks) != sorted(normalized_ranks) or home_count != 1:
        fail("saved runtime views do not match exact placement")

    retention = value.get("temporary_retention")
    if not isinstance(retention, dict):
        fail("temporary retention state is invalid")
    required = retention.get("required")
    applied = retention.get("applied")
    if not isinstance(required, bool) or not isinstance(applied, bool):
        fail("temporary retention state is invalid")
    expected_required = weight.get("original_retention") == "ephemeral"
    if required != expected_required:
        fail("temporary retention policy does not match the saved service")
    if value["phase"] == "captured" and applied:
        fail("temporary retention cannot be applied before retention phase")
    if value["phase"] in {"retained", "stopped"} and applied != required:
        fail("temporary retention phase is incomplete")
    return value


def create_exclusive(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fail("an unresolved replacement transaction already exists")
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_update(path: pathlib.Path, value: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def cmd_capture(args: argparse.Namespace) -> int:
    profile = require_profile(args.profile)
    launch_contract_id = require_digest(args.launch_contract_id, "launch contract")
    inventory = load_json(args.inventory)
    service = find_live_service(inventory, profile)
    if service.get("launch_contract_id") != launch_contract_id:
        fail(
            "running service lacks the current exact launch-contract label; "
            "automatic replacement is unavailable"
        )
    spec_decode = service.get("spec_decode")
    if spec_decode not in {"on", "off"}:
        fail(
            "running service lacks an exact speculative-decode label; "
            "automatic replacement is unavailable"
        )
    placement = capture_placement(inventory, service)
    source = service.get("weight_source")
    if source == "library-hot":
        if not args.library_health:
            fail("library-backed replacement requires current model-library health")
        weight = library_contract(
            load_json(args.library_health),
            profile=profile,
            service=service,
            placement=placement,
        )
    elif source == "replicated":
        fail(
            "running service predates the library-only decision (ADR 0006); "
            "stop and restart it to migrate before automatic replacement"
        )
    else:
        fail("previous service weight source is missing, mixed, or unsupported")
    now = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    transaction = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "transaction_id": uuid.uuid4().hex,
        "created_at": now,
        "phase": "captured",
        "previous_service": {
            "profile": profile,
            "launch_contract_id": launch_contract_id,
            "spec_decode": spec_decode,
            "placement": placement,
            "weight": weight,
        },
        "temporary_retention": {
            "required": weight.get("original_retention") == "ephemeral",
            "applied": False,
        },
    }
    validate_transaction(transaction)
    create_exclusive(pathlib.Path(args.output), transaction)
    print(json.dumps(transaction, indent=2, sort_keys=True))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    print(json.dumps(validate_transaction(load_json(args.path)), indent=2, sort_keys=True))
    return 0


def cmd_phase(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path)
    value = validate_transaction(load_json(path))
    transitions = {"captured": "retained", "retained": "stopped"}
    if transitions.get(value["phase"]) != args.to:
        fail(f"invalid transaction phase transition {value['phase']} -> {args.to}")
    value["phase"] = args.to
    if args.to == "retained":
        value["temporary_retention"]["applied"] = bool(
            value["temporary_retention"]["required"]
        )
    atomic_update(path, value)
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


def verify_library_views_for_rollback(
    transaction: dict[str, Any], health: dict[str, Any]
) -> None:
    if health.get("schema_version") != 1 or health.get("kind") != "pulsar-model-library-health":
        fail("current model-library health schema is unsupported")
    if (health.get("catalog") or {}).get("topology_compatible") is not True:
        fail("model-library catalog is stale for the confirmed topology")
    service = transaction["previous_service"]
    weight = service["weight"]
    models = [
        item
        for item in health.get("models") or []
        if service["profile"] in (item.get("profiles") or [])
        and item.get("revision") == weight["revision"]
        and item.get("expected_manifest") == weight["manifest_id"]
    ]
    if len(models) != 1:
        fail("saved catalog model identity is no longer exact")
    wanted = {item["rank"]: item["source"] for item in weight["runtime_views"]}
    matches = [
        item
        for item in health.get("hot_instances") or []
        if item.get("profile") == service["profile"]
        and item.get("revision") == weight["revision"]
        and item.get("rank") in wanted
    ]
    if len(matches) != len(wanted) or {item.get("rank") for item in matches} != set(wanted):
        fail("saved library runtime views are no longer fully observable")
    for item in matches:
        if item.get("metadata_schema") != 3 or item.get("metadata_status") != "current":
            fail("saved library runtime-view metadata is no longer current")
        if item.get("runtime_source") != wanted[item["rank"]]:
            fail("saved library runtime source changed")
        if item.get("identity_status") not in {"legacy-unsealed", "unvalidated"} \
                or item.get("witness_status") != "match":
            fail("saved library runtime-view identity drifted")
        if item.get("active_reference") is not False:
            fail("saved library runtime view still has an active reference")
        if item.get("retention") != "pinned":
            fail("saved library runtime view is no longer pinned")


def verify_current_placement(
    placement: dict[str, Any], inventory: dict[str, Any]
) -> None:
    if inventory.get("schema_version") != 1:
        fail("current inventory schema is unsupported")
    mode = placement["mode"]
    expected_topology = placement.get("topology_id")
    if mode != "standalone-local" and inventory.get("topology_id") != expected_topology:
        fail("confirmed topology changed after service capture")
    if mode == "standalone-local":
        if inventory.get("topology_id"):
            fail("standalone placement became ambiguous after topology confirmation")
        return
    current_by_rank = {}
    for node in (inventory.get("nodes") or {}).values():
        if not isinstance(node, dict):
            continue
        index = node.get("topology_index")
        if isinstance(index, int) and not isinstance(index, bool):
            current_by_rank[index] = node.get("node_id") or ""
    for item in placement["ranks"]:
        if current_by_rank.get(item["rank"]) != item["node_id"]:
            fail("captured physical node placement changed")


def cmd_verify_rollback(args: argparse.Namespace) -> int:
    value = validate_transaction(load_json(args.path))
    if value["phase"] != "stopped":
        fail("previous service has not been confirmed stopped")
    if args.launch_contract_id != value["previous_service"]["launch_contract_id"]:
        fail("repository profile changed after the previous service was captured")
    verify_current_placement(
        value["previous_service"]["placement"], load_json(args.inventory)
    )
    if value["previous_service"]["weight"]["source"] == "library-hot":
        if not args.library_health:
            fail("library rollback requires current model-library health")
        verify_library_views_for_rollback(value, load_json(args.library_health))
    print(json.dumps(value["previous_service"], indent=2, sort_keys=True))
    return 0


def classify_saved_transaction(value: Any) -> dict[str, Any]:
    """Classify a saved record without treating incompatibility as a crash.

    Current model-library transactions validate normally. Pre-ADR 0006
    replicated captures and other unreadable records are incompatible:
    exact rollback is impossible, but the live file can be archived.
    """
    if not isinstance(value, dict):
        return {
            "compatible": False,
            "reason": "unreadable",
            "detail": "saved transaction is not a JSON object",
            "profile": None,
        }
    profile = None
    service = value.get("previous_service")
    if isinstance(service, dict) and isinstance(service.get("profile"), str):
        try:
            profile = require_profile(service.get("profile"))
        except TransactionError:
            profile = None
    try:
        validated = validate_transaction(value)
        return {
            "compatible": True,
            "reason": None,
            "detail": None,
            "profile": validated["previous_service"]["profile"],
            "transaction": validated,
        }
    except TransactionError as exc:
        weight = service.get("weight") if isinstance(service, dict) else None
        source = weight.get("source") if isinstance(weight, dict) else None
        if source == "replicated":
            return {
                "compatible": False,
                "reason": "replicated",
                "detail": (
                    "saved transaction was captured under the removed "
                    "replicated mechanism (ADR 0006); exact rollback is "
                    "impossible"
                ),
                "profile": profile,
            }
        return {
            "compatible": False,
            "reason": "unsupported",
            "detail": str(exc),
            "profile": profile,
        }


def observe_named_service(inventory: dict[str, Any], profile: str | None) -> str:
    if not profile:
        return "unknown"
    if inventory.get("schema_version") != 1:
        return "ambiguous"
    matches = [
        item
        for item in inventory.get("services") or []
        if isinstance(item, dict) and item.get("conf") == profile
    ]
    running = [
        item
        for item in matches
        if item.get("state") == "running"
        or any(
            isinstance(rank, dict) and rank.get("running") is True
            for rank in item.get("ranks") or []
        )
    ]
    complete_running = [
        item
        for item in running
        if item.get("complete") is True
        and item.get("ownership") == "managed"
        and item.get("observability") == "complete"
        and item.get("safe_to_stop") is True
        and bool(item.get("ranks"))
        and all(
            isinstance(rank, dict) and rank.get("running") is True
            for rank in item.get("ranks") or []
        )
    ]
    if not matches:
        return "stopped"
    if len(running) == 1 and len(complete_running) == 1:
        return "running"
    return "ambiguous"


def inspect_recovery(path: pathlib.Path, inventory: dict[str, Any]) -> dict[str, Any]:
    resolved = pathlib.Path(path)
    archive_cmd = (
        f"python3 scripts/replacement_transaction.py archive "
        f"--path {resolved} --yes"
    )
    try:
        raw = load_json(resolved)
    except TransactionError as exc:
        return {
            "state": "incompatible",
            "reason": "unreadable",
            "detail": str(exc),
            "profile": None,
            "service": "unknown",
            "path": str(resolved),
            "archive_command": archive_cmd,
        }
    classified = classify_saved_transaction(raw)
    service_state = observe_named_service(inventory, classified.get("profile"))
    if classified["compatible"]:
        value = classified["transaction"]
        running = [
            item
            for item in inventory.get("services") or []
            if any(rank.get("running") is True for rank in item.get("ranks") or [])
        ]
        exact = [
            item
            for item in running
            if service_matches_snapshot(inventory, item, value["previous_service"])
        ]
        if len(running) == 0 and value["phase"] == "stopped":
            state = "stopped"
        elif len(running) == 1 and len(exact) == 1:
            state = "previous-running"
        else:
            state = "ambiguous"
        return {
            "state": state,
            "reason": None,
            "detail": None,
            "profile": value["previous_service"]["profile"],
            "service": service_state,
            "path": str(resolved),
            "archive_command": archive_cmd,
        }
    return {
        "state": "incompatible",
        "reason": classified["reason"],
        "detail": classified["detail"],
        "profile": classified.get("profile"),
        "service": service_state,
        "path": str(resolved),
        "archive_command": archive_cmd,
    }


def archive_destination(path: pathlib.Path, *, stamp: str) -> pathlib.Path:
    if ARCHIVE_STAMP_RE.fullmatch(stamp) is None:
        fail("archive timestamp is invalid")
    dest_dir = path.parent / RECOVERED_DIRNAME / stamp
    return dest_dir / path.name


def archive_transaction(path: pathlib.Path, *, yes: bool = False) -> dict[str, Any]:
    if not yes:
        fail("archive will move the live transaction; re-run with --yes")
    live = pathlib.Path(path)
    if not live.is_file() or live.is_symlink():
        fail("live replacement transaction is missing")
    try:
        raw = live.read_bytes()
    except OSError as exc:
        fail(f"cannot read live replacement transaction: {exc}")
    stamp = (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y%m%dT%H%M%SZ")
    )
    dest = archive_destination(live, stamp=stamp)
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        fail("archive destination already exists; retry")
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        live.unlink()
    except OSError as exc:
        fail(
            f"archived to {dest} but could not remove the live file: {exc}; "
            "inspect both paths before retrying"
        )
    return {
        "state": "archived",
        "from": str(live),
        "to": str(dest),
    }


def service_matches_snapshot(
    inventory: dict[str, Any], service: dict[str, Any], snapshot: dict[str, Any]
) -> bool:
    if service.get("state") != "running" or service.get("complete") is not True:
        return False
    if service.get("ownership") != "managed" or service.get("observability") != "complete":
        return False
    if service.get("conf") != snapshot["profile"]:
        return False
    if service.get("launch_contract_id") != snapshot["launch_contract_id"]:
        return False
    if service.get("spec_decode") != snapshot["spec_decode"]:
        return False
    if service.get("weight_source") != snapshot["weight"]["source"]:
        return False
    try:
        if capture_placement(inventory, service) != snapshot["placement"]:
            return False
    except TransactionError:
        return False
    weight = snapshot["weight"]
    if weight["source"] == "library-hot":
        return (
            service.get("model_revision") == weight["revision"]
            and service.get("model_identity_status")
            in {"legacy-unsealed", "unvalidated"}
            and service.get("weight_owner_node_id") == weight["home_node_id"]
            and service.get("weight_configuration_id") == weight["content_id"]
        )
    for key in ("revision", "model_seal_id", "validation_bundle_id"):
        expected = weight.get(key)
        service_key = {
            "revision": "model_revision",
            "model_seal_id": "model_seal_id",
            "validation_bundle_id": "validation_bundle_id",
        }[key]
        if expected is not None and service.get(service_key) != expected:
            return False
    return True


def cmd_recovery_state(args: argparse.Namespace) -> int:
    inventory = load_json(args.inventory)
    report = inspect_recovery(pathlib.Path(args.path), inventory)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["state"] != "ambiguous" else 1


def cmd_archive(args: argparse.Namespace) -> int:
    result = archive_transaction(pathlib.Path(args.path), yes=bool(args.yes))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    path = pathlib.Path(args.path)
    validate_transaction(load_json(path))
    path.unlink()
    print(json.dumps({"state": "complete", "outcome": args.outcome}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--inventory", required=True)
    capture.add_argument("--library-health")
    capture.add_argument("--profile", required=True)
    capture.add_argument("--launch-contract-id", required=True)
    capture.add_argument("--output", required=True)
    capture.set_defaults(func=cmd_capture)
    show = sub.add_parser("show")
    show.add_argument("--path", required=True)
    show.set_defaults(func=cmd_show)
    phase = sub.add_parser("phase")
    phase.add_argument("--path", required=True)
    phase.add_argument("--to", choices=("retained", "stopped"), required=True)
    phase.set_defaults(func=cmd_phase)
    verify = sub.add_parser("verify-rollback")
    verify.add_argument("--path", required=True)
    verify.add_argument("--launch-contract-id", required=True)
    verify.add_argument("--inventory", required=True)
    verify.add_argument("--library-health")
    verify.set_defaults(func=cmd_verify_rollback)
    recover = sub.add_parser("recovery-state")
    recover.add_argument("--path", required=True)
    recover.add_argument("--inventory", required=True)
    recover.set_defaults(func=cmd_recovery_state)
    archive = sub.add_parser("archive")
    archive.add_argument("--path", required=True)
    archive.add_argument(
        "--yes",
        action="store_true",
        help="move the live transaction into a timestamped recovered directory",
    )
    archive.set_defaults(func=cmd_archive)
    complete = sub.add_parser("complete")
    complete.add_argument("--path", required=True)
    complete.add_argument("--outcome", choices=("replacement", "rollback"), required=True)
    complete.set_defaults(func=cmd_complete)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return int(args.func(args))
    except TransactionError as exc:
        print(f"replacement transaction: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
