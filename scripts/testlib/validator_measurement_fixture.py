#!/usr/bin/env python3
"""Deterministic capture and bench fixtures for validator measurements."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any


def capture_row(
    *,
    prompt: str = "p",
    text: str = "ab",
    tokens: list[str] | None = None,
    logprobs: list[float | None] | None = None,
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "text": text,
        "tokens": list(tokens if tokens is not None else ["a", "b"]),
        "logprobs": list(logprobs if logprobs is not None else [-0.1, -0.2]),
    }


def identical_captures() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [
        capture_row(prompt="alpha", text="one", tokens=["one"], logprobs=[-0.1]),
        capture_row(prompt="beta", text="two", tokens=["two"], logprobs=[-0.2]),
    ]
    return copy.deepcopy(rows), copy.deepcopy(rows)


def fp_equivalent_captures() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    left = [capture_row(prompt="p", text="ab", tokens=["a", "b"], logprobs=[-0.1, -0.2])]
    right = [capture_row(prompt="p", text="ac", tokens=["a", "c"], logprobs=[-0.1, -0.25])]
    return left, right


def divergent_captures() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    left = [capture_row(prompt="p", text="ab", tokens=["a", "b"], logprobs=[-0.1, -2.0])]
    right = [capture_row(prompt="p", text="ax", tokens=["a", "x"], logprobs=[-0.1, -0.2])]
    return left, right


def truncated_captures() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    left = [capture_row(prompt="p", text="ab", tokens=["a", "b"], logprobs=[-0.1, -0.2])]
    right = [capture_row(prompt="p", text="a", tokens=["a"], logprobs=[-0.1])]
    return left, right


def unusable_capture() -> list[dict[str, Any]]:
    return []


def write_capture(path, rows: list[dict[str, Any]] | Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def bench_result(ttft: float, decode_tps: float = 10.0, ntok: int = 8) -> dict[str, Any]:
    return {
        "ttft": ttft,
        "decode_tps": decode_tps,
        "total_s": 1.0,
        "ntok": ntok,
    }


def spread_ttft_results() -> list[dict[str, Any]]:
    return [
        bench_result(0.010),
        bench_result(0.020),
        bench_result(0.030),
        bench_result(0.040),
        bench_result(0.100),
    ]


def exact_match_rate(identical: int, sample: int) -> str:
    value = Decimal(identical) / Decimal(sample)
    normalized = format(value, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


COMPLETE_PHASE = {"completion": "complete", "reason": "completed"}


def complete_identity_payload(
    *,
    spec_id: str = "9cd7164d49591f763ba506d7845a13f96b247bbae193bce49f978a67e1e4aa16",
    manifest_id: str = (
        "c1f04ed98a7afa381025f79cc2b8a774cc75a9fdf210b8fa7ac2017cdfb91bfd"
    ),
    expected_file_count: int = 2,
    matched_file_count: int = 2,
    mismatched_file_count: int = 0,
    missing_file_count: int = 0,
    extra_file_count: int = 0,
) -> dict[str, Any]:
    return {
        "spec_id": spec_id,
        "manifest_id": manifest_id,
        "expected_file_count": expected_file_count,
        "matched_file_count": matched_file_count,
        "mismatched_file_count": mismatched_file_count,
        "missing_file_count": missing_file_count,
        "extra_file_count": extra_file_count,
    }


def complete_serve_smoke_payload() -> dict[str, Any]:
    return {
        "health": copy.deepcopy(COMPLETE_PHASE),
        "warmup": copy.deepcopy(COMPLETE_PHASE),
        "completion": copy.deepcopy(COMPLETE_PHASE),
    }


def complete_compare_payload() -> dict[str, Any]:
    digest_a = "a" * 64
    digest_b = "c" * 64
    return {
        "sample_count": 2,
        "identical_record_count": 2,
        "exact_text_count": 2,
        "mean_prefix_match": "1",
        "min_prefix_match": "1",
        "max_matched_prefix_logprob_delta": "0",
        "hard_disagreement_count": 0,
        "diagnostic_verdict": "identical",
        "source_digests": {"a": digest_a, "b": digest_b},
    }


def fp_equivalent_compare_payload() -> dict[str, Any]:
    return {
        "sample_count": 1,
        "identical_record_count": 0,
        "exact_text_count": 0,
        "mean_prefix_match": "0.5",
        "min_prefix_match": "0.5",
        "max_matched_prefix_logprob_delta": "0.05",
        "hard_disagreement_count": 0,
        "diagnostic_verdict": "fp-equivalent",
        "source_digests": {"a": "a" * 64, "b": "c" * 64},
    }


def complete_accuracy_payload(
    *,
    correct_count: int = 85,
    measured_sample_count: int = 100,
    requested_sample_count: int = 100,
    accuracy: str = "0.85",
) -> dict[str, Any]:
    return {
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
        "requested_sample_count": requested_sample_count,
        "measured_sample_count": measured_sample_count,
        "correct_count": correct_count,
        "request_error_count": 0,
        "accuracy": accuracy,
    }


def complete_soak_payload(
    *,
    duration_seconds: str = "3600",
    started_at: str = "2026-09-02T00:00:00Z",
    ended_at: str = "2026-09-02T01:00:00Z",
    completed_requests: int = 10,
    request_error_count: int = 0,
    concurrency: int = 8,
) -> dict[str, Any]:
    return {
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "concurrency": concurrency,
        "completed_requests": completed_requests,
        "request_error_count": request_error_count,
    }


def complete_benchmark_level(concurrency: int) -> dict[str, Any]:
    requested = max(2 * concurrency, 4)
    return {
        "concurrency": concurrency,
        "requested_request_count": requested,
        "measured_request_count": requested,
        "completion": "complete",
        "reason": "completed",
        "ttft_p50_ms": "10",
        "ttft_p95_ms": "20",
        "decode_tps_p50": "10",
        "aggregate_tps": "40",
        "wall_s": "1",
    }
