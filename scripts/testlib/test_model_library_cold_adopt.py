#!/usr/bin/env python3
"""Cold-adopt publication and lifecycle-lock safety contracts."""

from __future__ import annotations

import fcntl
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402
from scripts import model_library_receipt  # noqa: E402


class ColdAdoptSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tempdir.name)
        self.cold_root = self.root / "cold"
        self.cache_root = self.root / "cache"
        self.model_id = "DemoOrg/Demo-Model"
        self.source = (
            self.cold_root / "Official Models" / "DemoOrg" / "Demo-Model"
        )
        self.source.mkdir(parents=True)
        (self.source / "config.json").write_text(
            '{"architectures":["Fixture"]}\n', encoding="utf-8"
        )
        (self.source / "model.safetensors").write_bytes(b"fixture-weights\n")
        self.dest = (
            self.cache_root
            / "hub"
            / model_library.model_id_to_hub_dirname(self.model_id)
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def plan(self) -> dict:
        return model_library.plan_cold_adopt(
            cold_root=str(self.cold_root),
            model_id=self.model_id,
            cache_root=self.cache_root,
        )

    def assert_no_staging(self) -> None:
        hub_root = self.cache_root / "hub"
        leftovers = (
            list(hub_root.glob(".pulsar-cold-adopt-*"))
            if hub_root.exists()
            else []
        )
        self.assertEqual(leftovers, [])

    def test_success_stages_privately_and_publishes_complete_tree(self) -> None:
        plan = self.plan()
        real_materialize = model_library.materialize_hub_tree

        def materialize_while_destination_is_absent(*args, **kwargs):
            self.assertFalse(self.dest.exists())
            return real_materialize(*args, **kwargs)

        with mock.patch(
            "scripts.model_library.materialize_hub_tree",
            side_effect=materialize_while_destination_is_absent,
        ):
            result = model_library.execute_cold_adopt(plan)

        self.assertEqual(result["dest_state"], "complete")
        self.assertEqual(result["staging_cleanup"], "removed")
        revision = result["revision"]
        self.assertTrue(
            (self.dest / "snapshots" / revision / "model.safetensors").is_file()
        )
        homes = model_library.scan_hub_cache(
            self.cache_root,
            rank=0,
            node_id="node-0",
            hostname="fixture-0",
            ssh_host="local",
        )
        catalog = model_library.build_catalog(
            topology_id="a" * 64,
            homes=homes,
            profiles=[],
        )
        model_library_receipt.classify_catalog_occupancy(
            catalog, self.root / "library"
        )
        model_library.refresh_catalog_profile_identity(catalog)
        model_library._apply_catalog_primary_policies(catalog)
        home = catalog["models"][0]["homes"][0]
        self.assertEqual(home["home_class"], "unbound-complete")
        self.assertEqual(home["unbound_reason"], "non-exact-revision")
        self.assertFalse(home["primary"])
        self.assert_no_staging()

    def test_plan_refuses_existing_destination_without_modification(self) -> None:
        self.dest.mkdir(parents=True)
        sentinel = self.dest / "preserve-me"
        sentinel.write_text("existing durable content\n", encoding="utf-8")

        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "destination repository already exists",
        ):
            self.plan()

        self.assertEqual(
            sentinel.read_text(encoding="utf-8"),
            "existing durable content\n",
        )

    def test_publish_race_preserves_new_destination(self) -> None:
        plan = self.plan()
        real_materialize = model_library.materialize_hub_tree
        sentinel = self.dest / "raced-content"

        def materialize_then_race(*args, **kwargs):
            result = real_materialize(*args, **kwargs)
            self.dest.mkdir(parents=True)
            sentinel.write_text("raced durable content\n", encoding="utf-8")
            return result

        with mock.patch(
            "scripts.model_library.materialize_hub_tree",
            side_effect=materialize_then_race,
        ):
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "appeared before publication",
            ):
                model_library.execute_cold_adopt(plan)

        self.assertEqual(
            sentinel.read_text(encoding="utf-8"),
            "raced durable content\n",
        )
        self.assert_no_staging()

    def test_interrupted_materialization_leaves_no_final_repository(self) -> None:
        plan = self.plan()

        def interrupted_materialize(_source, staged_hub, **_kwargs):
            staged = pathlib.Path(staged_hub)
            staged.mkdir(parents=True)
            (staged / "partial").write_text("partial\n", encoding="utf-8")
            raise model_library.ModelLibraryError("simulated interrupted copy")

        with mock.patch(
            "scripts.model_library.materialize_hub_tree",
            side_effect=interrupted_materialize,
        ):
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "simulated interrupted copy",
            ):
                model_library.execute_cold_adopt(plan)

        self.assertFalse(self.dest.exists())
        self.assert_no_staging()

    def test_public_command_requires_exclusive_lifecycle_lock(self) -> None:
        lock_path = self.root / "lifecycle.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            environment = os.environ.copy()
            environment.update(
                {
                    "PULSAR_MODEL_LIBRARY_LOCK_FILE": str(lock_path),
                    "PULSAR_MODEL_LIBRARY_LOCK_TIMEOUT_SECONDS": "0.1",
                    "MODEL_LIBRARY_DIR": str(self.root / "library"),
                }
            )
            process = subprocess.run(
                [
                    str(REPO_ROOT / "scripts" / "model-library.sh"),
                    "cold",
                    "adopt",
                    self.model_id,
                    "--root",
                    str(self.cold_root),
                    "--cache-root",
                    str(self.cache_root),
                    "--yes",
                ],
                cwd=REPO_ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            os.close(descriptor)

        self.assertNotEqual(process.returncode, 0)
        self.assertIn(
            "exclusive model-library mutation lock timed out",
            process.stderr,
        )
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
