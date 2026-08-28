#!/usr/bin/env python3
"""Contracts for explicit-only cold recovery storage configuration."""

from __future__ import annotations

import fcntl
import io
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts import model_library_cold_archive as cold_archive  # noqa: E402
from scripts import model_library_cold_storage as cold_storage  # noqa: E402


ENV_KEYS = (
    "PULSAR_COLD_ROOT",
    "MODELS_NFS",
    "PULSAR_SELFTEST",
    "PULSAR_COLD_STORAGE_TEST_DOTENV",
    "MODEL_LIBRARY_DIR",
    "PULSAR_HOT_ROOT",
    "HF_CACHE",
)


class ColdStorageCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.env_file = self.root / "env"
        self.library = self.root / "library"
        self.hot = self.root / "hot"
        self.cold = self.root / "cold"
        self.hf = self.root / "hf"
        self.library.mkdir()
        self.hot.mkdir()
        self.cold.mkdir()
        self.hf.mkdir()
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ["PULSAR_SELFTEST"] = "1"
        os.environ["PULSAR_COLD_STORAGE_TEST_DOTENV"] = str(self.env_file)
        os.environ["MODEL_LIBRARY_DIR"] = str(self.library)
        os.environ["PULSAR_HOT_ROOT"] = str(self.hot)
        os.environ["HF_CACHE"] = str(self.hf)
        cold_storage.WRITE_LOCK_HOOK = None

    def tearDown(self) -> None:
        cold_storage.WRITE_LOCK_HOOK = None
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _job(self, receipt_id: str | None = None, state: str = "pending") -> dict:
        receipt_id = receipt_id or ("a" * 64)
        return cold_archive.validate_cold_archive_job(
            {
                "schema_version": 1,
                "kind": cold_archive.COLD_ARCHIVE_JOB_KIND,
                "receipt_id": receipt_id,
                "model_id": "Org/Model",
                "snapshot_revision": "b" * 40,
                "state": state,
                "detail": "fixture",
            }
        )

    def _cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "model_library_cold_storage.py"), *args],
            check=False,
            capture_output=True,
            text=True,
            env=merged,
        )


