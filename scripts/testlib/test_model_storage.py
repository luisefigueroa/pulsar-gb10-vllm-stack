#!/usr/bin/env python3
"""Renderer and contract tests for the read-only Models & storage experience."""

from __future__ import annotations

import copy
import contextlib
import io
import unittest

from scripts import model_storage


REVISION = "7" * 40
MANIFEST = "8" * 64


def healthy_report() -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "pulsar-model-library-health",
        "state": "healthy",
        "catalog": {
            "status": "cached",
            "topology_compatible": True,
            "refreshed_at": "2026-08-12T12:00:00.000Z",
        },
        "models": [{
            "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
            "revision": REVISION,
            "profiles": ["deepseek-v4-flash"],
            "expected_manifest": MANIFEST,
            "validation": "expected-unverified",
            "home_ranks": [1],
            "primary": {
                "mode": "automatic-single-home",
                "status": "match",
                "rank": 1,
            },
            "duplicate_home": "none",
        }],
        "hot_instances": [
            {
                "rank": 0,
                "profile": "deepseek-v4-flash",
                "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "sealed-hot",
                "retention": "ephemeral",
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": False,
                "repairable": False,
                "repair_id": None,
            },
            {
                "rank": 1,
                "profile": "deepseek-v4-flash",
                "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "durable-home",
                "retention": "ephemeral",
                "identity_status": "match",
                "witness_status": "match",
                "active_reference": False,
                "repairable": False,
                "repair_id": None,
            },
        ],
        "issues": [],
    }


def capture(function, *args, **kwargs) -> str:
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        function(*args, **kwargs)
    return stream.getvalue()


def normalized(value: str) -> str:
    return " ".join(value.split())


class ModelStorageContracts(unittest.TestCase):
    def test_health_schema_is_validated_fail_closed(self) -> None:
        report = healthy_report()
        self.assertIs(model_storage.validate_report(report), report)
        for field, value in (
            ("schema_version", 2),
            ("kind", "other"),
            ("state", "ready"),
        ):
            with self.subTest(field=field):
                invalid = healthy_report()
                invalid[field] = value
                with self.assertRaises(model_storage.ModelStorageContractError):
                    model_storage.validate_report(invalid)

        invalid = healthy_report()
        invalid["issues"] = [{
            "code": "bad-remediation",
            "detail": "fixture",
            "remediation": "not-an-object",
        }]
        with self.assertRaises(model_storage.ModelStorageContractError):
            model_storage.validate_report(invalid)

    def test_summary_keeps_default_and_claim_boundary_visible(self) -> None:
        output = capture(model_storage.render_summary, healthy_report(), width=48)
        prose = normalized(output)
        self.assertIn("MODELS & STORAGE", output)
        self.assertIn("replicated local model copies", prose)
        self.assertIn("guided default", prose)
        self.assertIn("experimental read-only", prose)
        self.assertIn("does not refresh", prose)
        self.assertIn("start a model", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

    def test_choices_are_compact_and_profile_first(self) -> None:
        labels = model_storage.model_choice_labels(healthy_report(), width=48)
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].startswith("deepseek-v4-flash"))
        self.assertLessEqual(len(labels[0]), 43)

    def test_stale_topology_never_maps_cached_placement_to_current_nodes(self) -> None:
        report = healthy_report()
        report["state"] = "attention"
        report["catalog"]["topology_compatible"] = False
        labels = model_storage.model_choice_labels(report, width=80)
        self.assertIn("placement stale", labels[0])
        self.assertNotIn("home n2", labels[0])

        output = capture(model_storage.render_detail, report, 0, width=80)
        catalog_detail = normalized(output.split("Runtime views", 1)[0])
        prose = normalized(output)
        self.assertIn("home unavailable · cached topology is stale", catalog_detail)
        self.assertIn("primary unavailable · refresh catalog", catalog_detail)
        self.assertNotIn("home node 2", catalog_detail)
        self.assertNotIn("primary node 2", catalog_detail)
        self.assertIn("duplicates cached none · topology stale", catalog_detail)
        self.assertIn("placement cannot be confirmed", prose)

    def test_long_colliding_labels_remain_a_supported_display_case(self) -> None:
        report = healthy_report()
        first = report["models"][0]
        first["profiles"] = [
            "very-long-model-profile-with-identical-prefix-alpha"
        ]
        second = copy.deepcopy(first)
        second["model_id"] = "example/second-model"
        second["profiles"] = [
            "very-long-model-profile-with-identical-prefix-beta"
        ]
        second["revision"] = "9" * 40
        report["models"] = [first, second]
        labels = model_storage.model_choice_labels(report, width=48)
        self.assertEqual(labels[0], labels[1])

    def test_detail_exposes_exact_identity_views_and_dependency(self) -> None:
        output = capture(
            model_storage.render_detail, healthy_report(), 0, width=48
        )
        prose = normalized(output)
        compact = "".join(output.split())
        self.assertIn(REVISION, compact)
        self.assertIn(MANIFEST, compact)
        self.assertIn("node 2 (rank 1)", prose)
        self.assertIn("sealed hot", prose)
        self.assertIn("durable home", prose)
        self.assertIn("witness match", prose)
        self.assertIn("do not provide durable-home-loss", prose)
        self.assertIn("do not establish model qualification", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

        wide_output = capture(
            model_storage.render_detail, healthy_report(), 0, width=80
        )
        duplicate_line = next(
            line for line in wide_output.splitlines() if line.startswith("duplicates")
        )
        self.assertEqual(duplicate_line, "duplicates none")

    def test_findings_show_remediation_without_running_it(self) -> None:
        report = healthy_report()
        report["state"] = "attention"
        report["issues"] = [{
            "code": "catalog-topology-stale",
            "detail": "cached catalog differs from confirmed topology",
            "remediation": {
                "command": "scripts/model-library.sh catalog refresh",
            },
        }]
        output = capture(model_storage.render_findings, report, width=48)
        prose = normalized(output)
        self.assertIn("catalog topology stale", prose)
        self.assertIn("Next: scripts/model-library.sh", prose)
        self.assertIn("catalog refresh", prose)

    def test_not_configured_preserves_replicated_availability(self) -> None:
        report = {
            "schema_version": 1,
            "kind": "pulsar-model-library-health",
            "state": "not-configured",
            "catalog": {
                "status": "absent",
                "topology_compatible": None,
            },
            "models": [],
            "hot_instances": [],
            "issues": [],
        }
        model_storage.validate_report(report)
        output = capture(model_storage.render_summary, report, width=48)
        prose = normalized(output)
        self.assertIn("No cached distributed catalog", prose)
        self.assertIn("Replicated serving remains available", prose)
        self.assertIn("catalog refresh", prose)

    def test_about_states_storage_invariants(self) -> None:
        output = capture(model_storage.render_about, width=48)
        prose = normalized(output)
        self.assertIn("one durable home", prose)
        self.assertIn("only on non-home", prose)
        self.assertIn("still requires the durable home", prose)
        self.assertIn("remain experimental", prose)


if __name__ == "__main__":
    unittest.main(verbosity=2)
