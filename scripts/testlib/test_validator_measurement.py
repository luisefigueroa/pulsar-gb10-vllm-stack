#!/usr/bin/env python3
"""Contracts for closed validator-measurement documents."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "validate"))

from scripts.testlib import validator_measurement_fixture as fixture  # noqa: E402
import bench_serve as bench  # noqa: E402
import compare_captures  # noqa: E402
import validator_measurement as measurement  # noqa: E402


class ValidatorMeasurementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pulsar-vmeas-"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_compare(self, left: pathlib.Path, right: pathlib.Path, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = compare_captures.main([str(left), str(right), *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_identical_compare_measurement(self) -> None:
        left_rows, right_rows = fixture.identical_captures()
        left = self.tmpdir / "left.json"
        right = self.tmpdir / "right.json"
        out = self.tmpdir / "compare.json"
        fixture.write_capture(left, left_rows)
        fixture.write_capture(right, right_rows)
        code, stdout, stderr = self.run_compare(
            left, right, "--require-identical", "--result-json", str(out)
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("IDENTICAL", stdout)
        self.assertNotIn("schema_version", stdout)
        document = json.loads(out.read_text(encoding="utf-8"))
        payload = document["compare-captures"]
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(payload["identical_record_count"], 2)
        self.assertEqual(payload["sample_count"], 2)
        self.assertEqual(payload["diagnostic_verdict"], "identical")
        self.assertEqual(payload["source_digests"]["a"], measurement.file_digest(left))
        self.assertNotIn("exit_code", document)
        self.assertNotIn(str(left), json.dumps(document))

    def test_fp_equivalent_is_not_identical(self) -> None:
        left_rows, right_rows = fixture.fp_equivalent_captures()
        left = self.tmpdir / "left.json"
        right = self.tmpdir / "right.json"
        out = self.tmpdir / "compare.json"
        fixture.write_capture(left, left_rows)
        fixture.write_capture(right, right_rows)
        code, stdout, _stderr = self.run_compare(
            left, right, "--require-identical", "--result-json", str(out)
        )
        self.assertEqual(code, 1)
        self.assertIn("NOT-IDENTICAL", stdout)
        document = json.loads(out.read_text(encoding="utf-8"))
        payload = document["compare-captures"]
        self.assertEqual(payload["diagnostic_verdict"], "fp-equivalent")
        self.assertEqual(payload["identical_record_count"], 0)
        self.assertEqual(payload["exact_text_count"], 0)
        self.assertEqual(payload["sample_count"], 1)
        self.assertNotEqual(
            fixture.exact_match_rate(
                payload["identical_record_count"], payload["sample_count"]
            ),
            "1",
        )

    def test_divergent_and_truncated_compare(self) -> None:
        left_rows, right_rows = fixture.divergent_captures()
        left = self.tmpdir / "left.json"
        right = self.tmpdir / "right.json"
        out = self.tmpdir / "divergent.json"
        fixture.write_capture(left, left_rows)
        fixture.write_capture(right, right_rows)
        code, stdout, _stderr = self.run_compare(left, right, "--result-json", str(out))
        self.assertEqual(code, 1)
        self.assertIn("DIVERGENT", stdout)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["compare-captures"]["diagnostic_verdict"], "divergent")
        self.assertGreater(
            document["compare-captures"]["hard_disagreement_count"], 0
        )

        left_rows, right_rows = fixture.truncated_captures()
        fixture.write_capture(left, left_rows)
        fixture.write_capture(right, right_rows)
        truncated = self.tmpdir / "truncated.json"
        code, stdout, _stderr = self.run_compare(
            left, right, "--result-json", str(truncated)
        )
        self.assertEqual(code, 1)
        self.assertIn("truncated output", stdout)
        document = json.loads(truncated.read_text(encoding="utf-8"))
        self.assertEqual(document["compare-captures"]["diagnostic_verdict"], "divergent")
        self.assertEqual(document["compare-captures"]["hard_disagreement_count"], 1)

    def test_unusable_compare_is_incomplete(self) -> None:
        left = self.tmpdir / "left.json"
        empty = self.tmpdir / "empty.json"
        out = self.tmpdir / "unusable.json"
        fixture.write_capture(left, fixture.identical_captures()[0])
        fixture.write_capture(empty, [])
        code, _stdout, stderr = self.run_compare(left, empty, "--result-json", str(out))
        self.assertEqual(code, 2)
        self.assertIn("non-empty JSON list", stderr)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "unusable-input")
        self.assertEqual(document["compare-captures"]["diagnostic_verdict"], "unusable")

    def test_measurement_rejects_privacy_and_status(self) -> None:
        document = measurement.build_compare_measurement(
            completion="complete",
            reason="completed",
            payload={
                "sample_count": 1,
                "identical_record_count": 1,
                "exact_text_count": 1,
                "mean_prefix_match": "1",
                "min_prefix_match": "1",
                "max_matched_prefix_logprob_delta": "0",
                "hard_disagreement_count": 0,
                "diagnostic_verdict": "identical",
                "source_digests": {"a": "a" * 64, "b": "b" * 64},
            },
        )
        document["validation_status"] = "validated"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)
        document = measurement.build_compare_measurement(
            completion="complete",
            reason="completed",
            payload={
                "sample_count": 1,
                "identical_record_count": 1,
                "exact_text_count": 1,
                "mean_prefix_match": "1",
                "min_prefix_match": "1",
                "max_matched_prefix_logprob_delta": "0",
                "hard_disagreement_count": 0,
                "diagnostic_verdict": "identical",
                "source_digests": {"a": "a" * 64, "b": "b" * 64},
            },
        )
        document["hostname"] = "node-a.example"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)
        document = measurement.build_compare_measurement(
            completion="complete",
            reason="completed",
            payload={
                "sample_count": 1,
                "identical_record_count": 1,
                "exact_text_count": 1,
                "mean_prefix_match": "1",
                "min_prefix_match": "1",
                "max_matched_prefix_logprob_delta": "0",
                "hard_disagreement_count": 0,
                "diagnostic_verdict": "identical",
                "source_digests": {"a": "a" * 64, "b": "b" * 64},
            },
        )
        document["compare-captures"]["note"] = "http://127.0.0.1:8000"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)

    def test_bench_p50_is_not_p95_and_decimals_are_stable(self) -> None:
        results = fixture.spread_ttft_results()
        p50 = bench.percentile([row["ttft"] for row in results], 50.0)
        p95 = bench.percentile([row["ttft"] for row in results], 95.0)
        self.assertLess(p50, p95)
        payload = bench.complete_level_payload(
            concurrency=1,
            requested=5,
            results=results,
            wall=2.0,
        )
        self.assertEqual(payload["ttft_p50_ms"], "30")
        self.assertEqual(payload["ttft_p95_ms"], "88")
        self.assertNotEqual(payload["ttft_p50_ms"], payload["ttft_p95_ms"])
        again = bench.complete_level_payload(
            concurrency=1,
            requested=5,
            results=results,
            wall=2.0,
        )
        self.assertEqual(payload, again)

    def test_bench_explicit_request_count_and_failed_level(self) -> None:
        seen: list[tuple[int, int, bool]] = []

        async def fake_run_level(_url, _model, conc, nreq, _in_toks, _out_toks, warm, api_key=None):
            seen.append((conc, nreq, warm))
            count = conc if warm else nreq
            rows = [
                {"ttft": 0.01 + index * 0.001, "decode_tps": 2.0, "total_s": 1.0, "ntok": 4}
                for index in range(count)
            ]
            return rows, 1.0

        bench.run_level = fake_run_level
        out = self.tmpdir / "legacy.json"
        measured = self.tmpdir / "bench.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            sys.argv = [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "2",
                "--num-requests",
                "6",
                "--out",
                str(out),
                "--result-json",
                str(measured),
            ]
            code = asyncio.run(bench.main())
        self.assertEqual(code, 0)
        self.assertIn((2, 6, False), seen)
        legacy = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(legacy[0]["n"], 6)
        self.assertIn("ttft_p50_ms", legacy[0])
        self.assertNotIn("ttft_p95_ms", legacy[0])
        document = json.loads(measured.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(document["benchmark-serving"]["explicit_request_count"], 6)
        self.assertEqual(
            document["benchmark-serving"]["levels"][0]["measured_request_count"], 6
        )
        self.assertNotIn("url", json.dumps(document))
        self.assertNotIn("127.0.0.1", json.dumps(document))

        async def failed_warmup(*_args, **_kwargs):
            return [], 0.1

        bench.run_level = failed_warmup
        failed = self.tmpdir / "failed.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            sys.argv = [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "1",
                "--result-json",
                str(failed),
            ]
            code = asyncio.run(bench.main())
        self.assertEqual(code, 1)
        document = json.loads(failed.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "warmup-failed")
        self.assertEqual(
            document["benchmark-serving"]["levels"][0]["reason"], "warmup-failed"
        )

    def test_bench_rejects_request_count_below_concurrency(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "8",
                "--num-requests",
                "1",
            ],
        ), contextlib.redirect_stderr(stderr), self.assertRaises(
            SystemExit
        ) as raised:
            asyncio.run(bench.main())
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("at least the largest --concurrency", stderr.getvalue())

    def test_bench_rejects_duplicate_concurrency_before_network_work(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            sys,
            "argv",
            [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "1",
                "1",
            ],
        ), mock.patch.object(
            bench, "run_level", new_callable=mock.AsyncMock
        ) as run_level, contextlib.redirect_stderr(stderr), self.assertRaises(
            SystemExit
        ) as raised:
            asyncio.run(bench.main())
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--concurrency values must be unique", stderr.getvalue())
        run_level.assert_not_awaited()

    def test_semantic_count_and_range_errors(self) -> None:
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(
                measurement.build_compare_measurement(
                    completion="complete",
                    reason="completed",
                    payload={
                        "sample_count": 1,
                        "identical_record_count": 2,
                        "exact_text_count": 1,
                        "mean_prefix_match": "1",
                        "min_prefix_match": "1",
                        "max_matched_prefix_logprob_delta": "0",
                        "hard_disagreement_count": 0,
                        "diagnostic_verdict": "identical",
                        "source_digests": {"a": "a" * 64, "b": "b" * 64},
                    },
                )
            )
        document = measurement.build_compare_measurement(
            completion="complete",
            reason="completed",
            payload={
                "sample_count": 1,
                "identical_record_count": 1,
                "exact_text_count": 1,
                "mean_prefix_match": "1",
                "min_prefix_match": "1",
                "max_matched_prefix_logprob_delta": "0",
                "hard_disagreement_count": 0,
                "diagnostic_verdict": "identical",
                "source_digests": {"a": "a" * 64, "b": "b" * 64},
            },
        )
        document["compare-captures"]["mean_prefix_match"] = "1.5"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_compare_measurement(
                completion="complete",
                reason="completed",
                payload={
                    "sample_count": 1,
                    "identical_record_count": 1,
                    "exact_text_count": 1,
                    "mean_prefix_match": "1",
                    "min_prefix_match": "1",
                    "max_matched_prefix_logprob_delta": "0",
                    "hard_disagreement_count": 0,
                    "diagnostic_verdict": "identical",
                    "source_digests": {"a": None, "b": "b" * 64},
                },
            )
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_benchmark_measurement(
                completion="complete",
                reason="completed",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=4,
                levels=[
                    {
                        "concurrency": 1,
                        "requested_request_count": 4,
                        "measured_request_count": 2,
                        "completion": "complete",
                        "reason": "completed",
                        "ttft_p50_ms": "20",
                        "ttft_p95_ms": "30",
                        "decode_tps_p50": "10",
                        "aggregate_tps": "10",
                        "wall_s": "1",
                    }
                ],
            )
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(
                {
                    "schema_version": 1,
                    "kind": measurement.MEASUREMENT_KIND,
                    "program": "validate/compare_captures.py",
                    "operation": "compare-captures",
                    "completion": "complete",
                    "reason": "completed",
                    "compare-captures": {
                        "sample_count": True,
                        "identical_record_count": 1,
                        "exact_text_count": 1,
                        "mean_prefix_match": "1",
                        "min_prefix_match": "1",
                        "max_matched_prefix_logprob_delta": "0",
                        "hard_disagreement_count": 0,
                        "diagnostic_verdict": "identical",
                        "source_digests": {"a": "a" * 64, "b": "b" * 64},
                    },
                }
            )

    def test_nonstandard_json_and_bool_logprobs_persist_incomplete(self) -> None:
        left = self.tmpdir / "left.json"
        nan = self.tmpdir / "nan.json"
        out = self.tmpdir / "nan-result.json"
        fixture.write_capture(left, fixture.identical_captures()[0])
        nan.write_text(
            '[{"prompt":"p","text":"a","tokens":["a"],"logprobs":[NaN]}]\n',
            encoding="utf-8",
        )
        code, _stdout, stderr = self.run_compare(left, nan, "--result-json", str(out))
        self.assertEqual(code, 2)
        self.assertRegex(stderr.lower(), "constant|nan|malformed|json")
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")

        boolean = self.tmpdir / "bool.json"
        boolean.write_text(
            '[{"prompt":"p","text":"a","tokens":["a"],"logprobs":[true]}]\n',
            encoding="utf-8",
        )
        bool_out = self.tmpdir / "bool-result.json"
        code, _stdout, stderr = self.run_compare(
            left, boolean, "--result-json", str(bool_out)
        )
        self.assertEqual(code, 2)
        self.assertIn("logprobs must be finite numeric or null", stderr)
        document = json.loads(bool_out.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")

    def test_verdict_and_benchmark_consistency(self) -> None:
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_compare_measurement(
                completion="complete",
                reason="completed",
                payload={
                    "sample_count": 2,
                    "identical_record_count": 1,
                    "exact_text_count": 1,
                    "mean_prefix_match": "0.5",
                    "min_prefix_match": "0.5",
                    "max_matched_prefix_logprob_delta": "0",
                    "hard_disagreement_count": 0,
                    "diagnostic_verdict": "identical",
                    "source_digests": {"a": "a" * 64, "b": "b" * 64},
                },
            )
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_compare_measurement(
                completion="complete",
                reason="completed",
                payload={
                    "sample_count": 1,
                    "identical_record_count": 1,
                    "exact_text_count": 1,
                    "mean_prefix_match": "1",
                    "min_prefix_match": "1",
                    "max_matched_prefix_logprob_delta": "0",
                    "hard_disagreement_count": 0,
                    "diagnostic_verdict": "unusable",
                    "source_digests": {"a": "a" * 64, "b": "b" * 64},
                },
            )
        complete_level = {
            "concurrency": 1,
            "requested_request_count": 4,
            "measured_request_count": 4,
            "completion": "complete",
            "reason": "completed",
            "ttft_p50_ms": "20",
            "ttft_p95_ms": "30",
            "decode_tps_p50": "10",
            "aggregate_tps": "10",
            "wall_s": "1",
        }
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_benchmark_measurement(
                completion="incomplete",
                reason="measured-incomplete",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=4,
                levels=[complete_level],
            )
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_benchmark_measurement(
                completion="complete",
                reason="completed",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=4,
                levels=[complete_level, {**complete_level, "concurrency": 1}],
            )
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_benchmark_measurement(
                completion="complete",
                reason="completed",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=4,
                levels=[{**complete_level, "aggregate_tps": "0"}],
            )
        with self.assertRaisesRegex(
            measurement.ValidatorMeasurementError,
            "requested_request_count must be at least concurrency",
        ):
            measurement.build_benchmark_measurement(
                completion="complete",
                reason="completed",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=1,
                levels=[
                    {
                        **complete_level,
                        "concurrency": 8,
                        "requested_request_count": 1,
                        "measured_request_count": 1,
                    }
                ],
            )

    def test_symlink_parent_cannot_escape_result_json(self) -> None:
        real = self.tmpdir / "real-parent"
        real.mkdir()
        link = self.tmpdir / "linked-parent"
        link.symlink_to(real)
        target = link / "escaped.json"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.atomic_write_json(
                target,
                measurement.build_compare_measurement(
                    completion="incomplete",
                    reason="unusable-input",
                ),
            )
        self.assertFalse((real / "escaped.json").exists())

    def test_cross_field_measurement_invariants(self) -> None:
        compare = measurement.build_compare_measurement(
            completion="complete",
            reason="completed",
            payload={
                "sample_count": 2,
                "identical_record_count": 1,
                "exact_text_count": 1,
                "mean_prefix_match": "0.75",
                "min_prefix_match": "0.5",
                "max_matched_prefix_logprob_delta": "0.1",
                "hard_disagreement_count": 0,
                "diagnostic_verdict": "fp-equivalent",
                "source_digests": {"a": "a" * 64, "b": "b" * 64},
            },
        )
        invalid = json.loads(json.dumps(compare))
        invalid["compare-captures"]["exact_text_count"] = 0
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(invalid)
        invalid = json.loads(json.dumps(compare))
        invalid["compare-captures"]["min_prefix_match"] = "0.8"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(invalid)

        level = {
            "concurrency": 1,
            "requested_request_count": 4,
            "measured_request_count": 4,
            "completion": "complete",
            "reason": "completed",
            "ttft_p50_ms": "30",
            "ttft_p95_ms": "20",
            "decode_tps_p50": "10",
            "aggregate_tps": "10",
            "wall_s": "1",
        }
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.build_benchmark_measurement(
                completion="complete",
                reason="completed",
                input_tokens=512,
                output_tokens=256,
                prompt_style="synthetic",
                explicit_request_count=5,
                levels=[level],
            )

    def test_bench_exception_persists_incomplete_result(self) -> None:
        async def boom(*_args, **_kwargs):
            raise RuntimeError("http://127.0.0.1:8000/secret")

        bench.run_level = boom
        measured = self.tmpdir / "raised.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            sys.argv = [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "1",
                "2",
                "--result-json",
                str(measured),
            ]
            code = asyncio.run(bench.main())
        self.assertEqual(code, 1)
        self.assertIn("benchmark execution failed", stderr.getvalue())
        self.assertNotIn("127.0.0.1", stderr.getvalue())
        document = json.loads(measured.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "measured-incomplete")
        self.assertEqual(len(document["benchmark-serving"]["levels"]), 2)
        self.assertNotIn("127.0.0.1", json.dumps(document))

    def test_legacy_output_failure_preserves_complete_measurement(self) -> None:
        async def complete_run(
            _url, _model, conc, nreq, _in_toks, _out_toks, warm, api_key=None
        ):
            count = conc if warm else nreq
            return [
                {
                    "ttft": 0.01,
                    "decode_tps": 2.0,
                    "total_s": 1.0,
                    "ntok": 4,
                }
                for _index in range(count)
            ], 1.0

        measured = self.tmpdir / "complete-despite-legacy-failure.json"
        with mock.patch.object(bench, "run_level", complete_run), mock.patch(
            "builtins.open", side_effect=OSError("private output path")
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            sys.argv = [
                "bench_serve.py",
                "--model",
                "fixture",
                "--concurrency",
                "1",
                "--num-requests",
                "4",
                "--out",
                str(self.tmpdir / "legacy.json"),
                "--result-json",
                str(measured),
            ]
            code = asyncio.run(bench.main())
        self.assertEqual(code, 1)
        document = json.loads(measured.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "complete")
        self.assertEqual(document["reason"], "completed")

    def test_overflow_infinity_logprob_persists_incomplete(self) -> None:
        left = self.tmpdir / "left.json"
        huge = self.tmpdir / "huge.json"
        out = self.tmpdir / "huge-result.json"
        fixture.write_capture(left, fixture.identical_captures()[0])
        huge.write_text(
            '[{"prompt":"p","text":"a","tokens":["a"],"logprobs":[1e999]}]\n',
            encoding="utf-8",
        )
        code, _stdout, stderr = self.run_compare(left, huge, "--result-json", str(out))
        self.assertEqual(code, 2)
        self.assertIn("finite", stderr)
        document = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(document["completion"], "incomplete")

    def test_result_json_is_exclusive(self) -> None:
        left_rows, right_rows = fixture.identical_captures()
        left = self.tmpdir / "left.json"
        right = self.tmpdir / "right.json"
        out = self.tmpdir / "once.json"
        fixture.write_capture(left, left_rows)
        fixture.write_capture(right, right_rows)
        code, _stdout, _stderr = self.run_compare(left, right, "--result-json", str(out))
        self.assertEqual(code, 0)
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.write_measurement(out, json.loads(out.read_text(encoding="utf-8")))

    def test_accuracy_measurement_binds_counts_and_dataset(self) -> None:
        document = measurement.build_accuracy_measurement(
            completion="complete",
            reason="completed",
            payload={
                "dataset_id": "openai/gsm8k",
                "dataset_revision": "a" * 40,
                "dataset_file_sha256": "b" * 64,
                "subset": "main",
                "split": "test",
                "selection": "sha256-order-first-100",
                "answer_normalization": "gsm8k-final-number-v1",
                "max_completion_tokens": 4096,
                "reasoning_mode": "enabled",
                "temperature": "0",
                "requested_sample_count": 100,
                "measured_sample_count": 100,
                "correct_count": 85,
                "request_error_count": 0,
                "accuracy": "0.85",
            },
        )
        self.assertEqual(document["evaluate-gsm8k"]["accuracy"], "0.85")
        document["evaluate-gsm8k"]["correct_count"] = 84
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)

    def test_soak_measurement_binds_timestamp_interval(self) -> None:
        document = measurement.build_soak_measurement(
            completion="complete",
            reason="completed",
            payload={
                "started_at": "2026-08-29T00:00:00Z",
                "ended_at": "2026-08-29T00:30:00Z",
                "duration_seconds": "1800",
                "concurrency": 8,
                "completed_requests": 500,
                "request_error_count": 0,
            },
        )
        self.assertEqual(document["validate-soak"]["duration_seconds"], "1800")
        document["validate-soak"]["duration_seconds"] = "1799"
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(document)


    def test_resource_diagnostic_is_status_neutral_and_rank_complete(self) -> None:
        rank = {
            "rank": "single",
            "collection_status": "complete",
            "sample_count": 2,
            "workload_sample_count": 2,
            "mem_available_min_bytes": 1000,
            "swap_used_max_bytes": 0,
            "node_memory_pressure_some_total_delta_us": 1,
            "workload_memory_current_max_bytes": 900,
            "workload_memory_peak_start_bytes": 800,
            "workload_memory_peak_end_bytes": 900,
            "workload_swap_current_max_bytes": 0,
            "oom_delta": 0,
            "oom_kill_delta": 0,
        }
        document = measurement.build_resource_measurement(
            completion="complete",
            reason="completed",
            payload={
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T12:00:02Z",
                "duration_seconds": "2",
                "qualification_scope": "model-qualification",
                "sample_interval_seconds": "1",
                "expected_rank_count": 1,
                "observed_rank_count": 1,
                "sample_count": 2,
                "ranks": [rank],
            },
        )
        self.assertEqual(document["operation"], "observe-resources")
        self.assertNotIn("status", document)
        invalid = json.loads(json.dumps(document))
        invalid["observe-resources"]["ranks"][0]["oom_kill_delta"] = -1
        with self.assertRaises(measurement.ValidatorMeasurementError):
            measurement.validate_measurement(invalid)

    def test_incomplete_resource_diagnostic_preserves_unavailable_rank(self) -> None:
        document = measurement.build_resource_measurement(
            completion="incomplete",
            reason="no-samples",
            payload={
                "started_at": "2026-08-14T12:00:00Z",
                "ended_at": "2026-08-14T12:00:01Z",
                "duration_seconds": "1",
                "qualification_scope": "model-qualification",
                "sample_interval_seconds": "1",
                "expected_rank_count": 1,
                "observed_rank_count": 0,
                "sample_count": 0,
                "ranks": [
                    {
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
                ],
            },
        )
        self.assertEqual(document["completion"], "incomplete")
        self.assertEqual(document["reason"], "no-samples")


if __name__ == "__main__":
    unittest.main()