class DotenvParseWriteTests(ColdStorageCase):
    def test_lock_path_matches_shell_for_repository_dotenv(self) -> None:
        self.assertEqual(
            cold_storage._lock_path(self.root / ".env").name,
            ".env.pulsar-cold-storage.lock",
        )
        self.assertEqual(
            cold_storage._lock_path(self.root / "env").name,
            ".env.pulsar-cold-storage.lock",
        )

    def test_parse_absent_and_empty_and_path(self) -> None:
        parsed = cold_storage.parse_dotenv_bytes(b"FOO=1\n")
        self.assertEqual(parsed["state"], "absent")
        parsed = cold_storage.parse_dotenv_bytes(b"PULSAR_COLD_ROOT=''\n")
        self.assertEqual(parsed["state"], "empty")
        self.assertEqual(parsed["value"], "")
        parsed = cold_storage.parse_dotenv_bytes(
            b"PULSAR_COLD_ROOT='/tmp/cold root'\n"
        )
        self.assertEqual(parsed["value"], "/tmp/cold root")

    def test_reject_duplicate_export_dynamic_and_relative(self) -> None:
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(
                b"PULSAR_COLD_ROOT=/a\nPULSAR_COLD_ROOT=/b\n"
            )
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(b"export PULSAR_COLD_ROOT=/a\n")
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(b"PULSAR_COLD_ROOT=$(pwd)\n")
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(b"PULSAR_COLD_ROOT=/tmp/foo extra\n")
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.lexical_absolute("relative/path")
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.lexical_absolute("~/models")

    def test_writer_preserves_unrelated_bytes_including_non_utf8(self) -> None:
        original = b"FOO=ok\n" + bytes([0xFF, 0xFE]) + b"\nBAR=1\n"
        self.env_file.write_bytes(original)
        os.chmod(self.env_file, 0o600)
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            lib_dir=self.library,
        )
        out = self.env_file.read_bytes()
        self.assertIn(b"FOO=ok\n", out)
        self.assertIn(bytes([0xFF, 0xFE]), out)
        self.assertIn(b"BAR=1\n", out)
        self.assertIn(b"PULSAR_COLD_ROOT=", out)
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)

    def test_create_missing_private_and_refuse_world_readable(self) -> None:
        self.assertFalse(self.env_file.exists())
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            lib_dir=self.library,
        )
        self.assertTrue(self.env_file.is_file())
        self.assertEqual(stat.S_IMODE(self.env_file.stat().st_mode), 0o600)
        self.env_file.write_bytes(b"PULSAR_COLD_ROOT=''\n")
        os.chmod(self.env_file, 0o644)
        before = self.env_file.read_bytes()
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.write_dotenv_assignment(
                str(self.cold),
                path=self.env_file,
                lib_dir=self.library,
            )
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_refuse_symlink_dotenv_and_invalid_test_override(self) -> None:
        target = self.root / "real-env"
        target.write_bytes(b"PULSAR_COLD_ROOT=''\n")
        os.chmod(target, 0o600)
        self.env_file.symlink_to(target)
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.read_dotenv(self.env_file)
        os.environ.pop("PULSAR_SELFTEST", None)
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.dotenv_path()
        os.environ["PULSAR_SELFTEST"] = "1"
        os.environ["PULSAR_COLD_STORAGE_TEST_DOTENV"] = "relative.env"
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.dotenv_path()

    def test_spaces_and_metacharacters_round_trip(self) -> None:
        weird = self.root / "cold dir" / "a'b;$literal"
        weird.mkdir(parents=True)
        cold_storage.write_dotenv_assignment(
            str(weird),
            path=self.env_file,
            lib_dir=self.library,
        )
        parsed = cold_storage.read_dotenv(self.env_file)
        self.assertEqual(parsed["value"], str(weird))

    def test_reject_unquoted_shell_control_and_non_utf8_preferred_line(self) -> None:
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(b"PULSAR_COLD_ROOT=/tmp/cold;false\n")
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.parse_dotenv_bytes(b"PULSAR_COLD_ROOT=\xff\n")

    def test_failure_cleanup_preserves_unowned_matching_sibling(self) -> None:
        sibling = self.root / f".{self.env_file.name}.pulsar-cold-storage.unowned"
        sibling.write_text("keep\n", encoding="utf-8")
        plan = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )

        def hook(path: pathlib.Path) -> None:
            path.write_bytes(b"PULSAR_COLD_ROOT=''\n")
            os.chmod(path, 0o600)

        cold_storage.WRITE_LOCK_HOOK = hook
        with self.assertRaises(cold_storage.ColdStorageError):
            cold_storage.write_dotenv_assignment(
                str(self.cold),
                path=self.env_file,
                expected_plan_id=plan["plan_id"],
                lib_dir=self.library,
            )
        self.assertEqual(sibling.read_text(encoding="utf-8"), "keep\n")


