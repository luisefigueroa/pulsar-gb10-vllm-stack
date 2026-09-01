#!/usr/bin/env python3
"""Contracts for ADR 0004 attempt composition from validator measurements."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import (  # noqa: E402
    model_serving_release,
    model_serving_release_attempt as attempt,
    model_serving_release_capture as capture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_attempt_fixture as fixture,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402
import validator_measurement  # noqa: E402


CLI = REPO_ROOT / "scripts" / "model-serving-release-attempt.sh"
FORBIDDEN_CRITERIA = {
    "accuracy-gsm8k",
    "stability-soak",
    "provenance-security-review",
    "serving-integration-smoke",
    "physical-geometry-dgx",
}


class ModelServingReleaseAttemptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-msra-"))
        self.repo = self.tmpdir / "repo"
        fixture.seed_attempt_repo(self.repo)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_main(self, arguments: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = attempt.main(["--repo-root", str(self.repo), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [str(CLI), *args, "--repo-root", str(self.repo)],
            cwd=str(REPO_ROOT),
            env=merged,
            text=True,
            capture_output=True,
            check=False,
        )

    def compose_dir(self, name: str = "compose") -> pathlib.Path:
        return (
            self.repo
            / "experiments"
            / "model-serving-release-attempts"
            / name
        )

    def compose(self, inputs: dict[str, object], extra: list[str] | None = None) -> dict[str, object]:
        output_dir = self.compose_dir()
        if output_dir.exists():
            shutil.rmtree(output_dir)
        args = [
            "compose",
            "--release-plan",
            str(inputs["plan_dir"]),
            "--context",
            str(inputs["context_path"]),
            "--compare-measurement",
            str(inputs["compare_path"]),
            "--benchmark-measurement",
            str(inputs["bench_path"]),
            "--output-dir",
            str(output_dir),
            "--json",
        ]
        if extra:
            args.extend(extra)
        code, stdout, stderr = self.run_main(args)
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        payload["_output_dir"] = str(output_dir)
        return payload

    def load_specs(self, payload: dict[str, object]) -> dict[str, dict[str, object]]:
        output_dir = pathlib.Path(str(payload["_output_dir"]))
        return {
            "compare-captures": json.loads(
                (output_dir / "compare-captures.attempt-spec.json").read_text(
                    encoding="utf-8"
                )
            ),
            "benchmark-serving": json.loads(
                (output_dir / "benchmark-serving.attempt-spec.json").read_text(
                    encoding="utf-8"
                )
            ),
        }

    def plan_capture(self, inputs: dict[str, object], spec: dict[str, object]) -> int:
        spec_path = self.repo / "capture-specs" / f"{spec['attempt']['attempt_id']}.json"
        fixture.write_json(spec_path, spec)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = capture.main(
                [
                    "--repo-root",
                    str(self.repo),
                    "plan",
                    "--release-plan",
                    str(inputs["plan_dir"]),
                    "--attempt-spec",
                    str(spec_path),
                    "--json",
                ]
            )
        self.assertEqual(code, 0, stderr.getvalue() + stdout.getvalue())
        planned = json.loads(stdout.getvalue())
        self.assertTrue(planned["ok"])
        self.assertEqual(planned["state"], "draft")
        self.assertEqual(planned["authority"], "none")
        return code

    def compose_extra(
        self,
        *,
        release: dict[str, object],
        contract: dict[str, object],
        operation: str,
        measurement: dict[str, object],
        evidence_path: str,
    ) -> dict[str, object]:
        plan_dir, _candidate = fixture.capture_fixture.write_release_plan_candidate(
            self.repo, release, contract
        )
        measurement_path = self.repo.joinpath(*pathlib.PurePosixPath(evidence_path).parts)
        fixture.write_json(measurement_path, measurement)
        context = fixture.attempt_context(release=release)
        context["attempts"] = {
            operation: {
                "attempt_id": ("d" if operation == "evaluate-gsm8k" else "e") * 64,
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": (
                    "2026-08-14T14:30:00Z"
                    if operation == "validate-soak"
                    else "2026-08-14T12:30:00Z"
                ),
            }
        }
        ended_at = context["attempts"][operation]["ended_at"]
        duration = "9000" if operation == "validate-soak" else "1800"
        context["evidence_sources"] = {
            operation: {
                "source_key": operation,
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": evidence_path,
            }
        }
        resource_path = f"results/extra/{operation}-resources.json"
        context["resource_diagnostic_sources"] = {
            operation: {
                "source_key": f"resource-{operation}",
                "class": "publishable",
                "qualification_scope": "model-qualification",
                "media_type": "application/json",
                "repository_path": resource_path,
            }
        }
        fixture.write_json(
            self.repo.joinpath(*pathlib.PurePosixPath(resource_path).parts),
            fixture.resource_measurement(
                release=release,
                started_at="2026-08-14T12:00:00Z",
                ended_at=ended_at,
                duration=duration,
            ),
        )
        context_path = self.repo / f"{operation}-context.json"
        fixture.write_json(context_path, context)
        output_dir = self.compose_dir(operation)
        code, stdout, stderr = self.run_main(
            [
                "compose-extra",
                "--release-plan",
                str(plan_dir),
                "--context",
                str(context_path),
                "--operation",
                operation,
                "--measurement",
                str(measurement_path),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        spec = json.loads(
            (output_dir / attempt.ATTEMPT_SPEC_NAMES[operation]).read_text(
                encoding="utf-8"
            )
        )
        self.plan_capture({"plan_dir": plan_dir}, spec)
        return spec

    def test_strict_mapping_uses_identical_record_rate(self) -> None:
        compare = fixture.complete_compare_measurement(
            identical_record_count=0,
            exact_text_count=30,
            diagnostic_verdict="fp-equivalent",
        )
        inputs = fixture.prepare_compose_inputs(self.repo, compare=compare)
        specs = self.load_specs(self.compose(inputs))
        observation = specs["compare-captures"]["criterion_observations"][0]
        self.assertEqual(observation["criterion_id"], "strict-same-boot-captures")
        self.assertEqual(observation["completion"], "complete")
        self.assertEqual(
            observation["metrics"],
            [{"metric": "exact_match_rate", "unit": "ratio", "value": "0"}],
        )
        self.assertNotEqual(observation["metrics"][0]["value"], "1")

    def test_matching_benchmark_protocol_maps_p95_not_p50(self) -> None:
        bench = fixture.complete_bench_measurement(
            levels=[
                fixture.complete_bench_level(ttft_p50_ms="20", ttft_p95_ms="88")
            ]
        )
        inputs = fixture.prepare_compose_inputs(self.repo, bench=bench)
        specs = self.load_specs(self.compose(inputs))
        by_id = {
            item["criterion_id"]: item
            for item in specs["benchmark-serving"]["criterion_observations"]
        }
        self.assertEqual(
            by_id["throughput-serving"]["metrics"],
            [
                {
                    "metric": "output_tokens_per_second",
                    "unit": "tokens-per-second",
                    "value": "25",
                }
            ],
        )
        self.assertEqual(
            by_id["latency-ttft"]["metrics"],
            [{"metric": "ttft_p95", "unit": "milliseconds", "value": "88"}],
        )
        self.assertNotEqual(by_id["latency-ttft"]["metrics"][0]["value"], "20")

    def test_resource_diagnostic_is_run_evidence_not_criterion_or_review(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        spec = self.load_specs(self.compose(inputs))["benchmark-serving"]
        self.assertEqual(
            spec["run_diagnostic_source_keys"],
            ["resource-benchmark-serving"],
        )
        self.assertEqual(spec["review_source_keys"], [])
        observation_keys = {
            key
            for observation in spec["criterion_observations"]
            for key in observation["evidence_source_keys"]
        }
        self.assertNotIn("resource-benchmark-serving", observation_keys)
        self.assertIn(
            "scripts/model-serving-experiment-monitor.sh",
            [item["program"] for item in spec["commands"]],
        )

    def test_incomplete_resource_diagnostic_does_not_rewrite_criterion_result(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        resource_path = self.repo.joinpath(
            *pathlib.PurePosixPath(fixture.BENCH_RESOURCE_RESULT).parts
        )
        unavailable_rank = {
            "rank": "single",
            "collection_status": "unavailable",
            "sample_count": 0,
            "workload_sample_count": 0,
            "mem_available_min_bytes": None,
            "swap_used_max_bytes": None,
            "node_memory_pressure_some_total_delta_us": None,
            "workload_memory_current_max_bytes": None,
            "workload_memory_peak_start_bytes": None,
            "workload_memory_peak_end_bytes": None,
            "workload_swap_current_max_bytes": None,
            "oom_delta": None,
            "oom_kill_delta": None,
        }
        fixture.write_json(
            resource_path,
            validator_measurement.build_resource_measurement(
                completion="incomplete",
                reason="no-samples",
                payload={
                    "started_at": "2026-08-14T12:05:00Z",
                    "ended_at": "2026-08-14T12:15:00Z",
                    "duration_seconds": "600",
                    "qualification_scope": "model-qualification",
                    "sample_interval_seconds": "1",
                    "expected_rank_count": 1,
                    "observed_rank_count": 0,
                    "sample_count": 0,
                    "ranks": [unavailable_rank],
                },
            ),
        )
        spec = self.load_specs(self.compose(inputs))["benchmark-serving"]
        self.assertEqual(spec["attempt"]["completion"], "completed")

    def test_missing_resource_diagnostic_is_an_explicit_capture_gap(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        self.repo.joinpath(
            *pathlib.PurePosixPath(fixture.BENCH_RESOURCE_RESULT).parts
        ).unlink()
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir()),
            ]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("resource diagnostic is missing", stderr)

    def test_accuracy_extra_attempt_maps_closed_measurement(self) -> None:
        release = release_fixture.build_release()
        criteria = release_fixture.criteria()
        accuracy = next(item for item in criteria if item["dimension"] == "accuracy")
        accuracy["protocol"] = {
            "name": "pulsar-gsm8k-exact-answer",
            "version": "1",
            "parameters": {
                "answer_normalization": "gsm8k-final-number-v1",
                "max_completion_tokens": 4096,
                "reasoning_mode": "enabled",
                "temperature": "0",
            },
        }
        accuracy["workload"]["parameters"] = {
            "dataset_id": "openai/gsm8k",
            "dataset_revision": "a" * 40,
            "dataset_file_sha256": "b" * 64,
            "subset": "main",
            "split": "test",
            "selection": "sha256-order-first-100",
        }
        sibling = copy.deepcopy(accuracy)
        sibling["criterion_id"] = "accuracy-secondary"
        sibling["workload"]["parameters"]["dataset_revision"] = "c" * 40
        sibling["workload"]["parameters"]["dataset_file_sha256"] = "d" * 64
        criteria.append(sibling)
        sample_sibling = copy.deepcopy(accuracy)
        sample_sibling["criterion_id"] = "accuracy-sample-secondary"
        sample_sibling["sample_size"] = 50
        criteria.append(sample_sibling)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        measurement = validator_measurement.build_accuracy_measurement(
            completion="complete",
            reason="completed",
            payload={
                **accuracy["workload"]["parameters"],
                **accuracy["protocol"]["parameters"],
                "dataset_file_sha256": "b" * 64,
                "requested_sample_count": 100,
                "measured_sample_count": 100,
                "correct_count": 85,
                "request_error_count": 0,
                "accuracy": "0.85",
            },
        )
        spec = self.compose_extra(
            release=release,
            contract=contract,
            operation="evaluate-gsm8k",
            measurement=measurement,
            evidence_path="results/extra/accuracy.json",
        )
        observation = spec["criterion_observations"][0]
        self.assertEqual(
            spec["attempt"]["attempted_criterion_ids"], ["accuracy-gsm8k"]
        )
        incomplete = validator_measurement.build_accuracy_measurement(
            completion="incomplete",
            reason="request-failed",
            payload={
                **accuracy["workload"]["parameters"],
                **accuracy["protocol"]["parameters"],
                "requested_sample_count": 100,
                "measured_sample_count": 99,
                "correct_count": 84,
                "request_error_count": 1,
                "accuracy": None,
            },
        )
        self.assertEqual(
            [
                item["criterion_id"]
                for item in attempt.criteria_for_operation(
                    contract,
                    "evaluate-gsm8k",
                    measurement=incomplete,
                )
            ],
            ["accuracy-gsm8k"],
        )
        self.assertEqual(observation["criterion_id"], "accuracy-gsm8k")
        self.assertEqual(
            observation["metrics"],
            [{"metric": "accuracy", "unit": "ratio", "value": "0.85"}],
        )
        variants = []
        wrong_digest = copy.deepcopy(accuracy)
        wrong_digest["workload"]["parameters"]["dataset_file_sha256"] = "c" * 64
        variants.append(("dataset-digest", wrong_digest))
        for component in ("workload", "protocol"):
            changed_name = copy.deepcopy(accuracy)
            changed_name[component]["name"] += "-variant"
            variants.append((f"{component}-name", changed_name))
            extra = copy.deepcopy(accuracy)
            extra[component]["parameters"]["unsupported"] = True
            variants.append((f"{component}-parameter", extra))
            changed_version = copy.deepcopy(accuracy)
            changed_version[component]["version"] = "2"
            variants.append((f"{component}-version", changed_version))
        for label, variant in variants:
            with self.subTest(label=label):
                mapped = attempt._map_accuracy_criterion(
                    variant, measurement, source_state="completed"
                )
                self.assertFalse(mapped["ok"])
                self.assertEqual(mapped["reason"], "protocol-mismatch")

    def test_short_soak_attempt_is_preserved_as_inconclusive(self) -> None:
        release = release_fixture.build_release()
        criteria = release_fixture.criteria()
        stability = next(item for item in criteria if item["dimension"] == "stability")
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        measurement = validator_measurement.build_soak_measurement(
            completion="complete",
            reason="completed",
            payload={
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T14:13:20Z",
                "duration_seconds": "8000",
                "concurrency": 5,
                "completed_requests": 499,
                "request_error_count": 1,
            },
        )
        spec = self.compose_extra(
            release=release,
            contract=contract,
            operation="validate-soak",
            measurement=measurement,
            evidence_path="results/extra/short-soak.json",
        )
        self.assertEqual(spec["attempt"]["completion"], "inconclusive")
        observation = spec["criterion_observations"][0]
        self.assertEqual(observation["completion"], "inconclusive")
        self.assertEqual(observation["reason"], "short-sample")
        self.assertEqual(observation["sample_size"], 499)
        self.assertEqual(
            observation["contract_requirements"]["soak"]["duration_seconds"],
            "8000",
        )
        self.assertEqual(
            observation["contract_requirements"]["soak"]["request_errors"],
            1,
        )

    def test_soak_extra_attempt_maps_nested_requirement(self) -> None:
        release = release_fixture.build_release()
        criteria = release_fixture.criteria()
        stability = next(item for item in criteria if item["dimension"] == "stability")
        stability["protocol"]["parameters"] = {"concurrency": 5}
        secondary = copy.deepcopy(stability)
        secondary["criterion_id"] = "stability-secondary"
        criteria.append(secondary)
        different = copy.deepcopy(stability)
        different["criterion_id"] = "stability-different"
        different["protocol"]["parameters"]["concurrency"] = 4
        criteria.append(different)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        measurement = validator_measurement.build_soak_measurement(
            completion="complete",
            reason="completed",
            payload={
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T14:30:00Z",
                "duration_seconds": "9000",
                "concurrency": 5,
                "completed_requests": 500,
                "request_error_count": 0,
            },
        )
        spec = self.compose_extra(
            release=release,
            contract=contract,
            operation="validate-soak",
            measurement=measurement,
            evidence_path="results/extra/soak.json",
        )
        observations = {
            item["criterion_id"]: item for item in spec["criterion_observations"]
        }
        observation = observations["stability-soak"]
        self.assertEqual(observation["criterion_id"], "stability-soak")
        self.assertEqual(
            observation["metrics"],
            [{"metric": "request_error_count", "unit": "count", "value": "0"}],
        )
        self.assertEqual(
            observation["contract_requirements"]["soak"]["duration_seconds"],
            "9000",
        )
        self.assertEqual(
            spec["run_diagnostic_source_keys"],
            ["resource-validate-soak"],
        )
        self.assertIsNone(
            observations["stability-secondary"]["contract_requirements"]["soak"]
        )
        incomplete = validator_measurement.build_soak_measurement(
            completion="incomplete",
            reason="zero-completions",
            payload={
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T12:00:00Z",
                "duration_seconds": "0",
                "concurrency": 5,
                "completed_requests": 0,
                "request_error_count": 0,
            },
        )
        self.assertEqual(
            [
                item["criterion_id"]
                for item in attempt.criteria_for_operation(
                    contract,
                    "validate-soak",
                    measurement=incomplete,
                )
            ],
            ["stability-secondary", "stability-soak"],
        )
        variants = []
        for component in ("workload", "protocol"):
            changed_name = copy.deepcopy(stability)
            changed_name[component]["name"] += "-variant"
            variants.append((f"{component}-name", changed_name))
            extra = copy.deepcopy(stability)
            extra[component]["parameters"]["unsupported"] = True
            variants.append((f"{component}-parameter", extra))
            changed_version = copy.deepcopy(stability)
            changed_version[component]["version"] = "2"
            variants.append((f"{component}-version", changed_version))
        for label, variant in variants:
            with self.subTest(label=label):
                mapped = attempt._map_soak_criterion(
                    variant, measurement, source_state="completed"
                )
                self.assertFalse(mapped["ok"])
                self.assertEqual(mapped["reason"], "protocol-mismatch")

    def test_missing_corrupt_short_unsupported_mismatch_interrupt(self) -> None:
        inputs = fixture.prepare_compose_inputs(
            self.repo, compare=fixture.incomplete_compare_measurement()
        )
        payload = self.compose(inputs)
        specs = self.load_specs(payload)
        compare = specs["compare-captures"]
        self.assertEqual(compare["attempt"]["completion"], "failed")
        self.assertEqual(
            compare["criterion_observations"][0]["completion"], "inconclusive"
        )
        self.assertEqual(
            compare["criterion_observations"][0]["reason"], "unusable-input"
        )
        self.assertEqual(
            specs["benchmark-serving"]["attempt"]["completion"], "completed"
        )

        fixture.write_json(inputs["compare_path"], {"kind": "not-a-measurement"})
        payload = self.compose(inputs)
        specs = self.load_specs(payload)
        self.assertEqual(
            specs["compare-captures"]["attempt"]["completion"], "failed"
        )
        self.assertEqual(
            specs["compare-captures"]["criterion_observations"][0]["reason"],
            "corrupt-measurement",
        )

        short = fixture.complete_bench_measurement(
            levels=[fixture.complete_bench_level(measured_request_count=16)],
            explicit_request_count=16,
        )
        inputs = fixture.prepare_compose_inputs(self.repo, bench=short)
        specs = self.load_specs(self.compose(inputs))
        bench = specs["benchmark-serving"]
        self.assertEqual(bench["attempt"]["completion"], "inconclusive")
        reasons = {item["reason"] for item in bench["criterion_observations"]}
        self.assertEqual(reasons, {"short-sample"})

        mismatched = fixture.complete_bench_measurement(
            levels=[fixture.complete_bench_level(concurrency=1)]
        )
        inputs = fixture.prepare_compose_inputs(self.repo, bench=mismatched)
        specs = self.load_specs(self.compose(inputs))
        reasons = {
            item["reason"]
            for item in specs["benchmark-serving"]["criterion_observations"]
        }
        self.assertEqual(reasons, {"protocol-mismatch"})
        self.assertEqual(
            specs["benchmark-serving"]["attempt"]["completion"], "inconclusive"
        )

        interrupted = fixture.complete_bench_measurement(
            completion="incomplete",
            reason="interrupted",
            levels=[
                {
                    **fixture.complete_bench_level(),
                    "completion": "incomplete",
                    "reason": "interrupted",
                    "ttft_p50_ms": None,
                    "ttft_p95_ms": None,
                    "decode_tps_p50": None,
                    "aggregate_tps": None,
                    "wall_s": None,
                }
            ],
        )
        inputs = fixture.prepare_compose_inputs(self.repo, bench=interrupted)
        specs = self.load_specs(self.compose(inputs))
        self.assertEqual(
            specs["benchmark-serving"]["attempt"]["completion"], "interrupted"
        )
        self.assertTrue(
            all(
                item["completion"] == "inconclusive"
                for item in specs["benchmark-serving"]["criterion_observations"]
            )
        )

    def test_one_validator_failure_does_not_rewrite_the_other(self) -> None:
        inputs = fixture.prepare_compose_inputs(
            self.repo, bench=fixture.incomplete_bench_measurement()
        )
        specs = self.load_specs(self.compose(inputs))
        self.assertEqual(
            specs["compare-captures"]["attempt"]["completion"], "completed"
        )
        self.assertEqual(
            specs["compare-captures"]["criterion_observations"][0]["metrics"][0][
                "value"
            ],
            "1",
        )
        self.assertEqual(
            specs["benchmark-serving"]["attempt"]["completion"], "inconclusive"
        )
        self.assertEqual(
            specs["benchmark-serving"]["criterion_observations"][0]["completion"],
            "inconclusive",
        )

    def test_generated_specs_are_accepted_by_capture_plan(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        specs = self.load_specs(self.compose(inputs))
        self.plan_capture(inputs, specs["compare-captures"])
        self.plan_capture(inputs, specs["benchmark-serving"])

    def test_no_deferred_criteria_are_declared(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        specs = self.load_specs(self.compose(inputs))
        declared = set(specs["compare-captures"]["attempt"]["attempted_criterion_ids"])
        declared.update(specs["benchmark-serving"]["attempt"]["attempted_criterion_ids"])
        self.assertEqual(
            declared,
            {"strict-same-boot-captures", "throughput-serving", "latency-ttft"},
        )
        self.assertTrue(declared.isdisjoint(FORBIDDEN_CRITERIA))

    def test_privacy_and_forbidden_claims_reject(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        context = json.loads(inputs["context_path"].read_text(encoding="utf-8"))
        context["validation_status"] = "validated"
        fixture.write_json(inputs["context_path"], context)
        output_dir = self.compose_dir("bad-out")
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(output_dir),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("forbidden field", stderr)

        context = fixture.attempt_context()
        context["observed_environment"]["hostname"] = "spark-a"
        fixture.write_json(inputs["context_path"], context)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("bad-out-2")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertRegex(stderr, "private field|hostname")

        context = fixture.attempt_context()
        context["evidence_sources"]["compare-captures"]["media_type"] = "text/plain"
        fixture.write_json(inputs["context_path"], context)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("bad-media-type")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("media_type must be application/json", stderr)

        context = fixture.attempt_context()
        context["notes"] = "http://127.0.0.1:8000"
        fixture.write_json(inputs["context_path"], context)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("bad-out-3")),
            ]
        )
        self.assertEqual(code, 1)

    def test_invocation_plan_and_human_width(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        code, stdout, stderr = self.run_main(
            ["plan-invocation", "--release-plan", str(inputs["plan_dir"]), "--json"]
        )
        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["kind"], attempt.INVOCATION_PLAN_KIND)
        self.assertEqual(plan["benchmark-serving"]["num_requests"], 100)
        self.assertEqual(plan["benchmark-serving"]["concurrency"], [8])
        self.assertEqual(plan["compare-captures"]["sample_size"], 30)
        argv = attempt.bench_argv(plan)
        self.assertEqual(
            argv[:4],
            ["--concurrency", "8", "--num-requests", "100"],
        )

        human = self.run_cli(
            "compose",
            "--release-plan",
            str(inputs["plan_dir"]),
            "--context",
            str(inputs["context_path"]),
            "--compare-measurement",
            str(inputs["compare_path"]),
            "--benchmark-measurement",
            str(inputs["bench_path"]),
            "--output-dir",
            str(self.compose_dir("human-out")),
            env={"COLUMNS": "40"},
        )
        self.assertEqual(human.returncode, 0, human.stderr)
        collapsed = " ".join(human.stdout.split())
        self.assertIn("no issuance authority", collapsed)
        self.assertIn("Authority none", collapsed)
        for line in human.stdout.splitlines():
            self.assertLessEqual(len(line), 40, line)
        self.assertNotIn(str(self.repo), human.stdout)
        self.assertNotIn("Validated", human.stdout)

    def test_unsupported_metric_is_inconclusive(self) -> None:
        release = release_fixture.build_release()
        criteria = []
        for item in release_fixture.criteria():
            if item["criterion_id"] == "latency-ttft":
                item = dict(item)
                item["thresholds"] = [
                    {
                        "metric": "ttft_p99",
                        "operator": "lte",
                        "value": "1500",
                        "unit": "milliseconds",
                    }
                ]
            criteria.append(item)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        plan_dir, _candidate = __import__(
            "scripts.testlib.model_serving_release_capture_fixture",
            fromlist=["write_release_plan_candidate"],
        ).write_release_plan_candidate(self.repo, release, contract)
        compare_path, bench_path = fixture.write_default_measurements(self.repo)
        context = fixture.attempt_context(release=release)
        context_path = self.repo / "attempt-context.json"
        fixture.write_json(context_path, context)
        output_dir = self.compose_dir("unsupported-out")
        code, stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(plan_dir),
                "--context",
                str(context_path),
                "--compare-measurement",
                str(compare_path),
                "--benchmark-measurement",
                str(bench_path),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        spec = json.loads(
            (output_dir / "benchmark-serving.attempt-spec.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(spec["attempt"]["completion"], "inconclusive")
        reasons = {item["reason"] for item in spec["criterion_observations"]}
        self.assertIn("unsupported-metric", reasons)
        self.assertNotIn("accuracy-gsm8k", spec["attempt"]["attempted_criterion_ids"])

    def test_invocation_plan_rejects_malicious_values(self) -> None:
        dest = self.tmpdir / "evil-plan.json"
        payload = {
            "schema_version": 1,
            "kind": attempt.INVOCATION_PLAN_KIND,
            "benchmark-serving": {
                "program": "/bin/sh",
                "operation": "benchmark-serving",
                "concurrency": ["$(reboot)"],
                "num_requests": True,
                "prompt_style": "not-a-style",
            },
        }
        dest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(attempt.ModelServingReleaseAttemptError):
            attempt.load_invocation_plan(dest)

        payload["benchmark-serving"] = {
            "program": "validate/bench_serve.py",
            "operation": "benchmark-serving",
            "concurrency": [8],
            "num_requests": 100,
        }
        dest.write_text(json.dumps(payload), encoding="utf-8")
        validated = attempt.load_invocation_plan(dest)
        self.assertEqual(
            attempt.bench_argv(validated),
            ["--concurrency", "8", "--num-requests", "100"],
        )

        payload["benchmark-serving"]["concurrency"] = [True]
        dest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(attempt.ModelServingReleaseAttemptError):
            attempt.bench_argv(attempt.load_invocation_plan(dest))

    def test_run_gates_does_not_ignore_bench_argv_failure(self) -> None:
        fake = self.tmpdir / "fake_attempt.py"
        fake.write_text("import sys\nsys.stderr.write('bad plan\\n')\nsys.exit(1)\n")
        plan = self.tmpdir / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        env = os.environ.copy()
        env["PULSAR_MODEL_SERVING_RELEASE_ATTEMPT_PY"] = str(fake)
        proc = subprocess.run(
            [
                str(REPO_ROOT / "validate" / "run-gates.sh"),
                "model",
                "--tag",
                "argvfail",
                "--invocation-plan",
                str(plan),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("invocation plan is invalid", proc.stderr)
        self.assertNotIn("gate 1:", proc.stdout)

    def test_measurement_must_match_publishable_evidence(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        other = self.repo / "results" / "other-measurement.json"
        fixture.write_json(other, fixture.complete_compare_measurement())
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(other),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("mismatch")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("does not match the bound evidence source", stderr)

        linked = self.repo / "results" / "linked-compare.json"
        linked.symlink_to(inputs["compare_path"])
        context = fixture.attempt_context(compare_path="results/linked-compare.json")
        fixture.write_json(inputs["context_path"], context)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(linked),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("symlink")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("symlink", stderr)

        context = fixture.attempt_context(
            compare_path="results/../models/seals/escape.json"
        )
        fixture.write_json(inputs["context_path"], context)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("escape")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertRegex(stderr, "normalized|unsafe|escape|path")

    def test_compare_protocol_mismatch_is_inconclusive(self) -> None:
        release = release_fixture.build_release()
        criteria = []
        for item in release_fixture.criteria():
            if item["criterion_id"] == "strict-same-boot-captures":
                item = json.loads(json.dumps(item))
                item["protocol"]["name"] = "token-prefix-compare"
                item["protocol"]["parameters"]["extra"] = "unsupported"
            criteria.append(item)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        from scripts.testlib import (
            model_serving_release_capture_fixture as capture_fixture,
        )

        plan_dir, _candidate = capture_fixture.write_release_plan_candidate(
            self.repo, release, contract
        )
        compare_path, bench_path = fixture.write_default_measurements(self.repo)
        context = fixture.attempt_context(release=release)
        context_path = self.repo / "attempt-context.json"
        fixture.write_json(context_path, context)
        output_dir = self.compose_dir("protocol-mismatch")
        code, stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(plan_dir),
                "--context",
                str(context_path),
                "--compare-measurement",
                str(compare_path),
                "--benchmark-measurement",
                str(bench_path),
                "--output-dir",
                str(output_dir),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        spec = json.loads(
            (output_dir / "compare-captures.attempt-spec.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(spec["attempt"]["completion"], "inconclusive")
        self.assertEqual(
            spec["criterion_observations"][0]["reason"], "protocol-mismatch"
        )
        self.assertEqual(spec["criterion_observations"][0]["completion"], "inconclusive")

    def test_output_dir_and_noreplace(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.repo / "models" / "nope"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("protected", stderr)

        first = self.compose(inputs)
        self.assertTrue(pathlib.Path(str(first["_output_dir"])).is_dir())
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(first["_output_dir"]),
            ]
        )
        self.assertEqual(code, 1)
        self.assertRegex(stderr, "already exists|refusing to overwrite")

        plan_out = (
            self.repo
            / "experiments"
            / "model-serving-release-attempts"
            / "invocation.json"
        )
        code, _stdout, stderr = self.run_main(
            [
                "plan-invocation",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--output",
                str(plan_out),
                "--json",
            ]
        )
        self.assertEqual(code, 0, stderr)
        code, _stdout, stderr = self.run_main(
            [
                "plan-invocation",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--output",
                str(plan_out),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("refusing to overwrite", stderr)

        escaped_parent = self.tmpdir / "escaped-plan-parent"
        escaped_parent.mkdir()
        linked_parent = (
            self.repo
            / "experiments"
            / "model-serving-release-attempts"
            / "linked-parent"
        )
        linked_parent.symlink_to(escaped_parent)
        code, _stdout, stderr = self.run_main(
            [
                "plan-invocation",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--output",
                str(linked_parent / "escaped.json"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertRegex(stderr, "symlink|unsafe|output parent")
        self.assertFalse((escaped_parent / "escaped.json").exists())

    def test_missing_measurement_is_refused_without_inventing_evidence(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo, write_compare=False)
        code, _stdout, stderr = self.run_main(
            [
                "compose",
                "--release-plan",
                str(inputs["plan_dir"]),
                "--context",
                str(inputs["context_path"]),
                "--compare-measurement",
                str(inputs["compare_path"]),
                "--benchmark-measurement",
                str(inputs["bench_path"]),
                "--output-dir",
                str(self.compose_dir("missing")),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("supply a validator --result-json", stderr)
        self.assertFalse(self.compose_dir("missing").exists())

    def test_unreadable_measurement_fails_without_unpack_error(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        dest = self.compose_dir("unreadable")
        original_read = attempt.read_stable_bytes

        def reject_measurement(path: Path, *, label: str) -> bytes:
            if label.endswith("measurement"):
                raise attempt.ValidatorMeasurementError("unsafe private path")
            return original_read(path, label=label)

        with mock.patch.object(
            attempt,
            "read_stable_bytes",
            side_effect=reject_measurement,
        ):
            code, _stdout, stderr = self.run_main(
                [
                    "compose",
                    "--release-plan",
                    str(inputs["plan_dir"]),
                    "--context",
                    str(inputs["context_path"]),
                    "--compare-measurement",
                    str(inputs["compare_path"]),
                    "--benchmark-measurement",
                    str(inputs["bench_path"]),
                    "--output-dir",
                    str(dest),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("measurement cannot be read safely", stderr)
        self.assertNotIn("not enough values to unpack", stderr)
        self.assertNotIn("unsafe private path", stderr)
        self.assertFalse(dest.exists())

    def test_validation_failure_leaves_no_partial_output(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        dest = self.compose_dir("partial")
        calls = {"n": 0}
        original = capture.build_capture_from_plan

        def boom(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] >= 2:
                raise capture.ModelServingReleaseCaptureError("injected failure")
            return original(*args, **kwargs)

        with mock.patch.object(
            capture, "build_capture_from_plan", side_effect=boom
        ):
            code, _stdout, stderr = self.run_main(
                [
                    "compose",
                    "--release-plan",
                    str(inputs["plan_dir"]),
                    "--context",
                    str(inputs["context_path"]),
                    "--compare-measurement",
                    str(inputs["compare_path"]),
                    "--benchmark-measurement",
                    str(inputs["bench_path"]),
                    "--output-dir",
                    str(dest),
                ]
            )
        self.assertEqual(code, 1, stderr)
        self.assertFalse(dest.exists())
        self.assertGreaterEqual(calls["n"], 2)

    def test_evidence_change_after_mapping_is_refused(self) -> None:
        inputs = fixture.prepare_compose_inputs(self.repo)
        dest = self.compose_dir("changed-evidence")
        original = capture.build_capture_from_plan
        calls = {"n": 0}

        def mutate_after_first(*args: object, **kwargs: object) -> object:
            result = original(*args, **kwargs)
            calls["n"] += 1
            if calls["n"] == 1:
                changed = fixture.complete_compare_measurement(
                    identical_record_count=29,
                    exact_text_count=29,
                    diagnostic_verdict="fp-equivalent",
                )
                fixture.write_json(inputs["compare_path"], changed)
            return result

        with mock.patch.object(
            capture, "build_capture_from_plan", side_effect=mutate_after_first
        ):
            code, _stdout, stderr = self.run_main(
                [
                    "compose",
                    "--release-plan",
                    str(inputs["plan_dir"]),
                    "--context",
                    str(inputs["context_path"]),
                    "--compare-measurement",
                    str(inputs["compare_path"]),
                    "--benchmark-measurement",
                    str(inputs["bench_path"]),
                    "--output-dir",
                    str(dest),
                ]
            )
        self.assertEqual(code, 1)
        self.assertIn("evidence changed during composition", stderr)
        self.assertFalse(dest.exists())

    def test_plan_invocation_rejects_unsupported_compare_protocol(self) -> None:
        release = release_fixture.build_release()
        criteria = []
        for item in release_fixture.criteria():
            if item["criterion_id"] == "strict-same-boot-captures":
                item = json.loads(json.dumps(item))
                item["protocol"]["name"] = "token-prefix-compare"
                item["protocol"]["parameters"]["extra"] = "unsupported"
            criteria.append(item)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        from scripts.testlib import (
            model_serving_release_capture_fixture as capture_fixture,
        )

        plan_dir, _candidate = capture_fixture.write_release_plan_candidate(
            self.repo, release, contract
        )
        with self.assertRaises(attempt.ModelServingReleaseAttemptError) as raised:
            attempt.plan_invocation(contract)
        self.assertIn("strict-same-boot invocation requires", str(raised.exception))
        code, _stdout, stderr = self.run_main(
            ["plan-invocation", "--release-plan", str(plan_dir), "--json"]
        )
        self.assertEqual(code, 1)
        self.assertIn("strict-same-boot invocation requires", stderr)

    def test_invocation_plan_rejects_duplicate_concurrency(self) -> None:
        dest = self.tmpdir / "dup-conc.json"
        dest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": attempt.INVOCATION_PLAN_KIND,
                    "benchmark-serving": {
                        "program": "validate/bench_serve.py",
                        "operation": "benchmark-serving",
                        "concurrency": [8, 8],
                        "num_requests": 100,
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(attempt.ModelServingReleaseAttemptError):
            attempt.load_invocation_plan(dest)

    def test_invocation_plan_rejects_too_few_requests_for_concurrency(self) -> None:
        release = release_fixture.build_release()
        criteria = []
        for item in release_fixture.criteria():
            item = json.loads(json.dumps(item))
            if item["criterion_id"] in {"throughput-serving", "latency-ttft"}:
                item["sample_size"] = 1
            criteria.append(item)
        contract = release_fixture.build_contract(
            release=release, release_criteria=criteria
        )
        with self.assertRaisesRegex(
            attempt.ModelServingReleaseAttemptError,
            "sample_size must be at least the largest declared concurrency",
        ):
            attempt.plan_invocation(contract)

        dest = self.tmpdir / "short-bench-plan.json"
        dest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": attempt.INVOCATION_PLAN_KIND,
                    "benchmark-serving": {
                        "program": "validate/bench_serve.py",
                        "operation": "benchmark-serving",
                        "concurrency": [8],
                        "num_requests": 1,
                    },
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            attempt.ModelServingReleaseAttemptError,
            "num_requests must be at least the largest concurrency",
        ):
            attempt.load_invocation_plan(dest)

    def test_run_gates_stops_after_interrupt(self) -> None:
        gate_repo = self.tmpdir / "gate-repo"
        validate_dir = gate_repo / "validate"
        validate_dir.mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "validate" / "run-gates.sh", validate_dir)
        mock_bin = self.tmpdir / "mock-bin"
        mock_bin.mkdir()
        calls = self.tmpdir / "python-calls.log"
        fake_python = mock_bin / "python3"
        fake_python.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$PULSAR_TEST_CALLS\"\n"
            "kill -s \"$PULSAR_TEST_SIGNAL\" \"$PPID\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

        for signal_name, expected_code in (("INT", 130), ("TERM", 143)):
            calls.unlink(missing_ok=True)
            env = os.environ.copy()
            env["PATH"] = f"{mock_bin}{os.pathsep}{env['PATH']}"
            env["PULSAR_TEST_CALLS"] = str(calls)
            env["PULSAR_TEST_SIGNAL"] = signal_name
            proc = subprocess.run(
                [
                    str(validate_dir / "run-gates.sh"),
                    "fixture",
                    "--tag",
                    signal_name.lower(),
                ],
                cwd=str(gate_repo),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(
                proc.returncode, expected_code, proc.stdout + proc.stderr
            )
            invoked = calls.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(invoked), 1, invoked)
            self.assertIn("validate/greedy_capture.py", invoked[0])
            self.assertNotIn("gate 3:", proc.stdout)
            self.assertIn("GATES INTERRUPTED", proc.stderr)


if __name__ == "__main__":
    unittest.main()
