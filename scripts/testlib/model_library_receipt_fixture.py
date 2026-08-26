#!/usr/bin/env python3
"""Builders for download-receipt acquisition unit and CLI tests."""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity, model_library  # noqa: E402
from scripts import model_library_receipt as source_attested  # noqa: E402
from scripts.topology_manifest import topology_digest  # noqa: E402


COMMIT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
GIT_TEXT = '{"ok":true}\n'
LFS_BYTES = b"lfs-weight-bytes-fixture"


def write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o700)


def git_oid(data: bytes) -> str:
    return source_attested.git_blob_oid(data)


def lfs_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def inventory_rows() -> list[dict[str, object]]:
    return [
        source_attested.normalize_huggingface_v1_inventory_entry(
            path="config.json",
            size=len(GIT_TEXT.encode("utf-8")),
            blob_kind=source_attested.HF_V1_BLOB_GIT,
            git_oid=git_oid(GIT_TEXT.encode("utf-8")),
        ),
        source_attested.normalize_huggingface_v1_inventory_entry(
            path="empty.txt",
            size=0,
            blob_kind=source_attested.HF_V1_BLOB_GIT,
            git_oid=git_oid(b""),
        ),
        source_attested.normalize_huggingface_v1_inventory_entry(
            path="model.safetensors",
            size=len(LFS_BYTES),
            blob_kind=source_attested.HF_V1_BLOB_LFS,
            sha256=lfs_sha256(LFS_BYTES),
        ),
    ]


