#!/usr/bin/env python3
"""Regression tests for the sustained-load client."""

from __future__ import annotations

import contextlib
import inspect
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validate"))

import soak  # noqa: E402


class SoakWorkerStartupTests(unittest.TestCase):
    def test_main_passes_resolved_api_key_to_every_worker(self) -> None:
        constructed: list[tuple[object, ...]] = []

        class BoundThread:
            def __init__(self, *, target, args, daemon):
                inspect.signature(target).bind(*args)
                self.args = args
                self.daemon = daemon
                constructed.append(args)

            def start(self) -> None:
                return None

        soak.STOP = False
        soak.ERRORS.clear()
        soak.COMPLETED[0] = 0
        argv = [
            "soak.py",
            "--model",
            "fixture-model",
            "--minutes",
            "0",
            "--concurrency",
            "3",
            "--api-key",
            "fixture-secret",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(soak.threading, "Thread", BoundThread),
            mock.patch.object(soak.time, "sleep", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as exit_result,
        ):
            soak.main()

        self.assertEqual(exit_result.exception.code, 1)
        self.assertEqual(len(constructed), 3)
        self.assertTrue(all(args[-1] == "fixture-secret" for args in constructed))

    def test_zero_completion_writes_closed_incomplete_measurement(self) -> None:
        root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-soak-measurement-"))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        output = root / "soak.json"

        class BoundThread:
            def __init__(self, *, target, args, daemon):
                inspect.signature(target).bind(*args)

            def start(self) -> None:
                return None

        soak.STOP = False
        soak.ERRORS.clear()
        soak.COMPLETED[0] = 0
        argv = [
            "soak.py",
            "--model",
            "fixture-model",
            "--minutes",
            "0",
            "--concurrency",
            "1",
            "--result-json",
            str(output),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(soak.threading, "Thread", BoundThread),
            mock.patch.object(soak.time, "sleep", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as exit_result,
        ):
            soak.main()
        self.assertEqual(exit_result.exception.code, 1)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "zero-completions")


if __name__ == "__main__":
    unittest.main(verbosity=2)
