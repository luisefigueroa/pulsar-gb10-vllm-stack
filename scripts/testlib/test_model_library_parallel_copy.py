#!/usr/bin/env python3
"""Contracts for deterministic parallel model-library blob transfer plans."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library


class ParallelCopyPlanContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.hub = self.root / "hub"
        self.blobs = self.hub / "blobs"
        self.blobs.mkdir(parents=True)
        for name, size in {
            "a": 10,
            "b": 8,
            "c": 7,
            "d": 6,
            "e": 5,
        }.items():
            (self.blobs / name).write_bytes(b"x" * size)

    def test_greedy_partition_is_balanced_and_deterministic(self) -> None:
        first = model_library.partition_blob_files(self.hub, streams=3)
        second = model_library.partition_blob_files(self.hub, streams=3)

        self.assertEqual(first, second)
        self.assertEqual(first["effective_streams"], 3)
        self.assertEqual(first["total_bytes"], 36)
        self.assertNotIn("hub_path", first)
        self.assertEqual(
            first["groups"],
            [
                {"stream": 0, "bytes": 10, "files": ["blobs/a"]},
                {
                    "stream": 1,
                    "bytes": 13,
                    "files": ["blobs/b", "blobs/e"],
                },
                {
                    "stream": 2,
                    "bytes": 13,
                    "files": ["blobs/c", "blobs/d"],
                },
            ],
        )

    def test_effective_streams_are_bounded_by_file_count(self) -> None:
        plan = model_library.partition_blob_files(self.hub, streams=16)
        self.assertEqual(plan["requested_streams"], 16)
        self.assertEqual(plan["effective_streams"], 5)
        self.assertTrue(all(group["files"] for group in plan["groups"]))

        for streams in (0, 17):
            with self.subTest(streams=streams):
                with self.assertRaisesRegex(
                    model_library.ModelLibraryError,
                    "between 1 and 16",
                ):
                    model_library.partition_blob_files(
                        self.hub,
                        streams=streams,
                    )

    def test_missing_or_symlinked_blob_tree_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "blob directory is missing",
        ):
            model_library.partition_blob_files(
                self.root / "missing",
                streams=2,
            )

        unsafe = self.root / "unsafe" / "blobs"
        unsafe.mkdir(parents=True)
        outside = self.root / "outside"
        outside.write_bytes(b"outside")
        (unsafe / "link").symlink_to(outside)
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "contains a symlink",
        ):
            model_library.partition_blob_files(
                unsafe.parent,
                streams=2,
            )


if __name__ == "__main__":
    unittest.main()
