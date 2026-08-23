#!/usr/bin/env python3
"""Contracts for persistent model-library primary selection."""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_library  # noqa: E402


class PrimarySelectionContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.catalog_path = self.root / "catalog.json"
        self.model_id = "Example/Model"
        self.revision = "a" * 40
        self.identity = f"{self.model_id}@{self.revision}"
        self.homes = [
            self._home(rank=0, node_id="node-a"),
            self._home(rank=1, node_id="node-b"),
        ]

    def _home(self, *, rank: int, node_id: str) -> dict[str, object]:
        return {
            "rank": rank,
            "node_id": node_id,
            "hostname": f"fixture-{rank}",
            "ssh_host": f"fixture-{rank}",
            "cache_root": f"/cache/{rank}",
            "hub_path": f"/cache/{rank}/hub/models--Example--Model",
            "model_id": self.model_id,
            "revision": self.revision,
            "identity_key": self.identity,
            "state": "complete",
            "active": True,
            "bytes": 1024,
        }

    def _build(
        self,
        homes: list[dict[str, object]] | None = None,
        *,
        selections: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        return model_library.build_catalog(
            topology_id="topology-fixture",
            homes=self.homes if homes is None else homes,
            profiles=[],
            primary_selections=selections,
        )

    def _write(self, catalog: dict[str, object]) -> None:
        self.catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    @staticmethod
    def _entry(catalog: dict[str, object]) -> dict[str, object]:
        return catalog["models"][0]  # type: ignore[index,return-value]

    def test_duplicates_require_an_explicit_selection(self) -> None:
        entry = self._entry(self._build())
        self.assertTrue(entry["duplicate"])
        self.assertFalse(entry["has_primary"])
        self.assertEqual(
            entry["primary_selection"],
            {"mode": "operator-required", "status": "missing"},
        )

    def test_set_is_exact_persistent_and_idempotent(self) -> None:
        self._write(self._build())
        result = model_library.set_catalog_primary(
            self.catalog_path,
            self.model_id,
            "1",
            topology_id="topology-fixture",
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["selection"]["node_id"], "node-b")

        stored = model_library.load_catalog(self.catalog_path)
        entry = self._entry(stored)
        self.assertTrue(entry["has_primary"])
        self.assertEqual(entry["primary_selection"]["status"], "match")
        selected_at = stored["primary_selections"][0]["selected_at"]
        before_repeat = self.catalog_path.read_bytes()

        repeated = model_library.set_catalog_primary(
            self.catalog_path,
            self.identity,
            "node-b",
            topology_id="topology-fixture",
        )
        self.assertFalse(repeated["changed"])
        self.assertEqual(repeated["selection"]["selected_at"], selected_at)
        self.assertEqual(self.catalog_path.read_bytes(), before_repeat)

        models_dir = self.root / "models"
        models_dir.mkdir()
        homes_path = self.root / "homes.json"
        homes_path.write_text(json.dumps(self.homes), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            rc = model_library.main(
                [
                    "build",
                    "--topology-id",
                    "topology-fixture",
                    "--models-dir",
                    str(models_dir),
                    "--homes-json",
                    str(homes_path),
                    "--output",
                    str(self.catalog_path),
                    "--preserve-primary-from",
                    str(self.catalog_path),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        refreshed = model_library.load_catalog(self.catalog_path)
        refreshed_entry = self._entry(refreshed)
        primary = next(
            home for home in refreshed_entry["homes"] if home["primary"]
        )
        self.assertEqual(primary["node_id"], "node-b")
        self.assertEqual(
            refreshed["primary_selections"][0]["selected_at"],
            selected_at,
        )

    def test_stale_selection_never_auto_elects_remaining_home(self) -> None:
        selected_at = "2026-08-12T00:00:00.000Z"
        catalog = self._build(
            [self.homes[1]],
            selections=[
                {
                    "identity_key": self.identity,
                    "node_id": "node-a",
                    "selected_at": selected_at,
                }
            ],
        )
        entry = self._entry(catalog)
        self.assertFalse(entry["has_primary"])
        self.assertEqual(entry["primary_selection"]["status"], "stale")
        with self.assertRaisesRegex(model_library.ModelLibraryError, "stale"):
            model_library.resolve_entry(
                catalog,
                model_id=self.model_id,
                cold_root=None,
            )

        entry["homes"] = []
        entry["duplicate"] = False
        entry["on_disk"] = False
        with self.assertRaisesRegex(model_library.ModelLibraryError, "stale"):
            model_library.resolve_entry(
                catalog,
                model_id=self.model_id,
                cold_root=None,
            )

    def test_clear_returns_duplicate_to_operator_required(self) -> None:
        self._write(self._build())
        model_library.set_catalog_primary(
            self.catalog_path,
            self.identity,
            "node-a",
            topology_id="topology-fixture",
        )
        cleared = model_library.clear_catalog_primary(
            self.catalog_path,
            self.identity,
            topology_id="topology-fixture",
        )
        self.assertTrue(cleared["changed"])
        self.assertEqual(cleared["selection"]["mode"], "operator-required")
        catalog = model_library.load_catalog(self.catalog_path)
        self.assertEqual(catalog["primary_selections"], [])
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "duplicate complete homes without primary",
        ):
            model_library.resolve_entry(
                catalog,
                model_id=self.model_id,
                cold_root=None,
            )

    def test_occupancy_home_ignores_unbound_complete_duplicate(self) -> None:
        catalog = self._build()
        entry = self._entry(catalog)
        entry["homes"][0]["home_class"] = "occupancy"
        entry["homes"][0]["occupancy"] = True
        entry["homes"][1]["home_class"] = "unbound-complete"
        entry["homes"][1]["occupancy"] = False
        model_library._apply_entry_primary_policy(entry, None)
        self.assertFalse(entry["duplicate"])
        self.assertTrue(entry["has_primary"])
        self.assertEqual(entry["primary_selection"]["mode"], "automatic-single-home")
        resolved = model_library.resolve_entry(
            catalog, model_id=self.model_id, cold_root=None
        )
        self.assertEqual(resolved["home"]["node_id"], "node-a")

    def test_unbound_complete_without_occupancy_fails_closed(self) -> None:
        catalog = self._build()
        entry = self._entry(catalog)
        for home in entry["homes"]:
            home["home_class"] = "unbound-complete"
            home["occupancy"] = False
        model_library._apply_entry_primary_policy(entry, None)
        self.assertFalse(entry["duplicate"])
        self.assertFalse(entry["has_primary"])
        with self.assertRaisesRegex(
            model_library.ModelLibraryError, "complete tree is unbound"
        ):
            model_library.resolve_entry(
                catalog, model_id=self.model_id, cold_root=None
            )

    def test_cleanup_recommends_only_explicit_safe_steps(self) -> None:
        self._write(self._build())
        unresolved = model_library.cleanup_recommend(
            model_library.load_catalog(self.catalog_path)
        )[0]
        self.assertEqual(len(unresolved["select_commands"]), 2)
        self.assertEqual(unresolved["removal_commands"], [])

        model_library.set_catalog_primary(
            self.catalog_path,
            self.identity,
            "node-b",
            topology_id="topology-fixture",
        )
        rec = model_library.cleanup_recommend(
            model_library.load_catalog(self.catalog_path)
        )[0]
        self.assertEqual(rec["select_commands"], [])
        self.assertEqual(len(rec["removal_commands"]), 1)
        self.assertIn("--node 0", rec["removal_commands"][0]["check"])
        self.assertIn("--node 0 --yes", rec["removal_commands"][0]["remove"])
        self.assertNotIn("--allow-last-home", rec["removal_commands"][0]["remove"])

    def test_invalid_or_stale_catalog_state_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "exact identity_key",
        ):
            model_library.normalize_primary_selections(
                [
                    {
                        "identity_key": self.model_id,
                        "node_id": "node-a",
                        "selected_at": "now",
                    }
                ]
            )
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "millisecond UTC timestamp",
        ):
            model_library.normalize_primary_selections(
                [
                    {
                        "identity_key": self.identity,
                        "node_id": "node-a",
                        "selected_at": "now",
                    }
                ]
            )

        self._write(self._build())
        with self.assertRaisesRegex(
            model_library.ModelLibraryError,
            "catalog topology is stale",
        ):
            model_library.set_catalog_primary(
                self.catalog_path,
                self.identity,
                "node-a",
                topology_id="different-topology",
            )

        with mock.patch.object(
            model_library,
            "load_topology_for_plan",
            return_value={
                "topology_id": "topology-fixture",
                "nodes": [{"rank": 1, "node_id": "different-node"}],
            },
        ):
            with self.assertRaisesRegex(
                model_library.ModelLibraryError,
                "differs from confirmed rank/node identity",
            ):
                model_library.set_catalog_primary(
                    self.catalog_path,
                    self.identity,
                    "node-b",
                    topology_id="topology-fixture",
                    topology_file=self.root / "topology.json",
                )

    def test_human_list_is_readable_at_narrow_width(self) -> None:
        self._write(self._build())
        records = model_library.catalog_primary_records(
            model_library.load_catalog(self.catalog_path)
        )
        output = io.StringIO()
        with redirect_stdout(output):
            model_library.render_catalog_primary_records(records, width=48)
        lines = output.getvalue().splitlines()
        self.assertTrue(lines)
        self.assertLessEqual(max(len(line) for line in lines), 48)
        self.assertIn("operator-required", output.getvalue())

        result_output = io.StringIO()
        with redirect_stdout(result_output):
            model_library.render_catalog_primary_result(
                {
                    "action": "set",
                    "changed": True,
                    "identity_key": self.identity,
                    "selection": {"status": "match"},
                    "home": {"rank": 1, "node_id": "node-b"},
                },
                width=48,
            )
        self.assertLessEqual(
            max(len(line) for line in result_output.getvalue().splitlines()),
            48,
        )


if __name__ == "__main__":
    unittest.main()
