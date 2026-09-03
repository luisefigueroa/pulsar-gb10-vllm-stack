#!/usr/bin/env python3
"""Fixtures for WP1.4c spec-start selftests."""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from release_spec import pretty_json_bytes, verify_spec  # noqa: E402
from scripts import model_library  # noqa: E402
from scripts import release_consumer as consumer  # noqa: E402
from scripts.testlib.test_release_consumer import (  # noqa: E402
    overlay_document,
    released_nano_spec,
    write_json,
)


def write_released_nano(releases_root: pathlib.Path, *, review_status: str = "stable"):
    spec = released_nano_spec()
    document = json.loads(pretty_json_bytes(spec))
    document["review"]["status"] = review_status
    spec = verify_spec(document)
    path = pathlib.Path(releases_root) / f"{spec['spec_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pretty_json_bytes(spec))
    return spec, path


def write_overlay(
    path: pathlib.Path,
    *,
    served_name: str | None = "nemotron-3-nano",
    port: int = 8000,
    cache_root: str | None = None,
    node_id: str | None = None,
    extra_default: dict | None = None,
) -> None:
    defaults = {
        "port": port,
        "served_name": served_name,
        "cache_root": cache_root,
        "placement": None if node_id is None else {"node_id": node_id},
    }
    if extra_default:
        defaults.update(extra_default)
    write_json(path, overlay_document(defaults=defaults))


def write_identity_hot_view(
    hot_root: pathlib.Path,
    *,
    profile: str,
    topology_id: str,
    model_id: str,
    revision: str,
    manifest: dict | None = None,
    content_id: str = "c" * 12,
    activated_at: str = "2026-09-02T00:00:00Z",
) -> pathlib.Path:
    """Ready stamp under a conf-named directory for find-hot --identity.

    ``manifest`` is the sealed snapshot manifest the stamp claims; a spec
    start must only accept a view whose manifest id equals the spec's.
    """
    instance = model_library.hot_instance_dir(
        hot_root, profile, topology_id, content_id
    )
    instance.mkdir(parents=True, exist_ok=True)
    stamp = {
        "schema_version": 3,
        "state": "ready",
        "profile": profile,
        "model_id": model_id,
        "revision": revision,
        "identity_key": f"{model_id}@{revision}",
        "home_node_id": "fixture-node-0",
        "topology_id": topology_id,
        "content_id": content_id,
        "content_digest": "a" * 64,
        "integrity": {
            "scheme": "sha256-snapshot-manifest-v1",
            "manifest": dict(manifest or {}),
        },
        "validation": {"identity_status": "receipt-occupancy", "expected_seal": None},
        "backend": "copy",
        "bytes_logical": 1,
        "activated_at": activated_at,
        "pinned": False,
        "budget_bytes_accounted": 1,
        "transport": "ssh-roce",
    }
    model_library.write_hot_stamp(instance, stamp)
    return instance


def compare_start_plans(conf_plan: dict, spec_plan: dict, spec: dict) -> None:
    expected = consumer.comparable_contract_from_spec(spec)
    for label, plan in (("conf", conf_plan), ("spec", spec_plan)):
        comparable = consumer.plan_to_comparable(plan)
        result = consumer.compare_contracts(comparable, expected)
        if result != {"result": "equal", "fields": []}:
            raise SystemExit(f"{label} plan comparable {result}")
    for field in ("served_name", "port", "nodes", "topology_id", "network_mode"):
        if conf_plan.get(field) != spec_plan.get(field):
            raise SystemExit(
                f"{field} differs: conf={conf_plan.get(field)!r} "
                f"spec={spec_plan.get(field)!r}"
            )
    conf_ranks = [
        {k: row.get(k) for k in ("rank", "node_id", "hostname", "ssh_host")}
        for row in conf_plan.get("ranks") or []
    ]
    spec_ranks = [
        {k: row.get(k) for k in ("rank", "node_id", "hostname", "ssh_host")}
        for row in spec_plan.get("ranks") or []
    ]
    if conf_ranks != spec_ranks:
        raise SystemExit(f"ranks differ: conf={conf_ranks} spec={spec_ranks}")
    keys = (
        "model_id",
        "revision",
        "content_id",
        "hub_path",
        "container_model_path",
        "transport",
        "identity_status",
    )
    conf_storage = {k: (conf_plan.get("storage") or {}).get(k) for k in keys}
    spec_storage = {k: (spec_plan.get("storage") or {}).get(k) for k in keys}
    if conf_storage != spec_storage:
        raise SystemExit(
            f"library view differs: conf={conf_storage} spec={spec_storage}"
        )
    if conf_plan.get("profile") == spec_plan.get("profile"):
        raise SystemExit("profile keys should differ")
    if conf_plan.get("container_name") == spec_plan.get("container_name"):
        raise SystemExit("container names should differ")
    if conf_plan.get("launch_contract_id") == spec_plan.get("launch_contract_id"):
        raise SystemExit("launch_contract_id should differ")
