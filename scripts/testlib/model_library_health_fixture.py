#!/usr/bin/env python3
"""Create role-driven, disposable fixtures for model-library health shell tests."""

from __future__ import annotations

import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.topology_manifest import topology_digest  # noqa: E402


def write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def legacy_stamp(topology_id: str) -> dict[str, object]:
    revision = "a" * 40
    return {
        "schema_version": 1,
        "state": "ready",
        "profile": "fixture-health",
        "model_id": "Fixture/Health",
        "revision": revision,
        "identity_key": f"Fixture/Health@{revision}",
        "home_node_id": "fixture-owner-id",
        "topology_id": topology_id,
        "content_id": "content",
        "content_digest": "b" * 64,
        "backend": "copy",
        "bytes_logical": 8,
        "activated_at": "2026-01-01T00:00:00.000Z",
        "pinned": False,
        "budget_bytes_accounted": 8,
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: model_library_health_fixture.py ROOT")
    root = pathlib.Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    topology = {
        "schema_version": 1,
        "nodes": [
            {
                "rank": 0,
                "node_id": "fixture-owner-id",
                "hostname": "fixture-owner",
                "ssh_host": "local",
                "control": {"interface": "lan0", "ip": "192.0.2.10"},
                "gpu": "NVIDIA GB10",
                "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.10/24"]}],
            },
            {
                "rank": 1,
                "node_id": "fixture-client-id",
                "hostname": "fixture-client",
                "ssh_host": "fixture-client.test",
                "control": {"interface": "lan0", "ip": "192.0.2.11"},
                "gpu": "NVIDIA GB10",
                "rdma": [{"hca": "roce0", "netdev": "fabric0", "cidrs": ["198.51.100.11/24"]}],
            },
        ],
        "links": [{
            "ranks": [0, 1],
            "rails": [{
                "network": "198.51.100.0/24",
                "a": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.10"},
                "b": {"hca": "roce0", "netdev": "fabric0", "ip": "198.51.100.11"},
            }],
        }],
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": True,
            "connectivity_verified": True,
            "min_rails_per_pair": 1,
        },
    }
    topology["topology_id"] = topology_digest(topology)
    (root / "topology.json").write_text(json.dumps(topology), encoding="utf-8")

    hot_roots = {
        "fabric-owner": root / "hot-owner",
        "fabric-client": root / "hot-client",
    }
    for hot_root in hot_roots.values():
        instance = hot_root / "fixture-health-topology" / "content"
        metadata = instance / ".pulsar"
        metadata.mkdir(parents=True)
        (metadata / "hot.json").write_text(
            json.dumps(legacy_stamp(topology["topology_id"])), encoding="utf-8"
        )
        (instance.parent / "sibling").mkdir()
    external = root / "external"
    external.mkdir()
    (external / "sentinel").write_text("preserve", encoding="utf-8")
    owner_instance = hot_roots["fabric-owner"] / "fixture-health-topology" / "content"
    (owner_instance / "external-link").symlink_to(external, target_is_directory=True)

    bin_dir = root / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  info) exit 0 ;;
  ps) exit 0 ;;
  inspect) exit 3 ;;
  *) exit 1 ;;
esac
""",
    )
    write_executable(
        root / "ssh",
        """#!/usr/bin/env bash
set -euo pipefail
args=("$@")
command="${args[$((${#args[@]} - 1))]}"
command="${command//${MOCK_OWNER_HOT_ROOT:?}/${MOCK_CLIENT_HOT_ROOT:?}}"
PATH="${MOCK_BIN:?}:$PATH" exec bash -c "$command"
""",
    )
    write_executable(
        root / "doctor",
        """#!/usr/bin/env bash
set -euo pipefail
printf 'doctor-routed:%s\\n' "$*"
""",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
