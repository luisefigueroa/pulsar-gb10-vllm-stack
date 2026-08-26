#!/usr/bin/env python3
"""Contracts for Model Serving Release and frozen Validation Contract schemas."""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import unittest
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import model_identity, model_serving_release  # noqa: E402
from scripts.testlib import model_serving_release_fixture as fixture  # noqa: E402

HF_TOKEN_SHAPED_VALUE = "hf_" + ("A1b2C3d4" * 4) + "Z9"


def rebuild_recipe(
    recipe: dict[str, Any],
    **changes: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "artifact_bindings": recipe["artifact_bindings"],
        "engine_args": recipe["engine_args"],
        "container_env": recipe["container_env"],
        "gpu_memory_utilization": recipe["gpu_memory_utilization"],
        "spec_decode_args": recipe["speculative_decoding"]["arguments"],
        "spec_decode_enabled_by_default": recipe["speculative_decoding"][
            "enabled_by_default"
        ],
        "model_access_contract": recipe["model_access_contract"],
        "tensor_parallel_size": recipe["parallelism"]["tensor_parallel_size"],
        "pipeline_parallel_size": recipe["parallelism"][
            "pipeline_parallel_size"
        ],
        "weights_ram_gib": recipe["memory_policy"]["weights_ram_gib"],
        "kv_gib": recipe["memory_policy"]["kv_gib"],
        "overhead_gib": recipe["memory_policy"]["overhead_gib"],
        "mem_min_free_gib": recipe["memory_policy"]["mem_min_free_gib"],
        "engine": recipe["engine"],
    }
    values.update(changes)
    return model_serving_release.build_serving_recipe(**values)


class ModelServingReleaseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = fixture.build_release()
        self.contract = fixture.build_contract(release=self.release)

    def test_fixture_has_frozen_deterministic_identities(self) -> None:
        self.assertEqual(self.release["release_id"], fixture.EXPECTED_RELEASE_ID)
        self.assertEqual(self.contract["contract_id"], fixture.EXPECTED_CONTRACT_ID)
        self.assertEqual(
            set(model_serving_release.model_serving_release_identity(self.release)),
            {
                "model_artifact_set",
                "serving_recipe",
                "runtime_image_identity",
                "supported_hardware_geometry",
            },
        )

    def test_documents_survive_canonical_json_round_trip(self) -> None:
        release = json.loads(
            json.dumps(self.release, sort_keys=True, separators=(",", ":"))
        )
        contract = json.loads(
            json.dumps(self.contract, sort_keys=True, separators=(",", ":"))
        )
        self.assertEqual(
            model_serving_release.validate_model_serving_release(release),
            self.release,
        )
        self.assertEqual(
            model_serving_release.validate_validation_contract(
                contract,
                expected_release=release,
            ),
            self.contract,
        )

    def test_builders_normalize_set_like_inputs_and_decimals(self) -> None:
        artifacts = model_serving_release.build_model_artifact_set(
            list(reversed(fixture.model_artifacts()))
        )
        recipe = rebuild_recipe(
            self.release["serving_recipe"],
            artifact_bindings=list(
                reversed(self.release["serving_recipe"]["artifact_bindings"])
            ),
            container_env=list(
                reversed(self.release["serving_recipe"]["container_env"])
            ),
            gpu_memory_utilization="0.8000",
            weights_ram_gib="40.00",
            kv_gib="20.0",
        )
        rebuilt = fixture.build_release(artifact_set=artifacts, recipe=recipe)
        contract = fixture.build_contract(
            release=rebuilt,
            release_criteria=list(reversed(fixture.criteria())),
        )
        self.assertEqual(rebuilt, self.release)
        self.assertEqual(contract, self.contract)
        self.assertEqual(recipe["gpu_memory_utilization"], "0.8")
        self.assertEqual(recipe["memory_policy"]["weights_ram_gib"], "40")
        self.assertEqual(
            contract["release_criteria"]["context_requirement"]["depths"],
            ["0.05", "0.5", "0.95"],
        )

    def test_each_release_tuple_part_changes_the_release_id(self) -> None:
        artifacts_input = fixture.model_artifacts()
        artifacts_input[2]["manifest"]["manifest_id"] = "1" * 64
        changed_artifacts = fixture.build_release(
            artifact_set=model_serving_release.build_model_artifact_set(
                artifacts_input
            )
        )

        changed_recipe = fixture.build_release(
            recipe=fixture.build_recipe(
                engine_args=[
                    "--max-model-len",
                    "65536",
                    "--distributed-executor-backend",
                    "mp",
                ]
            )
        )
        changed_runtime = fixture.build_release(
            runtime=fixture.build_runtime(
                image_reference="mirror.invalid/vllm@sha256:" + ("1" * 64)
            )
        )
        changed_geometry = fixture.build_release(
            geometry=fixture.build_geometry(
                hardware_class="nvidia-dgx-spark-gb10-revision-b"
            )
        )

        ids = {
            changed_artifacts["release_id"],
            changed_recipe["release_id"],
            changed_runtime["release_id"],
            changed_geometry["release_id"],
        }
        self.assertEqual(len(ids), 4)
        self.assertNotIn(self.release["release_id"], ids)

    def test_engine_argument_order_is_identity_but_registry_name_is_not(self) -> None:
        args = list(self.release["serving_recipe"]["engine_args"])
        args[0], args[2] = args[2], args[0]
        reordered = fixture.build_release(
            recipe=rebuild_recipe(self.release["serving_recipe"], engine_args=args)
        )
        self.assertNotEqual(reordered["release_id"], self.release["release_id"])

        alternate_reference = fixture.build_runtime(
            image_reference="another.invalid/renamed@sha256:" + ("f" * 64)
        )
        self.assertEqual(
            alternate_reference,
            self.release["runtime_image_identity"],
        )
        self.assertEqual(
            fixture.build_release(runtime=alternate_reference)["release_id"],
            self.release["release_id"],
        )

    def test_live_remote_readonly_is_retired_for_new_plans(self) -> None:
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            r"retired \(ADR 0005\)",
        ):
            fixture.build_recipe(model_access_contract="live-remote-readonly")

    def test_content_addressed_model_can_be_the_primary_artifact(self) -> None:
        release = fixture.build_content_addressed_release()
        validated = model_serving_release.validate_model_serving_release(release)
        self.assertEqual(
            validated["release_id"],
            fixture.EXPECTED_CONTENT_ADDRESSED_RELEASE_ID,
        )
        primary = validated["model_artifact_set"]["artifacts"][0]
        self.assertEqual(primary["kind"], "content-addressed-model")
        self.assertNotIn("status", validated)
        self.assertNotIn("source_path", json.dumps(validated))

        changed = fixture.content_addressed_model_artifact()
        changed["manifest"]["manifest_id"] = "8" * 64
        rebuilt = fixture.build_release(
            artifact_set=model_serving_release.build_model_artifact_set([changed]),
            recipe=fixture.build_primary_only_recipe(),
        )
        self.assertNotEqual(rebuilt["release_id"], release["release_id"])

    def test_generic_digest_artifact_cannot_be_the_primary_model(self) -> None:
        artifact = {
            "artifact_key": "primary",
            "kind": "digest-artifact",
            "artifact_id": "fixture/not-a-complete-model",
            "revision": "v1",
            "digest": {"scheme": "sha256", "value": "7" * 64},
        }
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "complete content-addressed model",
        ):
            fixture.build_release(
                artifact_set=model_serving_release.build_model_artifact_set(
                    [artifact]
                ),
                recipe=fixture.build_primary_only_recipe(),
            )

    def test_content_addressed_model_rejects_private_identity_values(self) -> None:
        for field, value in (
            ("artifact_id", "/mnt/private/model"),
            ("revision", "https://catalog.internal/revision"),
        ):
            artifact = fixture.content_addressed_model_artifact()
            artifact[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    model_serving_release.build_model_artifact_set([artifact])

    def test_numeric_version_range_helpers_enforce_boundaries(self) -> None:
        self.assertEqual(
            model_serving_release.parse_numeric_version_range(">=6.11,<6.12"),
            ((6, 11), (6, 12)),
        )
        cases = (
            (">=580,<590", ("580", "589.99"), ("579.99", "590")),
            (">=28,<29", ("28", "28.99"), ("27.99", "29")),
            (
                ">=6.11,<6.12",
                ("6.11", "6.11.0", "6.11.99"),
                ("6.10.99", "6.12", "7"),
            ),
        )
        for version_range, included, excluded in cases:
            for observed in included:
                with self.subTest(
                    version_range=version_range,
                    observed=observed,
                    expected=True,
                ):
                    self.assertTrue(
                        model_serving_release.numeric_version_in_range(
                            observed,
                            version_range,
                        )
                    )
            for observed in excluded:
                with self.subTest(
                    version_range=version_range,
                    observed=observed,
                    expected=False,
                ):
                    self.assertFalse(
                        model_serving_release.numeric_version_in_range(
                            observed,
                            version_range,
                        )
                    )

    def test_deployed_vendor_versions_compare_by_numeric_core(self) -> None:
        cases = (
            ("580.173.02", ">=580,<590", True),
            ("6.17.0-1026-nvidia", ">=6.17,<6.18", True),
            ("29.2.1-ce", ">=29,<30", True),
            ("6.18.0-1000-nvidia", ">=6.17,<6.18", False),
        )
        for observed, version_range, expected in cases:
            with self.subTest(observed=observed, version_range=version_range):
                self.assertEqual(
                    model_serving_release.numeric_version_in_range(
                        observed,
                        version_range,
                    ),
                    expected,
                )

        for malformed in ("6..17-vendor", "6.17-", "v6.17.0", " 6.17.0"):
            with self.subTest(malformed=malformed):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "dotted numeric version with optional vendor suffix",
                ):
                    model_serving_release.numeric_version_in_range(
                        malformed,
                        ">=6.17,<6.18",
                    )

    def test_numeric_versions_and_ranges_reject_noncanonical_forms(self) -> None:
        for observed in ("", "06.11", "6..11", "6.11-rc1", " 6.11"):
            with self.subTest(observed=observed):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "canonical dotted numeric version",
                ):
                    model_serving_release.parse_numeric_version(observed)

        malformed_ranges = (
            "580",
            ">=580,<=590",
            ">=580, <590",
            ">=0580,<590",
            ">=580.,<590",
            ">=580.0-rc1,<590",
            ">=580,<",
            ">=580,<580",
            ">=590,<580",
        )
        range_fields = {
            "driver_abi_range": ">=580,<590",
            "container_runtime_range": ">=28,<29",
            "kernel_range": ">=6.11,<6.12",
        }
        for field in range_fields:
            for malformed in malformed_ranges:
                with self.subTest(field=field, malformed=malformed):
                    values = dict(range_fields)
                    values[field] = malformed
                    with self.assertRaisesRegex(
                        model_serving_release.ModelServingReleaseError,
                        "canonical >=LOW,<HIGH|lower endpoint must be less",
                    ):
                        fixture.build_runtime(**values)

    def test_runtime_architecture_must_match_supported_geometry(self) -> None:
        runtime = fixture.build_runtime(architecture="x86-64")
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "runtime host architecture differs",
        ):
            fixture.build_release(runtime=runtime)

        persisted = copy.deepcopy(self.release)
        persisted["runtime_image_identity"]["host_compatibility"][
            "architecture"
        ] = "x86-64"
        persisted["release_id"] = model_serving_release.model_serving_release_id(
            persisted
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "runtime host architecture differs",
        ):
            model_serving_release.validate_model_serving_release(persisted)

    def test_recipe_parallelism_must_match_supported_geometry(self) -> None:
        recipe = fixture.build_recipe(tensor_parallel_size=1)
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "tensor parallelism differs",
        ):
            fixture.build_release(recipe=recipe)

    def test_structured_engine_flags_cannot_be_duplicated(self) -> None:
        for arguments in (
            ["--gpu-memory-utilization", "0.8"],
            ["-tp", "2"],
            ["--speculative-model=draft"],
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "repeats structured field",
                ):
                    fixture.build_recipe(engine_args=arguments)

    def test_recipe_environment_rejects_credentials_and_placement(self) -> None:
        for item in ("VLLM_API_KEY=secret", "HF_HOME=/private/cache"):
            with self.subTest(item=item):
                recipe = self.release["serving_recipe"]
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "credential or deployment-only|private, secret",
                ):
                    rebuild_recipe(recipe, container_env=[item])

    def test_recipe_values_reject_secrets_paths_and_endpoints(self) -> None:
        recipe = self.release["serving_recipe"]
        mutations = (
            {
                "container_env": [
                    f"PUBLIC_SETTING={HF_TOKEN_SHAPED_VALUE}"
                ],
            },
            {
                "engine_args": ["--config", "/home/operator/runtime.json"],
            },
            {
                "spec_decode_args": [
                    "--speculative-config",
                    '{"model":"/mnt/private/draft"}',
                ],
            },
            {
                "engine_args": ["--endpoint", "https://lab.internal:8000"],
            },
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    rebuild_recipe(recipe, **changes)

    def test_persisted_documents_require_canonical_decimal_strings(self) -> None:
        for value in ("0.80", 1):
            with self.subTest(value=value):
                release = copy.deepcopy(self.release)
                release["serving_recipe"]["gpu_memory_utilization"] = value
                release["release_id"] = (
                    model_serving_release.model_serving_release_id(release)
                )
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "not canonical",
                ):
                    model_serving_release.validate_model_serving_release(release)

    def test_release_rejects_status_evidence_and_site_specific_fields(self) -> None:
        for field, value in (
            ("status", "Validated"),
            ("evidence", ["results/example.json"]),
            ("reviewer", "fixture-reviewer"),
            ("issued_at", "2026-08-14T00:00:00Z"),
            ("distribution_transport", "ssh-roce"),
            ("physical_placement", {"rank": 0}),
        ):
            with self.subTest(field=field):
                release = copy.deepcopy(self.release)
                release[field] = value
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "fields differ",
                ):
                    model_serving_release.validate_model_serving_release(release)

        release = copy.deepcopy(self.release)
        release["supported_hardware_geometry"]["node_ids"] = ["rank-a", "rank-b"]
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "fields differ",
        ):
            model_serving_release.validate_model_serving_release(release)

    def test_contract_has_criteria_not_results_or_issuance(self) -> None:
        self.assertNotIn("status", self.contract)
        self.assertNotIn("evidence", self.contract)
        self.assertNotIn("reviewer", self.contract)
        self.assertNotIn("issued_at", self.contract)
        self.assertEqual(self.contract["release_id"], self.release["release_id"])

    def test_contract_fails_closed_when_any_required_dimension_is_missing(self) -> None:
        for dimension in model_serving_release.VALIDATION_DIMENSIONS:
            with self.subTest(dimension=dimension):
                incomplete = [
                    item
                    for item in fixture.criteria()
                    if item["dimension"] != dimension
                ]
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "missing required dimensions",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=incomplete,
                    )

    def test_every_dimension_rejects_every_noncanonical_scope(self) -> None:
        expected_scopes = (
            model_serving_release.VALIDATION_DIMENSION_QUALIFICATION_SCOPES
        )
        self.assertEqual(
            self.contract["repository_invariants"][
                "dimension_qualification_scopes"
            ],
            expected_scopes,
        )
        for criterion_index, criterion in enumerate(fixture.criteria()):
            expected_scope = expected_scopes[criterion["dimension"]]
            for wrong_scope in sorted(
                model_serving_release.QUALIFICATION_SCOPES - {expected_scope}
            ):
                with self.subTest(
                    dimension=criterion["dimension"],
                    wrong_scope=wrong_scope,
                ):
                    criteria = fixture.criteria()
                    criteria[criterion_index]["qualification_scope"] = wrong_scope
                    with self.assertRaisesRegex(
                        model_serving_release.ModelServingReleaseError,
                        f"qualification_scope must be {expected_scope}",
                    ):
                        fixture.build_contract(
                            release=self.release,
                            release_criteria=criteria,
                        )

    def test_all_catalog_scope_laundering_fails_closed(self) -> None:
        criteria = fixture.criteria()
        for criterion in criteria:
            criterion["qualification_scope"] = "catalog-artifact"
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "qualification_scope must be",
        ):
            fixture.build_contract(
                release=self.release,
                release_criteria=criteria,
            )

    def test_strict_same_boot_cannot_be_relaxed_to_fp_equivalence(self) -> None:
        for field, value in (
            ("comparison", "fp-equivalent"),
            ("fp_equivalent_satisfies", True),
        ):
            with self.subTest(field=field):
                criteria = fixture.criteria()
                strict = next(
                    item
                    for item in criteria
                    if item["dimension"] == "strict-same-boot"
                )
                strict["protocol"]["parameters"][field] = value
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "strict-same-boot",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )

        criteria = fixture.criteria()
        strict = next(
            item for item in criteria if item["dimension"] == "strict-same-boot"
        )
        strict["thresholds"][0]["value"] = "0.99"
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "exact_match_rate",
        ):
            fixture.build_contract(
                release=self.release,
                release_criteria=criteria,
            )

    def test_provenance_security_requires_exact_review_template(self) -> None:
        mutated_criteria: list[tuple[str, list[dict[str, Any]]]] = []

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["protocol"]["parameters"]["reviewed_issuance_required"] = False
        mutated_criteria.append(("required review disabled", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["thresholds"][0]["value"] = "pending"
        mutated_criteria.append(("review threshold changed", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["thresholds"].append(
            {
                "metric": "security_findings",
                "operator": "eq",
                "value": "0",
                "unit": "count",
            }
        )
        mutated_criteria.append(("extra security findings threshold", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["protocol"]["parameters"]["allow_open_findings"] = True
        mutated_criteria.append(("extra protocol parameter", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["workload"]["parameters"]["review_subset"] = "security-only"
        mutated_criteria.append(("extra workload parameter", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["sample_size"] = 2
        mutated_criteria.append(("sample size changed", criteria))

        criteria = fixture.criteria()
        provenance = next(
            item for item in criteria if item["dimension"] == "provenance-security"
        )
        provenance["workload"]["name"] = "partial-release-inputs"
        mutated_criteria.append(("workload changed", criteria))

        for mutation, criteria in mutated_criteria:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "canonical review-derived template",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )

    def test_no_predecessor_keeps_absolute_performance_required(self) -> None:
        relative = self.contract["release_criteria"]["relative_performance"]
        self.assertEqual(relative, model_serving_release.no_comparable_predecessor())
        dimensions = {
            item["dimension"]
            for item in self.contract["release_criteria"]["criteria"]
        }
        self.assertIn("throughput", dimensions)
        self.assertIn("latency", dimensions)

    def test_relative_budgets_bind_predecessor_protocol_and_geometry(self) -> None:
        criteria = fixture.criteria()
        throughput = next(
            item for item in criteria if item["dimension"] == "throughput"
        )
        latency = next(item for item in criteria if item["dimension"] == "latency")
        relative = model_serving_release.build_relative_performance_requirement(
            release=self.release,
            predecessor_release_id="1" * 64,
            predecessor_contract_id="2" * 64,
            predecessor_bundle_id="3" * 64,
            predecessor_decision_id="4" * 64,
            throughput_criterion=throughput,
            throughput_predecessor_criterion_id="predecessor-throughput",
            throughput_predecessor_run_record_id="5" * 64,
            latency_criterion=latency,
            latency_predecessor_criterion_id="predecessor-latency",
            latency_predecessor_run_record_id="6" * 64,
            throughput_max_regression_percent="5.00",
            latency_max_regression_percent="10.0",
        )
        contract = fixture.build_contract(
            release=self.release,
            release_criteria=criteria,
            relative_performance=relative,
        )
        observed = contract["release_criteria"]["relative_performance"]
        self.assertEqual(observed["throughput"]["maximum_regression_percent"], "5")
        self.assertEqual(observed["latency"]["maximum_regression_percent"], "10")
        self.assertEqual(observed["predecessor_contract_id"], "2" * 64)
        self.assertEqual(observed["predecessor_bundle_id"], "3" * 64)
        self.assertEqual(observed["predecessor_decision_id"], "4" * 64)
        self.assertEqual(
            observed["throughput"]["predecessor_criterion_id"],
            "predecessor-throughput",
        )
        self.assertEqual(
            observed["throughput"]["predecessor_run_record_id"],
            "5" * 64,
        )
        self.assertEqual(
            observed["latency"]["predecessor_criterion_id"],
            "predecessor-latency",
        )
        self.assertEqual(
            observed["latency"]["predecessor_run_record_id"],
            "6" * 64,
        )
        self.assertEqual(
            observed["supported_hardware_geometry_id"],
            model_serving_release.supported_hardware_geometry_id(
                self.release["supported_hardware_geometry"]
            ),
        )

        bad_protocol = copy.deepcopy(contract)
        bad_protocol["release_criteria"]["relative_performance"]["throughput"][
            "benchmark_protocol_id"
        ] = "2" * 64
        bad_protocol["contract_id"] = model_serving_release.validation_contract_id(
            bad_protocol
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "protocol identity mismatch",
        ):
            model_serving_release.validate_validation_contract(
                bad_protocol,
                expected_release=self.release,
            )

        bad_geometry = copy.deepcopy(relative)
        bad_geometry["supported_hardware_geometry_id"] = "3" * 64
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "geometry mismatch",
        ):
            fixture.build_contract(
                release=self.release,
                release_criteria=criteria,
                relative_performance=bad_geometry,
            )

    def test_relative_budgets_require_shaped_predecessor_source_ids(self) -> None:
        criteria = fixture.criteria()
        throughput = next(
            item for item in criteria if item["dimension"] == "throughput"
        )
        latency = next(item for item in criteria if item["dimension"] == "latency")
        relative = model_serving_release.build_relative_performance_requirement(
            release=self.release,
            predecessor_release_id="1" * 64,
            predecessor_contract_id="2" * 64,
            predecessor_bundle_id="3" * 64,
            predecessor_decision_id="4" * 64,
            throughput_criterion=throughput,
            throughput_predecessor_criterion_id="predecessor-throughput",
            throughput_predecessor_run_record_id="5" * 64,
            latency_criterion=latency,
            latency_predecessor_criterion_id="predecessor-latency",
            latency_predecessor_run_record_id="6" * 64,
            throughput_max_regression_percent="5",
            latency_max_regression_percent="10",
        )

        for field in (
            "predecessor_release_id",
            "predecessor_contract_id",
            "predecessor_bundle_id",
            "predecessor_decision_id",
        ):
            with self.subTest(field=field):
                malformed = copy.deepcopy(relative)
                malformed[field] = "not-a-content-id"
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "sha256 content ID",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                        relative_performance=malformed,
                    )

        for dimension in ("throughput", "latency"):
            with self.subTest(dimension=dimension, field="predecessor_criterion_id"):
                malformed = copy.deepcopy(relative)
                malformed[dimension]["predecessor_criterion_id"] = "not safe"
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "predecessor_criterion_id is invalid",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                        relative_performance=malformed,
                    )
            with self.subTest(dimension=dimension, field="predecessor_run_record_id"):
                malformed = copy.deepcopy(relative)
                malformed[dimension]["predecessor_run_record_id"] = "not-a-digest"
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "sha256 content ID",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                        relative_performance=malformed,
                    )

    def test_protocol_identity_excludes_pass_threshold(self) -> None:
        criterion = next(
            item for item in fixture.criteria() if item["dimension"] == "throughput"
        )
        changed_threshold = copy.deepcopy(criterion)
        changed_threshold["thresholds"][0]["value"] = "25"
        self.assertEqual(
            model_serving_release.benchmark_protocol_id(criterion),
            model_serving_release.benchmark_protocol_id(changed_threshold),
        )

    def test_context_and_soak_references_are_checked(self) -> None:
        bad_context = copy.deepcopy(self.contract)
        bad_context["release_criteria"]["context_requirement"]["criterion_ids"] = [
            "missing-criterion"
        ]
        bad_context["contract_id"] = model_serving_release.validation_contract_id(
            bad_context
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "references unknown criterion",
        ):
            model_serving_release.validate_validation_contract(
                bad_context,
                expected_release=self.release,
            )

        bad_soak = copy.deepcopy(self.contract)
        bad_soak["release_criteria"]["soak_requirement"]["criterion_id"] = (
            "latency-ttft"
        )
        bad_soak["contract_id"] = model_serving_release.validation_contract_id(
            bad_soak
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "must reference stability",
        ):
            model_serving_release.validate_validation_contract(
                bad_soak,
                expected_release=self.release,
            )

    def test_extensible_parameters_reject_floats_and_private_keys(self) -> None:
        for field, value, message in (
            ("temperature", 0.0, "not floats"),
            ("host", "private-node", "private field"),
            ("mount_path", "/private/model", "private field"),
        ):
            with self.subTest(field=field):
                criteria = fixture.criteria()
                criteria[0]["protocol"]["parameters"][field] = value
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    message,
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )

    def test_extensible_parameters_reject_credential_bearing_keys(self) -> None:
        for field in ("api_key", "serviceApiKey", "hf_token", "client_secret"):
            with self.subTest(field=field):
                criteria = fixture.criteria()
                criteria[0]["protocol"]["parameters"][field] = "plain-old-value"
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "credential-bearing field",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )

    def test_extensible_parameter_values_reject_private_data(self) -> None:
        private_values: tuple[Any, ...] = (
            "/home/operator/private-result.json",
            "node-a",
            "endpoint=https://lab.internal:8000",
            HF_TOKEN_SHAPED_VALUE,
            {"nested": ["topology_id=site-topology"]},
        )
        for private_value in private_values:
            with self.subTest(private_value=private_value):
                criteria = fixture.criteria()
                criteria[0]["protocol"]["parameters"]["description"] = private_value
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )
    def test_not_applicable_reasons_reject_private_values(self) -> None:
        release_criteria = self.contract["release_criteria"]
        for requirement in ("context_requirement", "soak_requirement"):
            with self.subTest(requirement=requirement):
                context = copy.deepcopy(release_criteria["context_requirement"])
                soak = copy.deepcopy(release_criteria["soak_requirement"])
                replacement = {
                    "status": "not-applicable",
                    "reason": HF_TOKEN_SHAPED_VALUE,
                }
                if requirement == "context_requirement":
                    context = replacement
                else:
                    soak = replacement
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    model_serving_release.build_validation_contract(
                        release=self.release,
                        criteria=fixture.criteria(),
                        context_requirement=context,
                        soak_requirement=soak,
                        relative_performance=(
                            model_serving_release.no_comparable_predecessor()
                        ),
                    )

    def test_public_parameter_values_preserve_content_identities(self) -> None:
        criteria = fixture.criteria()
        criteria[0]["protocol"]["parameters"]["public_identity"] = {
            "model_id": "Fixture/Public-Model",
            "manifest_id": "a" * 64,
            "revision": "fixture-public-v1",
        }
        contract = fixture.build_contract(
            release=self.release,
            release_criteria=criteria,
        )
        throughput = next(
            item
            for item in contract["release_criteria"]["criteria"]
            if item["criterion_id"] == "throughput-serving"
        )
        self.assertEqual(
            throughput["protocol"]["parameters"]["public_identity"]["model_id"],
            "Fixture/Public-Model",
        )

    def test_dotted_and_hf_prefixed_public_identifiers_are_preserved(self) -> None:
        criteria = fixture.criteria()
        accuracy = next(
            item for item in criteria if item["dimension"] == "accuracy"
        )
        accuracy["workload"]["name"] = "accuracy.mmlu"
        accuracy["protocol"]["name"] = "torch.distributed"
        accuracy["protocol"]["parameters"]["hf_transfer_mode"] = (
            "hf_public_adapter"
        )
        accuracy["thresholds"][0]["metric"] = "tokens.per.second"
        contract = fixture.build_contract(
            release=self.release,
            release_criteria=criteria,
        )
        observed = next(
            item
            for item in contract["release_criteria"]["criteria"]
            if item["criterion_id"] == accuracy["criterion_id"]
        )
        self.assertEqual(observed["workload"]["name"], "accuracy.mmlu")
        self.assertEqual(observed["protocol"]["name"], "torch.distributed")
        self.assertEqual(
            observed["protocol"]["parameters"]["hf_transfer_mode"],
            "hf_public_adapter",
        )
        self.assertEqual(observed["thresholds"][0]["metric"], "tokens.per.second")

    def test_open_contract_strings_reject_secret_like_values(self) -> None:
        mutations = ("criterion_id", "workload_name", "threshold_value")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                criteria = fixture.criteria()
                throughput = next(
                    item for item in criteria if item["dimension"] == "throughput"
                )
                if mutation == "criterion_id":
                    throughput["criterion_id"] = HF_TOKEN_SHAPED_VALUE
                elif mutation == "workload_name":
                    throughput["workload"]["name"] = HF_TOKEN_SHAPED_VALUE
                else:
                    throughput["thresholds"][0]["value"] = HF_TOKEN_SHAPED_VALUE
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    fixture.build_contract(
                        release=self.release,
                        release_criteria=criteria,
                    )

        criteria = fixture.criteria()
        throughput = next(
            item for item in criteria if item["dimension"] == "throughput"
        )
        throughput["thresholds"][0]["value"] = (
            "PUBLIChf_abcdefghijklmnopqrstuvwxyz123456"
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "private, secret, or deployment-only",
        ):
            fixture.build_contract(
                release=self.release,
                release_criteria=criteria,
            )

    def test_model_domain_token_terminology_is_not_a_credential(self) -> None:
        criterion = next(
            item for item in fixture.criteria() if item["dimension"] == "throughput"
        )
        criterion["criterion_id"] = "token-throughput-public-v2"
        criterion["workload"]["name"] = "token-generation-evaluation"
        criterion["protocol"]["name"] = "tokenizer-benchmark"
        criterion["thresholds"][0] = {
            "metric": "time_to_first_token",
            "operator": "lte",
            "value": "1500",
            "unit": "milliseconds-per-token",
        }
        self.assertEqual(
            model_serving_release.validate_validation_criterion(criterion),
            criterion,
        )

    def test_digest_artifact_identity_rejects_absolute_site_path(self) -> None:
        artifacts = fixture.model_artifacts()
        adapter = next(item for item in artifacts if item["kind"] == "digest-artifact")
        adapter["artifact_id"] = "/home/operator/private-adapter"
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "private, secret, or deployment-only",
        ):
            model_serving_release.build_model_artifact_set(artifacts)

    def test_open_release_and_contract_strings_preserve_public_identifiers(
        self,
    ) -> None:
        artifacts = fixture.model_artifacts()
        adapter = next(item for item in artifacts if item["kind"] == "digest-artifact")
        adapter["artifact_id"] = "Fixture/Public-Adapter"
        adapter["revision"] = "release-v1.2"
        artifact_set = model_serving_release.build_model_artifact_set(artifacts)
        observed_adapter = next(
            item
            for item in artifact_set["artifacts"]
            if item["kind"] == "digest-artifact"
        )
        self.assertEqual(observed_adapter["artifact_id"], "Fixture/Public-Adapter")
        self.assertEqual(observed_adapter["revision"], "release-v1.2")

        criterion = next(
            item for item in fixture.criteria() if item["dimension"] == "throughput"
        )
        criterion["criterion_id"] = "throughput-public-v2"
        criterion["workload"]["name"] = "public-eval-v2"
        criterion["workload"]["version"] = "dataset-v2"
        criterion["protocol"]["name"] = "public-benchmark-v2"
        criterion["protocol"]["version"] = "protocol-v2"
        criterion["thresholds"][0] = {
            "metric": "public-throughput",
            "operator": "gte",
            "value": "public-baseline-v2",
            "unit": "public-units",
        }
        self.assertEqual(
            model_serving_release.validate_validation_criterion(criterion),
            criterion,
        )

    def test_contract_changes_never_change_release_identity(self) -> None:
        criteria = fixture.criteria()
        accuracy = next(
            item for item in criteria if item["dimension"] == "accuracy"
        )
        accuracy["thresholds"][0]["value"] = "0.75"
        changed = fixture.build_contract(
            release=self.release,
            release_criteria=criteria,
        )
        self.assertNotEqual(changed["contract_id"], self.contract["contract_id"])
        self.assertEqual(changed["release_id"], self.release["release_id"])

    def test_builder_type_errors_fail_as_schema_errors(self) -> None:
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "contain only objects",
        ):
            model_serving_release.build_model_artifact_set(
                [None]  # type: ignore[list-item]
            )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "required_kernel_features must be strings",
        ):
            model_serving_release.build_runtime_image_identity(
                image_reference="registry.invalid/x@sha256:" + ("f" * 64),
                architecture="aarch64",
                driver_abi_family="fixture",
                driver_abi_range="1",
                container_runtime_family="docker",
                container_runtime_range="1",
                required_container_capabilities=["gpu"],
                kernel_range="1",
                required_kernel_features=[1],  # type: ignore[list-item]
            )

    def test_public_string_helper_matches_private_alias(self) -> None:
        public = model_serving_release.validate_public_string_value
        private = model_serving_release._validate_public_string_value
        self.assertEqual(
            public("fixture-public-id", label="sample"),
            "fixture-public-id",
        )
        self.assertEqual(
            public("fixture-public-id", label="sample"),
            private("fixture-public-id", label="sample"),
        )
        with self.assertRaisesRegex(
            model_serving_release.ModelServingReleaseError,
            "must be a string",
        ):
            public(1, label="sample")
        for value in (
            "token=secret",
            "/home/operator/private-result.json",
            "https://lab.internal:8000",
            "node-a.local",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    public(value, label="sample")
                with self.assertRaisesRegex(
                    model_serving_release.ModelServingReleaseError,
                    "private, secret, or deployment-only",
                ):
                    private(value, label="sample")

    def test_legacy_schema_one_seals_are_archived_not_loaded(self) -> None:
        self.assertFalse((REPO_ROOT / "models" / "seals").exists())
        self.assertFalse((REPO_ROOT / "models" / "validation-bundles").exists())
        archive = REPO_ROOT / "docs" / "archive" / "schema-1-expected-seal"
        seals = sorted((archive / "seals").glob("*.json"))
        bundles = sorted((archive / "validation-bundles").glob("*.json"))
        self.assertTrue(seals)
        self.assertTrue(bundles)
        for path in seals:
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["kind"], "pulsar-expected-model-seal")
        for path in bundles:
            document = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 1)
            self.assertEqual(document["kind"], "pulsar-validation-bundle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
