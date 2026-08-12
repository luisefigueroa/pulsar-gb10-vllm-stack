#!/usr/bin/env python3
"""Create sanitized model-storage health fixtures for shell scenarios."""

from __future__ import annotations

import copy
import json
import pathlib
import sys


REVISION = "7" * 40
MANIFEST = "8" * 64


def write(path: pathlib.Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def healthy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-health",
        "state": "healthy",
        "catalog": {
            "status": "cached",
            "topology_compatible": True,
            "refreshed_at": "2026-08-12T12:00:00.000Z",
        },
        "models": [{
            "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "revision": REVISION,
            "profiles": ["deepseek-v4-flash"],
            "expected_manifest": MANIFEST,
            "validation": "expected-unverified",
            "home_ranks": [1],
            "primary": {
                "mode": "automatic-single-home",
                "status": "match",
                "rank": 1,
            },
            "duplicate_home": "none",
        }],
        "hot_instances": [
            {
                "rank": 0,
                "profile": "deepseek-v4-flash",
                "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "sealed-hot",
                "retention": "ephemeral",
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": False,
                "repairable": False,
                "repair_id": None,
            },
            {
                "rank": 1,
                "profile": "deepseek-v4-flash",
                "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "durable-home",
                "retention": "ephemeral",
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": False,
                "repairable": False,
                "repair_id": None,
            },
        ],
        "issues": [],
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: model_storage_fixture.py ROOT")
    root = pathlib.Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)

    base = healthy()
    write(root / "healthy.json", base)

    refreshed = json.loads(json.dumps(base))
    refreshed["catalog"]["refreshed_at"] = "2026-08-12T13:00:00.000Z"
    write(root / "refreshed.json", refreshed)

    attention = json.loads(json.dumps(base))
    attention["state"] = "attention"
    attention["catalog"]["topology_compatible"] = False
    attention["hot_instances"][0]["witness_status"] = "drift"
    attention["issues"] = [
        {
            "code": "catalog-topology-stale",
            "detail": "cached catalog differs from confirmed topology",
            "remediation": {
                "command": "scripts/model-library.sh catalog refresh",
            },
        },
        {
            "code": "witness-not-current",
            "rank": 0,
            "detail": "serve witness is missing, malformed, or drifted",
        },
    ]
    write(root / "attention.json", attention)

    collision = healthy()
    first = collision["models"][0]
    first["model_id"] = "example/first-model"
    first["profiles"] = [
        "very-long-model-profile-with-identical-prefix-alpha"
    ]
    first["revision"] = "7" * 40
    second = copy.deepcopy(first)
    second["model_id"] = "example/second-model"
    second["profiles"] = [
        "very-long-model-profile-with-identical-prefix-beta"
    ]
    second["revision"] = "9" * 40
    collision["models"] = [first, second]
    collision["hot_instances"] = []
    write(root / "collision.json", collision)

    write(root / "not-configured.json", {
        "schema_version": 1,
        "kind": "pulsar-model-library-health",
        "state": "not-configured",
        "catalog": {
            "status": "absent",
            "topology_compatible": None,
        },
        "models": [],
        "hot_instances": [],
        "issues": [],
    })

    write(root / "unavailable.json", {
        "schema_version": 1,
        "kind": "pulsar-model-library-health",
        "state": "unavailable",
        "catalog": {
            "status": "unavailable",
            "topology_compatible": None,
        },
        "models": [],
        "hot_instances": [],
        "issues": [{
            "code": "observation-unavailable",
            "detail": "confirmed topology or rank observations are invalid",
        }],
    })

    (root / "invalid.json").write_text("not-json\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
