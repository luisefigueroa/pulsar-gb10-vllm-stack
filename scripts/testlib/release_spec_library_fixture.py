#!/usr/bin/env python3
"""Fixture builder for scripts/selftest-release-spec-library.sh (WP1.4d).

The shell scenario stays arrange-act-assert: it calls this helper for every
fixture it needs and keeps only CLI invocations and assertions.

  arrange REPO STATE                       library, catalog, receipt, topology,
                                           released nano spec, overlay, a
                                           verifiable spec-named view under
                                           hot-spec, paths.json,
                                           home-inventory.json
  rank1-view REPO STATE TOPOLOGY SPEC_ID MODEL REV MANIFEST_JSON
                                           a spec-named ready stamp with no
                                           payload under hot-rank1
  docker-shim PATH SPEC_ID FLAG            docker that reports one managed
                                           container for SPEC_ID while FLAG exists
  purge-docker-shim PATH                   docker whose `ps` lists
                                           FAKE_DOCKER_SHARED_CONF for a
                                           weight-config filter
  record-tool PATH                         python tool that records its argv
"""
from __future__ import annotations

import pathlib
import sys


def cmd_arrange(argv: list[str]) -> None:
    import json
    import os
    import pathlib
    import sys

    repo = pathlib.Path(sys.argv[1])
    root = pathlib.Path(sys.argv[2])
    sys.path.insert(0, str(repo))

    from release_spec import pretty_json_bytes
    from scripts import model_library
    from scripts import model_library_receipt as source_attested
    from scripts.testlib.model_library_receipt_fixture import write_snapshot_hub
    from scripts.testlib.release_spec_start_fixture import (
        write_identity_hot_view,
        write_overlay,
        write_released_nano,
    )
    from scripts.testlib.test_release_consumer import receipt_for
    from scripts.testlib.test_release_spec_generate import NANO, PINNED_IMAGE
    from scripts.testlib.topology_manifest_fixture import build as build_topology
    from scripts.topology_manifest import topology_digest, validate_manifest

    spec, _path = write_released_nano(root / "releases")
    write_overlay(root / "overlay.json")
    receipt = source_attested.validate_source_attested_acquisition_receipt(
        json.loads(receipt_for(spec["identity"]["model_id"]).read_text(encoding="utf-8"))
    )
    hub = root / "durable-home"
    write_snapshot_hub(hub, revision=receipt["snapshot_revision"])
    live = model_library.inspect_live_directory_identity(hub)
    library = root / "library"
    source_attested.write_source_attested_receipt(library, receipt, operation="test")
    source_attested.write_source_attested_home_attachment(
        library,
        receipt=receipt,
        node_id="fixture-node-0",
        durable_home_path=str(hub.resolve()),
        directory_identity={
            "device": live["device"],
            "inode": live["inode"],
            "ctime_ns": live["ctime_ns"],
        },
    )
    topology = build_topology("worker.test")
    validate_manifest(topology)
    topology["topology_id"] = topology_digest(topology)
    (root / "topology.json").write_text(
        json.dumps(topology, indent=2) + "\n", encoding="utf-8"
    )
    inventory = model_library.inspect_hub_inventory(
        hub,
        rank=0,
        node_id="fixture-node-0",
        model_id=spec["identity"]["model_id"],
        revision=receipt["snapshot_revision"],
        allow_empty_files=True,
    )
    catalog = {
        "schema_version": 2,
        "generated_at": "2026-09-02T00:00:00.000Z",
        "topology_id": topology["topology_id"],
        "models": [
            {
                "model_id": spec["identity"]["model_id"],
                "revision": receipt["snapshot_revision"],
                "identity_key": (
                    f"{spec['identity']['model_id']}@{receipt['snapshot_revision']}"
                ),
                "validation": "unvalidated",
                "profiles": [NANO],
                "profile_validation": [],
                "homes": [
                    {
                        "rank": 0,
                        "node_id": "fixture-node-0",
                        "hostname": "fixture-head",
                        "ssh_host": "local",
                        "cache_root": str(root / "cache-0"),
                        "hub_path": str(hub.resolve()),
                        "state": "complete",
                        "home_class": "occupancy",
                        "occupancy": True,
                        "bytes": inventory["bytes_logical"],
                        "primary": True,
                    }
                ],
                "duplicate": False,
                "has_primary": True,
                "primary_selection": {
                    "status": "selected",
                    "node_id": "fixture-node-0",
                    "mode": "automatic-single-home",
                },
            }
        ],
        "primary_selections": [],
    }
    (library / "catalog.json").write_text(
        json.dumps(catalog, indent=2) + "\n", encoding="utf-8"
    )
    manifest = spec["identity"]["snapshot_manifest"]
    # The spec-named view that pin, unpin, and prepare inspect must verify
    # for real: a strict lookup hashes rank 0, so it carries the reviewed
    # manifest's payload and a stamp whose digest and validation match it.
    instance = write_identity_hot_view(
        root / "hot-spec",
        profile=spec["spec_id"],
        topology_id=topology["topology_id"],
        model_id=spec["identity"]["model_id"],
        revision=receipt["snapshot_revision"],
        manifest=manifest,
        content_id="specview0001",
    )
    make_view_verifiable(
        instance,
        hub_source=hub,
        profile=spec["spec_id"],
        model_id=spec["identity"]["model_id"],
        manifest=manifest,
    )
    rank1 = json.loads(json.dumps(catalog))
    rank1["models"][0]["homes"][0]["rank"] = 1
    rank1["models"][0]["homes"][0]["node_id"] = "fixture-node-1"
    rank1["models"][0]["homes"][0]["hostname"] = "fixture-worker"
    rank1["models"][0]["primary_selection"]["node_id"] = "fixture-node-1"
    (library / "catalog-rank1.json").write_text(
        json.dumps(rank1, indent=2) + "\n", encoding="utf-8"
    )
    (root / "paths.json").write_text(
        json.dumps(
            {
                "spec_id": spec["spec_id"],
                "model_id": spec["identity"]["model_id"],
                "revision": receipt["snapshot_revision"],
                "manifest_id": manifest["manifest_id"],
                "manifest": manifest,
                "inventory": inventory,
                "topology_id": topology["topology_id"],
                "image": PINNED_IMAGE,
                "identity_key": (
                    f"{spec['identity']['model_id']}@{receipt['snapshot_revision']}"
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "home-inventory.json").write_text(
        json.dumps(inventory) + "\n", encoding="utf-8"
    )


def make_view_verifiable(instance, *, hub_source, profile, model_id, manifest) -> None:
    """Give a stub view the payload and stamp fields verify-hot checks."""
    import shutil

    from scripts import model_library

    stamp = model_library.load_hot_stamp(instance)
    stamp["validation"] = model_library.require_activation_identity(
        {"profile": profile, "model_id": model_id},
        manifest,
        allow_unvalidated=False,
    )
    stamp["content_digest"] = manifest["manifest_id"]
    stamp["bytes_logical"] = manifest["total_bytes"]
    model_library.write_hot_stamp(instance, stamp)
    hub_dest = model_library.hot_hub_path(instance, model_id)
    if not hub_dest.exists():
        shutil.copytree(hub_source, hub_dest, symlinks=True)




def cmd_rank1_view(argv: list[str]) -> None:
    import json, pathlib, sys
    sys.path.insert(0, sys.argv[1])
    from scripts.testlib.release_spec_start_fixture import write_identity_hot_view
    manifest = json.loads(sys.argv[7])
    path = write_identity_hot_view(
        pathlib.Path(sys.argv[2]) / "hot-rank1",
        profile=sys.argv[4],
        topology_id=sys.argv[3],
        model_id=sys.argv[5],
        revision=sys.argv[6],
        manifest=manifest,
        content_id="rank1view0001",
    )
    print(path)




def cmd_docker_shim(argv: list[str]) -> None:
    from pathlib import Path
    import sys
    spec_id = sys.argv[2]
    flag = sys.argv[3]
    Path(flag).write_text("1\n", encoding="utf-8")
    Path(sys.argv[1]).write_text(
        f"""#!/usr/bin/env bash
    set -euo pipefail
    case "${{1:-}}" in
      info) exit 0 ;;
      inspect)
        [ -f {flag!r} ] || exit 1
        printf '%s\\n' '{{"id":"{'a'*64}","name":"/vllm-{spec_id}","labels":{{"io.pulsar.gb10.managed":"true","io.pulsar.gb10.conf":"{spec_id}","io.pulsar.gb10.rank":"single","io.pulsar.gb10.weight-source":"local-files"}}}}'
        exit 0
        ;;
      rm)
        rm -f {flag!r}
        exit 0
        ;;
      ps) exit 0 ;;
      *) exit 0 ;;
    esac
    """,
        encoding="utf-8",
    )
    pathlib.Path(sys.argv[1]).chmod(0o755)


def cmd_purge_docker_shim(argv: list[str]) -> None:
    path = pathlib.Path(argv[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [ "${1:-}" = ps ]; then
  # A stopped-but-present container is only visible with --all; the guard
  # must ask for the view's own name (conf label) as well as its content id,
  # so the sharer is reported only when both filters name it.
  case "$*" in
    *--all*weight-config=*conf="${FAKE_DOCKER_SHARED_CONF:-__none__}"*) printf '%s\\n' "$FAKE_DOCKER_SHARED_CONF" ;;
  esac
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def cmd_record_tool(argv: list[str]) -> None:
    path = pathlib.Path(argv[0])
    path.write_text(
        """import os, sys
with open(os.environ["VERIFY_ARGS_LOG"], "a", encoding="utf-8") as fh:
    fh.write("\\n".join(sys.argv[1:]) + "\\n--END--\\n")
""",
        encoding="utf-8",
    )


COMMANDS = {
    "arrange": cmd_arrange,
    "rank1-view": cmd_rank1_view,
    "docker-shim": cmd_docker_shim,
    "purge-docker-shim": cmd_purge_docker_shim,
    "record-tool": cmd_record_tool,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__, file=sys.stderr)
        return 2
    command, argv = sys.argv[1], sys.argv[2:]
    # The command bodies were written as scripts reading sys.argv[1:].
    sys.argv = [command, *argv]
    COMMANDS[command](argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
