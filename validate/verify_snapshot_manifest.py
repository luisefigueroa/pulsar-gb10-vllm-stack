#!/usr/bin/env python3
"""Verify a served snapshot tree against a release spec's snapshot manifest.

  validate/verify_snapshot_manifest.py --spec FILE --hub DIR \
      --result-json OUT [--workers N]

The spec's ``identity.snapshot_manifest`` is the reviewed expected file list
(ADR 0017 decision 6). Every file beneath ``<hub>/snapshots/<revision>`` is
sized and hashed and compared with that list. The closed measurement records
counts only: expected, matched, mismatched, missing, and extra files. It
names no path, assigns no status, and never modifies the tree.

Exit 0 when the walk completed and every expected file matched, 1 when the
walk completed but the tree differs, and 2 when the tree could not be
verified or the arguments are unusable. A measurement is written whenever
the spec itself loads.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_VALIDATE_DIR = Path(__file__).resolve().parent
if str(_VALIDATE_DIR) not in sys.path:
    sys.path.insert(0, str(_VALIDATE_DIR))

from release_spec import ReleaseSpecError, load_spec  # noqa: E402
from scripts import model_library  # noqa: E402

from validator_measurement import (  # noqa: E402
    ValidatorMeasurementError,
    build_identity_measurement,
    write_measurement,
)

MAX_WORKERS = 16


def zero_counts() -> dict[str, int]:
    return {
        "matched_file_count": 0,
        "mismatched_file_count": 0,
        "missing_file_count": 0,
        "extra_file_count": 0,
    }


def compare_tree(
    hub: Path, manifest: dict[str, Any], *, workers: int
) -> dict[str, int]:
    """Return matched, mismatched, missing, and extra counts for one tree.

    Raises when the snapshot cannot be walked or a file cannot be read; the
    caller records that as an incomplete measurement.
    """
    _revision, actual_files = model_library.iter_snapshot_files(
        hub, revision=manifest["snapshot_revision"]
    )
    actual = dict(actual_files)
    expected = {item["path"]: item for item in manifest["files"]}
    common = sorted(set(expected) & set(actual))
    counts = zero_counts()
    counts["missing_file_count"] = len(set(expected) - set(actual))
    counts["extra_file_count"] = len(set(actual) - set(expected))
    to_hash: list[tuple[str, Path, int]] = []
    for relative in common:
        size = actual[relative].stat().st_size
        if size != expected[relative]["size"]:
            counts["mismatched_file_count"] += 1
            continue
        to_hash.append((relative, actual[relative], size))

    def hash_one(entry: tuple[str, Path, int]) -> tuple[str, str]:
        relative, resolved, size = entry
        return relative, model_library.sha256_file(resolved, expected_size=size)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        digests = dict(pool.map(hash_one, to_hash))
    for relative, _resolved, _size in to_hash:
        if digests[relative] == expected[relative]["sha256"]:
            counts["matched_file_count"] += 1
        else:
            counts["mismatched_file_count"] += 1
    return counts


def unmatched(counts: dict[str, int]) -> int:
    return (
        counts["mismatched_file_count"]
        + counts["missing_file_count"]
        + counts["extra_file_count"]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--spec", required=True, help="measured release spec")
    parser.add_argument(
        "--hub", required=True, help="hub directory holding snapshots/<revision>"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"hash workers, 1-{MAX_WORKERS} (default: library setting)",
    )
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args(argv)
    workers = (
        model_library.default_integrity_workers()
        if args.workers is None
        else args.workers
    )
    if workers < 1 or workers > MAX_WORKERS:
        parser.error(f"--workers must be between 1 and {MAX_WORKERS}")

    try:
        spec = load_spec(args.spec)
    except ReleaseSpecError as exc:
        print(f"spec is unusable: {exc}", file=sys.stderr)
        return 2
    manifest = spec["identity"]["snapshot_manifest"]

    counts = zero_counts()
    completion = "incomplete"
    reason = "mismatch"
    try:
        counts = compare_tree(Path(args.hub), manifest, workers=workers)
        completion = "complete"
        reason = "completed"
    except KeyboardInterrupt:
        reason = "interrupted"
        print("snapshot verification interrupted", file=sys.stderr)
    except Exception as exc:  # preserve a closed incomplete measurement
        print(
            f"snapshot tree could not be verified: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    payload = {
        "spec_id": spec["spec_id"],
        "manifest_id": manifest["manifest_id"],
        "expected_file_count": manifest["file_count"],
        **counts,
    }
    try:
        document = build_identity_measurement(
            completion=completion, reason=reason, payload=payload
        )
        write_measurement(args.result_json, document)
    except ValidatorMeasurementError as exc:
        print(f"measurement could not be written: {exc}", file=sys.stderr)
        return 2
    print(
        f"snapshot manifest {manifest['manifest_id'][:12]} {completion}: "
        f"expected={payload['expected_file_count']} "
        f"matched={counts['matched_file_count']} "
        f"mismatched={counts['mismatched_file_count']} "
        f"missing={counts['missing_file_count']} "
        f"extra={counts['extra_file_count']}"
    )
    if completion != "complete":
        return 2
    return 0 if unmatched(counts) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