def build_source(
    *,
    model_id: str = "Fixture/Unbound-Model",
    selector: str = "main",
    snapshot_revision: str = COMMIT,
    inventory: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return source_attested.build_huggingface_v1_acquisition_source(
        model_id=model_id,
        selector=selector,
        snapshot_revision=snapshot_revision,
        inventory=inventory or inventory_rows(),
    )


def repo_info_payload(source: dict[str, object]) -> dict[str, object]:
    return {"id": source["model_id"], "sha": source["snapshot_revision"]}


def repo_tree_payload(source: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in source["inventory"]:
        if item["blob_kind"] == source_attested.HF_V1_BLOB_GIT:
            rows.append(
                {
                    "type": "file",
                    "path": item["path"],
                    "oid": item["git_oid"],
                    "size": item["size"],
                }
            )
        else:
            rows.append(
                {
                    "type": "file",
                    "path": item["path"],
                    "oid": "b" * 40,
                    "size": item["size"],
                    "lfs": {"sha256": item["sha256"], "size": item["size"]},
                }
            )
    return rows


def write_snapshot_hub(hub: pathlib.Path, *, revision: str = COMMIT) -> None:
    snapshot = hub / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text(GIT_TEXT, encoding="utf-8")
    (snapshot / "empty.txt").write_bytes(b"")
    (snapshot / "model.safetensors").write_bytes(LFS_BYTES)
    refs = hub / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text(revision + "\n", encoding="utf-8")


def observation(
    cache_root: pathlib.Path,
    *,
    rank: int,
    node_id: str,
    model_id: str,
    revision: str,
    content_bytes: int,
    available_bytes: int,
    hf_cli: str = "hf",
    target_state: str | None = None,
) -> dict[str, object]:
    row = model_library.inspect_home_acquisition_target(
        cache_root,
        model_id=model_id,
        revision=revision,
        required_content_bytes=content_bytes,
        rank=rank,
        node_id=node_id,
        hf_cli=hf_cli,
    )
    if target_state is not None:
        row["target_state"] = target_state
        row["eligible"] = target_state == "absent" and row.get("eligible")
    row["available_bytes"] = available_bytes
    return row


def write_topology(path: pathlib.Path, ranks: int = 2) -> dict[str, object]:
    topology = {
        "schema_version": 1,
        "nodes": [
            {
                "rank": rank,
                "node_id": f"node-{rank}",
                "hostname": f"fixture-{rank}",
                "ssh_host": "local" if rank == 0 else f"fixture-{rank}",
                "control": {"interface": "mgmt0", "ip": f"192.0.2.{10 + rank}"},
                "gpu": "NVIDIA GB10",
                "rdma": (
                    [
                        {
                            "hca": "roce0",
                            "netdev": "fabric0",
                            "cidrs": [f"198.51.100.{10 + rank}/24"],
                        }
                    ]
                    if ranks > 1
                    else []
                ),
            }
            for rank in range(ranks)
        ],
        "links": (
            [
                {
                    "ranks": [0, 1],
                    "rails": [
                        {
                            "network": "198.51.100.0/24",
                            "a": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": "198.51.100.10",
                            },
                            "b": {
                                "hca": "roce0",
                                "netdev": "fabric0",
                                "ip": "198.51.100.11",
                            },
                        }
                    ],
                }
            ]
            if ranks == 2
            else []
        ),
        "validation": {
            "class": "roce-full-mesh",
            "full_mesh": True,
            "connectivity_verified": True,
            "min_rails_per_pair": 1 if ranks > 1 else 0,
        },
    }
    topology["topology_id"] = topology_digest(topology)
    path.write_text(json.dumps(topology), encoding="utf-8")
    return topology


def write_cli_fixture(root: pathlib.Path, *, ranks: int = 1) -> dict[str, object]:
    """Create a one-rank public-CLI fixture for an unsealed Hugging Face profile."""
    root.mkdir(parents=True, exist_ok=True)
    model_id = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
    source = build_source(model_id=model_id, selector="main")
    info = repo_info_payload(source)
    tree = repo_tree_payload(source)
    topology = write_topology(root / "topology.json", ranks=ranks)
    (root / "library").mkdir()
    cache = root / "cache"
    cache.mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "hf",
        f"""#!{sys.executable}
import os
import pathlib
import sys

args = sys.argv[1:]
with open(os.environ["MOCK_HF_LOG"], "a", encoding="utf-8") as handle:
    handle.write(" ".join(args) + "\\n")
if args and args[0] == "download":
    cache_dir = args[args.index("--cache-dir") + 1]
    revision = args[args.index("--revision") + 1]
    hub = pathlib.Path(cache_dir) / "models--nvidia--NVIDIA-Nemotron-3-Nano-30B-A3B-NVFP4"
    snapshot = hub / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    (hub / "refs").mkdir(parents=True, exist_ok=True)
    (snapshot / "config.json").write_text('{{"ok":true}}\\n', encoding="utf-8")
    (snapshot / "empty.txt").write_bytes(b"")
    (snapshot / "model.safetensors").write_bytes(b"lfs-weight-bytes-fixture")
    (hub / "refs" / "main").write_text(revision + "\\n", encoding="utf-8")
    raise SystemExit(0)
if args[:2] == ["cache", "verify"]:
    raise SystemExit(0)
print("unexpected hf invocation: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
""",
    )
    write_executable(
        bin_dir / "hf-source-inventory.py",
        f"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
fail_cache = os.environ.get("MOCK_HF_INVENTORY_FAIL_IF_CACHE", "")
if fail_cache and os.environ.get("HF_CACHE") == fail_cache:
    raise SystemExit(1)
with open(os.environ["MOCK_HF_LOG"], "a", encoding="utf-8") as handle:
    handle.write("source-inventory " + " ".join(args) + "\\n")
print(json.dumps({json.dumps({"id": info["id"], "sha": info["sha"], "siblings": tree})}))
""",
    )
    if ranks > 1:
        remote_cache = root / "cache-1"
        remote_cache.mkdir()
        write_executable(
            bin_dir / "ssh",
            """#!/usr/bin/env bash
set -euo pipefail
command=${!#}
printf '%s\n' "$command" >>"$MOCK_SSH_LOG"
export HF_CACHE="$MOCK_REMOTE_HF_CACHE"
export HOME="$MOCK_REMOTE_HOME"
exec bash -c "$command"
""",
        )
    return {
        "model_id": model_id,
        "source": source,
        "topology_id": topology["topology_id"],
        "commit": source["snapshot_revision"],
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: model_library_receipt_fixture.py ROOT")
    write_cli_fixture(pathlib.Path(sys.argv[1]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
