#!/usr/bin/env python3
"""Write a minimal confirmed two-node topology manifest for selftests.

Usage:
    topology_manifest_fixture.py <output-path> [worker-ssh-host]

The manifest is a valid schema-1 roce-full-mesh pair (two rails) whose
rank 0 is local and whose rank 1 SSH endpoint defaults to worker.test.
Addresses use TEST-NET ranges only.
"""

from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.topology_manifest import topology_digest, validate_manifest


def endpoint(hca: str, netdev: str, ip: str) -> dict[str, str]:
    return {"hca": hca, "netdev": netdev, "ip": ip}


def build(worker_ssh_host: str) -> dict[str, object]:
    nodes = [
        {
            "rank": 0,
            "node_id": "fixture-node-0",
            "hostname": "fixture-head",
            "ssh_host": "local",
            "control": {"interface": "admin0", "ip": "192.0.2.10"},
            "gpu": "NVIDIA GB10",
            "rdma": [
                {"hca": "a0x", "netdev": "data0x", "cidrs": ["198.51.100.1/24"]},
                {"hca": "a0y", "netdev": "data0y", "cidrs": ["203.0.113.1/24"]},
            ],
        },
        {
            "rank": 1,
            "node_id": "fixture-node-1",
            "hostname": "fixture-worker",
            "ssh_host": worker_ssh_host,
            "control": {"interface": "admin1", "ip": "192.0.2.11"},
            "gpu": "NVIDIA GB10",
            "rdma": [
                {"hca": "b0x", "netdev": "peer0x", "cidrs": ["198.51.100.2/24"]},
                {"hca": "b0y", "netdev": "peer0y", "cidrs": ["203.0.113.2/24"]},
            ],
        },
    ]
    links = [
        {
            "ranks": [0, 1],
            "rails": [
                {
                    "network": "198.51.100.0/24",
                    "a": endpoint("a0x", "data0x", "198.51.100.1"),
                    "b": endpoint("b0x", "peer0x", "198.51.100.2"),
                },
                {
                    "network": "203.0.113.0/24",
                    "a": endpoint("a0y", "data0y", "203.0.113.1"),
                    "b": endpoint("b0y", "peer0y", "203.0.113.2"),
                },
            ],
        },
    ]
    topology: dict[str, object] = {
        "schema_version": 1,
        "generated_at": "2026-08-07T00:00:00+00:00",
        "nodes": nodes,
        "links": links,
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": True,
            "connectivity_verified": True,
            "min_rails_per_pair": 2,
        },
    }
    topology["topology_id"] = topology_digest(topology)
    validate_manifest(topology, require_verified=True)
    return topology


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 3:
        print(__doc__, file=sys.stderr)
        return 2
    output = pathlib.Path(argv[1])
    worker_ssh_host = argv[2] if len(argv) == 3 else "worker.test"
    output.write_text(
        json.dumps(build(worker_ssh_host), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
