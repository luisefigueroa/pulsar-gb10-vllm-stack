#!/usr/bin/env python3
"""Deterministic, fully valid download receipts for release-spec tests.

Each receipt is built through the receipt schema owner
(`scripts/model_library_receipt.py`) from the shared fixture inventory, so the
generator can validate it exactly the way it validates a real receipt. Run as
a script to regenerate the committed files under
`scripts/testdata/release-spec-from-profile/receipts/`.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts import model_library_receipt as source_attested  # noqa: E402
from scripts.testlib import model_library_receipt_fixture as fixture  # noqa: E402

RECEIPT_DIR = REPO_ROOT / "scripts" / "testdata" / "release-spec-from-profile" / "receipts"
TOPOLOGY_GENERATION = "d" * 64
FILE_CONTENT = {
    "config.json": fixture.GIT_TEXT.encode("utf-8"),
    "empty.txt": b"",
    "model.safetensors": fixture.LFS_BYTES,
}


def receipt_path(model_id: str) -> pathlib.Path:
    return RECEIPT_DIR / f"{model_id.replace('/', '__')}.json"


def build_fixture_receipt(
    model_id: str, *, profile: str, snapshot_revision: str = fixture.COMMIT
) -> dict[str, object]:
    source = fixture.build_source(model_id=model_id, snapshot_revision=snapshot_revision)
    identity = source_attested.resolve_huggingface_v1_acquisition_identity(
        source=source, profile=profile
    )
    approval = source_attested.build_source_attested_acquisition_approval(
        source=source,
        identity=identity,
        serving_ranks=[0],
        selected_rank=0,
        selection="most-free-space",
        topology_generation=TOPOLOGY_GENERATION,
    )
    files = [
        {
            "path": row["path"],
            "size": row["size"],
            "sha256": hashlib.sha256(FILE_CONTENT[row["path"]]).hexdigest(),
        }
        for row in source["inventory"]
    ]
    files.sort(key=lambda item: item["path"])
    manifest = {
        "schema_version": 1,
        "kind": model_library.SNAPSHOT_MANIFEST_KIND,
        "model_id": model_id,
        "snapshot_revision": source["snapshot_revision"],
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
    }
    manifest["manifest_id"] = model_library.snapshot_manifest_id(manifest)
    return source_attested.build_source_attested_acquisition_receipt(
        source=source,
        identity=identity,
        approval=approval,
        observed_manifest=manifest,
    )


def receipt_bytes(receipt: dict[str, object]) -> bytes:
    return (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")


def profile_model_ids() -> dict[str, str]:
    """Map each distinct MODEL in models/*.conf to the first profile naming it."""
    mapping: dict[str, str] = {}
    for conf in sorted((REPO_ROOT / "models").glob("*.conf")):
        for line in conf.read_text(encoding="utf-8").splitlines():
            if line.startswith("MODEL="):
                model_id = line[len("MODEL="):].strip().strip('"')
                mapping.setdefault(model_id, conf.stem)
                break
    return mapping


def main() -> int:
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    for model_id, profile in sorted(profile_model_ids().items()):
        receipt_path(model_id).write_bytes(
            receipt_bytes(build_fixture_receipt(model_id, profile=profile))
        )
        print(receipt_path(model_id).relative_to(REPO_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
