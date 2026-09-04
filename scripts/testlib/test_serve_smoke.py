#!/usr/bin/env python3
"""Contracts for the closed serving-smoke measurement producer."""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from typing import Any
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "validate"))

import serve_smoke  # noqa: E402


class _FakeResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class ServeSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-smoke-"))
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.result = self.root / "serve-smoke.json"

    def run_main(self, **patches: Any) -> int:
        defaults = {
            "check_health": lambda *a, **k: None,
            "run_warmup": lambda *a, **k: None,
            "check_completion": lambda *a, **k: "4",
        }
        defaults.update(patches)
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.multiple(serve_smoke, **defaults), redirect_stdout(
            out
        ), redirect_stderr(err):
            return serve_smoke.main(
                [
                    "--url",
                    "http://127.0.0.1:9",
                    "--model",
                    "fixture",
                    "--health-timeout",
                    "0",
                    "--result-json",
                    str(self.result),
                ]
            )

    def document(self) -> dict[str, Any]:
        return json.loads(self.result.read_text(encoding="utf-8"))

    def test_every_phase_complete_writes_closed_measurement(self) -> None:
        self.assertEqual(self.run_main(), 0)
        document = self.document()
        self.assertEqual(document["operation"], "serve-smoke")
        self.assertEqual(document["program"], "validate/serve_smoke.py")
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(document["reason"], "completed")
        for phase in ("health", "warmup", "completion"):
            self.assertEqual(
                document["serve-smoke"][phase],
                {"completion": "complete", "reason": "completed"},
            )
        self.assertNotIn("127.0.0.1", json.dumps(document))

    def test_warmup_failure_stops_later_phases(self) -> None:
        def failing_warmup(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("short/c1/sync: 1/1 failed")

        self.assertEqual(self.run_main(run_warmup=failing_warmup), 1)
        document = self.document()
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "warmup-failed")
        phases = document["serve-smoke"]
        self.assertEqual(phases["health"]["completion"], "complete")
        self.assertEqual(phases["warmup"], {"completion": "incomplete", "reason": "failed"})
        self.assertEqual(
            phases["completion"],
            {"completion": "incomplete", "reason": "measured-incomplete"},
        )

    def test_health_failure_leaves_every_other_phase_unmeasured(self) -> None:
        def failing_health(*_a: Any, **_k: Any) -> None:
            raise RuntimeError("health did not answer 200")

        self.assertEqual(self.run_main(check_health=failing_health), 1)
        document = self.document()
        self.assertEqual(document["reason"], "health-failed")
        self.assertEqual(document["serve-smoke"]["warmup"]["reason"], "measured-incomplete")

    def test_completion_failure_is_recorded(self) -> None:
        def failing_completion(*_a: Any, **_k: Any) -> str:
            raise RuntimeError("completion returned no text")

        self.assertEqual(self.run_main(check_completion=failing_completion), 1)
        document = self.document()
        self.assertEqual(document["reason"], "completion-failed")
        self.assertEqual(document["serve-smoke"]["completion"]["reason"], "failed")

    def test_check_completion_rejects_empty_text(self) -> None:
        with mock.patch.object(
            serve_smoke.urllib.request,
            "urlopen",
            return_value=_FakeResponse({"choices": [{"text": "  "}]}),
        ):
            with self.assertRaises(RuntimeError):
                serve_smoke.check_completion("http://127.0.0.1:9", "m", None, timeout=1)
        with mock.patch.object(
            serve_smoke.urllib.request,
            "urlopen",
            return_value=_FakeResponse({"choices": [{"text": " 4"}]}),
        ):
            self.assertEqual(
                serve_smoke.check_completion("http://127.0.0.1:9", "m", None, timeout=1),
                " 4",
            )

    def test_check_health_gives_up_after_timeout(self) -> None:
        with mock.patch.object(
            serve_smoke.urllib.request, "urlopen", side_effect=OSError("refused")
        ):
            with self.assertRaises(RuntimeError):
                serve_smoke.check_health(
                    "http://127.0.0.1:9", None, timeout=1, health_timeout=0
                )

    def test_run_warmup_covers_every_warmup_phase(self) -> None:
        seen: list[str] = []
        with mock.patch.object(
            serve_smoke.warmup,
            "run_phase",
            side_effect=lambda _u, _m, label, *rest: seen.append(label),
        ):
            serve_smoke.run_warmup("http://127.0.0.1:9", "m", None, timeout=1)
        self.assertEqual(seen, [phase[0] for phase in serve_smoke.warmup.PHASES])


if __name__ == "__main__":
    unittest.main()
