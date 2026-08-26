#!/usr/bin/env python3
"""Renderer and contract tests for the Models & storage experience."""

from __future__ import annotations

import copy
import contextlib
import io
import unittest

from scripts import model_storage


REVISION = "7" * 40
MANIFEST = "8" * 64


def serving_profiles() -> dict[str, object]:
    return {
        "models": [
            {
                "id": "qwen3.8-27b-fp8-2node",
                "status": "untested",
                "nodes": 2,
                "source": "hf",
                "purpose": "serving",
                "weights_gib": 29.0,
                "reviewed_identity": False,
                "reviewed_model_id": None,
                "reviewed_revision": None,
                "reviewed_manifest": None,
            },
            {
                "id": "legacy-serving",
                "status": "tested",
                "nodes": 2,
                "source": "hf",
                "purpose": "serving",
                "weights_gib": 10.0,
                "reviewed_identity": False,
                "reviewed_model_id": None,
                "reviewed_revision": None,
                "reviewed_manifest": None,
            },
        ]
    }


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
            "model_id": "Qwen/Qwen3.8-27B-FP8",
            "revision": REVISION,
            "profiles": ["qwen3.8-27b-fp8-2node"],
            "expected_manifest": None,
            "validation": "unvalidated",
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
                "profile": "qwen3.8-27b-fp8-2node",
                "model_id": "Qwen/Qwen3.8-27B-FP8",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "working-copy",
                "retention": "ephemeral",
                "identity_status": "receipt-occupancy",
                "witness_status": "match",
                "active_reference": False,
                "repairable": False,
                "repair_id": None,
            },
            {
                "rank": 1,
                "profile": "qwen3.8-27b-fp8-2node",
                "model_id": "Qwen/Qwen3.8-27B-FP8",
                "revision": REVISION,
                "metadata_schema": 3,
                "metadata_status": "current",
                "runtime_source": "durable-home",
                "retention": "ephemeral",
                "identity_status": "receipt-occupancy",
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

        profiles = serving_profiles()
        self.assertIs(model_storage.validate_profiles(profiles), profiles)
        invalid_profiles = serving_profiles()
        invalid_profiles["models"][0]["reviewed_identity"] = "yes"
        with self.assertRaises(model_storage.ModelStorageContractError):
            model_storage.validate_profiles(invalid_profiles)

    def test_summary_keeps_default_and_claim_boundary_visible(self) -> None:
        output = capture(model_storage.render_summary, healthy_report(), width=48)
        prose = normalized(output)
        self.assertIn("MODELS & STORAGE", output)
        self.assertIn("model library", prose)
        self.assertIn("only weight mechanism", prose)
        self.assertIn("read-only inventory", prose)
        self.assertIn("does not automatically refresh", prose)
        self.assertIn("start a model", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

    def test_choices_are_compact_and_profile_first(self) -> None:
        labels = model_storage.model_choice_labels(healthy_report(), width=48)
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].startswith("qwen3.8-27b-fp8-2node"))
        self.assertLessEqual(len(labels[0]), 43)

    def test_stale_topology_never_maps_cached_placement_to_current_nodes(self) -> None:
        report = healthy_report()
        report["state"] = "attention"
        report["catalog"]["topology_compatible"] = False
        labels = model_storage.model_choice_labels(report, width=80)
        self.assertIn("placement stale", labels[0])
        self.assertNotIn("home n2", labels[0])

        output = capture(
            model_storage.render_detail,
            report,
            serving_profiles(),
            0,
            width=80,
        )
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
            model_storage.render_detail,
            healthy_report(),
            serving_profiles(),
            0,
            width=48,
        )
        prose = normalized(output)
        compact = "".join(output.split())
        self.assertIn(REVISION, compact)
        self.assertIn("no reviewed expected manifest", prose)
        self.assertIn("node 2 (rank 1)", prose)
        self.assertIn("working copy on other node", prose)
        self.assertIn("durable home", prose)
        self.assertIn("witness match", prose)
        self.assertIn("do not provide durable-home-loss", prose)
        self.assertIn("do not establish model qualification", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

        wide_output = capture(
            model_storage.render_detail,
            healthy_report(),
            serving_profiles(),
            0,
            width=80,
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

    def test_not_configured_keeps_running_services_unaffected(self) -> None:
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
        self.assertIn("running services are unaffected", prose)
        self.assertIn("catalog refresh", prose)

    def test_about_states_storage_invariants(self) -> None:
        output = capture(model_storage.render_about, width=48)
        prose = normalized(output)
        self.assertIn("one durable home", prose)
        self.assertIn("only on non-home", prose)
        self.assertIn("still requires the durable home", prose)
        self.assertIn("Every live profile", prose)
        self.assertIn("local files on every rank", prose)

    def test_refresh_preview_is_explicit_bounded_and_width_aware(self) -> None:
        output = capture(
            model_storage.render_refresh, healthy_report(), width=48
        )
        prose = normalized(output)
        self.assertIn("REFRESH DISTRIBUTED CATALOG", output)
        self.assertIn("every confirmed rank", prose)
        self.assertIn("atomically updates the cached inventory", prose)
        self.assertIn("preserves explicit exact-revision primary selections", prose)
        self.assertIn("fails without fallback", prose)
        self.assertIn("does not download, copy, prepare, start", prose)
        self.assertIn("delete model files", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

    def test_preparation_check_allows_only_current_reviewed_serving_profile(self) -> None:
        report = healthy_report()
        check = model_storage.preparation_check(
            report, serving_profiles(), 0
        )
        self.assertEqual(check["state"], "available")
        self.assertEqual(
            [item["profile"] for item in check["candidates"]],
            ["qwen3.8-27b-fp8-2node"],
        )
        self.assertTrue(check["candidates"][0]["already_prepared"])
        self.assertNotIn("legacy-serving", str(check))

        report["hot_instances"] = []
        check = model_storage.preparation_check(
            report, serving_profiles(), 0
        )
        self.assertFalse(check["candidates"][0]["already_prepared"])

    def test_preparation_check_blocks_stale_or_unsealed_identity(self) -> None:
        report = healthy_report()
        report["catalog"]["topology_compatible"] = False
        stale = model_storage.preparation_check(
            report, serving_profiles(), 0
        )
        self.assertEqual(stale["state"], "blocked")
        self.assertIn("refresh the catalog", " ".join(stale["blockers"]))

        report = healthy_report()
        report["models"][0]["expected_manifest"] = None
        unsealed = model_storage.preparation_check(
            report, serving_profiles(), 0
        )
        self.assertEqual(unsealed["state"], "available")

        profiles = serving_profiles()
        profiles["models"][0]["reviewed_revision"] = "9" * 40
        mismatch = model_storage.preparation_check(
            healthy_report(), profiles, 0
        )
        self.assertEqual(mismatch["state"], "available")

    def test_preparation_preview_exposes_policy_and_claim_boundaries(self) -> None:
        output = capture(
            model_storage.render_preparation,
            healthy_report(),
            serving_profiles(),
            0,
            0,
            width=48,
        )
        prose = normalized(output)
        self.assertIn("PREPARE FOR TWO-RANK SERVING", output)
        self.assertIn("SSH over confirmed RoCE · 8 streams", prose)
        self.assertIn("fallback none", prose)
        self.assertIn("29 GiB on each non-home", prose)
        self.assertIn("full-verify the durable home", prose)
        self.assertIn("does not start or qualify a model", prose)
        self.assertIn("durable home remains required", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))

    def test_serving_check_requires_exact_ready_views(self) -> None:
        ready = model_storage.serving_preparation_check(
            healthy_report(), serving_profiles(), "qwen3.8-27b-fp8-2node"
        )
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["target_ranks"], [0, 1])
        self.assertEqual(ready["prepare_transport"], "ssh-roce")
        self.assertEqual(ready["copy_streams"], 8)

        report = healthy_report()
        report["hot_instances"] = report["hot_instances"][:1]
        missing = model_storage.serving_preparation_check(
            report, serving_profiles(), "qwen3.8-27b-fp8-2node"
        )
        self.assertEqual(missing["state"], "needs-preparation")

    def test_one_node_serving_check_requires_the_home_rank(self) -> None:
        profiles = serving_profiles()
        profile = copy.deepcopy(profiles["models"][0])
        profile["id"] = "one-node-sealed"
        profile["nodes"] = 1
        profiles["models"] = [profile]
        report = healthy_report()
        report["models"][0]["profiles"] = ["one-node-sealed"]
        report["hot_instances"] = [{
            **report["hot_instances"][1],
            "profile": "one-node-sealed",
        }]

        ready = model_storage.serving_preparation_check(
            report, profiles, "one-node-sealed", target_rank=1
        )
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["transfer"], "none · durable-home local view")
        self.assertEqual(ready["prepare_transport"], "ssh-control")
        self.assertEqual(ready["copy_streams"], 1)
        preview = capture(
            model_storage.render_serving_preparation, ready, width=48
        )
        self.assertIn("DISTRIBUTED CATALOG · ONE-RANK SERVING", preview)

        blocked = model_storage.serving_preparation_check(
            report, profiles, "one-node-sealed", target_rank=0
        )
        self.assertEqual(blocked["state"], "blocked")
        self.assertEqual(blocked["home_rank"], 1)
        self.assertIn("durable-home node", " ".join(blocked["blockers"]))

    def test_serving_preview_preserves_claim_boundary(self) -> None:
        check = model_storage.serving_preparation_check(
            healthy_report(), serving_profiles(), "qwen3.8-27b-fp8-2node"
        )
        output = capture(model_storage.render_serving_preparation, check, width=48)
        prose = normalized(output)
        self.assertIn("DISTRIBUTED CATALOG · TWO-RANK SERVING", output)
        self.assertIn("exact, witnessed runtime views", prose)
        self.assertIn("durable home remains required", prose)
        self.assertIn("does not establish model qualification", prose)
        self.assertTrue(all(len(line) <= 48 for line in output.splitlines()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
