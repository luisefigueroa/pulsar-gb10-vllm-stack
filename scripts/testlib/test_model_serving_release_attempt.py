#!/usr/bin/env python3
"""Contracts for ADR 0004 attempt composition from validator measurements."""

from __future__ import annotations

import contextlib
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
    model_serving_release_attempt as attempt,
    model_serving_release_capture as capture,
)
from scripts.testlib import (  # noqa: E402
    model_serving_release_attempt_fixture as fixture,
)
from scripts.testlib import model_serving_release_fixture as release_fixture  # noqa: E402


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
        self.assertEqual(planned["state"], "unreviewed")
        self.assertEqual(planned["authority"], "none")
        return code

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


if __name__ == "__main__":
    unittest.main()
