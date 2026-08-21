#!/usr/bin/env python3
"""Write a canned find-hot info JSON for fake_model_library.py.

The shape mirrors model_library.py find-hot output closely enough for
lib.sh resolve_library_hot_for_profile: instance_dir, hub_path,
container_model_path, and a stamp carrying provenance plus validation.
Values default to a legacy-unsealed two-node fixture; every field a test
cares about is overridable.
"""

from __future__ import annotations

import argparse
import json
import pathlib


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output")
    parser.add_argument("--profile", default="qwen3-1.7b-2node")
    parser.add_argument("--model-id", default="Qwen/Qwen3-1.7B")
    parser.add_argument("--topology-id", default="f" * 64)
    parser.add_argument("--home-node-id", default="node-fixture-home")
    parser.add_argument("--revision", default="d" * 40)
    parser.add_argument(
        "--identity-status",
        default="legacy-unsealed",
        choices=("match", "legacy-unsealed", "unvalidated"),
    )
    parser.add_argument("--seal-id", default="")
    parser.add_argument("--bundle-id", default="")
    parser.add_argument("--hot-root", default="/var/tmp/pulsar-hot")
    parser.add_argument("--pinned", action="store_true")
    args = parser.parse_args()

    hub_dirname = "models--" + args.model_id.replace("/", "--")
    instance_dir = (
        f"{args.hot_root}/{args.profile}-{args.topology_id[:12]}/" + "c" * 12
    )
    hub_path = f"{instance_dir}/{hub_dirname}"
    validation: dict[str, object] = {"identity_status": args.identity_status}
    if args.identity_status == "match":
        validation["expected_seal"] = {
            "seal_id": args.seal_id or "e" * 64,
            "validation_bundle_id": args.bundle_id or "f" * 64,
        }
    info = {
        "instance_dir": instance_dir,
        "hub_path": hub_path,
        "container_model_path": (
            f"/root/.cache/huggingface/hub/{hub_dirname}/snapshots/"
            f"{args.revision}"
        ),
        "stamp": {
            "profile": args.profile,
            "model_id": args.model_id,
            "topology_id": args.topology_id,
            "home_node_id": args.home_node_id,
            "content_id": "c" * 12,
            "content_digest": "a" * 64,
            "transport": "ssh-roce",
            "integrity": {"scheme": "sha256-snapshot-manifest-v1"},
            "revision": args.revision,
            "validation": validation,
            "pinned": bool(args.pinned),
            "state": "ready",
        },
    }
    pathlib.Path(args.output).write_text(
        json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
