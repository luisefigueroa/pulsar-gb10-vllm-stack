#!/usr/bin/env python3
"""Contracts for the operator platform reference schema and selection rules."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import platform_reference as platform  # noqa: E402

MODULE = ROOT / "scripts" / "platform_reference.py"
PRODUCTION = ROOT / "platforms" / "dgx-spark-gb10.json"
TEST_OTHER = ROOT / "scripts" / "testdata" / "platforms" / "test-other.json"
ENV_KEYS = ("PULSAR_PLATFORM", "PULSAR_PLATFORM_FILE")


class PlatformReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        for key in ENV_KEYS:
            if env is None or key not in env:
                merged.pop(key, None)
            elif env[key] is None:  # type: ignore[comparison-overlap]
                merged.pop(key, None)
        return subprocess.run(
            [sys.executable, str(MODULE), *args],
            check=False,
            capture_output=True,
            text=True,
            env=merged,
        )

    def test_default_load_matches_production_file(self) -> None:
        loaded = platform.load_current_platform()
        self.assertEqual(loaded["platform_id"], "dgx-spark-gb10")
        self.assertEqual(loaded["gpu_name"], "NVIDIA GB10")
        self.assertEqual(loaded["display_name"], "GB10")
        self.assertEqual(loaded["architectures"], ["aarch64", "arm64"])
        self.assertEqual(loaded["accelerators_per_node"], 1)
        self.assertEqual(loaded["rdma"]["min_active_links_for_qualify"], 1)
        self.assertEqual(loaded["rdma"]["verbs_device"], "/dev/infiniband/uverbs0")
        self.assertEqual(loaded["memory"]["hard_floor_available_gib"], 4)
        self.assertEqual(loaded["memory"]["min_os_buffer_gib"], 8)
        self.assertEqual(loaded["memory"]["launch_spike_gib"], 3)
        self.assertEqual(loaded["memory"]["overhead_gib_default"], 10)
        self.assertEqual(loaded["memory"]["preflight_warn_available_gib"], 100)
        self.assertEqual(loaded["memory"]["cold_start_footprint_slack"], 0.92)
        self.assertEqual(
            loaded,
            platform.validate_platform_reference(
                json.loads(PRODUCTION.read_text(encoding="utf-8"))
            ),
        )

    def test_unknown_id_fails(self) -> None:
        os.environ["PULSAR_PLATFORM"] = "no-such-platform"
        with self.assertRaisesRegex(platform.PlatformReferenceError, "missing"):
            platform.load_current_platform()
        result = self._run("print-json", env={"PULSAR_PLATFORM": "no-such-platform"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("platform-reference:", result.stderr)

    def test_empty_id_fails(self) -> None:
        os.environ["PULSAR_PLATFORM"] = ""
        with self.assertRaisesRegex(platform.PlatformReferenceError, "empty"):
            platform.load_current_platform()
        result = self._run("print-json", env={"PULSAR_PLATFORM": ""})
        self.assertNotEqual(result.returncode, 0)

    def test_empty_file_override_fails(self) -> None:
        os.environ["PULSAR_PLATFORM_FILE"] = ""
        with self.assertRaisesRegex(platform.PlatformReferenceError, "empty"):
            platform.load_current_platform()

    def test_relative_file_override_fails(self) -> None:
        os.environ["PULSAR_PLATFORM_FILE"] = "scripts/testdata/platforms/test-other.json"
        with self.assertRaisesRegex(platform.PlatformReferenceError, "absolute"):
            platform.load_current_platform()

    def test_file_override_takes_precedence(self) -> None:
        os.environ["PULSAR_PLATFORM"] = "dgx-spark-gb10"
        os.environ["PULSAR_PLATFORM_FILE"] = str(TEST_OTHER)
        loaded = platform.load_current_platform()
        self.assertEqual(loaded["platform_id"], "test-other")
        self.assertEqual(loaded["gpu_name"], "NVIDIA TESTGPU")
        self.assertEqual(loaded["memory"]["hard_floor_available_gib"], 50)
        self.assertEqual(loaded["rdma"]["verbs_device"], "/dev/infiniband/uverbs9")
        self.assertEqual(loaded["architectures"], ["aarch64", "x86_64"])

    def test_test_other_is_not_a_production_id(self) -> None:
        os.environ["PULSAR_PLATFORM"] = "test-other"
        with self.assertRaisesRegex(platform.PlatformReferenceError, "missing"):
            platform.load_current_platform()

    def test_unknown_field_fails(self) -> None:
        document = json.loads(PRODUCTION.read_text(encoding="utf-8"))
        document["extra"] = "nope"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
        ) as handle:
            json.dump(document, handle)
            path = handle.name
        try:
            os.environ["PULSAR_PLATFORM_FILE"] = path
            with self.assertRaisesRegex(platform.PlatformReferenceError, "unknown field"):
                platform.load_current_platform()
        finally:
            pathlib.Path(path).unlink(missing_ok=True)

    def test_id_mismatch_against_selected_id_fails(self) -> None:
        document = json.loads(PRODUCTION.read_text(encoding="utf-8"))
        document["platform_id"] = "other-name"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            path = root / "dgx-spark-gb10.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            original = platform.PLATFORMS_DIR
            platform.PLATFORMS_DIR = root
            try:
                os.environ["PULSAR_PLATFORM"] = "dgx-spark-gb10"
                with self.assertRaisesRegex(
                    platform.PlatformReferenceError, "does not match selected id"
                ):
                    platform.load_current_platform()
            finally:
                platform.PLATFORMS_DIR = original

    def test_export_shell_round_trip(self) -> None:
        result = self._run("export-shell")
        self.assertEqual(result.returncode, 0)
        self.assertIn("export PULSAR_PLATFORM_ID=", result.stdout)
        self.assertIn("export PULSAR_GPU_NAME=", result.stdout)
        script = (
            result.stdout
            + 'printf "%s\\t%s\\t%s\\t%s\\t%s\\n" '
            + '"$PULSAR_PLATFORM_ID" "$PULSAR_GPU_NAME" '
            + '"$PULSAR_HARD_FLOOR_AVAILABLE_GIB" '
            + '"$PULSAR_COLD_START_FOOTPRINT_SLACK" '
            + '"$PULSAR_ARCHITECTURES"\n'
        )
        echoed = subprocess.run(
            ["bash", "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )
        parts = echoed.stdout.strip().split("\t")
        self.assertEqual(
            parts,
            [
                "dgx-spark-gb10",
                "NVIDIA GB10",
                "4",
                "0.92",
                "aarch64 arm64",
            ],
        )

    def test_print_json_matches_loader(self) -> None:
        result = self._run("print-json")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            json.loads(result.stdout),
            platform.load_current_platform(),
        )


if __name__ == "__main__":
    unittest.main()