class PrecedenceAndFallbackTests(ColdStorageCase):
    def test_configured_cold_root_ignores_models_nfs_and_mnt_models(self) -> None:
        os.environ["MODELS_NFS"] = "/mnt/Models"
        os.environ.pop("PULSAR_COLD_ROOT", None)
        self.assertIsNone(model_library.configured_cold_root())
        os.environ["PULSAR_COLD_ROOT"] = ""
        self.assertIsNone(model_library.configured_cold_root())
        os.environ["PULSAR_COLD_ROOT"] = str(self.cold)
        self.assertEqual(model_library.configured_cold_root(), str(self.cold))
        source = (REPO_ROOT / "scripts" / "model_library.py").read_text(
            encoding="utf-8"
        )
        body = source.split("def configured_cold_root", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("MODELS_NFS", body.split("not aliases", 1)[1])
        self.assertNotIn("/mnt/Models", body.split("not aliases", 1)[1])

    def test_process_empty_overrides_persisted_path(self) -> None:
        self.env_file.write_bytes(
            f"PULSAR_COLD_ROOT={cold_storage.quote_env_value(str(self.cold))}\n".encode()
        )
        os.chmod(self.env_file, 0o600)
        os.environ["PULSAR_COLD_ROOT"] = ""
        status = cold_storage.show_status(
            lib_dir=self.library, dotenv=self.env_file
        )
        self.assertEqual(status["state"], "environment-override")
        self.assertEqual(status["persisted"]["state"], "configured-available")
        self.assertEqual(status["effective"]["state"], "disabled")
        self.assertEqual(status["effective"]["source"], "process")
        self.assertEqual(status["exit_code"], 0)

    def test_absent_is_not_configured(self) -> None:
        status = cold_storage.show_status(
            lib_dir=self.library, dotenv=self.env_file
        )
        self.assertEqual(status["state"], "not-configured")
        self.assertEqual(status["exit_code"], 0)
        self.assertFalse((self.library / "cold-archive-jobs").exists())


class PathPlanHealthTests(ColdStorageCase):
    def test_current_writability_is_health_not_configuration_policy(self) -> None:
        os.chmod(self.cold, 0o555)
        try:
            plan = cold_storage.build_plan(
                requested=str(self.cold),
                disable=False,
                lib_dir=self.library,
                dotenv=self.env_file,
            )
        finally:
            os.chmod(self.cold, 0o700)
        self.assertEqual(plan["action"], "set-new")
        self.assertFalse(plan["path_health"]["writable"])
        self.assertTrue(plan["path_health"]["usable"])

    def test_missing_relative_symlink_and_nested_roots(self) -> None:
        missing = self.root / "missing-cold"
        plan = cold_storage.build_plan(
            requested=str(missing),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(plan["action"], "change-blocked")
        self.assertFalse(missing.exists())
        self.assertIn(cold_storage.AUTHORITY_ASSERTION, plan["findings"])
        link = self.root / "cold-link"
        link.symlink_to(self.cold)
        plan = cold_storage.build_plan(
            requested=str(link),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(plan["action"], "change-blocked")
        self.assertTrue(plan["path_health"]["final_symlink"])
        nested = self.library / "inside"
        nested.mkdir()
        plan = cold_storage.build_plan(
            requested=str(nested),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(plan["action"], "change-blocked")
        self.assertTrue(plan["path_health"]["unsafe"])

    def test_set_keep_disable_and_stable_plan_id(self) -> None:
        first = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        second = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["action"], "set-new")
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            expected_plan_id=first["plan_id"],
            lib_dir=self.library,
        )
        keep = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(keep["action"], "keep")
        disable = cold_storage.build_plan(
            requested=None,
            disable=True,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(disable["action"], "set-new")
        cold_storage.write_dotenv_assignment(
            "",
            path=self.env_file,
            disable=True,
            expected_plan_id=disable["plan_id"],
            lib_dir=self.library,
        )
        parsed = cold_storage.read_dotenv(self.env_file)
        self.assertEqual(parsed["state"], "empty")

    def test_toc_tou_mismatch_and_concurrent_lock(self) -> None:
        plan = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )

        def hook(path: pathlib.Path) -> None:
            path.write_bytes(b"PULSAR_COLD_ROOT=''\n")
            os.chmod(path, 0o600)

        cold_storage.WRITE_LOCK_HOOK = hook
        with self.assertRaises(cold_storage.ColdStorageError) as ctx:
            cold_storage.write_dotenv_assignment(
                str(self.cold),
                path=self.env_file,
                expected_plan_id=plan["plan_id"],
                lib_dir=self.library,
            )
        self.assertEqual(ctx.exception.exit_code, 1)
        cold_storage.WRITE_LOCK_HOOK = None

        lock = self.root / f".{self.env_file.name}.pulsar-cold-storage.lock"
        started = threading.Event()

        def holder() -> None:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            # Archive/recovery operations hold the shared side; configuration
            # mutation must wait for the exclusive side.
            fcntl.flock(fd, fcntl.LOCK_SH)
            started.set()
            time.sleep(0.3)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(started.wait(1))
        begun = time.monotonic()
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            lib_dir=self.library,
        )
        elapsed = time.monotonic() - begun
        thread.join(1)
        self.assertGreaterEqual(elapsed, 0.25)
        self.assertTrue(self.env_file.is_file())


class StrandAndJobTests(ColdStorageCase):
    def test_job_blocks_disable_and_root_change(self) -> None:
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            lib_dir=self.library,
        )
        cold_archive.write_cold_archive_job(self.library, self._job())
        other = self.root / "other-cold"
        other.mkdir()
        change = cold_storage.build_plan(
            requested=str(other),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(change["action"], "change-blocked")
        self.assertEqual(change["affected"]["archive_job_ids"], ["a" * 64])
        disable = cold_storage.build_plan(
            requested=None,
            disable=True,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(disable["action"], "change-blocked")
        self.assertEqual(disable["exit_code"], 1)

    def test_recovery_objects_block_and_receipt_alone_does_not(self) -> None:
        cold_storage.write_dotenv_assignment(
            str(self.cold),
            path=self.env_file,
            lib_dir=self.library,
        )
        replica = (
            self.cold
            / "pulsar-control"
            / "download-receipts"
            / f"{'c' * 64}.json"
        )
        replica.parent.mkdir(parents=True)
        replica.write_bytes(b"{}\n")
        os.chmod(replica, 0o600)
        os.chmod(replica.parent, 0o700)
        os.chmod(self.cold / "pulsar-control", 0o700)
        other = self.root / "other-cold"
        other.mkdir()
        change = cold_storage.build_plan(
            requested=str(other),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(change["action"], "change-blocked")
        self.assertEqual(change["affected"]["receipt_replica_ids"], ["c" * 64])

    def test_process_override_cannot_hide_persisted_root_recovery(self) -> None:
        cold_storage.write_dotenv_assignment(
            str(self.cold), path=self.env_file, lib_dir=self.library
        )
        cold_archive.write_cold_archive_job(self.library, self._job())
        override = self.root / "override"
        replacement = self.root / "replacement"
        override.mkdir()
        replacement.mkdir()
        os.environ["PULSAR_COLD_ROOT"] = str(override)
        change = cold_storage.build_plan(
            requested=str(replacement),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        self.assertEqual(change["action"], "change-blocked")
        self.assertEqual(change["affected"]["archive_job_ids"], ["a" * 64])

    def test_only_local_occupancy_paths_participate_in_nesting(self) -> None:
        attachment = {"node_id": "remote-node", "durable_home_path": str(self.cold)}
        with mock.patch.object(
            cold_storage,
            "_occupancy_paths",
            return_value={"ok": True, "attachments": [attachment], "detail": None},
        ), mock.patch.object(
            cold_storage, "_local_topology_node_id", return_value="local-node"
        ):
            self.assertIsNone(
                cold_storage._occupancy_nest_detail(str(self.cold), self.library)
            )
            attachment["node_id"] = "local-node"
            self.assertIsNotNone(
                cold_storage._occupancy_nest_detail(str(self.cold), self.library)
            )

    def test_malformed_receipt_replica_makes_recovery_unavailable(self) -> None:
        control = self.cold / "pulsar-control"
        store = control / "download-receipts"
        store.mkdir(parents=True)
        os.chmod(control, 0o700)
        os.chmod(store, 0o700)
        replica = store / f"{'c' * 64}.json"
        replica.write_text("{}\n", encoding="utf-8")
        os.chmod(replica, 0o600)
        recovery = cold_storage.inspect_recovery(str(self.cold), lib_dir=self.library)
        self.assertFalse(recovery["ok"])
        self.assertIn("invalid", str(recovery["detail"]))

    def test_list_jobs_missing_store_is_empty_and_does_not_mkdir(self) -> None:
        jobs = cold_archive.list_cold_archive_jobs(self.library)
        self.assertEqual(jobs, [])
        self.assertFalse((self.library / "cold-archive-jobs").exists())
        document = cold_storage.list_archive_jobs_document(lib_dir=self.library)
        self.assertEqual(document["count"], 0)
        self.assertFalse((self.library / "cold-archive-jobs").exists())

    def test_malformed_job_store_fails_without_skipping(self) -> None:
        store = self.library / "cold-archive-jobs"
        store.mkdir()
        (store / "not-a-job.txt").write_text("nope\n", encoding="utf-8")
        with self.assertRaises(cold_archive.ColdArchiveError):
            cold_archive.list_cold_archive_jobs(self.library)

    def test_retry_eligibility_refuses_running_complete_and_missing_receipt(self) -> None:
        running = cold_archive.write_cold_archive_job(
            self.library, self._job(state="running")
        )
        plan = cold_storage.retry_plan(running["receipt_id"], lib_dir=self.library)
        self.assertFalse(plan["eligible"])
        complete = cold_archive.write_cold_archive_job(
            self.library, self._job(receipt_id="d" * 64, state="complete")
        )
        plan = cold_storage.retry_plan(complete["receipt_id"], lib_dir=self.library)
        self.assertFalse(plan["eligible"])
        pending = cold_archive.write_cold_archive_job(
            self.library, self._job(receipt_id="e" * 64, state="pending")
        )
        plan = cold_storage.retry_plan(pending["receipt_id"], lib_dir=self.library)
        self.assertFalse(plan["eligible"])
        self.assertIn("receipt", plan["reason"])
        self.assertEqual(
            plan["command"],
            [
                "scripts/model-library.sh",
                "home",
                "archive",
                "run",
                "--receipt",
                pending["receipt_id"],
                "--yes",
            ],
        )


class ShowPlanCliTests(ColdStorageCase):
    def test_render_plan_accepts_the_public_serialized_shape(self) -> None:
        plan = cold_storage.build_plan(
            requested=str(self.cold),
            disable=False,
            lib_dir=self.library,
            dotenv=self.env_file,
        )
        public_plan = {key: value for key, value in plan.items() if key != "exit_code"}
        with mock.patch(
            "sys.stdin", new=io.StringIO(json.dumps(public_plan))
        ), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(cold_storage.cmd_render_plan(object()), 0)

    def test_show_plan_jobs_json_and_exits(self) -> None:
        result = self._cli("show", "--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], cold_storage.STATUS_KIND)
        self.assertEqual(payload["state"], "not-configured")
        self.assertNotIn("exit_code", payload)
        result = self._cli("plan", "--path", str(self.cold), "--json")
        self.assertEqual(result.returncode, 0)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["action"], "set-new")
        result = self._cli("set", "--path", str(self.cold))
        self.assertEqual(result.returncode, 2)
        self.assertIn("--yes", result.stderr)
        result = self._cli("set", "--path", str(self.cold), "--yes", "--json")
        self.assertEqual(result.returncode, 0)
        mutation = json.loads(result.stdout)
        self.assertEqual(set(mutation), cold_storage.MUTATION_FIELDS - {"exit_code"})
        self.assertEqual(mutation["kind"], cold_storage.MUTATION_KIND)
        self.assertEqual(mutation["plan"]["kind"], cold_storage.PLAN_KIND)
        self.assertEqual(mutation["status"]["kind"], cold_storage.STATUS_KIND)
        self.assertNotIn("mutation", mutation["status"])
        result = self._cli("show", "--json")
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "configured-available")
        result = self._cli("archive-jobs", "--json")
        self.assertEqual(result.returncode, 0)
        jobs = json.loads(result.stdout)
        self.assertEqual(jobs["kind"], cold_storage.JOBS_KIND)
        missing = str(self.root / "no-such-cold")
        result = self._cli("plan", "--path", missing, "--json")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(pathlib.Path(missing).exists())

    def test_human_output_at_forty_columns_without_color(self) -> None:
        env = os.environ.copy()
        env["COLUMNS"] = "40"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        result = self._cli("show", env=env)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("\x1b[", result.stdout)
        for line in result.stdout.splitlines():
            self.assertLessEqual(len(line), 40, line)
        self.assertIn("Pulsar can verify path safety", result.stdout)
        self.assertIn("failure-domain policy", result.stdout)

    def test_docs_do_not_teach_live_models_nfs_fallback(self) -> None:
        operations = (REPO_ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        self.assertIn("explicit `PULSAR_COLD_ROOT` only", operations)
        self.assertNotIn(
            "MODELS_NFS=/mnt/Models`, overridable with `PULSAR_COLD_ROOT`",
            operations,
        )
        example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PULSAR_COLD_ROOT", example)
        self.assertNotIn("MODELS_NFS=/mnt/Models", example)
        launch_plan = (REPO_ROOT / "scripts" / "launch_plan.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("models_nfs", launch_plan)
        self.assertNotIn('"target": "/mnt/Models"', launch_plan)


if __name__ == "__main__":
    unittest.main()
