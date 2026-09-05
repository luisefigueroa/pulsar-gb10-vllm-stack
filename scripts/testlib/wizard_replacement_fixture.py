#!/usr/bin/env python3
"""Parameterized state mutations for serving-wizard rollback scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REVISION = "7" * 40
SEAL_ID = "a" * 64
BUNDLE_ID = "b" * 64
CONTENT_ID = "c" * 12


def load(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write(path: str | Path, value: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def seed_running(args: argparse.Namespace) -> int:
    inventory = load(args.inventory)
    empty = load(args.empty_inventory)
    topology = load(args.topology)
    health = load(args.health)
    topology_id = topology["topology_id"]
    inventory["topology_id"] = topology_id
    empty["topology_id"] = topology_id

    nodes_by_rank = {
        node["topology_index"]: node["node_id"]
        for node in inventory["nodes"].values()
    }
    home_ranks = {
        item["rank"]
        for item in health["hot_instances"]
        if item.get("runtime_source") == "durable-home"
    }
    if len(home_ranks) != 1:
        raise SystemExit("fixture requires exactly one durable-home view")
    home_node_id = nodes_by_rank[next(iter(home_ranks))]

    service = inventory["services"][0]
    service.update({
        "launch_contract_id": args.contract_id,
        "spec_decode": "on",
        "model_revision": REVISION,
        "model_seal_id": None,
        "validation_bundle_id": None,
        "model_identity_status": "receipt-occupancy",
        "weight_owner_node_id": home_node_id,
        "weight_configuration_id": CONTENT_ID,
    })
    for rank in service["ranks"]:
        index = int(rank["rank"])
        rank["labels"] = {
            "io.pulsar.gb10.managed": "true",
            "io.pulsar.gb10.conf": args.profile,
            "io.pulsar.gb10.rank": str(index),
            "io.pulsar.gb10.topology": topology_id,
            "io.pulsar.gb10.node-id": nodes_by_rank[index],
            "io.pulsar.gb10.weight-source": "local-files",
            "io.pulsar.gb10.weight-owner": home_node_id,
            "io.pulsar.gb10.weight-config": CONTENT_ID,
            "io.pulsar.gb10.model-revision": REVISION,
            "io.pulsar.gb10.model-identity-status": "receipt-occupancy",
            "io.pulsar.gb10.launch-contract": args.contract_id,
            "io.pulsar.gb10.spec-decode": "on",
        }

    for item in health["hot_instances"]:
        item["active_reference"] = True
    write(args.inventory, inventory)
    write(args.empty_inventory, empty)
    write(args.active_health, health)
    return 0


def mutate_health(args: argparse.Namespace) -> int:
    report = load(args.path)
    if args.purge:
        report["hot_instances"] = []
    else:
        for item in report.get("hot_instances", []):
            if args.retention:
                item["retention"] = args.retention
            if args.active is not None:
                item["active_reference"] = args.active == "true"
    write(args.path, report)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed-running")
    seed.add_argument("--inventory", required=True)
    seed.add_argument("--empty-inventory", required=True)
    seed.add_argument("--topology", required=True)
    seed.add_argument("--health", required=True)
    seed.add_argument("--active-health", required=True)
    seed.add_argument("--contract-id", required=True)
    seed.add_argument("--profile", required=True, help="spec id the service was launched as")
    seed.set_defaults(func=seed_running)
    mutate = commands.add_parser("mutate-health")
    mutate.add_argument("--path", required=True)
    mutate.add_argument("--retention", choices=("ephemeral", "pinned"))
    mutate.add_argument("--active", choices=("true", "false"))
    mutate.add_argument("--purge", action="store_true")
    mutate.set_defaults(func=mutate_health)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
