#!/usr/bin/env python3
"""Deterministic GSM8K exact-answer evaluation with a closed measurement.

The dataset file is supplied explicitly and paired with a public dataset ID
and exact revision. Rows are selected by SHA-256(question UTF-8) order so the
same snapshot and sample count produce the same workload. The measurement
contains aggregate facts only; it assigns no ADR 0004 status.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any

from http_auth import api_headers, resolve_api_key
from validator_measurement import (
    build_accuracy_measurement,
    canonical_decimal,
    file_digest,
    write_measurement,
)


NUMBER_RE = re.compile(r"[-+]?(?:\d[\d,]*)(?:\.\d+)?")
NORMALIZATION = "gsm8k-final-number-v1"


def _load_rows(path: pathlib.Path) -> list[dict[str, str]]:
    if path.suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise ValueError("pyarrow is required for a parquet dataset") from exc
        values = pq.read_table(path, columns=["question", "answer"]).to_pylist()
    else:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            values = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            values = json.loads(text)
    if not isinstance(values, list) or not values:
        raise ValueError("dataset must contain a non-empty row list")
    rows: list[dict[str, str]] = []
    for index, row in enumerate(values):
        if not isinstance(row, dict):
            raise ValueError(f"dataset row {index} must be an object")
        question = row.get("question")
        answer = row.get("answer")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"dataset row {index} question is invalid")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"dataset row {index} answer is invalid")
        rows.append({"question": question, "answer": answer})
    return rows


def select_rows(rows: list[dict[str, str]], count: int) -> list[dict[str, str]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            hashlib.sha256(row["question"].encode("utf-8")).hexdigest(),
            row["question"],
            row["answer"],
        ),
    )
    if len(ordered) < count:
        raise ValueError(f"dataset has {len(ordered)} rows; {count} required")
    return ordered[:count]


def normalize_number(text: str, *, gold: bool) -> str | None:
    candidate = text.rsplit("####", 1)[-1] if gold and "####" in text else text
    matches = NUMBER_RE.findall(candidate)
    if not matches:
        return None
    raw = matches[-1].replace(",", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return canonical_decimal(value, label="normalized GSM8K answer", require_canonical=False)


def complete(
    url: str,
    model: str,
    question: str,
    max_tokens: int,
    reasoning_mode: str,
    api_key: str | None,
) -> str:
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": question + "\nGive the final numeric answer clearly.",
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    if reasoning_mode == "disabled":
        body["chat_template_kwargs"] = {"enable_thinking": False}
    request = urllib.request.Request(
        url + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=api_headers(api_key, content_type=True),
    )
    with urllib.request.urlopen(request, timeout=1800) as response:
        document = json.load(response)
    return str(document["choices"][0]["message"].get("content") or "")


def measurement_payload(
    *,
    args: argparse.Namespace,
    dataset_digest: str | None,
    measured: int,
    correct: int,
    errors: int,
) -> dict[str, Any]:
    accuracy = (
        canonical_decimal(
            Decimal(correct) / Decimal(measured),
            label="accuracy",
            require_canonical=False,
        )
        if measured
        else None
    )
    return {
        "dataset_id": args.dataset_id,
        "dataset_revision": args.dataset_revision,
        "dataset_file_sha256": dataset_digest,
        "subset": args.subset,
        "split": args.split,
        "selection": f"sha256-order-first-{args.sample_size}",
        "answer_normalization": NORMALIZATION,
        "max_completion_tokens": args.max_completion_tokens,
        "reasoning_mode": args.reasoning_mode,
        "temperature": "0",
        "requested_sample_count": args.sample_size,
        "measured_sample_count": measured,
        "correct_count": correct,
        "request_error_count": errors,
        "accuracy": accuracy,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--dataset-revision", required=True)
    parser.add_argument("--subset", default="main")
    parser.add_argument("--split", default="test")
    parser.add_argument("--sample-size", type=int, required=True)
    parser.add_argument("--max-completion-tokens", type=int, default=4096)
    parser.add_argument(
        "--reasoning-mode", choices=("enabled", "disabled"), default="enabled"
    )
    parser.add_argument("--result-json", required=True)
    args = parser.parse_args(argv)
    if args.sample_size < 1:
        parser.error("--sample-size must be positive")
    if args.max_completion_tokens < 1:
        parser.error("--max-completion-tokens must be positive")

    dataset_path = pathlib.Path(args.dataset)
    digest = file_digest(dataset_path)
    try:
        selected = select_rows(_load_rows(dataset_path), args.sample_size)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        document = build_accuracy_measurement(
            completion="incomplete",
            reason="dataset-invalid",
            payload=measurement_payload(
                args=args,
                dataset_digest=digest,
                measured=0,
                correct=0,
                errors=0,
            ),
        )
        write_measurement(args.result_json, document)
        print(f"GSM8K dataset invalid: {exc}", file=sys.stderr)
        return 2

    api_key = resolve_api_key(args.api_key)
    measured = 0
    correct = 0
    errors = 0
    for index, row in enumerate(selected, start=1):
        try:
            response = complete(
                args.url,
                args.model,
                row["question"],
                args.max_completion_tokens,
                args.reasoning_mode,
                api_key,
            )
            predicted = normalize_number(response, gold=False)
            expected = normalize_number(row["answer"], gold=True)
            measured += 1
            if predicted is not None and predicted == expected:
                correct += 1
            print(
                f"[{index}/{len(selected)}] correct={correct} measured={measured}",
                file=sys.stderr,
            )
        except Exception as exc:  # preserve a closed incomplete measurement
            errors += 1
            print(
                f"[{index}/{len(selected)}] request error: {type(exc).__name__}",
                file=sys.stderr,
            )

    complete_run = measured == args.sample_size and errors == 0
    reason = "completed" if complete_run else "request-failed"
    document = build_accuracy_measurement(
        completion="complete" if complete_run else "incomplete",
        reason=reason,
        payload=measurement_payload(
            args=args,
            dataset_digest=digest,
            measured=measured,
            correct=correct,
            errors=errors,
        ),
    )
    write_measurement(args.result_json, document)
    accuracy = document["evaluate-gsm8k"]["accuracy"]
    print(
        f"GSM8K measured={measured}/{args.sample_size} correct={correct} "
        f"accuracy={accuracy} errors={errors}"
    )
    return 0 if complete_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
