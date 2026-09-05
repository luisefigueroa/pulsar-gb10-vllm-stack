#!/usr/bin/env python3
"""Write a small set of released fixture specs plus an overlay for selftests.

  python3 scripts/testlib/release_spec_fixture_set.py DIR

Creates DIR/releases/<spec_id>.json for a one-node spec (the released nano
fixture, review stable) and a two-node spec (the two-node Qwen fixture,
review stable), DIR/overlay.json (served name ``fixture-served``, port
8000), and DIR/ids.json naming them as ``one_node`` and ``two_node`` with
their model ids. Selftests that used to load a conf by name load these ids
under PULSAR_RELEASES_ROOT and PULSAR_OVERLAY_PATH instead.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from release_spec import build_snapshot_manifest, pretty_json_bytes, spec_id_for, verify_spec  # noqa: E402
from scripts.release_spec_generate import build_spec_from_profile  # noqa: E402
from scripts.testlib.release_spec_start_fixture import write_overlay, write_released_nano  # noqa: E402
from scripts.testlib.test_release_spec_generate import (  # noqa: E402
    PINNED_IMAGE,
    STACK_VERSION,
    TWO_NODE,
    draft_path,
    model_id_for,
    receipt_for,
)

SERVED_NAME = "fixture-served"
ONE_NODE_QWEN = "qwen3.8-27b-fp8"
DIAGNOSTIC_TWO_NODE = "qwen3-1.7b-2node"


def _draft_fields(profile: str) -> dict[str, object]:
    """Recipe fields of a fixture draft (the small declarative subset)."""
    text = draft_path(profile).read_text(encoding="utf-8")
    fields: dict[str, object] = {"engine_args": [], "container_env": [], "gpu_mem_util": "0.80", "nodes": 1}
    block: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if block is not None:
            if line == ")":
                block = None
                continue
            for token in line.split():
                fields_key = "engine_args" if block == "ENGINE_ARGS" else "container_env"
                fields[fields_key].append(token.strip('"'))  # type: ignore[union-attr]
            continue
        if line.startswith("ENGINE_ARGS=("):
            block = "ENGINE_ARGS"; continue
        if line.startswith("CONTAINER_ENV=("):
            block = "CONTAINER_ENV"
            if line.endswith(")"):
                block = None
            continue
        if line.startswith("GPU_MEM_UTIL="):
            fields["gpu_mem_util"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("NODES="):
            fields["nodes"] = int(line.split("=", 1)[1].strip().strip('"'))
    return fields


def _released_like(template: dict, spec: dict) -> dict:
    document = json.loads(pretty_json_bytes(spec))
    document["state"] = "released"
    for key in ("measurements", "evidence", "baselines", "review"):
        document[key] = template[key]
    return verify_spec(document)


def write_released_variant(
    releases: pathlib.Path,
    *,
    model_id: str,
    nodes: int = 1,
    revision: str = "d" * 40,
    files: list[dict] | None = None,
) -> str:
    """Write one more released spec (a copy of the nano fixture under another
    model identity) and return its spec id. For selftests that need a profile
    for a model the standard set does not cover."""
    template, _path = write_released_nano(releases)
    document = json.loads(pretty_json_bytes(template))
    identity = document["identity"]
    identity["model_id"] = model_id
    identity["snapshot_revision"] = revision
    identity["geometry"]["nodes"] = nodes
    if nodes > 1:
        identity["geometry"]["tensor_parallel_size"] = nodes
    identity["snapshot_manifest"] = build_snapshot_manifest(
        model_id=model_id,
        snapshot_revision=revision,
        files=identity["snapshot_manifest"]["files"] if files is None else files,
    )
    document["spec_id"] = spec_id_for(identity)
    spec = verify_spec(document)
    (releases / f"{spec['spec_id']}.json").write_bytes(pretty_json_bytes(spec))
    return spec["spec_id"]


def write_fixture_set(root: pathlib.Path) -> dict[str, dict[str, str]]:
    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    nano, _path = write_released_nano(releases)
    two, report = build_spec_from_profile(
        profile=TWO_NODE,
        model_id=model_id_for(TWO_NODE),
        image=PINNED_IMAGE,
        nodes=2,
        gpu_mem_util="0.80",
        engine_args=[
            "--max-model-len", "131072", "--max-num-seqs", "16",
            "--tensor-parallel-size", "2", "--distributed-executor-backend", "mp",
        ],
        container_env=[],
        spec_decode_args=[],
        platform_id="dgx-spark-gb10",
        stack_version=STACK_VERSION,
        spec_decode=False,
        receipt_path=receipt_for(model_id_for(TWO_NODE)),
        repo_root=ROOT,
    )
    if two is None:
        raise SystemExit(f"two-node fixture spec did not generate: {report}")
    two_spec = _released_like(nano, two)
    (releases / f"{two_spec['spec_id']}.json").write_bytes(pretty_json_bytes(two_spec))
    qwen_fields = _draft_fields(ONE_NODE_QWEN)
    qwen, report = build_spec_from_profile(
        profile=ONE_NODE_QWEN,
        model_id=model_id_for(ONE_NODE_QWEN),
        image=PINNED_IMAGE,
        nodes=int(qwen_fields["nodes"]),
        gpu_mem_util=str(qwen_fields["gpu_mem_util"]),
        engine_args=list(qwen_fields["engine_args"]),  # type: ignore[arg-type]
        container_env=list(qwen_fields["container_env"]),  # type: ignore[arg-type]
        spec_decode_args=[],
        platform_id="dgx-spark-gb10",
        stack_version=STACK_VERSION,
        spec_decode=False,
        receipt_path=receipt_for(model_id_for(ONE_NODE_QWEN)),
        repo_root=ROOT,
    )
    if qwen is None:
        raise SystemExit(f"one-node qwen fixture spec did not generate: {report}")
    qwen_spec = _released_like(nano, qwen)
    (releases / f"{qwen_spec['spec_id']}.json").write_bytes(pretty_json_bytes(qwen_spec))
    small_fields = _draft_fields(DIAGNOSTIC_TWO_NODE)
    small, report = build_spec_from_profile(
        profile=DIAGNOSTIC_TWO_NODE,
        model_id=model_id_for(DIAGNOSTIC_TWO_NODE),
        image=PINNED_IMAGE,
        nodes=int(small_fields["nodes"]),
        gpu_mem_util=str(small_fields["gpu_mem_util"]),
        engine_args=list(small_fields["engine_args"]),  # type: ignore[arg-type]
        container_env=list(small_fields["container_env"]),  # type: ignore[arg-type]
        spec_decode_args=[],
        platform_id="dgx-spark-gb10",
        stack_version=STACK_VERSION,
        spec_decode=False,
        receipt_path=receipt_for(model_id_for(DIAGNOSTIC_TWO_NODE)),
        repo_root=ROOT,
    )
    if small is None:
        raise SystemExit(f"diagnostic two-node fixture spec did not generate: {report}")
    small_spec = _released_like(nano, small)
    (releases / f"{small_spec['spec_id']}.json").write_bytes(pretty_json_bytes(small_spec))
    write_overlay(root / "overlay.json", served_name=SERVED_NAME)
    ids = {
        "one_node": {"spec_id": nano["spec_id"], "model_id": nano["identity"]["model_id"], "served_name": SERVED_NAME},
        "two_node": {"spec_id": two_spec["spec_id"], "model_id": two_spec["identity"]["model_id"], "served_name": SERVED_NAME},
        "one_node_qwen": {"spec_id": qwen_spec["spec_id"], "model_id": qwen_spec["identity"]["model_id"], "served_name": SERVED_NAME},
        "diagnostic_two_node": {"spec_id": small_spec["spec_id"], "model_id": small_spec["identity"]["model_id"], "served_name": SERVED_NAME},
        "image": PINNED_IMAGE,
    }
    (root / "ids.json").write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
    return ids


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    ids = write_fixture_set(pathlib.Path(args[0]))
    print(json.dumps(ids))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
