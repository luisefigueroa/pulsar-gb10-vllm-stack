#!/usr/bin/env python3
"""Stop-time local-files retention contracts with local command shims."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOWN = REPO_ROOT / "scripts" / "down.sh"
PROFILE = "qwen3.8-27b-fp8"
CONTAINER = f"vllm-{PROFILE}"


class DownHotPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="pulsar-down-hot-")
        self.root = pathlib.Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "docker.json"
        self.library_log = self.root / "library.log"
        self._write_shims()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_shims(self) -> None:
        docker = self.bin / "docker"
        docker.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                import pathlib
                import sys

                path = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
                state = json.loads(path.read_text(encoding="utf-8"))
                command = sys.argv[1] if len(sys.argv) > 1 else ""
                if command == "info":
                    raise SystemExit(0)
                if command == "inspect":
                    ref = sys.argv[-1]
                    item = next((value for value in state if
                                 value["id"] == ref or
                                 value["id"].startswith(ref) or
                                 value["name"] == ref.lstrip("/")), None)
                    if item is None:
                        raise SystemExit(1)
                    print(json.dumps({
                        "id": item["id"],
                        "name": "/" + item["name"].lstrip("/"),
                        "labels": item.get("labels") or {},
                    }, separators=(",", ":")))
                    raise SystemExit(0)
                if command == "rm":
                    ref = sys.argv[-1]
                    state = [value for value in state if value["id"] != ref]
                    path.write_text(json.dumps(state), encoding="utf-8")
                    raise SystemExit(0)
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        docker.chmod(0o755)

        library = self.bin / "model-library"
        library.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s\n' "$*" >>"$FAKE_LIBRARY_LOG"
                exit "${FAKE_LIBRARY_RC:-0}"
                """
            ),
            encoding="utf-8",
        )
        library.chmod(0o755)

    def _seed(self, source: str) -> None:
        labels = {
            "io.pulsar.gb10.managed": "true",
            "io.pulsar.gb10.conf": PROFILE,
            "io.pulsar.gb10.rank": "single",
        }
        if source:
            labels["io.pulsar.gb10.weight-source"] = source
        self.state.write_text(
            json.dumps([{
                "id": "a" * 64,
                "name": CONTAINER,
                "labels": labels,
            }]),
            encoding="utf-8",
        )
        self.library_log.write_text("", encoding="utf-8")

    def _run(
        self,
        *args: str,
        library_rc: int = 0,
        extra_env: dict[str, str] | None = None,
        target: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "PULSAR_DOCKER": str(self.bin / "docker"),
            "PULSAR_MODEL_LIBRARY_CMD": str(self.bin / "model-library"),
            "FAKE_DOCKER_STATE": str(self.state),
            "FAKE_LIBRARY_LOG": str(self.library_log),
            "FAKE_LIBRARY_RC": str(library_rc),
            "CLUSTER_TOPOLOGY_FILE": str(self.root / "absent-topology.json"),
        })
        env.pop("PULSAR_HOT_STOP_POLICY", None)
        if extra_env:
            env.update(extra_env)
        argv = [str(DOWN), target if target is not None else PROFILE, *args]
        return subprocess.run(
            argv,
            cwd=REPO_ROOT,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )

    def _library_args(self) -> str:
        return self.library_log.read_text(encoding="utf-8").strip()

    def _container_count(self) -> int:
        return len(json.loads(self.state.read_text(encoding="utf-8")))

    def test_library_hot_stop_retains_unpinned_views_by_default(self) -> None:
        self._seed("local-files")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._library_args(), "")
        self.assertIn("will be retained after stop", result.stdout)
        self.assertEqual(self._container_count(), 0)

    def test_explicit_retain_does_not_call_the_library(self) -> None:
        self._seed("local-files")
        result = self._run("--retain-weights")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._library_args(), "")
        self.assertIn("retaining unpinned prepared views", result.stdout)

    def test_explicit_pin_retains_selected_placement(self) -> None:
        self._seed("local-files")
        result = self._run("--pin-weights")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._library_args(), f"pin {PROFILE} --node head")

    def test_explicit_purge_can_remove_a_pin(self) -> None:
        self._seed("local-files")
        result = self._run("--purge-hot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._library_args(),
            f"purge-hot {PROFILE} --node head --yes --force-unpin",
        )

    def test_replicated_stop_does_not_touch_model_library(self) -> None:
        self._seed("replicated")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._library_args(), "")

    def test_site_policy_purge_removes_unpinned_views(self) -> None:
        self._seed("local-files")
        result = self._run(extra_env={"PULSAR_HOT_STOP_POLICY": "purge"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._library_args(),
            f"purge-hot {PROFILE} --node head --yes",
        )
        self.assertNotIn("--force-unpin", self._library_args())

    def test_retain_flag_overrides_site_policy_purge(self) -> None:
        self._seed("local-files")
        result = self._run(
            "--retain-weights",
            extra_env={"PULSAR_HOT_STOP_POLICY": "purge"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._library_args(), "")

    def test_invalid_site_policy_fails_closed_before_stop(self) -> None:
        self._seed("local-files")
        result = self._run(extra_env={"PULSAR_HOT_STOP_POLICY": "evict"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("PULSAR_HOT_STOP_POLICY must be retain or purge", result.stderr)
        self.assertEqual(self._library_args(), "")
        self.assertEqual(self._container_count(), 1)

    def test_empty_site_policy_fails_closed(self) -> None:
        self._seed("local-files")
        result = self._run(extra_env={"PULSAR_HOT_STOP_POLICY": ""})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self._container_count(), 1)

    def test_site_policy_purge_warns_when_library_refuses(self) -> None:
        self._seed("local-files")
        result = self._run(
            library_rc=7,
            extra_env={"PULSAR_HOT_STOP_POLICY": "purge"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("hot staging was retained", result.stderr)
        self.assertNotIn("--force-unpin", self._library_args())

    def test_retention_flags_are_mutually_exclusive(self) -> None:
        self._seed("local-files")
        result = self._run("--retain-weights", "--purge-hot")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self._container_count(), 1)
        self.assertEqual(self._library_args(), "")


if __name__ == "__main__":
    unittest.main()
