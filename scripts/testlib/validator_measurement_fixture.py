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
